#!/usr/bin/env python3
"""mp_metrics.py — Local-only event metrics for memory system.

Lightweight JSONL append-only logger. Each event is one line:
    {"ts":"2026-05-27T08:34:21","event":"<name>",<fields>}

Design rules:
- fail-silent: any write error swallowed. Metrics must never break the hot
  path it observes.
- append-only: O_APPEND atomic for sub-PIPE_BUF writes on POSIX, so no lock
  needed for single-line JSON records (< 4 KB).
- lazy cleanup: this module never removes old files. mp-metrics-summary
  triggers cleanup before reading, or rotate-logs.sh cron'd separately.
- schema-free: callers pass arbitrary kwargs. Standardized field names
  documented in D002 PRD section C.

Standardized fields (per D002 PRD):
    ts          (auto-injected, ISO-8601 local time)
    event       (positional, dotted namespace e.g. "hook.auto_mine.spawned")
    status      in {ok, fail, skip, timeout}; fail/timeout should include error
    wing        (for mine/search/hook events involving a wing)
    latency_ms  (int, for any timed event)
    error       (<=80 char short description; only when status != ok)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

METRICS_DIR = "${WAYPALACE_DATA}/metrics"
RETENTION_DAYS = 30


def _today_file() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(METRICS_DIR, f"{today}.jsonl")


def _iso_now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def record_event(event: str, **fields) -> None:
    """Append one JSONL record. fail-silent.

    Usage:
        record_event("mine", wing="global", chunks=12, total_ms=1840, status="ok")
        record_event("search", wing="<project-a>", hybrid=True, latency_ms=237, status="ok")
        record_event("hook.auto_mine.spawned", file="/path/to/foo.md", pid=12345)
    """
    try:
        os.makedirs(METRICS_DIR, exist_ok=True)
        record = {"ts": _iso_now(), "event": event}
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(_today_file(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # fail-silent


def cleanup_old(days: int = RETENTION_DAYS) -> int:
    """Delete metrics files older than `days`. Returns count removed.

    Intended to be called by mp-metrics-summary before reading, or by
    rotate-logs.sh. Idempotent and safe to call frequently.
    """
    try:
        cutoff = datetime.now() - timedelta(days=days)
        cutoff_name = cutoff.strftime("%Y-%m-%d")
        removed = 0
        if not os.path.isdir(METRICS_DIR):
            return 0
        for name in os.listdir(METRICS_DIR):
            if not name.endswith(".jsonl"):
                continue
            date_part = name[:-len(".jsonl")]
            if date_part < cutoff_name:
                try:
                    os.remove(os.path.join(METRICS_DIR, name))
                    removed += 1
                except OSError:
                    pass
        return removed
    except Exception:
        return 0


def read_events(days: int = 7) -> list[dict]:
    """Read events from last `days` files. Returns chronological list.

    Used by mp-metrics-summary. Skips unparseable lines silently.
    """
    out: list[dict] = []
    if not os.path.isdir(METRICS_DIR):
        return out
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    files = sorted(
        n for n in os.listdir(METRICS_DIR)
        if n.endswith(".jsonl") and n[:-len(".jsonl")] >= cutoff
    )
    for name in files:
        path = os.path.join(METRICS_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return out


if __name__ == "__main__":
    record_event("test.smoke", message="mp_metrics self-test", status="ok")
    print(f"Wrote test event to: {_today_file()}")
    events = read_events(days=1)
    print(f"Today's events: {len(events)}")
    if events:
        print(f"Latest: {events[-1]}")
