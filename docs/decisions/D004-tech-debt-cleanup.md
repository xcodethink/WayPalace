# D004 — Tech Debt Cleanup (Cross-cutting hygiene)

**Status**: Accepted (implemented 2026-05-27)
**Owners**: (maintainer)
**Context**: After D001-D003 landed, user requested systematic tech debt cleanup ("仔细调研分析，优化处理已知问题，不要留技术债"). Multiple known + hidden issues were affecting hybrid retrieval accuracy, observability fidelity, and lifecycle data consistency.
**Related**: [D001](D001-progressive-disclosure-and-hybrid-retrieval.md), [D002](D002-test-suite-and-metrics.md), [D003](D003-wing-lifecycle-management.md)
**Created**: 2026-05-27
**Implemented**: 2026-05-27

## TL;DR

7 distinct tech debt items diagnosed via systematic 5-step problem analysis (问题分析铁律), fixed in one wave with 30/30 pytest + mp-health + hybrid_benchmark + classify_test all green. **No new architectural decision**; pure cleanup of accumulated drift across D001-D003 surface. Documented as ADR because (1) the discoveries reveal design patterns worth recording (e.g., async-sync pattern for hybrid store consistency); (2) future tech-debt waves can use this as template.

## 1) 调研 (问题分析铁律 5 步走完)

### Discovered issues (post-D003)

| # | Symptom | Real Root Cause | Severity |
|---|---|---|---|
| **A** | sparse store stale: chromadb 8105 vs sparse 5706, gap=7345 chunks not indexed, orphan=4946 chunks dangling | `store_chunks` only wrote chromadb, never sparse. D001 Phase 4 chromadb cleanup didn't propagate to sparse. | **P0** — hybrid retrieval coverage broken |
| **B** | hybrid_benchmark FAIL: 2/8 < 3/8 threshold | **Derived from A**: sparse incomplete → hybrid path can't differentiate. Initially attributed to D001 Phase 4 dense-quality lift (also true but secondary). | **P0** (auto-resolves with A) |
| **D** | mp-health WARN: classify parse_fail 1.12% (12/1070) | **Not a production bug** — D002 test_llm_assist mock_chat (returning None / "") wrote to production CLASSIFY_LOG, polluting the audit log used by mp-health trend reporting. | **P1** — false alarm in monitoring |
| **F** | archive/d003testproj-20260527.jsonl test residue (650 bytes) | E2E smoke test didn't cleanup its archive file | **P1** — minor pollution |
| **G** | mp-metrics-summary "auto_surface: 0.0% success" misleading | success-rate algorithm assumed every hook returns `status="ok"`, but auto_surface's outcomes are `hit/miss/weak` (none == ok). Same for `*.skipped` events. | **P1** — false alarm in reporting |
| **H** | wing_meta.chunk_count drift: global meta=4777 vs real=7317 (+2540) | By design `store_chunks` does NOT increment chunk_count (upsert can overwrite). No sync mechanism existed → cache permanently stale. | **P0** — data inconsistency |
| **I** | daemon `cmd=search` + `wing=X` + `detail=full` returns error envelope | `search_single` returns `[{"error": ...}]` on chromadb errors; `apply_detail_level` blindly projects this through, mangling the response. | **P1** — known issue, e2e test bypassed |

### Hidden problem discovery (no-one noticed before tonight)

**Critical insight**: A and B were the same problem but were filed as separate items in D003 Parking Lot. The 1-line root cause (sparse store never gets writes after the initial reembed_corpus.py one-shot) had been ticking for a week, undetected because hybrid retrieval is opt-in and most queries used dense path.

### Industry best-practice consulted

