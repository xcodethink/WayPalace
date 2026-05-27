#!/usr/bin/env python3
"""memory-auto-mine.py — PostToolUse hook: auto-index Claude-written memory files.

Triggered after Claude Code finishes a Write/Edit/MultiEdit. If the touched
file is inside an auto-memory directory, asynchronously spawn mp-mine to
index it into ChromaDB. Failures are fully silent (exit 0 always).

Design rules:
  - **fail-silent**: any exception → exit 0. The user's Claude Code session
    must not be disrupted by indexing hiccups.
  - **detached spawn**: mp-mine takes ~15-25s (bge-m3 cold load + LLM
    summarize). We must NOT wait for it. The hook should return in <50ms.
  - **path filter**: only fires for files inside ~/.claude/projects/*/memory/*.md.
    All other Edit/Write tool calls are ignored at near-zero cost.
  - **idempotent**: mp-mine has mtime-based incremental — running it twice
    on the same file is a no-op the second time.

Registered in ~/.claude/settings.json under PostToolUse with matcher
"Edit|Write|MultiEdit" and async: true so this whole pipeline is fire-and-forget.

If you need to disable: comment out the hook entry in settings.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

LOG_FILE = "${WAYPALACE_DATA}/logs/auto-mine.log"
PYTHON = "${WAYPALACE_VENV}/bin/python"
MINE_SCRIPT = "${WAYPALACE_HOME}/scripts/memory_mine.py"


def _log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass  # logging is best-effort


def _is_memory_file(path: str) -> bool:
    """Check if path is inside an auto-memory directory.

    Matches /.claude/projects/<anything>/memory/<anything>.md
    """
    if not path or not path.endswith(".md"):
        return False
    p_idx = path.find("/.claude/projects/")
    if p_idx < 0:
        return False
    m_idx = path.find("/memory/", p_idx + 1)
    return m_idx > p_idx


def _spawn_mine(file_path: str) -> None:
    """Fire-and-forget mp-mine. Detach from this hook process."""
    devnull = subprocess.DEVNULL
    subprocess.Popen(
        [
            PYTHON, MINE_SCRIPT,
            file_path,
            "--llm-classify", "--llm-summarize",
            "--quiet",
        ],
        stdin=devnull, stdout=devnull, stderr=devnull,
        start_new_session=True,  # detach: child survives even if hook is killed
        close_fds=True,
    )


def _emit_metric(event: str, **fields) -> None:
    """Local-only metrics; fail-silent."""
    try:
        sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
        from mp_metrics import record_event
        record_event(event, **fields)
    except Exception:
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        tool_input = data.get("tool_input", {}) or {}
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        if not _is_memory_file(file_path):
            _emit_metric("hook.auto_mine.skipped", reason="path_filter")
            return 0
        if not os.path.isfile(file_path):
            _emit_metric("hook.auto_mine.skipped", reason="not_a_file", file=file_path)
            return 0
        _spawn_mine(file_path)
        _log(f"[{os.getpid()}] spawned mine for {file_path}")
        _emit_metric("hook.auto_mine.spawned", file=file_path, status="ok")
    except Exception as e:
        _log(f"[error] {e!r}")
        _emit_metric("hook.auto_mine.spawned", status="fail", error=str(e)[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
