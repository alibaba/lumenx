"""Typed commands and pure operations for approved Agent canvas calls."""

from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...utils.json_storage import StorageConflictError
from .agent_canvas_authorization import TOOL_CANVAS_CREATE_VIDEO_NODE, TOOL_CANVAS_UPDATE_NODE_PROMPT, require_approved_canvas_call
from .models import AtelierAgentToolStatus, AtelierProject
from .nodes import CreateNodeCommand, NodeTextPatch, create_node, update_text


class RecordAgentCanvasApprovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    expected_revision: int = Field(ge=0)
    turn_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "denied", "revoked"]


class ApplyApprovedAgentCanvasCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    expected_revision: int = Field(ge=0)
    turn_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def apply_approved_canvas_call(
    project: AtelierProject,
    command: ApplyApprovedAgentCanvasCommand,
    *,
    now: float,
    server_storage_identity: str | None,
    node_id_factory: Callable[[], str],
    finalizer: Literal["standalone", "loop"] = "standalone",
) -> dict:
    binding = require_approved_canvas_call(
        project,
        turn_id=command.turn_id,
        call_id=command.call_id,
        approval_id=command.approval_id,
        authorization_hash=command.authorization_hash,
        server_storage_identity=server_storage_identity,
    )
    turn = next(turn for turn in project.agent_turns if turn.id == command.turn_id)
    call = next(call for call in turn.tool_calls if call.call_id == command.call_id)
    if call.status == AtelierAgentToolStatus.COMPLETED and call.result_snapshot is not None:
        return call.result_snapshot
    if call.status in {AtelierAgentToolStatus.DENIED, AtelierAgentToolStatus.FAILED}:
        raise StorageConflictError("atelier_agent_canvas_call_not_active")
    if binding.canvas_authorization_ref.tool_name == TOOL_CANVAS_CREATE_VIDEO_NODE:
        args = binding.normalized_arguments
        node_ids = create_node(
            project,
            CreateNodeCommand(
                expected_revision=project.revision,
                type="video",
                title=str(args.get("title", "")),
                prompt=str(args.get("prompt", "")),
                x=float(args.get("x", 0.0)),
                y=float(args.get("y", 0.0)),
                width=float(args.get("width", 320.0)),
                height=float(args.get("height", 180.0)),
            ),
            node_id_factory=node_id_factory,
            now=now,
        )
        for node in project.nodes:
            if node.id in node_ids:
                node.created_by = "agent"
        changed_fields = ["nodes"]
    elif binding.canvas_authorization_ref.tool_name == TOOL_CANVAS_UPDATE_NODE_PROMPT:
        args = binding.normalized_arguments
        patch = {"expected_revision": project.revision, "prompt": str(args["prompt"])}
        if "title" in args:
            patch["title"] = str(args["title"])
        node_ids, _ = update_text(project, str(args["node_id"]), NodeTextPatch(**patch), now=now)
        changed_fields = ["prompt"] if "title" not in args else ["title", "prompt"]
    else:  # pragma: no cover - guarded by binding validation
        raise StorageConflictError("atelier_agent_canvas_tool_mismatch")
    result = {
        "tool_name": binding.canvas_authorization_ref.tool_name,
        "node_ids": node_ids,
        "changed_fields": changed_fields,
        "committed_revision": project.revision + 1,
    }
    call.status = AtelierAgentToolStatus.COMPLETED
    call.completed_at = now
    call.result_snapshot = result
    turn.pending_tool_calls = [item for item in turn.pending_tool_calls if not (isinstance(item, dict) and item.get("call_id") == command.call_id)]
    turn.messages_history.append({"role": "tool", "tool_call_id": command.call_id, "name": call.tool_name, "content": result})
    if all(c.status in {AtelierAgentToolStatus.COMPLETED, AtelierAgentToolStatus.DENIED} for c in turn.tool_calls):
        if finalizer == "loop":
            if turn.loop_start_context is None:
                raise StorageConflictError("atelier_agent_loop_context_missing")
            turn.status = "pending"
            turn.completed_at = None
        else:
            turn.status = "completed"
            turn.completed_at = now
    project.updated_at = now
    return result


