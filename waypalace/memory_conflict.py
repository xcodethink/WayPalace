#!/usr/bin/env python3
"""
memory_conflict.py - AI-based conflict detection (V2.4)

When two chunks have 0.80-0.92 similarity (close but not duplicate),
use an LLM to determine if they semantically contradict each other.

Queue-based design:
  - During mine: potential conflicts are queued to a JSONL file (fast, non-blocking)
  - Cron job: processes the queue with AI calls, records results in chunk_conflicts

Model selection:
  - Prefer Claude Haiku (cheap + fast) via Anthropic API if ANTHROPIC_API_KEY set
  - Fall back to no-op (skip AI, just leave in queue) if unavailable
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_aging

QUEUE_PATH = Path(os.path.expanduser("~/.mempalace-zh/conflict_queue.jsonl"))
PROCESSED_LOG = Path(os.path.expanduser("~/.mempalace-zh/logs/conflict_processed.log"))
BATCH_LIMIT = 30  # max conflicts to process per cron run
MIN_AGE_MIN = 5   # wait 5 min before processing (batch up related writes)


def queue_conflicts(conflicts: list[dict]):
    """Append potential conflicts to the queue file (non-blocking)."""
    if not conflicts:
        return
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("a", encoding="utf-8") as f:
        for c in conflicts:
            f.write(json.dumps({
                **c,
                "queued_at": time.time(),
            }, ensure_ascii=False) + "\n")


def _read_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    items = []
    with QUEUE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items


def _write_queue(items: list[dict]):
    if not items:
        if QUEUE_PATH.exists():
            QUEUE_PATH.unlink()
        return
    with QUEUE_PATH.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _call_claude(prompt: str, system: str, max_tokens: int = 400) -> str | None:
    """Call Anthropic Claude Haiku. Returns text or None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import urllib.request
        req_body = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=req_body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        blocks = data.get("content", [])
        for b in blocks:
            if b.get("type") == "text":
                return b.get("text", "")
        return None
    except Exception:
        return None


CONFLICT_SYSTEM = """You are a memory curator. Given two memory chunks, determine if they contradict each other.

Respond with ONLY a JSON object (no prose, no markdown fencing):
{
  "verdict": "COMPATIBLE" | "CONFLICT" | "SUPERSEDED",
  "conflict_type": "factual" | "version" | "deprecated" | null,
  "severity": "low" | "medium" | "high" | null,
  "description": "one-sentence reason"
}

Rules:
- COMPATIBLE: No contradiction, they cover different aspects or agree
- CONFLICT: They make claims that cannot both be true (e.g. different values for same config, contradictory advice)
- SUPERSEDED: One is clearly a newer/more-complete version of the other
- Use "version" conflict_type when values changed over time (e.g. port number changed)
- Use "factual" for direct contradictions in claims
- Use "deprecated" when one describes an old approach that is no longer valid"""


def _analyze_one(item: dict) -> dict | None:
    """Use AI to analyze a conflict pair. Returns verdict dict or None."""
    a_text = item.get("existing_text", "")[:1500]
    b_text = item.get("incoming_text", "")[:1500]
    prompt = f"Memory A:\n{a_text}\n\n---\n\nMemory B:\n{b_text}"
    reply = _call_claude(prompt, CONFLICT_SYSTEM)
    if not reply:
        return None
    reply = reply.strip()
    # Strip markdown fencing if present
    if reply.startswith("```"):
        reply = reply.strip("`")
        reply = reply.split("\n", 1)[1] if "\n" in reply else reply
        reply = reply.rsplit("```", 1)[0] if "```" in reply else reply
    try:
        return json.loads(reply)
    except Exception:
        return None


def process_queue(max_items: int = BATCH_LIMIT) -> dict:
    """
    Process queued potential conflicts. Non-destructive — if AI call fails,
    item stays in queue for retry.
    """
    queue = _read_queue()
    if not queue:
        return {"processed": 0, "conflicts_recorded": 0, "remaining": 0}

    now_ts = time.time()
    ready = [q for q in queue if (now_ts - q.get("queued_at", 0)) >= MIN_AGE_MIN * 60]
    still_waiting = [q for q in queue if q not in ready]

    to_process = ready[:max_items]
    unprocessed = ready[max_items:]

    processed = 0
    conflicts_recorded = 0
    kept_for_retry = []

    for item in to_process:
        verdict = _analyze_one(item)
        processed += 1
        if verdict is None:
            # AI unavailable → keep for next run, but cap retries (7 days)
            age_days = (now_ts - item.get("queued_at", 0)) / 86400
            if age_days < 7:
                kept_for_retry.append(item)
            continue

        v_type = verdict.get("verdict")
        if v_type == "CONFLICT":
            memory_aging.record_conflict(
                chunk_a_id=item["existing_id"],
                chunk_b_id=item["incoming_id"],
                wing=item.get("wing", ""),
                conflict_type=verdict.get("conflict_type", "factual"),
                severity=verdict.get("severity", "medium"),
                description=verdict.get("description", ""),
            )
            conflicts_recorded += 1
        elif v_type == "SUPERSEDED":
            # Mark older as superseded
            try:
                conn = memory_aging.get_db()
                conn.execute(
                    "UPDATE chunk_aging SET superseded_by = ? WHERE chunk_id = ?",
                    (item["incoming_id"], item["existing_id"]),
                )
                conn.commit()
            except Exception:
                pass
        # COMPATIBLE → nothing to do

    # Rewrite queue with items still waiting + retry items + unprocessed
    _write_queue(still_waiting + unprocessed + kept_for_retry)

    PROCESSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSED_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} processed={processed} conflicts={conflicts_recorded} remaining={len(still_waiting) + len(unprocessed) + len(kept_for_retry)}\n")

    return {
        "processed": processed,
        "conflicts_recorded": conflicts_recorded,
        "remaining": len(still_waiting) + len(unprocessed) + len(kept_for_retry),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("process", help="Process conflict queue")
    sub.add_parser("status", help="Show queue status")
    sub.add_parser("list", help="List unresolved conflicts")
    args = parser.parse_args()

    if args.cmd == "process":
        r = process_queue()
        print(f"Processed: {r['processed']}, Recorded conflicts: {r['conflicts_recorded']}, Remaining: {r['remaining']}")
    elif args.cmd == "status":
        q = _read_queue()
        print(f"Queue size: {len(q)}")
        if q:
            print(f"Oldest: {time.strftime('%Y-%m-%d %H:%M', time.localtime(min(i.get('queued_at', 0) for i in q)))}")
    elif args.cmd == "list":
        for c in memory_aging.get_unresolved_conflicts(limit=50):
            print(f"[{c['severity']}] {c['conflict_type']} — {c['description'][:100]}")
            print(f"  A={c['chunk_a_id']} B={c['chunk_b_id']}")
    else:
        parser.print_help()
