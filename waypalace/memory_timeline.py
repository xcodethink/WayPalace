#!/usr/bin/env python3
"""memory_timeline.py — time-based browsing + direct ID lookup.

Two cheap helpers that complement memory_search:

  list_timeline(wing, start, end, limit)
      Browse memory chunks ordered by `filed_at` metadata.
      Useful for "what did I record last week" style queries
      where keyword search would miss the point.

  get_by_ids(ids)
      Fetch the full text of specific chunk_ids in one round-trip.
      Designed to be paired with memory_search(detail_level="index"):
      first scan cheap snippets, then pull full text for the few
      chunks that matter — avoids the 5000-token "open 10 full
      chunks every time" pattern.

Both helpers go through memory_core.get_collection() so they share
the same wing isolation, sensitive-dict protection, and aging system
as the rest of the memory toolkit.
"""
import argparse
import json
import os
import sys
from datetime import datetime

import memory_core


TIMELINE_SNIPPET_CHARS = 120
TIMELINE_DEFAULT_LIMIT = 50
TIMELINE_MAX_LIMIT = 500


def _norm_ts(value):
    """Accept ISO date / datetime / None. Returns ISO string or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).strip()


def list_timeline(wing=None, start=None, end=None, limit=TIMELINE_DEFAULT_LIMIT):
    """Return chunks ordered by `filed_at` desc, optionally filtered by wing/date range.

    Args:
        wing: Restrict to a single wing. None = all wings.
        start: ISO timestamp lower bound (inclusive). e.g. "2026-05-01".
        end:   ISO timestamp upper bound (inclusive). e.g. "2026-05-13T23:59:59".
        limit: Max results to return (capped at TIMELINE_MAX_LIMIT).

    Returns:
        dict with keys: wing, start, end, total_scanned, total_matched, returned, results.
        Each result item: chunk_id, wing, source_name, source_file, filed_at, snippet.
    """
    limit = max(1, min(int(limit or TIMELINE_DEFAULT_LIMIT), TIMELINE_MAX_LIMIT))
    start_ts = _norm_ts(start)
    end_ts = _norm_ts(end)

    col = memory_core.get_collection()
    where = {"wing": wing} if wing else None

    try:
        total_count = col.count() or 1
        kwargs = {"include": ["metadatas", "documents"], "limit": total_count}
        if where:
            kwargs["where"] = where
        raw = col.get(**kwargs)
    except Exception as e:
        return {"error": str(e), "results": []}

    ids = raw.get("ids", []) or []
    metas = raw.get("metadatas", []) or []
    docs = raw.get("documents", []) or []

    matched = []
    for cid, meta, doc in zip(ids, metas, docs):
        filed_at = (meta or {}).get("filed_at", "")
        if start_ts and (not filed_at or filed_at < start_ts):
            continue
        if end_ts and filed_at and filed_at > end_ts:
            continue
        snippet = (doc or "")[:TIMELINE_SNIPPET_CHARS].replace("\n", " ").strip()
        if doc and len(doc) > TIMELINE_SNIPPET_CHARS:
            snippet += "..."
        matched.append({
            "chunk_id": cid,
            "wing": (meta or {}).get("wing", "unknown"),
            "source_name": (meta or {}).get("source_name", ""),
            "source_file": (meta or {}).get("source_file", ""),
            "filed_at": filed_at,
            "snippet": snippet,
        })

    matched.sort(key=lambda x: x.get("filed_at") or "", reverse=True)

    return {
        "wing": wing,
        "start": start_ts,
        "end": end_ts,
        "total_scanned": len(ids),
        "total_matched": len(matched),
        "returned": min(len(matched), limit),
        "results": matched[:limit],
    }


def get_by_ids(ids):
    """Fetch full text by chunk_id list. Cross-wing — caller is responsible
    for not leaking values between projects.
    """
    if not ids:
        return {"requested": 0, "found": 0, "results": []}
    if isinstance(ids, str):
        ids = [ids]

    col = memory_core.get_collection()
    try:
        raw = col.get(ids=list(ids), include=["documents", "metadatas"])
    except Exception as e:
        return {"error": str(e), "requested": len(ids), "found": 0, "results": []}

    out = []
    for cid, doc, meta in zip(
        raw.get("ids", []) or [],
        raw.get("documents", []) or [],
        raw.get("metadatas", []) or [],
    ):
        out.append({
            "chunk_id": cid,
            "wing": (meta or {}).get("wing", "unknown"),
            "source_name": (meta or {}).get("source_name", ""),
            "source_file": (meta or {}).get("source_file", ""),
            "chunk_index": (meta or {}).get("chunk_index", 0),
            "filed_at": (meta or {}).get("filed_at", ""),
            "text": doc or "",
        })

    return {
        "requested": len(ids),
        "found": len(out),
        "results": out,
    }


# ========== CLI ==========

def _cli_timeline(args):
    result = list_timeline(
        wing=args.wing,
        start=args.start,
        end=args.end,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cli_get(args):
    ids = args.ids
    if args.stdin:
        ids = [line.strip() for line in sys.stdin if line.strip()]
    result = get_by_ids(ids)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Memory timeline / direct lookup")
    sub = parser.add_subparsers(dest="cmd", required=True)

    tl = sub.add_parser("timeline", help="List chunks by filed_at")
    tl.add_argument("--wing", help="Restrict to a wing (default: all)")
    tl.add_argument("--start", help="ISO lower bound, e.g. 2026-05-01")
    tl.add_argument("--end", help="ISO upper bound, e.g. 2026-05-13")
    tl.add_argument("--limit", type=int, default=TIMELINE_DEFAULT_LIMIT)
    tl.set_defaults(func=_cli_timeline)

    g = sub.add_parser("get", help="Fetch full text by chunk_id")
    g.add_argument("ids", nargs="*", help="chunk_id values (positional)")
    g.add_argument("--stdin", action="store_true", help="Read ids from stdin, one per line")
    g.set_defaults(func=_cli_get)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
