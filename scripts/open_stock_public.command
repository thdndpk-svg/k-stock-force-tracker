#!/bin/zsh
URL="https://thdndpk-svg.github.io/k-stock-force-tracker/"
LOG_DIR="$HOME/Library/Logs"
mkdir -p "$LOG_DIR"

if /usr/bin/open -a "Google Chrome" "$URL" >> "$LOG_DIR/KStockForceTracker.log" 2>&1; then
  exit 0
fi

/usr/bin/open "$URL" >> "$LOG_DIR/KStockForceTracker.log" 2>&1
