#!/usr/bin/env python3
"""mp_health.py — Unified health check for the local long-term memory system.

One command shows the status of all components:
  - 3 launchd daemons (mempalace, mlx-llm, memory-refresh, memory-backup, memory-audit)
  - mempalace Unix socket + ping
  - mlx-llm HTTP server + ping
  - ChromaDB chunk/wing counts
  - Last refresh/backup/audit timestamps
  - Symlink graph integrity (skills, commands across 4 profiles)
  - Disk usage of HF cache + mempalace data
  - Recent classify_wing decisions trend

Exit code 0 if all checks pass; 1 if any [FAIL]. Designed for both human
glance (terminal) and machine consumption (--json).

Usage:
  mp-health           # human-readable report
  mp-health --json    # JSON for monitoring scripts
  mp-health --quick   # skip slow checks (LLM ping, chromadb count)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, str(e)


def check_launchd_agents() -> list[dict]:
    expected = [
        "com.user.waypalace.mempalace.daemon",
        "com.user.waypalace.mlx-llm.daemon",
        "com.user.waypalace.memory-refresh.daemon",
        "com.user.waypalace.memory-backup.daemon",
        "com.user.waypalace.memory-audit.daemon",
    ]
    rc, out = _run(["/bin/launchctl", "list"])
    loaded = {line.split()[2] for line in out.splitlines() if len(line.split()) >= 3 and line.split()[2].startswith("com.user.waypalace.")}
    out_list = []
    for label in expected:
        if label in loaded:
            line = next((ln for ln in out.splitlines() if label in ln), "")
            parts = line.split()
            pid = parts[0] if parts and parts[0] != "-" else "-"
            exit_code = parts[1] if len(parts) > 1 else "?"
            out_list.append({"label": label, "ok": True, "pid": pid, "last_exit": exit_code})
        else:
            out_list.append({"label": label, "ok": False, "reason": "not loaded"})
    return out_list


def check_mempalace_socket() -> dict:
    sock_path = os.path.expanduser("~/.mempalace-zh/daemon.sock")
    if not os.path.exists(sock_path):
        return {"ok": False, "reason": "socket missing"}
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(sock_path)
        s.sendall(b'{"cmd":"ping"}\n')
        data = s.recv(4096).decode("utf-8")
        s.close()
        resp = json.loads(data.strip())
        return {"ok": bool(resp.get("ok")), "pid": resp.get("pid"), "uptime_sec": resp.get("uptime_sec")}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def check_mlx_llm_server() -> dict:
    try:
        req = urllib.request.Request("http://127.0.0.1:8081/v1/models")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
        models = [m.get("id") for m in data.get("data", [])]
        return {"ok": True, "models": models}
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        return {"ok": False, "reason": str(e)}


def check_chromadb() -> dict:
    try:
        sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
        import memory_core
        stats = memory_core.get_wing_stats()
        total = memory_core.get_total_count()
        return {
            "ok": True,
            "total_chunks": total,
            "wing_count": len(stats),
            "total_files": sum(s["files"] for s in stats.values()),
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def check_logs_freshness() -> dict:
    """Return last-modified time of key logs."""
    files = {
        "refresh": "~/.mempalace-zh/logs/refresh.log",
        "backup": "~/.mempalace-zh/logs/backup.log",
        "audit_weekly": "~/.mempalace-zh/logs/audit-weekly.log",
        "auto_mine": "~/.mempalace-zh/logs/auto-mine.log",
        "daemon": "~/.mempalace-zh/logs/daemon.log",
        "classify_decisions": "~/.mempalace-zh/logs/classify_decisions.jsonl",
    }
    out = {}
    now = datetime.datetime.now()
    for key, path in files.items():
        full = os.path.expanduser(path)
        if not os.path.isfile(full):
            out[key] = {"exists": False}
            continue
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(full))
        age = now - mtime
        size = os.path.getsize(full)
        out[key] = {"exists": True, "last_modified": mtime.isoformat(timespec="seconds"), "age_hours": round(age.total_seconds()/3600, 1), "size_bytes": size}
    return out


def check_symlink_graph() -> dict:
    """Verify 4-profile + main symlink integrity."""
    results = {}
    expected_links = {
        "main_skills": ("${WAYPALACE_HOME}/skills", "${USER_WORKSPACE}/<personal-skill-library>"),
    }
    for p in ("a", "b", "c"):
        for kind in ("skills", "commands", "projects", "plugins", "settings.json"):
            expected_links[f"{p}_{kind}"] = (
                f"${WAYPALACE_HOME}-profiles/{p}/.claude/{kind}",
                None,  # any valid target OK
            )
    for key, (path, expected_target) in expected_links.items():
        if not os.path.islink(path):
            results[key] = {"ok": False, "reason": "not a symlink"}
            continue
        target = os.readlink(path)
        target_exists = os.path.exists(path)  # follows symlink
        if expected_target and target != expected_target:
            results[key] = {"ok": False, "target": target, "expected": expected_target}
        elif not target_exists:
            results[key] = {"ok": False, "target": target, "reason": "target missing"}
        else:
            results[key] = {"ok": True, "target": target}
    return results


def check_disk_usage() -> dict:
    """Show key paths' disk usage."""
    paths = {
        "hf_cache": "~/.cache/huggingface",
        "mempalace_zh": "~/.mempalace-zh",
        "mempalace": "~/.mempalace",
        "claude_dir": "~/.claude",
        "user_profiles": "~/.user-profiles",
    }
    out = {}
    for key, p in paths.items():
        full = os.path.expanduser(p)
        if not os.path.exists(full):
            out[key] = {"exists": False}
            continue
        rc, dout = _run(["/usr/bin/du", "-sh", full], timeout=10)
        size = dout.split("\t")[0].strip() if rc == 0 else "?"
        out[key] = {"size": size}
    return out


