#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <path-to-binary-or-app> <codesign-identity>"
  exit 1
fi

TARGET="$1"
IDENTITY="$2"

if [[ ! -e "$TARGET" ]]; then
  echo "Target not found: $TARGET"
  exit 1
fi

echo "Signing $TARGET with identity: $IDENTITY"
codesign --force --options runtime --timestamp --sign "$IDENTITY" "$TARGET"
codesign --verify --deep --strict --verbose=2 "$TARGET"
echo "Sign completed."

echo "Optional notarization (recommended):"
echo "xcrun notarytool submit \"$TARGET\" --keychain-profile <PROFILE> --wait"
