"""Storage validation for Atelier provider generation ledgers."""

from __future__ import annotations

import math
from typing import Any

from .models import (
    AtelierGenerationAttempt,
    AtelierMediaGenerationAttempt,
    AtelierProject,
    ordered_generation_media_is_canonical,
)
from ...models import provider_recovery
from ...models.media_generation_records import MediaGenerationCandidateRecord
from ...models.provider_media_intents import validate_sync_artifact_record_intent


GENERATION_CANDIDATE_CONTRACT = "atelier-provider-video-v1"
MEDIA_GENERATION_CANDIDATE_CONTRACT = "atelier-provider-media-v2"
GENERATION_ENTRYPOINT = "atelier.provider_video.v1"
GENERATION_ORDERED_ENTRYPOINT = "atelier.provider_video.v2"
GENERATION_IMAGE_ENTRYPOINT = "atelier.provider_image.v1"
GENERATION_AUDIO_ENTRYPOINT = "atelier.provider_audio.v1"
GENERATION_REQUEST_CONTRACT = "request-json-v1"
GENERATION_ORDERED_INPUT_CONTRACT = "ordered-media-v1"
GENERATION_TERMINAL_STATUSES = {"failed", "cancelled", "completed"}
GENERATION_PENDING_STATUSES = {"pending", "processing", "needs_confirmation"}
_CANDIDATE_FIELDS = {
    "execution_contract",
    "id",
    "attempt_id",
    "media_kind",
    "status",
    "video_url",
    "error_code",
    "created_at",
    "updated_at",
}


def _is_media_attempt(attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt) -> bool:
    return isinstance(attempt, AtelierMediaGenerationAttempt)


