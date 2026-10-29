"""Local mutations used only by the storage-owned Agent transactions."""

from copy import deepcopy

from ...models.provider_recovery import (
    MAX_PROVIDER_MEDIA_BYTES,
    build_accepted_provider_submission_and_record,
    reconstruct_submission_from_record,
)
from ...utils.json_storage import StorageConflictError, StorageError
from ...utils.media_versions import freeze_media
from ...utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION
from ...utils.storage_identity import initialize_storage_identity
from .agent_commands import AdmitApprovedAgentGenerationCommand, RecordAgentApprovalCommand
from .generation_commands import ATELIER_VIDEO_ENTRYPOINT, CreateVideoCandidateCommand
from .generation_ledger import GENERATION_CANDIDATE_CONTRACT
from .generation_preview import require_generation_source
from .models import (
    AtelierAgentApprovalRecord,
    AtelierAgentToolStatus,
    AtelierAgentTurn,
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierProject,
)


def _find_call(project, turn_id, call_id):
    turn = next((t for t in project.agent_turns if t.id == turn_id), None)
    call = next((c for c in turn.tool_calls if c.call_id == call_id), None) if turn else None
    if turn is None or call is None:
        raise StorageConflictError("atelier_agent_call_missing")
    return turn, call


def append_pending_turn(project: AtelierProject, turn: AtelierAgentTurn) -> None:
    if turn.project_id != project.id or any(t.id == turn.id for t in project.agent_turns):
        raise StorageConflictError("atelier_agent_turn_identity_conflict")
    if turn.status not in {"pending", "waiting_approval"} or turn.completed_at is not None:
        raise StorageError("Agent turn creation cannot supply completed execution state")
    for call in turn.tool_calls:
        if (
            call.approval_record is not None or call.approval_granted
            or call.result_snapshot is not None or call.completed_at is not None
            or call.status not in {AtelierAgentToolStatus.PROPOSED, AtelierAgentToolStatus.APPROVAL_REQUIRED}
        ):
            raise StorageError("Agent turn creation cannot supply existing execution authority")
    project.agent_turns.append(turn)
    project.updated_at = turn.created_at


def record_approval(project, command: RecordAgentApprovalCommand, *, decided_at: float) -> None:
    turn, call = _find_call(project, command.turn_id, command.call_id)
    binding = call.preview_binding
    ref = binding.agent_authorization_ref if binding is not None else None
    if ref is None or ref.approval_id != command.approval_id or ref.authorization_hash != command.authorization_hash:
        raise StorageConflictError("atelier_agent_approval_identity_mismatch")
    previous = call.approval_record
    if previous is not None and previous.decision == command.decision:
        return
    if command.decision == "approved" and (
        turn.status in {"canceled", "cancelled", "failed"}
        or previous is not None
        or call.status in {AtelierAgentToolStatus.COMPLETED, AtelierAgentToolStatus.DENIED, AtelierAgentToolStatus.FAILED}
    ):
        raise StorageConflictError("atelier_agent_approval_requires_new_preview")
    if command.decision == "revoked" and (previous is None or previous.decision != "approved"):
        raise StorageConflictError("atelier_agent_approval_not_granted")
    if command.decision == "denied" and previous is not None:
        raise StorageConflictError("atelier_agent_approval_requires_revocation")
    call.approval_record = AtelierAgentApprovalRecord(
        approval_id=ref.approval_id, authorization_hash=ref.authorization_hash,
        decision=command.decision, decided_at=decided_at,
    )
    call.approval_granted = command.decision == "approved"
    if command.decision == "approved":
        call.status = AtelierAgentToolStatus.PROPOSED
        turn.status = "pending"
    else:
        call.status = AtelierAgentToolStatus.DENIED
        call.completed_at = decided_at
        if turn.loop_start_context is not None:
            turn.pending_tool_calls = []
            for other_call in turn.tool_calls:
                if other_call.call_id != command.call_id and other_call.status in {AtelierAgentToolStatus.PROPOSED, AtelierAgentToolStatus.APPROVAL_REQUIRED}:
                    other_call.status = AtelierAgentToolStatus.FAILED
                    other_call.completed_at = decided_at
        turn.status = "canceled" if command.decision == "revoked" else "failed"
        turn.completed_at = decided_at
    project.updated_at = decided_at


def cancel_turn(project, turn_id: str, *, cancelled_at: float) -> None:
    turn = next((t for t in project.agent_turns if t.id == turn_id), None)
    if turn is None:
        raise StorageConflictError("atelier_agent_turn_missing")
    if turn.status in {"canceled", "cancelled"}:
        return
    turn.status = "canceled"
    turn.completed_at = cancelled_at
    project.updated_at = cancelled_at


