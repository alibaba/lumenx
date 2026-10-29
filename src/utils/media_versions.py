"""Content-addressed managed media for macOS/Linux, without network transport.

Callers hold their runtime lifecycle lock throughout freeze_media and reuse the
runtime's directory writer lease. This module does not acquire a second lease.
The immutable record identifies bytes, not verified media encoding/dimensions,
authorization, or a provider-ready URL. Read verified bytes before preparing a
transport; verifying a path and then reopening it would reintroduce TOCTOU.
"""

import base64
import binascii
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from typing import Callable, ContextManager, Iterable, Literal

from .json_storage import DirectoryWriterLock, StorageConflictError, StorageError
from .media_refs import LOCAL_MEDIA_PREFIXES


_PLATFORM = sys.platform
_CHUNK_SIZE = 1024 * 1024
_SOURCE_DIRS = {p.rstrip("/") for p in LOCAL_MEDIA_PREFIXES} - {"output", "outputs"}
_SOURCE_DIRS.add("video_inputs")
_ROLES = {"first_frame", "last_frame", "reference_image", "reference_video", "reference_audio"}
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
_MIME_BY_SUFFIX = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".wav": "audio/wav",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
}

ManagedMediaKind = Literal["video", "image", "audio"]
_IMAGE_OUTPUT_EXTENSIONS = {".png"}
_AUDIO_OUTPUT_EXTENSIONS = {".mp3"}
_SAFE_MANAGED_OUTPUT_FILE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


class MediaVersionError(ValueError):
    """A reference or recorded content version cannot be safely consumed."""


@contextmanager
def verified_private_media_file(
    path: str | Path, *, scratch_root: str | Path,
    expected_sha256: str, expected_size: int, max_bytes: int,
):
    """Yield verified private bytes on the same descriptor used for publication."""
    path, scratch = Path(path), Path(scratch_root)
    if path.is_symlink() or not path.resolve().is_relative_to(scratch.resolve()):
        raise MediaVersionError("Private result identity is invalid")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise MediaVersionError("Private result limit is invalid")
    if type(expected_size) is not int or not 0 < expected_size <= max_bytes:
        raise MediaVersionError("Private result size is invalid")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
            raise MediaVersionError("Private result is not the expected regular file")
        digest, count = hashlib.sha256(), 0
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            count += len(chunk)
            if count > max_bytes:
                raise MediaVersionError("Private result exceeds size limit")
            digest.update(chunk)
        if count != expected_size or digest.hexdigest() != expected_sha256:
            raise MediaVersionError("Private result proof does not match bytes")
        source.seek(0)
        yield source


@dataclass(frozen=True)
class MediaVersion:
    sha256: str
    size_bytes: int
    role: str
    ordinal: int
    declared_media_type: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        _validate_version(self)


def _role(role: str, ordinal: int) -> None:
    if not isinstance(role, str) or role not in _ROLES:
        raise MediaVersionError("Unknown media role")
    if type(ordinal) is not int or ordinal < 0:
        raise MediaVersionError("ordinal must be a nonnegative integer within its role")
    if role in {"first_frame", "last_frame"} and ordinal != 0:
        raise MediaVersionError("A frame role has only ordinal zero")