def record_canvas_approval(project: AtelierProject, command: RecordAgentCanvasApprovalCommand, *, decided_at: float) -> None:
    binding = require_approved_canvas_call_for_record(
        project,
        turn_id=command.turn_id,
        call_id=command.call_id,
        approval_id=command.approval_id,
        authorization_hash=command.authorization_hash,
    )
    turn = next(turn for turn in project.agent_turns if turn.id == command.turn_id)
    call = next(call for call in turn.tool_calls if call.call_id == command.call_id)
    previous = call.canvas_approval_record
    if previous is not None and previous.decision == command.decision:
        return
    if turn.status in {"failed", "canceled", "cancelled"}:
        raise StorageConflictError("atelier_agent_canvas_turn_not_active")
    if command.decision == "approved" and (previous is not None or call.status in {AtelierAgentToolStatus.COMPLETED, AtelierAgentToolStatus.DENIED, AtelierAgentToolStatus.FAILED}):
        raise StorageConflictError("atelier_agent_canvas_approval_requires_new_preview")
    if command.decision == "revoked" and (previous is None or previous.decision != "approved"):
        raise StorageConflictError("atelier_agent_canvas_approval_not_granted")
    if command.decision == "denied" and previous is not None:
        raise StorageConflictError("atelier_agent_canvas_approval_requires_revocation")
    from .agent_canvas_authorization import build_agent_canvas_approval_record

    call.canvas_approval_record = build_agent_canvas_approval_record(binding.canvas_authorization_ref, decision=command.decision, decided_at=decided_at)
    call.approval_granted = command.decision == "approved"
    if command.decision in {"denied", "revoked"}:
        result = {
            "tool_name": binding.canvas_authorization_ref.tool_name,
            "decision": command.decision,
            "node_ids": [],
            "changed_fields": [],
            "committed_revision": project.revision + 1,
        }
        call.status = AtelierAgentToolStatus.DENIED
        call.completed_at = decided_at
        call.result_snapshot = result
        if turn.loop_start_context is not None:
            for other_call in turn.tool_calls:
                if other_call.call_id == command.call_id:
                    continue
                if other_call.status in {AtelierAgentToolStatus.PROPOSED, AtelierAgentToolStatus.APPROVAL_REQUIRED}:
                    other_call.status = AtelierAgentToolStatus.FAILED
                    other_call.completed_at = decided_at
                    other_call.result_snapshot = {
                        "tool_name": other_call.tool_name,
                        "decision": "blocked_by_" + command.decision,
                        "node_ids": [],
                        "changed_fields": [],
                        "committed_revision": project.revision + 1,
                    }
            turn.pending_tool_calls = []
        else:
            turn.pending_tool_calls = [item for item in turn.pending_tool_calls if not (isinstance(item, dict) and item.get("call_id") == command.call_id)]
        if not any(isinstance(item, dict) and item.get("tool_call_id") == command.call_id for item in turn.messages_history):
            turn.messages_history.append({"role": "tool", "tool_call_id": command.call_id, "name": call.tool_name, "content": result})
        if turn.loop_start_context is not None or all(c.status in {AtelierAgentToolStatus.COMPLETED, AtelierAgentToolStatus.DENIED} for c in turn.tool_calls):
            turn.status = "canceled" if command.decision == "revoked" else "failed"
            turn.completed_at = decided_at
    project.updated_at = decided_at


def require_approved_canvas_call_for_record(project: AtelierProject, *, turn_id: str, call_id: str, approval_id: str, authorization_hash: str):
    turn = next((turn for turn in project.agent_turns if turn.id == turn_id), None)
    call = next((call for call in turn.tool_calls if call.call_id == call_id), None) if turn is not None else None
    if turn is None or call is None or call.canvas_approval_binding is None:
        raise StorageConflictError("atelier_agent_canvas_call_missing")
    binding = call.canvas_approval_binding
    ref = binding.canvas_authorization_ref
    if ref.turn_id != turn_id or ref.call_id != call_id or ref.approval_id != approval_id or ref.authorization_hash != authorization_hash:
        raise StorageConflictError("atelier_agent_canvas_approval_identity_mismatch")
    return binding
