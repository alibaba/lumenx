from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is an optional speed-up
    np = None


REPO_ROOT = Path(__file__).resolve().parents[3]
BBox = Tuple[int, int, int, int]


def resolve_manifest_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path.resolve()
    return (REPO_ROOT / path).resolve()


def _resolve_path(raw_path: str, manifest_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path.resolve()

    repo_path = (REPO_ROOT / path).resolve()
    if repo_path.exists():
        return repo_path

    manifest_relative_path = (manifest_dir / path).resolve()
    if manifest_relative_path.exists():
        return manifest_relative_path

    return repo_path


def load_manifest(path: str | Path) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_manifest(path: str | Path, manifest: Dict[str, Any]) -> None:
    manifest_path = resolve_manifest_path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _crop_label(entry: Dict[str, Any]) -> str:
    return str(entry.get("id") or entry.get("role") or "crop")


def _bbox_tuple(entry: Dict[str, Any]) -> Optional[BBox]:
    bbox = entry.get("bbox")
    if not isinstance(bbox, dict):
        return None

    try:
        x = int(bbox["x"])
        y = int(bbox["y"])
        width = int(bbox["width"])
        height = int(bbox["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid bbox for crop {_crop_label(entry)}: {bbox}") from exc

    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid non-positive bbox for crop {_crop_label(entry)}: {bbox}")

    return x, y, width, height


def _bbox_dict(bbox: BBox) -> Dict[str, int]:
    x, y, width, height = bbox
    return {"x": x, "y": y, "width": width, "height": height}


def _anchor_points(width: int, height: int) -> List[Tuple[int, int]]:
    raw_points = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, height // 2),
        (width // 4, height // 4),
        ((width * 3) // 4, (height * 3) // 4),
        (width // 3, (height * 2) // 3),
        ((width * 2) // 3, height // 3),
    ]
    points: List[Tuple[int, int]] = []
    seen = set()
    for point in raw_points:
        if point not in seen:
            points.append(point)
            seen.add(point)
    return points


def _find_exact_crop_numpy(base_image: Image.Image, crop_image: Image.Image) -> BBox:
    base = np.array(base_image.convert("RGB"))
    crop = np.array(crop_image.convert("RGB"))
    base_height, base_width = base.shape[:2]
    crop_height, crop_width = crop.shape[:2]

    if crop_height > base_height or crop_width > base_width:
        raise ValueError("Crop is larger than the base image.")

    search_height = base_height - crop_height + 1
    search_width = base_width - crop_width + 1
    mask = np.ones((search_height, search_width), dtype=bool)

    for dx, dy in _anchor_points(crop_width, crop_height):
        target = crop[dy, dx]
        window = base[dy : dy + search_height, dx : dx + search_width]
        mask &= np.all(window == target, axis=2)
        if not mask.any():
            break

    candidates = np.argwhere(mask)
    for y, x in candidates:
        if np.array_equal(base[y : y + crop_height, x : x + crop_width], crop):
            return int(x), int(y), int(crop_width), int(crop_height)

    raise ValueError("Could not locate an exact crop match in the base image.")


def _find_exact_crop_pillow(base_image: Image.Image, crop_image: Image.Image) -> BBox:
    base = base_image.convert("RGB")
    crop = crop_image.convert("RGB")
    base_width, base_height = base.size
    crop_width, crop_height = crop.size

    if crop_height > base_height or crop_width > base_width:
        raise ValueError("Crop is larger than the base image.")

    search_height = base_height - crop_height + 1
    search_width = base_width - crop_width + 1
    base_pixels = base.load()
    crop_pixels = crop.load()
    anchors = _anchor_points(crop_width, crop_height)
    crop_bytes = crop.tobytes()

    for y in range(search_height):
        for x in range(search_width):
            for dx, dy in anchors:
                if base_pixels[x + dx, y + dy] != crop_pixels[dx, dy]:
                    break
            else:
                if base.crop((x, y, x + crop_width, y + crop_height)).tobytes() == crop_bytes:
                    return int(x), int(y), int(crop_width), int(crop_height)

    raise ValueError("Could not locate an exact crop match in the base image.")


def find_exact_crop(base_image: Image.Image, crop_image: Image.Image) -> BBox:
    if np is not None:
        return _find_exact_crop_numpy(base_image, crop_image)
    return _find_exact_crop_pillow(base_image, crop_image)


def _validate_bbox(
    *,
    base_image: Image.Image,
    base_crop_path: Path,
    bbox: BBox,
    crop_id: str,
) -> None:
    with Image.open(base_crop_path) as loaded_crop:
        detected = find_exact_crop(base_image, loaded_crop)
    if detected != bbox:
        raise ValueError(
            f"Crop {crop_id} bbox mismatch: manifest={bbox}, detected={detected}"
        )


def detect_manifest_crops(
    manifest: Dict[str, Any],
    manifest_dir: Path,
) -> List[Dict[str, Any]]:
    base_path = _resolve_path(str(manifest["base_image"]), manifest_dir)
    crops: List[Dict[str, Any]] = list(manifest.get("crops") or [])
    if not crops:
        raise ValueError("Manifest must contain at least one crop entry.")

    with Image.open(base_path) as loaded_base:
        base_image = loaded_base.convert("RGB")

    detections: List[Dict[str, Any]] = []
    for entry in crops:
        crop_id = _crop_label(entry)
        if not entry.get("base_crop"):
            raise ValueError(f"Crop {crop_id} cannot be detected without base_crop.")

        base_crop_path = _resolve_path(str(entry["base_crop"]), manifest_dir)
        with Image.open(base_crop_path) as loaded_crop:
            detected = find_exact_crop(base_image, loaded_crop)

        declared = _bbox_tuple(entry)
        detections.append(
            {
                "id": crop_id,
                "base_crop": str(base_crop_path),
                "detected_bbox": _bbox_dict(detected),
                "manifest_bbox": _bbox_dict(declared) if declared else None,
                "matches_manifest": declared is None or declared == detected,
            }
        )

    return detections


def detect_manifest_crops_from_path(manifest_path: str | Path) -> List[Dict[str, Any]]:
    resolved_manifest_path = resolve_manifest_path(manifest_path)
    manifest = load_manifest(resolved_manifest_path)
    return detect_manifest_crops(manifest, resolved_manifest_path.parent)


def manifest_with_detected_bboxes(
    manifest: Dict[str, Any],
    detections: List[Dict[str, Any]],
) -> Dict[str, Any]:
    updated = deepcopy(manifest)
    for entry, detection in zip(updated.get("crops") or [], detections):
        entry["bbox"] = detection["detected_bbox"]
    return updated


def compose_frame_crops_from_manifest(
    manifest_path: str | Path,
    out_override: Optional[str | Path] = None,
    verify: bool = True,
) -> Dict[str, Any]:
    resolved_manifest_path = resolve_manifest_path(manifest_path)
    manifest = load_manifest(resolved_manifest_path)
    output_path, crop_summaries = _compose(
        manifest=manifest,
        manifest_dir=resolved_manifest_path.parent,
        out_override=str(out_override) if out_override else None,
        verify=verify,
    )
    base_path = _resolve_path(str(manifest["base_image"]), resolved_manifest_path.parent)
    return {
        "manifest_path": str(resolved_manifest_path),
        "base_image": str(base_path),
        "output_image": str(output_path),
        "frame_id": manifest.get("frame_id"),
        "project_slug": manifest.get("project_slug"),
        "schema_version": manifest.get("schema_version"),
        "crops": crop_summaries,
    }


def _compose(
    manifest: Dict[str, Any],
    manifest_dir: Path,
    out_override: Optional[str],
    verify: bool,
) -> Tuple[Path, List[Dict[str, Any]]]:
    base_path = _resolve_path(str(manifest["base_image"]), manifest_dir)
    output_path = _resolve_path(out_override or str(manifest["output_image"]), manifest_dir)
    crops: List[Dict[str, Any]] = list(manifest.get("crops") or [])

    if not crops:
        raise ValueError("Manifest must contain at least one crop entry.")

    with Image.open(base_path) as loaded_base:
        base_image = loaded_base.convert("RGBA")
    canvas = base_image.copy()
    crop_summaries: List[Dict[str, Any]] = []

    for entry in crops:
        crop_id = _crop_label(entry)
        edited_crop_path = _resolve_path(str(entry["edited_crop"]), manifest_dir)
        base_crop_path = (
            _resolve_path(str(entry["base_crop"]), manifest_dir)
            if entry.get("base_crop")
            else None
        )
        bbox = _bbox_tuple(entry)

        if bbox is None:
            if not base_crop_path:
                raise ValueError(f"Crop {crop_id} requires bbox or base_crop.")
            with Image.open(base_crop_path) as loaded_crop:
                bbox = find_exact_crop(base_image, loaded_crop)
        elif verify and base_crop_path:
            _validate_bbox(
                base_image=base_image,
                base_crop_path=base_crop_path,
                bbox=bbox,
                crop_id=crop_id,
            )

        x, y, width, height = bbox
        with Image.open(edited_crop_path) as loaded_edited_crop:
            edited_crop = loaded_edited_crop.convert("RGBA")
        if edited_crop.size != (width, height):
            raise ValueError(
                f"Crop {crop_id} size mismatch: edited={edited_crop.size}, bbox={(width, height)}"
            )

        canvas.paste(edited_crop, (x, y), edited_crop)
        crop_summaries.append(
            {
                "id": crop_id,
                "role": entry.get("role"),
                "prompt": entry.get("prompt"),
                "reference_images": list(entry.get("reference_images") or []),
                "base_crop": str(base_crop_path) if base_crop_path else None,
                "edited_crop": str(edited_crop_path),
                "bbox": _bbox_dict(bbox),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    return output_path, crop_summaries
