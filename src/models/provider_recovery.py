"""Neutral helpers for durable provider execution recovery records."""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import asdict
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .provider_execution import ProviderExecutionRecord
from .provider_image_policy import get_provider_image_policy, recovery_image_max_bytes
from .provider_tasks import (
    PreparedMedia,
    ProviderArtifact,
    ProviderBindingError,
    ProviderHandle,
    ProviderObservation,
    ProviderScope,
    ProviderSubmission,
    ProviderTaskStatus,
    ResolvedCredential,
    validate_image_observation_for_handle,
)
from ..utils.generation_intent import GenerationIntent
from ..utils.json_storage import serialize_json
from ..utils.media_versions import MediaVersion, read_verified_media


MAX_PROVIDER_MEDIA_BYTES = 20 * 1024 * 1024
MAX_PROVIDER_VIDEO_BYTES = 100 * 1024 * 1024
MAX_PROVIDER_AUDIO_BYTES = 15 * 1024 * 1024
_ACCEPTED_MEDIA_TRANSPORTS = {"data_uri_sha256_v1", "frozen_av_sha256_v1"}


class ProviderRecoveryError(ValueError):
    """A provider execution record cannot be safely used."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(serialize_json(value).encode("utf-8")).hexdigest()


def _validate_submission_without_network(submission: ProviderSubmission) -> None:
    # Each adapter reuses its exact scope and payload rules without network, config or secret access.
    from .seedance_core_contract import SEEDANCE_PROVIDER_MODELS, seedance_submission_payload

    if submission.provider_model_id in SEEDANCE_PROVIDER_MODELS:
        seedance_submission_payload(submission)
        return
    from .wanx import WanxModel

    WanxModel.validate_submission(submission)


def _normalize_prepared_media_arguments(
    *,
    media_version: MediaVersion | None,
    prepared_media: PreparedMedia | None,
    media: tuple[PreparedMedia, ...] | list[PreparedMedia] | None,
) -> tuple[PreparedMedia, ...]:
    old_style = media_version is not None or prepared_media is not None
    new_style = media is not None
    if old_style and new_style:
        raise ProviderRecoveryError("provider_media_arguments_ambiguous")
    if old_style:
        if not isinstance(prepared_media, PreparedMedia) or prepared_media.version != media_version:
            raise ProviderRecoveryError("provider_media_version_mismatch")
        items = (prepared_media,)
    elif new_style:
        if not isinstance(media, (tuple, list)) or any(not isinstance(item, PreparedMedia) for item in media):
            raise ProviderRecoveryError("provider_media_invalid")
        items = tuple(media)
    else:
        raise ProviderRecoveryError("provider_media_missing")
    for item in items:
        if item.transport_binding not in _ACCEPTED_MEDIA_TRANSPORTS:
            raise ProviderRecoveryError("provider_media_transport_unverified")
    return items


def build_accepted_provider_submission_and_record(
    intent: GenerationIntent,
    *,
    provider_model_id: str,
    media_version: MediaVersion | None = None,
    prepared_media: PreparedMedia | None = None,
    media: tuple[PreparedMedia, ...] | list[PreparedMedia] | None = None,
    scope: ProviderScope,
    credential_ref: str,
    attempt_id: str,
    mode_source: str = "request",
    admission_request_fields: tuple[str, ...] | None = None,
) -> tuple[ProviderSubmission, ProviderExecutionRecord]:
    """Construct one accepted identity from normalized, frozen product inputs.

    Product ownership, receipt deduplication and commit remain with the caller.
    The optional provenance fields preserve existing entrypoint metadata without
    allowing that metadata to replace execution parameters or media bindings.
    """
    media_items = _normalize_prepared_media_arguments(
        media_version=media_version, prepared_media=prepared_media, media=media,
    )
    submission = ProviderSubmission(
        intent=intent, provider_model_id=provider_model_id, media=media_items,
        scope=scope, credential_ref=credential_ref,
    )
    _validate_submission_without_network(submission)
    if mode_source not in {"request", "entry_default"}:
        raise ProviderRecoveryError("provider_mode_provenance_invalid")
    intent_dict = intent.to_dict()
    if mode_source != "request":
        intent_dict["sources"]["mode"] = mode_source
        intent_dict["request_fields"] = [field for field in intent_dict["request_fields"] if field != "mode"]
    if admission_request_fields is not None:
        if not isinstance(admission_request_fields, tuple) or any(type(field) is not str or not field for field in admission_request_fields):
            raise ProviderRecoveryError("provider_admission_fields_invalid")
        intent_dict["admission_request_fields"] = sorted(admission_request_fields)
    identity = {
        "attempt_id": attempt_id,
        "provider_model_id": provider_model_id,
        "intent": intent_dict,
        "media": [asdict(item.version) for item in media_items],
        "scope": scope.to_dict(),
        "credential_ref": credential_ref,
    }
    return submission, ProviderExecutionRecord(
        schema_version=1, phase="accepted", **identity,
        fingerprint=_fingerprint(identity),
    )


def reconstruct_submission_from_record(
    record: ProviderExecutionRecord, *, output_root: Path, max_bytes: int | None = None,
) -> ProviderSubmission:
    """Rebuild submit input only from the frozen accepted record."""
    if not isinstance(record, ProviderExecutionRecord):
        raise ProviderRecoveryError("provider_execution_record_invalid")
    if record.schema_version == 3:
        raise ProviderRecoveryError("provider_async_image_requires_explicit_submit_contract")
    _verify_record_fingerprint(record)
    intent = _generation_intent_from_record(record.intent)
    try:
        image_limit = recovery_image_max_bytes(record.provider_model_id, max_bytes)
    except ValueError as exc:
        raise ProviderRecoveryError("provider_media_limit_invalid") from exc
    media = tuple(
        _reconstruct_prepared_media(MediaVersion(**item), output_root=output_root, max_bytes=image_limit)
        for item in record.media
    )
    if any(item.transport_binding not in _ACCEPTED_MEDIA_TRANSPORTS for item in media):
        raise ProviderRecoveryError("provider_media_invalid")
    submission = ProviderSubmission(
        intent=intent,
        provider_model_id=record.provider_model_id,
        media=media,
        scope=ProviderScope.from_dict(record.scope),
        credential_ref=record.credential_ref,
    )
    try:
        _validate_submission_without_network(submission)
    except ProviderBindingError as exc:
        raise ProviderRecoveryError("provider_submission_invalid") from exc
    return submission


def _reconstruct_prepared_media(
    version: MediaVersion, *, output_root: Path, max_bytes: int,
) -> PreparedMedia:
    if version.role == "reference_video":
        data = read_verified_media(version, output_root=output_root, max_bytes=MAX_PROVIDER_VIDEO_BYTES)
        return PreparedMedia.from_pending_av_bytes(version, data)
    if version.role == "reference_audio":
        data = read_verified_media(version, output_root=output_root, max_bytes=MAX_PROVIDER_AUDIO_BYTES)
        return PreparedMedia.from_pending_av_bytes(version, data)
    return PreparedMedia.from_media_version(version, output_root=output_root, max_bytes=max_bytes)


def prepare_submission_transport(
    submission: ProviderSubmission,
    credential: ResolvedCredential,
    *,
    uploader=None,
) -> ProviderSubmission:
    """Upload pending media into provider transport without mutating records."""
    if not isinstance(submission, ProviderSubmission) or not isinstance(credential, ResolvedCredential):
        raise ProviderRecoveryError("provider_submission_invalid")
    if credential.scope != submission.scope or credential.credential_ref != submission.credential_ref:
        raise ProviderRecoveryError("provider_credential_mismatch")
    try:
        _validate_submission_without_network(submission)
    except ProviderBindingError as exc:
        raise ProviderRecoveryError("provider_submission_invalid") from exc
    from .seedance_core_contract import SEEDANCE_PROVIDER_MODELS

    # Seedance's accepted image bytes already have a verified data-URI transport.
    # Its validator rejects pending AV and unverified URLs before this return.
    if submission.provider_model_id in SEEDANCE_PROVIDER_MODELS:
        return submission
    if uploader is None:
        from .provider_upload import upload_dashscope_temporary_media

        uploader = upload_dashscope_temporary_media
    from .provider_upload import ProviderUploadError

    prepared_media: list[PreparedMedia] = []
    for item in submission.media:
        if (
            get_provider_image_policy(submission.provider_model_id) is not None
            and item.transport_binding == "data_uri_sha256_v1"
        ):
            data = _verified_data_uri_bytes(item, submission.provider_model_id)
            try:
                result = uploader(
                    scope=submission.scope,
                    provider_model_id=submission.provider_model_id,
                    credential=credential,
                    version=item.version,
                    data=data,
                )
            except ProviderUploadError as exc:
                raise ProviderRecoveryError("provider_media_upload_failed") from exc
            _validate_upload_result_binding(result, item, submission, credential)
            try:
                prepared_media.append(
                    PreparedMedia.from_verified_provider_image_url(
                        item.version, upload_result=result, data=data,
                    )
                )
            except ProviderBindingError as exc:
                raise ProviderRecoveryError("provider_media_upload_mismatch") from exc
            continue
        if item.transport_binding != "frozen_av_sha256_v1":
            prepared_media.append(item)
            continue
        if item.frozen_bytes is None or item.frozen_metadata is None:
            raise ProviderRecoveryError("provider_media_pending_invalid")
        try:
            result = uploader(
                scope=submission.scope,
                provider_model_id=submission.provider_model_id,
                credential=credential,
                version=item.version,
                data=item.frozen_bytes,
            )
        except ProviderUploadError as exc:
            raise ProviderRecoveryError("provider_media_upload_failed") from exc
        _validate_upload_result_binding(result, item, submission, credential)
        try:
            prepared_media.append(
                PreparedMedia.from_verified_provider_url(
                    item.version, upload_result=result, metadata=item.frozen_metadata,
                )
            )
        except ProviderBindingError as exc:
            raise ProviderRecoveryError("provider_media_upload_mismatch") from exc
    prepared = ProviderSubmission(
        intent=submission.intent,
        provider_model_id=submission.provider_model_id,
        media=tuple(prepared_media),
        scope=submission.scope,
        credential_ref=submission.credential_ref,
    )
    try:
        _validate_submission_without_network(prepared)
    except ProviderBindingError as exc:
        raise ProviderRecoveryError("provider_submission_invalid") from exc
    return prepared


def _validate_upload_result_binding(
    result: Any,
    item: PreparedMedia,
    submission: ProviderSubmission,
    credential: ResolvedCredential,
) -> None:
    if (
        getattr(result, "version", None) != item.version
        or getattr(result, "scope", None) != submission.scope
        or getattr(result, "provider_model_id", None) != submission.provider_model_id
        or getattr(result, "credential_ref", None) != credential.credential_ref
        or not isinstance(getattr(result, "url", None), str)
        or getattr(result, "url", None) != f"oss://{getattr(result, 'key', None)}"
    ):
        raise ProviderRecoveryError("provider_media_upload_mismatch")


def _verified_data_uri_bytes(item: PreparedMedia, provider_model_id: str) -> bytes:
    version = item.version
    policy = get_provider_image_policy(provider_model_id)
    if (
        policy is None or version.role != policy.role
        or version.declared_media_type not in policy.mime_suffixes
        or version.size_bytes > policy.max_bytes
    ):
        raise ProviderRecoveryError("provider_media_pending_invalid")
    prefix = f"data:{version.declared_media_type};base64,"
    if not isinstance(item.url, str) or not item.url.startswith(prefix):
        raise ProviderRecoveryError("provider_media_pending_invalid")
    encoded = item.url[len(prefix):]
    if not encoded or len(encoded) > 4 * ((policy.max_bytes + 2) // 3):
        raise ProviderRecoveryError("provider_media_pending_invalid")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ProviderRecoveryError("provider_media_pending_invalid") from exc
    if len(data) != version.size_bytes or hashlib.sha256(data).hexdigest() != version.sha256:
        raise ProviderRecoveryError("provider_media_pending_invalid")
    return data


def handle_from_record(record: ProviderExecutionRecord) -> ProviderHandle:
    if not isinstance(record, ProviderExecutionRecord) or record.handle is None:
        raise ProviderRecoveryError("provider_handle_missing")
    return ProviderHandle.from_dict(record.handle)


def image_query_handle_from_record(record: ProviderExecutionRecord) -> ProviderHandle:
    """Read an explicit known image handle without rebuilding submit material.

    The owning ledger supplies record provenance. This validates structural
    identity, not a signature or authorization to submit/project. The frozen
    generation fingerprint is deliberately not recomputed from missing history.
    """
    if not isinstance(record, ProviderExecutionRecord) or record.schema_version != 3:
        raise ProviderRecoveryError("provider_async_image_record_invalid")
    if record.phase not in {"submitted", "observed", "completed"}:
        raise ProviderRecoveryError("provider_image_handle_missing")
    validated = ProviderExecutionRecord.model_validate(record.model_dump(mode="json"))
    return handle_from_record(validated)


def record_with_changes(record: ProviderExecutionRecord, **changes) -> ProviderExecutionRecord:
    payload = record.model_dump(mode="json")
    payload.update(changes)
    return ProviderExecutionRecord(**payload)


def require_matching_handle(record: ProviderExecutionRecord, handle: ProviderHandle) -> ProviderHandle:
    if not isinstance(handle, ProviderHandle):
        raise ProviderRecoveryError("provider_handle_invalid")
    if record.schema_version == 3:
        if record.handle is not None and record.handle != handle.to_dict():
            raise ProviderRecoveryError("provider_handle_changed_after_submit")
        if record.phase not in {"submitting", "submitted", "observed", "completed"}:
            raise ProviderRecoveryError("provider_image_submit_phase_invalid")
        changes: dict[str, Any] = {"handle": handle.to_dict()}
        if record.phase == "submitting":
            changes["phase"] = "submitted"
        candidate = record_with_changes(record, **changes)
    else:
        candidate = record_with_changes(record, handle=handle.to_dict())
    if not isinstance(candidate.handle, dict):
        raise ProviderRecoveryError("provider_handle_invalid")
    return ProviderHandle.from_dict(candidate.handle)


def validate_observation_for_handle(observation: ProviderObservation, handle: ProviderHandle) -> ProviderObservation:
    if (
        not isinstance(observation, ProviderObservation)
        or observation.task_id != handle.task_id
        or observation.source_handle != handle
        or not isinstance(observation.status, ProviderTaskStatus)
    ):
        raise ProviderRecoveryError("provider_observation_invalid")
    if handle.schema_version == 2:
        try:
            validate_image_observation_for_handle(observation, handle)
        except ProviderBindingError as exc:
            raise ProviderRecoveryError("provider_observation_invalid") from exc
    elif observation.image_url is not None:
        raise ProviderRecoveryError("provider_observation_invalid")
    return observation


def observation_to_dict(observation: ProviderObservation) -> dict[str, Any]:
    if not isinstance(observation, ProviderObservation):
        raise ProviderRecoveryError("provider_observation_invalid")
    return observation.to_dict()


def _record_fingerprint_payload(record: ProviderExecutionRecord) -> dict[str, Any]:
    payload = {
        "attempt_id": record.attempt_id,
        "provider_model_id": record.provider_model_id,
        "intent": record.intent,
        "media": record.media,
        "scope": record.scope,
        "credential_ref": record.credential_ref,
    }
    if record.schema_version in {2, 3}:
        payload.update({
            "execution_kind": record.execution_kind,
            "media_kind": record.media_kind,
            "output_ref": record.output_ref,
        })
    if record.schema_version == 3:
        payload["schema_version"] = 3
    return payload


def _verify_record_fingerprint(record: ProviderExecutionRecord) -> None:
    expected = _fingerprint(_record_fingerprint_payload(record))
    if expected != record.fingerprint:
        raise ProviderRecoveryError("provider_execution_fingerprint_mismatch")


def _generation_intent_from_record(raw: Mapping[str, Any]) -> GenerationIntent:
    required = {
        "rules_version", "model_id", "catalog_mode", "mode", "prompt",
        "params", "sources", "request_fields", "preset_id", "preset_version",
    }
    if not isinstance(raw, Mapping) or required - set(raw):
        raise ProviderRecoveryError("provider_intent_missing")
    params = raw["params"]
    sources = raw["sources"]
    request_fields = raw["request_fields"]
    if not isinstance(params, Mapping) or not isinstance(sources, Mapping):
        raise ProviderRecoveryError("provider_intent_invalid")
    if not isinstance(request_fields, list | tuple):
        raise ProviderRecoveryError("provider_intent_invalid")
    return GenerationIntent(
        model_id=raw["model_id"],
        catalog_mode=raw["catalog_mode"],
        mode=raw["mode"],
        prompt=raw["prompt"],
        params=MappingProxyType(dict(params)),
        sources=MappingProxyType(dict(sources)),
        request_fields=tuple(request_fields),
        preset_id=raw["preset_id"],
        preset_version=raw["preset_version"],
        rules_version=raw["rules_version"],
    )


def observation_from_record(record: ProviderExecutionRecord) -> ProviderObservation:
    handle = handle_from_record(record)
    raw = record.observation
    if not isinstance(raw, Mapping):
        raise ProviderRecoveryError("provider_observation_missing")
    try:
        observation = ProviderObservation(
            task_id=raw["task_id"],
            status=ProviderTaskStatus(raw["status"]),
            raw_status=raw["raw_status"],
            request_id=raw.get("request_id"),
            video_url=raw.get("video_url"),
            error_code=raw.get("error_code"),
            source_handle=ProviderHandle.from_dict(raw["source_handle"]),
            image_url=raw.get("image_url"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderRecoveryError("provider_observation_invalid") from exc
    return validate_observation_for_handle(observation, handle)


def artifact_proof_from_record(record: ProviderExecutionRecord, artifact: ProviderArtifact) -> dict[str, Any]:
    if not isinstance(artifact, ProviderArtifact):
        raise ProviderRecoveryError("provider_artifact_invalid")
    handle = handle_from_record(record)
    if artifact.handle != handle:
        raise ProviderRecoveryError("provider_artifact_handle_mismatch")
    proof: dict[str, Any] = {
        "schema_version": 1,
        "attempt_id": record.attempt_id,
        "handle": handle.to_dict(),
        "relative_path": artifact.relative_path,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }
    if record.schema_version == 3:
        image_query_handle_from_record(record)
        proof.update({
            "schema_version": 3,
            "contract_version": "provider-async-image-artifact-v1",
            "media_kind": "image",
            "output_ref": record.output_ref,
        })
    if record.schema_version == 3 and record.prepared_artifact is not None and proof != record.prepared_artifact:
        raise ProviderRecoveryError("provider_artifact_changed_after_prepare")
    # Reuse the record's strict schema and cross-field validators.
    record_with_changes(record, prepared_artifact=proof)
    return proof


def artifact_from_proof(proof: Mapping[str, Any]) -> ProviderArtifact:
    if not isinstance(proof, Mapping):
        raise ProviderRecoveryError("provider_artifact_invalid")
    return ProviderArtifact(
        ProviderHandle.from_dict(proof["handle"]),
        proof["relative_path"],
        proof["sha256"],
        proof["size_bytes"],
    )


def require_matching_artifact_proof(
    record: ProviderExecutionRecord, artifact: ProviderArtifact, expected: Mapping[str, Any] | None,
) -> dict[str, Any]:
    proof = artifact_proof_from_record(record, artifact)
    if expected is not None and proof != dict(expected):
        raise ProviderRecoveryError("provider_artifact_changed_after_prepare")
    return proof
