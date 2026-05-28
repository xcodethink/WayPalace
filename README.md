<div align="center">

# WayPalace

**Local-first long-term memory for AI coding assistants.**
Chinese-optimized hybrid retrieval. Zero telemetry by design.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status)
[![CI](https://github.com/xcodethink/WayPalace/actions/workflows/test.yml/badge.svg)](https://github.com/xcodethink/WayPalace/actions/workflows/test.yml)

**[English](README.md) · [中文](README.zh-CN.md)**

</div>

---

## The problem

Every new conversation with your AI coding assistant starts from zero.

- 🔁 You re-explain your project structure every Monday morning
- 🐛 You re-debug the same Cloud Run / OAuth / Cloudflare issue you fixed four months ago
- 🔓 You worry about pasting project A's secrets into project B's context — or worse, having the AI leak them
- 📝 You take careful memory notes... that nobody (not even you) searches at the right moment

Existing solutions miss the mark:

- **mem0 / Letta** push your data to their cloud, or require a heavyweight self-host setup
- **Vector databases** (Pinecone / Weaviate / Qdrant) are storage, not memory management — you build the indexing pipeline yourself
- **Manual notes** rot — written but never retrieved at the moment you need them
- **claude-mem** is a great start, but English-first and has no namespace lifecycle

## The WayPalace approach

A **local-first memory layer** for AI coding assistants. Five things it does for you:

### 🧠 Remembers what you write — automatically

When Claude Code writes a memory file (a `feedback_*.md` decision, a `project_*.md` note), WayPalace indexes it within ~20 seconds via a PostToolUse hook. No `client.add()` calls. No taxonomy decisions.

> When you fix a tricky OAuth bug today, write down what you learned. Next month when you hit the same issue on a different project, WayPalace surfaces it before you even ask.

### 🛡️ Isolates project secrets — strictly

Project A's GCP project IDs, API keys, and domain names cannot leak into project B's memory. A PreToolUse cross-project guard physically blocks writes that mix sensitive identifiers across namespace boundaries — not a soft filter, an actual block.

> No more accidentally pasting `projectA-prod` GCP project ID into projectB's deploy doc. 100% block rate on designed leak attempts.

### 🌐 Optimized for Chinese (and English)

The retrieval pipeline is bge-m3 dense + bge-m3 sparse + RRF fusion + bge-reranker. On a 12-query Chinese golden set, recall@10 is **92%** — comparable to mem0 on English LOCOMO. English queries work just as well.

> bge-m3 is the only mainstream embedding model that's first-class for both Chinese and English. WayPalace builds around that fact.

### 🏠 Stays 100% local — zero telemetry

Your memory data lives on your disk. No accounts. No signups. No quotas. No "free tier vs paid tier" mind games. `mp-metrics-summary` shows you exactly what's happening in your system, all from local JSONL files.

> Privacy is the contract, not a setting you can toggle.

### 🤖 Self-manages — six daemons + four reconcilers + 30 tests

Once installed, the system runs 24/7 without intervention:

- Six launchd daemons keep the memory daemon + LLM + refresh + backup + log rotation + audit alive
- Four hourly reconcilers keep ChromaDB ↔ sparse store ↔ namespace metadata consistent
- 30 pytest cases (you can run them anytime) catch regressions before they bite

> Install once. Forget it exists. Until you query.

## How it works in 30 seconds

```
   ┌──────────────────────────────────────────────────┐
   │ Claude Code / Cursor / Codex (your AI tool)      │
   └──┬─────────────────────────────────┬─────────────┘
      │ MCP / CLI                       │ Hooks (optional)
      ▼                                 ▼
   ┌─────────────┐                ┌────────────────────┐
   │ mp-* CLI    │                │ auto-mine hook     │
   │  search     │                │ auto-surface hook  │
   │  mine       │                │ session-start hook │
   └──────┬──────┘                └──────────┬─────────┘
          │ Unix socket                       │ Detached spawn
          ▼                                   ▼
   ┌──────────────────────────────────────────────────┐
   │ memory daemon (warm, launchd-managed)            │
   │   bge-m3 dense + sparse + RRF + bge-reranker     │
   │   aging boost · cross-project filter             │
   └──┬──────────────────┬──────────────────┬─────────┘
      ▼                  ▼                  ▼
   ┌──────────┐    ┌──────────────┐    ┌────────────────┐
   │ChromaDB  │↔   │Sparse store  │↔   │Namespace meta  │
   │HNSW dense│    │SQLite + bge  │    │SQLite, 4-tier  │
   └──────────┘    └──────────────┘    └────────────────┘
```

Three layers:

1. **Indexing** — PostToolUse hooks + hourly batch + manual `mp-mine` converge on one `store_chunks` path
2. **Retrieval** — Query → daemon socket → dense + (optional sparse + RRF) → bge-reranker → aging boost → return
3. **Lifecycle** — Namespaces auto-classified into `active` / `dormant` / `stale` / `orphan` with asset-existence override

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed picture and [docs/decisions/](docs/decisions/) for design rationale.

## Real workflows

### Scenario 1 — Avoiding a Cloud Run bug you fixed four months ago

**Without WayPalace:**

```
You: gcloud run deploy --port=8080 --healthcheck-path=/healthz
[deploys, health check returns 404]
You: ...wait, didn't I hit this before?
[30 minutes of Stack Overflow + grep through old projects]
You: Right! Cloud Run intercepts /healthz at the GFE layer.
```

**With WayPalace** — PreToolUse hook scores 0.78 similarity to a past memory:

```
You: gcloud run deploy --port=8080 --healthcheck-path=/healthz

💡 WayPalace surfaced from your memory:
   "Cloud Run intercepts /healthz at the GFE layer and returns 404 directly
    (does not forward to the container). Use /api/health or similar.
    [Solved 2026-01-18 on ProjectA]"

You: Right. --healthcheck-path=/api/health
```

### Scenario 2 — Monday-morning context restoration

You work on five+ projects. Each Monday, you spend 20 minutes telling Claude Code what you were doing on Friday.

**Without WayPalace:**

```
You: Help me with the OAuth flow for ProjectA
AI: Sure, can you remind me which auth library you use?
You: <re-explains for 5 minutes>
AI: OK. The default config is...
You: No, we use the override. Let me find the doc...
```

**With WayPalace** — SessionStart hook injects fresh project context when your cwd is inside a project directory:

```
[New conversation in ~/Developer/ProjectA]
[SessionStart hook detects current-task.md + recent conversation-log + HANDOFF.md]

You: Help me with the OAuth flow for ProjectA
AI: I see from your current task and recent decisions that you're using
    NextAuth.js with a Cloudflare Workers callback override. Last week
    you noted that NEXTAUTH_URL must match the deployed domain or callbacks
    silently fail. What aspect of the flow are you working on?
You: Yes, exactly. Now I need to add the refresh-token flow...
```

### Scenario 3 — Cross-project knowledge transfer

You learn something on project A that applies to projects B, C, D too.

WayPalace's `global` namespace holds cross-cutting lessons (deploy rules, debugging patterns, language gotchas). Per-project namespaces hold project-specific stuff (this project's secrets, this project's idioms).

When you search:

- From inside project A's directory → WayPalace queries `projectA + global` (A-specific + cross-cutting)
- Explicit cross-project search (`mp-search-all`) → queries everything but warns you about namespace mixing

The auto-classification LLM decides which namespace each new memory belongs to. Wrong calls happen ~1% of the time and can be reclassified with `mp-wing-archive` + write a salvaged version into the right namespace.

## WayPalace vs alternatives

| Feature | WayPalace | mem0 | Letta | claude-mem |
|---|---|---|---|---|
| Local-first / zero telemetry | ✅ by design | Optional (default cloud) | Self-host | ✅ |
| Chinese-optimized retrieval | ✅ (bge-m3 + RRF + reranker) | English-first | English-first | English-first |
| Multi-signal namespace lifecycle | ✅ (4-tier with asset-existence override) | ❌ | ❌ | ❌ |
| Cross-project secret leak prevention | ✅ (physical hook + sensitive dict) | ❌ (routing only) | ❌ | ❌ |
| Auto-mine on file write | ✅ (PostToolUse hook) | ❌ (manual `add()`) | LLM self-edit | ❌ |
| Progressive disclosure (3 detail levels) | ✅ | ❌ | ❌ | ✅ (inspired by) |
| Hybrid retrieval (dense + sparse + RRF) | ✅ | ✅ | — | — |
| Open ADRs documenting design rationale | ✅ (D001-D004) | — | — | — |

WayPalace is **not** trying to displace mem0 / Letta in their sweet spots (cloud-hosted SaaS / agent self-editing). It's the right choice when you want **local-first, Chinese-friendly, hands-off** memory for AI coding workflows.

## Get started

Choose your install tier:

### 🚀 Tier 0 — Anywhere (no LLM)

Fast retrieval without auto-classification or summarization. Works on any machine with Python 3.11+ and 4 GB free RAM.

```bash
git clone https://github.com/xcodethink/WayPalace.git
cd WayPalace
bash install.sh
```

### ⚡ Tier 1 — Small local LLM

Auto-classification + summarization on a modest machine. ~ 8 GB RAM.

```bash
bash install.sh --tier=small
```

### 🔥 Tier 2 — Full local stack (Mac 64 GB+ recommended)

The full experience: Qwen3.6-35B via Apple MLX for nuanced classification and summarization. Tested on Apple Silicon.

```bash
bash install.sh --tier=mlx
```

### ☁️ Tier 3 — Bring your own API

OpenAI / Anthropic / Groq / any OpenAI-compatible endpoint.

```bash
bash install.sh --tier=external
```

### First mine and search

```bash
source $HOME/.waypalace/venv/bin/activate
mp-mine /path/to/your/notes/directory --namespace global
mp-search "your query here"
```

### Optional: Claude Code integration

Three hooks (`auto-mine`, `auto-surface`, `session-start`) make WayPalace much more useful — see [docs/INSTALL.md § Claude Code hooks](docs/INSTALL.md).

## Benchmarks at a glance

On Apple Silicon, ~ 8 000 chunks across 23 namespaces (see [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for methodology and full data):

| Metric | WayPalace | Industry reference |
|---|---|---|
| Recall@10 (Chinese golden set) | **92 %** | mem0 LOCOMO 67-92 % (English) |
| Precision@1 | 50 % | top-3 reach much higher |
| Token saving per chunk (`index` vs `full`) | **~ 12.5×** | claude-mem reports 11-18× |
| Search p50 latency (warm daemon) | **467 ms** | mem0 LOCOMO 710 ms / mem0g 1090 ms |
| Hybrid retrieval top-1 differentiation | 3/8 cases | dense-only baseline |
| Cross-project guard | **100 %** blocked | no comparable feature in alternatives |
| pytest | **30/30** in ~ 31 s | — |

These numbers come from one machine and one dataset. Reproduce locally with `python -m pytest tests/` and `python waypalace/hybrid_benchmark.py`.

## What WayPalace is NOT

Setting expectations clearly:

- 🚫 **Not cloud-hosted SaaS** — for hosted memory with a web dashboard, use mem0 or Letta; they're better at it
- 🚫 **Not multi-tenant** — single-machine, single-user design; teams should look elsewhere
- 🚫 **Not production-ready for SLA agents** — alpha quality, APIs may change
- 🚫 **Not Linux-tested** — daemon code is portable, but launchd is macOS; systemd templates are shipped untested, PRs welcome
- 🚫 **Not a replacement for your project documentation** — WayPalace complements docs, doesn't replace them
- 🚫 **No web UI** — CLI + MCP only

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — installation, launchd daemon, Claude Code hooks
- [docs/USAGE.md](docs/USAGE.md) — CLI reference (`mp-search`, `mp-mine`, `mp-wings-review`, etc.)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system architecture
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — detailed methodology + results
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — how to contribute
- [docs/decisions/](docs/decisions/) — Architecture Decision Records (D001-D004)
- [ROADMAP.md](ROADMAP.md) — what's coming, what's explicitly NOT planned

## Community

- 🐛 [Issues](https://github.com/xcodethink/WayPalace/issues) — bug reports, feature requests, questions
- 💬 [Discussions](https://github.com/xcodethink/WayPalace/discussions) — design conversations, show-and-tell
- 🤝 PRs welcome — see [CONTRIBUTING.md](docs/CONTRIBUTING.md)

## Status

**Alpha (v0.1.0)**. Recommended for early adopters who:

- Want to experiment with local-first agent memory
- Use Claude Code / Cursor / similar AI coding tools
- Have a Mac (Linux planned), 16 GB+ RAM
- Are comfortable with CLI workflows

## Acknowledgments

- [ChromaDB](https://www.trychroma.com/) — the vector store
- [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) — the embedding model that makes Chinese retrieval first-class
- [BAAI/bge-reranker](https://huggingface.co/BAAI/bge-reranker-v2-m3) — the cross-encoder reranker
- [Qwen](https://github.com/QwenLM/Qwen3) team for the classification LLM
- [MLX](https://github.com/ml-explore/mlx) team for Apple Silicon inference
- [claude-mem](https://github.com/thedotmack/claude-mem) for the progressive-disclosure pattern that inspired ours
- The Anthropic Claude Code team for the hooks API

## License

[MIT](LICENSE)