def _positive_integer(value: int, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise MediaVersionError(f"{label} must be an explicit positive integer")


def _validate_version(version: MediaVersion) -> None:
    if type(version.schema_version) is not int or version.schema_version != 1:
        raise MediaVersionError("Unknown media version schema")
    if not isinstance(version.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", version.sha256) is None:
        raise MediaVersionError("Invalid SHA-256 media identity")
    _positive_integer(version.size_bytes, "size_bytes")
    _role(version.role, version.ordinal)
    if version.declared_media_type is not None and (
        not isinstance(version.declared_media_type, str)
        or re.fullmatch(r"(?:image|audio|video)/[a-z0-9.+-]+", version.declared_media_type) is None
    ):
        raise MediaVersionError("Invalid declared media type")


def _platform() -> None:
    if _PLATFORM not in {"darwin", "linux"}:
        raise MediaVersionError("Secure media versions currently require macOS or Linux")


def _root_path(output_root: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(output_root)))


def _component(name: str) -> None:
    if not name or name in {".", ".."} or any(c in name for c in ("/", "\\", "\0")):
        raise MediaVersionError("Invalid managed media path component")


@contextmanager
def _opened(path, flags, *, parent_fd=None, mode=0o600):
    try:
        fd = os.open(path, flags, mode, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise MediaVersionError("Media path is missing or contains a symlink/non-directory") from exc
        raise
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _directory(parent_fd: int, name: str, *, create=False):
    _component(name)
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    with _opened(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, parent_fd=parent_fd) as fd:
        if create:
            # Sync even if another thread just created this entry but has not
            # synced it yet. Never assume FileExistsError proves durability.
            os.fsync(fd)
            os.fsync(parent_fd)
        yield fd


def _source_parts(ref: str, root: Path) -> list[str]:
    raw = ref
    if os.path.isabs(raw):
        prefix = str(root) + os.sep
        if not raw.startswith(prefix):
            raise MediaVersionError("Source is outside the selected media root")
        raw = raw[len(prefix):]
    for prefix in ("output/", "outputs/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    parts = raw.split("/")
    for part in parts:
        _component(part)
    if len(parts) < 2 or parts[0] not in _SOURCE_DIRS:
        raise MediaVersionError("Only managed local media and supported image data URIs can be frozen")
    return parts


def _inline_image(ref: str, max_bytes: int, role: str) -> tuple[bytes, str]:
    header, separator, encoded = ref.partition(",")
    if not separator or not header.endswith(";base64"):
        raise MediaVersionError("Expected a base64 image data URI")
    mime = header[5:-7]
    if mime not in _IMAGE_MIMES or role not in {"first_frame", "last_frame", "reference_image"}:
        raise MediaVersionError("Unsupported data URI media type or role")
    if len(encoded) > 4 * ((max_bytes + 2) // 3):
        raise MediaVersionError("Inline media exceeds max_bytes")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaVersionError("Malformed base64 media") from exc
    if not data or len(data) > max_bytes:
        raise MediaVersionError("Inline media is empty or exceeds max_bytes")
    return data, mime


def _file_state(fd: int):
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode):
        raise MediaVersionError("Media must be a regular file")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _chunks(fd: int):
    while data := os.read(fd, _CHUNK_SIZE):
        yield data


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Incomplete media write")
        view = view[written:]


def _output_video_parts(ref: str, root: Path) -> list[str]:
    parts = _source_parts(ref, root)
    if parts[0] not in {"video", "playground"} or Path(parts[-1]).suffix.lower() not in {
        ".mp4", ".mov", ".webm",
    }:
        raise MediaVersionError("Download destination must be a managed video file")
    return parts


def _relative_output_parts(ref: str) -> list[str]:
    if not isinstance(ref, str) or not ref or os.path.isabs(ref):
        raise MediaVersionError("Expected a relative managed media reference")
    parts = ref.split("/")
    for part in parts:
        _component(part)
    return parts


def _safe_output_filename(name: str, allowed_extensions: set[str], label: str) -> None:
    if _SAFE_MANAGED_OUTPUT_FILE.fullmatch(name) is None:
        raise MediaVersionError(f"{label} destination must use a safe file name")
    if Path(name).suffix.lower() not in allowed_extensions:
        raise MediaVersionError(f"{label} destination has an unsupported extension")


def _output_image_parts(ref: str) -> list[str]:
    parts = _relative_output_parts(ref)
    if len(parts) != 3 or parts[:2] not in (["assets", "atelier"], ["assets", "studio"], ["playground", "images"]):
        raise MediaVersionError("Image destination must be a managed product image file")
    if parts[:2] == ["assets", "studio"] and not parts[-1].endswith(".png"):
        raise MediaVersionError("Studio image destination must be a PNG file")
    _safe_output_filename(parts[-1], _IMAGE_OUTPUT_EXTENSIONS, "Image")
    return parts


def _output_audio_parts(ref: str) -> list[str]:
    parts = _relative_output_parts(ref)
    if len(parts) != 3 or parts[:2] != ["audio", "atelier"]:
        raise MediaVersionError("Audio destination must be a managed Atelier audio file")
    _safe_output_filename(parts[-1], _AUDIO_OUTPUT_EXTENSIONS, "Audio")
    return parts


def _managed_output_parts(ref: str, root: Path, media_kind: ManagedMediaKind) -> list[str]:
    if media_kind == "video":
        return _output_video_parts(ref, root)
    if media_kind == "image":
        return _output_image_parts(ref)
    if media_kind == "audio":
        return _output_audio_parts(ref)
    raise MediaVersionError("Unsupported managed media kind")


def managed_media_file_exists(
    output_ref: str, *, output_root: str | Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]], media_kind: ManagedMediaKind = "video",
) -> bool:
    root = _root_path(output_root)
    if not callable(storage_guard):
        raise MediaVersionError("A runtime storage guard is required")
    if not isinstance(writer_lock, DirectoryWriterLock) or root != writer_lock.data_dir:
        raise MediaVersionError("Writer lease belongs to another output_root")
    parts = _managed_output_parts(output_ref, root, media_kind)
    with storage_guard():
        writer_lock.assert_owned()
        with ExitStack() as stack:
            current = stack.enter_context(_opened(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            writer_lock.assert_owned_for(current)
            for component in parts[:-1]:
                _component(component)
                try:
                    fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                except FileNotFoundError:
                    return False
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise MediaVersionError("Media path is missing or contains a symlink/non-directory") from exc
                    raise
                stack.callback(os.close, fd)
                current = fd
            try:
                value = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if not stat.S_ISREG(value.st_mode):
                raise MediaVersionError("Managed destination must be a regular file")
            return True


def verify_managed_media_file(
    output_ref: str, *, output_root: str | Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]], expected_sha256: str,
    expected_size: int, max_bytes: int, media_kind: ManagedMediaKind = "video",
) -> tuple[str, str, int]:
    verified = _managed_media_file_operation(
        output_ref, output_root=output_root, writer_lock=writer_lock,
        storage_guard=storage_guard, expected_sha256=expected_sha256,
        expected_size=expected_size, max_bytes=max_bytes, media_kind=media_kind,
    )
    assert isinstance(verified, tuple)
    return verified


def delete_verified_managed_media_file(
    output_ref: str, *, output_root: str | Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]], expected_sha256: str,
    expected_size: int, max_bytes: int,
) -> Literal["deleted", "already_missing"]:
    """Delete only the proven file under the caller's live lifecycle guard.

    The caller must first persist deletion intent and check live references in
    the same guard. An exception after unlink is an unknown durability outcome;
    the persisted intent allows a later explicit retry to observe absence.
    """
    if not _is_deletable_video_output_ref(output_ref):
        raise MediaVersionError("Deletion requires a canonical managed export path")
    result = _managed_media_file_operation(
        output_ref, output_root=output_root, writer_lock=writer_lock,
        storage_guard=storage_guard, expected_sha256=expected_sha256,
        expected_size=expected_size, max_bytes=max_bytes, delete=True,
    )
    assert result in {"deleted", "already_missing"}
    return result


