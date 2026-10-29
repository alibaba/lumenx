"""Atelier managed upload validation and private publication."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from typing import Callable, ContextManager, Iterable, Literal

from ...utils.json_storage import DirectoryWriterLock, StorageError
from ...utils.media_versions import MediaVersionError, store_upload_stream
from ...utils.media_probe import AV_FORMATS, MediaProbeError, probe_media_file
from ...utils.system_check import get_ffmpeg_path

UploadKind = Literal["image", "audio", "video"]

DEFAULT_MAX_BYTES = {
    "image": 25 * 1024 * 1024,
    "audio": 100 * 1024 * 1024,
    "video": 250 * 1024 * 1024,
}
_EXTENSIONS: dict[UploadKind, set[str]] = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "video": {".mp4", ".mov", ".webm", ".mkv"},
}
_FFMPEG_PROTOCOL_WHITELIST = "file"
_FFMPEG_FORMAT_WHITELIST: dict[UploadKind, str] = {
    "image": "image2,png_pipe,jpeg_pipe,webp_pipe,gif",
    **AV_FORMATS,
}


class AtelierMediaUploadError(ValueError):
    """An upload cannot be accepted as managed Atelier media."""


@dataclass(frozen=True)
class ProbedMedia:
    kind: UploadKind
    duration: float | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class ManagedUpload:
    url: str
    media_kind: UploadKind
    size_bytes: int
    sha256: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    poster_url: str | None = None
    poster_size_bytes: int | None = None
    poster_sha256: str | None = None


@dataclass(frozen=True)
class _StagedUploadIdentity:
    size_bytes: int
    sha256: str


def save_managed_upload(
    chunks: Iterable[bytes], *, filename: str, content_type: str | None,
    data_dir: str | Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]],
    upload_id_factory: Callable[[], str] | None = None,
    max_bytes_by_kind: dict[str, int] | None = None,
) -> ManagedUpload:
    root = Path(data_dir).expanduser().resolve()
    primary_published = False
    try:
        if not isinstance(writer_lock, DirectoryWriterLock) or writer_lock.data_dir != root:
            raise AtelierMediaUploadError("Writer lease belongs to another output_root")
        kind, extension = _classify_upload(filename, content_type)
        limits = {**DEFAULT_MAX_BYTES, **(max_bytes_by_kind or {})}
        max_bytes = _positive_max_bytes(limits[kind])
        upload_id = _safe_upload_id(upload_id_factory() if upload_id_factory else uuid.uuid4().hex)
        with tempfile.TemporaryDirectory(prefix="lumenx-atelier-upload-") as staging_dir:
            stage_path = Path(staging_dir) / f"payload{extension}"
            staged = _write_private_stage(stage_path, chunks, max_bytes=max_bytes)
            _assert_runtime_available(writer_lock, storage_guard)
            metadata = _validate_staged_media(stage_path, kind)
            _assert_runtime_available(writer_lock, storage_guard)
            output_ref = f"uploads/{upload_id}{extension}"

            def require_validated_bytes(relative: str, digest: str, size: int) -> None:
                if relative != output_ref or digest != staged.sha256 or size != staged.size_bytes:
                    raise AtelierMediaUploadError("Upload bytes changed after validation")

            relative, digest, published_size = store_upload_stream(
                _verified_file_chunks(
                    stage_path,
                    expected_sha256=staged.sha256,
                    expected_size=staged.size_bytes,
                    max_bytes=max_bytes,
                ),
                output_ref=output_ref,
                output_root=root,
                writer_lock=writer_lock,
                storage_guard=storage_guard,
                max_bytes=max_bytes,
                on_prepared=require_validated_bytes,
            )
            if published_size != staged.size_bytes or digest != staged.sha256:
                raise StorageError("Published upload identity differs from validated bytes", commit_uncertain=True)
            primary_published = True
            poster_url = None
            poster_size = None
            poster_sha256 = None
            if kind == "video":
                poster = _publish_video_poster(
                    stage_path,
                    upload_id=upload_id,
                    root=root,
                    writer_lock=writer_lock,
                    storage_guard=storage_guard,
                )
                if poster is not None:
                    poster_url, poster_sha256, poster_size = poster
            return ManagedUpload(
                url=relative,
                media_kind=kind,
                size_bytes=published_size,
                sha256=digest,
                width=metadata.width,
                height=metadata.height,
                duration=metadata.duration,
                poster_url=poster_url,
                poster_size_bytes=poster_size,
                poster_sha256=poster_sha256,
            )
    except StorageError as exc:
        if primary_published and not exc.commit_uncertain:
            raise StorageError("Managed upload is visible; completion uncertain", commit_uncertain=True) from exc
        raise
    except (OSError, MediaVersionError, AtelierMediaUploadError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        if primary_published:
            raise StorageError("Managed upload is visible; completion uncertain", commit_uncertain=True) from exc
        if isinstance(exc, AtelierMediaUploadError):
            raise
        raise AtelierMediaUploadError(str(exc)) from exc


def discard_managed_upload(
    managed: ManagedUpload, *, data_dir: str | Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]],
) -> None:
    """Remove this request's verified upload when no project commit references it."""
    try:
        root = Path(data_dir).expanduser().resolve()
        _discard_upload_file(
            managed.url,
            root=root,
            writer_lock=writer_lock,
            storage_guard=storage_guard,
            expected_sha256=managed.sha256,
            expected_size=managed.size_bytes,
        )
        poster_fields = (managed.poster_url, managed.poster_size_bytes, managed.poster_sha256)
        if any(value is not None for value in poster_fields):
            if not all(value is not None for value in poster_fields):
                raise StorageError("Managed upload poster cleanup proof is incomplete", commit_uncertain=True)
            _discard_upload_file(
                managed.poster_url or "",
                root=root,
                writer_lock=writer_lock,
                storage_guard=storage_guard,
                expected_sha256=managed.poster_sha256,
                expected_size=managed.poster_size_bytes,
            )
    except StorageError as exc:
        if exc.commit_uncertain:
            raise
        raise StorageError("Managed upload cleanup failed; completion uncertain", commit_uncertain=True) from exc
    except Exception as exc:
        raise StorageError("Managed upload cleanup failed; completion uncertain", commit_uncertain=True) from exc


