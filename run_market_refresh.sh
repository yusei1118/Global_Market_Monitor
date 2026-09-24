#!/bin/bash
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
SCRIPT="$PROJECT_DIR/refresh_market.py"
LOG_DIR="$PROJECT_DIR/logs"
LOCK_DIR="$PROJECT_DIR/.market_refresh.lock"

mkdir -p "$LOG_DIR"

if [ ! -x "$PYTHON" ]; then
  echo "$(date) ERROR: Python venv not found: $PYTHON" >> "$LOG_DIR/market_refresh_error.log"
  exit 1
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date) INFO: previous market refresh still running; skipped." >> "$LOG_DIR/market_refresh.log"
  exit 0
fi

cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

cd "$PROJECT_DIR" || exit 1
"$PYTHON" "$SCRIPT" --mode market >> "$LOG_DIR/market_refresh.log" 2>> "$LOG_DIR/market_refresh_error.log"
