"""test_mine.py — 5 mp-mine boundary tests (D002 Part A.2).

Strategy: mock chromadb store path + LLM calls so tests are fast and
deterministic, without touching production chromadb.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import memory_mine
import memory_core

VENV_PY = "${WAYPALACE_VENV}/bin/python"
MINE_SCRIPT = "${WAYPALACE_HOME}/scripts/memory_mine.py"


# ---------- 1. LLM summarize failure → fallback (chunks still indexed) ----------

def test_mine_llm_summarize_failure_fallback(tmp_path, monkeypatch):
    """When summarize_chunk raises, mine_file must still index chunks (without summary)."""
    f = tmp_path / "test.md"
    f.write_text("# Title\n\n" + ("This is some content. " * 50) + "\n")

    # Mock storage path so we don't touch real chromadb
    stored = {}
    monkeypatch.setattr(memory_core, "get_stored_mtime", lambda wing, fp: None)
    monkeypatch.setattr(memory_core, "store_chunks",
                        lambda wing, fp, chunks, mtime: (stored.update({"chunks": chunks, "wing": wing}), len(chunks))[1])

    # Make summarize_chunk explode (any kind of LLM failure)
    import memory_llm_assist
    def boom(*a, **kw):
        raise RuntimeError("simulated LLM down")
    monkeypatch.setattr(memory_llm_assist, "summarize_chunk", boom)

    result = memory_mine.mine_file(str(f), wing="global", llm_summarize=True, verbose=False)
    assert result["status"] == "indexed"
    assert result["chunks"] > 0
    # No summary fields should be present (since LLM failed)
    for c in stored["chunks"]:
        assert "summary" not in c or not c["summary"]


# ---------- 2. --force with --llm-summarize reprocesses existing summary ----------

def test_mine_force_reprocess_existing_summary(tmp_path, monkeypatch):
    """When --force + --llm-summarize, mtime check is bypassed and chunks re-stored."""
    f = tmp_path / "test.md"
    f.write_text("# Title\n\nSome stable content.\n")

    mtime = os.path.getmtime(str(f))

    # Simulate that this file was already mined with the SAME mtime
    monkeypatch.setattr(memory_core, "get_stored_mtime", lambda wing, fp: mtime)

    # Capture store calls
    store_calls = {"n": 0}
    def fake_store(wing, fp, chunks, mt):
        store_calls["n"] += 1
        return len(chunks)
    monkeypatch.setattr(memory_core, "store_chunks", fake_store)

    # Mock summarize_chunk to return a stub
    import memory_llm_assist
    monkeypatch.setattr(memory_llm_assist, "summarize_chunk", lambda text, max_words=30: "stub summary")

    # Without --force, with --llm-summarize, since this code path checks "all chunks have summary?":
    # We need to also mock the chromadb metadata check that the function does inline.
    # Simplest: pass force=True to bypass that whole path.
    result = memory_mine.mine_file(str(f), wing="global", force=True, llm_summarize=True, verbose=False)
    assert result["status"] == "indexed"
    assert store_calls["n"] == 1, "force=True must trigger a fresh store"


# ---------- 3. mtime gap incremental: same mtime → skip; bumped mtime → re-mine ----------

def test_mine_mtime_gap_incremental(tmp_path, monkeypatch):
    """Identical mtime ⇒ skipped. Newer mtime ⇒ re-mined (store called again)."""
    f = tmp_path / "test.md"
    f.write_text("# Title\n\nfirst pass.\n")
    initial_mtime = os.path.getmtime(str(f))

    # First call: nothing stored yet (returns None) → should index
    stored_mtime = {"v": None}
    store_calls = {"n": 0}

    def get_stored(wing, fp):
        return stored_mtime["v"]

    def fake_store(wing, fp, chunks, mt):
        store_calls["n"] += 1
        stored_mtime["v"] = mt
        return len(chunks)

    monkeypatch.setattr(memory_core, "get_stored_mtime", get_stored)
    monkeypatch.setattr(memory_core, "store_chunks", fake_store)

    r1 = memory_mine.mine_file(str(f), wing="global", verbose=False)
    assert r1["status"] == "indexed"
    assert store_calls["n"] == 1

    # Second call with same mtime → skipped (no new store)
    r2 = memory_mine.mine_file(str(f), wing="global", verbose=False)
    assert r2["status"] == "skipped"
    assert store_calls["n"] == 1, "no new store on unchanged mtime"

    # Bump mtime, now should re-mine
    new_mtime = initial_mtime + 100
    os.utime(str(f), (new_mtime, new_mtime))

    r3 = memory_mine.mine_file(str(f), wing="global", verbose=False)
    assert r3["status"] == "indexed"
    assert store_calls["n"] == 2, "bumped mtime should trigger re-store"


# ---------- 4. Mine a nonexistent path → CLI exits gracefully, doesn't crash ----------

def test_mine_directory_not_exists(tmp_path, isolated_metrics_dir):
    """`mp-mine /nonexistent` must exit non-zero with a clean error message (no crash)."""
    nonexistent = str(tmp_path / "definitely_not_here_xyz123")
    assert not os.path.exists(nonexistent)
    # CLI takes --wing positional arg
    proc = subprocess.run(
        [VENV_PY, MINE_SCRIPT, nonexistent, "--wing", "global", "--quiet"],
        capture_output=True, text=True, timeout=30,
    )
    # Should exit non-zero (CLI explicitly does sys.exit(1) for missing path) but not crash
    assert proc.returncode == 1, f"expected exit 1 for missing path, got {proc.returncode}"
    err = proc.stderr or ""
    out = proc.stdout or ""
    combined = (err + " " + out).lower()
    assert "不存在" in combined or "not" in combined, \
        f"expected friendly error mentioning missing path; got stderr={err!r} stdout={out!r}"


# ---------- 5. Empty file → no chunks stored ----------

def test_mine_empty_file(tmp_path, monkeypatch):
    """A zero-byte / whitespace-only file must be classified as 'empty' and stored 0 chunks."""
    f = tmp_path / "empty.md"
    f.write_text("   \n\n  \n")  # whitespace only

    # Spy on store — must not be called
    monkeypatch.setattr(memory_core, "get_stored_mtime", lambda wing, fp: None)

    called = {"n": 0}
    def fake_store(wing, fp, chunks, mtime):
        called["n"] += 1
        return len(chunks)
    monkeypatch.setattr(memory_core, "store_chunks", fake_store)

    result = memory_mine.mine_file(str(f), wing="global", verbose=False)
    assert result["status"] == "empty"
    assert called["n"] == 0
