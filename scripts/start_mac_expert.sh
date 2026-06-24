#!/bin/zsh
SCRIPT_DIR="${0:A:h}"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$APP_ROOT"
exec /usr/bin/python3 stock_force_tracker.py serve --port 8777
