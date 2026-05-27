#!/usr/bin/env python3
"""memory-session-start.py — SessionStart hook for conditional project context injection.

Injects up to ~6 KB of project context (current-task / HANDOFF / latest
conversation-log) at conversation start, BUT only when conditions are met
(see _should_trigger). Designed for the 4-profile-frequent-switching
workflow: (maintainer) hops between ~/Developer/X projects and wants automatic
"上文重建" without rote re-explaining.

Why conditional (not unconditional like claude-mem):
  Claude Code wraps additionalContext as a system reminder that the model
  CANNOT deprioritize. Injecting noisy or stale context degrades every
  subsequent turn ("lost in the middle"). We only inject when there is
  fresh, project-specific signal — otherwise stay silent.

Trigger conditions (ANY one fires injection):
  1. cwd is ~/Developer/<project>/ AND project has conversation-log <14d
  2. cwd has tasks/current-task.md
  3. cwd has docs/HANDOFF.md updated within last 7 days

Skip conditions (NEVER inject):
  - cwd outside ~/Developer/
  - No conversation-log AND no HANDOFF AND no tasks/current-task.md
  - All candidate files older than freshness thresholds

Total injected size capped at 6000 chars (40% under 10k Claude Code limit).

Fail-silent: any exception → exit 0 with empty additionalContext.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

DEV_ROOT = "${USER_WORKSPACE}"
TOTAL_BUDGET = 6000          # chars; Claude limit is 10k, leave 40% safety margin
PER_SECTION_CAP = 2000       # max chars from any one file
LOG_MAX_AGE_DAYS = 14        # conversation-log must be fresher than this
HANDOFF_MAX_AGE_DAYS = 7     # HANDOFF must be fresher than this
LOG_FILE = "${WAYPALACE_DATA}/logs/session-start.log"


def _log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _empty() -> dict:
    """Return empty hook output — silent skip."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ""
        }
    }


def _fresh(path: str, max_age_days: int) -> bool:
    """Is the file newer than max_age_days?"""
    try:
        age = time.time() - os.path.getmtime(path)
        return age < max_age_days * 86400
    except OSError:
        return False


