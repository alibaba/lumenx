"""Playground service layer -- orchestrates AI generation by delegating to existing model adapters.

Routes based on model_id to the appropriate adapter (WanxModel, KlingModel,
ViduModel, MuleRouterVideoModel/ImageModel, WanxImageModel).  Mirrors the
routing logic in ``src/apps/comic_gen/pipeline.py:process_video_task()``.
"""

import hashlib
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, ContextManager, Optional, cast

from .models import (
    GenerateRequest,
    PlaygroundAttempt,
    PlaygroundGeneration,
    PlaygroundMode,
    PlaygroundOutput,
    PlaygroundRequestReceipt,
)
from .storage import PlaygroundStorage
from .provider_video_execution import PlaygroundProviderVideoExecutionService
from .happyhorse_video_admission import HappyHorseVideoAdmission, admitted_happyhorse_video_generation
from .kling_video_admission import KlingVideoAdmission, admitted_kling_video_generation
from .vidu_video_admission import ViduVideoAdmission, admitted_vidu_video_generation
from .pixverse_video_admission import PixVerseVideoAdmission, admitted_pixverse_video_generation
from .seedance_video_admission import SeedanceCapabilityError, SeedanceVideoAdmission, admitted_seedance_video_generation
from .video_admission import PlaygroundVideoAdmission, VideoRuntimeProvider, admitted_wan27_video_generation
from ...utils import get_logger
from ...utils.media_refs import resolve_local_media_path
from ...utils.json_storage import StorageConflictError, StorageError
from ...utils.private_media_directory import OwnedPrivateMediaDirectory
from ...utils.generation_intent import GenerationIntent, IntentValidationError, normalize_generation_intent
from ...utils.media_probe import probe_frozen_image, probe_image_file
from ...utils.media_versions import (
    freeze_media,
    read_verified_media,
    store_media_stream,
    verified_private_media_file,
    verify_managed_media_file,
)
from ...models.provider_execution import ProviderExecutionRecord
from ...models.provider_media_intents import build_accepted_sync_artifact_record, sync_artifact_proof_from_record
from ...models.provider_media_runtime import MediaRuntimeAuthority, resolve_media_runtime_authority
from ...models.provider_tasks import ProviderBindingError, ProviderScope
from ...models.wan27_image_contract import WAN27_IMAGE_CONTRACT_VERSION, wan27_image_payload
from ...models.qwen20_image_contract import QWEN20_IMAGE_CONTRACT_VERSION, qwen20_image_payload
from ...models.provider_recovery import MAX_PROVIDER_MEDIA_BYTES, ProviderRecoveryError, record_with_changes
from ...models.provider_runtime import ProviderRecoveryRuntime, build_provider_runtime_for_admission, build_provider_runtime_for_scope, build_seedance_http_scope_from_snapshot
from ...models.provider_tasks import PreparedMedia
from ...utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION, fingerprint_request, validate_client_request_id

if TYPE_CHECKING:
    from ...models.provider_media_execution import PrivateSyncArtifact

logger = get_logger(__name__)
_ACTIVE_IMAGE_PROCESSING_STATE_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Output directories
# ---------------------------------------------------------------------------
IMAGE_OUTPUT_DIR = os.path.join("output", "playground", "images")
VIDEO_OUTPUT_DIR = os.path.join("output", "playground", "videos")
_SAFE_OUTPUT_ID = re.compile(r"[^A-Za-z0-9_-]+")
_STRICT_WAN27_IMAGE_IDS = {
    "wan2.7-image": "wan2.7-image",
    "wan2.7-image-pro": "wan2.7-image-pro",
    "wan/wan2.7-image#image": "wan2.7-image",
    "wan/wan2.7-image-pro#image": "wan2.7-image-pro",
}
_STRICT_QWEN20_IMAGE_IDS = {
    "qwen-image-2.0": "qwen-image-2.0",
    "qwen-image-2.0-pro": "qwen-image-2.0-pro",
    "qwen/qwen-image-2.0#image": "qwen-image-2.0",
    "qwen/qwen-image-2.0-pro#image": "qwen-image-2.0-pro",
}

MediaAuthorityProvider = Callable[[GenerationIntent], ContextManager[MediaRuntimeAuthority]]
MediaAuthorityResolver = Callable[[ProviderExecutionRecord], ContextManager[MediaRuntimeAuthority]]
CoreMediaExecutor = Callable[..., object]
VideoScopeRuntimeProvider = Callable[[ProviderScope], ProviderRecoveryRuntime | None]


class _PlaygroundImageInProgress(RuntimeError):
    pass


