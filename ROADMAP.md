# Roadmap

> Loose, demand-driven plan. Open issues / PRs to influence priority.

## Near-term (v0.2.x, ~ next 1-2 months)

These items have known designs and exist as Parking Lot entries from the
v0.1.0 ADRs (D001-D004). They will land as user feedback shapes priority.

- **Linux systemd integration** — `templates/systemd/` parallel to `templates/launchd/`.
  Currently macOS-only. Help wanted — `memory_daemon.py` itself is portable;
  only the supervisor wrapper needs a Linux equivalent.
- **Configurable embedding backend** — `bge-m3` is hard-coded in places.
  Define a clean `Embedder` protocol so users can swap in other models
  (multilingual-e5, nomic-embed, OpenAI text-embedding-3-small, etc.)
- **Pre-push sanitize hook** for maintainers contributing back upstream
  (already implemented locally; documenting + bundling for contributors).
- **Sparse store backend swap** — current `sqlite + bge-m3 lexical_weights` is
  optimized for our scale; large-scale users may want SPLADE or BM25 + Tantivy.
- **Tier 1 LLM tier polish** — a sample `transformers`-based small model
  config so non-Mac users have a working LLM-assist path out of the box.

## Mid-term (v0.3 - v0.5, contingent on user demand)

These items address gaps mentioned in [docs/BENCHMARKS.md § 8](docs/BENCHMARKS.md):

- **Cross-machine sync** — current design is external-disk cold backup.
  Real-time sync (cloud-storage-backed) is an explicit non-goal for the
  flagship version, but a community contribution backend (e.g., for users
  who accept the privacy trade-off) is welcome.
- **Read-only web UI** — namespaces dashboard, lifecycle browser, metrics
  visualizer. FastAPI + simple SPA stack preferred.
- **Adversarial robustness layer** — prompt-injection defenses for the
  LLM-assist path (classification + summarization).
- **More language benchmarks** — currently Chinese + English mix only.
  PRs welcome for Japanese, Korean, Arabic, etc.

## Long-term / aspirational (v1.0+)

These are direction-setting items, not commitments:

- **Production-grade SLA**: HA daemon (active-active or fast failover),
  documented latency budgets, error budget policies
- **Multi-user concurrent access** on a single host (not multi-tenancy in
  the SaaS sense; just shared namespaces across team members on a NAS)
- **Plug-in architecture** for AI tool integrations beyond Claude Code
  (Cursor, Codex, OpenCode, Cody, etc.) without per-tool maintainer load
- **Formal verification of cross-project guard** — currently regex + hook,
  could be model-checked

## What we will NOT do

These have been considered and rejected:

- **Cloud-hosted SaaS version**: violates the local-first promise. If you
  want cloud-hosted agent memory, use [mem0](https://mem0.ai) or
  [Letta](https://letta.com); they're better at it.
- **Default telemetry**: see above. Local-only metrics are the contract.
- **Multi-tenancy in the SaaS sense**: not our problem space.
- **Closed-source enterprise features**: MIT, all features, all the time.

## Influencing the roadmap

- File an issue with a use case description
- Open a PR for items in "Near-term"
- For "Mid-term" items, please open an issue first to discuss design before
  spending time on a PR
- "Long-term" items are open — discuss in issues, but expect slower iteration

## Cadence

- Patch releases (`v0.1.x`): as bugs come in
- Minor releases (`v0.2`, `v0.3`): quarterly when something noteworthy lands
- Major releases (`v1.0`): after sufficient real-world usage. No deadline.
