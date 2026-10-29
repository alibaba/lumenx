"""Internal Atelier image/TTS media generation preparation.

This module stops at local preparation. It validates product commands through the
pure preview builders, freezes owned source media when required, checks an
already-bound runtime authority, and constructs a schema v2 accepted sync-artifact
provider record. It does not create ledger entries, call providers, or bind
credentials.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

from ...models.provider_media_intents import build_accepted_sync_artifact_record, prepared_tts_intent_payload
from ...models.provider_media_runtime import (
    MediaRuntimeAuthority,
    MediaRuntimeAuthorityError,
    validate_media_runtime_authority,
)
from ...models.provider_recovery import MAX_PROVIDER_MEDIA_BYTES
from ...models.provider_tasks import PreparedMedia
from ...utils.json_storage import serialize_json
from ...utils.media_probe import probe_frozen_image
from ...utils.media_versions import freeze_media, read_verified_media
from ...utils.model_catalog import get_catalog_accessor
from .generation_commands import ImageCandidateCommand, image_contract_options, strict_qwen20_image_provider_model, strict_wan27_image_provider_model
from .image_preview import AtelierImageGenerationPreview, build_image_generation_preview
from .models import AtelierNode, AtelierProject
from .tts_commands import TTSCandidateCommand
from .tts_preview import AtelierTTSGenerationPreview, build_tts_generation_preview

PREPARATION_CONTRACT_VERSION = "atelier-media-generation-preparation-v1"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


@dataclass(frozen=True, repr=False)
class AtelierMediaGenerationPreparation:
    """Internal prepared sync-artifact generation state."""

    preview_fingerprint: str
    public_record: dict[str, Any]
    preview: AtelierImageGenerationPreview | AtelierTTSGenerationPreview
    record: Any
    output_ref: str
    frozen_media: tuple[PreparedMedia, ...]
    authority: MediaRuntimeAuthority
    target_node: AtelierNode


def build_image_media_generation_preparation(
    project: AtelierProject,
    node_id: str,
    command: ImageCandidateCommand,
    *,
    media_scope: Any,
    authority: MediaRuntimeAuthority,
    server_storage_identity: str,
    attempt_id: str,
    candidate_id: str,
    preview: AtelierImageGenerationPreview | None = None,
) -> AtelierMediaGenerationPreparation:
    """Prepare an accepted image sync-artifact record without provider work."""

    output_ref = media_generation_output_ref("image", attempt_id=attempt_id, candidate_id=candidate_id)
    preview = preview if preview is not None else build_image_generation_preview(project, node_id, command)
    if preview.target_node.id != node_id or preview.target_node.project_id != project.id:
        raise ValueError("Atelier image preview target mismatch")
    _require_storage_identity(server_storage_identity)
    expected_provider_model_id = expected_image_provider_model_id(preview.intent.model_id, mode=preview.intent.mode)
    _require_authority(
        authority,
        media_kind="image",
        provider_model_id=expected_provider_model_id,
    )
    media = tuple(_freeze_image_media(preview, media_scope))
    record = build_accepted_sync_artifact_record(
        intent=preview.intent,
        provider_model_id=authority.provider_model_id,
        media=media,
        scope=authority.scope,
        credential_ref=authority.credential_ref,
        attempt_id=attempt_id,
        output_ref=output_ref,
        media_kind="image",
        **image_contract_options(preview.intent),
    )
    return _prepared(
        project=project,
        node_id=preview.target_node.id,
        preview=preview,
        record=record,
        output_ref=output_ref,
        media=media,
        authority=authority,
        server_storage_identity=server_storage_identity,
        attempt_id=attempt_id,
        candidate_id=candidate_id,
        media_kind="image",
    )


def build_tts_media_generation_preparation(
    project: AtelierProject,
    node_id: str,
    command: TTSCandidateCommand,
    *,
    media_scope: Any,
    authority: MediaRuntimeAuthority,
    server_storage_identity: str,
    attempt_id: str,
    candidate_id: str,
) -> AtelierMediaGenerationPreparation:
    """Prepare an accepted audio sync-artifact record without provider work."""

    del media_scope  # TTS has no source media to freeze in this preparation slice.
    output_ref = media_generation_output_ref("audio", attempt_id=attempt_id, candidate_id=candidate_id)
    preview = build_tts_generation_preview(project, node_id, command)
    _require_storage_identity(server_storage_identity)
    _require_authority(
        authority,
        media_kind="audio",
        provider_model_id=preview.prepared.model,
    )
    intent_payload = prepared_tts_intent_payload(
        preview.prepared,
        request_fields=_explicit_tts_request_fields(command),
    )
    record = build_accepted_sync_artifact_record(
        intent=intent_payload,
        provider_model_id=authority.provider_model_id,
        media=(),
        scope=authority.scope,
        credential_ref=authority.credential_ref,
        attempt_id=attempt_id,
        output_ref=output_ref,
        media_kind="audio",
    )
    return _prepared(
        project=project,
        node_id=preview.target_node.id,
        preview=preview,
        record=record,
        output_ref=output_ref,
        media=(),
        authority=authority,
        server_storage_identity=server_storage_identity,
        attempt_id=attempt_id,
        candidate_id=candidate_id,
        media_kind="audio",
    )


def _prepared(
    *,
    project: AtelierProject,
    node_id: str,
    preview: AtelierImageGenerationPreview | AtelierTTSGenerationPreview,
    record: Any,
    output_ref: str,
    media: tuple[PreparedMedia, ...],
    authority: MediaRuntimeAuthority,
    server_storage_identity: str,
    attempt_id: str,
    candidate_id: str,
    media_kind: str,
) -> AtelierMediaGenerationPreparation:
    public_record = {
        "schema_version": 1,
        "preparation_contract_version": PREPARATION_CONTRACT_VERSION,
        "project_id": project.id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "candidate_id": candidate_id,
        "media_kind": media_kind,
        "output_ref": output_ref,
        "server_storage_identity": server_storage_identity,
        "preview_fingerprint": preview.preview_fingerprint,
        "preview_public_record": preview.public_record,
        "provider_execution": record.model_dump(mode="json", exclude_none=True),
        "runtime_authority": authority.public_identity(),
        "frozen_media": [_media_public(item) for item in media],
    }
    return AtelierMediaGenerationPreparation(
        preview_fingerprint=_fingerprint(public_record),
        public_record=public_record,
        preview=preview,
        record=record,
        output_ref=output_ref,
        frozen_media=media,
        authority=authority,
        target_node=preview.target_node,
    )


def _freeze_image_media(
    preview: AtelierImageGenerationPreview,
    media_scope: Any,
) -> list[PreparedMedia]:
    result: list[PreparedMedia] = []
    for item in preview.public_record["media"]:
        version = freeze_media(
            item["media_url"],
            output_root=media_scope.data_dir,
            writer_lock=media_scope.writer_lock,
            role=item["role"],
            ordinal=item["ordinal"],
            max_bytes=MAX_PROVIDER_MEDIA_BYTES,
        )
        data = read_verified_media(version, output_root=media_scope.data_dir, max_bytes=MAX_PROVIDER_MEDIA_BYTES)
        probe_frozen_image(version, data)
        result.append(PreparedMedia.from_media_version(
            version,
            output_root=media_scope.data_dir,
            max_bytes=MAX_PROVIDER_MEDIA_BYTES,
        ))
    return result


def _require_authority(
    authority: MediaRuntimeAuthority,
    *,
    media_kind: str,
    provider_model_id: str,
) -> None:
    try:
        validate_media_runtime_authority(authority)
    except MediaRuntimeAuthorityError as exc:
        raise ValueError(f"Atelier media preparation authority invalid: {exc.reason}") from exc
    if authority.media_kind != media_kind:
        raise ValueError("Atelier media preparation authority kind mismatch")
    if authority.provider_model_id != provider_model_id:
        raise ValueError("Atelier media preparation authority provider mismatch")


def expected_image_provider_model_id(model_id: str, *, mode: str | None = None) -> str:
    strict_model = strict_wan27_image_provider_model(model_id, mode) or strict_qwen20_image_provider_model(model_id, mode)
    if strict_model is not None:
        return strict_model
    entry = get_catalog_accessor().get_mode_entry(model_id)
    runtime = entry.get("runtime") if isinstance(entry, dict) else None
    if isinstance(runtime, dict):
        for backend in runtime.values():
            if isinstance(backend, dict) and isinstance(backend.get("api_model_id"), str):
                return backend["api_model_id"]
    legacy = entry.get("legacy_model_id") if isinstance(entry, dict) else None
    if isinstance(legacy, str) and legacy:
        return legacy
    raise ValueError("Atelier image model has no provider runtime identity")


def _explicit_tts_request_fields(command: TTSCandidateCommand) -> list[str]:
    fields = set(command.model_fields_set) - {"expected_revision", "client_request_id", "params"}
    fields.update(f"params.{key}" for key in command.params)
    return sorted(fields)


def media_generation_output_ref(media_kind: str, *, attempt_id: str, candidate_id: str) -> str:
    _require_identifier(attempt_id)
    _require_identifier(candidate_id)
    if media_kind == "image":
        return f"assets/atelier/{candidate_id}-{attempt_id}.png"
    if media_kind == "audio":
        return f"audio/atelier/{candidate_id}-{attempt_id}.mp3"
    raise ValueError("Atelier media preparation kind is unsupported")


def _require_identifier(value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError("Atelier media preparation identifier is invalid")


def _require_storage_identity(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("Atelier media preparation requires server storage identity")


def _media_public(item: PreparedMedia) -> dict[str, Any]:
    return asdict(item.version)


def _fingerprint(public_record: dict[str, Any]) -> str:
    return hashlib.sha256(serialize_json(public_record).encode("utf-8")).hexdigest()