def check_classify_trend(window_days: int = 7) -> dict:
    """Recent classify_wing fallback rate."""
    log = os.path.expanduser("~/.mempalace-zh/logs/classify_decisions.jsonl")
    if not os.path.isfile(log):
        return {"ok": True, "note": "no decisions logged yet"}
    since = datetime.datetime.now() - datetime.timedelta(days=window_days)
    total, fallback, parse_fail = 0, 0, 0
    try:
        with open(log, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    ts = datetime.datetime.fromisoformat(d.get("ts", ""))
                    if ts < since:
                        continue
                    total += 1
                    ps = d.get("parse_status", "")
                    if ps.startswith("wing_not_in_candidates"):
                        fallback += 1
                    elif ps.startswith("json_parse_failed"):
                        parse_fail += 1
                except Exception:
                    continue
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {
        "ok": True,
        "window_days": window_days,
        "total": total,
        "fallback_rate": (fallback / total) if total else 0,
        "parse_fail_rate": (parse_fail / total) if total else 0,
    }


def render_human(report: dict) -> str:
    L = []
    all_ok = True
    def head(t): L.append(""); L.append("─" * 70); L.append(f"  {t}"); L.append("─" * 70)
    def line(mark, text):
        L.append(f"  {mark} {text}")

    L.append("=" * 70)
    L.append("  mp-health — Local Long-term Memory System health check")
    L.append("=" * 70)

    head("Launchd Agents")
    for d in report["launchd"]:
        if d["ok"]:
            line("[OK]", f"{d['label']:35s}  pid={d['pid']:>6s}  last_exit={d['last_exit']}")
        else:
            line("[FAIL]", f"{d['label']:35s}  {d.get('reason','')}"); all_ok = False

    head("Daemons / Endpoints")
    m = report["mempalace"]
    if m["ok"]:
        line("[OK]", f"mempalace daemon         pid={m.get('pid')}  uptime={m.get('uptime_sec','?')}s")
    else:
        line("[FAIL]", f"mempalace daemon: {m.get('reason')}"); all_ok = False
    l = report["mlx_llm"]
    if l["ok"]:
        line("[OK]", f"mlx-llm http://:8081     model={l['models'][0] if l.get('models') else '?'}")
    else:
        line("[FAIL]", f"mlx-llm: {l.get('reason')}"); all_ok = False

    head("ChromaDB")
    c = report["chromadb"]
    if c["ok"]:
        line("[OK]", f"chunks={c['total_chunks']}  wings={c['wing_count']}  files={c['total_files']}")
    else:
        line("[FAIL]", f"{c.get('reason')}"); all_ok = False

    head("Symlink Graph (5 keys main + 5×3 profile)")
    bad = [k for k, v in report["symlinks"].items() if not v["ok"]]
    if not bad:
        line("[OK]", f"all {len(report['symlinks'])} symlinks healthy")
    else:
        for k in bad:
            v = report["symlinks"][k]
            line("[FAIL]", f"{k}: {v}"); all_ok = False

    head("Log Freshness")
    for k, v in report["logs"].items():
        if not v.get("exists"):
            line("[--]", f"{k:25s}  (not present)")
            continue
        age = v["age_hours"]
        mark = "[OK]" if age < 168 else "[WARN]"  # warn if log idle > 1 week
        size_kb = v["size_bytes"] / 1024
        line(mark, f"{k:25s}  age={age:5.1f}h  size={size_kb:7.1f} KB")

    head("Disk Usage")
    for k, v in report["disk"].items():
        if not v.get("exists", True):
            line("[--]", f"{k:25s}  (n/a)")
        else:
            line("[OK]", f"{k:25s}  {v['size']}")

    head("Classify Trend (last 7 days)")
    t = report["classify_trend"]
    if t.get("note"):
        line("[--]", t["note"])
    elif t["ok"]:
        fr = t.get("fallback_rate", 0) * 100
        pf = t.get("parse_fail_rate", 0) * 100
        mark = "[OK]" if (fr < 5 and pf < 1) else "[WARN]"
        line(mark, f"total={t['total']}  fallback={fr:.1f}%  parse_fail={pf:.2f}%")

    L.append("")
    L.append("=" * 70)
    L.append(f"  OVERALL: {'[OK] all healthy' if all_ok else '[FAIL] one or more checks failed'}")
    L.append("=" * 70)
    return "\n".join(L), all_ok


def main() -> int:
    p = argparse.ArgumentParser(description="Unified health check for local memory system")
    p.add_argument("--json", action="store_true", help="Output JSON instead of human report")
    p.add_argument("--quick", action="store_true", help="Skip slow checks (LLM ping, ChromaDB count)")
    args = p.parse_args()

    report = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "launchd": check_launchd_agents(),
        "mempalace": check_mempalace_socket(),
        "mlx_llm": {"ok": True, "skipped": True} if args.quick else check_mlx_llm_server(),
        "chromadb": {"ok": True, "skipped": True} if args.quick else check_chromadb(),
        "symlinks": check_symlink_graph(),
        "logs": check_logs_freshness(),
        "disk": check_disk_usage(),
        "classify_trend": check_classify_trend(),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    text, all_ok = render_human(report)
    print(text)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
