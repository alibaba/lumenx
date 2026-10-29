from typing import TYPE_CHECKING, Dict, Any, List, Optional, Tuple
from contextlib import contextmanager
from copy import deepcopy
from pydantic import ValidationError
import json
import os
import re
import time
import uuid
import subprocess
import threading
import hashlib
from pathlib import Path
from urllib.parse import quote, urlsplit
from .models import Script, GenerationStatus, VideoTask, Character, Scene, Prop, StoryboardFrame, Series, PromptConfig, ArtDirection, GlobalAssetLibrary, ModelSettings, AssetUnit, ImageAsset, ImageVariant
from .models import DialogueAudioOptions, DialogueAudioSnapshot, DubSource, ProviderRequestReceipt, ProviderBatchRequestReceipt, ProviderRequestReceiptMember, MotionReferenceReceipt
from .llm import ScriptProcessor, PolishError
from .assets import AssetGenerator, AssetGenerationOutputError
from .storyboard import StoryboardGenerator
from .video import VideoGenerator
from .audio import AudioGenerator, UnsupportedAudioParameters, dialogue_prepared_projection, prepared_dialogue_is_stale
from .export import ExportManager
from ...utils.oss_utils import is_object_key
from ...utils import get_logger
from ...utils.provider_registry import resolve_provider_backend
from ...utils.endpoints import get_provider_base_url
from ...utils.system_check import get_ffmpeg_path, get_ffmpeg_install_instructions
from ...utils.json_storage import CorruptStorageError, DirectoryWriterLock, StorageConflictError, StorageError, read_json, serialize_json
from ...utils.json_transactions import JsonFileTransaction
from .series_import import (
    IMPORT_PREVIEW_TTL_SECONDS, MAX_PENDING_IMPORTS, ImportRecord, ImportResult,
    ImportPreviewUnavailable, import_fingerprint, load_import_records, validate_episodes, validate_records,
)
from .series_episode import CreateSeriesEpisodeRequest, SeriesEpisodeCreationReceipt, episode_creation_fingerprint
from ...utils import media_versions
from ...utils.media_refs import LOCAL_MEDIA_PREFIXES
from ...utils.storage_identity import initialize_storage_identity
from ...utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION, fingerprint_request, validate_client_request_id
from ...utils.model_catalog import get_catalog_accessor
from ...models.provider_binding import ProviderCredentialResolutionError, ProviderCredentialStore
from ...models.provider_tasks import ProviderBindingError, ProviderFetchError, ProviderQueryError, ProviderSubmitError, ProviderTaskStatus
from ...models.provider_recovery import (
    build_accepted_provider_submission_and_record as _build_accepted_provider_record,
    prepare_submission_transport as _prepare_submission_transport,
)
from . import provider_recovery

if TYPE_CHECKING:
    from .asset_image_execution import AssetImageAdmission


logger = get_logger(__name__)
_AUDIO_UNSET = object()
_LAST_FRAME_URL_UNSET = object()
_ORDERED_WAN3_LEGACY_VIDEO_TASK_MODELS = frozenset({
    "wan3.0-video",
    "wan3.0-video-prime",
    "wan/wan3.0-video#video",
    "wan/wan3.0-video-prime#video",
})


def _is_ordered_wan3_legacy_video_task_model(model: object) -> bool:
    return isinstance(model, str) and model in _ORDERED_WAN3_LEGACY_VIDEO_TASK_MODELS


class CommandValidationError(ValueError):
    """The requested value cannot be persisted; the storage runtime stays usable."""


def _validate_setting_value(value) -> None:
    # Use exactly the durable writer's JSON rules before mutating live state.
    try:
        serialize_json(value.model_dump())
    except (StorageError, ValueError, TypeError) as exc:
        raise CommandValidationError("Settings must contain finite JSON values and valid UTF-8 text") from exc


class ProjectContextError(ValueError):
    """A project/context prerequisite is missing or invalid."""

    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


class ProjectGenerationError(RuntimeError):
    """A generation call failed or returned invalid project/frame data."""


def project_text_revision(text: str) -> str:
    """Keep the existing episode-cache marker compatible with saved projects."""
    return f"{len(text)}-{hashlib.md5(text.encode('utf-8')).hexdigest()[:12]}"

# --- Security helpers ---

# Allowed pattern for IDs used in file paths (UUID hex + hyphens)
_SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]+$')


def _validate_safe_id(value: str, label: str = "id") -> str:
    """Ensure a value is safe to embed in file paths / command args (UUID-like)."""
    if not value or not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {label}: contains unsafe characters")
    return value


def _safe_resolve_path(base_dir: str, untrusted_rel: str) -> str:
    """Resolve *untrusted_rel* under *base_dir* and ensure the result stays inside it.

    Prevents path-traversal attacks (e.g. ``../../etc/passwd``).
    Returns the resolved absolute path; raises ValueError on escape attempts.
    """
    base = os.path.realpath(base_dir)
    resolved = os.path.realpath(os.path.join(base, untrusted_rel))
    if not resolved.startswith(base + os.sep):
        raise ValueError(f"Path escapes base directory: {untrusted_rel}")
    return resolved


class LibraryAssetInUseError(Exception):
    """Raised when a global library asset cannot be hard-deleted because it is
    still referenced by one or more storyboard frames (design Q2 reference
    integrity). Carries the referrers so the API can surface them (HTTP 409).

    ``references`` is a list of dicts, each:
        {"owner_kind": "project"|"series", "owner_id": str,
         "owner_title": Optional[str], "frame_id": str}
    """

    def __init__(self, asset_type: str, asset_id: str, references: List[Dict[str, Any]]):
        self.asset_type = asset_type
        self.asset_id = asset_id
        self.references = references
        super().__init__(
            f"Library {asset_type} {asset_id} is referenced by "
            f"{len(references)} storyboard frame(s); refusing to delete "
            f"(pass force=True to delete anyway)."
        )


class ComicGenPipeline:
    def __init__(
        self,
        config: Dict[str, Any] = None,
        data_dir: str | os.PathLike[str] = "output",
        recover_orphan_tasks: bool = False,
        warmup_demucs: bool = False,
        repair_series_bindings: bool = False,
        script_processor: Any = None,
        runtime_environment=None,
    ):
        self.config = config or {}
        self.runtime_environment = runtime_environment
        self._component_overrides = {}
        self._standalone_components = {}
        self.data_dir = Path(data_dir).expanduser().resolve()
        self._save_lock = threading.RLock()
        self._storage_failed: StorageError | None = None
        self._closed = False
        self._writer_lock = DirectoryWriterLock(self.data_dir).acquire()
        try:
            self._storage = JsonFileTransaction(self.data_dir, (
                "projects.json", "series.json", "library_assets.json", "import_receipts.json",
            ))
            self._storage.recover()
            self.storage_identity = initialize_storage_identity(
                self.data_dir, writer_lock=self._writer_lock,
            )
            self._initialize_runtime(recover_orphan_tasks, warmup_demucs, repair_series_bindings, script_processor)
        except BaseException:
            self.close()
            raise

    def _initialize_runtime(self, recover_orphan_tasks, warmup_demucs, repair_series_bindings, script_processor):

        output_root = str(self.data_dir)
        if script_processor is not None:
            self._component_overrides["script"] = script_processor
        for name in ("script", "assets", "storyboard", "video", "audio"):
            self._component(name)
        self.export_manager = ExportManager(self._component_config("export", self.data_dir / "export", output_root))

        self.data_file = str(self.data_dir / "projects.json")
        self.series_data_file = str(self.data_dir / "series.json")
        self.library_data_file = str(self.data_dir / "library_assets.json")
        self.scripts: Dict[str, Script] = self._load_data()
        self.series_store: Dict[str, Series] = self._load_series_data()
        # Project-independent global asset library (lowest resolver layer).
        self.library_store: GlobalAssetLibrary = self._load_library_data()
        self.import_receipts = load_import_records(self.data_dir / "import_receipts.json")
        self._import_preview_inflight = 0
        self._committed = self._storage_payloads(("projects.json", "series.json", "library_assets.json", "import_receipts.json"))
        if repair_series_bindings:
            self._repair_series_bindings()

        # Cache key includes the committed revision, requested text and prompt.
        self._extraction_cache: Dict[str, tuple] = {}

        # Task management for async asset generation
        # Format: { task_id: { status: str, progress: int, error: str, script_id: str, asset_id: str, created_at: float } }
        self.asset_generation_tasks: Dict[str, Dict[str, Any]] = {}
        self.video_generation_tasks: Dict[str, Dict[str, Any]] = {}
        self._video_task_runs: Dict[Tuple[str, str], object] = {}
        # Cached model instances (lazily initialized)
        self._kling_model = None
        self._vidu_model = None
        self._mulerouter_video_model = None
        self.provider_recovery_runtime = None
        self.provider_runtime_selector = None
        self._provider_credential_store = ProviderCredentialStore(
            self.data_dir,
            writer_lock=self._writer_lock,
            runtime_lock=self._save_lock,
        )

        # Pre-download Demucs model in background so first dub request is fast
        self._demucs_ready = threading.Event()
        self._demucs_error: Optional[str] = None
        if warmup_demucs:
            threading.Thread(target=self._warmup_demucs_model, daemon=True).start()
        else:
            self._demucs_ready.set()

        # A process restart cannot prove what happened at the provider. Legacy
        # records lack scoped receipts; retain them as unconfirmed without any
        # network operation or automatic resubmission.
        if recover_orphan_tasks:
            try:
                self._recover_orphan_tasks()
            except StorageError:
                raise
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("Orphan task recovery failed: %s", exc)

    def environment_snapshot(self):
        """None is reserved for standalone compatibility, never app assembly."""
        environment = getattr(self, "runtime_environment", None)
        return None if environment is None else environment.snapshot()

    def _component(self, name):
        overrides = getattr(self, "_component_overrides", {})
        if name in overrides:
            return overrides[name]

        def create(env):
            kwargs = {"env": env} if env is not None else {}
            if name == "script":
                return ScriptProcessor(output_root=str(self.data_dir), **kwargs)
            component_types = {"assets": AssetGenerator, "storyboard": StoryboardGenerator,
                               "video": VideoGenerator, "audio": AudioGenerator}
            return component_types[name](
                self._component_config(name, self.data_dir / name, str(self.data_dir)),
                before_generation=self.assert_storage_available, **kwargs,
            )

        if getattr(self, "runtime_environment", None) is not None:
            return self.runtime_environment.client(f"studio.{name}", create)
        if name not in self._standalone_components:
            self._standalone_components[name] = create(None)
        return self._standalone_components[name]

    def oss_uploader(self):
        from ...utils.oss_utils import OSSImageUploader
        if getattr(self, "runtime_environment", None) is None:
            return OSSImageUploader()
        return self.runtime_environment.client(
            "studio.oss", lambda env: OSSImageUploader(env=env, output_root=self.data_dir),
        )

    @property
    def script_processor(self):
        return self._component("script")

    @script_processor.setter
    def script_processor(self, value):
        self.__dict__.setdefault("_component_overrides", {})["script"] = value

    @property
    def asset_generator(self):
        return self._component("assets")

    @asset_generator.setter
    def asset_generator(self, value):
        self.__dict__.setdefault("_component_overrides", {})["assets"] = value

    @property
    def asset_image_service(self):
        """One adapter shares this pipeline's storage and process-local claims."""
        from .asset_image_execution import StudioAssetImageService
        with self._save_lock:
            if not hasattr(self, "_asset_image_service"):
                self._asset_image_service = StudioAssetImageService(self)
            return self._asset_image_service

    @property
    def motion_reference_service(self):
        from .motion_reference import StudioMotionReferenceService
        with self._save_lock:
            if not hasattr(self, "_motion_reference_service"):
                self._motion_reference_service = StudioMotionReferenceService(self)
            return self._motion_reference_service

    @property
    def storyboard_generator(self):
        return self._component("storyboard")

    @storyboard_generator.setter
    def storyboard_generator(self, value):
        self.__dict__.setdefault("_component_overrides", {})["storyboard"] = value

    @property
    def video_generator(self):
        return self._component("video")

    @video_generator.setter
    def video_generator(self, value):
        self.__dict__.setdefault("_component_overrides", {})["video"] = value

    @property
    def audio_generator(self):
        return self._component("audio")

    @audio_generator.setter
    def audio_generator(self, value):
        self.__dict__.setdefault("_component_overrides", {})["audio"] = value

    def close(self) -> None:
        """Stop new writes before releasing this runtime's directory ownership."""
        with self._save_lock:
            self._closed = True
            self._writer_lock.close()

    def assert_storage_available(self) -> None:
        if self._closed:
            raise StorageError("Storage runtime is closed; restart the application")
        if self._storage_failed is not None:
            raise StorageError(
                "Storage save failed; restart the application before continuing",
                commit_uncertain=self._storage_failed.commit_uncertain,
            ) from self._storage_failed

    def _storage_payloads(self, names):
        payloads = {}
        for name in names:
            if name == "projects.json":
                payloads[name] = {key: value.model_dump(mode="json") for key, value in self.scripts.items()}
            elif name == "series.json":
                payloads[name] = {key: value.model_dump(mode="json") for key, value in self.series_store.items()}
            elif name == "library_assets.json":
                payloads[name] = self.library_store.model_dump(mode="json")
            elif name == "import_receipts.json":
                payloads[name] = {key: value.model_dump(mode="json") for key, value in self.import_receipts.items()}
            else:
                raise ValueError(f"Unknown Studio storage file: {name}")
        return payloads

    def mark_storage_failed(self, error: StorageError) -> None:
        """Close this runtime after an associated sidecar storage failure."""
        with self._save_lock:
            self._storage_failed = error

    def _commit_storage(self, *names) -> None:
        """Commit the affected aggregates together; a failed runtime needs restart.

        Closing writes after every storage failure prevents older in-flight
        references from persisting detached objects after the memory rollback.
        Paid output files are intentionally retained for later recovery.
        """
        with self._save_lock:
            self.assert_storage_available()
            try:
                payloads = self._storage_payloads(names)
                # Derive each version from its last committed content, including
                # changes made by older commands that do not set updated_at.
                # Model versions remain unchanged until persistence succeeds.
                for name, model in (("projects.json", Script), ("series.json", Series)):
                    for key, value in payloads.get(name, {}).items():
                        previous = self._committed[name].get(key)
                        if previous is None:
                            value["revision"] = 0
                        else:
                            content = {k: v for k, v in value.items() if k != "revision"}
                            old_content = {k: v for k, v in previous.items() if k != "revision"}
                            value["revision"] = previous["revision"] + int(content != old_content)
                        # Assignment is not validated by these mutable domain
                        # models. Require every saved candidate to be reloadable.
                        candidate = model.model_validate(value)
                        if candidate.id != key:
                            raise ValueError(f"Identity does not match storage key in {name}")
                if "library_assets.json" in payloads:
                    value = payloads["library_assets.json"]
                    previous = self._committed["library_assets.json"]
                    content = {k: v for k, v in value.items() if k != "revision"}
                    old_content = {k: v for k, v in previous.items() if k != "revision"}
                    value["revision"] = previous["revision"] + int(content != old_content)
                    GlobalAssetLibrary.model_validate(value)
                if "import_receipts.json" in payloads:
                    validate_records(payloads["import_receipts.json"])
                self._storage.commit(payloads)
            except (StorageError, ValueError, TypeError) as exc:
                error = exc if isinstance(exc, StorageError) else StorageError("Cannot validate or serialize Studio storage")
                self._storage_failed = error
                for name in names:
                    snapshot = deepcopy(self._committed[name])
                    if name == "projects.json":
                        self.scripts = {key: Script.model_validate(value) for key, value in snapshot.items()}
                    elif name == "series.json":
                        self.series_store = {key: Series.model_validate(value) for key, value in snapshot.items()}
                    elif name == "import_receipts.json":
                        self.import_receipts = validate_records(snapshot)
                    else:
                        self.library_store = GlobalAssetLibrary.model_validate(snapshot)
                logger.error("Studio storage save failed; restart required (commit uncertain=%s)", error.commit_uncertain)
                if error is exc:
                    raise
                raise error from exc
            self._committed.update(deepcopy(payloads))
            for name, records in (("projects.json", self.scripts), ("series.json", self.series_store)):
                for key, value in payloads.get(name, {}).items():
                    records[key].revision = value["revision"]
            if "library_assets.json" in payloads:
                self.library_store.revision = payloads["library_assets.json"]["revision"]

    def _output_path(self, *parts: str) -> str:
        return _safe_resolve_path(str(self.data_dir), os.path.join(*[os.fspath(part) for part in parts]))

    def _output_ref_path(self, ref: str) -> str:
        value = (ref or "").strip()
        for prefix in ("/files/outputs/", "/files/output/", "/files/", "files/", "output/", "outputs/"):
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        return _safe_resolve_path(str(self.data_dir), value)

    def _output_relpath(self, path: str) -> str:
        return os.path.relpath(path, str(self.data_dir))

    def _output_subdir(self, *parts: str) -> str:
        path = self._output_path(*parts)
        os.makedirs(path, exist_ok=True)
        return path

    def _component_config(self, key: str, output_dir: Path, output_root: str) -> Dict[str, Any]:
        base = dict(self.config.get(key) or {})
        base["output_dir"] = str(output_dir)
        base["output_root"] = output_root
        model_config = dict(base.get("model") or {})
        model_config["output_root"] = output_root
        base["model"] = model_config
        return base

    _ORPHAN_RECOVERY_REASON = (
        "Backend was restarted. The provider result is unconfirmed; "
        "no new generation was submitted. Retained provider IDs are diagnostic only."
    )

    def _recover_orphan_tasks(self) -> None:
        """Retain interrupted legacy tasks without inferring provider failure.

        Plain provider IDs do not establish account/region/credential scope.
        This pass performs no submit, query or fetch. Scoped receipts remain
        eligible for the app-owned recovery dispatcher; legacy identifiers do
        not. Matching shared asset mirrors and receipt-less asset states move
        in the same transaction. Asset status covers both images and motion.
        """
        with self._save_lock:
            self.assert_storage_available()
            recovered = 0
            storage_names = set()
            for script in self.scripts.values():
                for task in script.video_tasks or []:
                    if task.status not in {"pending", "processing"}:
                        continue
                    record = task.provider_execution
                    if record is not None and record.handle is not None and (
                        record.prepared_artifact is not None
                        or record.phase in {"submitting", "submitted", "observed"}
                    ):
                        # Receipt validation and credential binding happen in
                        # query/fetch. Never reconstruct a receipt from legacy
                        # provider IDs or resubmit accepted work on startup.
                        continue
                    previous = task.model_dump()
                    task.status = "needs_confirmation"
                    if not task.error:
                        task.error = self._ORPHAN_RECOVERY_REASON
                    # Never overwrite a replaced/terminal/shared task merely
                    # because it has the same ID as this legacy project entry.
                    if task.asset_id and task.project_id == script.id:
                        try:
                            owner, source = self._find_asset_video_task_owner(script, task)
                            if owner is task or owner.model_dump() == previous:
                                self._sync_asset_video_task(script, task)
                                storage_names.update(self._video_task_storage_names(source))
                        except StorageConflictError:
                            pass  # A removed owner cannot erase the surviving task.
                    storage_names.add("projects.json")
                    recovered += 1
            # Startup owns no image/motion workers. A later explicit invocation
            # must not reclassify currently owned executions as interrupted.
            if not getattr(self, "_asset_image_runs", {}) and not getattr(self, "_motion_ref_runs", {}):
                for name, roots in (
                    ("projects.json", self.scripts.values()),
                    ("series.json", self.series_store.values()),
                    ("library_assets.json", (self.library_store,)),
                ):
                    for root in roots:
                        for field in ("characters", "scenes", "props"):
                            for asset in getattr(root, field):
                                if asset.status == GenerationStatus.PROCESSING:
                                    asset.status = "needs_confirmation"
                                    storage_names.add(name)
                                    recovered += 1
                        if isinstance(root, Script) and not getattr(self, "_storyboard_image_runs", {}):
                            for frame in root.frames:
                                if frame.image_request_id and frame.status == GenerationStatus.PROCESSING:
                                    frame.status = "needs_confirmation"
                                    storage_names.add(name)
                                    recovered += 1
            if recovered:
                self._commit_storage(*sorted(storage_names))
                logger.warning("Orphan recovery: retained %d generation record(s) as unconfirmed.", recovered)
            else:
                logger.debug("Orphan task recovery: no interrupted tasks found.")
            # A frozen proof can repair a completed local publication. An
            # accepted record grants no execution right after a restart.
            repairs = [
                (request.request_root_kind, request.request_root_id, request.client_request_id)
                for owner in (*self.scripts.values(), *self.series_store.values(), self.library_store)
                for request in owner.asset_image_requests
                if any((member.execution.phase == "submitting"
                        or (member.execution.schema_version == 3 and member.execution.phase == "observed"))
                       and member.execution.prepared_artifact is not None
                       for member in request.members)
            ]
            for root_kind, root_id, client_request_id in repairs:
                self.asset_image_service.repair_request(root_kind, root_id, client_request_id)

    _MAX_LABEL_LEN = 20

    def annotate_video_task(
        self,
        script_id: str,
        task_id: str,
        is_starred: Optional[bool] = None,
        label: Optional[str] = None,
        clear_label: bool = False,
    ) -> Optional["VideoTask"]:
        """Set the user's review annotations on a video task. Two fields,
        both optional so callers can update either independently:
          - is_starred: shortlist flag, multi-select per shot
          - label: short free-text note (≤20 chars). Pass clear_label=True
            to explicitly remove the label (None on its own means "don't
            change").
        Returns the updated VideoTask, or None if script/task not found
        (caller can decide whether that's a 404)."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                return None
            tasks = getattr(script, "video_tasks", None) or []
            task = next((t for t in tasks if getattr(t, "id", None) == task_id), None)
            if not task:
                return None
            if is_starred is not None:
                task.is_starred = bool(is_starred)
            if clear_label:
                task.label = None
            elif label is not None:
                trimmed = label.strip()[: self._MAX_LABEL_LEN]
                task.label = trimmed or None
            self._save_data()
            committed = self.read_committed_script(script_id)
            return next(item for item in committed.video_tasks if item.id == task_id)

    _T2I_HISTORY_LIMIT = 10
    _MAX_GENERATE_COUNT = 6
    _WORKBENCH_TAB_VALUES = ("t2i_i2v", "direct_r2v")
    _WORKBENCH_LAST_FRAME_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    _WORKBENCH_LAST_FRAME_ROOTS = {p.rstrip("/") for p in LOCAL_MEDIA_PREFIXES} - {"output", "outputs"}
    _WORKBENCH_LAST_FRAME_ROOTS.add("video_inputs")

    def _normalize_workbench_last_frame_url(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("last_frame_url must be a string, null, or omitted")
        raw = value.strip()
        if not raw:
            return None
        parsed = urlsplit(raw)
        if parsed.scheme or raw.startswith("//"):
            raise ValueError("last_frame_url must be a managed local image reference")
        try:
            path = self._output_ref_path(raw)
        except ValueError as exc:
            raise ValueError("last_frame_url must stay inside managed output storage") from exc
        ext = os.path.splitext(path)[1].lower()
        if ext not in self._WORKBENCH_LAST_FRAME_EXTS:
            raise ValueError("last_frame_url must reference a managed local image file")
        if not os.path.isfile(path):
            raise ValueError("last_frame_url media file does not exist")
        ref = self._output_relpath(path).replace(os.sep, "/")
        root = ref.split("/", 1)[0]
        if root not in self._WORKBENCH_LAST_FRAME_ROOTS:
            raise ValueError("last_frame_url must use a supported managed media directory")
        return ref

    def update_frame_workbench(
        self,
        script_id: str,
        frame_id: str,
        workbench_tab_mode: Optional[str] = None,
        t2i_image_urls: Optional[List[str]] = None,
        t2i_selected_index: Optional[int] = None,
        workbench_generate_count: Optional[int] = None,
        last_frame_url: Any = _LAST_FRAME_URL_UNSET,
    ) -> Optional["StoryboardFrame"]:
        """Persist Storyboard R2V workbench state onto a frame.

        Each field is optional; only the ones the caller passes get
        written. The four fields cover everything the per-shot panel
        carries that needs to survive refresh/cross-device:
          - workbench_tab_mode: 't2i_i2v' | 'direct_r2v'
          - t2i_image_urls: full ordered history (caller is the source
            of truth, server clamps to _T2I_HISTORY_LIMIT FIFO)
          - t2i_selected_index: active首帧 index, clamped to range
          - workbench_generate_count: per-shot batch size, clamped to
            [1, _MAX_GENERATE_COUNT]

        Returns the updated StoryboardFrame, or None if the
        script/frame can't be found (caller maps to 404).
        Unknown enum values for workbench_tab_mode are rejected with
        ValueError so a typo doesn't silently persist garbage."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                return None
            frames = getattr(script, "frames", None) or []
            frame = next((f for f in frames if getattr(f, "id", None) == frame_id), None)
            if not frame:
                return None
            normalized_last_frame_url = _LAST_FRAME_URL_UNSET
            if last_frame_url is not _LAST_FRAME_URL_UNSET:
                normalized_last_frame_url = self._normalize_workbench_last_frame_url(last_frame_url)
            if workbench_tab_mode is not None:
                if workbench_tab_mode not in self._WORKBENCH_TAB_VALUES:
                    raise ValueError(
                        f"workbench_tab_mode must be one of {self._WORKBENCH_TAB_VALUES}, "
                        f"got {workbench_tab_mode!r}",
                    )
                frame.workbench_tab_mode = workbench_tab_mode
            if t2i_image_urls is not None:
                # Filter empties + cap FIFO so the client can't grow the
                # list unbounded by repeated calls. The client also caps
                # at the same limit, but defense in depth.
                cleaned = [u for u in t2i_image_urls if isinstance(u, str) and u.strip()]
                if len(cleaned) > self._T2I_HISTORY_LIMIT:
                    cleaned = cleaned[-self._T2I_HISTORY_LIMIT:]
                frame.t2i_image_urls = cleaned
            if t2i_selected_index is not None:
                # Clamp against the resulting URL list, not whatever was
                # there before — t2i_image_urls may have been written
                # this same call.
                urls = frame.t2i_image_urls or []
                if not urls:
                    frame.t2i_selected_index = 0
                else:
                    frame.t2i_selected_index = max(0, min(int(t2i_selected_index), len(urls) - 1))
            if workbench_generate_count is not None:
                frame.workbench_generate_count = max(
                    1, min(int(workbench_generate_count), self._MAX_GENERATE_COUNT)
                )
            if normalized_last_frame_url is not _LAST_FRAME_URL_UNSET:
                frame.last_frame_url = normalized_last_frame_url
            frame.updated_at = time.time()
            self._save_data()
            committed = self.read_committed_script(script_id)
            return next(item for item in committed.frames if item.id == frame_id)

    def upload_t2i_frame(
        self,
        script_id: str,
        frame_id: str,
        file_path: str,
    ) -> Optional["StoryboardFrame"]:
        """Append an uploaded image to a frame's T2I history and auto-select it.

        Mirrors `update_frame_workbench`'s clamping rules (≤ _T2I_HISTORY_LIMIT
        FIFO; t2i_selected_index → index of the newly appended URL). Caller is
        expected to have already saved the file under output/uploads/ and pass
        the relative URL path the frontend can resolve via /files.

        Returns the updated frame, or None if script/frame can't be found.
        """
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                return None
            frames = getattr(script, "frames", None) or []
            frame = next((f for f in frames if getattr(f, "id", None) == frame_id), None)
            if not frame:
                return None
            current = list(getattr(frame, "t2i_image_urls", None) or [])
            current.append(file_path)
            # Same FIFO cap as update_frame_workbench so uploads can't grow
            # the history unbounded either.
            if len(current) > self._T2I_HISTORY_LIMIT:
                current = current[-self._T2I_HISTORY_LIMIT:]
            frame.t2i_image_urls = current
            # Newly uploaded image becomes the active首帧 — Issue 10 design
            # requires the upload immediately unlocks Step 2.
            frame.t2i_selected_index = len(current) - 1
            frame.updated_at = time.time()
            self._save_data()
            committed = self.read_committed_script(script_id)
            return next(item for item in committed.frames if item.id == frame_id)

    def mark_video_task_failed(
        self, script_id: str, task_id: str, error_message: str
    ) -> bool:
        """Belt-and-suspenders setter used by BG-task wrappers when an
        exception escapes the pipeline's own try/except. Writes
        status='failed' + error so the UI never sees an eternal
        spinner. Also used by the cancel endpoint. Returns True when a
        task was found and marked."""
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                return False
            tasks = getattr(script, "video_tasks", None) or []
            task = next((t for t in tasks if getattr(t, "id", None) == task_id), None)
            if not task:
                return False
            if getattr(task, "provider_execution", None) is not None:
                # A provider execution record means local cancel/background errors
                # cannot assert the provider-side outcome. The recovery flow must
                # decide how to close the attempt.
                return False
            if getattr(task, "status", None) in {"completed", "needs_confirmation"}:
                # Late local errors/cancel cannot prove a remote failure or
                # downgrade a successful result.
                return False
            previous_task = task.model_dump()
            task.status = "failed"
            try:
                if not getattr(task, "error", None):
                    task.error = error_message
            except Exception:
                pass
            owner_source = None
            if getattr(task, "asset_id", None):
                try:
                    owner_task, owner_source = self._find_asset_video_task_owner(script, task)
                    if owner_task is task or owner_task.model_dump() == previous_task:
                        owner_source = self._sync_asset_video_task(script, task)
                except StorageConflictError:
                    owner_source = None
            try:
                self._commit_storage(*self._video_task_storage_names(owner_source))
            except StorageError:
                raise
            except Exception:
                logger.warning("mark_video_task_failed: save failed")
            return True

    def _resolve_video_backend(self, model_name: str) -> str:
        try:
            return resolve_provider_backend(model_name, env=self.environment_snapshot())
        except (KeyError, ValueError):
            logger.debug(
                "Provider backend not registered for video model %s, defaulting to dashscope.",
                model_name,
            )
            return "dashscope"
        except Exception as e:
            logger.warning(
                "Unexpected error resolving provider backend for video model %s: %s. "
                "Falling back to dashscope.",
                model_name,
                e,
            )
            return "dashscope"

    # ... (existing methods)

    def export_project(self, script_id: str, options: Dict[str, Any]) -> str:
        """Step 7: Export project to final video."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        export_url = self.export_manager.render_project(script, options)
        return export_url

    def export_project_package(self, script_id: str):
        """Build a portable Studio project package from one committed project view."""
        from .project_package import build_package

        with self._save_lock:
            self.assert_storage_available()
            script, series, library = self.read_committed_project_view(script_id)
            if script is None:
                raise ValueError("Script not found")
            resolved = self._resolve_asset_layers(script, series, library)
            return build_package(
                script,
                resolved_assets=GlobalAssetLibrary(**resolved),
                source_series=series,
                source_library=library,
                data_dir=self.data_dir,
                source_storage_id=self.storage_identity["storage_id"],
                writer_lock=self._writer_lock,
            )

    def import_project_package(self, package_bytes: bytes) -> Script:
        """Stage and commit an independent imported Studio project."""
        from .project_package import discard_prepared_import, prepare_import

        with self._save_lock:
            self.assert_storage_available()
            prepared = prepare_import(
                package_bytes,
                data_dir=self.data_dir,
                writer_lock=self._writer_lock,
            )
            if prepared.script.id in self.scripts:
                try:
                    discard_prepared_import(
                        prepared,
                        data_dir=self.data_dir,
                        writer_lock=self._writer_lock,
                    )
                except Exception:
                    logger.warning("Failed to clean staged project package media after id collision", exc_info=True)
                raise StorageConflictError("Imported project id already exists")
            self.scripts[prepared.script.id] = prepared.script
            try:
                self._commit_storage("projects.json")
            except StorageError as exc:
                if not exc.commit_uncertain:
                    try:
                        discard_prepared_import(
                            prepared,
                            data_dir=self.data_dir,
                            writer_lock=self._writer_lock,
                        )
                    except Exception:
                        logger.warning("Failed to clean staged project package media after commit failure", exc_info=True)
                raise
            return self.read_committed_script(prepared.script.id)

    def _load_data(self) -> Dict[str, Script]:
        data = read_json(self.data_file, dict)
        try:
            if not isinstance(data, dict):
                raise ValueError("Expected a project map")
            projects = {key: Script.model_validate(value) for key, value in data.items()}
            if any(key != value.id for key, value in projects.items()):
                raise ValueError("Project key does not match its identity")
            return projects
        except (ValidationError, TypeError, ValueError) as exc:
            raise CorruptStorageError("Invalid projects.json schema; existing data preserved") from exc

    def _save_data(self):
        self._commit_storage("projects.json")

    def _repair_series_bindings(self):
        """Repair episodes listed in series.episode_ids that have series_id=None."""
        repaired = False
        for series_id, series in self.series_store.items():
            for ep_id in series.episode_ids:
                script = self.scripts.get(ep_id)
                if script and not script.series_id:
                    script.series_id = series_id
                    if not script.episode_number:
                        script.episode_number = series.episode_ids.index(ep_id) + 1
                    repaired = True
                    logger.info(f"Repaired series binding: episode {ep_id} → series {series_id}")
        if repaired:
            self._save_data()

    def create_project(self, title: str, text: str, skip_analysis: bool = False, workflow_mode: str = "i2v_legacy", series_id: Optional[str] = None) -> Script:
        """Step 1: Parse novel and create project.

        When `series_id` is provided the new project is bound as the next
        episode of that existing series (episode_number = current max
        episode number in the series + 1) via the same
        `add_episode_to_series` mechanism used elsewhere. When `series_id`
        is None the behavior is the original standalone-project path,
        bit-for-bit unchanged.
        """
        self.assert_storage_available()
        with self._save_lock:
            if series_id and series_id not in self.series_store:
                raise ValueError("Series not found")
        if skip_analysis:
            script = self.script_processor.create_draft_script(title, text)
        else:
            script = self.script_processor.parse_novel(title, text)

        script.workflow_mode = workflow_mode
        with self._save_lock:
            self.assert_storage_available()
            if series_id:
                series = self.series_store.get(series_id)
                if not series:
                    raise ValueError("Series not found")
                existing = self.get_series_episodes(series_id)
                max_ep = max([ep.episode_number for ep in existing if ep.episode_number] or [0])
                self.scripts[script.id] = script
                self.add_episode_to_series(series_id, script.id, episode_number=max_ep + 1)
            else:
                self.scripts[script.id] = script
                self._save_data()
        return script

    def delete_project(self, script_id: str) -> Script:
        """Remove a project and its series membership in one saved command."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Project not found")
            series = self.series_store.get(script.series_id) if script.series_id else None
            if series and script_id in series.episode_ids:
                series.episode_ids.remove(script_id)
                series.updated_at = time.time()
            del self.scripts[script_id]
            self._commit_storage("projects.json", "series.json")
            return script

    def reconcile_project_assets(self, script_id: str, actions: Dict[str, List[Dict[str, Any]]]) -> Script:
        """Move local assets and their frame references in one saved command."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Project not found")
            series = self.series_store.get(script.series_id) if script.series_id else None
            if series is None:
                raise ValueError("Project not in a series")
            def apply_list(local_pool_attr: str, series_pool_attr: str, actions: list, frame_ref_attr: Optional[str] = None):
                local_pool = getattr(script, local_pool_attr)
                series_pool = getattr(series, series_pool_attr)
                for act in actions:
                    local_item = next((x for x in local_pool if x.id == act["local_id"]), None)
                    if not local_item:
                        continue
                    if act["action"] == "create_new_in_series":
                        # Move media, leaving request history at the original owner.
                        if isinstance(local_item, Character):
                            local_item.reference_sheet_request_id = None
                            local_item.full_body_request_id = None
                            local_item.three_view_request_id = None
                            local_item.headshot_request_id = None
                            local_item.full_body_motion_request_id = None
                            local_item.head_shot_motion_request_id = None
                        elif isinstance(local_item, (Scene, Prop)):
                            local_item.image_request_id = None
                            local_item.motion_request_id = None
                        if local_item.status == GenerationStatus.PROCESSING:
                            local_item.status = "needs_confirmation"
                        series_pool.append(local_item)
                        local_pool.remove(local_item)
                    elif act["action"] == "merge_into_series" and act.get("target_series_id"):
                        # Rewire frame references then drop local
                        if frame_ref_attr:
                            for frame in script.frames:
                                if frame_ref_attr == "scene_id":
                                    if frame.scene_id == local_item.id:
                                        frame.scene_id = act.get("target_series_id")
                                elif frame_ref_attr == "character_ids":
                                    frame.character_ids = [
                                        act.get("target_series_id") if cid == local_item.id else cid
                                        for cid in frame.character_ids
                                    ]
                                elif frame_ref_attr == "prop_ids":
                                    frame.prop_ids = [
                                        act.get("target_series_id") if pid == local_item.id else pid
                                        for pid in frame.prop_ids
                                    ]
                        local_pool.remove(local_item)
                    # skip: do nothing
                setattr(script, local_pool_attr, local_pool)
                setattr(series, series_pool_attr, series_pool)

            apply_list("characters", "characters", actions.get("characters", []), "character_ids")
            apply_list("scenes", "scenes", actions.get("scenes", []), "scene_id")
            apply_list("props", "props", actions.get("props", []), "prop_ids")

            script.updated_at = time.time()
            series.updated_at = time.time()
            self._commit_storage("projects.json", "series.json")
            return script

    def _check_project_result(self, snapshot: Script) -> Script:
        """Re-find the live target under the command lock before applying work."""
        self.assert_storage_available()
        current = self.scripts.get(snapshot.id)
        if current is None:
            raise ProjectContextError("Project not found", not_found=True)
        if current.revision != snapshot.revision:
            raise StorageConflictError("Project changed while generating; reload before applying the result")
        return current

    def _parse_project_snapshot(self, snapshot: Script, text: str) -> Script:
        self.assert_storage_available()
        try:
            result = self.script_processor.parse_novel(snapshot.title, text, snapshot.prompt_config.entity_extraction)
            # Do not adopt an object owned by the processor or its caller.
            return Script.model_validate(deepcopy(result.model_dump()))
        except StorageError:
            raise
        except Exception as exc:
            raise ProjectGenerationError(f"LLM extraction failed: {exc}") from exc

    @staticmethod
    def _extraction_key(snapshot: Script, text: str) -> tuple:
        return snapshot.revision, text, snapshot.prompt_config.entity_extraction

    def extract_preview(self, script_id: str, text: str) -> Script:
        """Extract outside the lock; cache only for the unchanged saved input."""
        snapshot = self.read_committed_script(script_id)
        result = self._parse_project_snapshot(snapshot, text)
        with self._save_lock:
            self._check_project_result(snapshot)
            self._extraction_cache[script_id] = (
                time.time(), self._extraction_key(snapshot, text), result.model_copy(deep=True),
            )
            return result

    def _assert_no_active_motion_operation(self, script_id: str) -> None:
        script = self.scripts.get(script_id)
        if script is not None and any(
            task.motion_reference is not None and (script_id, task.id) in self._video_task_runs
            for task in script.video_tasks
        ):
            raise StorageConflictError("Motion reference operation is active; retry extraction after it settles")

    def reparse_project(self, script_id: str, text: str, *, expected_revision: int) -> Script:
        """Re-parse the text for an existing project, replacing all entities."""
        with self._save_lock:
            snapshot = self.read_committed_script(script_id)
            if snapshot.revision != expected_revision:
                raise StorageConflictError("Project changed; reload before applying the extraction")
            self._assert_no_active_motion_operation(script_id)
            cached = self._extraction_cache.pop(script_id, None)
            new_script = (
                cached[2].model_copy(deep=True)
                if cached and 0 <= time.time() - cached[0] < 300
                and cached[1] == self._extraction_key(snapshot, text) else None
            )
        if new_script is None:
            new_script = self._parse_project_snapshot(snapshot, text)
        with self._save_lock:
            self._check_project_result(snapshot)
            self._assert_no_active_motion_operation(script_id)
            # Preserve replacement of entities, frames, ordinary frame tasks
            # and context caches; project settings and paid motion history survive.
            # Series custom voices are not stored on Script and stay untouched.
            # Reparse retains this owner's image history, never parser authority.
            for field in (
                "id", "created_at", "art_direction", "model_settings", "style_preset", "style_prompt",
                "merged_video_url", "merged_video_source", "workflow_mode", "series_id", "episode_number", "prompt_config",
                "default_generation_mode", "bgm_url", "mix_settings",
                "asset_image_requests", "asset_image_request_scope_id",
            ):
                setattr(new_script, field, deepcopy(getattr(snapshot, field)))
            # Motion execution belongs to this project, not its replaced entities.
            # Keep original keys (including deleted-task tombstones), never
            # motion authority produced by the parser or a new same-ID character.
            motion_receipts = [
                receipt for receipt in snapshot.provider_request_receipts
                if isinstance(receipt, MotionReferenceReceipt)
            ]
            motion_ids = {receipt.task_id for receipt in motion_receipts}
            new_script.video_tasks = [task for task in new_script.video_tasks
                                      if task.motion_reference is None and task.id not in motion_ids] + [
                task.model_copy(deep=True) for task in snapshot.video_tasks
                if task.id in motion_ids and task.motion_reference is not None
            ]
            new_script.provider_request_receipts = [
                receipt for receipt in new_script.provider_request_receipts
                if not isinstance(receipt, MotionReferenceReceipt)
            ] + deepcopy(motion_receipts)
            for asset in [*new_script.characters, *new_script.scenes, *new_script.props]:
                if isinstance(asset, Character):
                    asset.reference_sheet_request_id = None
                    asset.full_body_request_id = None
                    asset.three_view_request_id = None
                    asset.headshot_request_id = None
                    asset.full_body_motion_request_id = None
                    asset.head_shot_motion_request_id = None
                elif isinstance(asset, (Scene, Prop)):
                    asset.image_request_id = None
                    asset.motion_request_id = None
                if asset.status == GenerationStatus.PROCESSING:
                    asset.status = "needs_confirmation"
            for frame in new_script.frames:
                frame.clear_image_request_for_copy()
            new_script.revision = snapshot.revision
            new_script.updated_at = time.time()
            self.scripts[script_id] = new_script
            self._save_data()
            return self.read_committed_script(script_id)

    def read_previous_episode_context(self, script_id: str, *, required: bool = False):
        """Capture current episode, series order and previous episode together."""
        with self._save_lock:
            script, series, _ = self.read_committed_project_view(script_id)
            if script is None:
                raise ProjectContextError("Project not found", not_found=True)
            if not script.series_id:
                if required:
                    raise ProjectContextError("Episode not in a series")
                return script, None, None
            if series is None:
                if required:
                    raise ProjectContextError("Series not found", not_found=True)
                return script, None, None
            try:
                index = series.episode_ids.index(script_id)
            except ValueError:
                if required:
                    raise ProjectContextError("Episode not registered in series order")
                return script, series, None
            if index <= 0:
                if required:
                    raise ProjectContextError("No previous episode")
                return script, series, None
            value = self._committed["projects.json"].get(series.episode_ids[index - 1])
            previous = Script.model_validate(deepcopy(value)) if value is not None else None
            if required and (previous is None or not previous.original_text.strip()):
                raise ProjectContextError("Previous episode has no script")
            return script, series, previous

    def _generate_project_context(self, prompt: str, label: str) -> str:
        from .llm_adapter import LLMAdapter

        self.assert_storage_available()
        try:
            result = LLMAdapter(**({"env": self.environment_snapshot()} if getattr(self, "runtime_environment", None) is not None else {})).chat(messages=[{"role": "user", "content": prompt}], model=None)
            if not isinstance(result, str) or not result.strip():
                raise ProjectGenerationError(f"Empty {label} from LLM")
            return result.strip()
        except StorageError:
            raise
        except ProjectGenerationError:
            raise
        except Exception as exc:
            raise ProjectGenerationError(f"LLM call failed: {exc}") from exc

    def generate_previous_episode_summary(self, script_id: str) -> Dict[str, Any]:
        snapshot, series, previous = self.read_previous_episode_context(script_id, required=True)
        prompt = (
            "请用 150-220 字概括下面这段剧本的核心情节、关键角色出场与结尾留下的悬念/钩子。"
            "不要列点，写成一段连贯的中文叙述，便于作者快速回顾上一集走到了哪里。\n\n"
            f"剧本：\n{previous.original_text}"
        )
        summary = self._generate_project_context(prompt, "summary")
        with self._save_lock:
            current = self._check_project_result(snapshot)
            current_series = self.series_store.get(series.id)
            current_previous = self.scripts.get(previous.id)
            if (current.series_id != series.id or current_series is None
                or current_series.episode_ids != series.episode_ids or current_previous is None
                or current_previous.revision != previous.revision):
                raise StorageConflictError("Previous episode context changed while generating; reload before applying")
            current.last_episode_summary_cache = summary
            current.last_episode_summary_revision = project_text_revision(previous.original_text)
            current.updated_at = time.time()
            self._save_data()
            return {
                "ai_summary": summary, "ai_summary_stale": False,
                "previous_episode_id": previous.id, "previous_episode_title": previous.title,
            }

    def generate_next_episode_hook(self, script_id: str) -> Dict[str, Any]:
        snapshot = self.read_committed_script(script_id)
        if not snapshot.original_text.strip():
            raise ProjectContextError("Episode has no script text yet")
        prompt = (
            "下面是一集剧本的结尾。请你站在编剧的视角，预测下一集开头可能的 hook，"
            "用 120-180 字给出 2-3 个具体方向（包括场景、角色出场、悬念点）。"
            "不要列点编号，写成连贯的中文段落，便于作者参考。\n\n"
            f"本集结尾：\n{snapshot.original_text[-1500:]}"
        )
        hook = self._generate_project_context(prompt, "hook")
        with self._save_lock:
            current = self._check_project_result(snapshot)
            current.next_hook_cache = hook
            current.next_hook_revision = project_text_revision(snapshot.original_text)
            current.updated_at = time.time()
            self._save_data()
            return {"hook": hook, "stale": False}

    def update_next_episode_hook(self, script_id: str, hook: Any) -> Dict[str, Any]:
        with self._save_lock:
            snapshot = self.read_committed_script(script_id)
            current = self._check_project_result(snapshot)
            current.next_hook_cache = None if hook is None else str(hook).strip()
            current.next_hook_revision = None if hook is None else project_text_revision(snapshot.original_text)
            current.updated_at = time.time()
            self._save_data()
            return {"hook": current.next_hook_cache, "stale": False}

    def update_last_episode_summary(self, script_id: str, summary: Any) -> Script:
        with self._save_lock:
            snapshot, _, previous = self.read_previous_episode_context(script_id)
            current = self._check_project_result(snapshot)
            current.last_episode_summary_cache = None if summary is None else str(summary).strip()
            if summary is None:
                current.last_episode_summary_revision = None
            elif previous is not None:
                current.last_episode_summary_revision = project_text_revision(previous.original_text)
            current.updated_at = time.time()
            self._save_data()
            return self.read_committed_script(script_id)


    def generate_assets(self, script_id: str) -> Script:
        """Freeze local batch membership/inputs and persist each asset in order."""
        with self._save_lock:
            view = self.read_committed_project_view(script_id)
            script = view[0]
            if script is None:
                raise ProjectContextError("Script not found", not_found=True)
            members = [
                (asset.id, kind) for kind, values in (
                    ("character", sorted(script.characters, key=lambda item: bool(item.base_character_id))),
                    ("scene", script.scenes), ("prop", script.props),
                ) for asset in values
            ]
            contexts = [self._prepare_asset_image(view, asset_id, kind) for asset_id, kind in members]
            expected_scope = script.asset_image_request_scope_id
        for context in contexts:
            if context["kind"] == "character" and context["params"]["generation_type"] == "all":
                with self._save_lock:
                    admission = self.asset_image_service.admit_character_all(
                        "project", script_id, view, context["asset_id"], context["params"],
                        prepared_context={**context, "request_root_scope_id": expected_scope},
                    )
                if admission is not None:
                    if expected_scope is None:
                        expected_scope = admission.request.request_root_scope_id
                    self.process_asset_generation_task(admission.request.id)
                    with self._save_lock:
                        self._require_completed_core_asset_image(admission)
                    continue
            if not self._is_core_asset_image(context["kind"], context["params"]["generation_type"],
                                             context["options"]["model_name"]):
                self._generate_asset_image(context)
                continue
            from .asset_image_execution import AssetImageRequestCommand
            command = AssetImageRequestCommand.model_validate({
                "client_request_id": str(uuid.uuid4()), "asset_id": context["asset_id"],
                "asset_type": context["kind"], **context["params"],
            })
            admission = self.asset_image_service._admit_prepared_context(
                command, {**context, "request_root_scope_id": expected_scope},
            )
            # Only the first receipt may initialize this batch's root scope;
            # all other originally frozen inputs and membership remain unchanged.
            if expected_scope is None:
                expected_scope = admission.request.request_root_scope_id
            self.asset_image_service.run_original_request(admission.request.id)
            with self._save_lock:
                self._require_completed_core_asset_image(admission)
        with self._save_lock:
            result, _, _ = self.read_committed_project_view(script_id)
            if result is None:
                raise ProjectContextError("Script not found", not_found=True)
            return result

    def generate_asset(self, script_id: str, asset_id: str, asset_type: str, style_preset: str = None, reference_image_url: str = None, style_prompt: str = None, generation_type: str = "all", prompt: str = None, apply_style: bool = True, negative_prompt: str = None, batch_size: int = 1, model_name: str = None, aspect_ratio: str = None) -> Script:
        """Generate against the saved asset owner without exposing private output."""
        params = dict(style_preset=style_preset, reference_image_url=reference_image_url,
                      style_prompt=style_prompt, generation_type=generation_type, prompt=prompt,
                      apply_style=apply_style, negative_prompt=negative_prompt, batch_size=batch_size,
                      model_name=model_name, aspect_ratio=aspect_ratio)
        with self._save_lock:
            view = self.read_committed_project_view(script_id)
            admission = self._admit_core_asset_image("project", script_id, view, asset_id, asset_type, params)
            context = self._prepare_asset_image(view, asset_id, asset_type, params) if admission is None else None
        if context is not None:
            return self._generate_asset_image(context)
        assert admission is not None
        self.process_asset_generation_task(admission.request.id)
        if admission.request.channel != "reference_sheet":
            with self._save_lock:
                self._require_completed_core_asset_image(admission)
                return self.read_committed_script(script_id)
        return self.read_committed_script(script_id)

    def _require_completed_core_asset_image(self, admission):
        """Do not report a new synchronous batch as successful while it is uncertain."""
        service = self.asset_image_service
        record = admission.request
        current = service.read_request(record.request_root_kind, record.request_root_id, record.client_request_id)
        expected_members = [(m.variant_id, m.execution.fingerprint) for m in record.members]
        current_members = [(m.variant_id, m.execution.fingerprint) for m in current.request.members]
        if (current.request.id != record.id or current.request.request_fingerprint != record.request_fingerprint
                or current_members != expected_members):
            raise StorageConflictError("Studio image request identity changed")
        if current.reason is not None:
            raise StorageConflictError(current.reason)
        if current.request.character_all is not None and not current.request.character_all.completed:
            raise ProjectGenerationError("Character all generation is not confirmed; inspect the original request")
        members = service.request_state(current.request)["members"]
        if not isinstance(members, list) or not members or not all(member["status"] == "completed" for member in members):
            raise ProjectGenerationError("Asset image generation is not confirmed; inspect the original request")

    @staticmethod
    def _is_core_asset_image(asset_type, generation_type, model):
        from .asset_image_models import STUDIO_GPT_IMAGE_MODELS
        from ...models.qwen20_image_contract import QWEN20_IMAGE_PROVIDER_MODELS
        from ...models.wan27_image_contract import WAN27_IMAGE_PROVIDER_MODELS
        migrated = ((asset_type == "character" and generation_type in {"reference_sheet", "full_body"})
                    or (asset_type in {"scene", "prop"} and generation_type == "all"))
        names = WAN27_IMAGE_PROVIDER_MODELS | {f"wan/{name}#image" for name in WAN27_IMAGE_PROVIDER_MODELS}
        qwen_names = QWEN20_IMAGE_PROVIDER_MODELS | {f"qwen/{name}#image" for name in QWEN20_IMAGE_PROVIDER_MODELS}
        return isinstance(model, str) and (
            (migrated and model in names | qwen_names | STUDIO_GPT_IMAGE_MODELS)
            or (asset_type == "character" and generation_type in {"three_view", "headshot"} and model in STUDIO_GPT_IMAGE_MODELS)
            or (asset_type == "character" and generation_type == "three_view" and model in names | qwen_names)
        )

    def _admit_core_asset_image(self, root_kind, root_id, view, asset_id, asset_type, params) -> "Optional[AssetImageAdmission]":
        """Route only the migrated channel; Core errors must never fall back."""
        from .asset_image_execution import AssetImageRequestCommand
        root = view[0]
        if root is None:
            return None
        if asset_type == "character" and params.get("generation_type") == "all":
            return self.asset_image_service.admit_character_all(root_kind, root_id, view, asset_id, params)
        full_body_edit = False
        if asset_type == "character" and params.get("generation_type") == "three_view":
            from .asset_image_models import STUDIO_GPT_IMAGE_MODELS, STUDIO_WAN_IMAGE_MODELS, STUDIO_QWEN_IMAGE_MODELS
            from .assets import selected_three_view_reference, derived_image_fallback_reference
            from ...utils.media_refs import classify_media_ref, MEDIA_REF_LOCAL_PATH, MEDIA_REF_REMOTE_URL, MEDIA_REF_OBJECT_KEY
            qwen_edit = root.model_settings.i2i_model in STUDIO_QWEN_IMAGE_MODELS
            dashscope_edit = root.model_settings.i2i_model in STUDIO_WAN_IMAGE_MODELS | STUDIO_QWEN_IMAGE_MODELS
            if (root.model_settings.i2i_model not in STUDIO_GPT_IMAGE_MODELS | STUDIO_WAN_IMAGE_MODELS | STUDIO_QWEN_IMAGE_MODELS
                    or (dashscope_edit and root_kind != "project")):
                return None
            target, _ = self._saved_asset_image_target(view, asset_id, asset_type, root_kind=root_kind)
            if dashscope_edit and (not isinstance(target, Character) or target.base_character_id):
                return None
            try:
                reference = selected_three_view_reference(target, reject_ambiguous=True) if isinstance(target, Character) else None
            except ValueError as exc:
                # An ambiguous saved choice is a rejected command, not a
                # reason to fall back to the paid legacy generator.
                raise CommandValidationError(str(exc)) from exc
            if reference is None:
                if isinstance(target, Character) and target.full_body_asset is not None and target.full_body_asset.selected_id:
                    raise CommandValidationError("studio_three_view_selected_reference_missing")
                if dashscope_edit or not isinstance(target, Character):
                    return None
                try:
                    fallback = derived_image_fallback_reference(target, "three_view", reject_ambiguous=True)
                except ValueError as exc:
                    raise CommandValidationError(str(exc)) from exc
                if fallback is None:
                    return None
                reference_source, reference_id, reference_url = fallback
                reference_kind = classify_media_ref(reference_url, output_root=str(self.data_dir))
                if reference_kind in {MEDIA_REF_REMOTE_URL, MEDIA_REF_OBJECT_KEY}:
                    return None
                if reference_kind != MEDIA_REF_LOCAL_PATH:
                    raise CommandValidationError("studio_three_view_fallback_reference_invalid")
                params = dict(params, reference_source=reference_source)
            else:
                reference_id, reference_url = reference.id, reference.url
                if classify_media_ref(reference_url, output_root=str(self.data_dir)) != MEDIA_REF_LOCAL_PATH:
                    return None
            allowed_models = STUDIO_GPT_IMAGE_MODELS
            if dashscope_edit:
                family = "qwen" if qwen_edit else "wan"
                provider_model = root.model_settings.i2i_model.removeprefix(f"{family}/").removesuffix("#image")
                allowed_models = frozenset({provider_model, f"{family}/{provider_model}#image"})
            if params.get("model_name") is not None and params["model_name"] not in allowed_models:
                raise CommandValidationError("Three-view model must match the saved I2I model")
            if params.get("reference_image_url") is not None and params["reference_image_url"] != reference_url:
                raise CommandValidationError("Three-view reference must match the selected full-body image")
            # Old function defaults are not authored strict-field presence.
            params = {key: value for key, value in params.items()
                      if key not in {"model_name", "aspect_ratio"} or value is not None}
            params.update(reference_image_url=reference_url)
            if reference_id is not None:
                params["reference_variant_id"] = reference_id
            model = root.model_settings.i2i_model
        elif asset_type == "character" and params.get("generation_type") == "headshot":
            from .asset_image_models import STUDIO_GPT_IMAGE_MODELS
            from .assets import selected_three_view_reference, derived_image_fallback_reference
            from ...utils.media_refs import classify_media_ref, MEDIA_REF_LOCAL_PATH, MEDIA_REF_REMOTE_URL, MEDIA_REF_OBJECT_KEY
            if root.model_settings.i2i_model not in STUDIO_GPT_IMAGE_MODELS:
                return None
            target, _ = self._saved_asset_image_target(view, asset_id, asset_type, root_kind=root_kind)
            if not isinstance(target, Character):
                return None
            try:
                reference = selected_three_view_reference(target, reject_ambiguous=True)
            except ValueError as exc:
                raise CommandValidationError("studio_headshot_selected_reference_ambiguous") from exc
            # A saved but missing choice must not silently choose the legacy URL.
            if reference is None:
                if isinstance(target, Character) and target.full_body_asset is not None and target.full_body_asset.selected_id:
                    raise CommandValidationError("studio_headshot_selected_reference_missing")
                if not isinstance(target, Character):
                    return None
                try:
                    fallback = derived_image_fallback_reference(target, "headshot", reject_ambiguous=True)
                except ValueError as exc:
                    raise CommandValidationError(str(exc)) from exc
                if fallback is None:
                    return None
                reference_source, reference_id, reference_url = fallback
                reference_kind = classify_media_ref(reference_url, output_root=str(self.data_dir))
                if reference_kind in {MEDIA_REF_REMOTE_URL, MEDIA_REF_OBJECT_KEY}:
                    return None
                if reference_kind != MEDIA_REF_LOCAL_PATH:
                    raise CommandValidationError("studio_headshot_fallback_reference_invalid")
                params = dict(params, reference_source=reference_source)
            else:
                reference_id, reference_url = reference.id, reference.url
                if classify_media_ref(reference_url, output_root=str(self.data_dir)) != MEDIA_REF_LOCAL_PATH:
                    return None
            if params.get("model_name") is not None and params["model_name"] not in STUDIO_GPT_IMAGE_MODELS:
                raise CommandValidationError("Headshot model must match the saved I2I model")
            if params.get("reference_image_url") is not None and params["reference_image_url"] != reference_url:
                raise CommandValidationError("Headshot reference must match the selected full-body image")
            # Keep authored unsupported fields so Core can reject without fallback.
            params = {key: value for key, value in params.items()
                      if key not in {"model_name", "aspect_ratio"} or value is not None}
            params.update(reference_image_url=reference_url)
            if reference_id is not None:
                params["reference_variant_id"] = reference_id
            model = root.model_settings.i2i_model
        elif asset_type == "character" and params.get("generation_type") == "full_body":
            from .asset_image_models import STUDIO_GPT_IMAGE_MODELS
            from .assets import uploaded_full_body_reference, selected_three_view_reference
            from ...utils.media_refs import classify_media_ref, MEDIA_REF_LOCAL_PATH, MEDIA_REF_REMOTE_URL, MEDIA_REF_OBJECT_KEY
            target, target_owner = self._saved_asset_image_target(view, asset_id, asset_type, root_kind=root_kind)
            reference = None
            if root.model_settings.i2i_model in STUDIO_GPT_IMAGE_MODELS and isinstance(target, Character):
                try:
                    if target.base_character_id:
                        if root_kind == "project" and target_owner == ("script", root.id):
                            parent, parent_owner = self._saved_asset_image_target(view, target.base_character_id, "character")
                            if isinstance(parent, Character) and parent.id != target.id and parent_owner == target_owner:
                                reference = selected_three_view_reference(parent, reject_ambiguous=True)
                    else:
                        source = uploaded_full_body_reference(target, reject_ambiguous=True)
                        reference = source[1] if source else None
                except ValueError as exc:
                    raise CommandValidationError(str(exc)) from exc
            if reference is not None:
                if not reference.id or not reference.url:
                    raise CommandValidationError("studio_full_body_uploaded_reference_missing")
                reference_kind = classify_media_ref(reference.url, output_root=str(self.data_dir))
                if reference_kind in {MEDIA_REF_REMOTE_URL, MEDIA_REF_OBJECT_KEY}:
                    return None
                if reference_kind != MEDIA_REF_LOCAL_PATH:
                    raise CommandValidationError("studio_full_body_uploaded_reference_invalid")
                # The old UI supplied its T2I default even when the generator
                # actually used the project's saved I2I model for reverse input.
                if (params.get("model_name") is not None
                        and params["model_name"] not in STUDIO_GPT_IMAGE_MODELS
                        and params["model_name"] != root.model_settings.t2i_model):
                    raise CommandValidationError("Full-body reference model must match the saved I2I model")
                if params.get("reference_image_url") is not None and params["reference_image_url"] != reference.url:
                    raise CommandValidationError("Full-body reference must match the first uploaded source")
                params = dict(params, model_name=root.model_settings.i2i_model,
                              reference_variant_id=reference.id, reference_image_url=reference.url)
                model = root.model_settings.i2i_model
                full_body_edit = True
            else:
                model = params.get("model_name") or root.model_settings.t2i_model
        else:
            model = params.get("model_name") or root.model_settings.t2i_model
        if not full_body_edit and not self._is_core_asset_image(asset_type, params.get("generation_type"), model):
            return None
        if asset_type == "character" and params.get("generation_type") == "full_body" and not full_body_edit:
            from .assets import has_full_body_reference_input
            target, _ = self._saved_asset_image_target(view, asset_id, asset_type, root_kind=root_kind)
            if target is not None and has_full_body_reference_input(target):
                return None
        # Legacy callers have no client key. This authorizes one invocation;
        # only the new request endpoint can replay an HTTP request by key.
        command = AssetImageRequestCommand.model_validate({
            "client_request_id": str(uuid.uuid4()), "asset_id": asset_id,
            "asset_type": asset_type, **params,
        })
        return self.asset_image_service.admit(root_kind, root_id, command)

    @staticmethod
    def _asset_image_options(view, asset_type, params, *, root_kind="project"):
        """Preserve current model/style precedence; explicit reference URL is not wired yet."""
        from .assets import ASPECT_RATIO_TO_SIZE

        root, series, _ = view
        aspect = params["aspect_ratio"] or getattr(root.model_settings, f"{asset_type}_aspect_ratio")
        default_size = {"character": "576*1024", "scene": "1024*576", "prop": "1024*1024"}[asset_type]
        positive, negative = "", params["negative_prompt"] or ""
        art = root.art_direction or (series.art_direction if series else None)
        if params["apply_style"]:
            if art and art.style_config:
                positive = art.style_config.get("positive_prompt", "")
                global_neg = art.style_config.get("negative_prompt", "")
                if any(value is not None and not isinstance(value, str) for value in (positive, global_neg)):
                    raise ValueError("Effective style prompts must be text")
                if global_neg:
                    negative = f"{negative}, {global_neg}" if negative else global_neg
            elif params["style_prompt"]:
                positive = params["style_prompt"]
            elif params["style_preset"]:
                positive = f"{params['style_preset']} style"
            elif root_kind == "project" and root.style_preset:
                positive = f"{root.style_preset} style"
                if root.style_prompt:
                    positive += f", {root.style_prompt}"
        result = dict(prompt=params["prompt"], positive_prompt=positive, negative_prompt=negative, batch_size=params["batch_size"],
                      model_name=params["model_name"] or root.model_settings.t2i_model,
                      size=ASPECT_RATIO_TO_SIZE.get(aspect, default_size))
        if asset_type == "character":
            # Standalone Series did not wire its configured i2i model. Keep
            # that paid-route decision separate from this ownership change.
            result.update(generation_type=params["generation_type"],
                          prompt=(params["prompt"] or "") if root_kind == "series" else params["prompt"],
                          i2i_model_name=root.model_settings.i2i_model if root_kind == "project" else None)
        if any(value is not None and not isinstance(value, str) for key, value in result.items() if key != "batch_size"):
            raise ValueError("Effective image prompts, model and size must be text")
        return result

    def _read_asset_image_view(self, root_kind, root_id):
        if root_kind == "series":
            root, _ = self.read_committed_series_view(root_id)
            return root, None, None
        return self.read_committed_project_view(root_id)

    def _live_asset_image_target(self, root_kind, root_id, asset_id, kind):
        """Keep the request root distinct from the asset's actual owner."""
        if kind == "storyboard_frame":
            root = self.scripts.get(root_id) if root_kind == "project" else None
            target = next((frame for frame in root.frames if frame.id == asset_id), None) if root else None
            return root, target, root, ("script", root_id) if target is not None else None
        if root_kind == "series":
            root = self.series_store.get(root_id)
            field = {"character": "characters", "scene": "scenes", "prop": "props"}[kind]
            target = next((value for value in getattr(root, field) if value.id == asset_id), None) if root else None
            return root, target, root, ("series", root_id) if target is not None else None
        root = self.scripts.get(root_id)
        target, source = self._find_asset_with_source(root, asset_id, kind) if root else (None, None)
        owner_object = root if source == "script" else (self.series_store.get(root.series_id) if source == "series" else self.library_store)
        return root, target, owner_object, self._asset_owner(root, source) if target is not None else None

    def _saved_asset_image_target(self, view, asset_id, kind, *, root_kind="project"):
        root, series, _ = view
        if root is None:
            return None, None
        if kind == "storyboard_frame":
            target = next((frame for frame in root.frames if frame.id == asset_id), None) if root_kind == "project" else None
            return target, ("script", root.id) if target is not None else None
        field = {"character": "characters", "scene": "scenes", "prop": "props"}[kind]
        if root_kind == "series":
            target = next((item for item in getattr(root, field) if item.id == asset_id), None)
            return target, ("series", root.id) if target is not None else None
        target = next((item for item in self._resolve_asset_layers(*view)[field] if item.id == asset_id), None)
        source = "script" if any(item.id == asset_id for item in getattr(root, field)) else (
            "series" if series and any(item.id == asset_id for item in getattr(series, field)) else "global"
        )
        return target, self._asset_owner(root, source) if target is not None else None

    def _prepare_asset_image(self, view, asset_id, kind, params=None, *, root_kind="project"):
        root = view[0]
        if root is None:
            raise ProjectContextError("Asset request root not found", not_found=True)
        params = dict(style_preset=None, reference_image_url=None, style_prompt=None,
                      generation_type="all", prompt=None, apply_style=True, negative_prompt=None,
                      batch_size=1, model_name=None, aspect_ratio=None) | deepcopy(params or {})
        try:
            if kind not in ("character", "scene", "prop"):
                raise ValueError("Invalid asset type")
            _validate_safe_id(asset_id, "asset id")
            if type(params["batch_size"]) is not int or params["batch_size"] < 1:
                raise ValueError("batch_size must be a positive integer")
            if type(params["apply_style"]) is not bool:
                raise ValueError("apply_style must be boolean")
            for key, value in params.items():
                if key not in ("batch_size", "apply_style") and value is not None and not isinstance(value, str):
                    raise ValueError("Asset image parameters must be text")
            if kind == "character" and params["generation_type"] not in ("all", "reference_sheet", "full_body", "three_view", "headshot"):
                raise ValueError("Invalid character generation_type")
            serialize_json(params)
            options = self._asset_image_options(view, kind, params, root_kind=root_kind)
            serialize_json(options)
        except (StorageError, ValueError, TypeError) as exc:
            raise CommandValidationError("Invalid asset image inputs") from exc
        target, owner = self._saved_asset_image_target(view, asset_id, kind, root_kind=root_kind)
        if target is None:
            raise ProjectContextError("Asset not found", not_found=True)
        _validate_setting_value(target)
        live_root, live_target, owner_object, _ = self._live_asset_image_target(root_kind, root.id, asset_id, kind)
        return dict(root_kind=root_kind, root_id=root.id, asset_id=asset_id, kind=kind, owner=owner,
                    key=(*owner, kind, asset_id), root_identity=live_root, owner_identity=owner_object,
                    target_identity=live_target, target=target, expected=target.model_dump(),
                    series_id=root.series_id if root_kind == "project" else None, params=params, options=options)

    def _check_asset_image(self, context, token=None, *, finishing_failure=False):
        with self._save_lock:
            self.assert_storage_available()
            root, target, owner_object, live_owner = self._live_asset_image_target(
                context["root_kind"], context["root_id"], context["asset_id"], context["kind"])
            view = self._read_asset_image_view(context["root_kind"], context["root_id"])
            saved, owner = self._saved_asset_image_target(view, context["asset_id"], context["kind"], root_kind=context["root_kind"])
            current_options = None
            if not finishing_failure and view[0] is not None:
                try:
                    current_options = self._asset_image_options(view, context["kind"], context["params"], root_kind=context["root_kind"])
                except (ValueError, TypeError):
                    # Invalid newly edited settings also invalidate this
                    # accepted call; do not turn them into a retryable error.
                    pass
            if (
                root is not context["root_identity"] or target is not context["target_identity"]
                or owner_object is not context["owner_identity"] or target is None or saved is None
                or owner != context["owner"] or live_owner != context["owner"]
                or (token is not None and self._asset_image_runs.get(context["key"]) is not token)
                or (finishing_failure and (
                    target.status != GenerationStatus.PROCESSING or saved.status != GenerationStatus.PROCESSING))
                or (not finishing_failure and (
                    target.model_dump() != context["expected"] or saved.model_dump() != context["expected"]
                    or (context["root_kind"] == "project" and root.series_id != context["series_id"])
                    or current_options != context["options"]))
            ):
                raise StorageConflictError("Asset image target or inputs changed; reload before retrying")
            return target

    def _finish_asset_image_failure(self, context, token):
        with self._save_lock:
            if self._closed or self._storage_failed is not None:
                return
            try:
                target = self._check_asset_image(context, token, finishing_failure=True)
            except StorageConflictError:
                return
            # The owned image run ends even if the user edited its description
            # or selection. Preserve those fields; never touch a new run/owner.
            target.status = GenerationStatus.FAILED
            self._save_after_asset_mutation(context["owner"][0])

    def _reconcile_asset_image_result(self, original, candidate, kind, generation_type, *, failed):
        """Synchronize image representations while preserving independent video fields."""
        if kind == "character":
            for channel, _ in self._variant_channels(candidate, kind, generation_type):
                unit_field, legacy_field, _ = self._CHARACTER_IMAGE_CHANNELS[channel]
                legacy = getattr(candidate, legacy_field) if legacy_field else None
                unit = getattr(candidate, unit_field) if unit_field else None
                if legacy is not None and unit is not None:
                    by_id = {value.id: value for value in legacy.variants}
                    existing_ids = {value.id for value in unit.image_variants}
                    unit.image_variants = [by_id.get(value.id, value).model_copy(deep=True) for value in unit.image_variants]
                    unit.image_variants.extend(value.model_copy(deep=True) for value in legacy.variants if value.id not in existing_ids)
                    self._set_selected_image(unit, legacy.selected_id)
                self._sync_variant_urls(candidate, kind, channel)
        if failed:
            before = dict(self._variant_channels(original, kind))
            for channel, containers in self._variant_channels(candidate, kind):
                for old, current in zip(before[channel], containers):
                    if current is not None:
                        selected = self._selected_image(old)
                        # Legacy candidate cleanup may have pruned the user's
                        # old selection. Restore its metadata, not a dangling ID.
                        old_variant = next((value for value in self._image_variants(old) if value.id == selected), None)
                        if old_variant is not None and not any(value.id == selected for value in self._image_variants(current)):
                            self._image_variants(current).append(old_variant.model_copy(deep=True))
                        self._set_selected_image(current, selected)
            for key in ("image_url", "avatar_url", "full_body_image_url", "three_view_image_url", "headshot_image_url"):
                if hasattr(original, key):
                    setattr(candidate, key, getattr(original, key))

    def _begin_asset_image(self, context):
        """Reserve the real owner and save PROCESSING before it can be dispatched."""
        token = object()
        with self._save_lock:
            self._check_asset_image(context)
            if not hasattr(self, "_asset_image_runs"):
                self._asset_image_runs = {}
            if context["key"] in self._asset_image_runs:
                raise StorageConflictError("This asset is already generating an image")
            self._asset_image_runs[context["key"]] = token
            try:
                target = self._check_asset_image(context, token)
                target.status = GenerationStatus.PROCESSING
                self._save_after_asset_mutation(context["owner"][0])
                context["expected"] = target.model_dump()
            except BaseException:
                if self._asset_image_runs.get(context["key"]) is token:
                    del self._asset_image_runs[context["key"]]
                raise
        return token

    def _generate_asset_image(self, context, *, token=None):
        token = self._begin_asset_image(context) if token is None else token
        try:
            self._check_asset_image(context, token)
            candidate = context["target"].model_copy(deep=True)
            candidate.status = GenerationStatus.PROCESSING
            failure = None
            try:
                getattr(self.asset_generator, f"generate_{context['kind']}")(
                    candidate, **context["options"],
                    check_current=lambda: self._check_asset_image(context, token),
                )
            except StorageError:
                raise
            except AssetGenerationOutputError as exc:
                failure = ProjectGenerationError("Asset image generation returned invalid data")
                failure.__cause__ = exc
                candidate.status = GenerationStatus.FAILED
            except Exception as exc:
                failure = exc
                candidate.status = GenerationStatus.FAILED
            try:
                candidate = type(candidate).model_validate(candidate.model_dump())
                serialize_json(candidate.model_dump())
                if candidate.status not in (GenerationStatus.COMPLETED, GenerationStatus.FAILED):
                    raise ValueError("Asset generator did not return a terminal status")
                if candidate.status == GenerationStatus.FAILED and failure is None:
                    failure = ProjectGenerationError("Asset image generation failed")
                self._reconcile_asset_image_result(context["target"], candidate, context["kind"],
                                                  context["params"]["generation_type"], failed=failure is not None)
                serialize_json(candidate.model_dump())
            except (StorageError, ValueError, TypeError) as exc:
                self._finish_asset_image_failure(context, token)
                raise ProjectGenerationError("Asset image generation returned invalid data") from exc
            with self._save_lock:
                target = self._check_asset_image(context, token)
                # An image result cannot edit descriptions, voices or motion
                # references, even if a future generator mutates its snapshot.
                fields = ["status", "image_url"]
                if context["kind"] == "character":
                    fields.extend(("avatar_url", "is_consistent"))
                    for channel, (unit_field, legacy_field, url_field) in self._CHARACTER_IMAGE_CHANNELS.items():
                        unit = getattr(candidate, unit_field)
                        old_unit = getattr(context["target"], unit_field)
                        if unit is not None:
                            current = getattr(target, unit_field) or AssetUnit()
                            for key in ("selected_image_id", "image_variants", "image_prompt", "image_updated_at"):
                                if old_unit is None or getattr(unit, key) != getattr(old_unit, key):
                                    setattr(current, key, deepcopy(getattr(unit, key)))
                            setattr(target, unit_field, current)
                        if legacy_field:
                            fields.extend((legacy_field, url_field, f"{channel}_prompt", f"{channel}_updated_at"))
                else:
                    fields.append("image_asset")
                original = context["target"].model_dump()
                result_data = candidate.model_dump()
                for key in fields:
                    if key == "status" or result_data[key] != original[key]:
                        setattr(target, key, deepcopy(getattr(candidate, key)))
                self._save_after_asset_mutation(context["owner"][0])
                result = self._read_asset_image_view(context["root_kind"], context["root_id"])[0]
            if failure is not None:
                raise failure
            return result
        except StorageConflictError:
            self._finish_asset_image_failure(context, token)
            raise
        finally:
            with self._save_lock:
                if self._asset_image_runs.get(context["key"]) is token:
                    del self._asset_image_runs[context["key"]]

    def create_asset_generation_task(self, script_id: str, asset_id: str, asset_type: str,
                                      style_preset: str = None, reference_image_url: str = None,
                                      style_prompt: str = None, generation_type: str = "all",
                                      prompt: str = None, apply_style: bool = True,
                                      negative_prompt: str = None, batch_size: int = 1,
                                      model_name: str = None, aspect_ratio: str = None) -> Tuple[Script, str]:
        """Accept inputs and save the actual owner before exposing an in-memory task."""
        params = dict(style_preset=style_preset, reference_image_url=reference_image_url,
                      style_prompt=style_prompt, generation_type=generation_type, prompt=prompt,
                      apply_style=apply_style, negative_prompt=negative_prompt, batch_size=batch_size,
                      model_name=model_name, aspect_ratio=aspect_ratio)
        return self._enqueue_asset_image("project", script_id, asset_id, asset_type, params)

    def _enqueue_asset_image(self, root_kind, root_id, asset_id, asset_type, params):
        with self._save_lock:
            view = self._read_asset_image_view(root_kind, root_id)
            admission = self._admit_core_asset_image(root_kind, root_id, view, asset_id, asset_type, params)
            if admission is not None:
                return self._read_asset_image_view(root_kind, root_id)[0], admission.request.id
            context = self._prepare_asset_image(view, asset_id, asset_type, params, root_kind=root_kind)
            task_id = str(uuid.uuid4())
            token = self._begin_asset_image(context)
            self.asset_generation_tasks[task_id] = {
                "status": "pending", "progress": 0, "error": None,
                "script_id": root_id, "asset_id": asset_id, "asset_type": asset_type,
                "created_at": time.time(), "is_series": root_kind == "series",
                "params": deepcopy(context["params"]),
                # Process-local execution ownership, never a persistent receipt.
                "_context": context, "_token": token,
            }
            return self._read_asset_image_view(root_kind, root_id)[0], task_id

    def process_asset_generation_task(self, task_id: str):
        """Claim pending exactly once; use only the context accepted at enqueue."""
        with self._save_lock:
            candidate = self.asset_generation_tasks.get(task_id)
            core_request_id = candidate.get("_core_image_request_id") if candidate else None
        if core_request_id is not None:
            # The Core worker owns the atomic claim. Never hold this outer
            # lock over credentials or provider I/O, or mark processing here.
            if candidate and candidate.get("_character_all_context") is not None:
                self.asset_image_service.run_character_all_original(core_request_id)
            else:
                self.asset_image_service.run_original_request(core_request_id)
            return
        with self._save_lock:
            task = self.asset_generation_tasks.get(task_id)
            if not task:
                logger.error(f"Task {task_id} not found")
                return
            if task["status"] != "pending":
                return
            self.assert_storage_available()
            task["status"] = "processing"
        try:
            self._generate_asset_image(task["_context"], token=task["_token"])
        except StorageError as exc:
            with self._save_lock:
                if self.asset_generation_tasks.get(task_id) is task:
                    task.update(status="failed", error=str(exc))
            raise
        except Exception as e:
            with self._save_lock:
                if self.asset_generation_tasks.get(task_id) is task:
                    task.update(status="failed", error=str(e))
            logger.error(f"Task {task_id} failed: {e}")
        else:
            with self._save_lock:
                if self.asset_generation_tasks.get(task_id) is task and task["status"] == "processing":
                    task.update(status="completed", progress=100)
            logger.info(f"Task {task_id} completed successfully")

    def get_asset_generation_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Use retained Core receipts; preserve closed-runtime legacy status reads."""
        with self._save_lock:
            motion = self.motion_reference_service.read_task_status(task_id)
            if motion is not None:
                return motion
            task = self.asset_generation_tasks.get(task_id) or self.video_generation_tasks.get(task_id)
            if task is None or task.get("_core_image_request_id") is not None:
                return self.asset_image_service.read_task_status(task_id)
            return deepcopy({
                "task_id": task_id, "status": task["status"], "progress": task.get("progress", 0),
                "error": task.get("error"), "asset_id": task.get("asset_id"),
                "asset_type": task.get("asset_type"), "script_id": task.get("script_id"),
                "created_at": task.get("created_at"),
            })

    @staticmethod
    def _motion_kind(asset_type: str) -> str:
        return "character" if asset_type in ("full_body", "head_shot") else asset_type

    @staticmethod
    def _motion_default_prompt(asset, asset_type: str, audio_url: Optional[str]) -> str:
        if asset_type in ("full_body", "head_shot"):
            label = asset_type.replace("_", " ").title()
            if audio_url:
                return (
                    f"{label} character reference video. {asset.description}. The character is speaking naturally "
                    "matching the audio, with accurate lip-sync and facial expressions. Stable camera, high quality, 4k."
                )
            return (
                f"{label} character reference video. {asset.description}. Looking around, breathing, "
                "slight movement, subtle gestures. Stable camera, high quality, 4k."
            )
        if asset_type == "scene":
            if audio_url:
                return (
                    f"Cinematic scene video reference of {asset.name}. {asset.description}. Ambient motion, "
                    "lighting changes, natural elements moving, birds, clouds. Soundscape matching the audio. "
                    "High quality, 4k."
                )
            return (
                f"Cinematic scene video reference of {asset.name}. {asset.description}. Ambient motion, "
                "lighting changes, natural elements moving, birds, clouds. Slow pan across the scene. "
                "High quality, 4k."
            )
        if audio_url:
            return (
                f"Cinematic prop video reference of {asset.name}. {asset.description}. Rotating object, "
                "detailed textures visible, ambient motion, subtle movements matching audio. High quality, 4k."
            )
        return (
            f"Cinematic prop video reference of {asset.name}. {asset.description}. Rotating object, "
            "detailed textures visible, ambient motion, subtle movements. High quality, 4k."
        )

    @staticmethod
    def _motion_source_image(asset, asset_type: str) -> Optional[str]:
        if asset_type in ("full_body", "head_shot"):
            unit = getattr(asset, asset_type, None)
            if unit and unit.selected_image_id:
                selected = next((value for value in unit.image_variants if value.id == unit.selected_image_id), None)
                if selected:
                    return selected.url
            return asset.full_body_image_url if asset_type == "full_body" else asset.headshot_image_url
        return asset.image_url

    def _saved_motion_target(self, view, asset_id: str, asset_type: str):
        script, series, _ = view
        if script is None:
            return None, None, None
        kind = self._motion_kind(asset_type)
        if kind not in ("character", "scene", "prop"):
            return None, None, None
        field = {"character": "characters", "scene": "scenes", "prop": "props"}[kind]
        target = next((item for item in self._resolve_asset_layers(*view)[field] if item.id == asset_id), None)
        source = "script" if any(item.id == asset_id for item in getattr(script, field)) else (
            "series" if series and any(item.id == asset_id for item in getattr(series, field)) else "global"
        )
        return target, source, self._asset_owner(script, source) if target is not None else None

    def _live_motion_target(self, script_id: str, asset_id: str, asset_type: str):
        script = self.scripts.get(script_id)
        kind = self._motion_kind(asset_type)
        target, source = self._find_asset_with_source(script, asset_id, kind) if script else (None, None)
        owner_object = script if source == "script" else (
            self.series_store.get(script.series_id) if script and source == "series" else self.library_store
        )
        return script, target, source, owner_object, self._asset_owner(script, source) if target is not None else None

    def _prepare_motion_ref(self, view, asset_id: str, asset_type: str, params: Dict[str, Any]):
        script = view[0]
        if script is None:
            raise ProjectContextError("Project not found", not_found=True)
        params = dict(prompt=None, audio_url=None, duration=5, batch_size=1) | deepcopy(params or {})
        try:
            _validate_safe_id(asset_id, "asset id")
            if asset_type not in ("full_body", "head_shot", "scene", "prop"):
                raise ValueError("Invalid motion reference asset_type")
            if type(params["duration"]) is not int or params["duration"] < 1:
                raise ValueError("duration must be a positive integer")
            if type(params["batch_size"]) is not int or params["batch_size"] < 1:
                raise ValueError("batch_size must be a positive integer")
            for key in ("prompt", "audio_url"):
                if params[key] is not None and not isinstance(params[key], str):
                    raise ValueError("Motion reference text inputs must be text")
            serialize_json(params)
        except (StorageError, ValueError, TypeError) as exc:
            raise CommandValidationError("Invalid motion reference inputs") from exc
        target, source, owner = self._saved_motion_target(view, asset_id, asset_type)
        if target is None:
            raise ProjectContextError("Motion reference target not found", not_found=True)
        source_image_url = self._motion_source_image(target, asset_type)
        if not source_image_url:
            raise CommandValidationError(f"No source image available for {asset_type}")
        prompt = params["prompt"] or self._motion_default_prompt(target, asset_type, params["audio_url"])
        options = {
            "image_url": source_image_url,
            "prompt": prompt,
            "duration": params["duration"],
            "audio_url": params["audio_url"],
            "batch_size": params["batch_size"],
        }
        serialize_json(options)
        live_script, live_target, live_source, owner_object, live_owner = self._live_motion_target(script.id, asset_id, asset_type)
        if live_source != source or live_owner != owner:
            raise StorageConflictError("Motion reference target changed; reload before retrying")
        return {
            "script_id": script.id, "asset_id": asset_id, "asset_type": asset_type,
            "kind": self._motion_kind(asset_type), "source": source, "owner": owner,
            "key": (*owner, asset_type, asset_id), "script_identity": live_script,
            "owner_identity": owner_object, "target_identity": live_target,
            "expected": target.model_dump(), "params": params, "options": options,
        }

    def _check_motion_ref(self, context, token=None):
        with self._save_lock:
            self.assert_storage_available()
            live_script, live_target, live_source, owner_object, live_owner = self._live_motion_target(
                context["script_id"], context["asset_id"], context["asset_type"]
            )
            view = self.read_committed_project_view(context["script_id"])
            saved, saved_source, owner = self._saved_motion_target(view, context["asset_id"], context["asset_type"])
            if (
                live_script is not context["script_identity"] or live_target is not context["target_identity"]
                or owner_object is not context["owner_identity"] or live_target is None or saved is None
                or live_source != context["source"] or saved_source != context["source"]
                or owner != context["owner"] or live_owner != context["owner"]
                or saved.model_dump() != context["expected"] or live_target.model_dump() != context["expected"]
                or self._motion_source_image(saved, context["asset_type"]) != context["options"]["image_url"]
                or (token is not None and self._motion_ref_runs.get(context["key"]) is not token)
            ):
                raise StorageConflictError("Motion reference target or inputs changed; reload before retrying")
            return live_target

    def _begin_motion_ref(self, context):
        token = object()
        with self._save_lock:
            self._check_motion_ref(context)
            self.motion_reference_service.assert_owner_available(context)
            if not hasattr(self, "_motion_ref_runs"):
                self._motion_ref_runs = {}
            self._motion_ref_runs[context["key"]] = token
            try:
                target = self._check_motion_ref(context, token)
                target.status = GenerationStatus.PROCESSING
                self._save_after_asset_mutation(context["source"])
                context["expected"] = target.model_dump()
            except BaseException:
                if self._motion_ref_runs.get(context["key"]) is token:
                    del self._motion_ref_runs[context["key"]]
                raise
        return token

    def _release_motion_ref(self, context, token) -> None:
        with self._save_lock:
            if self._motion_ref_runs.get(context["key"]) is token:
                del self._motion_ref_runs[context["key"]]

    def _finish_motion_ref_status(self, context, token, status: GenerationStatus) -> None:
        with self._save_lock:
            target = self._check_motion_ref(context, token)
            target.status = status
            self._save_after_asset_mutation(context["source"])
            context["expected"] = target.model_dump()

    def create_motion_ref_task(self, script_id: str, asset_id: str, asset_type: str,
                                prompt: Optional[str] = None, audio_url: Optional[str] = None,
                                duration: int = 5, batch_size: int = 1) -> Tuple[Script, str]:
        """Accept a motion-reference task only after the real owner is saved."""
        params = {"prompt": prompt, "audio_url": audio_url, "duration": duration, "batch_size": batch_size}
        with self._save_lock:
            context = self._prepare_motion_ref(self.read_committed_project_view(script_id), asset_id, asset_type, params)
            task_id = str(uuid.uuid4())
            token = self._begin_motion_ref(context)
            self.video_generation_tasks[task_id] = {
                "status": "pending", "progress": 0, "error": None,
                "script_id": script_id, "asset_id": asset_id, "asset_type": asset_type,
                "created_at": time.time(), "params": deepcopy(context["params"]),
                "_context": context, "_token": token,
            }
            return self.read_committed_script(script_id), task_id

    def process_durable_motion_ref_task(self, script_id: str, task_id: str):
        """Dispatch a newly admitted motion once, then reuse query/fetch recovery."""
        try:
            result = self.submit_provider_video_task(script_id, task_id)
            task = next(item for item in result.video_tasks if item.id == task_id)
            record = task.provider_execution
            if record is None or record.handle is None or task.status == "completed":
                return
            result = self.query_provider_video_task(script_id, task_id)
            task = next(item for item in result.video_tasks if item.id == task_id)
            record = task.provider_execution
            if record is None or record.observation is None:
                return
            observation = provider_recovery.observation_from_record(record)
            if observation is not None and observation.status is ProviderTaskStatus.SUCCEEDED:
                self.fetch_provider_video_task(script_id, task_id)
        except (StorageConflictError, ValueError) as exc:
            # A replaced target may stop an unsubmitted request. A concurrent
            # operation owns its own status; a late handler must not change it.
            with self._save_lock:
                script = self.scripts.get(script_id)
                current = next((item for item in script.video_tasks if item.id == task_id), None) if script else None
                if (
                    current is not None and current.motion_reference is not None
                    and current.provider_execution is not None
                    and current.provider_execution.phase == "accepted"
                    and (script_id, task_id) not in self._video_task_runs
                ):
                    current.status, current.error = "needs_confirmation", str(exc)
                    self._commit_storage("projects.json")
            logger.warning("Motion reference %s requires confirmation: %s", task_id, exc)

    def process_motion_ref_task(self, script_id: str, task_id: str):
        """Claim a pending motion-reference task exactly once."""
        with self._save_lock:
            task = self.video_generation_tasks.get(task_id)
            if not task:
                logger.error(f"Video task {task_id} not found")
                return
            if task["status"] != "pending":
                return
            self.assert_storage_available()
            if task.get("script_id") != script_id:
                task.update(status="failed", error="Motion reference task project mismatch")
                try:
                    self._finish_motion_ref_status(task["_context"], task["_token"], GenerationStatus.FAILED)
                finally:
                    self._release_motion_ref(task["_context"], task["_token"])
                return
            task["status"] = "processing"
        try:
            generated = self._generate_motion_ref_context(task["_context"], token=task["_token"])
        except StorageError as exc:
            with self._save_lock:
                if self.video_generation_tasks.get(task_id) is task:
                    task.update(status="failed", error=str(exc))
            raise
        except Exception as e:
            with self._save_lock:
                if self.video_generation_tasks.get(task_id) is task:
                    task.update(status="failed", error=str(e))
            logger.error(f"Video task {task_id} failed: {e}")
        else:
            with self._save_lock:
                if self.video_generation_tasks.get(task_id) is task and task["status"] == "processing":
                    task.update(status="completed", progress=100)
            logger.info(f"Task {task_id} completed successfully with {generated} motion reference result(s)")

    def sync_descriptions_from_script_entities(self, script_id: str) -> Script:
        """
        Syncs entity descriptions from ScriptProcessor parsed entities.
        This clears saved prompts so the UI will regenerate them from the current description.

        Note: This only updates prompts, not generated images/videos.
        """
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            # Clear saved prompts for all characters so UI will regenerate from description
            for character in script.characters:
                character.full_body_prompt = None
                character.three_view_prompt = None
                character.headshot_prompt = None
                character.video_prompt = None

            # Scenes and props might also have prompts to clear (if applicable)
            for scene in script.scenes:
                if hasattr(scene, 'prompt'):
                    scene.prompt = None

            for prop in script.props:
                if hasattr(prop, 'prompt'):
                    prop.prompt = None

            self._save_data()
            logger.info(f"Descriptions synced for script {script_id}: cleared prompts for {len(script.characters)} characters, {len(script.scenes)} scenes, {len(script.props)} props")
            return script.model_copy(deep=True)

    def add_character(self, script_id: str, name: str, description: str) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            new_char = Character(
                id=f"char_{uuid.uuid4().hex[:8]}",
                name=name,
                description=description
            )
            script.characters.append(new_char)
            self._save_data()
            return script.model_copy(deep=True)

    def delete_character(self, script_id: str, char_id: str) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            script.characters = [c for c in script.characters if c.id != char_id]
            self._save_data()
            return script.model_copy(deep=True)

    def add_scene(self, script_id: str, name: str, description: str) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            new_scene = Scene(
                id=f"scene_{uuid.uuid4().hex[:8]}",
                name=name,
                description=description
            )
            script.scenes.append(new_scene)
            self._save_data()
            return script.model_copy(deep=True)

    def delete_scene(self, script_id: str, scene_id: str) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            script.scenes = [s for s in script.scenes if s.id != scene_id]
            self._save_data()
            return script.model_copy(deep=True)

    def add_prop(self, script_id: str, name: str, description: str = "") -> Script:
        """Create a project-owned prop after checking runtime availability."""
        from .models import Prop
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Project not found")
            prop = Prop(id=f"prop_{uuid.uuid4().hex[:8]}", name=name, description=description)
            script.props.append(prop)
            script.updated_at = time.time()
            self._save_data()
            return script.model_copy(deep=True)

    def delete_prop(self, script_id: str, prop_id: str) -> Script:
        """Delete a prop and its frame references in the same project commit."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Project not found")
            kept = [prop for prop in script.props if prop.id != prop_id]
            if len(kept) == len(script.props):
                raise ValueError("Prop not found")
            script.props = kept
            for frame in script.frames:
                if prop_id in frame.prop_ids:
                    frame.prop_ids.remove(prop_id)
            script.updated_at = time.time()
            self._save_data()
            return script.model_copy(deep=True)

    def _find_asset_with_source(
        self, script: "Script", asset_id: str, asset_type: str
    ) -> Tuple[Optional[object], Optional[str]]:
        """Locate an asset by (id, type) in either the episode's local
        list OR the parent series' shared pool. Returns
        (asset, source) where source ∈ {"script", "series", "global"} so the
        caller can mutate the right object and save the right side.

        Episode-local always wins (the user explicitly forked this
        asset to override the series version). Falls back to series
        only when the id isn't local. Returns (None, None) when the
        asset doesn't exist in either container — caller should 404.
        """
        if asset_type == "character":
            ep_list = script.characters
        elif asset_type == "scene":
            ep_list = script.scenes
        elif asset_type == "prop":
            ep_list = script.props
        else:
            return None, None
        local = next((a for a in ep_list if a.id == asset_id), None)
        if local is not None:
            return local, "script"
        # Fall back to series shared pool if this episode belongs to
        # a series.
        if script.series_id:
            series = self.series_store.get(script.series_id)
            if series:
                if asset_type == "character":
                    sh_list = series.characters
                elif asset_type == "scene":
                    sh_list = series.scenes
                else:  # prop
                    sh_list = series.props
                shared = next((a for a in sh_list if a.id == asset_id), None)
                if shared is not None:
                    return shared, "series"
            # Series miss → fall through to the global library below.
        # Fall back to the project-independent global asset library
        # (lowest layer). Empty by default, so this is a no-op until
        # the global pool is populated.
        if asset_type == "character":
            gl_list = self.library_store.characters
        elif asset_type == "scene":
            gl_list = self.library_store.scenes
        else:  # prop
            gl_list = self.library_store.props
        glob = next((a for a in gl_list if a.id == asset_id), None)
        if glob is not None:
            return glob, "global"
        return None, None

    def _save_after_asset_mutation(self, source: str) -> None:
        """Persist after mutating an asset; pick the right save path
        based on which container the asset lives in (episode vs series
        vs global library)."""
        if source == "series":
            self._save_series_data()
        elif source == "global":
            self._save_library_data()
        else:
            self._save_data()

    def toggle_asset_lock(self, script_id: str, asset_id: str, asset_type: str) -> Script:
        """Toggle the locked status of an asset. Works on both
        episode-local and series-shared assets (A2 decision: default
        write to series, since locking a shared character should
        affect all episodes that use it)."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if not target_asset:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

            # Toggle the locked status
            target_asset.locked = not target_asset.locked
            self._save_after_asset_mutation(source)
            return self.read_committed_script(script_id)

    def toggle_asset_starred(self, script_id: str, asset_id: str, asset_type: str) -> Script:
        """Toggle the starred (asset-library shortlist) status of an asset.
        Mirrors toggle_asset_lock — works on both episode-local and
        series-shared assets via _find_asset_with_source."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if not target_asset:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

            target_asset.starred = not target_asset.starred
            self._save_after_asset_mutation(source)
            return self.read_committed_script(script_id)

    def toggle_project_starred(self, script_id: str) -> Script:
        """Toggle the user-starred (featured shortlist) flag on a project.
        Starred projects get the amber-halation 'featured' treatment in the
        gallery. Mirrors toggle_asset_starred but at the Script level. The
        read-modify-write is wrapped in _save_lock so the toggle is atomic."""
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            script.starred = not script.starred
            self._save_data()
            return script

    def toggle_frame_lock(self, script_id: str, frame_id: str) -> Script:
        """Toggle the locked status of a frame."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            target_frame = next((f for f in script.frames if f.id == frame_id), None)
            if not target_frame:
                raise ValueError(f"Frame {frame_id} not found")

            # Toggle the locked status
            target_frame.locked = not target_frame.locked
            self._save_data()
            return script.model_copy(deep=True)

    def update_asset_image(self, script_id: str, asset_id: str, asset_type: str, image_url: str) -> Script:
        """Updates the image URL of an asset manually. Per A2 decision,
        series-shared assets are updated in place (shared semantics);
        episode-local assets are updated locally."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            target, source = self._find_asset_with_source(script, asset_id, asset_type)
            if target is None or source is None:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")
            self._set_manual_asset_image(target, asset_type, image_url)
            self._save_after_asset_mutation(source)
            return self.read_committed_script(script_id)

    @classmethod
    def _set_manual_asset_image(cls, target, asset_type: str, image_url: str) -> None:
        """Apply an explicit manual choice without treating it as an upload."""
        patch = {"image_url": image_url}
        if asset_type == "character":
            patch["avatar_url"] = image_url
        candidate = type(target).model_validate({**target.model_dump(), **patch})
        url = candidate.image_url
        field, channel = ("reference_sheet", "reference_sheet") if asset_type == "character" else ("image_asset", "image")
        container = getattr(target, field)
        if url and container is None:
            container = AssetUnit.model_validate({}) if asset_type == "character" else ImageAsset.model_validate({})
            setattr(target, field, container)
        if container is not None:
            selected = None
            if url:
                images = cls._image_variants(container)
                matches = [image for image in images if image.url == url]
                chosen = next((image for image in matches if image.id == cls._selected_image(container)), None)
                if chosen is None:
                    chosen = matches[0] if matches else ImageVariant.model_validate({"id": str(uuid.uuid4()), "url": url, "created_at": time.time()})
                    if not matches:
                        images.append(chosen)
                selected = chosen.id
            cls._set_selected_image(container, selected)
            cls._sync_variant_urls(target, asset_type, channel)
        # An explicit clear keeps the legacy empty/null alias, not a fallback image.
        target.image_url = url
        if asset_type == "character":
            target.avatar_url = candidate.avatar_url

    def update_asset_description(self, script_id: str, asset_id: str, asset_type: str, description: str) -> Script:
        """Updates the description of an asset."""
        return self.update_asset_attributes(script_id, asset_id, asset_type, {"description": description})

    def update_asset_attributes(self, script_id: str, asset_id: str, asset_type: str, attributes: Dict[str, Any]) -> Script:
        """Updates arbitrary attributes of an asset. Routes the write
        to either the episode-local or the parent series' shared copy
        depending on which container owns the asset (A2 decision)."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if not target_asset:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

            # Validate the whole patch before any field on the live asset changes.
            patch = {}
            for key, value in attributes.items():
                if key in type(target_asset).model_fields:
                    patch[key] = value
                else:
                    logger.warning(f"Attribute {key} not found in {asset_type} model")
            candidate = type(target_asset).model_validate({**target_asset.model_dump(), **patch})
            for key in patch:
                setattr(target_asset, key, getattr(candidate, key))

            self._save_after_asset_mutation(source)
            return self.read_committed_script(script_id)

    @staticmethod
    def _upload_channel(asset_type: str, upload_type: str) -> str:
        if asset_type == "character":
            channels = {"full_body": "full_body", "head_shot": "headshot", "three_views": "three_view"}
            if upload_type in channels:
                return channels[upload_type]
        elif asset_type in {"scene", "prop"} and upload_type == "image":
            return "image"
        raise ValueError(f"Invalid upload_type for {asset_type}: {upload_type}")

    def _variant_target(self, script_id: str, asset_id: str, asset_type: str):
        """Resolve under the command lock; episode-local assets take precedence."""
        script = self.scripts.get(script_id)
        if script is None:
            raise ValueError("Script not found")
        if asset_type == "storyboard_frame":
            target = next((frame for frame in script.frames if frame.id == asset_id), None)
            source = "script"
        else:
            target, source = self._find_asset_with_source(script, asset_id, asset_type)
        if target is None:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")
        return script, target, source

    @staticmethod
    def _asset_owner(script: Script, source: str) -> tuple[str, str]:
        return source, script.series_id if source == "series" else (script.id if source == "script" else "library")

    def validate_asset_upload(self, script_id: str, asset_type: str, asset_id: str, upload_type: str) -> tuple[str, str]:
        """Preflight before file/network IO; commit must resolve the target again."""
        with self._save_lock:
            self.assert_storage_available()
            self._upload_channel(asset_type, upload_type)
            script, _, source = self._variant_target(script_id, asset_id, asset_type)
            return self._asset_owner(script, source)

    def add_uploaded_asset_variant(
        self, script_id: str, asset_type: str, asset_id: str, upload_type: str,
        image_url: str, description: Optional[str] = None, *,
        expected_owner: tuple[str, str] | None = None,
    ) -> Script:
        """Commit an upload to its existing channel without changing schema."""
        with self._save_lock:
            self.assert_storage_available()
            channel = self._upload_channel(asset_type, upload_type)
            script, target, source = self._variant_target(script_id, asset_id, asset_type)
            if expected_owner is not None and self._asset_owner(script, source) != expected_owner:
                raise StorageConflictError("Asset owner changed during upload; reload before attaching it")
            variant = ImageVariant(
                id=str(uuid.uuid4()), url=image_url,
                prompt_used=description or target.description,
                is_uploaded_source=True, upload_type=upload_type,
            )
            # Validate the optional patch before appending to either copy.
            if description:
                description = type(target).model_validate({**target.model_dump(), "description": description}).description
            if asset_type == "character":
                unit_field, legacy_field, _ = self._CHARACTER_IMAGE_CHANNELS[channel]
                if getattr(target, unit_field) is None:
                    setattr(target, unit_field, AssetUnit())
                if getattr(target, legacy_field) is None:
                    setattr(target, legacy_field, ImageAsset())
                unit, legacy = getattr(target, unit_field), getattr(target, legacy_field)
                unit.image_variants.append(variant)
                legacy.variants.append(variant.model_copy(deep=True))
                self._set_selected_image(unit, variant.id)
                self._set_selected_image(legacy, variant.id)
            else:
                if target.image_asset is None:
                    target.image_asset = ImageAsset()
                target.image_asset.variants.append(variant)
                self._set_selected_image(target.image_asset, variant.id)
            if description:
                target.description = description
            self._sync_variant_urls(target, asset_type, channel)
            self._save_after_asset_mutation(source)
            return self.read_committed_script(script_id)

    def update_project_style(self, script_id: str, style_preset: str, style_prompt: Optional[str] = None) -> Script:
        """Validate the complete style patch before changing a live project."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            candidate = Script.model_validate({**script.model_dump(), "style_preset": style_preset, "style_prompt": style_prompt})
            _validate_setting_value(candidate)
            script.style_preset = candidate.style_preset
            script.style_prompt = candidate.style_prompt
            script.updated_at = time.time()
            self._save_data()
            return self.read_committed_script(script_id)

    def save_art_direction(self, script_id: str, selected_style_id: str, style_config: Dict[str, Any], custom_styles: List[Dict[str, Any]] = None, ai_recommendations: List[Dict[str, Any]] = None, *, expected_revision: int) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            if script.revision != expected_revision:
                raise StorageConflictError("Project changed; reload before saving art direction")
            direction = ArtDirection(
                selected_style_id=selected_style_id, style_config=deepcopy(style_config),
                custom_styles=deepcopy(custom_styles or []), ai_recommendations=deepcopy(ai_recommendations or []),
            )
            _validate_setting_value(direction)
            script.art_direction = direction
            script.updated_at = time.time()
            self._save_data()
            return self.read_committed_script(script_id)

    def clear_art_direction(self, script_id: str, *, expected_revision: int) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            if script.revision != expected_revision:
                raise StorageConflictError("Project changed; reload before clearing art direction")
            script.art_direction = None
            script.updated_at = time.time()
            self._save_data()
            return self.read_committed_script(script_id)

    def update_prompt_config(self, script_id: str, prompts: Dict[str, str]) -> Script:
        """Replace the six editable prompts, retaining the separately selected model."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            fields = {"storyboard_polish", "video_polish", "r2v_polish", "entity_extraction", "style_analysis", "storyboard_extraction"}
            if set(prompts) - fields:
                raise ValueError("Unknown editable prompt field")
            candidate = PromptConfig(**deepcopy(prompts), polish_model=script.prompt_config.polish_model)
            _validate_setting_value(candidate)
            script.prompt_config = candidate
            self._save_data()
            return self.read_committed_script(script_id)

    # === STORYBOARD DRAMATIZATION v2 ===

    def _read_frame_generation_inputs(self, script_id: str, kind: str, frame_id: Optional[str] = None):
        """Freeze only the effective shared fields consumed by this command."""
        script, series, library = self.read_committed_project_view(script_id)
        if script is None:
            raise ProjectContextError("Script not found", not_found=True)
        resolved = self._resolve_asset_layers(script, series, library)
        prompt = None
        if kind == "analyze":
            dependency = {
                name: [{"id": item.id, "name": item.name, "description": item.description} for item in items]
                for name, items in resolved.items()
            }
            prompt = self.get_effective_prompt("storyboard_extraction", script, series)
        elif kind == "rich":
            frame = next((item for item in script.frames if item.id == frame_id), None)
            if frame is None:
                raise ProjectContextError(f"Frame {frame_id} not found", not_found=True)
            dependency = {
                "characters": [
                    {key: getattr(item, key) for key in ("id", "name", "description", "clothing", "age", "gender")}
                    for item in resolved["characters"] if item.id in frame.character_ids
                ],
                "scenes": [
                    {"id": item.id, "name": item.name, "description": item.description}
                    for item in resolved["scenes"] if item.id == frame.scene_id
                ],
            }
        else:
            dependency = {}
            prompt = self.get_effective_prompt("storyboard_polish", script, series)
        return script, resolved, prompt, (dependency, prompt)

    def _check_frame_generation_result(self, snapshot: Script, kind: str, frame_id: Optional[str], dependency) -> Script:
        """Caller holds the short command lock; no LLM or yield runs here."""
        current = self._check_project_result(snapshot)
        _, _, _, latest = self._read_frame_generation_inputs(snapshot.id, kind, frame_id)
        if latest != dependency:
            raise StorageConflictError("Shared inputs changed while refining; reload before applying the result")
        return current

    def _apply_refined_frame(self, snapshot: Script, frame_id: str, candidate: StoryboardFrame, kind: str, dependency) -> StoryboardFrame:
        original = next(frame for frame in snapshot.frames if frame.id == frame_id)
        before, after = original.model_dump(), candidate.model_dump()
        changed = [key for key in after if after[key] != before[key]]
        with self._save_lock:
            current = self._check_frame_generation_result(snapshot, kind, frame_id, dependency)
            frame = next((item for item in current.frames if item.id == frame_id), None)
            if frame is None:
                raise ProjectContextError(f"Frame {frame_id} not found", not_found=True)
            for key in changed:
                setattr(frame, key, deepcopy(getattr(candidate, key)))
            self._save_data()
            return next(item for item in self.read_committed_script(snapshot.id).frames if item.id == frame_id)

    def analyze_text_to_frames(self, script_id: str, text: str) -> Script:
        """Replace frames only while the saved project and shared inputs match."""
        script, resolved, prompt, dependency = self._read_frame_generation_inputs(script_id, "analyze")
        all_characters, all_scenes, all_props = (resolved[name] for name in ("characters", "scenes", "props"))
        self.assert_storage_available()
        try:
            raw_frames = self.script_processor.analyze_to_storyboard(
                text, deepcopy(dependency[0]), custom_extraction_prompt=prompt,
            )
            if not raw_frames or not isinstance(raw_frames, list):
                raise ValueError("AI 分镜分析未返回有效帧数据，请重试。")
            new_frames = self._build_analyzed_frames(raw_frames, all_characters, all_scenes, all_props)
        except (StorageError, PolishError):
            raise
        except Exception as exc:
            raise ProjectGenerationError(f"Storyboard analysis failed: {exc}") from exc
        with self._save_lock:
            current = self._check_frame_generation_result(script, "analyze", None, dependency)
            current.frames = new_frames
            current.updated_at = time.time()
            self._save_data()
            return self.read_committed_script(script_id)

    @staticmethod
    def _build_analyzed_frames(raw_frames, all_characters, all_scenes, all_props):
        # Convert raw frame dicts to StoryboardFrame objects
        new_frames = []
        for frame_data in raw_frames:
            if not isinstance(frame_data, dict):
                raise ValueError("Analyzed frame must be an object")
            action = frame_data.get("action_summary", frame_data.get("action_description"))
            if not isinstance(action, str) or not action.strip():
                raise ValueError("Analyzed frame requires a non-empty action description")
            # Resolve scene ID by name
            scene_ref_name = frame_data.get("scene_ref_name", "")
            scene_id = None
            for scene in all_scenes:
                if scene.name == scene_ref_name or scene_ref_name in scene.name:
                    scene_id = scene.id
                    break
            if not scene_id and all_scenes:
                scene_id = all_scenes[0].id  # Fallback to first scene
            elif not scene_id:
                scene_id = str(uuid.uuid4())  # Generate a placeholder ID

            # Resolve character IDs by names (case-insensitive, bidirectional contains)
            char_ref_names = frame_data.get("character_ref_names", [])
            character_ids = []
            for char_name in char_ref_names:
                cn = char_name.strip().lower()
                for char in all_characters:
                    cname = char.name.strip().lower()
                    if cname == cn or cn in cname or cname in cn:
                        character_ids.append(char.id)
                        break

            # Resolve prop IDs by names (case-insensitive, bidirectional contains)
            prop_ref_names = frame_data.get("prop_ref_names", [])
            prop_ids = []
            for prop_name in prop_ref_names:
                pn = prop_name.strip().lower()
                for prop in all_props:
                    pname = prop.name.strip().lower()
                    if pname == pn or pn in pname or pname in pn:
                        prop_ids.append(prop.id)
                        break

            frame = StoryboardFrame(
                id=str(uuid.uuid4()),
                scene_id=scene_id,
                character_ids=character_ids,
                prop_ids=prop_ids,
                action_description=action,
                visual_atmosphere=frame_data.get("visual_atmosphere"),
                shot_size=frame_data.get("shot_size"),
                camera_angle=frame_data.get("camera_angle", "平视"),
                camera_movement=frame_data.get("camera_movement"),
                dialogue=frame_data.get("dialogue"),
                speaker=frame_data.get("speaker"),
                duration=frame_data.get("duration"),
                status=GenerationStatus.PENDING
            )
            new_frames.append(frame)

        return new_frames

    def refine_frame(self, script_id: str, frame_id: str) -> StoryboardFrame:
        """Phase 2: Refine a single coarse frame into a rich frame."""
        script, resolved, _, dependency = self._read_frame_generation_inputs(script_id, "rich", frame_id)
        frame = next(item for item in script.frames if item.id == frame_id).model_copy(deep=True)
        frame_idx = script.frames.index(frame)
        all_characters, all_scenes = resolved["characters"], resolved["scenes"]

        # Build coarse frame dict for LLM
        coarse = {
            "action_summary": frame.action_description,
            "shot_size": frame.shot_size,
            "camera_angle": frame.camera_angle,
            "camera_movement": frame.camera_movement,
            "dialogue": frame.dialogue,
            "speaker": frame.speaker,
            "duration": frame.duration,
            "character_names": [c.name for c in all_characters if c.id in frame.character_ids],
            "scene_name": next((s.name for s in all_scenes if s.id == frame.scene_id), None),
        }

        # Character/scene assets
        char_assets = [
            {"name": c.name, "description": c.description, "clothing": c.clothing or ""}
            for c in all_characters if c.id in frame.character_ids
        ]
        scene_assets = [
            {"name": s.name, "description": s.description}
            for s in all_scenes if s.id == frame.scene_id
        ]

        # Adjacent frame context
        prev_ctx = None
        if frame_idx > 0:
            pf = script.frames[frame_idx - 1]
            prev_ctx = f"Action: {pf.action_description}. Shot: {pf.shot_size}, {pf.camera_angle}."
        next_ctx = None
        if frame_idx < len(script.frames) - 1:
            nf = script.frames[frame_idx + 1]
            next_ctx = f"Action: {nf.action_description}. Shot: {nf.shot_size}, {nf.camera_angle}."

        self.assert_storage_available()
        try:
            result = self.script_processor.refine_frame_to_rich(
                coarse, char_assets, scene_assets, prev_ctx, next_ctx,
            )
            candidate = self._build_rich_frame(frame, result, all_characters, all_scenes)
        except (StorageError, PolishError):
            raise
        except Exception as exc:
            raise ProjectGenerationError(f"Frame refinement failed: {exc}") from exc
        return self._apply_refined_frame(script, frame_id, candidate, "rich", dependency)

    @staticmethod
    def _build_rich_frame(frame: StoryboardFrame, result, all_characters, all_scenes) -> StoryboardFrame:
        """Validate the entire upstream patch before it can reach live state."""
        from .prompt_assembly import assemble_prompt, sync_dialogue_to_tts
        from .models import DialogueStructured, CameraMovementData, Blocking, AudioNote, LightingData, StageSubject

        if not isinstance(result, dict):
            raise ValueError("Rich frame result must be an object")
        scalar_keys = ("visual_description", "shot_size", "camera_angle", "duration", "transition_hint")
        scalar_patch = {key: result[key] for key in scalar_keys if result.get(key) is not None}
        StoryboardFrame.model_validate({**frame.model_dump(), **scalar_patch})
        # Missing/empty fields remain no-ops, but explicitly malformed fields
        # must not disappear merely because they are falsey (e.g. primary=[]).
        structures = (
            ("camera_movement", CameraMovementData, {"primary": ""}),
            ("blocking", Blocking, {"description": ""}),
            ("dialogue_structured", DialogueStructured, {"speaker": "", "line": ""}),
            ("audio_note", AudioNote, {}),
            ("lighting", LightingData, {}),
        )
        if not set(result).intersection((*scalar_keys, *(key for key, _, _ in structures))):
            raise ValueError("Rich frame result contains no refinement fields")
        for key, model, defaults in structures:
            value = result.get(key)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"{key} must be an object")
            payload = {**defaults, **{field: item for field, item in value.items() if item is not None}}
            if key == "blocking" and payload.get("stage") is not None:
                if not isinstance(payload["stage"], list):
                    raise ValueError("blocking.stage must be a list")
                payload["stage"] = [
                    {"ref": "", "zone": "center", "depth": "mid", **item}
                    if isinstance(item, dict) else item for item in payload["stage"]
                ]
            model.model_validate(payload)

        # Map result onto frame fields
        if result.get("visual_description"):
            from .prompt_assembly import inject_reference_tags
            frame.visual_description = inject_reference_tags(
                result["visual_description"], frame, all_characters, all_scenes
            )
        if result.get("shot_size"):
            frame.shot_size = result["shot_size"]
        if result.get("camera_angle"):
            frame.camera_angle = result["camera_angle"]
        if result.get("duration"):
            frame.duration = result["duration"]
        if result.get("transition_hint"):
            frame.transition_hint = result["transition_hint"]

        # Camera movement structured
        cm = result.get("camera_movement")
        if cm and cm.get("primary"):
            frame.camera_movement_structured = CameraMovementData(
                primary=cm["primary"],
                secondary=cm.get("secondary"),
                speed=cm.get("speed", "normal"),
                description=cm.get("description"),
            )

        # Blocking
        blk = result.get("blocking")
        if blk and blk.get("description"):
            stage_list = None
            if blk.get("stage"):
                stage_list = [
                    StageSubject(
                        ref=s.get("ref", ""),
                        zone=s.get("zone", "center"),
                        depth=s.get("depth", "mid"),
                        height=s.get("height"),
                        facing=s.get("facing"),
                        posture=s.get("posture"),
                    )
                    for s in blk["stage"]
                ]
            frame.blocking = Blocking(
                description=blk["description"],
                stage=stage_list,
                camera_relation=blk.get("camera_relation"),
            )

        # Dialogue structured
        ds = result.get("dialogue_structured")
        if ds and ds.get("line"):
            frame.dialogue_structured = DialogueStructured(
                speaker=ds.get("speaker", frame.speaker or ""),
                line=ds["line"],
                emotion=ds.get("emotion"),
                delivery=ds.get("delivery"),
            )

        # Audio note
        an = result.get("audio_note")
        if an and (an.get("sfx") or an.get("ambience")):
            frame.audio_note = AudioNote(
                sfx=an.get("sfx"),
                ambience=an.get("ambience"),
                bgm_note=an.get("bgm_note"),
            )

        # Lighting
        lt = result.get("lighting")
        if lt and (lt.get("description") or lt.get("direction")):
            frame.lighting = LightingData(
                direction=lt.get("direction"),
                quality=lt.get("quality"),
                color_temp=lt.get("color_temp"),
                description=lt.get("description"),
            )

        # Sync dialogue → TTS instructions & compute assembled prompt
        sync_dialogue_to_tts(frame)
        frame.assembled_prompt = assemble_prompt(frame, all_characters)
        frame.updated_at = time.time()

        return StoryboardFrame.model_validate(frame.model_dump())

    @staticmethod
    def _refinement_abort(exc, total: int, success: int, failed: int) -> dict:
        if isinstance(exc, StorageConflictError):
            code, message = "storage_conflict", "项目或共享输入已变化，请重新加载后重试"
        elif isinstance(exc, StorageError):
            code, message = "storage_unavailable", "存储不可用，请重新启动后重试"
        else:
            code, message = "target_missing", "项目或分镜已删除，批量精修已停止"
        return {"code": code, "error": message, "total": total, "success": success, "failed": failed}

    def refine_batch_generator(self, script_id: str):
        """Freeze membership; stop subsequent calls on a failed write boundary."""
        try:
            script = self.read_committed_script(script_id)
        except (StorageError, ValueError) as exc:
            yield ("batch_aborted", self._refinement_abort(exc, 0, 0, 0))
            return
        frames = [(frame.id, frame.action_description) for frame in script.frames]
        total, success, failed = len(frames), 0, 0
        for idx, (frame_id, description) in enumerate(frames):
            yield ("frame_refine_start", {
                "frame_id": frame_id,
                "frame_index": idx,
                "total": total,
                "label": description[:40] if description else f"Frame {idx+1}",
            })
            try:
                self.refine_frame(script_id, frame_id)
            except (StorageError, ProjectContextError) as exc:
                failed += 1
                event = self._refinement_abort(exc, total, success, failed)
                event.update(frame_id=frame_id, frame_index=idx)
                yield ("batch_aborted", event)
                return
            except Exception as exc:
                failed += 1
                logger.error(f"[refine_batch] frame={frame_id} error={exc}")
                yield ("frame_refine_error", {
                    "frame_id": frame_id,
                    "frame_index": idx,
                    "error": f"分镜优化失败（{type(exc).__name__}），详情请查看服务端日志",
                    **({"reason": exc.reason} if isinstance(exc, PolishError) else {}),
                })
            else:
                success += 1
                yield ("frame_refine_complete", {
                    "frame_id": frame_id,
                    "frame_index": idx,
                    "total": total,
                })
        yield ("batch_complete", {"total": total, "success": success, "failed": failed})

    def refine_frame_prompt(self, script_id: str, frame_id: str, raw_prompt: str, assets: List[Dict[str, Any]], feedback: str = "") -> Dict[str, Any]:
        """Polish caller inputs; update only a target that existed at capture."""
        script, _, custom_prompt, dependency = self._read_frame_generation_inputs(script_id, "prompt", frame_id)
        original = next((frame for frame in script.frames if frame.id == frame_id), None)
        from .llm import DEFAULT_STORYBOARD_POLISH_PROMPT
        if custom_prompt == DEFAULT_STORYBOARD_POLISH_PROMPT:
            custom_prompt = ""
        self.assert_storage_available()
        try:
            result = self.script_processor.polish_storyboard_prompt(raw_prompt, deepcopy(assets), feedback, custom_prompt)
            if not isinstance(result, dict):
                raise ValueError("Polished prompt result must be an object")
            if any(not isinstance(result.get(key), str) or not result[key].strip() for key in ("prompt_cn", "prompt_en")):
                raise ValueError("Polished prompt result requires non-empty Chinese and English prompts")
            patch = {
                "image_prompt_cn": result.get("prompt_cn"),
                "image_prompt_en": result.get("prompt_en"),
                "image_prompt": result.get("prompt_en"),
                "updated_at": time.time(),
            }
            # Validate return-only responses too, without inventing a live target.
            base = original or StoryboardFrame(id=frame_id, scene_id="", action_description="")
            candidate = StoryboardFrame.model_validate({**base.model_dump(), **patch})
        except (StorageError, PolishError):
            raise
        except Exception as exc:
            raise ProjectGenerationError(f"Prompt refinement failed: {exc}") from exc
        self.assert_storage_available()
        if original is not None:
            self._apply_refined_frame(script, frame_id, candidate, "prompt", dependency)
        return {
            "prompt_cn": candidate.image_prompt_cn,
            "prompt_en": candidate.image_prompt_en,
            "frame_updated": original is not None,
        }

    def generate_storyboard(self, script_id: str) -> Script:
        """Freeze this batch's inputs and save each result before the next call."""
        with self._save_lock:
            view = self.read_committed_project_view(script_id)
            script = view[0]
            if script is None:
                raise ProjectContextError("Script not found", not_found=True)
            contexts = [
                self._prepare_storyboard_image(view, frame.id, None, None, 1, batch=True)
                for frame in script.frames
                if not (frame.status == GenerationStatus.COMPLETED and frame.image_url)
            ]
            expected_scope = script.asset_image_request_scope_id
            if contexts:
                # Preserve the batch renderer's active model and default size.
                model_params = self.storyboard_generator.model.params
                single_output = model_params.get("n", 1)
                if type(single_output) is int and single_output == 1:
                    for context in contexts:
                        referenced = bool(context["implicit_reference_urls"])
                        model = model_params.get("i2i_model_name" if referenced else "model_name",
                                                 "wan2.7-image" if referenced else "wan2.7-image-pro")
                        options = dict(context["options"], model_name=model, size="1024*576")
                        if self._is_core_storyboard_image({**context, "options": options}):
                            context["options"] = options
        for context in contexts:
            if not self._is_core_storyboard_image(context):
                self._generate_storyboard_image(context)
                continue
            from .asset_image_execution import AssetImageRequestCommand
            command = AssetImageRequestCommand(
                client_request_id=f"storyboard-{uuid.uuid4()}", asset_id=context["frame"].id,
                asset_type="storyboard_frame", generation_type="all", prompt="",
                composition_data=None, expected_revision=script.revision,
            )
            prepared = dict(context, key=("script", script_id, "storyboard_frame", command.asset_id),
                            root_kind="project", root_id=script_id, kind="storyboard_frame", asset_id=command.asset_id,
                            target=context["frame"], root_identity=context["script_identity"],
                            target_identity=context["frame_identity"], owner_identity=context["script_identity"],
                            owner=("script", script_id), series_id=script.series_id, view=view,
                            request_root_scope_id=expected_scope, expected_revision=None,
                            storyboard_context=context, base_prompt=command.prompt,
                            reference_image_url=next(iter(context["implicit_reference_urls"]), None),
                            params=command.model_dump(exclude={
                                'client_request_id', 'asset_id', 'asset_type', 'seed', 'watermark',
                                'expected_revision', 'composition_data', 'reference_variant_id', 'reference_source',
                            }))
            admission = self.asset_image_service._admit_prepared_context(command, prepared)
            if expected_scope is None:
                expected_scope = admission.request.request_root_scope_id
            self.asset_image_service.run_original_request(admission.request.id)
            with self._save_lock:
                self._require_completed_core_asset_image(admission)
        with self._save_lock:
            result, _, _ = self.read_committed_project_view(script_id)
            if result is None:
                raise ProjectContextError("Script not found", not_found=True)
            return result

    def _storyboard_image_dependency(self, view, frame, options):
        """Compare only the resolved assets this frame consumes, plus its style/model."""
        from .storyboard import storyboard_reference_url

        script, series, library = view
        resolved = self._resolve_asset_layers(script, series, library)
        explicit_refs = bool(options["ref_image_paths"])

        def asset_input(kind, asset_id):
            asset = next((value for value in resolved[kind] if value.id == asset_id), None)
            if asset is None:
                return None
            owner = next(
                (name, value.id if name != "library" else None)
                for name, value in (("project", script), ("series", series), ("library", library))
                if value is not None and any(item.id == asset_id for item in getattr(value, kind))
            )
            text = (asset.name, asset.description) if kind == "characters" or not options["prompt"] else None
            return owner, asset.id, text, None if explicit_refs else storyboard_reference_url(asset)

        art_direction = script.art_direction or (series.art_direction if series else None)
        return {
            "owner": (script.created_at, script.series_id),
            "model": (script.model_settings.i2i_model, script.model_settings.storyboard_aspect_ratio),
            "style": (script.style_preset, script.style_prompt, art_direction.model_dump() if art_direction else None),
            "characters": [asset_input("characters", key) for key in frame.character_ids],
            "scene": asset_input("scenes", frame.scene_id),
        }

    def _prepare_storyboard_image(self, view, frame_id, composition_data, prompt, batch_size, *, batch=False):
        """Called under the command lock; no provider sees the live target."""
        from .assets import ASPECT_RATIO_TO_SIZE
        from .storyboard import storyboard_reference_url

        script, series, library = view
        if script is None:
            raise ProjectContextError("Script not found", not_found=True)
        frame = next((item for item in script.frames if item.id == frame_id), None)
        if frame is None:
            raise ProjectContextError("Frame not found", not_found=True)
        try:
            _validate_safe_id(frame.id, "frame id")
            if type(batch_size) is not int or batch_size < 1:
                raise ValueError("batch_size must be a positive integer")
            if prompt is not None and not isinstance(prompt, str):
                raise ValueError("prompt must be text")
            if composition_data is not None and not isinstance(composition_data, dict):
                raise ValueError("composition_data must be an object")
            composition = deepcopy(composition_data)
            serialize_json({"prompt": prompt, "composition": composition})
            references = (composition or {}).get("reference_image_urls", [])
            single = (composition or {}).get("reference_image_url")
            if not isinstance(references, list) or any(not isinstance(value, str) for value in references):
                raise ValueError("reference_image_urls must be a list of text values")
            if single is not None and not isinstance(single, str):
                raise ValueError("reference_image_url must be text")
            paths = []
            for value in [*references, *([single] if single and single not in references else [])]:
                if not value:
                    continue
                path = value if is_object_key(value, env=self.environment_snapshot()) or value.startswith("http") else self._output_ref_path(value)
                if (path == value or os.path.exists(path)) and path not in paths:
                    paths.append(path)
            options = {
                "ref_image_path": paths[0] if paths else None, "ref_image_paths": paths,
                "prompt": prompt, "batch_size": batch_size,
                # Preserve the existing batch generator's default model/size.
                "size": None if batch else ASPECT_RATIO_TO_SIZE.get(script.model_settings.storyboard_aspect_ratio, "1024*576"),
                "model_name": None if batch else script.model_settings.i2i_model,
            }
            resolved = self._resolve_asset_layers(script, series, library)
            characters = [item for item in resolved["characters"] if item.id in frame.character_ids]
            scene = next((item for item in resolved["scenes"] if item.id == frame.scene_id), None)
            dependency = self._storyboard_image_dependency(view, frame, options)
            serialize_json({"frame": frame.model_dump(), "options": options, "dependency": dependency})
        except (StorageError, ValueError, TypeError) as exc:
            raise CommandValidationError("Invalid storyboard image inputs") from exc
        live_script = self.scripts.get(script.id)
        live_frame = next((item for item in live_script.frames if item.id == frame_id), None) if live_script else None
        return {
            "key": (script.id, frame.id), "frame": frame, "expected": frame.model_dump(),
            "script_identity": live_script, "frame_identity": live_frame,
            "characters": characters, "scene": scene, "dependency": dependency,
            "implicit_reference_urls": list(dict.fromkeys(
                value for asset in [*characters, *([scene] if scene else [])]
                if (value := storyboard_reference_url(asset))
            )),
            "composition": composition, "options": options,
        }

    def _is_core_storyboard_image(self, context):
        """Qwen single-frame requests use either no source or one local source."""
        from ...models.qwen20_image_contract import QWEN20_IMAGE_PROVIDER_MODELS
        from ...utils.media_refs import classify_media_ref, MEDIA_REF_LOCAL_PATH

        models = QWEN20_IMAGE_PROVIDER_MODELS | {f"qwen/{name}#image" for name in QWEN20_IMAGE_PROVIDER_MODELS}
        options = context["options"]
        references = context["implicit_reference_urls"]
        return (options["model_name"] in models and options["batch_size"] == 1
                and options["size"] is not None and not context["composition"]
                and not options["ref_image_paths"] and len(references) <= 1
                and all(classify_media_ref(value, output_root=str(self.data_dir)) == MEDIA_REF_LOCAL_PATH
                        for value in references))

    def _check_storyboard_image(self, context, token=None):
        """Short-lock CAS check; never holds the lock across provider or OSS I/O."""
        with self._save_lock:
            self.assert_storage_available()
            script_id, frame_id = context["key"]
            script = self.scripts.get(script_id)
            frame = next((item for item in script.frames if item.id == frame_id), None) if script else None
            view = self.read_committed_project_view(script_id)
            saved_frame = next((item for item in view[0].frames if item.id == frame_id), None) if view[0] else None
            if (
                script is not context["script_identity"] or frame is not context["frame_identity"]
                or frame is None or saved_frame is None
                or frame.model_dump() != context["expected"] or saved_frame.model_dump() != context["expected"]
                or self._storyboard_image_dependency(view, saved_frame, context["options"]) != context["dependency"]
                or (token is not None and self._storyboard_image_runs.get(context["key"]) is not token)
            ):
                raise StorageConflictError("Storyboard image inputs changed while generating; reload before retrying")
            return frame

    def _finish_conflicted_storyboard_image(self, context, token) -> None:
        """Finish our unchanged PROCESSING target even when shared inputs moved."""
        with self._save_lock:
            # Preserve an existing runtime/provider error; an unavailable
            # runtime cannot persist cleanup and must not manufacture a new one.
            if self._closed or self._storage_failed is not None:
                return
            if self._storyboard_image_runs.get(context["key"]) is not token:
                return
            script_id, frame_id = context["key"]
            script = self.scripts.get(script_id)
            frame = next((item for item in script.frames if item.id == frame_id), None) if script else None
            saved = self._committed["projects.json"].get(script_id)
            saved_frame = next((item for item in saved["frames"] if item["id"] == frame_id), None) if saved else None
            if (
                script is not context["script_identity"] or frame is not context["frame_identity"]
                or frame is None or context["expected"]["status"] != GenerationStatus.PROCESSING
                or frame.model_dump() != context["expected"] or saved_frame != context["expected"]
            ):
                return
            frame.status = GenerationStatus.FAILED
            self._save_data()

    def _generate_storyboard_image(self, context) -> Script:
        token = object()
        started = False
        with self._save_lock:
            self._check_storyboard_image(context)
            if not hasattr(self, "_storyboard_image_runs"):
                self._storyboard_image_runs = {}
            if context["key"] in self._storyboard_image_runs:
                raise StorageConflictError("This storyboard frame is already generating an image")
            if ("script", context["key"][0], "storyboard_frame", context["key"][1]) in getattr(self, "_asset_image_runs", {}):
                raise StorageConflictError("This storyboard frame is already generating an image")
            self._storyboard_image_runs[context["key"]] = token
        try:
            with self._save_lock:
                frame = self._check_storyboard_image(context, token)
                candidate = context["frame"].model_copy(deep=True)
                candidate.status = GenerationStatus.PROCESSING
                if context["composition"]:
                    candidate.composition_data = deepcopy(context["composition"])
                candidate.image_prompt = context["options"]["prompt"]
                _validate_setting_value(candidate)
                for key in ("status", "composition_data", "image_prompt"):
                    setattr(frame, key, deepcopy(getattr(candidate, key)))
                self._save_data()
                context["expected"] = frame.model_dump()
                started = True

            self.storyboard_generator.generate_frame(
                candidate, context["characters"], context["scene"], **context["options"],
                check_current=lambda: self._check_storyboard_image(context, token),
            )
            try:
                candidate = StoryboardFrame.model_validate(candidate.model_dump())
                serialize_json(candidate.model_dump())
            except (StorageError, ValueError, TypeError) as exc:
                # Invalid provider metadata must finish this attempt without
                # publishing its private media or overwriting an edited target.
                with self._save_lock:
                    frame = self._check_storyboard_image(context, token)
                    frame.status = GenerationStatus.FAILED
                    self._save_data()
                raise ProjectGenerationError("Storyboard image generation returned invalid data") from exc
            with self._save_lock:
                frame = self._check_storyboard_image(context, token)
                for key in ("status", "image_prompt", "rendered_image_asset", "rendered_image_url", "image_url", "updated_at"):
                    setattr(frame, key, deepcopy(getattr(candidate, key)))
                self._save_data()
                return self.read_committed_script(context["key"][0])
        except StorageConflictError:
            if started:
                self._finish_conflicted_storyboard_image(context, token)
            raise
        finally:
            with self._save_lock:
                if self._storyboard_image_runs.get(context["key"]) is token:
                    del self._storyboard_image_runs[context["key"]]

    def update_frame(self, script_id: str, frame_id: str, *, expected_revision: int, **kwargs) -> Script:
        """Update frame data (prompt, scene_id, character_ids, etc.)."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            if script.revision != expected_revision:
                raise StorageConflictError("Project changed; reload before saving the frame edit")

            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError(f"Frame {frame_id} not found")

            fields = (
                "image_prompt", "action_description", "visual_description", "dialogue", "camera_angle", "scene_id",
                "character_ids", "duration", "shot_size", "transition_hint",
            )
            patch = {key: kwargs[key] for key in fields if kwargs.get(key) is not None}
            if "dialogue" in patch and frame.dialogue_structured is not None:
                patch["dialogue_structured"] = {
                    **frame.dialogue_structured.model_dump(), "line": patch["dialogue"],
                }
            if kwargs.get("camera_movement_description") is not None:
                movement = (
                    frame.camera_movement_structured.model_dump()
                    if frame.camera_movement_structured else {"speed": "normal"}
                )
                movement.update(
                    primary=kwargs["camera_movement_description"],
                    description=kwargs["camera_movement_description"],
                )
                patch["camera_movement_structured"] = movement
            candidate = StoryboardFrame.model_validate({**frame.model_dump(), **patch})
            if "visual_description" in patch:
                from .prompt_assembly import assemble_prompt

                candidate.assembled_prompt = assemble_prompt(
                    candidate, self.resolve_episode_assets(script)["characters"],
                )
                patch["assembled_prompt"] = candidate.assembled_prompt
            for key in patch:
                setattr(frame, key, getattr(candidate, key))

            self._save_data()
            return script.model_copy(deep=True)

    def add_frame(self, script_id: str, scene_id: str = None, action_description: str = "", camera_angle: str = "medium_shot", insert_at: int = None) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            new_frame = StoryboardFrame(
                id=f"frame_{uuid.uuid4().hex[:8]}",
                scene_id=scene_id or (script.scenes[0].id if script.scenes else ""),
                character_ids=[],
                action_description=action_description,
                camera_angle=camera_angle
            )

            if insert_at is not None and 0 <= insert_at <= len(script.frames):
                script.frames.insert(insert_at, new_frame)
            else:
                script.frames.append(new_frame)

            self._save_data()
            return script.model_copy(deep=True)

    def copy_frame(self, script_id: str, frame_id: str, insert_at: int = None) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            original_frame = next((f for f in script.frames if f.id == frame_id), None)
            if not original_frame:
                raise ValueError(f"Frame {frame_id} not found")

            # Keep content/media references, with independent nested metadata.
            new_frame = original_frame.model_copy(deep=True)
            new_frame.id = f"frame_{uuid.uuid4().hex[:8]}"
            new_frame.updated_at = time.time()
            new_frame.locked = False
            new_frame.clear_image_request_for_copy()

            task_id_map: dict[str, str] = {}
            task_by_id = {task.id: task for task in script.video_tasks}
            cloned_tasks: list[VideoTask] = []
            for task in list(script.video_tasks):
                if task.frame_id != original_frame.id or task.status != "completed" or not task.video_url:
                    continue
                cloned_task = task.model_copy(deep=True)
                cloned_task.id = f"task_{uuid.uuid4().hex[:12]}"
                cloned_task.frame_id = new_frame.id
                cloned_task.provider_name = None
                cloned_task.provider_task_id = None
                cloned_task.provider_request_id = None
                cloned_task.provider_execution = None
                cloned_task.created_at = time.time()
                task_id_map[task.id] = cloned_task.id
                cloned_tasks.append(cloned_task)

            def remap_task_id(value: Optional[str]) -> Optional[str]:
                if not value:
                    return None
                return task_id_map.get(value, value)

            new_frame.selected_video_id = remap_task_id(original_frame.selected_video_id)
            new_frame.final_take_id = remap_task_id(original_frame.final_take_id)
            new_frame.dubbed_video_task_id = remap_task_id(original_frame.dubbed_video_task_id)

            def remap_dub_source(original_source, copied_source):
                if not isinstance(original_source, DubSource) or not isinstance(copied_source, DubSource):
                    return copied_source
                new_task_id = task_id_map.get(original_source.task_id)
                old_task = task_by_id.get(original_source.task_id)
                if not new_task_id or not old_task or old_task.video_url != original_source.video_url or original_frame.audio_url != original_source.audio_url:
                    return copied_source
                return copied_source.model_copy(update={"task_id": new_task_id})

            new_frame.dub_preview_source = remap_dub_source(original_frame.dub_preview_source, new_frame.dub_preview_source)
            new_frame.dub_applied_source = remap_dub_source(original_frame.dub_applied_source, new_frame.dub_applied_source)

            if insert_at is not None and 0 <= insert_at <= len(script.frames):
                script.frames.insert(insert_at, new_frame)
            else:
                # Insert after the original frame by default
                try:
                    original_index = script.frames.index(original_frame)
                    script.frames.insert(original_index + 1, new_frame)
                except ValueError:
                    script.frames.append(new_frame)
            script.video_tasks.extend(cloned_tasks)

            self._commit_storage("projects.json")
            return script.model_copy(deep=True)

    def delete_frame(self, script_id: str, frame_id: str) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            script.frames = [f for f in script.frames if f.id != frame_id]
            self._save_data()
            return script.model_copy(deep=True)

    def reorder_frames(self, script_id: str, frame_ids: List[str]) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            frame_map = {f.id: f for f in script.frames}
            if (
                len(frame_ids) != len(script.frames)
                or len(set(frame_ids)) != len(frame_ids)
                or set(frame_ids) != set(frame_map)
            ):
                raise StorageConflictError(
                    "Frames changed or order is incomplete; reload before reordering"
                )
            script.frames = [frame_map[frame_id] for frame_id in frame_ids]
            self._save_data()
            return script.model_copy(deep=True)

    def _motion_result_payload(self, context, video_url: str):
        from .models import VideoVariant

        if not isinstance(video_url, str) or not video_url.strip():
            raise ValueError("Motion reference provider returned no video_url")
        try:
            serialize_json({"video_url": video_url})
        except (StorageError, ValueError, TypeError) as exc:
            raise ValueError("Motion reference provider returned an invalid video_url") from exc
        if context["asset_type"] in ("full_body", "head_shot"):
            return VideoVariant(
                id=f"video_{uuid.uuid4().hex[:8]}",
                url=video_url,
                prompt_used=context["options"]["prompt"],
                audio_url=context["options"]["audio_url"],
                source_image_id=None,
            )
        return VideoTask(
            id=f"video_{uuid.uuid4().hex[:8]}",
            project_id=context["script_id"],
            asset_id=context["asset_id"],
            image_url=context["options"]["image_url"],
            prompt=context["options"]["prompt"],
            status="completed",
            video_url=video_url,
            duration=context["options"]["duration"],
            created_at=time.time(),
            generate_audio=bool(context["options"]["audio_url"]),
            model="wan2.6-i2v",
            generation_mode="i2v",
        )

    def _commit_motion_result(self, context, token, result) -> None:
        with self._save_lock:
            target = self._check_motion_ref(context, token)
            if context["asset_type"] in ("full_body", "head_shot"):
                unit = getattr(target, context["asset_type"], None)
                if unit is None:
                    unit = AssetUnit()
                    setattr(target, context["asset_type"], unit)
                unit.video_variants.append(result)
                if not unit.selected_video_id:
                    unit.selected_video_id = result.id
                unit.video_prompt = context["options"]["prompt"]
                unit.video_updated_at = time.time()
            else:
                target.video_assets.append(result)
            target.status = GenerationStatus.COMPLETED
            self._save_after_asset_mutation(context["source"])
            context["expected"] = target.model_dump()

    def _generate_motion_ref_context(self, context, *, token=None) -> int:
        token = self._begin_motion_ref(context) if token is None else token
        generated = 0
        try:
            for _ in range(context["options"]["batch_size"]):
                self._check_motion_ref(context, token)
                try:
                    video_result = self.video_generator.generate_i2v(
                        image_url=context["options"]["image_url"],
                        prompt=context["options"]["prompt"],
                        duration=context["options"]["duration"],
                        audio_url=context["options"]["audio_url"],
                    )
                    if not video_result or not video_result.get("video_url"):
                        raise ValueError("Motion reference provider returned no video_url")
                    result = self._motion_result_payload(context, video_result["video_url"])
                except StorageError:
                    raise
                except Exception as exc:
                    logger.error(f"Failed to generate motion ref video for {context['asset_type']}: {exc}")
                    continue
                self._commit_motion_result(context, token, result)
                generated += 1
            if generated == 0:
                self._finish_motion_ref_status(context, token, GenerationStatus.FAILED)
                raise RuntimeError(f"Failed to generate any motion reference videos for {context['asset_type']}")
            return generated
        finally:
            self._release_motion_ref(context, token)

    def generate_motion_ref(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        prompt: Optional[str] = None,
        audio_url: Optional[str] = None,
        duration: int = 5,
        batch_size: int = 1,
    ) -> Script:
        """Generate Motion Reference video for an asset and return a committed snapshot."""
        params = {"prompt": prompt, "audio_url": audio_url, "duration": duration, "batch_size": batch_size}
        with self._save_lock:
            context = self._prepare_motion_ref(self.read_committed_project_view(script_id), asset_id, asset_type, params)
        self._generate_motion_ref_context(context)
        return self.read_committed_script(script_id)

    def generate_storyboard_render(self, script_id: str, frame_id: str, composition_data: Optional[Dict[str, Any]], prompt: str, batch_size: int = 1) -> Script:
        """Step 3b: Render a specific frame from composition data."""
        from .asset_image_execution import AssetImageRequestCommand

        with self._save_lock:
            view = self.read_committed_project_view(script_id)
            context = self._prepare_storyboard_image(
                view, frame_id, composition_data, prompt, batch_size,
            )
            if self._is_core_storyboard_image(context):
                assert view[0] is not None
                admission = self.asset_image_service.admit("project", script_id, AssetImageRequestCommand(
                    client_request_id=f"storyboard-{uuid.uuid4()}", asset_id=frame_id,
                    asset_type="storyboard_frame", generation_type="all", prompt=prompt or "",
                    composition_data=composition_data, expected_revision=view[0].revision,
                ))
            else:
                admission = None
        if admission is not None:
            self.asset_image_service.run_original_request(admission.request.id)
            with self._save_lock:
                self._require_completed_core_asset_image(admission)
                return self.read_committed_script(script_id)
        return self._generate_storyboard_image(context)

    def generate_video(self, script_id: str) -> Script:
        """Legacy script-wide video generation is retired; use video tasks."""
        self.assert_storage_available()
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        raise NotImplementedError("Legacy script-wide video generation is unsupported; use POST /projects/{id}/video_tasks")

    def create_video_task(self, script_id: str, image_url: str, prompt: str, duration: int = 5, seed: int = None, resolution: str = "720p", generate_audio: bool = False, audio_url: str = None, prompt_extend: bool = True, negative_prompt: str = None, model: str = "wan2.7-i2v", frame_id: str = None, shot_type: str = "single", generation_mode: str = "i2v", reference_video_urls: list = None, reference_image_urls: list = None, ratio: str = None, watermark: Optional[bool] = None, mode: str = None, sound: str = None, cfg_scale: float = None, vidu_audio: bool = None, movement_amplitude: str = None, workbench_tab: Optional[str] = None) -> Tuple[Script, str]:
        """Creates a new video generation task."""
        if _is_ordered_wan3_legacy_video_task_model(model):
            raise ValueError("Wan3 ordered-media video models require the unified storyboard workbench and ordered-media-v1 provider video task route")

        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            script_identity = script
            frame_identity = next((f for f in script.frames if f.id == frame_id), None) if frame_id else None
            frame_snapshot = frame_identity.model_copy(deep=True) if frame_identity is not None else None
            frame_dialogue_projection = self._video_frame_dialogue_projection(frame_identity) if frame_identity is not None else None

        task_id = str(uuid.uuid4())

        if generation_mode == "r2v":
            if not (model and model.endswith("-r2v")):
                if model and model.startswith("happyhorse-"):
                    model = "happyhorse-1.1-r2v"
                elif model and model.startswith("wan2.7-"):
                    model = "wan2.7-r2v"
                elif model and model.startswith("kling"):
                    model = "kling-v3-r2v"
                elif model and model.startswith("pixverse"):
                    model = "pixverse-c1-r2v"
                elif model and model.startswith("vidu"):
                    model = "viduq3-pro-r2v"
                elif model and model.startswith("seedance"):
                    model = "seedance-2.0-r2v"
                else:
                    model = "wan2.7-r2v"

        is_r2v_model = isinstance(model, str) and model.endswith("-r2v")
        if is_r2v_model:
            needs_video_refs = model == "wan2.6-r2v"
            refs = (reference_video_urls or []) if needs_video_refs else (reference_image_urls or [])
            if not refs:
                kind = "video" if needs_video_refs else "image"
                raise ValueError(
                    f"Model '{model}' is reference-to-video and requires {kind} references, "
                    f"but none were provided. Attach reference {kind}s (use @ in the prompt "
                    "to reference characters / scenes / props) or switch to an I2V model "
                    "(e.g. wan2.7-i2v)."
                )

        snapshot_url = image_url
        try:
            if image_url and not image_url.startswith("http"):
                src_path = self._output_ref_path(image_url)
                if os.path.exists(src_path) and os.path.isfile(src_path):
                    snapshot_dir = self._output_subdir("video_inputs")
                    ext = os.path.splitext(os.path.basename(image_url))[1] or ".png"
                    _validate_safe_id(task_id, "task_id")
                    snapshot_filename = f"{task_id}{ext}"
                    snapshot_path = _safe_resolve_path(snapshot_dir, snapshot_filename)
                    import shutil
                    shutil.copy2(src_path, snapshot_path)
                    snapshot_url = f"video_inputs/{snapshot_filename}"
        except Exception as exc:
            with self._save_lock:
                if self.scripts.get(script_id) is not script_identity:
                    raise StorageConflictError("Project changed while preparing video task") from exc
            if isinstance(exc, ValueError):
                raise
            logger.error(f"Failed to snapshot input image: {exc}")

        if frame_snapshot and prompt:
            from .prompt_assembly import enrich_prompt_with_dialogue
            prompt = enrich_prompt_with_dialogue(prompt, frame_snapshot)

        task = VideoTask(
            id=task_id,
            project_id=script_id,
            frame_id=frame_id,
            image_url=snapshot_url,
            prompt=prompt,
            status="pending",
            duration=duration,
            seed=seed,
            resolution=resolution,
            generate_audio=generate_audio,
            audio_url=audio_url,
            prompt_extend=prompt_extend,
            negative_prompt=negative_prompt,
            model=model,
            shot_type=shot_type,
            generation_mode=generation_mode,
            reference_video_urls=reference_video_urls or [],
            reference_image_urls=reference_image_urls or [],
            ratio=ratio,
            watermark=watermark,
            mode=mode,
            sound=sound,
            cfg_scale=cfg_scale,
            vidu_audio=vidu_audio,
            movement_amplitude=movement_amplitude,
            workbench_tab=workbench_tab,
            created_at=time.time()
        )

        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is not script_identity:
                raise StorageConflictError("Project changed while creating video task")
            if frame_id and not any(frame is frame_identity for frame in script.frames):
                raise StorageConflictError("Video task frame changed or was deleted")
            if frame_id and self._video_frame_dialogue_projection(frame_identity) != frame_dialogue_projection:
                raise StorageConflictError("Video task frame dialogue changed while creating video task")
            if not script.video_tasks:
                script.video_tasks = []
            script.video_tasks.append(task)
            self._save_data()
            return self.read_committed_script(script_id), task_id

    @staticmethod
    def _frame_image_target_snapshot(frame: StoryboardFrame) -> Dict[str, Any]:
        asset = frame.rendered_image_asset
        variants = list(asset.variants) if asset else []
        selected_id = asset.selected_id if asset else None
        selected_url = next((variant.url for variant in variants if variant.id == selected_id), None)
        return {
            "frame_id": frame.id,
            "selected_id": selected_id,
            "selected_url": selected_url,
            "variant_refs": [{"id": variant.id, "url": variant.url} for variant in variants],
            "rendered_image_url": frame.rendered_image_url,
            "image_url": frame.image_url,
        }

    def _check_frame_image_target(
        self,
        script_id: str,
        frame_id: str,
        script_identity: Script,
        frame_identity: StoryboardFrame,
        expected_target: Dict[str, Any],
    ) -> Tuple[Script, StoryboardFrame]:
        self.assert_storage_available()
        script = self.scripts.get(script_id)
        if script is None or script is not script_identity:
            raise StorageConflictError("Frame image project changed or was deleted")
        frame = next((candidate for candidate in script.frames if candidate.id == frame_id), None)
        if frame is None or frame is not frame_identity:
            raise StorageConflictError("Frame image target changed or was deleted")
        if self._frame_image_target_snapshot(frame) != expected_target:
            raise StorageConflictError("Frame image target changed during processing")
        return script, frame

    def _append_frame_image_variant(
        self,
        script: Script,
        frame: StoryboardFrame,
        image_url: str,
        *,
        prompt_used: str,
    ) -> None:
        from .models import ImageVariant, ImageAsset

        variant = ImageVariant(
            id=str(uuid.uuid4()),
            url=image_url,
            prompt_used=prompt_used,
            is_uploaded_source=True,
            upload_type="image",
        )
        if not frame.rendered_image_asset:
            frame.rendered_image_asset = ImageAsset()
        frame.rendered_image_asset.variants.append(variant)
        frame.rendered_image_asset.selected_id = variant.id
        frame.rendered_image_url = image_url
        script.updated_at = time.time()

    def extract_last_frame(self, script_id: str, frame_id: str, video_task_id: str) -> Script:
        """Extract the last frame from a video task and add it as a variant of the frame's rendered_image_asset."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError("Frame not found")
            video_task = self._find_video_task_for_frame(script, frame_id, video_task_id)
            video_url = video_task.video_url
            script_identity = script
            frame_identity = frame
            task_identity = video_task
            expected_target = self._frame_image_target_snapshot(frame)

        # Resolve video path (managed output/ files, temp downloads, or URLs) outside the command lock.
        video_path = video_url
        if video_path.startswith("http"):
            video_path = self._download_temp_image(video_path)
        elif video_path.startswith("/"):
            resolved = os.path.realpath(video_path)
            out_base = os.path.realpath(str(self.data_dir))
            if not resolved.startswith(out_base + os.sep):
                raise ValueError(f"Video path outside managed output directory: {video_url}")
            video_path = resolved
        else:
            video_path = self._output_ref_path(video_path)

        if not os.path.exists(video_path):
            raise ValueError(f"Video file not found: {video_path}")

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError("FFmpeg is required for frame extraction but was not found.")

        output_dir = self._output_subdir("storyboard")
        safe_frame_id = _validate_safe_id(frame_id, "frame_id")
        output_filename = f"frame_{safe_frame_id}_lastframe_{uuid.uuid4().hex[:8]}.jpg"
        output_path = _safe_resolve_path(output_dir, output_filename)

        cmd = [
            ffmpeg_path, "-sseof", "-0.1",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            "-y", output_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg error: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg frame extraction timed out")

        if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
            raise RuntimeError("Failed to extract a non-empty last frame from video")

        uploader = self.oss_uploader()
        oss_url = uploader.upload_image(output_path)
        image_url = oss_url if oss_url else self._output_relpath(output_path)

        with self._save_lock:
            script, frame = self._check_frame_image_target(
                script_id, frame_id, script_identity, frame_identity, expected_target
            )
            current_task = self._find_video_task_for_frame(script, frame_id, video_task_id)
            if current_task is not task_identity or current_task.video_url != video_url:
                raise StorageConflictError("Video task changed during frame extraction")
            self._append_frame_image_variant(
                script, frame, image_url, prompt_used="Extracted last frame from video"
            )
            self._commit_storage("projects.json")
            return self.read_committed_script(script_id)

    def upload_frame_image(self, script_id: str, frame_id: str, image_path: str) -> Script:
        """Upload an image as a variant of the frame's rendered_image_asset."""
        safe_path = self._output_ref_path(image_path)
        if not os.path.isfile(safe_path) or os.path.getsize(safe_path) <= 0:
            raise ValueError("Uploaded image file not found or empty")

        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError("Frame not found")
            script_identity = script
            frame_identity = frame
            expected_target = self._frame_image_target_snapshot(frame)

        uploader = self.oss_uploader()
        oss_url = uploader.upload_image(safe_path)
        image_url = oss_url if oss_url else self._output_relpath(safe_path)

        with self._save_lock:
            script, frame = self._check_frame_image_target(
                script_id, frame_id, script_identity, frame_identity, expected_target
            )
            self._append_frame_image_variant(
                script, frame, image_url, prompt_used="User uploaded image"
            )
            self._commit_storage("projects.json")
            return self.read_committed_script(script_id)

    def _download_temp_image(self, url: str) -> str:
        """Downloads an image to a temporary file."""
        import requests
        import tempfile

        # If it's a local file path (relative to output)
        if not url.startswith("http"):
            local_path = self._output_ref_path(url)
            if os.path.exists(local_path):
                return local_path

        # Download from URL
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()

            # Create temp file
            fd, path = tempfile.mkstemp(suffix=".png")
            with os.fdopen(fd, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return path
        except Exception as e:
            logger.error(f"Failed to download image: {e}")
            raise
    def select_video_for_frame(self, script_id: str, frame_id: str, video_id: str) -> Script:
        """Manual select: user pins this video as the active take.

        Sets is_video_pinned=True so subsequent auto_select_latest_video
        calls (fired by polling completion) skip this frame and don't
        overwrite the user's hand-picked choice.
        """
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError("Frame not found")

            video = self._find_video_task_for_frame(script, frame_id, video_id)

            frame.selected_video_id = video_id
            frame.video_url = video.video_url
            frame.is_video_pinned = True

            self._save_data()
            return script.model_copy(deep=True)

    def auto_select_latest_video(self, script_id: str, frame_id: str) -> Script:
        """Auto select: pick the latest completed video task for this frame.

        Idempotent. Skips the update entirely if the frame is pinned by the
        user (is_video_pinned=True). Called by the frontend on every task
        completion poll — the pin check is what makes latest-wins respect
        user intent.
        """
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError("Frame not found")

            if frame.is_video_pinned:
                return self.read_committed_script(script_id)  # user has manually pinned — don't overwrite

            # Latest completed task wins. VideoTask carries created_at
            # (default_factory=time.time); we use it as the "completion order"
            # proxy. Backend doesn't track per-task completion time, but tasks
            # in the same batch are queued at roughly the same created_at and
            # complete in arrival order — close enough for "show me what just
            # came out" UX.
            frame_tasks = [
                t for t in script.video_tasks
                if t.frame_id == frame_id
                and t.status == GenerationStatus.COMPLETED
                and t.video_url
            ]
            if not frame_tasks:
                return self.read_committed_script(script_id)  # nothing to select yet

            latest = max(frame_tasks, key=lambda t: getattr(t, "created_at", 0) or 0)
            if frame.selected_video_id == latest.id and frame.video_url == latest.video_url:
                return self.read_committed_script(script_id)  # already selected — no-op

            frame.selected_video_id = latest.id
            frame.video_url = latest.video_url
            # is_video_pinned stays False — this is an auto-select

            self._save_data()
            return self.read_committed_script(script_id)

    def unpin_video(self, script_id: str, frame_id: str) -> Script:
        """Clear the manual pin so auto_select_latest_video resumes.

        Intentionally does NOT touch selected_video_id or video_url — the
        user keeps seeing the same take until the next generation runs
        and auto_select picks a newer one.
        """
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError("Frame not found")

            if not frame.is_video_pinned:
                return self.read_committed_script(script_id)  # already unpinned — no-op

            frame.is_video_pinned = False
            self._save_data()
            return self.read_committed_script(script_id)

    def _resolve_media_path(self, url: str, suffix: str = "") -> Optional[str]:
        """Resolve a media URL to a local file path.

        Handles three cases:
        1. Local relative path (e.g. 'video/xxx.mp4') → resolve under output/
        2. OSS object key (e.g. 'lumenx/videos/xxx.mp4') → sign URL then download
        3. Full HTTP URL → download directly
        """
        if not url:
            return None

        # Case 1: Try as local path first
        if not url.startswith("http"):
            local_path = self._output_ref_path(url)
            if os.path.exists(local_path):
                return local_path
            # Not found locally — might be an OSS object key
            if is_object_key(url, env=self.environment_snapshot()):
                uploader = self.oss_uploader()
                if uploader.is_configured:
                    url = uploader.sign_url_for_api(url)
                else:
                    logger.error(f"[DUB] File not local and OSS not configured: {url}")
                    return None
            else:
                return None

        # Case 2 & 3: Download from HTTP URL
        import hashlib
        url_hash = hashlib.md5(url.split("?")[0].encode()).hexdigest()[:12]
        cache_dir = self._output_subdir("cache")
        cached = os.path.join(cache_dir, f"{url_hash}{suffix}")
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            return cached
        try:
            import requests
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(cached, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            logger.info(f"[DUB] Downloaded remote media -> {cached}")
            return cached
        except Exception as e:
            logger.error(f"[DUB] Failed to download media: {e}")
            if os.path.exists(cached):
                os.remove(cached)
            return None

    def _warmup_demucs_model(self):
        """Pre-download htdemucs model at startup so first dub request is fast."""
        try:
            from demucs.pretrained import get_model
            get_model("htdemucs")
            logger.info("[DUB] Demucs htdemucs model ready")
            self._demucs_ready.set()
        except Exception as e:
            self._demucs_error = str(e)
            self._demucs_ready.set()
            logger.warning(f"[DUB] Demucs model warmup failed: {e}")

    def _separate_background_audio(self, video_path: str, work_dir: str) -> Optional[str]:
        """Extract audio from video and separate background (no_vocals) using Demucs.

        Returns the path to the background audio WAV file, or None if
        separation fails (caller falls back to simple replacement).
        """
        ffmpeg_path = get_ffmpeg_path()
        extracted_audio = os.path.join(work_dir, "original_audio.wav")

        # Step 1: Extract audio from video
        extract_cmd = [
            ffmpeg_path, "-y",
            "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
            extracted_audio,
        ]
        try:
            result = subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if result.returncode != 0 or not os.path.exists(extracted_audio):
                logger.warning("[DUB] No audio track in source video, skipping separation")
                return None
        except Exception as e:
            logger.warning(f"[DUB] Audio extraction failed: {e}")
            return None

        # Check if extracted audio has any content (some videos are silent)
        if os.path.getsize(extracted_audio) < 1000:
            logger.info("[DUB] Source video has negligible audio, skipping separation")
            return None

        # Step 2: Run Demucs separation (two-stems: vocals + no_vocals)
        # Wait for background model warmup to finish (avoids duplicate download)
        if not self._demucs_ready.wait(timeout=120):
            raise RuntimeError("Demucs 模型正在下载中（首次约需30秒），请稍后重试。")

        try:
            import demucs.separate
            demucs.separate.main([
                "--two-stems", "vocals",
                "-n", "htdemucs",
                "--out", work_dir,
                extracted_audio,
            ])
        except Exception as e:
            logger.warning(f"[DUB] Demucs separation failed: {e}, falling back to simple replacement")
            return None

        # Demucs outputs to: {work_dir}/htdemucs/original_audio/no_vocals.wav
        bg_path = os.path.join(work_dir, "htdemucs", "original_audio", "no_vocals.wav")
        if not os.path.exists(bg_path):
            # Try alternate path structures
            for root, dirs, files in os.walk(work_dir):
                if "no_vocals.wav" in files:
                    bg_path = os.path.join(root, "no_vocals.wav")
                    break

        if os.path.exists(bg_path):
            logger.info(f"[DUB] Background audio separated successfully: {bg_path}")
            return bg_path

        logger.warning("[DUB] Demucs output not found, falling back to simple replacement")
        return None

    def _ensure_bg_audio_cached(self, frame, video_path: str, video_url: str) -> Optional[str]:
        """Ensure background audio is separated and cached for this frame's video.

        Returns absolute path to bg audio WAV, or None if video has no audio.
        Caches each result to a unique output/audio/bg_{frame_id}_*.wav; re-runs Demucs
        if video source changed.
        """
        if frame.bg_audio_url and frame.bg_audio_source_video == video_url:
            cached_path = self._output_ref_path(frame.bg_audio_url)
            if os.path.exists(cached_path):
                logger.info(f"[DUB] Background audio cache hit: {frame.bg_audio_url}")
                return cached_path

        import tempfile
        import shutil
        work_dir = tempfile.mkdtemp(prefix="demucs_")
        try:
            bg_path = self._separate_background_audio(video_path, work_dir)
            if not bg_path:
                frame.bg_audio_url = None
                frame.bg_audio_source_video = video_url
                return None

            cache_filename = f"bg_{frame.id}_{uuid.uuid4().hex[:12]}.wav"
            cache_path = _safe_resolve_path(self._output_subdir("audio"), cache_filename)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            shutil.copy2(bg_path, cache_path)

            frame.bg_audio_url = f"audio/{cache_filename}"
            frame.bg_audio_source_video = video_url
            logger.info(f"[DUB] Background audio cached: {cache_filename}")
            return cache_path
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _find_frame(self, script: Script, frame_id: str) -> StoryboardFrame:
        frame = next((candidate for candidate in script.frames if candidate.id == frame_id), None)
        if frame is None:
            raise ValueError(f"Frame {frame_id} not found")
        return frame

    def _find_video_task_for_frame(self, script: Script, frame_id: str, video_task_id: str) -> VideoTask:
        task = next((candidate for candidate in script.video_tasks if candidate.id == video_task_id), None)
        if task is None or not task.video_url or not task.video_url.strip():
            raise ValueError(f"Video task {video_task_id} not found or has no video_url")
        if task.project_id != script.id or task.frame_id != frame_id:
            raise ValueError(f"Video task {video_task_id} does not belong to frame {frame_id}")
        if task.status != "completed":
            raise ValueError(f"Video task {video_task_id} is not completed")
        return task

    def _effective_video_task_for_frame(self, frame: StoryboardFrame, tasks: List[VideoTask]) -> Optional[VideoTask]:
        frame_tasks = [task for task in tasks if task.frame_id == frame.id and task.video_url and task.status == "completed"]
        if frame.selected_video_id:
            return next((task for task in frame_tasks if task.id == frame.selected_video_id), None)
        return frame_tasks[0] if frame_tasks else None

    def _dub_source_matches(
        self,
        source: object,
        task: Optional[VideoTask],
        audio_url: Optional[str],
    ) -> bool:
        return (
            isinstance(source, DubSource)
            and task is not None
            and source.task_id == task.id
            and source.video_url == task.video_url
            and source.audio_url == audio_url
        )

    def resolve_frame_video_for_render(
        self,
        frame: StoryboardFrame,
        video_tasks: List[VideoTask],
        *,
        allow_preview: bool = False,
    ) -> Optional[str]:
        """Return current video URL; use dub only when v1 provenance still matches."""
        task = self._effective_video_task_for_frame(frame, video_tasks)
        if task is None:
            return None
        if (
            allow_preview
            and frame.preview_video_url
            and self._dub_source_matches(frame.dub_preview_source, task, frame.audio_url)
            and os.path.exists(self._output_ref_path(frame.preview_video_url))
        ):
            return frame.preview_video_url
        if (
            frame.dubbed_video_url
            and self._dub_source_matches(frame.dub_applied_source, task, frame.audio_url)
            and os.path.exists(self._output_ref_path(frame.dubbed_video_url))
        ):
            return frame.dubbed_video_url
        return task.video_url

    def preview_dub(self, script_id: str, frame_id: str, video_task_id: str, offset_ms: int = 0) -> "Script":
        """Generate a preview dubbed video (Demucs cached + fast adelay+amix+mux).

        Replaces any existing preview_video_url (lazy cleanup).
        Does NOT touch dubbed_video_url.
        """
        self.assert_storage_available()
        _validate_safe_id(script_id, "script_id")
        if type(offset_ms) is not int or offset_ms < 0:
            raise ValueError("offset_ms must be a non-negative integer")
        script = self.read_committed_script(script_id)
        frame = self._find_frame(script, frame_id)

        if not frame.audio_url:
            raise ValueError("Frame has no TTS audio (audio_url). Generate dialogue audio first.")

        video_task = self._find_video_task_for_frame(script, frame_id, video_task_id)

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError("FFmpeg is required for audio dubbing but was not found.")

        video_path = self._resolve_media_path(video_task.video_url, suffix=".mp4")
        tts_path = self._resolve_media_path(frame.audio_url, suffix=".mp3")

        if not video_path or not os.path.exists(video_path):
            raise ValueError(f"Video file not found: {video_task.video_url}")
        if not tts_path or not os.path.exists(tts_path):
            raise ValueError(f"Audio file not found: {frame.audio_url}")
        if os.path.getsize(tts_path) < 1000:
            raise ValueError("TTS audio file is invalid or empty. Please regenerate dialogue audio.")

        # frame.id comes from the store (== frame_id), keeping ffmpeg args taint-free.
        output_filename = f"preview_{frame.id}_{uuid.uuid4().hex[:12]}.mp4"
        output_path = _safe_resolve_path(self._output_subdir("video"), output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Ensure background audio is cached (Demucs runs only on first call or video change)
        bg_audio_path = self._ensure_bg_audio_cached(frame, video_path, video_task.video_url)

        import tempfile
        work_dir = tempfile.mkdtemp(prefix="dub_mix_")
        try:
            if bg_audio_path:
                mixed_audio = os.path.join(work_dir, "mixed.wav")
                delay_str = f"{offset_ms}|{offset_ms}"

                mix_cmd = [
                    ffmpeg_path, "-y",
                    "-i", bg_audio_path,
                    "-i", tts_path,
                    "-filter_complex",
                    f"[1:a]adelay={delay_str}[tts];[0:a][tts]amix=inputs=2:duration=first:weights=1 1[out]",
                    "-map", "[out]",
                    "-ac", "2", "-ar", "44100",
                    mixed_audio,
                ]

                logger.info(f"[DUB] Mixing TTS with background (adelay={offset_ms}ms)")
                subprocess.run(mix_cmd, check=True, capture_output=True, timeout=60)

                if not os.path.exists(mixed_audio):
                    raise RuntimeError("Audio mixing failed: output file not created")

                mux_cmd = [
                    ffmpeg_path, "-y",
                    "-i", video_path,
                    "-i", mixed_audio,
                    "-map", "0:v",
                    "-map", "1:a",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    output_path,
                ]
                subprocess.run(mux_cmd, check=True, capture_output=True, timeout=60)
            else:
                delay_str = f"{offset_ms}|{offset_ms}"
                cmd = [
                    ffmpeg_path, "-y",
                    "-i", video_path,
                    "-i", tts_path,
                    "-filter_complex",
                    f"[1:a]adelay={delay_str}[tts];[tts]apad[out]",
                    "-map", "0:v",
                    "-map", "[out]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",  # End padded silence with the selected video.
                    "-movflags", "+faststart",
                    output_path,
                ]
                logger.info(f"[DUB] Simple replacement with adelay={offset_ms}ms")
                subprocess.run(cmd, check=True, capture_output=True, timeout=120)

        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else "No error output"
            logger.error(f"[DUB] FFmpeg failed: {stderr_msg[:400]}")
            raise RuntimeError(f"Audio dubbing failed: {stderr_msg[:200]}")
        finally:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)

        if not os.path.exists(output_path):
            raise RuntimeError("Preview video was not created")

        preview_url = f"video/{output_filename}"
        source = DubSource(
            schema_version=1,
            task_id=video_task.id,
            video_url=video_task.video_url,
            audio_url=frame.audio_url,
            offset_ms=offset_ms,
        )
        with self._save_lock:
            current = self._check_project_result(script)
            current_frame = self._find_frame(current, frame_id)
            current_task = self._find_video_task_for_frame(current, frame_id, video_task_id)
            if (
                current_frame.selected_video_id != frame.selected_video_id
                or current_frame.audio_url != frame.audio_url
                or current_task.video_url != video_task.video_url
                or current_task.status != video_task.status
            ):
                raise StorageConflictError("Dub preview inputs changed while generating; reload before retrying")
            current_frame.preview_video_url = preview_url
            current_frame.dub_preview_source = source
            current_frame.bg_audio_url = frame.bg_audio_url
            current_frame.bg_audio_source_video = frame.bg_audio_source_video
            # Preview offset is stored only in dub_preview_source. dub_offset_ms
            # remains the applied compatibility offset until apply_dub promotes
            # the preview.
            self._save_data()

        logger.info(f"[DUB] Preview generated: {output_filename}")
        return self.read_committed_script(script_id)

    def apply_dub(self, script_id: str, frame_id: str) -> "Script":
        """Promote preview_video_url to dubbed_video_url."""
        self.assert_storage_available()
        _validate_safe_id(script_id, "script_id")
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            frame = self._find_frame(script, frame_id)
            if not frame.preview_video_url:
                raise ValueError("No preview to apply. Generate a preview first.")
            if not isinstance(frame.dub_preview_source, DubSource):
                raise StorageConflictError("Dub preview provenance is unknown; regenerate the preview before applying")
            task = self._effective_video_task_for_frame(frame, script.video_tasks)
            if not self._dub_source_matches(frame.dub_preview_source, task, frame.audio_url):
                raise StorageConflictError("Dub preview no longer matches the selected video/audio inputs")
            if not os.path.exists(self._output_ref_path(frame.preview_video_url)):
                raise ValueError("Preview video file is missing. Generate a preview first.")
            frame.dubbed_video_url = frame.preview_video_url
            frame.dub_applied_source = frame.dub_preview_source
            frame.dubbed_video_task_id = frame.dub_preview_source.task_id
            frame.dub_offset_ms = frame.dub_preview_source.offset_ms
            frame.preview_video_url = None
            frame.dub_preview_source = None
            self._save_data()

        logger.info(f"[DUB] Applied: {frame.dubbed_video_url}")
        return self.read_committed_script(script_id)

    def revert_dub(self, script_id: str, frame_id: str) -> "Script":
        """Revert dubbing — clear dubbed and preview, keep bg cache."""
        self.assert_storage_available()
        _validate_safe_id(script_id, "script_id")
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            frame = self._find_frame(script, frame_id)
            frame.dubbed_video_url = None
            frame.preview_video_url = None
            frame.dub_applied_source = None
            frame.dub_preview_source = None
            frame.dub_offset_ms = 0
            frame.dubbed_video_task_id = None
            self._save_data()
        return self.read_committed_script(script_id)

    @staticmethod
    def _normalize_merge_text(value: Any) -> Optional[str]:
        if not isinstance(value, str) or not value.strip():
            return None
        return value

    @staticmethod
    def _normalize_merge_bgm_ref(value: Any) -> Tuple[bool, Optional[str]]:
        if value is None:
            return True, None
        if not isinstance(value, str) or not value.strip():
            return False, None
        return True, value

    @staticmethod
    def _normalize_merge_mix_settings(value: Any) -> Optional[Dict[str, int]]:
        defaults = {"dialogue": 100, "bgm": 35, "sfx": 60}
        if not isinstance(value, dict) or set(value.keys()) != set(defaults.keys()):
            return None
        normalized: Dict[str, int] = {}
        for key in defaults:
            level = value.get(key)
            if type(level) is not int or level < 0 or level > 100:
                return None
            normalized[key] = level
        return normalized

    def _merge_source_projection(self, script: Script) -> Dict[str, Any]:
        frames: List[List[str]] = []
        for frame in script.frames:
            frame_id = self._normalize_merge_text(frame.id)
            video_ref = self._normalize_merge_text(self.resolve_frame_video_for_render(frame, script.video_tasks))
            if frame_id is None or video_ref is None:
                raise ValueError("Merge source contains an invalid frame/video reference")
            frames.append([frame_id, video_ref])

        bgm_valid, bgm_url = self._normalize_merge_bgm_ref(script.bgm_url)
        mix_settings = self._normalize_merge_mix_settings(script.mix_settings)
        if not bgm_valid or mix_settings is None:
            raise ValueError("Merge source contains invalid audio mix settings")

        return {
            "schema_version": 1,
            "frames": frames,
            "bgm_url": bgm_url,
            "mix_settings": mix_settings,
        }

    def _merge_input_projection(self, script: Script) -> Dict[str, Any]:
        return {
            "source": self._merge_source_projection(script),
            "merged_video_url": script.merged_video_url,
        }

    @classmethod
    def _normalize_merge_source(cls, source: Any) -> Tuple[Optional[Dict[str, Any]], str]:
        if source is None:
            return None, "missing_source"
        if not isinstance(source, dict):
            return None, "invalid_source"
        schema_version = source.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            return None, "unknown_source_schema" if "schema_version" in source and type(schema_version) is int else "invalid_source"
        frames = source.get("frames")
        mix_settings = cls._normalize_merge_mix_settings(source.get("mix_settings"))
        bgm_valid, bgm_url = cls._normalize_merge_bgm_ref(source.get("bgm_url")) if "bgm_url" in source else (False, None)
        if not isinstance(frames, list) or not frames or mix_settings is None or not bgm_valid:
            return None, "invalid_source"

        normalized_frames: List[List[str]] = []
        for item in frames:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return None, "invalid_source"
            frame_id = cls._normalize_merge_text(item[0])
            media_ref = cls._normalize_merge_text(item[1])
            if frame_id is None or media_ref is None:
                return None, "invalid_source"
            normalized_frames.append([frame_id, media_ref])

        return {
            "schema_version": 1,
            "frames": normalized_frames,
            "bgm_url": bgm_url,
            "mix_settings": mix_settings,
        }, "current"

    def merge_video_freshness(self, script: Script) -> Dict[str, Any]:
        base = {"state": "unknown", "reason": "no_merged_video", "project_revision": script.revision}
        if not script.merged_video_url:
            return base

        stored_source, reason = self._normalize_merge_source(getattr(script, "merged_video_source", None))
        if stored_source is None:
            return {"state": "unknown", "reason": reason, "project_revision": script.revision}

        try:
            merged_output = self._output_ref_path(script.merged_video_url)
            output = Path(merged_output)
            if (output.parent != self.data_dir / "video"
                or not output.name.startswith(f"merged_{script.id}_")
                or output.suffix != ".mp4"):
                return {"state": "unknown", "reason": "invalid_output", "project_revision": script.revision}
            if not os.path.isfile(merged_output) or os.path.getsize(merged_output) <= 0:
                return {"state": "stale", "reason": "output_missing", "project_revision": script.revision}
            current_source = self._merge_source_projection(script)
            self._merge_input_paths(script)
        except ValueError:
            return {"state": "stale", "reason": "inputs_invalid", "project_revision": script.revision}

        if current_source == stored_source:
            return {"state": "current", "reason": "current", "project_revision": script.revision}
        return {"state": "stale", "reason": "inputs_changed", "project_revision": script.revision}

    def _merge_input_paths(self, script: Script) -> List[str]:
        paths: List[str] = []
        for index, frame in enumerate(script.frames, start=1):
            if frame.selected_video_id:
                selected = next((
                    task for task in script.video_tasks
                    if task.id == frame.selected_video_id
                    and task.frame_id == frame.id
                    and task.video_url
                    and task.status == "completed"
                ), None)
                if selected is None:
                    raise ValueError(
                        f"Frame {frame.id} (#{index}) selected video {frame.selected_video_id} is missing, incomplete, or no longer belongs to this frame"
                    )

            video_ref = self.resolve_frame_video_for_render(frame, script.video_tasks)
            if not video_ref:
                raise ValueError(f"Frame {frame.id} (#{index}) has no completed video selected for merge")
            if video_ref.startswith("http"):
                raise ValueError(f"Frame {frame.id} (#{index}) video is not a managed local file and cannot be merged")
            resolved = self._output_ref_path(video_ref)
            if not os.path.isfile(resolved):
                raise ValueError(f"Frame {frame.id} (#{index}) selected video file is missing: {video_ref}")
            paths.append(resolved)
        if not paths:
            raise ValueError("No videos selected to merge. Please select videos for each frame first.")
        return paths

    def merge_videos(self, script_id: str) -> Script:
        """Render a private snapshot, then publish only while its inputs still match."""
        _validate_safe_id(script_id, "script_id")
        with self._save_lock:
            self.assert_storage_available()
            identity = self.scripts.get(script_id)
            if identity is None:
                raise ValueError("Script not found")
            frame_identities = tuple(identity.frames)
            script = self.read_committed_script(script_id)
            self._merge_input_paths(script)
            expected = self._merge_input_projection(script)

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError(
                "FFmpeg is required for video merging but was not found.\n\n"
                f"{get_ffmpeg_install_instructions()}\n\n"
                "After installation, restart the application."
            )

        abs_video_paths = self._merge_input_paths(script)
        run_id = uuid.uuid4().hex
        list_path = self._output_path(f"merge_list_{script.id}_{run_id}.txt")
        output_filename = f"merged_{script.id}_{run_id}.mp4"
        output_path = _safe_resolve_path(self._output_subdir("video"), output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        cmd = [
            ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-c:a", "aac",
            "-b:a", "128k", "-movflags", "+faststart", output_path,
        ]
        try:
            with open(list_path, "w") as handle:
                for path in abs_video_paths:
                    handle.write(f"file '{path}'\n")
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
                raise RuntimeError("Video merge did not create a non-empty output file")

            mixed_path = self._maybe_apply_bgm_mux(script, output_path, ffmpeg_path)
            if mixed_path:
                os.replace(mixed_path, output_path)
            if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
                raise RuntimeError("Video merge did not create a non-empty output file")

            with self._save_lock:
                self.assert_storage_available()
                current = self.scripts.get(script_id)
                if (current is not identity
                    or len(current.frames) != len(frame_identities)
                    or any(a is not b for a, b in zip(current.frames, frame_identities))
                    or self._merge_input_projection(current) != expected):
                    raise StorageConflictError("Merge inputs changed during rendering")
                current.merged_video_url = self._output_relpath(output_path)
                current.merged_video_source = deepcopy(expected["source"])
                self._commit_storage("projects.json")
                return self.read_committed_script(script_id)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg timed out. The videos may be too large.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            logger.error(f"[MERGE] FFmpeg failed: {stderr}")
            raise RuntimeError(self._extract_ffmpeg_error_message(stderr, abs_video_paths)) from exc
        finally:
            if os.path.exists(list_path):
                os.remove(list_path)

    def _maybe_apply_bgm_mux(
        self,
        script: Script,
        video_path: str,
        ffmpeg_path: str,
    ) -> Optional[str]:
        """Apply configured merge audio levels to the already-merged video.

        Dialogue volume is honored even when no BGM is configured. When a
        BGM reference is configured, it is part of the accepted merge output:
        missing media or mux failure must fail the merge instead of silently
        publishing an unmixed video. SFX remains out of scope until real SFX
        tracks exist.
        """
        mix = script.mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60}
        dial = max(0, min(100, int(mix.get("dialogue", 100)))) / 100.0
        bgm_lvl = max(0, min(100, int(mix.get("bgm", 35)))) / 100.0
        bgm_rel = (script.bgm_url or "").strip()

        if not bgm_rel and dial == 1.0:
            return None

        mixed_path = video_path.replace(".mp4", "_mixed.mp4")
        if not bgm_rel:
            cmd = [
                ffmpeg_path, "-y",
                "-i", video_path,
                "-map", "0:v", "-map", "0:a:0?",
                "-af", f"volume={dial:.3f}",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                mixed_path,
            ]
            logger.info(f"[MERGE/MIX] applying dialogue volume dial={dial:.2f}")
            self._run_merge_mux_command(cmd, mixed_path, "dialogue volume mix")
            return mixed_path

        bgm_abs = self._output_ref_path(bgm_rel)
        if not os.path.exists(bgm_abs):
            raise RuntimeError(f"BGM file is missing: {bgm_rel}")

        if not self._merge_video_has_audio(video_path, ffmpeg_path):
            cmd = [
                ffmpeg_path, "-y", "-i", video_path,
                "-stream_loop", "-1", "-i", bgm_abs,
                "-map", "0:v", "-map", "1:a:0", "-af", f"volume={bgm_lvl:.3f}",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart", mixed_path,
            ]
            self._run_merge_mux_command(cmd, mixed_path, "BGM mux")
            return mixed_path

        # -stream_loop -1 loops BGM until shortest (the video) ends.
        # apad on the dialogue side avoids amix cutting early on silence.
        filter_complex = (
            f"[0:a]volume={dial:.3f},apad[a0];"
            f"[1:a]volume={bgm_lvl:.3f},aloop=loop=-1:size=2e9[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            ffmpeg_path, "-y",
            "-i", video_path,
            "-stream_loop", "-1", "-i", bgm_abs,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            mixed_path,
        ]
        logger.info(f"[MERGE/BGM] muxing BGM dial={dial:.2f} bgm={bgm_lvl:.2f} — {os.path.basename(bgm_abs)}")
        self._run_merge_mux_command(cmd, mixed_path, "BGM mux")
        return mixed_path

    def _merge_video_has_audio(self, video_path: str, ffmpeg_path: str) -> bool:
        """Probe the first audio stream using the same bundled FFmpeg binary.

        Only a missing stream is absence. Corrupt input, timeouts or other
        failures must stop the merge instead of dropping existing dialogue.
        """
        try:
            result = subprocess.run([
                ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", video_path,
                "-map", "0:a:0", "-c", "copy", "-t", "0", "-f", "null", "-",
            ], capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Merge audio stream check timed out") from exc
        if result.returncode == 0:
            return True
        error = result.stderr.decode(errors="replace") if result.stderr else ""
        # Some FFmpeg releases consume the specifier before formatting this
        # diagnostic, leaving empty quotes. This command has exactly one map.
        if re.search(r"(?m)^Stream map ['\"](?:0:a:0)?['\"] matches no streams\.", error):
            return False
        raise RuntimeError(f"Merge audio stream check failed: {error[:400]}")

    def _run_merge_mux_command(self, cmd: List[str], mixed_path: str, label: str) -> None:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{label} timed out") from exc
        except subprocess.CalledProcessError as exc:
            stderr_msg = exc.stderr.decode(errors="replace") if exc.stderr else ""
            logger.warning(f"[MERGE/MIX] {label} ffmpeg failed: {stderr_msg[:400]}")
            raise RuntimeError(f"{label} failed: {stderr_msg[:400] or 'ffmpeg exited with an error'}") from exc
        if not os.path.exists(mixed_path) or os.path.getsize(mixed_path) <= 0:
            raise RuntimeError(f"{label} failed to create a non-empty output file")

    def _extract_ffmpeg_error_message(self, stderr: str, video_paths: List[str]) -> str:
        """
        Extract a user-friendly error message from ffmpeg stderr output.

        Args:
            stderr: The stderr output from ffmpeg
            video_paths: List of video file paths that were being processed

        Returns:
            A user-friendly error message
        """
        if not stderr:
            return "FFmpeg merge failed with no error output. Please check the log files."

        stderr_lower = stderr.lower()

        # Common error patterns with user-friendly messages
        if "no such file or directory" in stderr_lower:
            return (
                "One or more video files could not be found.\n"
                "The videos may have been deleted or moved.\n"
                "Please try regenerating the missing videos."
            )

        if "invalid data found" in stderr_lower or "invalid file" in stderr_lower or "moov atom not found" in stderr_lower:
            return (
                "One or more video files are corrupted or incomplete.\n"
                "This can happen if video generation was interrupted.\n"
                "Please try regenerating the affected videos."
            )

        if ("codec" in stderr_lower and ("not supported" in stderr_lower or "unknown" in stderr_lower)):
            return (
                "Video codec compatibility issue detected.\n"
                "The video format may not be supported by your FFmpeg installation.\n"
                "Try updating FFmpeg to the latest version."
            )

        if "permission denied" in stderr_lower or "access is denied" in stderr_lower:
            return (
                "Permission denied when accessing video files.\n"
                "Please check that the application has read/write permissions\n"
                "for the output directory."
            )

        if "disk full" in stderr_lower or "no space" in stderr_lower:
            return (
                "Insufficient disk space to create the merged video.\n"
                "Please free up some space and try again."
            )

        if "height not divisible" in stderr_lower or "width not divisible" in stderr_lower:
            return (
                "Video resolution compatibility issue.\n"
                "The videos have incompatible dimensions.\n"
                "This should not happen - please report this issue."
            )

        if "invalid argument" in stderr_lower:
            # Check if it's related to file list
            if any("filelist" in line.lower() or "concat" in line.lower() for line in stderr.split('\n')):
                return (
                    "FFmpeg could not read the video file list.\n"
                    "This might be a file path encoding issue.\n"
                    "Please ensure video filenames don't contain special characters."
                )

        # Fallback: extract the most relevant error line
        # Usually the last non-empty line before the final summary
        error_lines = [line.strip() for line in stderr.split('\n') if line.strip()]
        if error_lines:
            # Look for lines that seem like actual errors (contain "error", "failed", etc.)
            for line in reversed(error_lines):
                line_lower = line.lower()
                if any(keyword in line_lower for keyword in ['error', 'failed', 'invalid', 'cannot', 'unable']):
                    # Truncate if too long
                    if len(line) > 200:
                        line = line[:200] + "..."
                    return f"FFmpeg error: {line}\n\nPlease check the application logs for more details."

            # If no error keyword found, use last line
            last_line = error_lines[-1]
            if len(last_line) > 200:
                last_line = last_line[:200] + "..."
            return f"FFmpeg merge failed: {last_line}\n\nPlease check the application logs for more details."

        return "FFmpeg merge failed with unknown error. Please check the application logs for details."

    def create_asset_video_task(self, script_id: str, asset_id: str, asset_type: str, prompt: str, duration: int = 5, aspect_ratio: str = None) -> Tuple[Script, str]:
        """Creates a new video generation task for an asset (R2V)."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if not target_asset or not source:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

            image_url = getattr(target_asset, "image_url", None)
            if not image_url and asset_type == "character":
                image_url = target_asset.full_body_image_url or target_asset.avatar_url
            if not image_url:
                raise ValueError("Asset has no reference image")

            if prompt:
                target_asset.video_prompt = prompt

            task_id = str(uuid.uuid4())
            task = VideoTask(
                id=task_id,
                project_id=script_id,
                asset_id=asset_id,
                image_url=image_url,
                prompt=prompt or f"Cinematic shot of {target_asset.name}",
                status="pending",
                duration=duration,
                model=script.model_settings.r2v_model if hasattr(script.model_settings, 'r2v_model') and script.model_settings.r2v_model else "wan2.7-r2v",
                generation_mode="r2v",
                created_at=time.time()
            )

            if not script.video_tasks:
                script.video_tasks = []
            script.video_tasks.append(task)
            if not target_asset.video_assets:
                target_asset.video_assets = []
            target_asset.video_assets.append(task)

            storage_names = ["projects.json"]
            if source == "series":
                storage_names.append("series.json")
            elif source == "global":
                storage_names.append("library_assets.json")
            self._commit_storage(*storage_names)
            return self.read_committed_script(script_id), task_id

    def _provider_runtime_or_none(self):
        runtime = getattr(self, "provider_recovery_runtime", None)
        if runtime is None:
            return None
        return self._validate_provider_runtime(runtime, expected_gateway="dashscope")

    def _validate_provider_runtime(self, runtime, *, expected_gateway: str | None = None):
        if not isinstance(runtime, provider_recovery.StudioProviderRecoveryRuntime):
            raise StorageConflictError("Provider recovery runtime is incomplete")
        if runtime.scope.provider != "dashscope" or runtime.scope.backend != "dashscope":
            raise StorageConflictError("Provider recovery runtime has unsupported scope")
        if expected_gateway is not None and runtime.scope.gateway != expected_gateway:
            raise StorageConflictError("Provider recovery runtime has unsupported scope")
        return runtime

    def _provider_model_metadata(self, provider_model_id: str) -> tuple[str, str]:
        catalog = get_catalog_accessor()
        canonical_model_id = catalog.resolve_legacy_to_canonical(provider_model_id) or provider_model_id
        entry = catalog.get_mode_entry(canonical_model_id)
        legacy_model_id = entry.get("legacy_model_id") if entry else None
        gateway = catalog.get_gateway(canonical_model_id, "dashscope")
        if gateway is None and provider_model_id in {"wan2.6-i2v", "wan2.6-i2v-flash", "wan2.7-i2v"}:
            gateway = "dashscope"
        if legacy_model_id != provider_model_id or not isinstance(gateway, str) or not gateway:
            raise ValueError("provider_model_runtime_metadata_missing")
        return provider_model_id, gateway

    def _provider_runtime_for_admission(self, *, provider_model_id: str, gateway: str):
        selector = getattr(self, "provider_runtime_selector", None)
        if selector is None:
            current = getattr(self, "provider_recovery_runtime", None)
            if current is None:
                raise ValueError("provider_recovery_runtime_missing")
            if getattr(getattr(current, "scope", None), "gateway", None) != gateway:
                raise ValueError("unsupported_dashscope_gateway")
            if gateway == "dashscope" and provider_model_id not in {"wan2.6-i2v", "wan2.6-i2v-flash", "wan2.7-i2v"}:
                raise ValueError("unsupported_provider_model")
            return self._validate_provider_runtime(current, expected_gateway=gateway)
        result = selector.for_admission(provider_model_id=provider_model_id, gateway=gateway)
        if result.runtime is None:
            raise ValueError(result.reason or "provider_recovery_runtime_missing")
        return self._validate_provider_runtime(result.runtime, expected_gateway=gateway)

    def _provider_runtime_for_scope(self, scope):
        provider_scope = provider_recovery.ProviderScope.from_dict(scope) if isinstance(scope, dict) else scope
        current = getattr(self, "provider_recovery_runtime", None)
        if current is not None and getattr(current, "scope", None) == provider_scope:
            return self._validate_provider_runtime(current, expected_gateway=provider_scope.gateway)
        selector = getattr(self, "provider_runtime_selector", None)
        if selector is not None:
            result = selector.for_scope(provider_scope)
            if result.runtime is None:
                raise ValueError(result.reason or "provider_recovery_runtime_missing")
            return self._validate_provider_runtime(result.runtime, expected_gateway=provider_scope.gateway)
        raise ValueError("provider_recovery_runtime_missing")

    def admit_provider_video_task(self, script_id: str, request: Dict[str, Any]) -> Tuple[Script, str]:
        """Internal compatibility entry without a client replay identity.

        External paid entrances must use the receipt-bearing entries below.
        """
        result = self._admit_provider_video_tasks(script_id, request, client_request_id=None, batch_size=1)
        return result.script, result.task_ids[0]

    def admit_provider_video_task_with_receipt(
        self, script_id: str, request: Dict[str, Any], *, client_request_id: str,
    ) -> provider_recovery.StudioProviderAdmissionResult:
        """Persist one caller request and task atomically, without submitting."""
        result = self._admit_provider_video_tasks(
            script_id,
            request,
            client_request_id=validate_client_request_id(client_request_id),
            batch_size=1,
        )
        member = result.members[0]
        return provider_recovery.StudioProviderAdmissionResult(
            script=result.script,
            task_id=member.task_id,
            attempt_id=member.attempt_id,
            execution_fingerprint=member.execution_fingerprint,
            client_request_id=result.client_request_id,
            replayed=result.replayed,
            new_task_ids=result.new_task_ids,
            reason=result.reason,
        )

    def admit_provider_video_tasks_with_receipt(
        self, script_id: str, request: Dict[str, Any], *, client_request_id: str, batch_size: int,
    ) -> provider_recovery.StudioProviderBatchAdmissionResult:
        """Persist one caller request and a batch of provider tasks atomically."""
        return self._admit_provider_video_tasks(
            script_id,
            request,
            client_request_id=validate_client_request_id(client_request_id),
            batch_size=self._validate_provider_batch_size(batch_size),
        )

    def _validate_provider_batch_size(self, batch_size: int) -> int:
        if type(batch_size) is not int or not 1 <= batch_size <= 6:
            raise ValueError("provider_batch_size_invalid")
        return batch_size

    def _provider_request_raw(self, request: Dict[str, Any], batch_size: int) -> Dict[str, Any]:
        raw = deepcopy(dict(request))
        if "batch_size" in raw:
            raise ValueError("provider_request_batch_size_transport_field")
        if batch_size != 1:
            raw["batch_size"] = batch_size
        return raw

    def _provider_receipt_result_members(self, receipt: ProviderRequestReceipt | ProviderBatchRequestReceipt) -> tuple[provider_recovery.StudioProviderReceiptMember, ...]:
        return tuple(
            provider_recovery.StudioProviderReceiptMember(
                task_id=member.task_id,
                attempt_id=member.attempt_id,
                execution_fingerprint=member.execution_fingerprint,
            )
            for member in receipt.receipt_members
        )

    def _replay_provider_request(self, script: Script, raw: Dict[str, Any], client_request_id: str):
        matches = [receipt for receipt in script.provider_request_receipts if (
            not isinstance(receipt, MotionReferenceReceipt) and receipt.client_request_id == client_request_id
        )]
        if len(matches) > 1:
            raise StorageConflictError("provider_request_receipt_invalid")
        if not matches:
            return None
        receipt = matches[0]
        if receipt.entrypoint not in {
            provider_recovery.STUDIO_VIDEO_ENTRYPOINT, provider_recovery.STUDIO_ORDERED_VIDEO_ENTRYPOINT,
        }:
            raise StorageConflictError("provider_request_receipt_invalid")
        digest = fingerprint_request(
            raw, entrypoint=receipt.entrypoint, contract_version=receipt.request_contract_version,
        )
        if digest != receipt.request_fingerprint:
            raise StorageConflictError("provider_request_conflict")
        members = self._provider_receipt_result_members(receipt)
        tasks_by_id = {item.id: item for item in script.video_tasks}
        reason = None
        for member in members:
            task = tasks_by_id.get(member.task_id)
            if task is None:
                reason = "provider_request_deleted"
                continue
            record = task.provider_execution
            if (
                task.project_id != script.id or task.frame_id != receipt.frame_id
                or record is None or record.attempt_id != member.attempt_id
                or record.fingerprint != member.execution_fingerprint
            ):
                raise StorageConflictError("provider_request_receipt_invalid")
        if reason is None and not any(frame.id == receipt.frame_id for frame in script.frames):
            reason = "provider_request_owner_deleted"
        return provider_recovery.StudioProviderBatchAdmissionResult(
            script=self.read_committed_script(script.id),
            task_ids=tuple(member.task_id for member in members),
            members=members,
            client_request_id=client_request_id,
            replayed=True,
            new_task_ids=(),
            reason=reason,
        )

    def _provider_video_task_from_record(
        self,
        *,
        script_id: str,
        frame_id: str,
        raw: Dict[str, Any],
        task_id: str,
        record,
        created_at: float,
    ) -> VideoTask:
        intent_params = record.intent.get("params", {})
        return VideoTask(
            id=task_id,
            project_id=script_id,
            frame_id=frame_id,
            asset_id=None,
            image_url=raw.get("image_url", ""),
            prompt=record.intent["prompt"],
            last_frame_url=raw.get("last_frame_url"),
            reference_audio_urls=raw.get("reference_audio_urls", [raw["audio_url"]] if "audio_url" in raw else []),
            status="pending",
            duration=int(intent_params["duration"]),
            seed=int(intent_params["seed"]) if "seed" in intent_params else None,
            resolution=str(intent_params["resolution"]),
            generate_audio=bool(intent_params.get("audio", False)),
            audio_url=None,
            prompt_extend=bool(intent_params.get("prompt_extend", True)),
            negative_prompt=intent_params.get("negative_prompt"),
            model=record.provider_model_id,
            shot_type=str(intent_params.get("shot_type", "single")),
            generation_mode=record.intent["mode"],
            reference_video_urls=raw.get("reference_video_urls", []),
            reference_image_urls=raw.get("reference_image_urls", []),
            ratio=intent_params.get("ratio"),
            watermark=intent_params.get("watermark") if "contract_version" in raw else None,
            workbench_tab=raw.get("workbench_tab"),
            provider_execution=record,
            created_at=created_at,
        )

    def _admit_provider_video_tasks(
        self, script_id: str, request: Dict[str, Any], *, client_request_id: str | None, batch_size: int,
    ) -> provider_recovery.StudioProviderBatchAdmissionResult:
        batch_size = self._validate_provider_batch_size(batch_size)
        raw = self._provider_request_raw(request, batch_size)
        execution_raw = dict(raw)
        execution_raw.pop("batch_size", None)
        ordered = execution_raw.get("contract_version") == provider_recovery.ORDERED_MEDIA_CONTRACT_VERSION
        entrypoint = (provider_recovery.STUDIO_ORDERED_VIDEO_ENTRYPOINT if ordered
                      else provider_recovery.STUDIO_VIDEO_ENTRYPOINT)
        normalize_request = (provider_recovery._normalize_ordered_raw_request if ordered
                             else provider_recovery._normalize_raw_request)
        build_submission = (provider_recovery.build_ordered_accepted_submission_and_record if ordered
                            else provider_recovery.build_accepted_submission_and_record)
        request_digest = None
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            if client_request_id is not None:
                replay = self._replay_provider_request(script, raw, client_request_id)
                if replay is not None:
                    return replay
                request_digest = fingerprint_request(raw, entrypoint=entrypoint)
            _, raw_provider_model_id, _ = normalize_request(execution_raw)
            provider_model_id, gateway = self._provider_model_metadata(raw_provider_model_id)
            runtime = self._provider_runtime_for_admission(provider_model_id=provider_model_id, gateway=gateway)
            script_identity = script
            frame_id = raw.get("frame_id")
            frame_identity = next((f for f in script.frames if f.id == frame_id), None) if frame_id else None
            if frame_identity is None:
                raise ValueError("provider_frame_owner_required")
            frame_dialogue_projection = self._video_frame_dialogue_projection(frame_identity)

        secret = runtime.resolve_secret()
        raw.setdefault("frame_id", frame_id)
        task_ids = tuple(str(uuid.uuid4()) for _ in range(batch_size))
        attempt_ids = tuple(str(uuid.uuid4()) for _ in range(batch_size))

        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is not script_identity:
                raise StorageConflictError("Project changed while preparing provider task")
            if client_request_id is not None:
                replay = self._replay_provider_request(script, raw, client_request_id)
                if replay is not None:
                    return replay
            current_runtime = self._provider_runtime_for_admission(provider_model_id=provider_model_id, gateway=gateway)
            if current_runtime is not runtime:
                raise StorageConflictError("Provider recovery runtime changed before admission")
            if frame_id and not any(frame is frame_identity for frame in script.frames):
                raise StorageConflictError("Provider task frame changed or was deleted")
            if self._video_frame_dialogue_projection(frame_identity) != frame_dialogue_projection:
                raise StorageConflictError("Provider task frame dialogue changed while creating task")
            try:
                credential = self._provider_credential_store.bind(runtime.scope, secret)
            except StorageError as exc:
                self.mark_storage_failed(exc)
                self.close()
                raise

            tasks: list[VideoTask] = []
            receipt_members: list[ProviderRequestReceiptMember] = []
            result_members: list[provider_recovery.StudioProviderReceiptMember] = []
            created_at = time.time()
            # A batch is one user action over one fixed input snapshot. Later
            # members receive distinct attempts, never a fresh source read.
            submission, first_record = build_submission(
                execution_raw,
                output_root=self.data_dir,
                writer_lock=self._writer_lock,
                scope=credential.scope,
                credential_ref=credential.credential_ref,
                attempt_id=attempt_ids[0],
                max_bytes=runtime.max_bytes,
            )
            for index, (task_id, attempt_id) in enumerate(zip(task_ids, attempt_ids)):
                record = first_record if index == 0 else _build_accepted_provider_record(
                    submission.intent,
                    provider_model_id=submission.provider_model_id,
                    media=submission.media,
                    scope=submission.scope,
                    credential_ref=submission.credential_ref,
                    attempt_id=attempt_id,
                    mode_source=first_record.intent["sources"]["mode"],
                    admission_request_fields=tuple(first_record.intent["admission_request_fields"]),
                )[1]
                tasks.append(self._provider_video_task_from_record(
                    script_id=script_id,
                    frame_id=frame_id,
                    raw=execution_raw,
                    task_id=task_id,
                    record=record,
                    created_at=created_at,
                ))
                receipt_members.append(ProviderRequestReceiptMember(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    execution_fingerprint=record.fingerprint,
                ))
                result_members.append(provider_recovery.StudioProviderReceiptMember(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    execution_fingerprint=record.fingerprint,
                ))

            if self.scripts.get(script_id) is not script_identity:
                raise StorageConflictError("Project changed while preparing provider task")
            if frame_id and not any(frame is frame_identity for frame in script.frames):
                raise StorageConflictError("Provider task frame changed or was deleted")
            if self._video_frame_dialogue_projection(frame_identity) != frame_dialogue_projection:
                raise StorageConflictError("Provider task frame dialogue changed while creating task")
            if not script.video_tasks:
                script.video_tasks = []
            script.video_tasks.extend(tasks)
            if client_request_id is not None:
                if batch_size == 1:
                    only = receipt_members[0]
                    script.provider_request_receipts.append(ProviderRequestReceipt(
                        request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
                        entrypoint=entrypoint,
                        client_request_id=client_request_id,
                        frame_id=frame_id,
                        request_fingerprint=request_digest,
                        execution_fingerprint=only.execution_fingerprint,
                        task_id=only.task_id,
                        attempt_id=only.attempt_id,
                        created_at=created_at,
                    ))
                else:
                    script.provider_request_receipts.append(ProviderBatchRequestReceipt(
                        schema_version=2,
                        request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
                        entrypoint=entrypoint,
                        client_request_id=client_request_id,
                        frame_id=frame_id,
                        request_fingerprint=request_digest,
                        members=receipt_members,
                        created_at=created_at,
                    ))
            self._commit_storage("projects.json")
            return provider_recovery.StudioProviderBatchAdmissionResult(
                script=self.read_committed_script(script_id),
                task_ids=task_ids,
                members=tuple(result_members),
                client_request_id=client_request_id,
                replayed=False,
                new_task_ids=task_ids,
            )


    def _provider_execution_projection(self, record) -> Dict[str, Any]:
        if record is None:
            raise StorageConflictError("Provider execution record missing")
        return {
            "attempt_id": record.attempt_id,
            "phase": record.phase,
            "fingerprint": record.fingerprint,
            "handle": deepcopy(record.handle),
            "observation": deepcopy(record.observation),
            "prepared_artifact": deepcopy(record.prepared_artifact),
            "artifact": deepcopy(record.artifact),
        }

    def _check_provider_task_context(
        self, context: Dict[str, Any], token: object, *, verify_fingerprint: bool = False,
    ) -> Tuple[Script, VideoTask, Optional[object], Dict[str, Any]]:
        self.assert_storage_available()
        runtime = context.get("runtime")
        if not isinstance(runtime, provider_recovery.StudioProviderRecoveryRuntime):
            raise StorageConflictError("Provider operation runtime is incomplete")
        script, task, frame, owner = self._check_video_task_context(context, token)
        expected_motion = context.get("expected_motion_request")
        if expected_motion is not None:
            self.motion_reference_service.require_recovery(script, task, expected_motion)
        record = task.provider_execution
        if task.motion_reference is not None and record is not None and record.phase == "accepted":
            self.motion_reference_service.target(script.id, task)
        if self._provider_execution_projection(record) != context["provider_execution"]:
            raise StorageConflictError("Provider execution record changed during operation")
        if verify_fingerprint:
            try:
                provider_recovery.reconstruct_submission_from_record(
                    record, output_root=self.data_dir, max_bytes=context["runtime"].max_bytes,
                )
            except provider_recovery.StudioProviderRecoveryError as exc:
                raise StorageConflictError("Provider execution record changed during operation") from exc
        return script, task, frame, owner

    def _set_provider_context_record(self, context: Dict[str, Any], record) -> None:
        context["provider_execution"] = self._provider_execution_projection(record)

    def cancel_provider_video_task(self, script_id: str, task_id: str) -> VideoTask:
        """Cancel only work proven unsubmitted, retaining its replay authority."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            task = next((item for item in script.video_tasks if item.id == task_id), None) if script else None
            if task is None or task.project_id != script_id:
                raise ValueError("Video task not found")
            record = task.provider_execution
            if record is None:
                raise ValueError("provider_execution_missing")
            if record.phase == "cancelled":
                return next(item for item in self.read_committed_script(script_id).video_tasks if item.id == task_id)
            if (
                record.phase != "accepted" or task.status == "completed"
                or (script_id, task_id) in self._video_task_runs
                or any(value is not None for value in (
                    record.handle, record.observation, record.prepared_artifact, record.artifact,
                ))
            ):
                raise StorageConflictError("provider_cancel_unconfirmed")
            record.phase = "cancelled"
            task.status = "failed"
            task.error = "provider_cancelled_before_submit"
            self._commit_storage("projects.json")
            return next(item for item in self.read_committed_script(script_id).video_tasks if item.id == task_id)

    def continue_accepted_provider_video_task(
        self, script_id: str, task_id: str, *, expected_attempt_id: str, expected_fingerprint: str,
    ) -> VideoTask:
        """Explicit user action on the original unsubmitted attempt, never a retry."""
        result = self.submit_provider_video_task(
            script_id, task_id, expected_continuation=(expected_attempt_id, expected_fingerprint),
        )
        return next(task for task in result.video_tasks if task.id == task_id)

    def _require_provider_continuation(self, script: Script, task: VideoTask, expected) -> None:
        record = task.provider_execution
        if (
            record is None or record.phase != "accepted"
            or task.status not in {"pending", "processing", "needs_confirmation"}
            or (record.attempt_id, record.fingerprint) != expected
            or any(value is not None for value in (
                record.handle, record.observation, record.prepared_artifact, record.artifact,
            ))
        ):
            raise StorageConflictError("provider_continue_not_allowed")
        committed = self.read_committed_script(script.id)
        original = next((item for item in committed.video_tasks if item.id == task.id), None)
        if (
            original is None or original.provider_execution != record
            or original.frame_id != task.frame_id or original.project_id != task.project_id
            or script.provider_request_receipts != committed.provider_request_receipts
        ):
            raise StorageConflictError("provider_continue_not_allowed")
        matches = [
            (receipt, member)
            for receipt in committed.provider_request_receipts
            for member in receipt.receipt_members
            if member.task_id == task.id
        ]
        if len(matches) != 1:
            raise StorageConflictError("provider_continue_not_allowed")
        receipt, member = matches[0]
        if isinstance(receipt, MotionReferenceReceipt):
            from .motion_reference import ENTRYPOINT
            if (
                task.motion_reference is None or receipt.entrypoint != ENTRYPOINT
                or (member.attempt_id, member.execution_fingerprint) != expected
            ):
                raise StorageConflictError("provider_continue_not_allowed")
            self.motion_reference_service.target(script.id, task)
            return
        if task.motion_reference is not None:
            raise StorageConflictError("provider_continue_not_allowed")
        expected_entrypoint = (
            provider_recovery.STUDIO_ORDERED_VIDEO_ENTRYPOINT
            if record.provider_model_id in provider_recovery.ORDERED_WAN3_MODELS
            else provider_recovery.STUDIO_VIDEO_ENTRYPOINT
        )
        if (
            receipt.entrypoint != expected_entrypoint
            or receipt.frame_id != task.frame_id
            or not any(frame.id == task.frame_id for frame in script.frames)
            or (member.attempt_id, member.execution_fingerprint) != expected
        ):
            raise StorageConflictError("provider_continue_not_allowed")

    def submit_provider_video_task(
        self, script_id: str, task_id: str, *, expected_continuation: Optional[Tuple[str, str]] = None,
    ) -> Script:
        """Explicitly submit one accepted provider execution, at most once."""
        key = (script_id, task_id)
        token = object()
        claimed = False
        context = None
        try:
            with self._save_lock:
                self.assert_storage_available()
                if key in self._video_task_runs:
                    if expected_continuation is not None:
                        raise StorageConflictError("provider_operation_running")
                    raise StorageConflictError("Video task provider operation already running")
                script = self.scripts.get(script_id)
                if not script:
                    raise ValueError("Script not found")
                task = next((candidate for candidate in script.video_tasks if candidate.id == task_id), None)
                if task is None or task.project_id != script_id:
                    if expected_continuation is not None:
                        raise ValueError("Video task not found")
                    raise StorageConflictError("Video task changed or was deleted")
                if expected_continuation is not None:
                    self._require_provider_continuation(script, task, expected_continuation)
                record = task.provider_execution
                if record is None:
                    raise ValueError("provider_execution_missing")
                if record.phase == "cancelled":
                    return self.read_committed_script(script_id)
                runtime = self._provider_runtime_for_scope(record.scope)
                if task.status == "completed" or record.phase == "completed" or record.artifact is not None:
                    return self.read_committed_script(script_id)
                if not callable(getattr(runtime.provider, "submit", None)):
                    raise StorageConflictError("Provider recovery runtime cannot submit")
                if record.phase != "accepted":
                    if record.phase == "submitting" and record.handle is None:
                        task.status = "needs_confirmation"
                        task.error = "provider_submit_receipt_missing"
                        self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)
                frame = self._find_video_frame_identity(script, task.frame_id)
                if task.frame_id and frame is None:
                    raise StorageConflictError("Video task frame changed or was deleted")
                owner = self._video_task_owner_snapshot(script, task)
                context = {
                    "key": key,
                    "script_id": script_id,
                    "task_id": task_id,
                    "asset_id": task.asset_id,
                    "frame_id": task.frame_id,
                    "script_identity": script,
                    "task_identity": task,
                    "frame_identity": frame,
                    "owner": owner,
                    "expected_task": self._video_task_provider_projection(task),
                    "attempt_id": record.attempt_id,
                    "runtime": runtime,
                    "provider_execution": self._provider_execution_projection(record),
                }
                self._video_task_runs[key] = token
                claimed = True

            try:
                secret = runtime.resolve_secret()
            except provider_recovery.StudioProviderRecoveryError:
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    current.status = "needs_confirmation"
                    current.error = "provider_credentials_required"
                    self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)

            with self._save_lock:
                current_script, current, _, _ = self._check_provider_task_context(
                    context, token, verify_fingerprint=True,
                )
                if expected_continuation is not None:
                    self._require_provider_continuation(
                        current_script, current, expected_continuation,
                    )
                record = current.provider_execution
                try:
                    credential = self._provider_credential_store.resolve_binding(
                        provider_recovery.ProviderScope.from_dict(record.scope),
                        record.credential_ref,
                        secret,
                    )
                except ProviderCredentialResolutionError:
                    current.status = "needs_confirmation"
                    current.error = "provider_credentials_required"
                    self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)
                except StorageError as exc:
                    self.mark_storage_failed(exc)
                    self.close()
                    raise
                submission = provider_recovery.reconstruct_submission_from_record(
                    record,
                    output_root=self.data_dir,
                    max_bytes=runtime.max_bytes,
                )
            recheck_runtime_after_transport = any(
                item.transport_binding == "frozen_av_sha256_v1" for item in submission.media
            )
            try:
                submission = _prepare_submission_transport(submission, credential)
            except provider_recovery.StudioProviderRecoveryError as exc:
                with self._save_lock:
                    current_script, current, _, _ = self._check_provider_task_context(
                        context, token, verify_fingerprint=True,
                    )
                    if expected_continuation is not None:
                        self._require_provider_continuation(
                            current_script, current, expected_continuation,
                        )
                    current.status = "needs_confirmation"
                    current.error = exc.reason
                    self._commit_storage("projects.json")
                return self.read_committed_script(script_id)

            with self._save_lock:
                current_script, current, _, _ = self._check_provider_task_context(
                    context, token, verify_fingerprint=True,
                )
                if expected_continuation is not None:
                    self._require_provider_continuation(
                        current_script, current, expected_continuation,
                    )
                record = current.provider_execution
                if recheck_runtime_after_transport:
                    try:
                        current_runtime = self._provider_runtime_for_scope(record.scope)
                    except ValueError as exc:
                        raise StorageConflictError("Provider recovery runtime changed before submit") from exc
                    if current_runtime is not runtime:
                        raise StorageConflictError("Provider recovery runtime changed before submit")
                current.provider_execution = provider_recovery.record_with_changes(record, phase="submitting")
                current.status = "processing"
                current.error = None
                self._commit_storage("projects.json")
                self._set_provider_context_record(context, current.provider_execution)

            def save_handle(handle):
                with self._save_lock:
                    current_script, current, _, _ = self._check_provider_task_context(
                        context, token, verify_fingerprint=True,
                    )
                    record = current.provider_execution
                    if record.phase != "submitting" or record.handle is not None:
                        raise StorageConflictError("Provider receipt was already saved or superseded")
                    accepted = provider_recovery.require_matching_handle(record, handle)
                    current.provider_execution = provider_recovery.record_with_changes(
                        record,
                        phase="submitted",
                        handle=accepted.to_dict(),
                    )
                    current.status = "processing"
                    current.error = None
                    self._commit_storage("projects.json")
                    self._set_provider_context_record(context, current.provider_execution)

            provider = runtime.provider
            try:
                returned = provider.submit(submission, credential=credential, on_submitted=save_handle)
            except StorageError:
                raise
            except (ProviderSubmitError, ProviderBindingError):
                with self._save_lock:
                    try:
                        _, current, _, _ = self._check_provider_task_context(context, token)
                    except StorageConflictError:
                        raise
                    record = current.provider_execution
                    if record is not None and record.handle is None:
                        current.status = "needs_confirmation"
                        current.error = "provider_submit_receipt_missing"
                        self._commit_storage("projects.json")
                return self.read_committed_script(script_id)

            with self._save_lock:
                _, current, _, _ = self._check_provider_task_context(context, token)
                record = current.provider_execution
                try:
                    returned = provider_recovery.require_matching_handle(record, returned)
                    saved = provider_recovery.handle_from_record(record)
                    if saved != returned:
                        raise provider_recovery.StudioProviderRecoveryError("provider_handle_return_mismatch")
                except provider_recovery.StudioProviderRecoveryError:
                    current.status = "needs_confirmation"
                    current.error = "provider_submit_receipt_mismatch"
                    self._commit_storage("projects.json")
                return self.read_committed_script(script_id)
        finally:
            if claimed:
                with self._save_lock:
                    if self._video_task_runs.get(key) is token:
                        del self._video_task_runs[key]

    def query_provider_video_task(
        self, script_id: str, task_id: str, *,
        expected_motion_request: Optional[Tuple[str, str, str]] = None,
    ) -> Script:
        """Explicitly query an original provider handle; never submit or fetch."""
        key = (script_id, task_id)
        token = object()
        claimed = False
        context = None
        try:
            with self._save_lock:
                self.assert_storage_available()
                if key in self._video_task_runs:
                    if expected_motion_request is not None:
                        raise StorageConflictError("provider_operation_running")
                    raise StorageConflictError("Video task provider operation already running")
                script = self.scripts.get(script_id)
                if not script:
                    raise ValueError("Script not found")
                task = next((candidate for candidate in script.video_tasks if candidate.id == task_id), None)
                if task is None or task.project_id != script_id:
                    if expected_motion_request is not None:
                        raise ValueError("Video task not found")
                    raise StorageConflictError("Video task changed or was deleted")
                if expected_motion_request is not None:
                    action = self.motion_reference_service.require_recovery(script, task, expected_motion_request)
                    if action != "query":
                        return self.read_committed_script(script_id)
                record = task.provider_execution
                if record is None:
                    raise ValueError("provider_execution_missing")
                if record.phase == "cancelled":
                    return self.read_committed_script(script_id)
                runtime = self._provider_runtime_for_scope(record.scope)
                if task.status == "completed" or record.phase == "completed" or record.artifact is not None:
                    return self.read_committed_script(script_id)
                if not callable(getattr(runtime.provider, "query", None)):
                    raise StorageConflictError("Provider recovery runtime cannot query")
                if record.handle is None:
                    if record.phase == "submitting":
                        task.status = "needs_confirmation"
                        task.error = "provider_submit_receipt_missing"
                        self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)
                frame = self._find_video_frame_identity(script, task.frame_id)
                if task.frame_id and frame is None:
                    raise StorageConflictError("Video task frame changed or was deleted")
                owner = self._video_task_owner_snapshot(script, task)
                context = {
                    "key": key,
                    "script_id": script_id,
                    "task_id": task_id,
                    "asset_id": task.asset_id,
                    "frame_id": task.frame_id,
                    "script_identity": script,
                    "task_identity": task,
                    "frame_identity": frame,
                    "owner": owner,
                    "expected_task": self._video_task_provider_projection(task),
                    "attempt_id": record.attempt_id,
                    "runtime": runtime,
                    "provider_execution": self._provider_execution_projection(record),
                    "expected_motion_request": expected_motion_request,
                }
                handle = provider_recovery.handle_from_record(record)
                self._video_task_runs[key] = token
                claimed = True

            try:
                secret = runtime.resolve_secret()
            except provider_recovery.StudioProviderRecoveryError:
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    current.status = "needs_confirmation"
                    current.error = "provider_credentials_required"
                    self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)

            with self._save_lock:
                _, current, _, _ = self._check_provider_task_context(context, token)
                record = current.provider_execution
                handle = provider_recovery.handle_from_record(record)
                try:
                    credential = self._provider_credential_store.resolve_handle(handle, secret)
                except ProviderCredentialResolutionError as exc:
                    current.status = "needs_confirmation"
                    current.error = (
                        "provider_receipt_invalid"
                        if exc.kind == "missing_handle_binding" else "provider_credentials_required"
                    )
                    self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)
                except StorageError as exc:
                    self.mark_storage_failed(exc)
                    self.close()
                    raise

            provider = runtime.provider
            try:
                observation = provider.query(handle, credential=credential)
            except ProviderBindingError:
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    current.status = "needs_confirmation"
                    current.error = "provider_receipt_invalid"
                    self._commit_storage("projects.json")
                return self.read_committed_script(script_id)
            except ProviderQueryError:
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    current.status = "needs_confirmation"
                    current.error = "provider_query_unavailable"
                    self._commit_storage("projects.json")
                return self.read_committed_script(script_id)

            with self._save_lock:
                _, current, _, _ = self._check_provider_task_context(context, token)
                record = current.provider_execution
                try:
                    observation = provider_recovery.validate_observation_for_handle(observation, handle)
                except provider_recovery.StudioProviderRecoveryError:
                    current.status = "needs_confirmation"
                    current.error = "provider_query_invalid"
                    self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)
                status, error, phase = provider_recovery.status_after_observation(observation)
                current.provider_execution = provider_recovery.record_with_changes(
                    record,
                    phase=phase,
                    observation=provider_recovery.observation_to_dict(observation),
                )
                current.status = status
                current.error = error
                if status != "completed":
                    current.video_url = None
                self._commit_storage("projects.json")
                return self.read_committed_script(script_id)
        finally:
            if claimed:
                with self._save_lock:
                    if self._video_task_runs.get(key) is token:
                        del self._video_task_runs[key]


    @contextmanager
    def _provider_media_storage_guard(self):
        with self._save_lock:
            self.assert_storage_available()
            self._writer_lock.assert_owned()
            yield

    def _provider_fetch_output_ref(self, task: VideoTask, record) -> str:
        return f"video/provider-{task.id}-{record.attempt_id}.mp4"

    def _provider_output_max_bytes(self, runtime) -> int:
        return getattr(runtime, "output_max_bytes", 200 * 1024 * 1024)

    def _verify_provider_final_artifact(self, proof: Dict[str, Any], runtime) -> bool:
        if not media_versions.managed_media_file_exists(
            proof["relative_path"],
            output_root=self.data_dir,
            writer_lock=self._writer_lock,
            storage_guard=self._provider_media_storage_guard,
        ):
            return False
        media_versions.verify_managed_media_file(
            proof["relative_path"],
            output_root=self.data_dir,
            writer_lock=self._writer_lock,
            storage_guard=self._provider_media_storage_guard,
            expected_sha256=proof["sha256"],
            expected_size=proof["size_bytes"],
            max_bytes=self._provider_output_max_bytes(runtime),
        )
        return True

    def _complete_provider_artifact_from_proof(self, script_id: str, current: VideoTask, record, proof: Dict[str, Any]) -> Script:
        current.provider_execution = provider_recovery.record_with_changes(
            record,
            phase="completed",
            artifact=proof,
        )
        current.status = "completed"
        current.error = None
        current.video_url = proof["relative_path"]
        motion_source = None
        if current.motion_reference is not None:
            motion_source = self.motion_reference_service.project_completed(script_id, current)
        try:
            self._commit_storage(*self._video_task_storage_names(motion_source))
        except StorageError as exc:
            self.mark_storage_failed(exc)
            self.close()
            raise
        return self.read_committed_script(script_id)

    def _mark_provider_fetch_needs_confirmation(self, script_id: str, current: VideoTask, error: str) -> Script:
        current.status = "needs_confirmation"
        current.error = error
        self._commit_storage("projects.json")
        return self.read_committed_script(script_id)

    def fetch_provider_video_task(
        self, script_id: str, task_id: str, *,
        expected_motion_request: Optional[Tuple[str, str, str]] = None,
    ) -> Script:
        """Explicitly fetch bytes for one trusted succeeded provider observation."""
        key = (script_id, task_id)
        token = object()
        claimed = False
        context = None
        try:
            with self._save_lock:
                self.assert_storage_available()
                if key in self._video_task_runs:
                    if expected_motion_request is not None:
                        raise StorageConflictError("provider_operation_running")
                    raise StorageConflictError("Video task provider operation already running")
                script = self.scripts.get(script_id)
                if not script:
                    raise ValueError("Script not found")
                task = next((candidate for candidate in script.video_tasks if candidate.id == task_id), None)
                if task is None or task.project_id != script_id:
                    if expected_motion_request is not None:
                        raise ValueError("Video task not found")
                    raise StorageConflictError("Video task changed or was deleted")
                if expected_motion_request is not None:
                    action = self.motion_reference_service.require_recovery(script, task, expected_motion_request)
                    if action == "terminal":
                        return self.read_committed_script(script_id)
                    if action != "fetch":
                        raise StorageConflictError("motion_reference_recover_not_allowed")
                record = task.provider_execution
                if record is None:
                    raise ValueError("provider_execution_missing")
                if record.phase == "cancelled":
                    return self.read_committed_script(script_id)
                runtime = self._provider_runtime_for_scope(record.scope)
                if task.status == "completed" or record.phase == "completed" or record.artifact is not None:
                    artifact = record.artifact
                    if artifact is None:
                        return self._mark_provider_fetch_needs_confirmation(
                            script_id, task, "provider_fetch_artifact_invalid",
                        )
                    try:
                        final_ready = self._verify_provider_final_artifact(artifact, runtime)
                    except (media_versions.MediaVersionError, StorageError):
                        return self._mark_provider_fetch_needs_confirmation(
                            script_id, task, "provider_fetch_artifact_invalid",
                        )
                    if not final_ready:
                        return self._mark_provider_fetch_needs_confirmation(
                            script_id, task, "provider_fetch_artifact_missing",
                        )
                    return self.read_committed_script(script_id)
                frame = self._find_video_frame_identity(script, task.frame_id)
                if task.frame_id and frame is None:
                    raise StorageConflictError("Video task frame changed or was deleted")
                owner = self._video_task_owner_snapshot(script, task)
                output_ref = self._provider_fetch_output_ref(task, record)
                context = {
                    "key": key,
                    "script_id": script_id,
                    "task_id": task_id,
                    "asset_id": task.asset_id,
                    "frame_id": task.frame_id,
                    "script_identity": script,
                    "task_identity": task,
                    "frame_identity": frame,
                    "owner": owner,
                    "expected_task": self._video_task_provider_projection(task),
                    "attempt_id": record.attempt_id,
                    "runtime": runtime,
                    "provider_execution": self._provider_execution_projection(record),
                    "expected_motion_request": expected_motion_request,
                }
                self._video_task_runs[key] = token
                claimed = True
                prepared = record.prepared_artifact
                if prepared is not None:
                    if prepared["relative_path"] != output_ref:
                        return self._mark_provider_fetch_needs_confirmation(
                            script_id, task, "provider_fetch_artifact_target_invalid",
                        )
                    try:
                        final_ready = self._verify_provider_final_artifact(prepared, runtime)
                    except (media_versions.MediaVersionError, StorageError):
                        return self._mark_provider_fetch_needs_confirmation(
                            script_id, task, "provider_fetch_artifact_invalid",
                        )
                    if final_ready:
                        return self._complete_provider_artifact_from_proof(script_id, task, record, prepared)
                elif media_versions.managed_media_file_exists(
                    output_ref,
                    output_root=self.data_dir,
                    writer_lock=self._writer_lock,
                    storage_guard=self._provider_media_storage_guard,
                ):
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, task, "provider_fetch_artifact_unrecorded",
                    )

                if not callable(getattr(runtime.provider, "fetch", None)):
                    raise StorageConflictError("Provider recovery runtime cannot fetch")
                try:
                    observation = provider_recovery.observation_from_record(record)
                except provider_recovery.StudioProviderRecoveryError:
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, task, "provider_fetch_receipt_invalid",
                    )
                if observation.status is not ProviderTaskStatus.SUCCEEDED:
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, task, "provider_fetch_requires_succeeded",
                    )

            try:
                secret = runtime.resolve_secret()
            except provider_recovery.StudioProviderRecoveryError:
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, current, "provider_credentials_required",
                    )

            with self._save_lock:
                _, current, _, _ = self._check_provider_task_context(context, token)
                record = current.provider_execution
                try:
                    handle = provider_recovery.handle_from_record(record)
                    observation = provider_recovery.observation_from_record(record)
                except provider_recovery.StudioProviderRecoveryError:
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, current, "provider_fetch_receipt_invalid",
                    )
                try:
                    self._provider_credential_store.resolve_handle(handle, secret)
                except ProviderCredentialResolutionError as exc:
                    current.status = "needs_confirmation"
                    current.error = (
                        "provider_receipt_invalid"
                        if exc.kind == "missing_handle_binding" else "provider_credentials_required"
                    )
                    self._commit_storage("projects.json")
                    return self.read_committed_script(script_id)
                except StorageError as exc:
                    self.mark_storage_failed(exc)
                    self.close()
                    raise
                prepared = record.prepared_artifact
                output_ref = self._provider_fetch_output_ref(current, record)

            def save_prepared(artifact):
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    record = current.provider_execution
                    existing = record.prepared_artifact
                    proof = provider_recovery.require_matching_artifact_proof(record, artifact, existing)
                    if proof["relative_path"] != self._provider_fetch_output_ref(current, record):
                        raise provider_recovery.StudioProviderRecoveryError(
                            "provider_fetch_artifact_target_invalid",
                        )
                    if existing is None:
                        current.provider_execution = provider_recovery.record_with_changes(
                            record,
                            prepared_artifact=proof,
                        )
                        try:
                            self._commit_storage("projects.json")
                        except StorageError as exc:
                            self.mark_storage_failed(exc)
                            self.close()
                            raise
                        self._set_provider_context_record(context, current.provider_execution)

            provider = runtime.provider
            try:
                artifact = provider.fetch(
                    handle,
                    observation,
                    output_root=self.data_dir,
                    output_ref=output_ref,
                    writer_lock=self._writer_lock,
                    storage_guard=self._provider_media_storage_guard,
                    max_bytes=self._provider_output_max_bytes(runtime),
                    on_prepared=save_prepared,
                    publish_policy="no_overwrite",
                )
            except provider_recovery.StudioProviderRecoveryError as exc:
                reason = (
                    "provider_fetch_artifact_target_invalid"
                    if exc.reason == "provider_fetch_artifact_target_invalid"
                    else "provider_fetch_artifact_invalid"
                )
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    return self._mark_provider_fetch_needs_confirmation(script_id, current, reason)
            except (ProviderBindingError, ProviderFetchError):
                with self._save_lock:
                    _, current, _, _ = self._check_provider_task_context(context, token)
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, current, "provider_fetch_unavailable",
                    )

            with self._save_lock:
                _, current, _, _ = self._check_provider_task_context(context, token)
                record = current.provider_execution
                prepared = record.prepared_artifact
                if prepared is None:
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, current, "provider_fetch_prepare_missing",
                    )
                try:
                    proof = provider_recovery.require_matching_artifact_proof(record, artifact, prepared)
                except provider_recovery.StudioProviderRecoveryError:
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, current, "provider_fetch_artifact_invalid",
                    )
                if proof["relative_path"] != self._provider_fetch_output_ref(current, record):
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, current, "provider_fetch_artifact_target_invalid",
                    )
                try:
                    if not self._verify_provider_final_artifact(proof, runtime):
                        return self._mark_provider_fetch_needs_confirmation(
                            script_id, current, "provider_fetch_artifact_missing",
                        )
                except (media_versions.MediaVersionError, StorageError):
                    return self._mark_provider_fetch_needs_confirmation(
                        script_id, current, "provider_fetch_artifact_invalid",
                    )
                return self._complete_provider_artifact_from_proof(script_id, current, record, proof)
        finally:
            if claimed:
                with self._save_lock:
                    if self._video_task_runs.get(key) is token:
                        del self._video_task_runs[key]

    def process_video_task(self, script_id: str, task_id: str):
        """Processes a video task with an in-process claim and short-lock commits."""
        key = (script_id, task_id)
        token = object()
        claimed = False
        context: Dict[str, Any] | None = None

        try:
            with self._save_lock:
                self.assert_storage_available()
                script = self.scripts.get(script_id)
                if not script:
                    logger.error(f"Script {script_id} not found for task {task_id}")
                    return
                task = next((candidate for candidate in script.video_tasks if candidate.id == task_id), None)
                if not task:
                    logger.error(f"Task {task_id} not found in script {script_id}")
                    return
                if task.project_id != script_id:
                    raise StorageConflictError("Video task belongs to a different project")
                if task.provider_execution is not None:
                    if task.status in {"pending", "processing", "needs_confirmation"}:
                        task.status = "needs_confirmation"
                        task.error = "provider_execution_not_enabled"
                        self._commit_storage(*self._video_task_storage_names(None))
                    return
                if task.status != "pending":
                    return
                if key in self._video_task_runs:
                    return
                frame = self._find_video_frame_identity(script, task.frame_id)
                if task.frame_id and frame is None:
                    raise StorageConflictError("Video task frame changed or was deleted")
                self._video_task_owner_snapshot(script, task)
                self._video_task_runs[key] = token
                claimed = True
                task.status = "processing"
                task.error = None
                owner_source = None
                if task.asset_id:
                    owner_source = self._sync_asset_video_task(script, task)
                try:
                    self._commit_storage(*self._video_task_storage_names(owner_source))
                except BaseException:
                    task.status = "pending"
                    task.error = None
                    if task.asset_id:
                        try:
                            self._sync_asset_video_task(script, task)
                        except StorageConflictError:
                            pass
                    raise
                owner = self._video_task_owner_snapshot(script, task)
                context = {
                    "key": key,
                    "script_id": script_id,
                    "task_id": task_id,
                    "asset_id": task.asset_id,
                    "frame_id": task.frame_id,
                    "script_identity": script,
                    "task_identity": task,
                    "frame_identity": frame,
                    "owner": owner,
                    "expected_task": self._video_task_provider_projection(task),
                }
                request = task.model_copy(deep=True)

            def save_provider_ids(provider_name: str, ptask_id: Optional[str], preq_id: Optional[str]) -> None:
                with self._save_lock:
                    try:
                        current_script, current, _, _ = self._check_video_task_context(context, token)
                    except StorageConflictError:
                        return
                    if current.status != "processing":
                        return
                    current.provider_name = provider_name
                    current.provider_task_id = ptask_id
                    current.provider_request_id = preq_id
                    owner_source = None
                    if current.asset_id:
                        owner_source = self._sync_asset_video_task(current_script, current)
                    self._commit_storage(*self._video_task_storage_names(owner_source))

            img_path = None
            if request.image_url:
                img_path = self._download_temp_image(request.image_url)

            output_filename = f"video_{task_id}_{uuid.uuid4().hex[:12]}.mp4"
            output_path = self._output_path("video", output_filename)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            final_audio_url = request.audio_url if request.audio_url else None
            final_generate_audio = False if request.audio_url else bool(request.generate_audio)
            img_url = request.image_url

            model_name = request.model or ""
            model_name_lower = model_name.lower()
            video_env = self.environment_snapshot()
            backend = (resolve_provider_backend(model_name, env=video_env)
                       if video_env is not None else self._resolve_video_backend(model_name))
            use_vendor_kling = backend == "vendor" and (
                model_name_lower.startswith("kling-") or model_name_lower.startswith("kling/kling-")
            )
            use_vendor_vidu = backend == "vendor" and (
                model_name_lower.startswith("vidu")
                or model_name_lower.startswith("viduq2")
                or model_name_lower.startswith("viduq3")
                or model_name_lower.startswith("vidu/vidu")
            )
            use_mulerouter = backend == "mulerouter" and model_name_lower.startswith("seedance")

            if use_mulerouter:
                if video_env is not None:
                    from ...models.mulerouter import MuleRouterVideoModel
                    active_model = MuleRouterVideoModel({"output_root": str(self.data_dir)}, env=video_env)
                else:
                    if self._mulerouter_video_model is None:
                        from ...models.mulerouter import MuleRouterVideoModel
                        self._mulerouter_video_model = MuleRouterVideoModel({"output_root": str(self.data_dir)})
                    active_model = self._mulerouter_video_model
                active_model.generate(
                    prompt=request.prompt,
                    output_path=output_path,
                    img_url=img_url,
                    img_path=img_path,
                    duration=request.duration,
                    resolution=request.resolution,
                    aspect_ratio=request.ratio or "16:9",
                    seed=request.seed,
                    watermark=bool(request.watermark) if request.watermark is not None else False,
                    generation_mode=request.generation_mode,
                    ref_image_urls=request.reference_image_urls if request.generation_mode == "r2v" else None,
                )
            elif use_vendor_kling:
                if video_env is not None:
                    from ...models.kling import KlingModel
                    active_model = KlingModel({"output_root": str(self.data_dir)}, env=video_env)
                else:
                    if self._kling_model is None:
                        from ...models.kling import KlingModel
                        self._kling_model = KlingModel({"output_root": str(self.data_dir)})
                    active_model = self._kling_model
                active_model.generate(
                    prompt=request.prompt,
                    output_path=output_path,
                    img_url=img_url,
                    img_path=img_path,
                    duration=request.duration,
                    model=request.model,
                    negative_prompt=request.negative_prompt,
                    aspect_ratio="16:9",
                    mode=request.mode or "std",
                    sound=request.sound or "off",
                    cfg_scale=request.cfg_scale,
                )
            elif use_vendor_vidu:
                if video_env is not None:
                    from ...models.vidu import ViduModel
                    active_model = ViduModel({"output_root": str(self.data_dir)}, env=video_env)
                else:
                    if self._vidu_model is None:
                        from ...models.vidu import ViduModel
                        self._vidu_model = ViduModel({"output_root": str(self.data_dir)})
                    active_model = self._vidu_model
                active_model.generate(
                    prompt=request.prompt,
                    output_path=output_path,
                    img_url=img_url,
                    img_path=img_path,
                    duration=request.duration,
                    model=request.model,
                    resolution=request.resolution,
                    aspect_ratio="16:9",
                    seed=request.seed or 0,
                    audio=request.vidu_audio if request.vidu_audio is not None else True,
                    movement_amplitude=request.movement_amplitude or "auto",
                )
            else:
                if video_env is not None:
                    from ...models.wanx import WanxModel
                    active_model = WanxModel({"output_root": str(self.data_dir)}, env=video_env)
                else:
                    active_model = self.video_generator.model
                active_model.generate(
                    prompt=request.prompt,
                    output_path=output_path,
                    img_path=img_path,
                    img_url=img_url,
                    duration=request.duration,
                    seed=request.seed,
                    resolution=request.resolution,
                    audio_url=final_audio_url,
                    audio=final_generate_audio,
                    prompt_extend=request.prompt_extend,
                    negative_prompt=request.negative_prompt,
                    model=request.model,
                    shot_type=request.shot_type,
                    ref_video_urls=request.reference_video_urls if request.generation_mode == "r2v" else None,
                    ref_image_urls=request.reference_image_urls if request.generation_mode == "r2v" else None,
                    ratio=request.ratio,
                    watermark=bool(request.watermark) if request.watermark is not None else False,
                    audio_setting=request.audio_setting,
                    camera_motion=None,
                    subject_motion=None,
                    on_provider_ids=save_provider_ids,
                )

            if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
                raise ProjectGenerationError("Video generation did not create a non-empty output file")

            with self._save_lock:
                current_script, current, _, owner = self._check_video_task_context(context, token)
                if current.status != "processing":
                    return

                completed = current.model_copy(deep=True)
                completed.video_url = self._output_relpath(output_path)
                completed.status = "completed"
                owner_source = None
                if completed.asset_id:
                    owner_source = self._sync_asset_video_task(current_script, completed)

                for index, candidate in enumerate(current_script.video_tasks):
                    if candidate.id == task_id:
                        current_script.video_tasks[index] = completed
                        break
                storage_names = ["projects.json"]
                if owner_source == "series":
                    storage_names.append("series.json")
                elif owner_source == "global":
                    storage_names.append("library_assets.json")
                self._commit_storage(*storage_names)
        except StorageConflictError:
            raise
        except StorageError:
            raise
        except Exception as exc:
            logger.exception("Failed to process video task")
            logger.error(f"Video generation failed: {exc}")
            if context is None:
                raise
            with self._save_lock:
                if context is not None:
                    try:
                        current_script, current, _, _ = self._check_video_task_context(context, token)
                    except StorageConflictError:
                        current = None
                        current_script = None
                    if current and current.status == "processing":
                        current.status = "failed"
                        if not current.error:
                            current.error = str(exc)
                        owner_source = None
                        if current.asset_id:
                            try:
                                owner_source = self._sync_asset_video_task(current_script, current)
                            except StorageConflictError:
                                owner_source = None
                        storage_names = ["projects.json"]
                        if owner_source == "series":
                            storage_names.append("series.json")
                        elif owner_source == "global":
                            storage_names.append("library_assets.json")
                        self._commit_storage(*storage_names)
        finally:
            if claimed:
                with self._save_lock:
                    if self._video_task_runs.get(key) is token:
                        del self._video_task_runs[key]

    def _find_video_frame_identity(self, script: Script, frame_id: Optional[str]) -> Optional[object]:
        if not frame_id:
            return None
        return next((frame for frame in script.frames if frame.id == frame_id), None)

    def _video_frame_dialogue_projection(self, frame: StoryboardFrame) -> Dict[str, Any]:
        """Frame dialogue fields consumed when create_video_task enriches the prompt."""
        return {
            "id": frame.id,
            "dialogue": frame.dialogue,
            "speaker": frame.speaker,
            "dialogue_structured": frame.dialogue_structured.model_dump(mode="json") if frame.dialogue_structured else None,
        }

    def _video_task_provider_projection(self, task: VideoTask) -> Dict[str, Any]:
        """VideoTask fields read by process_video_task to call the provider."""
        return {
            "image_url": task.image_url,
            "prompt": task.prompt,
            "duration": task.duration,
            "seed": task.seed,
            "resolution": task.resolution,
            "generate_audio": task.generate_audio,
            "audio_url": task.audio_url,
            "prompt_extend": task.prompt_extend,
            "negative_prompt": task.negative_prompt,
            "model": task.model,
            "shot_type": task.shot_type,
            "generation_mode": task.generation_mode,
            "reference_video_urls": list(task.reference_video_urls or []),
            "reference_image_urls": list(task.reference_image_urls or []),
            "ratio": task.ratio,
            "watermark": task.watermark,
            "mode": task.mode,
            "sound": task.sound,
            "cfg_scale": task.cfg_scale,
            "vidu_audio": task.vidu_audio,
            "movement_amplitude": task.movement_amplitude,
            "audio_setting": task.audio_setting,
            "motion_reference": task.motion_reference.model_dump(mode="json") if task.motion_reference else None,
        }

    def _video_task_owner_snapshot(self, script: Script, task: VideoTask) -> Dict[str, Any]:
        owner = {"asset": None, "source": None, "kind": None, "task": None}
        if task.motion_reference is not None:
            # Paid history stays project-owned if its asset moves or changes.
            # Admission/pre-submit and final projection check the real owner.
            return owner
        if task.asset_id:
            target, source, kind = self._find_asset_with_source_by_id(script, task.asset_id)
            if target is None or source is None:
                raise StorageConflictError("Video task target asset changed or was deleted")
            owner_task = next((existing for existing in target.video_assets if existing.id == task.id), None)
            if owner_task is None:
                raise StorageConflictError("Video task is no longer owned by the target asset")
            if owner_task is not task and owner_task.model_dump() != task.model_dump():
                raise StorageConflictError("Video task owner entry changed during processing")
            owner.update(asset=target, source=source, kind=kind, task=owner_task)
        return owner

    def _check_video_task_context(self, context: Dict[str, Any], token: object) -> Tuple[Script, VideoTask, Optional[object], Dict[str, Any]]:
        if self._video_task_runs.get(context["key"]) is not token:
            raise StorageConflictError("Video task run was superseded")
        script = self.scripts.get(context["script_id"])
        if script is None or script is not context["script_identity"]:
            raise StorageConflictError("Video task project changed or was deleted")
        task = next((candidate for candidate in script.video_tasks if candidate.id == context["task_id"]), None)
        if task is None or task is not context["task_identity"]:
            raise StorageConflictError("Video task changed during processing")
        if task.project_id != context["script_id"]:
            raise StorageConflictError("Video task project changed during processing")
        if task.frame_id != context["frame_id"] or task.asset_id != context["asset_id"]:
            raise StorageConflictError("Video task ownership changed during processing")
        if self._video_task_provider_projection(task) != context["expected_task"]:
            raise StorageConflictError("Video task provider input changed during processing")
        frame = self._find_video_frame_identity(script, context["frame_id"])
        if context["frame_id"]:
            if frame is None or frame is not context["frame_identity"]:
                raise StorageConflictError("Video task frame changed during processing")
        owner = context["owner"]
        if owner["source"] is not None:
            target, source, kind = self._find_asset_with_source_by_id(script, context["asset_id"])
            if (
                target is None or target is not owner["asset"]
                or source != owner["source"] or kind != owner["kind"]
                or not any(existing is owner["task"] for existing in target.video_assets)
            ):
                raise StorageConflictError("Video task target asset changed during processing")
        return script, task, frame, owner

    def _find_asset_with_source_by_id(self, script: Script, asset_id: str) -> Tuple[Optional[object], Optional[str], Optional[str]]:
        for asset_type in ("character", "scene", "prop"):
            target, source = self._find_asset_with_source(script, asset_id, asset_type)
            if target is not None:
                return target, source, asset_type
        return None, None, None

    def _find_asset_video_task_owner(self, script: Script, task: VideoTask) -> Tuple[VideoTask, Optional[str]]:
        target_asset, source, _ = self._find_asset_with_source_by_id(script, task.asset_id)
        if target_asset is None or source is None:
            raise StorageConflictError("Video task target asset changed or was deleted")
        owner_task = next((existing for existing in target_asset.video_assets if existing.id == task.id), None)
        if owner_task is None:
            raise StorageConflictError("Video task is no longer owned by the target asset")
        return owner_task, source

    def _sync_asset_video_task(self, script: Script, task: VideoTask) -> Optional[str]:
        """Sync an asset task to its true owner and return that owner source."""
        if not task.asset_id:
            return None
        target_asset, source, _ = self._find_asset_with_source_by_id(script, task.asset_id)
        if target_asset is None or source is None:
            raise StorageConflictError("Video task target asset changed or was deleted")

        for index, existing in enumerate(target_asset.video_assets):
            if existing.id == task.id:
                target_asset.video_assets[index] = task
                return source
        raise StorageConflictError("Video task is no longer owned by the target asset")

    def _video_task_storage_names(self, owner_source: Optional[str]) -> Tuple[str, ...]:
        storage_names = ["projects.json"]
        if owner_source == "series":
            storage_names.append("series.json")
        elif owner_source == "global":
            storage_names.append("library_assets.json")
        return tuple(storage_names)

    def _asset_unit_video_containers(self, asset: object) -> List["AssetUnit"]:
        """Return AssetUnit containers that may point at an asset video."""
        from .models import AssetUnit

        units: List[AssetUnit] = []
        for name in ("reference_sheet", "full_body", "three_views", "head_shot"):
            value = getattr(asset, name, None)
            if isinstance(value, AssetUnit):
                units.append(value)
        return units

    def delete_asset_video(self, script_id: str, asset_id: str, asset_type: str, video_id: str) -> Script:
        """Remove a generated asset video from metadata without deleting media bytes."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if not target_asset or not source:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

            found = False
            if getattr(target_asset, "video_assets", None):
                kept = []
                for task in target_asset.video_assets:
                    if task.id == video_id:
                        found = True
                    else:
                        kept.append(task)
                target_asset.video_assets = kept

            for unit in self._asset_unit_video_containers(target_asset):
                if unit.video_variants:
                    kept_variants = []
                    for variant in unit.video_variants:
                        if variant.id == video_id:
                            found = True
                        else:
                            kept_variants.append(variant)
                    if len(kept_variants) != len(unit.video_variants):
                        unit.video_variants = kept_variants
                        if unit.selected_video_id == video_id:
                            unit.selected_video_id = kept_variants[0].id if kept_variants else None
                        unit.video_updated_at = time.time()
                elif unit.selected_video_id == video_id:
                    found = True
                    unit.selected_video_id = None
                    unit.video_updated_at = time.time()

            if not found:
                raise ValueError(f"Video {video_id} not found for asset {asset_id}")

            removed_project_task = False
            if script.video_tasks:
                kept_tasks = []
                for task in script.video_tasks:
                    if task.id == video_id and task.asset_id == asset_id and not task.frame_id:
                        removed_project_task = True
                    else:
                        kept_tasks.append(task)
                script.video_tasks = kept_tasks

            if source == "script" or removed_project_task:
                script.updated_at = time.time()
            storage_names = ["projects.json"] if source == "script" or removed_project_task else []
            source_name = {"series": "series.json", "global": "library_assets.json"}.get(source)
            if source_name:
                storage_names.append(source_name)
            if not storage_names:
                storage_names = ["projects.json"]
            self._commit_storage(*dict.fromkeys(storage_names))
            return self.read_committed_script(script_id)

    def generate_audio(self, script_id: str) -> Script:
        """Legacy compatibility: generate dialogue audio via the current command path."""
        script, _, _ = self.generate_dialogue_audio_batch(script_id)
        return script

    @staticmethod
    def _resolve_dialogue_speaker(frame: StoryboardFrame, characters: List[Character]) -> Optional[Character]:
        """Prefer the first character ID, then exact name, then a partial name."""
        if frame.character_ids:
            speaker = next((item for item in characters if item.id == frame.character_ids[0]), None)
            if speaker is not None:
                return speaker
        name = frame.speaker or (frame.dialogue_structured.speaker if frame.dialogue_structured else None)
        key = name.strip().lower() if name else ""
        if not key:
            return None
        named = [(item, item.name.strip().lower()) for item in characters if item.name.strip()]
        exact = next((item for item, normalized in named if normalized == key), None)
        if exact is not None:
            return exact
        return next((item for item, normalized in named if key in normalized or normalized in key), None)

    def _read_dialogue_context(self, script_id: str, frame_id: str):
        """Capture the frame, effective character and registered voice together."""
        with self._save_lock:
            script, series, library = self.read_committed_project_view(script_id)
            if script is None:
                raise ProjectContextError("Script not found", not_found=True)
            frame = next((item for item in script.frames if item.id == frame_id), None)
            if frame is None:
                raise ProjectContextError("Frame not found", not_found=True)
            characters = self._resolve_asset_layers(script, series, library)["characters"]
            speaker = self._resolve_dialogue_speaker(frame, characters)
            custom = None
            if speaker and speaker.voice_id:
                # Custom voices can belong to another series. Only the fields
                # actually passed to synthesis form a dependency.
                for value in self._committed["series.json"].values():
                    found = next((item for item in value.get("custom_voices", []) if item["id"] == speaker.voice_id), None)
                    if found:
                        custom = {key: found[key] for key in ("id", "target_model", "family")}
                        break
            return script, frame, speaker, custom

    def read_dialogue_audio_state(self, script_id: str, frame_id: str) -> Dict[str, Any]:
        """Check saved audio against one committed capture without generating or saving.

        Custom voices may live outside the project series. This is an on-demand
        check, not a subscription covered by the project's read version vector.
        """
        from .audio import _effective_dialogue_text

        with self._save_lock:
            context = self._read_dialogue_context(script_id, frame_id)
            script, frame, speaker, _ = context
            _, series, library = self.read_committed_project_view(script_id)
            result = {
                "project_id": script.id, "frame_id": frame.id,
                "read_versions": {
                    "project": script.revision,
                    "series": {"id": script.series_id, "revision": series.revision if series else None}
                    if script.series_id else None,
                    "library": library.revision,
                },
                "audio_url": frame.audio_url, "audio_error": frame.audio_error,
                "historical_instructions": frame.dialogue_instructions,
                "dialogue": _effective_dialogue_text(frame),
                "voice_id": speaker.voice_id if speaker else None,
                "options": frame.dialogue_audio_options.model_dump(),
                "resolved_request": None, "sources": None, "prepared": None,
                "freshness": "unknown", "reason": None, "error": None,
            }
            if not result["dialogue"]:
                result["reason"] = "dialogue_missing"
            elif speaker is None:
                result["reason"] = "speaker_missing"
            elif not speaker.voice_id:
                result["reason"] = "voice_missing"
            else:
                try:
                    prepared, resolved, sources = self._prepare_dialogue_context(context, frame.dialogue_audio_options)
                except UnsupportedAudioParameters as exc:
                    result.update(reason="unsupported_audio_parameters", error=deepcopy(exc.detail))
                    return result
                except ProjectContextError:
                    result["reason"] = "invalid_audio_parameters"
                    return result
                result.update(resolved_request=resolved, sources=sources,
                              prepared=prepared.to_snapshot() if prepared else None)
                snapshot = frame.dialogue_audio_snapshot
                if prepared is None:
                    result["reason"] = "tts_unavailable"
                elif not frame.audio_url:
                    result.update(freshness="missing", reason="audio_missing")
                elif frame.audio_error:
                    result.update(freshness="stale", reason="generation_failed")
                elif snapshot is None:
                    result["reason"] = "snapshot_missing"
                elif snapshot.schema_version != 1:
                    result["reason"] = "snapshot_version_unknown"
                elif snapshot.audio_url != frame.audio_url:
                    result["reason"] = "snapshot_media_mismatch"
                elif snapshot.prepared.get("rules_version") != prepared.rules_version:
                    result["reason"] = "rules_version_unknown"
                elif prepared_dialogue_is_stale(frame, prepared):
                    result.update(freshness="stale", reason="parameters_changed")
                else:
                    result.update(freshness="current", reason="parameters_match")
            return result

    @staticmethod
    def _dialogue_dependency(speaker: Optional[Character], custom, preparation):
        if speaker is None:
            return None
        prepared, resolved, _ = preparation
        actual = dialogue_prepared_projection(prepared.to_snapshot()) if prepared is not None else resolved
        return speaker.id, speaker.voice_id, custom, actual

    def _prepare_dialogue_context(self, context, options: DialogueAudioOptions):
        _, frame, speaker, custom = context
        if speaker is None:
            raise ProjectContextError("No speaker found for this dialogue")
        self.assert_storage_available()
        try:
            serialize_json(options.model_dump())
            preparation = self.audio_generator.prepare_dialogue(
                frame, speaker, options,
                model_override=custom["target_model"] if custom else None,
                family_override=custom["family"] if custom else None,
            )
            serialize_json(preparation[1])
            return preparation
        except UnsupportedAudioParameters:
            raise
        except (StorageError, ValueError, TypeError) as exc:
            # Storage lifecycle errors must retain their fail-closed response.
            self.assert_storage_available()
            raise ProjectContextError("Audio parameters must contain finite JSON values and valid UTF-8 text") from exc

    def _generate_dialogue_snapshot(self, context, options: DialogueAudioOptions, preparation) -> Script:
        script, original, speaker, custom = context
        prepared, resolved, sources = preparation
        dependency = self._dialogue_dependency(speaker, custom, preparation)
        candidate = original.model_copy(deep=True)
        candidate.dialogue_audio_options = options.model_copy(deep=True)
        self.audio_generator.generate_prepared_dialogue(candidate, speaker, prepared)
        try:
            if prepared is not None and candidate.audio_url and not candidate.audio_error and candidate.status == GenerationStatus.COMPLETED:
                candidate.dialogue_audio_snapshot = DialogueAudioSnapshot(
                    audio_url=candidate.audio_url, options=options.model_copy(deep=True),
                    sources=sources, resolved_request=resolved, prepared=prepared.to_snapshot(),
                )
            candidate = StoryboardFrame.model_validate(candidate.model_dump())
            serialize_json(candidate.model_dump())
        except (StorageError, ValueError, TypeError) as exc:
            raise ProjectGenerationError("Audio generation returned invalid data") from exc
        with self._save_lock:
            current = self._check_project_result(script)
            current_context = self._read_dialogue_context(script.id, original.id)
            _, _, current_speaker, current_custom = current_context
            try:
                current_preparation = self._prepare_dialogue_context(current_context, options)
            except (ProjectContextError, UnsupportedAudioParameters) as exc:
                raise StorageConflictError("Dialogue voice inputs changed while generating; reload before retrying") from exc
            if self._dialogue_dependency(current_speaker, current_custom, current_preparation) != dependency:
                raise StorageConflictError("Dialogue voice inputs changed while generating; reload before retrying")
            frame = next(item for item in current.frames if item.id == original.id)
            for key in ("audio_url", "audio_error", "status", "dialogue_voice_id", "dialogue_text_hash",
                        "dialogue_audio_options", "dialogue_audio_snapshot"):
                setattr(frame, key, getattr(candidate, key))
            self._save_data()
            return self.read_committed_script(script.id)

    def generate_dialogue_line(
        self,
        script_id: str,
        frame_id: str,
        speed: Any = _AUDIO_UNSET,
        pitch: Any = _AUDIO_UNSET,
        volume: Any = _AUDIO_UNSET,
        instructions: Any = _AUDIO_UNSET,
    ) -> Script:
        """Omission preserves desired overrides; explicit null returns to inheritance."""
        from .audio import _effective_dialogue_text

        context = self._read_dialogue_context(script_id, frame_id)
        patch = {key: value for key, value in {"speed": speed, "pitch": pitch, "volume": volume,
                                              "instructions": instructions}.items() if value is not _AUDIO_UNSET}
        try:
            options = DialogueAudioOptions.model_validate({**context[1].dialogue_audio_options.model_dump(), **patch})
            serialize_json(options.model_dump())
        except (ValueError, TypeError, StorageError) as exc:
            raise ProjectContextError("Audio overrides must have valid types, finite values and UTF-8 text") from exc
        if not _effective_dialogue_text(context[1]):
            return context[0]
        preparation = self._prepare_dialogue_context(context, options)
        return self._generate_dialogue_snapshot(context, options, preparation)

    def generate_dialogue_audio_batch(self, script_id: str) -> Tuple[Script, Dict[str, int], List[Dict[str, Any]]]:
        """Freeze membership; save each result before reporting its outcome."""
        from .audio import _effective_dialogue_text

        initial, _, _ = self.read_committed_project_view(script_id)
        if initial is None:
            raise ProjectContextError("Script not found", not_found=True)
        frame_ids = [frame.id for frame in initial.frames]
        stats = {"generated": 0, "skipped": 0, "failed": 0, "no_voice": 0}
        errors = []
        for frame_id in frame_ids:
            context = self._read_dialogue_context(script_id, frame_id)
            _, frame, speaker, _ = context
            if not _effective_dialogue_text(frame):
                continue
            if speaker is None or not speaker.voice_id:
                stats["no_voice"] += 1
                continue
            try:
                preparation = self._prepare_dialogue_context(context, frame.dialogue_audio_options)
                if frame.audio_url and not frame.audio_error and not prepared_dialogue_is_stale(frame, preparation[0]):
                    stats["skipped"] += 1
                    continue
                saved = self._generate_dialogue_snapshot(context, frame.dialogue_audio_options, preparation)
            except UnsupportedAudioParameters as exc:
                stats["failed"] += 1
                errors.append({"frame_id": frame_id, **exc.detail})
                continue
            except (StorageError, ProjectContextError):
                raise
            except Exception:
                logger.exception("Dialogue generation failed for frame %s", frame_id)
                stats["failed"] += 1
                continue
            result = next(item for item in saved.frames if item.id == frame_id)
            if result.audio_error or not result.audio_url or result.status != GenerationStatus.COMPLETED:
                stats["failed"] += 1
            else:
                stats["generated"] += 1
        script, _, _ = self.read_committed_project_view(script_id)
        if script is None:
            raise ProjectContextError("Script not found", not_found=True)
        return script, stats, errors

    def bind_voice(self, script_id: str, char_id: str, voice_id: str, voice_name: str) -> Script:
        return self._update_character_voice(script_id, char_id, {"voice_id": voice_id, "voice_name": voice_name})

    def update_voice_params(self, script_id: str, char_id: str, *, speed: float, pitch: float, volume: int) -> Script:
        return self._update_character_voice(script_id, char_id, {"voice_speed": speed, "voice_pitch": pitch, "voice_volume": volume})

    def _update_character_voice(self, script_id: str, char_id: str, patch: Dict[str, Any]) -> Script:
        """Write to the same episode/series/global character shown by project GET."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            character, source = self._find_asset_with_source(script, char_id, "character")
            if character is None:
                raise ValueError("Character not found")
            candidate = Character.model_validate({**character.model_dump(), **patch})
            _validate_setting_value(candidate)
            for key in patch:
                setattr(character, key, getattr(candidate, key))
            self._save_after_asset_mutation(source)
            return self.read_committed_script(script_id)

    def get_script(self, script_id: str) -> Optional[Script]:
        self.assert_storage_available()
        return self.scripts.get(script_id)

    def read_committed_script(self, script_id: str) -> Script:
        """A command response must not expose another operation's unsaved fields."""
        with self._save_lock:
            self.assert_storage_available()
            value = self._committed["projects.json"].get(script_id)
            if value is None:
                raise ValueError("Script not found")
            return Script.model_validate(deepcopy(value))

    def read_committed_projects(self) -> List[Script]:
        """Detached public read models; internal commands retain live getters."""
        with self._save_lock:
            self.assert_storage_available()
            return [Script.model_validate(deepcopy(value)) for value in self._committed["projects.json"].values()]

    def read_committed_series(self) -> List[Series]:
        with self._save_lock:
            self.assert_storage_available()
            return [Series.model_validate(deepcopy(value)) for value in self._committed["series.json"].values()]

    def _committed_series_response(self, series_id: str) -> Series:
        value = self._committed["series.json"].get(series_id)
        if value is None:
            raise ValueError("Series not found")
        return Series.model_validate(deepcopy(value))

    def _committed_library_asset_response(self, asset_type: str, asset_id: str):
        library = GlobalAssetLibrary.model_validate(deepcopy(self._committed["library_assets.json"]))
        field = {"character": "characters", "scene": "scenes", "prop": "props"}.get(asset_type)
        if field is None:
            raise ValueError(f"Invalid asset type: {asset_type}")
        asset = next((item for item in getattr(library, field) if item.id == asset_id), None)
        if asset is None:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found in library")
        return asset

    def _committed_library_asset_response_by_id(self, asset_id: str):
        library = GlobalAssetLibrary.model_validate(deepcopy(self._committed["library_assets.json"]))
        for field in ("characters", "scenes", "props"):
            asset = next((item for item in getattr(library, field) if item.id == asset_id), None)
            if asset is not None:
                return asset
        raise ValueError(f"Asset {asset_id} not found in library")

    def _committed_project_asset_response(self, script_id: str, asset_type: str, asset_id: str):
        value = self._committed["projects.json"].get(script_id)
        if value is None:
            raise ValueError("Script not found")
        script = Script.model_validate(deepcopy(value))
        field = {"character": "characters", "scene": "scenes", "prop": "props"}.get(asset_type)
        if field is None:
            raise ValueError(f"Invalid asset type: {asset_type}")
        asset = next((item for item in getattr(script, field) if item.id == asset_id), None)
        if asset is None:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found in project")
        return asset

    def read_committed_project_view(self, script_id: str) -> Tuple[Optional[Script], Optional[Series], GlobalAssetLibrary]:
        """Capture every layer used by the project presentation in one read."""
        with self._save_lock:
            self.assert_storage_available()
            value = self._committed["projects.json"].get(script_id)
            script = Script.model_validate(deepcopy(value)) if value is not None else None
            series_value = self._committed["series.json"].get(script.series_id) if script is not None else None
            series = Series.model_validate(deepcopy(series_value)) if series_value is not None else None
            library = GlobalAssetLibrary.model_validate(deepcopy(self._committed["library_assets.json"]))
            return script, series, library

    def read_committed_series_view(self, series_id: str) -> Tuple[Optional[Series], List[Script]]:
        """Capture series membership and its episode summaries together."""
        with self._save_lock:
            self.assert_storage_available()
            value = self._committed["series.json"].get(series_id)
            if value is None:
                return None, []
            series = Series.model_validate(deepcopy(value))
            projects = self._committed["projects.json"]
            episodes = [Script.model_validate(deepcopy(projects[key])) for key in series.episode_ids if key in projects]
            return series, episodes

    def update_script_text(self, script_id: str, text: str, *, expected_revision: int) -> Script:
        """Save a text edit only against the version the editor loaded."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            if script.revision != expected_revision:
                raise StorageConflictError("Project changed; reload before saving the text edit")
            if script.original_text != text:
                script.original_text = text
                script.updated_at = time.time()
                self._save_data()
            return self.read_committed_script(script_id)

    def update_audio_mix(self, script_id: str, updates: Dict[str, Any]) -> Script:
        """Merge an audio patch with current settings and return its committed snapshot."""
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            bgm_url = script.bgm_url
            if "bgm_url" in updates:
                bgm_url = updates["bgm_url"] or None  # Explicit null and empty string clear BGM.
            mix = dict(script.mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60})
            for track in ("dialogue", "bgm", "sfx"):
                volume = updates.get(f"{track}_volume")
                if volume is not None:
                    mix[track] = max(0, min(100, volume))
            if script.bgm_url != bgm_url or script.mix_settings != mix:
                script.bgm_url = bgm_url
                script.mix_settings = mix
                self._save_data()
            return self.read_committed_script(script_id)

    # Upload names remain distinct from legacy generation names. No implicit
    # promotion from a legacy channel into the canonical reference sheet.
    _CHARACTER_IMAGE_CHANNELS = {
        "reference_sheet": ("reference_sheet", None, None),
        "full_body": ("full_body", "full_body_asset", "full_body_image_url"),
        "three_view": ("three_views", "three_view_asset", "three_view_image_url"),
        "headshot": ("head_shot", "headshot_asset", "headshot_image_url"),
    }

    @staticmethod
    def _image_variants(container: ImageAsset | AssetUnit | None) -> List[ImageVariant]:
        if container is None:
            return []
        if isinstance(container, AssetUnit):
            return container.image_variants
        if isinstance(container, ImageAsset):
            return container.variants
        raise ValueError("Unsupported image container")

    @staticmethod
    def _selected_image(container: ImageAsset | AssetUnit | None) -> Optional[str]:
        if container is None:
            return None
        return container.selected_image_id if isinstance(container, AssetUnit) else container.selected_id

    @classmethod
    def _set_selected_image(cls, container: ImageAsset | AssetUnit, variant_id: Optional[str]) -> None:
        if cls._selected_image(container) == variant_id:
            return
        if isinstance(container, AssetUnit):
            container.selected_image_id = variant_id
            container.image_updated_at = time.time()
        else:
            container.selected_id = variant_id

    @classmethod
    def _selected_variant_url(cls, containers) -> Optional[str]:
        for container in containers:
            selected = cls._selected_image(container)
            variant = next((v for v in cls._image_variants(container) if v.id == selected), None)
            if variant is not None:
                return variant.url
        return None

    @classmethod
    def _variant_channels(cls, target, asset_type: str, generation_type: Optional[str] = None):
        if asset_type == "character":
            if generation_type not in (None, "all") and generation_type not in cls._CHARACTER_IMAGE_CHANNELS:
                raise ValueError(f"Invalid generation_type: {generation_type}")
            names = cls._CHARACTER_IMAGE_CHANNELS if generation_type in (None, "all") else [generation_type]
            return [
                (name, [getattr(target, field) for field in cls._CHARACTER_IMAGE_CHANNELS[name][:2] if field])
                for name in names
            ]
        if generation_type not in (None, "all", "image"):
            raise ValueError(f"Invalid generation_type for {asset_type}: {generation_type}")
        if asset_type == "storyboard_frame":
            return [("rendered_image", [target.rendered_image_asset]), ("image", [target.image_asset])]
        return [("image", [target.image_asset])]

    @classmethod
    def _sync_variant_urls(cls, target, asset_type: str, channel: str) -> None:
        if asset_type == "character":
            groups = dict(cls._variant_channels(target, asset_type))
            _, _, url_field = cls._CHARACTER_IMAGE_CHANNELS[channel]
            if url_field:
                value = cls._selected_variant_url(groups[channel])
                setattr(target, url_field, value)
                if channel == "headshot":
                    target.avatar_url = value
            # A populated sheet is authoritative; legacy channels keep their
            # own URLs, and remain a fallback when the sheet has no selection.
            target.image_url = next((
                url for group in groups.values() if (url := cls._selected_variant_url(group)) is not None
            ), None) or target.full_body_image_url or target.three_view_image_url or target.headshot_image_url
        elif asset_type == "storyboard_frame":
            if channel == "rendered_image":
                target.rendered_image_url = cls._selected_variant_url([target.rendered_image_asset])
            target.image_url = target.rendered_image_url or cls._selected_variant_url([target.image_asset])
        else:
            target.image_url = cls._selected_variant_url([target.image_asset])

    def _change_asset_variant(
        self, script_id: str, asset_id: str, asset_type: str, variant_id: str,
        operation: str, generation_type: Optional[str] = None, is_favorited: bool = False,
    ) -> Script:
        with self._save_lock:
            self.assert_storage_available()
            _, target, source = self._variant_target(script_id, asset_id, asset_type)
            groups = self._variant_channels(target, asset_type, generation_type)
            found = next((
                (channel, containers) for channel, containers in groups
                if any(v.id == variant_id for container in containers for v in self._image_variants(container))
            ), None)
            if found is None:
                raise ValueError(f"Variant {variant_id} not found")
            channel, containers = found
            before = target.model_dump()
            selected = None
            if operation == "select":
                selected = next(
                    v for container in containers for v in self._image_variants(container) if v.id == variant_id
                )
            for container in containers:
                variants = self._image_variants(container)
                variant = next((v for v in variants if v.id == variant_id), None)
                if variant is None:
                    continue
                if operation == "favorite":
                    variant.is_favorited = is_favorited
                elif operation == "delete":
                    remaining = [v for v in variants if v.id != variant_id]
                    if isinstance(container, AssetUnit):
                        container.image_variants = remaining
                    else:
                        container.variants = remaining
            if operation == "delete":
                # Keep a valid current choice before choosing a fallback.
                # Channel order makes AssetUnit authoritative over ImageAsset.
                selected = next((
                    v for container in containers for v in self._image_variants(container)
                    if v.id == self._selected_image(container)
                ), None)
                if selected is None:
                    selected = next((
                        variants[-1] for container in containers
                        if (variants := self._image_variants(container))
                    ), None)
            if operation != "favorite":
                # Old copies may have different histories. Reconcile only the
                # chosen candidate, without creating a missing legacy container.
                for container in containers:
                    if container is not None:
                        variants = self._image_variants(container)
                        if selected is not None and not any(v.id == selected.id for v in variants):
                            variants.append(selected.model_copy(deep=True))
                        self._set_selected_image(container, selected.id if selected else None)
                self._sync_variant_urls(target, asset_type, channel)
            if target.model_dump() != before:
                self._save_after_asset_mutation(source)
            return self.read_committed_script(script_id)

    def select_asset_variant(self, script_id: str, asset_id: str, asset_type: str, variant_id: str, generation_type: str = None) -> Script:
        return self._change_asset_variant(script_id, asset_id, asset_type, variant_id, "select", generation_type)

    def delete_asset_variant(self, script_id: str, asset_id: str, asset_type: str, variant_id: str) -> Script:
        """Remove variant metadata only; shared media and motion history remain."""
        return self._change_asset_variant(script_id, asset_id, asset_type, variant_id, "delete")

    def update_model_settings(self, script_id: str, t2i_model: str = None, i2i_model: str = None, i2v_model: str = None, r2v_model: str = None, character_aspect_ratio: str = None, scene_aspect_ratio: str = None, prop_aspect_ratio: str = None, storyboard_aspect_ratio: str = None, image_model: str = None) -> Script:
        """Keep legacy null/empty omission semantics, validating before assignment."""
        updates = {
            "t2i_model": t2i_model, "i2i_model": i2i_model, "i2v_model": i2v_model,
            "r2v_model": r2v_model, "image_model": image_model,
            "character_aspect_ratio": character_aspect_ratio, "scene_aspect_ratio": scene_aspect_ratio,
            "prop_aspect_ratio": prop_aspect_ratio, "storyboard_aspect_ratio": storyboard_aspect_ratio,
        }
        with self._save_lock:
            self.assert_storage_available()
            script = self.scripts.get(script_id)
            if script is None:
                raise ValueError("Script not found")
            patch = {key: value for key, value in updates.items() if value}
            candidate = ModelSettings.model_validate({**script.model_settings.model_dump(), **patch})
            _validate_setting_value(candidate)
            script.model_settings = candidate
            self._save_data()
            return self.read_committed_script(script_id)

    def toggle_variant_favorite(self, script_id: str, asset_id: str, asset_type: str, variant_id: str, is_favorited: bool, generation_type: str = None) -> Script:
        return self._change_asset_variant(
            script_id, asset_id, asset_type, variant_id, "favorite", generation_type, is_favorited,
        )

    # ============================================================
    # Series Storage & CRUD
    # ============================================================

    def create_series_asset(self, series_id: str, asset_type: str, *, name: str, description: Optional[str] = "", persona: Optional[str] = "", image_url: Optional[str] = None, voice_id: Optional[str] = None):
        """Create, validate and save under one command lock; keep the Cast schema."""
        from .models import Prop
        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if series is None:
                raise ValueError("Series not found")
            prefix = {"character": "char", "scene": "scene", "prop": "prop"}.get(asset_type)
            if prefix is None:
                raise ValueError("Invalid asset type")
            fields = {"id": f"{prefix}_{uuid.uuid4().hex[:12]}", "name": name, "description": description or ""}
            if asset_type == "character":
                sheet = AssetUnit()
                if image_url:
                    variant = ImageVariant(id=f"img_{uuid.uuid4().hex[:12]}", url=image_url)
                    sheet.image_variants.append(variant)
                    sheet.selected_image_id = variant.id
                asset = Character(**fields, persona=persona or "", voice_id=voice_id, reference_sheet=sheet)
            else:
                asset = (Scene if asset_type == "scene" else Prop)(**fields, image_url=image_url)
            field = {"character": "characters", "scene": "scenes", "prop": "props"}[asset_type]
            _validate_setting_value(asset)
            getattr(series, field).append(asset)
            series.updated_at = time.time()
            self._save_series_data()
            return type(asset).model_validate(deepcopy(self._committed["series.json"][series_id][field][-1]))

    def _load_series_data(self) -> Dict[str, Series]:
        data = read_json(self.series_data_file, dict)
        try:
            if not isinstance(data, dict):
                raise ValueError("Expected a series map")
            series = {key: Series.model_validate(value) for key, value in data.items()}
            if any(key != value.id for key, value in series.items()):
                raise ValueError("Series key does not match its identity")
            return series
        except (ValidationError, TypeError, ValueError) as exc:
            raise CorruptStorageError("Invalid series.json schema; existing data preserved") from exc

    def _save_series_data_unlocked(self):
        """Save series data without acquiring the lock (caller must hold self._save_lock)."""
        self._commit_storage("series.json")

    def _save_series_data(self):
        """Save series data with thread lock."""
        with self._save_lock:
            self._save_series_data_unlocked()

    # ============================================================
    # Global Asset Library Storage (project-independent shared pool)
    # ============================================================

    def _load_library_data(self) -> GlobalAssetLibrary:
        data = read_json(self.library_data_file, dict)
        try:
            if not isinstance(data, dict) or set(data) - set(GlobalAssetLibrary.model_fields):
                raise ValueError("Unknown library schema; explicit migration required")
            return GlobalAssetLibrary.model_validate(data)
        except (ValidationError, TypeError, ValueError) as exc:
            raise CorruptStorageError("Invalid library_assets.json schema; existing data preserved") from exc

    def _save_library_data_unlocked(self):
        """Save global library data without acquiring the lock (caller must hold self._save_lock)."""
        self._commit_storage("library_assets.json")

    def _save_library_data(self):
        """Save global library data with thread lock."""
        with self._save_lock:
            self._save_library_data_unlocked()

    # ------------------------------------------------------------------
    # Global Asset Library — CRUD + feed channels (LumenX Core shared pool)
    # ------------------------------------------------------------------
    # These methods are the single source of truth for mutating the
    # project-independent library. Both the /library/assets endpoints and
    # the Playground "录入资产库" flow call them, so the wiring stays
    # consistent. The library is curated/opt-in (anti-bloat): nothing is
    # auto-ingested here.

    def _library_list_for_type(self, asset_type: str) -> List:
        """Return the live list backing the given asset type in the global
        library (so callers can append/iterate). Raises on unknown type."""
        if asset_type == "character":
            return self.library_store.characters
        elif asset_type == "scene":
            return self.library_store.scenes
        elif asset_type == "prop":
            return self.library_store.props
        raise ValueError(f"Invalid asset type: {asset_type}")

    def _find_library_asset(self, asset_type: str, asset_id: str):
        """Locate a global library asset by (type, id). Raises ValueError
        when the type is invalid or the id is absent."""
        target_list = self._library_list_for_type(asset_type)
        asset = next((a for a in target_list if a.id == asset_id), None)
        if asset is None:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found in library")
        return asset

    def list_library_assets(self) -> GlobalAssetLibrary:
        """Capture the public library assets and revision from one saved state."""
        with self._save_lock:
            self.assert_storage_available()
            return GlobalAssetLibrary.model_validate(deepcopy(self._committed["library_assets.json"]))

    def create_library_asset(self, asset_type: str, payload: Dict[str, Any], *, source_key: Optional[str] = None):
        """Create a new global library asset of `asset_type`
        ("character" | "scene" | "prop") from a plain payload dict, persist
        it, and return the created asset object.

        Mirrors the series quick-create endpoints
        (create_series_character/scene/prop) but targets the
        project-independent global pool. Tolerates a partial payload (used
        by the Playground录入 flow, which calls this directly rather than
        through a request model). Recognized payload keys: name,
        description, image_url, persona (characters), voice_id
        (characters)."""
        from .models import Character, Scene, Prop, AssetUnit, ImageVariant
        with self._save_lock:
            self.assert_storage_available()
            source_id = f"pg_{uuid.uuid5(uuid.NAMESPACE_URL, source_key).hex}" if source_key is not None else None
            if source_id:
                for pool in (self.library_store.characters, self.library_store.scenes, self.library_store.props):
                    existing = next((item for item in pool if item.id == source_id), None)
                    if existing is not None:
                        return self._committed_library_asset_response_by_id(existing.id)
            payload = dict(payload or {})
            name = payload.get("name") or "未命名"
            description = payload.get("description") or ""
            image_url = payload.get("image_url")
            if asset_type == "character":
                ref_sheet = AssetUnit()
                if image_url:
                    variant = ImageVariant(id=f"img_{uuid.uuid4().hex[:12]}", url=image_url)
                    ref_sheet.image_variants.append(variant)
                    ref_sheet.selected_image_id = variant.id
                asset = Character(
                    id=source_id or f"char_{uuid.uuid4().hex[:12]}",
                    name=name,
                    description=description,
                    persona=payload.get("persona") or "",
                    voice_id=payload.get("voice_id"),
                    reference_sheet=ref_sheet,
                )
            elif asset_type == "scene":
                asset = Scene(
                    id=source_id or f"scene_{uuid.uuid4().hex[:12]}",
                    name=name,
                    description=description,
                    image_url=image_url,
                )
            elif asset_type == "prop":
                asset = Prop(
                    id=source_id or f"prop_{uuid.uuid4().hex[:12]}",
                    name=name,
                    description=description,
                    image_url=image_url,
                )
            else:
                raise ValueError(f"Invalid asset type: {asset_type}")
            self._library_list_for_type(asset_type).append(asset)
            self._save_library_data_unlocked()
            return self._committed_library_asset_response(asset_type, asset.id)

    def update_library_asset(self, asset_type: str, asset_id: str, patch: Dict[str, Any]):
        """Patch attributes of a global library asset and persist. Mirrors
        update_series_asset_attributes — only sets keys that exist on the
        asset, and never touches id/status (use create/delete to manage
        those)."""
        with self._save_lock:
            asset = self._find_library_asset(asset_type, asset_id)
            for key, value in (patch or {}).items():
                if hasattr(asset, key) and key not in ("id", "status"):
                    setattr(asset, key, value)
            self._save_library_data_unlocked()
            return self._committed_library_asset_response(asset_type, asset_id)

    def _scan_library_asset_references(self, asset_type: str, asset_id: str) -> List[Dict[str, Any]]:
        """Find every storyboard frame (across all projects and series) that
        references the given asset id through the type-appropriate field:
        scene -> frame.scene_id, character -> frame.character_ids,
        prop -> frame.prop_ids. Returns a list of referrer descriptors
        (empty when nothing references it). Used by delete_library_asset for
        design Q2 reference integrity.

        Note: Series currently hold no frames of their own (their frames live
        in episode Scripts, which are in self.scripts), so the series loop is
        a defensive no-op today via getattr — kept so the scan stays correct
        if Series ever gains a frames list."""
        references: List[Dict[str, Any]] = []

        def _frame_hits(frame) -> bool:
            if asset_type == "scene":
                return getattr(frame, "scene_id", None) == asset_id
            if asset_type == "character":
                return asset_id in (getattr(frame, "character_ids", None) or [])
            if asset_type == "prop":
                return asset_id in (getattr(frame, "prop_ids", None) or [])
            return False

        def _scan(owner_kind: str, owner_id: str, owner, frames) -> None:
            for frame in frames or []:
                if _frame_hits(frame):
                    references.append({
                        "owner_kind": owner_kind,
                        "owner_id": owner_id,
                        "owner_title": getattr(owner, "title", None),
                        "frame_id": getattr(frame, "id", None),
                    })

        for sid, script in (getattr(self, "scripts", {}) or {}).items():
            _scan("project", sid, script, getattr(script, "frames", None))
        for sid, series in (getattr(self, "series_store", {}) or {}).items():
            _scan("series", sid, series, getattr(series, "frames", None))
        return references

    def delete_library_asset(self, asset_type: str, asset_id: str, force: bool = False) -> None:
        """Hard-delete a global library asset.

        Design Q2 (reference integrity): unless ``force`` is True, scan all
        project/series storyboard frames first; if any still reference this
        asset (scene_id / character_ids / prop_ids) the delete is refused via
        ``LibraryAssetInUseError`` (API maps to HTTP 409 and lists referrers).
        With ``force=True`` the asset is removed anyway, leaving those frame
        references dangling (the asset resolver simply drops the unknown id).

        Raises ValueError when the asset (or asset type) is absent — this is
        checked BEFORE the reference scan so a missing id still maps to 404."""
        with self._save_lock:
            target_list = self._library_list_for_type(asset_type)
            if not any(a.id == asset_id for a in target_list):
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found in library")
            if not force:
                refs = self._scan_library_asset_references(asset_type, asset_id)
                if refs:
                    raise LibraryAssetInUseError(asset_type, asset_id, refs)
            kept = [a for a in target_list if a.id != asset_id]
            if asset_type == "character":
                self.library_store.characters = kept
            elif asset_type == "scene":
                self.library_store.scenes = kept
            else:  # prop
                self.library_store.props = kept
            self._save_library_data_unlocked()

    def promote_asset_to_library(self, source_kind: str, source_id: str, asset_type: str, asset_id: str):
        """Deep-copy an asset from a Project (episode) or Series into the
        global library with a fresh id, persist, and return the new asset.

        Reuses the import_assets_from_series deepcopy + new-uuid pattern.
        The source asset is left intact (D1 活引用: promotion is additive;
        fork-on-use of the original is a documented follow-up, design Q3).
        `source_kind` ∈ {"project", "series"}."""
        import copy
        if asset_type not in ("character", "scene", "prop"):
            raise ValueError(f"Invalid asset type: {asset_type}")
        with self._save_lock:
            if source_kind == "series":
                container = self.series_store.get(source_id)
                if not container:
                    raise ValueError("Source series not found")
            elif source_kind == "project":
                container = self.scripts.get(source_id)
                if not container:
                    raise ValueError("Source project not found")
            else:
                raise ValueError(f"Invalid source kind: {source_kind}")

            if asset_type == "character":
                src_list = container.characters
            elif asset_type == "scene":
                src_list = container.scenes
            else:  # prop
                src_list = container.props
            source_asset = next((a for a in src_list if a.id == asset_id), None)
            if source_asset is None:
                raise ValueError(
                    f"Asset {asset_id} of type {asset_type} not found in {source_kind} {source_id}"
                )

            new_asset = copy.deepcopy(source_asset)
            if isinstance(new_asset, Character):
                new_asset.reference_sheet_request_id = None
                new_asset.full_body_request_id = None
                new_asset.three_view_request_id = None
                new_asset.headshot_request_id = None
                new_asset.full_body_motion_request_id = None
                new_asset.head_shot_motion_request_id = None
            elif isinstance(new_asset, (Scene, Prop)):
                new_asset.image_request_id = None
                new_asset.motion_request_id = None
            if new_asset.status == GenerationStatus.PROCESSING:
                new_asset.status = "needs_confirmation"
            new_asset.id = str(uuid.uuid4())
            self._library_list_for_type(asset_type).append(new_asset)
            self._save_library_data_unlocked()
            return self._committed_library_asset_response(asset_type, new_asset.id)

    def fork_library_asset_to_project(self, script_id: str, asset_type: str, library_asset_id: str):
        """Deep-copy a *global library* asset into a project's local asset list
        with a fresh id, persist the project, and return the new (now
        project-owned) asset.

        This is the inverse direction of promote_asset_to_library and the
        "按需 fork" of design Q3: under D1 活引用 semantics a project references
        shared library assets live; forking materializes an independent,
        editable local copy so subsequent edits no longer touch the shared
        original. The source library asset is left intact (additive).

        Raises ValueError when the project, asset type, or library asset is
        absent. ``asset_type`` ∈ {"character", "scene", "prop"}."""
        import copy
        if asset_type not in ("character", "scene", "prop"):
            raise ValueError(f"Invalid asset type: {asset_type}")
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError(f"Project not found: {script_id}")
            # _find_library_asset raises ValueError when the id/type is absent.
            source_asset = self._find_library_asset(asset_type, library_asset_id)
            new_asset = copy.deepcopy(source_asset)
            prefix = {"character": "char", "scene": "scene", "prop": "prop"}[asset_type]
            new_asset.id = f"{prefix}_{uuid.uuid4().hex[:12]}"
            if asset_type == "character":
                new_asset.reference_sheet_request_id = None
                new_asset.full_body_request_id = None
                new_asset.three_view_request_id = None
                new_asset.headshot_request_id = None
                new_asset.full_body_motion_request_id = None
                new_asset.head_shot_motion_request_id = None
            else:
                new_asset.image_request_id = None
                new_asset.motion_request_id = None
            if new_asset.status == GenerationStatus.PROCESSING:
                new_asset.status = "needs_confirmation"
            if asset_type == "character":
                script.characters.append(new_asset)
            elif asset_type == "scene":
                script.scenes.append(new_asset)
            else:  # prop
                script.props.append(new_asset)
            script.updated_at = time.time()
            self._save_data()
            return self._committed_project_asset_response(script_id, asset_type, new_asset.id)

    def create_series(self, title: str, description: str = "", workflow_mode: str = "i2v_legacy", content_mode: str = "scripted", default_generation_mode: str = "r2v") -> Series:
        """Create a new Series."""
        with self._save_lock:
            series = Series(
                id=str(uuid.uuid4()),
                title=title,
                description=description,
                workflow_mode=workflow_mode,
                content_mode=content_mode,
                default_generation_mode=default_generation_mode,
                created_at=time.time(),
                updated_at=time.time(),
            )
            self.series_store[series.id] = series
            self._save_series_data_unlocked()
            return self._committed_series_response(series.id)

    def get_series(self, series_id: str) -> Optional[Series]:
        self.assert_storage_available()
        return self.series_store.get(series_id)

    def list_series(self) -> List[Series]:
        self.assert_storage_available()
        return list(self.series_store.values())

    def update_series(self, series_id: str, updates: Dict[str, Any], *, expected_revision: Optional[int] = None) -> Series:
        """Update Series fields (title, description, etc.)."""
        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            if expected_revision is not None and series.revision != expected_revision:
                raise StorageConflictError("Series changed; reload before saving the edit")
            changed = False
            for key, value in updates.items():
                if hasattr(series, key) and key not in ("id", "created_at", "episode_ids", "revision", "episode_creation_receipts"):
                    if key == "art_direction" and isinstance(value, dict):
                        value = ArtDirection(**value)
                    if getattr(series, key) != value:
                        setattr(series, key, value)
                        changed = True
            if changed:
                series.updated_at = time.time()
                self._save_series_data_unlocked()
            return Series.model_validate(deepcopy(self._committed["series.json"][series_id]))

    def update_series_model_settings(self, series_id: str, updates: Dict[str, Optional[str]]) -> Series:
        """Merge model fields while keeping concurrent patches to other fields."""
        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if series is None:
                raise ValueError("Series not found")
            patch = {key: value for key, value in updates.items() if key in ModelSettings.model_fields and value is not None}
            settings = series.model_settings.model_copy(update=patch)
            return self.update_series(series_id, {"model_settings": settings})

    def delete_series(self, series_id: str) -> None:
        """Delete a Series and disassociate its episodes."""
        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            # Disassociate episodes
            for ep_id in series.episode_ids:
                script = self.scripts.get(ep_id)
                if script and (not script.series_id or script.series_id == series_id):
                    script.series_id = None
                    script.episode_number = None
            del self.series_store[series_id]
            self._commit_storage("projects.json", "series.json")

    def add_episode_to_series(self, series_id: str, script_id: str, episode_number: Optional[int] = None) -> Series:
        """Add an existing Script/Project as an Episode to a Series."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            # If script already belongs to another series, remove it from the old one
            if script.series_id and script.series_id != series_id:
                old_series = self.series_store.get(script.series_id)
                if old_series and script_id in old_series.episode_ids:
                    old_series.episode_ids.remove(script_id)
            if script_id not in series.episode_ids:
                series.episode_ids.append(script_id)
            script.series_id = series_id
            script.episode_number = episode_number or len(series.episode_ids)
            series.updated_at = time.time()
            self._commit_storage("projects.json", "series.json")
            return self._committed_series_response(series_id)

    def read_series_episode_creation(self, series_id: str, client_request_id: str) -> SeriesEpisodeCreationReceipt:
        """Read the original result, including after its episode was removed."""
        validate_client_request_id(client_request_id)
        with self._save_lock:
            self.assert_storage_available()
            series = self._committed["series.json"].get(series_id)
            if series is None:
                raise ValueError("Series not found")
            for receipt in series.get("episode_creation_receipts", []):
                if receipt["client_request_id"] == client_request_id:
                    return SeriesEpisodeCreationReceipt.model_validate(deepcopy(receipt))
            raise ValueError("Episode creation request not found")

    def create_series_episode(self, series_id: str, request: CreateSeriesEpisodeRequest) -> SeriesEpisodeCreationReceipt:
        """Commit a blank episode, its association and replay identity together."""
        fingerprint = episode_creation_fingerprint(series_id, request)
        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if series is None:
                raise ValueError("Series not found")
            for receipt in series.episode_creation_receipts:
                if receipt.client_request_id == request.client_request_id:
                    if receipt.request_fingerprint != fingerprint:
                        raise StorageConflictError("Episode creation identity was already used for another request")
                    return self.read_series_episode_creation(series_id, request.client_request_id)
            if series.revision != request.expected_revision:
                raise StorageConflictError("Series changed; reload before creating the episode")
            number = max((
                self.scripts[episode_id].episode_number or 0
                for episode_id in series.episode_ids if episode_id in self.scripts
            ), default=0) + 1
            script = self.script_processor.create_draft_script(request.title, "")
            script.workflow_mode = series.workflow_mode
            script.default_generation_mode = series.default_generation_mode
            script.series_id = series_id
            script.episode_number = number
            receipt = SeriesEpisodeCreationReceipt(
                series_id=series_id, client_request_id=request.client_request_id,
                request_fingerprint=fingerprint, episode_id=script.id,
                episode_number=number, title=script.title, created_at=time.time(),
            )
            self.scripts[script.id] = script
            series.episode_ids.append(script.id)
            series.episode_creation_receipts.append(receipt)
            series.updated_at = time.time()
            self._commit_storage("projects.json", "series.json")
            return self.read_series_episode_creation(series_id, request.client_request_id)

    def remove_episode_from_series(self, series_id: str, script_id: str) -> Series:
        """Remove an Episode from a Series (does not delete the project)."""
        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            script = self.scripts.get(script_id)
            if script and script.series_id and script.series_id != series_id:
                raise StorageConflictError("Episode belongs to another series; reload before removing it")
            if script_id in series.episode_ids:
                series.episode_ids.remove(script_id)
            if script:
                script.series_id = None
                script.episode_number = None
            series.updated_at = time.time()
            self._commit_storage("projects.json", "series.json")
            return self._committed_series_response(series_id)

    # ─────────────────────────────────────────────────────────────
    # PR-3h/i · Custom voice (clone + design) management
    # Per Q16.1: series-level pool. Episodes / characters in the series
    # share access via VoicePickerModal's 我的复刻 / 我的设计 tabs.
    # ─────────────────────────────────────────────────────────────

    def create_voice_clone(
        self,
        series_id: str,
        audio_url: str,
        label: str,
        target_model: str = "cosyvoice-v3.5-plus",
    ) -> 'CustomVoice':
        """Clone a voice from a reference audio URL via dashscope customization.

        Calls /services/audio/tts/customization with model='voice-enrollment'
        action='create_voice'. Persists the returned voice_id under
        series.custom_voices[]. Returns the CustomVoice entry.

        Per doc: audio must be ≤10MB, MP3/WAV/M4A, ≥16kHz, 10-20s recommended.
        Frontend should pre-validate before calling.
        """
        import requests
        from .models import CustomVoice  # local import to avoid circular

        def text(name: str, value: str) -> str:
            if not isinstance(value, str) or not value.strip():
                raise CommandValidationError(f"{name} must be a non-empty string")
            return value.strip()

        def validate_audio_source(value: str) -> None:
            try:
                parsed = urlsplit(value)
            except ValueError as exc:
                raise CommandValidationError("audio_url must be a valid media reference") from exc
            # Minimal source bound: existing managed relative references remain
            # valid for this slice; explicit remote URLs must be http(s), and
            # explicit unsafe/local schemes are rejected before paid provider I/O.
            if parsed.scheme and parsed.scheme not in {"http", "https"}:
                raise CommandValidationError("audio_url must be a managed relative reference or an http(s) URL")
            if parsed.scheme in {"http", "https"}:
                try:
                    hostname = parsed.hostname
                except ValueError as exc:
                    raise CommandValidationError("audio_url http(s) URL must include a valid host") from exc
                if not parsed.netloc or not hostname:
                    raise CommandValidationError("audio_url http(s) URL must include a host")

        series_id = text("series_id", series_id)
        audio_url = text("audio_url", audio_url)
        validate_audio_source(audio_url)
        label = text("label", label)
        target_model = text("target_model", target_model)
        try:
            serialize_json({
                "series_id": series_id,
                "audio_url": audio_url,
                "label": label,
                "target_model": target_model,
            })
        except (StorageError, ValueError, TypeError) as exc:
            raise CommandValidationError("Voice clone inputs must contain valid JSON and UTF-8 text") from exc

        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if not series or series_id not in self._committed["series.json"]:
                raise ValueError(f"Series not found: {series_id}")
            series_ref = series

            env = self.environment_snapshot()
            api_key = (os.environ if env is None else env).get("DASHSCOPE_API_KEY")
            if not api_key:
                raise RuntimeError("DASHSCOPE_API_KEY not configured")

            url = f"{get_provider_base_url('DASHSCOPE', env=env)}/api/v1/services/audio/tts/customization"
            payload = {
                "model": "voice-enrollment",
                "input": {
                    "action": "create_voice",
                    "target_model": target_model,
                    "prefix": label[:20],  # API has prefix length limit
                    "url": audio_url,
                },
            }
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        logger.info(f"[voice/clone] creating voice for series={series_id} label='{label}' target={target_model}")
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            logger.error("[voice/clone] dashscope error status=%s", resp.status_code)
            raise RuntimeError(f"Voice clone failed: HTTP {resp.status_code}")

        try:
            data = resp.json()
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Voice clone response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Voice clone response was not a JSON object")
        output = data.get("output") if isinstance(data, dict) else None
        output = output if isinstance(output, dict) else {}
        voice_id = output.get("voice") or output.get("voice_id") or data.get("voice")
        if not isinstance(voice_id, str) or not voice_id.strip():
            logger.error("[voice/clone] invalid voice_id in provider response")
            raise RuntimeError("Voice clone succeeded but voice_id was invalid")
        voice_id = voice_id.strip()
        custom = CustomVoice(
            id=voice_id,
            label=label,
            origin="clone",
            target_model=target_model,
            family="cosyvoice",  # PR-3h hardcodes CosyVoice clone target
            source_audio_url=audio_url,
        )
        try:
            serialize_json(custom.model_dump(mode="json"))
        except (StorageError, ValueError, TypeError) as exc:
            logger.error("[voice/clone] invalid voice_id in provider response")
            raise RuntimeError("Voice clone succeeded but voice_id was invalid") from exc

        with self._save_lock:
            self.assert_storage_available()
            series = self.series_store.get(series_id)
            if series is not series_ref:
                raise StorageConflictError("Series changed during voice clone; reload before saving the cloned voice")
            if any(existing.id == voice_id for existing in (series.custom_voices or [])):
                raise StorageConflictError("Custom voice already exists")
            if series.custom_voices is None:
                series.custom_voices = []
            series.custom_voices.append(custom)
            series.updated_at = time.time()
            self._save_series_data_unlocked()
            logger.info(f"[voice/clone] success voice_id={voice_id} stored on series={series_id}")
            committed = next(voice for voice in self._committed["series.json"][series_id]["custom_voices"] if voice["id"] == voice_id)
            return CustomVoice.model_validate(deepcopy(committed))

    def list_custom_voices(self, series_id: str) -> List['CustomVoice']:
        """Return all custom voices in a series (clones + designs).
        Empty list if series has none or doesn't exist."""
        series = self.series_store.get(series_id)
        if not series:
            return []
        return list(series.custom_voices or [])

    def delete_custom_voice(self, series_id: str, voice_id: str) -> bool:
        """Remove a custom voice entry. Returns True if removed, False if
        not found. Note: does NOT call dashscope to delete the underlying
        voice (the platform allows re-use for 24h; cleanup is best-effort)."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series or not series.custom_voices:
                return False
            before = len(series.custom_voices)
            series.custom_voices = [v for v in series.custom_voices if v.id != voice_id]
            removed = before != len(series.custom_voices)
            if removed:
                series.updated_at = time.time()
                self._save_series_data_unlocked()
            return removed

    def find_custom_voice(self, voice_id: str) -> Optional['CustomVoice']:
        """Search all series for a custom voice by voice_id. Used by
        /voice/preview to resolve target_model for cloned/designed voices
        (which aren't in the static TTS_VOICE_REGISTRY)."""
        for series in self.series_store.values():
            for cv in (series.custom_voices or []):
                if cv.id == voice_id:
                    return cv
        return None

    # ─────────────────────────────────────────────────────────────
    # PR-3i · Voice design (iterate: prompt → preview → accept)
    # Unlike clone (audio-driven, 1 shot), design is text-driven and
    # users naturally iterate. Each preview mints a new voice on
    # dashscope; we only persist the voice the user explicitly accepts.
    # ─────────────────────────────────────────────────────────────

    def voice_design_preview(
        self,
        voice_prompt: str,
        preview_text: str,
        target_model: str = "cosyvoice-v3.5-plus",
    ) -> Dict[str, Any]:
        """Mint a new design voice via dashscope (preview returned inline).

        Per dashscope contract: create_voice with voice_prompt MUST be paired
        with preview_text in the same call; the API returns both the voice_id
        and a preview audio URL. We download the URL into our cache dir so
        the frontend can play it through the same /files static mount used
        by /voice/preview.

        Does NOT persist; user iterates by re-calling with tweaked params.
        """
        self.assert_storage_available()
        import requests
        import hashlib

        env = self.environment_snapshot()
        api_key = (os.environ if env is None else env).get("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY not configured")

        url = f"{get_provider_base_url('DASHSCOPE', env=env)}/api/v1/services/audio/tts/customization"
        payload = {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": target_model,
                "prefix": "design",
                "voice_prompt": voice_prompt[:500],
                "preview_text": (preview_text or "你好，这是一段音色测试。")[:200],
            },
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        logger.info(f"[voice/design] preview voice_prompt='{voice_prompt[:60]}…' target={target_model}")
        # dashscope voice design has variable latency (10-60s); the customization
        # service occasionally returns its own timeout. Retry once on 5xx/timeout.
        resp = None
        last_err = None
        for attempt in range(2):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=120)
                if resp.status_code == 200:
                    break
                last_err = f"HTTP {resp.status_code} — {resp.text[:200]}"
                if resp.status_code < 500 and "Timeout" not in (resp.text or ""):
                    break  # client error, don't retry
                logger.warning(f"[voice/design] attempt {attempt+1} failed: {last_err}; retrying")
            except requests.RequestException as e:
                last_err = str(e)
                logger.warning(f"[voice/design] attempt {attempt+1} network error: {e}; retrying")
        if resp is None or resp.status_code != 200:
            logger.error(f"[voice/design] all attempts failed: {last_err}")
            raise RuntimeError(f"Voice design failed: {last_err}")

        data = resp.json()
        output = data.get("output", {}) or {}
        voice_id = output.get("voice") or output.get("voice_id") or data.get("voice")
        remote_preview = output.get("preview_audio") or output.get("preview_audio_url") or output.get("audio_url")
        if not voice_id:
            logger.error(f"[voice/design] no voice_id in response: {data}")
            raise RuntimeError(f"Voice design API returned no voice_id: {data}")

        voice_id_str = str(voice_id)

        cache_dir = self._output_subdir("cache", "voice_design_preview")
        cache_key = hashlib.md5(f"{voice_id_str}|{preview_text}".encode("utf-8")).hexdigest()
        cache_path = os.path.join(cache_dir, f"{cache_key}.mp3")

        if remote_preview:
            # Download the dashscope-served preview into our cache.
            try:
                audio_resp = requests.get(remote_preview, timeout=60)
                audio_resp.raise_for_status()
                with open(cache_path, "wb") as f:
                    f.write(audio_resp.content)
            except Exception as e:
                logger.warning(f"[voice/design] preview download failed, falling back to local TTS: {e}")
                remote_preview = None

        if not remote_preview:
            if not self.audio_generator.tts:
                raise RuntimeError("TTS unavailable; cannot synthesize preview")
            self.audio_generator.tts.synthesize(
                text=preview_text,
                output_path=cache_path,
                voice=voice_id_str,
                model_override=target_model,
                family_override="cosyvoice",
            )

        preview_url = f"cache/voice_design_preview/{cache_key}.mp3"
        return {"voice_id": voice_id_str, "preview_url": preview_url, "target_model": target_model}

    def voice_design_save(
        self,
        series_id: str,
        voice_id: str,
        voice_prompt: str,
        label: str,
        target_model: str = "cosyvoice-v3.5-plus",
    ) -> 'CustomVoice':
        """Persist a previewed design voice into series.custom_voices[]."""
        from .models import CustomVoice

        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError(f"Series not found: {series_id}")

            existing = next(
                (cv for cv in (series.custom_voices or []) if cv.id == voice_id),
                None,
            )
            if existing:
                logger.info(f"[voice/design] save: voice_id={voice_id} already exists; returning existing")
                return existing

            custom = CustomVoice(
                id=voice_id,
                label=label,
                origin="design",
                target_model=target_model,
                family="cosyvoice",
                voice_prompt=voice_prompt[:500],
            )
            if series.custom_voices is None:
                series.custom_voices = []
            series.custom_voices.append(custom)
            series.updated_at = time.time()
            self._save_series_data_unlocked()
            logger.info(f"[voice/design] saved voice_id={voice_id} to series={series_id}")
            return custom

    def translate_character_to_voice_prompt(self, description: str) -> str:
        """LLM helper: convert a character description into a CosyVoice
        voice_prompt suitable for /services/audio/tts/customization.

        The prompt should describe vocal qualities (timbre, pace, age, mood)
        in concise Chinese. CosyVoice voice_prompt cap is 500 chars; we
        target ~120-200 to leave headroom for tone hints.
        """
        self.assert_storage_available()
        from .llm_adapter import LLMAdapter

        adapter = LLMAdapter(**({"env": self.environment_snapshot()} if getattr(self, "runtime_environment", None) is not None else {}))
        if not adapter.is_configured:
            raise RuntimeError("LLM adapter not configured (missing DASHSCOPE_API_KEY)")

        system_prompt = (
            "你是一个语音设计师，擅长将角色设定转化为简洁的中文音色描述。"
            "输出要求："
            "1. 只描述音色、语速、年龄、情绪，不要描写外貌或剧情。"
            "2. 用 100-200 字中文，单段无标题，不带引号或多余说明。"
            "3. 重点：性别·年龄·音色质感·语速·气质氛围。"
        )
        user_prompt = f"角色设定：\n{description.strip()[:1000]}\n\n请输出音色描述。"

        text = adapter.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return (text or "").strip()[:500]

    def get_series_episodes(self, series_id: str) -> List[Script]:
        """Get all Episodes belonging to a Series, in order."""
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")
        episodes = []
        for ep_id in series.episode_ids:
            script = self.scripts.get(ep_id)
            if script:
                episodes.append(script)
        return episodes

    @staticmethod
    def _resolve_asset_layers(episode: Script, series: Optional[Series], library: GlobalAssetLibrary) -> Dict[str, List]:
        """Resolve explicit layers without consulting mutable runtime stores."""
        resolved = {}
        for name in ("characters", "scenes", "props"):
            items = list(getattr(episode, name))
            for layer in (series, library):
                if layer is not None:
                    seen = {item.id for item in items}
                    items.extend(item for item in getattr(layer, name) if item.id not in seen)
            resolved[name] = items
        return resolved

    def resolve_episode_assets(self, episode: Script, series: Optional[Series] = None) -> Dict[str, List]:
        """Merge assets by ID with Episode > Series > Global priority."""
        if series is None and episode.series_id:
            series = self.series_store.get(episode.series_id)
        return self._resolve_asset_layers(episode, series, self.library_store)

    # ============================================================
    # File Import & Episode Splitting
    # ============================================================

    def import_file_and_split(self, text: str, suggested_episodes: int = 3) -> List[Dict]:
        """Split text into episodes using LLM. Returns episode preview data."""
        self.assert_storage_available()
        return self.script_processor.split_into_episodes(text, suggested_episodes)

    def preview_series_import(self, text: str, filename: str, suggested_episodes: int) -> Dict:
        # Reserve capacity before paid analysis, without holding the storage lock during the call.
        with self._save_lock:
            self.assert_storage_available()
            now = time.time()
            pending = sum(item.status == "preview" and item.expires_at > now for item in self.import_receipts.values())
            if pending + self._import_preview_inflight >= MAX_PENDING_IMPORTS:
                raise ImportPreviewUnavailable("Too many pending imports; finish an import or retry after previews expire", 429)
            self._import_preview_inflight += 1
        try:
            episodes = validate_episodes(self.import_file_and_split(text, suggested_episodes))
            with self._save_lock:
                self.assert_storage_available()
                now = time.time()
                retained = {key: item for key, item in self.import_receipts.items()
                            if item.status == "completed" or item.expires_at > now}
                import_id = str(uuid.uuid4())
                retained[import_id] = ImportRecord(import_id=import_id, created_at=now,
                                                  expires_at=now + IMPORT_PREVIEW_TTL_SECONDS, text=text)
                self.import_receipts = retained
                self._commit_storage("import_receipts.json")
                return {"filename": filename, "text_length": len(text), "suggested_episodes": suggested_episodes,
                        "episodes": episodes, "import_id": import_id}
        finally:
            with self._save_lock:
                self._import_preview_inflight -= 1

    def _read_import_record(self, import_id: str) -> ImportRecord:
        record = self.import_receipts.get(import_id)
        if record is None:
            raise ImportPreviewUnavailable("Import preview not found; analyze the file again", 404)
        if record.status == "preview" and record.expires_at <= time.time():
            raise ImportPreviewUnavailable("Import preview expired; analyze the file again", 410)
        return record

    def read_series_import(self, import_id: str) -> Dict:
        """Read recovery state without modifying storage or rerunning the LLM."""
        with self._save_lock:
            self.assert_storage_available()
            record = self._read_import_record(import_id)
            result = {"import_id": import_id, "status": record.status}
            if record.result is not None:
                result["result"] = record.result.model_dump(mode="json")
            else:
                result["expires_at"] = record.expires_at
            return result

    def create_series_from_import(self, title: str, text: str, episodes_data: List[Dict],
                                   description: str = "", *, import_id: str = "") -> Dict:
        """Create a Series with Episodes from import data.
        episodes_data: list of dicts with episode_number, title, start_marker, end_marker."""
        with self._save_lock:
            self.assert_storage_available()
            record = self._read_import_record(import_id) if import_id else None
            fingerprint = import_fingerprint(title, description, episodes_data) if record else None
            if record is not None:
                if record.result is not None:
                    if fingerprint != record.request_fingerprint:
                        raise StorageConflictError("Import identity was already confirmed with different input")
                    return record.result.model_dump(mode="json")
                text = record.text
            if not text or not text.strip():
                raise ValueError("No text available. Provide import_id or text.")
            if not episodes_data:
                raise ValueError("An import needs at least one episode")
            series = Series(id=str(uuid.uuid4()), title=title, description=description,
                            created_at=time.time(), updated_at=time.time())
            episode_texts = self._split_text_by_markers(text, episodes_data)
            # Create Episode (Script) for each chunk
            created_episodes = []
            drafts = []
            for idx, ep_data in enumerate(episodes_data):
                ep_text = episode_texts[idx] if idx < len(episode_texts) else ""
                ep_title = ep_data.get("title", f"第{idx+1}集")
                episode_number = ep_data.get("episode_number", idx + 1)

                # Create draft script (no LLM analysis yet — user can trigger later)
                script = self.script_processor.create_draft_script(ep_title, ep_text)
                script.series_id = series.id
                script.episode_number = episode_number
                drafts.append(script)

                series.episode_ids.append(script.id)
                created_episodes.append({
                    "id": script.id,
                    "title": ep_title,
                    "episode_number": episode_number,
                    "text_length": len(ep_text),
                })

            self.series_store[series.id] = series
            self.scripts.update({script.id: script for script in drafts})
            result = {"series": series.model_dump(mode="json"), "episodes": created_episodes}
            files = ["projects.json", "series.json"]
            if record is not None:
                self.import_receipts[import_id] = record.model_copy(update={
                    "status": "completed", "text": None, "request_fingerprint": fingerprint,
                    "result": ImportResult.model_validate(result),
                })
                files.append("import_receipts.json")
            self._commit_storage(*files)
            return result

    def _split_text_by_markers(self, text: str, episodes_data: List[Dict]) -> List[str]:
        """Split text into chunks using start/end markers from LLM.
        Searches sequentially to avoid overlapping chunks."""
        chunks = []
        search_from = 0  # Track position to avoid overlap

        for ep in episodes_data:
            start_marker = ep.get("start_marker", "")
            end_marker = ep.get("end_marker", "")

            start_idx = search_from
            end_idx = len(text)

            if start_marker:
                found = text.find(start_marker, search_from)
                if found >= 0:
                    start_idx = found

            if end_marker:
                found = text.find(end_marker, start_idx)
                if found >= 0:
                    end_idx = found + len(end_marker)

            chunks.append(text[start_idx:end_idx])
            search_from = end_idx  # Next episode starts after this one

        # Fallback: if markers produced empty/overlapping chunks, do equal split
        if not chunks or all(len(c.strip()) == 0 for c in chunks):
            chunk_size = max(1, len(text) // len(episodes_data))
            chunks = []
            for i in range(len(episodes_data)):
                start = i * chunk_size
                end = start + chunk_size if i < len(episodes_data) - 1 else len(text)
                chunks.append(text[start:end])

        return chunks

    # ============================================================
    # Series Asset Operations
    # ============================================================

    def _find_series_asset(self, series_id: str, asset_id: str, asset_type: str):
        """Find an asset in a Series. Returns (series, asset) tuple."""
        if asset_type not in ("character", "scene", "prop"):
            raise ValueError(f"Invalid asset type: {asset_type}")
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in series.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in series.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in series.props if p.id == asset_id), None)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found in series")
        return series, target_asset

    def toggle_series_asset_lock(self, series_id: str, asset_id: str, asset_type: str) -> Series:
        """Toggle the locked status of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            target_asset.locked = not target_asset.locked
            self._save_series_data_unlocked()
            return self._committed_series_response(series_id)

    def toggle_series_asset_starred(self, series_id: str, asset_id: str, asset_type: str) -> Series:
        """Toggle the starred (library shortlist) status of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            target_asset.starred = not target_asset.starred
            self._save_series_data_unlocked()
            return self._committed_series_response(series_id)

    def update_series_asset_image(self, series_id: str, asset_id: str, asset_type: str, image_url: str) -> Series:
        """Updates the image URL of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            self._set_manual_asset_image(target_asset, asset_type, image_url)
            self._save_series_data_unlocked()
            return self._committed_series_response(series_id)

    def update_series_asset_attributes(self, series_id: str, asset_id: str, asset_type: str, attributes: Dict[str, Any]) -> Series:
        """Updates arbitrary attributes of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            for key, value in attributes.items():
                if hasattr(target_asset, key) and key not in ("id", "status", "locked"):
                    setattr(target_asset, key, value)
            series.updated_at = time.time()
            self._save_series_data_unlocked()
            return self._committed_series_response(series_id)

    def generate_series_asset(self, series_id: str, asset_id: str, asset_type: str,
                              style_preset: str = None, reference_image_url: str = None,
                              style_prompt: str = None, generation_type: str = "all",
                              prompt: str = None, apply_style: bool = True,
                              negative_prompt: str = None, batch_size: int = 1,
                              model_name: str = None, aspect_ratio: Optional[str] = None) -> tuple:
        """Accept a real standalone Series root without creating a Studio project."""
        params = dict(style_preset=style_preset, reference_image_url=reference_image_url,
                      style_prompt=style_prompt, generation_type=generation_type, prompt=prompt,
                      apply_style=apply_style, negative_prompt=negative_prompt, batch_size=batch_size,
                      model_name=model_name, aspect_ratio=aspect_ratio)
        return self._enqueue_asset_image("series", series_id, asset_id, asset_type, params)

    def import_assets_from_series(self, target_series_id: str, source_series_id: str, asset_ids: List[str]) -> Tuple[Series, List[str], List[str]]:
        """Deep-copy selected assets from source Series to target Series.
        Returns (target_series, imported_ids, skipped_ids)."""
        with self._save_lock:
            target = self.series_store.get(target_series_id)
            if not target:
                raise ValueError("Target series not found")
            source = self.series_store.get(source_series_id)
            if not source:
                raise ValueError("Source series not found")

            # Build lookup of all source assets
            source_assets = {}
            for c in source.characters:
                source_assets[c.id] = ("character", c)
            for s in source.scenes:
                source_assets[s.id] = ("scene", s)
            for p in source.props:
                source_assets[p.id] = ("prop", p)

            imported_ids = []
            skipped_ids = []
            for aid in asset_ids:
                if aid not in source_assets:
                    skipped_ids.append(aid)
                    continue
                asset_type, asset = source_assets[aid]
                # Deep copy with new ID
                import copy
                new_asset = copy.deepcopy(asset)
                new_asset.id = str(uuid.uuid4())
                if asset_type == "character":
                    new_asset.reference_sheet_request_id = None
                    new_asset.full_body_request_id = None
                    new_asset.three_view_request_id = None
                    new_asset.headshot_request_id = None
                    new_asset.full_body_motion_request_id = None
                    new_asset.head_shot_motion_request_id = None
                else:
                    new_asset.image_request_id = None
                    new_asset.motion_request_id = None
                if new_asset.status == GenerationStatus.PROCESSING:
                    new_asset.status = "needs_confirmation"
                if asset_type == "character":
                    target.characters.append(new_asset)
                elif asset_type == "scene":
                    target.scenes.append(new_asset)
                elif asset_type == "prop":
                    target.props.append(new_asset)
                imported_ids.append(aid)

            target.updated_at = time.time()
            self._save_series_data_unlocked()
            return self._committed_series_response(target_series_id), imported_ids, skipped_ids

    def get_effective_prompt(self, prompt_type: str, episode: Script, series: Optional[Series] = None) -> str:
        """Three-level fallback: Episode -> Series -> system default."""
        valid_prompt_types = ("storyboard_polish", "video_polish", "r2v_polish", "storyboard_extraction")
        if prompt_type not in valid_prompt_types:
            raise ValueError(f"Invalid prompt_type: {prompt_type}. Must be one of {valid_prompt_types}")
        from .llm import DEFAULT_STORYBOARD_POLISH_PROMPT, DEFAULT_VIDEO_POLISH_PROMPT, DEFAULT_R2V_POLISH_PROMPT, DEFAULT_STORYBOARD_EXTRACTION_PROMPT
        defaults = {
            "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
            "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
            "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
            "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
        }
        episode_value = getattr(episode.prompt_config, prompt_type, "")
        if episode_value.strip():
            return episode_value
        if series:
            series_value = getattr(series.prompt_config, prompt_type, "")
            if series_value.strip():
                return series_value
        return defaults.get(prompt_type, "")
