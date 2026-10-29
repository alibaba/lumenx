"""Typed Agent generation authorization for Atelier paid provider submits."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from ...utils.json_storage import StorageConflictError, StorageError, serialize_json
from ...utils.request_identity import fingerprint_request
from .agent_tools import TOOL_SCHEMA_VERSION
from .generation_commands import ATELIER_VIDEO_ENTRYPOINT
from .models import (
    AtelierAgentApprovalRecord,
    AtelierAgentGenerationBinding,
    AtelierAgentPolicy,
    AtelierAgentToolCall,
    AtelierAgentTurn,
    AtelierGenerationAttempt,
    AtelierProject,
)

TOOL_GENERATION_CREATE_VIDEO_CANDIDATES = "generation.createVideoCandidates"
AGENT_GENERATION_TOOL_SCHEMA_VERSION = TOOL_SCHEMA_VERSION


def fingerprint_agent_generation_request(
    *,
    turn_id: str,
    call_id: str,
    approval_id: str,
    authorization_hash: str,
) -> str:
    """Fingerprint the tiny Agent admission command identity."""

    return fingerprint_request(
        {
            "turn_id": turn_id,
            "call_id": call_id,
            "approval_id": approval_id,
            "authorization_hash": authorization_hash,
        },
        entrypoint=ATELIER_VIDEO_ENTRYPOINT,
    )


def policy_snapshot_hash(policy: AtelierAgentPolicy | Mapping[str, Any]) -> str:
    """Hash the approval-relevant policy snapshot, excluding mutable timestamps."""

    data = _model_json(policy) if isinstance(policy, AtelierAgentPolicy) else dict(policy)
    data.pop("updated_at", None)
    return _hash64({"schema_version": 1, "policy": data})


def build_agent_generation_binding(
    preview,
    *,
    turn_id: str,
    call_id: str,
    approval_id: str,
    policy: AtelierAgentPolicy | Mapping[str, Any],
    created_at: float | None = None,
    clock: Callable[[], float] | None = None,
    tool_schema_version: str = AGENT_GENERATION_TOOL_SCHEMA_VERSION,
) -> AtelierAgentGenerationBinding:
    """Bind a prepared preview to one future approved Agent generation submit."""

    if created_at is None:
        if clock is None:
            raise ValueError("Agent generation binding requires created_at or clock")
        created_at = clock()
    public_record = dict(preview.public_record)
    project_id = _required_str(public_record, "project_id")
    server_storage_identity = _required_str(public_record, "server_storage_identity")
    preview_fingerprint = _required_str_attr(preview, "preview_fingerprint")
    provider_execution = preview.record
    policy_hash = policy_snapshot_hash(policy)
    authorization_hash = _authorization_hash(
        public_record=public_record,
        provider_execution=provider_execution.model_dump(mode="json"),
        project_id=project_id,
        turn_id=turn_id,
        call_id=call_id,
        approval_id=approval_id,
        tool_schema_version=tool_schema_version,
        preview_fingerprint=preview_fingerprint,
        server_storage_identity=server_storage_identity,
        policy_snapshot_hash=policy_hash,
    )
    request_fingerprint = fingerprint_agent_generation_request(
        turn_id=turn_id,
        call_id=call_id,
        approval_id=approval_id,
        authorization_hash=authorization_hash,
    )
    return AtelierAgentGenerationBinding(
        preview_public_record=public_record,
        provider_execution=provider_execution,
        agent_authorization_ref={
            "schema_version": 1,
            "project_id": project_id,
            "turn_id": turn_id,
            "call_id": call_id,
            "approval_id": approval_id,
            "tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
            "tool_schema_version": tool_schema_version,
            "authorization_hash": authorization_hash,
            "preview_fingerprint": preview_fingerprint,
            "request_fingerprint": request_fingerprint,
            "server_storage_identity": server_storage_identity,
            "policy_snapshot_hash": policy_hash,
        },
        created_at=created_at,
    )


def build_agent_generation_approval_record(
    ref,
    *,
    decision: str,
    decided_at: float | None = None,
    clock: Callable[[], float] | None = None,
) -> AtelierAgentApprovalRecord:
    """Create the strict durable approval decision for a binding ref."""

    if decided_at is None:
        if clock is None:
            raise ValueError("Agent approval record requires decided_at or clock")
        decided_at = clock()
    return AtelierAgentApprovalRecord(
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        decision=decision,
        decided_at=decided_at,
    )


def validate_agent_generation_binding(binding: AtelierAgentGenerationBinding, *, error: type[Exception] = StorageError) -> AtelierAgentGenerationBinding:
    """Validate static self-consistency of one persisted Agent generation binding."""

    try:
        ref = binding.agent_authorization_ref
        public_record = binding.preview_public_record
        project_id = _required_str(public_record, "project_id")
        server_storage_identity = _required_str(public_record, "server_storage_identity")
        expected = _authorization_hash(
            public_record=public_record,
            provider_execution=binding.provider_execution.model_dump(mode="json"),
            project_id=ref.project_id,
            turn_id=ref.turn_id,
            call_id=ref.call_id,
            approval_id=ref.approval_id,
            tool_schema_version=ref.tool_schema_version,
            preview_fingerprint=ref.preview_fingerprint,
            server_storage_identity=ref.server_storage_identity,
            policy_snapshot_hash=ref.policy_snapshot_hash,
        )
        if ref.project_id != project_id:
            raise ValueError("Agent authorization project mismatch")
        if ref.server_storage_identity != server_storage_identity:
            raise ValueError("Agent authorization storage mismatch")
        if ref.tool_name != TOOL_GENERATION_CREATE_VIDEO_CANDIDATES:
            raise ValueError("Agent authorization tool mismatch")
        if ref.preview_fingerprint != _hash64(public_record):
            raise ValueError("Agent authorization preview fingerprint mismatch")
        _require_unexecuted_preview_record(binding.provider_execution)
        _require_public_record_matches_provider(public_record, binding.provider_execution)
        if ref.authorization_hash != expected:
            raise ValueError("Agent authorization hash mismatch")
        expected_request = fingerprint_agent_generation_request(
            turn_id=ref.turn_id,
            call_id=ref.call_id,
            approval_id=ref.approval_id,
            authorization_hash=ref.authorization_hash,
        )
        if ref.request_fingerprint != expected_request:
            raise ValueError("Agent authorization request mismatch")
    except Exception as exc:  # noqa: BLE001 - normalized durable storage error boundary
        if isinstance(exc, error):
            raise
        raise error("Invalid Atelier agent generation binding") from exc
    return binding


def validate_agent_authorization_ledger(project: AtelierProject, error: type[Exception] = StorageError) -> None:
    """Validate static Agent authorization links without applying live policy."""

    bindings: dict[tuple[str, str, str, str], tuple[AtelierAgentGenerationBinding, AtelierAgentToolCall]] = {}
    attempts_by_call: dict[tuple[str, str, str, str], int] = {}
    try:
        for turn in project.agent_turns:
            call_ids: set[str] = set()
            for call in turn.tool_calls:
                if call.call_id in call_ids:
                    raise ValueError("Duplicate Agent call id")
                call_ids.add(call.call_id)
                if call.preview_binding is not None:
                    if call.tool_name != TOOL_GENERATION_CREATE_VIDEO_CANDIDATES:
                        raise ValueError("Agent authorization tool mismatch")
                    binding = validate_agent_generation_binding(call.preview_binding, error=error)
                    ref = binding.agent_authorization_ref
                    if ref.project_id != project.id or ref.turn_id != turn.id or ref.call_id != call.call_id:
                        raise ValueError("Agent authorization ledger link mismatch")
                    if call.approval_record is not None:
                        approval = call.approval_record
                        if approval.approval_id != ref.approval_id or approval.authorization_hash != ref.authorization_hash:
                            raise ValueError("Agent approval link mismatch")
                    bindings[(ref.turn_id, ref.call_id, ref.approval_id, ref.authorization_hash)] = (binding, call)
                elif call.approval_record is not None:
                    raise ValueError("Agent approval has no binding")
        for attempt in project.generation_attempts:
            if attempt.schema_version != 2:
                continue
            ref = attempt.agent_authorization_ref
            if ref is None:
                raise ValueError("Agent attempt lacks authorization ref")
            key = (ref.turn_id, ref.call_id, ref.approval_id, ref.authorization_hash)
            pair = bindings.get(key)
            if pair is None:
                raise ValueError("Agent attempt authorization ref is not backed by a tool call")
            binding, call = pair
            if binding.agent_authorization_ref != ref:
                raise ValueError("Agent attempt authorization ref mismatch")
            if _provider_record_identity(attempt.provider_execution) != _provider_record_identity(binding.provider_execution):
                raise ValueError("Agent attempt execution identity mismatch")
            if call.approval_record is None:
                raise ValueError("Agent attempt binding lacks approval record")
            receipt = next((r for r in project.generation_request_receipts if any(member.attempt_id == attempt.attempt_id for member in r.receipt_members)), None)
            if receipt is None or receipt.request_fingerprint != ref.request_fingerprint:
                raise ValueError("Agent attempt receipt mismatch")
            attempts_by_call[key] = attempts_by_call.get(key, 0) + 1
            if attempts_by_call[key] > 1:
                raise ValueError("Agent generation call has multiple attempts")
    except Exception as exc:  # noqa: BLE001 - normalized durable storage error boundary
        if isinstance(exc, error):
            raise
        raise error("Invalid Atelier agent authorization ledger") from exc


def require_approved_agent_call(
    project: AtelierProject,
    *,
    turn_id: str,
    call_id: str,
    approval_id: str,
    authorization_hash: str,
    server_storage_identity: str | None,
) -> AtelierAgentGenerationBinding:
    """Return the approved live binding or fail closed before paid submission."""

    if not isinstance(server_storage_identity, str) or not server_storage_identity:
        raise StorageConflictError("atelier_agent_generation_storage_identity_required")
    if project.agent_policy.enable_reviewer:
        raise StorageConflictError("atelier_agent_generation_reviewer_unverified")
    if project.agent_policy.allowed_tools and TOOL_GENERATION_CREATE_VIDEO_CANDIDATES not in project.agent_policy.allowed_tools:
        raise StorageConflictError("atelier_agent_generation_tool_not_allowed")
    turn = _find_turn(project.agent_turns, turn_id)
    if turn.status in {"failed", "canceled", "cancelled"}:
        raise StorageConflictError("atelier_agent_generation_turn_not_active")
    call = _find_call(turn.tool_calls, call_id)
    if call.tool_name != TOOL_GENERATION_CREATE_VIDEO_CANDIDATES:
        raise StorageConflictError("atelier_agent_generation_tool_mismatch")
    call_status = call.status.value if hasattr(call.status, "value") else call.status
    if call_status in {"denied", "failed"}:
        raise StorageConflictError("atelier_agent_generation_call_not_active")
    if not call.approval_granted:
        raise StorageConflictError("atelier_agent_generation_approval_not_granted")
    if call.preview_binding is None:
        raise StorageConflictError("atelier_agent_generation_binding_missing")
    binding = validate_agent_generation_binding(call.preview_binding, error=StorageConflictError)
    ref = binding.agent_authorization_ref
    if (
        ref.turn_id != turn_id
        or ref.call_id != call_id
        or ref.approval_id != approval_id
        or ref.authorization_hash != authorization_hash
        or ref.server_storage_identity != server_storage_identity
    ):
        raise StorageConflictError("atelier_agent_generation_authorization_mismatch")
    if ref.policy_snapshot_hash != policy_snapshot_hash(project.agent_policy):
        raise StorageConflictError("atelier_agent_generation_policy_changed")
    approval = call.approval_record
    if approval is None:
        raise StorageConflictError("atelier_agent_generation_approval_missing")
    if approval.approval_id != ref.approval_id or approval.authorization_hash != ref.authorization_hash:
        raise StorageConflictError("atelier_agent_generation_approval_mismatch")
    if approval.decision != "approved":
        raise StorageConflictError("atelier_agent_generation_not_approved")
    return binding


def require_agent_authorization(
    project: AtelierProject,
    attempt: AtelierGenerationAttempt,
    *,
    server_storage_identity: str | None,
) -> AtelierAgentGenerationBinding | None:
    """Validate an attempt's live paid submit authorization; manual v1 is allowed."""

    if attempt.schema_version == 1:
        return None
    ref = attempt.agent_authorization_ref
    if ref is None:
        raise StorageConflictError("atelier_agent_generation_authorization_missing")
    binding = require_approved_agent_call(
        project,
        turn_id=ref.turn_id,
        call_id=ref.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        server_storage_identity=server_storage_identity,
    )
    if binding.agent_authorization_ref != ref:
        raise StorageConflictError("atelier_agent_generation_authorization_mismatch")
    if _provider_record_identity(binding.provider_execution) != _provider_record_identity(attempt.provider_execution):
        raise StorageConflictError("atelier_agent_generation_execution_mismatch")
    receipt = next((r for r in project.generation_request_receipts if any(member.attempt_id == attempt.attempt_id for member in r.receipt_members)), None)
    if receipt is None:
        raise StorageConflictError("atelier_agent_generation_receipt_missing")
    if receipt.request_fingerprint != ref.request_fingerprint:
        raise StorageConflictError("atelier_agent_generation_receipt_mismatch")
    if receipt.execution_fingerprint != attempt.provider_execution.fingerprint:
        raise StorageConflictError("atelier_agent_generation_receipt_mismatch")
    raw_request = binding.preview_public_record.get("raw_request")
    client_request_id = raw_request.get("client_request_id") if isinstance(raw_request, Mapping) else None
    if isinstance(client_request_id, str) and receipt.client_request_id != client_request_id:
        raise StorageConflictError("atelier_agent_generation_receipt_mismatch")
    return binding


