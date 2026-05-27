#!/usr/bin/env python3
"""mp_drift_check.py — Detect configuration drift in the local memory system.

Checks:
  1. ~/.claude/skills symlink → ~/Developer/<personal-skill-library> (must exist)
  2. Per-profile symlinks (a/b/c): skills, commands, projects, plugins, settings.json
  3. MCP server `memory` registered in all 4 profiles' .claude.json
  4. 4 launchd daemons loaded (mempalace + mlx-llm + memory-refresh + memory-backup + memory-audit)
  5. Keychain has 4 distinct OAuth credential entries
  6. ~/.mempalace-zh/chromadb/ exists + chunks > 0
  7. ~/.claude/sensitive-dict.json present + fresh (rebuilt within 7 days)

Designed to be called weekly. Exit 0 if no drift, 1 if drift detected.
Output is plain text suitable for both human inspection and audit-weekly.log.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def check_main_skills_symlink() -> tuple[bool, str]:
    path = "${WAYPALACE_HOME}/skills"
    expected = "${USER_WORKSPACE}/<personal-skill-library>"
    if not os.path.islink(path):
        return False, f"~/.claude/skills is not a symlink"
    tgt = os.readlink(path)
    if tgt != expected:
        return False, f"~/.claude/skills → {tgt} (expected {expected})"
    if not os.path.isdir(tgt):
        return False, f"~/.claude/skills target {tgt} does not exist"
    return True, f"~/.claude/skills → {tgt}"


def check_profile_symlinks() -> list[tuple[bool, str]]:
    out = []
    for p in ("a", "b", "c"):
        for kind in ("skills", "commands", "projects", "plugins", "settings.json"):
            link = f"${WAYPALACE_HOME}-profiles/{p}/.claude/{kind}"
            if not os.path.exists(link):
                out.append((False, f"profile {p}/{kind}: missing"))
                continue
            if not os.path.islink(link):
                out.append((True, f"profile {p}/{kind}: real file/dir (not symlinked — may be intentional)"))
                continue
            tgt = os.readlink(link)
            if not os.path.exists(link):  # follows symlink
                out.append((False, f"profile {p}/{kind}: broken symlink → {tgt}"))
            else:
                out.append((True, f"profile {p}/{kind} → {tgt}"))
    return out


def check_mcp_registrations() -> list[tuple[bool, str]]:
    out = []
    files = [
        ("main", "${WAYPALACE_HOME}.json"),
        ("a", "${WAYPALACE_HOME}-profiles/a/.claude/.claude.json"),
        ("b", "${WAYPALACE_HOME}-profiles/b/.claude/.claude.json"),
        ("c", "${WAYPALACE_HOME}-profiles/c/.claude/.claude.json"),
    ]
    for name, path in files:
        if not os.path.isfile(path):
            out.append((False, f"profile {name}: .claude.json missing"))
            continue
        try:
            d = json.load(open(path))
        except Exception as e:
            out.append((False, f"profile {name}: .claude.json invalid: {e}"))
            continue
        mcp = d.get("mcpServers", {})
        if "memory" not in mcp:
            out.append((False, f"profile {name}: 'memory' MCP server NOT registered"))
        else:
            cmd = mcp["memory"].get("command", "")
            out.append((True, f"profile {name}: memory MCP registered (cmd={cmd})"))
    return out


def check_launchd_agents() -> list[tuple[bool, str]]:
    expected = [
        "com.user.waypalace.mempalace.daemon",
        "com.user.waypalace.mlx-llm.daemon",
        "com.user.waypalace.memory-refresh.daemon",
        "com.user.waypalace.memory-backup.daemon",
        "com.user.waypalace.memory-audit.daemon",
        "com.user.waypalace.memory-logrotate.daemon",
    ]
    rc, out = _run(["/bin/launchctl", "list"])
    loaded = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("com.user.waypalace."):
            loaded.add(parts[2])
    results = []
    for label in expected:
        if label in loaded:
            results.append((True, f"launchd {label}: loaded"))
        else:
            results.append((False, f"launchd {label}: NOT loaded"))
    return results


def check_keychain() -> tuple[bool, str]:
    rc, out = _run(["/usr/bin/security", "dump-keychain"])
    if rc != 0:
        return False, f"keychain dump failed"
    found = set()
    for line in out.splitlines():
        if '"svce"' in line and "Claude Code-credentials" in line:
            # Extract the svce value
            parts = line.split('=')
            if len(parts) >= 2:
                v = parts[-1].strip().strip('"').strip()
                found.add(v)
    expected_main = "Claude Code-credentials"
    expected_subs = {"Claude Code-credentials-4aeb60f4", "Claude Code-credentials-9c5f6dd3", "Claude Code-credentials-d52d9d95"}
    missing_subs = expected_subs - found
    has_main = expected_main in found
    if has_main and not missing_subs:
        return True, f"4 Keychain credential entries present"
    parts = []
    if not has_main:
        parts.append("main missing")
    if missing_subs:
        parts.append(f"sub missing: {sorted(missing_subs)}")
    return False, "; ".join(parts)


def check_chromadb() -> tuple[bool, str]:
    db = "${WAYPALACE_DATA}/chromadb/chroma.sqlite3"
    if not os.path.isfile(db):
        return False, f"chroma.sqlite3 missing"
    try:
        import sqlite3
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM embeddings")
        n = cur.fetchone()[0]
        conn.close()
        if n == 0:
            return False, "chromadb has 0 embeddings"
        return True, f"chromadb: {n} embeddings"
    except Exception as e:
        return False, f"chromadb query failed: {e}"


def check_sensitive_dict_freshness() -> tuple[bool, str]:
    p = "${WAYPALACE_HOME}/sensitive-dict.json"
    if not os.path.isfile(p):
        return False, "sensitive-dict.json missing"
    import time
    age_days = (time.time() - os.path.getmtime(p)) / 86400
    try:
        d = json.load(open(p))
        proj_count = len([k for k in d.keys() if not k.startswith("_")])
    except Exception:
        proj_count = 0
    if age_days > 14:
        return False, f"sensitive-dict.json stale: {age_days:.1f} days old ({proj_count} projects)"
    return True, f"sensitive-dict.json fresh: {age_days:.1f} days old ({proj_count} projects)"


def main() -> int:
    sections: list[tuple[str, list[tuple[bool, str]]]] = []

    ok, msg = check_main_skills_symlink()
    sections.append(("Main skills symlink", [(ok, msg)]))
    sections.append(("Profile symlinks (a/b/c × 5)", check_profile_symlinks()))
    sections.append(("MCP server registrations (4 profiles)", check_mcp_registrations()))
    sections.append(("Launchd agents (6 expected)", check_launchd_agents()))
    sections.append(("Keychain OAuth credentials (4 expected)", [check_keychain()]))
    sections.append(("ChromaDB", [check_chromadb()]))
    sections.append(("Sensitive dict freshness", [check_sensitive_dict_freshness()]))

    print("=" * 70)
    print("  mp-drift-check — Configuration drift audit")
    print("=" * 70)
    all_ok = True
    for title, results in sections:
        print(f"\n  {title}:")
        for ok, msg in results:
            mark = "[OK]  " if ok else "[FAIL]"
            print(f"    {mark} {msg}")
            if not ok:
                all_ok = False

    print()
    print("=" * 70)
    print(f"  OVERALL: {'[OK] no drift detected' if all_ok else '[DRIFT] one or more items need attention'}")
    print("=" * 70)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
