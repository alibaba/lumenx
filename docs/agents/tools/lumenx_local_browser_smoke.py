#!/usr/bin/env python3
"""Local real-backend/static-shell smoke launcher for P9 browser checks.

This launcher starts a real LumenX FastAPI app on loopback, mounts the existing
static shell read-only at /static, and writes bounded evidence for a later
browser driver. It intentionally avoids user config, .env loading, provider
credentials, provider recovery, bytecode writes, and non-loopback sockets.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import signal
import site
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[3]
SENSITIVE_ENV_KEYS = {
    "DASHSCOPE_API_KEY",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "OSS_BUCKET_NAME",
    "OSS_ENDPOINT",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
    "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY",
    "VIDU_API_KEY",
    "PIXVERSE_API_KEY",
    "MULERUN_TOKEN",
    "MULERUN_API_KEY",
    "OPENAI_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
}
ALLOW_ENV_KEYS = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM", "TZ", "HOME"}
STATIC_EXCLUDED_DIR_NAMES = {".cache", "cache", "__pycache__"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch safe local P9 LumenX browser smoke backend")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--static-root", type=Path, default=REPO_ROOT / "static")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--frontend-freeze-commit", required=True)
    parser.add_argument("--backend-source-commit", required=True)
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="Python executable whose virtualenv site-packages should be added when running with -S.",
    )
    return parser.parse_args()


def validate_loopback_literal(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise SystemExit(f"host must be a literal loopback IP, got {host!r}") from exc
    if not address.is_loopback:
        raise SystemExit(f"host must be loopback, got {host!r}")
    return host


def sanitize_environment(data_root: Path) -> None:
    preserved = {key: os.environ[key] for key in ALLOW_ENV_KEYS if key in os.environ}
    os.environ.clear()
    os.environ.update(preserved)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LUMENX_P9_SMOKE"] = "1"
    owned_dirs = {
        "LUMENX_LOG_DIR": data_root / "logs",
        "XDG_CONFIG_HOME": data_root / "xdg-config",
        "XDG_CACHE_HOME": data_root / "xdg-cache",
        "TMPDIR": data_root / "tmp",
    }
    for key, path in owned_dirs.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    for key in SENSITIVE_ENV_KEYS:
        os.environ.pop(key, None)


def candidate_prefixes(python_executable: Path | None) -> list[Path]:
    prefixes: list[Path] = []
    if python_executable is not None:
        executable = python_executable.expanduser().absolute()
        if not executable.is_file():
            raise SystemExit(f"--python does not exist: {executable}")
        if executable.parent.name in {"bin", "Scripts"}:
            prefixes.append(executable.parent.parent)
        prefixes.append(executable.parent)
    prefixes.append(Path(sys.prefix))
    unique: list[Path] = []
    for prefix in prefixes:
        if prefix not in unique:
            unique.append(prefix)
    return unique


def bootstrap_paths(python_executable: Path | None) -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for prefix in candidate_prefixes(python_executable):
        for package_path in site.getsitepackages(prefixes=[str(prefix)]):
            if Path(package_path).is_dir() and package_path not in sys.path:
                sys.path.append(package_path)


def ensure_owned_empty_data_root(data_root: Path) -> Path:
    root = data_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    existing = [entry.name for entry in root.iterdir()]
    if existing:
        raise SystemExit(f"data root must be empty before launch: {root}")
    return root


def validate_static_source(static_root: Path) -> Path:
    root = static_root.expanduser().resolve()
    if not root.is_dir() or not (root / "index.html").is_file():
        raise SystemExit(f"static shell missing: {root / 'index.html'}")
    return root


def freeze_static_snapshot(static_source: Path, data_root: Path, frontend_commit: str) -> tuple[Path, dict[str, Any]]:
    snapshot_root = data_root / "static"
    snapshot_root.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for source in sorted(static_source.rglob("*")):
        relative = source.relative_to(static_source)
        if any(part in STATIC_EXCLUDED_DIR_NAMES for part in relative.parts):
            continue
        if source.is_symlink():
            raise SystemExit(f"static snapshot refuses symlink: {source}")
        if source.is_dir():
            continue
        if not source.is_file():
            raise SystemExit(f"static snapshot refuses non-regular file: {source}")
        target = snapshot_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        digest = sha256_file(target)
        size = target.stat().st_size
        total_bytes += size
        files.append({"path": relative.as_posix(), "sha256": digest, "size": size})
    if not (snapshot_root / "index.html").is_file():
        raise SystemExit("static snapshot missing index.html")
    manifest = {
        "source_root": str(static_source),
        "mount_root": str(snapshot_root),
        "frontend_freeze_commit": frontend_commit,
        "association": "caller-supplied verified build; not independently inferred from bundle",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }
    return snapshot_root, manifest


def choose_port(host: str, requested: int) -> int:
    if requested:
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(args: list[str]) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


class Evidence:
    def __init__(self, path: Path, payload: dict[str, Any]):
        self.path = path
        self.payload = payload
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write()

    def write(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)

    def update(self, **values: Any) -> None:
        with self.lock:
            self.payload.update(values)
        self.write()

    def append_blocked_socket(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.payload.setdefault("blocked_sockets", []).append(event)
        self.write()


def is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_loopback for info in infos)


def install_socket_guard(evidence: Evidence) -> None:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check(address: Any) -> None:
        host = None
        if isinstance(address, tuple) and len(address) >= 2:
            host = str(address[0])
        if host is not None and is_loopback_host(host):
            return
        event = {"address": repr(address), "time": time.time(), "pid": os.getpid()}
        evidence.append_blocked_socket(event)
        raise RuntimeError("P9 smoke blocks outbound network")

    def guarded_connect(self: socket.socket, address: Any):
        check(address)
        return original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any):
        check(address)
        return original_connect_ex(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[assignment]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[assignment]


def build_initial_evidence(
    data_root: Path,
    static_root: Path,
    static_manifest: dict[str, Any],
    host: str,
    port: int,
    backend_commit: str,
    backend_hashes: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "lumenx.p9.local_browser_smoke",
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}/static/index.html",
        "health_url": f"http://{host}:{port}/health",
        "data_root": str(data_root),
        "cwd": str(Path.cwd()),
        "output_root": str(data_root / "output"),
        "static_root": str(static_root),
        "static_manifest": static_manifest,
        "repo_root": str(REPO_ROOT),
        "backend_source_fixed_commit": backend_commit,
        "backend_source_hashes": backend_hashes,
        "started_at": time.time(),
        "git_head": git_output(["rev-parse", "HEAD"]),
        "git_status_short": git_output(["status", "--short", "--untracked-files=all"]),
        "blocked_sockets": [],
        "checks": {},
        "environment": {
            "python_dotenv_disabled": os.environ.get("PYTHON_DOTENV_DISABLED"),
            "home": os.environ.get("HOME"),
            "lumenx_log_dir": os.environ.get("LUMENX_LOG_DIR"),
            "tmpdir": os.environ.get("TMPDIR"),
            "xdg_config_home": os.environ.get("XDG_CONFIG_HOME"),
            "xdg_cache_home": os.environ.get("XDG_CACHE_HOME"),
            "sensitive_present": {key: key in os.environ for key in sorted(SENSITIVE_ENV_KEYS)},
        },
    }


def create_smoke_app(data_root: Path, static_root: Path):
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app

    app = create_app(
        data_dir=data_root / "output",
        user_data_dir=data_root / "user-config",
        load_config=False,
        setup_logs=False,
        recover_orphan_tasks=False,
        warmup_demucs=False,
        access_mode="local",
    )
    app.mount("/static", StaticFiles(directory=str(static_root), html=True), name="static")
    return app


def wait_for_health(host: str, port: int, timeout: float = 15.0) -> dict[str, Any]:
    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                payload = json.loads(response.read(65536).decode("utf-8"))
            if payload.get("ok") is True and payload.get("service") == "lumenx-core":
                return payload
            last_error = RuntimeError(f"unexpected health payload: {payload!r}")
        except Exception as exc:  # noqa: BLE001 - surfaced in evidence
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"health check failed: {last_error}")


def check_static(host: str, port: int) -> dict[str, Any]:
    url = f"http://{host}:{port}/static/index.html"
    with urllib.request.urlopen(url, timeout=3.0) as response:
        body = response.read(8192)
        status = getattr(response, "status", getattr(response, "code", None))
        content_type = response.headers.get("content-type", "")
    if status != 200 or (b"<html" not in body.lower() and b"<!doctype" not in body.lower()):
        raise RuntimeError(f"static shell check failed: status={status} content_type={content_type}")
    return {"status": status, "content_type": content_type, "prefix_sha256": hashlib.sha256(body).hexdigest()}


def main() -> None:
    args = parse_args()
    frontend_commit = git_output(["rev-parse", "--verify", args.frontend_freeze_commit + "^{commit}"])
    backend_commit = git_output(["rev-parse", "--verify", args.backend_source_commit + "^{commit}"])
    if not frontend_commit or not backend_commit:
        raise SystemExit("both source references must resolve to fixed commits")
    backend_hashes = {}
    paths = git_output(["ls-tree", "-r", "--name-only", backend_commit, "src"])
    for relative in (paths or "").splitlines():
        if not relative.endswith(".py"):
            continue
        committed = subprocess.check_output(["git", "show", f"{backend_commit}:{relative}"], cwd=REPO_ROOT)
        digest = hashlib.sha256(committed).hexdigest()
        if sha256_file(REPO_ROOT / relative) != digest:
            raise SystemExit(f"backend source differs from supplied commit: {relative}")
        backend_hashes[relative] = digest
    if not backend_hashes:
        raise SystemExit("backend source reference has no Python files")
    current_paths = git_output(["ls-files", "--cached", "--others", "--exclude-standard", "src"])
    current_python = {p for p in (current_paths or "").splitlines() if p.endswith(".py")}
    if current_python != set(backend_hashes):
        raise SystemExit("backend Python file set differs from supplied commit")
    host = validate_loopback_literal(args.host)
    data_root = ensure_owned_empty_data_root(args.data_root)
    sanitize_environment(data_root)
    os.chdir(data_root)
    bootstrap_paths(args.python)
    static_source = validate_static_source(args.static_root)
    static_root, static_manifest = freeze_static_snapshot(static_source, data_root, frontend_commit)
    port = choose_port(host, args.port)
    evidence = Evidence(
        args.evidence.expanduser().resolve(),
        build_initial_evidence(data_root, static_root, static_manifest, host, port, backend_commit, backend_hashes),
    )
    install_socket_guard(evidence)

    import uvicorn

    app = create_smoke_app(data_root, static_root)
    config = uvicorn.Config(app, host=host, port=port, reload=False, log_level="info", access_log=True)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def handle_stop(signum: int, _frame: Any) -> None:
        evidence.update(stopping_signal=signum, stopping_at=time.time())
        server.should_exit = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        health = wait_for_health(host, port)
        static = check_static(host, port)
        evidence.update(
            ready=True,
            ready_at=time.time(),
            checks={"health": health, "static": static},
        )
        print(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "port": port,
                    "url": f"http://{host}:{port}/static/index.html",
                    "health_url": f"http://{host}:{port}/health",
                    "data_root": str(data_root),
                    "evidence": str(evidence.path),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        while thread.is_alive() and not server.should_exit:
            time.sleep(0.5)
    except BaseException as exc:
        evidence.update(ready=False, failed_at=time.time(), error=repr(exc))
        server.should_exit = True
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        evidence.update(stopped_at=time.time(), stopped=not thread.is_alive())


if __name__ == "__main__":
    main()
