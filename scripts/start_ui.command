#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "dist/Tradingm5UI" ]]; then
  exec "dist/Tradingm5UI"
fi

if [[ -x "./Tradingm5UI" ]]; then
  exec "./Tradingm5UI"
fi

echo "Tradingm5UI binary not found. Falling back to source launch..."
exec python3 run_ui.py