def _authorization_hash(
    *,
    public_record: Mapping[str, Any],
    provider_execution: Mapping[str, Any],
    project_id: str,
    turn_id: str,
    call_id: str,
    approval_id: str,
    tool_schema_version: str,
    preview_fingerprint: str,
    server_storage_identity: str,
    policy_snapshot_hash: str,
) -> str:
    return _hash64(
        {
            "schema_version": 1,
            "project_id": project_id,
            "turn_id": turn_id,
            "call_id": call_id,
            "approval_id": approval_id,
            "tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
            "tool_schema_version": tool_schema_version,
            "preview_fingerprint": preview_fingerprint,
            "server_storage_identity": server_storage_identity,
            "policy_snapshot_hash": policy_snapshot_hash,
            "public_record": public_record,
            "provider_execution": provider_execution,
        }
    )


def _require_unexecuted_preview_record(record) -> None:
    if (
        record.phase != "accepted"
        or record.handle is not None
        or record.observation is not None
        or record.prepared_artifact is not None
        or record.artifact is not None
    ):
        raise ValueError("Agent authorization binding must freeze an unexecuted provider preview")


def _require_public_record_matches_provider(public_record: Mapping[str, Any], record) -> None:
    if public_record.get("provider_model_id") != record.provider_model_id:
        raise ValueError("Agent authorization provider model mismatch")
    if public_record.get("intent") != record.intent:
        raise ValueError("Agent authorization intent mismatch")
    if _media_identity(public_record.get("media")) != _media_identity(record.media):
        raise ValueError("Agent authorization media mismatch")
    if public_record.get("scope") != record.scope:
        raise ValueError("Agent authorization scope mismatch")
    if public_record.get("credential_ref") != record.credential_ref:
        raise ValueError("Agent authorization credential mismatch")


