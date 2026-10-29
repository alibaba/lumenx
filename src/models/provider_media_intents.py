"""Frozen provider image and speech intent contracts.

This module is intentionally product-neutral. It validates only already-frozen
records and builds explicit schema v2 sync or schema v3 async-image identities. It
never reads the current catalog, voice registry, runtime environment, credential
stores, provider SDKs, files, or network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any, Literal

from ..audio.tts import PreparedTTS, TTSParameterNotice, TTS_RULES_VERSION
from ..utils.generation_intent import GenerationIntent, RULES_VERSION
from ..utils.media_versions import MediaVersion, MediaVersionError
from .provider_execution import ProviderExecutionRecord
from .provider_recovery import ProviderRecoveryError, image_query_handle_from_record, _fingerprint, _generation_intent_from_record, _record_fingerprint_payload, record_with_changes
from .provider_tasks import PreparedMedia, ProviderImageQueryContract, ProviderScope, ProviderSubmission, validate_image_query_contract
from .wan27_image_contract import (
    WAN27_IMAGE_EDIT_CONTRACT_VERSION, WAN27_IMAGE_CONTRACT_VERSIONS, validate_wan27_image_intent,
)
from .qwen20_image_contract import (
    QWEN20_IMAGE_EDIT_CONTRACT_VERSION, QWEN20_IMAGE_CONTRACT_VERSIONS, validate_qwen20_image_intent,
)
from .gpt_image2_edit_contract import GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, validate_gpt_image2_edit_intent

PROVIDER_PREPARED_TTS_CONTRACT_VERSION = "provider-prepared-tts-v1"
_UNSET_IMAGE_CONTRACT = object()
_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/bmp"}


def prepared_tts_intent_payload(prepared: PreparedTTS, *, request_fields: Sequence[str]) -> dict[str, Any]:
    """Return a frozen provider-neutral TTS intent payload."""
    if not isinstance(prepared, PreparedTTS):
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    return {
        "intent_contract_version": PROVIDER_PREPARED_TTS_CONTRACT_VERSION,
        "prepared_tts": prepared.to_snapshot(),
        "request_fields": _normalize_request_fields(request_fields),
    }


def build_accepted_sync_artifact_record(
    *,
    intent: GenerationIntent | Mapping[str, Any],
    provider_model_id: str,
    media_kind: Literal["image", "audio"],
    media: Sequence[PreparedMedia | Mapping[str, Any]],
    scope: ProviderScope,
    credential_ref: str,
    attempt_id: str,
    output_ref: str,
    image_contract_version: object = _UNSET_IMAGE_CONTRACT,
) -> ProviderExecutionRecord:
    """Construct a schema v2 accepted sync-artifact execution identity."""
    if media_kind == "image":
        if not isinstance(intent, GenerationIntent):
            raise ProviderRecoveryError("provider_image_intent_invalid")
        intent_payload: dict[str, Any] = intent.to_dict()
    elif media_kind == "audio":
        if not isinstance(intent, Mapping):
            raise ProviderRecoveryError("provider_tts_intent_invalid")
        intent_payload = dict(intent)
    else:
        raise ProviderRecoveryError("provider_media_kind_invalid")

    if image_contract_version is not _UNSET_IMAGE_CONTRACT:
        if media_kind != "image" or image_contract_version not in (
            *WAN27_IMAGE_CONTRACT_VERSIONS, *QWEN20_IMAGE_CONTRACT_VERSIONS, *GPT_IMAGE2_EDIT_CONTRACT_VERSIONS,
        ):
            raise ProviderRecoveryError("provider_image_contract_invalid")
        intent_payload["intent_contract_version"] = image_contract_version
        if image_contract_version in QWEN20_IMAGE_CONTRACT_VERSIONS:
            # Reject malformed new-profile inputs before fingerprint serialization.
            if not isinstance(intent, GenerationIntent):
                raise ProviderRecoveryError("provider_image_intent_invalid")
            validate_qwen20_image_intent(intent, provider_model_id, scope, contract_version=image_contract_version)
        elif image_contract_version in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS:
            if not isinstance(intent, GenerationIntent):
                raise ProviderRecoveryError("provider_image_intent_invalid")
            validate_gpt_image2_edit_intent(intent, provider_model_id, scope, contract_version=image_contract_version)

    media_payload = [_media_record(item) for item in media]
    identity = {
        "attempt_id": attempt_id,
        "provider_model_id": provider_model_id,
        "intent": intent_payload,
        "media": media_payload,
        "scope": scope.to_dict(),
        "credential_ref": credential_ref,
        "execution_kind": "sync_artifact",
        "media_kind": media_kind,
        "output_ref": output_ref,
    }
    record = ProviderExecutionRecord(
        schema_version=2,
        phase="accepted",
        **identity,
        fingerprint=_fingerprint(identity),
    )
    validate_sync_artifact_record_intent(record)
    return record


def build_accepted_async_image_record(
    *,
    intent: GenerationIntent,
    provider_model_id: str,
    media: Sequence[PreparedMedia | Mapping[str, Any]],
    scope: ProviderScope,
    credential_ref: str,
    attempt_id: str,
    output_ref: str,
    image_contract_version: object = _UNSET_IMAGE_CONTRACT,
) -> ProviderExecutionRecord:
    """Opt in to a new identity; never upgrade a persisted sync execution.

    Reuse frozen sync intent validation without provider/configuration/file I/O.
    Submission still requires the original worker claim and adapter validation.
    """
    frozen = build_accepted_sync_artifact_record(
        intent=intent, provider_model_id=provider_model_id, media_kind="image", media=media,
        scope=scope, credential_ref=credential_ref, attempt_id=attempt_id, output_ref=output_ref,
        image_contract_version=image_contract_version,
    )
    identity = _record_fingerprint_payload(frozen)
    identity.update({"schema_version": 3, "execution_kind": "async_artifact"})
    record = ProviderExecutionRecord(phase="accepted", **identity, fingerprint=_fingerprint(identity))
    image_intent_from_async_record_for_submit(record)
    return record


def image_intent_from_async_record_for_submit(record: ProviderExecutionRecord) -> GenerationIntent:
    """Validate submit *material*, not permission to POST or resume an attempt.

    Only an original in-process worker claim can advance a clean accepted
    attempt. A reloaded submitting/no-handle record remains unknown; this helper
    cannot distinguish it from that original worker and grants no retry right.
    Adapter payload/bytes validation is required before its single POST.
    """
    if (
        not isinstance(record, ProviderExecutionRecord) or record.schema_version != 3
        or record.execution_kind != "async_artifact" or record.media_kind != "image"
        or record.phase not in {"accepted", "submitting"}
        or any(value is not None for value in (record.handle, record.observation, record.prepared_artifact, record.artifact))
    ):
        raise ProviderRecoveryError("provider_async_image_submit_material_invalid")
    return validate_async_image_record_intent(record)


def validate_async_image_record_intent(record: ProviderExecutionRecord) -> GenerationIntent:
    """Validate complete frozen material, independently of execution permission.

    Historical phase/handle are retained and revalidated; this never grants POST,
    query or product projection rights and never fills incomplete history.
    """
    if (not isinstance(record, ProviderExecutionRecord) or record.schema_version != 3
            or record.execution_kind != "async_artifact" or record.media_kind != "image"):
        raise ProviderRecoveryError("provider_async_image_record_invalid")
    ProviderExecutionRecord.model_validate(record.model_dump(mode="json"))
    if _fingerprint(_record_fingerprint_payload(record)) != record.fingerprint:
        raise ProviderRecoveryError("provider_execution_fingerprint_mismatch")
    _validate_image_intent(record)
    intent = _generation_intent_from_record(record.intent)
    scope = ProviderScope.from_dict(record.scope)
    # Reuse scalar/text snapshot checks; reference metadata is checked above.
    ProviderSubmission(intent, record.provider_model_id, (), scope, record.credential_ref)
    if intent.rules_version != RULES_VERSION or not intent.prompt.strip():
        raise ProviderRecoveryError("provider_async_image_intent_incomplete")
    version = record.intent.get("intent_contract_version")
    if version in WAN27_IMAGE_CONTRACT_VERSIONS:
        contract = ProviderImageQueryContract.WAN27_IMAGE
    elif version in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS:
        contract = ProviderImageQueryContract.GPT_IMAGE2_EDIT
    elif (
        version is None and "intent_contract_version" not in record.intent
        and record.provider_model_id == "gpt-image-2" and intent.mode == "t2i"
        and intent.model_id == "gpt-image/gpt-image-2#image"
    ):
        if (
            set(intent.params) - {"size", "quality"} or "size" not in intent.params
            or any(not isinstance(value, str) or not value for value in intent.params.values())
        ):
            raise ProviderRecoveryError("provider_async_image_intent_incomplete")
        contract = ProviderImageQueryContract.GPT_IMAGE2_GENERATION
    else:
        raise ProviderRecoveryError("provider_async_image_intent_unsupported")
    validate_image_query_contract(contract, record.provider_model_id, scope)
    if record.handle is not None and image_query_handle_from_record(record).query_contract is not contract:
        raise ProviderRecoveryError("provider_async_image_contract_mismatch")
    return intent


def validate_sync_artifact_record_intent(record: ProviderExecutionRecord) -> Literal["image", "audio"]:
    """Validate a frozen schema v2 sync-artifact record's intent contract."""
    if not isinstance(record, ProviderExecutionRecord) or record.schema_version != 2 or record.execution_kind != "sync_artifact":
        raise ProviderRecoveryError("provider_sync_artifact_record_invalid")
    # Reuse the shared exact identity fingerprint; this does not inspect live state.
    if _fingerprint(_record_fingerprint_payload(record)) != record.fingerprint:
        raise ProviderRecoveryError("provider_execution_fingerprint_mismatch")
    if record.media_kind == "image":
        _validate_image_intent(record)
        return "image"
    if record.media_kind == "audio":
        _validate_tts_intent(record)
        return "audio"
    raise ProviderRecoveryError("provider_media_kind_invalid")


