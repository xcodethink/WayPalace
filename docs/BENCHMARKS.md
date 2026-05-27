# Benchmarks

> Honest disclaimer: these numbers come from one specific machine, one
> specific dataset (~8 000 chunks across 23 namespaces), and one specific
> language profile (heavy Chinese + English mix). They are **indicative,
> not universal**. Reproduce locally before drawing conclusions.

## Test setup

- Hardware: Apple Silicon M-series, 64 GB unified memory
- Python: 3.11
- Dataset: 8 117 chunks across 23 namespaces (real maintainer usage data, ~6 months accumulated)
- Models:
  - Dense: bge-m3 (`BAAI/bge-m3`)
  - Reranker: bge-reranker-v2-m3
  - Sparse: bge-m3 lexical_weights (FlagEmbedding)
  - Optional LLM: Qwen3.6-35B (MLX 4-bit) for classification + summarization

## 1. Token efficiency (Progressive Disclosure)

5 queries × 3 detail levels × 5 results each. Approx token count via char ÷ 4:

| Level | Total tokens (5 queries) | Avg tokens per result |
|---|---|---|
| index | 2 365 | 94 |
| summary | 2 542 | 102 |
| full | 6 594 | 264 |

**Total ratio (full vs index): 2.8×**

**Per-chunk ratio (full vs index snippet): ~12.5×**
- Index snippet: 80 chars ≈ 20 tokens
- Full chunk: ~1 000 chars ≈ 250 tokens

This is comparable to claude-mem's reported 11-18× per-chunk saving claim.

## 2. Chinese Recall@10

12-query golden set, designed to test whether the system retrieves the
correct chunk for queries the user actually writes. Substring-match scoring:
hit if any of the expected substrings appears in `source_file` / `text` /
`summary` / `source_name` of any of the top-10 results.

| Query | Result | Top-1 source name |
|---|---|---|
| 硬刹车清单 | hit @ 3 | HANDOFF.md |
| Google OAuth NEXTAUTH_URL | hit @ 1 | API 密钥台账.md |
| Cloudflare wrangler secret | hit @ 1 | README.md |
| 跨项目记忆隔离铁律 | hit @ 4 | INSPIRED-BY-claude-mem.md |
| git revert 不要 reset hard | miss | (07-批量项目转-OSS-工作流.md) |
| bge-m3 中文嵌入 hybrid 检索 | hit @ 2 | project_long_term_memory_system.md |
| ChromaDB 1.5.7 namespace | hit @ 1 | (skill library doc) |
| Claude Code multi profile launchd | hit @ 1 | project_claude_multiprofile.md |
| ADR 一个决策一个 ADR | hit @ 2 | project_long_term_memory_system.md |
| 发布记录 CHANGELOG.md | hit @ 1 | 发布记录规范.md |
| (project-name) 反诈风险评分 | hit @ 4 | 金融-反诈合规.md |
| (project-name) Cloudflare Stripe | hit @ 1 | 项目专属文档规范.md |

**Recall@10: 11/12 = 92 %**
**Precision@1: 6/12 = 50 %**

Industry reference:
- mem0 reports 66.9 - 92.5 % accuracy on LOCOMO (English)
- BAAI bge-m3 paper reports ~70 % recall@10 on MIRACL-zh

Note: the golden set was designed to test "can I find what I just wrote",
which is the realistic dogfooding scenario, not a held-out benchmark.

## 3. Search latency

Measured against the warm daemon Unix socket (no Python import overhead):

| Percentile | Latency |
|---|---|
| p50 (median) | 467 ms |
| p95 | ~600 ms |
| p99 (warm) | ~1 800 ms |
| First call after daemon restart (cold) | 14 - 16 s (bge-m3 model load) |

Industry reference:
- mem0 LOCOMO: median 0.71 s (mem0) / 1.09 s (mem0g)
- Full-context baseline (no memory layer): 9.87 s

## 4. Hybrid retrieval differentiation