def validate_generation_ledger(project: AtelierProject, error: type[Exception]) -> None:
    """Validate typed generation attempts, receipts, and candidate projections."""

    nodes_by_id = {node.id: node for node in project.nodes}
    live_node_ids = set(nodes_by_id)
    projection_map = _generation_candidate_map(project, error, include_imported=True)
    candidate_map = {key: value for key, value in projection_map.items() if value.get("authority") != "imported"}
    attempts_by_id: dict[str, AtelierGenerationAttempt | AtelierMediaGenerationAttempt] = {}
    candidate_ids: set[str] = set()
    for attempt in project.generation_attempts:
        if attempt.project_id != project.id:
            raise error("Atelier generation attempt project_id does not match owner")
        if attempt.attempt_id in attempts_by_id:
            raise error("Duplicate Atelier generation attempt id")
        attempts_by_id[attempt.attempt_id] = attempt
        if attempt.candidate_id in candidate_ids:
            raise error("Duplicate Atelier generation candidate id")
        candidate_ids.add(attempt.candidate_id)
        if attempt.provider_execution.attempt_id != attempt.attempt_id:
            raise error("Atelier provider execution attempt does not match generation attempt")
        _validate_provider_execution_identity(attempt, error)
        _validate_ordered_input_media(attempt, error)
        _validate_attempt_execution_state(attempt, error)
        owner_node = nodes_by_id.get(attempt.node_id)
        if owner_node is not None:
            if _is_media_attempt(attempt):
                if owner_node.type != attempt.media_kind:
                    raise error("Atelier generation owner node media kind is invalid")
            elif owner_node.type != "video":
                raise error("Atelier generation owner node must be video")
        if attempt.status in GENERATION_PENDING_STATUSES and attempt.node_id not in live_node_ids:
            raise error("Atelier active generation attempt cannot be detached from its node")

    receipt_keys: set[str] = set()
    receipt_attempts: set[str] = set()
    for receipt in project.generation_request_receipts:
        if receipt.project_id != project.id:
            raise error("Atelier generation receipt project_id does not match owner")
        if receipt.entrypoint not in {GENERATION_ENTRYPOINT, GENERATION_ORDERED_ENTRYPOINT, GENERATION_IMAGE_ENTRYPOINT, GENERATION_AUDIO_ENTRYPOINT}:
            raise error("Atelier generation receipt entrypoint is invalid")
        key = receipt.client_request_id
        if key in receipt_keys:
            raise error("Duplicate Atelier generation request id")
        receipt_keys.add(key)
        if receipt.committed_revision != receipt.base_revision + 1 or receipt.committed_revision > project.revision:
            raise error("Atelier generation receipt revision is invalid")
        for member in receipt.receipt_members:
            attempt = attempts_by_id.get(member.attempt_id)
            if attempt is None:
                raise error("Atelier generation receipt points at missing generation attempt")
            if member.attempt_id in receipt_attempts:
                raise error("Duplicate Atelier generation receipt attempt")
            if (
                receipt.node_id != attempt.node_id
                or member.candidate_id != attempt.candidate_id
                or member.execution_fingerprint != attempt.provider_execution.fingerprint
            ):
                raise error("Atelier generation receipt execution fingerprint does not match attempt")
            if receipt.entrypoint == GENERATION_ORDERED_ENTRYPOINT:
                if _is_media_attempt(attempt) or attempt.input_contract_version != GENERATION_ORDERED_INPUT_CONTRACT:
                    raise error("Atelier ordered generation receipt points at legacy attempt")
            elif receipt.entrypoint == GENERATION_IMAGE_ENTRYPOINT:
                if not _is_media_attempt(attempt) or attempt.media_kind != "image":
                    raise error("Atelier image generation receipt points at non-image attempt")
            elif receipt.entrypoint == GENERATION_AUDIO_ENTRYPOINT:
                if not _is_media_attempt(attempt) or attempt.media_kind != "audio":
                    raise error("Atelier audio generation receipt points at non-audio attempt")
            elif _is_media_attempt(attempt) or attempt.input_contract_version is not None:
                raise error("Atelier legacy generation receipt points at ordered attempt")
            receipt_attempts.add(member.attempt_id)

    for attempt in project.generation_attempts:
        if attempt.attempt_id not in receipt_attempts:
            raise error("Atelier generation attempt has no request receipt")
        candidate = candidate_map.get((attempt.node_id, attempt.candidate_id))
        if candidate is None:
            if attempt.node_id not in live_node_ids and attempt.status in GENERATION_TERMINAL_STATUSES:
                continue
            raise error("Atelier generation attempt candidate not found")
        _validate_candidate_projection(candidate, attempt, error)

    for (node_id, candidate_id), candidate in candidate_map.items():
        attempt_id = candidate["attempt_id"]
        attempt = attempts_by_id.get(attempt_id)
        if attempt is None:
            raise error("Atelier generation candidate has no generation attempt")
        if attempt.node_id != node_id or attempt.candidate_id != candidate_id:
            raise error("Atelier generation candidate does not match attempt")
        if attempt_id not in receipt_attempts:
            raise error("Atelier generation candidate has no generation receipt")


def has_pending_generation(project: AtelierProject) -> bool:
    return any(attempt.status in GENERATION_PENDING_STATUSES for attempt in project.generation_attempts)


