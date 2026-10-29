"""Neutral package media primitives for product project copy flows.

This module owns JSON/ZIP/path/media staging mechanics only. Product modules
provide model projection, manifest schema, and error type mapping.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

from ..utils import media_versions as managed_media
from ..utils.json_storage import DirectoryWriterLock


class ProjectPackageMediaError(ValueError):
    """Raised when neutral package media validation fails."""


@dataclass(frozen=True)
class MediaRootPolicy:
    allowed_roots: frozenset[str]
    ref_prefixes: tuple[str, ...] = ()
    allowed_files: frozenset[str] = frozenset()


def _raise(error: type[Exception], message: str, cause: BaseException | None = None):
    exc = error(message)
    if cause is not None:
        raise exc from cause
    raise exc


@contextmanager
def owned_writer_lock(
    data_root: Path,
    writer_lock: DirectoryWriterLock | None,
    *,
    error: type[Exception] = ProjectPackageMediaError,
) -> Iterator[DirectoryWriterLock]:
    if writer_lock is not None:
        if not isinstance(writer_lock, DirectoryWriterLock) or writer_lock.data_dir != data_root:
            _raise(error, "Writer lease belongs to another project package root")
        writer_lock.assert_owned_path()
        yield writer_lock
        writer_lock.assert_owned_path()
        return
    lock = DirectoryWriterLock(data_root).acquire()
    try:
        lock.assert_owned_path()
        yield lock
        lock.assert_owned_path()
    finally:
        lock.close()


def canonical_json(value: Any, *, error: type[Exception] = ProjectPackageMediaError) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except ValueError as exc:
        _raise(error, "Project package metadata is not strict JSON", exc)


def loads_strict(data: bytes, label: str, *, error: type[Exception] = ProjectPackageMediaError) -> Any:
    def object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _raise(error, f"Duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def parse_constant(value: str) -> None:
        _raise(error, f"Project package metadata is not strict JSON: {value}")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=object_pairs_hook, parse_constant=parse_constant)
    except error:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _raise(error, "Project package metadata is not valid JSON", exc)


def normalize_media_ref(
    value: Any,
    *,
    policy: MediaRootPolicy,
    error: type[Exception] = ProjectPackageMediaError,
) -> str:
    if not isinstance(value, str) or not value.strip():
        _raise(error, "Media reference must be a non-empty string")
    ref = value.strip()
    lower = ref.lower()
    if "://" in lower or lower.startswith(("data:", "//")):
        _raise(error, f"Remote media cannot be packaged: {value}")
    for prefix in policy.ref_prefixes:
        if ref.startswith(prefix):
            ref = ref[len(prefix) :]
            break
    if "\x00" in ref or "\\" in ref:
        _raise(error, f"Unsafe media path: {value}")
    pure = PurePosixPath(ref)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _raise(error, f"Unsafe media path: {value}")
    if not pure.parts or (pure.parts[0] not in policy.allowed_roots and pure.as_posix() not in policy.allowed_files):
        _raise(error, f"Unsupported media path: {value}")
    return pure.as_posix()


def identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


@contextmanager
def media_parent(
    data_root: Path,
    ref: str,
    writer_lock: DirectoryWriterLock,
    *,
    policy: MediaRootPolicy,
    create: bool = False,
    error: type[Exception] = ProjectPackageMediaError,
):
    """Pin every parent with the same no-follow primitives as managed media."""
    parts = normalize_media_ref(ref, policy=policy, error=error).split("/")
    managed_media._platform()
    try:
        with ExitStack() as stack:
            root_fd = stack.enter_context(managed_media._opened(
                data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            ))
            writer_lock.assert_owned_current_path_for(root_fd)
            parents = [root_fd]
            for part in parts[:-1]:
                parents.append(stack.enter_context(managed_media._directory(parents[-1], part, create=create)))

            def check():
                writer_lock.assert_owned_current_path_for(root_fd)
                for parent, child, part in zip(parents, parents[1:], parts[:-1]):
                    current = os.stat(part, dir_fd=parent, follow_symlinks=False)
                    if not stat.S_ISDIR(current.st_mode) or identity(current) != identity(os.fstat(child)):
                        _raise(error, "Package media directory changed")

            check()
            yield parents[-1], parts[-1], check
            check()
    except error:
        raise
    except (OSError, managed_media.MediaVersionError) as exc:
        _raise(error, "Package media path is unavailable or unsafe", exc)


def read_owned_media(
    data_root: Path,
    ref: str,
    writer_lock: DirectoryWriterLock,
    max_bytes: int,
    *,
    policy: MediaRootPolicy,
    error: type[Exception] = ProjectPackageMediaError,
) -> bytes:
    with media_parent(data_root, ref, writer_lock, policy=policy, error=error) as (parent_fd, name, check):
        with managed_media._opened(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, parent_fd=parent_fd) as fd:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
                _raise(error, "Package media must be a bounded regular file")
            data = bytearray()
            for chunk in managed_media._chunks(fd):
                data.extend(chunk)
                if len(data) > max_bytes:
                    _raise(error, "Package media exceeds size limit")
            after = os.fstat(fd)
            check()
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                identity(linked) != identity(before)
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(data) != before.st_size
            ):
                _raise(error, "Package media changed during export")
            return bytes(data)


def validate_zip_infos(
    zf: zipfile.ZipFile,
    *,
    metadata_entries: set[str],
    media_entry_prefix: str,
    max_files: int,
    max_file_bytes: int,
    max_package_bytes: int,
    max_compression_ratio: int,
    error: type[Exception] = ProjectPackageMediaError,
) -> None:
    infos = zf.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        _raise(error, "Duplicate package entry")
    if len(infos) > max_files + len(metadata_entries):
        _raise(error, "Project package has too many files")
    total_size = 0
    for info in infos:
        validate_zip_path(info.filename, metadata_entries=metadata_entries, media_entry_prefix=media_entry_prefix, error=error)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            _raise(error, f"Unsafe package path: {info.filename}")
        if info.is_dir():
            _raise(error, f"Unsafe package path: {info.filename}")
        if info.file_size > max_file_bytes:
            _raise(error, f"Package entry is too large: {info.filename}")
        total_size += info.file_size
        if total_size > max_package_bytes:
            _raise(error, "Project package is too large")
        if info.file_size > 0:
            if info.compress_size == 0:
                _raise(error, f"Package entry has invalid compression ratio: {info.filename}")
            if info.file_size / info.compress_size > max_compression_ratio:
                _raise(error, f"Package entry exceeds compression ratio: {info.filename}")


def validate_zip_path(
    name: str,
    *,
    metadata_entries: set[str],
    media_entry_prefix: str,
    error: type[Exception] = ProjectPackageMediaError,
) -> None:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        _raise(error, f"Unsafe package path: {name}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _raise(error, f"Unsafe package path: {name}")
    if name not in metadata_entries and not name.startswith(media_entry_prefix):
        _raise(error, f"Unsafe package path: {name}")


def validated_manifest_media(
    manifest: Mapping[str, Any],
    zf: zipfile.ZipFile,
    *,
    policy: MediaRootPolicy,
    media_entry_prefix: str,
    max_files: int,
    max_file_bytes: int,
    metadata_entries: set[str],
    error: type[Exception] = ProjectPackageMediaError,
) -> list[dict[str, Any]]:
    entries = manifest.get("media")
    if not isinstance(entries, list):
        _raise(error, "Project package media manifest is invalid")
    if len(entries) > max_files:
        _raise(error, "Project package has too many media files")
    by_package = {info.filename: info for info in zf.infolist()}
    seen_refs: set[str] = set()
    seen_paths: set[str] = set()
    validated: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"logical_ref", "package_path", "sha256", "size_bytes"}:
            _raise(error, "Project package media entry is invalid")
        logical_ref = normalize_media_ref(entry.get("logical_ref"), policy=policy, error=error)
        package_path = entry.get("package_path")
        digest = entry.get("sha256")
        size_bytes = entry.get("size_bytes")
        if not isinstance(package_path, str) or not package_path.startswith(media_entry_prefix):
            _raise(error, "Project package media path is invalid")
        validate_zip_path(package_path, metadata_entries=metadata_entries, media_entry_prefix=media_entry_prefix, error=error)
        if package_path != media_entry_prefix + logical_ref:
            _raise(error, "Project package media path does not match logical ref")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            _raise(error, "Project package media hash is invalid")
        if type(size_bytes) is not int or size_bytes < 0 or size_bytes > max_file_bytes:
            _raise(error, "Project package media size is invalid")
        if logical_ref in seen_refs or package_path in seen_paths:
            _raise(error, "Duplicate package entry")
        seen_refs.add(logical_ref)
        seen_paths.add(package_path)
        info = by_package.get(package_path)
        if info is None:
            _raise(error, f"Project package is missing media: {logical_ref}")
        if info.file_size != size_bytes:
            _raise(error, f"Project package media size mismatch: {logical_ref}")
        try:
            data = zf.read(package_path)
        except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
            _raise(error, f"Project package media could not be read: {logical_ref}", exc)
        if hashlib.sha256(data).hexdigest() != digest:
            _raise(error, f"Project package media hash mismatch: {logical_ref}")
        validated.append({"logical_ref": logical_ref, "package_path": package_path, "sha256": digest, "size_bytes": size_bytes})
    return validated


def stage_media(
    zf: zipfile.ZipFile,
    media_entries: list[dict[str, Any]],
    *,
    media_map: Mapping[str, str],
    data_root: Path,
    writer_lock: DirectoryWriterLock,
    policy: MediaRootPolicy,
    error: type[Exception] = ProjectPackageMediaError,
) -> None:
    written: list[tuple[str, tuple[int, int]]] = []
    writer_lock.assert_owned_path()
    try:
        for entry in media_entries:
            logical_ref = entry["logical_ref"]
            target_ref = media_map[logical_ref]
            data = zf.read(entry["package_path"])
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                _raise(error, f"Project package media hash mismatch: {logical_ref}")
            with media_parent(data_root, target_ref, writer_lock, policy=policy, create=True, error=error) as (parent_fd, name, check):
                with managed_media._opened(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, parent_fd=parent_fd,
                ) as fd:
                    file_identity = identity(os.fstat(fd))
                    try:
                        written.append((target_ref, file_identity))
                        check()
                        managed_media._write_all(fd, data)
                        os.fsync(fd)
                        check()
                        if identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != file_identity:
                            _raise(error, "Package media target changed")
                    except Exception:
                        try:
                            unlink_created_media(parent_fd, name, file_identity)
                        except OSError:
                            pass
                        raise
                os.fsync(parent_fd)
    except Exception:
        for target_ref, file_identity in reversed(written):
            try:
                with media_parent(data_root, target_ref, writer_lock, policy=policy, error=error) as (parent_fd, name, check):
                    check()
                    unlink_created_media(parent_fd, name, file_identity)
            except Exception:
                pass
        raise


def unlink_created_media(parent_fd: int, name: str, file_identity: tuple[int, int]) -> None:
    if identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) == file_identity:
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)


def cleanup_imported_media(
    media_entries: list[dict[str, Any]],
    *,
    media_map: Mapping[str, str],
    import_project_id: str,
    data_root: Path,
    writer_lock: DirectoryWriterLock,
    policy: MediaRootPolicy,
    error: type[Exception] = ProjectPackageMediaError,
) -> None:
    for entry in media_entries:
        target = media_map[entry["logical_ref"]]
        parts = normalize_media_ref(target, policy=policy, error=error).split("/")
        if len(parts) < 4 or parts[1:3] != ["imported", import_project_id]:
            _raise(error, "Cleanup target is not owned by this import")
        with media_parent(data_root, target, writer_lock, policy=policy, error=error) as (parent_fd, name, check):
            try:
                with managed_media._opened(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, parent_fd=parent_fd) as fd:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_size != entry["size_bytes"]:
                        _raise(error, "Cleanup target changed")
                    digest = hashlib.sha256()
                    count = 0
                    for chunk in managed_media._chunks(fd):
                        count += len(chunk)
                        if count > entry["size_bytes"]:
                            _raise(error, "Cleanup target changed")
                        digest.update(chunk)
                    check()
                    if (
                        digest.hexdigest() != entry["sha256"]
                        or identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != identity(before)
                    ):
                        _raise(error, "Cleanup target changed")
                    os.unlink(name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except FileNotFoundError:
                continue
