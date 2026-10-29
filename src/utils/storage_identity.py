"""Stable non-secret identity for an app-owned storage root.

The identity belongs to the data directory, not the host, URL, config, logs,
provider account, or credentials. Callers must already hold the directory's
writer lease while initializing or reading it.
"""

from __future__ import annotations

import json
import math
import os
import stat
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .json_storage import DirectoryWriterLock, StorageConflictError, StorageError, serialize_json

STORAGE_IDENTITY_FILENAME = "storage_identity.json"
STORAGE_IDENTITY_SCHEMA_VERSION = 1
_STORAGE_IDENTITY_MAX_BYTES = 4096

StorageIdentity = dict[str, int | str]


def _directory_fd_ops_supported() -> bool:
    required = (os.open, os.stat, os.unlink, os.link)
    return os.name != "nt" and all(func in os.supports_dir_fd for func in required)


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"JSON number exceeds finite range: {value}")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_storage_identity(value: object) -> StorageIdentity:
    if not isinstance(value, dict) or set(value) != {"schema_version", "storage_id"}:
        raise StorageError("Invalid storage identity schema")
    schema_version = value.get("schema_version")
    storage_id = value.get("storage_id")
    if type(schema_version) is not int or schema_version != STORAGE_IDENTITY_SCHEMA_VERSION:
        raise StorageError("Invalid storage identity schema version")
    if not isinstance(storage_id, str):
        raise StorageError("Invalid storage identity id")
    try:
        parsed = uuid.UUID(storage_id)
    except ValueError as exc:
        raise StorageError("Invalid storage identity id") from exc
    if parsed.version != 4 or str(parsed) != storage_id:
        raise StorageError("Invalid storage identity id")
    return {"schema_version": STORAGE_IDENTITY_SCHEMA_VERSION, "storage_id": storage_id}


def _validate_writer_root(data_dir: str | os.PathLike[str], writer_lock: DirectoryWriterLock) -> Path:
    if not isinstance(writer_lock, DirectoryWriterLock):
        raise StorageConflictError("Storage identity requires a directory writer lease")
    root = Path(data_dir).expanduser().resolve()
    if root != writer_lock.data_dir:
        raise StorageConflictError("Storage identity writer lease does not match data directory")
    return root


def _open_owned_root(data_dir: str | os.PathLike[str], writer_lock: DirectoryWriterLock) -> tuple[Path, int]:
    root = _validate_writer_root(data_dir, writer_lock)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise StorageError(f"Cannot open storage identity root {root}: {exc}") from exc
    try:
        writer_lock.assert_owned_current_path_for(root_fd)
    except BaseException:
        os.close(root_fd)
        raise
    return root, root_fd


def _read_bounded_json(fd: int) -> Any:
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = os.read(fd, min(1024, _STORAGE_IDENTITY_MAX_BYTES + 1 - total))
        except OSError as exc:
            raise StorageError(f"Cannot read storage identity: {exc}") from exc
        if not chunk:
            break
        total += len(chunk)
        if total > _STORAGE_IDENTITY_MAX_BYTES:
            raise StorageError("Storage identity exceeds maximum size")
        chunks.append(chunk)
    try:
        text = b"".join(chunks).decode("utf-8")
        return json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise StorageError(f"Cannot decode storage identity: {exc}") from exc


def _write_all(fd: int, encoded: bytes) -> None:
    total = 0
    while total < len(encoded):
        written = os.write(fd, encoded[total:])
        if written <= 0:
            raise StorageError("Cannot write storage identity")
        total += written


