"""Pure helpers for Studio provider execution recovery records.

Admission, reconstruction, receipt validation, and observation mapping live here
without product-side orchestration. Pipeline methods own lifecycle decisions,
submit/query sequencing, locks, and status persistence. There is still no
startup scheduling, default migration, or implicit legacy-path wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ...models import provider_recovery as _provider_recovery
from ...models.provider_execution import ProviderExecutionRecord
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import (
    ProviderObservation,
    ProviderScope,
    ProviderSubmission,
    ProviderTaskStatus,
)
from ...utils.generation_intent import normalize_generation_intent
from ...utils.media_versions import freeze_media, read_verified_media
from .models import Script


MAX_PROVIDER_MEDIA_BYTES = _provider_recovery.MAX_PROVIDER_MEDIA_BYTES
MAX_PROVIDER_VIDEO_BYTES = _provider_recovery.MAX_PROVIDER_VIDEO_BYTES
MAX_PROVIDER_AUDIO_BYTES = _provider_recovery.MAX_PROVIDER_AUDIO_BYTES
PreparedMedia = _provider_recovery.PreparedMedia
StudioProviderRecoveryError = _provider_recovery.ProviderRecoveryError
StudioProviderRecoveryRuntime = ProviderRecoveryRuntime
_fingerprint = _provider_recovery._fingerprint
_generation_intent_from_record = _provider_recovery._generation_intent_from_record
_verify_record_fingerprint = _provider_recovery._verify_record_fingerprint
artifact_from_proof = _provider_recovery.artifact_from_proof
artifact_proof_from_record = _provider_recovery.artifact_proof_from_record
handle_from_record = _provider_recovery.handle_from_record
observation_from_record = _provider_recovery.observation_from_record
observation_to_dict = _provider_recovery.observation_to_dict
record_with_changes = _provider_recovery.record_with_changes
reconstruct_submission_from_record = _provider_recovery.reconstruct_submission_from_record
require_matching_artifact_proof = _provider_recovery.require_matching_artifact_proof
require_matching_handle = _provider_recovery.require_matching_handle
validate_observation_for_handle = _provider_recovery.validate_observation_for_handle


RECOVERABLE_WAN_MODELS = {"wan2.6-i2v", "wan2.6-i2v-flash", "wan2.7-i2v"}
STUDIO_VIDEO_ENTRYPOINT = "studio.provider_video.v1"
STUDIO_ORDERED_VIDEO_ENTRYPOINT = "studio.provider_video.v2"
ORDERED_MEDIA_CONTRACT_VERSION = "ordered-media-v1"
ORDERED_WAN3_MODELS = {"wan3.0-video", "wan3.0-video-prime"}
_ORDERED_VIDEO_PARAMETERS = {"duration", "resolution", "ratio", "seed", "prompt_extend", "watermark", "audio"}
_ALLOWED_FIELDS = {
    "image_url",
    "prompt",
    "model",
    "model_id",
    "frame_id",
    "asset_id",
    "generation_mode",
    "reference_video_urls",
    "reference_image_urls",
    "audio_url",
    "watermark",
    "duration",
    "resolution",
    "seed",
    "negative_prompt",
    "prompt_extend",
    "generate_audio",
    "shot_type",
    "workbench_tab",
}
_ALLOWED_INTENT_PARAMS = {
    "duration",
    "resolution",
    "seed",
    "negative_prompt",
    "prompt_extend",
    "audio",
    "shot_type",
    "watermark",
}


@dataclass(frozen=True)
class StudioProviderAdmissionResult:
    """Stable admission identity plus a detached current product read view.

    Only newly committed tasks appear in ``new_task_ids``. A replay never
    grants dispatch permission, including while the original task is queued.
    """

    script: Script
    task_id: str
    attempt_id: str
    execution_fingerprint: str
    client_request_id: str | None
    replayed: bool
    new_task_ids: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class StudioProviderReceiptMember:
    """Stable task identity captured by a provider request receipt."""

    task_id: str
    attempt_id: str
    execution_fingerprint: str


@dataclass(frozen=True)
class StudioProviderBatchAdmissionResult:
    """Stable batch admission identity plus a detached current product read view."""

    script: Script
    task_ids: tuple[str, ...]
    members: tuple[StudioProviderReceiptMember, ...]
    client_request_id: str
    replayed: bool
    new_task_ids: tuple[str, ...]
    reason: str | None = None


def uses_recoverable_wan_model(model: Any) -> bool:
    return isinstance(model, str) and model in RECOVERABLE_WAN_MODELS


def build_accepted_submission_and_record(
    request: Mapping[str, Any],
    *,
    output_root: Path,
    writer_lock,
    scope: ProviderScope,
    credential_ref: str,
    attempt_id: str,
    max_bytes: int | None = None,
) -> tuple[ProviderSubmission, ProviderExecutionRecord]:
    """Normalize raw admission input and return a durable accepted record."""
    max_bytes = MAX_PROVIDER_MEDIA_BYTES if max_bytes is None else max_bytes
    raw, model_id, mode_source = _normalize_raw_request(request)
    params = _raw_intent_params(raw)
    intent = normalize_generation_intent(
        {"mode": "i2v", "prompt": raw["prompt"], "model_id": model_id, "params": params},
        allowed_modes={"i2v"},
        allowed_parameters=_ALLOWED_INTENT_PARAMS,
    )
    media_version = freeze_media(
        raw["image_url"],
        output_root=output_root,
        writer_lock=writer_lock,
        role="first_frame",
        ordinal=0,
        max_bytes=max_bytes,
    )
    prepared_media = PreparedMedia.from_media_version(
        media_version,
        output_root=output_root,
        max_bytes=max_bytes,
    )
    return _provider_recovery.build_accepted_provider_submission_and_record(
        intent,
        provider_model_id=model_id,
        media_version=media_version,
        prepared_media=prepared_media,
        scope=scope,
        credential_ref=credential_ref,
        attempt_id=attempt_id,
        mode_source=mode_source,
        admission_request_fields=tuple(raw["_request_fields"]),
    )



def build_ordered_accepted_submission_and_record(
    request: Mapping[str, Any],
    *,
    output_root: Path,
    writer_lock,
    scope: ProviderScope,
    credential_ref: str,
    attempt_id: str,
    max_bytes: int | None = None,
) -> tuple[ProviderSubmission, ProviderExecutionRecord]:
    """Normalize explicit Wan3 ordered Studio input and return an accepted record.

    This is a pure admission helper: it freezes current managed media bytes into
    Core provider media versions and never uploads or submits to the provider.
    Pipeline/API receipt wiring remains a separate owner.
    """
    max_bytes = MAX_PROVIDER_MEDIA_BYTES if max_bytes is None else max_bytes
    raw, provider_model_id, mode = _normalize_ordered_raw_request(request)
    params = _ordered_intent_params(raw)
    intent = normalize_generation_intent(
        {"mode": mode, "prompt": raw["prompt"], "model_id": raw["model_id"], "params": params},
        allowed_modes={"t2v", "i2v", "r2v"},
        allowed_parameters=_ORDERED_VIDEO_PARAMETERS,
    )
    if intent.model_id not in {"wan/wan3.0-video#video", "wan/wan3.0-video-prime#video"}:
        raise StudioProviderRecoveryError("provider_model_unsupported")
    media = _ordered_prepared_media(
        raw["_media"], output_root=output_root, writer_lock=writer_lock, max_bytes=max_bytes,
    )
    return _provider_recovery.build_accepted_provider_submission_and_record(
        intent,
        provider_model_id=provider_model_id,
        media=tuple(media),
        scope=scope,
        credential_ref=credential_ref,
        attempt_id=attempt_id,
        mode_source="request",
        admission_request_fields=tuple(raw["_request_fields"]),
    )


def _normalize_ordered_raw_request(request: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    if not isinstance(request, Mapping):
        raise StudioProviderRecoveryError("provider_request_invalid")
    allowed = {
        "contract_version", "image_url", "last_frame_url", "prompt", "model", "model_id",
        "frame_id", "asset_id", "generation_mode", "reference_video_urls", "reference_image_urls",
        "reference_audio_urls", "audio_url", "duration", "resolution", "ratio", "seed",
        "prompt_extend", "watermark", "generate_audio",
        "workbench_tab",
    }
    if set(request) - allowed:
        raise StudioProviderRecoveryError("provider_request_unknown_field")
    raw = dict(request)
    raw["_request_fields"] = set(request.keys())
    if raw.get("contract_version") != ORDERED_MEDIA_CONTRACT_VERSION:
        raise StudioProviderRecoveryError("provider_contract_unsupported")
    model_present = "model" in raw
    model_id_present = "model_id" in raw
    if not model_present and not model_id_present:
        raise StudioProviderRecoveryError("provider_model_required")
    if model_present and (not isinstance(raw["model"], str) or not raw["model"]):
        raise StudioProviderRecoveryError("provider_model_invalid")
    if model_id_present and (not isinstance(raw["model_id"], str) or not raw["model_id"]):
        raise StudioProviderRecoveryError("provider_model_invalid")
    model_id = raw["model"] if model_present else raw["model_id"]
    if model_present and model_id_present and raw["model"] != raw["model_id"]:
        raise StudioProviderRecoveryError("provider_model_alias_conflict")
    if model_id not in ORDERED_WAN3_MODELS:
        raise StudioProviderRecoveryError("provider_model_unsupported")
    raw["model_id"] = f"wan/{model_id}#video"
    if "asset_id" in raw:
        raise StudioProviderRecoveryError("provider_asset_owner_unsupported")
    if not raw.get("frame_id"):
        raise StudioProviderRecoveryError("provider_frame_owner_required")
    if "workbench_tab" in raw and raw["workbench_tab"] is not None and not isinstance(raw["workbench_tab"], str):
        raise StudioProviderRecoveryError("provider_workbench_tab_invalid")
    mode = raw.get("generation_mode")
    if mode not in {"t2v", "i2v", "r2v"}:
        raise StudioProviderRecoveryError("provider_mode_unsupported")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        raise StudioProviderRecoveryError("provider_prompt_required")
    raw["_media"] = _ordered_media_from_studio_fields(raw, mode)
    return raw, model_id, mode


def _nonempty_text(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not value:
        raise StudioProviderRecoveryError(reason)
    return value


def _string_list(value: Any, reason: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise StudioProviderRecoveryError(reason)
    return list(value)


def _ordered_media_from_studio_fields(raw: Mapping[str, Any], mode: str) -> tuple[dict[str, Any], ...]:
    image_url = raw.get("image_url")
    last_frame_url = raw.get("last_frame_url")
    reference_images = _string_list(raw.get("reference_image_urls", []), "provider_reference_image_invalid")
    reference_videos = _string_list(raw.get("reference_video_urls", []), "provider_reference_video_invalid")
    reference_audios = _string_list(raw.get("reference_audio_urls", []), "provider_reference_audio_invalid")
    if "audio_url" in raw:
        if reference_audios:
            raise StudioProviderRecoveryError("provider_reference_audio_conflict")
        reference_audios = [_nonempty_text(raw["audio_url"], "provider_reference_audio_invalid")]
    references_present = bool(reference_images or reference_videos or reference_audios)
    frame_present = "image_url" in raw or "last_frame_url" in raw
    if mode == "t2v":
        if frame_present or references_present:
            raise StudioProviderRecoveryError("provider_media_conflict")
        return ()
    if mode == "i2v":
        if references_present:
            raise StudioProviderRecoveryError("provider_references_unsupported")
        first = _nonempty_text(image_url, "provider_first_frame_required")
        media = [{"role": "first_frame", "ordinal": 0, "media_url": first}]
        if "last_frame_url" in raw:
            media.append({"role": "last_frame", "ordinal": 0, "media_url": _nonempty_text(last_frame_url, "provider_last_frame_invalid")})
        return tuple(media)
    if mode == "r2v":
        if frame_present:
            raise StudioProviderRecoveryError("provider_frame_refs_conflict")
        media = []
        media.extend({"role": "reference_image", "ordinal": index, "media_url": value} for index, value in enumerate(reference_images))
        media.extend({"role": "reference_video", "ordinal": index, "media_url": value} for index, value in enumerate(reference_videos))
        media.extend({"role": "reference_audio", "ordinal": index, "media_url": value} for index, value in enumerate(reference_audios))
        if not media:
            raise StudioProviderRecoveryError("provider_references_required")
        image_count = len(reference_images)
        video_count = len(reference_videos)
        audio_count = len(reference_audios)
        if image_count > 10 or video_count > 5 or audio_count > 5 or len(media) > 20:
            raise StudioProviderRecoveryError("provider_references_unsupported")
        return tuple(media)
    raise StudioProviderRecoveryError("provider_mode_unsupported")


def _ordered_intent_params(request: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for source_key, param_key in (
        ("duration", "duration"),
        ("resolution", "resolution"),
        ("ratio", "ratio"),
        ("seed", "seed"),
        ("prompt_extend", "prompt_extend"),
        ("watermark", "watermark"),
        ("generate_audio", "audio"),
    ):
        if source_key in request:
            params[param_key] = request[source_key]
    return params


def _ordered_prepared_media(
    selections: tuple[dict[str, Any], ...],
    *,
    output_root: Path,
    writer_lock,
    max_bytes: int,
) -> list[PreparedMedia]:
    prepared: list[PreparedMedia] = []
    for item in selections:
        role = item["role"]
        ordinal = item["ordinal"]
        limit = {
            "reference_video": MAX_PROVIDER_VIDEO_BYTES,
            "reference_audio": MAX_PROVIDER_AUDIO_BYTES,
        }.get(role, max_bytes)
        version = freeze_media(
            item["media_url"],
            output_root=output_root,
            writer_lock=writer_lock,
            role=role,
            ordinal=ordinal,
            max_bytes=limit,
        )
        if role in {"reference_video", "reference_audio"}:
            data = read_verified_media(version, output_root=output_root, max_bytes=limit)
            prepared.append(PreparedMedia.from_pending_av_bytes(version, data))
        else:
            prepared.append(PreparedMedia.from_media_version(version, output_root=output_root, max_bytes=limit))
    return prepared

def _normalize_raw_request(request: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    if not isinstance(request, Mapping):
        raise StudioProviderRecoveryError("provider_request_invalid")
    if set(request) - _ALLOWED_FIELDS:
        raise StudioProviderRecoveryError("provider_request_unknown_field")
    raw = dict(request)
    raw["_request_fields"] = set(request.keys())
    model_present = "model" in raw
    model_id_present = "model_id" in raw
    if not model_present and not model_id_present:
        raise StudioProviderRecoveryError("provider_model_required")
    if model_present and (not isinstance(raw["model"], str) or not raw["model"]):
        raise StudioProviderRecoveryError("provider_model_invalid")
    if model_id_present and (not isinstance(raw["model_id"], str) or not raw["model_id"]):
        raise StudioProviderRecoveryError("provider_model_invalid")
    if model_present and model_id_present and raw["model"] != raw["model_id"]:
        raise StudioProviderRecoveryError("provider_model_alias_conflict")
    model_id = raw["model"] if model_present else raw["model_id"]
    if not uses_recoverable_wan_model(model_id):
        raise StudioProviderRecoveryError("provider_model_unsupported")
    if "asset_id" in raw:
        raise StudioProviderRecoveryError("provider_asset_owner_unsupported")
    if not raw.get("frame_id"):
        raise StudioProviderRecoveryError("provider_frame_owner_required")
    if "generation_mode" in raw:
        if raw["generation_mode"] != "i2v":
            raise StudioProviderRecoveryError("provider_mode_unsupported")
        mode_source = "request"
    else:
        raw["generation_mode"] = "i2v"
        mode_source = "entry_default"
    if "reference_video_urls" in raw or "reference_image_urls" in raw:
        raise StudioProviderRecoveryError("provider_references_unsupported")
    if "audio_url" in raw:
        raise StudioProviderRecoveryError("provider_audio_url_unsupported")
    if "watermark" in raw and model_id != "wan2.7-i2v":
        raise StudioProviderRecoveryError("provider_watermark_unsupported_in_admission")
    image_url = raw.get("image_url")
    if not isinstance(image_url, str) or not image_url:
        raise StudioProviderRecoveryError("provider_first_frame_required")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        raise StudioProviderRecoveryError("provider_prompt_required")
    return raw, model_id, mode_source


def _raw_intent_params(request: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for source_key, param_key in (
        ("duration", "duration"),
        ("resolution", "resolution"),
        ("seed", "seed"),
        ("negative_prompt", "negative_prompt"),
        ("prompt_extend", "prompt_extend"),
        ("generate_audio", "audio"),
        ("shot_type", "shot_type"),
        ("watermark", "watermark"),
    ):
        if source_key in request:
            params[param_key] = request[source_key]
    return params


def status_after_observation(observation: ProviderObservation) -> tuple[str, str | None, str]:
    if observation.status is ProviderTaskStatus.FAILED:
        return "failed", "provider_remote_failed", "observed"
    if observation.status is ProviderTaskStatus.CANCELED:
        return "failed", "provider_remote_canceled", "observed"
    if observation.status is ProviderTaskStatus.UNKNOWN:
        return "needs_confirmation", "provider_query_unknown", "observed"
    return "processing", None, "observed"
