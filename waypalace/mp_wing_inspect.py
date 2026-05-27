#!/usr/bin/env python3
"""mp_wing_inspect.py — D003 Show all chunks in a wing, grouped by source file.

Used during quarterly review to decide which wings (or specific files) to
salvage vs archive. NEVER deletes anything.

Usage:
    mp-wing-inspect <wing>
    mp-wing-inspect <wing> --json
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import wing_lifecycle


def main() -> int:
    ap = argparse.ArgumentParser(prog="mp-wing-inspect")
    ap.add_argument("wing", help="Wing name to inspect")
    ap.add_argument("--limit", type=int, default=200, help="Max chunks to show (default 200)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full-text", action="store_true",
                    help="Show full chunk text (default: 200-char preview)")
    args = ap.parse_args()

    import memory_core
    col = memory_core.get_collection()
    items = col.get(where={"wing": args.wing}, include=["metadatas", "documents"])
    metas = items.get("metadatas", []) or []
    docs = items.get("documents", []) or []
    ids = items.get("ids", []) or []

    if not metas:
        print(f"No chunks found for wing {args.wing!r}", file=sys.stderr)
        return 1

    # Group by source_file
    by_source: dict[str, list[dict]] = defaultdict(list)
    for cid, meta, doc in zip(ids, metas, docs):
        sf = meta.get("source_file", "(unknown)")
        by_source[sf].append({
            "chunk_id": cid,
            "chunk_index": meta.get("chunk_index"),
            "chunk_total": meta.get("chunk_total"),
            "summary": meta.get("summary", ""),
            "source_mtime": meta.get("source_mtime"),
            "text": doc or "",
        })

    if args.json:
        import json as _json
        out = {"wing": args.wing, "total_chunks": len(metas),
               "unique_sources": len(by_source),
               "sources": {sf: chunks for sf, chunks in by_source.items()}}
        print(_json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    import os as _os
    print(f"=== Wing: {args.wing} ===")
    print(f"Total chunks: {len(metas)}  |  Unique sources: {len(by_source)}")
    print()

    shown = 0
    for sf, chunks in sorted(by_source.items()):
        exists = "✓" if _os.path.exists(sf) else "✗"
        print(f"--- {exists} {sf}  ({len(chunks)} chunks) ---")
        chunks.sort(key=lambda c: c["chunk_index"] or 0)
        for c in chunks:
            if shown >= args.limit:
                print(f"  ... (limit {args.limit} reached, {len(metas) - shown} more)")
                return 0
            idx = c["chunk_index"]
            total = c["chunk_total"]
            summary = (c["summary"] or "(no summary)")[:80]
            preview = c["text"].replace("\n", " ").strip()
            if not args.full_text:
                preview = preview[:200] + ("..." if len(preview) > 200 else "")
            print(f"  [{idx}/{total}] {c['chunk_id'][:40]}")
            print(f"    summary: {summary}")
            print(f"    text:    {preview}")
            shown += 1
        print()

    print(f"Next: if this wing is salvage-worthy, copy valuable chunks into a new .md under")
    print(f"      ~/.claude/projects/-user/memory/salvaged_{args.wing}_<date>.md")
    print(f"      Then mp-wing-archive {args.wing} → mp-wing-delete {args.wing} --confirm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
