from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping


class JsonStoreLockTimeout(TimeoutError):
    """Raised when another process keeps a JSON store locked for too long."""


class JsonStoreLock:
    def __init__(
        self,
        target_path: str | Path,
        *,
        timeout_seconds: float = 10.0,
        stale_after_seconds: float = 300.0,
        poll_seconds: float = 0.1,
    ) -> None:
        self.target_path = Path(target_path)
        self.lock_path = Path(f"{self.target_path}.lock")
        self.timeout_seconds = timeout_seconds
        self.stale_after_seconds = stale_after_seconds
        self.poll_seconds = poll_seconds
        self._acquired = False

    def __enter__(self) -> "JsonStoreLock":
        deadline = time.monotonic() + self.timeout_seconds
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump({"pid": os.getpid(), "created_at": time.time()}, handle)
                except Exception:
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    raise
                self._acquired = True
                return self
            except FileExistsError:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise JsonStoreLockTimeout(
                        f"Timed out waiting for JSON store lock: {self.lock_path}"
                    )
                time.sleep(self.poll_seconds)

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        if not self._acquired:
            return
        try:
            self.lock_path.unlink(missing_ok=True)
        finally:
            self._acquired = False

    def _remove_stale_lock(self) -> bool:
        try:
            stat = self.lock_path.stat()
        except FileNotFoundError:
            return True

        lock_age = time.time() - stat.st_mtime
        if lock_age < self.stale_after_seconds:
            try:
                with self.lock_path.open("r", encoding="utf-8") as handle:
                    metadata = json.load(handle)
                pid = int(metadata.get("pid"))
            except Exception:
                return False

            if pid > 0 and not _process_is_alive(pid):
                try:
                    self.lock_path.unlink()
                    return True
                except FileNotFoundError:
                    return True
                except OSError:
                    return False
            return False

        try:
            self.lock_path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


def _process_is_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True

    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _backup_path(path: Path, suffix: str) -> Path:
    return Path(f"{path}{suffix}")


def load_json_object_with_backup(
    path: str | Path,
    *,
    backup_suffix: str = ".bak",
) -> dict[str, Any]:
    target_path = Path(path)
    backup_path = _backup_path(target_path, backup_suffix)
    last_error: Exception | None = None

    for candidate in (target_path, backup_path):
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError(f"Expected JSON object in {candidate}")
            return data
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return {}


def save_json_object_atomic(
    path: str | Path,
    data: Mapping[str, Any],
    *,
    backup_suffix: str = ".bak",
    lock_timeout_seconds: float = 10.0,
) -> None:
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_path(target_path, backup_suffix)
    temp_path: Path | None = None

    with JsonStoreLock(target_path, timeout_seconds=lock_timeout_seconds):
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(target_path.parent),
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(data, temp_file, ensure_ascii=False, indent=2)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            if target_path.exists() and _is_valid_json_object(target_path):
                shutil.copy2(target_path, backup_path)

            os.replace(temp_path, target_path)
            _fsync_parent_directory(target_path)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return

    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return

    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _is_valid_json_object(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return isinstance(json.load(handle), dict)
    except Exception:
        return False
