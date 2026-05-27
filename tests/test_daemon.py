"""test_daemon.py — 4 daemon lifecycle tests (D002 Part A.3).

Tests are non-destructive (no kill -9 of the daemon). Tests that would
require restarting daemons or interrupting production traffic are converted
to "warm-path observation" style that asserts the daemon is responsive
without disturbing it.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager

import pytest

DAEMON_SOCK = "${WAYPALACE_DATA}/daemon.sock"
MINE_LOCK = "${WAYPALACE_DATA}/mine.lock"
MLX_PORT = 8081
VENV_PY = "${WAYPALACE_VENV}/bin/python"


def _socket_send(payload: dict, timeout: float = 30.0) -> dict:
    """Send a JSON command to memory daemon and return the response dict."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(DAEMON_SOCK)
    s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    buf = b""
    while b"\n" not in buf and len(buf) < 1 << 20:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode("utf-8").strip())


# ---------- 1. Daemon socket ping ----------

def test_daemon_socket_ping(daemon_alive):
    """Daemon must answer ping within 1 second."""
    t0 = time.time()
    resp = _socket_send({"cmd": "ping"}, timeout=3.0)
    elapsed = time.time() - t0
    assert resp.get("ok") is True, f"ping returned non-ok: {resp}"
    assert elapsed < 1.0, f"ping took {elapsed:.2f}s, expected <1s"


# ---------- 2. Daemon search warm-path envelope ----------

def test_daemon_search_warm_path(daemon_alive):
    """A search over the warm daemon must return a well-shaped envelope within 10s.

    NOTE: Original PRD wording called for 'kill daemon → cold start'. We do NOT
    kill the production daemon (it's used by hooks across all profiles). Instead
    we verify the warm path returns a correctly-shaped response with results.
    """
    t0 = time.time()
    resp = _socket_send({"cmd": "search", "query": "硬刹车", "limit": 3}, timeout=15.0)
    elapsed = time.time() - t0
    assert resp.get("ok") is True, f"search returned non-ok: {resp}"
    assert isinstance(resp.get("results"), list), "results must be a list"
    assert elapsed < 10.0, f"warm search took {elapsed:.2f}s, expected <10s"


# ---------- 3. mlx-llm health endpoint ----------

def test_mlx_llm_health_endpoint(mlx_llm_alive):
    """GET :8081/v1/models must return 200 + a list of models."""
    t0 = time.time()
    req = urllib.request.Request(f"http://127.0.0.1:{MLX_PORT}/v1/models")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        assert resp.status == 200
    elapsed = time.time() - t0
    data = json.loads(body)
    assert "data" in data, f"missing 'data' key: {data}"
    assert len(data["data"]) >= 1, f"expected at least one model: {data}"
    # Model id should reference Qwen3.6 (per Stage 4 setup)
    ids = [m.get("id", "") for m in data["data"]]
    assert any("qwen" in i.lower() or "Qwen" in i for i in ids), \
        f"expected a Qwen model in: {ids}"
    assert elapsed < 5.0


# ---------- 4. fcntl lock serializes concurrent mine attempts ----------

def test_fcntl_lock_serialization(tmp_path):
    """3 mp-mine processes started concurrently must all succeed (no chromadb
    corruption) by serializing on the file lock.

    We use a small markdown file in tmp_path with --wing global (a real wing).
    Since mp-mine is idempotent (mtime check), the second/third invocations
    will likely skip — what we verify is no process crashes / hangs / exits
    non-zero, AND all three release the lock in finite time.
    """
    f = tmp_path / "fcntl_test.md"
    f.write_text("# fcntl test\n\n" + ("payload " * 50) + "\n")

    procs = []
    for _ in range(3):
        p = subprocess.Popen(
            [VENV_PY, "${WAYPALACE_HOME}/scripts/memory_mine.py",
             str(f), "--wing", "global", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        procs.append(p)

    t0 = time.time()
    for p in procs:
        # 120s lock timeout + safety margin
        rc = p.wait(timeout=180)
        assert rc == 0, f"process {p.pid} exited {rc}; stderr={p.stderr.read().decode()!r}"
    elapsed = time.time() - t0
    # 3 serialised should complete in well under the 120s per-process timeout
    assert elapsed < 90.0, f"3 concurrent mine took {elapsed:.1f}s, expected <90s"