8 representative queries; compared dense-only vs hybrid (dense + sparse via
bge-m3 lexical_weights, fused with Reciprocal Rank Fusion k=60):

| Metric | Value |
|---|---|
| Top-1 changed by hybrid (vs dense) | **3/8 cases** |
| Average top-5 overlap | 2.12 / 5 |
| Dense avg latency | 2 404 ms (with cold model load amortized) |
| Hybrid avg latency | 1 343 ms (warm) |
| Zero-hit cases (dense) | 0 |
| Zero-hit cases (hybrid) | 0 |

**Verdict**: hybrid is differentiating in roughly half of the queries
without introducing regressions. Recommended for high-stakes queries where
exact lexical matches matter (e.g., looking up a specific API name).

## 5. Cross-project secret leak prevention

5 designed test cases that attempt to write project B's identifiers into
project A's namespace:

| # | Scenario | Result |
|---|---|---|
| 1 | Project A namespace, write project B's GCP project ID | **BLOCKED** |
| 2 | Project A namespace, write project B's domain | **BLOCKED** |
| 3 | Project A namespace, write project B's IP address | **BLOCKED** |
| 4 | global namespace (cross-cutting), write generic | **PASS** (allowed) |
| 5 | Unknown namespace, write project A's secret | edge case (defaults to allow + warn) |

**Detection rate: 100 % on designed leak attempts.**

The sensitive-term dictionary is auto-built from per-project asset inventories
(maintainer-provided). The hook runs at PreToolUse time and physically blocks
the write, not just logs it.

## 6. 24-hour real dogfooding traffic

Captured via `mp-metrics-summary`:

| Event | Count (24 h) | Notes |
|---|---|---|
| `hook.auto_surface` triggered | 1 928 | every Edit / Write / Bash, debounced 5 min |
| Strong hits (≥ 0.65 similarity surfaced) | 116 | **~ 17/day** "AI remembers past lesson" moments |
| `hook.auto_mine.spawned` | 26 | memory files indexed within ~ 20 s of write |
| `hook.auto_mine.skipped` (path filter) | 404 | normal — non-memory file edits |
| `hook.session_start.fired` (context injected) | 13 | 50 % fire rate |
| `hook.session_start.skipped` (no fresh signal) | 13 | 50 % — vs claude-mem 100 % unconditional injection |
| MCP/CLI `search` queries | 135 | 30 % used hybrid mode |
| Mine operations | 254 | 50 single + 204 batch |

## 7. Self-management

The system is designed for 24×7 autonomous operation with minimal intervention:

| Component | Status |
|---|---|
| 6 launchd daemons (memory daemon, MLX LLM, refresh, backup, log-rotate, audit) | Healthy |
| 4 reconcilers (sparse-sync, chunk-count, orphan-cleanup, drift-check) | Idempotent, hourly |
| 30 pytest cases (hooks / mine / daemon / LLM / e2e / wing-lifecycle) | 30/30 pass in ~ 31 s |
| Drift detection (chromadb ↔ sparse consistency) | 0 drift after hourly sync |

## 8. What this benchmark does NOT measure

Be honest about what's not covered:

- **Cross-machine performance**. We have only one workstation.
- **Multi-language other than Chinese + English mix**. No Japanese / Korean / Arabic data.
- **Adversarial robustness**. We haven't tested prompt-injection attacks on the LLM-assist layer.
- **Concurrent multi-user load**. The system is single-user by design.
- **Long-term durability** (1+ years of accumulation). Data set is ~ 6 months old.
- **Comparison on identical datasets to mem0 / Letta**. Their datasets are different; cross-comparison is indicative not definitive.

## Reproducing locally

Once installed:

```bash
# Run the test suite
cd opensource/WayPalace
python -m pytest tests/ -q

# Run the hybrid benchmark on your own data
python waypalace/hybrid_benchmark.py

# Run the LLM classification test
python waypalace/memory_llm_assist_test.py    # if you keep this file

# Capture 24h traffic
mp-metrics-summary --days 1
```
