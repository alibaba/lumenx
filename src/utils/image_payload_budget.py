from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - Pillow is a runtime dependency.
    Image = None
    ImageOps = None


DEFAULT_CODEX_IMAGEGEN_MAX_REFERENCE_BYTES = 5 * 1024 * 1024
DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES = 1 * 1024 * 1024
DEFAULT_CODEX_IMAGEGEN_MAX_SIDE = 1920
DEFAULT_CODEX_IMAGEGEN_MIN_SIDE = 640
DEFAULT_CODEX_IMAGEGEN_JPEG_QUALITY = 92
DEFAULT_CODEX_IMAGEGEN_MIN_JPEG_QUALITY = 45
DEFAULT_REFERENCE_BACKGROUND_RGB = (246, 246, 244)


@dataclass(frozen=True)
class _SourceReference:
    index: int
    path: Path
    original_bytes: int
    original_size: Tuple[int, int]
    image: "Image.Image"
    alpha_flattened: bool


@dataclass(frozen=True)
class _EncodedReference:
    source: _SourceReference
    content: bytes
    size: Tuple[int, int]


def estimate_base64_payload_bytes(raw_bytes: int) -> int:
    return int(math.ceil(max(0, raw_bytes) * 4 / 3))


def prepare_image_references_for_payload(
    reference_paths: Sequence[str | Path],
    output_dir: str | Path,
    *,
    max_total_bytes: int = DEFAULT_CODEX_IMAGEGEN_MAX_REFERENCE_BYTES,
    max_side: int = DEFAULT_CODEX_IMAGEGEN_MAX_SIDE,
    min_side: int = DEFAULT_CODEX_IMAGEGEN_MIN_SIDE,
    jpeg_quality: int = DEFAULT_CODEX_IMAGEGEN_JPEG_QUALITY,
    min_jpeg_quality: int = DEFAULT_CODEX_IMAGEGEN_MIN_JPEG_QUALITY,
    background_rgb: Tuple[int, int, int] = DEFAULT_REFERENCE_BACKGROUND_RGB,
) -> Dict[str, Any]:
    """Create JPEG reference copies that fit a hard aggregate request budget.

    This is intentionally aggregate-budget first. A gateway can reject a request
    even when every individual image is valid, because JSON/base64 or multipart
    overhead is paid by the whole request.
    """

    if Image is None:
        raise RuntimeError("Pillow is required to prepare image references.")
    if not reference_paths:
        raise ValueError("At least one reference image is required.")
    if max_total_bytes <= 0:
        raise ValueError("max_total_bytes must be positive.")
    if max_side <= 0 or min_side <= 0:
        raise ValueError("max_side and min_side must be positive.")
    if min_side > max_side:
        raise ValueError("min_side cannot be greater than max_side.")

    output_path = Path(output_dir)
    sources = _load_source_references(reference_paths, background_rgb)
    original_total = sum(source.original_bytes for source in sources)

    smallest_candidate: Tuple[int, int, List[_EncodedReference]] | None = None
    for candidate_side, candidate_quality in _candidate_reference_settings(
        max_side,
        min_side,
        jpeg_quality,
        min_jpeg_quality,
    ):
        encoded = [
            _encode_reference(source, max_side=candidate_side, jpeg_quality=candidate_quality)
            for source in sources
        ]
        candidate_total = sum(len(item.content) for item in encoded)
        if smallest_candidate is None or candidate_total < sum(
            len(item.content) for item in smallest_candidate[2]
        ):
            smallest_candidate = (candidate_side, candidate_quality, encoded)
        if candidate_total <= max_total_bytes:
            return _write_payload_manifest(
                encoded,
                output_path,
                max_total_bytes=max_total_bytes,
                original_total_bytes=original_total,
                max_side_used=candidate_side,
                jpeg_quality_used=candidate_quality,
                fits_budget=True,
            )

    assert smallest_candidate is not None
    smallest_total = sum(len(item.content) for item in smallest_candidate[2])
    raise ValueError(
        "Reference images cannot fit the aggregate payload budget: "
        f"smallest={smallest_total} bytes, budget={max_total_bytes} bytes, "
        f"refs={len(sources)}."
    )


def _load_source_references(
    reference_paths: Sequence[str | Path],
    background_rgb: Tuple[int, int, int],
) -> List[_SourceReference]:
    sources: List[_SourceReference] = []
    for index, raw_path in enumerate(reference_paths, start=1):
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Reference image not found: {path}")

        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened) if ImageOps is not None else opened.copy()
            image.load()

        original_size = image.size
        rgb_image, alpha_flattened = _to_rgb_reference(image, background_rgb)
        sources.append(
            _SourceReference(
                index=index,
                path=path,
                original_bytes=path.stat().st_size,
                original_size=original_size,
                image=rgb_image,
                alpha_flattened=alpha_flattened,
            )
        )
    return sources


