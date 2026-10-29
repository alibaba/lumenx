"""Internal Atelier image/audio media admission; no provider execution is owned here."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any, ContextManager, Literal

from pydantic import BaseModel, ConfigDict

from ...models.media_generation_records import MediaGenerationCandidateRecord
from ...models.provider_media_intents import build_accepted_sync_artifact_record
from ...models.provider_media_runtime import MediaRuntimeAuthority
from ...utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION, validate_client_request_id
from .generation_commands import (
    ATELIER_IMAGE_ENTRYPOINT,
    ImageCandidateCommand,
    fingerprint_image_candidate_request,
    image_contract_options,
)
from .generation_ledger import MEDIA_GENERATION_CANDIDATE_CONTRACT
from .image_preview import AtelierImageGenerationPreview, build_image_generation_preview
from .media_generation_preparation import (
    AtelierMediaGenerationPreparation,
    build_image_media_generation_preparation,
    build_tts_media_generation_preparation,
    expected_image_provider_model_id,
    media_generation_output_ref,
)
from .models import (
    AtelierGenerationRequestReceipt,
    AtelierGenerationRequestReceiptMember,
    AtelierMediaGenerationAttempt,
    AtelierProject,
)
from .storage import AtelierStorage
from .tts_commands import ATELIER_TTS_ENTRYPOINT, TTSCandidateCommand, fingerprint_tts_candidate_request
from .tts_preview import build_tts_generation_preview


class MediaGenerationRequestNotFound(ValueError):
    """There is no committed Atelier media request with the supplied identity."""


class MediaGenerationAdmissionMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: AtelierMediaGenerationAttempt
    candidate: dict[str, Any] | None


class MediaGenerationAdmissionAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    receipt: AtelierGenerationRequestReceipt
    members: list[MediaGenerationAdmissionMember]
    replayed: bool
    new_candidate_ids: list[str]
    reason: str | None = None


AuthorityProvider = Callable[..., ContextManager[MediaRuntimeAuthority]]


class AtelierMediaGenerationAdmissionService:
    """Commit prepared image/audio media candidates to the shared generation ledger."""

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        authority_provider: AuthorityProvider,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        server_storage_identity: Callable[[], str] | str = "server-store",
    ):
        self.storage = storage
        self._authority_provider = authority_provider
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._server_storage_identity = server_storage_identity

    def read_request(self, project_id: str, client_request_id: str) -> MediaGenerationAdmissionAck:
        validate_client_request_id(client_request_id)
        return _ack(self.storage.get_project(project_id), client_request_id, replayed=True)

    def admit_image(
        self,
        project_id: str,
        node_id: str,
        command: ImageCandidateCommand,
    ) -> MediaGenerationAdmissionAck:
        batch_size = _image_batch_size(command)
        fingerprint = fingerprint_image_candidate_request(command, node_id=node_id)

        def apply(project: AtelierProject) -> None:
            preview = build_image_generation_preview(project, node_id, command)
            with self._bind_authority(media_kind="image", provider_model_id=expected_image_provider_model_id(preview.intent.model_id, mode=preview.intent.mode), preview=preview) as authority:
                self._append_image_admission(
                    project,
                    node_id,
                    command,
                    authority=authority,
                    preview=preview,
                    batch_size=batch_size,
                    request_fingerprint=fingerprint,
                )

        project, replayed = self.storage.apply_generation_admission(
            project_id,
            expected_revision=command.expected_revision,
            client_request_id=command.client_request_id,
            request_fingerprint=fingerprint,
            command=apply,
            expected_count=batch_size,
        )
        return _ack(project, command.client_request_id, replayed=replayed)

    def admit_tts(
        self,
        project_id: str,
        node_id: str,
        command: TTSCandidateCommand,
    ) -> MediaGenerationAdmissionAck:
        fingerprint = fingerprint_tts_candidate_request(command, node_id=node_id)

        def apply(project: AtelierProject) -> None:
            preview = build_tts_generation_preview(project, node_id, command)
            with self._bind_authority(media_kind="audio", provider_model_id=preview.prepared.model, preview=preview) as authority:
                self._append_tts_admission(project, node_id, command, authority=authority, request_fingerprint=fingerprint)

        project, replayed = self.storage.apply_generation_admission(
            project_id,
            expected_revision=command.expected_revision,
            client_request_id=command.client_request_id,
            request_fingerprint=fingerprint,
            command=apply,
            expected_count=1,
        )
        return _ack(project, command.client_request_id, replayed=replayed)

    def _append_image_admission(
        self,
        project: AtelierProject,
        node_id: str,
        command: ImageCandidateCommand,
        *,
        authority: MediaRuntimeAuthority,
        preview: AtelierImageGenerationPreview,
        batch_size: int,
        request_fingerprint: str,
    ) -> None:
        scope = self.storage._media_write_scope()
        ids = [(self._id_factory(), self._id_factory()) for _ in range(batch_size)]
        first_attempt_id, first_candidate_id = ids[0]
        prepared = build_image_media_generation_preparation(
            project,
            node_id,
            command,
            media_scope=scope,
            authority=authority,
            server_storage_identity=self._resolve_server_storage_identity(),
            attempt_id=first_attempt_id,
            candidate_id=first_candidate_id,
            preview=preview,
        )
        records = [prepared.record]
        for attempt_id, candidate_id in ids[1:]:
            records.append(build_accepted_sync_artifact_record(
                intent=prepared.preview.intent,
                provider_model_id=prepared.authority.provider_model_id,
                media=prepared.frozen_media,
                scope=prepared.authority.scope,
                credential_ref=prepared.authority.credential_ref,
                attempt_id=attempt_id,
                output_ref=media_generation_output_ref("image", attempt_id=attempt_id, candidate_id=candidate_id),
                media_kind="image",
                **image_contract_options(preview.intent),
            ))
        self._append_prepared(
            project,
            prepared,
            ids=ids,
            records=records,
            entrypoint=ATELIER_IMAGE_ENTRYPOINT,
            client_request_id=command.client_request_id,
            request_fingerprint=request_fingerprint,
        )

    def _append_tts_admission(
        self,
        project: AtelierProject,
        node_id: str,
        command: TTSCandidateCommand,
        *,
        authority: MediaRuntimeAuthority,
        request_fingerprint: str,
    ) -> None:
        attempt_id, candidate_id = self._id_factory(), self._id_factory()
        prepared = build_tts_media_generation_preparation(
            project,
            node_id,
            command,
            media_scope=None,
            authority=authority,
            server_storage_identity=self._resolve_server_storage_identity(),
            attempt_id=attempt_id,
            candidate_id=candidate_id,
        )
        self._append_prepared(
            project,
            prepared,
            ids=[(attempt_id, candidate_id)],
            records=[prepared.record],
            entrypoint=ATELIER_TTS_ENTRYPOINT,
            client_request_id=command.client_request_id,
            request_fingerprint=request_fingerprint,
        )

    def _append_prepared(
        self,
        project: AtelierProject,
        prepared: AtelierMediaGenerationPreparation,
        *,
        ids: list[tuple[str, str]],
        records: list[Any],
        entrypoint: Literal["atelier.provider_image.v1", "atelier.provider_audio.v1"],
        client_request_id: str,
        request_fingerprint: str,
    ) -> None:
        if len(ids) != len(records):
            raise ValueError("Atelier media generation admission identity count mismatch")
        now = self._clock()
        node = prepared.target_node
        data = dict(node.data)
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list):
            raise TypeError("Atelier media candidate history is malformed")
        appended_candidates: list[dict[str, Any]] = []
        receipt_members: list[AtelierGenerationRequestReceiptMember] = []
        for attempt_id, candidate_id in ids:
            record = records[len(appended_candidates)]
            attempt = AtelierMediaGenerationAttempt(
                execution_contract=MEDIA_GENERATION_CANDIDATE_CONTRACT,
                project_id=project.id,
                node_id=node.id,
                candidate_id=candidate_id,
                attempt_id=attempt_id,
                attempt_revision=0,
                media_kind=prepared.record.media_kind,
                status="pending",
                error_code=None,
                output_ref=None,
                provider_execution=record,
                created_at=now,
                updated_at=now,
            )
            candidate = MediaGenerationCandidateRecord(
                schema_version=2,
                execution_contract=MEDIA_GENERATION_CANDIDATE_CONTRACT,
                id=candidate_id,
                media_kind=prepared.record.media_kind,
                status="pending",
                media_url=None,
                error_code=None,
                authority="provider",
                attempt_id=attempt_id,
                execution_fingerprint=record.fingerprint,
            ).model_dump(mode="json")
            project.generation_attempts.append(attempt)
            appended_candidates.append(candidate)
            receipt_members.append(AtelierGenerationRequestReceiptMember(
                candidate_id=candidate_id,
                attempt_id=attempt_id,
                execution_fingerprint=record.fingerprint,
            ))
        data["candidates"] = [*candidates, *appended_candidates]
        node.data = data
        node.updated_at = now
        if len(receipt_members) == 1:
            only = receipt_members[0]
            receipt = AtelierGenerationRequestReceipt(
                project_id=project.id,
                entrypoint=entrypoint,
                request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
                client_request_id=client_request_id,
                request_fingerprint=request_fingerprint,
                node_id=node.id,
                candidate_id=only.candidate_id,
                attempt_id=only.attempt_id,
                execution_fingerprint=only.execution_fingerprint,
                base_revision=project.revision,
                committed_revision=project.revision + 1,
                created_at=now,
            )
        else:
            receipt = AtelierGenerationRequestReceipt(
                schema_version=2,
                project_id=project.id,
                entrypoint=entrypoint,
                request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
                client_request_id=client_request_id,
                request_fingerprint=request_fingerprint,
                node_id=node.id,
                members=receipt_members,
                base_revision=project.revision,
                committed_revision=project.revision + 1,
                created_at=now,
            )
        project.generation_request_receipts.append(receipt)
        project.updated_at = now

    def _bind_authority(self, *, media_kind: str, provider_model_id: str, preview: Any):
        return self._authority_provider(media_kind=media_kind, provider_model_id=provider_model_id, preview=preview)

    def _resolve_server_storage_identity(self) -> str:
        value = self._server_storage_identity() if callable(self._server_storage_identity) else self._server_storage_identity
        if not isinstance(value, str) or not value:
            raise ValueError("Atelier media generation requires a server storage identity")
        return value


def _image_batch_size(command: ImageCandidateCommand) -> int:
    value = getattr(command, "batch_size", 1)
    if type(value) is not int or not 1 <= value <= 6:
        raise ValueError("atelier_media_generation_batch_size_invalid")
    return value


def _ack(project: AtelierProject, client_request_id: str, *, replayed: bool) -> MediaGenerationAdmissionAck:
    receipt = next((r for r in project.generation_request_receipts if r.client_request_id == client_request_id), None)
    if receipt is None or receipt.entrypoint not in {ATELIER_IMAGE_ENTRYPOINT, ATELIER_TTS_ENTRYPOINT}:
        raise MediaGenerationRequestNotFound("Atelier media generation request not found")
    attempts_by_id = {
        attempt.attempt_id: attempt
        for attempt in project.generation_attempts
        if isinstance(attempt, AtelierMediaGenerationAttempt)
    }
    node = next((item for item in project.nodes if item.id == receipt.node_id), None)
    candidate_map = _candidate_map(node)
    reason = "generation_owner_missing" if node is None else None
    members = [
        MediaGenerationAdmissionMember(
            attempt=attempts_by_id[member.attempt_id],
            candidate=deepcopy(candidate_map.get(member.candidate_id)),
        )
        for member in receipt.receipt_members
    ]
    return MediaGenerationAdmissionAck(
        project=project,
        receipt=receipt,
        members=members,
        replayed=replayed,
        new_candidate_ids=[] if replayed else [member.candidate_id for member in receipt.receipt_members],
        reason=reason,
    )


def _candidate_map(node) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if node is None:
        return result
    for field in ("candidates", "deleted_candidates"):
        values = node.data.get(field, [])
        if isinstance(values, list):
            for candidate in values:
                if isinstance(candidate, dict) and isinstance(candidate.get("id"), str):
                    result[candidate["id"]] = candidate
    return result