def _is_deletable_video_output_ref(output_ref: str) -> bool:
    if not isinstance(output_ref, str):
        return False
    parts = output_ref.split("/")
    if (
        len(parts) == 2
        and parts[0] == "video"
        and re.fullmatch(r"[A-Za-z0-9_-]+\.mp4", parts[1]) is not None
    ):
        return True
    if len(parts) < 4 or parts[0] != "video" or parts[1] != "imported":
        return False
    safe_component = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
    if safe_component.fullmatch(parts[2]) is None:
        return False
    if Path(parts[-1]).suffix.lower() != ".mp4":
        return False
    return all(safe_component.fullmatch(part) is not None for part in parts[3:])


def _managed_media_file_operation(
    output_ref: str, *, output_root: str | Path, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]], expected_sha256: str,
    expected_size: int, max_bytes: int, delete: bool = False,
    media_kind: ManagedMediaKind = "video",
) -> tuple[str, str, int] | Literal["deleted", "already_missing"]:
    root = _root_path(output_root)
    _positive_integer(max_bytes, "max_bytes")
    _positive_integer(expected_size, "expected_size")
    if expected_size > max_bytes:
        raise MediaVersionError("Managed media exceeds max_bytes")
    if not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise MediaVersionError("Invalid expected media hash")
    if not callable(storage_guard):
        raise MediaVersionError("A runtime storage guard is required")
    if not isinstance(writer_lock, DirectoryWriterLock) or root != writer_lock.data_dir:
        raise MediaVersionError("Writer lease belongs to another output_root")
    parts = _managed_output_parts(output_ref, root, media_kind)

    def identity_from_fd(fd: int) -> tuple[int, int]:
        value = os.fstat(fd)
        return value.st_dev, value.st_ino

    def identity_from_stat(value) -> tuple[int, int]:
        return value.st_dev, value.st_ino

    with storage_guard():
        writer_lock.assert_owned()
        with ExitStack() as stack:
            root_fd = stack.enter_context(_opened(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            writer_lock.assert_owned_for(root_fd)
            root_identity = identity_from_fd(root_fd)
            parent_fd = root_fd
            parent_identities: list[tuple[str, tuple[int, int]]] = []

            @contextmanager
            def current_parent():
                with ExitStack() as check_stack:
                    checked = check_stack.enter_context(_opened(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
                    if identity_from_fd(checked) != root_identity:
                        raise StorageConflictError("Managed output root changed during verification")
                    for component, original_identity in parent_identities:
                        checked = check_stack.enter_context(_directory(checked, component))
                        if identity_from_fd(checked) != original_identity:
                            raise StorageConflictError("Managed output directory changed during verification")
                    yield checked

            def sync_absence(name: str) -> None:
                with current_parent() as checked:
                    writer_lock.assert_owned_current_path_for(root_fd)
                    try:
                        os.stat(name, dir_fd=checked, follow_symlinks=False)
                    except FileNotFoundError:
                        os.fsync(checked)
                    else:
                        raise StorageConflictError("Managed media path appeared during verification")

            for component in parts[:-1]:
                try:
                    parent_fd = stack.enter_context(_directory(parent_fd, component))
                except MediaVersionError as exc:
                    if not delete or not isinstance(exc.__cause__, FileNotFoundError):
                        raise
                    sync_absence(component)
                    return "already_missing"
                parent_identities.append((component, identity_from_fd(parent_fd)))

            try:
                fd = stack.enter_context(_opened(
                    parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, parent_fd=parent_fd,
                ))
            except MediaVersionError as exc:
                if not delete or not isinstance(exc.__cause__, FileNotFoundError):
                    raise
                sync_absence(parts[-1])
                return "already_missing"
            before_state = os.fstat(fd)
            if not stat.S_ISREG(before_state.st_mode) or before_state.st_size != expected_size:
                raise MediaVersionError("Managed media size differs from expected proof")
            fd_identity = identity_from_stat(before_state)
            current_path = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(current_path.st_mode) or identity_from_stat(current_path) != fd_identity:
                raise StorageConflictError("Managed media path changed during verification")

            digest = hashlib.sha256()
            size = 0
            for data in _chunks(fd):
                size += len(data)
                if size > max_bytes:
                    raise MediaVersionError("Managed media exceeds max_bytes")
                digest.update(data)

            after_state = os.fstat(fd)
            if (
                not stat.S_ISREG(after_state.st_mode)
                or identity_from_stat(after_state) != fd_identity
                or after_state.st_size != expected_size
                or after_state.st_mtime_ns != before_state.st_mtime_ns
                or after_state.st_ctime_ns != before_state.st_ctime_ns
            ):
                raise StorageConflictError("Managed media file changed during verification")

            with current_parent() as checked:
                current_path = os.stat(parts[-1], dir_fd=checked, follow_symlinks=False)
            if not stat.S_ISREG(current_path.st_mode) or identity_from_stat(current_path) != fd_identity:
                raise StorageConflictError("Managed media path changed during verification")
            if size != expected_size or digest.hexdigest() != expected_sha256:
                raise MediaVersionError("Managed media content differs from expected proof")
            writer_lock.assert_owned_current_path_for(root_fd)
            if delete:
                # Stay inside the same guard and verified descriptors through
                # unlink; never return a path for a later unguarded deletion.
                os.unlink(parts[-1], dir_fd=parent_fd)
                os.fsync(parent_fd)
                return "deleted"
            return "/".join(parts), digest.hexdigest(), size


def _output_upload_parts(ref: str, root: Path) -> list[str]:
    if not isinstance(ref, str) or not ref or os.path.isabs(ref):
        raise MediaVersionError("Expected a relative managed upload reference")
    parts = ref.split("/")
    for part in parts:
        _component(part)
    if len(parts) != 2 or parts[0] != "uploads":
        raise MediaVersionError("Upload destination must be a managed uploads file")
    if not Path(parts[-1]).suffix.lower():
        raise MediaVersionError("Upload destination must include a file extension")
    return parts


def store_media_stream(
    chunks: Iterable[bytes], *, output_ref: str, output_root: str | Path,
    writer_lock: DirectoryWriterLock, storage_guard: Callable[[], ContextManager[None]],
    max_bytes: int, on_prepared: Callable[[str, str, int], None] | None = None,
    publish_policy: str = "no_overwrite", media_kind: ManagedMediaKind = "video",
) -> tuple[str, str, int]:
    """Publish complete bytes; return relative path, SHA-256, and actual size.

    Iteration may perform network I/O and always runs outside storage_guard.
    The caller's guard must use the same lifecycle lock as its close operation;
    a callable alone does not prove mutual exclusion. We independently verify
    the explicit lease and opened directory identities for every write.

    By default this transfer slice permits video destinations under video/ or
    playground/. Explicit image publication uses the strict product image
    namespaces; audio publication remains limited to audio/atelier. A managed
    prefix alone never permits replacing metadata.
    These are ordinary bytes, not an input-role MediaVersion or encoding proof.
    Failure before replacement preserves the old destination.
    If ownership is lost, leave the unique, non-authoritative stage untouched.
    """
    root = _root_path(output_root)
    return _store_stream_to_parts(
        chunks,
        parts=_managed_output_parts(output_ref, root, media_kind),
        output_root=root,
        writer_lock=writer_lock,
        storage_guard=storage_guard,
        max_bytes=max_bytes,
        on_prepared=on_prepared,
        publish_policy=publish_policy,
    )


def store_upload_stream(
    chunks: Iterable[bytes], *, output_ref: str, output_root: str | Path,
    writer_lock: DirectoryWriterLock, storage_guard: Callable[[], ContextManager[None]],
    max_bytes: int, on_prepared: Callable[[str, str, int], None] | None = None,
) -> tuple[str, str, int]:
    """Publish verified upload bytes under uploads/ without overwriting."""
    root = _root_path(output_root)
    return _store_stream_to_parts(
        chunks,
        parts=_output_upload_parts(output_ref, root),
        output_root=root,
        writer_lock=writer_lock,
        storage_guard=storage_guard,
        max_bytes=max_bytes,
        on_prepared=on_prepared,
        publish_policy="no_overwrite",
    )


def _store_stream_to_parts(
    chunks: Iterable[bytes], *, parts: list[str], output_root: Path,
    writer_lock: DirectoryWriterLock, storage_guard: Callable[[], ContextManager[None]],
    max_bytes: int, on_prepared: Callable[[str, str, int], None] | None = None,
    publish_policy: str = "no_overwrite",
) -> tuple[str, str, int]:
    _platform()
    _positive_integer(max_bytes, "max_bytes")
    if not callable(storage_guard):
        raise MediaVersionError("A runtime storage guard is required")
    if on_prepared is not None and not callable(on_prepared):
        raise MediaVersionError("A synchronous prepared callback is required")
    if publish_policy not in {"no_overwrite", "replace"}:
        raise MediaVersionError("Unsupported media publish policy")
    root = output_root
    if not isinstance(writer_lock, DirectoryWriterLock) or root != writer_lock.data_dir:
        raise MediaVersionError("Writer lease belongs to another output_root")
    temporary = f".fetch-{uuid.uuid4().hex}.tmp"
    created = published = False

    def identity(fd):
        value = os.fstat(fd)
        return value.st_dev, value.st_ino

    def destination_is_regular(parent_fd):
        try:
            value = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(value.st_mode):
            raise MediaVersionError("Managed destination must be a regular file")

    def destination_exists(parent_fd):
        try:
            value = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(value.st_mode):
            raise MediaVersionError("Managed destination must be a regular file")
        return True

    try:
        with ExitStack() as stack:
            # Keep descriptors pinned across network waits, but never the lock.
            with storage_guard():
                writer_lock.assert_owned()
                root_fd = stack.enter_context(_opened(
                    root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                ))
                writer_lock.assert_owned_for(root_fd)
                parents = [root_fd]
                for component in parts[:-1]:
                    parents.append(stack.enter_context(_directory(parents[-1], component, create=True)))
                parent_fd = parents[-1]
                destination_is_regular(parent_fd)
                stage_fd = stack.enter_context(_opened(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    parent_fd=parent_fd,
                ))
                created = True
                stage_identity = identity(stage_fd)
                stage_state = _file_state(stage_fd)

            def verify_paths():
                writer_lock.assert_owned_for(root_fd)
                with ExitStack() as current:
                    checked = current.enter_context(_opened(
                        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    ))
                    if identity(checked) != identity(root_fd):
                        raise StorageConflictError("Managed output root changed during transfer")
                    for component, original_fd in zip(parts[:-1], parents[1:]):
                        checked = current.enter_context(_directory(checked, component))
                        if identity(checked) != identity(original_fd):
                            raise StorageConflictError("Managed output directory changed during transfer")
                writer_lock.assert_owned_for(root_fd)

            def verify_stage():
                value = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(value.st_mode) or (value.st_dev, value.st_ino) != stage_identity:
                    raise StorageConflictError("Managed temporary file changed during transfer")
                if _file_state(stage_fd) != stage_state:
                    raise StorageConflictError("Managed temporary content changed during transfer")

            def verify_stage_content(expected_digest, expected_size):
                with _opened(temporary, os.O_RDONLY | os.O_NOFOLLOW, parent_fd=parent_fd) as checked_fd:
                    value = os.fstat(checked_fd)
                    if (value.st_dev, value.st_ino) != stage_identity or value.st_size != expected_size:
                        raise StorageConflictError("Managed temporary file changed during transfer")
                    digest_check = hashlib.sha256()
                    size_check = 0
                    for data in _chunks(checked_fd):
                        size_check += len(data)
                        digest_check.update(data)
                    if size_check != expected_size or digest_check.hexdigest() != expected_digest:
                        raise StorageConflictError("Managed temporary content changed during transfer")

            try:
                digest = hashlib.sha256()
                size = 0
                for data in chunks:  # next() is deliberately outside the guard.
                    if not isinstance(data, bytes):
                        raise MediaVersionError("Media stream must yield bytes")
                    if size + len(data) > max_bytes:
                        raise MediaVersionError("Media stream exceeds max_bytes")
                    with storage_guard():
                        verify_paths()
                        verify_stage()
                        _write_all(stage_fd, data)
                        stage_state = _file_state(stage_fd)
                        digest.update(data)
                        size += len(data)
                if not size:
                    raise MediaVersionError("Media stream is empty")
                with storage_guard():
                    verify_paths()
                    verify_stage()
                    if publish_policy == "no_overwrite" and destination_exists(parent_fd):
                        raise MediaVersionError("Managed destination already exists")
                    destination_is_regular(parent_fd)
                    if _file_state(stage_fd)[2] != size:
                        raise StorageConflictError("Managed temporary size changed during transfer")
                    os.fsync(stage_fd)
                    verify_paths()
                    verify_stage()
                    if on_prepared is not None:
                        callback_result = on_prepared("/".join(parts), digest.hexdigest(), size)
                        if callback_result is not None:
                            raise MediaVersionError("Prepared callback must return None synchronously")
                    verify_paths()
                    verify_stage()
                    verify_stage_content(digest.hexdigest(), size)
                    if publish_policy == "no_overwrite" and destination_exists(parent_fd):
                        raise MediaVersionError("Managed destination already exists")
                    if publish_policy == "no_overwrite":
                        try:
                            os.link(
                                temporary, parts[-1],
                                src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False,
                            )
                        except FileExistsError as exc:
                            raise MediaVersionError("Managed destination already exists") from exc
                        published = True
                        os.unlink(temporary, dir_fd=parent_fd)
                        created = False
                    else:
                        os.replace(temporary, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                        published = True
                    final = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                    if (final.st_dev, final.st_ino) != stage_identity:
                        raise StorageConflictError("Published media identity changed")
                    os.fsync(parent_fd)
                    verify_paths()
                return "/".join(parts), digest.hexdigest(), size
            finally:
                if created and not published:
                    failure = sys.exc_info()[1]
                    try:
                        with storage_guard():
                            verify_paths()
                            verify_stage()
                            os.unlink(temporary, dir_fd=parent_fd)
                            os.fsync(parent_fd)
                    except (OSError, StorageError, MediaVersionError):
                        if failure is None:
                            raise
                        failure.add_note("Temporary media was retained because cleanup could not verify ownership or complete")
    except StorageError as exc:
        if published:
            raise StorageError("Media replacement is visible; durability uncertain", commit_uncertain=True) from exc
        raise
    except (OSError, MediaVersionError) as exc:
        if isinstance(exc, MediaVersionError) and not published:
            raise
        raise StorageError("Cannot publish downloaded media", commit_uncertain=published) from exc


def _read_blob(parent_fd: int, version: MediaVersion, max_bytes: int, *, sync=False) -> bytes:
    with _opened(version.sha256, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, parent_fd=parent_fd) as fd:
        if _file_state(fd)[2] != version.size_bytes:
            raise MediaVersionError("Stored media size differs from the fixed version")
        result = bytearray()
        digest = hashlib.sha256()
        for data in _chunks(fd):
            if len(result) + len(data) > min(max_bytes, version.size_bytes):
                raise MediaVersionError("Stored media exceeds its fixed size or read limit")
            digest.update(data)
            result.extend(data)
        if len(result) != version.size_bytes or digest.hexdigest() != version.sha256:
            raise MediaVersionError("Stored media content differs from the fixed version")
        if sync:
            # Durability applies to the inode actually published/reused, not
            # just the temporary descriptor that was synced before linking.
            os.fsync(fd)
        return bytes(result)


def freeze_media(
    ref: str, *, output_root: str | Path, writer_lock: DirectoryWriterLock,
    role: str, ordinal: int, max_bytes: int,
) -> MediaVersion:
    """Copy managed bytes into an immutable application-owned content version.

    No network/OSS/blob references are fetched. Remote support must import bytes
    before admission, never redownload an expired URL to impersonate a version.
    Source MIME is only a filename/data-URI declaration, not a decoding verdict.
    """
    _platform()
    _positive_integer(max_bytes, "max_bytes")
    _role(role, ordinal)
    if not isinstance(ref, str) or not ref:
        raise MediaVersionError("ref must be a nonempty string")
    root = _root_path(output_root)
    if not isinstance(writer_lock, DirectoryWriterLock) or root != writer_lock.data_dir:
        raise MediaVersionError("Writer lease belongs to another output_root")
    writer_lock.assert_owned()
    inline = _inline_image(ref, max_bytes, role) if ref.startswith("data:") else None
    parts = None if inline else _source_parts(ref, root)
    published = False
    try:
        with ExitStack() as stack:
            root_fd = stack.enter_context(_opened(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            writer_lock.assert_owned_for(root_fd)
            source_fd = None
            state = None
            if parts:
                parent = root_fd
                for component in parts[:-1]:
                    parent = stack.enter_context(_directory(parent, component))
                source_fd = stack.enter_context(_opened(
                    parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, parent_fd=parent,
                ))
                state = _file_state(source_fd)
                if not 0 < state[2] <= max_bytes:
                    raise MediaVersionError("Source media is empty or exceeds max_bytes")
            versions_fd = stack.enter_context(_directory(root_fd, "media_versions", create=True))
            temporary = f".stage-{uuid.uuid4().hex}"
            created = False
            try:
                with _opened(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             parent_fd=versions_fd) as stage_fd:
                    created = True
                    digest = hashlib.sha256()
                    size = 0
                    if inline:
                        source_chunks = (inline[0],)
                    else:
                        if source_fd is None or parts is None:
                            raise MediaVersionError("Source media is missing")
                        source_chunks = _chunks(source_fd)
                    for data in source_chunks:
                        size += len(data)
                        if size > max_bytes:
                            raise MediaVersionError("Source media exceeds max_bytes")
                        _write_all(stage_fd, data)
                        digest.update(data)
                    if source_fd is not None and _file_state(source_fd) != state:
                        raise MediaVersionError("Source media changed while being frozen")
                    os.fsync(stage_fd)
                if inline:
                    mime = inline[1]
                else:
                    if parts is None:
                        raise MediaVersionError("Source media is missing")
                    mime = _MIME_BY_SUFFIX.get(Path(parts[-1]).suffix.lower())
                version = MediaVersion(digest.hexdigest(), size, role, ordinal, mime)
                writer_lock.assert_owned_for(root_fd)
                algorithm_fd = stack.enter_context(_directory(versions_fd, "sha256", create=True))
                shard_fd = stack.enter_context(_directory(algorithm_fd, version.sha256[:2], create=True))
                try:
                    os.link(temporary, version.sha256, src_dir_fd=versions_fd,
                            dst_dir_fd=shard_fd, follow_symlinks=False)
                    published = True
                except FileExistsError:
                    pass
                # Verify the published entry even for our own new link: a
                # replaced temporary name must not create an accepted version.
                _read_blob(shard_fd, version, max_bytes, sync=True)
                os.fsync(shard_fd)
                os.unlink(temporary, dir_fd=versions_fd)
                created = False
                os.fsync(versions_fd)
                writer_lock.assert_owned_for(root_fd)
                return version
            finally:
                if created:
                    failure = sys.exc_info()[1]
                    try:
                        os.unlink(temporary, dir_fd=versions_fd)
                        os.fsync(versions_fd)
                    except OSError as cleanup:
                        if failure is None:
                            raise
                        failure.add_note(f"Media temporary cleanup failed: {cleanup}")
    except OSError as exc:
        raise StorageError("Cannot publish fixed media version", commit_uncertain=published) from exc


def read_verified_media(
    version: MediaVersion, *, output_root: str | Path, max_bytes: int,
) -> bytes:
    """Return bounded, verified bytes. Never fall back to any mutable source."""
    _platform()
    _positive_integer(max_bytes, "max_bytes")
    if not isinstance(version, MediaVersion):
        raise MediaVersionError("Expected a media version record")
    _validate_version(version)
    if version.size_bytes > max_bytes:
        raise MediaVersionError("Fixed media exceeds max_bytes")
    try:
        with ExitStack() as stack:
            parent = stack.enter_context(_opened(
                _root_path(output_root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            ))
            for component in ("media_versions", "sha256", version.sha256[:2]):
                parent = stack.enter_context(_directory(parent, component))
            return _read_blob(parent, version, max_bytes)
    except OSError as exc:
        raise StorageError("Cannot read fixed media version") from exc
