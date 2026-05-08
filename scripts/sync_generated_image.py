from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image


def _latest_codex_image() -> Path:
    root = Path.home() / ".codex" / "generated_images"
    candidates = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}
    ]
    if not candidates:
        raise FileNotFoundError(f"No generated images found under {root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _parse_size(value: str) -> tuple[int, int]:
    cleaned = value.lower().replace("x", " ").replace("*", " ")
    parts = [part for part in cleaned.split() if part]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("size must look like WIDTHxHEIGHT")
    return int(parts[0]), int(parts[1])


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy or crop the latest Codex generated image into the workspace.")
    parser.add_argument("--source", help="Explicit source image path. Defaults to the latest Codex generated image.")
    parser.add_argument("--dest", required=True, help="Destination image path.")
    parser.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"), help="Crop box from the source image.")
    parser.add_argument("--resize", type=_parse_size, help="Resize the result to WIDTHxHEIGHT with Lanczos resampling.")
    args = parser.parse_args()

    source_path = Path(args.source) if args.source else _latest_codex_image()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    dest_path = Path(args.dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as image:
        working = image.copy()
    if args.crop:
        x, y, w, h = args.crop
        working = working.crop((x, y, x + w, y + h))
    if args.resize:
        working = working.resize(args.resize, Image.Resampling.LANCZOS)

    if not args.crop and not args.resize and source_path.suffix.lower() == dest_path.suffix.lower():
        shutil.copyfile(source_path, dest_path)
        return 0

    working.save(dest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
