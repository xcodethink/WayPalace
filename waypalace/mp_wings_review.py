#!/usr/bin/env python3
"""mp_wings_review.py — D003 Wing health review CLI.

Lists all wings classified as active / dormant / stale / orphan, with
chunk counts, last activity, and source-file health signals. NEVER
auto-deletes — only reports.

Usage:
    mp-wings-review                 # 4-tier report
    mp-wings-review --wing <name>   # single wing detail
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import wing_lifecycle


STATUS_ORDER = ["active", "dormant", "stale", "orphan"]
STATUS_DESC = {
    "active": "ACTIVE (90 天内有活动)",
    "dormant": "DORMANT (90-180 天无活动)",
    "stale": "STALE (180-365 天无活动)",
    "orphan": "ORPHAN (365+ 天 OR 源缺失 >=80%)",
}


def _human_age(ts: int | None, now: int) -> str:
    if not ts:
        return "never"
    delta = now - ts
    days = delta // 86400
    if days < 1:
        h = delta // 3600
        return f"{h}h ago"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _live_chunk_count(wing: str) -> int:
    """Live query chromadb for wing's actual chunk count."""
    try:
        import memory_core
        col = memory_core.get_collection()
        items = col.get(where={"wing": wing}, include=[])
        return len(items.get("ids", []))
    except Exception:
        return -1


def main() -> int:
    ap = argparse.ArgumentParser(prog="mp-wings-review")
    ap.add_argument("--wing", help="Show detail for one wing only")
    ap.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = ap.parse_args()

    now = int(time.time())
    rows = wing_lifecycle.list_wings(include_archived=False)

    if args.wing:
        target = next((r for r in rows if r["wing_name"] == args.wing), None)
        if not target:
            print(f"Wing {args.wing!r} not found in wing_meta", file=sys.stderr)
            return 1
        audit = wing_lifecycle.audit_wing_sources(args.wing)
        status = wing_lifecycle.classify_status(target, now,
                                                missing_ratio=audit["missing_ratio"],
                                                asset_exists=audit["developer_dir_exists"])
        if args.json:
            import json as _json
            print(_json.dumps({**target, **audit, "status": status},
                              ensure_ascii=False, indent=2, default=str))
            return 0
        print(f"=== Wing detail: {args.wing} ===")
        print(f"  Status:           {status}")
        print(f"  Source dir:       {target.get('source_dir')}")
        print(f"  Source machine:   {target.get('source_machine')}")
        print(f"  Chunks (meta):    {target.get('chunk_count')}")
        print(f"  Chunks (live):    {audit['chunks']}")
        print(f"  Unique sources:   {audit['unique_sources']}")
        print(f"  Sources existing: {audit['sources_exist']}")
        print(f"  Sources missing:  {audit['sources_missing']} ({audit['missing_ratio']*100:.0f}%)")
        print(f"  Last mine:        {_human_age(target.get('last_mine_at'), now)}")
        print(f"  Last search:      {_human_age(target.get('last_search_at'), now)}")
        print(f"  Created at:       {datetime.datetime.fromtimestamp(target['created_at']).strftime('%Y-%m-%d %H:%M')}")
        return 0

    # Full review — compute audit + classify for each wing
    print(f"=== Wings Health Report ({datetime.date.today().isoformat()}) ===")
    print(f"Total wings: {len(rows)}")
    print()

    classified: dict[str, list] = {s: [] for s in STATUS_ORDER}
    for row in rows:
        audit = wing_lifecycle.audit_wing_sources(row["wing_name"])
        status = wing_lifecycle.classify_status(row, now,
                                                missing_ratio=audit["missing_ratio"],
                                                asset_exists=audit["developer_dir_exists"])
        # D004 P0-H: write live count back to wing_meta cache (free piggyback
        # on the query we just did, fixes drift without extra IO).
        wing_lifecycle.sync_chunk_count(row["wing_name"], audit["chunks"])
        # Also refresh the in-memory row for accurate display
        merged = {**row, **audit, "status": status, "chunk_count": audit["chunks"]}
        classified[status].append(merged)

    if args.json:
        import json as _json
        print(_json.dumps(classified, ensure_ascii=False, indent=2, default=str))
        return 0

    for status in STATUS_ORDER:
        items = classified[status]
        if not items:
            continue
        # Sort: active by last_active desc; others by chunk count desc
        if status == "active":
            items.sort(key=lambda r: -(r.get("last_mine_at") or 0))
        else:
            items.sort(key=lambda r: -r["chunks"])

        flag = {"active": "", "dormant": "▼ review 时关注",
                "stale": "◆ 建议归档", "orphan": "✗ 强烈建议清理"}[status]
        print(f"--- {STATUS_DESC[status]}  ({len(items)} wings)  {flag} ---")
        hdr = f"  {'Wing':<28}{'Chunks':>7}  {'LastMine':<12}{'LastSrch':<12}{'Missing':<10}{'DevDir':<8}Machine"
        print(hdr)
        for r in items:
            wing = r["wing_name"][:27]
            chunks = r["chunks"]
            last_mine = _human_age(r.get("last_mine_at"), now)
            last_search = _human_age(r.get("last_search_at"), now)
            missing = f"{r['sources_missing']}/{r['unique_sources']}" if r["unique_sources"] else "—"
            dev = "alive" if r.get("developer_dir_exists") else "gone"
            machine = r.get("source_machine", "?")
            print(f"  {wing:<28}{chunks:>7}  {last_mine:<12}{last_search:<12}{missing:<10}{dev:<8}{machine}")
        print()

    print("Next steps:")
    print("  1. Review STALE / ORPHAN wings — for each, run `mp-wing-inspect <wing>`")
    print("  2. Salvage valuable info (write a new .md to ~/.claude/projects/-user/memory/)")
    print("  3. mp-wing-archive <wing>  → mp-wing-delete <wing> --confirm")
    next_review = datetime.date.today().replace(day=1)
    next_review = next_review.replace(month=((next_review.month - 1) // 3 + 1) * 3 + 1) \
        if next_review.month % 3 == 0 else next_review.replace(month=next_review.month + (3 - next_review.month % 3))
    print(f"  Next quarterly review reminder: {next_review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
