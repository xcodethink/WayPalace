"""Shared pytest fixtures for memory system tests (D002).

Design principles:
- Fast: unit tests use tmp_path / monkeypatch, no real daemon / chromadb.
- Real-daemon tests are marked and SKIP if daemon socket missing.
- Tests must not touch production chromadb (use isolated tmp paths).
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = "${WAYPALACE_HOME}/scripts"
HOOKS_DIR = "${WAYPALACE_HOME}/hooks"
DAEMON_SOCK = "${WAYPALACE_DATA}/daemon.sock"
MLX_LLM_PORT = 8081
VENV_PYTHON = "${WAYPALACE_VENV}/bin/python"

# Make memory modules importable from tests
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


@pytest.fixture
def isolated_metrics_dir(tmp_path, monkeypatch):
    """Redirect mp_metrics output to tmp_path so tests don't pollute prod metrics."""
    import mp_metrics
    monkeypatch.setattr(mp_metrics, "METRICS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_memory_path(tmp_path):
    """Create a fake ~/.claude/projects/<x>/memory/ tree for hook tests."""
    d = tmp_path / ".claude" / "projects" / "test_proj" / "memory"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def daemon_alive():
    """Skip the test if daemon socket is missing. Does not manage daemon lifecycle."""
    if not os.path.exists(DAEMON_SOCK):
        pytest.skip(f"daemon socket missing at {DAEMON_SOCK}")
    return DAEMON_SOCK


@pytest.fixture
def mlx_llm_alive():
    """Skip if mlx-llm endpoint not reachable."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", MLX_LLM_PORT))
        s.close()
    except Exception:
        pytest.skip(f"mlx-llm not reachable on port {MLX_LLM_PORT}")


@pytest.fixture
def mock_chat(monkeypatch, tmp_path):
    """Replace memory_llm_assist._chat with a MagicMock for failure-mode tests.

    Also redirects the production audit log (classify_decisions.jsonl) to a
    tmp file so test mocks don't pollute the real decision log used by
    mp-health classify_trend reporting.
    """
    from unittest.mock import MagicMock
    import memory_llm_assist
    mock = MagicMock()
    monkeypatch.setattr(memory_llm_assist, "_chat", mock)
    # P1-D fix: isolate test-time audit log from production
    isolated_log = str(tmp_path / "classify_decisions_test.jsonl")
    monkeypatch.setattr(memory_llm_assist, "CLASSIFY_LOG", isolated_log)
    return mock
