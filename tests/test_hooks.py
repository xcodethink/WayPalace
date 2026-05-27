"""test_hooks.py — 6 hook behavior tests (D002 Part A.1)."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

HOOKS = "${WAYPALACE_HOME}/hooks"


def _load_hook(name: str):
    """Load a hook .py file as a module."""
    path = os.path.join(HOOKS, name)
    spec = importlib.util.spec_from_file_location(f"hook_{name.replace('.', '_').replace('-', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 1. memory-auto-mine path filter ----------

def test_memory_auto_mine_path_filter():
    """_is_memory_file() must match the exact path pattern only."""
    mod = _load_hook("memory-auto-mine.py")
    f = mod._is_memory_file

    # Positive cases
    assert f("${WAYPALACE_HOME}/projects/-user/memory/foo.md") is True
    assert f("${WAYPALACE_HOME}/projects/-user-workspace-<project-a>/memory/feedback_x.md") is True
    assert f("${WAYPALACE_HOME}/projects/abc/memory/x.md") is True
    assert f("${WAYPALACE_HOME}/projects/-something/memory/note.md") is True

    # Negative cases
    assert f("") is False
    assert f("${WAYPALACE_HOME}/projects/-user/memory/foo.txt") is False  # not .md
    assert f("${WAYPALACE_HOME}/projects/foo.md") is False  # no /memory/
    assert f("${HOME}/random/memory/x.md") is False  # no /.claude/projects/


# ---------- 2. memory-auto-mine fail-silent ----------

def test_memory_auto_mine_fail_silent(monkeypatch, isolated_metrics_dir, tmp_path):
    """When mp-mine spawn fails, hook must still exit 0 (fail-silent)."""
    mod = _load_hook("memory-auto-mine.py")

    # Create a real memory-like file so the path filter passes
    target = tmp_path / ".claude" / "projects" / "-user" / "memory" / "foo.md"
    target.parent.mkdir(parents=True)
    target.write_text("# fake memory")

    # Force subprocess.Popen to raise — simulating mp-mine crash
    import subprocess

    def boom(*a, **kw):
        raise OSError("simulated Popen failure")

    monkeypatch.setattr(subprocess, "Popen", boom)

    # Inject stdin payload
    payload = {"tool_input": {"file_path": str(target)}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    rc = mod.main()
    assert rc == 0, "hook must always exit 0 even on internal failure"

    # And it must have logged the error to metrics
    files = list(isolated_metrics_dir.glob("*.jsonl"))
    assert files, "expected a metrics file even on failure"
    content = files[0].read_text()
    assert "auto_mine.spawned" in content
    assert "fail" in content


# ---------- 3. memory-auto-surface tool filter ----------

def test_memory_auto_surface_tool_filter(monkeypatch, isolated_metrics_dir):
    """When tool is not in {Edit,Write,MultiEdit,Bash}, hook returns 0 without query."""
    mod = _load_hook("memory-auto-surface.py")

    # Read is NOT in the handled set — should early-return without invoking daemon
    payload = {"tool_name": "Read", "tool_input": {"file_path": "/tmp/foo"}, "cwd": "/tmp"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    # Spy on daemon socket call — must NOT be invoked
    called = {"n": 0}
    orig = mod.run_memory_search

    def spy(*a, **kw):
        called["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(mod, "run_memory_search", spy)

    rc = mod.main()
    assert rc == 0
    assert called["n"] == 0, "Read tool must not trigger daemon search"


# ---------- 4. memory-session-start triggers (parameterized OR conditions) ----------

@pytest.mark.parametrize("has_task,has_handoff,has_log,expect_fire", [
    (False, False, False, False),   # nothing → skip
    (True, False, False, True),     # only current-task → fire
    (False, True, False, True),     # only fresh HANDOFF → fire
    (False, False, True, True),     # only fresh log → fire
    (True, True, True, True),       # all three → fire
])
def test_memory_session_start_triggers(monkeypatch, isolated_metrics_dir, tmp_path,
                                       has_task, has_handoff, has_log, expect_fire):
    """Three trigger sources (current-task / HANDOFF / conversation-log) in OR."""
    mod = _load_hook("memory-session-start.py")

    # Build a fake ~/Developer/<proj>/ structure
    dev_root = tmp_path / "Developer"
    proj = dev_root / "TestProj"
    proj.mkdir(parents=True)

    if has_task:
        tasks = proj / "tasks"
        tasks.mkdir()
        (tasks / "current-task.md").write_text("# do the thing")
    if has_handoff:
        docs = proj / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "HANDOFF.md").write_text("wave state")
    if has_log:
        log_dir = proj / "docs" / "conversation-log"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "2026-05-27-01-test.md").write_text("recent log")

    monkeypatch.setattr(mod, "DEV_ROOT", str(dev_root))
    monkeypatch.setenv("CLAUDE_CWD", str(proj))

    # Capture stdout
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    rc = mod.main()
    assert rc == 0
    out = buf.getvalue()
    payload = json.loads(out)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    if expect_fire:
        assert ctx, f"expected context with has_task={has_task}/handoff={has_handoff}/log={has_log}"
    else:
        assert ctx == "", "expected empty context (skip)"


# ---------- 5. cross-project-guard blocks foreign project secrets ----------

def test_cross_project_guard_blocks_foreign_id():
    """get_other_project_terms() must exclude the current wing's own terms,
    and check_content() must flag a foreign term."""
    mod = _load_hook("cross-project-guard.py")
    sd_path = "${WAYPALACE_HOME}/sensitive-dict.json"
    if not os.path.exists(sd_path):
        pytest.skip("sensitive-dict.json not built")

    sd = mod.load_dict()
    # Find two wings each with at least one string term in any category
    def _first_term(wing_assets) -> str | None:
        if not isinstance(wing_assets, dict):
            return None
        for cat, terms in wing_assets.items():
            if isinstance(terms, list) and terms:
                t = terms[0]
                if isinstance(t, str) and len(t) > 3:
                    return t
        return None

    wing_terms = {w: _first_term(a) for w, a in sd.items() if w != "_meta"}
    eligible = [(w, t) for w, t in wing_terms.items() if t]
    if len(eligible) < 2:
        pytest.skip("need at least 2 wings with usable terms")
    (wing_a, _), (wing_b, term_b) = eligible[0], eligible[1]

    # When current wing is wing_a, terms from wing_b should be flagged as foreign
    other_terms = mod.get_other_project_terms(wing_a, sd)
    hits = mod.check_content(f"some pre-text {term_b} post-text", other_terms)
    assert hits, f"expected check_content to flag {term_b!r} when current={wing_a}"


# ---------- 6. git-guardrails read vs write ----------

def test_git_guardrails_read_vs_write():
    """detect_v1_regex must allow `git config --global user.name` (read),
    block `git config --global user.name 'X'` (write)."""
    mod = _load_hook("git-guardrails.py")

    # Read forms — must NOT match any dangerous pattern
    allowed = [
        "git config --global user.name",
        "git config --global user.email",
        "git config --get user.name",
        "git config --list",
    ]
    for cmd in allowed:
        hit = mod.detect_v1_regex(cmd)
        assert hit is None, f"read form should NOT be blocked: {cmd!r} (hit={hit})"

    # Write forms — must match the git-config-write pattern.
    # NB: hook strips quoted literals first (to avoid `echo "git config ..."` false-positives),
    # so we test forms that read as writes after quote-stripping.
    blocked = [
        "git config --global user.name OtherPerson",
        "git config --global --add user.email foo@bar.com",
        "git config --global --unset user.name",
        "git config --global --replace-all user.email foo@bar.com",
    ]
    for cmd in blocked:
        hit = mod.detect_v1_regex(cmd)
        assert hit is not None, f"write form SHOULD be blocked: {cmd!r}"
        label, pattern = hit
        assert "config" in label.lower() or "git config" in label, \
            f"expected git config label, got {label!r}"
