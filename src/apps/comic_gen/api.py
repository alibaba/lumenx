# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT — handler async/sync convention (audited 2026-05-21):
#
# DEFAULT every new endpoint to `def`, NOT `async def`.
#
# FastAPI's contract:
#   - `async def` handlers run on the asyncio event loop. If the body does any
#     blocking I/O (sync HTTP call, json.dump, requests.post, openai sync
#     client, ffmpeg via subprocess.run, etc.) it FREEZES every other request
#     until the blocking call returns — including unrelated GETs the modal
#     uses to load config. Symptom: "modal stuck on 加载配置中...".
#   - `def` handlers get auto-dispatched to anyio's threadpool, so blocking
#     I/O doesn't touch the event loop. Concurrent requests stay responsive.
#
# Use `async def` ONLY when the body has `await` (streaming uploads, real
# asyncio primitives, awaiting `call_next` in middleware, asyncio.to_thread).
#
# The 7 legitimate async endpoints (verified to use await): middleware
# add_cache_control_header, create_project, reparse_project,
# import_file_preview, import_file_confirm, upload_t2i_frame,
# analyze_script_for_styles. All others are `def` for a reason.
# ─────────────────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager, contextmanager
from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Request, Depends, Query
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, StrictBool, StrictInt, StrictStr, field_validator
from typing import Optional, Dict, List, Any, Annotated, Literal
import asyncio
import math
import time
from functools import partial
import json
import hashlib
import os
import shutil
import sys
import uuid
import logging
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote
from src.lumenx_service_identity import LUMENX_PROTOCOL_VERSION, LUMENX_SERVICE_NAME
from .models import (
    DialogueAudioOptions,
    ArtDirection,
    PromptConfig,
    ProviderBackend,
    ProviderRoutingConfig,
    Script,
    StoryboardFrame,
    VideoTask,
)
from fastapi.responses import JSONResponse, Response
from ...utils.json_storage import (
    StorageConflictError, StorageError, serialize_json,
    read_json, atomic_write_json, atomic_write_text,
)
from .documents import (
    DocumentNotFound,
    DocumentProjectNotFound,
    DocumentStore,
    UnsafeDocumentId,
)
from ...utils.app_access import (
    AppAccessState,
    LumenXAccessMiddleware,
    configure_app_access,
    is_access_required,
    is_request_authenticated,
)
from ...utils.media_static import ManagedMediaStaticFiles
from .series_import import ConfirmImportRequest, ImportPreviewUnavailable, MAX_IMPORT_TEXT_BYTES
from .series_episode import CreateSeriesEpisodeRequest, SeriesEpisodeCreationReceipt
from ...utils.app_logging import app_log_context, create_app_file_handler
from dotenv import dotenv_values, set_key
from ...utils.runtime_environment import RuntimeEnvironment, validate_environment
from ...utils.request_identity import validate_client_request_id
from ...models.provider_runtime import (
    ProviderRuntimeBuildResult,
    build_provider_runtime_for_admission,
    build_provider_runtime_for_scope,
)
from ...models.provider_tasks import ProviderScope
from .provider_runtime import build_studio_provider_recovery_runtime
from .project_package import DEFAULT_MAX_PACKAGE_BYTES, ProjectPackageError
from .asset_image_execution import AssetImageRequestCommand, AssetImageRequestNotFound
from .asset_image_wire import asset_image_request_wire
from .motion_reference import MotionReferenceAdmissionRejected
from ..atelier.api import router as atelier_router
from ..atelier.export_history import ExportReferenceCheckUnavailable
from ...utils.media_refs import payload_contains_managed_ref
from ..atelier.export_history_api import router as atelier_export_history_router
from ..atelier.sequence_export_api import router as atelier_sequence_export_router
from ..atelier.generation_api import router as atelier_generation_router
from ..atelier.agent_api import router as atelier_agent_router
from ..atelier.agent_loop_api import router as atelier_agent_loop_router
from ..atelier.project_package_api import router as atelier_project_package_router
from ..playground.api import router as playground_router


class OSSImageUploader:
    def __new__(cls, *args, **kwargs):
        from ...utils.oss_utils import OSSImageUploader as RealOSSImageUploader

        return RealOSSImageUploader(*args, **kwargs)

    @staticmethod
    def reset_instance():
        from ...utils.oss_utils import OSSImageUploader as RealOSSImageUploader

        return RealOSSImageUploader.reset_instance()


def sign_media_refs_in_response(*args, **kwargs):
    from ...utils.oss_utils import sign_media_refs_in_response as real_sign_media_refs_in_response

    return real_sign_media_refs_in_response(*args, **kwargs)


def get_pipeline(request: Request) -> Any:
    pipeline = request.app.state.pipeline
    check = getattr(pipeline, "assert_storage_available", None)
    if callable(check):
        check()
    return pipeline


router = APIRouter()
logger = logging.getLogger(__name__)

_ENVIRONMENT_HEADER = "x-lumenx-environment"
_ENVIRONMENT_HEADER_EXEMPT_PATHS = {"/health", "/auth/session", "/runtime/environment"}


