#!/bin/bash
# Dependency preparation is deliberately separate from this build entry.
set -euo pipefail

if [[ "${1:-}" != "--python" || $# -lt 2 ]]; then
    echo "Usage: $0 --python /absolute/python --source /fixed/source --static /verified/static --ffmpeg /absolute/ffmpeg --ffprobe /absolute/ffprobe --output /new/owned/directory [--plan] [--dmg]" >&2
    exit 2
fi
BUILD_PYTHON="$2"
shift 2
if [[ "$BUILD_PYTHON" != /* || ! -x "$BUILD_PYTHON" ]]; then
    echo "--python must be an absolute executable path" >&2
    exit 2
fi
SCRIPT_PATH="${BASH_SOURCE[0]}"
[[ "$SCRIPT_PATH" == */* ]] || SCRIPT_PATH="./$SCRIPT_PATH"
SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd)"
exec "$BUILD_PYTHON" -B "$SCRIPT_DIR/scripts/build_macos.py" "$@"
