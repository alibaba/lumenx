"""Studio's selected asset motion association over the durable video executor.

The requesting project owns execution/receipts; the resolved asset owner only
receives a guarded projection. Explicit recovery delegates to the existing
video executor and never grants another submission.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from ...models.provider_recovery import (
    build_accepted_provider_submission_and_record, handle_from_record,
    observation_from_record, require_matching_handle,
)
from ...models.provider_tasks import PreparedMedia, ProviderTaskStatus
from ...utils.generation_intent import normalize_generation_intent
from ...utils.media_refs import classify_media_ref, MEDIA_REF_DATA_URI, MEDIA_REF_LOCAL_PATH
from ...utils.media_versions import freeze_media
from ...utils.request_identity import fingerprint_request, validate_client_request_id
from ...utils.json_storage import StorageConflictError, StorageError
from .models import Character, GenerationStatus, MotionReferenceBinding, MotionReferenceReceipt, Prop, Scene, VideoTask, VideoVariant
from .provider_recovery import StudioProviderAdmissionResult

if TYPE_CHECKING:
    from .pipeline import ComicGenPipeline


ENTRYPOINT = "studio.motion_reference.v1"
MODEL = "wan2.7-i2v"


class MotionReferenceAdmissionRejected(ValueError):
    """A strict command proven unaccepted, after original receipt lookup."""

    def __init__(self, key: str, *, reason: str | None = None):
        self.client_request_id = key
        self.reason = reason
        self.code = "motion_reference_not_eligible" if reason is not None else "motion_reference_target_changed"
        self.status_code = 422 if reason is not None else 409
        super().__init__(self.code)


def target_fingerprint(target) -> str:
    # Startup can mark orphan assets unconfirmed without changing author input.
    raw = target.model_dump(mode="json")
    raw.pop("status", None)
    raw.pop("full_body_motion_request_id", None)
    raw.pop("head_shot_motion_request_id", None)
    raw.pop("motion_request_id", None)
    return fingerprint_request(raw, entrypoint=ENTRYPOINT + ".target")


class StudioMotionReferenceService:
    def __init__(self, pipeline: ComicGenPipeline):
        self.pipeline = pipeline

    @staticmethod
    def _receipt(script, key):
        return next((receipt for receipt in script.provider_request_receipts
                     if isinstance(receipt, MotionReferenceReceipt)
                     and receipt.client_request_id == key), None)

    def _result(self, script_id, receipt, *, replayed):
        script = self.pipeline.read_committed_script(script_id)
        exists = any(task.id == receipt.task_id for task in script.video_tasks)
        return StudioProviderAdmissionResult(
            script=script, task_id=receipt.task_id, attempt_id=receipt.attempt_id,
            execution_fingerprint=receipt.execution_fingerprint,
            client_request_id=receipt.client_request_id, replayed=replayed,
            new_task_ids=() if replayed else (receipt.task_id,),
            reason=None if exists else "task_deleted",
        )

    @staticmethod
    def _effective_parameters(params):
        resolution = params.get("resolution") or "720P"
        effective = {
            "resolution": resolution.lower() if isinstance(resolution, str) else resolution,
            "prompt_extend": params.get("prompt_extend", True),
            "watermark": params.get("watermark", False),
        }
        if params.get("negative_prompt"):
            effective["negative_prompt"] = params["negative_prompt"]
        if params.get("seed") is not None:
            effective["seed"] = params["seed"]
        return effective

    def _qualification(self, script_id, asset_id, asset_type, batch_size, has_audio):
        """Capture one non-secret author precondition; caller holds save lock."""
        from .pipeline import CommandValidationError, ProjectContextError, _validate_safe_id

        p = self.pipeline
        p.assert_storage_available()
        try:
            _validate_safe_id(asset_id, "asset id")
            if asset_type not in {"full_body", "head_shot", "scene", "prop"}:
                raise ValueError("Invalid motion reference asset_type")
            if type(batch_size) is not int or batch_size < 1 or type(has_audio) is not bool:
                raise ValueError("Invalid motion reference qualifiers")
        except ValueError as exc:
            raise CommandValidationError(str(exc)) from exc
        view = p.read_committed_project_view(script_id)
        script, series, library = view
        if script is None:
            raise ProjectContextError("Project not found", not_found=True)
        target, source, owner = p._saved_motion_target(view, asset_id, asset_type)
        if target is None or owner is None:
            raise ProjectContextError("Motion reference target not found", not_found=True)
        live, live_target, live_source, live_owner_object, live_owner = p._live_motion_target(script_id, asset_id, asset_type)
        owner_object = script if source == "script" else (series if source == "series" else library)
        live_series = p.series_store.get(series.id) if series is not None else None
        if (
            live is None or not isinstance(live_target, (Character, Scene, Prop))
            or owner_object is None or live_owner_object is None
            or source != live_source or owner != live_owner
            or live.revision != script.revision or live.series_id != script.series_id
            or live_owner_object.revision != owner_object.revision
            or live_target.model_dump() != target.model_dump()
            or (series is not None and (live_series is None or live_series.revision != series.revision
                                       or live_series.episode_ids != series.episode_ids))
        ):
            raise StorageConflictError("Motion reference target changed; reload before retrying")
        config = p._component_config("video", p.data_dir / "video", str(p.data_dir))
        model = config.get("model", {})
        if not isinstance(model, dict):
            raise CommandValidationError("Invalid motion reference model configuration")
        params = model.get("params", {})
        if not isinstance(params, dict):
            raise CommandValidationError("Invalid motion reference model configuration")
        model_id = params.get("i2v_model_name", MODEL)
        if not isinstance(model_id, str) or not model_id:
            raise CommandValidationError("Invalid motion reference model configuration")
        unit = getattr(target, asset_type) if isinstance(target, Character) else target.image_asset
        selected = next((value for value in p._image_variants(unit) if value.id == p._selected_image(unit)), None)
        source_url = p._motion_source_image(target, asset_type) or ""
        source_kind = classify_media_ref(source_url, output_root=str(p.data_dir))
        reason = next((reason for condition, reason in (
            (batch_size != 1, "batch_unsupported"),
            (has_audio, "request_audio_present"),
            (model_id != MODEL, "model_unsupported"),
            (bool(params.get("audio_url")), "configured_audio_present"),
            (selected is None, "selected_image_required"),
            (isinstance(target, (Scene, Prop)) and selected is not None and selected.url != source_url,
             "selected_image_mismatch"),
            (source_kind not in {MEDIA_REF_LOCAL_PATH, MEDIA_REF_DATA_URI}, "source_kind_unsupported"),
        ) if condition), None)
        route = None
        if model_id == MODEL:
            # Selector construction is read-only. In particular, never resolve
            # a secret or bind credentials merely to display eligibility.
            scope = p._provider_runtime_for_admission(provider_model_id=MODEL, gateway="dashscope").scope
            route = {name: getattr(scope, name) for name in ("provider", "backend", "gateway", "base_url", "region")}
        stamp = fingerprint_request({
            "project_id": script.id, "project_revision": script.revision,
            "owner": owner, "owner_revision": owner_object.revision,
            "series_id": script.series_id,
            "series_membership": series.episode_ids if series else None,
            "asset_id": asset_id, "asset_type": asset_type,
            "target": target_fingerprint(target), "selected_image_id": selected.id if selected else None, "source_kind": source_kind,
            "batch_size": batch_size, "has_audio": has_audio,
            "model_id": model_id, "configured_audio": bool(params.get("audio_url")),
            "effective_parameters": self._effective_parameters(params), "route": route,
        }, entrypoint=ENTRYPOINT + ".author-precondition.v1")
        return {
            "project_id": script.id, "asset_id": asset_id, "asset_type": asset_type,
            "eligible": reason is None, "model_id": model_id, "reason": reason,
            "owner": {"kind": owner[0], "id": owner[1]}, "selected_image_id": selected.id if selected else None,
            "source_kind": source_kind, "expected_target": stamp,
        }, params

    def preflight(self, script_id, asset_id, asset_type, *, batch_size=1, has_audio=False):
        with self.pipeline._save_lock:
            result, _ = self._qualification(script_id, asset_id, asset_type, batch_size, has_audio)
            return result

    def assert_owner_available(self, context):
        """Shared admission boundary; callers hold the pipeline save lock."""
        p = self.pipeline
        if context["key"] in getattr(p, "_motion_ref_runs", {}):
            raise StorageConflictError("This asset is already generating a motion reference")
        for project_id in p._committed.get("projects.json", {}):
            for task in p.read_committed_script(project_id).video_tasks:
                binding, record = task.motion_reference, task.provider_execution
                if (
                    binding is None or record is None or task.asset_id != context["asset_id"]
                    or p._motion_kind(binding.channel) != context["kind"]
                    or (binding.owner_kind, binding.owner_id) != context["owner"]
                ):
                    continue
                if task.has_completed_motion_artifact() or record.phase == "cancelled":
                    continue
                if record.phase == "observed":
                    try:
                        status = observation_from_record(record).status
                    except ValueError as exc:
                        raise StorageConflictError("motion_reference_request_unconfirmed") from exc
                    if status in {ProviderTaskStatus.FAILED, ProviderTaskStatus.CANCELED}:
                        continue
                raise StorageConflictError("motion_reference_request_unconfirmed")

    def admit(self, script_id: str, raw: dict, client_request_id: str | None, *, expected_target: str | None = None):
        """Return None for an untouched legacy route; never fall back on error."""
        from .pipeline import CommandValidationError, ProjectContextError

        p = self.pipeline
        with p._save_lock:
            p.assert_storage_available()
            script = p.scripts.get(script_id)
            if script is None:
                raise ProjectContextError("Project not found", not_found=True)
            try:
                key = validate_client_request_id(client_request_id) if client_request_id is not None else str(uuid.uuid4())
                request_digest = fingerprint_request(raw, entrypoint=ENTRYPOINT)
            except ValueError as exc:
                raise CommandValidationError(str(exc)) from exc
            receipt = self._receipt(script, key)
            if expected_target is not None:
                committed = p.read_committed_script(script_id)
                live_receipts = [value for value in script.provider_request_receipts
                                 if isinstance(value, MotionReferenceReceipt) and value.client_request_id == key]
                saved_receipts = [value for value in committed.provider_request_receipts
                                  if isinstance(value, MotionReferenceReceipt) and value.client_request_id == key]
                if len(live_receipts) > 1 or live_receipts != saved_receipts:
                    raise StorageConflictError("motion_request_receipt_invalid")
                receipt = saved_receipts[0] if saved_receipts else None
            if receipt is not None:
                if receipt.request_fingerprint != request_digest:
                    raise StorageConflictError("motion_request_identity_conflict")
                return self._result(script_id, receipt, replayed=True)

            if expected_target is not None:
                qualification, params = self._qualification(
                    script_id, raw["asset_id"], raw["asset_type"], raw["batch_size"], bool(raw["audio_url"]),
                )
                if not qualification["eligible"]:
                    raise MotionReferenceAdmissionRejected(key, reason=qualification["reason"])
                if qualification["expected_target"] != expected_target:
                    raise MotionReferenceAdmissionRejected(key)
            else:
                if raw["asset_type"] not in {"full_body", "head_shot", "scene", "prop"} or raw["batch_size"] != 1 or raw["audio_url"]:
                    return None
                config = p._component_config("video", p.data_dir / "video", str(p.data_dir))
                params = (config.get("model") or {}).get("params") or {}
                if params.get("i2v_model_name", MODEL) != MODEL or params.get("audio_url"):
                    return None
            context = p._prepare_motion_ref(
                p.read_committed_project_view(script_id), raw["asset_id"], raw["asset_type"],
                {name: raw[name] for name in ("prompt", "audio_url", "duration", "batch_size")},
            )
            target = context["target_identity"]
            unit = getattr(target, raw["asset_type"]) if isinstance(target, Character) else target.image_asset
            selected = next((value for value in p._image_variants(unit) if value.id == p._selected_image(unit)), None)
            if selected is None:
                return None
            source = context["options"]["image_url"]
            if isinstance(target, (Scene, Prop)) and selected.url != source:
                raise CommandValidationError("Selected motion image does not match the current image URL")
            if classify_media_ref(source, output_root=str(p.data_dir)) not in {MEDIA_REF_DATA_URI, MEDIA_REF_LOCAL_PATH}:
                return None
            self.assert_owner_available(context)

            effective = {"duration": raw["duration"], **self._effective_parameters(params)}
            # Freeze the same configured resolution/default and seed as legacy
            # Wan2.7. Existing receipts retain their original intent on recovery.
            task_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
            try:
                intent = normalize_generation_intent({
                    "model_id": MODEL, "mode": "i2v", "prompt": context["options"]["prompt"],
                    "params": effective,
                }, allowed_modes={"i2v"}, allowed_parameters=set(effective))
                runtime = p._provider_runtime_for_admission(provider_model_id=MODEL, gateway="dashscope")
                version = freeze_media(
                    source, output_root=p.data_dir, writer_lock=p._writer_lock,
                    role="first_frame", ordinal=0, max_bytes=runtime.max_bytes or 20 * 1024 * 1024,
                )
                media = PreparedMedia.from_media_version(
                    version, output_root=p.data_dir, max_bytes=runtime.max_bytes or 20 * 1024 * 1024,
                )
                # The actual Core validator runs before any credential binding.
                submission, _ = build_accepted_provider_submission_and_record(
                    intent, provider_model_id=MODEL, media_version=version, prepared_media=media,
                    scope=runtime.scope, credential_ref="motion-admission-validation", attempt_id=attempt_id,
                    mode_source="entry_default", admission_request_fields=tuple(raw),
                )
                secret = runtime.resolve_secret()
            except ValueError as exc:
                raise CommandValidationError(str(exc)) from exc
            p._check_motion_ref(context)
            if p._provider_runtime_for_admission(provider_model_id=MODEL, gateway="dashscope") is not runtime:
                raise StorageConflictError("Provider recovery runtime changed before admission")
            try:
                credential = p._provider_credential_store.bind(runtime.scope, secret)
            except StorageError as exc:
                p.mark_storage_failed(exc)
                p.close()
                raise
            _, record = build_accepted_provider_submission_and_record(
                intent, provider_model_id=MODEL, media=submission.media, scope=credential.scope,
                credential_ref=credential.credential_ref, attempt_id=attempt_id,
                mode_source="entry_default", admission_request_fields=tuple(raw),
            )
            owner_kind, owner_id = context["owner"]
            binding = MotionReferenceBinding(
                channel=raw["asset_type"], owner_kind=owner_kind, owner_id=owner_id, client_request_id=key,
                source_image_id=selected.id, target_fingerprint=target_fingerprint(target),
            )
            task = VideoTask.model_validate({
                "id": task_id, "project_id": script_id, "asset_id": raw["asset_id"],
                "image_url": source, "prompt": intent.prompt, "duration": raw["duration"],
                "resolution": effective["resolution"], "generate_audio": True, "model": MODEL,
                "prompt_extend": effective["prompt_extend"], "watermark": effective["watermark"],
                "negative_prompt": effective.get("negative_prompt"), "seed": effective.get("seed"),
                "motion_reference": binding, "provider_execution": record,
            })
            receipt = MotionReferenceReceipt(
                client_request_id=key, asset_id=raw["asset_id"], asset_type=raw["asset_type"],
                owner_kind=owner_kind, owner_id=owner_id,
                request_fingerprint=request_digest, task_id=task_id, attempt_id=attempt_id,
                execution_fingerprint=record.fingerprint, created_at=time.time(),
            )
            pointer = f'{raw["asset_type"]}_motion_request_id' if isinstance(target, Character) else "motion_request_id"
            setattr(target, pointer, task_id)
            target.status = GenerationStatus.PROCESSING
            script.video_tasks.append(task)
            script.provider_request_receipts.append(receipt)
            p._commit_storage(*p._video_task_storage_names(context["source"]))
            return self._result(script_id, receipt, replayed=False)

    def target(self, script_id, task):
        """Resolve both live/committed association; never rebind the execution."""
        p, binding = self.pipeline, task.motion_reference
        if binding is None:
            raise StorageConflictError("motion_reference_binding_missing")
        _, target, source, _, owner = p._live_motion_target(script_id, task.asset_id, binding.channel)
        saved, saved_source, saved_owner = p._saved_motion_target(
            p.read_committed_project_view(script_id), task.asset_id, binding.channel,
        )
        expected_owner = (binding.owner_kind, binding.owner_id)
        asset_class = Character if binding.channel in {"full_body", "head_shot"} else Scene if binding.channel == "scene" else Prop
        if not isinstance(target, asset_class) or not isinstance(saved, asset_class) or owner != expected_owner or saved_owner != expected_owner or source != saved_source:
            raise StorageConflictError("motion_reference_owner_changed")
        pointer = f"{binding.channel}_motion_request_id" if isinstance(target, Character) else "motion_request_id"
        if getattr(target, pointer) != task.id or getattr(saved, pointer) != task.id:
            raise StorageConflictError("motion_reference_target_changed")
        if target_fingerprint(target) != binding.target_fingerprint or target_fingerprint(saved) != binding.target_fingerprint:
            raise StorageConflictError("motion_reference_inputs_changed")
        return target, source

    def project_completed(self, script_id, task):
        """Called under the executor lock, committed with the paid artifact."""
        try:
            target, source = self.target(script_id, task)
            unit = getattr(target, task.motion_reference.channel, None)
            if isinstance(target, Character) and unit is None:
                raise StorageConflictError("motion_reference_inputs_changed")
        except StorageConflictError as exc:
            task.status, task.error = "needs_confirmation", str(exc)
            return None
        if unit is not None:
            unit.video_variants.append(VideoVariant.model_validate({
                "id": task.id, "url": task.video_url, "prompt_used": task.prompt,
                "source_image_id": task.motion_reference.source_image_id,
            }))
            if not unit.selected_video_id:
                unit.selected_video_id = task.id
            unit.video_prompt, unit.video_updated_at = task.prompt, time.time()
        else:
            # Scene/prop history retains its existing VideoTask shape. Only
            # the requesting project's task owns execution and query authority.
            target.video_assets.append(task.model_copy(deep=True, update={
                "provider_name": None, "provider_task_id": None, "provider_request_id": None,
                "provider_execution": None, "motion_reference": None,
            }))
        target.status = GenerationStatus.COMPLETED
        return source

    def read_request(self, script_id, key):
        key = validate_client_request_id(key)
        with self.pipeline._save_lock:
            script = self.pipeline.read_committed_script(script_id)
            receipt = self._receipt(script, key) if script else None
            if receipt is None:
                return None
            task = next((item for item in script.video_tasks if item.id == receipt.task_id), None)
            return {
                "task_id": receipt.task_id, "request": receipt.model_dump(mode="json"),
                "task": task.model_dump(mode="json") if task else None,
                "reason": task.error if task else "task_deleted",
            }

    def require_recovery(self, script, task, expected):
        """Validate original authority at each real operation claim/commit lock."""
        key, attempt_id, fingerprint = expected
        try:
            saved = self.pipeline.read_committed_script(script.id)
            original = next((value for value in saved.video_tasks if value.id == task.id), None)
            live_receipts = [value for value in script.provider_request_receipts
                             if isinstance(value, MotionReferenceReceipt) and value.client_request_id == key]
            saved_receipts = [value for value in saved.provider_request_receipts
                              if isinstance(value, MotionReferenceReceipt) and value.client_request_id == key]
            members = [member for receipt in script.provider_request_receipts
                       for member in receipt.receipt_members if member.task_id == task.id]
            record, binding = task.provider_execution, task.motion_reference
            if (
                len(live_receipts) != 1 or live_receipts != saved_receipts or len(members) != 1
                or original is None or task.model_dump() != original.model_dump()
                or record is None or binding is None or task.project_id != script.id or task.frame_id is not None
                or task.model != MODEL or record.provider_model_id != MODEL or task.generation_mode != "i2v"
                or (record.attempt_id, record.fingerprint) != (attempt_id, fingerprint)
            ):
                raise ValueError("Original motion authority changed")
            receipt = live_receipts[0]
            if (
                receipt.entrypoint != ENTRYPOINT or receipt.task_id != task.id or receipt.asset_id != task.asset_id
                or receipt.asset_type != binding.channel
                or (receipt.attempt_id, receipt.execution_fingerprint) != (attempt_id, fingerprint)
                or (receipt.owner_kind, receipt.owner_id, receipt.client_request_id)
                != (binding.owner_kind, binding.owner_id, binding.client_request_id)
            ):
                raise ValueError("Original motion receipt changed")
            if record.phase == "cancelled":
                return "terminal"
            if record.phase == "accepted":
                raise ValueError("An unsubmitted attempt requires explicit continuation")
            require_matching_handle(record, handle_from_record(record))
            if record.phase == "completed" or record.artifact is not None or record.prepared_artifact is not None:
                return "fetch"
            if record.observation is not None:
                status = observation_from_record(record).status
                if status in {ProviderTaskStatus.FAILED, ProviderTaskStatus.CANCELED}:
                    return "terminal"
                if status is ProviderTaskStatus.SUCCEEDED:
                    return "fetch"
            return "query"
        except (ValueError, TypeError, KeyError) as exc:
            raise StorageConflictError("motion_reference_recover_not_allowed") from exc

    def _recovery_target(self, script_id, expected):
        """Caller holds save lock; absence never becomes a new admission."""
        p = self.pipeline
        p.assert_storage_available()
        script = p.scripts.get(script_id)
        if script is None:
            raise ValueError("Script not found")
        saved = p.read_committed_script(script_id)
        receipt = self._receipt(saved, expected[0])
        if receipt is None:
            raise ValueError("Motion reference request not found")
        task = next((value for value in script.video_tasks if value.id == receipt.task_id), None)
        if task is None:
            raise ValueError("Video task not found")
        if (script_id, task.id) in p._video_task_runs:
            raise StorageConflictError("provider_operation_running")
        return task, self.require_recovery(script, task, expected)

    def recover(self, script_id, key, *, expected_attempt_id, expected_fingerprint):
        """Only query/fetch the original handle; never submit or auto-adopt."""
        expected = (validate_client_request_id(key), expected_attempt_id, expected_fingerprint)
        p = self.pipeline
        with p._save_lock:
            task, action = self._recovery_target(script_id, expected)
            task_id = task.id
        if action == "query":
            p.query_provider_video_task(script_id, task_id, expected_motion_request=expected)
            # Another operation may have advanced the task between claims. The
            # next primitive validates again under its own execution lock.
            with p._save_lock:
                _, action = self._recovery_target(script_id, expected)
        if action == "fetch":
            p.fetch_provider_video_task(script_id, task_id, expected_motion_request=expected)
        return self.read_request(script_id, key)

    def read_task_status(self, task_id):
        for raw in self.pipeline._committed.get("projects.json", {}).values():
            for task in raw.get("video_tasks", []):
                if task.get("id") == task_id and task.get("motion_reference"):
                    return {
                        "task_id": task_id, "status": task["status"],
                        "progress": 100 if task["status"] == "completed" else 0,
                        "error": task.get("error"), "asset_id": task["asset_id"],
                        "asset_type": task["motion_reference"].get("channel", "full_body"),
                        "script_id": task["project_id"], "created_at": task["created_at"],
                        "client_request_id": task["motion_reference"]["client_request_id"],
                    }
        return None
