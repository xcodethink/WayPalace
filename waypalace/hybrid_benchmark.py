#!/usr/bin/env python3
"""hybrid_benchmark.py — Phase 2b A/B benchmark: dense-only vs hybrid retrieval.

Runs 8 carefully chosen queries (4 zh + 2 en + 2 mixed) through both paths and
reports:
  - Top-5 chunk_id overlap (how different are the results)
  - Top-1 source (qualitative: is the result more relevant?)
  - Latency delta
  - "部署铁律" zero-hit regression check (the Pre-flight blocker)

Exit code 0 if at least: hybrid Top-1 differs from dense for ≥3 queries,
indicating the sparse signal is actually changing results (not a no-op).
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import memory_search


CASES = [
    # (label, query, expected_keyword_in_top1_source)
    # 中文
    ("zh-deploy-iron-law",      "部署铁律",           "部署"),
    ("zh-memory-system-config", "记忆系统配置",        "memory"),
    ("zh-hard-brake",           "硬刹车铁律",         "硬刹车"),
    ("zh-bge-m3",               "嵌入模型 bge-m3",    "bge-m3"),
    # 英文/术语
    ("en-oauth-deploy",         "OAuth deployment",   "OAuth"),
    ("en-mp-mine",              "mp-mine command",    "mp-mine"),
    # 中英混合
    ("mix-cf-secret",           "Cloudflare 部署密钥", "Cloudflare"),
    ("mix-claude-mcp",          "Claude Code MCP server", "MCP"),
]


def run_one(query: str, hybrid: bool) -> tuple[list[str], list[str], float]:
    """Returns (chunk_ids_top5, source_names_top5, latency_sec)."""
    t0 = time.time()
    result = memory_search.search_isolated(
        query=query,
        current_wing=None,
        n_results=5,
        threshold=0.4,
        detail_level="full",
        hybrid=hybrid,
    )
    elapsed = time.time() - t0
    items = result.get("results", []) or []
    return [r["chunk_id"] for r in items], [r.get("source_name", "?") for r in items], elapsed


def main() -> int:
    print("=" * 80)
    print("  Phase 2b Hybrid Retrieval Benchmark — 8 cases")
    print("=" * 80)

    top1_diff_count = 0
    overlap_avg = 0.0
    dense_latency_total = 0.0
    hybrid_latency_total = 0.0
    zero_hit_dense = 0
    zero_hit_hybrid = 0

    for label, query, _expected in CASES:
        print()
        print(f"━━ [{label}] query: \"{query}\"")
        d_ids, d_src, d_lat = run_one(query, hybrid=False)
        dense_latency_total += d_lat
        if not d_ids:
            zero_hit_dense += 1
        h_ids, h_src, h_lat = run_one(query, hybrid=True)
        hybrid_latency_total += h_lat
        if not h_ids:
            zero_hit_hybrid += 1

        overlap = len(set(d_ids) & set(h_ids))
        overlap_avg += overlap
        top1_changed = (d_ids[:1] != h_ids[:1])
        if top1_changed:
            top1_diff_count += 1

        print(f"  dense ({d_lat*1000:.0f} ms):")
        for i, (cid, src) in enumerate(zip(d_ids, d_src), 1):
            print(f"    [{i}] {src[:50]:50s}  {cid[:30]}")
        print(f"  hybrid ({h_lat*1000:.0f} ms):")
        for i, (cid, src) in enumerate(zip(h_ids, h_src), 1):
            mark = "*" if cid not in d_ids else " "
            print(f"    [{i}] {mark} {src[:48]:48s}  {cid[:30]}")
        print(f"  → top-5 overlap: {overlap}/5, top-1 changed: {top1_changed}")

    n = len(CASES)
    print()
    print("=" * 80)
    print(f"  Summary")
    print("=" * 80)
    print(f"  Top-1 changed by hybrid:  {top1_diff_count}/{n} cases")
    print(f"  Avg top-5 overlap:        {overlap_avg/n:.2f}/5")
    print(f"  Dense avg latency:        {dense_latency_total/n*1000:.0f} ms/query")
    print(f"  Hybrid avg latency:       {hybrid_latency_total/n*1000:.0f} ms/query")
    print(f"  Hybrid extra latency:     {(hybrid_latency_total-dense_latency_total)/n*1000:.0f} ms/query")
    print(f"  Zero-hit (dense):         {zero_hit_dense}")
    print(f"  Zero-hit (hybrid):        {zero_hit_hybrid}")
    print()

    pass_threshold = top1_diff_count >= 3 and zero_hit_hybrid <= zero_hit_dense
    if pass_threshold:
        print("  [PASS] hybrid changes top-1 in >=3 cases AND no regression on zero-hits")
        return 0
    else:
        print("  [FAIL] hybrid not changing enough results — investigate")
        return 1


if __name__ == "__main__":
    sys.exit(main())