def image_intent_from_sync_record(record: ProviderExecutionRecord) -> GenerationIntent:
    """Reconstruct validated frozen image parameters without current defaults."""
    if validate_sync_artifact_record_intent(record) != "image":
        raise ProviderRecoveryError("provider_image_intent_invalid")
    return _generation_intent_from_record(record.intent)


def prepared_tts_from_sync_record(record: ProviderExecutionRecord) -> PreparedTTS:
    """Reconstruct the accepted speech settings without consulting a voice registry."""
    if validate_sync_artifact_record_intent(record) != "audio":
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    return _prepared_tts_from_payload(record.intent)


def sync_artifact_proof_from_record(
    record: ProviderExecutionRecord, *, relative_path: str, sha256: str, size_bytes: int,
) -> dict[str, Any]:
    """Build publication metadata from verified bytes; this performs no file I/O.

    The caller owns encoding validation and durable preparation before file
    publication. Once prepared, only the identical proof may be repaired.
    """
    validate_sync_artifact_record_intent(record)
    if record.phase not in {"submitting", "completed"}:
        raise ProviderRecoveryError("provider_sync_artifact_phase_invalid")
    proof = {
        "schema_version": 2,
        "contract_version": "provider-sync-artifact-v1",
        "attempt_id": record.attempt_id,
        "media_kind": record.media_kind,
        "relative_path": relative_path,
        "output_ref": record.output_ref,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }
    if record.prepared_artifact is not None and proof != record.prepared_artifact:
        raise ProviderRecoveryError("provider_artifact_changed_after_prepare")
    record_with_changes(record, prepared_artifact=proof)
    return proof