class PlaygroundService:
    """High-level service that creates generation records and delegates to
    the correct model adapter for execution."""

    def __init__(
        self,
        storage: PlaygroundStorage,
        data_dir: str | os.PathLike[str] = "output",
        library_writer=None,
        runtime_environment=None,
        env=None,
        media_authority_provider: MediaAuthorityProvider | None = None,
        media_authority_resolver: MediaAuthorityResolver | None = None,
        core_media_executor: CoreMediaExecutor | None = None,
        image_model_factory: Callable[..., object] | None = None,
        video_runtime_provider: VideoRuntimeProvider | None = None,
        video_scope_runtime_provider: VideoScopeRuntimeProvider | None = None,
        provider_credential_store=None,
        video_execution_service: PlaygroundProviderVideoExecutionService | None = None,
        max_media_bytes: int = MAX_PROVIDER_MEDIA_BYTES,
    ):
        self.storage = storage
        self.runtime_environment = runtime_environment
        self._env = None if env is None else MappingProxyType(dict(env))
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.image_output_dir = self.data_dir / "playground" / "images"
        self.video_output_dir = self.data_dir / "playground" / "videos"
        self.library_writer = library_writer
        self._media_authority_provider = media_authority_provider
        self._media_authority_resolver = media_authority_resolver
        self._core_media_executor = core_media_executor
        self._image_model_factory = image_model_factory
        self._video_runtime_provider = video_runtime_provider
        self._video_scope_runtime_provider = video_scope_runtime_provider
        self._max_media_bytes = max_media_bytes
        self._provider_credential_store = provider_credential_store
        self._video_execution_service = video_execution_service
        self.model_config = {"output_root": str(self.data_dir)}
        # Lazy-initialised model instances (cached for the lifetime of the service)
        self._wanx_model = None
        self._kling_model = None
        self._vidu_model = None
        self._mulerouter_video_model = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_generation(self, request: GenerateRequest) -> PlaygroundGeneration:
        """Create or replay a generation, returning the accepted generation."""
        receipt = self.admit_generation(request)
        if receipt.generation is None:
            raise StorageConflictError("Playground request has been deleted")
        return receipt.generation

    def admit_generation(self, request: GenerateRequest) -> PlaygroundRequestReceipt:
        """Persist a client-owned admission before any provider work is scheduled."""
        client_request_id = validate_client_request_id(request.client_request_id)
        payload = request.identity_payload()
        try:
            request_fingerprint = fingerprint_request(payload, entrypoint="playground.generate")
        except ValueError as exc:
            if request.mode == PlaygroundMode.T2I and request.model_id in _STRICT_QWEN20_IMAGE_IDS:
                raise IntentValidationError("Qwen Image 2.0 request must be strict UTF-8 JSON") from exc
            raise
        batch_size = request.batch_size or 1
        gen = PlaygroundGeneration(
            schema_version=1,
            id=str(uuid.uuid4()),
            client_request_id=client_request_id,
            request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
            request_fingerprint=request_fingerprint,
            mode=request.mode,
            model_id=request.model_id,
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            input_media=request.input_media or [],
            parameters=request.parameters or {},
            batch_size=batch_size,
            outputs=[],
            attempts=[
                PlaygroundAttempt(
                    attempt_id=f"{client_request_id}-{index}",
                    ordinal=index,
                    status="pending",
                    error=None,
                    provider_execution=None,
                )
                for index in range(batch_size)
            ],
            status="pending",
            error=None,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        existing = self.storage.read_request(client_request_id)
        if existing is not None:
            fingerprint = existing.generation.request_fingerprint if existing.generation else (
                existing.tombstone.request_fingerprint if existing.tombstone else None
            )
            if fingerprint != request_fingerprint:
                raise StorageConflictError("Playground request id was already used for a different request")
            return existing.model_copy(deep=True)
        if request.model_id in _STRICT_QWEN20_IMAGE_IDS and request.mode not in (PlaygroundMode.T2I, PlaygroundMode.I2I):
            raise IntentValidationError("Qwen Image 2.0 requires an image mode")
        # New admission may freeze media and bind credentials before history is saved.
        self.storage.require_writable()
        if request.mode in (PlaygroundMode.T2I, PlaygroundMode.I2I):
            self._admit_core_image_generation(gen)
        elif admitted_wan27_video_generation(request.mode, request.model_id):
            self._admit_core_video_generation(gen)
        elif admitted_happyhorse_video_generation(request.mode, request.model_id):
            self._admit_happyhorse_video_generation(gen)
        elif admitted_kling_video_generation(request.mode, request.model_id):
            self._admit_kling_video_generation(gen)
        elif admitted_vidu_video_generation(request.mode, request.model_id):
            self._admit_vidu_video_generation(gen)
        elif admitted_pixverse_video_generation(request.mode, request.model_id):
            self._admit_pixverse_video_generation(gen)
        elif admitted_seedance_video_generation(request.mode, request.model_id):
            self._admit_seedance_video_generation(gen)
        return self.storage.admit_generation(gen)

    def process_generation(self, generation_id: str) -> None:
        """Execute the actual generation.  Intended to run in a background
        thread -- all calls are synchronous (blocking)."""
        try:
            existing_before_claim = self.storage.get_generation(generation_id)
            if existing_before_claim is not None and self._is_core_video_generation(existing_before_claim):
                self._process_video_generation(existing_before_claim)
                return
            with self._active_image_processing(generation_id):
                gen = self.storage.claim_generation_for_processing(generation_id)
                allow_accepted_image_execution = gen is not None
                if gen is None:
                    existing = self.storage.get_generation(generation_id)
                    if existing is None or existing.status != "processing" or existing.mode not in (PlaygroundMode.T2I, PlaygroundMode.I2I):
                        logger.info("Generation %s is not pending; processing skipped", generation_id)
                        return
                    gen = existing

                try:
                    # Each claimed generation, including its batch, retains one snapshot.
                    runner = self if self.runtime_environment is None else PlaygroundService(
                        self.storage,
                        self.data_dir,
                        self.library_writer,
                        runtime_environment=self.runtime_environment,
                        env=self.runtime_environment.snapshot(),
                        media_authority_provider=self._media_authority_provider,
                        media_authority_resolver=self._media_authority_resolver,
                        core_media_executor=self._core_media_executor,
                        image_model_factory=self._image_model_factory,
                        video_runtime_provider=self._video_runtime_provider,
                        video_scope_runtime_provider=self._video_scope_runtime_provider,
                        provider_credential_store=self._provider_credential_store_instance(),
                        max_media_bytes=self._max_media_bytes,
                    )
                    mode = gen.mode
                    if mode in (PlaygroundMode.T2I, PlaygroundMode.I2I):
                        runner._process_image_generation(gen, allow_accepted_execution=allow_accepted_image_execution)
                    elif mode in (PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V):
                        runner._process_video_generation(gen)
                    else:
                        raise ValueError(f"Unsupported playground mode: {mode}")

                    if gen.status != "needs_confirmation":
                        gen.status = "completed"
                        gen.error = None
                except StorageError:
                    # A provider may already have charged or produced media. Keep that
                    # evidence and stop; do not continue the batch or report completion.
                    raise
                except Exception as exc:
                    logger.exception("Generation %s failed", generation_id)
                    gen.status = "failed"
                    gen.error = str(exc)

                if not self.storage.update_claimed_generation(gen):
                    logger.warning("Generation %s left processing before worker completion; result ignored", generation_id)
        except _PlaygroundImageInProgress:
            logger.info("Generation %s is already being processed by an active image worker", generation_id)
            return

    def save_to_library(self, generation_id: str, output_id: str, category: str = "general") -> bool:
        """Copy a generated output to ``output/assets/{category}/`` and flag
        :pyattr:`PlaygroundOutput.saved_to_library` = True."""
        gen = self.storage.get_generation(generation_id)
        if gen is None:
            logger.warning("save_to_library: generation %s not found", generation_id)
            return False

        target_output: Optional[PlaygroundOutput] = None
        for out in gen.outputs:
            if out.id == output_id:
                target_output = out
                break
        if target_output is None:
            logger.warning("save_to_library: output %s not found in generation %s", output_id, generation_id)
            return False
        if target_output.saved_to_library:
            return True
        self.storage.require_writable()

        # Stored refs are portable; resolve legacy output/ prefixes inside this app.
        src_path = resolve_local_media_path(target_output.media_path, output_root=str(self.data_dir))
        if src_path is None or not os.path.isfile(src_path):
            logger.error("save_to_library: source file not found: %s", target_output.media_path)
            return False

        dest_dir = (self.data_dir / "assets" / category).resolve()
        if not dest_dir.is_relative_to(self.data_dir / "assets"):
            return False
        source_key = f"playground:{generation_id}:{output_id}"
        filename = hashlib.sha256(source_key.encode("utf-8")).hexdigest() + Path(src_path).suffix
        dest_path = str(dest_dir / filename)
        # Publish a complete copy under a deterministic, collision-resistant name.
        # The paid source remains intact if copy/registration/history saving fails.
        temporary = None
        replaced = False
        try:
            os.makedirs(dest_dir, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".playground-", suffix=".tmp", dir=dest_dir)
            os.close(descriptor)
            shutil.copy2(src_path, temporary)
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, dest_path)
            replaced = True
            self._sync_library_directories(dest_dir)
        except OSError as exc:
            raise StorageError(
                f"Cannot copy Playground output to the library: {exc}",
                commit_uncertain=replaced,
            ) from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove incomplete library copy %s", temporary)
        logger.info("Saved output %s to library: %s", output_id, dest_path)

        # Wave A (shared asset pool): besides copying the file, register a real
        # global library asset record so the output is curatable through the
        # /library/assets CRUD. category -> asset_type mapping; anything
        # unknown (incl. the "general" default) falls back to "prop".
        asset_type = self._category_to_asset_type(category)
        prompt_text = (gen.prompt or "").strip()
        asset_name = prompt_text[:40] or os.path.splitext(os.path.basename(dest_path))[0]
        try:
            if self.library_writer is None:
                raise RuntimeError("Playground library writer is not configured")
            asset = self.library_writer(
                asset_type,
                {
                    "name": asset_name,
                    "description": prompt_text,
                    # Point the library record at the freshly-copied file.
                    "image_url": Path(dest_path).relative_to(self.data_dir).as_posix(),
                },
                source_key=source_key,
            )
            logger.info(
                "save_to_library: created global %s asset %s from output %s",
                asset_type,
                getattr(asset, "id", "?"),
                output_id,
            )
        except StorageError:
            raise
        except Exception:
            logger.exception(
                "save_to_library: failed to register global library asset for output %s",
                output_id,
            )
            return False

        self.storage.mark_output_saved(generation_id, output_id)
        return True


    def _admit_core_video_generation(self, gen: PlaygroundGeneration) -> None:
        PlaygroundVideoAdmission(
            output_root=self.data_dir,
            writer_lock=self.storage._writer_lock,
            runtime_lock=self.storage._lock,
            runtime_provider=self._resolve_video_runtime_for_admission,
            credential_store=self._provider_credential_store_instance(),
            max_media_bytes=self._max_media_bytes,
        ).admit(gen)

    def _admit_happyhorse_video_generation(self, gen: PlaygroundGeneration) -> None:
        HappyHorseVideoAdmission(
            output_root=self.data_dir,
            writer_lock=self.storage._writer_lock,
            runtime_lock=self.storage._lock,
            runtime_provider=self._resolve_video_runtime_for_admission,
            credential_store=self._provider_credential_store_instance(),
            max_media_bytes=self._max_media_bytes,
        ).admit(gen)

    def _admit_kling_video_generation(self, gen: PlaygroundGeneration) -> None:
        KlingVideoAdmission(
            output_root=self.data_dir,
            writer_lock=self.storage._writer_lock,
            runtime_lock=self.storage._lock,
            runtime_provider=self._resolve_video_runtime_for_admission,
            credential_store=self._provider_credential_store_instance(),
        ).admit(gen)

    def _admit_vidu_video_generation(self, gen: PlaygroundGeneration) -> None:
        ViduVideoAdmission(
            output_root=self.data_dir,
            writer_lock=self.storage._writer_lock,
            runtime_lock=self.storage._lock,
            runtime_provider=self._resolve_video_runtime_for_admission,
            credential_store=self._provider_credential_store_instance(),
        ).admit(gen)

    def _admit_pixverse_video_generation(self, gen: PlaygroundGeneration) -> None:
        PixVerseVideoAdmission(
            output_root=self.data_dir,
            writer_lock=self.storage._writer_lock,
            runtime_lock=self.storage._lock,
            runtime_provider=self._resolve_video_runtime_for_admission,
            credential_store=self._provider_credential_store_instance(),
        ).admit(gen)

    def get_capabilities(self) -> dict:
        """Read one app-owned snapshot without resolving or binding credentials."""
        snapshot = self.runtime_environment.snapshot() if self.runtime_environment is not None else {}
        result = build_seedance_http_scope_from_snapshot(snapshot)
        available = result.scope is not None
        return {
            "schema_version": 1,
            "seedance": {
                "available": available,
                "reason": result.reason,
                "channel": "mulerouter_http" if available else None,
                "input_kinds": ["image"],
            },
        }

    def _admit_seedance_video_generation(self, gen: PlaygroundGeneration) -> None:
        SeedanceVideoAdmission(
            output_root=self.data_dir,
            writer_lock=self.storage._writer_lock,
            runtime_lock=self.storage._lock,
            runtime_provider=self._resolve_video_runtime_for_admission,
            credential_store=self._provider_credential_store_instance(),
        ).admit(gen)

    def _resolve_video_runtime_for_admission(self, *, provider_model_id: str, gateway: str):
        if self._video_runtime_provider is not None:
            return self._video_runtime_provider(provider_model_id=provider_model_id, gateway=gateway)
        if self.runtime_environment is None:
            if admitted_seedance_video_generation(PlaygroundMode.T2V, provider_model_id):
                raise SeedanceCapabilityError("seedance_http_key_required")
            raise ProviderRecoveryError("provider_recovery_runtime_missing")
        result = build_provider_runtime_for_admission(
            self.runtime_environment,
            output_root=self.data_dir,
            provider_model_id=provider_model_id,
            gateway=gateway,
        )
        if result.runtime is None:
            raise ProviderRecoveryError(result.reason or "provider_recovery_runtime_missing")
        return result.runtime

    def _resolve_video_runtime_for_scope(self, scope: ProviderScope):
        if self._video_scope_runtime_provider is not None:
            runtime = self._video_scope_runtime_provider(scope)
            if runtime is None:
                raise ProviderRecoveryError("provider_recovery_runtime_missing")
            return runtime
        if self.runtime_environment is None:
            raise ProviderRecoveryError("provider_recovery_runtime_missing")
        result = build_provider_runtime_for_scope(
            self.runtime_environment,
            output_root=self.data_dir,
            scope=scope,
        )
        if result.runtime is None:
            raise ProviderRecoveryError(result.reason or "provider_recovery_runtime_missing")
        return result.runtime

    def video_execution_service(self) -> PlaygroundProviderVideoExecutionService:
        if self._video_execution_service is None:
            self._video_execution_service = PlaygroundProviderVideoExecutionService(
                self.storage,
                runtime_provider=self._resolve_video_runtime_for_scope,
                credential_store=self._provider_credential_store_instance(),
            )
        return self._video_execution_service

    @staticmethod
    def _is_core_video_generation(gen: PlaygroundGeneration) -> bool:
        return gen.mode in (PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V) and any(
            attempt.provider_execution is not None for attempt in gen.attempts
        )

    def _provider_credential_store_instance(self):
        from ...models.provider_binding import ProviderCredentialStore
        if self._provider_credential_store is None:
            self._provider_credential_store = ProviderCredentialStore(
                self.data_dir,
                writer_lock=self.storage._writer_lock,
                runtime_lock=self.storage._lock,
            )
        return self._provider_credential_store

    # ------------------------------------------------------------------
    # Image generation (t2i / i2i)
    # ------------------------------------------------------------------

    def _admit_core_image_generation(self, gen: PlaygroundGeneration) -> None:
        intent = self._image_generation_intent(gen)
        media = self._freeze_image_inputs(gen) if gen.mode == PlaygroundMode.I2I else []
        contract = {}
        if self._uses_strict_wan27_t2i(gen):
            contract = {"image_contract_version": WAN27_IMAGE_CONTRACT_VERSION}
        elif self._uses_strict_qwen20_t2i(gen):
            contract = {"image_contract_version": QWEN20_IMAGE_CONTRACT_VERSION}
        with self._resolve_image_authority(intent, **contract) as authority:
            for idx in range(gen.batch_size):
                attempt_id = self._attempt_id(gen, idx)
                record = build_accepted_sync_artifact_record(
                    intent=intent,
                    provider_model_id=authority.provider_model_id,
                    media=media,
                    scope=authority.scope,
                    credential_ref=authority.credential_ref,
                    attempt_id=attempt_id,
                    output_ref=self._playground_image_output_ref(gen, idx),
                    media_kind="image",
                    **contract,
                )
                self._set_attempt(gen, idx, "pending", provider_execution=record.model_dump(mode="json"))

    def _process_image_generation(self, gen: PlaygroundGeneration, *, allow_accepted_execution: bool) -> None:
        os.makedirs(self.image_output_dir, exist_ok=True)

        failures: list[str] = []
        needs_confirmation = False

        for idx in range(gen.batch_size):
            try:
                self.storage.require_writable()
                self.storage.assert_claimed_generation(gen.id)
                outcome = self._process_core_image_attempt(gen, idx, allow_accepted_execution=allow_accepted_execution)
                if outcome == "needs_confirmation":
                    needs_confirmation = True
                    current = self.storage.get_generation(gen.id)
                    # Stop only for this member's persisted unknown result. A
                    # lost claim must still reach the next member's guard.
                    if (current is not None and current.status == "needs_confirmation"
                            and idx < len(current.attempts)
                            and current.attempts[idx] == gen.attempts[idx]):
                        break
                elif outcome == "failed":
                    failures.append(gen.attempts[idx].error or "image attempt failed")
            except (StorageError, _PlaygroundImageInProgress):
                raise
            except Exception as exc:
                logger.error("Image generation %s batch %d failed: %s", gen.id, idx, exc)
                failures.append(str(exc))
                self._set_attempt(gen, idx, "failed", str(exc))
                if not self.storage.update_claimed_generation(gen):
                    if self.storage.get_generation(gen.id) is None:
                        raise StorageConflictError(f"Generation {gen.id} is no longer processing")
                    return

        if needs_confirmation:
            gen.status = "needs_confirmation"
            gen.error = None
            return
        if failures and not gen.outputs:
            raise RuntimeError(f"All {len(failures)} batch items failed: {failures[0]}")

    def _process_core_image_attempt(self, gen: PlaygroundGeneration, idx: int, *, allow_accepted_execution: bool) -> str:
        self._attempt_id(gen, idx)
        attempt = gen.attempts[idx]
        if attempt.status == "completed" and attempt.provider_execution is not None:
            return "completed"
        if not attempt.provider_execution:
            self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_record_missing")
            return "needs_confirmation"
        try:
            record = ProviderExecutionRecord.model_validate(attempt.provider_execution)
        except (TypeError, ValueError) as exc:
            self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_record_invalid")
            logger.warning("Playground image attempt %s has invalid provider execution: %s", attempt.attempt_id, exc)
            return "needs_confirmation"
        if record.schema_version != 2 or record.execution_kind != "sync_artifact" or record.media_kind != "image":
            self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_record_invalid")
            return "needs_confirmation"
        if record.attempt_id != attempt.attempt_id:
            self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_attempt_mismatch")
            return "needs_confirmation"

        if record.phase == "accepted":
            if not allow_accepted_execution:
                self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_accepted_without_worker")
                return "needs_confirmation"
            return self._execute_accepted_core_image_attempt(gen, idx, record)
        if record.phase == "submitting":
            return self._repair_submitting_core_image_attempt(gen, idx, record)
        if record.phase == "completed":
            self._append_core_image_output(gen, idx, record)
            self._set_attempt(gen, idx, "completed", provider_execution=record.model_dump(mode="json"))
            if not self.storage.update_claimed_generation(gen):
                raise StorageConflictError(f"Generation {gen.id} is no longer processing")
            return "completed"
        self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_record_uncertain")
        return "needs_confirmation"

    def _execute_accepted_core_image_attempt(
        self, gen: PlaygroundGeneration, idx: int, record: ProviderExecutionRecord,
    ) -> str:
        submitting = record_with_changes(record, phase="submitting")
        self._set_attempt(gen, idx, "processing", provider_execution=submitting.model_dump(mode="json"))
        if not self.storage.update_claimed_generation(gen):
            raise StorageConflictError(f"Generation {gen.id} is no longer processing")

        directory: OwnedPrivateMediaDirectory | None = None
        result = None
        verified = durable = False
        try:
            with self._resolve_record_authority(submitting) as authority:
                directory = OwnedPrivateMediaDirectory(prefix="lumenx-playground-image-")
                scratch = directory.path
                result = self._execute_core_media(
                    submitting,
                    authority,
                    output_root=self.data_dir,
                    scratch_dir=scratch,
                    max_bytes=self._max_media_bytes,
                    image_model_factory=self._image_model_factory,
                )
                result_path = Path(result.path)
                if (result.media_kind != "image" or result.encoding != "image/png"
                        or result_path.parent != scratch or result_path.suffix != ".png"):
                    raise StorageError("Playground Core image execution returned an invalid private artifact")
                with verified_private_media_file(
                    result_path, scratch_root=scratch,
                    expected_sha256=result.sha256, expected_size=result.size_bytes,
                    max_bytes=self._max_media_bytes,
                ) as source:
                    # The Core executor normalizes PNGs. Check the returned bytes too,
                    # so an invalid executor result never gains retention eligibility.
                    if source.read(8) != b"\x89PNG\r\n\x1a\n":
                        raise StorageError("Playground Core image artifact is not PNG")
                    metadata = probe_image_file(result_path)
                    if metadata.codec_name != "png" or "png_pipe" not in metadata.format_names:
                        raise StorageError("Playground Core image artifact is not PNG")
                    source.seek(0)
                    verified = True
                    proof = self._publish_core_image_result(
                        gen, idx, submitting, result,
                        output_ref=submitting.output_ref or "", source=source,
                    )
                saved = self.storage.get_generation(gen.id)
                expected = record_with_changes(submitting, prepared_artifact=proof).model_dump(mode="json")
                if (saved is None or saved.status != "processing" or idx >= len(saved.attempts)
                        or saved.attempts[idx].attempt_id != submitting.attempt_id
                        or saved.attempts[idx].provider_execution != expected
                        or not self._published_core_image_matches(submitting, proof)):
                    raise StorageConflictError("Playground Core image publication is not durably recorded")
                durable = True
        except StorageError:
            raise
        except Exception as exc:
            logger.warning("Playground Core image sync outcome is uncertain for %s: %s", record.attempt_id, exc)
            self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_needs_confirmation")
            return "needs_confirmation"
        finally:
            if directory is not None:
                keep = False
                if verified and not durable and result is not None:
                    try:
                        directory.assert_owned()
                        with verified_private_media_file(
                            result.path, scratch_root=directory.path,
                            expected_sha256=result.sha256, expected_size=result.size_bytes,
                            max_bytes=self._max_media_bytes,
                        ):
                            keep = True
                    except (ValueError, OSError):
                        pass
                directory.cleanup(keep_name=Path(result.path).name if keep and result is not None else None)

        completed = record_with_changes(submitting, phase="completed", prepared_artifact=proof, artifact=proof)
        self._set_attempt(gen, idx, "completed", provider_execution=completed.model_dump(mode="json"))
        self._append_core_image_output(gen, idx, completed)
        if not self.storage.update_claimed_generation(gen):
            if self.storage.get_generation(gen.id) is None:
                raise StorageConflictError(f"Generation {gen.id} is no longer processing")
            logger.warning("Generation %s left processing after Core image publication; completion will be repaired", gen.id)
        return "completed"

    def _repair_submitting_core_image_attempt(self, gen: PlaygroundGeneration, idx: int, record: ProviderExecutionRecord) -> str:
        proof = record.prepared_artifact
        if proof is None:
            self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_needs_confirmation")
            return "needs_confirmation"
        if not self._published_core_image_matches(record, proof):
            self._mark_core_image_needs_confirmation(gen, idx, "playground_core_image_needs_confirmation")
            return "needs_confirmation"
        completed = record_with_changes(record, phase="completed", prepared_artifact=proof, artifact=proof)
        self._set_attempt(gen, idx, "completed", provider_execution=completed.model_dump(mode="json"))
        self._append_core_image_output(gen, idx, completed)
        if not self.storage.update_claimed_generation(gen):
            if self.storage.get_generation(gen.id) is None:
                raise StorageConflictError(f"Generation {gen.id} is no longer processing")
            logger.warning("Generation %s left processing after Core image publication; completion will be repaired", gen.id)
        return "completed"

    def _published_core_image_matches(self, record: ProviderExecutionRecord, proof: dict) -> bool:
        try:
            if proof.get("output_ref") != record.output_ref or proof.get("relative_path") != record.output_ref:
                return False
            verify_managed_media_file(
                record.output_ref or "",
                output_root=self.data_dir,
                writer_lock=self.storage._writer_lock,
                storage_guard=self._storage_guard,
                expected_sha256=proof["sha256"],
                expected_size=proof["size_bytes"],
                max_bytes=self._max_media_bytes,
                media_kind="image",
            )
        except Exception:
            return False
        return True

    def _mark_core_image_needs_confirmation(self, gen: PlaygroundGeneration, idx: int, reason: str) -> None:
        self._set_attempt(gen, idx, "needs_confirmation", reason)
        gen.status = "needs_confirmation"
        gen.error = None
        if not self.storage.update_claimed_generation(gen):
            if self.storage.get_generation(gen.id) is None:
                raise StorageConflictError(f"Generation {gen.id} is no longer processing")

    def _append_core_image_output(self, gen: PlaygroundGeneration, idx: int, record: ProviderExecutionRecord) -> None:
        proof = record.artifact or record.prepared_artifact
        if proof is None:
            raise StorageError("Playground Core image completion is missing artifact proof")
        output_ref = proof["relative_path"]
        attempt_id = record.attempt_id
        for output in gen.outputs:
            if output.attempt_id == attempt_id or output.media_path == output_ref:
                return
        gen.outputs.append(PlaygroundOutput(
            id=str(uuid.uuid4()),
            media_path=output_ref,
            media_type="image",
            thumbnail_path=None,
            saved_to_library=False,
            attempt_id=attempt_id,
        ))

    def _execute_core_media(self, *args, **kwargs) -> "PrivateSyncArtifact":
        if self._core_media_executor is not None:
            return cast("PrivateSyncArtifact", self._core_media_executor(*args, **kwargs))
        from ...models.provider_media_execution import execute_sync_artifact_to_private_file
        return execute_sync_artifact_to_private_file(*args, **kwargs)

    def _publish_core_image_result(
        self,
        gen: PlaygroundGeneration,
        idx: int,
        record: ProviderExecutionRecord,
        result,
        *,
        output_ref: str,
        source,
    ) -> dict:
        prepared_proof: dict | None = None

        def on_prepared(relative_path: str, sha256: str, size: int) -> None:
            nonlocal prepared_proof
            if relative_path != output_ref or sha256 != result.sha256 or size != result.size_bytes:
                raise StorageConflictError("Playground Core image artifact changed before publication")
            proof = sync_artifact_proof_from_record(
                record,
                relative_path=relative_path,
                sha256=sha256,
                size_bytes=size,
            )
            prepared = record_with_changes(record, prepared_artifact=proof)
            self._set_attempt(gen, idx, "processing", provider_execution=prepared.model_dump(mode="json"))
            if not self.storage.update_claimed_generation(gen):
                raise StorageConflictError(f"Generation {gen.id} is no longer processing")
            prepared_proof = proof

        store_media_stream(
            iter(lambda: source.read(64 * 1024), b""),
            output_ref=output_ref,
            output_root=self.data_dir,
            writer_lock=self.storage._writer_lock,
            storage_guard=self._storage_guard,
            max_bytes=self._max_media_bytes,
            on_prepared=on_prepared,
            publish_policy="no_overwrite",
            media_kind="image",
        )
        if prepared_proof is None:
            raise StorageError("Playground Core image publication did not record proof")
        return prepared_proof

    def _resolve_record_authority(self, record: ProviderExecutionRecord) -> ContextManager[MediaRuntimeAuthority]:
        if self._media_authority_resolver is not None:
            return self._media_authority_resolver(record)
        if self.runtime_environment is None:
            raise ValueError("Playground Core image execution requires a runtime environment")
        return resolve_media_runtime_authority(
            self.runtime_environment,
            self._provider_credential_store_instance(),
            media_kind="image",
            provider_model_id=record.provider_model_id,
            scope=ProviderScope.from_dict(record.scope),
            credential_ref=record.credential_ref,
            **({"image_contract_version": record.intent["intent_contract_version"]}
               if "intent_contract_version" in record.intent else {}),
        )

    @contextmanager
    def _active_image_processing(self, generation_id: str):
        active, lock = self._active_image_processing_state()
        with lock:
            if generation_id in active:
                raise _PlaygroundImageInProgress()
            active.add(generation_id)
        try:
            yield
        finally:
            with lock:
                active.discard(generation_id)

    def _active_image_processing_state(self):
        # The runtime writer lock is the shared lease boundary for a data_dir.
        # Separate PlaygroundService/PlaygroundStorage objects that share it must
        # see the same active image worker, while separate processes cannot hold
        # the writer lease concurrently. Initialisation is guarded separately so
        # two service objects cannot install different lock/set objects.
        owner = self.storage._writer_lock
        with _ACTIVE_IMAGE_PROCESSING_STATE_LOCK:
            lock = getattr(owner, "_playground_active_image_processing_lock", None)
            if lock is None:
                lock = threading.RLock()
                setattr(owner, "_playground_active_image_processing_lock", lock)
            active = getattr(owner, "_playground_active_image_processing", None)
            if not isinstance(active, set):
                active = set()
                setattr(owner, "_playground_active_image_processing", active)
        return active, lock

    @contextmanager
    def _storage_guard(self):
        with self.storage._lock:
            yield

    @staticmethod
    def _uses_strict_wan27_t2i(gen: PlaygroundGeneration) -> bool:
        return gen.mode == PlaygroundMode.T2I and gen.model_id in _STRICT_WAN27_IMAGE_IDS

    @staticmethod
    def _uses_strict_qwen20_t2i(gen: PlaygroundGeneration) -> bool:
        return gen.mode == PlaygroundMode.T2I and gen.model_id in _STRICT_QWEN20_IMAGE_IDS

    def _image_generation_intent(self, gen: PlaygroundGeneration) -> GenerationIntent:
        strict = self._uses_strict_wan27_t2i(gen)
        qwen = self._uses_strict_qwen20_t2i(gen)
        if strict and (gen.negative_prompt is not None or gen.input_media):
            raise IntentValidationError("Wan2.7 T2I does not support negative prompt or input media")
        if qwen and gen.input_media:
            raise IntentValidationError("Qwen Image 2.0 T2I does not support input media")
        params = dict(gen.parameters)
        if gen.negative_prompt is not None:
            if qwen and "negative_prompt" in params and (
                type(params["negative_prompt"]) is not type(gen.negative_prompt)
                or params["negative_prompt"] != gen.negative_prompt
            ):
                raise IntentValidationError("Conflicting Qwen Image 2.0 negative prompt values")
            params["negative_prompt"] = gen.negative_prompt
        intent = normalize_generation_intent(
            {
                "mode": gen.mode.value,
                "model_id": gen.model_id,
                "prompt": gen.prompt,
                "params": params,
            },
            allowed_modes={"t2i", "i2i"},
            allowed_parameters=({"size", "seed", "watermark"} if strict else
                                {"size", "seed", "negative_prompt", "prompt_extend", "watermark"} if qwen else
                                {"size", "quality", "seed", "negative_prompt", "prompt_extend", "watermark"}),
            # This entrance supplies a product default, never a synthetic user value.
            preset=({"id": "playground.wan27.t2i", "version": "1", "mode": "t2i", "params": {"watermark": False}} if strict else
                    {"id": "playground.qwen20.t2i", "version": "1", "mode": "t2i", "params": {"watermark": False, "prompt_extend": True}} if qwen else None),
        )
        if strict:
            if not {"size", "watermark"}.issubset(intent.params):
                raise IntentValidationError("Wan2.7 T2I requires resolved size and watermark")
            try:
                wan27_image_payload(
                    intent.prompt, _STRICT_WAN27_IMAGE_IDS[gen.model_id],
                    intent.params["size"], intent.params.get("seed"), intent.params["watermark"],
                )
            except ProviderBindingError as exc:
                raise IntentValidationError(str(exc)) from exc
        if qwen:
            if not {"size", "watermark", "prompt_extend"}.issubset(intent.params):
                raise IntentValidationError("Qwen Image 2.0 T2I requires resolved size and flags")
            try:
                qwen20_image_payload(
                    intent.prompt, _STRICT_QWEN20_IMAGE_IDS[gen.model_id],
                    intent.params["size"], intent.params.get("seed"), intent.params["watermark"],
                    intent.params["prompt_extend"], intent.params.get("negative_prompt"),
                )
            except ProviderBindingError as exc:
                raise IntentValidationError(str(exc)) from exc
        return intent

    def _freeze_image_inputs(self, gen: PlaygroundGeneration) -> list[PreparedMedia]:
        if not gen.input_media:
            raise IntentValidationError("i2i image generation requires input media")
        frozen: list[PreparedMedia] = []
        for ordinal, media_ref in enumerate(gen.input_media):
            version = freeze_media(
                media_ref,
                output_root=self.data_dir,
                writer_lock=self.storage._writer_lock,
                role="reference_image",
                ordinal=ordinal,
                max_bytes=self._max_media_bytes,
            )
            data = read_verified_media(version, output_root=self.data_dir, max_bytes=self._max_media_bytes)
            probe_frozen_image(version, data)
            frozen.append(PreparedMedia.from_verified_bytes(version, data, max_bytes=self._max_media_bytes))
        return frozen

    def _resolve_image_authority(
        self, intent: GenerationIntent, *, image_contract_version: str | None = None,
    ) -> ContextManager[MediaRuntimeAuthority]:
        if self._media_authority_provider is not None:
            return self._media_authority_provider(intent)
        if self.runtime_environment is None:
            raise ValueError("Playground Core image execution requires a runtime environment")
        from ...models.provider_media_runtime import bind_media_runtime_authority
        if image_contract_version is not None:
            return bind_media_runtime_authority(
                self.runtime_environment,
                self._provider_credential_store_instance(),
                image_intent=intent,
                image_contract_version=image_contract_version,
            )
        return bind_media_runtime_authority(
            self.runtime_environment,
            self._provider_credential_store_instance(),
            image_intent=intent,
        )

    @staticmethod
    def _playground_image_output_ref(gen: PlaygroundGeneration, idx: int) -> str:
        base = _SAFE_OUTPUT_ID.sub("-", f"{gen.mode.value}-{gen.id}-{idx}").strip("-")
        if not base:
            base = uuid.uuid4().hex
        return f"playground/images/{base}.png"

    # ------------------------------------------------------------------
    # Video generation (t2v / i2v / r2v / v2v)
    # ------------------------------------------------------------------

    def _process_video_generation(self, gen: PlaygroundGeneration) -> None:
        os.makedirs(self.video_output_dir, exist_ok=True)

        model_lower = gen.model_id.lower()
        failures = []
        needs_confirmation = False

        if any(attempt.provider_execution is not None for attempt in gen.attempts):
            execution = self.video_execution_service()
            current = gen
            for attempt in list(gen.attempts):
                if attempt.provider_execution is None:
                    continue
                ack = execution.run_attempt(current.id, attempt.attempt_id)
                current = ack.generation
            gen.status = current.status
            gen.error = current.error
            gen.outputs = current.outputs
            gen.attempts = current.attempts
            return

        for idx in range(gen.batch_size):
            attempt_id = self._attempt_id(gen, idx)
            attempt = gen.attempts[idx]
            out_filename = f"{gen.mode.value}_{gen.id}_{idx}.mp4"
            out_path = os.path.join(str(self.video_output_dir), out_filename)

            try:
                self.storage.require_writable()
                self.storage.assert_claimed_generation(gen.id)
                if model_lower.startswith("seedance"):
                    self._generate_video_mulerouter(gen, out_path)
                elif model_lower.startswith("kling"):
                    self._generate_video_kling(gen, out_path)
                elif model_lower.startswith("vidu") or model_lower.startswith("viduq"):
                    self._generate_video_vidu(gen, out_path)
                elif model_lower.startswith("happyhorse"):
                    self._generate_video_wanx(gen, out_path)
                elif model_lower.startswith("pixverse"):
                    self._generate_video_wanx(gen, out_path)
                else:
                    self._generate_video_wanx(gen, out_path)

                output_entry = PlaygroundOutput(
                    id=str(uuid.uuid4()),
                    media_path=Path(out_path).relative_to(self.data_dir).as_posix(),
                    media_type="video",
                    thumbnail_path=None,
                    saved_to_library=False,
                    attempt_id=attempt_id,
                )
                gen.outputs.append(output_entry)
                self._set_attempt(gen, idx, "completed")
                if not self.storage.update_claimed_generation(gen):
                    raise StorageConflictError(f"Generation {gen.id} is no longer processing")
            except StorageError:
                raise
            except Exception as exc:
                logger.error("Video generation %s batch %d failed: %s", gen.id, idx, exc)
                failures.append(str(exc))
                self._set_attempt(gen, idx, "failed", str(exc))
                if not self.storage.update_claimed_generation(gen):
                    if self.storage.get_generation(gen.id) is None:
                        raise StorageConflictError(f"Generation {gen.id} is no longer processing")
                    return

        if needs_confirmation:
            gen.status = "needs_confirmation"
            gen.error = None
            return
        if failures and not gen.outputs:
            raise RuntimeError(f"All {len(failures)} batch items failed: {failures[0]}")

    @staticmethod
    def _attempt_id(gen: PlaygroundGeneration, index: int) -> str:
        while len(gen.attempts) <= index:
            gen.attempts.append(PlaygroundAttempt(
                attempt_id=f"{gen.client_request_id or gen.id}-{len(gen.attempts)}",
                ordinal=len(gen.attempts),
                status="pending",
                error=None,
                provider_execution=None,
            ))
        return gen.attempts[index].attempt_id

    @staticmethod
    def _set_attempt(
        gen: PlaygroundGeneration,
        index: int,
        status: str,
        error: str | None = None,
        provider_execution: dict | None = None,
    ) -> None:
        PlaygroundService._attempt_id(gen, index)
        gen.attempts[index].status = status
        gen.attempts[index].error = error
        if provider_execution is not None:
            gen.attempts[index].provider_execution = provider_execution

    # -- adapter delegates ------------------------------------------------

    def _generate_video_wanx(self, gen: PlaygroundGeneration, out_path: str) -> None:
        """Delegate to :class:`WanxModel` (DashScope video generation -- wan2.x / happyhorse)."""
        from ...models.wanx import WanxModel

        if self._wanx_model is None:
            self._wanx_model = WanxModel(dict(self.model_config), **({"env": self._env} if self._env is not None else {}))

        params = gen.parameters
        img_path, img_url = self._resolve_first_input_media(gen)

        kwargs = {
            "model": gen.model_id,
            "duration": params.get("duration", 5),
            "resolution": params.get("resolution", "720P"),
            "seed": params.get("seed"),
            "negative_prompt": gen.negative_prompt,
            "prompt_extend": params.get("prompt_extend", True),
            "watermark": params.get("watermark", False),
            "ratio": params.get("ratio"),
            "audio_url": params.get("audio_url"),
        }

        # r2v: reference images
        if gen.mode == PlaygroundMode.R2V and gen.input_media:
            kwargs["ref_image_urls"] = list(gen.input_media)

        # v2v: video input
        if gen.mode == PlaygroundMode.V2V and gen.input_media:
            kwargs["video_url"] = gen.input_media[0]

        self._wanx_model.generate(
            prompt=gen.prompt,
            output_path=out_path,
            img_path=cast(str, img_path),
            img_url=cast(str, img_url),
            **kwargs,
        )

    def _generate_video_mulerouter(self, gen: PlaygroundGeneration, out_path: str) -> None:
        """Delegate to :class:`MuleRouterVideoModel` (Seedance 2.0)."""
        from ...models.mulerouter import MuleRouterVideoModel

        if self._mulerouter_video_model is None:
            self._mulerouter_video_model = MuleRouterVideoModel(dict(self.model_config), **({"env": self._env} if self._env is not None else {}))

        params = gen.parameters
        img_path, img_url = self._resolve_first_input_media(gen)

        kwargs = {
            "duration": params.get("duration", 5),
            "resolution": params.get("resolution", "1080p"),
            "aspect_ratio": params.get("aspect_ratio", "16:9"),
            "seed": params.get("seed"),
            "watermark": params.get("watermark", False),
        }

        # r2v: reference images
        if gen.mode == PlaygroundMode.R2V and gen.input_media:
            kwargs["generation_mode"] = "r2v"
            kwargs["ref_image_urls"] = list(gen.input_media)

        self._mulerouter_video_model.generate(
            prompt=gen.prompt,
            output_path=out_path,
            img_url=cast(str, img_url),
            img_path=cast(str, img_path),
            **kwargs,
        )

    def _generate_video_kling(self, gen: PlaygroundGeneration, out_path: str) -> None:
        """Delegate to :class:`KlingModel`."""
        from ...models.kling import KlingModel

        if self._kling_model is None:
            self._kling_model = KlingModel(dict(self.model_config), **({"env": self._env} if self._env is not None else {}))

        params = gen.parameters
        img_path, img_url = self._resolve_first_input_media(gen)

        self._kling_model.generate(
            prompt=gen.prompt,
            output_path=out_path,
            img_url=cast(str, img_url),
            img_path=cast(str, img_path),
            duration=params.get("duration", 5),
            model=gen.model_id,
            negative_prompt=gen.negative_prompt,
            aspect_ratio=params.get("aspect_ratio", "16:9"),
            mode=params.get("mode", "std"),
            sound=params.get("sound", "off"),
            cfg_scale=params.get("cfg_scale"),
        )

    def _generate_video_vidu(self, gen: PlaygroundGeneration, out_path: str) -> None:
        """Delegate to :class:`ViduModel`."""
        from ...models.vidu import ViduModel

        if self._vidu_model is None:
            self._vidu_model = ViduModel(dict(self.model_config), **({"env": self._env} if self._env is not None else {}))

        params = gen.parameters
        img_path, img_url = self._resolve_first_input_media(gen)

        self._vidu_model.generate(
            prompt=gen.prompt,
            output_path=out_path,
            img_url=cast(str, img_url),
            img_path=cast(str, img_path),
            duration=params.get("duration", 5),
            model=gen.model_id,
            resolution=params.get("resolution", "720p"),
            aspect_ratio=params.get("aspect_ratio", "16:9"),
            seed=params.get("seed", 0),
            audio=params.get("audio", True),
            movement_amplitude=params.get("movement_amplitude", "auto"),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sync_library_directories(self, directory: Path) -> None:
        """Make the copied file and newly created ancestor entries durable.

        Sync before library registration so metadata cannot point at a copy whose
        directory entry was never flushed. As with JSON storage, Windows has no
        portable stdlib directory-fsync support; file sync and atomic replacement
        there do not promise power-loss durability of directory entries.
        """
        if os.name == "nt":
            return
        while True:
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if directory == self.data_dir:
                return
            directory = directory.parent

    @staticmethod
    def _category_to_asset_type(category: Optional[str]) -> str:
        """Map a Playground save category to a global-library asset_type.

        Known categories (``character`` / ``scene`` / ``prop``) pass through;
        everything else -- including the ``"general"`` default, empty string,
        or ``None`` -- falls back to ``"prop"``."""
        normalized = (category or "").strip().lower()
        if normalized in ("character", "scene", "prop"):
            return normalized
        return "prop"

    def _resolve_first_input_media(self, gen: PlaygroundGeneration) -> tuple[str | None, str | None]:
        """Return ``(img_path, img_url)`` for the first entry in
        :pyattr:`input_media`.  Local files are returned as *img_path*;
        remote URLs as *img_url*."""
        if not gen.input_media:
            return None, None

        first = gen.input_media[0]
        if first.startswith(("http://", "https://")):
            return None, first

        candidate = resolve_local_media_path(first, output_root=str(self.data_dir))
        if candidate is not None and os.path.isfile(candidate):
            return str(candidate), None
        # Remote/object-key handling remains in the provider adapter; bound adapters
        # reject local references which escape this application's media root.
        return None, first