def _classify_upload(filename: str, content_type: str | None) -> tuple[UploadKind, str]:
    extension = Path(filename or "").suffix.lower()
    kind = next((name for name, extensions in _EXTENSIONS.items() if extension in extensions), None)
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if (
        kind is None
        or (normalized_content_type and normalized_content_type != "application/octet-stream" and not normalized_content_type.startswith(f"{kind}/"))
    ):
        raise AtelierMediaUploadError("Unsupported media file type")
    return kind, extension


def _safe_upload_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise AtelierMediaUploadError("Invalid upload id")
    return value


def _positive_max_bytes(value) -> int:
    if type(value) is not int or value <= 0:
        raise AtelierMediaUploadError("Upload size limit must be a positive integer")
    return value


def _assert_runtime_available(
    writer_lock: DirectoryWriterLock, storage_guard: Callable[[], ContextManager[None]],
) -> None:
    with storage_guard():
        writer_lock.assert_owned()


def _write_private_stage(path: Path, chunks: Iterable[bytes], *, max_bytes: int) -> _StagedUploadIdentity:
    size = 0
    digest = hashlib.sha256()
    with path.open("xb") as target:
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise AtelierMediaUploadError("Media stream must yield bytes")
            size += len(chunk)
            if size > max_bytes:
                raise AtelierMediaUploadError("Media upload exceeds size limit")
            target.write(chunk)
            digest.update(chunk)
        target.flush()
        os.fsync(target.fileno())
    if size == 0:
        raise AtelierMediaUploadError("Empty media upload")
    return _StagedUploadIdentity(size_bytes=size, sha256=digest.hexdigest())


def _validate_staged_media(path: Path, kind: UploadKind) -> ProbedMedia:
    if kind == "image":
        return _probe_image(path)
    return _probe_media(path, kind)


def _probe_image(path: Path) -> ProbedMedia:
    ffmpeg, probe = _ffmpeg_tools()
    try:
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-nostdin",
                * _ffmpeg_input_limits("image"),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=20,
        )
        result = subprocess.run(
            [
                probe,
                * _ffprobe_input_limits("image"),
                "-v",
                "error",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=20,
        )
        info = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise AtelierMediaUploadError("Invalid image upload") from exc
    stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), None)
    codec = (stream or {}).get("codec_name")
    if codec not in {"png", "mjpeg", "webp", "gif"}:
        raise AtelierMediaUploadError("Invalid image upload")
    width = _positive_int(stream.get("width"), "image width")
    height = _positive_int(stream.get("height"), "image height")
    return ProbedMedia(kind="image", width=width, height=height)