def _fsync_directory_path(root: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(root, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _identity_file_stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # These are observable change indicators, not proof against a writer able
    # to restore or forge metadata. The directory writer lease remains required.
    # Exclude atime: reading an otherwise unchanged identity may update it.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_verified_identity(
    fd: int, expected: os.stat_result, stat_entry: Callable[[], os.stat_result]
) -> StorageIdentity:
    try:
        opened = os.fstat(fd)
        stamp = _identity_file_stamp(opened)
        if not stat.S_ISREG(opened.st_mode) or stamp != _identity_file_stamp(expected):
            raise StorageError("Storage identity changed during read")
        identity = validate_storage_identity(_read_bounded_json(fd))
        finished = os.fstat(fd)
        current_entry = stat_entry()
        if (_identity_file_stamp(finished) != stamp
                or not stat.S_ISREG(current_entry.st_mode)
                or _identity_file_stamp(current_entry) != stamp):
            raise StorageError("Storage identity changed during read")
        return identity
    except OSError as exc:
        raise StorageError("Storage identity changed during read") from exc


def _read_identity_from_owned_root(root_fd: int) -> StorageIdentity | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        info = os.stat(STORAGE_IDENTITY_FILENAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StorageError(f"Cannot stat storage identity: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise StorageError("Storage identity is not a regular file")
    if info.st_size > _STORAGE_IDENTITY_MAX_BYTES:
        raise StorageError("Storage identity exceeds maximum size")
    try:
        fd = os.open(STORAGE_IDENTITY_FILENAME, flags, dir_fd=root_fd)
    except FileNotFoundError as exc:
        raise StorageError("Storage identity disappeared during read") from exc
    except OSError as exc:
        raise StorageError(f"Cannot open storage identity: {exc}") from exc
    try:
        return _read_verified_identity(
            fd, info,
            lambda: os.stat(STORAGE_IDENTITY_FILENAME, dir_fd=root_fd, follow_symlinks=False),
        )
    finally:
        os.close(fd)


def _read_identity_by_path(root: Path) -> StorageIdentity | None:
    path = root / STORAGE_IDENTITY_FILENAME
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StorageError(f"Cannot stat storage identity {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise StorageError("Storage identity is not a regular file")
    if info.st_size > _STORAGE_IDENTITY_MAX_BYTES:
        raise StorageError("Storage identity exceeds maximum size")
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise StorageError("Storage identity disappeared during read") from exc
    except OSError as exc:
        raise StorageError(f"Cannot open storage identity {path}: {exc}") from exc
    try:
        return _read_verified_identity(fd, info, path.lstat)
    finally:
        os.close(fd)


def _new_identity() -> StorageIdentity:
    return {
        "schema_version": STORAGE_IDENTITY_SCHEMA_VERSION,
        "storage_id": str(uuid.uuid4()),
    }


def _publish_new_identity_to_owned_root(root_fd: int, identity: StorageIdentity) -> None:
    encoded = serialize_json(identity).encode("utf-8")
    if len(encoded) > _STORAGE_IDENTITY_MAX_BYTES:
        raise StorageError("Storage identity exceeds maximum size")
    temporary = f".{STORAGE_IDENTITY_FILENAME}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    linked = False
    try:
        fd = os.open(temporary, flags, 0o600, dir_fd=root_fd)
        _write_all(fd, encoded)
        os.fsync(fd)
        os.close(fd)
        fd = None
        try:
            os.stat(STORAGE_IDENTITY_FILENAME, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StorageError(f"Cannot verify storage identity target: {exc}") from exc
        else:
            raise StorageConflictError("Storage identity appeared during initialization")
        os.link(
            temporary,
            STORAGE_IDENTITY_FILENAME,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
            follow_symlinks=False,
        )
        linked = True
        if os.name != "nt":
            os.fsync(root_fd)
    except OSError as exc:
        raise StorageError(
            f"Cannot initialize storage identity: {exc}", commit_uncertain=linked
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=root_fd)
            if os.name != "nt" and linked:
                os.fsync(root_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _write_new_identity_by_path(root: Path, identity: StorageIdentity) -> None:
    encoded = serialize_json(identity).encode("utf-8")
    if len(encoded) > _STORAGE_IDENTITY_MAX_BYTES:
        raise StorageError("Storage identity exceeds maximum size")
    path = root / STORAGE_IDENTITY_FILENAME
    temporary = root / f".{STORAGE_IDENTITY_FILENAME}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    linked = False
    try:
        fd = os.open(temporary, flags, 0o600)
        _write_all(fd, encoded)
        os.fsync(fd)
        os.close(fd)
        fd = None
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StorageError(f"Cannot verify storage identity target {path}: {exc}") from exc
        else:
            raise StorageConflictError("Storage identity appeared during initialization")
        os.link(temporary, path)
        linked = True
        _fsync_directory_path(root)
    except FileExistsError as exc:
        raise StorageConflictError("Storage identity appeared during initialization") from exc
    except OSError as exc:
        raise StorageError(
            f"Cannot initialize storage identity {path}: {exc}", commit_uncertain=linked
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temporary.unlink()
            if linked:
                _fsync_directory_path(root)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _load_storage_identity_by_path(
    data_dir: str | os.PathLike[str], *, writer_lock: DirectoryWriterLock
) -> StorageIdentity:
    root = _validate_writer_root(data_dir, writer_lock)
    writer_lock.assert_owned_path()
    value = _read_identity_by_path(root)
    if value is None:
        raise StorageError(f"Storage identity does not exist: {root / STORAGE_IDENTITY_FILENAME}")
    writer_lock.assert_owned_path()
    return dict(value)


def _initialize_storage_identity_by_path(
    data_dir: str | os.PathLike[str], *, writer_lock: DirectoryWriterLock
) -> StorageIdentity:
    root = _validate_writer_root(data_dir, writer_lock)
    writer_lock.assert_owned_path()
    existing = _read_identity_by_path(root)
    if existing is not None:
        writer_lock.assert_owned_path()
        return dict(existing)
    identity = _new_identity()
    writer_lock.assert_owned_path()
    _write_new_identity_by_path(root, identity)
    writer_lock.assert_owned_path()
    loaded = _read_identity_by_path(root)
    if loaded != identity:
        raise StorageError("Storage identity changed during initialization")
    writer_lock.assert_owned_path()
    return dict(identity)


def load_storage_identity(
    data_dir: str | os.PathLike[str], *, writer_lock: DirectoryWriterLock
) -> StorageIdentity:
    if not _directory_fd_ops_supported():
        return _load_storage_identity_by_path(data_dir, writer_lock=writer_lock)
    root, root_fd = _open_owned_root(data_dir, writer_lock)
    try:
        value = _read_identity_from_owned_root(root_fd)
        if value is None:
            raise StorageError(f"Storage identity does not exist: {root / STORAGE_IDENTITY_FILENAME}")
        writer_lock.assert_owned_current_path_for(root_fd)
        return dict(value)
    finally:
        os.close(root_fd)


def initialize_storage_identity(
    data_dir: str | os.PathLike[str], *, writer_lock: DirectoryWriterLock
) -> StorageIdentity:
    if not _directory_fd_ops_supported():
        return _initialize_storage_identity_by_path(data_dir, writer_lock=writer_lock)
    _root, root_fd = _open_owned_root(data_dir, writer_lock)
    try:
        existing = _read_identity_from_owned_root(root_fd)
        if existing is not None:
            writer_lock.assert_owned_current_path_for(root_fd)
            return dict(existing)
        identity = _new_identity()
        writer_lock.assert_owned_current_path_for(root_fd)
        _publish_new_identity_to_owned_root(root_fd, identity)
        writer_lock.assert_owned_current_path_for(root_fd)
        loaded = _read_identity_from_owned_root(root_fd)
        if loaded != identity:
            raise StorageError("Storage identity changed during initialization")
        writer_lock.assert_owned_current_path_for(root_fd)
        return dict(identity)
    finally:
        os.close(root_fd)
