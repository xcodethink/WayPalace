#!/usr/bin/env python3
"""sparse_sync.py — Keep sparse_store in lock-step with chromadb.

Two-phase sync (idempotent, safe to call hourly):
  1. Cleanup: remove sparse rows whose chunk_id is no longer in chromadb
  2. Fill:    embed (bge-m3 sparse only) any chromadb chunk not yet in sparse

Designed for Tier 1 hourly batch (refresh-memory.sh). When nothing to do,
exits in <2s (no bge-m3 load). When work needed, loads bge-m3 (60s cold,
instant warm) and processes incrementally.

Replaces ad-hoc invocations of reembed_corpus.py --resume +
sparse_orphan_cleanup.py.

Usage:
    python sparse_sync.py            # actually sync
    python sparse_sync.py --dry-run  # report only
    python sparse_sync.py --quiet    # minimal output for cron
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")

SPARSE_DB = "${WAYPALACE_DATA}/sparse.sqlite3"


def _log(quiet: bool, msg: str) -> None:
    if not quiet:
        print(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    t_start = time.time()
    _log(args.quiet, f"[{time.strftime('%H:%M:%S')}] sparse_sync start")

    # ---------- Read both stores ----------
    import memory_core
    col = memory_core.get_collection()
    t0 = time.time()
    all_data = col.get(include=["documents", "metadatas"])
    chromadb_ids = list(all_data["ids"])
    chromadb_id_set = set(chromadb_ids)
    docs = all_data["documents"]
    metas = all_data["metadatas"]
    _log(args.quiet, f"  chromadb: {len(chromadb_ids)} chunks  ({time.time()-t0:.1f}s)")

    conn = sqlite3.connect(SPARSE_DB)
    sparse_ids = {r[0] for r in conn.execute("SELECT chunk_id FROM chunk_sparse_weights")}
    _log(args.quiet, f"  sparse:   {len(sparse_ids)} chunks")

    orphans = sparse_ids - chromadb_id_set
    missing = chromadb_id_set - sparse_ids
    _log(args.quiet, f"  to remove: {len(orphans)}, to add: {len(missing)}")

    if args.dry_run:
        _log(args.quiet, "[DRY-RUN] No writes.")
        conn.close()
        return 0

    # ---------- Phase 1: cleanup orphans ----------
    deleted = 0
    if orphans:
        orphan_list = sorted(orphans)
        batch = 500
        for i in range(0, len(orphan_list), batch):
            chunk = orphan_list[i:i + batch]
            placeholders = ",".join("?" * len(chunk))
            sql = f"DELETE FROM chunk_sparse_weights WHERE chunk_id IN ({placeholders})"
            cur = conn.execute(sql, chunk)
            deleted += cur.rowcount
        conn.commit()
        _log(args.quiet, f"  deleted {deleted} orphan rows")
    conn.close()

    # ---------- Phase 2: fill missing ----------
    written = 0
    if missing:
        _log(args.quiet, f"[{time.strftime('%H:%M:%S')}] loading bge-m3...")
        t0 = time.time()
        import hybrid_embedder
        import sparse_store
        embedder = hybrid_embedder.get_embedder()
        _log(args.quiet, f"  bge-m3 loaded in {time.time()-t0:.1f}s")

        # Build the working set (only missing chunks)
        idx_map = {cid: i for i, cid in enumerate(chromadb_ids)}
        keep_idx = [idx_map[cid] for cid in missing if cid in idx_map]
        b_ids = [chromadb_ids[i] for i in keep_idx]
        b_docs = [docs[i] for i in keep_idx]
        b_wings = [(metas[i] or {}).get("wing", "global") for i in keep_idx]

        batch = 16
        t0 = time.time()
        for i in range(0, len(b_ids), batch):
            grp_ids = b_ids[i:i + batch]
            grp_docs = b_docs[i:i + batch]
            grp_wings = b_wings[i:i + batch]
            out = embedder.embed(grp_docs, dense=False, sparse=True, colbert=False,
                                 batch_size=batch)
            rows = list(zip(grp_ids, out["sparse"], grp_wings))
            sparse_store.upsert_batch(rows)
            written += len(rows)
        _log(args.quiet, f"  filled {written} missing rows in {time.time()-t0:.1f}s")

    # ---------- Final state ----------
    conn = sqlite3.connect(SPARSE_DB)
    final = conn.execute("SELECT COUNT(*) FROM chunk_sparse_weights").fetchone()[0]
    conn.close()
    _log(args.quiet, f"[{time.strftime('%H:%M:%S')}] sparse_sync done in {time.time()-t_start:.1f}s")
    _log(args.quiet, f"  chromadb {len(chromadb_ids)} == sparse {final}: "
                     f"{'OK' if len(chromadb_ids) == final else 'DRIFT'}")
    return 0 if len(chromadb_ids) == final else 1


if __name__ == "__main__":
    sys.exit(main())
