from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (REPO_ROOT / candidate).resolve()


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reference_alpha_mask(image: Image.Image) -> Image.Image:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    border = 18

    strips = [
        rgb[:border, :, :],
        rgb[-border:, :, :],
        rgb[:, :border, :],
        rgb[:, -border:, :],
    ]
    border_pixels = np.concatenate([strip.reshape(-1, 3) for strip in strips], axis=0)
    bg = border_pixels.mean(axis=0)

    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    max_channel = rgb.max(axis=2)
    min_channel = rgb.min(axis=2)
    saturation = max_channel - min_channel
    blue_lift = rgb[:, :, 2] - ((rgb[:, :, 0] + rgb[:, :, 1]) / 2.0)
    warm_lift = rgb[:, :, 0] - rgb[:, :, 2]

    alpha = np.maximum.reduce(
        [
            (dist - 18.0) / 34.0,
            (saturation - 18.0) / 50.0,
            (max_channel - 178.0) / 45.0,
            (82.0 - max_channel) / 30.0,
            (blue_lift - 8.0) / 35.0,
            (warm_lift - 18.0) / 45.0,
        ]
    )
    alpha = np.clip((np.clip(alpha, 0.0, 1.0) - 0.2) / 0.8, 0.0, 1.0)
    alpha = (alpha * 255.0).astype(np.uint8)
    mask = Image.fromarray(alpha, mode="L").filter(ImageFilter.GaussianBlur(radius=1.4))
    return mask


def _soft_ellipse_mask(size: tuple[int, int]) -> Image.Image:
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width]
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    rx = width * 0.50
    ry = height * 0.52
    dist = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    alpha = np.clip((1.08 - dist) / 0.18, 0.0, 1.0)
    alpha = (alpha * 255.0).astype(np.uint8)
    return Image.fromarray(alpha, mode="L").filter(ImageFilter.GaussianBlur(radius=2.0))


def _multiply_masks(first: Image.Image, second: Image.Image) -> Image.Image:
    first_alpha = np.asarray(first, dtype=np.float32) / 255.0
    second_alpha = np.asarray(second, dtype=np.float32) / 255.0
    return Image.fromarray((first_alpha * second_alpha * 255.0).astype(np.uint8), mode="L")


def _bbox_tuple(raw: dict) -> tuple[int, int, int, int]:
    return int(raw["x"]), int(raw["y"]), int(raw["width"]), int(raw["height"])


def _alpha_composite_clipped(base: Image.Image, overlay: Image.Image, xy: tuple[int, int]) -> None:
    x, y = xy
    left = max(0, -x)
    top = max(0, -y)
    right = min(overlay.width, base.width - x)
    bottom = min(overlay.height, base.height - y)
    if right <= left or bottom <= top:
        raise ValueError(f"Identity patch is outside the base crop: xy={xy}, size={overlay.size}")

    base.alpha_composite(overlay.crop((left, top, right, bottom)), (max(0, x), max(0, y)))


def compose_child_identity_crop(manifest_path: Path, output_path: Path) -> Path:
    manifest = _load_manifest(manifest_path)
    crop = manifest["crops"][0]

    base_crop_path = _resolve(crop["base_crop"])
    reference_path = _resolve(crop["reference_images"][0])
    source_box = _bbox_tuple(crop["reference_source_bbox"])
    target_box = _bbox_tuple(crop.get("identity_patch_bbox") or crop["bbox"])

    with Image.open(base_crop_path) as base_image, Image.open(reference_path) as reference_image:
        base = base_image.convert("RGBA")
        x, y, width, height = target_box
        source_x, source_y, source_width, source_height = source_box
        source = reference_image.convert("RGBA").crop(
            (
                source_x,
                source_y,
                source_x + source_width,
                source_y + source_height,
            )
        )
        alpha = _multiply_masks(_reference_alpha_mask(source), _soft_ellipse_mask(source.size))
        source.putalpha(alpha)
        source = source.resize((width, height), Image.Resampling.LANCZOS)

        composed = base.copy()
        _alpha_composite_clipped(composed, source, (x, y))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        composed.save(output_path)

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose child Xiaoqi identity crops locally.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest_path = _resolve(args.manifest)
    output_path = _resolve(args.output)
    compose_child_identity_crop(manifest_path, output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
