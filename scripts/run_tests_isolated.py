#!/usr/bin/env python3
"""Run LumenX backend pytest under a collection-time isolation guard."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SECRET_MARKERS = (
    "ALIBABA",
    "DASHSCOPE",
    "OPENAI",
    "KLING",
    "VIDU",
    "OSS",
    "AWS",
    "MULE",
    "HAPPY",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "ACCESS_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)

SAFE_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TZ",
    "SYSTEMROOT",
    "WINDIR",
    "ProgramFiles",
    "ProgramFiles(x86)",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _site_packages_for(python: Path) -> list[Path]:
    candidates: list[Path] = []
    python_path = python.expanduser()
    venv = python_path.parents[1] if len(python_path.parents) > 1 else None
    if venv is not None:
        lib = venv / "lib"
        if lib.exists():
            candidates.extend(sorted(lib.glob("python*/site-packages")))
        win_site = venv / "Lib" / "site-packages"
        if win_site.exists():
            candidates.append(win_site)
    return [path.resolve() for path in candidates if path.exists()]


def _sanitized_env(repo_root: Path, tmp_root: Path, python: Path, sentinel_file: Path | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if key in SAFE_ENV_ALLOWLIST and not any(marker in upper for marker in SECRET_MARKERS):
            env[key] = value

    data_dir = tmp_root / "data"
    log_dir = tmp_root / "logs"
    config_dir = tmp_root / "config"
    cache_dir = tmp_root / "cache"
    for path in (data_dir, log_dir, config_dir, cache_dir):
        path.mkdir(parents=True, exist_ok=True)

    site_packages = _site_packages_for(python)
    read_roots = [
        repo_root,
        tmp_root,
        Path(sys.base_prefix).resolve(),
        Path(sys.prefix).resolve(),
        *site_packages,
    ]

    env.update(
        {
            "LUMENX_TEST_ISOLATED": "1",
            "LUMENX_TEST_FAIL_ON_GUARD_VIOLATIONS": "1",
            "LUMENX_REPO_ROOT": str(repo_root),
            "LUMENX_TEST_ISOLATION_ROOT": str(tmp_root),
            "LUMENX_TEST_READ_ROOTS": os.pathsep.join(str(path) for path in read_roots),
            "LUMENX_TEST_WRITE_ROOTS": str(tmp_root),
            "LUMENX_TEST_SITE_PACKAGES": os.pathsep.join(str(path) for path in site_packages),
            "LUMENX_TEST_SENTINEL_FILE": str(sentinel_file) if sentinel_file else "",
            "LUMENX_DATA_DIR": str(data_dir),
            "LUMENX_LOG_DIR": str(log_dir),
            "LUMENX_OUTPUT_DIR": str(tmp_root / "output"),
            "XDG_CONFIG_HOME": str(config_dir),
            "XDG_CACHE_HOME": str(cache_dir),
            "TMPDIR": str(tmp_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONNOUSERSITE": "1",
            "NO_COLOR": "1",
        }
    )

    ffmpeg = shutil.which("ffmpeg", path=env.get("PATH"))
    ffprobe = shutil.which("ffprobe", path=env.get("PATH"))
    if ffmpeg:
        env["LUMENX_TEST_FFMPEG"] = ffmpeg
    if ffprobe:
        env["LUMENX_TEST_FFPROBE"] = ffprobe
    return env


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-temp", action="store_true", help="keep the isolation root")
    parser.add_argument("--tmp-root", type=Path, help="use a specific temporary root")
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python interpreter with pytest installed",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def _normalize_pytest_args(repo_root: Path, pytest_args: list[str]) -> list[str]:
    normalized: list[str] = []
    for arg in pytest_args:
        if arg.startswith("-"):
            normalized.append(arg)
            continue
        filename, *node = arg.split("::")
        candidate = repo_root / filename
        normalized.append("::".join([str(candidate), *node]) if candidate.exists() else arg)
    return normalized


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    repo_root = _repo_root()
    python = args.python.expanduser()
    pytest_args = args.pytest_args or ["tests", "src/apps/comic_gen/test_shared_asset_pool.py", "src/apps/comic_gen/test_shared_asset_channels.py"]
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    pytest_args = [
        "--rootdir",
        str(repo_root),
        "-p",
        "no:cacheprovider",
        *_normalize_pytest_args(repo_root, pytest_args),
    ]

    if args.tmp_root is not None:
        parent_tmp_root = args.tmp_root.expanduser().resolve()
        if parent_tmp_root.exists():
            raise ValueError("--tmp-root must be a new directory, never an existing data root")
        parent_tmp_root.mkdir(parents=True)
        tmp_root = Path(tempfile.mkdtemp(prefix="lumenx-pytest-run-", dir=parent_tmp_root)).resolve()
    else:
        tmp_root = Path(tempfile.mkdtemp(prefix="lumenx-pytest-")).resolve()
    owned_temp = True
    tmp_root.mkdir(parents=True, exist_ok=True)

    sentinel_dir = Path(tempfile.mkdtemp(prefix="lumenx-pytest-sentinel-"))
    sentinel_file = sentinel_dir / "outside-isolation.txt"
    sentinel_file.write_text("sentinel-owned-by-runner", encoding="utf-8")

    env = _sanitized_env(repo_root, tmp_root, python, sentinel_file=sentinel_file)
    cmd = [
        str(python),
        "-S",
        str(repo_root / "scripts" / "test_support" / "isolated_pytest.py"),
        *pytest_args,
    ]
    print(f"lumenx isolated pytest root: {tmp_root}", file=sys.stderr)
    try:
        result = subprocess.run(cmd, cwd=tmp_root, env=env)
        return result.returncode
    finally:
        content = sentinel_file.read_text(encoding="utf-8") if sentinel_file.is_file() else None
        if content != "sentinel-owned-by-runner":
            print("lumenx isolated pytest sentinel was modified or deleted", file=sys.stderr)
            shutil.rmtree(sentinel_dir, ignore_errors=True)
            if not args.keep_temp:
                shutil.rmtree(tmp_root, ignore_errors=True)
            return 98
        shutil.rmtree(sentinel_dir, ignore_errors=True)
        if owned_temp and not args.keep_temp:
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