def _to_rgb_reference(
    image: "Image.Image",
    background_rgb: Tuple[int, int, int],
) -> Tuple["Image.Image", bool]:
    if image.mode in {"RGBA", "LA"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (*background_rgb, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB"), True
    if image.mode == "P" and image.info.get("transparency") is not None:
        return _to_rgb_reference(image.convert("RGBA"), background_rgb)
    if image.mode != "RGB":
        return image.convert("RGB"), False
    return image.copy(), False


def _descending_side_steps(max_side: int, min_side: int) -> Iterable[int]:
    current = max_side
    seen = set()
    while current >= min_side and current not in seen:
        seen.add(current)
        yield current
        if current == min_side:
            break
        current = max(min_side, int(round(current * 0.85)))


def _descending_quality_steps(jpeg_quality: int, min_jpeg_quality: int) -> Iterable[int]:
    candidates = [
        jpeg_quality,
        92,
        90,
        88,
        86,
        84,
        82,
        80,
        78,
        75,
        72,
        69,
        66,
        63,
        60,
        57,
        54,
        50,
        min_jpeg_quality,
    ]
    normalized = sorted(
        {
            min(95, max(min_jpeg_quality, int(candidate)))
            for candidate in candidates
            if int(candidate) >= min_jpeg_quality
        },
        reverse=True,
    )
    for quality in normalized:
        yield quality


def _candidate_reference_settings(
    max_side: int,
    min_side: int,
    jpeg_quality: int,
    min_jpeg_quality: int,
) -> List[Tuple[int, int]]:
    candidates = [
        (side, quality)
        for side in _descending_side_steps(max_side, min_side)
        for quality in _descending_quality_steps(jpeg_quality, min_jpeg_quality)
    ]
    return sorted(
        set(candidates),
        key=lambda item: (_reference_quality_score(item[0], item[1]), item[1], item[0]),
        reverse=True,
    )


def _reference_quality_score(max_side: int, jpeg_quality: int) -> float:
    # Penalize low JPEG quality more strongly than moderate resolution changes.
    return float(max_side) * (float(jpeg_quality) / 100.0) ** 2


def _resize_to_max_side(image: "Image.Image", max_side: int) -> "Image.Image":
    current_max = max(image.size)
    if current_max <= max_side:
        return image.copy()
    scale = max_side / float(current_max)
    size = (
        max(1, int(round(image.size[0] * scale))),
        max(1, int(round(image.size[1] * scale))),
    )
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    return image.resize(size, resampling)


def _encode_reference(
    source: _SourceReference,
    *,
    max_side: int,
    jpeg_quality: int,
) -> _EncodedReference:
    resized = _resize_to_max_side(source.image, max_side)
    buffer = BytesIO()
    resized.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True, progressive=True)
    return _EncodedReference(source=source, content=buffer.getvalue(), size=resized.size)


def _safe_output_name(source: _SourceReference) -> str:
    stem = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_" for char in source.path.stem
    )
    return f"{source.index:02d}_{stem}_codex_safe.jpg"


def _write_payload_manifest(
    encoded: Sequence[_EncodedReference],
    output_dir: Path,
    *,
    max_total_bytes: int,
    original_total_bytes: int,
    max_side_used: int,
    jpeg_quality_used: int,
    fits_budget: bool,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    references: List[Dict[str, Any]] = []
    prepared_total = 0

    for item in encoded:
        prepared_path = output_dir / _safe_output_name(item.source)
        prepared_path.write_bytes(item.content)
        prepared_bytes = len(item.content)
        prepared_total += prepared_bytes
        references.append(
            {
                "index": item.source.index,
                "source_path": str(item.source.path.resolve()),
                "prepared_path": str(prepared_path.resolve()),
                "source_bytes": item.source.original_bytes,
                "prepared_bytes": prepared_bytes,
                "source_size": list(item.source.original_size),
                "prepared_size": list(item.size),
                "content_type": "image/jpeg",
                "alpha_flattened": item.source.alpha_flattened,
            }
        )

    return {
        "max_total_bytes": max_total_bytes,
        "fits_budget": fits_budget,
        "reference_count": len(references),
        "total_source_bytes": original_total_bytes,
        "total_prepared_bytes": prepared_total,
        "estimated_base64_bytes": estimate_base64_payload_bytes(prepared_total),
        "max_side_used": max_side_used,
        "jpeg_quality_used": jpeg_quality_used,
        "references": references,
    }
