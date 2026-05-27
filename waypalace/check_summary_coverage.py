#!/usr/bin/env python3
"""check_summary_coverage.py — report ChromaDB summary-metadata coverage.

Output (per-wing + total):
  wing      total  with_summary  coverage%  empty
  global     4983         48      1.0%      4935
  ...
  TOTAL      5806         58      1.0%      5748

Phase 4 success metric: TOTAL coverage ≥ 95% post re-mine.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")


def main() -> int:
    import memory_core
    col = memory_core.get_collection()
    n = col.count()

    batch = 2000
    offset = 0
    per_wing: dict[str, dict[str, int]] = {}
    total = 0
    with_sum = 0
    suspicious_fallbacks = 0  # garbage outputs from LLM
    while True:
        r = col.get(limit=batch, offset=offset, include=["metadatas"])
        metas = r.get("metadatas") or []
        if not metas:
            break
        for m in metas:
            total += 1
            w = (m or {}).get("wing", "?")
            slot = per_wing.setdefault(w, {"total": 0, "with": 0, "empty": 0})
            slot["total"] += 1
            s = (m or {}).get("summary")
            if s and isinstance(s, str) and s.strip():
                slot["with"] += 1
                with_sum += 1
                low = s.strip().lower()
                if any(t in low for t in ("无法", "i cannot", "i can't", "sorry", "unable")):
                    suspicious_fallbacks += 1
            else:
                slot["empty"] += 1
        if len(metas) < batch:
            break
        offset += batch

    print(f"{'wing':<28} {'total':>6} {'with':>6} {'cov%':>6} {'empty':>6}")
    print("-" * 60)
    for w in sorted(per_wing.keys(), key=lambda k: -per_wing[k]["total"]):
        s = per_wing[w]
        cov = s["with"] / max(s["total"], 1) * 100
        print(f"{w:<28} {s['total']:>6} {s['with']:>6} {cov:>5.1f}% {s['empty']:>6}")
    print("-" * 60)
    cov = with_sum / max(total, 1) * 100
    print(f"{'TOTAL':<28} {total:>6} {with_sum:>6} {cov:>5.1f}% {total - with_sum:>6}")
    print()
    print(f"chromadb.count() = {n}")
    print(f"suspicious LLM-fallback summaries: {suspicious_fallbacks}")

    # Phase 4 success metric
    threshold = 95.0
    if cov >= threshold:
        print(f"\n[PASS] coverage {cov:.1f}% >= {threshold}% target")
        return 0
    else:
        print(f"\n[BELOW TARGET] coverage {cov:.1f}% < {threshold}% target")
        return 1


if __name__ == "__main__":
    sys.exit(main())
