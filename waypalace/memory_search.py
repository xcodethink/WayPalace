#!/usr/bin/env python3
"""memory_search.py - search logic with Stage 2 aging integration + Phase 2b hybrid retrieval (dense + sparse)"""
import json
import os
import sys
import time
from datetime import datetime

import memory_core
import memory_aging
import memory_rerank


def _emit_metric(event: str, **fields) -> None:
    """Local-only metrics; fail-silent."""
    try:
        from mp_metrics import record_event
        record_event(event, **fields)
    except Exception:
        pass

# Phase 2b: bge-m3 native hybrid retrieval. Imported lazily because hybrid_embedder
# loads a 2 GB BGEM3FlagModel (~60s cold start) — only when hybrid=True is requested.
try:
    import sparse_store
    _SPARSE_STORE_AVAILABLE = True
except Exception:
    _SPARSE_STORE_AVAILABLE = False

THRESHOLD_DEFAULT = 0.5
THRESHOLD_CROSS = 0.4
RELEVANCE_HIGH = 0.7
RELEVANCE_MID = 0.5

DETAIL_LEVELS = ("index", "summary", "full")
DETAIL_INDEX_SNIPPET_CHARS = 80
DETAIL_SUMMARY_CHARS = 300

LOG_FILE = os.path.expanduser("~/.mempalace-zh/logs/search.jsonl")


def project_to_detail(chunk: dict, detail_level: str = "full") -> dict:
    """Project a raw chunk dict to the requested detail level.

    index   -> id + source + similarity + ~80 char snippet (cheapest)
    summary -> id + source + similarity + first paragraph (≤ 300 chars)
    full    -> untouched chunk (legacy backward-compat behaviour)
    """
    if detail_level not in DETAIL_LEVELS:
        detail_level = "full"
    if detail_level == "full":
        return chunk

    base = {
        "chunk_id": chunk.get("chunk_id"),
        "wing": chunk.get("wing"),
        "source_name": chunk.get("source_name"),
        "source_file": chunk.get("source_file"),
        "similarity": chunk.get("similarity"),
        "boosted_score": chunk.get("boosted_score"),
    }
    if chunk.get("rerank_score") is not None:
        base["rerank_score"] = chunk.get("rerank_score")

    text = chunk.get("text", "") or ""
    if detail_level == "index":
        snippet = text[:DETAIL_INDEX_SNIPPET_CHARS].replace("\n", " ").strip()
        if len(text) > DETAIL_INDEX_SNIPPET_CHARS:
            snippet += "..."
        base["snippet"] = snippet
    else:  # summary
        first_para = text.split("\n\n", 1)[0]
        summary = first_para[:DETAIL_SUMMARY_CHARS].strip()
        if len(text) > len(summary):
            summary += "..."
        base["summary"] = summary

    return base


def apply_detail_level(results, detail_level: str = "full"):
    """Apply detail projection to a list of chunks or wing-grouped dict."""
    if detail_level == "full":
        return results
    if isinstance(results, list):
        return [project_to_detail(r, detail_level) for r in results]
    if isinstance(results, dict):
        return {wing: [project_to_detail(r, detail_level) for r in items]
                for wing, items in results.items()}
    return results


def detect_current_wing(cwd=None):
    if cwd is None:
        cwd = os.getcwd()
    cwd = os.path.abspath(cwd)
    dev_root = os.path.expanduser("~/Developer")
    if not cwd.startswith(dev_root):
        return None
    rest = cwd[len(dev_root):].lstrip("/")
    if not rest:
        return None
    project = rest.split("/")[0]
    return project.lower().replace(" ", "_").replace("-", "_")


