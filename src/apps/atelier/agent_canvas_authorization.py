"""Typed Agent authorization for local Atelier canvas mutations."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from typing import Any

from ...utils.json_storage import StorageConflictError, StorageError, serialize_json
from .agent_authorization import policy_snapshot_hash
from .models import (
    AtelierAgentCanvasApprovalRecord,
    AtelierAgentCanvasBinding,
    AtelierAgentCanvasAuthorizationRef,
    AtelierAgentToolCall,
    AtelierAgentTurn,
    AtelierNode,
    AtelierProject,
)

TOOL_CANVAS_CREATE_VIDEO_NODE = "canvas.createVideoNode"
TOOL_CANVAS_UPDATE_NODE_PROMPT = "canvas.updateNodePrompt"
CANVAS_TOOL_SCHEMA_VERSION = "atelier.canvas.tools.v1"
_CANVAS_TOOLS = {TOOL_CANVAS_CREATE_VIDEO_NODE, TOOL_CANVAS_UPDATE_NODE_PROMPT}


def build_agent_canvas_binding(
    project: AtelierProject,
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    turn_id: str,
    call_id: str,
    approval_id: str,
    server_storage_identity: str,
    created_at: float | None = None,
    clock: Callable[[], float] | None = None,
    tool_schema_version: str = CANVAS_TOOL_SCHEMA_VERSION,
) -> AtelierAgentCanvasBinding:
    """Freeze one proposed local canvas mutation for a later exact approval."""

    if tool_name not in _CANVAS_TOOLS:
        raise ValueError("Unsupported Agent canvas tool")
    if not isinstance(server_storage_identity, str) or not server_storage_identity:
        raise ValueError("Agent canvas binding requires server storage identity")
    if created_at is None:
        if clock is None:
            raise ValueError("Agent canvas binding requires created_at or clock")
        created_at = clock()
    normalized = normalize_canvas_arguments(tool_name, arguments)
    target = canvas_target_state(project, tool_name, normalized)
    target_fingerprint = _hash64(target)
    arguments_fingerprint = _hash64(normalized)
    policy_hash = policy_snapshot_hash(project.agent_policy)
    authorization_hash = _authorization_hash(
        project_id=project.id,
        turn_id=turn_id,
        call_id=call_id,
        approval_id=approval_id,
        tool_name=tool_name,
        tool_schema_version=tool_schema_version,
        arguments_fingerprint=arguments_fingerprint,
        target_fingerprint=target_fingerprint,
        server_storage_identity=server_storage_identity,
        policy_snapshot_hash=policy_hash,
        normalized_arguments=normalized,
        target_state=target,
    )
    return AtelierAgentCanvasBinding(
        normalized_arguments=normalized,
        target_state=target,
        canvas_authorization_ref=AtelierAgentCanvasAuthorizationRef(
            project_id=project.id,
            turn_id=turn_id,
            call_id=call_id,
            approval_id=approval_id,
            tool_name=tool_name,
            tool_schema_version=tool_schema_version,
            authorization_hash=authorization_hash,
            arguments_fingerprint=arguments_fingerprint,
            target_fingerprint=target_fingerprint,
            server_storage_identity=server_storage_identity,
            policy_snapshot_hash=policy_hash,
        ),
        created_at=created_at,
    )


def build_agent_canvas_approval_record(
    ref: AtelierAgentCanvasAuthorizationRef,
    *,
    decision: str,
    decided_at: float | None = None,
    clock: Callable[[], float] | None = None,
) -> AtelierAgentCanvasApprovalRecord:
    if decided_at is None:
        if clock is None:
            raise ValueError("Agent canvas approval record requires decided_at or clock")
        decided_at = clock()
    return AtelierAgentCanvasApprovalRecord(
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        decision=decision,
        decided_at=decided_at,
    )


def normalize_canvas_arguments(tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if tool_name == TOOL_CANVAS_CREATE_VIDEO_NODE:
        allowed = {"type", "title", "prompt", "x", "y", "width", "height"}
        _reject_unknown(arguments, allowed)
        if arguments.get("type", "video") != "video":
            raise ValueError("canvas.createVideoNode type must be video")
        result: dict[str, Any] = {"type": "video"}
        for key in ("title", "prompt"):
            value = arguments.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            result[key] = value
        for key, default in (("x", 0.0), ("y", 0.0), ("width", 320.0), ("height", 180.0)):
            value = arguments.get(key, default)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{key} must be a finite number")
            if key in {"width", "height"} and float(value) <= 0:
                raise ValueError(f"{key} must be positive")
            result[key] = float(value)
        return result
    if tool_name == TOOL_CANVAS_UPDATE_NODE_PROMPT:
        allowed = {"node_id", "prompt", "title"}
        _reject_unknown(arguments, allowed)
        node_id = arguments.get("node_id")
        prompt = arguments.get("prompt")
        title = arguments.get("title")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id is required")
        if not isinstance(prompt, str):
            raise ValueError("prompt is required")
        result = {"node_id": node_id, "prompt": prompt}
        if title is not None:
            if not isinstance(title, str):
                raise ValueError("title must be a string")
            result["title"] = title
        return result
    raise ValueError("Unsupported Agent canvas tool")


def canvas_target_state(project: AtelierProject, tool_name: str, normalized_arguments: Mapping[str, Any]) -> dict[str, Any]:
    if tool_name == TOOL_CANVAS_CREATE_VIDEO_NODE:
        return {
            "kind": "project",
            "project_id": project.id,
            "node_count": len(project.nodes),
            "node_ids": [node.id for node in project.nodes],
        }
    if tool_name == TOOL_CANVAS_UPDATE_NODE_PROMPT:
        node_id = normalized_arguments.get("node_id")
        node = _find_node(project, node_id if isinstance(node_id, str) else "")
        return {"kind": "node", "project_id": project.id, "node": _node_state(node)}
    raise ValueError("Unsupported Agent canvas tool")


def validate_agent_canvas_binding(binding: AtelierAgentCanvasBinding, *, error: type[Exception] = StorageError) -> AtelierAgentCanvasBinding:
    try:
        ref = binding.canvas_authorization_ref
        if ref.tool_name not in _CANVAS_TOOLS:
            raise ValueError("Agent canvas tool mismatch")
        if normalize_canvas_arguments(ref.tool_name, binding.normalized_arguments) != binding.normalized_arguments:
            raise ValueError("Agent canvas arguments are not normalized")
        if ref.arguments_fingerprint != _hash64(binding.normalized_arguments):
            raise ValueError("Agent canvas arguments fingerprint mismatch")
        if ref.target_fingerprint != _hash64(binding.target_state):
            raise ValueError("Agent canvas target fingerprint mismatch")
        expected = _authorization_hash(
            project_id=ref.project_id,
            turn_id=ref.turn_id,
            call_id=ref.call_id,
            approval_id=ref.approval_id,
            tool_name=ref.tool_name,
            tool_schema_version=ref.tool_schema_version,
            arguments_fingerprint=ref.arguments_fingerprint,
            target_fingerprint=ref.target_fingerprint,
            server_storage_identity=ref.server_storage_identity,
            policy_snapshot_hash=ref.policy_snapshot_hash,
            normalized_arguments=binding.normalized_arguments,
            target_state=binding.target_state,
        )
        if ref.authorization_hash != expected:
            raise ValueError("Agent canvas authorization hash mismatch")
    except Exception as exc:  # noqa: BLE001 - durable storage boundary
        if isinstance(exc, error):
            raise
        raise error("Invalid Agent canvas authorization binding") from exc
    return binding


def require_approved_canvas_call(
    project: AtelierProject,
    *,
    turn_id: str,
    call_id: str,
    approval_id: str,
    authorization_hash: str,
    server_storage_identity: str | None,
) -> AtelierAgentCanvasBinding:
    if not isinstance(server_storage_identity, str) or not server_storage_identity:
        raise StorageConflictError("atelier_agent_canvas_storage_identity_required")
    turn = _find_turn(project.agent_turns, turn_id)
    if turn.status in {"failed", "canceled", "cancelled"}:
        raise StorageConflictError("atelier_agent_canvas_turn_not_active")
    call = _find_call(turn.tool_calls, call_id)
    if call.canvas_approval_binding is None:
        raise StorageConflictError("atelier_agent_canvas_binding_missing")
    binding = validate_agent_canvas_binding(call.canvas_approval_binding, error=StorageConflictError)
    ref = binding.canvas_authorization_ref
    if call.tool_name != ref.tool_name or call.tool_name not in _CANVAS_TOOLS:
        raise StorageConflictError("atelier_agent_canvas_tool_mismatch")
    if project.agent_policy.allowed_tools and ref.tool_name not in project.agent_policy.allowed_tools:
        raise StorageConflictError("atelier_agent_canvas_tool_not_allowed")
    if ref.project_id != project.id or ref.turn_id != turn_id or ref.call_id != call_id:
        raise StorageConflictError("atelier_agent_canvas_authorization_mismatch")
    if ref.approval_id != approval_id or ref.authorization_hash != authorization_hash:
        raise StorageConflictError("atelier_agent_canvas_authorization_mismatch")
    if ref.server_storage_identity != server_storage_identity:
        raise StorageConflictError("atelier_agent_canvas_storage_mismatch")
    if ref.policy_snapshot_hash != policy_snapshot_hash(project.agent_policy):
        raise StorageConflictError("atelier_agent_canvas_policy_changed")
    if ref.target_fingerprint != _hash64(canvas_target_state(project, ref.tool_name, binding.normalized_arguments)):
        raise StorageConflictError("atelier_agent_canvas_target_changed")
    if not call.approval_granted:
        raise StorageConflictError("atelier_agent_canvas_approval_not_granted")
    approval = call.canvas_approval_record
    if approval is None:
        raise StorageConflictError("atelier_agent_canvas_approval_missing")
    if approval.approval_id != ref.approval_id or approval.authorization_hash != ref.authorization_hash:
        raise StorageConflictError("atelier_agent_canvas_approval_mismatch")
    if approval.decision != "approved":
        raise StorageConflictError("atelier_agent_canvas_not_approved")
    return binding


def _authorization_hash(**payload: Any) -> str:
    return _hash64({"schema_version": 1, **payload})


def _hash64(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(serialize_json(value).encode("utf-8")).hexdigest()


def _reject_unknown(arguments: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"Unsupported canvas argument: {sorted(unknown)[0]}")


def _find_turn(turns: list[AtelierAgentTurn], turn_id: str) -> AtelierAgentTurn:
    for turn in turns:
        if turn.id == turn_id:
            return turn
    raise StorageConflictError("atelier_agent_canvas_turn_missing")


def _find_call(calls: list[AtelierAgentToolCall], call_id: str) -> AtelierAgentToolCall:
    for call in calls:
        if call.call_id == call_id:
            return call
    raise StorageConflictError("atelier_agent_canvas_call_missing")


def _find_node(project: AtelierProject, node_id: str) -> AtelierNode:
    for node in project.nodes:
        if node.id == node_id:
            return node
    raise StorageConflictError("atelier_agent_canvas_target_missing")


def _node_state(node: AtelierNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "project_id": node.project_id,
        "type": node.type,
        "title": node.title,
        "prompt": node.prompt,
        "status": node.status,
        "updated_at": node.updated_at,
    }