def _probe_media(path: Path, kind: UploadKind) -> ProbedMedia:
    _ffmpeg, probe = _ffmpeg_tools()
    try:
        metadata = probe_media_file(path, kind, probe_executable=probe)
    except MediaProbeError as exc:
        raise AtelierMediaUploadError("Invalid media upload") from exc
    return ProbedMedia(
        kind=metadata.kind, duration=metadata.duration,
        width=metadata.width, height=metadata.height,
    )


def _positive_int(value, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise AtelierMediaUploadError(f"Invalid {label}")
    return value


def _publish_video_poster(
    video_path: Path, *, upload_id: str, root: Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]],
) -> tuple[str, str, int] | None:
    poster_path = video_path.with_name("poster.jpg")
    if not _extract_video_poster(video_path, poster_path):
        _assert_runtime_available(writer_lock, storage_guard)
        return None
    try:
        relative, digest, size = store_upload_stream(
            _file_chunks(poster_path),
            output_ref=f"uploads/{upload_id}_poster.jpg",
            output_root=root,
            writer_lock=writer_lock,
            storage_guard=storage_guard,
            max_bytes=DEFAULT_MAX_BYTES["image"],
        )
        return relative, digest, size
    except StorageError:
        raise
    except MediaVersionError:
        return None


def _extract_video_poster(video_path: Path, destination: Path) -> bool:
    try:
        ffmpeg, _probe = _ffmpeg_tools()
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                * _ffmpeg_input_limits("video"),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                "-y",
                str(destination),
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=20,
        )
    except subprocess.SubprocessError:
        with suppress(OSError):
            destination.unlink()
        return False
    return destination.is_file() and destination.stat().st_size > 0


def _ffmpeg_tools() -> tuple[str, str]:
    ffmpeg = get_ffmpeg_path() or shutil.which("ffmpeg") or "ffmpeg"
    return ffmpeg, str(Path(ffmpeg).with_name("ffprobe"))


def _ffmpeg_input_limits(kind: UploadKind) -> list[str]:
    return [
        "-protocol_whitelist",
        _FFMPEG_PROTOCOL_WHITELIST,
        "-format_whitelist",
        _FFMPEG_FORMAT_WHITELIST[kind],
    ]


def _ffprobe_input_limits(kind: UploadKind) -> list[str]:
    return _ffmpeg_input_limits(kind)


def _verified_file_chunks(path: Path, *, expected_sha256: str, expected_size: int, max_bytes: int):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise AtelierMediaUploadError("Upload bytes changed after validation")
            digest.update(chunk)
            yield chunk
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise AtelierMediaUploadError("Upload bytes changed after validation")


def _file_chunks(path: Path):
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            yield chunk


def _discard_upload_file(
    ref: str, *, root: Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]], expected_sha256: str | None,
    expected_size: int | None,
) -> None:
    parts = ref.split("/")
    if len(parts) != 2 or parts[0] != "uploads" or not parts[1] or "/" in parts[1] or "\0" in parts[1]:
        raise AtelierMediaUploadError("Invalid managed upload reference")
    if not isinstance(writer_lock, DirectoryWriterLock) or writer_lock.data_dir != root:
        raise AtelierMediaUploadError("Writer lease belongs to another output_root")
    with storage_guard():
        writer_lock.assert_owned()
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            writer_lock.assert_owned_for(root_fd)
            uploads_fd = os.open("uploads", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                fd = os.open(parts[1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=uploads_fd)
                try:
                    state = os.fstat(fd)
                    if not stat.S_ISREG(state.st_mode):
                        raise AtelierMediaUploadError("Managed upload file is not a regular file")
                    if expected_size is not None and state.st_size != expected_size:
                        raise AtelierMediaUploadError("Managed upload size differs from expected proof")
                    if expected_sha256 is not None:
                        digest = hashlib.sha256()
                        size = 0
                        while data := os.read(fd, 1024 * 1024):
                            size += len(data)
                            digest.update(data)
                        if size != expected_size or digest.hexdigest() != expected_sha256:
                            raise AtelierMediaUploadError("Managed upload content differs from expected proof")
                finally:
                    os.close(fd)
                os.unlink(parts[1], dir_fd=uploads_fd)
                os.fsync(uploads_fd)
            finally:
                os.close(uploads_fd)
        finally:
            os.close(root_fd)
