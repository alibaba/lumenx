"""Local AV metadata from private files or verified content-version bytes.

Metadata describes the fixed input; it is not proof that every frame decodes.
No caller-supplied URLs, network protocols, playlists or editable node metadata
are used when probing frozen media.
"""

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal

from .media_versions import MediaVersion
from .system_check import get_ffmpeg_path


AV_FORMATS = {
    "audio": "mp3,wav,ogg,flac,aac,mov,m4a,3gp,3g2,mj2",
    "video": "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm",
}
IMAGE_FORMATS = "image2,png_pipe,jpeg_pipe,webp_pipe,bmp_pipe"
_FROZEN_TYPES = {
    ("reference_video", "video/mp4"): ("video", ".mp4", 100 * 1024 * 1024, {"mov", "mp4"}),
    ("reference_video", "video/quicktime"): ("video", ".mov", 100 * 1024 * 1024, {"mov"}),
    ("reference_audio", "audio/wav"): ("audio", ".wav", 15 * 1024 * 1024, {"wav"}),
    ("reference_audio", "audio/mpeg"): ("audio", ".mp3", 15 * 1024 * 1024, {"mp3"}),
}
_FROZEN_IMAGE_TYPES = {
    ("first_frame", "image/png"): (".png", {"png_pipe"}, {"png"}),
    ("last_frame", "image/png"): (".png", {"png_pipe"}, {"png"}),
    ("reference_image", "image/png"): (".png", {"png_pipe"}, {"png"}),
    ("first_frame", "image/jpeg"): (".jpg", {"jpeg_pipe"}, {"mjpeg"}),
    ("last_frame", "image/jpeg"): (".jpg", {"jpeg_pipe"}, {"mjpeg"}),
    ("reference_image", "image/jpeg"): (".jpg", {"jpeg_pipe"}, {"mjpeg"}),
    ("first_frame", "image/webp"): (".webp", {"webp_pipe"}, {"webp"}),
    ("last_frame", "image/webp"): (".webp", {"webp_pipe"}, {"webp"}),
    ("reference_image", "image/webp"): (".webp", {"webp_pipe"}, {"webp"}),
    ("first_frame", "image/bmp"): (".bmp", {"image2"}, {"bmp"}),
    ("last_frame", "image/bmp"): (".bmp", {"image2"}, {"bmp"}),
    ("reference_image", "image/bmp"): (".bmp", {"image2"}, {"bmp"}),
}
_FROZEN_IMAGE_LIMIT = 20 * 1024 * 1024


class MediaProbeError(ValueError):
    """Local media metadata could not be verified."""


@dataclass(frozen=True)
class FrozenMediaMetadata:
    kind: Literal["audio", "video"]
    duration: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    format_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrozenImageMetadata:
    kind: Literal["image"]
    width: int
    height: int
    format_names: tuple[str, ...] = ()
    codec_name: str | None = None
    pixel_format: str | None = None
    has_alpha: bool = False


def probe_media_file(path: Path, kind: str, *, probe_executable: str | None = None) -> FrozenMediaMetadata:
    """Probe a caller-owned private file; callers own its lifecycle/identity."""
    if kind not in AV_FORMATS:
        raise MediaProbeError("Unsupported probe media type")
    if probe_executable is None:
        ffmpeg = get_ffmpeg_path() or shutil.which("ffmpeg") or "ffmpeg"
        probe_executable = str(Path(ffmpeg).with_name("ffprobe"))
    try:
        result = subprocess.run(
            [probe_executable, "-protocol_whitelist", "file", "-format_whitelist", AV_FORMATS[kind],
             "-v", "error", "-select_streams", "v:0" if kind == "video" else "a:0",
             "-show_entries", "stream=codec_type,width,height,duration,avg_frame_rate,r_frame_rate:format=duration,format_name",
             "-of", "json", str(path)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True, timeout=20,
        )
        if len(result.stdout) > 64 * 1024:
            raise MediaProbeError("Media metadata response exceeds limit")
        info = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError) as exc:
        raise MediaProbeError("Invalid media metadata") from exc
    if not isinstance(info, dict) or not isinstance(info.get("streams"), list):
        raise MediaProbeError("Invalid media metadata")
    stream = next((item for item in info["streams"] if isinstance(item, dict) and item.get("codec_type") == kind), None)
    if stream is None:
        raise MediaProbeError("Media content does not match its type")
    container = info.get("format", {})
    if not isinstance(container, dict):
        raise MediaProbeError("Invalid media metadata")
    duration = _positive_number(container.get("duration") or stream.get("duration"))
    format_name = container.get("format_name", "")
    if not isinstance(format_name, str):
        raise MediaProbeError("Invalid media container")
    formats = tuple(format_name.split(",")) if format_name else ()
    if kind == "audio":
        return FrozenMediaMetadata(kind="audio", duration=duration, format_names=formats)
    width, height = stream.get("width"), stream.get("height")
    if any(type(value) is not int or value <= 0 for value in (width, height)):
        raise MediaProbeError("Invalid media dimensions")
    fps = _frame_rate(stream.get("avg_frame_rate")) or _frame_rate(stream.get("r_frame_rate"))
    return FrozenMediaMetadata(kind="video", duration=duration, width=width, height=height, fps=fps, format_names=formats)