def validate_generation_transition(
    before: AtelierGenerationAttempt | AtelierMediaGenerationAttempt,
    after: AtelierGenerationAttempt | AtelierMediaGenerationAttempt,
    error: type[Exception],
) -> None:
    """Keep saved execution authority immutable as one attempt progresses."""
    old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
    for field in ("status", "error_code", "output_ref", "updated_at", "provider_execution"):
        old.pop(field)
        new.pop(field)
    if old != new:
        raise error("Atelier generation update cannot change attempt identity or revision")
    if before.status in GENERATION_TERMINAL_STATUSES and before != after:
        raise error("Atelier terminal generation attempt is immutable")
    old_record, new_record = before.provider_execution, after.provider_execution
    old_identity, new_identity = old_record.model_dump(mode="json"), new_record.model_dump(mode="json")
    for field in ("phase", "handle", "observation", "prepared_artifact", "artifact"):
        old_identity.pop(field)
        new_identity.pop(field)
    if old_identity != new_identity:
        raise error("Atelier generation update cannot change frozen execution identity")
    allowed_phases = {
        "accepted": {"accepted", "submitting", "cancelled"},
        "submitting": {"submitting", "submitted"},
        "submitted": {"submitted", "observed"},
        "observed": {"observed", "completed"},
        "completed": {"completed"},
        "cancelled": {"cancelled"},
    }
    if old_record.schema_version == 2 and old_record.execution_kind == "sync_artifact":
        allowed_phases = {
            "accepted": {"accepted", "submitting", "cancelled"},
            "submitting": {"submitting", "completed"},
            "completed": {"completed"},
            "cancelled": {"cancelled"},
        }
    if new_record.phase not in allowed_phases[old_record.phase]:
        raise error("Atelier provider execution cannot roll back or skip phases")
    for field in ("handle", "prepared_artifact", "artifact"):
        previous = getattr(old_record, field)
        if previous is not None and getattr(new_record, field) != previous:
            raise error("Atelier generation update cannot replace saved provider evidence")
    if new_record.observation is not None:
        try:
            provider_recovery.observation_from_record(new_record)
        except provider_recovery.ProviderRecoveryError as exc:
            raise error("Atelier generation observation does not match saved handle") from exc
    if old_record.observation is not None and new_record.observation is None:
        raise error("Atelier generation update cannot remove a provider observation")
    if new_record.phase == "completed" and (
        old_record.prepared_artifact is None or new_record.artifact != old_record.prepared_artifact
    ):
        raise error("Atelier generation completion requires the saved publication proof")


def assert_generation_unchanged_for_product_command(
    before: AtelierProject,
    after: AtelierProject,
    error: type[Exception],
) -> None:
    """Block non-generation commands from editing ledger identity or projections."""

    if before.generation_attempts != after.generation_attempts or before.generation_request_receipts != after.generation_request_receipts:
        raise error("Atelier product command cannot change generation ledger")
    before_nodes = {node.id for node in before.nodes}
    after_nodes = {node.id for node in after.nodes}
    before_candidates = _generation_candidate_map(before, error, include_imported=True)
    after_candidates = _generation_candidate_map(after, error, include_imported=True)
    attempts = {attempt.attempt_id: attempt for attempt in before.generation_attempts}
    for key, candidate in before_candidates.items():
        if key not in after_candidates:
            attempt = attempts.get(candidate["attempt_id"])
            if attempt is None or attempt.status not in GENERATION_TERMINAL_STATUSES or key[0] in after_nodes:
                raise error("Atelier product command cannot change generation candidate projection")
            continue
        if candidate != after_candidates[key]:
            raise error("Atelier product command cannot change generation candidate projection")
    for key, candidate in after_candidates.items():
        if key in before_candidates:
            continue
        attempt = attempts.get(candidate["attempt_id"])
        if attempt is None or attempt.status not in GENERATION_TERMINAL_STATUSES or key[0] in before_nodes:
            raise error("Atelier product command cannot change generation candidate projection")


def _generation_candidate_map(
    project: AtelierProject,
    error: type[Exception],
    *,
    include_imported: bool = False,
) -> dict[tuple[str, str], dict[str, Any]]:
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for node in project.nodes:
        data = node.data or {}
        for item in _candidate_items(data.get("candidates")) + _candidate_items(data.get("deleted_candidates")):
            contract = item.get("execution_contract")
            if contract not in {GENERATION_CANDIDATE_CONTRACT, MEDIA_GENERATION_CANDIDATE_CONTRACT}:
                continue
            candidate = _validate_candidate_shape(item, error)
            if candidate.get("execution_contract") == MEDIA_GENERATION_CANDIDATE_CONTRACT and node.type != candidate["media_kind"]:
                raise error("Atelier media generation candidate owner node media kind is invalid")
            if candidate.get("authority") == "imported" and not include_imported:
                continue
            key = (node.id, candidate["id"])
            if key in candidates:
                raise error("Duplicate Atelier generation candidate id")
            candidates[key] = candidate
    return candidates


