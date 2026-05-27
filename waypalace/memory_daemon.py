#!/usr/bin/env python3
"""
memory_daemon.py - Persistent memory search daemon (V2.5 support)

Problem: Each `memory_search.py` subprocess invocation re-loads bge-m3 + reranker
(8GB models, ~14 seconds cold start). Unusable for PreToolUse hooks which need
sub-second response.

Solution: A long-running daemon listens on a Unix socket. Models stay loaded.
Search requests complete in <500ms after warm-up.

Protocol (line-oriented JSON over Unix socket):
  Request:  {"cmd": "search", "query": "...", "wing": "...", "limit": 5, "threshold": 0.65}
  Response: {"ok": true, "results": [...]}

  Request:  {"cmd": "ping"}
  Response: {"ok": true, "pid": 12345, "uptime_sec": 3600}

Usage:
  # Start daemon (once)
  nohup ~/.mempalace/venv-zh/bin/python ~/.claude/scripts/memory_daemon.py &

  # From any process
  echo '{"cmd":"search","query":"X publisher"}' | nc -U ~/.mempalace-zh/daemon.sock
"""
from __future__ import annotations
import json
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SOCKET_PATH = Path(os.path.expanduser("~/.mempalace-zh/daemon.sock"))
PID_FILE = Path(os.path.expanduser("~/.mempalace-zh/daemon.pid"))
LOG_FILE = Path(os.path.expanduser("~/.mempalace-zh/logs/daemon.log"))

START_TIME = time.time()
_log_lock = threading.Lock()


def log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


# Pre-load on startup so first request is fast
def warmup():
    log("Warming up models...")
    import memory_core
    memory_core.load_embedder()
    log("  - bge-m3 loaded")
    try:
        import memory_rerank
        memory_rerank.load_reranker()
        log("  - bge-reranker loaded")
    except Exception as e:
        log(f"  - reranker failed: {e}")
    log(f"Warmup complete in {time.time() - START_TIME:.1f}s")


def handle_search(req: dict) -> dict:
    query = req.get("query", "")
    wing = req.get("wing")
    limit = int(req.get("limit", 5))
    threshold = float(req.get("threshold", 0.5))
    fast = bool(req.get("fast", False))  # Skip reranker for speed
    if not query:
        return {"ok": False, "error": "empty query"}
    try:
        if fast:
            # Fast path: vector search + aging boost only, no reranker (~200ms)
            import memory_core, memory_aging
            col = memory_core.get_collection()
            # Chroma's embedding_function handles encoding automatically via query_texts
            where = {"wing": wing} if wing else None
            result = col.query(
                query_texts=[query], n_results=limit * 3,
                where=where, include=["distances", "documents", "metadatas"],
            )
            if not result or not result.get("ids") or not result["ids"][0]:
                return {"ok": True, "results": []}
            ids = result["ids"][0]
            dists = result["distances"][0]
            docs = result["documents"][0]
            metas = result["metadatas"][0]
            archived = memory_aging.get_archived_chunk_ids()
            aging_data = memory_aging.get_aging_data(ids)
            items = []
            for cid, dist, doc, meta in zip(ids, dists, docs, metas):
                if cid in archived:
                    continue
                sim = 1.0 - dist
                if sim < threshold:
                    continue
                aging = aging_data.get(cid, {})
                boosted = round(memory_aging.compute_score(sim, aging), 3)
                if boosted <= 0:
                    continue
                items.append({
                    "chunk_id": cid,
                    "text": doc,
                    "wing": meta.get("wing", ""),
                    "source_file": meta.get("source_file", ""),
                    "source_name": meta.get("source_name", ""),
                    "similarity": round(sim, 3),
                    "boosted_score": boosted,
                })
            items.sort(key=lambda r: -r["boosted_score"])
            items = items[:limit]
            # Record access (usage events) for the returned chunks
            if items:
                chunk_ids = [r["chunk_id"] for r in items]
                wing_map = {r["chunk_id"]: (r["wing"], r["source_file"]) for r in items}
                memory_aging.record_access(chunk_ids, wing_map=wing_map)
                events = [{"chunk_id": r["chunk_id"], "wing": r["wing"], "query": query,
                          "mode": "auto_surface", "similarity": r["similarity"],
                          "boosted_score": r["boosted_score"]} for r in items]
                memory_aging.log_usage_events(events)
            return {"ok": True, "results": items}

        import memory_search
        # G1 (2026-05-26): support detail_level in socket protocol.
        # Default "index" because hook-mediated callers want cheap snippets;
        # explicit "full" still works for callers that need complete chunks.
        detail_level = req.get("detail_level", "index")
        # Phase 2b (2026-05-26): support hybrid dense+sparse via socket.
        # Default False — daemon serves both autosurface hook (fast path, want
        # cheap dense-only) and explicit hybrid callers. Hook callers should
        # NOT set hybrid=true because hybrid_embedder cold-load is 60s.
        hybrid = bool(req.get("hybrid", False))
        if wing:
            results = memory_search.search_single(query, wing=wing, n_results=limit, threshold=threshold)
            # D004 P1-I: search_single returns [{"error": ...}] envelope on chromadb error.
            # Don't pass that through apply_detail_level (which would project the error dict
            # as if it were a chunk). Surface the error cleanly.
            if results and isinstance(results, list) and isinstance(results[0], dict) and results[0].get("error"):
                return {"ok": False, "error": f"search_single failed: {results[0]['error']}"}
            results = memory_search.apply_detail_level(results, detail_level)
            return {"ok": True, "results": results}
        else:
            result = memory_search.search_isolated(query, n_results=limit, threshold=threshold,
                                                    detail_level=detail_level, hybrid=hybrid)
            return {"ok": True, "results": result.get("results", []), "current_wing": result.get("current_wing")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_ping() -> dict:
    return {"ok": True, "pid": os.getpid(), "uptime_sec": int(time.time() - START_TIME)}


def handle_request(raw: bytes) -> bytes:
    try:
        req = json.loads(raw.decode("utf-8").strip())
    except Exception as e:
        return (json.dumps({"ok": False, "error": f"bad json: {e}"}) + "\n").encode("utf-8")

    cmd = req.get("cmd")
    if cmd == "search":
        resp = handle_search(req)
    elif cmd == "ping":
        resp = handle_ping()
    else:
        resp = {"ok": False, "error": f"unknown cmd: {cmd}"}

    return (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")


class Handler(socketserver.StreamRequestHandler):
    timeout = 5

    def handle(self):
        try:
            raw = self.rfile.readline(65536)
            if not raw:
                return
            response = handle_request(raw)
            self.wfile.write(response)
            self.wfile.flush()
        except Exception as e:
            log(f"handler error: {e}")


class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def check_existing():
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)  # signal 0 = check if alive
            log(f"Another daemon running (PID {old_pid}), exiting")
            return True
        except (OSError, ValueError):
            pass
    return False


def cleanup(*_):
    try:
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
    except Exception:
        pass
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass
    log("Daemon stopped")
    sys.exit(0)


def main():
    if check_existing():
        sys.exit(0)

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    PID_FILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    log(f"Daemon starting (PID {os.getpid()})")
    warmup()

    server = ThreadedUnixServer(str(SOCKET_PATH), Handler)
    os.chmod(SOCKET_PATH, 0o600)
    log(f"Listening on {SOCKET_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
