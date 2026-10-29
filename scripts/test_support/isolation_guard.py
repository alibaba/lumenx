"""Process-level guard for LumenX Python tests.

The runner installs this module before pytest is imported.  It keeps test
collection away from personal configuration, live provider credentials, and
writes outside the runner's temporary root.
"""

from __future__ import annotations

import atexit
import builtins
import io
import os
import mimetypes
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


_INSTALLED = False
_VIOLATIONS: list[str] = []
_READ_ROOTS: tuple[Path, ...] = ()
_WRITE_ROOTS: tuple[Path, ...] = ()
_REPO_ROOT: Path | None = None

_REAL_OPEN = builtins.open
_REAL_IO_OPEN = io.open
_REAL_OS_OPEN = os.open
_REAL_MAKEDIRS = os.makedirs
_REAL_MKDIR = os.mkdir
_REAL_REPLACE = os.replace
_REAL_RENAME = os.rename
_REAL_REMOVE = os.remove
_REAL_UNLINK = os.unlink
_REAL_RMDIR = os.rmdir
_REAL_CHDIR = os.chdir
_REAL_SOCKET = socket.socket
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_POPEN = subprocess.Popen
_REAL_RUN = subprocess.run


def _paths_from_env(name: str) -> list[Path]:
    raw = os.environ.get(name, "")
    paths: list[Path] = []
    for value in raw.split(os.pathsep):
        if value.strip():
            paths.append(Path(value).expanduser().resolve())
    return paths


def _root() -> Path:
    value = os.environ.get("LUMENX_TEST_ISOLATION_ROOT")
    if not value:
        raise RuntimeError("LUMENX_TEST_ISOLATION_ROOT is required")
    return Path(value).resolve()


def _repo_root() -> Path:
    if _REPO_ROOT is not None:
        return _REPO_ROOT
    value = os.environ.get("LUMENX_REPO_ROOT")
    if not value:
        raise RuntimeError("LUMENX_REPO_ROOT is required")
    return Path(value).resolve()


def _read_roots() -> list[Path]:
    if _READ_ROOTS:
        return list(_READ_ROOTS)
    roots = [_root(), _repo_root(), Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()]
    roots.extend(_paths_from_env("LUMENX_TEST_READ_ROOTS"))
    system_version = Path("/System/Library/CoreServices/SystemVersion.plist")
    if system_version.exists():
        roots.append(system_version)
    return roots


def _write_roots() -> list[Path]:
    return list(_WRITE_ROOTS) if _WRITE_ROOTS else [_root()]


def _record(message: str) -> None:
    _VIOLATIONS.append(message)


def violations() -> tuple[str, ...]:
    return tuple(_VIOLATIONS)


def clear_violations() -> None:
    _VIOLATIONS.clear()


def assert_no_violations() -> None:
    if _VIOLATIONS:
        details = "\n".join(f"- {item}" for item in _VIOLATIONS)
        raise AssertionError(f"LumenX test isolation guard recorded violations:\n{details}")


def _resolve_path(path: object) -> Path | None:
    if isinstance(path, int):
        return None
    try:
        return Path(os.fsdecode(os.fspath(path))).expanduser().resolve()
    except (TypeError, ValueError, OSError) as exc:
        _block(f"unresolvable path blocked: {type(exc).__name__}")


def _at(path: object, dir_fd=None):
    if dir_fd in (None, -1) or Path(os.fsdecode(path)).is_absolute():
        return path
    try:
        if sys.platform == "darwin":
            import fcntl
            folder = os.fsdecode(fcntl.fcntl(dir_fd, 50, b"\0" * 1024).split(b"\0", 1)[0])
        else:
            folder = os.readlink(f"/proc/self/fd/{dir_fd}")
        return Path(folder) / os.fsdecode(path)
    except (OSError, ValueError, TypeError):
        _block("unresolvable dir_fd-relative path blocked")


