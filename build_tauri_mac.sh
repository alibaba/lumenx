#!/usr/bin/env bash
# build_tauri_mac.sh — One-click build for LumenX Studio macOS app (.app + .dmg)
# Produces: src-tauri/target/release/bundle/dmg/LumenX Studio_*.dmg

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "╔═══════════════════════════════════════════════════╗"
echo "║  LumenX Studio — Tauri macOS Build               ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ─── Step 1: Check prerequisites ───
echo "→ Checking prerequisites..."

if ! command -v cargo &>/dev/null; then
    echo "❌ Rust/Cargo not found. Install via: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

if ! command -v node &>/dev/null; then
    echo "❌ Node.js not found."
    exit 1
fi

if ! command -v npx &>/dev/null; then
    echo "❌ npx not found."
    exit 1
fi

echo "  ✓ Rust $(rustc --version | awk '{print $2}')"
echo "  ✓ Node $(node --version)"
echo ""

# ─── Step 2: Build Python sidecar ───
echo "→ Step 2: Building Python sidecar..."
bash build_sidecar.sh
echo ""

# ─── Step 3: Build frontend for Tauri ───
echo "→ Step 3: Building frontend (static export for Tauri)..."
cd frontend
TAURI_BUILD=true npm run build
cd ..
echo "  ✓ Frontend built to frontend/out/"
echo ""

# ─── Step 4: Build Tauri app ───
echo "→ Step 4: Building Tauri application..."

# Determine target based on architecture
ARCH=$(uname -m)
case "$ARCH" in
    arm64|aarch64) TARGET="aarch64-apple-darwin" ;;
    x86_64)        TARGET="x86_64-apple-darwin" ;;
    *)             echo "❌ Unsupported architecture: $ARCH"; exit 1 ;;
esac

echo "  Target: ${TARGET}"
npx tauri build --target "$TARGET"

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║  ✅ Build Complete!                               ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""
echo "Output:"
echo "  .app: src-tauri/target/${TARGET}/release/bundle/macos/"
echo "  .dmg: src-tauri/target/${TARGET}/release/bundle/dmg/"
echo ""

# List the output
ls -la "src-tauri/target/${TARGET}/release/bundle/dmg/" 2>/dev/null || echo "  (DMG not found — check build output above)"