def append_approved_generation(
    project: AtelierProject, command: AdmitApprovedAgentGenerationCommand,
    *, media_scope, now: float, attempt_id: str, candidate_id: str, finalizer: str = "standalone",
) -> None:
    from .agent_authorization import require_approved_agent_call

    identity = initialize_storage_identity(media_scope.data_dir, writer_lock=media_scope.writer_lock)
    binding = require_approved_agent_call(
        project, turn_id=command.turn_id, call_id=command.call_id,
        approval_id=command.approval_id, authorization_hash=command.authorization_hash,
        server_storage_identity=identity["storage_id"],
    )
    ref = binding.agent_authorization_ref
    turn, call = _find_call(project, command.turn_id, command.call_id)
    if finalizer == "standalone" and turn.loop_start_context is not None:
        raise StorageConflictError("atelier_agent_generation_loop_finalizer_required")
    if finalizer == "loop" and turn.loop_start_context is None:
        raise StorageConflictError("atelier_agent_loop_context_missing")
    if finalizer not in {"standalone", "loop"}:
        raise StorageConflictError("atelier_agent_generation_finalizer_invalid")
    raw = binding.preview_public_record["raw_request"]
    if raw.get("client_request_id") != command.client_request_id:
        raise StorageConflictError("atelier_agent_original_request_required")
    if any(
        a.agent_authorization_ref is not None
        and (a.agent_authorization_ref.turn_id, a.agent_authorization_ref.call_id) == (command.turn_id, command.call_id)
        for a in project.generation_attempts
    ):
        raise StorageConflictError("atelier_agent_call_already_admitted")
    request = CreateVideoCandidateCommand(**raw, expected_revision=command.expected_revision)
    node_id = binding.preview_public_record["node_id"]
    node = require_generation_source(project, node_id, request)
    expected_arguments = {"node_id": node_id, **{k: v for k, v in raw.items() if k != "client_request_id"}}
    if call.arguments != expected_arguments:
        raise StorageConflictError("atelier_agent_arguments_changed")
    # Load the saved blob first: a missing approved version must not be healed
    # by silently freezing today's bytes at the same source URL.
    submission = reconstruct_submission_from_record(binding.provider_execution, output_root=media_scope.data_dir)
    prepared = submission.media[0]
    current_version = freeze_media(
        request.first_frame.media_url, output_root=media_scope.data_dir,
        writer_lock=media_scope.writer_lock, role="first_frame", ordinal=0,
        max_bytes=MAX_PROVIDER_MEDIA_BYTES,
    )
    if (
        current_version.sha256, current_version.size_bytes, current_version.declared_media_type
    ) != (prepared.version.sha256, prepared.version.size_bytes, prepared.version.declared_media_type):
        raise StorageConflictError("atelier_agent_source_changed_requires_preview")
    _, record = build_accepted_provider_submission_and_record(
        submission.intent, provider_model_id=submission.provider_model_id,
        media_version=prepared.version, prepared_media=prepared,
        scope=submission.scope, credential_ref=submission.credential_ref, attempt_id=attempt_id,
    )
    attempt = AtelierGenerationAttempt(
        schema_version=2, project_id=project.id, node_id=node.id,
        candidate_id=candidate_id, attempt_id=attempt_id, status="pending",
        first_frame_source=AtelierGenerationFirstFrameSource(**request.first_frame.model_dump()),
        provider_execution=record, agent_authorization_ref=ref.model_copy(deep=True),
        created_at=now, updated_at=now,
    )
    candidate = {
        "execution_contract": GENERATION_CANDIDATE_CONTRACT,
        "id": candidate_id, "attempt_id": attempt_id, "media_kind": "video",
        "status": "pending", "video_url": None, "error_code": None,
        "created_at": now, "updated_at": now,
    }
    data = dict(node.data)
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        raise StorageError("Atelier candidate history is malformed")
    data["candidates"] = [*candidates, candidate]
    node.data, node.updated_at = data, now
    receipt = AtelierGenerationRequestReceipt(
        project_id=project.id, entrypoint=ATELIER_VIDEO_ENTRYPOINT,
        request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
        client_request_id=command.client_request_id, request_fingerprint=ref.request_fingerprint,
        node_id=node.id, candidate_id=candidate_id, attempt_id=attempt_id,
        execution_fingerprint=record.fingerprint,
        base_revision=project.revision, committed_revision=project.revision + 1, created_at=now,
    )
    project.generation_attempts.append(attempt)
    project.generation_request_receipts.append(receipt)
    call.status, call.completed_at = AtelierAgentToolStatus.COMPLETED, now
    call.result_snapshot = {
        "client_request_id": receipt.client_request_id, "request_fingerprint": receipt.request_fingerprint,
        "attempt_id": attempt_id, "candidate_id": candidate_id,
        "committed_revision": receipt.committed_revision,
    }
    if finalizer == "loop":
        turn.pending_tool_calls = [item for item in turn.pending_tool_calls if not (isinstance(item, dict) and item.get("call_id") == command.call_id)]
        if not any(isinstance(item, dict) and item.get("tool_call_id") == command.call_id for item in turn.messages_history):
            turn.messages_history.append({"role": "tool", "tool_call_id": command.call_id, "name": call.tool_name, "content": call.result_snapshot})
        if turn.pending_tool_calls:
            turn.status = "waiting_approval" if any(
                isinstance(item, dict) and item.get("requires_approval") for item in turn.pending_tool_calls
            ) else "pending"
            turn.completed_at = None
        else:
            turn.status, turn.completed_at = "pending", None
    elif all(c.status in {AtelierAgentToolStatus.COMPLETED, AtelierAgentToolStatus.DENIED} for c in turn.tool_calls):
        turn.status, turn.completed_at = "completed", now
    project.updated_at = now


