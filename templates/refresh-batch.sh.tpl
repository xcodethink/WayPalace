#!/bin/bash
# refresh-memory.sh - Hourly incremental sync for local memory system
# Triggered by launchd: ~/Library/LaunchAgents/com.user.waypalace.memory-refresh.daemon.plist
# Old cron entry was removed 2026-05-26 (cron is sandboxed on macOS Sequoia+,
# wasn't actually running anyway — refresh.log never had content).

set -u

LOCK_FILE=/tmp/refresh-memory.lock
LOG_DIR=~/.mempalace-zh/logs
LOG_FILE=$LOG_DIR/refresh.log
PYTHON=~/.mempalace/venv-zh/bin/python
MINE=~/.claude/scripts/memory_mine.py

mkdir -p "$LOG_DIR"

# PID-based lock (macOS has no flock)
if [ -f "$LOCK_FILE" ]; then
  OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[$(date)] Another refresh running (PID $OLD_PID). Skipping." >> "$LOG_FILE"
    exit 0
  fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

echo "" >> "$LOG_FILE"
echo "[$(date)] refresh-memory start (Tier 1 hourly batch)" >> "$LOG_FILE"

# ========================================================================
# 1. ClaudeCodeSkills → global wing
# ========================================================================
$PYTHON $MINE ${WAYPALACE_HOME}/skills --wing global --quiet >> "$LOG_FILE" 2>&1

# ========================================================================
# 2. Claude Code auto-memory (main profile) → LLM-classified
#    This is the directory Claude writes feedback_*.md / project_*.md to
#    during conversations. Content is mixed-wing (some feedback rules are
#    global; some project notes belong to specific wings), so let LLM
#    decide per-file rather than dump everything to one wing.
# ========================================================================
AUTO_MEM_DIR=~/.claude/projects/-user/memory
if [ -d "$AUTO_MEM_DIR" ]; then
  $PYTHON $MINE "$AUTO_MEM_DIR" --llm-classify --llm-summarize --quiet >> "$LOG_FILE" 2>&1
fi

# ========================================================================
# 3. Sub-profile auto-memory (a/b/c) → LLM-classified
#    These share the same projects/ dir via symlink to main, so likely
#    already covered by step 2. But scan them defensively in case symlink
#    breaks or user adds standalone memory in a sub-profile.
# ========================================================================
for p in a b c; do
  SUB=~/.user-profiles/$p/.claude/projects/-user/memory
  if [ -d "$SUB" ] && [ ! -L "$SUB" ]; then
    # Only if it's a real dir, not a symlink (avoid double-mining)
    $PYTHON $MINE "$SUB" --llm-classify --llm-summarize --quiet >> "$LOG_FILE" 2>&1
  fi
done

# ========================================================================
# 4. Per-project memory dirs → glob scan, wing name derived from dir name
#    (D003: replaces hardcoded 22-project list with auto-discovery.)
#    Naming: -user-workspace-<X> → normalize: lowercase + '-'/'' → '_'
# ========================================================================
PROJECTS_ROOT=${WAYPALACE_HOME}/projects
for dir in "$PROJECTS_ROOT"/-user-workspace-*/memory; do
  [ -d "$dir" ] || continue
  parent=$(basename "$(dirname "$dir")")
  proj="${parent#-user-workspace-}"
  # Normalize to canonical wing name (lowercase + '-' '/' ' ' → '_'); strip leading/trailing _
  wing=$(printf '%s' "$proj" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_' | sed -e 's/__*/_/g' -e 's/^_//' -e 's/_$//')
  [ -z "$wing" ] && continue
  $PYTHON $MINE "$dir" --wing "$wing" --quiet >> "$LOG_FILE" 2>&1
done

# ========================================================================
# 4c. D004: Keep sparse_store in lock-step with chromadb (hourly, idempotent)
#     - Removes sparse rows whose chunk_id no longer in chromadb
#     - Fills missing sparse rows by encoding (bge-m3 sparse only)
#     - No-op when in sync (~9s overhead, no bge-m3 load)
# ========================================================================
$PYTHON ${WAYPALACE_HOME}/scripts/sparse_sync.py --quiet >> "$LOG_FILE" 2>&1

# ========================================================================
# 5. Rebuild sensitive-dict from skills/10-项目资产管理/
# ========================================================================
$PYTHON ${WAYPALACE_HOME}/scripts/build_sensitive_dict.py >> "$LOG_FILE" 2>&1

# ========================================================================
# 6. Process conflict queue (fast)
# ========================================================================
$PYTHON ${WAYPALACE_HOME}/scripts/memory_conflict.py process >> "$LOG_FILE" 2>&1

# ========================================================================
# 7. Weekly deep cleanup on Sundays at the first run of the day
#    (Hourly schedule means this triggers 24x/Sunday — guard against that)
# ========================================================================
if [ "$(date +%u)" = "7" ] && [ "$(date +%H)" = "03" ]; then
  $PYTHON ${WAYPALACE_HOME}/scripts/memory_cleanup.py --quiet >> "$LOG_FILE" 2>&1
fi

# ========================================================================
# 8. D003: Quarterly wing-review reminder (1st of each month at 08:00)
#    Just nudges the log; user runs `mp-wings-review` manually.
# ========================================================================
if [ "$(date +%d)" = "01" ] && [ "$(date +%H)" = "08" ]; then
  echo "[REMINDER] Wing review due. Run: mp-wings-review" >> "$LOG_FILE"
fi

echo "[$(date)] refresh-memory done" >> "$LOG_FILE"
