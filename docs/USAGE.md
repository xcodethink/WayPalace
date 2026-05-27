# CLI usage

All commands assume you've activated the venv (`source $WAYPALACE_VENV/bin/activate`).

## mp-search — query the memory

```bash
mp-search "your query"                    # default: current project + global, full detail, top 5
mp-search "..." --limit 10                # top 10
mp-search "..." --threshold 0.4           # lower bar
mp-search "..." --detail index            # cheapest: ~80-char snippet per result
mp-search "..." --detail summary          # ~300-char per-chunk summary
mp-search "..." --detail full             # complete chunks (default)
mp-search "..." --hybrid                  # dense + sparse with RRF fusion
mp-search "..." --wing my-namespace       # search only one namespace
mp-search "..." --json                    # machine-readable output
```

## mp-search-all — cross-namespace search

```bash
mp-search-all "your query"                # search across all namespaces
mp-search-all "..." --limit 20            # broader recall
```

Warning: results may include content from different projects. Don't blindly
copy specific values (API keys, URLs, IDs) across project contexts.

## mp-mine — index files

```bash
mp-mine /path/to/file.md --namespace global             # index one file
mp-mine /path/to/dir --namespace project-x              # index whole directory recursively
mp-mine /path/to/dir --namespace project-x --force      # ignore mtime cache, re-index everything
mp-mine /path/to/dir --llm-classify                     # auto-pick namespace per file (needs Tier 1+)
mp-mine /path/to/dir --llm-summarize                    # generate per-chunk summary (needs Tier 1+)
mp-mine /path/to/dir --llm-classify --llm-summarize     # both
mp-mine /path/to/file --quiet                           # only print summary
```

`--llm-classify` is opt-in because each file requires one LLM call (~ 2 s).
`--llm-summarize` is opt-in because each chunk requires one LLM call (~ 1.3 s).

## mp-status — current state

```bash
mp-status
# Prints:
#   Total chunks
#   Number of namespaces
#   Top namespaces by chunk count
```

## mp-health — system snapshot

```bash
mp-health
# Checks:
#   - All launchd daemons running
#   - Daemon endpoint reachable
#   - MLX LLM endpoint reachable (if Tier 2)
#   - ChromaDB ↔ sparse store consistency
#   - Disk usage thresholds
#   - Log file freshness
#   - LLM classification trend (last 7 days)
```

## mp-wings-review — namespace lifecycle audit

```bash
mp-wings-review                  # full 4-tier report (active / dormant / stale / orphan)
mp-wings-review --wing my-ns     # detail for one namespace
mp-wings-review --json           # machine-readable
```

The 4 tiers:

- **active**: had mine or search activity in the last 90 days
- **dormant**: 90-180 days, OR would be orphan but the project directory still exists
- **stale**: 180-365 days, project still exists
- **orphan**: 365+ days OR project directory gone OR source files all missing

The asset-existence override prevents wrongly archiving namespaces for
projects you haven't touched recently but still have the codebase for.

## mp-wing-inspect — examine one namespace

```bash
mp-wing-inspect my-namespace
# Lists every chunk grouped by source file with summaries + previews
```

Use this before archiving to decide what to salvage.

## mp-wing-archive — soft-delete + dump

```bash
mp-wing-archive my-namespace
```

This:
1. Dumps every chunk in that namespace to `$WAYPALACE_DATA/archive/<ns>-YYYYMMDD.jsonl`
2. Marks the namespace `archived = 1` in the metadata table
3. **Does NOT yet delete from ChromaDB / sparse store** — that's the next step

After archiving, you can:
- Inspect the jsonl, copy valuable insights into a "salvaged" markdown
- Then run `mp-wing-delete --confirm` to physically purge

## mp-wing-delete — physical purge

```bash
mp-wing-delete my-namespace                  # dry-run: shows what would be deleted
mp-wing-delete my-namespace --confirm        # actually delete
```

Requires `archived = 1` (run `mp-wing-archive` first). The jsonl dump remains
so deletion is reversible until you remove the dump.

## mp-metrics-summary — observability

```bash
mp-metrics-summary --days 1                  # last 24 hours
mp-metrics-summary --days 7                  # last week
mp-metrics-summary --days 30                 # last month
mp-metrics-summary --event search            # only search events
```

Shows:
- Hook trigger counts and outcomes (auto-mine, auto-surface, session-start)
- Search latency percentiles, hybrid vs dense ratio, per-namespace counts
- Mine throughput
- LLM assist latency + success rate
- Recent errors (last 10)

All data lives in `$WAYPALACE_DATA/metrics/YYYY-MM-DD.jsonl`. Zero
external telemetry.

## mp-drift-check — invariants audit

```bash
mp-drift-check
# Validates ~33 invariants:
#   - 6 launchd daemon labels exist
#   - MCP server registered in Claude Code config
#   - All expected symlinks resolve
#   - Sensitive dictionary is up to date
```

Run weekly or after major upgrades.

## Programmatic usage (Python)

```python
import sys
sys.path.insert(0, "$WAYPALACE_HOME/waypalace")  # adjust path

import memory_search

result = memory_search.search_isolated(
    query="my query",
    current_wing="global",
    n_results=5,
    threshold=0.4,
    detail_level="summary",   # index / summary / full
    hybrid=True,              # opt-in hybrid path
)

for r in result["results"]:
    print(r["chunk_id"], r["similarity"], r.get("source_name"), r.get("summary"))
```

## MCP server (for Claude Desktop / Claude Code)

If you use Claude Code with MCP, register the memory server:

```bash
claude mcp add -s user memory \
    $WAYPALACE_VENV/bin/python \
    $WAYPALACE_HOME/waypalace/memory_mcp_server.py
```

Then in any Claude conversation:
- `memory_search(query, wing, detail_level)` — search this project + global
- `memory_search_all(query)` — cross-namespace search
- `memory_timeline(wing, start_date, end_date)` — chronological view
- `memory_get_observations(ids)` — fetch full chunks by ID
