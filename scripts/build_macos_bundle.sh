#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
VERSION="${1:-v1}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Installing build dependencies..."
"$PYTHON_BIN" -m pip install --upgrade pip
# MetaTrader5 wheel is Windows-only; skip it on macOS build hosts.
"$PYTHON_BIN" -m pip install python-dotenv "numpy<2" fastapi "uvicorn[standard]" pydantic cryptography
"$PYTHON_BIN" -m pip install pyinstaller

echo "Building Tradingm5UI binary (macOS)..."
"$PYTHON_BIN" -m PyInstaller --clean --noconfirm "packaging/tradingm5_ui.spec"

RELEASE_DIR="$ROOT_DIR/release"
mkdir -p "$RELEASE_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
BUNDLE_NAME="Tradingm5UI_${VERSION}_macOS_${STAMP}"
BUNDLE_DIR="$RELEASE_DIR/$BUNDLE_NAME"
mkdir -p "$BUNDLE_DIR"

cp "dist/Tradingm5UI" "$BUNDLE_DIR/Tradingm5UI"
chmod +x "$BUNDLE_DIR/Tradingm5UI"

cat > "$BUNDLE_DIR/README_RELEASE.txt" <<'EOF'
Tradingm5UI Release Bundle (macOS)
==================================

1) Run ./Tradingm5UI
2) Add accounts in UI -> Accounts tab
3) Run Healthcheck from UI
4) Place orders from Trading tab

No source code is shipped in this bundle.

Logs:
- Runtime logs: ./logs/ui_backend_*.log
- Closed deals journal: ./logs/closed_deals_journal.jsonl

If macOS blocks first launch:
- System Settings -> Privacy & Security -> Open Anyway
EOF

ZIP_PATH="$RELEASE_DIR/${BUNDLE_NAME}.zip"
rm -f "$ZIP_PATH"
(cd "$BUNDLE_DIR" && /usr/bin/zip -r "$ZIP_PATH" . >/dev/null)

echo "Release zip created: $ZIP_PATH"