def _is_inside(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _mode_writes(mode: object) -> bool:
    text = str(mode)
    return any(flag in text for flag in ("w", "a", "x", "+"))


def _is_repo_env(path: Path) -> bool:
    return path.name == ".env" and path.parent == _repo_root()


def _check_path(path: object, *, write: bool, operation: str) -> None:
    resolved = _resolve_path(path)
    if resolved is None:
        return
    if resolved == Path(os.devnull).resolve():
        return
    if not write and _is_inside(resolved, [_repo_root() / "output", _repo_root() / "logs"]):
        _block(f"{operation} blocked repository runtime data: {resolved}")
    if _is_repo_env(resolved):
        message = f"{operation} blocked real repository .env: {resolved}"
        _record(message)
        raise RuntimeError(message)
    if write:
        if not _is_inside(resolved, _write_roots()):
            message = f"{operation} blocked write outside isolated root: {resolved}"
            _record(message)
            raise RuntimeError(message)
    elif not _is_inside(resolved, _read_roots()):
        message = f"{operation} blocked read outside allowed roots: {resolved}"
        _record(message)
        raise RuntimeError(message)


def _guarded_open(file, mode="r", *args, **kwargs):
    _check_path(file, write=_mode_writes(mode), operation="open")
    return _REAL_OPEN(file, mode, *args, **kwargs)


def _guarded_io_open(file, mode="r", *args, **kwargs):
    _check_path(file, write=_mode_writes(mode), operation="io.open")
    return _REAL_IO_OPEN(file, mode, *args, **kwargs)


def _guarded_os_open(file, flags, mode=0o777, *args, **kwargs):
    write = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    _check_path(_at(file, kwargs.get("dir_fd")), write=write, operation="os.open")
    return _REAL_OS_OPEN(file, flags, mode, *args, **kwargs)


def _guarded_makedirs(name, mode=0o777, exist_ok=False):
    _check_path(name, write=True, operation="os.makedirs")
    return _REAL_MAKEDIRS(name, mode=mode, exist_ok=exist_ok)


def _guarded_mkdir(path, mode=0o777, *args, **kwargs):
    _check_path(_at(path, kwargs.get("dir_fd")), write=True, operation="os.mkdir")
    return _REAL_MKDIR(path, mode, *args, **kwargs)


def _guarded_replace(src, dst, *args, **kwargs):
    _check_path(_at(src, kwargs.get("src_dir_fd")), write=True, operation="os.replace source")
    _check_path(_at(dst, kwargs.get("dst_dir_fd")), write=True, operation="os.replace destination")
    return _REAL_REPLACE(src, dst, *args, **kwargs)


def _guarded_rename(src, dst, *args, **kwargs):
    _check_path(_at(src, kwargs.get("src_dir_fd")), write=True, operation="os.rename source")
    _check_path(_at(dst, kwargs.get("dst_dir_fd")), write=True, operation="os.rename destination")
    return _REAL_RENAME(src, dst, *args, **kwargs)


def _guarded_remove(path, *args, **kwargs):
    _check_path(_at(path, kwargs.get("dir_fd")), write=True, operation="os.remove")
    return _REAL_REMOVE(path, *args, **kwargs)


def _guarded_unlink(path, *args, **kwargs):
    _check_path(_at(path, kwargs.get("dir_fd")), write=True, operation="os.unlink")
    return _REAL_UNLINK(path, *args, **kwargs)


def _guarded_rmdir(path, *args, **kwargs):
    _check_path(_at(path, kwargs.get("dir_fd")), write=True, operation="os.rmdir")
    return _REAL_RMDIR(path, *args, **kwargs)


def _guarded_chdir(path):
    _check_path(path, write=True, operation="os.chdir")
    return _REAL_CHDIR(path)


class _GuardedSocket(socket.socket):
    def connect(self, address):  # type: ignore[override]
        message = f"socket.connect blocked by test isolation: {address!r}"
        _record(message)
        raise RuntimeError(message)

    def connect_ex(self, address):  # type: ignore[override]
        message = f"socket.connect_ex blocked by test isolation: {address!r}"
        _record(message)
        raise RuntimeError(message)


def _guarded_create_connection(address, *args, **kwargs):
    message = f"socket.create_connection blocked by test isolation: {address!r}"
    _record(message)
    raise RuntimeError(message)


def _command_sequence(args: object) -> Sequence[str]:
    if isinstance(args, (str, bytes, os.PathLike)):
        return [os.fspath(args)]
    return [os.fspath(item) for item in args]  # type: ignore[arg-type]


def _looks_like_path(value: str) -> bool:
    return value.startswith("/") or value.startswith(".") or "/" in value or "\\" in value


def _env_value(env: object | None, key: str) -> str | None:
    source = os.environ if env is None else env
    try:
        value = source.get(key)  # type: ignore[attr-defined]
    except AttributeError:
        return None
    return value if value is None else str(value)


def _resolve_executable(command: str, env: object | None) -> Path | None:
    raw = Path(command)
    if raw.is_absolute() or raw.parent != Path("."):
        try:
            return raw.expanduser().resolve()
        except OSError:
            return None
    found = shutil.which(command, path=_env_value(env, "PATH") or os.environ.get("PATH"))
    return Path(found).resolve() if found else None


def _allowed_python_helpers() -> set[Path]:
    support = _repo_root() / "scripts" / "test_support"
    return {
        (support / "isolated_pytest.py").resolve(),
        (support / "isolated_child.py").resolve(),
    }


def _block(message: str) -> None:
    _record(message)
    raise RuntimeError(message)


def _is_network_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith((
        "http://", "https://", "ftp://", "ftps://", "s3://",
        "gs://", "oss://", "rtmp://", "rtsp://", "tcp://", "udp://",
    ))


def _allow_python_subprocess(parts: Sequence[str], env: object | None) -> None:
    resolved = _resolve_executable(parts[0], env)
    if resolved != Path(sys.executable).resolve():
        _block(f"subprocess blocked untrusted Python executable: {parts[0]}")
    if len(parts) < 4 or parts[1] != "-S" or _resolve_path(parts[2]) not in _allowed_python_helpers():
        _block("Python subprocess must use exact -S scripts/test_support/isolated_child.py prefix")
    if _env_value(env, "LUMENX_TEST_ISOLATED") != "1":
        _block("Python subprocess missing isolated_child environment")
    if _env_value(env, "LUMENX_REPO_ROOT") != str(_repo_root()):
        _block("Python subprocess cannot change repository root")
    _check_path(_env_value(env, "LUMENX_TEST_ISOLATION_ROOT"), write=True, operation="child isolation root")


_MEDIA_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,lavfi,image2,png_pipe,jpeg_pipe,webp_pipe,bmp_pipe,gif,mp3,wav,ogg,flac,aac"
_MEDIA_FORMATS_WITH_CONCAT = f"{_MEDIA_FORMATS},concat"
_SAFE_PROTOCOLS = {"file"}
_SAFE_FORMATS = set(_MEDIA_FORMATS.split(","))
_SAFE_FORMATS_WITH_CONCAT = set(_MEDIA_FORMATS_WITH_CONCAT.split(","))
_MEDIA_FILE_SUFFIXES = {
    ".mp4", ".mov", ".m4a", ".3gp", ".3g2", ".mj2", ".webm", ".mkv",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
    ".mp3", ".wav", ".ogg", ".flac", ".aac",
}


def _ffmpeg_input_format(parts: Sequence[str], input_index: int) -> str | None:
    start = 1
    for index in range(input_index - 1, 0, -1):
        if parts[index] == "-i":
            start = index + 2
            break
    fmt = None
    index = start
    while index < input_index:
        if parts[index] == "-f" and index + 1 < input_index:
            fmt = parts[index + 1].lower()
            index += 2
            continue
        index += 1
    return fmt


def _inside_write_root(path: Path) -> bool:
    return _is_inside(path.resolve(), _write_roots())


def _reject_nested_concat_file(path: Path, *, operation: str) -> None:
    try:
        with _REAL_OPEN(path, "rb") as handle:
            header = handle.read(128).lstrip()
    except OSError as exc:
        _block(f"{operation} unreadable: {type(exc).__name__}")
    if header.startswith(b"ffconcat"):
        _block(f"{operation} cannot reference nested concat media: {path}")


def _validate_media_file(path: Path, *, operation: str, reject_nested_concat: bool = False) -> None:
    resolved = path.resolve()
    if not _inside_write_root(resolved):
        _block(f"{operation} outside isolated root: {path}")
    if path.is_symlink() or resolved.is_symlink():
        _block(f"{operation} cannot use symlinked media: {path}")
    if not resolved.exists():
        _block(f"{operation} is missing: {path}")
    if not resolved.is_file():
        _block(f"{operation} is not a file: {path}")
    if resolved.suffix.lower() not in _MEDIA_FILE_SUFFIXES:
        _block(f"{operation} has unsupported file type: {path}")
    if reject_nested_concat:
        _reject_nested_concat_file(resolved, operation=operation)


def _validate_concat_manifest_value(value: str) -> None:
    if any(character in value for character in ("\x00", "\\", "'")):
        _block(f"ffmpeg concat manifest has unsupported path token: {value!r}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _block(f"ffmpeg concat manifest has unsupported path token: {value!r}")


def _validate_concat_manifest(path_value: str) -> None:
    path = _resolve_path(path_value)
    if path is None or not _inside_write_root(path):
        _block(f"ffmpeg concat manifest outside isolated root: {path_value}")
    if path.is_symlink():
        _block(f"ffmpeg concat manifest cannot be a symlink: {path_value}")
    try:
        with _REAL_OPEN(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        _block(f"ffmpeg concat manifest unreadable: {type(exc).__name__}")
    if not lines or not any(line.strip() for line in lines):
        _block("ffmpeg concat manifest is empty")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not (stripped.startswith("file '") and stripped.endswith("'")):
            _block(f"ffmpeg concat manifest has unsupported line: {stripped!r}")
        value = stripped[len("file '"):-1]
        _validate_concat_manifest_value(value)
        if _is_network_url(value) or value.startswith(("file:", "concat:", "crypto:", "data:", "pipe:")):
            _block(f"ffmpeg concat manifest blocked protocol: {value}")
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        resolved = candidate.resolve()
        if candidate.is_symlink() or not _inside_write_root(resolved):
            _block(f"ffmpeg concat manifest references outside isolated root: {value}")
        _validate_media_file(resolved, operation="ffmpeg concat manifest referenced media", reject_nested_concat=True)


def _ffmpeg_format_whitelist(parts: Sequence[str], input_index: int) -> str:
    return _MEDIA_FORMATS_WITH_CONCAT if _ffmpeg_input_format(parts, input_index) == "concat" else _MEDIA_FORMATS


def _list_option_values(parts: Sequence[str], option: str) -> list[tuple[int, str]]:
    values: list[tuple[int, str]] = []
    for index, value in enumerate(parts[:-1]):
        if value == option:
            values.append((index, parts[index + 1]))
    return values


def _has_ffmpeg_limits(parts: Sequence[str]) -> bool:
    return "-protocol_whitelist" in parts or "-format_whitelist" in parts


def _with_injected_ffmpeg_limits(parts: Sequence[str]) -> list[str]:
    scoped: list[str] = [parts[0]]
    for index, part in enumerate(parts[1:], start=1):
        if part == "-i":
            scoped.extend(["-protocol_whitelist", "file", "-format_whitelist", _ffmpeg_format_whitelist(parts, index)])
        scoped.append(part)
    if "-i" not in parts:
        scoped = [parts[0], "-protocol_whitelist", "file", "-format_whitelist", _MEDIA_FORMATS, *parts[1:]]
    return scoped


def _csv_set(value: str, *, option: str) -> set[str]:
    items = value.split(",")
    if not items or any(not item for item in items):
        _block(f"ffmpeg {option} has an empty entry")
    lowered = {item.lower() for item in items}
    if len(lowered) != len(items):
        _block(f"ffmpeg {option} has duplicate entries")
    return lowered


def _validate_explicit_ffmpeg_limits(parts: Sequence[str]) -> None:
    protocol_values = _list_option_values(parts, "-protocol_whitelist")
    format_values = _list_option_values(parts, "-format_whitelist")
    input_count = sum(1 for value in parts if value == "-i")
    expected = input_count or 1
    if len(protocol_values) != expected or len(format_values) != expected:
        _block("ffmpeg requires explicit restricted protocols and formats")
    for _index, value in protocol_values:
        protocols = _csv_set(value, option="protocol_whitelist")
        if not protocols <= _SAFE_PROTOCOLS:
            _block("ffmpeg protocol whitelist exceeds isolated safe subset")
    for _index, value in format_values:
        formats = _csv_set(value, option="format_whitelist")
        allowed = _SAFE_FORMATS_WITH_CONCAT if "concat" in formats else _SAFE_FORMATS
        if not formats <= allowed:
            _block("ffmpeg format whitelist exceeds isolated safe subset")
        if "concat" in formats and "-f" not in parts:
            _block("ffmpeg concat whitelist requires explicit concat input format")
    for index, value in enumerate(parts):
        if value == "-i":
            prefix = list(parts[max(1, index - 4):index])
            if len(prefix) != 4 or prefix[:3] != ["-protocol_whitelist", "file", "-format_whitelist"]:
                _block("ffmpeg requires restricted protocols and formats before each input")
            formats = _csv_set(prefix[3], option="format_whitelist")
            if _ffmpeg_input_format(parts, index) == "concat":
                if "concat" not in formats:
                    _block("ffmpeg concat input requires concat demuxer whitelist")
            elif "concat" in formats:
                _block("ffmpeg non-concat input cannot whitelist concat demuxer")
    if input_count == 0 and (len(parts) < 5 or parts[1:3] != ["-protocol_whitelist", "file"] or parts[3] != "-format_whitelist"):
        _block("ffmpeg requires restricted protocols and formats")


def _allow_ffmpeg_subprocess(parts: Sequence[str], env: object | None) -> None:
    name = Path(parts[0]).name.lower()
    expected_var = "LUMENX_TEST_FFMPEG" if name == "ffmpeg" else "LUMENX_TEST_FFPROBE"
    expected = _env_value(None, expected_var)
    if not expected:
        _block(f"subprocess blocked {name} because {expected_var} is unset")
    resolved = _resolve_executable(parts[0], env)
    if resolved != Path(expected).resolve():
        _block(f"subprocess blocked untrusted {name} executable: {parts[0]}")
    if _has_ffmpeg_limits(parts):
        _validate_explicit_ffmpeg_limits(parts)

    input_indexes = [index for index, value in enumerate(parts) if value == "-i" and index + 1 < len(parts)]
    if name == "ffmpeg":
        for input_index in input_indexes:
            value = parts[input_index + 1]
            fmt = _ffmpeg_input_format(parts, input_index)
            if _is_network_url(value):
                _block(f"subprocess blocked ffmpeg network URL: {value}")
            if fmt == "lavfi":
                pass
            elif fmt == "concat":
                _validate_concat_manifest(value)
            elif _looks_like_path(value):
                path = _resolve_path(value)
                if path is None:
                    _block(f"ffmpeg unsupported ffmpeg input: {value}")
                _validate_media_file(path, operation="ffmpeg unsupported ffmpeg input")
            else:
                _block(f"ffmpeg unsupported ffmpeg input: {value}")
    elif name == "ffprobe" and input_indexes:
        for input_index in input_indexes:
            if _ffmpeg_input_format(parts, input_index) == "concat":
                _block("ffprobe concat inputs are not supported by the isolated harness")
            value = parts[input_index + 1]
            if _is_network_url(value):
                _block(f"subprocess blocked ffmpeg network URL: {value}")
            if _looks_like_path(value):
                path = _resolve_path(value)
                if path is None:
                    _block(f"ffprobe unsupported input: {value}")
                _validate_media_file(path, operation="ffprobe input")

    for arg in parts[1:]:
        # Absolute filesystem paths are not filter graphs. Frozen-media and
        # isolation-root names can legitimately contain a filter substring.
        # They still pass the containment check below; do not exempt merely
        # path-like expressions such as "amovie=/outside/file.wav".
        if not Path(arg).is_absolute() and any(
            token in arg.lower()
            for token in ("movie=", "amovie=", "zmq", "sendcmd", "drawtext", "subtitles=", "ass=")
        ):
            _block("ffmpeg indirect filter input blocked")
        if _is_network_url(arg):
            _block(f"subprocess blocked ffmpeg network URL: {arg}")
        if _looks_like_path(arg) and not arg.startswith("-"):
            _check_path(arg, write=True, operation="ffmpeg media outside isolated root")
        if arg.startswith(("file:", "concat:", "crypto:", "data:", "pipe:", "movie=")):
            _block("ffmpeg indirect media protocol blocked")


def _allow_subprocess(args: object, env: object | None = None) -> None:
    parts = _command_sequence(args)
    if not parts:
        _block("subprocess blocked empty command")
    if os.environ.get("LUMENX_TEST_PHASE") == "collecting":
        _block(f"subprocess blocked during pytest collection phase: {parts[0]}")
    executable = Path(parts[0]).name.lower()
    if executable in {"python", "python3", "python3.11", Path(sys.executable).name.lower()}:
        _allow_python_subprocess(parts, env)
    elif executable in {"ffmpeg", "ffprobe"}:
        _allow_ffmpeg_subprocess(parts, env)
    else:
        _block(f"subprocess blocked non-allowlisted command: {parts[0]}")
    for arg in parts[1:]:
        if arg.startswith("-") or _is_network_url(arg):
            continue
        if _looks_like_path(arg):
            resolved = _resolve_path(arg)
            if resolved is not None and not (
                _is_inside(resolved, _read_roots()) or _is_inside(resolved, _write_roots())
            ):
                _block(f"subprocess blocked path outside isolation: {arg}")


def _guarded_popen(args, *popen_args, **kwargs):
    if kwargs.get("shell"):
        _block("shell subprocess blocked")
    _check_path(kwargs.get("cwd") or Path.cwd(), write=True, operation="subprocess cwd")
    env = kwargs.get("env")
    if kwargs.get("cwd") is not None and Path(kwargs["cwd"]).resolve() != Path.cwd():
        _block("child cwd must match isolated parent cwd")
    _allow_subprocess(args, env=env)
    if env is not None and env.get("LUMENX_TEST_ISOLATED") != "1":
        _block("subprocess blocked child without LUMENX_TEST_ISOLATED=1")
    parts = _command_sequence(args)
    if kwargs.get("executable") is not None and _resolve_executable(kwargs["executable"], env) != _resolve_executable(parts[0], env):
        _block("subprocess executable override blocked")
    if Path(parts[0]).name.lower() in {"ffmpeg", "ffprobe"}:
        if _has_ffmpeg_limits(parts):
            _validate_explicit_ffmpeg_limits(parts)
        else:
            args = _with_injected_ffmpeg_limits(parts)
            parts = _command_sequence(args)
            _validate_explicit_ffmpeg_limits(parts)
    return _REAL_POPEN(args, *popen_args, **kwargs)


def _guarded_run(args, *run_args, **kwargs):
    if kwargs.get("shell"):
        _block("shell subprocess blocked")
    _check_path(kwargs.get("cwd") or Path.cwd(), write=True, operation="subprocess cwd")
    env = kwargs.get("env")
    if kwargs.get("cwd") is not None and Path(kwargs["cwd"]).resolve() != Path.cwd():
        _block("child cwd must match isolated parent cwd")
    _allow_subprocess(args, env=env)
    if env is not None and env.get("LUMENX_TEST_ISOLATED") != "1":
        _block("subprocess.run blocked child without LUMENX_TEST_ISOLATED=1")
    return _REAL_RUN(args, *run_args, **kwargs)


def _audit_hook(event: str, args: tuple[object, ...]) -> None:
    if event == "open" and args:
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        writes = _mode_writes(args[1]) or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        _check_path(args[0], write=writes, operation="audit.open")
    elif event == "subprocess.Popen":
        _check_path(args[2] or Path.cwd(), write=True, operation="subprocess cwd")
        parts = _command_sequence(args[1] or [args[0]])
        if _resolve_executable(str(args[0]), args[3]) != _resolve_executable(parts[0], args[3]):
            _block("subprocess executable override blocked")
        _allow_subprocess(parts, env=args[3])
        if Path(parts[0]).name.lower() in {"ffmpeg", "ffprobe"}:
            _validate_explicit_ffmpeg_limits(parts)
    elif event in {"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        _block(f"{event} blocked by test isolation")
    elif event in {"os.rename", "os.replace"}:
        _check_path(_at(args[0], args[2]), write=True, operation=f"audit.{event} source")
        _check_path(_at(args[1], args[3]), write=True, operation=f"audit.{event} destination")
    elif event in {"os.remove", "os.rmdir"}:
        _check_path(_at(args[0], args[1]), write=True, operation=f"audit.{event}")
    elif event in {"os.mkdir", "os.chmod"}:
        _check_path(_at(args[0], args[2]), write=True, operation=f"audit.{event}")
    elif event == "os.chdir":
        _check_path(args[0], write=True, operation="audit.os.chdir")
    elif event == "os.symlink":
        destination = Path(_at(args[1], args[2]))
        source = Path(os.fsdecode(args[0]))
        if not source.is_absolute():
            source = destination.parent / source
        _check_path(source, write=True, operation="symlink target")
        _check_path(destination, write=True, operation="symlink destination")
    elif event == "os.link":
        _check_path(_at(args[0], args[2]), write=True, operation="link source")
        _check_path(_at(args[1], args[3]), write=True, operation="link destination")
    elif event in {"os.system", "os.exec", "os.posix_spawn"}:
        _block(f"{event} blocked by test isolation")


def install() -> None:
    global _INSTALLED, _READ_ROOTS, _WRITE_ROOTS, _REPO_ROOT
    if _INSTALLED:
        return
    if os.environ.get("LUMENX_TEST_ISOLATED") != "1":
        raise RuntimeError("Install isolation via scripts/run_tests_isolated.py")

    _READ_ROOTS = tuple(_read_roots())
    _WRITE_ROOTS = tuple(_write_roots())
    _REPO_ROOT = _repo_root()
    builtins.open = _guarded_open
    io.open = _guarded_io_open
    os.open = _guarded_os_open
    os.makedirs = _guarded_makedirs
    os.mkdir = _guarded_mkdir
    os.replace = _guarded_replace
    os.rename = _guarded_rename
    os.remove = _guarded_remove
    os.unlink = _guarded_unlink
    os.rmdir = _guarded_rmdir
    os.chdir = _guarded_chdir
    # Network is unavailable in this harness. Prevent urllib3 import-time IPv6
    # availability probes from attempting a real loopback bind. All bind/connect
    # operations remain guarded; this does not waive a recorded violation.
    socket.has_ipv6 = False
    socket.socket = _GuardedSocket
    socket.create_connection = _guarded_create_connection
    subprocess.Popen = _guarded_popen
    subprocess.run = _guarded_run
    mimetypes.knownfiles = []
    mimetypes.init(files=[])
    sys.addaudithook(_audit_hook)
    _INSTALLED = True


def _exit_if_violations() -> None:
    if os.environ.get("LUMENX_TEST_FAIL_ON_GUARD_VIOLATIONS") != "1":
        return
    if not _VIOLATIONS:
        return
    for item in _VIOLATIONS:
        print(f"LUMENX_TEST_ISOLATION_VIOLATION: {item}", file=sys.stderr)
    os._exit(97)


atexit.register(_exit_if_violations)
