from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.apps.comic_gen.frame_crop_composition import (  # noqa: E402
    compose_frame_crops_from_manifest,
    detect_manifest_crops_from_path,
    manifest_with_detected_bboxes,
    resolve_manifest_path,
    write_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compose edited frame crops back onto a base storyboard image."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to a crop composition manifest JSON.",
    )
    parser.add_argument("--out", help="Optional output image path override.")
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip exact base-crop coordinate verification when bbox values are provided.",
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="Detect and print crop coordinates without composing an output image.",
    )
    parser.add_argument(
        "--write-detected-manifest",
        help="Optional path for a manifest copy with detected bbox values filled in.",
    )
    args = parser.parse_args()

    manifest_path = resolve_manifest_path(args.manifest)

    if args.detect_only or args.write_detected_manifest:
        detections = detect_manifest_crops_from_path(manifest_path)
        print(json.dumps(detections, ensure_ascii=False, indent=2))

        if args.write_detected_manifest:
            from src.apps.comic_gen.frame_crop_composition import load_manifest

            manifest = load_manifest(manifest_path)
            detected_manifest = manifest_with_detected_bboxes(manifest, detections)
            detected_manifest_path = resolve_manifest_path(args.write_detected_manifest)
            write_manifest(detected_manifest_path, detected_manifest)
            print(f"wrote {detected_manifest_path}")

        if args.detect_only:
            return 0

    result = compose_frame_crops_from_manifest(
        manifest_path,
        out_override=args.out,
        verify=not args.no_verify,
    )
    for crop in result["crops"]:
        bbox = crop["bbox"]
        print(
            "composited {crop_id}: {edited_crop} -> x={x}, y={y}, w={w}, h={h}".format(
                crop_id=crop["id"],
                edited_crop=crop["edited_crop"],
                x=bbox["x"],
                y=bbox["y"],
                w=bbox["width"],
                h=bbox["height"],
            )
        )
    print(f"wrote {result['output_image']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
