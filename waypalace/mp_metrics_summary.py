#!/usr/bin/env python3
"""mp_metrics_summary.py — Aggregate and display memory system metrics.

Reads JSONL files from ~/.mempalace-zh/metrics/ and emits a human-readable
summary with hook counts, search latency percentiles, mine timings, and
recent errors. No external deps.

Usage:
    mp-metrics-summary [--days N] [--event NAME] [--weekly]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import mp_metrics


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def fmt_pct(num: int, denom: int) -> str:
    if denom == 0:
        return "N/A"
    return f"{num / denom * 100:.1f}%"


def summarize_window(events: list[dict], label: str) -> None:
    print(f"\n=== {label} ===\n")
    if not events:
        print("  (no events in this window)")
        return

    by_event: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_event[e.get("event", "?")].append(e)

    # ---------- Hooks ----------
    # Different hook types have different success semantics:
    #   auto_mine.spawned / session_start.fired → status field ('ok' vs 'fail')
    #   auto_surface → outcome is hit/miss/weak (none is 'fail'; all are normal)
    #   *.skipped → always 'normal' (the skip itself is success)
    hook_keys = sorted(k for k in by_event if k.startswith("hook."))
    if hook_keys:
        print("Hooks:")
        for k in hook_keys:
            items = by_event[k]
            n = len(items)
            short = k.replace("hook.", "")
            if k == "hook.auto_surface":
                hits = sum(1 for x in items if x.get("status") == "hit")
                extra = f" ({fmt_pct(hits, n)} hit rate)" if n > 0 else ""
            elif k.endswith(".skipped") or "skipped" in k:
                extra = ""  # skip is normal outcome, no rate needed
            else:
                fails = sum(1 for x in items if x.get("status") in ("fail", "timeout"))
                if fails > 0:
                    extra = f" ({fmt_pct(fails, n)} fail rate)"
                else:
                    extra = " (all ok)" if n > 0 else ""
            print(f"  {short:<28} : {n:>5}{extra}")
        print()

    # ---------- Search ----------
    search = by_event.get("search", [])
    if search:
        n = len(search)
        hybrid = sum(1 for x in search if x.get("hybrid"))
        dense = n - hybrid
        latencies = sorted(x["latency_ms"] for x in search if isinstance(x.get("latency_ms"), (int, float)))
        wing_counts = Counter(x.get("wing", "?") for x in search)
        detail_counts = Counter(x.get("detail_level", "?") for x in search)
        print("Search:")
        print(f"  Total queries:        {n}")
        print(f"  Hybrid: {hybrid} ({fmt_pct(hybrid, n)}), Dense: {dense} ({fmt_pct(dense, n)})")
        if latencies:
            p50 = percentile(latencies, 0.50)
            p95 = percentile(latencies, 0.95)
            p99 = percentile(latencies, 0.99)
            print(f"  Latency p50/p95/p99:  {p50:.0f}ms / {p95:.0f}ms / {p99:.0f}ms")
        if detail_counts:
            tops = ", ".join(f"{k}={v}" for k, v in detail_counts.most_common())
            print(f"  Detail levels:        {tops}")
        if wing_counts:
            tops = ", ".join(f"{k}={v}" for k, v in wing_counts.most_common(8))
            print(f"  By wing (top 8):      {tops}")
        print()

    # ---------- Mine ----------
    mine = by_event.get("mine", [])
    if mine:
        n = len(mine)
        total_ms = [x["total_ms"] for x in mine if isinstance(x.get("total_ms"), (int, float))]
        llm_ms = [x["llm_summarize_ms"] for x in mine if isinstance(x.get("llm_summarize_ms"), (int, float))]
        chunks_added = sum(x.get("chunks", 0) for x in mine)
        print("Mine:")
        print(f"  Total runs:           {n}")
        if total_ms:
            avg_total = sum(total_ms) / len(total_ms) / 1000
            print(f"  Avg total time:       {avg_total:.1f}s")
        if llm_ms:
            llm_sorted = sorted(llm_ms)
            llm_p95 = percentile(llm_sorted, 0.95)
            avg_llm = sum(llm_ms) / len(llm_ms) / 1000
            print(f"  LLM summarize avg:    {avg_llm:.1f}s  (p95: {llm_p95 / 1000:.1f}s)")
        files = sum(1 for _ in mine)
        if files:
            print(f"  Chunks added:         {chunks_added}  (avg {chunks_added / files:.1f}/run)")
        print()

    # ---------- LLM assist ----------
    llm = [e for k, evs in by_event.items() if k.startswith("llm.") for e in evs]
    if llm:
        n = len(llm)
        ok = sum(1 for x in llm if x.get("status", "ok") == "ok")
        latencies = sorted(x["latency_ms"] for x in llm if isinstance(x.get("latency_ms"), (int, float)))
        print("LLM assist:")
        print(f"  Total calls:          {n}  ({fmt_pct(ok, n)} success)")
        if latencies:
            p50 = percentile(latencies, 0.50)
            p95 = percentile(latencies, 0.95)
            print(f"  Latency p50/p95:      {p50:.0f}ms / {p95:.0f}ms")
        print()

    # ---------- Errors ----------
    errors = [e for e in events if e.get("status") in ("fail", "timeout")]
    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors[-10:]:
            ts = e.get("ts", "?")[:19]
            ev = e.get("event", "?")
            st = e.get("status", "?")
            err = (e.get("error") or "")[:80]
            print(f"  {ts}  {ev:<24} {st:<9} {err!r}")
        if len(errors) > 10:
            print(f"  (showing last 10 of {len(errors)})")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(prog="mp-metrics-summary")
    ap.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
    ap.add_argument("--event", type=str, default=None, help="Filter to single event type")
    ap.add_argument("--weekly", action="store_true", help="Compare this week vs last week")
    ap.add_argument("--no-cleanup", action="store_true", help="Skip lazy cleanup of old files")
    args = ap.parse_args()

    if not args.no_cleanup:
        mp_metrics.cleanup_old()

    if args.weekly:
        all_events = mp_metrics.read_events(days=14)
        now = datetime.now()
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)

        def in_window(e: dict, start: datetime, end: datetime) -> bool:
            ts = e.get("ts", "")
            try:
                dt = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                return False
            return start <= dt < end

        last_week = [e for e in all_events if in_window(e, two_weeks_ago, week_ago)]
        this_week = [e for e in all_events if in_window(e, week_ago, now)]

        print(f"\nMemory System Metrics — weekly comparison")
        print(f"This week: {len(this_week)} events  |  Last week: {len(last_week)} events")
        summarize_window(last_week, f"Last week ({two_weeks_ago:%Y-%m-%d} to {week_ago:%Y-%m-%d})")
        summarize_window(this_week, f"This week ({week_ago:%Y-%m-%d} to {now:%Y-%m-%d})")
        return 0

    events = mp_metrics.read_events(days=args.days)
    if args.event:
        events = [e for e in events if e.get("event") == args.event]
        label = f"Memory System Metrics — event={args.event}, last {args.days} days"
    else:
        label = f"Memory System Metrics (last {args.days} days)"

    summarize_window(events, label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
