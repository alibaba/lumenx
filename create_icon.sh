#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE="$SCRIPT_DIR/src-tauri/icons/icon.png"
TARGET="$SCRIPT_DIR/src-tauri/icons/icon.icns"
if [ ! -f "$SOURCE" ] || [ -L "$SOURCE" ]; then
    echo "Error: $SOURCE not found"
    exit 1
fi
if [ -L "$SCRIPT_DIR/src-tauri" ] || [ -L "$SCRIPT_DIR/src-tauri/icons" ] || [ -L "$TARGET" ] || { [ -e "$TARGET" ] && [ ! -f "$TARGET" ]; }; then
    echo "Error: icon paths must be regular files in the source tree"
    exit 1
fi

echo "Creating $TARGET from $SOURCE..."

# Keep staging on the target filesystem so replacement is atomic.
WORK_DIR="$(mktemp -d "$SCRIPT_DIR/src-tauri/icons/.lumenx-icon.XXXXXX")"
trap 'rm -rf -- "$WORK_DIR"' EXIT
ICONSET="$WORK_DIR/LumenX.iconset"
mkdir "$ICONSET"

# Generate icons of different sizes
sips -z 16 16     "$SOURCE" --out "${ICONSET}/icon_16x16.png"
sips -z 32 32     "$SOURCE" --out "${ICONSET}/icon_16x16@2x.png"
sips -z 32 32     "$SOURCE" --out "${ICONSET}/icon_32x32.png"
sips -z 64 64     "$SOURCE" --out "${ICONSET}/icon_32x32@2x.png"
sips -z 128 128   "$SOURCE" --out "${ICONSET}/icon_128x128.png"
sips -z 256 256   "$SOURCE" --out "${ICONSET}/icon_128x128@2x.png"
sips -z 256 256   "$SOURCE" --out "${ICONSET}/icon_256x256.png"
sips -z 512 512   "$SOURCE" --out "${ICONSET}/icon_256x256@2x.png"
sips -z 512 512   "$SOURCE" --out "${ICONSET}/icon_512x512.png"
sips -z 1024 1024 "$SOURCE" --out "${ICONSET}/icon_512x512@2x.png"

# Convert completely before replacing the existing canonical icon.
iconutil -c icns "$ICONSET" -o "$WORK_DIR/icon.icns"
mv -f -- "$WORK_DIR/icon.icns" "$TARGET"

echo "Success! $TARGET created."