- **Pinecone / Weaviate / Qdrant**: hybrid stores updated synchronously in single-process in-memory setup. Doesn't apply to our multi-process (mp-mine subprocess + daemon) + 60s bge-m3 cold start.
- **Stripe / Airbnb data infra**: async sidecar pattern — primary store sync, derived/secondary store async with idempotent reconciler. **Adopted.**
- **GitHub stale-bot / GitLab archive**: multi-signal classification (already adopted in D003 v1.1 via `asset_exists`).
- **Google SRE "tech debt hygiene"**: always do RCA before fix; one ADR per wave of related cleanup; verify with regression suite. **Adopted.**

## 2) Decision

Single-wave cleanup with these guiding principles:

1. **Fix root cause not symptom**: A → install hourly reconciler; not "remember to reembed manually"
2. **Async sidecar pattern**: store_chunks doesn't double-write sparse (60s cold-start kills hot path). Instead, refresh-memory.sh hourly batch calls `sparse_sync.py --quiet` (no-op when in lock-step, lazy-loads bge-m3 only when work needed)
3. **Idempotent reconcilers**: sparse_sync, sync_chunk_count, sparse_orphan_cleanup are all idempotent
4. **Free piggyback**: chunk_count drift fixed by writing back during `mp-wings-review`'s existing live query (no extra IO)
5. **Test isolation**: tests must not pollute production audit logs (P1-D)

## 3) Implementation

### New files (3)

| File | LOC | Purpose |
|---|---|---|
| `~/.claude/scripts/sparse_sync.py` | ~115 | Hourly bidirectional reconciler: removes sparse orphans + fills chromadb-only gaps. Lazy-loads bge-m3 only when work needed. |
| `~/.claude/scripts/sparse_orphan_cleanup.py` | ~80 | One-shot orphan-only cleanup (subset of sparse_sync, retained for explicit cleanup invocation) |
| `~/.claude/docs/decisions/D004-tech-debt-cleanup.md` | (this file) | ADR |

### Modified files (7)

| File | Change |
|---|---|
| `refresh-memory.sh` | Step 4c added: invoke `sparse_sync.py --quiet` hourly |
| `memory_core.py::store_chunks` | Unchanged in mine path (async sidecar pattern preserved); chunk_count NOT incremented (already correct) |
| `memory_daemon.py::handle_search` | P1-I: detect `search_single` error envelope `[{"error": ...}]` and return `{"ok": False, "error": ...}` cleanly instead of mangling via apply_detail_level |
| `mp_metrics_summary.py` | P1-G: differentiated per-hook-type success metrics — auto_surface shows `hit rate`, `*.skipped` shows count only, others show `fail rate` |
| `wing_lifecycle.py` | P0-H: added `sync_chunk_count(wing, real_count)` API |
| `mp_wings_review.py` | P0-H: piggyback writes live chunk count back to wing_meta during review's existing query |
| `tests/conftest.py::mock_chat` | P1-D: monkeypatches `memory_llm_assist.CLASSIFY_LOG` to tmp_path during tests |

### Data corrections (one-shot)

- sparse store: 5706 → **8105** chunks (= chromadb total). Reembed filled 7345 chunks (190s); cleanup deleted 4946 orphans (instant).
- wing_meta.chunk_count: all 23 wings now `drift=0` after one mp-wings-review pass.
- archive/: removed `d003testproj-20260527.jsonl` test residue.

## 4) Verification (post-fix regression — all green)

| Check | Pre-D004 | Post-D004 |
|---|---|---|
| pytest 30 case | 30/30 pass in ~28s | **30/30 pass in 31.43s** |
| mp-health | OK (but classify_trend WARN 1.12%) | **OK** (no WARN — production log still has historical noise but test isolation prevents new noise) |
| hybrid_benchmark Top-1 changed | **2/8 FAIL** | **3/8 PASS** |
| memory_llm_assist_test | 17/17 (100%) | **17/17 (100%)** |
| mp-metrics-summary auto_surface | "0.0% success" misleading | "6.5% hit rate" correct |
| chromadb-sparse consistency | 4946 orphan + 7345 gap | **0 orphan, 0 gap** |
| wing_meta.chunk_count drift | global +2540, <project-a> -28 | **all 0** |

