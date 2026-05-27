# Installation

## Prerequisites

- macOS (Apple Silicon recommended) or Linux
- Python 3.11+
- 16 GB+ RAM minimum (32 GB+ recommended; 64 GB if using local Qwen LLM)
- ~10 GB disk for embedding model + dependencies; ~25 GB if using local Qwen LLM
- A package manager: `brew` on Mac is helpful but not required

## Quick install

```bash
git clone https://github.com/xcodethink/WayPalace.git
cd WayPalace
bash install.sh
```

This installs Tier 0 (no LLM assist). For LLM-assisted classification and
summarization, see [Tiers](#tiers) below.

## Environment variables

WayPalace reads these env vars (with sensible defaults):

| Variable | Default | Purpose |
|---|---|---|
| `WAYPALACE_HOME` | `$HOME/.waypalace` | Top-level config + scripts |
| `WAYPALACE_DATA` | `$WAYPALACE_HOME/data` | ChromaDB + sparse store + metrics + archive |
| `WAYPALACE_VENV` | `$WAYPALACE_HOME/venv` | Python virtual environment |
| `MLX_LLM_HOME` | `$HOME/.mlx-llm` | (Tier 2 only) MLX LLM venv + model cache |
| `USER_WORKSPACE` | `$HOME/Developer` | Where your projects live (used by hooks) |

Override before running install:

```bash
WAYPALACE_HOME=/opt/waypalace bash install.sh
```

## Tiers

### Tier 0: No LLM assist (minimum viable)

```bash
bash install.sh
```

What you get:
- ChromaDB + bge-m3 embedding (dense retrieval)
- Sparse store + RRF fusion (hybrid retrieval, optional)
- bge-reranker (cross-encoder reranking)
- All CLI tools (`mp-search`, `mp-mine`, lifecycle commands, metrics)

What you DON'T get:
- Automatic namespace classification (`mp-mine --llm-classify` falls back to a static default)
- Per-chunk LLM summary in metadata (`mp-mine --llm-summarize` is no-op)

### Tier 1: Small local LLM

```bash
bash install.sh --tier=small
```

Adds the `transformers` library so you can wire in a small HuggingFace model
(e.g., Qwen2.5-1.5B, Llama-3.2-3B). Requires manual config in
`$WAYPALACE_HOME/waypalace/memory_llm_assist.py` to point at your endpoint.

### Tier 2: Qwen3.6-35B via MLX (Mac only, 64 GB+ recommended)

```bash
bash install.sh --tier=mlx
```

Installs `mlx-lm`. After install:

```bash
# Start the MLX LLM server (port 8081)
mlx_lm.server --model unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit --host 127.0.0.1 --port 8081

# Or use the provided launchd template (recommended for unattended operation)
cp templates/launchd/com.user.waypalace.mlx-llm.daemon.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.waypalace.mlx-llm.daemon.plist
```

### Tier 3: External OpenAI-compatible endpoint

```bash
bash install.sh --tier=external
```

Adds the `openai` SDK. Configure your endpoint in
`$WAYPALACE_HOME/waypalace/memory_llm_assist.py`:

```python
API_URL = "https://your-endpoint.example.com/v1/chat/completions"
MODEL = "your-model-name"
```

You can use any OpenAI-compatible API: OpenAI, Anthropic via proxy, Groq,
Together.ai, locally hosted vLLM, etc.

## Running the daemon

The memory daemon keeps bge-m3 + bge-reranker warm in memory so queries
return in < 1 s instead of paying the 14 s cold start every time.

### Option A: launchd (macOS, recommended)

```bash
# Copy the templated plist + substitute paths
mkdir -p ~/Library/LaunchAgents
sed "s|\${WAYPALACE_HOME}|$WAYPALACE_HOME|g; s|\${WAYPALACE_VENV}|$WAYPALACE_VENV|g" \
    templates/launchd/com.user.waypalace.memory.daemon.plist.tpl \
    > ~/Library/LaunchAgents/com.user.waypalace.memory.daemon.plist
launchctl load ~/Library/LaunchAgents/com.user.waypalace.memory.daemon.plist
```

Check it's up:

```bash
launchctl list | grep waypalace
echo '{"cmd":"ping"}' | nc -U $WAYPALACE_DATA/daemon.sock
```

### Option B: foreground (for testing)

```bash
source $WAYPALACE_VENV/bin/activate
python $WAYPALACE_HOME/waypalace/memory_daemon.py
```

### Option C: Linux systemd

Not yet implemented. PRs welcome — the daemon itself is platform-neutral; only
the launchd-specific bits need a systemd equivalent.

## Claude Code hooks (optional but recommended)

If you use Claude Code, three hooks make WayPalace much more useful:

- **PostToolUse hook** auto-indexes memory files within ~ 20 s of you writing them
- **PreToolUse hook** surfaces relevant past memories before Edit / Write / Bash
- **SessionStart hook** injects fresh project context at conversation start

### Setup

1. Copy hooks to your Claude Code hooks dir:

   ```bash
   mkdir -p ~/.claude/hooks
   cp hooks/*.py ~/.claude/hooks/
   chmod +x ~/.claude/hooks/*.py
   ```

2. Edit `~/.claude/settings.json` to wire them up:

   ```json
   {
     "hooks": {
       "PostToolUse": [{
         "matcher": "Edit|Write|MultiEdit",
         "hooks": [{
           "type": "command",
           "command": "$HOME/.claude/hooks/memory-auto-mine.py",
           "async": true,
           "timeout": 2
         }]
       }],
       "PreToolUse": [{
         "matcher": "Edit|Write|Bash",
         "hooks": [{
           "type": "command",
           "command": "$HOME/.claude/hooks/memory-auto-surface.py",
           "timeout": 3
         }]
       }],
       "SessionStart": [{
         "hooks": [{
           "type": "command",
           "command": "$HOME/.claude/hooks/memory-session-start.py",
           "timeout": 2
         }]
       }]
     }
   }
   ```

3. Restart Claude Code.

The hooks fail-silent — if anything goes wrong, your session is not blocked,
just no memory surfacing happens.

## Hourly batch (Tier 1 incremental indexing)

Want new memory files indexed even if you don't have Claude Code? Set up an
hourly batch:

```bash
# Use the templated batch script
sed "s|\${WAYPALACE_HOME}|$WAYPALACE_HOME|g; s|\${WAYPALACE_DATA}|$WAYPALACE_DATA|g" \
    templates/refresh-batch.sh.tpl > $WAYPALACE_HOME/refresh-batch.sh
chmod +x $WAYPALACE_HOME/refresh-batch.sh

# On macOS — launchd
cp templates/launchd/com.user.waypalace.refresh.daemon.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.waypalace.refresh.daemon.plist

# Linux — cron
echo "17 * * * * bash $WAYPALACE_HOME/refresh-batch.sh" | crontab -
```

## Verifying the install

```bash
source $WAYPALACE_VENV/bin/activate

# 1. Health snapshot
mp-health

# 2. Mine a test file
echo "# Test\nThis is a test memory." > /tmp/test-memory.md
mp-mine /tmp/test-memory.md --namespace global

# 3. Search for it
mp-search "test memory"

# 4. Run the test suite
cd opensource/WayPalace
python -m pytest tests/ -q
```

All four should succeed.

## Troubleshooting

### "ModuleNotFoundError: No module named 'chromadb'"

Activate the venv first:
```bash
source $WAYPALACE_VENV/bin/activate
```

### Daemon socket missing

```bash
# Check daemon status
launchctl list | grep waypalace

# Restart
launchctl unload ~/Library/LaunchAgents/com.user.waypalace.memory.daemon.plist
launchctl load   ~/Library/LaunchAgents/com.user.waypalace.memory.daemon.plist

# Watch logs
tail -f $WAYPALACE_DATA/logs/daemon.log
```

### Cold-start every query

The daemon isn't running. Each `mp-search` call is loading bge-m3 from scratch
(14-16 s). Start the daemon — see [Running the daemon](#running-the-daemon).

### bge-m3 download fails

Set `HF_TOKEN` or use a HuggingFace mirror:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## Uninstalling

```bash
# Stop daemons
launchctl unload ~/Library/LaunchAgents/com.user.waypalace.*.plist
rm ~/Library/LaunchAgents/com.user.waypalace.*.plist

# Remove venv + data (WARNING: this deletes your memory data)
rm -rf $WAYPALACE_HOME

# Remove hooks (if installed)
rm ~/.claude/hooks/memory-*.py
# (manually remove the corresponding entries from ~/.claude/settings.json)
```
