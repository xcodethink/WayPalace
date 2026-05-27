# WayPalace

> Local-first, multi-signal long-term memory for AI coding assistants.
> Chinese-optimized hybrid retrieval. Zero telemetry by design.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#)

---

## What is WayPalace?

WayPalace is a long-term memory system designed to give AI coding assistants
(Claude Code, Cursor, Codex, etc.) the ability to:

- **Remember across conversations and projects** — your preferences, lessons,
  decisions, and project state survive between sessions, no manual setup needed
- **Isolate per-namespace** while sharing general knowledge — project A's
  secrets never leak into project B's memory, but general lessons (deploy
  rules, debugging patterns) are cross-cutting
- **Run 100% locally** with zero telemetry egress — your memory data never
  leaves your machine
- **Stay self-managed** — six launchd daemons + four reconcilers + 30 tests
  keep the system healthy 24/7 without manual intervention

Built on ChromaDB + bge-m3 (Chinese-optimized embedding) + optional
Qwen3.6-35B (MLX) for LLM-assisted classification and summarization.

## Why "way" + "palace"?

A nod to Sherlock Holmes' Mind Palace, but local-first and engineered.
Memories live in a structured palace of namespaces ("wings"), each with its
own lifecycle, that you walk through when you need to recall.

## Key differentiators

Versus other agent-memory systems (mem0 / Letta / claude-mem):

| Feature | WayPalace | mem0 | Letta | claude-mem |
|---|---|---|---|---|
| Local-first / zero telemetry | ✅ by design | Optional (default cloud) | Self-host | ✅ |
| Chinese-optimized retrieval | ✅ (bge-m3 + RRF + reranker) | English-first | English-first | English-first |
| Multi-signal namespace lifecycle | ✅ (4-tier with asset-existence override) | ❌ | ❌ | ❌ |
| Cross-project secret leak prevention | ✅ (physical hook + sensitive dict) | ❌ (namespace routing only) | ❌ | ❌ |
| Auto-mine on file write | ✅ (PostToolUse hook) | ❌ (`client.add()` manual) | LLM self-edit | ❌ |
| Progressive disclosure (3 detail levels) | ✅ | ❌ | ❌ | ✅ (inspired) |
| Hybrid retrieval (dense + sparse + RRF) | ✅ | ✅ | — | — |
| Open ADRs documenting design rationale | ✅ (D001-D004) | — | — | — |

## Benchmarks (own measurements, see [docs/BENCHMARKS.md](docs/BENCHMARKS.md))

Tested on Apple Silicon M-series, 64 GB RAM, ~8 000 chunks across 23 namespaces:

| Metric | WayPalace | Notes |
|---|---|---|
| Recall@10 (Chinese golden set) | **92 %** | 12 queries, substring-match scoring |
| Precision@1 | 50 % | Top-1 exactness; top-3 reach is much higher |
| Token efficiency (per chunk, index vs full) | **~12.5×** | snippet ≈ 80 chars vs full ≈ 1 000 chars |
| Search p50 latency (warm daemon socket) | **467 ms** | mem0 LOCOMO ~710 ms |
| Search p95 latency (warm) | ~600 ms | First call after restart pays a 14 s cold start |
| Hybrid retrieval Top-1 differentiation | 3/8 cases | Compared to dense-only baseline |
| Cross-project guard (designed leak attempts) | **100 %** blocked | 5 designed test cases |

These numbers come from one specific dataset and one machine. Treat them as
indicative, not as a universal benchmark. The Chinese recall figure depends
heavily on the bge-m3 model's strengths.

## Quick start

```bash
# Clone
git clone https://github.com/xcodethink/WayPalace.git
cd WayPalace

# Install (Tier 0: no LLM assist, runs on any machine)
bash install.sh

# OR Install with optional local LLM (Mac 64GB+ recommended)
bash install.sh --tier=mlx

# Activate venv
source $HOME/.waypalace/venv/bin/activate

# First mine
mp-mine /path/to/your/notes/directory --namespace global

# Search
mp-search "your query here"
```

See [docs/INSTALL.md](docs/INSTALL.md) for full installation options, including
launchd daemon setup and optional Claude Code integration.

## Architecture in 30 seconds

```
                ┌──────────────────────────────────────────────┐
                │ Claude Code / Cursor / Codex (your AI tool)  │
                └──┬───────────────────────────────┬────────────┘
                   │ MCP / CLI                     │ Hooks (optional)
                   ▼                               ▼
              ┌─────────┐                  ┌──────────────────┐
              │ mp-*    │                  │ auto-mine hook   │
              │ search  │                  │ auto-surface hook│
              │ mine    │                  │ session-start    │
              └────┬────┘                  └────────┬─────────┘
                   ▼                                ▼
              ┌──────────────────────────────────────────┐
              │ memory daemon (Unix socket, warm)        │
              │  - bge-m3 embedder (dense)               │
              │  - bge-m3 sparse + RRF fusion (optional) │
              │  - bge-reranker                          │
              │  - aging boost + cross-project filter    │
              └────┬────────────────────┬─────────────────┘
                   ▼                    ▼
            ┌─────────────┐      ┌─────────────────┐
            │ ChromaDB    │      │ Sparse store    │
            │ (dense vecs)│ ←──→ │ (lexical wts)   │
            └─────────────┘      └─────────────────┘
                   │                    │
                   ▼                    ▼
              ┌──────────────────────────────────────────┐
              │ Wing metadata + lifecycle (sqlite3)      │
              │  active / dormant / stale / orphan       │
              └──────────────────────────────────────────┘
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture.

## Documentation

- [INSTALL.md](docs/INSTALL.md) — Installation, launchd daemon, Claude Code hooks
- [USAGE.md](docs/USAGE.md) — CLI reference (`mp-search`, `mp-mine`, `mp-wings-review`, etc.)
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — System architecture
- [BENCHMARKS.md](docs/BENCHMARKS.md) — Detailed benchmark methodology + results
- [CONTRIBUTING.md](docs/CONTRIBUTING.md) — How to contribute
- [decisions/](docs/decisions/) — Architecture Decision Records (D001-D004) documenting design rationale

## Status

**Alpha (v0.1.0)**. API may change. Recommended for early adopters who:
- Want to experiment with local-first agent memory
- Use Claude Code / Cursor / similar AI coding tools
- Have a Mac (Linux support planned but not tested), 16 GB+ RAM
- Are comfortable with CLI workflows

Not yet recommended for:
- Production agent deployments with SLA requirements
- Teams (multi-user concurrent access is single-machine for now)
- Users without Python familiarity

## Known limitations

- **Cold start 14-16 s** for the very first search after daemon restart;
  subsequent searches are sub-second
- **No web UI** — CLI and MCP only
- **Single-machine** — external-disk backup recommended for
  cross-machine continuity; no real-time multi-machine sync
- **bge-m3 model is ~2 GB**; optional Qwen3.6-35B MLX is ~20 GB resident
- **Linux launchd integration not yet implemented** (systemd planned)

## License

[MIT](LICENSE)

## Acknowledgments

- ChromaDB team for the vector store
- BAAI for bge-m3 and bge-reranker
- Alibaba Qwen team for Qwen3.6 + MLX team for Apple Silicon inference
- The [claude-mem](https://github.com/thedotmack/claude-mem) project for the
  progressive-disclosure pattern that we independently re-implemented
- Anthropic Claude Code team for the hooks API that makes auto-mine possible

## Contact / Issues

[github.com/xcodethink/WayPalace/issues](https://github.com/xcodethink/WayPalace/issues)
