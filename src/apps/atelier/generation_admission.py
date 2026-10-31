"""Atomic Atelier admission; no provider submit/query/fetch is owned here."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict

from ...models.provider_recovery import build_accepted_provider_submission_and_record
from ...models.provider_tasks import ResolvedCredential
from ...utils.request_identity import (
    REQUEST_IDENTITY_CONTRACT_VERSION,
    validate_client_request_id,
)
from ...utils.storage_identity import initialize_storage_identity
from .generation_commands import (
    CreateVideoCandidateCommand,
    OrderedVideoCandidateCommand,
    fingerprint_ordered_video_candidate_request,
    fingerprint_video_candidate_request,
    generation_entrypoint_for_command,
)
from .generation_ledger import GENERATION_CANDIDATE_CONTRACT
from .generation_preview import build_generation_preview
from .models import (
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationMediaSource,
    AtelierGenerationRequestReceipt,
    AtelierGenerationRequestReceiptMember,
    AtelierProject,
)
from .product_skills import AtelierProductSkillSnapshot
from .skill_runtime import bind_skill_to_video_request
from .storage import AtelierStorage


class GenerationRequestNotFound(ValueError):
    """There is no committed Atelier request with the supplied identity."""


class GenerationAdmissionAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    receipt: AtelierGenerationRequestReceipt
    attempt: AtelierGenerationAttempt
    candidate: dict[str, Any] | None
    replayed: bool
    new_candidate_ids: list[str]
    reason: str | None = None


class GenerationAdmissionBatchMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: AtelierGenerationAttempt
    candidate: dict[str, Any] | None


class GenerationBatchAdmissionAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    receipt: AtelierGenerationRequestReceipt
    members: list[GenerationAdmissionBatchMember]
    replayed: bool
    new_candidate_ids: list[str]
    reason: str | None = None


class AtelierGenerationAdmissionService:
    """Accept explicit manual candidate requests using app-owned credentials.

    The injected credential provider binds the current app configuration to an
    opaque Core reference. It must perform only local resolution/binding. It is
    never called for receipt replay, and secret material never enters a project.
    """

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        credential_provider: Callable[..., ResolvedCredential],
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        server_storage_identity: Callable[[], str] | str | None = None,
    ):
        self.storage = storage
        self._credential_provider = credential_provider
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._server_storage_identity = (
            (lambda: _default_storage_identity(storage))
            if server_storage_identity is None
            else server_storage_identity
        )

    def read_request(self, project_id: str, client_request_id: str) -> GenerationAdmissionAck | GenerationBatchAdmissionAck:
        validate_client_request_id(client_request_id)
        return _ack(self.storage.get_project(project_id), client_request_id, replayed=True)

    def admit(
        self,
        project_id: str,
        node_id: str,
        command: CreateVideoCandidateCommand | OrderedVideoCandidateCommand,
        *,
        skill_snapshot: AtelierProductSkillSnapshot | None = None,
    ) -> GenerationAdmissionAck | GenerationBatchAdmissionAck:
        batch_size = _manual_batch_size(command)
        ordered = isinstance(command, OrderedVideoCandidateCommand)
        fingerprint = (fingerprint_ordered_video_candidate_request if ordered else fingerprint_video_candidate_request)(command, node_id=node_id)
        skill_binding = bind_skill_to_video_request(skill_snapshot, command.model_dump(mode="json")) if skill_snapshot is not None else None
        if skill_binding is not None:
            fingerprint = skill_binding.request_fingerprint
        entrypoint = generation_entrypoint_for_command(command)

        def apply(project: AtelierProject) -> None:
            # The storage transaction checks the original receipt and revision
            # before this callback, including under concurrent admission.
            scope = self.storage._media_write_scope()
            ids = [(self._id_factory(), self._id_factory()) for _ in range(batch_size)]
            first_attempt_id, _ = ids[0]
            preview = build_generation_preview(
                project,
                node_id,
                command,
                media_scope=scope,
                credential=self._credential_provider,
                server_storage_identity=self._resolve_server_storage_identity(),
                attempt_id=first_attempt_id,
            )
            now = self._clock()
            node = preview.target_node
            data = dict(node.data)
            candidates = data.get("candidates", [])
            if not isinstance(candidates, list):
                raise TypeError("Atelier candidate history is malformed")
            appended_candidates: list[dict[str, Any]] = []
            receipt_members: list[AtelierGenerationRequestReceiptMember] = []
            for index, (attempt_id, candidate_id) in enumerate(ids):
                record = preview.record if index == 0 else build_accepted_provider_submission_and_record(
                    preview.intent,
                    provider_model_id=preview.provider_model_id,
                    media=preview.submission.media,
                    scope=preview.credential.scope,
                    credential_ref=preview.credential.credential_ref,
                    attempt_id=attempt_id,
                )[1]
                source_fields = {
                    "input_contract_version": command.contract_version,
                    "input_media": [AtelierGenerationMediaSource(**item.model_dump()) for item in command.media],
                } if ordered else {
                    "first_frame_source": AtelierGenerationFirstFrameSource(**command.first_frame.model_dump()),
                }
                attempt = AtelierGenerationAttempt(
                    project_id=project.id, node_id=node.id, candidate_id=candidate_id,
                    attempt_id=attempt_id, attempt_revision=0, status="pending",
                    **source_fields,
                    provider_execution=record, skill_binding=skill_binding,
                    created_at=now, updated_at=now,
                )
                candidate = {
                    "execution_contract": GENERATION_CANDIDATE_CONTRACT,
                    "id": candidate_id, "attempt_id": attempt_id, "media_kind": "video",
                    "status": "pending", "video_url": None, "error_code": None,
                    "created_at": now, "updated_at": now,
                }
                if skill_binding is not None:
                    candidate["skill_binding"] = skill_binding.model_dump(mode="json")
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
            if batch_size == 1:
                only = receipt_members[0]
                receipt = AtelierGenerationRequestReceipt(
                    project_id=project.id, entrypoint=entrypoint,
                    request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
                    client_request_id=command.client_request_id, request_fingerprint=fingerprint,
                    node_id=node.id, candidate_id=only.candidate_id, attempt_id=only.attempt_id,
                    execution_fingerprint=only.execution_fingerprint,
                    base_revision=project.revision, committed_revision=project.revision + 1,
                    created_at=now,
                )
            else:
                receipt = AtelierGenerationRequestReceipt(
                    schema_version=2,
                    project_id=project.id, entrypoint=entrypoint,
                    request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
                    client_request_id=command.client_request_id, request_fingerprint=fingerprint,
                    node_id=node.id, members=receipt_members,
                    base_revision=project.revision, committed_revision=project.revision + 1,
                    created_at=now,
                )
            project.generation_request_receipts.append(receipt)
            project.updated_at = now

        project, replayed = self.storage.apply_generation_admission(
            project_id, expected_revision=command.expected_revision,
            client_request_id=command.client_request_id,
            request_fingerprint=fingerprint, command=apply, expected_count=batch_size,
        )
        return _ack(project, command.client_request_id, replayed=replayed)

    def _resolve_server_storage_identity(self) -> str:
        value = self._server_storage_identity() if callable(self._server_storage_identity) else self._server_storage_identity
        if not isinstance(value, str) or not value:
            raise ValueError("Atelier generation requires a server storage identity")
        return value


def _manual_batch_size(command: CreateVideoCandidateCommand | OrderedVideoCandidateCommand) -> int:
    value = getattr(command, "batch_size", 1)
    if type(value) is not int or not 1 <= value <= 6:
        raise ValueError("atelier_generation_batch_size_invalid")
    return value


def _ack(project: AtelierProject, client_request_id: str, *, replayed: bool) -> GenerationAdmissionAck | GenerationBatchAdmissionAck:
    receipt = next((r for r in project.generation_request_receipts if r.client_request_id == client_request_id), None)
    if receipt is None:
        raise GenerationRequestNotFound("Atelier generation request not found")
    members = receipt.receipt_members
    attempts_by_id = {attempt.attempt_id: attempt for attempt in project.generation_attempts}
    node = next((n for n in project.nodes if n.id == receipt.node_id), None)
    candidate_map = _candidate_map(node)
    reason = "generation_owner_missing" if node is None else None
    if len(members) == 1 and receipt.schema_version == 1:
        member = members[0]
        attempt = attempts_by_id[member.attempt_id]
        candidate = candidate_map.get(member.candidate_id)
        return GenerationAdmissionAck(
            project=project, receipt=receipt, attempt=attempt,
            candidate=deepcopy(candidate), replayed=replayed,
            new_candidate_ids=[] if replayed else [member.candidate_id],
            reason=reason,
        )
    ack_members = [
        GenerationAdmissionBatchMember(
            attempt=attempts_by_id[member.attempt_id],
            candidate=deepcopy(candidate_map.get(member.candidate_id)),
        )
        for member in members
    ]
    return GenerationBatchAdmissionAck(
        project=project,
        receipt=receipt,
        members=ack_members,
        replayed=replayed,
        new_candidate_ids=[] if replayed else [member.candidate_id for member in members],
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


def _default_storage_identity(storage: AtelierStorage) -> str:
    identity = initialize_storage_identity(storage.data_dir, writer_lock=storage._writer_lock)
    storage_id = identity.get("storage_id")
    if not isinstance(storage_id, str) or not storage_id:
        raise ValueError("Atelier generation requires a server storage identity")
    return storage_id
