# Architecture

For the design rationale behind each component, see the
[Architecture Decision Records](decisions/).

## Layered overview

```
┌─────────────────────────────────────────────────────────────┐
│  AI tool (Claude Code / Cursor / Codex / your own agent)    │
└──┬──────────────────────────────────────────────┬───────────┘
   │ MCP / CLI                                    │ Optional hooks
   │ (synchronous query)                          │ (event-driven)
   ▼                                              ▼
┌──────────────────────┐                ┌────────────────────────┐
│  mp-* CLI binaries   │                │ Claude Code hooks      │
│  - mp-search         │                │ - auto-mine (PostUse)  │
│  - mp-mine           │                │ - auto-surface (PreUse)│
│  - mp-wings-review   │                │ - session-start        │
│  - mp-metrics-summary│                └────────────────────────┘
└──────────┬───────────┘                            │
           │ Unix socket / direct import            │ Detached spawn
           ▼                                        ▼
┌──────────────────────────────────────────────────────────────┐
│  memory_daemon (long-running, launchd-managed)               │
│   - Pre-loaded bge-m3 encoder                                │
│   - Pre-loaded bge-reranker (cross-encoder)                  │
│   - Optional bge-m3 sparse embedder (lazy-loaded)            │
│   - Aging boost + cross-project filtering                    │
└──────────┬─────────────┬─────────────┬──────────────────────┘
           │             │             │
           ▼             ▼             ▼
┌──────────────┐  ┌──────────────┐  ┌───────────────────┐
│ ChromaDB     │  │ Sparse store │  │ Wing metadata     │
│ (HNSW dense) │  │ (sqlite +    │  │ (sqlite, separate │
│              │  │  lexical wts)│  │  to avoid locks)  │
└──────────────┘  └──────────────┘  └───────────────────┘
```

## Three retrieval modes

1. **Dense (default)**: query → bge-m3 embedding → ChromaDB HNSW → top-N by cosine → bge-reranker → return
2. **Hybrid (opt-in `--hybrid`)**: parallel dense + sparse (bge-m3 lexical_weights) → RRF fusion (k=60) → bge-reranker → return
3. **Fast (PreToolUse hook only)**: dense only, no reranker, lower latency for inline hook use

## Three indexing tiers

1. **Tier 1 — hourly batch**: a `refresh-batch.sh` script runs every hour
   via launchd, scans configured directories with mtime-based incremental
   logic. Backbone for completeness.

2. **Tier 2 — PostToolUse hook**: when Claude Code writes a memory file,
   the hook detached-spawns `mp-mine` so the file appears in ChromaDB ~ 15-25 s
   later. Accelerator.

3. **Tier 3 — manual**: `mp-mine /path` for ad-hoc ingestion.

All three converge on the same `store_chunks` path which writes to ChromaDB
and updates wing metadata (last_mine_at).

## Sparse store reconciliation

`store_chunks` writes only to ChromaDB. The sparse store is synced
asynchronously by `sparse_sync.py` (called from the hourly batch).

This is an "async sidecar" pattern (Stripe / Airbnb-style data infrastructure):
the primary store is updated synchronously, the secondary store catches up
hourly via an idempotent reconciler. This avoids paying the 60 s bge-m3
sparse model cold-start cost on every `mp-mine` invocation.

The reconciler:
1. Lists chunk IDs in ChromaDB and in sparse store
2. Computes set differences (orphans / missing)
3. Deletes orphans from sparse store
4. Encodes + writes any missing chunks via bge-m3 sparse pass

When the two stores are in sync, the reconciler exits in ~ 2 s without
loading bge-m3 (just metadata diffing).

## Wing (namespace) lifecycle

The `wing_meta` SQLite table tracks per-namespace:
- `created_at`, `last_mine_at`, `last_search_at`
- `chunk_count` (lazy-updated by `mp-wings-review` via free piggyback on live count)
- `source_dir`, `source_machine` (heuristic-inferred during backfill)
- `archived` (soft-delete flag)

The 4-tier classifier `classify_status(wing_row, missing_ratio, asset_exists)`
combines multiple signals:

```
asset_exists = True  AND any orphan signal → dormant (instead of orphan)
missing_ratio ≥ 0.8                         → orphan
last_active never                           → orphan
age > 365 days                              → orphan
age > 180 days                              → stale
age > 90 days                               → dormant
age ≤ 90 days                               → active
```

The **asset-existence override** is the most important design choice: if the
project's source code directory still exists on disk, the namespace cannot
be auto-marked orphan, even if no source file in ChromaDB is reachable
(perhaps the user hasn't run Claude Code in that directory yet on this
machine). This is industry-standard multi-signal classification, in contrast
to single-signal age-based classification that would wrongly archive 16 live
projects in our reference data set.

See [D003](decisions/D003-wing-lifecycle-management.md) for the full
rationale + the v1.1 amendment.

## Observability

Every hook fire and every search / mine call emits a JSON line to
`$WAYPALACE_DATA/metrics/YYYY-MM-DD.jsonl`. The schema is documented in
`waypalace/mp_metrics.py`.

`mp-metrics-summary` aggregates these into hit rates, latency percentiles,
and recent errors. **Zero external telemetry** — the metrics file lives
entirely on your machine.

## Multi-process safety

- ChromaDB writes use `fcntl` advisory file lock (`mine.lock`) to serialize
  Tier 1 + Tier 2 + manual mine attempts
- The 3-process concurrent mine test in `tests/test_daemon.py` validates this

## Security defenses

1. **File-name blacklist** — `.env`, secrets.*, *.key, etc. are never mined
2. **Content scan** — chunks containing regex matches for API keys / private
   keys are rejected before storage
3. **Cross-project guard** — `cross-project-guard.py` (Claude Code hook) blocks
   Edit/Write/MultiEdit that would write namespace B's identifiers into
   namespace A's content. Uses an auto-built sensitive dictionary.

## ADR map

| ADR | Topic |
|---|---|
| [D001](decisions/D001-progressive-disclosure-and-hybrid-retrieval.md) | Progressive disclosure (3 detail levels) + Hybrid retrieval (bge-m3 native) + Conditional SessionStart + Full corpus re-mine |
| [D002](decisions/D002-test-suite-and-metrics.md) | Test suite (30 cases) + Observability (mp-metrics) |
| [D003](decisions/D003-wing-lifecycle-management.md) | Wing lifecycle management with v1.1 multi-signal classifier |
| [D004](decisions/D004-tech-debt-cleanup.md) | Tech debt cleanup: async-sidecar sparse sync, per-hook-type metrics, test isolation |

Future ADRs will continue this format. Each describes:
- Context / decision drivers
- Decision + alternatives considered
- Implementation findings
- Verification + rollback plan
- Lessons learned