def _validate_image_intent(record: ProviderExecutionRecord) -> None:
    try:
        intent = _generation_intent_from_record(record.intent)
    except ProviderRecoveryError as exc:
        raise ProviderRecoveryError("provider_image_intent_invalid") from exc
    if "intent_contract_version" in record.intent:
        version = record.intent["intent_contract_version"]
        if version in WAN27_IMAGE_CONTRACT_VERSIONS:
            validate_wan27_image_intent(intent, record.provider_model_id, ProviderScope.from_dict(record.scope), contract_version=version)
        elif version in (*QWEN20_IMAGE_CONTRACT_VERSIONS, *GPT_IMAGE2_EDIT_CONTRACT_VERSIONS):
            expected = {"rules_version", "scope", "model_id", "catalog_mode", "mode", "prompt", "params", "sources",
                        "request_fields", "preset_id", "preset_version", "intent_contract_version"}
            if set(record.intent) != expected or record.intent["scope"] != "parameters-only":
                raise ProviderRecoveryError("provider_image_contract_invalid")
            scope = ProviderScope.from_dict(record.scope)
            if version in QWEN20_IMAGE_CONTRACT_VERSIONS:
                validate_qwen20_image_intent(intent, record.provider_model_id, scope, contract_version=version)
            else:
                validate_gpt_image2_edit_intent(intent, record.provider_model_id, scope, contract_version=version)
        else:
            raise ProviderRecoveryError("provider_image_contract_invalid")
    if intent.catalog_mode != "image" or intent.mode not in {"t2i", "i2i"}:
        raise ProviderRecoveryError("provider_image_intent_invalid")
    media = [_media_version(item) for item in record.media]
    if record.intent.get("intent_contract_version") in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, QWEN20_IMAGE_EDIT_CONTRACT_VERSION) and len(media) != 1:
        raise ProviderRecoveryError("provider_image_media_invalid")
    if record.intent.get("intent_contract_version") == WAN27_IMAGE_EDIT_CONTRACT_VERSION and not 1 <= len(media) <= 9:
        raise ProviderRecoveryError("provider_image_media_invalid")
    if intent.mode == "t2i":
        if media:
            raise ProviderRecoveryError("provider_image_media_invalid")
        return
    if not media:
        raise ProviderRecoveryError("provider_image_media_invalid")
    for expected, version in enumerate(media):
        if version.role != "reference_image":
            raise ProviderRecoveryError("provider_image_media_invalid")
        if version.ordinal != expected:
            raise ProviderRecoveryError("provider_image_media_ordinal_invalid")
        if version.declared_media_type not in _IMAGE_MEDIA_TYPES:
            raise ProviderRecoveryError("provider_image_media_kind_invalid")


