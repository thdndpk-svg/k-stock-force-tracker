#!/bin/zsh
unsetopt bg_nice 2>/dev/null
SCRIPT_DIR="${0:A:h}"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ ! -f "$APP_ROOT/stock_force_tracker.py" ]]; then
  APP_ROOT="/Users/mac/Documents/Codex/2026-06-20/new-chat/outputs/k-stock-force-tracker"
fi
LOG_DIR="$HOME/Library/Logs"
PORT="8777"
URL="http://127.0.0.1:${PORT}/"
mkdir -p "$LOG_DIR"

if /usr/bin/curl -fsS "$URL" >/dev/null 2>&1; then
  /usr/bin/open "$URL"
  exit 0
fi

cd "$APP_ROOT"
exec /usr/bin/python3 stock_force_tracker.py serve --port "$PORT" >> "$LOG_DIR/KStockForceTracker.log" 2>&1
