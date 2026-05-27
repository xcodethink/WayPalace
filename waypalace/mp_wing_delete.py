#!/usr/bin/env python3
"""mp_wing_delete.py — D003 Physical purge of a wing from chromadb + sparse.

Hard delete. Requires the wing to be archived first (mp-wing-archive must
have run). Without --confirm, prints what would be deleted and exits.

Usage:
    mp-wing-delete <wing>            # dry-run (shows counts, no delete)
    mp-wing-delete <wing> --confirm  # actually delete
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import wing_lifecycle

SPARSE_DB = "${WAYPALACE_DATA}/sparse.sqlite3"


def _sparse_count(wing: str) -> int:
    try:
        conn = sqlite3.connect(SPARSE_DB)
        cur = conn.execute("SELECT COUNT(*) FROM chunk_sparse_weights WHERE wing = ?", (wing,))
        n = cur.fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def _sparse_delete(wing: str) -> int:
    try:
        conn = sqlite3.connect(SPARSE_DB)
        cur = conn.execute("DELETE FROM chunk_sparse_weights WHERE wing = ?", (wing,))
        n = cur.rowcount
        conn.commit()
        conn.close()
        return n
    except Exception as e:
        print(f"  ! sparse delete failed: {e}", file=sys.stderr)
        return -1


def main() -> int:
    ap = argparse.ArgumentParser(prog="mp-wing-delete")
    ap.add_argument("wing", help="Wing name to physically delete")
    ap.add_argument("--confirm", action="store_true", help="Actually delete (default: dry-run)")
    ap.add_argument("--force", action="store_true",
                    help="Skip archived-first check (DANGEROUS — only if you really want)")
    args = ap.parse_args()

    wing = args.wing

    # Refuse if not archived (unless --force)
    row = wing_lifecycle.get_wing(wing)
    if not row:
        print(f"Wing {wing!r} not in wing_meta (already deleted? Or never existed?)", file=sys.stderr)
        return 1
    if not row.get("archived") and not args.force:
        print(f"REFUSING — wing {wing!r} is not archived (run mp-wing-archive first).", file=sys.stderr)
        print(f"Or pass --force if you know what you're doing.", file=sys.stderr)
        return 2

    # Count chunks in both stores
    import memory_core
    col = memory_core.get_collection()
    items = col.get(where={"wing": wing}, include=[])
    chromadb_count = len(items.get("ids", []))
    sparse_count = _sparse_count(wing)

    print(f"Wing {wing!r} → chromadb={chromadb_count} chunks, sparse={sparse_count} rows")

    if not args.confirm:
        print("\n[DRY-RUN] Pass --confirm to actually delete. Nothing changed.")
        return 0

    # Actual deletion
    deleted_chroma = 0
    try:
        col.delete(where={"wing": wing})
        # re-count to verify
        items_after = col.get(where={"wing": wing}, include=[])
        deleted_chroma = chromadb_count - len(items_after.get("ids", []))
    except Exception as e:
        print(f"  ! chromadb delete failed: {e}", file=sys.stderr)
        return 3

    deleted_sparse = _sparse_delete(wing)

    # Hard-delete wing_meta row
    wing_lifecycle.hard_delete(wing)

    print(f"  ✓ chromadb: deleted {deleted_chroma} chunks")
    print(f"  ✓ sparse:   deleted {deleted_sparse} rows")
    print(f"  ✓ wing_meta: row removed")
    print(f"\nDone. {wing!r} purged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
