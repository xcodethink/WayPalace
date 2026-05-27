#!/usr/bin/env python3
"""reembed_corpus.py — One-shot: generate sparse lexical_weights for every
existing ChromaDB chunk and write to sparse_store.sqlite3.

This does NOT re-generate dense embeddings (those stay in ChromaDB from the
original sentence-transformers run). It only fills the parallel sparse store
so that Phase 2b hybrid retrieval (dense + sparse + RRF) becomes possible.

Estimated runtime on M5 Max:
  - 5782 chunks / 16 batch = ~361 batches × ~0.5s/batch = ~3 min compute
  - + bge-m3 cold load ~60s (first run only)
  Total: ~4-5 min for first run, faster if model warm.

Safety:
  - chromadb chunks are read-only; we only read documents + metadata.
  - sparse_store uses upsert so re-running is idempotent.
  - mempalace daemon does NOT need to stop (we don't touch chromadb).
  - mine.lock NOT taken (we're not mining, just embedding extra signal).

Usage:
  ${WAYPALACE_VENV}/bin/python ~/.claude/scripts/reembed_corpus.py
  # Optional flags:
  #   --batch-size 32          # default 16
  #   --limit 100              # process only first N (testing)
  #   --resume                 # skip chunk_ids already in sparse store
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory_core
import sparse_store
import hybrid_embedder


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--limit", type=int, default=0, help="Process only first N (0=all)")
    p.add_argument("--resume", action="store_true",
                   help="Skip chunks already in sparse_store")
    args = p.parse_args()

    print(f"[{time.strftime('%H:%M:%S')}] Loading chromadb collection...")
    col = memory_core.get_collection()
    total = col.count()
    print(f"  total chunks in chromadb: {total}")

    print(f"[{time.strftime('%H:%M:%S')}] Pulling all chunk documents + metadata...")
    t0 = time.time()
    data = col.get(include=["documents", "metadatas"])
    ids = data["ids"]
    docs = data["documents"]
    metas = data["metadatas"]
    print(f"  pulled {len(ids)} chunks in {time.time()-t0:.1f}s")

    # Resume: filter out chunks already in sparse store
    if args.resume:
        from sparse_store import _conn
        conn = _conn()
        existing = {row[0] for row in conn.execute("SELECT chunk_id FROM chunk_sparse_weights")}
        conn.close()
        keep_idx = [i for i, cid in enumerate(ids) if cid not in existing]
        print(f"  resume mode: {len(existing)} already done, {len(keep_idx)} remaining")
        ids = [ids[i] for i in keep_idx]
        docs = [docs[i] for i in keep_idx]
        metas = [metas[i] for i in keep_idx]

    if args.limit:
        ids = ids[:args.limit]
        docs = docs[:args.limit]
        metas = metas[:args.limit]
        print(f"  limit={args.limit}: processing first {len(ids)} chunks")

    if not ids:
        print("  nothing to do.")
        return 0

    print(f"[{time.strftime('%H:%M:%S')}] Loading hybrid embedder (bge-m3)...")
    t0 = time.time()
    embedder = hybrid_embedder.get_embedder()
    print(f"  loaded in {time.time()-t0:.1f}s")

    print(f"[{time.strftime('%H:%M:%S')}] Encoding {len(ids)} chunks (sparse only)...")
    t0 = time.time()
    batch = args.batch_size
    written = 0
    for i in range(0, len(ids), batch):
        b_ids = ids[i:i+batch]
        b_docs = docs[i:i+batch]
        b_wings = [m.get("wing", "global") for m in metas[i:i+batch]]
        out = embedder.embed(b_docs, dense=False, sparse=True, colbert=False,
                              batch_size=batch)
        rows = list(zip(b_ids, out["sparse"], b_wings))
        sparse_store.upsert_batch(rows)
        written += len(rows)
        if (i // batch) % 10 == 0 or i + batch >= len(ids):
            elapsed = time.time() - t0
            rate = written / elapsed if elapsed > 0 else 0
            eta = (len(ids) - written) / rate if rate > 0 else 0
            print(f"  [{time.strftime('%H:%M:%S')}] {written}/{len(ids)} "
                  f"({100*written/len(ids):.1f}%)  rate={rate:.1f}/s  eta={eta:.0f}s")
    total_time = time.time() - t0
    print(f"[{time.strftime('%H:%M:%S')}] Done. Encoded {written} chunks in {total_time:.1f}s "
          f"({written/total_time:.1f}/s)")

    print()
    print("=== Final sparse_store stats ===")
    stats = sparse_store.get_stats()
    print(f"  total: {stats['total']}, wings: {stats['wings']}")
    for w, n in stats["top_wings"].items():
        print(f"    {w:30s}  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
