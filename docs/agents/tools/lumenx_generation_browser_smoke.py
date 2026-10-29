#!/usr/bin/env python3
"""Local Atelier generation browser smoke launcher with a fake Wan provider.

This harness starts the real LumenX FastAPI app on loopback, mounts a frozen
static shell read-only at /static, and injects a local fake Wan provider during
app lifespan. Atelier admission, ledger, execution, dispatcher, Core artifact
proof, and no-overwrite media publication remain real. The fake provider never
connects to a real provider, never reads user config, and never exposes secrets.
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager, contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import site
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable, ContextManager, Iterable, Literal
import urllib.request
import uuid
import wave

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
    "MULEROUTER_API_KEY",
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
SMOKE_SCOPE_BASE_URL = "https://dashscope.aliyuncs.com"
SMOKE_WORKSPACE_BASE_URL = "https://smoke-workspace.cn-beijing.maas.aliyuncs.com"
SMOKE_WORKSPACE_REGION = "cn-beijing"
SMOKE_WORKSPACE_ID = "smoke-workspace"
SMOKE_SECRET = "fake"
FAKE_MODEL_PLANS = ("disabled", "read", "canvas", "generation")
FakeModelPlan = Literal["disabled", "read", "canvas", "generation"]
_FAKE_UPLOAD_PATCH_LOCK = threading.Lock()
_ACTIVE_FAKE_UPLOAD_PATCH = False
_FAKE_MEDIA_PATCH_LOCK = threading.Lock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch safe local LumenX generation browser smoke backend")
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
    parser.add_argument(
        "--fake-model-plan",
        choices=FAKE_MODEL_PLANS,
        default="disabled",
        help=(
            "Explicit browser-smoke test mode for Atelier model_adapter loops. "
            "When enabled, the real app/claim/settle/HTTP chain runs and only the external LLM chat transport is replaced."
        ),
    )
    parser.add_argument(
        "--fake-wan3", action="store_true",
        help="Explicitly add a fake Wan3 workspace runtime alongside classic Wan; no real provider I/O.",
    )
    parser.add_argument(
        "--fake-media", action="store_true",
        help="Replace image and built-in speech transport with synthetic local media; keep real admission and publication.",
    )
    parser.add_argument(
        "--fake-wan27-videoedit", action="store_true",
        help="Explicitly fake Wan 2.7 VideoEdit frozen-AV upload/runtime without enabling Wan3 workspace behavior.",
    )
    parser.add_argument(
        "--fake-happyhorse11", action="store_true",
        help="Explicitly fake HappyHorse 1.1 workspace runtime for Playground T2V/I2V/R2V without enabling Wan3 behavior.",
    )
    parser.add_argument(
        "--fake-happyhorse-videoedit", action="store_true",
        help="Explicitly fake HappyHorse 1.0 VideoEdit workspace runtime and AV upload without enabling Wan3 or HappyHorse 1.1 behavior.",
    )
    parser.add_argument(
        "--fake-kling", action="store_true",
        help="Explicitly fake Kling workspace runtime and image upload without enabling Wan3 or HappyHorse behavior.",
    )
    parser.add_argument(
        "--fake-vidu", action="store_true",
        help="Explicitly fake the four Vidu workspace runtimes and frozen image uploads.",
    )
    parser.add_argument(
        "--fake-pixverse", action="store_true",
        help="Explicitly fake the four existing PixVerse workspace runtimes and frozen image uploads.",
    )
    parser.add_argument(
        "--fake-seedance", action="store_true",
        help="Explicitly fake only the three standard Seedance HTTP video modes, without uploads or CLI.",
    )
    return parser.parse_args()


def validate_loopback_literal(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise SystemExit(f"host must be a literal loopback IP, got {host!r}") from exc
    if address.version != 4 or not address.is_loopback:
        raise SystemExit(f"host must be an IPv4 loopback address, got {host!r}")
    return host


def sanitize_environment(data_root: Path) -> None:
    preserved = {key: os.environ[key] for key in ALLOW_ENV_KEYS if key in os.environ}
    os.environ.clear()
    os.environ.update(preserved)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LUMENX_GENERATION_SMOKE"] = "1"
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

    def append_provider_event(self, kind: str, event: dict[str, Any]) -> None:
        with self.lock:
            provider = self.payload.setdefault("fake_provider", {})
            counts = provider.setdefault("counts", {"submit": 0, "query": 0, "fetch": 0})
            counts[kind] = int(counts.get(kind, 0)) + 1
            provider.setdefault("events", []).append({"kind": kind, "time": time.time(), **event})
        self.write()

    def append_model_event(self, kind: str, event: dict[str, Any]) -> None:
        with self.lock:
            planner = self.payload.setdefault("fake_model_planner", {})
            counts = planner.setdefault("counts", {"init": 0, "chat_result": 0})
            counts[kind] = int(counts.get(kind, 0)) + 1
            planner.setdefault("events", []).append({"kind": kind, "time": time.time(), **event})
        self.write()

    def append_upload_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            upload = self.payload.setdefault("fake_upload", {})
            counts = upload.setdefault("counts", {"upload": 0})
            counts["upload"] = int(counts.get("upload", 0)) + 1
            upload.setdefault("events", []).append({"time": time.time(), **event})
        self.write()

    def append_blocked_socket(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.payload.setdefault("blocked_sockets", []).append(event)
        self.write()

    def append_media_event(self, kind: str, event: dict[str, Any]) -> None:
        with self.lock:
            media = self.payload.setdefault("fake_media", {})
            counts = media.setdefault("counts", {"image": 0, "audio": 0})
            counts[kind] += 1
            media.setdefault("events", []).append({"kind": kind, "time": time.time(), **event})
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
        raise RuntimeError("generation smoke blocks outbound network")

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
    fake_model_plan: FakeModelPlan,
    fake_wan3: bool = False,
    fake_media: bool = False,
    fake_wan27_videoedit: bool = False,
    fake_happyhorse11: bool = False,
    fake_happyhorse_videoedit: bool = False,
    fake_kling: bool = False,
    fake_vidu: bool = False,
    fake_pixverse: bool = False,
    fake_seedance: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "lumenx.atelier_generation.local_browser_smoke",
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
        "tool_source_sha256": sha256_file(Path(__file__)),
        "backend_source_fixed_commit": backend_commit,
        "backend_source_hashes": backend_hashes,
        "started_at": time.time(),
        "git_head": git_output(["rev-parse", "HEAD"]),
        "git_status_short": git_output(["status", "--short", "--untracked-files=all"]),
        "blocked_sockets": [],
        "checks": {},
        "fake_provider": {"counts": {"submit": 0, "query": 0, "fetch": 0}, "events": []},
        "fake_wan3": fake_wan3,
        "fake_wan27_videoedit": fake_wan27_videoedit,
        "fake_happyhorse11": fake_happyhorse11,
        "fake_happyhorse_videoedit": fake_happyhorse_videoedit,
        "fake_kling": fake_kling,
        "fake_vidu": fake_vidu,
        "fake_pixverse": fake_pixverse,
        "fake_seedance": fake_seedance,
        "fake_media": {"enabled": fake_media, "counts": {"image": 0, "audio": 0}, "events": []},
        "fake_upload": {"counts": {"upload": 0}, "events": []},
        "fake_model_planner": {
            "enabled": fake_model_plan != "disabled",
            "plan": fake_model_plan,
            "counts": {"init": 0, "chat_result": 0},
            "events": [],
        },
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


class SmokeWanRuntimeSelector:
    """Compose existing injected selectors without changing product runtime rules."""

    def __init__(self, classic, workspace=None, workspace_model_ids: set[str] | None = None, seedance=None):
        from src.apps.comic_gen.api import _InjectedProviderRuntimeSelector

        self.seedance = seedance
        self.classic = _InjectedProviderRuntimeSelector(classic)
        self.workspace = _InjectedProviderRuntimeSelector(workspace)
        self.workspace_model_ids = set(workspace_model_ids or ())

    def classic_compatibility(self):
        return self.classic.classic_compatibility()

    def for_admission(self, *, provider_model_id: str, gateway: str):
        from src.models.provider_runtime import ProviderRuntimeBuildResult

        if gateway == "mulerouter":
            allowed = {"seedance-2.0-t2v", "seedance-2.0-i2v", "seedance-2.0-r2v"}
            if self.seedance is not None and provider_model_id in allowed:
                return ProviderRuntimeBuildResult(self.seedance)
            return ProviderRuntimeBuildResult(None, "unsupported_provider_model")
        if gateway == "dashscope_workspace":
            if provider_model_id not in self.workspace_model_ids:
                return ProviderRuntimeBuildResult(None, "unsupported_provider_model")
            return self.workspace.for_admission(provider_model_id=provider_model_id, gateway=gateway)
        if gateway == "dashscope" and provider_model_id in {"wan2.7-t2v", "wan2.7-r2v", "wan2.7-videoedit"}:
            return self.classic.classic_compatibility()
        return self.classic.for_admission(provider_model_id=provider_model_id, gateway=gateway)

    def for_scope(self, scope):
        if self.seedance is not None and scope == self.seedance.scope:
            from src.models.provider_runtime import ProviderRuntimeBuildResult

            return ProviderRuntimeBuildResult(self.seedance)
        result = self.classic.for_scope(scope)
        return result if result.runtime is not None else self.workspace.for_scope(scope)


def create_smoke_app(
    data_root: Path, static_root: Path, evidence: Evidence, *,
    fake_model_plan: FakeModelPlan = "disabled", fake_wan3: bool = False, fake_media: bool = False,
    fake_wan27_videoedit: bool = False, fake_happyhorse11: bool = False,
    fake_happyhorse_videoedit: bool = False, fake_kling: bool = False, fake_vidu: bool = False, fake_pixverse: bool = False, fake_seedance: bool = False,
):
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app
    from src.models.provider_runtime import ProviderRecoveryRuntime
    from src.models.provider_tasks import ProviderScope

    install_fake_model_planner(fake_model_plan, evidence)
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
    original_lifespan = app.router.lifespan_context
    provider_root = data_root / "fake-provider"

    @asynccontextmanager
    async def smoke_lifespan(target_app):
        global _ACTIVE_FAKE_UPLOAD_PATCH

        original_upload = None
        patched_upload = None
        if fake_wan3 or fake_wan27_videoedit or fake_happyhorse_videoedit or fake_kling or fake_vidu or fake_pixverse:
            from src.models import provider_upload

            with _FAKE_UPLOAD_PATCH_LOCK:
                if _ACTIVE_FAKE_UPLOAD_PATCH:
                    raise RuntimeError("fake provider temporary upload is already active")
                original_upload = provider_upload.upload_dashscope_temporary_media
                patched_upload = FakeDashScopeUploader(evidence)
                provider_upload.upload_dashscope_temporary_media = patched_upload
                _ACTIVE_FAKE_UPLOAD_PATCH = True
            evidence.update(fake_upload_injected_at=time.time())
        try:
            async with original_lifespan(target_app):
                scope = ProviderScope(
                    provider="dashscope",
                    backend="dashscope",
                    gateway="dashscope",
                    base_url=SMOKE_SCOPE_BASE_URL,
                    region="cn-beijing",
                )
                runtime = ProviderRecoveryRuntime(
                    scope=scope,
                    secret_resolver=lambda: SMOKE_SECRET,
                    provider=FakeWanProvider(evidence, provider_root),
                )
                selector = SmokeWanRuntimeSelector(runtime)
                scopes = {"classic": scope.to_dict()}
                workspace_model_ids: set[str] = set()
                if fake_wan3:
                    workspace_model_ids.update({"wan3.0-video", "wan3.0-video-prime"})
                if fake_happyhorse11:
                    workspace_model_ids.update({"happyhorse-1.1-t2v", "happyhorse-1.1-i2v", "happyhorse-1.1-r2v"})
                if fake_happyhorse_videoedit:
                    workspace_model_ids.add("happyhorse-1.0-video-edit")
                if fake_kling:
                    workspace_model_ids.update({
                        "kling/kling-v3-video-generation",
                        "kling/kling-v3-omni-video-generation",
                    })
                if fake_vidu:
                    workspace_model_ids.update({
                        "vidu/viduq3-pro_img2video", "vidu/viduq3-turbo_img2video",
                        "vidu/viduq3-mix_reference2video", "vidu/viduq3-turbo_reference2video",
                    })
                if fake_pixverse:
                    workspace_model_ids.update({
                        "pixverse/pixverse-v6-it2v", "pixverse/pixverse-c1-it2v",
                        "pixverse/pixverse-c1-r2v", "pixverse/pixverse-v5.6-r2v",
                    })
                if fake_wan3 or fake_happyhorse11 or fake_happyhorse_videoedit or fake_kling or fake_vidu or fake_pixverse:
                    workspace_scope = ProviderScope(
                        provider="dashscope", backend="dashscope", gateway="dashscope_workspace",
                        base_url=SMOKE_WORKSPACE_BASE_URL,
                        region=SMOKE_WORKSPACE_REGION, workspace_id=SMOKE_WORKSPACE_ID,
                    )
                    workspace_runtime = ProviderRecoveryRuntime(
                        scope=workspace_scope, secret_resolver=lambda: SMOKE_SECRET,
                        provider=runtime.provider,
                    )
                    selector = SmokeWanRuntimeSelector(runtime, workspace_runtime, workspace_model_ids)
                    scopes["workspace"] = workspace_scope.to_dict()
                if fake_seedance:
                    seedance_scope = ProviderScope(
                        provider="mulerouter", backend="mulerouter", gateway="mulerouter_http",
                        base_url="https://api.mulerouter.ai",
                    )
                    selector.seedance = ProviderRecoveryRuntime(
                        scope=seedance_scope, secret_resolver=lambda: SMOKE_SECRET, provider=runtime.provider,
                    )
                    scopes["seedance"] = seedance_scope.to_dict()
                    target_app.state.runtime_environment.update(
                        {"MULEROUTER_API_KEY": SMOKE_SECRET}, persist=lambda *_: None,
                    )
                target_app.state.provider_recovery_runtime = runtime
                target_app.state.provider_runtime_selector = selector
                target_app.state.pipeline.provider_recovery_runtime = runtime
                target_app.state.pipeline.provider_runtime_selector = selector

                def playground_admission_runtime(*, provider_model_id, gateway):
                    from src.models.provider_recovery import ProviderRecoveryError
                    from src.models.provider_runtime import build_seedance_http_scope_from_snapshot

                    if gateway == "mulerouter":
                        capability = build_seedance_http_scope_from_snapshot(target_app.state.runtime_environment.snapshot())
                        if capability.scope is None:
                            raise ProviderRecoveryError(capability.reason or "provider_recovery_runtime_missing")
                    result = selector.for_admission(provider_model_id=provider_model_id, gateway=gateway)
                    if result.runtime is None:
                        raise ProviderRecoveryError(result.reason or "provider_recovery_runtime_missing")
                    return result.runtime

                # Substitute only admission's external runtime. The real shared
                # dispatcher, credential binding and storage still execute.
                target_app.state.playground_service._video_runtime_provider = playground_admission_runtime
                from src.apps.playground.recovery_dispatcher import PlaygroundVideoRecoveryDispatcher

                if target_app.state.playground_video_recovery_dispatcher.is_running:
                    raise RuntimeError("Playground smoke recovery must start after fake transport injection")
                target_app.state.playground_video_recovery_dispatcher = PlaygroundVideoRecoveryDispatcher(
                    target_app.state.playground_storage,
                    target_app.state.playground_video_execution,
                    interval_seconds=0.05,
                )
                target_app.state.playground_video_recovery_dispatcher.start()
                evidence.update(fake_playground_recovery_started=True, fake_playground_recovery_interval_seconds=0.05)
                evidence.update(
                    fake_provider_injected_at=time.time(), fake_provider_scope=scope.to_dict(),
                    fake_provider_scopes=scopes, fake_wan3=fake_wan3, fake_wan27_videoedit=fake_wan27_videoedit,
                    fake_happyhorse11=fake_happyhorse11, fake_happyhorse_videoedit=fake_happyhorse_videoedit,
                    fake_kling=fake_kling, fake_vidu=fake_vidu, fake_pixverse=fake_pixverse, fake_seedance=fake_seedance,
                )
                if fake_wan3:
                    # Startup recovery was suppressed before fake scope injection.
                    # Run the actual Studio worker now so browser polling can observe
                    # query/fetch without a test driver mutating task state.
                    from src.apps.comic_gen.provider_recovery_dispatcher import StudioProviderRecoveryDispatcher

                    dispatcher = StudioProviderRecoveryDispatcher(target_app.state.pipeline, interval_seconds=0.2)
                    target_app.state.provider_recovery_dispatcher = dispatcher
                    dispatcher.start()
                    evidence.update(fake_studio_recovery_started=True, fake_studio_recovery_interval_seconds=0.2)
                yield
        finally:
            if original_upload is not None:
                from src.models import provider_upload

                with _FAKE_UPLOAD_PATCH_LOCK:
                    if provider_upload.upload_dashscope_temporary_media is patched_upload:
                        provider_upload.upload_dashscope_temporary_media = original_upload
                    _ACTIVE_FAKE_UPLOAD_PATCH = False
                evidence.update(fake_upload_restored_at=time.time())

    @asynccontextmanager
    async def media_lifespan(target_app):
        with fake_media_transport(evidence):
            async with smoke_lifespan(target_app):
                target_app.state.runtime_environment.update({"DASHSCOPE_API_KEY": SMOKE_SECRET}, persist=lambda *_: None)
                yield

    app.router.lifespan_context = media_lifespan if fake_media else smoke_lifespan
    return app


@contextmanager
def fake_media_transport(evidence: Evidence):
    """Patch only external Core factories, after isolation and before app startup."""
    from src.models import provider_media_execution as core
    from src.utils.system_check import get_ffmpeg_path

    if not _FAKE_MEDIA_PATCH_LOCK.acquire(blocking=False):
        raise RuntimeError("fake media transport is already active")
    originals = (core.WanxImageModel, core.TTSProcessor)

    class SyntheticImage:
        def __init__(self, config, *, env):
            self.env = env

        def generate(self, prompt, output, **kwargs):
            if self.env.get("DASHSCOPE_API_KEY") != SMOKE_SECRET:
                raise ValueError("synthetic image transport requires the smoke credential")
            return self._write_image(prompt, output, **kwargs)

        def generate_with_bound_http(self, prompt, output, credential, **kwargs):
            if credential.secret != SMOKE_SECRET:
                raise ValueError("synthetic image transport requires the smoke credential")
            return self._write_image(prompt, output, **kwargs)

        def _write_image(self, prompt, output, **kwargs):
            evidence.append_media_event("image", {
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "model": kwargs.get("model_name"), "seed": kwargs.get("seed"),
                "watermark": kwargs.get("watermark"),
            })
            subprocess.run([
                get_ffmpeg_path() or "ffmpeg", "-v", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=320x240",
                "-frames:v", "1", "-c:v", "png", "-f", "image2", str(output),
            ], check=True, capture_output=True, timeout=20)

    class SyntheticSpeech:
        def __init__(self, **kwargs):
            if kwargs.get("api_key") != SMOKE_SECRET:
                raise ValueError("synthetic speech transport requires the smoke credential")

        def execute(self, prepared, output):
            evidence.append_media_event("audio", {
                "text_sha256": hashlib.sha256(prepared.text.encode()).hexdigest(),
                "voice": prepared.voice, "volume": dict(prepared.parameters).get("volume"),
            })
            # Silence is intentional: this exercises media validity, never speech quality.
            with wave.open(str(output), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b"\0\0" * 16000)

    try:
        core.WanxImageModel, core.TTSProcessor = SyntheticImage, SyntheticSpeech
        evidence.update(fake_media_injected_at=time.time())
        yield
    finally:
        core.WanxImageModel, core.TTSProcessor = originals
        _FAKE_MEDIA_PATCH_LOCK.release()
        evidence.update(fake_media_restored_at=time.time())


class FakeChatResult:
    def __init__(self, *, content: str, requested_model: str):
        self.content = content
        self.requested_model = requested_model
        self.provider_model = "lumenx-smoke-fake-planner-provider-model"
        self.prompt_tokens = 7
        self.completion_tokens = 5
        self.total_tokens = 12


def install_fake_model_planner(fake_model_plan: FakeModelPlan, evidence: Evidence) -> None:
    if fake_model_plan == "disabled":
        return
    from src.apps.comic_gen import llm_adapter as llm_adapter_module

    class SmokeFakeLLMAdapter:
        provider = "lumenx-smoke-fake-planner"

        def __init__(self, *, env=None):
            self.env = env
            evidence.append_model_event("init", {
                "env_id": id(env),
                "env_keys": sorted(str(key) for key in (env or {}).keys()),
            })

        def _get_default_model(self):
            return "lumenx-smoke-fake-planner-model"

        def chat_result(self, messages, *, model, max_completion_tokens, timeout, response_format):
            content = build_fake_model_plan(fake_model_plan, messages)
            evidence.append_model_event("chat_result", {
                "plan": fake_model_plan,
                "model": model,
                "max_completion_tokens": max_completion_tokens,
                "timeout": timeout,
                "response_format": response_format,
                "message_count": len(messages) if isinstance(messages, list) else None,
                "message_sha256": hashlib.sha256(json.dumps(messages, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            })
            return FakeChatResult(content=content, requested_model=model)

    llm_adapter_module.LLMAdapter = SmokeFakeLLMAdapter
    evidence.update(fake_model_planner_injected_at=time.time())


def build_fake_model_plan(fake_model_plan: FakeModelPlan, messages: Any) -> str:
    payload = _planner_user_payload(messages)
    if fake_model_plan == "read":
        tool_calls = [{"tool_name": "canvas.readProject", "arguments": {}}]
        assistant_message = "Smoke fake model will read the Atelier project."
    elif fake_model_plan == "canvas":
        tool_calls = [{
            "tool_name": "canvas.createVideoNode",
            "arguments": {
                "type": "video",
                "title": "Smoke fake model take",
                "prompt": "A safe local smoke video node proposed by fake LLM.",
                "x": 120.0,
                "y": 80.0,
                "width": 320.0,
                "height": 180.0,
            },
        }]
        assistant_message = "Smoke fake model will propose a canvas video node."
    elif fake_model_plan == "generation":
        tool_calls = [{"tool_name": "generation.createVideoCandidates", "arguments": _fake_generation_arguments(payload)}]
        assistant_message = "Smoke fake model will propose a local Wan video generation."
    else:
        tool_calls = []
        assistant_message = "Smoke fake model has no enabled plan."
    return json.dumps({"assistant_message": assistant_message, "tool_calls": tool_calls}, ensure_ascii=False, sort_keys=True)


def _planner_user_payload(messages: Any) -> dict[str, Any]:
    if not isinstance(messages, list) or not messages:
        return {}
    last = messages[-1]
    if not isinstance(last, dict) or not isinstance(last.get("content"), str):
        return {}
    try:
        payload = json.loads(last["content"])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _fake_generation_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    selected_raw = payload.get("selected_node_snapshot")
    selected: dict[str, Any] = selected_raw if isinstance(selected_raw, dict) else {}
    selected_id = selected.get("id")
    node_id = selected_id if isinstance(selected_id, str) and selected_id else "video"
    media_url = None
    image_node_id = None
    reference_ids_raw = selected.get("reference_node_ids")
    reference_ids: list[Any] = reference_ids_raw if isinstance(reference_ids_raw, list) else []
    nodes_raw = payload.get("nodes")
    nodes: list[Any] = nodes_raw if isinstance(nodes_raw, list) else []
    for ref_id in reference_ids:
        for node in nodes:
            if isinstance(node, dict) and node.get("id") == ref_id:
                media_urls = node.get("media_urls")
                if isinstance(media_urls, list) and media_urls and isinstance(media_urls[0], str):
                    image_node_id = ref_id
                    media_url = media_urls[0]
                    break
        if media_url:
            break
    if media_url is None:
        reference_urls = selected.get("reference_image_urls")
        if isinstance(reference_urls, list) and reference_urls and isinstance(reference_urls[0], str):
            image_node_id = reference_ids[0] if reference_ids and isinstance(reference_ids[0], str) else "image"
            media_url = reference_urls[0]
    return {
        "node_id": node_id,
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "A safe local smoke generation proposed by fake LLM.",
        "first_frame": {"node_id": image_node_id or "image", "media_url": media_url or "uploads/frame.png"},
        "params": {"duration": 5, "resolution": "1080p"},
    }



class FakeDashScopeUploader:
    """Harness-only replacement for the external DashScope temporary upload."""

    _SUFFIX_BY_ROLE_MIME = {
        "reference_video": {"video/mp4": "mp4", "video/quicktime": "mov"},
        "reference_audio": {"audio/wav": "wav", "audio/mpeg": "mp3"},
        "first_frame": {"image/png": "png", "image/jpeg": "jpg"},
        "reference_image": {"image/png": "png", "image/jpeg": "jpg"},
    }

    def __init__(self, evidence: Evidence):
        self.evidence = evidence

    def __call__(self, *, scope, provider_model_id, credential, version, data, **_kwargs):
        from src.models.provider_tasks import ProviderScope, ResolvedCredential
        from src.models.provider_upload import ProviderUploadError, ProviderUploadResult
        from src.utils.media_versions import MediaVersion

        if not isinstance(scope, ProviderScope):
            raise ProviderUploadError("scope_invalid")
        classic_videoedit_scope = (
            scope.provider == "dashscope"
            and scope.backend == "dashscope"
            and scope.gateway == "dashscope"
            and scope.base_url == SMOKE_SCOPE_BASE_URL
            and scope.region == "cn-beijing"
            and scope.workspace_id is None
            and scope.account_identity is None
        )
        workspace_scope = (
            scope.provider == "dashscope"
            and scope.backend == "dashscope"
            and scope.gateway == "dashscope_workspace"
            and scope.base_url == SMOKE_WORKSPACE_BASE_URL
            and scope.region == SMOKE_WORKSPACE_REGION
            and scope.workspace_id == SMOKE_WORKSPACE_ID
            and scope.account_identity is None
        )
        if provider_model_id == "wan2.7-videoedit":
            if not classic_videoedit_scope:
                raise ProviderUploadError("scope_invalid")
        elif provider_model_id in {
            "wan3.0-video",
            "wan3.0-video-prime",
            "happyhorse-1.0-video-edit",
            "kling/kling-v3-video-generation",
            "kling/kling-v3-omni-video-generation",
            "vidu/viduq3-pro_img2video", "vidu/viduq3-turbo_img2video",
            "vidu/viduq3-mix_reference2video", "vidu/viduq3-turbo_reference2video",
            "pixverse/pixverse-v6-it2v", "pixverse/pixverse-c1-it2v",
            "pixverse/pixverse-c1-r2v", "pixverse/pixverse-v5.6-r2v",
        }:
            if not workspace_scope:
                raise ProviderUploadError("scope_invalid")
        else:
            raise ProviderUploadError("model_invalid")
        if not isinstance(credential, ResolvedCredential) or credential.scope != scope:
            raise ProviderUploadError("credential_scope_mismatch")
        if not isinstance(version, MediaVersion):
            raise ProviderUploadError("media_version_invalid")
        if type(data) is not bytes or not data:
            raise ProviderUploadError("media_bytes_invalid")
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != version.size_bytes or digest != version.sha256:
            raise ProviderUploadError("media_bytes_mismatch")
        role = version.role
        declared_media_type = version.declared_media_type
        if type(role) is not str or type(declared_media_type) is not str:
            raise ProviderUploadError("media_type_invalid")
        if provider_model_id in {"wan3.0-video", "wan3.0-video-prime"} and role not in {"reference_video", "reference_audio"}:
            raise ProviderUploadError("media_type_invalid")
        if provider_model_id in {"wan2.7-videoedit", "happyhorse-1.0-video-edit"} and role != "reference_video":
            raise ProviderUploadError("media_type_invalid")
        if provider_model_id == "kling/kling-v3-video-generation" and role != "first_frame":
            raise ProviderUploadError("media_type_invalid")
        if provider_model_id == "kling/kling-v3-omni-video-generation" and role != "reference_image":
            raise ProviderUploadError("media_type_invalid")
        vidu_roles = {
            "vidu/viduq3-pro_img2video": "first_frame",
            "vidu/viduq3-turbo_img2video": "first_frame",
            "vidu/viduq3-mix_reference2video": "reference_image",
            "vidu/viduq3-turbo_reference2video": "reference_image",
        }
        pixverse_roles = {
            "pixverse/pixverse-v6-it2v": "first_frame",
            "pixverse/pixverse-c1-it2v": "first_frame",
            "pixverse/pixverse-c1-r2v": "reference_image",
            "pixverse/pixverse-v5.6-r2v": "reference_image",
        }
        if provider_model_id in pixverse_roles:
            if role != pixverse_roles[provider_model_id] or version.size_bytes > 20 * 1024 * 1024:
                raise ProviderUploadError("media_type_invalid")
            suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(declared_media_type)
        elif provider_model_id in vidu_roles:
            if role != vidu_roles[provider_model_id] or version.size_bytes > 50 * 1024 * 1024:
                raise ProviderUploadError("media_type_invalid")
            suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(declared_media_type)
        else:
            suffix = self._SUFFIX_BY_ROLE_MIME.get(role, {}).get(declared_media_type)
        if suffix is None:
            raise ProviderUploadError("media_type_invalid")
        key = f"lumenx-smoke-upload/{role}-{version.ordinal}-{version.sha256[:16]}-{uuid.uuid4().hex}.{suffix}"
        result = ProviderUploadResult(
            url=f"oss://{key}",
            key=key,
            provider_model_id=provider_model_id,
            version=version,
            scope=scope,
            credential_ref=credential.credential_ref,
        )
        self.evidence.append_upload_event({
            "key": key,
            "url": result.url,
            "provider_model_id": provider_model_id,
            "scope": scope.to_dict(),
            "credential_ref": credential.credential_ref,
            "role": version.role,
            "ordinal": version.ordinal,
            "declared_media_type": version.declared_media_type,
            "sha256": version.sha256,
            "size_bytes": version.size_bytes,
        })
        return result


class FakeWanProvider:
    def __init__(self, evidence: Evidence, provider_root: Path):
        self.evidence = evidence
        self.provider_root = provider_root
        self.fixture = self._ensure_fixture()

    def submit(self, request, *, credential, on_submitted):
        from src.models.provider_tasks import ProviderHandle, QueryCapability

        task_id = f"smoke-task-{uuid.uuid4().hex}"
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id,
            task_id=task_id,
            scope=request.scope,
            credential_ref=request.credential_ref,
            request_id=task_id,
            query_capability=QueryCapability.BY_TASK_ID,
            submitted_at=time.time(),
        )
        self.evidence.append_provider_event("submit", {
            "task_id": task_id,
            "request_summary": summarize_submission(request),
            "credential_scope": credential.scope.to_dict(),
            "credential_ref_matches": credential.credential_ref == request.credential_ref,
        })
        on_submitted(handle)
        return handle

    def query(self, handle, *, credential):
        from src.models.provider_tasks import ProviderObservation, ProviderTaskStatus

        observation = ProviderObservation(
            task_id=handle.task_id,
            status=ProviderTaskStatus.SUCCEEDED,
            raw_status="SUCCEEDED",
            request_id=handle.request_id,
            video_url="https://lumenx-smoke.invalid/fake-wan-result.mp4",
            source_handle=handle,
        )
        self.evidence.append_provider_event("query", {
            "task_id": handle.task_id,
            "status": observation.status.value,
            "credential_ref_matches": credential.credential_ref == handle.credential_ref,
        })
        return observation

    def fetch(
        self,
        handle,
        observation,
        *,
        output_root: Path,
        output_ref: str,
        writer_lock,
        storage_guard: Callable[[], ContextManager[None]],
        max_bytes: int,
        on_prepared=None,
        publish_policy: str = "replace",
    ):
        from src.models.provider_tasks import ProviderArtifact, validate_fetch_binding
        from src.utils.media_versions import store_media_stream

        validate_fetch_binding(handle, observation)
        if publish_policy != "no_overwrite":
            raise RuntimeError("generation smoke expected no_overwrite fetch publication")

        def prepared_callback(relative_path: str, digest: str, size: int) -> None:
            proof = ProviderArtifact(handle, relative_path, digest, size)
            self.evidence.append_provider_event("fetch", {
                "task_id": handle.task_id,
                "output_ref": output_ref,
                "publish_policy": publish_policy,
                "prepared_artifact": proof.to_dict(),
            })
            if on_prepared is not None:
                result = on_prepared(proof)
                if result is not None:
                    raise RuntimeError("prepared callback must be synchronous")

        relative_path, digest, size = store_media_stream(
            self._fixture_chunks(),
            output_ref=output_ref,
            output_root=output_root,
            writer_lock=writer_lock,
            storage_guard=storage_guard,
            max_bytes=max_bytes,
            on_prepared=prepared_callback,
            publish_policy=publish_policy,
        )
        return ProviderArtifact(handle, relative_path, digest, size)

    def _fixture_chunks(self) -> Iterable[bytes]:
        with self.fixture.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                yield chunk

    def _ensure_fixture(self) -> Path:
        self.provider_root.mkdir(parents=True, exist_ok=True)
        fixture = self.provider_root / "fake-wan-color-3s.mp4"
        if fixture.is_file() and fixture.stat().st_size > 0:
            return fixture
        temporary = self.provider_root / ".fake-wan-color-3s.tmp.mp4"
        if temporary.exists():
            temporary.unlink()
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x180:rate=24",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        subprocess.run(command, cwd=self.provider_root, check=True)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("ffmpeg did not create the smoke video fixture")
        os.replace(temporary, fixture)
        return fixture


def summarize_submission(request) -> dict[str, Any]:
    intent = request.intent.to_dict()
    media = []
    for item in request.media:
        entry = {
            "sha256": item.version.sha256,
            "size_bytes": item.version.size_bytes,
            "role": item.version.role,
            "ordinal": item.version.ordinal,
            "declared_media_type": item.version.declared_media_type,
            "transport_binding": item.transport_binding,
        }
        if item.transport_binding == "dashscope_oss_sha256_v1":
            entry["url"] = item.url
        media.append(entry)
    return {
        "provider_model_id": request.provider_model_id,
        "credential_ref": request.credential_ref,
        "intent": {
            "model_id": intent.get("model_id"),
            "mode": intent.get("mode"),
            "catalog_mode": intent.get("catalog_mode"),
            "prompt_sha256": hashlib.sha256(str(intent.get("prompt", "")).encode("utf-8")).hexdigest(),
            "params": intent.get("params", {}),
            "request_fields": intent.get("request_fields", []),
        },
        "media": media,
        "scope": request.scope.to_dict(),
    }


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


def validate_source_reference(commit: str) -> dict[str, str]:
    backend_hashes = {}
    paths = git_output(["ls-tree", "-r", "--name-only", commit, "src"])
    for relative in (paths or "").splitlines():
        if not relative.endswith(".py"):
            continue
        committed = subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=REPO_ROOT)
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
    return backend_hashes


def main() -> None:
    args = parse_args()
    frontend_commit = git_output(["rev-parse", "--verify", args.frontend_freeze_commit + "^{commit}"])
    backend_commit = git_output(["rev-parse", "--verify", args.backend_source_commit + "^{commit}"])
    if not frontend_commit or not backend_commit:
        raise SystemExit("both source references must resolve to fixed commits")
    backend_hashes = validate_source_reference(backend_commit)
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
        build_initial_evidence(
            data_root, static_root, static_manifest, host, port, backend_commit, backend_hashes,
            args.fake_model_plan, args.fake_wan3, args.fake_media, args.fake_wan27_videoedit,
            args.fake_happyhorse11, args.fake_happyhorse_videoedit, args.fake_kling, args.fake_vidu, args.fake_pixverse, args.fake_seedance,
        ),
    )
    install_socket_guard(evidence)

    import uvicorn

    app = create_smoke_app(
        data_root, static_root, evidence, fake_model_plan=args.fake_model_plan, fake_wan3=args.fake_wan3,
        fake_media=args.fake_media, fake_wan27_videoedit=args.fake_wan27_videoedit,
        fake_happyhorse11=args.fake_happyhorse11, fake_happyhorse_videoedit=args.fake_happyhorse_videoedit,
        fake_kling=args.fake_kling, fake_vidu=args.fake_vidu, fake_pixverse=args.fake_pixverse, fake_seedance=args.fake_seedance,
    )
    config = uvicorn.Config(app, host=host, port=port, reload=False, log_level="info", access_log=True)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[reportAttributeAccessIssue]

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
