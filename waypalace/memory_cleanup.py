#!/usr/bin/env python3
"""
memory_cleanup.py - Weekly maintenance cron (V2.6)

Tasks:
  1. Recompute vitality scores for all chunks
  2. Auto-transition active → dormant → archived based on vitality
  3. Process conflict detection queue (calls memory_conflict.process_queue)
  4. Vacuum SQLite databases
  5. Write weekly report to logs/

Scheduled: Sundays at 04:00 (after daily 03:00 refresh)
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_aging
import memory_conflict

LOG_DIR = Path(os.path.expanduser("~/.mempalace-zh/logs"))
REPORT_DIR = Path(os.path.expanduser("~/.mempalace-zh/reports"))


def run_cleanup() -> dict:
    """Run all weekly cleanup tasks. Returns report dict."""
    report = {
        "started_at": datetime.now().isoformat(),
        "tasks": {},
    }

    # Task 1: Recompute vitality
    t0 = time.time()
    try:
        n = memory_aging.recompute_all_vitality()
        report["tasks"]["recompute_vitality"] = {
            "chunks_processed": n,
            "duration_sec": round(time.time() - t0, 2),
        }
    except Exception as e:
        report["tasks"]["recompute_vitality"] = {"error": str(e)}

    # Task 2: Auto-archive
    t0 = time.time()
    try:
        r = memory_aging.auto_archive_low_vitality()
        report["tasks"]["auto_archive"] = {
            **r,
            "duration_sec": round(time.time() - t0, 2),
        }
    except Exception as e:
        report["tasks"]["auto_archive"] = {"error": str(e)}

    # Task 3: Process conflict queue
    t0 = time.time()
    try:
        r = memory_conflict.process_queue(max_items=50)
        report["tasks"]["process_conflicts"] = {
            **r,
            "duration_sec": round(time.time() - t0, 2),
        }
    except Exception as e:
        report["tasks"]["process_conflicts"] = {"error": str(e)}

    # Task 4: VACUUM SQLite (reclaim space after archiving)
    t0 = time.time()
    try:
        # Open fresh connection to run VACUUM (can't run on connection with active tx)
        db_path = memory_aging.DB_PATH
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()
        report["tasks"]["vacuum"] = {
            "status": "ok",
            "duration_sec": round(time.time() - t0, 2),
        }
    except Exception as e:
        report["tasks"]["vacuum"] = {"error": str(e)}

    # Task 5: Health snapshot
    try:
        report["snapshot"] = memory_aging.get_v2_stats()
    except Exception as e:
        report["snapshot"] = {"error": str(e)}

    report["completed_at"] = datetime.now().isoformat()
    return report


def write_report(report: dict):
    """Write JSON report for the week, append one-line summary to log."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_file = REPORT_DIR / f"cleanup_{ts}.json"
    with report_file.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # One-line summary
    summary_parts = [
        f"vitality={report['tasks'].get('recompute_vitality', {}).get('chunks_processed', '?')}",
        f"dormant={report['tasks'].get('auto_archive', {}).get('dormant', 0)}",
        f"archived={report['tasks'].get('auto_archive', {}).get('archived', 0)}",
        f"conflicts_processed={report['tasks'].get('process_conflicts', {}).get('processed', 0)}",
        f"conflicts_recorded={report['tasks'].get('process_conflicts', {}).get('conflicts_recorded', 0)}",
    ]
    log_line = f"{datetime.now().isoformat()} cleanup: {' '.join(summary_parts)}\n"
    with (LOG_DIR / "cleanup.log").open("a", encoding="utf-8") as f:
        f.write(log_line)

    return report_file


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN ===")
        print("Would run: recompute_vitality, auto_archive, process_conflicts, vacuum")
        stats = memory_aging.get_v2_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        sys.exit(0)

    report = run_cleanup()
    report_file = write_report(report)

    if not args.quiet:
        print(f"Cleanup complete. Report: {report_file}")
        for name, task in report["tasks"].items():
            print(f"  {name}: {task}")
