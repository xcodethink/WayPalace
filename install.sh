#!/bin/bash
# WayPalace install — bootstrap script for fresh setup.
#
# Usage:
#   bash install.sh              # default: Tier 0 (no LLM assist)
#   bash install.sh --tier=mlx   # Tier 2: Qwen3.6-35B via MLX (Mac 64GB+)
#   bash install.sh --tier=small # Tier 1: smaller local LLM
#   bash install.sh --tier=external # Tier 3: external OpenAI-compatible endpoint
#
# What this does:
#   1. Verify Python 3.11+
#   2. Create venv at ${WAYPALACE_VENV:-$HOME/.waypalace/venv}
#   3. Install dependencies (chromadb + bge-m3 + optional LLM tier)
#   4. Create data dirs at ${WAYPALACE_DATA:-$HOME/.waypalace/data}
#   5. Print next steps (no daemon auto-installation — you opt in)

set -euo pipefail

# === Configurable paths (env vars override) ===
WAYPALACE_HOME="${WAYPALACE_HOME:-$HOME/.waypalace}"
WAYPALACE_DATA="${WAYPALACE_DATA:-$WAYPALACE_HOME/data}"
WAYPALACE_VENV="${WAYPALACE_VENV:-$WAYPALACE_HOME/venv}"

# === Parse args ===
TIER="0"
for arg in "$@"; do
  case "$arg" in
    --tier=*) TIER="${arg#*=}" ;;
    --tier) shift; TIER="$1" ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
  esac
done

echo "=== WayPalace install (tier=$TIER) ==="
echo "  HOME = $WAYPALACE_HOME"
echo "  DATA = $WAYPALACE_DATA"
echo "  VENV = $WAYPALACE_VENV"
echo

# === Step 1: Python version check ===
if ! command -v python3 >/dev/null; then
  echo "[FATAL] python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$(printf '%s\n3.11' "$PY_VERSION" | sort -V | head -1)" != "3.11" ]; then
  echo "[FATAL] Python 3.11+ required, found $PY_VERSION" >&2
  exit 1
fi
echo "[OK] python3 $PY_VERSION"

# === Step 2: venv ===
if [ ! -d "$WAYPALACE_VENV" ]; then
  echo "[install] creating venv at $WAYPALACE_VENV"
  python3 -m venv "$WAYPALACE_VENV"
else
  echo "[skip] venv already exists at $WAYPALACE_VENV"
fi
. "$WAYPALACE_VENV/bin/activate"
pip install --quiet --upgrade pip

# === Step 3: install ===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[install] installing WayPalace base..."
pip install --quiet "$SCRIPT_DIR"

case "$TIER" in
  0)
    echo "[install] Tier 0 selected (no LLM assist) — basic install complete"
    ;;
  small)
    echo "[install] Tier 1: small local LLM (transformers)..."
    pip install --quiet "$SCRIPT_DIR[llm-small]"
    ;;
  mlx)
    if [ "$(uname)" != "Darwin" ]; then
      echo "[WARN] Tier 2 (MLX) is Mac-only. Falling back to Tier 0."
      TIER="0"
    else
      echo "[install] Tier 2: Qwen3.6-35B via MLX (~20GB RAM resident)..."
      pip install --quiet "$SCRIPT_DIR[llm-mlx]"
    fi
    ;;
  external)
    echo "[install] Tier 3: external OpenAI-compatible endpoint..."
    pip install --quiet "$SCRIPT_DIR[llm-external]"
    ;;
  *)
    echo "[WARN] unknown tier '$TIER', skipping LLM install"
    ;;
esac

# === Step 4: data dirs ===
mkdir -p "$WAYPALACE_DATA"/{chromadb,sparse,metrics,archive,logs}
echo "[OK] data dirs ready under $WAYPALACE_DATA"

# === Step 5: next steps ===
cat <<EOF

=== Install complete ===

  Activate the venv:
    . $WAYPALACE_VENV/bin/activate

  First mine + search test:
    mp-mine /path/to/your/memory/dir --namespace global
    mp-search "your query here"

  Optional Claude Code integration:
    See docs/INSTALL.md § "Claude Code hooks (optional)"

  Optional launchd daemon (macOS):
    See templates/launchd/ and docs/INSTALL.md § "Running as a daemon"

EOF
