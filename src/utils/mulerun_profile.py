"""App-owned MuleRun profile helpers.

The MuleRun token/profile schema is opaque. These helpers only move the
well-documented ``.mulerun`` tree between owned HOME directories and run the CLI
with explicit site/base controls plus upgrade opt-outs.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from dataclasses import dataclass, field
from itertools import count
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

SUBPROCESS_ENV_ALLOWLIST = (
    "PATH", "TMPDIR", "TEMP", "TMP", "SHELL", "USER", "LOGNAME",
    "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
)

AUTO_UPGRADE_OPTOUTS = MappingProxyType({
    "MULERUN_DISABLE_AUTO_UPGRADE": "1",
    "MULERUN_SKIP_AUTO_UPGRADE": "1",
    "NO_UPDATE_NOTIFIER": "1",
})

MULERUN_SITE_BASE_URLS = MappingProxyType({
    "sg": "https://api.mulerun.com",
    "cn": "https://api.xiaoluozi.cn",
})
DEFAULT_MULERUN_SITE = "sg"
_MAX_PROFILE_FILES = 256
_MAX_PROFILE_BYTES = 8 * 1024 * 1024


class MuleRunProfileError(RuntimeError):
    """Raised when an app-owned MuleRun profile cannot be used safely."""


@dataclass(frozen=True)
class MuleRunProfileSnapshot:
    """A private copied MuleRun HOME plus its opaque profile digest."""

    home: Path
    fingerprint: str


def mulerun_profile_home(env: Mapping[str, str]) -> Path | None:
    """Return the internal app-owned MuleRun HOME, if one was injected."""
    value = env.get("LUMENX_MULERUN_HOME")
    if not value:
        return None
    return Path(value).expanduser()


def _mulerun_site(env: Mapping[str, str]) -> str:
    return (env.get("MULERUN_SITE") or DEFAULT_MULERUN_SITE).strip().lower()


def _mulerun_base_url(env: Mapping[str, str], site: str) -> str:
    explicit = env.get("MULERUN_API_BASE_URL") or env.get("MULERUN_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    try:
        return MULERUN_SITE_BASE_URLS[site]
    except KeyError as exc:
        raise MuleRunProfileError(f"Unsupported MuleRun site: {site}") from exc


def mulerun_global_flags(env: Mapping[str, str]) -> list[str]:
    """Build explicit global MuleRun CLI flags without reading ambient config."""
    flags: list[str] = []
    token = env.get("MULERUN_TOKEN")
    if token:
        flags.extend(["--token", token])
    site = _mulerun_site(env)
    flags.extend(["--base-url", _mulerun_base_url(env, site)])
    flags.extend(["--site", site])
    return flags


def build_mulerun_profile_env(
    env: Mapping[str, str],
    *,
    profile_home: str | os.PathLike[str],
    token_required: bool = False,
    include_token: bool = True,
    create_dirs: bool = True,
) -> dict[str, str]:
    """Build a sanitized subprocess env pinned to an owned HOME.

    Only OS execution/certificate variables are inherited. Provider credentials
    and process HOME/XDG state are deliberately not inherited.
    """
    token = env.get("MULERUN_TOKEN")
    if token_required and not token:
        raise MuleRunProfileError("MULERUN_TOKEN is required for token-backed MuleRun execution")

    home = Path(profile_home).expanduser()
    _reject_symlink_components(home)
    config_home = home / ".config"
    cache_home = home / ".cache"
    data_home = home / ".local" / "share"
    if create_dirs:
        for path in (home, config_home, cache_home, data_home):
            path.mkdir(parents=True, exist_ok=True)

    child_env = {key: value for key, value in os.environ.items() if key in SUBPROCESS_ENV_ALLOWLIST}
    child_env.update(AUTO_UPGRADE_OPTOUTS)
    child_env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_CACHE_HOME": str(cache_home),
        "XDG_DATA_HOME": str(data_home),
    })
    if include_token and token:
        child_env["MULERUN_TOKEN"] = token
    return child_env


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                raise MuleRunProfileError(f"Owned MuleRun profile path contains a symlink: {current}")
        except OSError as exc:
            raise MuleRunProfileError(f"Cannot inspect MuleRun profile path: {current}") from exc
        if not current.exists():
            break


def _profile_source_path(profile_home: str | os.PathLike[str]) -> Path:
    home = Path(profile_home).expanduser()
    _reject_symlink_components(home)
    source = home / ".mulerun"
    _reject_symlink_components(source)
    return source


def _require_safe_profile_tree(profile_home: str | os.PathLike[str]) -> Path:
    source = _profile_source_path(profile_home)
    if not source.is_dir():
        raise MuleRunProfileError("Owned MuleRun profile is not logged in")

    total_files = 0
    total_bytes = 0
    for path in source.rglob("*"):
        _reject_symlink_components(path)
        try:
            info = path.lstat()
        except (OSError, TypeError, NotImplementedError) as exc:
            raise MuleRunProfileError("Cannot inspect owned MuleRun profile") from exc
        if stat.S_ISLNK(info.st_mode):
            raise MuleRunProfileError("Owned MuleRun profile contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise MuleRunProfileError("Owned MuleRun profile contains an unsupported entry")
        total_files += 1
        if total_files > _MAX_PROFILE_FILES:
            raise MuleRunProfileError("Owned MuleRun profile has too many files")
        total_bytes += info.st_size
        if total_bytes > _MAX_PROFILE_BYTES:
            raise MuleRunProfileError("Owned MuleRun profile is too large")
    return source


def _require_descriptor_copy_support() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise MuleRunProfileError("Owned MuleRun profile copy requires no-follow directory support")


def _open_source_dir_no_follow(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current_fd = None
    try:
        # Recheck the owned root at open time as well as at pre-validation.
        # Once open, all descendants are accessed relative to this descriptor.
        _reject_symlink_components(path)
        expected = path.stat(follow_symlinks=False)
        current_fd = os.open(path, flags)
        opened = os.fstat(current_fd)
        _reject_symlink_components(path)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise MuleRunProfileError("Owned MuleRun profile directory changed during copy")
        result = current_fd
        current_fd = None
        return result
    except (OSError, TypeError, NotImplementedError) as exc:
        raise MuleRunProfileError("Cannot open owned MuleRun profile directory") from exc
    finally:
        if current_fd is not None:
            os.close(current_fd)


def _open_child_dir_no_follow(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except (OSError, TypeError, NotImplementedError) as exc:
        raise MuleRunProfileError("Owned MuleRun profile directory changed during copy") from exc


class _BoundedProfileReader:
    def __init__(self, raw: Any, *, existing_bytes: int) -> None:
        self.raw = raw
        self.existing_bytes = existing_bytes
        self.copied = 0

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        if data:
            self.copied += len(data)
            if self.existing_bytes + self.copied > _MAX_PROFILE_BYTES:
                raise MuleRunProfileError("Owned MuleRun profile is too large")
        return data


def _copy_regular_child_no_follow(
    parent_fd: int,
    name: str,
    target: Path,
    *,
    file_counter: Any,
    byte_counter: list[int],
) -> None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (OSError, TypeError, NotImplementedError) as exc:
        raise MuleRunProfileError("Cannot inspect owned MuleRun profile file") from exc
    if not stat.S_ISREG(info.st_mode):
        raise MuleRunProfileError("Owned MuleRun profile contains an unsupported entry")

    file_count = next(file_counter)
    if file_count > _MAX_PROFILE_FILES:
        raise MuleRunProfileError("Owned MuleRun profile has too many files")
    if byte_counter[0] + info.st_size > _MAX_PROFILE_BYTES:
        raise MuleRunProfileError("Owned MuleRun profile is too large")

    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        source_fd = os.open(name, flags, dir_fd=parent_fd)
    except (OSError, TypeError, NotImplementedError) as exc:
        raise MuleRunProfileError("Cannot copy owned MuleRun profile file") from exc
    try:
        opened_info = os.fstat(source_fd)
        if not stat.S_ISREG(opened_info.st_mode):
            raise MuleRunProfileError("Owned MuleRun profile contains an unsupported entry")
        if opened_info.st_ino != info.st_ino or opened_info.st_dev != info.st_dev:
            raise MuleRunProfileError("Owned MuleRun profile file changed during copy")
        if byte_counter[0] + opened_info.st_size > _MAX_PROFILE_BYTES:
            raise MuleRunProfileError("Owned MuleRun profile is too large")
        source_file = os.fdopen(source_fd, "rb")
        source_fd = -1
        try:
            try:
                target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(opened_info.st_mode) or 0o600)
            except OSError as exc:
                raise MuleRunProfileError("Cannot create owned MuleRun profile copy") from exc
            try:
                target_file = os.fdopen(target_fd, "wb")
                target_fd = -1
                try:
                    with source_file as src, target_file as dst:
                        bounded_src = _BoundedProfileReader(src, existing_bytes=byte_counter[0])
                        shutil.copyfileobj(bounded_src, dst)
                        byte_counter[0] += bounded_src.copied
                except MuleRunProfileError:
                    raise
                except OSError as exc:
                    raise MuleRunProfileError("Cannot copy owned MuleRun profile file") from exc
            finally:
                if target_fd >= 0:
                    os.close(target_fd)
        finally:
            source_file.close()
    finally:
        if source_fd >= 0:
            os.close(source_fd)

    try:
        os.utime(target, ns=(opened_info.st_atime_ns, opened_info.st_mtime_ns), follow_symlinks=False)
    except OSError as exc:
        raise MuleRunProfileError("Cannot preserve owned MuleRun profile file metadata") from exc


def _copy_profile_dir_from_fd(
    source_fd: int,
    destination: Path,
    *,
    file_counter: Any,
    byte_counter: list[int],
) -> None:
    try:
        names = sorted(os.listdir(source_fd))
    except (OSError, TypeError, NotImplementedError) as exc:
        raise MuleRunProfileError("Cannot list owned MuleRun profile") from exc

    for name in names:
        if name in {"", ".", ".."} or "/" in name or (os.altsep and os.altsep in name):
            raise MuleRunProfileError("Owned MuleRun profile contains an unsafe entry name")
        try:
            info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except (OSError, TypeError, NotImplementedError) as exc:
            raise MuleRunProfileError("Cannot inspect owned MuleRun profile") from exc
        child_destination = destination / name
        if stat.S_ISLNK(info.st_mode):
            raise MuleRunProfileError("Owned MuleRun profile contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            child_destination.mkdir()
            child_fd = _open_child_dir_no_follow(source_fd, name)
            try:
                opened_info = os.fstat(child_fd)
                if opened_info.st_ino != info.st_ino or opened_info.st_dev != info.st_dev:
                    raise MuleRunProfileError("Owned MuleRun profile directory changed during copy")
                _copy_profile_dir_from_fd(
                    child_fd,
                    child_destination,
                    file_counter=file_counter,
                    byte_counter=byte_counter,
                )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(info.st_mode):
            _copy_regular_child_no_follow(
                source_fd,
                name,
                child_destination,
                file_counter=file_counter,
                byte_counter=byte_counter,
            )
        else:
            raise MuleRunProfileError("Owned MuleRun profile contains an unsupported entry")


def copy_owned_mulerun_profile(source_home: str | os.PathLike[str], target_home: str | os.PathLike[str]) -> Path:
    """Copy only the opaque ``.mulerun`` tree into a private execution HOME."""
    _require_descriptor_copy_support()
    source = _require_safe_profile_tree(source_home)
    target = Path(target_home).expanduser() / ".mulerun"
    if target.exists():
        raise MuleRunProfileError("Target MuleRun profile already exists")
    target.mkdir(parents=True)

    source_fd = None
    try:
        source_fd = _open_source_dir_no_follow(source)
        _copy_profile_dir_from_fd(
            source_fd,
            target,
            file_counter=count(1),
            byte_counter=[0],
        )
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        if source_fd is not None:
            os.close(source_fd)
    return target


def fingerprint_owned_mulerun_profile(profile_home: str | os.PathLike[str]) -> str:
    """Return a stable secret-free fingerprint of an app-owned MuleRun profile.

    The source profile is first copied through ``copy_owned_mulerun_profile`` so
    fingerprinting inherits the same no-follow, bounded-size, bounded-file-count
    and race checks used by execution. The returned digest binds relative entry
    paths and file bytes from the opaque ``.mulerun`` tree, but never exposes
    those bytes or the absolute profile path.
    """
    with snapshot_mulerun_profile_to_temp_home_with_fingerprint(profile_home) as snapshot:
        return snapshot.fingerprint


def _fingerprint_copied_profile_tree(source: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"lumenx-mulerun-profile-fingerprint-v1\0")
    entries = sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
    for path in entries:
        relative = path.relative_to(source).as_posix().encode("utf-8")
        try:
            info = path.lstat()
        except OSError as exc:
            raise MuleRunProfileError("Cannot inspect copied MuleRun profile") from exc
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"D\0")
            digest.update(relative)
            digest.update(b"\0")
            continue
        if not stat.S_ISREG(info.st_mode):
            raise MuleRunProfileError("Copied MuleRun profile contains an unsupported entry")
        digest.update(b"F\0")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(info.st_size).encode("ascii"))
        digest.update(b"\0")
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as exc:
            raise MuleRunProfileError("Cannot read copied MuleRun profile") from exc
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def snapshot_mulerun_profile_to_temp_home_with_fingerprint(profile_home: str | os.PathLike[str]):
    """Yield one private HOME copy plus the digest of that same copied tree."""
    with tempfile.TemporaryDirectory(prefix="lumenx-mulerun-profile-fingerprint-") as temp_root:
        home = Path(temp_root) / "home"
        home.mkdir(parents=True, exist_ok=True)
        copied = copy_owned_mulerun_profile(profile_home, home)
        yield MuleRunProfileSnapshot(home=home, fingerprint=_fingerprint_copied_profile_tree(copied))


@contextmanager
def snapshot_mulerun_profile_to_temp_home(profile_home: str | os.PathLike[str]):
    """Yield a private HOME containing a snapshot of owned `.mulerun`."""
    with tempfile.TemporaryDirectory(prefix="lumenx-mulerun-profile-") as temp_root:
        home = Path(temp_root) / "home"
        home.mkdir(parents=True, exist_ok=True)
        copy_owned_mulerun_profile(profile_home, home)
        yield home


@dataclass
class MuleRunLoginSession:
    """Managed app-owned MuleRun browser-login subprocess wrapper."""

    profile_home: str | os.PathLike[str]
    env: Mapping[str, str]
    executable: str = "mulerun"
    popen_factory: Any = subprocess.Popen
    run_factory: Any = subprocess.run
    process: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.env = MappingProxyType(dict(self.env))
        self.profile_home = Path(self.profile_home).expanduser()

    def _env(self, *, create_dirs: bool) -> dict[str, str]:
        return build_mulerun_profile_env(
            self.env,
            profile_home=self.profile_home,
            token_required=False,
            include_token=False,
            create_dirs=create_dirs,
        )

    def _global_flags(self) -> list[str]:
        tokenless = {key: value for key, value in self.env.items() if key != "MULERUN_TOKEN"}
        return mulerun_global_flags(tokenless)

    def start(self) -> bool:
        """Start an owned browser-login process unless one is already running."""
        with self._lock:
            if self._closed:
                return False
            if self.process is not None and self.process.poll() is None:
                return False
            cmd = [self.executable] + self._global_flags() + ["login"]
            self.process = self.popen_factory(
                cmd,
                env=self._env(create_dirs=True),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
            )
            return True

    def status(self, *, timeout: int = 10) -> bool:
        """Return whether the owned profile is logged in according to the CLI."""
        with self._lock:
            if self._closed:
                return False
            try:
                _require_safe_profile_tree(self.profile_home)
            except MuleRunProfileError:
                return False
            cmd = [self.executable] + self._global_flags() + ["login", "status"]
            try:
                result = self.run_factory(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=self._env(create_dirs=False),
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return False
            return result.returncode == 0

    def close(self) -> None:
        """Terminate only this session's owned subprocess."""
        with self._lock:
            self._closed = True
            process = self.process
            self.process = None
            if process is None or process.poll() is not None:
                return
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
