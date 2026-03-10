#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
VERSION="${1:-v1}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Installing dependencies for hardened build..."
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install python-dotenv "numpy<2" fastapi "uvicorn[standard]" pydantic cryptography
"$PYTHON_BIN" -m pip install nuitka ordered-set zstandard

echo "Building hardened macOS binary with Nuitka..."
"$PYTHON_BIN" -m nuitka \
  --onefile \
  --standalone \
  --assume-yes-for-downloads \
  --enable-plugin=multiprocessing \
  --output-dir=dist_nuitka \
  --output-filename=Tradingm5UI \
  --include-data-dir=ui_backend/web=ui_backend/web \
  run_ui.py

RELEASE_DIR="$ROOT_DIR/release"
mkdir -p "$RELEASE_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
BUNDLE_NAME="Tradingm5UI_${VERSION}_macos_nuitka_${STAMP}"
BUNDLE_DIR="$RELEASE_DIR/$BUNDLE_NAME"
mkdir -p "$BUNDLE_DIR"

cp "dist_nuitka/Tradingm5UI" "$BUNDLE_DIR/Tradingm5UI"
chmod +x "$BUNDLE_DIR/Tradingm5UI"
[[ -f "accounts.example.json" ]] && cp "accounts.example.json" "$BUNDLE_DIR/accounts.example.json"
[[ -f ".env.example" ]] && cp ".env.example" "$BUNDLE_DIR/.env.example"

ZIP_PATH="$RELEASE_DIR/${BUNDLE_NAME}.zip"
rm -f "$ZIP_PATH"
(cd "$BUNDLE_DIR" && /usr/bin/zip -r "$ZIP_PATH" . >/dev/null)
echo "Hardened release zip created: $ZIP_PATH"