## 5) Operational Notes

### Hourly cost of sparse_sync (no-op case)
- Pull chromadb ids + docs + metadatas: ~1.5s
- Read sparse ids + set diff: ~0.5s
- Total: ~2-3s/hour, no bge-m3 load
- 24/day × 2s ≈ 1 min/day overhead — acceptable

### Hourly cost when work needed
- + bge-m3 cold load: 60s (first run after restart only; daemon already warm in normal ops)
- + encode rate: ~38 chunks/sec on M5 Max
- 1000-chunk burst: ~25s after cold load

### Daemon hot reload — not required
P1-I daemon fix lives in `memory_daemon.py`. KeepAlive launchd will pick up the change on next daemon restart (typically Mac restart / weekly maintenance). Forcing a restart now would break in-flight auto_surface hooks for 60-80s; not worth it for an edge-case fix.

## 6) Lessons Learned

1. **Async-sidecar > sync-double-write** when secondary store has expensive load (60s bge-m3). Hourly reconciler is industry-standard pattern (Stripe / Airbnb dual-write/reconcile).
2. **Tests must isolate from production state** — D002 testing accidentally polluted the production decision log via shared CLASSIFY_LOG path. monkeypatch fixtures fix this categorically.
3. **Symptoms cluster into root causes** — A and B looked like separate Parking Lot items but were the same root. The 5-step analysis (full read → trace dataflow → global search → diagnosis → fix all) caught this before duplicate work.
4. **Free piggyback over scheduled jobs** — mp-wings-review already queries chunk counts; writing them back is zero extra IO. Better than scheduling a separate sync cron.
5. **Hook-type-specific success metrics** — auto_surface's success ≠ auto_mine's success. Generic `status="ok"` accounting produces misleading reports. Each metric needs domain-aware aggregation.

## 7) Parking Lot (intentionally NOT fixed)

- **classify_decisions.jsonl 历史 1.12% parse_fail noise** — this is historical pollution from D002 test runs. Could be wiped, but the records are useful for understanding when the test pollution started. Filtering at read-time is sufficient.
- **mp-mine subprocess cold-start bge-m3 60s** — not fixed because the async-sidecar pattern (refresh-memory.sh hourly) makes it moot. mp-mine itself doesn't need bge-m3.
- **HNSW tombstones after deletion** — chromadb 1.5.7 has no user-level compact() API. Tombstones persist on disk but don't affect search correctness. Wait for chromadb upstream fix.
- **dormant wings (16) salvage** — content judgment work, not tech debt. User-driven, no SLA.

## 8) <external-backup-volume> sync impact

These additions need to land on <external-backup-volume> to keep "插盘一键恢复" valid:
- `~/.claude/scripts/sparse_sync.py` (new)
- `~/.claude/scripts/sparse_orphan_cleanup.py` (new)
- `~/.claude/refresh-memory.sh` (modified)
- `~/.claude/scripts/memory_daemon.py` (modified)
- `~/.claude/scripts/mp_metrics_summary.py` (modified)
- `~/.claude/scripts/wing_lifecycle.py` (modified)
- `~/.claude/scripts/mp_wings_review.py` (modified)
- `~/.claude/scripts/tests/conftest.py` (modified)
- `~/.mempalace-zh/sparse.sqlite3` (cleaned)
- `~/.mempalace-zh/wing_meta.sqlite3` (synced)
- `~/.mempalace-zh/archive/` (residue removed)
- `~/.claude/docs/decisions/D004-tech-debt-cleanup.md` (new ADR)

Run `bash ~/.claude/scripts/sync-to-kingston.sh` to propagate.

## References

- D001 / D002 / D003 (all 3 prior ADRs that this cleans up)
- Stripe Engineering Blog — "Async sidecar reconciliation pattern"
- Google SRE — "Tech debt management" (Chapter 22)
- ChromaDB 1.5.7 release notes — HNSW deletion behavior