def probe_image_file(
    path: Path, *, input_format: str | None = None, probe_executable: str | None = None,
    decode_executable: str | None = None,
) -> FrozenImageMetadata:
    """Probe a caller-owned private image file without trusting its extension."""
    if input_format is not None and input_format != "image2":
        raise MediaProbeError("Unsupported explicit image format")
    if probe_executable is None or decode_executable is None:
        ffmpeg = get_ffmpeg_path() or shutil.which("ffmpeg") or "ffmpeg"
    if probe_executable is None:
        probe_executable = str(Path(ffmpeg).with_name("ffprobe"))
    if decode_executable is None:
        decode_executable = ffmpeg
    args = [
        probe_executable,
        "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height,pix_fmt:format=format_name",
        "-of", "json",
        *_image_input_args(input_format),
        "-i", str(path),
    ]
    try:
        subprocess.run(
            [
                decode_executable,
                "-v", "error",
                "-xerror",
                "-err_detect", "explode",
                "-nostdin",
                *_image_input_args(input_format),
                "-i", str(path),
                "-frames:v", "1",
                "-f", "null",
                "-",
            ],
            capture_output=True, stdin=subprocess.DEVNULL, check=True, timeout=20,
        )
        result = subprocess.run(
            args, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True, timeout=20,
        )
        if len(result.stdout) > 64 * 1024:
            raise MediaProbeError("Image metadata response exceeds limit")
        info = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError) as exc:
        raise MediaProbeError("Invalid image metadata") from exc
    if not isinstance(info, dict) or not isinstance(info.get("streams"), list):
        raise MediaProbeError("Invalid image metadata")
    stream = next(
        (item for item in info["streams"] if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    if stream is None:
        raise MediaProbeError("Image content does not match its type")
    container = info.get("format", {})
    if not isinstance(container, dict):
        raise MediaProbeError("Invalid image metadata")
    width, height = stream.get("width"), stream.get("height")
    if any(type(value) is not int or value <= 0 for value in (width, height)):
        raise MediaProbeError("Invalid image dimensions")
    format_name = container.get("format_name", "")
    if not isinstance(format_name, str):
        raise MediaProbeError("Invalid image container")
    pixel_format = stream.get("pix_fmt")
    if pixel_format is not None and not isinstance(pixel_format, str):
        raise MediaProbeError("Invalid image pixel format")
    codec_name = stream.get("codec_name")
    if codec_name is not None and not isinstance(codec_name, str):
        raise MediaProbeError("Invalid image codec")
    formats = tuple(format_name.split(",")) if format_name else ()
    return FrozenImageMetadata(
        kind="image", width=width, height=height, format_names=formats,
        codec_name=codec_name, pixel_format=pixel_format,
        has_alpha=_pixel_format_has_alpha(pixel_format),
    )


def probe_frozen_media(version: MediaVersion, data: bytes) -> FrozenMediaMetadata:
    """Derive AV metadata from the exact bytes identified by a fixed record."""
    if not isinstance(version, MediaVersion):
        raise MediaProbeError("Invalid frozen media version")
    spec = _FROZEN_TYPES.get((version.role, version.declared_media_type))
    if spec is None:
        raise MediaProbeError("Unsupported frozen audio/video type")
    kind, suffix, limit, formats = spec
    if (type(data) is not bytes or not data or len(data) > limit or len(data) != version.size_bytes
            or hashlib.sha256(data).hexdigest() != version.sha256):
        raise MediaProbeError("Frozen media bytes do not match their version")
    with tempfile.TemporaryDirectory(prefix="lumenx-frozen-probe-") as directory:
        path = Path(directory) / f"input{suffix}"
        path.write_bytes(data)
        metadata = probe_media_file(path, kind)
    if not formats.intersection(metadata.format_names):
        raise MediaProbeError("Frozen media container does not match declared type")
    return metadata


def probe_frozen_image(
    version: MediaVersion, data: bytes, *, max_bytes: int = _FROZEN_IMAGE_LIMIT,
) -> FrozenImageMetadata:
    """Derive image metadata from the exact bytes identified by a fixed record."""
    if not isinstance(version, MediaVersion):
        raise MediaProbeError("Invalid frozen image version")
    if type(max_bytes) is not int or not 0 < max_bytes <= 50 * 1024 * 1024:
        raise MediaProbeError("Invalid frozen image byte limit")
    spec = _FROZEN_IMAGE_TYPES.get((version.role, version.declared_media_type))
    if spec is None:
        raise MediaProbeError("Unsupported frozen image type")
    suffix, formats, codecs = spec
    is_bmp = version.declared_media_type == "image/bmp"
    input_format = "image2" if is_bmp else None
    if (
        type(data) is not bytes
        or not data
        or len(data) > max_bytes
        or len(data) != version.size_bytes
        or hashlib.sha256(data).hexdigest() != version.sha256
    ):
        raise MediaProbeError("Frozen image bytes do not match their version")
    with tempfile.TemporaryDirectory(prefix="lumenx-frozen-image-probe-") as directory:
        path = Path(directory) / f"input{suffix}"
        path.write_bytes(data)
        metadata = probe_image_file(path, input_format=input_format)
    if version.declared_media_type == "image/png":
        metadata = FrozenImageMetadata(
            kind="image", width=metadata.width, height=metadata.height,
            format_names=metadata.format_names, codec_name=metadata.codec_name,
            pixel_format=metadata.pixel_format, has_alpha=_png_has_transparency(data),
        )
    if not formats.intersection(metadata.format_names):
        raise MediaProbeError("Frozen image container does not match declared type")
    if metadata.codec_name not in codecs:
        raise MediaProbeError("Frozen image codec does not match declared type")
    return metadata


def _image_input_args(input_format: str | None = None) -> list[str]:
    args: list[str] = []
    if input_format is not None:
        args.extend(["-f", input_format])
    args.extend(["-protocol_whitelist", "file", "-format_whitelist", IMAGE_FORMATS])
    return args


def _png_has_transparency(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MediaProbeError("Invalid PNG signature")
    offset = 8
    color_type = None
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        start = offset + 8
        end = start + length
        if length < 0 or end + 4 > len(data):
            raise MediaProbeError("Invalid PNG chunks")
        payload = data[start:end]
        if kind == b"IHDR":
            if length != 13:
                raise MediaProbeError("Invalid PNG header")
            color_type = payload[9]
            if color_type in {4, 6}:
                return True
        elif kind == b"tRNS":
            return True
        elif kind == b"IEND":
            break
        offset = end + 4
    if color_type is None:
        raise MediaProbeError("Invalid PNG header")
    return False


def _positive_number(value) -> float:
    if type(value) not in (str, int, float):
        raise MediaProbeError("Media duration is unavailable")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise MediaProbeError("Media duration is unavailable") from exc
    if not math.isfinite(number) or number <= 0:
        raise MediaProbeError("Media duration is unavailable")
    return number


def _frame_rate(value) -> float | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        fps = float(Fraction(value))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return fps if math.isfinite(fps) and fps > 0 else None


def _pixel_format_has_alpha(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.lower()
    return (
        normalized in {"rgba", "bgra", "argb", "abgr", "ya8", "pal8"}
        or normalized.startswith(("yuva", "gbrap"))
        or normalized.endswith("a")
    )