def _canonical_uuid4(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid environment binding") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("Invalid environment binding")
    return value


def _environment_response(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        {"code": code, "detail": detail},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _storage_error_json(exc: StorageError) -> JSONResponse:
    conflict = isinstance(exc, StorageConflictError)
    return JSONResponse(
        {
            "code": "storage_conflict" if conflict else "storage_unavailable",
            "detail": "The saved version changed; reload and retry" if conflict else
            "Saving is unavailable; restart the application before continuing",
            "commit_uncertain": exc.commit_uncertain,
        },
        status_code=409 if conflict else 503,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _current_storage_id_for_request(request: Request) -> str:
    pipeline = request.app.state.pipeline
    check = getattr(pipeline, "assert_storage_available", None)
    if callable(check):
        check()
    identity = getattr(pipeline, "storage_identity", None)
    from ...utils.storage_identity import validate_storage_identity

    return str(validate_storage_identity(identity)["storage_id"])


def _default_prompts() -> Dict[str, str]:
    from .llm import (
        DEFAULT_ENTITY_EXTRACTION_PROMPT,
        DEFAULT_R2V_POLISH_PROMPT,
        DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
        DEFAULT_STORYBOARD_POLISH_PROMPT,
        DEFAULT_STYLE_ANALYSIS_PROMPT,
        DEFAULT_VIDEO_POLISH_PROMPT,
    )

    return {
        "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
        "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
        "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
        "entity_extraction": DEFAULT_ENTITY_EXTRACTION_PROMPT,
        "style_analysis": DEFAULT_STYLE_ANALYSIS_PROMPT,
        "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
    }



router.include_router(playground_router, prefix="/playground")
router.include_router(atelier_router, prefix="/atelier")
router.include_router(atelier_sequence_export_router, prefix="/atelier")
router.include_router(atelier_export_history_router, prefix="/atelier")
router.include_router(atelier_generation_router, prefix="/atelier")
router.include_router(atelier_agent_router, prefix="/atelier")
router.include_router(atelier_agent_loop_router, prefix="/atelier")
router.include_router(atelier_project_package_router, prefix="/atelier")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_runtime_config(*, user_data_dir: Path | None, load_config: bool) -> RuntimeEnvironment:
    if not load_config:
        return RuntimeEnvironment(dict(os.environ))
    if user_data_dir is not None:
        return RuntimeEnvironment(load_user_config(user_data_dir=user_data_dir))
    initial = dict(os.environ)
    env_path = _project_root() / ".env"
    if env_path.exists():
        if env_path.is_symlink():
            raise StorageError("Configuration file cannot be a symlink")
        initial.update({key: value for key, value in dotenv_values(env_path).items() if value is not None})
    return RuntimeEnvironment(initial)


def _ensure_output_dirs(data_dir: Path) -> None:
    for rel in (
        "",
        "uploads",
        "video",
        "assets",
        "audio",
        "export",
        "storyboard",
        "playground/images",
        "playground/videos",
        "playground/uploads",
    ):
        (data_dir / rel).mkdir(parents=True, exist_ok=True)


def _mount_output_routes(target_app: FastAPI, data_dir: Path) -> None:
    output_prefixes = (
        "uploads",
        "assets",
        "video",
        "audio",
        "export",
        "storyboard",
        "video_inputs",
        "playground",
        "outputs",
    )
    mounts = [
        ("/files/outputs/videos", data_dir / "video", "files_outputs_videos", ()),
        ("/files/outputs/assets", data_dir / "assets", "files_outputs_assets", ()),
        ("/files/outputs", data_dir, "files_outputs", output_prefixes),
        ("/files/videos", data_dir / "video", "files_videos", ()),
        ("/files/assets", data_dir / "assets", "files_assets", ()),
        ("/files/playground", data_dir / "playground", "files_playground", ()),
        ("/files", data_dir, "files", output_prefixes),
    ]
    existing_paths = {getattr(route, "path", None) for route in target_app.router.routes}
    for path, directory, name, prefixes in mounts:
        if path not in existing_paths:
            target_app.mount(
                path,
                ManagedMediaStaticFiles(directory=str(directory), allowed_prefixes=prefixes),
                name=name,
            )


_CLASSIC_PROVIDER_MODELS = {"wan2.6-i2v", "wan2.6-i2v-flash", "wan2.7-i2v"}


class _InjectedProviderRuntimeSelector:
    def __init__(self, runtime):
        self._runtime = runtime

    def classic_compatibility(self):
        return ProviderRuntimeBuildResult(self._runtime)

    def for_admission(self, *, provider_model_id: str, gateway: str):
        runtime = self._runtime
        if runtime is None:
            return ProviderRuntimeBuildResult(None, "provider_recovery_runtime_missing")
        if runtime.scope.gateway != gateway:
            return ProviderRuntimeBuildResult(None, "unsupported_dashscope_gateway")
        if gateway == "dashscope" and provider_model_id not in _CLASSIC_PROVIDER_MODELS:
            return ProviderRuntimeBuildResult(None, "unsupported_provider_model")
        return ProviderRuntimeBuildResult(runtime)

    def for_scope(self, scope: ProviderScope):
        runtime = self._runtime
        if runtime is None:
            return ProviderRuntimeBuildResult(None, "provider_recovery_runtime_missing")
        if runtime.scope != scope:
            return ProviderRuntimeBuildResult(None, "unsupported_provider_scope")
        return ProviderRuntimeBuildResult(runtime)


class _ManagedProviderRuntimeSelector:
    def __init__(self, runtime_environment: RuntimeEnvironment, *, output_root: Path):
        self._runtime_environment = runtime_environment
        self._output_root = output_root
        self._classic = None
        self._admission: dict[tuple[str, str], object] = {}
        self._scoped: dict[tuple, object] = {}
        self._lock = threading.RLock()

    def classic_compatibility(self):
        with self._lock:
            if self._classic is None:
                self._classic = build_studio_provider_recovery_runtime(
                    self._runtime_environment,
                    output_root=self._output_root,
                )
            return self._classic

    def for_admission(self, *, provider_model_id: str, gateway: str):
        key = (provider_model_id, gateway)
        with self._lock:
            if key not in self._admission:
                self._admission[key] = build_provider_runtime_for_admission(
                    self._runtime_environment,
                    output_root=self._output_root,
                    provider_model_id=provider_model_id,
                    gateway=gateway,
                )
            return self._admission[key]

    def for_scope(self, scope: ProviderScope):
        with self._lock:
            classic = self.classic_compatibility()
            if classic.runtime is not None and classic.runtime.scope == scope:
                return classic
            key = tuple(sorted(scope.to_dict().items()))
            if key not in self._scoped:
                self._scoped[key] = build_provider_runtime_for_scope(
                    self._runtime_environment,
                    output_root=self._output_root,
                    scope=scope,
                )
            return self._scoped[key]


def _managed_provider_runtime_selector(target_app: FastAPI) -> _ManagedProviderRuntimeSelector:
    runtime_environment = target_app.state.runtime_environment
    output_root = target_app.state.data_dir
    return runtime_environment.client(
        "provider-runtime-selector",
        lambda snapshot: _ManagedProviderRuntimeSelector(
            RuntimeEnvironment(dict(snapshot)),
            output_root=output_root,
        ),
    )


def _current_runtime_for_scope(target_app: FastAPI, scope: ProviderScope):
    runtime = getattr(target_app.state, "provider_recovery_runtime", None)
    if runtime is not None and getattr(runtime, "scope", None) == scope:
        return runtime
    return None


def _provider_runtime_lock(target_app: FastAPI) -> threading.RLock:
    lock = getattr(target_app.state, "provider_recovery_runtime_lock", None)
    if lock is None:
        lock = threading.RLock()
        target_app.state.provider_recovery_runtime_lock = lock
    return lock


def _set_provider_recovery_runtime_state(target_app: FastAPI, *, owner: str, reason: str | None = None) -> None:
    target_app.state.provider_recovery_runtime_owner = owner
    target_app.state.provider_recovery_runtime_reason = reason


def _register_provider_recovery_runtime(target_app: FastAPI, *, allow_injected: bool) -> None:
    runtime_pipeline = target_app.state.pipeline
    if allow_injected and getattr(runtime_pipeline, "provider_recovery_runtime", None) is not None:
        selector = _InjectedProviderRuntimeSelector(runtime_pipeline.provider_recovery_runtime)
        target_app.state.provider_runtime_selector = selector
        runtime_pipeline.provider_runtime_selector = selector
        target_app.state.provider_recovery_runtime = runtime_pipeline.provider_recovery_runtime
        _set_provider_recovery_runtime_state(target_app, owner="injected")
        return

    selector = _managed_provider_runtime_selector(target_app)
    target_app.state.provider_runtime_selector = selector
    runtime_pipeline.provider_runtime_selector = selector
    result = selector.classic_compatibility()
    runtime_pipeline.provider_recovery_runtime = result.runtime
    target_app.state.provider_recovery_runtime = result.runtime
    _set_provider_recovery_runtime_state(
        target_app,
        owner="managed" if result.runtime is not None else "none",
        reason=result.reason,
    )


def _start_provider_recovery_dispatcher_if_ready(target_app: FastAPI) -> None:
    if not getattr(target_app.state, "provider_recovery_recover_orphan_tasks", False):
        return
    atelier_dispatcher = getattr(target_app.state, "atelier_generation_dispatcher", None)
    if atelier_dispatcher is not None:
        atelier_dispatcher.start()
        atelier_dispatcher.wake()
    playground_dispatcher = getattr(target_app.state, "playground_video_recovery_dispatcher", None)
    if playground_dispatcher is not None:
        playground_dispatcher.start()
        playground_dispatcher.wake()
    if hasattr(target_app.state, "provider_recovery_dispatcher"):
        wake = getattr(target_app.state.provider_recovery_dispatcher, "wake", None)
        if callable(wake):
            wake()
        return
    runtime_pipeline = target_app.state.pipeline
    if getattr(runtime_pipeline, "provider_recovery_runtime", None) is None and getattr(runtime_pipeline, "provider_runtime_selector", None) is None:
        return
    # Only explicitly registered recovery capabilities may run here. Startup
    # never infers credentials or converts legacy tasks into new submissions.
    from .provider_recovery_dispatcher import StudioProviderRecoveryDispatcher

    dispatcher = StudioProviderRecoveryDispatcher(runtime_pipeline)
    target_app.state.provider_recovery_dispatcher = dispatcher
    dispatcher.start()


def _refresh_managed_provider_recovery_runtime(target_app: FastAPI) -> None:
    with _provider_runtime_lock(target_app):
        if getattr(target_app.state, "provider_recovery_runtime_owner", None) == "injected":
            return
        _register_provider_recovery_runtime(target_app, allow_injected=False)
        if getattr(target_app.state.pipeline, "provider_recovery_runtime", None) is None:
            dispatcher = getattr(target_app.state, "provider_recovery_dispatcher", None)
            wake = getattr(dispatcher, "wake", None)
            if callable(wake):
                wake()
            atelier_dispatcher = getattr(target_app.state, "atelier_generation_dispatcher", None)
            if atelier_dispatcher is not None:
                atelier_dispatcher.wake()
            return
        _start_provider_recovery_dispatcher_if_ready(target_app)



def _safe_planner_credential_ref(env: Dict[str, str], provider_id: str) -> str | None:
    provider_prefix = provider_id.upper().replace("-", "_")
    for key in (
        "LLM_CREDENTIAL_REF",
        "LLM_CREDENTIAL_ALIAS",
        f"{provider_prefix}_CREDENTIAL_REF",
        f"{provider_prefix}_CREDENTIAL_ALIAS",
    ):
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            lowered = candidate.lower()
            if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "apikey", "secret", "token=", "sk-", "http://", "https://")):
                return None
            return candidate[:128]
    return None


def _planner_endpoint_identity(provider_id: str, env: Dict[str, str]) -> tuple[str, str]:
    from ...utils.endpoints import get_provider_base_url

    if provider_id == "openai":
        endpoint = (env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    else:
        endpoint = f"{get_provider_base_url('DASHSCOPE', env=env)}/compatible-mode/v1"
    endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
    return endpoint_hash, f"{provider_id}-compatible-{endpoint_hash[:12]}"


def _planner_config_identity_for_runtime(
    *,
    provider_id: str,
    model_id: str,
    env: Dict[str, str],
    storage_identity: str,
) -> Any:
    from ..atelier.models import AtelierAgentPlannerConfigIdentity

    endpoint_hash, provider_scope = _planner_endpoint_identity(provider_id, env)
    return AtelierAgentPlannerConfigIdentity(
        provider_scope=provider_scope,
        runtime_config_id=f"llm-endpoint-{endpoint_hash[:24]}",
        model_config_version=model_id,
        credential_ref=_safe_planner_credential_ref(env, provider_id),
        storage_identity=storage_identity,
    )


def _configure_runtime(
    target_app: FastAPI,
    *,
    data_dir: str | os.PathLike[str] = "output",
    user_data_dir: str | os.PathLike[str] | None = None,
    load_config: bool = True,
    setup_logs: bool = False,
    pipeline_instance: Any | None = None,
    recover_orphan_tasks: bool = True,
    warmup_demucs: bool = False,
    access_state: AppAccessState | None = None,
    access_mode: str | None = None,
    access_token: str | None = None,
    access_allowed_origins: str | list[str] | tuple[str, ...] | None = None,
    access_session_ttl_seconds: int | None = None,
    access_max_sessions: int | None = None,
    access_cookie_secure: bool | None = None,
) -> FastAPI:
    output_dir = Path(data_dir).expanduser().resolve()
    user_root = Path(user_data_dir).expanduser().resolve() if user_data_dir is not None else None
    if setup_logs:
        log_root = (user_root or output_dir.parent) / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        log_id, handler = create_app_file_handler(log_root / "app.log")
        # Level configuration stays process-wide, matching the existing runtime.
        # Handler lifetime belongs to this application; records are context-filtered.
        if logging.getLogger().getEffectiveLevel() > logging.INFO:
            logging.getLogger().setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        target_app.state.app_log_id = log_id
        target_app.state.owned_log_handlers = [handler]
    with app_log_context(getattr(target_app.state, "app_log_id", None)):
        configured_values = dict(_load_runtime_config(user_data_dir=user_root, load_config=load_config).snapshot())
        # CLI credentials are execution authority, never a portable project setting.
        # Include the storage root and login realm so two apps cannot share a profile.
        profile_scope = json.dumps([
            str(output_dir), configured_values.get("MULERUN_SITE", "sg"),
            configured_values.get("MULERUN_API_BASE_URL") or configured_values.get("MULERUN_BASE_URL", ""),
        ]).encode("utf-8")
        profile_id = hashlib.sha256(profile_scope).hexdigest()[:24]
        configured_values["LUMENX_MULERUN_HOME"] = str((user_root or output_dir) / ".runtime" / "mulerun" / profile_id)
        runtime_environment = RuntimeEnvironment(configured_values)
        target_app.state.runtime_environment = runtime_environment
        from ...utils.mulerun_profile import MuleRunLoginSession
        target_app.state.mulerun_login_session = MuleRunLoginSession(
            profile_home=configured_values["LUMENX_MULERUN_HOME"], env=runtime_environment.snapshot(),
        )
        if access_state is not None:
            configure_app_access(
                access_state,
                mode=access_mode if access_mode is not None else runtime_environment.snapshot().get("LUMENX_ACCESS_MODE"),
                token=access_token if access_token is not None else runtime_environment.snapshot().get("LUMENX_ACCESS_TOKEN"),
                allowed_origins=access_allowed_origins if access_allowed_origins is not None else runtime_environment.snapshot().get("LUMENX_ACCESS_ALLOWED_ORIGINS"),
                session_ttl_seconds=access_session_ttl_seconds,
                max_sessions=access_max_sessions,
                cookie_secure=access_cookie_secure,
            )
        _ensure_output_dirs(output_dir)
        from .pipeline import ComicGenPipeline

        runtime_pipeline = pipeline_instance or ComicGenPipeline(
            data_dir=output_dir,
            recover_orphan_tasks=recover_orphan_tasks,
            warmup_demucs=warmup_demucs,
            repair_series_bindings=recover_orphan_tasks,
            runtime_environment=runtime_environment,
        )
        # Register ownership before any later import or initialization can fail.
        target_app.state.pipeline = runtime_pipeline
        runtime_pipeline.runtime_environment = runtime_environment
        from ..playground.provider_video_execution import PlaygroundProviderVideoExecutionService
        from ..playground.recovery_dispatcher import PlaygroundVideoRecoveryDispatcher
        from ..playground.service import PlaygroundService
        from ..playground.storage import PlaygroundStorage
        from ...models.provider_binding import ProviderCredentialStore
        from ...models.provider_recovery import ProviderRecoveryError

        target_app.state.data_dir = output_dir
        target_app.state.user_data_dir = user_root
        target_app.state.provider_recovery_recover_orphan_tasks = bool(recover_orphan_tasks)
        target_app.state.provider_recovery_runtime_lock = threading.RLock()
        with _provider_runtime_lock(target_app):
            _register_provider_recovery_runtime(target_app, allow_injected=pipeline_instance is not None)
        target_app.state.playground_storage = PlaygroundStorage(
            output_dir,
            writer_lock=getattr(runtime_pipeline, "_writer_lock", None),
            runtime_check=getattr(runtime_pipeline, "assert_storage_available", None),
        )
        target_app.state.provider_credential_store = ProviderCredentialStore(
            output_dir,
            writer_lock=target_app.state.playground_storage._writer_lock,
            runtime_lock=target_app.state.playground_storage._lock,
        )

        def provider_runtime_for_scope(scope):
            current = _current_runtime_for_scope(target_app, scope)
            if current is not None:
                return current
            selector = getattr(target_app.state, "provider_runtime_selector", None)
            if selector is None:
                raise ProviderRecoveryError("provider_recovery_runtime_missing")
            result = selector.for_scope(scope)
            if result.runtime is None:
                raise ProviderRecoveryError(result.reason or "provider_recovery_runtime_missing")
            return result.runtime

        target_app.state.playground_video_execution = PlaygroundProviderVideoExecutionService(
            target_app.state.playground_storage,
            runtime_provider=provider_runtime_for_scope,
            credential_store=target_app.state.provider_credential_store,
        )
        target_app.state.playground_service = PlaygroundService(
            target_app.state.playground_storage,
            data_dir=output_dir,
            library_writer=runtime_pipeline.create_library_asset,
            runtime_environment=runtime_environment,
            video_scope_runtime_provider=provider_runtime_for_scope,
            provider_credential_store=target_app.state.provider_credential_store,
            video_execution_service=target_app.state.playground_video_execution,
        )
        target_app.state.playground_video_recovery_dispatcher = PlaygroundVideoRecoveryDispatcher(
            target_app.state.playground_storage,
            target_app.state.playground_video_execution,
        )
        from ..atelier.agent_service import AtelierAgentReadOnlyService
        from ..atelier.service import AtelierService
        from ..atelier.storage import AtelierStorage

        target_app.state.atelier_storage = AtelierStorage(
            output_dir,
            # Playground already borrowed the real pipeline lease or acquired
            # the sole fallback lease for a non-writing injected pipeline.
            writer_lock=target_app.state.playground_storage._writer_lock,
            lock=getattr(runtime_pipeline, "_save_lock", None),
            runtime_check=getattr(runtime_pipeline, "assert_storage_available", None),
            mark_storage_failed=getattr(runtime_pipeline, "mark_storage_failed", None),
        )
        target_app.state.atelier_service = AtelierService(
            target_app.state.atelier_storage,
            source_project_exists=lambda project_id: any(
                project.id == project_id for project in runtime_pipeline.read_committed_projects()
            ),
        )
        target_app.state.atelier_agent_service = AtelierAgentReadOnlyService(target_app.state.atelier_storage)
        from ..atelier.sequence_export_dispatcher import SequenceExportDispatcher
        from ..atelier.sequence_export_service import SequenceExportService
        from ..atelier.export_history import ExportHistoryService

        target_app.state.atelier_sequence_export_service = SequenceExportService(
            target_app.state.atelier_storage
        )
        target_app.state.atelier_export_history_service = ExportHistoryService(
            target_app.state.atelier_storage,
            external_reference_guard=_build_export_history_external_guard(target_app),
        )
        target_app.state.atelier_sequence_export_dispatcher = SequenceExportDispatcher(
            target_app.state.atelier_sequence_export_service
        )
        from ..atelier.generation_admission import AtelierGenerationAdmissionService
        from ..atelier.candidate_judgment import AtelierCandidateJudgmentService
        from ..atelier.generation_execution import AtelierGenerationExecutionService
        from ..atelier.generation_execution_router import AtelierGenerationExecutionRouter
        from ..atelier.generation_dispatcher import AtelierGenerationDispatcher
        from ..atelier.generation_selection import AtelierGenerationSelectionService
        from ..atelier.generation_commands import image_contract_options
        from ..atelier.media_generation_admission import AtelierMediaGenerationAdmissionService
        from ..atelier.media_generation_execution import AtelierMediaGenerationExecutionService
        from ...models.provider_binding import MuleRunCliRouteIdentity
        from ...models.provider_media_intents import prepared_tts_from_sync_record
        from ...models.provider_media_runtime import bind_media_runtime_authority, resolve_media_runtime_authority, mulerun_routes_from_snapshot
        from ...models.provider_tasks import ProviderScope
        from ...utils.storage_identity import initialize_storage_identity

        atelier_store = target_app.state.atelier_storage
        generation_credentials = target_app.state.provider_credential_store
        atelier_storage_identity = str(initialize_storage_identity(output_dir, writer_lock=atelier_store._writer_lock)["storage_id"])

        generation_runtime_for_scope = provider_runtime_for_scope

        def bind_generation_credential(*, provider_model_id: str, gateway: str):
            selector = getattr(target_app.state, "provider_runtime_selector", None)
            if selector is None:
                current = getattr(target_app.state, "provider_recovery_runtime", None)
                selector = _InjectedProviderRuntimeSelector(current)
            result = selector.for_admission(provider_model_id=provider_model_id, gateway=gateway)
            runtime = result.runtime
            if runtime is None:
                raise ProviderRecoveryError(result.reason or "provider_recovery_runtime_missing")
            try:
                return generation_credentials.bind(runtime.scope, runtime.resolve_secret())
            except StorageError as exc:
                atelier_store._record_storage_failure(exc)
                raise

        def mulerun_routes() -> MuleRunCliRouteIdentity:
            return mulerun_routes_from_snapshot(runtime_environment.snapshot())

        def mulerun_profile_home():
            value = runtime_environment.snapshot().get("LUMENX_MULERUN_HOME")
            return value or None

        def bind_media_generation_authority(*, media_kind: str, provider_model_id: str, preview):
            if media_kind == "image":
                contract = image_contract_options(preview.intent)
                if contract:
                    return bind_media_runtime_authority(
                        runtime_environment, generation_credentials,
                        image_intent=preview.intent, image_contract_version=contract["image_contract_version"],
                    )
                return bind_media_runtime_authority(
                    runtime_environment,
                    generation_credentials,
                    image_intent=preview.intent,
                    mulerun_routes=mulerun_routes(),
                    mulerun_profile_home=mulerun_profile_home(),
                )
            if media_kind == "audio":
                return bind_media_runtime_authority(
                    runtime_environment,
                    generation_credentials,
                    prepared_tts=preview.prepared,
                )
            raise ValueError("atelier_media_generation_kind_invalid")

        def resolve_media_generation_authority(record):
            media_kind = record.media_kind
            prepared_tts = prepared_tts_from_sync_record(record) if media_kind == "audio" else None
            return resolve_media_runtime_authority(
                runtime_environment,
                generation_credentials,
                media_kind=media_kind,
                provider_model_id=record.provider_model_id,
                scope=ProviderScope.from_dict(record.scope),
                credential_ref=record.credential_ref,
                **({"image_contract_version": record.intent["intent_contract_version"]}
                   if media_kind == "image" and "intent_contract_version" in record.intent else {}),
                prepared_tts=prepared_tts,
                mulerun_profile_home=mulerun_profile_home(),
            )

        target_app.state.atelier_generation_admission = AtelierGenerationAdmissionService(
            atelier_store, credential_provider=bind_generation_credential,
        )
        target_app.state.atelier_media_generation_admission = AtelierMediaGenerationAdmissionService(
            atelier_store,
            authority_provider=bind_media_generation_authority,
            server_storage_identity=atelier_storage_identity,
        )
        target_app.state.atelier_video_generation_execution = AtelierGenerationExecutionService(
            atelier_store, runtime_provider=generation_runtime_for_scope, credential_store=generation_credentials,
        )
        target_app.state.atelier_media_generation_execution = AtelierMediaGenerationExecutionService(
            atelier_store, authority_resolver=resolve_media_generation_authority,
        )
        target_app.state.atelier_generation_execution = AtelierGenerationExecutionRouter(
            atelier_store,
            target_app.state.atelier_video_generation_execution,
            target_app.state.atelier_media_generation_execution,
        )
        target_app.state.atelier_generation_dispatcher = AtelierGenerationDispatcher(
            atelier_store, target_app.state.atelier_generation_execution,
        )
        from ..atelier.generation_draft import AtelierGenerationDraftService
        from ..atelier.candidate_branch import AtelierCandidateBranchService

        target_app.state.atelier_generation_selection = AtelierGenerationSelectionService(atelier_store)
        target_app.state.atelier_candidate_judgment = AtelierCandidateJudgmentService(atelier_store)
        target_app.state.atelier_candidate_branch = AtelierCandidateBranchService(atelier_store)
        target_app.state.atelier_generation_draft = AtelierGenerationDraftService(atelier_store)
        from ..atelier.agent_executor import AtelierAgentExecutor

        target_app.state.atelier_agent_executor = AtelierAgentExecutor(
            atelier_store,
            credential_provider=bind_generation_credential,
            generation_scheduler=lambda project_id, attempt_id: target_app.state.atelier_generation_dispatcher.schedule(
                project_id, attempt_id,
            ),
        )
        from ..atelier.agent_loop import AtelierAgentLoopService
        from ..atelier.agent_loop_model_planner import ModelBackedLoopPlanner, ModelPlannerConfig
        from ..atelier.agent_loop_planner import plan_deterministic_loop
        from .llm_adapter import LLMAdapter

        def model_planner_config() -> ModelPlannerConfig:
            env = runtime_environment.snapshot()
            adapter = LLMAdapter(env=env)
            provider_id = adapter.provider
            model_id = adapter._get_default_model()

            def bounded_model_chat(messages, *, model, max_completion_tokens, timeout, response_format):
                return adapter.chat_result(
                    messages,
                    model=model,
                    max_completion_tokens=max_completion_tokens,
                    timeout=timeout,
                    response_format=response_format,
                )

            return ModelPlannerConfig(
                provider_id=provider_id,
                model_id=model_id,
                config_identity=_planner_config_identity_for_runtime(
                    provider_id=provider_id,
                    model_id=model_id,
                    env=dict(env),
                    storage_identity=atelier_storage_identity,
                ),
                max_completion_tokens=1024,
                timeout_seconds=60.0,
                bounded_chat=bounded_model_chat,
            )

        def bounded_model_chat(messages, *, model, max_completion_tokens, timeout, response_format):
            raise RuntimeError("model planner config did not bind a bounded chat executor")
        from ..atelier.project_package_service import AtelierProjectPackageService

        target_app.state.atelier_project_package_service = AtelierProjectPackageService(
            atelier_store,
            storage_id=atelier_storage_identity,
        )
        target_app.state.atelier_agent_loop_service = AtelierAgentLoopService(
            atelier_store,
            planner=plan_deterministic_loop,
            model_planner=ModelBackedLoopPlanner(
                config_provider=model_planner_config,
                bounded_chat=bounded_model_chat,
            ),
            id_factory=lambda: uuid.uuid4().hex,
            server_storage_identity=atelier_storage_identity,
            credential_provider=bind_generation_credential,
            generation_scheduler=lambda project_id, attempt_id: target_app.state.atelier_generation_dispatcher.schedule(
                project_id, attempt_id,
            ),
        )
        _mount_output_routes(target_app, output_dir)
        logger.info(
            "STARTUP: OSS_ENDPOINT=%s, OSS_BUCKET_NAME=%s, OSS_BASE_PATH=%s",
            runtime_environment.snapshot().get("OSS_ENDPOINT"),
            runtime_environment.snapshot().get("OSS_BUCKET_NAME"),
            runtime_environment.snapshot().get("OSS_BASE_PATH"),
        )
        _start_provider_recovery_dispatcher_if_ready(target_app)
    return target_app


def _cleanup_runtime(target_app: FastAPI) -> None:
    dispatcher = getattr(target_app.state, "provider_recovery_dispatcher", None)
    if dispatcher is not None:
        dispatcher.request_stop()
    generation_dispatcher = getattr(target_app.state, "atelier_generation_dispatcher", None)
    if generation_dispatcher is not None:
        generation_dispatcher.request_stop()
    playground_video_dispatcher = getattr(target_app.state, "playground_video_recovery_dispatcher", None)
    if playground_video_dispatcher is not None:
        playground_video_dispatcher.request_stop()
    export_dispatcher = getattr(target_app.state, "atelier_sequence_export_dispatcher", None)
    if export_dispatcher is not None:
        try:
            export_dispatcher.request_stop()
        except Exception:
            with app_log_context(getattr(target_app.state, "app_log_id", None)):
                logger.warning("Failed to stop Atelier export dispatcher")
    if playground_video_dispatcher is not None and not playground_video_dispatcher.join(timeout=5.0):
        with app_log_context(getattr(target_app.state, "app_log_id", None)):
            logger.warning("Waiting for Playground video dispatcher to stop before storage close")
        if not playground_video_dispatcher.join(timeout=None):
            raise StorageConflictError("Playground video dispatcher did not stop before storage close")
    atelier_storage = getattr(target_app.state, "atelier_storage", None)
    if atelier_storage is not None:
        # Seal stale product references before releasing the shared owner lease.
        atelier_storage.close()
    runtime_pipeline = getattr(target_app.state, "pipeline", None)
    close = getattr(runtime_pipeline, "close", None)
    if callable(close):
        close()
    if dispatcher is not None:
        # Provider HTTP can outlive the bounded join. The closed pipeline rejects
        # its late result before the storage lease can be reused by another app.
        dispatcher.join(timeout=5.0)
    if export_dispatcher is not None:
        export_dispatcher.join(timeout=5.0)
    if generation_dispatcher is not None:
        generation_dispatcher.join(timeout=5.0)
    login_session = getattr(target_app.state, "mulerun_login_session", None)
    if login_session is not None:
        try:
            login_session.close()
        except Exception:
            with app_log_context(getattr(target_app.state, "app_log_id", None)):
                logger.warning("Failed to stop owned MuleRun login process")
    storage = getattr(target_app.state, "playground_storage", None)
    if storage is not None:
        storage.close()
    access_state = getattr(target_app.state, "app_access", None)
    if access_state is not None:
        access_state.sessions.clear()
    for handler in getattr(target_app.state, "owned_log_handlers", []):
        logging.getLogger().removeHandler(handler)
        handler.close()
    for name in (
        "pipeline",
        "playground_storage",
        "playground_service",
        "playground_video_execution",
        "playground_video_recovery_dispatcher",
        "provider_credential_store",
        "atelier_storage",
        "atelier_service",
        "atelier_agent_service",
        "atelier_agent_executor",
        "atelier_agent_loop_service",
        "atelier_project_package_service",
        "atelier_sequence_export_service",
        "atelier_export_history_service",
        "atelier_sequence_export_dispatcher",
        "atelier_generation_admission",
        "atelier_media_generation_admission",
        "atelier_video_generation_execution",
        "atelier_media_generation_execution",
        "atelier_generation_execution",
        "atelier_generation_dispatcher",
        "atelier_generation_selection",
        "atelier_candidate_branch",
        "provider_recovery_runtime",
        "runtime_environment",
        "mulerun_login_session",
        "provider_recovery_dispatcher",
        "provider_recovery_runtime_owner",
        "provider_recovery_runtime_reason",
        "provider_recovery_recover_orphan_tasks",
        "provider_recovery_runtime_lock",
        "data_dir",
        "user_data_dir",
        "owned_log_handlers",
        "app_log_id",
    ):
        if hasattr(target_app.state, name):
            delattr(target_app.state, name)


def _build_export_history_external_guard(target_app: FastAPI):
    @contextmanager
    def guard(_project, export):
        pipeline = getattr(target_app.state, "pipeline", None)
        playground_storage = getattr(target_app.state, "playground_storage", None)
        save_lock = getattr(pipeline, "_save_lock", None)
        playground_lock = getattr(playground_storage, "_lock", None)
        if pipeline is None or save_lock is None or playground_storage is None or playground_lock is None:
            raise ExportReferenceCheckUnavailable("External reference checker is unavailable")
        target_ref = export.video_url
        with save_lock:
            with playground_lock:
                try:
                    if hasattr(pipeline, "assert_storage_available"):
                        pipeline.assert_storage_available()
                    if hasattr(playground_storage, "_require_writable"):
                        playground_storage._require_writable()
                    elif hasattr(playground_storage, "require_writable"):
                        playground_storage.require_writable()
                    elif hasattr(playground_storage, "_require_runtime"):
                        playground_storage._require_runtime()
                    has_reference = _export_ref_in_studio_payloads(pipeline, target_ref) or _export_ref_in_playground_payloads(playground_storage, target_ref)
                except Exception as exc:
                    raise ExportReferenceCheckUnavailable("External reference checker is unavailable") from exc
                yield not has_reference

    return guard


def _export_ref_in_studio_payloads(pipeline: Any, target_ref: str) -> bool:
    output_root = getattr(pipeline, "data_dir", None) or getattr(pipeline, "output_dir", None) or "output"
    projects = [item.model_dump(mode="json") for item in pipeline.read_committed_projects()]
    series = [item.model_dump(mode="json") for item in pipeline.read_committed_series()]
    library = pipeline.list_library_assets().model_dump(mode="json")
    return (
        payload_contains_managed_ref(projects, target_ref, output_root)
        or payload_contains_managed_ref(series, target_ref, output_root)
        or payload_contains_managed_ref(library, target_ref, output_root)
    )


def _export_ref_in_playground_payloads(playground_storage: Any, target_ref: str) -> bool:
    output_root = getattr(playground_storage, "data_dir", None) or "output"
    history = [item.model_dump(mode="json") for item in getattr(playground_storage, "_history", [])]
    templates = [item.model_dump(mode="json") for item in getattr(playground_storage, "_templates", [])]
    return payload_contains_managed_ref(history, target_ref, output_root) or payload_contains_managed_ref(templates, target_ref, output_root)


def _lifespan_factory(
    *,
    data_dir: str | os.PathLike[str],
    user_data_dir: str | os.PathLike[str] | None,
    load_config: bool,
    setup_logs: bool,
    pipeline_instance: Any | None,
    recover_orphan_tasks: bool,
    warmup_demucs: bool,
    access_state: AppAccessState | None,
    access_mode: str | None,
    access_token: str | None,
    access_allowed_origins: str | list[str] | tuple[str, ...] | None,
    access_session_ttl_seconds: int | None,
    access_max_sessions: int | None,
    access_cookie_secure: bool | None,
):
    @asynccontextmanager
    async def lifespan(target_app: FastAPI):
        try:
            _configure_runtime(
                target_app,
                data_dir=data_dir,
                user_data_dir=user_data_dir,
                load_config=load_config,
                setup_logs=setup_logs,
                pipeline_instance=pipeline_instance,
                recover_orphan_tasks=recover_orphan_tasks,
                warmup_demucs=warmup_demucs,
                access_state=access_state,
                access_mode=access_mode,
                access_token=access_token,
                access_allowed_origins=access_allowed_origins,
                access_session_ttl_seconds=access_session_ttl_seconds,
                access_max_sessions=access_max_sessions,
                access_cookie_secure=access_cookie_secure,
            )
            with app_log_context(getattr(target_app.state, "app_log_id", None)):
                yield
        finally:
            with app_log_context(getattr(target_app.state, "app_log_id", None)):
                _cleanup_runtime(target_app)

    return lifespan


def create_app(
    *,
    data_dir: str | os.PathLike[str] = "output",
    user_data_dir: str | os.PathLike[str] | None = None,
    load_config: bool = True,
    setup_logs: bool = False,
    pipeline_instance: Any | None = None,
    recover_orphan_tasks: bool = False,
    warmup_demucs: bool = False,
    access_mode: str | None = None,
    access_token: str | None = None,
    access_allowed_origins: str | list[str] | tuple[str, ...] | None = None,
    access_session_ttl_seconds: int | None = None,
    access_max_sessions: int | None = None,
    access_cookie_secure: bool | None = None,
) -> FastAPI:
    access_state = AppAccessState()
    target_app = FastAPI(
        title="AI Comic Gen API",
        lifespan=_lifespan_factory(
            data_dir=data_dir,
            user_data_dir=user_data_dir,
            load_config=load_config,
            setup_logs=setup_logs,
            pipeline_instance=pipeline_instance,
            recover_orphan_tasks=recover_orphan_tasks,
            warmup_demucs=warmup_demucs,
            access_state=access_state,
            access_mode=access_mode,
            access_token=access_token,
            access_allowed_origins=access_allowed_origins,
            access_session_ttl_seconds=access_session_ttl_seconds,
            access_max_sessions=access_max_sessions,
            access_cookie_secure=access_cookie_secure,
        ),
    )
    target_app.state.app_access = access_state
    target_app.include_router(router)

    @target_app.exception_handler(RequestValidationError)
    async def request_validation_response(request: Request, exc: RequestValidationError):
        route_path = getattr(request.scope.get("route"), "path", None)
        safe_validation_routes = {
            "/projects/{script_id}/video_tasks",
            "/projects/{script_id}/asset_image_requests",
            "/series/{series_id}/asset_image_requests",
            "/projects/{project_id}/document",
            "/projects/{project_id}/sync_derivation",
            "/projects/{project_id}/shot_blocks/{shot_id}/confirm",
        }
        # Included routers can retain their unprefixed route.path. Identify our
        # product router by its matched endpoint, not a client-supplied URL.
        endpoint_module = getattr(request.scope.get("endpoint"), "__module__", None)
        atelier_validation = endpoint_module in {
            "src.apps.atelier.api",
            "src.apps.atelier.sequence_export_api",
            "src.apps.atelier.export_history_api",
            "src.apps.atelier.generation_api",
            "src.apps.atelier.agent_api",
            "src.apps.atelier.agent_loop_api",
            "src.apps.atelier.project_package_api",
        }
        if not atelier_validation and (
            request.method != "POST" or route_path not in safe_validation_routes
        ):
            return await request_validation_exception_handler(request, exc)
        # FastAPI's default validation response includes the invalid input.
        # JSON permits neither NaN nor Infinity, so rejected finite-scalar or
        # document-content validation can make the default 422 response fail while
        # rendering. For these exact routes, omit raw input from the error body;
        # admission already failed before any task, snapshot, or write observes it.
        detail = []
        for error in exc.errors():
            item = {key: value for key, value in error.items() if key != "input"}
            if isinstance(item.get("ctx"), dict):
                item["ctx"] = {
                    key: value if value is None or isinstance(value, (str, int, bool))
                    or isinstance(value, float) and math.isfinite(value) else str(value)
                    for key, value in item["ctx"].items()
                }
            detail.append(item)
        return JSONResponse(status_code=422, content={"detail": detail})

    @target_app.exception_handler(StorageError)
    async def storage_error_response(request: Request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse(status_code=409 if conflict else 503, content={
            "code": "storage_conflict" if conflict else "storage_unavailable",
            "detail": "The saved version changed; reload and retry" if conflict else
                      "Saving is unavailable; restart the application before continuing",
            "commit_uncertain": exc.commit_uncertain,
        })
    target_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    @target_app.middleware("http")
    async def bind_app_log_context(request: Request, call_next):
        with app_log_context(getattr(request.app.state, "app_log_id", None)):
            return await call_next(request)

    @target_app.middleware("http")
    async def add_runtime_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/files/"):
            response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @target_app.middleware("http")
    async def enforce_environment_header(request: Request, call_next):
        if request.method.upper() == "OPTIONS" or request.url.path in _ENVIRONMENT_HEADER_EXEMPT_PATHS:
            return await call_next(request)
        requested_storage_id = request.headers.get(_ENVIRONMENT_HEADER)
        if requested_storage_id is None:
            return await call_next(request)
        try:
            requested_storage_id = _canonical_uuid4(requested_storage_id)
        except ValueError:
            return _environment_response(
                400,
                "invalid_environment",
                "Invalid environment binding",
            )
        if is_access_required(request.scope) and not is_request_authenticated(request.scope):
            return await call_next(request)
        try:
            current_storage_id = _current_storage_id_for_request(request)
        except StorageError as exc:
            return _storage_error_json(exc)
        if current_storage_id != requested_storage_id:
            return _environment_response(
                409,
                "environment_changed",
                "The storage environment changed; refresh before continuing",
            )
        return await call_next(request)

    target_app.add_middleware(LumenXAccessMiddleware, state=access_state)

    return target_app


# Allow-list map for uploaded file extensions: keys come from the (untrusted)
# client filename, values are trusted literals safe to embed in server paths.
_UPLOAD_EXT_MAP = {
    ".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpeg", ".webp": ".webp",
    ".gif": ".gif", ".bmp": ".bmp", ".svg": ".svg", ".ico": ".ico",
    ".mp4": ".mp4", ".mov": ".mov", ".webm": ".webm", ".avi": ".avi",
    ".mkv": ".mkv", ".mp3": ".mp3", ".wav": ".wav", ".m4a": ".m4a",
    ".aac": ".aac", ".ogg": ".ogg", ".flac": ".flac",
    ".txt": ".txt", ".md": ".md", ".pdf": ".pdf", ".json": ".json",
}


def _safe_upload_ext(filename: Optional[str]) -> str:
    """Return a trusted file extension literal for an uploaded filename.

    Unknown/missing extensions fall back to empty string so the stored file
    is still saved under its random UUID name.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    return _UPLOAD_EXT_MAP.get(ext, "")


@router.get("/debug/config")
def debug_config(active_pipeline: Any = Depends(get_pipeline)):
    """Diagnostic endpoint to check OSS and path configuration."""
    uploader = _runtime_uploader(active_pipeline)
    environment = getattr(active_pipeline, "runtime_environment", None)
    env = os.environ if environment is None else environment.snapshot()
    return {
        "oss_configured": uploader.is_configured,
        "oss_bucket_initialized": uploader.bucket is not None,
        "oss_base_path": env.get("OSS_BASE_PATH", "lumenx"),
        "output_dir_exists": Path(active_pipeline.data_dir).exists(),
        "output_contents": os.listdir(active_pipeline.data_dir) if Path(active_pipeline.data_dir).exists() else [],
        "cwd": os.getcwd(),
        "env_vars_present": {
            "OSS_ENDPOINT": bool(env.get("OSS_ENDPOINT")),
            "OSS_BUCKET_NAME": bool(env.get("OSS_BUCKET_NAME")),
            "ALIBABA_CLOUD_ACCESS_KEY_ID": bool(env.get("ALIBABA_CLOUD_ACCESS_KEY_ID")),
        }
    }

def _runtime_uploader(pipeline):
    environment = getattr(pipeline, "runtime_environment", None)
    if environment is None:
        return OSSImageUploader()
    return environment.client(
        "studio.oss", lambda env: OSSImageUploader(env=env, output_root=pipeline.data_dir),
    )


def signed_response(data, *, media_fields=None, pipeline: Any = None, runtime: Any = None):
    """Helper to sign OSS URLs in data before returning to frontend.
    
    Handles Pydantic models, lists of models, and dicts.
    Returns a JSONResponse with signed URLs.
    """
    if data is None:
        return JSONResponse(content=None)
    
    freshness_script = data if pipeline is not None and isinstance(data, Script) else None

    # Convert Pydantic models to dict
    if hasattr(data, "model_dump"):
        processed_data = data.model_dump()
    elif isinstance(data, list):
        processed_data = [item.model_dump() if hasattr(item, "model_dump") else item for item in data]
    else:
        processed_data = data

    if freshness_script is not None and isinstance(processed_data, dict):
        processed_data["merged_video_freshness"] = pipeline.merge_video_freshness(freshness_script)
    
    # Sign declared media fields when OSS is configured, and always remove
    # incoming response-only sidecars before returning data.
    processed_data = sign_media_refs_in_response(processed_data, _runtime_uploader(runtime or pipeline), media_fields=media_fields)
    
    # Return JSONResponse directly to avoid Pydantic re-validation stripping fields
    return JSONResponse(content=processed_data)


# ============================================================
# Shared Request Models (used by both Project and Series endpoints)
# ============================================================

class GenerateAssetRequest(BaseModel):
    asset_id: str
    asset_type: str
    style_preset: str = "Cinematic"
    reference_image_url: Optional[str] = None
    style_prompt: Optional[str] = None
    generation_type: str = "all"  # 'full_body', 'three_view', 'headshot', 'all', 'reference_sheet'
    prompt: Optional[str] = None
    apply_style: bool = Field(True, strict=True)
    negative_prompt: Optional[str] = None
    batch_size: int = Field(1, ge=1, strict=True)
    model_name: Optional[str] = None
    aspect_ratio: Optional[str] = None

class ToggleLockRequest(BaseModel):
    asset_id: str
    asset_type: str

class UpdateAssetImageRequest(BaseModel):
    asset_id: str
    asset_type: str
    image_url: str

class UpdateAssetAttributesRequest(BaseModel):
    asset_id: str
    asset_type: str
    attributes: Dict[str, Any]


@router.get("/runtime/environment")
def runtime_environment(active_pipeline: Any = Depends(get_pipeline)):
    identity = getattr(active_pipeline, "storage_identity", None)
    if not isinstance(identity, dict):
        raise StorageError("Storage identity is not initialized")
    from ...utils.storage_identity import validate_storage_identity

    return JSONResponse(
        content=validate_storage_identity(identity),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/health")
def health_check(
    request: Request,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Lightweight liveness probe used by the Diagnose UI on stuck
    tasks and by external uptime checks. Intentionally cheap: no DB
    hit, no provider call, just a 200 + a few facts the frontend can
    show next to the spinner ("backend reachable, log file at X")."""
    user_root = getattr(request.app.state, "user_data_dir", None)
    log_dir = Path(user_root or Path(active_pipeline.data_dir).parent) / "logs"
    log_file = log_dir / "app.log"
    response = {
        "ok": True,
        "service": LUMENX_SERVICE_NAME,
        "protocol_version": LUMENX_PROTOCOL_VERSION,
        "time": time.time(),
    }
    if not is_access_required(request.scope) or is_request_authenticated(request.scope):
        response.update({
            "log_file": str(log_file),
            "log_dir": str(log_dir),
            "studio_projects": len(getattr(active_pipeline, "scripts", {})),
        })
    return response


@router.get("/diagnose/log_tail")
def diagnose_log_tail(request: Request, lines: int = 200):
    """Return the last N lines of the app log + an ERROR/Exception
    summary so the Diagnose UI on stuck tasks can show actual log
    content instead of just a path. Read-only, capped to 1000 lines so
    a runaway client can't hose the process."""
    capped = max(1, min(int(lines or 200), 1000))
    user_root = request.app.state.user_data_dir or request.app.state.data_dir.parent
    log_path = str(Path(user_root) / "logs" / "app.log")
    if not os.path.exists(log_path):
        return {"path": log_path, "lines": [], "errors": [], "missing": True}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # Read all, then tail. App.log is bounded by the rotating
            # handler (≤ 5 MB active) so this is safe.
            all_lines = f.readlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read log: {exc}")
    tail = all_lines[-capped:] if len(all_lines) > capped else all_lines
    error_keywords = ("ERROR", "Exception", "Failed", "Unauthorized", "Traceback")
    errors = [
        ln.rstrip("\n")
        for ln in tail
        if any(k in ln for k in error_keywords)
    ][-30:]
    return {
        "path": log_path,
        "total_lines": len(all_lines),
        "returned_lines": len(tail),
        "lines": [ln.rstrip("\n") for ln in tail],
        "errors": errors,
        "missing": False,
    }


@router.get("/system/check")
def check_system():
    """Check system dependencies (ffmpeg, etc.) and configuration."""
    from ...utils.system_check import run_system_checks
    return run_system_checks()





@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    active_pipeline: Any = Depends(get_pipeline),
):
    """Uploads a file and returns its URL (OSS if configured, else local)."""
    try:
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        local_url = f"uploads/{filename}"
        upload_dir = Path(active_pipeline.data_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(upload_dir / filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Try uploading to OSS. Keep the legacy display `url` behavior, but
        # always expose the managed local ref for callers that must persist a
        # local media identity (for example Studio closing frames).
        oss_url = _runtime_uploader(active_pipeline).upload_image(file_path)
        if oss_url:
            return signed_response({"url": oss_url, "local_url": local_url}, media_fields={"url"}, runtime=active_pipeline)

        # Fallback to local URL (relative path for frontend getAssetUrl)
        return signed_response({"url": local_url, "local_url": local_url}, media_fields={"url"}, runtime=active_pipeline)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UploadAssetRequest(BaseModel):
    upload_type: str  # "full_body" | "head_shot" | "three_views" | "image"
    description: Optional[str] = None  # User-modified description for reverse generation


@router.post("/projects/{script_id}/assets/{asset_type}/{asset_id}/upload")
def upload_asset(
    script_id: str,
    asset_type: str,
    asset_id: str,
    upload_type: str,
    description: Optional[str] = None,
    file: UploadFile = File(...),
    active_pipeline: Any = Depends(get_pipeline),
):
    """
    Uploads an image as a new variant for an asset.
    The uploaded image is marked as the 'upload source' for reverse generation.
    
    - asset_type: "character", "scene", or "prop"
    - upload_type: "full_body", "head_shot", "three_views", or "image" (for scene/prop)
    - description: Optional modified description for the asset
    """
    try:
        owner = active_pipeline.validate_asset_upload(script_id, asset_type, asset_id, upload_type)
        # 1. Save file locally first
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        upload_dir = Path(active_pipeline.data_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(upload_dir / filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 2. Mirror to OSS while the managed upload remains the source of truth.
        active_pipeline.assert_storage_available()
        uploader = _runtime_uploader(active_pipeline)
        uploader.upload_image(file_path)
        
        # 3. Keep the already-written local bytes available for Core freezing.
        active_pipeline.add_uploaded_asset_variant(
            script_id=script_id,
            asset_type=asset_type,
            asset_id=asset_id,
            upload_type=upload_type,
            image_url=f"uploads/{filename}",
            description=description,
            expected_owner=owner,
        )
        
        return get_project(script_id, pipeline=active_pipeline)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception(f"Error uploading asset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CreateProjectRequest(BaseModel):
    title: str
    text: str
    workflow_mode: str = "r2v"  # "r2v" (default) or "i2v_legacy"
    # Optional series binding (T9). When set, the new project is created as
    # the next episode of this series (episode_number = current max + 1).
    # Omit for a standalone project — behavior unchanged.
    series_id: Optional[str] = None


@router.post("/projects", response_model=Script)
async def create_project(
    request: CreateProjectRequest,
    skip_analysis: bool = False,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Creates a new project from a novel text.

    When `series_id` is provided the project is bound as the next episode
    of that series; omitting it keeps the standalone-project behavior
    unchanged.
    """
    # Run in thread pool to avoid blocking event loop during LLM analysis (Python 3.8 compatible)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,  # Use default executor
            partial(active_pipeline.create_project, request.title, request.text, skip_analysis, request.workflow_mode, request.series_id)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return signed_response(result, runtime=active_pipeline)



class ExtractPreviewRequest(BaseModel):
    text: str


class ReparseProjectRequest(ExtractPreviewRequest):
    expected_revision: int = Field(..., ge=0, strict=True)


class UpdateScriptTextRequest(BaseModel):
    text: str
    expected_revision: int = Field(..., ge=0, strict=True)


@router.put("/projects/{script_id}/text", response_model=Script)
def update_script_text(
    script_id: str,
    request: UpdateScriptTextRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Persist `original_text` without re-parsing entities.

    Used by ScriptProcessor's onBlur so typing survives reload/navigation
    without triggering an LLM round-trip. Heavy reparse stays bound to the
    explicit "提取实体" CTA.
    """
    try:
        script = active_pipeline.update_script_text(
            script_id, request.text, expected_revision=request.expected_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return signed_response(script, runtime=active_pipeline)


@router.put("/projects/{script_id}/reparse", response_model=Script)
async def reparse_project(
    script_id: str,
    request: ReparseProjectRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Re-parses the text for an existing project, replacing all entities."""
    from .pipeline import ProjectGenerationError

    try:
        # Run the blocking LLM call in a thread pool to avoid blocking the event loop (Python 3.8 compatible)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,  # Use default executor
            partial(active_pipeline.reparse_project, script_id, request.text,
                    expected_revision=request.expected_revision)
        )
        return signed_response(result, runtime=active_pipeline)
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/projects/{script_id}/extract_preview")
async def extract_preview(
    script_id: str,
    request: ExtractPreviewRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Dry-run entity extraction — returns entities without saving."""
    from .pipeline import ProjectGenerationError

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(active_pipeline.extract_preview, script_id, request.text)
        )
        return {
            "characters": [c.dict() for c in result.characters],
            "scenes": [s.dict() for s in result.scenes],
            "props": [p.dict() for p in result.props],
        }
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/", response_model=List[dict])
def list_projects(active_pipeline: Any = Depends(get_pipeline)):
    """Lists all projects from backend storage."""
    scripts = active_pipeline.read_committed_projects()
    return signed_response(scripts, runtime=active_pipeline)


def _public_exception_detail(exc: Exception, messages: tuple[str, ...], fallback: str) -> str:
    """Return server-owned feedback, never text taken from an exception."""
    detail = str(exc)
    for message in messages:
        if detail == message:
            return message
    logger.warning("Studio request rejected", exc_info=(type(exc), exc, exc.__traceback__))
    return fallback


_PACKAGE_VALIDATION_DETAILS = (
    "Invalid project package zip", "Project package is too large",
    "Project package has too many media files", "Project package is missing required metadata",
    "Project package metadata could not be read", "Project package project payload is invalid",
    "Project package metadata is not strict JSON", "Project package source metadata is invalid",
    "Unsupported package schema", "Unsupported package kind", "Unexpected package entry",
    "Imported media target collision",
    "Project package could not be written", "Package source series differs from the project relation",
    "Unsafe dynamic field contains external reference", "Unsafe dynamic field contains undeclared media reference",
    "Project package manifest must be an object", "Project package media manifest is invalid",
    "Image selection is outside its asset", "Variant selection is outside its asset",
    "Missing packaged asset for video task", "Duplicate project entity identity",
    "Project package metadata is not valid JSON", "Media reference must be a non-empty string",
    "Package media directory changed", "Package media path is unavailable or unsafe",
    "Package media must be a bounded regular file", "Package media exceeds size limit",
    "Package media changed during export", "Duplicate package entry", "Project package has too many files",
    "Project package media entry is invalid", "Project package media path is invalid",
    "Project package media path does not match logical ref", "Project package media hash is invalid",
    "Project package media size is invalid", "Package media target changed",
    "Media manifest does not match project references", "Missing or ambiguous packaged task asset",
    "Invalid prepared image history", "Prepared image has no managed output",
    "Prepared image proofs disagree", "Prepared image bytes differ from their proof",
    "Prepared image is stored at another owner", "Prepared frame image belongs to another owner",
    "Prepared frame image has no unique packaged frame", "Variant identity refers to different media",
    "Selected take belongs to a different frame", "Video task points at a different project",
) + tuple(
    f"Prepared {label} {problem}"
    for label in ("three-view image", "reference sheet", "headshot image", "full-body image", "scene image", "prop image")
    for problem in ("has lost its project owner", "has no unique packaged target", "has no unique packaged character")
)


def _package_error_response(status_code: int, code: str, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "detail": _public_exception_detail(
            exc, _PACKAGE_VALIDATION_DETAILS, "Project package is invalid",
        )},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


class ProjectPackageTooLarge(Exception):
    pass


async def _read_raw_project_package(request: Request) -> bytes:
    media_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type and media_type not in {"application/zip", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Project package must be application/zip or application/octet-stream")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > DEFAULT_MAX_PACKAGE_BYTES:
                raise ProjectPackageTooLarge("Project package is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > DEFAULT_MAX_PACKAGE_BYTES:
            raise ProjectPackageTooLarge("Project package is too large")
        body.extend(chunk)
    return bytes(body)


@router.get("/projects/{script_id}/package/export")
def export_project_package(script_id: str, active_pipeline: Any = Depends(get_pipeline)):
    try:
        artifact = active_pipeline.export_project_package(script_id)
    except ProjectPackageError as exc:
        return _package_error_response(422, "project_package_invalid", exc)
    except ValueError as exc:
        if str(exc) == "Script not found":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _package_error_response(422, "project_package_invalid", exc)
    filename = f"lumenx-project-{script_id}.lumenx.zip"
    return Response(
        content=artifact.data,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.post("/projects/package/import", response_model=Script)
async def import_project_package(request: Request, active_pipeline: Any = Depends(get_pipeline)):
    try:
        package_bytes = await _read_raw_project_package(request)
        script = await asyncio.to_thread(active_pipeline.import_project_package, package_bytes)
    except ProjectPackageTooLarge as exc:
        return _package_error_response(413, "project_package_too_large", exc)
    except ProjectPackageError as exc:
        return _package_error_response(422, "project_package_invalid", exc)
    except ValueError as exc:
        return _package_error_response(422, "project_package_invalid", exc)
    return signed_response(script, runtime=active_pipeline)


@router.post("/projects/{script_id}/toggle_starred")
def toggle_project_starred(
    script_id: str,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Toggle the user-starred (featured shortlist) flag on a project."""
    try:
        script = active_pipeline.toggle_project_starred(script_id)
        return signed_response(script, runtime=active_pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Series CRUD
# ============================================================

class CreateSeriesRequest(BaseModel):
    title: str
    description: str = ""
    workflow_mode: str = "r2v"
    # R2V v2 Phase 6 — content mode (scripted | freeform), defaults
    # scripted (traditional script-first flow).
    content_mode: str = "scripted"
    # PR-3e (r2v-workflow-v3) — Visual control preference.
    # 'r2v' (节奏优先, default) | 'i2v' (画面优先).
    default_generation_mode: str = "r2v"


class UpdateSeriesRequest(BaseModel):
    expected_revision: int = Field(..., ge=0, strict=True)
    # exclude_unset keeps omitted fields untouched; explicit null is invalid.
    title: str = ""
    description: str = ""
    workflow_mode: str = ""
    content_mode: str = ""
    default_generation_mode: str = ""
    # R2V v2: series-level art_direction baseline (Phase 2). Inherits +
    # override flows at the episode level read this as the source of truth.
    art_direction: Optional[ArtDirection] = None


@router.post("/series")
def create_series(request: CreateSeriesRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Create a new Series."""
    series = pipeline.create_series(
        request.title,
        request.description,
        request.workflow_mode,
        request.content_mode,
        request.default_generation_mode,
    )
    return signed_response(series, runtime=pipeline)


@router.get("/series")
def list_series(
    pipeline: Any = Depends(get_pipeline),
):
    """List all Series."""
    series_list = pipeline.read_committed_series()
    return signed_response(series_list, runtime=pipeline)


@router.get("/series/{series_id}")
def get_series(series_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Get Series details including assets and episode list."""
    series, episodes = pipeline.read_committed_series_view(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    # Include episode summaries
    result = series.model_dump()
    result["episodes"] = [
        {
            "id": ep.id,
            "title": ep.title,
            "episode_number": ep.episode_number,
            "created_at": ep.created_at,
            "updated_at": ep.updated_at,
        }
        for ep in episodes
    ]
    return signed_response(result, runtime=pipeline)


@router.put("/series/{series_id}")
def update_series(series_id: str, request: UpdateSeriesRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Update Series fields. Uses `exclude_unset=True` so explicitly-null
    values (e.g. `{"art_direction": null}` to clear baseline) are honored,
    while fields the client didn't send remain untouched."""
    try:
        updates = request.model_dump(exclude_unset=True, exclude={"expected_revision"})
        series = pipeline.update_series(series_id, updates, expected_revision=request.expected_revision)
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/series/{series_id}")
def delete_series(series_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Delete a Series and disassociate its episodes."""
    try:
        pipeline.delete_series(series_id)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AddEpisodeRequest(BaseModel):
    script_id: str
    episode_number: Optional[int] = None


@router.post("/series/{series_id}/episode_requests", response_model=SeriesEpisodeCreationReceipt)
def create_series_episode(series_id: str, request: CreateSeriesEpisodeRequest,
    pipeline: Any = Depends(get_pipeline),
):
    try:
        return pipeline.create_series_episode(series_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/series/{series_id}/episode_requests/{client_request_id}", response_model=SeriesEpisodeCreationReceipt)
def read_series_episode_creation(series_id: str, client_request_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    try:
        return pipeline.read_series_episode_creation(series_id, client_request_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/series/{series_id}/episodes")
def add_episode_to_series(series_id: str, request: AddEpisodeRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Add an existing project as an episode to a Series."""
    try:
        series = pipeline.add_episode_to_series(series_id, request.script_id, request.episode_number)
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/series/{series_id}/episodes/{script_id}")
def remove_episode_from_series(series_id: str, script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Remove an episode from a Series (does not delete the project)."""
    try:
        series = pipeline.remove_episode_from_series(series_id, script_id)
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/series/{series_id}/episodes")
def get_series_episodes(series_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Get all episodes in a Series."""
    try:
        series, episodes = pipeline.read_committed_series_view(series_id)
        if series is None:
            raise ValueError("Series not found")
        return signed_response(episodes, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/series/{series_id}/prompt_config")
def get_series_prompt_config(series_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Get Series prompt config with system defaults."""
    series, _ = pipeline.read_committed_series_view(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return {
        "prompt_config": series.prompt_config.model_dump(),
        "defaults": {
            key: _default_prompts()[key]
            for key in ("storyboard_polish", "video_polish", "r2v_polish", "storyboard_extraction")
        },
    }


@router.put("/series/{series_id}/prompt_config")
def update_series_prompt_config(series_id: str, config: PromptConfig,
    pipeline: Any = Depends(get_pipeline),
):
    """Update Series-level prompt config."""
    try:
        series = pipeline.update_series(series_id, {"prompt_config": config})
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Series Model Settings
# ============================================================

class UpdateModelSettingsRequest(BaseModel):
    t2i_model: Optional[str] = None
    i2i_model: Optional[str] = None
    image_model: Optional[str] = None
    i2v_model: Optional[str] = None
    r2v_model: Optional[str] = None
    character_aspect_ratio: Optional[str] = None
    scene_aspect_ratio: Optional[str] = None
    prop_aspect_ratio: Optional[str] = None
    storyboard_aspect_ratio: Optional[str] = None

@router.get("/series/{series_id}/model_settings")
def get_series_model_settings(series_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Get Series model settings."""
    series, _ = pipeline.read_committed_series_view(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series.model_settings.model_dump()


@router.put("/series/{series_id}/model_settings")
def update_series_model_settings(series_id: str, settings: UpdateModelSettingsRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Update Series-level model settings."""
    try:
        series = pipeline.update_series_model_settings(series_id, settings.model_dump(exclude_unset=True))
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Series Asset Operations
# ============================================================

@router.get("/series/{series_id}/assets")
def get_series_assets(series_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Get all shared assets from a Series."""
    series, _ = pipeline.read_committed_series_view(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return signed_response({
        "characters": [c.model_dump() for c in series.characters],
        "scenes": [s.model_dump() for s in series.scenes],
        "props": [p.model_dump() for p in series.props],
    }, runtime=pipeline)


@router.post("/series/{series_id}/assets/generate")
def generate_series_asset(
    series_id: str,
    request: GenerateAssetRequest,
    background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Generate a single asset for a Series (async)."""
    from .pipeline import CommandValidationError, ProjectContextError

    try:
        series, task_id = active_pipeline.generate_series_asset(
            series_id,
            request.asset_id,
            request.asset_type,
            request.style_preset,
            request.reference_image_url,
            request.style_prompt,
            request.generation_type,
            request.prompt,
            request.apply_style,
            request.negative_prompt,
            request.batch_size,
            request.model_name,
            request.aspect_ratio,
        )
        background_tasks.add_task(active_pipeline.process_asset_generation_task, task_id)
        response_data = series.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data, runtime=active_pipeline)
    except CommandValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/series/{series_id}/assets/toggle_lock")
def toggle_series_asset_lock(series_id: str, request: ToggleLockRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Toggle the locked status of a Series asset."""
    try:
        series = pipeline.toggle_series_asset_lock(series_id, request.asset_id, request.asset_type)
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/series/{series_id}/assets/toggle_starred")
def toggle_series_asset_starred(series_id: str, request: ToggleLockRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Toggle the starred (library shortlist) status of a Series asset."""
    try:
        series = pipeline.toggle_series_asset_starred(series_id, request.asset_id, request.asset_type)
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/series/{series_id}/assets/update_image")
def update_series_asset_image(series_id: str, request: UpdateAssetImageRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Update a Series asset's image URL."""
    try:
        series = pipeline.update_series_asset_image(series_id, request.asset_id, request.asset_type, request.image_url)
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/series/{series_id}/assets/update_attributes")
def update_series_asset_attributes(series_id: str, request: UpdateAssetAttributesRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Update arbitrary attributes of a Series asset."""
    try:
        series = pipeline.update_series_asset_attributes(
            series_id, request.asset_id, request.asset_type, request.attributes
        )
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ImportAssetsRequest(BaseModel):
    source_series_id: str
    asset_ids: List[str]


# R2V v2 Phase 5 — quick-create CRUD for series-shared assets.
# Used by Cast step's "+ 新角色 / 新场景 / 新道具" modal. Optional
# `image_url` lets the user attach a pre-uploaded master sheet so the
# new asset isn't blank.
class CreateSeriesAssetRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    persona: Optional[str] = ""        # characters only — grouping label
    image_url: Optional[str] = None    # optional uploaded master sheet
    voice_id: Optional[str] = None     # characters only — TTS voice binding


def _setting_http_error(exc: ValueError) -> HTTPException:
    from .pipeline import CommandValidationError
    if isinstance(exc, (CommandValidationError, ValidationError)):
        return HTTPException(status_code=422, detail="Settings must contain valid values and UTF-8 text")
    return HTTPException(status_code=404, detail=str(exc))


def _create_series_asset(pipeline, series_id: str, asset_type: str, request: CreateSeriesAssetRequest):
    try:
        return signed_response(pipeline.create_series_asset(series_id, asset_type, **request.model_dump()), runtime=pipeline)
    except ValueError as exc:
        raise _setting_http_error(exc) from exc


@router.post("/series/{series_id}/characters")
def create_series_character(series_id: str, request: CreateSeriesAssetRequest,
    pipeline: Any = Depends(get_pipeline),
):
    return _create_series_asset(pipeline, series_id, "character", request)


@router.post("/series/{series_id}/scenes")
def create_series_scene(series_id: str, request: CreateSeriesAssetRequest,
    pipeline: Any = Depends(get_pipeline),
):
    return _create_series_asset(pipeline, series_id, "scene", request)


@router.post("/series/{series_id}/props")
def create_series_prop(series_id: str, request: CreateSeriesAssetRequest,
    pipeline: Any = Depends(get_pipeline),
):
    return _create_series_asset(pipeline, series_id, "prop", request)


@router.post("/series/{series_id}/assets/import")
def import_series_assets(series_id: str, request: ImportAssetsRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Deep-copy assets from another Series into this one."""
    try:
        series, imported_ids, skipped_ids = pipeline.import_assets_from_series(series_id, request.source_series_id, request.asset_ids)
        return signed_response(series, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Global Asset Library (project-independent shared pool) — CRUD + promote
# ============================================================
# Mirrors the /series/{id}/assets endpoints, one level up: a curated,
# project-independent pool any project can reference. All mutations route
# through the ComicGenPipeline.*_library_asset methods so the Playground
# "录入资产库" flow stays consistent with these endpoints.

class CreateLibraryAssetRequest(BaseModel):
    asset_type: str                    # "character" | "scene" | "prop"
    name: str
    description: Optional[str] = ""
    persona: Optional[str] = ""        # characters only — grouping label
    image_url: Optional[str] = None    # optional pre-uploaded master image
    voice_id: Optional[str] = None     # characters only — TTS voice binding


class UpdateLibraryAssetRequest(BaseModel):
    # Generic patch — only the fields the client actually sends are applied
    # (PATCH semantics; PUT here is lenient/partial).
    name: Optional[str] = None
    description: Optional[str] = None
    persona: Optional[str] = None
    image_url: Optional[str] = None
    voice_id: Optional[str] = None
    starred: Optional[bool] = None
    locked: Optional[bool] = None
    visual_weight: Optional[int] = None


class PromoteAssetRequest(BaseModel):
    source_kind: str   # "project" | "series"
    source_id: str
    asset_type: str    # "character" | "scene" | "prop"
    asset_id: str


class ForkFromLibraryRequest(BaseModel):
    asset_type: str          # "character" | "scene" | "prop"
    library_asset_id: str    # id of the source asset in the global library


@router.get("/library/assets")
def get_library_assets(
    pipeline: Any = Depends(get_pipeline),
):
    """List all assets in the global shared pool."""
    lib = pipeline.list_library_assets()
    return signed_response({
        "revision": lib.revision,
        "characters": [c.model_dump() for c in lib.characters],
        "scenes": [s.model_dump() for s in lib.scenes],
        "props": [p.model_dump() for p in lib.props],
    }, runtime=pipeline)


@router.post("/library/assets")
def create_library_asset(request: CreateLibraryAssetRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Create a new asset in the global shared pool."""
    try:
        payload = request.model_dump(exclude={"asset_type"})
        asset = pipeline.create_library_asset(request.asset_type, payload)
        return signed_response(asset.model_dump(), runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/library/assets/upload")
def upload_library_asset_image(
    file: UploadFile = File(...),
    active_pipeline: Any = Depends(get_pipeline),
):
    """Upload an image to use as a global library asset's master image.

    Saves the file under output/uploads/ (served via the /files static mount)
    and returns {"image_url": <path-or-URL the frontend can load>}. When OSS
    is configured the returned URL is the (signed) OSS URL; otherwise a local
    relative path "uploads/<name>" resolvable through the frontend's
    getAssetUrl helper. The caller then passes this image_url to
    POST /library/assets (image_url=...) or PATCH /library/assets/{type}/{id}
    to attach it to a library asset. Mirrors the generic /upload endpoint but
    returns the {image_url} contract the library UI expects.
    """
    try:
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        upload_dir = Path(active_pipeline.data_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(upload_dir / filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        # Prefer OSS when configured (signed), else fall back to local path.
        oss_url = _runtime_uploader(active_pipeline).upload_image(file_path)
        if oss_url:
            return signed_response({"image_url": oss_url}, runtime=active_pipeline)
        return {"image_url": f"uploads/{filename}"}
    except Exception as e:
        logger.exception("upload_library_asset_image failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.api_route("/library/assets/{asset_type}/{asset_id}", methods=["PUT", "PATCH"])
def update_library_asset(asset_type: str, asset_id: str, request: UpdateLibraryAssetRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Patch a global library asset (only the provided fields are applied)."""
    try:
        patch = request.model_dump(exclude_unset=True)
        asset = pipeline.update_library_asset(asset_type, asset_id, patch)
        return signed_response(asset.model_dump(), runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/library/assets/{asset_type}/{asset_id}")
def delete_library_asset(asset_type: str, asset_id: str, force: bool = False,
    pipeline: Any = Depends(get_pipeline),
):
    """Delete an asset from the global shared pool.

    Reference-integrity (design Q2): if any storyboard frame in any project or
    series still references this asset (via scene_id / character_ids /
    prop_ids), the delete is refused with HTTP 409 and the referrers are
    listed — unless ``force=true`` is passed, which deletes anyway and leaves
    those references dangling (the asset resolver simply drops the unknown id).
    """
    try:
        pipeline.delete_library_asset(asset_type, asset_id, force=force)
        return {"status": "deleted", "asset_type": asset_type, "id": asset_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        if e.__class__.__name__ == "LibraryAssetInUseError":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "library_asset_in_use",
                    "message": str(e),
                    "asset_type": e.asset_type,
                    "asset_id": e.asset_id,
                    "references": e.references,
                    "hint": "Pass ?force=true to delete anyway.",
                },
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/library/assets/promote")
def promote_asset_to_library(request: PromoteAssetRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Deep-copy an asset from a project or series into the global pool."""
    try:
        asset = pipeline.promote_asset_to_library(
            request.source_kind, request.source_id, request.asset_type, request.asset_id
        )
        return signed_response(asset.model_dump(), runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/assets/fork_from_library")
def fork_asset_from_library(script_id: str, request: ForkFromLibraryRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Fork (deep-copy) a global library asset into this project as an
    independent, editable local copy with a fresh id (design Q3, 按需 fork).

    Under live-reference (D1 活引用) semantics a project references shared
    library assets directly; this endpoint materializes a project-owned copy
    so subsequent edits no longer affect the shared original. Returns the new
    project-local asset.
    """
    try:
        asset = pipeline.fork_library_asset_to_project(
            script_id, request.asset_type, request.library_asset_id
        )
        return signed_response(asset.model_dump(), runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# File Import & Episode Splitting
# ============================================================

@router.post("/series/import/preview")
async def import_file_preview(
    file: UploadFile = File(...),
    suggested_episodes: int = 3,
    pipeline: Any = Depends(get_pipeline),
):
    """Upload a txt/md file and get LLM episode split preview."""
    if suggested_episodes < 1 or suggested_episodes > 50:
        raise HTTPException(status_code=400, detail="建议集数应在 1-50 之间")
    try:
        content_bytes = await file.read(MAX_IMPORT_TEXT_BYTES + 1)
        if len(content_bytes) > MAX_IMPORT_TEXT_BYTES:
            raise HTTPException(status_code=413, detail="导入文件不能超过 2 MiB")
        text = content_bytes.decode("utf-8")
        if not text.strip():
            raise HTTPException(status_code=400, detail="文件内容为空")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            partial(pipeline.preview_series_import, text, file.filename or "", suggested_episodes)
        )
    except HTTPException:
        raise
    except ImportPreviewUnavailable as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception("File import preview failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/series/import/confirm")
async def import_file_confirm(request: ConfirmImportRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Confirm the episode split and create Series + Episodes."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(
                pipeline.create_series_from_import,
                request.title,
                "",
                request.episodes,
                request.description,
                import_id=request.import_id,
            )
        )
        return signed_response(result, runtime=pipeline)
    except ImportPreviewUnavailable as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception("Import confirm failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/series/import/{import_id}")
def import_file_status(import_id: str, pipeline: Any = Depends(get_pipeline)):
    try:
        return signed_response(pipeline.read_series_import(import_id), runtime=pipeline)
    except ImportPreviewUnavailable as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


class EnvConfig(ProviderRoutingConfig):
    DASHSCOPE_API_KEY: Optional[str] = None
    ALIBABA_CLOUD_ACCESS_KEY_ID: Optional[str] = None
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: Optional[str] = None
    OSS_BUCKET_NAME: Optional[str] = None
    OSS_ENDPOINT: Optional[str] = None
    OSS_BASE_PATH: Optional[str] = None
    OSS_ENABLE: bool = True
    KLING_ACCESS_KEY: Optional[str] = None
    KLING_SECRET_KEY: Optional[str] = None
    VIDU_API_KEY: Optional[str] = None
    MULEROUTER_API_KEY: Optional[str] = None
    endpoint_overrides: Dict[str, str] = Field(default_factory=dict)


def _normalize_provider_mode(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized in (ProviderBackend.DASHSCOPE.value, ProviderBackend.VENDOR.value):
        return normalized
    return ProviderBackend.DASHSCOPE.value


def get_user_config_path(user_data_dir: str | os.PathLike[str] | None = None) -> str:
    """
    Returns the path to the user config file.
    - Development mode: Uses .env in project root
    - Packaged app mode: Uses ~/.lumen-x/config.json
    """
    from ...utils import get_user_data_dir
    if user_data_dir is not None:
        return os.path.join(str(Path(user_data_dir).expanduser()), "config.json")
    
    # Check if running in packaged mode (e.g., via environment variable or frozen check)
    is_packaged = os.getenv("LUMEN_X_PACKAGED", "false").lower() == "true" or getattr(sys, 'frozen', False)
    
    if is_packaged:
        # Use user home directory for packaged app
        config_dir = get_user_data_dir()
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "config.json")
    else:
        # Use .env in project root for development
        # Get absolute path to project root (api.py is in src/apps/comic_gen/)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(project_root, ".env")



_CONFIG_SAVE_LOCK = threading.RLock()


def _read_json_config(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise StorageError("Configuration file cannot be a symlink")
    config = read_json(path, dict)
    if not isinstance(config, dict):
        raise StorageError("Invalid configuration storage; original file preserved")
    return validate_environment(config)


def load_user_config(user_data_dir: str | os.PathLike[str] | None = None):
    """Read owned settings without publishing credentials into process state."""
    path = Path(get_user_config_path(user_data_dir=user_data_dir))
    if path.suffix == ".json":
        with _CONFIG_SAVE_LOCK:
            return _read_json_config(path)
    return validate_environment({key: value for key, value in dotenv_values(path).items() if value is not None})


def save_user_config(config_dict: dict, *, user_data_dir=None, remove_keys=()):
    """Prepare updates/removals together, then replace the configuration once.

    This lock serializes commands in this process, not independent services.
    The caller publishes its own runtime snapshot only after this save succeeds.
    """
    with _CONFIG_SAVE_LOCK:
        path = Path(get_user_config_path(user_data_dir=user_data_dir))
        if path.is_symlink():
            raise StorageError("Configuration file cannot be a symlink")
        if path.suffix == ".json":
            existing = _read_json_config(path)
            existing.update(config_dict)
            for key in remove_keys:
                existing.pop(key, None)
            atomic_write_json(path, existing)
            return

        # Let the existing dotenv implementation preserve quoting, multiline
        # values and comments in a private stage, never in the active file.
        from dotenv import unset_key
        try:
            original = path.read_text(encoding="utf-8") if path.exists() else ""
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".lumenx-config-", dir=path.parent) as stage_dir:
                stage = Path(stage_dir) / "settings.env"
                stage.write_text(original, encoding="utf-8")
                for key, value in config_dict.items():
                    set_key(str(stage), key, value)
                for key in remove_keys:
                    unset_key(str(stage), key)
                replacement = stage.read_text(encoding="utf-8")
            atomic_write_text(path, replacement)
        except (OSError, UnicodeError) as exc:
            raise StorageError("Cannot prepare configuration replacement") from exc


def remove_user_config_keys(keys: list, *, user_data_dir=None):
    """Removes keys from the persisted config file."""
    if keys:
        save_user_config({}, user_data_dir=user_data_dir, remove_keys=keys)


@router.get("/config/info")
def get_config_info(request: Request):
    user_data_dir = request.app.state.user_data_dir
    """Returns information about the current config storage mode."""
    config_path = get_user_config_path(user_data_dir=user_data_dir)
    is_packaged = os.getenv("LUMEN_X_PACKAGED", "false").lower() == "true" or getattr(sys, 'frozen', False)
    return {
        "mode": "packaged" if user_data_dir is not None or is_packaged else "development",
        "config_path": config_path,
        "config_exists": os.path.exists(config_path)
    }


@router.post("/config/env")
def update_env_config(config: EnvConfig, request: Request):
    user_data_dir = request.app.state.user_data_dir
    """Updates environment configuration and saves to config file."""
    try:
        raw_config = config.model_dump(exclude_unset=True)

        # Extract endpoint_overrides and flatten into config_dict
        endpoint_overrides = raw_config.pop("endpoint_overrides", {})

        # Filter out None values and serialize enum values as plain strings.
        config_dict: Dict[str, str] = {}
        for key, value in raw_config.items():
            if value is None:
                continue
            if isinstance(value, bool):
                # Booleans (e.g. OSS_ENABLE) persist as "true"/"false" strings so
                # they round-trip through os.environ and the .env/config.json store.
                config_dict[key] = "true" if value else "false"
            elif isinstance(value, ProviderBackend):
                config_dict[key] = value.value
            else:
                config_dict[key] = value

        # Secret masking guard: GET /config/env returns secrets masked with the
        # bullet sentinel. If the frontend re-submits an unchanged secret it will
        # still contain bullets — skip it so we don't overwrite the real stored
        # key with the mask. Only genuinely edited (bullet-free) values persist.
        for field in list(config_dict.keys()):
            if field in SECRET_FIELDS and _MASK_CHAR in str(config_dict[field]):
                config_dict.pop(field, None)

        # Process endpoint overrides: validate keys against known providers
        from ...utils.endpoints import PROVIDER_DEFAULTS
        allowed_keys = {f"{p}_BASE_URL" for p in PROVIDER_DEFAULTS}
        keys_to_remove = []
        for env_key, value in endpoint_overrides.items():
            if env_key not in allowed_keys:
                logger.warning(f"Ignoring unknown endpoint key: {env_key}")
                continue
            if value and value.strip():
                config_dict[env_key] = value.strip()
            else:
                keys_to_remove.append(env_key)

        for value in config_dict.values():
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise HTTPException(status_code=422, detail="Configuration must contain valid UTF-8 text") from exc
            if "\0" in value:
                raise HTTPException(status_code=422, detail="Configuration cannot contain null characters")

        with _provider_runtime_lock(request.app):
            request.app.state.runtime_environment.update(
                config_dict, remove_keys=keys_to_remove,
                persist=lambda updates, removals: save_user_config(
                    updates, user_data_dir=user_data_dir, remove_keys=removals,
                ),
            )
            _refresh_managed_provider_recovery_runtime(request.app)

        config_path = get_user_config_path(user_data_dir=user_data_dir)
        return {"status": "success", "message": f"Configuration saved to {config_path}"}
    except (StorageError, HTTPException):
        raise
    except Exception as e:
        logger.exception("Failed to save environment configuration")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/projects/{script_id}")
def get_project(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Retrieves a project by ID. When the project belongs to a
    Series, the response merges series-shared characters / scenes /
    props on top of the episode-local lists. Each item carries a
    `source` field ("episode" | "series" | "global") so the frontend can
    visually distinguish where the asset lives and route writes
    appropriately (per A2 design decision — shared writes default to
    the series side; local writes stay episode-side; the helper
    `_find_asset_with_source` in pipeline routes mutations correctly).

    Response model dropped from `Script` because the `source` field
    is a presentation-layer concern (never persisted, derived from
    container membership at read time)."""
    script, series, lib = pipeline.read_committed_project_view(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")

    payload = script.model_dump()
    # These versions describe the same detached capture as the merged assets.
    # A missing linked series is distinct from an unbound project or revision 0.
    payload["read_versions"] = {
        "project": script.revision,
        "series": {"id": script.series_id, "revision": series.revision if series else None}
        if script.series_id else None,
        "library": lib.revision,
    }

    # Episode-local entries always carry source="episode".
    for asset_list in (payload.get("characters", []),
                      payload.get("scenes", []),
                      payload.get("props", [])):
        for item in asset_list:
            item["source"] = "episode"

    # Merge series-shared assets on top (any id not already present
    # locally — episode-local overrides series). Without this step
    # the user "loses" characters when switching between episodes of
    # the same series, because the series shared pool isn't
    # reflected on each episode's response.
    if script.series_id:
        if series:
            ep_char_ids = {c.id for c in script.characters}
            ep_scene_ids = {s.id for s in script.scenes}
            ep_prop_ids = {p.id for p in script.props}
            for ch in series.characters:
                if ch.id not in ep_char_ids:
                    d = ch.model_dump()
                    d["source"] = "series"
                    payload["characters"].append(d)
            for sc in series.scenes:
                if sc.id not in ep_scene_ids:
                    d = sc.model_dump()
                    d["source"] = "series"
                    payload["scenes"].append(d)
            for pr in series.props:
                if pr.id not in ep_prop_ids:
                    d = pr.model_dump()
                    d["source"] = "series"
                    payload["props"].append(d)

    # Merge the project-independent global asset library underneath as
    # the lowest layer. Any id not already present from the episode or
    # series layers is appended with source="global" (read-time only —
    # never written back to projects.json). When the library is empty
    # this is a no-op and the response is byte-identical to before.
    if lib.characters or lib.scenes or lib.props:
        seen_char_ids = {c["id"] for c in payload["characters"]}
        seen_scene_ids = {s["id"] for s in payload["scenes"]}
        seen_prop_ids = {p["id"] for p in payload["props"]}
        for ch in lib.characters:
            if ch.id not in seen_char_ids:
                d = ch.model_dump()
                d["source"] = "global"
                payload["characters"].append(d)
        for sc in lib.scenes:
            if sc.id not in seen_scene_ids:
                d = sc.model_dump()
                d["source"] = "global"
                payload["scenes"].append(d)
        for pr in lib.props:
            if pr.id not in seen_prop_ids:
                d = pr.model_dump()
                d["source"] = "global"
                payload["props"].append(d)
    payload["merged_video_freshness"] = pipeline.merge_video_freshness(script)
    return signed_response(payload, pipeline=pipeline, runtime=pipeline)



@router.delete("/projects/{script_id}")
def delete_project(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Deletes a project by ID. WARNING: This permanently removes the project from backend storage."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    
    deleted = pipeline.delete_project(script_id)
    return {"status": "deleted", "id": script_id, "title": deleted.title}


# ─────────────────────────────────────────────────────────────────────
# R2V v2 Phase 4 — Cross-episode asset reconcile
# ─────────────────────────────────────────────────────────────────────
def _name_match_confidence(local_name: str, series_name: str) -> int:
    """0-100 simple confidence score.
    Phase 4 v1: exact match = 100, substring match = 75, none = 0.
    Future: embedding-based semantic similarity on `description`."""
    a, b = (local_name or "").strip().lower(), (series_name or "").strip().lower()
    if not a or not b:
        return 0
    if a == b:
        return 100
    if a in b or b in a:
        return 75
    return 0


@router.get("/projects/{script_id}/reconcile/suggestions")
def reconcile_suggestions(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Compute match suggestions for the current episode's just-extracted
    entities vs the parent series's shared asset library.

    Returned shape:
    {
      "characters": [
        { local_id, local_name, suggested_series_id|null, suggested_series_name|null, confidence }
      ],
      "scenes": [...],
      "props": [...],
    }
    Frontend uses this to render the ReconcileModal after script parse.
    """
    script, series, _ = pipeline.read_committed_project_view(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        # Standalone projects have no series library to reconcile against
        return {"characters": [], "scenes": [], "props": []}
    if not series:
        return {"characters": [], "scenes": [], "props": []}

    def best_match(local_name: str, pool: list, key: str = "name"):
        best_id, best_name, best_conf = None, None, 0
        for item in pool:
            sname = getattr(item, key, "") if hasattr(item, key) else item.get(key, "")
            conf = _name_match_confidence(local_name, sname)
            if conf > best_conf:
                best_id, best_name, best_conf = item.id if hasattr(item, "id") else item.get("id"), sname, conf
        return best_id, best_name, best_conf

    def build(local_pool, series_pool):
        result = []
        for local in local_pool:
            sid, sname, conf = best_match(local.name, series_pool)
            result.append({
                "local_id": local.id,
                "local_name": local.name,
                "suggested_series_id": sid if conf > 0 else None,
                "suggested_series_name": sname if conf > 0 else None,
                "confidence": conf,
            })
        return result

    return {
        "characters": build(script.characters, series.characters),
        "scenes": build(script.scenes, series.scenes),
        "props": build(script.props, series.props),
    }


class ReconcileAction(BaseModel):
    local_id: str
    action: str  # "merge_into_series" | "create_new_in_series" | "skip"
    target_series_id: Optional[str] = None


class ApplyReconcileRequest(BaseModel):
    characters: List[ReconcileAction] = Field(default_factory=list)
    scenes: List[ReconcileAction] = Field(default_factory=list)
    props: List[ReconcileAction] = Field(default_factory=list)


@router.post("/projects/{script_id}/reconcile/apply")
def reconcile_apply(script_id: str, request: ApplyReconcileRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Apply user-confirmed reconcile decisions.
    - merge_into_series: drop the local episode entity, replace all
      frame references with target_series_id (the series-shared asset).
    - create_new_in_series: promote the local entity to series scope.
    - skip: no-op (keep local-only).
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        raise HTTPException(status_code=400, detail="Project not in a series")
    series = pipeline.get_series(script.series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    script = pipeline.reconcile_project_assets(script_id, request.model_dump())
    return signed_response(script, runtime=pipeline)


# ─────────────────────────────────────────────────────────────────────
# R2V v2 Phase 3 — "Previously on..." (上回书说到)
# ─────────────────────────────────────────────────────────────────────
def _prev_text_revision(prev_text: str) -> str:
    from .pipeline import project_text_revision

    return project_text_revision(prev_text)


@router.get("/projects/{script_id}/previous_episode")
def get_previous_episode_summary(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Return previous-episode raw snippet + AI summary cache state for
    the "Previously on..." right rail in the Script step.

    Response shape:
        {
          "has_previous": bool,
          "previous_episode_id": str | null,
          "previous_episode_title": str | null,
          "raw_snippet": str  (last ~600 chars of previous original_text),
          "ai_summary": str | null  (cached summary if fresh),
          "ai_summary_stale": bool  (true when prev text changed since cache),
        }
    """
    from .pipeline import ProjectContextError

    try:
        script, series, prev = pipeline.read_previous_episode_context(script_id)
    except ProjectContextError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 400, detail=str(exc)) from exc

    if not script.series_id or not series:
        return {
            "has_previous": False,
            "previous_episode_id": None,
            "previous_episode_title": None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }

    try:
        idx = series.episode_ids.index(script_id)
    except ValueError:
        idx = -1
    prev_id = series.episode_ids[idx - 1] if idx > 0 else None
    if idx <= 0 or not prev or not (prev.original_text or "").strip():
        return {
            "has_previous": False,
            "previous_episode_id": prev_id,
            "previous_episode_title": prev.title if prev else None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }

    raw_snippet = (prev.original_text or "")[-800:]
    current_rev = _prev_text_revision(prev.original_text or "")
    is_stale = (
        script.last_episode_summary_cache is not None
        and script.last_episode_summary_revision != current_rev
    )
    # R2V v2 P2-a — last frames of previous episode for Storyboard
    # cross-step reference. We surface the *last 4 frames* with their
    # selected video / T2I image so authors composing this episode's
    # opening shots can see what the previous episode ended on.
    last_frames = []
    for f in (prev.frames or [])[-4:]:
        thumb_url = None
        # Prefer the selected video's poster/url, fall back to T2I image
        if getattr(f, "video_url", None):
            thumb_url = f.video_url
        elif getattr(f, "rendered_image_url", None):
            thumb_url = f.rendered_image_url
        elif getattr(f, "image_url", None):
            thumb_url = f.image_url
        elif getattr(f, "t2i_image_urls", None):
            urls = f.t2i_image_urls
            if urls:
                idx = getattr(f, "t2i_selected_index", 0) or 0
                idx = max(0, min(idx, len(urls) - 1))
                thumb_url = urls[idx]
        last_frames.append({
            "id": f.id,
            "action_description": (f.action_description or "")[:120],
            "thumbnail_url": thumb_url,
            "video_url": getattr(f, "video_url", None),
        })
    return {
        "has_previous": True,
        "previous_episode_id": prev_id,
        "previous_episode_title": prev.title,
        "raw_snippet": raw_snippet,
        "ai_summary": script.last_episode_summary_cache,
        "ai_summary_stale": is_stale,
        "last_frames": last_frames,
    }


@router.post("/projects/{script_id}/previous_episode/summary")
def generate_previous_episode_summary(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """On-demand AI summary of the previous episode (qwen3.6-plus).
    Per Q7-followup design: this is *user-triggered*, not auto, to
    respect LLM quota and user intent. Result is cached on the current
    episode's Script record with a revision marker for invalidation.
    """
    from .pipeline import ProjectContextError, ProjectGenerationError

    try:
        return pipeline.generate_previous_episode_summary(script_id)
    except ProjectContextError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 400, detail=str(exc)) from exc
    except ProjectGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/series/{series_id}/characters/{character_id}/appearances")
def get_character_appearances(series_id: str, character_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """R2V v2 P1-c — Cross-episode character appearance summary.
    Used by Script step's @mention helper popover. Returns chronological
    list of episodes where the character appears + per-episode frame
    count + aggregated 'last seen' info."""
    series, episodes = pipeline.read_committed_series_view(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    episode_by_id = {episode.id: episode for episode in episodes}
    char = next((c for c in series.characters if c.id == character_id), None)
    if not char:
        # Try as episode-local character — maybe id is from any episode
        for ep_id in series.episode_ids:
            ep = episode_by_id.get(ep_id)
            if ep:
                local = next((c for c in ep.characters if c.id == character_id), None)
                if local:
                    char = local
                    break
    if not char:
        raise HTTPException(status_code=404, detail="Character not found in series scope")
    appearances = []
    total_frames = 0
    for ep_id in series.episode_ids:
        ep = episode_by_id.get(ep_id)
        if not ep:
            continue
        count = sum(1 for f in ep.frames if character_id in (f.character_ids or []))
        if count > 0:
            appearances.append({
                "episode_id": ep_id,
                "episode_number": ep.episode_number,
                "episode_title": ep.title,
                "frame_count": count,
            })
            total_frames += count
    return {
        "character": {
            "id": char.id,
            "name": char.name,
            "persona": char.persona,
            "description": char.description,
        },
        "appearances": appearances,
        "total_frames": total_frames,
    }


# R2V v2 P2-b — "Next episode hook" prediction (forward-looking).
@router.get("/projects/{script_id}/next_hook")
def get_next_episode_hook(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Return cached hook prediction state for the Script step's
    'Hook for next' panel. Always returns the current revision marker
    so the frontend can decide if a refresh is warranted."""
    try:
        script = pipeline.read_committed_script(script_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")
    current_text = script.original_text or ""
    current_rev = _prev_text_revision(current_text)
    is_stale = (
        script.next_hook_cache is not None
        and script.next_hook_revision != current_rev
    )
    return {
        "has_text": bool(current_text.strip()),
        "hook": script.next_hook_cache,
        "stale": is_stale,
    }


@router.post("/projects/{script_id}/next_hook")
def generate_next_episode_hook(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """On-demand AI prediction of the next-episode opening hook based
    on THIS episode's ending. User-triggered (no auto-generate to
    respect LLM quota)."""
    from .pipeline import ProjectContextError, ProjectGenerationError

    try:
        return pipeline.generate_next_episode_hook(script_id)
    except ProjectContextError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/projects/{script_id}/next_hook")
def update_next_episode_hook(script_id: str, payload: dict,
    pipeline: Any = Depends(get_pipeline),
):
    """Manually edit / clear the next-hook cache."""
    try:
        return pipeline.update_next_episode_hook(script_id, payload.get("hook"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{script_id}/sync_descriptions", response_model=Script)
def sync_descriptions(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """
    Syncs entity descriptions from Script module to Assets module.
    
    This endpoint forces a refresh of the project data, ensuring that any
    description changes made in the Script module are reflected in Assets.
    
    Note: This only syncs descriptions; generated images/videos are preserved.
    """
    try:
        updated_script = pipeline.sync_descriptions_from_script_entities(script_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AddCharacterRequest(BaseModel):
    name: str
    description: str

@router.post("/projects/{script_id}/characters", response_model=Script)
def add_character(script_id: str, request: AddCharacterRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Adds a new character."""
    try:
        updated_script = pipeline.add_character(script_id, request.name, request.description)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/projects/{script_id}/characters/{char_id}", response_model=Script)
def delete_character(script_id: str, char_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Deletes a character."""
    try:
        updated_script = pipeline.delete_character(script_id, char_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class AddSceneRequest(BaseModel):
    name: str
    description: str

@router.post("/projects/{script_id}/scenes", response_model=Script)
def add_scene(script_id: str, request: AddSceneRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Adds a new scene."""
    try:
        updated_script = pipeline.add_scene(script_id, request.name, request.description)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/projects/{script_id}/scenes/{scene_id}", response_model=Script)
def delete_scene(script_id: str, scene_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Deletes a scene."""
    try:
        updated_script = pipeline.delete_scene(script_id, scene_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class UpdateStyleRequest(BaseModel):
    style_preset: str
    style_prompt: Optional[str] = None


@router.patch("/projects/{script_id}/style", response_model=Script)
def update_project_style(script_id: str, request: UpdateStyleRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates the global style settings for a project."""
    try:
        updated_script = pipeline.update_project_style(
            script_id,
            request.style_preset,
            request.style_prompt
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise _setting_http_error(e) from e
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/generate_assets", response_model=Script)
def generate_assets(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Generate the batch and report the command's committed result."""
    from .pipeline import CommandValidationError, ProjectContextError, ProjectGenerationError
    try:
        updated_script = pipeline.generate_assets(script_id)
        return signed_response(updated_script, runtime=pipeline)
    except CommandValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class GenerateMotionRefRequest(BaseModel):
    """Request model for generating Motion Reference videos."""
    asset_id: str
    asset_type: str  # 'full_body' | 'head_shot' for characters; 'scene' | 'prop' for scenes and props
    prompt: Optional[str] = None
    audio_url: Optional[str] = None  # Driving audio for lip-sync
    duration: int = Field(5, ge=1, strict=True)
    batch_size: int = Field(1, ge=1, strict=True)
    client_request_id: Optional[str] = Field(None, strict=True)


class MotionReferenceRequest(BaseModel):
    """Strict public command; the old compatible entrance remains separate."""

    model_config = {"extra": "forbid"}

    asset_id: str
    asset_type: Literal["full_body", "head_shot", "scene", "prop"]
    prompt: Optional[str] = None
    audio_url: Optional[str] = None
    duration: int = Field(5, ge=1, strict=True)
    batch_size: int = Field(1, ge=1, strict=True)
    client_request_id: StrictStr
    expected_target: StrictStr = Field(..., pattern=r"^[0-9a-f]{64}$")

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id(cls, value: str) -> str:
        return validate_client_request_id(value)


@router.get("/projects/{script_id}/motion_ref_requests")
def read_motion_ref_eligibility(
    script_id: str, asset_id: str, asset_type: Literal["full_body", "head_shot", "scene", "prop"],
    batch_size: int = Query(1, ge=1), has_audio: bool = False,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Read the actual selected owner and one opaque author precondition."""
    from .pipeline import CommandValidationError, ProjectContextError

    try:
        result = active_pipeline.motion_reference_service.preflight(
            script_id, asset_id, asset_type, batch_size=batch_size, has_audio=has_audio,
        )
    except CommandValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProjectContextError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc)) from exc
    except ValueError as exc:
        if str(exc) == "provider_recovery_runtime_missing":
            return JSONResponse(status_code=409, content={"code": "provider_recovery_unavailable"})
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.post("/projects/{script_id}/motion_ref_requests")
def create_motion_ref_request(
    script_id: str, request: MotionReferenceRequest, background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Admit a durable original request; ineligibility never invokes legacy."""
    from .pipeline import CommandValidationError, ProjectContextError

    try:
        admitted = active_pipeline.motion_reference_service.admit(
            script_id, request.model_dump(exclude={"client_request_id", "expected_target"}),
            request.client_request_id, expected_target=request.expected_target,
        )
    except MotionReferenceAdmissionRejected as exc:
        content = {
            "code": exc.code, "accepted": False, "client_request_id": exc.client_request_id,
            "detail": "The command was not accepted. Review the current asset before a new action.",
        }
        if exc.reason is not None:
            content["reason"] = exc.reason
        return JSONResponse(status_code=exc.status_code, content=content)
    except CommandValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProjectContextError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc)) from exc
    except ValueError as exc:
        if str(exc) == "provider_recovery_runtime_missing":
            return JSONResponse(status_code=409, content={"code": "provider_recovery_unavailable"})
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if admitted is None:
        raise RuntimeError("Strict motion admission cannot select the legacy entrance")
    for task_id in admitted.new_task_ids:
        background_tasks.add_task(active_pipeline.process_durable_motion_ref_task, script_id, task_id)
    result = active_pipeline.motion_reference_service.read_request(script_id, request.client_request_id)
    return signed_response({**result, "replayed": admitted.replayed}, runtime=active_pipeline)


@router.post("/projects/{script_id}/assets/generate_motion_ref")
def generate_motion_ref(
    script_id: str,
    request: GenerateMotionRefRequest,
    background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Generates a Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop)."""
    from .pipeline import CommandValidationError, ProjectContextError

    try:
        admitted = active_pipeline.motion_reference_service.admit(
            script_id, request.model_dump(exclude={"client_request_id"}), request.client_request_id,
        )
        if admitted is not None:
            for task_id in admitted.new_task_ids:
                background_tasks.add_task(active_pipeline.process_durable_motion_ref_task, script_id, task_id)
            return signed_response({
                **admitted.script.model_dump(), "_task_id": admitted.task_id,
                "_client_request_id": admitted.client_request_id, "_replayed": admitted.replayed,
                "_reason": admitted.reason,
            }, runtime=active_pipeline)
        script, task_id = active_pipeline.create_motion_ref_task(
            script_id=script_id,
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            prompt=request.prompt,
            audio_url=request.audio_url,
            duration=request.duration,
            batch_size=request.batch_size
        )
        
        # Add background processing
        background_tasks.add_task(active_pipeline.process_motion_ref_task, script_id, task_id)
        
        # Return script with task_id for frontend polling
        response_data = script.model_dump() if hasattr(script, 'model_dump') else script.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data, runtime=active_pipeline)

    except CommandValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{script_id}/motion_ref_requests/{client_request_id}")
def read_motion_ref_request(script_id: str, client_request_id: str,
                            active_pipeline: Any = Depends(get_pipeline)):
    """Read the original local receipt, never provider query/fetch or dispatch."""
    try:
        result = active_pipeline.motion_reference_service.read_request(script_id, client_request_id)
    except ValueError:
        result = None
    if result is None:
        raise HTTPException(status_code=404, detail="Motion reference request not found")
    return signed_response(result, runtime=active_pipeline)


# === STORYBOARD DRAMATIZATION v2 ===

class AnalyzeToStoryboardRequest(BaseModel):
    """Request to analyze script text into storyboard frames."""
    text: str


@router.post("/projects/{script_id}/storyboard/analyze")
def analyze_to_storyboard(script_id: str, request: AnalyzeToStoryboardRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """
    Analyzes script text and generates storyboard frames using AI (Prompt B).
    Replaces existing frames with newly generated ones.
    """
    from .pipeline import ProjectGenerationError
    from .llm import PolishError

    try:
        updated_script = pipeline.analyze_text_to_frames(script_id, request.text)
        return signed_response(updated_script, runtime=pipeline)
    except PolishError as e:
        raise HTTPException(status_code=502, detail=_polish_error_response(e)) from e
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.error(f"Error in analyze_to_storyboard: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class RefinePromptRequest(BaseModel):
    """Request to refine a frame's prompt using AI."""
    frame_id: str
    raw_prompt: str
    assets: list = []  # List of asset references
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement


@router.post("/projects/{script_id}/storyboard/refine_prompt")
def refine_storyboard_prompt(script_id: str, request: RefinePromptRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """
    Refines a raw prompt into bilingual (CN/EN) prompts using AI (Prompt C).
    Returns the refined prompts and optionally updates the frame.

    SYNC handler — pipeline.refine_frame_prompt() does a blocking LLM call.
    Declared `def` (not `async def`) so FastAPI runs it on the threadpool
    and the event loop stays free for concurrent GETs.
    """
    from .pipeline import ProjectGenerationError
    from .llm import PolishError

    try:
        result = pipeline.refine_frame_prompt(
            script_id,
            request.frame_id,
            request.raw_prompt,
            request.assets,
            request.feedback,
        )
        return result
    except PolishError as e:
        raise HTTPException(status_code=502, detail=_polish_error_response(e)) from e
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.error(f"Error in refine_storyboard_prompt: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/frames/{frame_id}/refine")
def refine_single_frame(script_id: str, frame_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Phase 2: Refine a single coarse frame into a rich frame with structured fields."""
    from .pipeline import ProjectGenerationError
    from .llm import PolishError

    try:
        frame = pipeline.refine_frame(script_id, frame_id)
        if not frame:
            raise HTTPException(status_code=500, detail="Refine returned no result")
        return frame.model_dump() if hasattr(frame, 'model_dump') else frame.dict()
    except PolishError as e:
        raise HTTPException(status_code=502, detail=_polish_error_response(e)) from e
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.error(f"Error in refine_single_frame: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/storyboard/refine_batch")
def refine_storyboard_batch(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Phase 2: Batch refine all coarse frames. Streams SSE events."""
    from fastapi.responses import StreamingResponse

    try:
        pipeline.read_committed_script(script_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Script not found") from exc

    def event_stream():
        for event_type, data in pipeline.refine_batch_generator(script_id):
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/projects/{script_id}/generate_storyboard", response_model=Script)
def generate_storyboard(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Triggers storyboard generation."""
    from .pipeline import CommandValidationError, ProjectContextError, ProjectGenerationError

    try:
        updated_script = pipeline.generate_storyboard(script_id)
        return signed_response(updated_script, runtime=pipeline)
    except CommandValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/projects/{script_id}/generate_video", response_model=Script)
def generate_video(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Legacy script-wide video generation is unsupported; use video tasks."""
    try:
        updated_script = pipeline.generate_video(script_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/projects/{script_id}/generate_audio", response_model=Script)
def generate_audio(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Legacy compatibility route for dialogue audio batch generation."""
    from .pipeline import ProjectContextError
    try:
        updated_script = pipeline.generate_audio(script_id)
        return signed_response(updated_script, runtime=pipeline)
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



StrictFiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
VideoTaskBatchSize = Annotated[StrictInt, Field(ge=1, le=6)]


class CreateVideoTaskRequest(BaseModel):
    image_url: StrictStr
    prompt: StrictStr
    frame_id: Optional[StrictStr] = None
    duration: StrictInt = 5
    seed: Optional[StrictInt] = None
    resolution: StrictStr = "720p"
    generate_audio: StrictBool = False
    audio_url: Optional[StrictStr] = None
    prompt_extend: StrictBool = True
    negative_prompt: Optional[StrictStr] = None
    batch_size: VideoTaskBatchSize = 1
    model: StrictStr = "wan2.6-i2v"
    shot_type: StrictStr = "single"  # 'single' or 'multi' (only for wan2.6-i2v)
    generation_mode: Literal["i2v", "r2v"] = "i2v"  # 'i2v' (image-to-video) or 'r2v' (reference-to-video)
    reference_video_urls: List[StrictStr] = Field(default_factory=list)  # Reference video URLs for R2V (max 3)
    # Kling params
    mode: Optional[StrictStr] = None
    sound: Optional[StrictStr] = None
    cfg_scale: Optional[StrictFiniteFloat] = None
    # Vidu params
    vidu_audio: Optional[StrictBool] = None
    movement_amplitude: Optional[StrictStr] = None
    # HappyHorse params
    reference_image_urls: List[StrictStr] = Field(default_factory=list)  # Reference URLs for HH R2V (max 9)
    ratio: Optional[StrictStr] = None  # Aspect ratio for HH T2V/R2V
    # Watermark toggle (wan / kling / vidu / pixverse / happyhorse video).
    # None = leave to provider default; True/False = explicit user choice.
    watermark: Optional[StrictBool] = None
    # Source tab in the Storyboard R2V workbench. Distinct from
    # generation_mode (backend dispatcher hint) — used by the candidates
    # panel to group takes per UI tab on refresh.
    workbench_tab: Optional[Literal["t2i_i2v", "direct_r2v"]] = None


def process_video_task(active_pipeline: Any, script_id: str, task_id: str):
    """Background task to generate video.

    The pipeline method has its own try/except that flips status to
    "failed" on errors during generation. This outer wrapper is a
    belt-and-suspenders writeback for exceptions that escape *before*
    that inner handler armed (e.g. `get_script` raising, persistence
    layer crashing). Without it the task would stay forever-`pending`
    and the UI would show an eternal spinner.
    """
    try:
        active_pipeline.process_video_task(script_id, task_id)
    except StorageError:
        raise
    except Exception as e:
        logger.exception(f"Error processing video task {task_id}")
        try:
            active_pipeline.mark_video_task_failed(
                script_id, task_id, f"Background error: {e}"
            )
        except StorageError:
            raise
        except Exception:
            logger.exception(
                f"Could not mark video task {task_id} as failed after wrapper exception"
            )


class AnnotateVideoTaskRequest(BaseModel):
    """User-review annotations on a video task ("抽卡 review").
    Both fields optional so the same endpoint covers star-only,
    label-only, or both. `clear_label=True` explicitly removes
    the label (None on its own means "don't change")."""
    is_starred: Optional[bool] = None
    label: Optional[str] = None
    clear_label: bool = False


@router.patch("/projects/{script_id}/video_tasks/{task_id}/annotate", response_model=VideoTask)
def annotate_video_task(script_id: str, task_id: str, request: AnnotateVideoTaskRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Set the user's star + label on a video task. Used by Storyboard's
    candidates panel for shortlist marking (multi-select) and short
    free-text notes (≤20 chars, truncated server-side)."""
    task = pipeline.annotate_video_task(
        script_id,
        task_id,
        is_starred=request.is_starred,
        label=request.label,
        clear_label=request.clear_label,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Video task not found")
    return signed_response(task, runtime=pipeline)


class UpdateFrameWorkbenchRequest(BaseModel):
    """Storyboard R2V workbench state writeback. Every field optional;
    only what the caller passes gets updated. The server clamps lists
    and indices to safe ranges; rejects unknown enum values."""
    workbench_tab_mode: Optional[str] = None  # 't2i_i2v' | 'direct_r2v'
    t2i_image_urls: Optional[List[str]] = None  # full ordered history, server caps at 10 FIFO
    t2i_selected_index: Optional[int] = None  # active首帧 index, clamped to range
    workbench_generate_count: Optional[int] = None  # batch size, clamped to [1, 6]
    last_frame_url: Optional[StrictStr] = None  # managed local closing-frame image; null/blank clears


@router.patch("/projects/{script_id}/frames/{frame_id}/workbench", response_model=StoryboardFrame)
def update_frame_workbench(
    script_id: str, frame_id: str, request: UpdateFrameWorkbenchRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Persist Storyboard R2V workbench state onto a frame so it
    survives refresh and cross-device opens. Previously this state
    lived only in React component state and got lost on reload."""
    try:
        update_kwargs = {
            "workbench_tab_mode": request.workbench_tab_mode,
            "t2i_image_urls": request.t2i_image_urls,
            "t2i_selected_index": request.t2i_selected_index,
            "workbench_generate_count": request.workbench_generate_count,
        }
        if "last_frame_url" in request.model_fields_set:
            update_kwargs["last_frame_url"] = request.last_frame_url
        frame = pipeline.update_frame_workbench(
            script_id,
            frame_id,
            **update_kwargs,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if frame is None:
        raise HTTPException(status_code=404, detail="Script or frame not found")
    return signed_response(frame, runtime=pipeline)


@router.post("/projects/{script_id}/video_tasks/{task_id}/cancel", response_model=VideoTask)
def cancel_video_task(script_id: str, task_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Cancel locally only when provider work is proven unsubmitted."""
    try:
        current = pipeline.read_committed_script(script_id)
    except ValueError:
        current = None
    task = next((item for item in current.video_tasks if item.id == task_id), None) if current else None
    if task is not None and task.provider_execution is not None:
        try:
            cancelled = pipeline.cancel_provider_video_task(script_id, task_id)
        except StorageConflictError as exc:
            if str(exc) != "provider_cancel_unconfirmed":
                raise
            return JSONResponse(status_code=409, content={
                "code": "provider_cancel_unconfirmed",
                "detail": "Provider work may already be submitted; local cancellation cannot be confirmed.",
            })
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return signed_response(cancelled, runtime=pipeline)
    ok = pipeline.mark_video_task_failed(script_id, task_id, "Canceled by user")
    if not ok:
        try:
            current = pipeline.read_committed_script(script_id)
        except ValueError:
            current = None
        task = next((item for item in current.video_tasks if item.id == task_id), None) if current else None
        if task is not None and task.status == "needs_confirmation":
            raise HTTPException(status_code=409, detail="Video result is unconfirmed; local cancellation cannot confirm provider failure")
        raise HTTPException(
            status_code=404,
            detail="Video task not found or already completed",
        )
    try:
        script = pipeline.read_committed_script(script_id)
    except ValueError:
        script = None
    task = next(
        (t for t in (script.video_tasks if script else []) if t.id == task_id),
        None,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Video task not found")
    return signed_response(task, runtime=pipeline)


class ProviderVideoTaskRequest(BaseModel):
    """Preserve raw generation field presence until receipt lookup completes."""

    model_config = {"extra": "allow"}

    client_request_id: StrictStr
    batch_size: StrictInt = Field(1, ge=1, le=6)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id(cls, value: str) -> str:
        return validate_client_request_id(value)


class ProviderVideoAdmissionResponse(BaseModel):
    client_request_id: str
    replayed: bool
    task_ids: List[str]
    tasks: List[VideoTask]
    reason: Optional[str] = None


class ProviderVideoContinueRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_attempt_id: StrictStr = Field(..., min_length=1, max_length=128)
    expected_fingerprint: StrictStr = Field(..., pattern=r"^[0-9a-f]{64}$")


@router.post("/projects/{script_id}/motion_ref_requests/{client_request_id}/recover")
def recover_motion_ref_request(
    script_id: str, client_request_id: str, request: ProviderVideoContinueRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Explicit query/fetch/local repair of an original paid motion attempt."""
    try:
        result = active_pipeline.motion_reference_service.recover(
            script_id, client_request_id, **request.model_dump(),
        )
    except StorageConflictError as exc:
        for code in ("motion_reference_recover_not_allowed", "provider_operation_running"):
            if str(exc) == code:
                return JSONResponse(status_code=409, content={"code": code, "detail": code})
        raise
    except ValueError as exc:
        if str(exc) in {"Script not found", "Video task not found", "Motion reference request not found"}:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        code = "provider_recovery_unavailable" if str(exc) == "provider_recovery_runtime_missing" else "motion_reference_recover_not_allowed"
        return JSONResponse(status_code=409, content={"code": code, "detail": "The original motion operation is unavailable."})
    return signed_response(result, runtime=active_pipeline)


@router.post("/projects/{script_id}/provider_video_tasks/{task_id}/continue", response_model=VideoTask)
def continue_provider_video_task(
    script_id: str, task_id: str, request: ProviderVideoContinueRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    try:
        task = active_pipeline.continue_accepted_provider_video_task(
            script_id, task_id, **request.model_dump(),
        )
    except StorageConflictError as exc:
        for code in ("provider_continue_not_allowed", "provider_operation_running"):
            if str(exc) == code:
                return JSONResponse(status_code=409, content={"code": code, "detail": code})
        raise
    except ValueError as exc:
        if str(exc) in {"Script not found", "Video task not found"}:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if str(exc) == "provider_recovery_runtime_missing":
            return JSONResponse(status_code=409, content={
                "code": "provider_recovery_unavailable", "detail": "The original provider runtime is unavailable.",
            })
        return JSONResponse(status_code=409, content={
            "code": "provider_continue_not_allowed", "detail": "The original submission can no longer be confirmed.",
        })
    return signed_response(task, runtime=active_pipeline)


@router.post("/projects/{script_id}/provider_video_tasks", response_model=ProviderVideoAdmissionResponse)
def create_provider_video_tasks(
    script_id: str,
    request: ProviderVideoTaskRequest,
    background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Admit one durable paid action; replays never authorize another submit."""
    raw = request.model_dump(exclude_unset=True)
    raw.pop("client_request_id")
    raw.pop("batch_size", None)
    try:
        result = active_pipeline.admit_provider_video_tasks_with_receipt(
            script_id, raw,
            client_request_id=request.client_request_id,
            batch_size=request.batch_size,
        )
    except StorageConflictError as exc:
        for code in ("provider_request_conflict", "provider_request_receipt_invalid"):
            if str(exc) == code:
                return JSONResponse(status_code=409, content={"code": code, "detail": code})
        raise
    except ValueError as exc:
        if str(exc) == "Script not found":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if str(exc) == "provider_recovery_runtime_missing":
            return JSONResponse(status_code=409, content={
                "code": "provider_recovery_unavailable",
                "detail": "The current provider configuration does not support this generation entry.",
            })
        return JSONResponse(status_code=422, content={
            "code": "provider_request_invalid",
            "detail": _public_exception_detail(
                exc, (
                    "provider_batch_size_invalid", "provider_request_batch_size_transport_field",
                    "provider_secret_unavailable", "Invalid credential",
                    "seedance_http_key_required", "seedance_cli_submission_unsafe",
                ),
                "Invalid provider video request; check the model, mode, parameters and references.",
            ),
        })

    tasks_by_id = {task.id: task for task in result.script.video_tasks}
    response = ProviderVideoAdmissionResponse(
        client_request_id=result.client_request_id,
        replayed=result.replayed,
        task_ids=list(result.task_ids),
        tasks=[tasks_by_id[task_id] for task_id in result.task_ids if task_id in tasks_by_id],
        reason=result.reason,
    )
    for task_id in result.new_task_ids:
        background_tasks.add_task(active_pipeline.submit_provider_video_task, script_id, task_id)
    return signed_response(response, runtime=active_pipeline)


@router.post("/projects/{script_id}/video_tasks", response_model=List[VideoTask])
def create_video_task(
    script_id: str,
    request: CreateVideoTaskRequest,
    background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Creates new video generation tasks."""
    try:
        tasks = []
        for _ in range(request.batch_size):
            script, task_id = active_pipeline.create_video_task(
                script_id=script_id,
                image_url=request.image_url,
                prompt=request.prompt,
                frame_id=request.frame_id,
                duration=request.duration,
                seed=request.seed,
                resolution=request.resolution,
                generate_audio=request.generate_audio,
                audio_url=request.audio_url,
                prompt_extend=request.prompt_extend,
                negative_prompt=request.negative_prompt,
                model=request.model,
                shot_type=request.shot_type,
                generation_mode=request.generation_mode,
                reference_video_urls=request.reference_video_urls,
                reference_image_urls=request.reference_image_urls,
                ratio=request.ratio,
                watermark=request.watermark,
                mode=request.mode,
                sound=request.sound,
                cfg_scale=request.cfg_scale,
                vidu_audio=request.vidu_audio,
                movement_amplitude=request.movement_amplitude,
                workbench_tab=request.workbench_tab,
            )

            # Find the created task object
            created_task = next((t for t in script.video_tasks if t.id == task_id), None)
            if created_task:
                tasks.append(created_task)

            # Add background processing
            background_tasks.add_task(process_video_task, active_pipeline, script_id, task_id)

        return signed_response(tasks, runtime=active_pipeline)

    except ValueError as e:
        # Validation failures from pipeline.create_video_task (model⇄
        # mode⇄refs mismatch, missing references for R2V, etc.) are
        # the user's responsibility to fix — surface as 400 so the
        # frontend can show an inline error instead of treating it
        # as a server error.
        raise HTTPException(status_code=400, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


_IMAGE_REQUEST_VALIDATION_DETAILS = (
    "studio_image_channel_not_supported", "studio_image_reference_variant_not_supported",
    "studio_image_reference_source_not_supported", "studio_storyboard_image_request_not_supported",
    "studio_image_frame_fields_not_supported", "studio_image_prepared_channel_not_supported",
    "studio_image_contract_not_supported", "studio_image_success_observation_required",
    "studio_image_recovery_action_invalid", "studio_storyboard_project_required",
    "studio_storyboard_image_inputs_not_supported", "studio_image_reference_not_supported",
    "studio_image_aspect_ratio_invalid", "studio_image_negative_prompt_not_supported",
    "studio_gpt_image_negative_prompt_not_supported", "studio_full_body_base_character_not_supported",
    "studio_headshot_selected_reference_ambiguous", "studio_image_base_prompt_required",
    "studio_three_view_selected_reference_ambiguous", "studio_full_body_uploaded_reference_ambiguous",
    "Invalid asset image inputs", "Invalid storyboard image inputs",
    "Invalid MuleRun profile",
    "Invalid provider credential", "Invalid credential", "conflicting_mulerun_routes",
    "Unsupported MuleRun login site", "Unsupported MuleRun studio site",
    "Invalid MuleRun login site", "Invalid MuleRun studio site",
    "Invalid MuleRun login base URL", "Invalid MuleRun studio base URL",
    "studio_image_negative_prompt_exceeds_500_characters", "studio_gpt_image_channel_not_supported",
    "studio_gpt_image_parameter_not_supported", "studio_image_model_not_supported",
    "studio_image_request_root_invalid", "seed must be omitted or an integer from 0 to 2147483647",
) + tuple(
    f"studio_{channel}_{problem}"
    for channel in ("reference_sheet", "full_body", "three_view", "headshot", "scene", "prop")
    for problem in (
        "model_not_supported", "parameter_not_supported", "selected_local_reference_required",
        "fallback_reference_not_supported", "fallback_reference_changed", "fallback_local_reference_required",
    )
)


def _asset_image_request_error(exc: ValueError) -> JSONResponse:
    from .pipeline import ProjectContextError

    if isinstance(exc, AssetImageRequestNotFound):
        return JSONResponse(status_code=404, content={
            "code": "studio_image_request_not_found",
            "detail": "No retained receipt is available for this request.",
        })
    if isinstance(exc, ProjectContextError):
        return JSONResponse(status_code=404 if exc.not_found else 422, content={
            "detail": _public_exception_detail(
                exc, ("Script not found", "Project not found", "Asset request root not found", "Asset not found", "Frame not found"),
                "Image request target not found" if exc.not_found else "Invalid image request target context",
            ),
        })
    return JSONResponse(status_code=422, content={
        "code": "studio_image_request_invalid",
        "detail": _public_exception_detail(
            exc, _IMAGE_REQUEST_VALIDATION_DETAILS,
            "Invalid image request; check the model, parameters and references.",
        ),
    })


def _admit_asset_image_request(root_kind, root_id, request, background_tasks, active_pipeline):
    service = active_pipeline.asset_image_service
    try:
        result = service.admit(root_kind, root_id, request)
    except ValueError as exc:
        return _asset_image_request_error(exc)
    scheduled = False
    try:
        response = JSONResponse(content=asset_image_request_wire(result, service.request_state(result.request)))
        if not result.replayed:
            background_tasks.add_task(service.run_original_request, result.request.id)
            scheduled = True
        return response
    except AssetImageRequestNotFound as exc:
        return _asset_image_request_error(exc)
    finally:
        if not result.replayed and not scheduled:
            service.abandon_pending_original_claim(result.request.id)


def _read_asset_image_request(root_kind, root_id, client_request_id, active_pipeline):
    service = active_pipeline.asset_image_service
    try:
        result = service.read_request(root_kind, root_id, client_request_id)
    except ValueError as exc:
        return _asset_image_request_error(exc)
    try:
        response = asset_image_request_wire(result, service.request_state(result.request))
    except AssetImageRequestNotFound as exc:
        return _asset_image_request_error(exc)
    return JSONResponse(content=response)


@router.post("/projects/{script_id}/asset_image_requests")
def create_project_asset_image_request(
    script_id: str, request: AssetImageRequestCommand, background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    return _admit_asset_image_request("project", script_id, request, background_tasks, active_pipeline)


@router.post("/series/{series_id}/asset_image_requests")
def create_series_asset_image_request(
    series_id: str, request: AssetImageRequestCommand, background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    return _admit_asset_image_request("series", series_id, request, background_tasks, active_pipeline)


@router.get("/projects/{script_id}/asset_image_requests/{client_request_id}")
def read_project_asset_image_request(
    script_id: str, client_request_id: str, active_pipeline: Any = Depends(get_pipeline),
):
    return _read_asset_image_request("project", script_id, client_request_id, active_pipeline)


@router.get("/series/{series_id}/asset_image_requests/{client_request_id}")
def read_series_asset_image_request(
    series_id: str, client_request_id: str, active_pipeline: Any = Depends(get_pipeline),
):
    return _read_asset_image_request("series", series_id, client_request_id, active_pipeline)


@router.post("/projects/{script_id}/assets/generate")
def generate_single_asset(
    script_id: str,
    request: GenerateAssetRequest,
    background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Generates a single asset with specific options (async).
    Returns immediately with task_id for polling progress."""
    from .pipeline import CommandValidationError, ProjectContextError

    try:
        script, task_id = active_pipeline.create_asset_generation_task(
            script_id,
            request.asset_id,
            request.asset_type,
            request.style_preset,
            request.reference_image_url,
            request.style_prompt,
            request.generation_type,
            request.prompt,
            request.apply_style,
            request.negative_prompt,
            request.batch_size,
            request.model_name,
            request.aspect_ratio,
        )
        
        # Add background processing
        background_tasks.add_task(active_pipeline.process_asset_generation_task, task_id)
        
        # Return script with task_id for frontend polling
        response_data = script.model_dump() if hasattr(script, 'model_dump') else script.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data, runtime=active_pipeline)

    except CommandValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{task_id}")
def get_task_status(
    task_id: str,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Returns the status of an asset generation task for polling."""
    status = active_pipeline.get_asset_generation_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # If completed, return the updated committed script as well
    if status["status"] == "completed":
        try:
            script = active_pipeline.read_committed_script(status["script_id"])
        except ValueError:
            script = None
        if script:
            status["script"] = signed_response(script, runtime=active_pipeline).body.decode('utf-8')
    
    return status


class GenerateAssetVideoRequest(BaseModel):
    prompt: Optional[str] = None
    duration: int = 5
    aspect_ratio: Optional[str] = None


@router.post("/projects/{script_id}/assets/{asset_type}/{asset_id}/generate_video", response_model=Script)
def generate_asset_video(
    script_id: str,
    asset_type: str,
    asset_id: str,
    request: GenerateAssetVideoRequest,
    background_tasks: BackgroundTasks,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Generates a video for a specific asset (I2V)."""
    try:
        script, task_id = active_pipeline.create_asset_video_task(
            script_id,
            asset_id,
            asset_type,
            request.prompt,
            request.duration,
            request.aspect_ratio
        )
        
        # Add background processing
        background_tasks.add_task(process_video_task, active_pipeline, script_id, task_id)
        
        return signed_response(script, runtime=active_pipeline)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{script_id}/assets/{asset_type}/{asset_id}/videos/{video_id}", response_model=Script)
def delete_asset_video(script_id: str, asset_type: str, asset_id: str, video_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Deletes a video from an asset."""
    try:
        updated_script = pipeline.delete_asset_video(
            script_id,
            asset_id,
            asset_type,
            video_id
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/projects/{script_id}/assets/toggle_lock", response_model=Script)
def toggle_asset_lock(script_id: str, request: ToggleLockRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Toggles the locked status of an asset."""
    try:
        updated_script = pipeline.toggle_asset_lock(
            script_id,
            request.asset_id,
            request.asset_type
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/assets/toggle_starred", response_model=Script)
def toggle_asset_starred(script_id: str, request: ToggleLockRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Toggles the starred (library shortlist) status of an asset."""
    try:
        updated_script = pipeline.toggle_asset_starred(
            script_id,
            request.asset_id,
            request.asset_type
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/projects/{script_id}/assets/update_image", response_model=Script)
def update_asset_image(script_id: str, request: UpdateAssetImageRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates an asset's image URL manually."""
    try:
        updated_script = pipeline.update_asset_image(
            script_id,
            request.asset_id,
            request.asset_type,
            request.image_url
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/projects/{script_id}/assets/update_attributes", response_model=Script)
def update_asset_attributes(script_id: str, request: UpdateAssetAttributesRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates arbitrary attributes of an asset."""
    try:
        updated_script = pipeline.update_asset_attributes(
            script_id,
            request.asset_id,
            request.asset_type,
            request.attributes
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False, include_url=False))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class UpdateAssetDescriptionRequest(BaseModel):
    asset_id: str
    asset_type: str
    description: str


@router.post("/projects/{script_id}/assets/update_description", response_model=Script)
def update_asset_description(script_id: str, request: UpdateAssetDescriptionRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates an asset's description."""
    try:
        updated_script = pipeline.update_asset_description(
            script_id,
            request.asset_id,
            request.asset_type,
            request.description
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class SelectVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str
    generation_type: Optional[str] = None  # reference_sheet or a legacy character channel

@router.post("/projects/{script_id}/assets/variant/select", response_model=Script)
def select_asset_variant(script_id: str, request: SelectVariantRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Selects a specific variant for an asset."""
    try:
        pipeline.select_asset_variant(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id,
            request.generation_type
        )
        return get_project(script_id, pipeline=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DeleteVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str

@router.post("/projects/{script_id}/assets/variant/delete", response_model=Script)
def delete_asset_variant(script_id: str, request: DeleteVariantRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Deletes a specific variant from an asset."""
    try:
        pipeline.delete_asset_variant(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id
        )
        return get_project(script_id, pipeline=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FavoriteVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str
    generation_type: Optional[str] = None  # For character: 'full_body', 'three_view', 'headshot'
    is_favorited: bool

@router.post("/projects/{script_id}/assets/variant/favorite", response_model=Script)
def toggle_variant_favorite(script_id: str, request: FavoriteVariantRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Toggles the favorite status of a variant. Favorited variants won't be auto-deleted when limit is reached."""
    try:
        pipeline.toggle_variant_favorite(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id,
            request.is_favorited,
            request.generation_type
        )
        return get_project(script_id, pipeline=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/projects/{script_id}/model_settings", response_model=Script)
def update_model_settings(script_id: str, request: UpdateModelSettingsRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates project's model settings for T2I/I2I/I2V and aspect ratios."""
    try:
        updated_script = pipeline.update_model_settings(
            script_id,
            request.t2i_model,
            request.i2i_model,
            request.i2v_model,
            r2v_model=request.r2v_model,
            character_aspect_ratio=request.character_aspect_ratio,
            scene_aspect_ratio=request.scene_aspect_ratio,
            prop_aspect_ratio=request.prop_aspect_ratio,
            storyboard_aspect_ratio=request.storyboard_aspect_ratio,
            image_model=request.image_model,
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise _setting_http_error(e) from e
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdatePromptConfigRequest(BaseModel):
    storyboard_polish: str = ""
    video_polish: str = ""
    r2v_polish: str = ""
    entity_extraction: str = ""
    style_analysis: str = ""
    storyboard_extraction: str = ""


@router.get("/projects/{script_id}/prompt_config")
def get_prompt_config(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Returns project prompt_config and system default prompts for reference."""
    try:
        try:
            script = pipeline.read_committed_script(script_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        config = script.prompt_config if hasattr(script, 'prompt_config') else PromptConfig()
        return {
            "prompt_config": config.model_dump(),
            "revision": script.revision,
            "defaults": {
                key: _default_prompts()[key]
                for key in ("storyboard_polish", "video_polish", "r2v_polish", "storyboard_extraction")
            },
        }
    except HTTPException:
        raise
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/projects/{script_id}/prompt_config")
def update_prompt_config(script_id: str, request: UpdatePromptConfigRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates project custom prompt configuration. Empty string = use system default."""
    try:
        script = pipeline.update_prompt_config(script_id, request.model_dump())
        return {"prompt_config": script.prompt_config.model_dump(), "revision": script.revision}
    except ValueError as exc:
        raise _setting_http_error(exc) from exc
    except HTTPException:
        raise
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/prompt_defaults")
def get_prompt_defaults():
    """Return the built-in default system prompts for all configurable prompt keys.

    The frontend uses this to pre-fill the prompt fields and to compute the
    "delta" on save (a field equal to its default is stored as "" so the
    built-in default is used instead of pinning a snapshot).
    """
    return _default_prompts()


class BindVoiceRequest(BaseModel):
    voice_id: str
    voice_name: str


@router.post("/projects/{script_id}/characters/{char_id}/voice", response_model=Script)
def bind_voice(script_id: str, char_id: str, request: BindVoiceRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Binds a voice to a character."""
    try:
        pipeline.bind_voice(script_id, char_id, request.voice_id, request.voice_name)
        return get_project(script_id, pipeline=pipeline)
    except ValueError as exc:
        raise _setting_http_error(exc) from exc
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateVoiceParamsRequest(BaseModel):
    speed: float = 1.0
    pitch: float = 1.0
    volume: int = 50


@router.put("/projects/{script_id}/characters/{char_id}/voice_params", response_model=Script)
def update_voice_params(script_id: str, char_id: str, request: UpdateVoiceParamsRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates voice parameters for a character."""
    try:
        pipeline.update_voice_params(script_id, char_id, **request.model_dump())
        return get_project(script_id, pipeline=pipeline)
    except ValueError as exc:
        raise _setting_http_error(exc) from exc


@router.get("/voices")
def get_voices(
    pipeline: Any = Depends(get_pipeline),
):
    """Returns list of available voices."""
    return pipeline.audio_generator.get_available_voices()


class VoicePreviewRequest(BaseModel):
    """PR-3g #3 · request shape for /voice/preview endpoint.

    Backs the Voice picker modal's inline ▶ button. Frontend hits this
    when user previews a voice card; backend either returns a cached
    URL or generates fresh audio via TTSProcessor.
    """
    voice_id: str
    text: str
    speed: float = 1.0
    pitch: float = 1.0
    volume: int = 50
    instructions: Optional[str] = None


@router.post("/voice/preview")
def voice_preview(
    request: VoicePreviewRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Generate or fetch cached preview audio for a voice.

    Cache key = md5(voice_id|text|speed|pitch|volume|instructions). First
    call triggers TTSProcessor.synthesize() and writes to
    output/cache/voice_preview/{key}.mp3. Subsequent identical calls
    return the cached URL instantly.

    PR-3h #2: handles CUSTOM voices (clones/designs) by looking up
    series.custom_voices[] for target_model + family overrides — required
    because cloned voice_ids aren't in static TTS_VOICE_REGISTRY.

    Spec: r2v-workflow-v3-unified.md §4.2.3 (cache strategy) + Q5 b/c.
    """
    import hashlib
    if not active_pipeline.audio_generator.tts:
        raise HTTPException(
            status_code=503,
            detail="TTS service unavailable. Check DASHSCOPE_API_KEY configuration.",
        )

    # PR-3h #2: resolve custom voice → target_model/family override
    custom = active_pipeline.find_custom_voice(request.voice_id)
    model_override = custom.target_model if custom else None
    family_override = custom.family if custom else None

    cache_dir = Path(active_pipeline.data_dir) / "cache" / "voice_preview"
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = hashlib.md5(
        f"{request.voice_id}|{request.text}|{request.speed}|{request.pitch}|{request.volume}|{request.instructions or ''}".encode("utf-8")
    ).hexdigest()
    cache_path = str(cache_dir / f"{cache_key}.mp3")
    cached = os.path.exists(cache_path)

    if not cached:
        try:
            active_pipeline.audio_generator.tts.synthesize(
                text=request.text,
                output_path=cache_path,
                voice=request.voice_id,
                speech_rate=request.speed,
                pitch_rate=request.pitch,
                volume=request.volume,
                instructions=request.instructions,
                model_override=model_override,
                family_override=family_override,
            )
        except Exception as e:
            logger.error(f"[/voice/preview] TTS error voice={request.voice_id}: {e}")
            raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")

    # Static mount /files maps to output/, so the relative path under output/
    # becomes the URL path frontend can hit. signed_response wraps for OSS
    # signing when configured, no-op otherwise.
    url = f"cache/voice_preview/{cache_key}.mp3"
    return signed_response({"url": url, "cached": cached}, runtime=active_pipeline)


# ─────────────────────────────────────────────────────────────
# PR-3h · Voice clone endpoints
# Per Q16: series-level scope for custom voices. Frontend uploads audio
# via existing /upload (gets URL), then calls /voice/clone with that URL.
# ─────────────────────────────────────────────────────────────

class VoiceCloneRequest(BaseModel):
    """PR-3h request: clone a voice from a reference audio URL.

    Frontend pre-validates: ≤10MB, MP3/WAV/M4A, ≥16kHz, 10-20s recommended.
    """
    series_id: str
    audio_url: str
    label: str
    target_model: str = "cosyvoice-v3.5-plus"


@router.post("/voice/clone")
def voice_clone(request: VoiceCloneRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Create a custom voice by cloning a reference audio sample.

    Per Q15.2: stored at series level so any character in the series can
    pick from the clone via the VoicePickerModal '我的复刻' tab.
    """
    from .pipeline import CommandValidationError

    try:
        custom = pipeline.create_voice_clone(
            series_id=request.series_id,
            audio_url=request.audio_url,
            label=request.label,
            target_model=request.target_model,
        )
        return signed_response(custom, runtime=pipeline)
    except CommandValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StorageConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/series/{series_id}/custom_voices")
def list_series_custom_voices(series_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Return all custom voices (clones + designs) in a series."""
    voices = pipeline.list_custom_voices(series_id)
    return signed_response(voices, runtime=pipeline)


@router.delete("/series/{series_id}/custom_voices/{voice_id}")
def delete_series_custom_voice(series_id: str, voice_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Remove a custom voice from a series.

    Note: does NOT delete on dashscope side (24h retention is best-effort).
    If a character has this voice_id bound, the binding becomes orphaned
    (frontend should warn before delete in v2).
    """
    removed = pipeline.delete_custom_voice(series_id, voice_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Custom voice not found")
    return signed_response({"removed": True}, runtime=pipeline)


# ─────────────────────────────────────────────────────────────
# PR-3i · Voice design endpoints
# Iterative pattern: preview → (tweak prompt) → preview → accept.
# Each preview mints a NEW voice on dashscope; only accept persists.
# ─────────────────────────────────────────────────────────────

class VoiceDesignPreviewRequest(BaseModel):
    voice_prompt: str
    preview_text: str = "你好，这是一段音色测试。请仔细听一听是否符合预期。"
    target_model: str = "cosyvoice-v3.5-plus"


@router.post("/voice/design/preview")
def voice_design_preview(request: VoiceDesignPreviewRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Mint a fresh design voice and return a preview audio URL.

    The user re-calls this with a tweaked voice_prompt to iterate.
    Returns: {voice_id, preview_url, target_model}.
    """
    try:
        result = pipeline.voice_design_preview(
            voice_prompt=request.voice_prompt,
            preview_text=request.preview_text,
            target_model=request.target_model,
        )
        return signed_response(result, runtime=pipeline)
    except StorageError:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


class VoiceDesignSaveRequest(BaseModel):
    series_id: str
    voice_id: str
    voice_prompt: str
    label: str
    target_model: str = "cosyvoice-v3.5-plus"


@router.post("/voice/design/accept")
def voice_design_accept(request: VoiceDesignSaveRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Persist a previewed design voice into series.custom_voices[]."""
    try:
        custom = pipeline.voice_design_save(
            series_id=request.series_id,
            voice_id=request.voice_id,
            voice_prompt=request.voice_prompt,
            label=request.label,
            target_model=request.target_model,
        )
        return signed_response(custom, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


class VoiceDesignTranslateRequest(BaseModel):
    description: str


@router.post("/voice/design/translate")
def voice_design_translate(request: VoiceDesignTranslateRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """LLM helper: character.description → CosyVoice voice_prompt."""
    if not request.description.strip():
        raise HTTPException(status_code=400, detail="description is empty")
    try:
        voice_prompt = pipeline.translate_character_to_voice_prompt(request.description)
        return {"voice_prompt": voice_prompt}
    except StorageError:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{script_id}/frames/{frame_id}/audio/state")
def get_dialogue_audio_state(script_id: str, frame_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Read current audio parameters without generating or migrating history."""
    from .pipeline import ProjectContextError

    try:
        payload = pipeline.read_dialogue_audio_state(script_id, frame_id)
        # Prepared text/options are synthesis inputs, even if they resemble an
        # object key. Only the declared media field may receive a display URL.
        signed_media = sign_media_refs_in_response({"audio_url": payload.get("audio_url")}, _runtime_uploader(pipeline))
        payload["audio_url"] = signed_media.get("audio_url")
        if signed_media.get("media_refs"):
            payload["media_refs"] = signed_media["media_refs"]
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    except ProjectContextError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc))


class GenerateLineAudioRequest(DialogueAudioOptions):
    """Only supplied fields patch desired options; null resets inheritance."""


@router.post("/projects/{script_id}/frames/{frame_id}/audio", response_model=Script)
def generate_line_audio(script_id: str, frame_id: str, request: GenerateLineAudioRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Generates audio for a specific frame with parameters."""
    from .pipeline import ProjectContextError, ProjectGenerationError
    from .audio import UnsupportedAudioParameters

    try:
        updated_script = pipeline.generate_dialogue_line(
            script_id, frame_id,
            **request.model_dump(exclude_unset=True),
        )
        return signed_response(updated_script, runtime=pipeline)
    except UnsupportedAudioParameters as e:
        raise HTTPException(status_code=422, detail=e.detail)
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
# PR-3k · Audio mix endpoints (BGM presets + per-script mix settings)
# Backs the Assembly Mix phase.
# ─────────────────────────────────────────────────────────────

@router.get("/bgm/presets")
def list_bgm_presets():
    """Return the BGM preset catalog. UI populates the Mix phase picker."""
    from .audio import get_bgm_presets
    return get_bgm_presets()


class AudioMixRequest(BaseModel):
    bgm_url: Optional[str] = None  # null clears bgm
    dialogue_volume: Optional[int] = None  # 0-100
    bgm_volume: Optional[int] = None
    sfx_volume: Optional[int] = None


@router.put("/projects/{script_id}/audio_mix", response_model=Script)
def update_audio_mix(script_id: str, request: AudioMixRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Patch explicitly supplied BGM and per-track mix fields for the final merge."""
    try:
        script = pipeline.update_audio_mix(script_id, request.model_dump(exclude_unset=True))
        return signed_response(script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class DubPreviewRequest(BaseModel):
    video_task_id: str
    offset_ms: int = Field(0, ge=0, strict=True)


@router.post("/projects/{script_id}/frames/{frame_id}/dub/preview")
def preview_dub(script_id: str, frame_id: str, request: DubPreviewRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Generate a preview dub (cached Demucs + fast adelay+amix+mux)."""
    try:
        updated_script = pipeline.preview_dub(
            script_id, frame_id,
            video_task_id=request.video_task_id,
            offset_ms=request.offset_ms,
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/frames/{frame_id}/dub/apply")
def apply_dub(script_id: str, frame_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Promote current preview to official dubbed video."""
    try:
        updated_script = pipeline.apply_dub(script_id, frame_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/projects/{script_id}/frames/{frame_id}/dub")
def revert_frame_dub(script_id: str, frame_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Revert dubbing — remove dubbed+preview, keep bg cache."""
    try:
        updated_script = pipeline.revert_dub(script_id, frame_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/projects/{script_id}/dialogue_audio/batch")
def generate_dialogue_audio_batch(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Generate saved dialogue results and report generated/skipped/failed/no_voice."""
    from .pipeline import ProjectContextError

    try:
        script, stats, errors = pipeline.generate_dialogue_audio_batch(script_id)
        response_data = script.model_dump()
        response_data["_batch_stats"] = stats
        response_data["_batch_errors"] = errors
        return signed_response(response_data, runtime=pipeline)
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/mix/generate_sfx", response_model=Script)
def generate_mix_sfx(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """SFX generation is not wired to a real provider/task boundary."""
    raise HTTPException(
        status_code=501,
        detail="SFX generation is not supported; no provider/task boundary is wired",
    )


@router.post("/projects/{script_id}/mix/generate_bgm", response_model=Script)
def generate_mix_bgm(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Generative BGM is unsupported; use BGM presets/uploads via audio_mix."""
    raise HTTPException(
        status_code=501,
        detail="Generative BGM is not supported; use BGM presets or upload audio via audio_mix",
    )


class ToggleFrameLockRequest(BaseModel):
    frame_id: str


@router.post("/projects/{script_id}/frames/toggle_lock", response_model=Script)
def toggle_frame_lock(script_id: str, request: ToggleFrameLockRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Toggles the locked status of a frame."""
    try:
        updated_script = pipeline.toggle_frame_lock(
            script_id,
            request.frame_id
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateFrameRequest(BaseModel):
    frame_id: str
    expected_revision: int = Field(..., ge=0, strict=True)
    image_prompt: Optional[str] = None
    action_description: Optional[str] = None
    visual_description: Optional[str] = None
    dialogue: Optional[str] = None
    camera_angle: Optional[str] = None
    scene_id: Optional[str] = None
    character_ids: Optional[List[str]] = None
    duration: Optional[int] = None
    shot_size: Optional[str] = None
    camera_movement_description: Optional[str] = None
    transition_hint: Optional[str] = None

@router.post("/projects/{script_id}/frames/update", response_model=Script)
def update_frame(script_id: str, request: UpdateFrameRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Updates frame data (prompt, scene, characters, etc.)."""
    try:
        updated_script = pipeline.update_frame(
            script_id,
            request.frame_id,
            expected_revision=request.expected_revision,
            image_prompt=request.image_prompt,
            action_description=request.action_description,
            visual_description=request.visual_description,
            dialogue=request.dialogue,
            camera_angle=request.camera_angle,
            scene_id=request.scene_id,
            character_ids=request.character_ids,
            duration=request.duration,
            shot_size=request.shot_size,
            camera_movement_description=request.camera_movement_description,
            transition_hint=request.transition_hint,
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class AddFrameRequest(BaseModel):
    scene_id: Optional[str] = None
    action_description: str = ""
    camera_angle: str = "medium_shot"
    insert_at: Optional[int] = None

@router.post("/projects/{script_id}/frames", response_model=Script)
def add_frame(script_id: str, request: AddFrameRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Adds a new storyboard frame."""
    try:
        updated_script = pipeline.add_frame(
            script_id, 
            request.scene_id, 
            request.action_description, 
            request.camera_angle,
            request.insert_at
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/projects/{script_id}/frames/{frame_id}", response_model=Script)
def delete_frame(script_id: str, frame_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Deletes a storyboard frame."""
    try:
        updated_script = pipeline.delete_frame(script_id, frame_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CopyFrameRequest(BaseModel):
    frame_id: str
    insert_at: Optional[int] = None

@router.post("/projects/{script_id}/frames/copy", response_model=Script)
def copy_frame(script_id: str, request: CopyFrameRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Copies a storyboard frame."""
    try:
        updated_script = pipeline.copy_frame(script_id, request.frame_id, request.insert_at)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ReorderFramesRequest(BaseModel):
    frame_ids: List[str]

@router.put("/projects/{script_id}/frames/reorder", response_model=Script)
def reorder_frames(script_id: str, request: ReorderFramesRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Reorders storyboard frames."""
    try:
        updated_script = pipeline.reorder_frames(script_id, request.frame_ids)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RenderFrameRequest(BaseModel):
    frame_id: str
    composition_data: Optional[Dict[str, Any]] = None
    prompt: str
    batch_size: int = Field(1, ge=1, strict=True)


@router.post("/projects/{script_id}/storyboard/render", response_model=Script)
def render_frame(script_id: str, request: RenderFrameRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Renders a specific frame using composition data (I2I)."""
    from .pipeline import CommandValidationError, ProjectContextError, ProjectGenerationError

    try:
        logger.info(f"Rendering frame {request.frame_id}")
        
        updated_script = pipeline.generate_storyboard_render(
            script_id,
            request.frame_id,
            request.composition_data,
            request.prompt,
            request.batch_size
        )
        return signed_response(updated_script, runtime=pipeline)
    except CommandValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectContextError as e:
        raise HTTPException(status_code=404 if e.not_found else 422, detail=str(e))
    except ProjectGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception(f"Error rendering frame {request.frame_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SelectVideoRequest(BaseModel):
    video_id: str


@router.post("/projects/{script_id}/frames/{frame_id}/select_video", response_model=Script)
def select_video(script_id: str, frame_id: str, request: SelectVideoRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Selects a video variant for a specific frame."""
    try:
        updated_script = pipeline.select_video_for_frame(script_id, frame_id, request.video_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/frames/{frame_id}/auto_select_latest_video", response_model=Script)
def auto_select_latest_video(script_id: str, frame_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Auto-pick the latest completed video as this frame's active take.

    Idempotent; skipped when the frame is pinned (is_video_pinned=True).
    Frontend calls this on every task-completion poll so the freshly
    generated take surfaces on the hero — unless the user has explicitly
    pinned a different take.
    """
    try:
        updated_script = pipeline.auto_select_latest_video(script_id, frame_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/frames/{frame_id}/unpin_video", response_model=Script)
def unpin_video(script_id: str, frame_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Clear the manual pin; auto_select_latest_video resumes on next poll.

    Leaves selected_video_id / video_url untouched — the user keeps seeing
    the current take until a new generation produces a newer completed
    task.
    """
    try:
        updated_script = pipeline.unpin_video(script_id, frame_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExtractLastFrameRequest(BaseModel):
    video_task_id: str


@router.post("/projects/{script_id}/frames/{frame_id}/extract_last_frame")
def extract_last_frame(script_id: str, frame_id: str, request: ExtractLastFrameRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Extract the last frame from a completed video and add it as a variant to the frame's rendered_image_asset."""
    try:
        updated_script = pipeline.extract_last_frame(script_id, frame_id, request.video_task_id)
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception(f"Error extracting last frame: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/frames/{frame_id}/upload_image")
def upload_frame_image(
    script_id: str,
    frame_id: str,
    file: UploadFile = File(...),
    active_pipeline: Any = Depends(get_pipeline),
):
    """Upload an image as a variant for a frame's rendered_image_asset."""
    file_path = None

    def cleanup_local_upload() -> None:
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except OSError:
                logger.warning("Could not remove rejected frame upload %s", file_path)

    try:
        # Save file locally first
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        upload_dir = Path(active_pipeline.data_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(upload_dir / filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        updated_script = active_pipeline.upload_frame_image(script_id, frame_id, file_path)
    except ValueError as e:
        cleanup_local_upload()
        raise HTTPException(status_code=404, detail=str(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception(f"Error uploading frame image: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return signed_response(updated_script, runtime=active_pipeline)


# Issue 10: T2I 首帧 upload — uploaded image is appended to the frame's
# t2i_image_urls history and becomes the active首帧 immediately, unlocking
# Step 2 (I2V generation). Whitelisted formats + 8 MB cap reflect that this
# is a user-facing creator upload, not an arbitrary file store.
_T2I_UPLOAD_MAX_BYTES = 8 * 1024 * 1024
_T2I_UPLOAD_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@router.post("/projects/{script_id}/frames/{frame_id}/upload_t2i")
async def upload_t2i_frame(
    script_id: str,
    frame_id: str,
    file: UploadFile = File(...),
    active_pipeline: Any = Depends(get_pipeline),
):
    """Upload an external image as a T2I首帧 candidate for an I2V flow.

    Validation:
      - ≤ 8 MB (rejected with 413 to differentiate from validation 400)
      - jpg/jpeg/png/webp only (rejected with 415)

    On success, returns the updated StoryboardFrame so the client can read
    back `t2i_image_urls` and `t2i_selected_index` without an extra round-
    trip. The uploaded image becomes the active首帧 (auto-selected).
    """
    try:
        original_name = (file.filename or "").strip()
        if not original_name:
            raise HTTPException(status_code=400, detail="No filename provided")
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in _T2I_UPLOAD_ALLOWED_EXTS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type {ext!r}. Allowed: {sorted(_T2I_UPLOAD_ALLOWED_EXTS)}",
            )

        # Stream-to-disk with explicit byte cap so we never load >8 MB
        # into memory if a client lies about Content-Length.
        filename = f"t2i_{uuid.uuid4().hex}{ext}"
        rel_path = os.path.join("uploads", filename)
        abs_path = str(Path(active_pipeline.data_dir) / rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        size = 0
        try:
            with open(abs_path, "wb") as buffer:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > _T2I_UPLOAD_MAX_BYTES:
                        buffer.close()
                        os.unlink(abs_path)
                        raise HTTPException(
                            status_code=413,
                            detail=f"File exceeds {_T2I_UPLOAD_MAX_BYTES // (1024 * 1024)} MB limit",
                        )
                    buffer.write(chunk)
        except HTTPException:
            raise
        except Exception as e:
            # Clean up partial file on any unexpected error
            if os.path.exists(abs_path):
                try:
                    os.unlink(abs_path)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail=f"Upload write failed: {e}")

        # `pipeline.upload_t2i_frame` synchronously serializes + writes the
        # whole projects.json (~50-500KB) — wrap in to_thread so we don't
        # block the event loop for the ~50ms write. Matches the pattern
        # used by create_project / reparse_project / analyze_script_for_styles.
        loop = asyncio.get_event_loop()
        frame = await loop.run_in_executor(
            None,
            partial(active_pipeline.upload_t2i_frame, script_id, frame_id, rel_path),
        )
        if frame is None:
            # Roll back the file — frame/script gone, no reference will exist
            if os.path.exists(abs_path):
                try:
                    os.unlink(abs_path)
                except OSError:
                    pass
            raise HTTPException(status_code=404, detail="Script or frame not found")
        return signed_response(frame, runtime=active_pipeline)
    except HTTPException:
        raise
    except StorageError:
        raise
    except Exception as e:
        logger.exception("upload_t2i_frame unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/merge", response_model=Script)
def merge_videos(script_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Merge all selected frame videos into final output"""
    try:
        merged_script = pipeline.merge_videos(script_id)
        return signed_response(merged_script, pipeline=pipeline, runtime=pipeline)
    except ValueError as e:
        # Known validation errors (no videos, etc.)
        logger.error(f"[MERGE ERROR] Validation failed: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=400, detail=str(e))
    except StorageError:
        raise
    except RuntimeError as e:
        # FFmpeg or processing errors
        logger.error(f"[MERGE ERROR] Runtime error: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[MERGE ERROR] Unexpected error: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=f"Merge failed: {str(e)}")


# ===== Export Endpoint =====

class ExportRequest(BaseModel):
    resolution: str = "1080p"
    format: str = "mp4"
    subtitles: str = "none"

@router.post("/projects/{script_id}/export")
def export_project(script_id: str, request: ExportRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Export project video by merging all selected frame videos.

    Currently delegates to the existing merge_videos pipeline.
    resolution/format/subtitles parameters are accepted but not yet applied
    (requires FFmpeg pipeline iteration).
    """
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Project not found")

        # Always run the guarded merge path. Existing merged URLs may have
        # been produced from older selected takes or mix settings, so they
        # are retained as files but not accepted as proof of current export.
        merged_script = pipeline.merge_videos(script_id)
        return signed_response({"url": merged_script.merged_video_url}, media_fields={"url"}, runtime=pipeline)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StorageError:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[EXPORT ERROR] {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


# ===== Art Direction Endpoints =====

class AnalyzeStyleRequest(BaseModel):
    script_text: str


class SaveArtDirectionRequest(BaseModel):
    expected_revision: int = Field(..., ge=0, strict=True)
    selected_style_id: str
    style_config: Dict[str, Any]
    custom_styles: List[Dict[str, Any]] = []
    ai_recommendations: List[Dict[str, Any]] = []


class ClearArtDirectionRequest(BaseModel):
    expected_revision: int = Field(..., ge=0, strict=True)


@router.post("/projects/{script_id}/art_direction/analyze")
async def analyze_script_for_styles(script_id: str, request: AnalyzeStyleRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Analyze script content and recommend visual styles using LLM"""
    try:
        # Get the committed script to ensure it exists and freeze prompt config.
        try:
            script = pipeline.read_committed_script(script_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Script not found")

        # Use LLM to analyze and recommend styles (run in thread pool to avoid blocking, Python 3.8 compatible)
        custom_style = getattr(getattr(script, "prompt_config", None), "style_analysis", "")
        loop = asyncio.get_event_loop()
        recommendations = await loop.run_in_executor(
            None,  # Use default executor
            partial(pipeline.script_processor.analyze_script_for_styles, request.script_text, custom_style)
        )

        return {"recommendations": recommendations}
    except HTTPException:
        raise
    except StorageError:
        raise
    except Exception as e:
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{script_id}/art_direction/clear")
def clear_project_art_direction(script_id: str, request: ClearArtDirectionRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """R2V v2 Phase 2 — clear project-level art_direction so the
    episode falls back to series baseline (inherit). Used by the
    Style step '重置为系列' button."""
    try:
        return signed_response(
            pipeline.clear_art_direction(script_id, expected_revision=request.expected_revision),
            runtime=pipeline,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/projects/{script_id}/last_episode_summary")
def update_last_episode_summary(script_id: str, payload: dict,
    pipeline: Any = Depends(get_pipeline),
):
    """R2V v2 Phase P1-b — manually edit the cached AI summary.
    Body: {"ai_summary": "user-edited text"} or null to clear."""
    try:
        return signed_response(pipeline.update_last_episode_summary(script_id, payload.get("ai_summary")), runtime=pipeline)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{script_id}/art_direction/save", response_model=Script)
def save_art_direction(script_id: str, request: SaveArtDirectionRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Save Art Direction configuration to the project"""
    try:
        updated_script = pipeline.save_art_direction(
            script_id,
            request.selected_style_id,
            request.style_config,
            request.custom_styles,
            request.ai_recommendations,
            expected_revision=request.expected_revision,
        )
        return signed_response(updated_script, runtime=pipeline)
    except ValueError as e:
        raise _setting_http_error(e) from e
    except StorageError:
        raise
    except Exception as e:
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/art_direction/presets")
def get_style_presets():
    """Get built-in style presets (v2: categories + presets)"""
    try:
        import json
        import os
        preset_file = os.path.join(os.path.dirname(__file__), "style_presets.json")

        if not os.path.exists(preset_file):
            return {"version": 2, "categories": [], "presets": []}

        with open(preset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and data.get("version") == 2:
                return data
            # Legacy fallback: plain array
            return {"version": 2, "categories": [], "presets": data if isinstance(data, list) else []}
    except Exception as e:
        logger.exception("Failed to load style presets")
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: /storyboard/polish_prompt removed - use /storyboard/refine_prompt instead


def _get_custom_prompt(active_pipeline: Any, script_id: str, field: str) -> str:
    """Read a custom prompt with 3-level fallback: Episode → Series → system default.
    Returns empty string if result equals system default (so LLM method uses its built-in)."""
    if not script_id:
        return ""
    try:
        script, series, _ = active_pipeline.read_committed_project_view(script_id)
    except ValueError:
        return ""
    if not script:
        return ""
    effective = active_pipeline.get_effective_prompt(field, script, series)
    # If it's the system default, return empty so the LLM method uses its built-in default
    from .llm import DEFAULT_STORYBOARD_POLISH_PROMPT, DEFAULT_VIDEO_POLISH_PROMPT, DEFAULT_R2V_POLISH_PROMPT
    defaults = {
        "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
        "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
        "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
    }
    if effective == defaults.get(field, ""):
        return ""
    return effective


def _get_polish_model_for_project(active_pipeline: Any, script_id: str) -> str:
    """Read polish_model with 3-level fallback: Episode.prompt_config → Series.prompt_config → "".
    Empty = LLMAdapter uses its default (qwen3.6-plus). The frontend's polish-model dropdown
    in PromptConfig modal writes here; backend just reads."""
    if not script_id:
        return ""
    try:
        script, series, _ = active_pipeline.read_committed_project_view(script_id)
    except ValueError:
        return ""
    if not script:
        return ""
    # Episode first
    ep_pc = getattr(script, "prompt_config", None)
    if ep_pc and getattr(ep_pc, "polish_model", ""):
        return ep_pc.polish_model
    # Series fallback
    if series:
        s_pc = getattr(series, "prompt_config", None)
        if s_pc and getattr(s_pc, "polish_model", ""):
            return s_pc.polish_model
    return ""


class PolishVideoPromptRequest(BaseModel):
    draft_prompt: str
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement
    script_id: str = ""  # Optional: project ID to load custom prompt config
    # 迭代时传入上一次的 CN 作为双语锚点，让模型同步修改双语；首次留空。
    prev_cn: str = ""
    # I2V 模式：首帧图 URL(s)。Vision-capable polish 模型会真正看见图像并
    # 用其指导润色（光影、构图、被摄主体等）。空列表 = 纯文本润色。
    image_urls: List[str] = Field(default_factory=list, max_length=4)
    # 显式覆盖 polish 用的 LLM 模型；空 = 用 project / series PromptConfig
    # 的 polish_model（再 fallback 到 system default）。
    polish_model: str = ""


def _polish_error_response(err) -> Dict[str, Any]:
    """把 PolishError 转成统一的 502 响应体。
    model_echo 时附带原文双语供前端做 warning 渲染。"""
    body: Dict[str, Any] = {
        "reason": err.reason,
        "message_zh": err.message_zh,
        "message_en": err.message_en,
    }
    if err.prompt_cn:
        body["prompt_cn"] = err.prompt_cn
    if err.prompt_en:
        body["prompt_en"] = err.prompt_en
    return body


@router.post("/video/polish_prompt")
def polish_video_prompt(
    request: PolishVideoPromptRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Polishes a video generation prompt using LLM. Returns bilingual prompts.

    NOTE: Defined as a SYNC handler on purpose. The body calls
    `processor.polish_video_prompt()` which makes a blocking HTTP call to
    DashScope (openai sync client). If declared `async def`, FastAPI would
    run it on the event loop itself — blocking ALL other endpoints for the
    10-30s LLM call (e.g. concurrent `GET /prompt_config` from the modal
    looks "stuck loading"). Sync handlers get auto-dispatched to anyio's
    threadpool, freeing the event loop for other requests.

    成功：200 + {prompt_cn, prompt_en}
    失败：502 + {reason, message_zh, message_en, prompt_cn?, prompt_en?}
      其中 reason ∈ {is_configured_false, api_error, json_parse_error,
                     missing_keys, model_echo}。
      model_echo 是 warning 性质（带原文双语），其余是 hard error。
    """
    from .llm import PolishError, ScriptProcessor
    try:
        custom_prompt = _get_custom_prompt(active_pipeline, request.script_id, "video_polish")
        # Polish model: request override → project/series PromptConfig → ""
        polish_model = request.polish_model or _get_polish_model_for_project(active_pipeline, request.script_id)
        processor = ScriptProcessor(output_root=str(active_pipeline.data_dir), env=active_pipeline.environment_snapshot())
        result = processor.polish_video_prompt(
            request.draft_prompt,
            request.feedback,
            custom_prompt,
            request.prev_cn,
            image_urls=request.image_urls or None,
            polish_model=polish_model,
        )
        return {
            "prompt_cn": result.get("prompt_cn", ""),
            "prompt_en": result.get("prompt_en", "")
        }
    except PolishError as e:
        logger.warning("polish_video_prompt failed: %s", e)
        raise HTTPException(status_code=502, detail=_polish_error_response(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception("polish_video_prompt unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


class RefSlot(BaseModel):
    description: str  # Character name, e.g., "雷震", "白兔"


class PolishR2VPromptRequest(BaseModel):
    draft_prompt: str
    slots: List[RefSlot]
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement
    script_id: str = ""  # Optional: project ID to load custom prompt config
    prev_cn: str = ""  # 双语锚点迭代用，首次留空
    # R2V 模式：用户挂载的 character1/2/3 参考图 URL(s)，让 vision 模型
    # 看清各角色实际形象。空列表 = 纯文本润色（兼容旧调用方）。
    image_urls: List[str] = Field(default_factory=list, max_length=9)
    polish_model: str = ""


@router.post("/video/polish_r2v_prompt")
def polish_r2v_prompt(
    request: PolishR2VPromptRequest,
    active_pipeline: Any = Depends(get_pipeline),
):
    """Polishes a R2V (Reference-to-Video) prompt using LLM. Returns bilingual prompts.
    错误约定同 /video/polish_prompt。
    SYNC handler on purpose — see polish_video_prompt for rationale."""
    from .llm import PolishError, ScriptProcessor
    try:
        custom_prompt = _get_custom_prompt(active_pipeline, request.script_id, "r2v_polish")
        polish_model = request.polish_model or _get_polish_model_for_project(active_pipeline, request.script_id)
        processor = ScriptProcessor(output_root=str(active_pipeline.data_dir), env=active_pipeline.environment_snapshot())
        slot_info = [{"description": s.description} for s in request.slots]
        result = processor.polish_r2v_prompt(
            request.draft_prompt,
            slot_info,
            request.feedback,
            custom_prompt,
            request.prev_cn,
            image_urls=request.image_urls or None,
            polish_model=polish_model,
        )
        return {
            "prompt_cn": result.get("prompt_cn", ""),
            "prompt_en": result.get("prompt_en", "")
        }
    except PolishError as e:
        logger.warning("polish_r2v_prompt failed: %s", e)
        raise HTTPException(status_code=502, detail=_polish_error_response(e))
    except StorageError:
        raise
    except Exception as e:
        logger.exception("polish_r2v_prompt unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


# ===== Environment Configuration Endpoints =====

def _mulerun_login_session(request: Request):
    return request.app.state.mulerun_login_session


def _check_mulerun_cli_status(request: Request) -> bool:
    if shutil.which("mulerun") is None:
        return False
    env = request.app.state.runtime_environment.snapshot()
    if env.get("MULERUN_TOKEN"):
        return True
    return _mulerun_login_session(request).status()


@router.post("/config/mulerun-login")
def trigger_mulerun_login(request: Request):
    """Open OAuth using only the current application's owned CLI profile."""
    if shutil.which("mulerun") is None:
        raise HTTPException(status_code=400, detail="MuleRun CLI 未安装。请先运行: npm i -g @mulerunai/cli")
    try:
        started = _mulerun_login_session(request).start()
        return {"status": "ok", "message": "浏览器已打开，请完成登录" if started else "登录正在进行，请完成当前登录"}
    except Exception:
        # CLI failures must not echo tokens, command lines or profile contents.
        raise HTTPException(status_code=503, detail="无法启动当前环境的 MuleRun 登录，请重试") from None


# Credential-like env fields that must never be returned in plaintext.
SECRET_FIELDS = {
    "DASHSCOPE_API_KEY",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY",
    "VIDU_API_KEY",
    "MULEROUTER_API_KEY",
}

# Bullet sentinel: never appears in a real key, so the save path can detect an
# unchanged (still-masked) field and avoid overwriting the stored secret.
_MASK_CHAR = "\u2022"


def _mask_secret(value: Optional[str]) -> str:
    """Return a masked representation (bullets + last 4 chars) for a configured
    secret, or an empty string when it is unset. Never reveals the full value."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 4:
        return _MASK_CHAR * len(v)
    return _MASK_CHAR * 8 + v[-4:]


@router.get("/config/env")
def get_env_config(request: Request):
    """Get current environment configuration.

    Secrets are masked (bullets + last 4 chars) and never returned in
    plaintext. `secrets_configured` reports which credential fields are set so
    the frontend can drive required-field / validation logic without the raw
    value. Non-secret config (OSS bucket/endpoint/base path, provider modes,
    endpoint overrides) is returned as-is."""
    env = request.app.state.runtime_environment.snapshot()
    try:
        from ...utils.endpoints import PROVIDER_DEFAULTS
        from ...utils.oss_utils import is_oss_enabled
        endpoint_overrides = {}
        for provider in PROVIDER_DEFAULTS:
            env_key = f"{provider}_BASE_URL"
            value = env.get(env_key)
            if value:
                endpoint_overrides[env_key] = value

        secrets_configured = {
            field: bool((env.get(field, "") or "").strip())
            for field in SECRET_FIELDS
        }

        return {
            # Masked secrets — never plaintext.
            "DASHSCOPE_API_KEY": _mask_secret(env.get("DASHSCOPE_API_KEY")),
            "ALIBABA_CLOUD_ACCESS_KEY_ID": _mask_secret(env.get("ALIBABA_CLOUD_ACCESS_KEY_ID")),
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": _mask_secret(env.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")),
            "KLING_ACCESS_KEY": _mask_secret(env.get("KLING_ACCESS_KEY")),
            "KLING_SECRET_KEY": _mask_secret(env.get("KLING_SECRET_KEY")),
            "VIDU_API_KEY": _mask_secret(env.get("VIDU_API_KEY")),
            "MULEROUTER_API_KEY": _mask_secret(env.get("MULEROUTER_API_KEY")),
            # Non-secret config.
            "OSS_BUCKET_NAME": env.get("OSS_BUCKET_NAME", ""),
            "OSS_ENDPOINT": env.get("OSS_ENDPOINT", ""),
            "OSS_BASE_PATH": env.get("OSS_BASE_PATH", ""),
            "OSS_ENABLE": is_oss_enabled(env=env),
            "MULERUN_CLI_LOGGED_IN": _check_mulerun_cli_status(request),
            "KLING_PROVIDER_MODE": _normalize_provider_mode(env.get("KLING_PROVIDER_MODE")),
            "VIDU_PROVIDER_MODE": _normalize_provider_mode(env.get("VIDU_PROVIDER_MODE")),
            "PIXVERSE_PROVIDER_MODE": _normalize_provider_mode(env.get("PIXVERSE_PROVIDER_MODE")),
            "endpoint_overrides": endpoint_overrides,
            "secrets_configured": secrets_configured,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))





# ============================================
# Prop CRUD Endpoints
# ============================================

class CreatePropRequest(BaseModel):
    name: str
    description: str = ""

@router.post("/projects/{script_id}/props")
def create_prop(script_id: str, request: CreatePropRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Creates a new prop in the project."""
    try:
        return signed_response(pipeline.add_prop(script_id, request.name, request.description), runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/projects/{script_id}/props/{prop_id}")
def delete_prop(script_id: str, prop_id: str,
    pipeline: Any = Depends(get_pipeline),
):
    """Deletes a prop from the project."""
    try:
        return signed_response(pipeline.delete_prop(script_id, prop_id), runtime=pipeline)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Document CRUD — Script Editor document persistence
# ─────────────────────────────────────────────────────────────────────────────

class SaveDocumentRequest(BaseModel):
    content: dict  # Tiptap JSON document
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    create_snapshot: bool = False

    @field_validator("content")
    @classmethod
    def validate_finite_document_content(cls, value: dict) -> dict:
        try:
            serialize_json(value)
        except Exception as exc:
            raise ValueError("Document content must be finite JSON") from exc
        return value


class RestoreDocumentRequest(BaseModel):
    expected_revision: Annotated[StrictInt, Field(ge=0)]


class DocumentSnapshotInfo(BaseModel):
    project_id: str
    snapshot_id: str
    timestamp: str
    created_at: str
    revision: int
    size_bytes: int


def _document_store(pipeline: Any) -> DocumentStore:
    return DocumentStore(
        data_dir=pipeline.data_dir,
        writer_lock=getattr(pipeline, "_writer_lock", None),
        lock=getattr(pipeline, "_save_lock", None),
        runtime_check=pipeline.assert_storage_available,
        project_exists=pipeline.read_committed_script,
        mark_storage_failed=getattr(pipeline, "mark_storage_failed", None),
    )


def _raise_document_http_error(exc: ValueError) -> None:
    if isinstance(exc, UnsafeDocumentId):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, (DocumentProjectNotFound, DocumentNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/document")
def save_document(project_id: str, request: SaveDocumentRequest, pipeline: Any = Depends(get_pipeline)):
    """Save the Tiptap JSON document for a committed Studio project."""
    try:
        return _document_store(pipeline).save(
            project_id,
            request.content,
            expected_revision=request.expected_revision,
            create_snapshot=request.create_snapshot,
        )
    except ValueError as exc:
        _raise_document_http_error(exc)


@router.get("/projects/{project_id}/document")
def load_document(project_id: str, pipeline: Any = Depends(get_pipeline)):
    """Load the Tiptap JSON document envelope for a committed Studio project."""
    try:
        return _document_store(pipeline).load(project_id)
    except ValueError as exc:
        _raise_document_http_error(exc)


@router.get("/projects/{project_id}/document/snapshots")
def list_document_snapshots(project_id: str, pipeline: Any = Depends(get_pipeline)):
    """List all document snapshots, ordered by creation time descending."""
    try:
        return _document_store(pipeline).list_snapshots(project_id)
    except ValueError as exc:
        _raise_document_http_error(exc)


@router.post("/projects/{project_id}/document/snapshots")
def create_document_snapshot(project_id: str, pipeline: Any = Depends(get_pipeline)):
    """Create a uniquely named immutable snapshot of the current document."""
    try:
        return _document_store(pipeline).create_snapshot(project_id)
    except ValueError as exc:
        _raise_document_http_error(exc)


@router.post("/projects/{project_id}/document/snapshots/{timestamp}/restore")
def restore_document_snapshot(
    project_id: str,
    timestamp: str,
    request: RestoreDocumentRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Restore a validated snapshot into a new document revision."""
    try:
        return _document_store(pipeline).restore(project_id, timestamp, expected_revision=request.expected_revision)
    except ValueError as exc:
        _raise_document_http_error(exc)


# ═══════════════════════════════════════════════════════════════
# Document Import/Export
# ═══════════════════════════════════════════════════════════════


class ImportDocRequest(BaseModel):
    filename: StrictStr
    content: StrictStr  # base64 encoded file content
    file_type: Literal["fdx", "fountain", "txt"]

    @field_validator("file_type", mode="before")
    @classmethod
    def normalize_file_type(cls, value):
        if isinstance(value, str):
            return value.lower()
        return value


class ExportDocRequest(BaseModel):
    content: dict  # Tiptap JSON document
    format: Literal["pdf", "docx"]
    options: dict = Field(default_factory=dict)

    @field_validator("format", mode="before")
    @classmethod
    def normalize_format(cls, value):
        if isinstance(value, str):
            return value.lower()
        return value


def _parse_fdx(xml_text: str) -> dict:
    """Parse FDX (Final Draft XML) into Tiptap JSON."""
    import re
    from xml.etree.ElementTree import ParseError

    # defusedxml guards against entity-expansion attacks (XML bomb).
    # Hard dependency (declared in requirements.txt) so the parse path is
    # always the hardened one; no stdlib fallback.
    from defusedxml.ElementTree import fromstring as _xml_fromstring

    # Reject DTD/entity declarations outright (defense-in-depth;
    # FDX files never legitimately contain them)
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise HTTPException(status_code=400, detail="FDX XML with DTD/entity declarations is not allowed")

    FDX_TO_NODE: dict = {
        "Scene Heading": "sceneHeading",
        "Action": "action",
        "Character": "characterCue",
        "Dialogue": "dialogue",
        "Parenthetical": "parenthetical",
        "Transition": "transition",
    }

    try:
        root = _xml_fromstring(xml_text)
    except (ParseError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid FDX XML: {e}")

    content_nodes = []
    # Find all Paragraph elements (support both with and without Content wrapper)
    paragraphs = root.findall(".//Paragraph")

    for para in paragraphs:
        para_type = para.get("Type", "Action")
        node_type = FDX_TO_NODE.get(para_type, "action")

        # Extract text from Text elements
        texts = []
        for text_el in para.findall(".//Text"):
            text_content = text_el.text or ""
            style = text_el.get("Style", "")
            marks = []
            if "Bold" in style:
                marks.append({"type": "bold"})
            if "Italic" in style:
                marks.append({"type": "italic"})
            if "Underline" in style:
                marks.append({"type": "underline"})

            text_node: dict = {"type": "text", "text": text_content}
            if marks:
                text_node["marks"] = marks
            texts.append(text_node)

        if not texts:
            texts = [{"type": "text", "text": ""}]

        node: dict = {"type": node_type, "content": texts}

        # Add attributes for scene headings
        if node_type == "sceneHeading":
            full_text = "".join(t.get("text", "") for t in texts)
            match = re.match(r"^(INT|EXT|INT/EXT|I/E)[\./\s]", full_text, re.IGNORECASE)
            attrs: dict = {"id": str(uuid.uuid4())}
            if match:
                attrs["intExt"] = match.group(1).upper()
            node["attrs"] = attrs

        content_nodes.append(node)

    return {"type": "doc", "content": content_nodes}


def _parse_fountain(text: str) -> dict:
    """Parse Fountain format text into Tiptap JSON."""
    import re

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_nodes = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Empty lines - skip
        if not stripped:
            i += 1
            continue

        # Forced scene heading (starts with .)
        if stripped.startswith(".") and len(stripped) > 1 and not stripped.startswith(".."):
            heading_text = stripped[1:]
            match = re.match(r"^(INT|EXT|INT/EXT|I/E)[\./\s]", heading_text, re.IGNORECASE)
            attrs: dict = {"id": str(uuid.uuid4())}
            if match:
                attrs["intExt"] = match.group(1).upper()
            content_nodes.append({
                "type": "sceneHeading",
                "attrs": attrs,
                "content": [{"type": "text", "text": heading_text}],
            })
            i += 1
            continue

        # Scene heading: INT./EXT. pattern
        if re.match(r"^(INT|EXT|INT/EXT|I/E)[\./\s]", stripped, re.IGNORECASE):
            match = re.match(r"^(INT|EXT|INT/EXT|I/E)", stripped, re.IGNORECASE)
            attrs = {"id": str(uuid.uuid4())}
            if match:
                attrs["intExt"] = match.group(1).upper()
            content_nodes.append({
                "type": "sceneHeading",
                "attrs": attrs,
                "content": [{"type": "text", "text": stripped}],
            })
            i += 1
            continue

        # Transition: all caps ending with colon, or starts with >
        if stripped.startswith(">"):
            t_text = stripped[1:].strip()
            content_nodes.append({
                "type": "transition",
                "content": [{"type": "text", "text": t_text}],
            })
            i += 1
            continue

        if (
            stripped.isupper()
            and stripped.endswith(":")
            and len(stripped) > 2
            and not stripped.startswith("(")
        ):
            content_nodes.append({
                "type": "transition",
                "content": [{"type": "text", "text": stripped}],
            })
            i += 1
            continue

        # Character cue: all uppercase, preceded by empty line (check)
        # In Fountain spec: a line of all-uppercase text
        if (
            stripped.isupper()
            and len(stripped) > 1
            and not stripped.endswith(":")
            and (i == 0 or not lines[i - 1].strip())
        ):
            content_nodes.append({
                "type": "characterCue",
                "content": [{"type": "text", "text": stripped}],
            })
            i += 1

            # Following lines until empty line are dialogue/parenthetical
            while i < len(lines) and lines[i].strip():
                dline = lines[i].strip()
                if dline.startswith("(") and dline.endswith(")"):
                    content_nodes.append({
                        "type": "parenthetical",
                        "content": [{"type": "text", "text": dline}],
                    })
                else:
                    content_nodes.append({
                        "type": "dialogue",
                        "content": [{"type": "text", "text": dline}],
                    })
                i += 1
            continue

        # Default: Action
        content_nodes.append({
            "type": "action",
            "content": [{"type": "text", "text": stripped}],
        })
        i += 1

    return {"type": "doc", "content": content_nodes}


def _parse_txt(text: str) -> dict:
    """Parse plain text into Tiptap JSON (all as action nodes)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_nodes = []

    for line in lines:
        if line.strip():
            content_nodes.append({
                "type": "action",
                "content": [{"type": "text", "text": line.strip()}],
            })

    if not content_nodes:
        content_nodes = [{"type": "action", "content": [{"type": "text", "text": ""}]}]

    return {"type": "doc", "content": content_nodes}


def _validate_tiptap_export_doc(doc: dict) -> dict:
    """Reject malformed Tiptap JSON before export formatting touches it."""
    try:
        serialize_json(doc)
    except StorageError as exc:
        raise HTTPException(status_code=422, detail="Document content must be finite JSON") from exc
    if doc.get("type") != "doc":
        raise HTTPException(status_code=422, detail="Expected a Tiptap document")
    pending = [doc]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            raise HTTPException(status_code=422, detail="Invalid document node")
        node_type = node.get("type")
        if node_type is not None and not isinstance(node_type, str):
            raise HTTPException(status_code=422, detail="Invalid document node type")
        if "text" in node and not isinstance(node["text"], str):
            raise HTTPException(status_code=422, detail="Invalid document text")
        children = node.get("content", [])
        if not isinstance(children, list):
            raise HTTPException(status_code=422, detail="Invalid document content")
        pending.extend(children)
    return doc


@router.post("/projects/{project_id}/document/import")
def import_document(project_id: str, req: ImportDocRequest, pipeline: Any = Depends(get_pipeline)):
    """Import FDX/Fountain/TXT file and convert to Tiptap JSON."""
    import base64
    import binascii

    try:
        _document_store(pipeline).ensure_project(project_id)
    except ValueError as exc:
        _raise_document_http_error(exc)

    try:
        raw_bytes = base64.b64decode(req.content, validate=True)
        text = raw_bytes.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Failed to decode file content: {e}")

    file_type = req.file_type

    if file_type == "fdx":
        doc = _parse_fdx(text)
    elif file_type == "fountain":
        doc = _parse_fountain(text)
    else:
        doc = _parse_txt(text)

    return {"content": doc, "filename": req.filename, "file_type": file_type}


@router.post("/projects/{project_id}/document/export")
def export_document(project_id: str, req: ExportDocRequest, pipeline: Any = Depends(get_pipeline)):
    """Export Tiptap JSON to PDF/DOCX."""
    from fastapi.responses import Response

    try:
        _document_store(pipeline).ensure_project(project_id)
    except ValueError as exc:
        _raise_document_http_error(exc)

    doc = _validate_tiptap_export_doc(req.content)
    fmt = req.format

    # Helper to extract plain text from Tiptap doc
    def extract_text(node: dict) -> str:
        if node.get("text"):
            return node["text"]
        children = node.get("content", [])
        return "".join(extract_text(c) for c in children)

    def doc_to_lines(doc: dict) -> list:
        lines = []
        for node in doc.get("content", []):
            node_type = node.get("type", "action")
            text = extract_text(node)
            lines.append((node_type, text))
        return lines

    if fmt == "pdf":
        # Missing optional rendering dependencies are explicit failures.
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.units import inch
            from reportlab.platypus import SimpleDocTemplate, Paragraph as RLParagraph
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.enums import TA_CENTER, TA_RIGHT
            import io

            buffer = io.BytesIO()
            doc_pdf = SimpleDocTemplate(buffer, pagesize=letter,
                                        leftMargin=1.5 * inch, rightMargin=1 * inch,
                                        topMargin=1 * inch, bottomMargin=1 * inch)

            styles = getSampleStyleSheet()
            styles.add(ParagraphStyle("SceneHeading", parent=styles["Heading3"],
                                      fontName="Helvetica-Bold", fontSize=12,
                                      spaceAfter=6, spaceBefore=18))
            styles.add(ParagraphStyle("CharacterCue", parent=styles["Normal"],
                                      fontName="Helvetica-Bold", fontSize=10,
                                      alignment=TA_CENTER, spaceBefore=12))
            styles.add(ParagraphStyle("DialogueStyle", parent=styles["Normal"],
                                      fontName="Helvetica", fontSize=10,
                                      leftIndent=1.5 * inch, rightIndent=1.5 * inch))
            styles.add(ParagraphStyle("TransitionStyle", parent=styles["Normal"],
                                      fontName="Helvetica-Bold", fontSize=10,
                                      alignment=TA_RIGHT, spaceBefore=12))

            story = []
            lines = doc_to_lines(doc)
            style_map = {
                "sceneHeading": "SceneHeading",
                "characterCue": "CharacterCue",
                "dialogue": "DialogueStyle",
                "transition": "TransitionStyle",
            }

            for node_type, text in lines:
                style_name = style_map.get(node_type, "Normal")
                story.append(RLParagraph(text or " ", styles[style_name]))

            doc_pdf.build(story)
            pdf_bytes = buffer.getvalue()
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="script.pdf"'},
            )

        except ImportError as exc:
            raise HTTPException(status_code=503, detail="PDF export requires reportlab") from exc

    elif fmt == "docx":
        # Try python-docx, fallback to plain text
        try:
            from docx import Document as DocxDocument
            from docx.shared import Inches
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            import io

            document = DocxDocument()
            lines = doc_to_lines(doc)

            for node_type, text in lines:
                if node_type == "sceneHeading":
                    p = document.add_paragraph()
                    p.style = document.styles["Heading 3"]
                    run = p.add_run(text.upper())
                    run.bold = True
                elif node_type == "characterCue":
                    p = document.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = p.add_run(text.upper())
                    run.bold = True
                elif node_type == "dialogue":
                    p = document.add_paragraph()
                    p.paragraph_format.left_indent = Inches(1.5)
                    p.paragraph_format.right_indent = Inches(1.5)
                    p.add_run(text)
                elif node_type == "transition":
                    p = document.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    run = p.add_run(text.upper())
                    run.bold = True
                else:
                    document.add_paragraph(text)

            buffer = io.BytesIO()
            document.save(buffer)
            docx_bytes = buffer.getvalue()

            return Response(
                content=docx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": 'attachment; filename="script.docx"'},
            )

        except ImportError as exc:
            raise HTTPException(status_code=503, detail="DOCX export requires python-docx") from exc

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")


# ═══════════════════════════════════════════════════════════════
# Script Editor - Derivation Sync & Pipeline Integration
# ═══════════════════════════════════════════════════════════════


class SyncDerivationRequest(BaseModel):
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    source_document_revision: Annotated[StrictInt, Field(ge=0)]
    scenes: List[Dict[str, Any]] = Field(default_factory=list)
    characters: List[Dict[str, Any]] = Field(default_factory=list)
    locations: List[StrictStr] = Field(default_factory=list)
    estimated_duration: StrictFiniteFloat = 0
    word_count: StrictInt = 0
    confidence_score: StrictFiniteFloat = 0

    @field_validator("scenes", "characters", "locations")
    @classmethod
    def validate_finite_derivation_payload(cls, value):
        try:
            serialize_json(value)
        except Exception as exc:
            raise ValueError("Derivation payload must be finite JSON") from exc
        return value

class DeriveGapEntity(BaseModel):
    type: Literal["character", "prop", "beat", "location"]
    name: StrictStr
    description: Optional[StrictStr] = None
    confidence: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=1)]
    scene_index: Optional[Annotated[StrictInt, Field(ge=0)]] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Entity name must not be blank")
        return value.strip()


class DeriveGapsResponse(BaseModel):
    entities: List[DeriveGapEntity]
    cached: bool = False  # 是否来自缓存


class DeriveGapsRequest(BaseModel):
    already_extracted: Optional[Dict[str, List[str]]] = None  # {"scenes": [...], "characters": [...]}
    gaps: List[str] = Field(default_factory=lambda: ["characters", "props", "beats", "locations"])
    raw_text_blocks: Optional[List[StrictStr]] = None  # 原始文本块（可选，否则从项目文档读取）


class ConfirmShotBlockRequest(BaseModel):
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    source_document_revision: Annotated[StrictInt, Field(ge=0)]
    shot_type: Optional[StrictStr] = None
    camera_movement: Optional[StrictStr] = None
    duration: Optional[StrictFiniteFloat] = None
    description: Optional[StrictStr] = None
    characters: List[StrictStr] = Field(default_factory=list)


class DeriveGapsLLMError(RuntimeError):
    """Raised when gap extraction cannot trust an LLM response."""


def _extract_tiptap_text_blocks(node: Any) -> List[str]:
    blocks: List[str] = []
    pending = [node]
    while pending:
        value = pending.pop()
        if not isinstance(value, dict):
            raise ValueError("Invalid document node")
        text = value.get("text")
        if text is not None and not isinstance(text, str):
            raise ValueError("Invalid document text")
        if text and text.strip():
            blocks.append(text.strip())
        children = value.get("content", [])
        if not isinstance(children, list):
            raise ValueError("Invalid document content")
        pending.extend(reversed(children))
    return blocks


@router.get("/projects/{project_id}/sync_derivation")
def get_sync_derivation(project_id: str, pipeline: Any = Depends(get_pipeline)):
    """Read the app-owned derivation sidecar revision and payload."""
    try:
        return _document_store(pipeline).load_derivation(project_id)
    except ValueError as exc:
        _raise_document_http_error(exc)


@router.post("/projects/{project_id}/sync_derivation")
def sync_derivation(project_id: str, req: SyncDerivationRequest, pipeline: Any = Depends(get_pipeline)):
    """Persist L1 derivation under the current app storage owner."""
    data = {
        "scenes": req.scenes,
        "characters": req.characters,
        "locations": req.locations,
        "estimated_duration": req.estimated_duration,
        "word_count": req.word_count,
        "confidence_score": req.confidence_score,
    }
    try:
        saved = _document_store(pipeline).save_derivation(
            project_id,
            data,
            expected_revision=req.expected_revision,
            source_document_revision=req.source_document_revision,
        )
    except ValueError as exc:
        _raise_document_http_error(exc)
    return {
        "status": "ok",
        "synced_at": saved["updated_at"],
        "revision": saved["revision"],
        "updated_at": saved["updated_at"],
        "source_document_revision": saved["source_document_revision"],
        "data": saved["data"],
    }


# ── derive_gaps helpers ──────────────────────────────────────────

def _build_derive_gaps_prompt(
    text_blocks: List[str],
    gaps: List[str],
    already_extracted: Dict[str, List[str]],
) -> str:
    """Construct the LLM prompt for incremental gap extraction."""
    text_content = "\n---\n".join(text_blocks)

    already_section = ""
    if already_extracted:
        parts = []
        for k, v in already_extracted.items():
            parts.append(f"  - {k}: {', '.join(v)}")
        already_section = (
            "\n\n以下内容已被提取，请勿重复输出：\n" + "\n".join(parts)
        )

    gap_descriptions = {
        "characters": "角色（人物名称、简短描述）",
        "props": "道具（从动作描述中识别关键道具）",
        "beats": "节拍（场景中的情感转折点或关键节奏）",
        "locations": "地点（从场景描述中提取具体地点）",
    }
    requested = ", ".join(gap_descriptions.get(g, g) for g in gaps)

    return f"""你是一名专业的剧本分析助手。请从以下文本中提取缺失的结构化信息。

需要提取的类型：{requested}
{already_section}

请以 JSON 对象返回结果，其中 entities 字段是数组，每个元素包含：
- type: 类型（"character" | "prop" | "beat" | "location"）
- name: 名称
- description: 简短描述（可选）
- confidence: 置信度 0.0-1.0
- scene_index: 关联场景序号（从 0 开始，可选）

仅返回包含 entities 的 JSON 对象，不要包含其他文字；没有结果时返回 {{"entities": []}}。

# 文本内容
{text_content}"""


def _call_llm_for_gaps(
    text_blocks: List[str],
    gaps: List[str],
    already_extracted: Dict[str, List[str]],
    *, env=None,
) -> List[DeriveGapEntity]:
    """Call LLM (Qwen) for incremental gap extraction with bounded request options."""
    from .llm_adapter import LLMAdapter
    from .llm import _strip_markdown_json

    adapter = LLMAdapter(**({"env": env} if env is not None else {}))
    if not adapter.is_configured:
        logger.warning("derive_gaps: LLM not configured")
        raise DeriveGapsLLMError("Gap extraction is not configured")

    prompt = _build_derive_gaps_prompt(text_blocks, gaps, already_extracted)
    messages = [{"role": "user", "content": prompt}]

    try:
        raw = adapter.chat(
            messages=messages,
            model=adapter._get_default_model(),
            response_format={"type": "json_object"},
            timeout=2,
            max_retries=0,
        )
    except Exception as exc:
        logger.error(f"derive_gaps: LLM call failed: {exc}")
        raise DeriveGapsLLMError("Gap extraction failed") from exc

    try:
        if not isinstance(raw, str):
            raise DeriveGapsLLMError("Malformed LLM response")
        raw = _strip_markdown_json(raw)
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("entities") if "entities" in data else data.get("items")
        if not isinstance(data, list):
            raise DeriveGapsLLMError("Malformed LLM response")
        return [DeriveGapEntity.model_validate(item) for item in data]
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        logger.warning("derive_gaps: failed to parse LLM JSON response")
        raise DeriveGapsLLMError("Malformed LLM response") from exc


@router.post("/projects/{project_id}/derive_gaps")
def derive_gaps(project_id: str, req: DeriveGapsRequest, pipeline: Any = Depends(get_pipeline)):
    """L3 LLM incremental extraction from explicit text or the app-owned document."""
    store = _document_store(pipeline)
    try:
        if req.raw_text_blocks is not None:
            store.ensure_project(project_id)
            text_blocks = [block for block in req.raw_text_blocks if block.strip()]
        else:
            document = store.load(project_id)
            text_blocks = _extract_tiptap_text_blocks(document["content"])
    except ValueError as exc:
        _raise_document_http_error(exc)

    if not text_blocks:
        return DeriveGapsResponse(entities=[], cached=False)

    already = req.already_extracted or {}
    try:
        entities = _call_llm_for_gaps(text_blocks, req.gaps, already, env=pipeline.environment_snapshot())
    except DeriveGapsLLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if req.already_extracted:
        entities = _filter_already_extracted(entities, req.already_extracted)

    return DeriveGapsResponse(entities=entities, cached=False)


def _filter_already_extracted(
    entities: List[DeriveGapEntity],
    already_extracted: Dict[str, List[str]],
    *, env=None,
) -> List[DeriveGapEntity]:
    """Remove entities whose name already appears in already_extracted."""
    known_names: set = set()
    for names_list in already_extracted.values():
        for n in names_list:
            known_names.add(n.strip().lower())
    return [e for e in entities if e.name.strip().lower() not in known_names]


@router.get("/projects/{project_id}/shot_blocks")
def get_shot_blocks(project_id: str, pipeline: Any = Depends(get_pipeline)):
    """Read the app-owned shot-block sidecar revision and blocks."""
    try:
        return _document_store(pipeline).load_shot_blocks(project_id)
    except ValueError as exc:
        _raise_document_http_error(exc)


@router.post("/projects/{project_id}/shot_blocks/{shot_id}/confirm")
def confirm_shot_block(
    project_id: str,
    shot_id: str,
    req: ConfirmShotBlockRequest,
    pipeline: Any = Depends(get_pipeline),
):
    """Confirm a ShotBlock in app-owned sidecar storage."""
    block = {
        "shot_type": req.shot_type,
        "camera_movement": req.camera_movement,
        "duration": req.duration,
        "description": req.description,
        "characters": req.characters,
    }
    try:
        return _document_store(pipeline).confirm_shot_block(
            project_id,
            shot_id,
            block,
            expected_revision=req.expected_revision,
            source_document_revision=req.source_document_revision,
        )
    except ValueError as exc:
        _raise_document_http_error(exc)


# Compatible Uvicorn entry point; importing this object never starts its runtime.
app = create_app(recover_orphan_tasks=True, setup_logs=True)
