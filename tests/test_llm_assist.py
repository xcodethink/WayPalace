"""test_llm_assist.py — 4 LLM failure-mode tests (D002 Part A.4).

These test the fallback behavior when the local mlx-llm daemon is slow,
broken, or returning garbage. The system must degrade gracefully — never
crash, always return a safe default.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import memory_llm_assist


CANDIDATES = ["global", "<project-a>", "<project-e>", "<project-c>"]


# ---------- 1. classify_wing timeout ----------

def test_classify_wing_timeout(mock_chat):
    """When _chat returns None (e.g. urlopen timeout), classify_wing falls back to global."""
    mock_chat.return_value = None  # simulates timeout/URLError-caught
    result = memory_llm_assist.classify_wing("某段内容", CANDIDATES)
    assert result == "global", f"timeout should fall back to global, got {result!r}"


# ---------- 2. classify_wing empty response ----------

def test_classify_wing_empty_response(mock_chat):
    """When LLM returns an empty string, JSON parsing fails → fallback to global."""
    mock_chat.return_value = ""
    result = memory_llm_assist.classify_wing("某段内容", CANDIDATES)
    assert result == "global", f"empty response should fall back, got {result!r}"


# ---------- 3. classify_wing invalid JSON ----------

def test_classify_wing_invalid_json(mock_chat):
    """When LLM returns malformed JSON, classify_wing must not crash; fallback to global."""
    mock_chat.return_value = "{wing: <project-a>, reason: 'broken json'"
    result = memory_llm_assist.classify_wing("某段内容", CANDIDATES)
    assert result == "global", f"invalid JSON should fall back, got {result!r}"


# ---------- 4. summarize_chunk service down ----------

def test_summarize_chunk_service_down(mock_chat):
    """When _chat returns None (service down), summarize_chunk must return empty or fallback,
    NEVER raise."""
    mock_chat.return_value = None
    # Should not raise
    try:
        out = memory_llm_assist.summarize_chunk("一段较长的文本内容用于摘要 " * 20, max_words=30)
    except Exception as e:
        pytest.fail(f"summarize_chunk raised {e!r} on service down")
    # Returns empty/falsy when LLM is unreachable — caller falls back to truncation upstream
    assert isinstance(out, str), f"must return str, got {type(out).__name__}"
    # On None response, current implementation returns "" (graceful degrade)
    assert out == "" or len(out) == 0, f"expected empty string fallback, got {out!r}"