def _read_capped(path: str, cap: int) -> str:
    """Read file, return up to `cap` chars, with truncation marker."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        if len(data) > cap:
            return data[:cap].rstrip() + f"\n…[truncated, file is {len(data)} chars total]"
        return data
    except Exception as e:
        return f"[failed to read {path}: {e}]"


def _latest_log(log_dir: str, max_age_days: int) -> str | None:
    """Find latest *.md in conversation-log dir, if fresher than max_age_days."""
    try:
        candidates = []
        for name in os.listdir(log_dir):
            if not name.endswith(".md") or name.startswith("."):
                continue
            full = os.path.join(log_dir, name)
            if not os.path.isfile(full):
                continue
            candidates.append((os.path.getmtime(full), full))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        latest_mtime, latest_path = candidates[0]
        if time.time() - latest_mtime > max_age_days * 86400:
            return None
        return latest_path
    except Exception:
        return None


def _detect_project(cwd: str) -> str | None:
    """Return ~/Developer/<project>/ if cwd is inside one; else None."""
    abs_cwd = os.path.realpath(cwd)
    if not abs_cwd.startswith(DEV_ROOT + "/"):
        return None
    rest = abs_cwd[len(DEV_ROOT) + 1:]
    project_dir = os.path.join(DEV_ROOT, rest.split("/")[0])
    if not os.path.isdir(project_dir):
        return None
    return project_dir


def _collect_sections(project_dir: str) -> list[tuple[str, str]]:
    """Gather candidate sections in priority order. Empty list = silent skip."""
    out: list[tuple[str, str]] = []

    # 1. tasks/current-task.md — highest signal if exists
    task_file = os.path.join(project_dir, "tasks", "current-task.md")
    if os.path.isfile(task_file):
        out.append(("当前任务 (tasks/current-task.md)", _read_capped(task_file, PER_SECTION_CAP)))

    # 2. docs/HANDOFF.md — wave-level state if fresh
    handoff_file = os.path.join(project_dir, "docs", "HANDOFF.md")
    if os.path.isfile(handoff_file) and _fresh(handoff_file, HANDOFF_MAX_AGE_DAYS):
        out.append(("Wave 状态 (docs/HANDOFF.md)", _read_capped(handoff_file, PER_SECTION_CAP)))

    # 3. Latest conversation-log if fresh
    log_dir = os.path.join(project_dir, "docs", "conversation-log")
    if os.path.isdir(log_dir):
        latest = _latest_log(log_dir, LOG_MAX_AGE_DAYS)
        if latest:
            out.append((f"最近对话 (docs/conversation-log/{os.path.basename(latest)})",
                        _read_capped(latest, PER_SECTION_CAP)))

    return out


def _build_context(project_dir: str, sections: list[tuple[str, str]]) -> str:
    """Assemble final additionalContext string, bounded by TOTAL_BUDGET."""
    proj_name = os.path.basename(project_dir)
    header = (
        f"[SESSION CONTEXT · HISTORICAL REFERENCE — 非当前指令]\n"
        f"对话起始时自动从 {project_dir} 注入的项目上下文。这些是过去的状态/任务/对话脉络,\n"
        f"用于让你（AI）快速理解项目当前进展。如果跟用户当前消息无关，安全地参考即可。\n"
        f"项目: {proj_name}\n"
    )
    body_parts = []
    used = len(header)
    for title, text in sections:
        section_str = f"\n\n## {title}\n\n{text}"
        if used + len(section_str) > TOTAL_BUDGET:
            remaining = TOTAL_BUDGET - used
            if remaining > 200:  # only include if meaningful
                section_str = section_str[:remaining] + "\n…[budget reached]"
                body_parts.append(section_str)
            break
        body_parts.append(section_str)
        used += len(section_str)
    return header + "".join(body_parts)


def _emit_metric(event: str, **fields) -> None:
    """Local-only metrics; fail-silent."""
    try:
        sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
        from mp_metrics import record_event
        record_event(event, **fields)
    except Exception:
        pass


def main() -> int:
    _t0 = time.time()
    try:
        # Hook stdin contains JSON with session/tool info — we just need cwd.
        # CLAUDE_CWD env or actual cwd.
        cwd = os.environ.get("CLAUDE_CWD", os.getcwd())

        project_dir = _detect_project(cwd)
        if project_dir is None:
            _log(f"skip: cwd '{cwd}' not in {DEV_ROOT}")
            print(json.dumps(_empty()))
            _emit_metric("hook.session_start.skipped", reason="not_in_developer")
            return 0

        sections = _collect_sections(project_dir)
        if not sections:
            _log(f"skip: '{os.path.basename(project_dir)}' has no fresh sections")
            print(json.dumps(_empty()))
            _emit_metric("hook.session_start.skipped", reason="no_fresh_sections",
                         project=os.path.basename(project_dir))
            return 0

        ctx = _build_context(project_dir, sections)
        _log(f"injected for '{os.path.basename(project_dir)}': "
             f"{len(sections)} sections, {len(ctx)} chars")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": ctx
            }
        }, ensure_ascii=False))
        _emit_metric("hook.session_start.fired", project=os.path.basename(project_dir),
                     sections=len(sections), chars=len(ctx),
                     latency_ms=int((time.time() - _t0) * 1000))
        return 0
    except Exception as e:
        _log(f"error: {e!r}")
        # Fail-silent: empty injection rather than blocking the session.
        print(json.dumps(_empty()))
        _emit_metric("hook.session_start.fired", status="fail", error=str(e)[:80])
        return 0


if __name__ == "__main__":
    sys.exit(main())