def restore_admitted_call_for_comparison(before, after, command, attempt, *, now, finalizer: str = "standalone"):
    """Remove only the permitted Agent admission changes from the diff check."""
    old_turn = next((t for t in before["agent_turns"] if t["id"] == command.turn_id), None)
    new_turn = next((t for t in after["agent_turns"] if t["id"] == command.turn_id), None)
    old_call = next((c for c in old_turn["tool_calls"] if c["call_id"] == command.call_id), None) if old_turn else None
    new_call = next((c for c in new_turn["tool_calls"] if c["call_id"] == command.call_id), None) if new_turn else None
    if old_call is None or new_call is None:
        raise StorageError("Agent admission requires its existing turn and call")
    expected_result = {
        "client_request_id": command.client_request_id,
        "request_fingerprint": attempt.agent_authorization_ref.request_fingerprint,
        "attempt_id": attempt.attempt_id, "candidate_id": attempt.candidate_id,
        "committed_revision": before["revision"] + 1,
    }
    if new_call["status"] != "completed" or new_call["completed_at"] != now or new_call["result_snapshot"] != expected_result:
        raise StorageError("Agent admission call result does not match its committed attempt")
    for field in ("status", "completed_at", "result_snapshot"):
        new_call[field] = deepcopy(old_call[field])
    if finalizer == "loop":
        old_pending = old_turn.get("pending_tool_calls", [])
        expected_pending = [item for item in old_pending if not (isinstance(item, dict) and item.get("call_id") == command.call_id)]
        if new_turn.get("pending_tool_calls") != expected_pending:
            raise StorageError("Agent loop admission pending calls were not consumed exactly")
        new_turn["pending_tool_calls"] = deepcopy(old_pending)
        old_messages = old_turn.get("messages_history", [])
        new_messages = new_turn.get("messages_history", [])
        if len(new_messages) != len(old_messages) + 1 or new_messages[:-1] != old_messages:
            raise StorageError("Agent loop admission tool message was not appended exactly")
        message = new_messages[-1]
        if not isinstance(message, dict) or message.get("role") != "tool" or message.get("tool_call_id") != command.call_id or message.get("name") != old_call.get("tool_name") or message.get("content") != expected_result:
            raise StorageError("Agent loop admission tool message is invalid")
        new_turn["messages_history"] = deepcopy(old_messages)
        expected_status = (
            "waiting_approval"
            if any(isinstance(item, dict) and item.get("requires_approval") for item in expected_pending)
            else "pending"
        )
        if new_turn.get("status") != expected_status or new_turn.get("completed_at") is not None:
            raise StorageError("Agent loop admission turn finalizer is invalid")
        new_turn["status"] = deepcopy(old_turn["status"])
        new_turn["completed_at"] = deepcopy(old_turn["completed_at"])
    else:
        expected_complete = all(c["status"] in {"completed", "denied"} or c["call_id"] == command.call_id for c in new_turn["tool_calls"])
        if expected_complete and (new_turn["status"] != "completed" or new_turn["completed_at"] != now):
            raise StorageError("Agent admission turn completion does not match its calls")
        if expected_complete:
            for field in ("status", "completed_at"):
                new_turn[field] = deepcopy(old_turn[field])