def _candidate_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _validate_candidate_shape(candidate: dict[str, Any], error: type[Exception]) -> dict[str, Any]:
    if candidate.get("execution_contract") == MEDIA_GENERATION_CANDIDATE_CONTRACT:
        try:
            record = MediaGenerationCandidateRecord(**candidate).model_dump(mode="json")
        except ValueError as exc:
            raise error("Atelier media generation candidate is invalid") from exc
        return record
    if set(candidate) != _CANDIDATE_FIELDS:
        raise error("Atelier generation candidate fields are invalid")
    if candidate["execution_contract"] != GENERATION_CANDIDATE_CONTRACT:
        raise error("Atelier generation candidate contract is invalid")
    for field in ("id", "attempt_id"):
        if type(candidate[field]) is not str or not candidate[field]:
            raise error("Atelier generation candidate identity is invalid")
    if candidate["media_kind"] != "video":
        raise error("Atelier generation candidate media kind is invalid")
    if candidate["status"] not in GENERATION_PENDING_STATUSES | GENERATION_TERMINAL_STATUSES:
        raise error("Atelier generation candidate status is invalid")
    if candidate["error_code"] is not None and (type(candidate["error_code"]) is not str or not candidate["error_code"]):
        raise error("Atelier generation candidate error code is invalid")
    for field in ("created_at", "updated_at"):
        value = candidate[field]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise error("Atelier generation candidate timestamp is invalid")
    video_url = candidate["video_url"]
    if candidate["status"] == "completed":
        if type(video_url) is not str or not video_url:
            raise error("Atelier completed candidate requires video_url")
    elif video_url is not None:
        raise error("Atelier unfinished candidate video_url must be null")
    return dict(candidate)


def _validate_candidate_projection(
    candidate: dict[str, Any],
    attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt,
    error: type[Exception],
) -> None:
    if candidate["attempt_id"] != attempt.attempt_id:
        raise error("Atelier generation candidate does not match attempt")
    if candidate["status"] != attempt.status or candidate["error_code"] != attempt.error_code:
        raise error("Atelier generation candidate projection does not match attempt")
    if candidate.get("execution_contract") == MEDIA_GENERATION_CANDIDATE_CONTRACT:
        if not _is_media_attempt(attempt):
            raise error("Atelier media generation candidate does not match attempt")
        if candidate["media_kind"] != attempt.media_kind:
            raise error("Atelier media generation candidate media kind does not match attempt")
        if candidate["execution_fingerprint"] != attempt.provider_execution.fingerprint:
            raise error("Atelier media generation candidate fingerprint does not match attempt")
        if attempt.status == "completed" and candidate["media_url"] != attempt.output_ref:
            raise error("Atelier media generation candidate media_url does not match attempt")
        return
    if _is_media_attempt(attempt):
        raise error("Atelier video generation candidate does not match media attempt")
    if candidate["created_at"] != attempt.created_at or candidate["updated_at"] != attempt.updated_at:
        raise error("Atelier generation candidate projection timestamp does not match attempt")
    if attempt.status == "completed" and candidate["video_url"] != attempt.output_ref:
        raise error("Atelier generation candidate video_url does not match attempt")


def _validate_provider_execution_identity(attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt, error: type[Exception]) -> None:
    try:
        provider_recovery._verify_record_fingerprint(attempt.provider_execution)
        if attempt.provider_execution.schema_version == 1:
            provider_recovery._generation_intent_from_record(attempt.provider_execution.intent)
        else:
            validate_sync_artifact_record_intent(attempt.provider_execution)
    except provider_recovery.ProviderRecoveryError as exc:
        raise error(f"Atelier provider execution identity is invalid: {exc.reason}") from exc