def search_single(query, wing=None, n_results=10, threshold=THRESHOLD_DEFAULT):
    """Search single wing. Returns results with chunk_id, similarity, boosted_score."""
    col = memory_core.get_collection()
    where = {"wing": wing} if wing else None
    try:
        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        results = col.query(**kwargs)
    except Exception as e:
        return [{"error": str(e)}]

    ids = results["ids"][0]
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    # Pre-filter by raw similarity
    candidates = []
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        similarity = round(1 - dist, 3)
        if similarity < threshold:
            continue
        candidates.append({
            "chunk_id": cid,
            "text": doc,
            "wing": meta.get("wing", "unknown"),
            "source_file": meta.get("source_file", ""),
            "source_name": meta.get("source_name", ""),
            "chunk_index": meta.get("chunk_index", 0),
            "similarity": similarity,
        })

    if not candidates:
        return []

    # Stage 2: apply aging-boosted score
    chunk_ids = [c["chunk_id"] for c in candidates]
    aging_data = memory_aging.get_aging_data(chunk_ids)

    for c in candidates:
        aging = aging_data.get(c["chunk_id"], {})
        c["boosted_score"] = round(memory_aging.compute_score(c["similarity"], aging), 3)
        c["access_count"] = aging.get("access_count", 0)
        c["importance"] = aging.get("importance", 5)

    # Filter out scored 0 (superseded or expired)
    candidates = [c for c in candidates if c["boosted_score"] > 0]

    # Sort by boosted score
    candidates.sort(key=lambda c: -c["boosted_score"])

    return candidates


def _sparse_recall(query: str, wing: str | None = None, n: int = 50) -> list[dict]:
    """Phase 2b: bge-m3 lexical-weights inner-product retrieval.

    Returns chunks shaped like the dense path's results so RRF can merge them.
    If sparse_store / hybrid_embedder unavailable → silently return [].
    """
    if not _SPARSE_STORE_AVAILABLE:
        return []
    try:
        import hybrid_embedder  # lazy: only loads bge-m3 when hybrid actually used
        embedder = hybrid_embedder.get_embedder()
        q_sparse = embedder.embed_query(query, dense=False, sparse=True)["sparse"]
        ranked = sparse_store.sparse_recall(q_sparse, wing=wing, n=n)
        if not ranked:
            return []
        chunk_ids = [cid for cid, _ in ranked]
        col = memory_core.get_collection()
        got = col.get(ids=chunk_ids, include=["metadatas", "documents"])
        score_map = dict(ranked)
        # Reassemble in score order (col.get returns in id order; restore ranking)
        meta_map = {cid: (meta, doc) for cid, meta, doc in zip(got["ids"], got["metadatas"], got["documents"])}
        out = []
        for cid in chunk_ids:
            if cid not in meta_map:
                continue
            meta, doc = meta_map[cid]
            out.append({
                "chunk_id": cid,
                "wing": meta.get("wing"),
                "source_name": meta.get("source_name"),
                "source_file": meta.get("source_file"),
                "text": doc,
                "similarity": float(score_map[cid]),  # reuse field for downstream
                "boosted_score": float(score_map[cid]),
                "sparse_score": float(score_map[cid]),
            })
        return out
    except Exception as e:
        sys.stderr.write(f"[memory_search] sparse_recall failed: {e}\n")
        return []


