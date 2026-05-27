#!/usr/bin/env python3
"""sparse_orphan_cleanup.py — Remove sparse_store rows whose chunk_id no
longer exists in chromadb.

Why this exists (D004 P0-A.2): D001 Phase 4 orphan cleanup removed 4925
chunks from chromadb but the sparse store wasn't synchronized. After D003
v1.1 cleanups + repeated mp-mine runs we accumulated thousands of orphan
sparse rows that bloat the DB and slow hybrid retrieval scans.

This is a pure cleanup — sparse rows are regenerable from chromadb via
reembed_corpus.py, so deletion is safe.

Usage:
    python sparse_orphan_cleanup.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")

SPARSE_DB = "${WAYPALACE_DATA}/sparse.sqlite3"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Show counts, no writes")
    args = ap.parse_args()

    import memory_core
    col = memory_core.get_collection()
    chromadb_ids = set(col.get(include=[])["ids"])

    conn = sqlite3.connect(SPARSE_DB)
    sparse_ids = {r[0] for r in conn.execute("SELECT chunk_id FROM chunk_sparse_weights")}
    orphans = sorted(sparse_ids - chromadb_ids)
    chromadb_only = chromadb_ids - sparse_ids

    print(f"chromadb chunks:   {len(chromadb_ids)}")
    print(f"sparse chunks:     {len(sparse_ids)}")
    print(f"orphan in sparse:  {len(orphans)}  (to remove)")
    print(f"missing in sparse: {len(chromadb_only)}  (run reembed_corpus.py --resume to fill)")

    if not orphans:
        print("Nothing to delete.")
        return 0

    if args.dry_run:
        print("\n[DRY-RUN] Pass without --dry-run to actually delete.")
        conn.close()
        return 0

    # Batched parameterised delete (SQLite default 999 host params)
    t0 = time.time()
    deleted = 0
    batch_size = 500
    for i in range(0, len(orphans), batch_size):
        batch = orphans[i:i + batch_size]
        placeholders = ",".join("?" * len(batch))
        sql = f"DELETE FROM chunk_sparse_weights WHERE chunk_id IN ({placeholders})"
        cur = conn.execute(sql, batch)
        deleted += cur.rowcount
    conn.commit()

    remaining = conn.execute("SELECT COUNT(*) FROM chunk_sparse_weights").fetchone()[0]
    conn.close()

    print(f"\nDeleted {deleted} orphan rows in {time.time() - t0:.1f}s")
    print(f"sparse after cleanup: {remaining}")
    print(f"chromadb-only gap now: {len(chromadb_ids - set(r for r in remaining_ids_helper()))}")
    return 0


def remaining_ids_helper():
    """Read-only helper for the final consistency print."""
    conn = sqlite3.connect(SPARSE_DB)
    ids = [r[0] for r in conn.execute("SELECT chunk_id FROM chunk_sparse_weights")]
    conn.close()
    return ids


if __name__ == "__main__":
    sys.exit(main())
