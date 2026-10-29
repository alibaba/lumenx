"""Atelier generation preview and provider preparation.

The builder in this module is the shared source of truth for manual admission
and future Agent approval binding. It performs local validation/preparation
only: normalize the request, verify the source attachment, freeze managed
media, bind credential metadata, and build the accepted provider execution
record. It never creates product candidates, receipts, attempts, or provider
network work. Only ``public_record`` and ``preview_fingerprint`` are secret-free
and durable; the returned ``AtelierGenerationPreview`` is an internal object
that carries credential/submission material and must not be serialized or
logged as a whole.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...models.provider_recovery import (
    MAX_PROVIDER_MEDIA_BYTES,
    MAX_PROVIDER_AUDIO_BYTES,
    MAX_PROVIDER_VIDEO_BYTES,
    build_accepted_provider_submission_and_record,
)
from ...models.provider_tasks import (
    PreparedMedia,
    ProviderSubmission,
    ResolvedCredential,
)
from ...utils.json_storage import serialize_json
from ...utils.media_versions import MediaVersion, freeze_media, read_verified_media
from ...utils.model_catalog import get_catalog_accessor
from .generation_commands import (
    CreateVideoCandidateCommand,
    OrderedVideoCandidateCommand,
    generation_entrypoint_for_command,
    normalize_ordered_video_candidate_request,
    normalize_video_candidate_request,
)
from .models import AtelierNode, AtelierProject
from .references import canonical_atelier_reference_data, is_pending_reference_to, reference_media_urls

PREVIEW_CONTRACT_VERSION = "atelier-generation-preview-v1"
PREVIEW_ATTEMPT_ID = "preview-attempt"


@dataclass(frozen=True, repr=False)
class AtelierGenerationPreview:
    """Internal prepared preview; only public_record and record are durable/loggable."""

    preview_fingerprint: str
    public_record: dict[str, Any]
    intent: Any
    media_version: MediaVersion | None
    prepared_media: PreparedMedia | None
    credential: ResolvedCredential
    provider_model_id: str
    submission: ProviderSubmission
    record: Any
    target_node: AtelierNode


def build_generation_preview(
    project: AtelierProject,
    node_id: str,
    command: CreateVideoCandidateCommand | OrderedVideoCandidateCommand,
    *,
    media_scope: Any,
    credential: ResolvedCredential | Callable[..., ResolvedCredential],
    server_storage_identity: str,
    attempt_id: str = PREVIEW_ATTEMPT_ID,
) -> AtelierGenerationPreview:
    """Build the exact local provider preparation used by admission.

    ``server_storage_identity`` is supplied by the server/app storage layer. It
    is not derived from browser API base URLs or client journal owners.
    """

    if not isinstance(server_storage_identity, str) or not server_storage_identity:
        raise ValueError("Atelier generation preview requires a server storage identity")
    ordered = isinstance(command, OrderedVideoCandidateCommand)
    intent = normalize_ordered_video_candidate_request(command) if ordered else normalize_video_candidate_request(command)
    target = require_generation_source(project, node_id, command)
    selections = command.media if ordered else (command.first_frame,)
    media: list[PreparedMedia] = []
    for selection in selections:
        role = selection.role if ordered else "first_frame"
        ordinal = selection.ordinal if ordered else 0
        max_bytes = {
            "reference_video": MAX_PROVIDER_VIDEO_BYTES,
            "reference_audio": MAX_PROVIDER_AUDIO_BYTES,
        }.get(role, MAX_PROVIDER_MEDIA_BYTES)
        version = freeze_media(
            selection.media_url,
            output_root=media_scope.data_dir,
            writer_lock=media_scope.writer_lock,
            role=role,
            ordinal=ordinal,
            max_bytes=max_bytes,
        )
        if role in {"reference_video", "reference_audio"}:
            data = read_verified_media(version, output_root=media_scope.data_dir, max_bytes=max_bytes)
            media.append(PreparedMedia.from_pending_av_bytes(version, data))
        else:
            media.append(PreparedMedia.from_media_version(
                version, output_root=media_scope.data_dir, max_bytes=max_bytes,
            ))
    provider_model_id, gateway = _provider_model_metadata(intent.model_id)
    resolved_credential = _resolve_credential(
        credential,
        provider_model_id=provider_model_id,
        gateway=gateway,
    )
    submission, record = build_accepted_provider_submission_and_record(
        intent,
        provider_model_id=provider_model_id,
        media=tuple(media),
        scope=resolved_credential.scope,
        credential_ref=resolved_credential.credential_ref,
        attempt_id=attempt_id,
    )
    public_record = _public_record(
        project=project,
        node_id=target.id,
        command=command,
        intent=intent.to_dict(),
        media=tuple(media),
        credential=resolved_credential,
        provider_model_id=provider_model_id,
        server_storage_identity=server_storage_identity,
    )
    return AtelierGenerationPreview(
        preview_fingerprint=_fingerprint(public_record),
        public_record=public_record,
        intent=intent,
        media_version=media[0].version if media else None,
        prepared_media=media[0] if media else None,
        credential=resolved_credential,
        provider_model_id=provider_model_id,
        submission=submission,
        record=record,
        target_node=target,
    )


def _resolve_credential(
    credential: ResolvedCredential | Callable[..., ResolvedCredential],
    *,
    provider_model_id: str,
    gateway: str,
) -> ResolvedCredential:
    resolved = credential(provider_model_id=provider_model_id, gateway=gateway) if callable(credential) else credential
    if not isinstance(resolved, ResolvedCredential):
        raise TypeError("Atelier generation preview requires an application credential binding")
    return resolved


def require_generation_source(
    project: AtelierProject,
    node_id: str,
    command: CreateVideoCandidateCommand | OrderedVideoCandidateCommand,
) -> AtelierNode:
    target = next((node for node in project.nodes if node.id == node_id), None)
    if target is None or target.type != "video":
        raise ValueError("Atelier video generation requires a video node")
    ordered = isinstance(command, OrderedVideoCandidateCommand)
    selections = command.media if ordered else (command.first_frame,)
    references = canonical_atelier_reference_data(target, project.nodes)
    for selection in selections:
        source = next((node for node in project.nodes if node.id == selection.node_id), None)
        expected_type = {
            "reference_video": "video", "reference_audio": "audio",
        }.get(selection.role, "image") if ordered else "image"
        if source is None or source.type != expected_type:
            raise ValueError("Atelier generation source has the wrong media kind")
        if source.id not in references["reference_node_ids"]:
            raise ValueError("Atelier generation source must be attached to the target node")
        if selection.media_url not in reference_media_urls(source):
            if is_pending_reference_to(target.data, source.id):
                raise ValueError("atelier_reference_pending_media")
            raise ValueError("Atelier generation media does not belong to the reference node")
    return target


def _provider_model_metadata(model_id: str) -> tuple[str, str]:
    catalog = get_catalog_accessor()
    model = catalog.get_mode_entry(model_id)
    provider_model_id = model.get("legacy_model_id") if model else None
    gateway = catalog.get_gateway(model_id, "dashscope")
    if not isinstance(provider_model_id, str) or not provider_model_id or not isinstance(gateway, str) or not gateway:
        raise ValueError("Atelier generation model has no provider runtime binding")
    return provider_model_id, gateway


def _public_record(
    *,
    project: AtelierProject,
    node_id: str,
    command: CreateVideoCandidateCommand | OrderedVideoCandidateCommand,
    intent: dict[str, Any],
    media: tuple[PreparedMedia, ...],
    credential: ResolvedCredential,
    provider_model_id: str,
    server_storage_identity: str,
) -> dict[str, Any]:
    raw_request = command.model_dump(
        mode="json",
        exclude_unset=True,
        exclude={"expected_revision"},
    )
    ordered = isinstance(command, OrderedVideoCandidateCommand)
    return {
        "schema_version": 1,
        "preview_contract_version": PREVIEW_CONTRACT_VERSION,
        "entrypoint": generation_entrypoint_for_command(command),
        "project_id": project.id,
        "node_id": node_id,
        "server_storage_identity": server_storage_identity,
        "raw_request": raw_request,
        "intent": intent,
        **({"input_contract_version": command.contract_version,
            "input_media": [selection.model_dump(mode="json") for selection in command.media]}
           if ordered else {"first_frame": command.first_frame.model_dump(mode="json")}),
        "media": [
            {
                "sha256": item.version.sha256,
                "size_bytes": item.version.size_bytes,
                "role": item.version.role,
                "ordinal": item.version.ordinal,
                "declared_media_type": item.version.declared_media_type,
            }
            for item in media
        ],
        "provider_model_id": provider_model_id,
        "scope": credential.scope.to_dict(),
        "credential_ref": credential.credential_ref,
    }


def _fingerprint(public_record: dict[str, Any]) -> str:
    return hashlib.sha256(serialize_json(public_record).encode("utf-8")).hexdigest()