def _provider_record_identity(record) -> dict[str, Any]:
    return {
        "provider_model_id": record.provider_model_id,
        "intent": record.intent,
        "media": _media_identity(record.media),
        "scope": record.scope,
        "credential_ref": record.credential_ref,
    }


def _media_identity(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Agent authorization media mismatch")
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Agent authorization media mismatch")
        result.append({
            "sha256": item.get("sha256"),
            "size_bytes": item.get("size_bytes"),
            "role": item.get("role"),
            "ordinal": item.get("ordinal"),
            "declared_media_type": item.get("declared_media_type"),
        })
    return result


def _hash64(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(serialize_json(value).encode("utf-8")).hexdigest()


def _model_json(value) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _required_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Agent authorization requires {key}")
    return value


def _required_str_attr(obj: Any, name: str) -> str:
    value = getattr(obj, name, None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Agent authorization requires {name}")
    return value


def _find_turn(turns: list[AtelierAgentTurn], turn_id: str) -> AtelierAgentTurn:
    for turn in turns:
        if turn.id == turn_id:
            return turn
    raise StorageConflictError("atelier_agent_generation_turn_missing")


def _find_call(calls: list[AtelierAgentToolCall], call_id: str) -> AtelierAgentToolCall:
    for call in calls:
        if call.call_id == call_id:
            return call
    raise StorageConflictError("atelier_agent_generation_call_missing")
