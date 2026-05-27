#!/usr/bin/env python3
"""wing_meta_backfill.py — One-shot fill wing_meta from existing chromadb.

Run once when D003 is first deployed. Idempotent (uses INSERT OR IGNORE +
UPDATE), so it's safe to re-run if you want to refresh chunk_count.

Usage:
    python wing_meta_backfill.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import wing_lifecycle


def _infer_source_dir(metas: list[dict]) -> str | None:
    """From a wing's chunk metadata, guess the source directory it came from.

    Returns the most common parent path 2 levels up from source_file, or None
    if no source_file metadata exists.
    """
    from collections import Counter
    parents = Counter()
    for m in metas:
        sf = m.get("source_file") or ""
        if not sf:
            continue
        # ~/.claude/projects/-user-workspace-<project-a>/memory/foo.md
        # → -user-workspace-<project-a>
        parts = sf.split("/")
        if "projects" in parts:
            idx = parts.index("projects")
            if idx + 1 < len(parts):
                parents[parts[idx + 1]] += 1
    if not parents:
        return None
    return parents.most_common(1)[0][0]


def backfill(dry_run: bool = False) -> dict:
    import memory_core
    col = memory_core.get_collection()

    # Get all chunks grouped by wing
    from collections import defaultdict
    print("Scanning chromadb...", file=sys.stderr)
    all_chunks = col.get(include=["metadatas"])
    metas = all_chunks.get("metadatas", []) or []
    by_wing: dict[str, list] = defaultdict(list)
    for m in metas:
        wing = m.get("wing", "?")
        by_wing[wing].append(m)

    print(f"Found {len(by_wing)} wings, {len(metas)} total chunks", file=sys.stderr)

    summary = {"wings_processed": 0, "wings_inserted": 0, "wings_updated": 0}

    for wing, items in sorted(by_wing.items(), key=lambda x: -len(x[1])):
        chunk_count = len(items)
        # last source_mtime = best approximation for last_mine_at
        mtimes = []
        for m in items:
            mt = m.get("source_mtime")
            if mt:
                try:
                    mtimes.append(float(mt))
                except (TypeError, ValueError):
                    pass
        last_mine_unix = int(max(mtimes)) if mtimes else None

        # filed_at as fallback (chunk insertion time)
        if last_mine_unix is None:
            filed_at_strs = [m.get("filed_at") for m in items if m.get("filed_at")]
            if filed_at_strs:
                # Try ISO format parse
                import datetime
                parsed = []
                for s in filed_at_strs:
                    try:
                        dt = datetime.datetime.fromisoformat(str(s))
                        parsed.append(dt.timestamp())
                    except (TypeError, ValueError):
                        pass
                if parsed:
                    last_mine_unix = int(max(parsed))

        source_dir = _infer_source_dir(items)

        # Audit source-file health to decide source_machine
        # If ≥80% sources don't exist locally, it's likely cross-machine restore
        sources = set(m.get("source_file") for m in items if m.get("source_file"))
        if sources:
            missing = sum(1 for s in sources if not os.path.exists(s))
            missing_ratio = missing / len(sources)
            source_machine = "<backup-volume-restore>" if missing_ratio >= 0.8 else "<workstation>"
        else:
            source_machine = "unknown"

        if dry_run:
            print(f"  [DRY] {wing:30s} chunks={chunk_count:5d} "
                  f"last_mine={last_mine_unix} src_dir={source_dir!r} "
                  f"machine={source_machine}")
            summary["wings_processed"] += 1
            continue

        # Insert or update
        was_inserted = wing_lifecycle.register_wing(wing, source_dir)
        # Force-update fields (register_wing only INSERTs)
        import sqlite3
        with sqlite3.connect(wing_lifecycle.DB_PATH) as conn:
            conn.execute(
                "UPDATE wing_meta SET source_dir = COALESCE(?, source_dir), "
                "chunk_count = ?, last_mine_at = COALESCE(last_mine_at, ?), "
                "source_machine = ? "
                "WHERE wing_name = ?",
                (source_dir, chunk_count, last_mine_unix, source_machine, wing),
            )
            conn.commit()

        print(f"  {'INSERT' if was_inserted else 'UPDATE'} {wing:30s} "
              f"chunks={chunk_count:5d} last_mine={last_mine_unix} "
              f"src_dir={source_dir!r} machine={source_machine}")
        summary["wings_processed"] += 1
        if was_inserted:
            summary["wings_inserted"] += 1
        else:
            summary["wings_updated"] += 1

    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = ap.parse_args()

    print(f"{'DRY-RUN ' if args.dry_run else ''}Backfilling wing_meta from chromadb...\n")
    summary = backfill(dry_run=args.dry_run)
    print(f"\nSummary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