def _validate_tts_intent(record: ProviderExecutionRecord) -> None:
    if record.media:
        raise ProviderRecoveryError("provider_tts_media_invalid")
    prepared = _prepared_tts_from_payload(record.intent)
    if record.provider_model_id != prepared.model:
        raise ProviderRecoveryError("provider_tts_provider_model_mismatch")


def _prepared_tts_from_payload(raw: Mapping[str, Any]) -> PreparedTTS:
    if not isinstance(raw, Mapping):
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    if set(raw) != {"intent_contract_version", "prepared_tts", "request_fields"}:
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    if raw["intent_contract_version"] != PROVIDER_PREPARED_TTS_CONTRACT_VERSION:
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    _normalize_request_fields(raw["request_fields"])
    snapshot = raw["prepared_tts"]
    if not isinstance(snapshot, Mapping) or set(snapshot) != {"rules_version", "family", "parameters", "adjustments", "unsupported"}:
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    if snapshot["rules_version"] != TTS_RULES_VERSION:
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    parameters = snapshot["parameters"]
    if not isinstance(parameters, Mapping):
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    try:
        return PreparedTTS(
            snapshot["family"],
            tuple(parameters.items()),
            adjustments=tuple(_notice(item) for item in snapshot["adjustments"]),
            unsupported=tuple(_notice(item) for item in snapshot["unsupported"]),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ProviderRecoveryError("provider_tts_intent_invalid") from exc


def _notice(raw: Any) -> TTSParameterNotice:
    if not isinstance(raw, Mapping) or set(raw) != {"field", "reason", "requested", "effective"}:
        raise ProviderRecoveryError("provider_tts_intent_invalid")
    try:
        return TTSParameterNotice(raw["field"], raw["reason"], raw["requested"], raw["effective"])
    except (TypeError, ValueError) as exc:
        raise ProviderRecoveryError("provider_tts_intent_invalid") from exc


def _normalize_request_fields(value: Sequence[str]) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ProviderRecoveryError("provider_tts_request_fields_invalid")
    fields = list(value)
    if any(type(field) is not str or not field or field.strip() != field for field in fields):
        raise ProviderRecoveryError("provider_tts_request_fields_invalid")
    if len(fields) != len(set(fields)):
        raise ProviderRecoveryError("provider_tts_request_fields_invalid")
    return sorted(fields)


def _media_record(item: PreparedMedia | Mapping[str, Any]) -> dict[str, Any]:
    return asdict(_media_version(item))


def _media_version(item: PreparedMedia | Mapping[str, Any]) -> MediaVersion:
    if isinstance(item, PreparedMedia):
        version = item.version
    elif isinstance(item, Mapping):
        try:
            version = MediaVersion(**dict(item))
        except (TypeError, ValueError, MediaVersionError) as exc:
            raise ProviderRecoveryError("provider_media_invalid") from exc
    else:
        raise ProviderRecoveryError("provider_media_invalid")
    if not isinstance(version, MediaVersion):
        raise ProviderRecoveryError("provider_media_invalid")
    try:
        return MediaVersion(**asdict(version))
    except (TypeError, ValueError, MediaVersionError) as exc:
        raise ProviderRecoveryError("provider_media_invalid") from exc
