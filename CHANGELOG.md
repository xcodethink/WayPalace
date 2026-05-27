# Changelog

All notable changes to WayPalace will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-28

Initial public release. Pre-alpha quality — APIs may change.

### Added

- **Core retrieval engine** (D001)
  - ChromaDB-backed vector store with bge-m3 Chinese-optimized embeddings
  - Progressive disclosure: 3 detail levels (`index` / `summary` / `full`) for token-budget-aware retrieval
  - Conditional SessionStart hook with multi-trigger logic (3-OR rule), in contrast to unconditional context injection
  - Hybrid retrieval pipeline: bge-m3 dense + sparse lexical (RRF fusion) + bge-reranker (Phase 2b)

- **Observability** (D002)
  - 30-case pytest suite covering hooks / mine / daemon / LLM assist / e2e
  - Local-only JSONL event metrics (`mp-metrics-summary`), zero telemetry egress

- **Namespace lifecycle** (D003)
  - 4-tier wing classification: `active` / `dormant` / `stale` / `orphan`
  - Multi-signal classification combining missing-source ratio, last-activity age, and asset existence — overrides historical signals when the underlying project asset is still alive
  - Auto-grow: new project directories detected via glob, no manual registration
  - Manual shrink: archive → review → delete flow with safety dumps in JSONL

- **Tech-debt cleanup** (D004)
  - Async-sidecar pattern for sparse store consistency: hourly idempotent reconciler `sparse_sync.py` (Stripe/Airbnb-style data-infra pattern), avoiding 60s bge-m3 cold-start in hot path
  - Per-hook-type metric aggregation (auto_surface hit-rate vs auto_mine fail-rate, instead of generic "success rate")
  - Test isolation: pytest fixtures monkeypatch private audit log paths

- **CLI tools**
  - `mp-search` / `mp-search-all` — retrieve with 3 detail levels + hybrid flag
  - `mp-mine` — index files, opt-in LLM classification + summarization
  - `mp-status` / `mp-health` — system snapshot + invariant audit
  - `mp-wings-review` / `mp-wing-inspect` / `mp-wing-archive` / `mp-wing-delete` — namespace lifecycle
  - `mp-metrics-summary` — 7-day trend report

- **Claude Code integration (optional)**
  - PostToolUse hook: auto-index newly written memory files into ChromaDB within ~20s
  - PreToolUse hook: surface high-confidence past memories (>= 0.65 similarity) before Edit/Write/Bash actions
  - SessionStart hook: inject project-specific context conditionally on cwd + fresh task/HANDOFF/log signals

- **Architecture Decision Records (ADRs)**
  - D001 — Progressive Disclosure + Hybrid Retrieval + Conditional SessionStart + Full Corpus Re-mine
  - D002 — Test Suite + Observability
  - D003 — Wing Lifecycle Management (with v1.1 amendment for multi-signal classification)
  - D004 — Tech Debt Cleanup (cross-cutting hygiene)

### Benchmarks (own measurements; see docs/BENCHMARKS.md for methodology)

- Chinese Recall@10: 92% (12 query golden set)
- Token savings per chunk: ~12.5x (index vs full)
- Search latency: p50 467ms (warm daemon socket)
- Hybrid benchmark: Top-1 changes vs dense in 3/8 cases
- Cross-project leak detection: 100% on 5 designed test cases

### Known limitations

- Single-machine, single-user design; no built-in multi-tenancy across users
- Heavy dependencies (bge-m3 ~2GB, optional Qwen3.6 ~20GB MLX) require 16GB+ RAM minimum
- macOS launchd integration tested; Linux systemd integration planned (not yet)
- No web UI; CLI + MCP only
- Cold-start latency 14-16s for first query after daemon restart (warm path: <1s)

### License

MIT
