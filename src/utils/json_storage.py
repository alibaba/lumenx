"""Strict JSON replacement and process ownership for local data directories.

These primitives do not provide multi-file transactions or revision checks. A
directory owner must hold its lock throughout loading, recovery and all writes.
"""

from __future__ import annotations

import errno
import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import IO, Any, Callable, TypeVar


class StorageError(RuntimeError):
    """A persistence operation failed.

    ``commit_uncertain`` means replacement happened but directory sync failed:
    the new file is visible, but its survival across a crash is not confirmed.
    Callers must not treat this as proof that the old value is still stored or
    automatically repeat an external side effect.
    """

    def __init__(self, message: str, *, commit_uncertain: bool = False):
        super().__init__(message)
        self.commit_uncertain = commit_uncertain


class CorruptStorageError(StorageError):
    """Existing storage cannot be decoded without losing information."""


class StorageConflictError(StorageError):
    """Another owner or revision prevents this operation."""


T = TypeVar("T")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"JSON number exceeds finite range: {value}")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: str | Path, default_factory: Callable[[], T]) -> Any | T:
    """Use the default only for an absent file; never replace corrupt storage."""
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(
                stream,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
                object_pairs_hook=_unique_object,
            )
    except FileNotFoundError:
        return default_factory()
    except (ValueError, RecursionError) as exc:
        raise CorruptStorageError(f"Cannot decode JSON storage {path}: {exc}") from exc
    except OSError as exc:
        raise StorageError(f"Cannot read JSON storage {path}: {exc}") from exc


def serialize_json(value: Any) -> str:
    """Validate JSON serialization without creating or changing any files."""
    try:
        contents = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        # Python coerces object keys to strings; collisions must not produce a
        # file that our reader correctly rejects as ambiguous.
        json.loads(contents, object_pairs_hook=_unique_object)
        contents.encode("utf-8")
        return contents
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise StorageError(f"Cannot serialize JSON storage: {exc}") from exc


def atomic_write_json(path: str | Path, value: Any) -> None:
    """Validate JSON fully before creating files, then atomically replace it."""
    atomic_write_text(path, serialize_json(value))


def atomic_write_text(path: str | Path, contents: str) -> None:
    """Flush and replace UTF-8 text without validating JSON or changing newlines.

    This also supports restoring previously validated JSON in its original
    formatting. POSIX syncs the parent directory. Windows has no portable
    stdlib directory-fsync operation: file fsync and replacement are supported,
    but power-loss durability of the directory entry is not promised there.
    """
    path = Path(path)
    try:
        encoded = contents.encode("utf-8")
    except UnicodeError as exc:
        raise StorageError(f"Cannot encode text storage {path}: {exc}") from exc

    temporary: Path | None = None
    replaced = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(filename)
        try:
            stream = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        replaced = True
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except (OSError, UnicodeError) as exc:
        state = "; replacement is visible, durability uncertain" if replaced else ""
        raise StorageError(
            f"Cannot save storage {path}{state}: {exc}", commit_uncertain=replaced
        ) from exc
    finally:
        if temporary is not None and not replaced:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Preserve the primary write error. An orphan temp cannot be
                # mistaken for the authoritative JSON filename.
                pass


class DirectoryWriterLock:
    """Hold one OS-backed writer lease, released even when the process exits.

    Never unlink the lock file: a waiter could otherwise lock its old inode
    while another writer acquires a replacement at the same path.
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self._file: IO[bytes] | None = None

    def acquire(self) -> DirectoryWriterLock:
        if self._file is not None:
            return self
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            stream = (self.data_dir / ".lumenx-writer.lock").open("a+b")
        except OSError as exc:
            raise StorageError(f"Cannot open data directory lock {self.data_dir}: {exc}") from exc
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise StorageConflictError(
                    f"Data directory already has a writer: {self.data_dir}"
                ) from exc
            raise StorageError(f"Cannot acquire data directory {self.data_dir}: {exc}") from exc
        self._file = stream
        return self

    def close(self) -> None:
        stream, self._file = self._file, None
        if stream is None:
            return
        # Closing the descriptor releases its lock. Explicit unlock would also
        # release a parent's shared file description if a process had forked.
        stream.close()

    def assert_owned(self) -> None:
        """Reject a dependent store after its shared owner has closed."""
        if self._file is None or self._file.closed:
            raise StorageConflictError(f'Data directory writer is closed: {self.data_dir}')

    def assert_owned_path(self) -> None:
        """Check that the configured path still contains this lease's lock file."""
        self.assert_owned()
        stream = self._file
        if stream is None:
            raise StorageConflictError(f'Data directory writer is closed: {self.data_dir}')
        try:
            root = self.data_dir.stat(follow_symlinks=False)
            entry = (self.data_dir / ".lumenx-writer.lock").stat(follow_symlinks=False)
            held = os.fstat(stream.fileno())
        except (OSError, ValueError) as exc:
            raise StorageConflictError("Cannot verify writer lease for data directory path") from exc
        if (not stat.S_ISDIR(root.st_mode) or not stat.S_ISREG(entry.st_mode)
                or (entry.st_dev, entry.st_ino) != (held.st_dev, held.st_ino)):
            raise StorageConflictError("Writer lease does not own the data directory path")
        self.assert_owned()

    def assert_owned_for(self, root_fd: int) -> None:
        """Check that an opened directory contains this lease's exact lock file.

        This validates directory identity; callers still own lifecycle locking.
        A replacement directory at the same pathname does not inherit the lease.
        """
        self.assert_owned()
        stream = self._file
        if type(root_fd) is not int or root_fd < 0 or stream is None:
            raise StorageConflictError("Invalid directory descriptor for writer lease")
        try:
            root = os.fstat(root_fd)
            entry = os.stat(".lumenx-writer.lock", dir_fd=root_fd, follow_symlinks=False)
            held = os.fstat(stream.fileno())
        except (OSError, ValueError) as exc:
            raise StorageConflictError("Cannot verify writer lease for opened directory") from exc
        if (not stat.S_ISDIR(root.st_mode) or not stat.S_ISREG(entry.st_mode)
                or (entry.st_dev, entry.st_ino) != (held.st_dev, held.st_ino)):
            raise StorageConflictError("Writer lease does not own the opened directory")
        self.assert_owned()

    def assert_owned_current_path_for(self, root_fd: int) -> None:
        """Check that an opened owned directory is still the configured path."""
        self.assert_owned_for(root_fd)
        try:
            root = os.fstat(root_fd)
            current = self.data_dir.stat(follow_symlinks=False)
        except (OSError, ValueError) as exc:
            raise StorageConflictError("Cannot verify opened directory is still current path") from exc
        if (not stat.S_ISDIR(current.st_mode)
                or (root.st_dev, root.st_ino) != (current.st_dev, current.st_ino)):
            raise StorageConflictError("Opened data directory is no longer the configured path")
        self.assert_owned()

    def __enter__(self) -> DirectoryWriterLock:
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
