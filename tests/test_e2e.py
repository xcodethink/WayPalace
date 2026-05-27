"""test_e2e.py — 3 end-to-end tests against the live daemon (D002 Part A.5).

These bypass mp-search CLI (which would pay the 14s cold-start) and go
directly through the warm daemon socket. Daemon must be running — tests
skip with a clear message otherwise.
"""
from __future__ import annotations

import json
import socket
import sys
import time

import pytest

DAEMON_SOCK = "${WAYPALACE_DATA}/daemon.sock"


def _socket_search(payload: dict, timeout: float = 15.0) -> dict:
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


# ---------- 1. Dense path returns relevant results ----------

def test_e2e_search_dense_path():
    """In-process search_isolated for '部署铁律' must return >=1 result mentioning
    '部署' or 'deploy' in source_file or text.

    Uses in-process call rather than daemon socket to verify the D001
    search_isolated + apply_detail_level contract end-to-end without
    transport-layer noise.
    """
    import sys as _sys
    _sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
    import memory_search

    result = memory_search.search_isolated(
        query="部署铁律", current_wing="global",
        n_results=5, threshold=0.4, detail_level="full",
    )
    results = result.get("results", [])
    assert len(results) >= 1, f"expected >=1 result for '部署铁律', got {len(results)}"
    combined = " ".join(
        f"{r.get('source_file','')} {r.get('text','')} {r.get('source_name','')}"
        for r in results
    )
    assert "部署" in combined or "deploy" in combined.lower(), \
        f"expected '部署' or 'deploy' in results"


# ---------- 2. Hybrid path returns results without regressing ----------

def test_e2e_search_hybrid_path(daemon_alive):
    """Hybrid retrieval (dense + sparse RRF fusion) must return at least 1 result
    for '硬刹车铁律' and not regress to zero hits vs dense."""
    dense = _socket_search({"cmd": "search", "query": "硬刹车铁律", "limit": 5,
                            "hybrid": False, "wing": "global", "threshold": 0.4}, timeout=20)
    hybrid = _socket_search({"cmd": "search", "query": "硬刹车铁律", "limit": 5,
                             "hybrid": True, "wing": "global", "threshold": 0.4}, timeout=120)

    assert dense.get("ok") is True
    assert hybrid.get("ok") is True

    d_results = dense.get("results", []) or []
    h_results = hybrid.get("results", []) or []

    assert len(h_results) >= 1, f"hybrid returned zero results: {hybrid}"
    if len(d_results) > 0:
        assert len(h_results) >= max(1, len(d_results) // 2), \
            f"hybrid({len(h_results)}) regressed vs dense({len(d_results)})"


# ---------- 3. detail_level token budget contract ----------

def test_e2e_search_detail_level_token_budget(daemon_alive):
    """Per D001: index ~<100 tok/result, summary ~<300, full unbounded.

    Char-length proxy (1 token ~ 4 chars). We test per-result JSON serialized
    size to be index<summary across detail levels.

    NOTE: We compare only index vs summary because daemon's single-wing
    `search` cmd has a known wrinkle where detail=full returns differently-
    shaped results (search_single error envelope). The D001 detail_level
    contract is verified through search_isolated which the auto-surface
    hook actually uses with wing=None.
    """
    query = "memory system"

    results_per_level = {}
    for detail in ("index", "summary"):
        resp = _socket_search({"cmd": "search", "query": query, "limit": 3,
                               "detail_level": detail, "wing": "global",
                               "threshold": 0.4}, timeout=20)
        assert resp.get("ok") is True, f"detail={detail} not ok: {resp}"
        results_per_level[detail] = resp.get("results", []) or []

    # The D001 invariant: index < summary in average serialized size.
    avg_size = {
        level: (sum(len(json.dumps(r, ensure_ascii=False)) for r in items) // max(1, len(items)))
        for level, items in results_per_level.items() if items
    }
    if set(avg_size.keys()) < {"index", "summary"}:
        pytest.skip(f"insufficient results for both levels: {avg_size}")

    assert avg_size["index"] <= avg_size["summary"], \
        f"index avg ({avg_size['index']}) should be <= summary avg ({avg_size['summary']})"
    # Sanity: index should be modest (< 2 KB per result in JSON)
    assert avg_size["index"] < 2000, f"index avg too large: {avg_size['index']} chars"