def _validate_ordered_input_media(attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt, error: type[Exception]) -> None:
    if _is_media_attempt(attempt):
        return
    if attempt.input_contract_version is None:
        return
    if attempt.input_contract_version != GENERATION_ORDERED_INPUT_CONTRACT:
        raise error("Atelier generation input contract is invalid")
    sources = attempt.input_media
    if sources is None:
        raise error("Atelier ordered generation input media is missing")
    provider_media = attempt.provider_execution.media
    if len(sources) != len(provider_media):
        raise error("Atelier ordered generation input media does not match provider media")
    try:
        if not ordered_generation_media_is_canonical(sources) or not ordered_generation_media_is_canonical(provider_media):
            raise error("Atelier ordered generation input media order is invalid")
    except ValueError as exc:
        raise error("Atelier ordered generation input media order is invalid") from exc
    for source, media in zip(sources, provider_media, strict=True):
        if not isinstance(media, dict):
            raise error("Atelier ordered generation provider media is invalid")
        if source.role != media.get("role") or source.ordinal != media.get("ordinal"):
            raise error("Atelier ordered generation source does not match provider media")


def _validate_attempt_execution_state(attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt, error: type[Exception]) -> None:
    record = attempt.provider_execution
    if record.phase == "accepted" and any(
        value is not None
        for value in (record.handle, record.observation, record.prepared_artifact, record.artifact)
    ):
        raise error("Atelier accepted provider execution cannot contain provider results")
    if attempt.status == "completed":
        if record.phase != "completed" or record.artifact is None:
            raise error("Atelier completed generation attempt requires completed provider artifact")
        if attempt.output_ref is None:
            raise error("Atelier completed generation output_ref is required")
        if record.artifact["relative_path"] != attempt.output_ref:
            raise error("Atelier completed generation output_ref does not match provider artifact")
        return
    if record.phase == "completed" or record.artifact is not None:
        raise error("Atelier non-completed generation attempt cannot contain completed provider artifact")
    if attempt.status == "cancelled":
        if record.phase != "cancelled":
            raise error("Atelier cancelled generation attempt requires cancelled provider execution")
        if attempt.output_ref is not None:
            raise error("Atelier unfinished generation output_ref must be null")
        return
    if record.phase == "cancelled":
        raise error("Atelier non-cancelled generation attempt cannot use cancelled provider execution")
    if attempt.status == "pending" and record.phase != "accepted":
        raise error("Atelier pending generation cannot use submitted provider execution")
    if attempt.status == "failed":
        _validate_failed_execution_state(attempt, error)
        return
    if record.phase in {"submitted", "observed"} and record.handle is None:
        raise error("Atelier observed generation provider execution requires handle")
    if attempt.output_ref is not None:
        raise error("Atelier unfinished generation output_ref must be null")


def _validate_failed_execution_state(attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt, error: type[Exception]) -> None:
    record = attempt.provider_execution
    if attempt.output_ref is not None:
        raise error("Atelier unfinished generation output_ref must be null")
    if record.phase == "accepted":
        return
    if record.phase == "submitting":
        raise error("Atelier failed generation provider execution is unresolved")
    if record.phase not in {"submitted", "observed"}:
        raise error("Atelier failed generation provider execution is unresolved")
    if record.handle is None or record.observation is None:
        raise error("Atelier failed generation requires failed provider observation")
    try:
        observation = provider_recovery.observation_from_record(record)
    except provider_recovery.ProviderRecoveryError as exc:
        raise error(f"Atelier failed generation provider observation is invalid: {exc.reason}") from exc
    if observation.status.value not in {"failed", "canceled"}:
        raise error("Atelier failed generation requires failed provider observation")
