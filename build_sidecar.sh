#!/usr/bin/env bash
# build_sidecar.sh — Package the Python backend into a standalone binary via PyInstaller
# Output: src-tauri/binaries/lumenx-backend-{arch}-apple-darwin

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "╔═══════════════════════════════════════════════════╗"
echo "║  LumenX Studio — Python Sidecar Builder          ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# Detect architecture
ARCH=$(uname -m)
case "$ARCH" in
    arm64|aarch64) TAURI_ARCH="aarch64" ;;
    x86_64)        TAURI_ARCH="x86_64" ;;
    *)             echo "❌ Unsupported architecture: $ARCH"; exit 1 ;;
esac

BINARY_NAME="lumenx-backend-${TAURI_ARCH}-apple-darwin"
OUTPUT_DIR="src-tauri/binaries"

echo "→ Building for architecture: ${TAURI_ARCH}"
echo "→ Output: ${OUTPUT_DIR}/${BINARY_NAME}"
echo ""

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Check for PyInstaller
if ! command -v pyinstaller &>/dev/null; then
    echo "⚠️  PyInstaller not found, installing..."
    pip install pyinstaller
fi

# Build with PyInstaller
echo "→ Running PyInstaller..."
pyinstaller \
    --name "$BINARY_NAME" \
    --onefile \
    --console \
    --noconfirm \
    --clean \
    --hidden-import=uvicorn \
    --hidden-import=uvicorn.logging \
    --hidden-import=uvicorn.loops \
    --hidden-import=uvicorn.loops.auto \
    --hidden-import=uvicorn.protocols \
    --hidden-import=uvicorn.protocols.http \
    --hidden-import=uvicorn.protocols.http.auto \
    --hidden-import=uvicorn.protocols.websockets \
    --hidden-import=uvicorn.protocols.websockets.auto \
    --hidden-import=uvicorn.lifespan \
    --hidden-import=uvicorn.lifespan.on \
    --hidden-import=fastapi \
    --hidden-import=pydantic \
    --hidden-import=starlette \
    --hidden-import=httptools \
    --hidden-import=dotenv \
    --hidden-import=yaml \
    --hidden-import=dashscope \
    --hidden-import=oss2 \
    --collect-all=dashscope \
    --add-data "src:src" \
    --distpath "$OUTPUT_DIR" \
    src/apps/comic_gen/api.py

# PyInstaller puts the binary in a folder, move it to the expected location
if [ -f "${OUTPUT_DIR}/${BINARY_NAME}/${BINARY_NAME}" ]; then
    mv "${OUTPUT_DIR}/${BINARY_NAME}/${BINARY_NAME}" "${OUTPUT_DIR}/${BINARY_NAME}"
    rm -rf "${OUTPUT_DIR}/${BINARY_NAME}.dir" 2>/dev/null || true
fi

# Clean up PyInstaller artifacts
rm -rf build/ "${BINARY_NAME}.spec" 2>/dev/null || true

echo ""
echo "✅ Sidecar binary built: ${OUTPUT_DIR}/${BINARY_NAME}"
echo "   Size: $(du -h "${OUTPUT_DIR}/${BINARY_NAME}" | cut -f1)"