def _rrf_fuse(dense: list[dict], sparse: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion of two ranked lists by chunk_id.

    Returns deduped merged list sorted by RRF score (descending). Each entry
    keeps metadata from the first list it appeared in; rrf_score is added.
    """
    scores: dict[str, float] = {}
    record: dict[str, dict] = {}
    for rank, r in enumerate(dense, 1):
        cid = r["chunk_id"]
        scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank)
        record.setdefault(cid, r)
    for rank, r in enumerate(sparse, 1):
        cid = r["chunk_id"]
        scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank)
        record.setdefault(cid, r)
    merged = sorted(record.values(), key=lambda r: -scores[r["chunk_id"]])
    for r in merged:
        r["rrf_score"] = scores[r["chunk_id"]]
        # Pump rrf into boosted_score so downstream rerank candidate-selection
        # picks the fused winners (current logic sorts by boosted_score).
        r["boosted_score"] = scores[r["chunk_id"]]
    return merged


def search_isolated(query, current_wing=None, n_results=5, threshold=THRESHOLD_DEFAULT,
                    detail_level="full", hybrid=False):
    """Search current project + global wing, then merge.

    detail_level: "index" | "summary" | "full" (default).
    Internal access logging always uses the full chunk; projection is applied
    only to the returned `results` array.

    hybrid (Phase 2b): when True, run dense + sparse (bge-m3 lexical_weights)
    retrieval in parallel and RRF-fuse them before reranking. Default False
    while we observe quality and decide whether to flip default.
    """
    _t0 = time.time()
    if current_wing is None:
        current_wing = detect_current_wing()

    project_results = []
    if current_wing:
        project_results = search_single(query, wing=current_wing, n_results=n_results * 2, threshold=threshold)

    global_results = search_single(query, wing="global", n_results=n_results * 2, threshold=threshold)

    # Deduplicate by chunk_id (same chunk could appear if wing detection overlaps)
    seen_ids = set()
    all_results = []
    for r in project_results + global_results:
        if "error" in r or r["chunk_id"] in seen_ids:
            continue
        seen_ids.add(r["chunk_id"])
        all_results.append(r)

    # Phase 2b: hybrid path — fuse dense + sparse via RRF before rerank
    if hybrid:
        sparse_results = _sparse_recall(query, wing=current_wing, n=n_results * 4)
        if sparse_results:
            all_results = _rrf_fuse(all_results, sparse_results, k=60)

    # V2.2: Filter out archived chunks before rerank
    try:
        archived = memory_aging.get_archived_chunk_ids()
        if archived:
            all_results = [r for r in all_results if r["chunk_id"] not in archived]
    except Exception:
        pass

    # Sort by boosted score (Stage 2) — for hybrid this is the RRF score already
    all_results.sort(key=lambda r: -r.get("boosted_score", r["similarity"]))

    # Stage 3: rerank top candidates with cross-encoder for higher precision
    rerank_candidates = all_results[:max(20, n_results * 4)]
    if len(rerank_candidates) > n_results:
        top = memory_rerank.rerank(query, rerank_candidates, top_k=n_results)
    else:
        top = rerank_candidates

    # Stage 2: record access for returned chunks
    if top:
        chunk_ids = [r["chunk_id"] for r in top]
        wing_map = {r["chunk_id"]: (r["wing"], r["source_file"]) for r in top}
        try:
            memory_aging.record_access(chunk_ids, wing_map=wing_map)
        except Exception as e:
            sys.stderr.write(f"[memory_search] aging update failed: {e}\n")
        # V2.1: Log usage events with full query context
        try:
            events = [{
                "chunk_id": r["chunk_id"],
                "wing": r.get("wing", ""),
                "query": query,
                "mode": "isolated",
                "similarity": r.get("similarity"),
                "rerank_score": r.get("rerank_score"),
                "boosted_score": r.get("boosted_score"),
            } for r in top]
            memory_aging.log_usage_events(events)
        except Exception:
            pass

    _log_search(query, "isolated", current_wing, len(top))
    _emit_metric("search", wing=current_wing or "?", hybrid=hybrid,
                 detail_level=detail_level, latency_ms=int((time.time() - _t0) * 1000),
                 n_results=len(top), mode="isolated", status="ok")
    # D003: bump last_search_at for both wings that participated
    try:
        import wing_lifecycle
        if current_wing:
            wing_lifecycle.update_wing_activity(current_wing, "search")
        wing_lifecycle.update_wing_activity("global", "search")
    except Exception:
        pass

    return {
        "query": query,
        "mode": "isolated",
        "current_wing": current_wing,
        "threshold": threshold,
        "detail_level": detail_level,
        "total_found": len(all_results),
        "returned": len(top),
        "results": apply_detail_level(top, detail_level),
    }


def search_all(query, n_results=10, threshold=THRESHOLD_CROSS, detail_level="full", hybrid=False):
    """Cross-project search, grouped by wing.

    detail_level: "index" | "summary" | "full" (default).
    hybrid (Phase 2b): when True, fuse dense + sparse before threshold + grouping.
    """
    _t0 = time.time()
    col = memory_core.get_collection()
    try:
        results = col.query(
            query_texts=[query],
            n_results=n_results * 3,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        return {"query": query, "mode": "all", "error": str(e), "results": []}

    ids = results["ids"][0]
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    # Pre-filter and collect
    candidates = []
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        similarity = round(1 - dist, 3)
        if similarity < threshold:
            continue
        candidates.append({
            "chunk_id": cid,
            "text": doc,
            "wing": meta.get("wing", "unknown"),
            "source_file": meta.get("source_file", ""),
            "source_name": meta.get("source_name", ""),
            "chunk_index": meta.get("chunk_index", 0),
            "similarity": similarity,
            "boosted_score": similarity,
        })

    # Phase 2b: search_all hybrid — sparse cross-wing (wing=None)
    if hybrid:
        sparse_results = _sparse_recall(query, wing=None, n=n_results * 4)
        if sparse_results:
            candidates = _rrf_fuse(candidates, sparse_results, k=60)

    # Apply aging boost + filter archived
    if candidates:
        chunk_ids = [c["chunk_id"] for c in candidates]
        aging_data = memory_aging.get_aging_data(chunk_ids)
        try:
            archived = memory_aging.get_archived_chunk_ids()
        except Exception:
            archived = set()
        for c in candidates:
            aging = aging_data.get(c["chunk_id"], {})
            c["boosted_score"] = round(memory_aging.compute_score(c["similarity"], aging), 3)
            c["access_count"] = aging.get("access_count", 0)
        candidates = [c for c in candidates if c["boosted_score"] > 0 and c["chunk_id"] not in archived]

    # Stage 3: rerank all candidates first
    if len(candidates) > n_results:
        candidates = memory_rerank.rerank(query, candidates, top_k=min(len(candidates), n_results * 3))

    # Global top-N first, then group by wing for display
    def _score(r):
        return r.get("rerank_score", r.get("boosted_score", r["similarity"]))

    candidates.sort(key=lambda r: -_score(r))
    top_candidates = candidates[:n_results]

    grouped = {}
    for c in top_candidates:
        wing = c["wing"]
        if wing not in grouped:
            grouped[wing] = []
        grouped[wing].append(c)

    # Stage 2: record access for all returned
    all_returned = [c for items in grouped.values() for c in items]
    if all_returned:
        chunk_ids = [r["chunk_id"] for r in all_returned]
        wing_map = {r["chunk_id"]: (r["wing"], r["source_file"]) for r in all_returned}
        try:
            memory_aging.record_access(chunk_ids, wing_map=wing_map)
        except Exception as e:
            sys.stderr.write(f"[memory_search] aging update failed: {e}\n")
        # V2.1: Log usage events
        try:
            events = [{
                "chunk_id": r["chunk_id"],
                "wing": r.get("wing", ""),
                "query": query,
                "mode": "all",
                "similarity": r.get("similarity"),
                "rerank_score": r.get("rerank_score"),
                "boosted_score": r.get("boosted_score"),
            } for r in all_returned]
            memory_aging.log_usage_events(events)
        except Exception:
            pass

    total = sum(len(v) for v in grouped.values())
    _log_search(query, "all", None, total)
    _emit_metric("search", wing="*", hybrid=hybrid, detail_level=detail_level,
                 latency_ms=int((time.time() - _t0) * 1000), n_results=total,
                 mode="all", status="ok")
    # D003: bump last_search_at for each wing that returned results
    try:
        import wing_lifecycle
        for wing_name in grouped.keys():
            wing_lifecycle.update_wing_activity(wing_name, "search")
    except Exception:
        pass

    return {
        "query": query,
        "mode": "all",
        "threshold": threshold,
        "detail_level": detail_level,
        "wings_found": list(grouped.keys()),
        "total_returned": total,
        "results_by_wing": apply_detail_level(grouped, detail_level),
        "warning": "Results from different projects - DO NOT copy specific values (API keys, URLs, IDs) across projects",
    }


def relevance_label(similarity):
    if similarity >= RELEVANCE_HIGH:
        return "HIGH"
    elif similarity >= RELEVANCE_MID:
        return "MID"
    else:
        return "LOW"


def format_results_for_human(result):
    lines = []
    mode = result.get("mode", "isolated")

    if mode == "isolated":
        lines.append("=" * 60)
        lines.append(f"  Query: {result['query']}")
        lines.append(f"  Scope: {result.get('current_wing', '?')} + global")
        lines.append(f"  Threshold: {result.get('threshold', 0.5)}")
        lines.append("=" * 60)
        results = result.get("results", [])
        if not results:
            lines.append("\n  No relevant memories found (similarity < 0.5)")
            lines.append("  Tip: try different keywords or use mp-search-all")
            return "\n".join(lines)
        for i, r in enumerate(results, 1):
            label = relevance_label(r["similarity"])
            preview = (r.get("text") or r.get("summary") or r.get("snippet") or "")[:200].replace("\n", " ")
            extras = []
            if r.get("rerank_score") is not None:
                extras.append(f"rerank:{r['rerank_score']:.3f}")
            if r.get("boosted_score") and r.get("access_count", 0) > 0:
                extras.append(f"boost:{r['boosted_score']:.2f}")
                extras.append(f"acc:{r['access_count']}")
            extra_info = (" " + " ".join(extras)) if extras else ""
            lines.append(f"\n  [{i}] [{label} {r['similarity']:.3f}{extra_info}] [{r['wing']}]")
            lines.append(f"      Source: {r['source_name']}")
            lines.append(f"      Preview: {preview}...")

    elif mode == "all":
        lines.append("=" * 60)
        lines.append(f"  Query: {result['query']}")
        lines.append(f"  Cross-project search (threshold: {result.get('threshold', 0.4)})")
        lines.append("=" * 60)
        warning = result.get("warning", "")
        if warning:
            lines.append(f"\n  [WARNING] {warning}")
        grouped = result.get("results_by_wing", {})
        if not grouped:
            lines.append("\n  No relevant memories found")
            return "\n".join(lines)
        for wing, items in grouped.items():
            lines.append(f"\n  --- wing: {wing} ---")
            for r in items:
                label = relevance_label(r["similarity"])
                preview = (r.get("text") or r.get("summary") or r.get("snippet") or "")[:150].replace("\n", " ")
                lines.append(f"    [{label} {r['similarity']:.3f}] {r['source_name']}")
                lines.append(f"      {preview}...")

    elif mode == "single_wing":
        lines.append("=" * 60)
        lines.append(f"  Query: {result['query']}")
        lines.append(f"  Wing: {result.get('wing', '?')}")
        lines.append("=" * 60)
        results = result.get("results", [])
        if not results:
            lines.append("\n  No relevant memories found")
            return "\n".join(lines)
        for i, r in enumerate(results, 1):
            label = relevance_label(r["similarity"])
            preview = (r.get("text") or r.get("summary") or r.get("snippet") or "")[:200].replace("\n", " ")
            lines.append(f"\n  [{i}] [{label} {r['similarity']:.3f}]")
            lines.append(f"      Source: {r['source_name']}")
            lines.append(f"      Preview: {preview}...")

    return "\n".join(lines)


def _log_search(query, mode, wing, count):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "query": query,
            "mode": mode,
            "wing": wing,
            "count": count,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Search local memory")
    parser.add_argument("query", help="search query")
    parser.add_argument("--wing", help="specify wing")
    parser.add_argument("--all", action="store_true", help="cross-project search")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--detail", choices=list(DETAIL_LEVELS), default="full",
                        help="Detail level: index (cheapest) / summary / full (default)")
    parser.add_argument("--hybrid", action="store_true",
                        help="Phase 2b: dense+sparse hybrid retrieval (RRF fusion). Opt-in.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.all:
        threshold = args.threshold if args.threshold is not None else THRESHOLD_CROSS
        result = search_all(args.query, n_results=args.limit, threshold=threshold,
                            detail_level=args.detail, hybrid=args.hybrid)
    elif args.wing:
        threshold = args.threshold if args.threshold is not None else THRESHOLD_DEFAULT
        # single-wing path doesn't currently support hybrid (rare use case);
        # CLI users wanting hybrid should drop --wing.
        results = search_single(args.query, wing=args.wing, n_results=args.limit, threshold=threshold)
        result = {
            "query": args.query,
            "mode": "single_wing",
            "wing": args.wing,
            "threshold": threshold,
            "detail_level": args.detail,
            "results": apply_detail_level(results, args.detail),
        }
    else:
        threshold = args.threshold if args.threshold is not None else THRESHOLD_DEFAULT
        result = search_isolated(args.query, n_results=args.limit, threshold=threshold,
                                 detail_level=args.detail, hybrid=args.hybrid)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_results_for_human(result))


if __name__ == "__main__":
    main()
