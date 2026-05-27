#!/usr/bin/env python3
"""mp_wing_archive.py — D003 Soft-delete + dump a wing's chunks to JSONL.

This is the safety step before `mp-wing-delete`:
  1. Dumps ALL chunks (metadata + text) to ~/.mempalace-zh/archive/<wing>-YYYYMMDD.jsonl
  2. Marks wing_meta.archived = 1 (soft-delete; chromadb/sparse data unchanged)

After running this, you can:
  - Review the jsonl, cherry-pick valuable info, write to global wing
  - Then mp-wing-delete <wing> --confirm to physically purge

Usage:
    mp-wing-archive <wing>
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import wing_lifecycle

ARCHIVE_DIR = "${WAYPALACE_DATA}/archive"


def main() -> int:
    ap = argparse.ArgumentParser(prog="mp-wing-archive")
    ap.add_argument("wing", help="Wing name to archive")
    args = ap.parse_args()

    wing = args.wing

    # Sanity: wing must exist in wing_meta
    row = wing_lifecycle.get_wing(wing)
    if not row:
        print(f"Wing {wing!r} not found in wing_meta", file=sys.stderr)
        return 1
    if row.get("archived"):
        print(f"Wing {wing!r} is already archived (at {row.get('archived_at')})", file=sys.stderr)
        return 1

    # Dump chunks
    import memory_core
    col = memory_core.get_collection()
    items = col.get(where={"wing": wing}, include=["metadatas", "documents"])
    metas = items.get("metadatas", []) or []
    docs = items.get("documents", []) or []
    ids = items.get("ids", []) or []

    if not metas:
        print(f"No chunks to archive for wing {wing!r} — will still soft-delete the wing_meta row")

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    today = datetime.date.today().isoformat().replace("-", "")
    out_path = os.path.join(ARCHIVE_DIR, f"{wing}-{today}.jsonl")

    n = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for cid, meta, doc in zip(ids, metas, docs):
            record = {"chunk_id": cid, "metadata": dict(meta), "document": doc}
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            n += 1

    size_kb = os.path.getsize(out_path) // 1024
    print(f"Archived {n} chunks → {out_path} ({size_kb} KB)")

    # Soft-delete wing_meta row
    wing_lifecycle.mark_archived(wing)
    print(f"wing_meta.archived = 1 for {wing!r}")
    print()
    print("Next steps:")
    print(f"  1. Review the dump: less {out_path}")
    print(f"  2. Salvage valuable info → write to ~/.claude/projects/-user/memory/salvaged_{wing}_{today}.md")
    print(f"  3. mp-wing-delete {wing} --confirm  (physical purge of chromadb + sparse)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
