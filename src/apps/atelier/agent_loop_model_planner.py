"""Model-backed Atelier loop planning through durable claim/settle authority."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol

from ...utils.json_storage import StorageConflictError
from .agent_canvas_authorization import TOOL_CANVAS_CREATE_VIDEO_NODE, TOOL_CANVAS_UPDATE_NODE_PROMPT
from .agent_tools import TOOL_SCHEMA_VERSION, list_tool_schemas
from .agent_loop import AgentLoopPlannerFailed, DeterministicLoopPlan, LoopPlannerContext
from .agent_planner_invocations import ClaimAgentPlannerInvocationCommand, SettleAgentPlannerInvocationCommand
from .references import pending_reference_nodes
from .models import (
    AtelierAgentPlannerConfigIdentity,
    AtelierAgentPlannerInvocation,
    AtelierAgentPlannerInvocationUsage,
    AtelierAgentPlannerMessage,
)
from .storage import AtelierStorage

_READ_TOOL = "canvas.readProject"


@dataclass(frozen=True)
class ModelPlannerConfig:
    provider_id: str
    model_id: str
    config_identity: AtelierAgentPlannerConfigIdentity
    max_completion_tokens: int
    timeout_seconds: float
    bounded_chat: "BoundedChatCallable | None" = None


class BoundedChatResultLike(Protocol):
    content: str
    requested_model: str
    provider_model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


BoundedChatCallable = Callable[..., BoundedChatResultLike]


class ModelBackedLoopPlanner:
    """Execute one model-backed planner iteration with durable call authority."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], ModelPlannerConfig],
        bounded_chat: BoundedChatCallable,
    ) -> None:
        self._config_provider = config_provider
        self._bounded_chat = bounded_chat

    def __call__(
        self,
        context: LoopPlannerContext,
        *,
        storage: AtelierStorage,
        project_id: str,
        expected_revision: int,
        iteration_index: int,
        clock: Callable[[], float],
        cancel_event: Event | None = None,
    ) -> DeterministicLoopPlan:
        turn = context.turn
        if turn.loop_start_context is None or turn.loop_start_context.planner != "model_adapter":
            raise StorageConflictError("atelier_agent_model_planner_context_mismatch")
        existing = _invocation_for_iteration(turn.planner_invocations, iteration_index)
        if existing is not None:
            return _plan_from_invocation(existing)

        config = self._config_provider()
        allowed_tools = _allowed_tool_names(context.project.agent_policy.allowed_tools)
        skill_snapshot = turn.loop_start_context.skill_snapshot
        if skill_snapshot is not None and skill_snapshot.expected_tools:
            allowed_tools = allowed_tools.intersection(skill_snapshot.expected_tools)
            if not allowed_tools:
                raise StorageConflictError("atelier_product_skill_tools_unavailable")
        if not allowed_tools:
            raise StorageConflictError("atelier_agent_model_planner_no_allowed_tools")
        messages = _build_messages(context, allowed_tools=allowed_tools)
        claim_command = ClaimAgentPlannerInvocationCommand(
            expected_revision=expected_revision,
            turn_id=turn.id,
            iteration_index=iteration_index,
            claim_request_id=f"{turn.id}:iteration:{iteration_index}:claim",
            provider_id=config.provider_id,
            model_id=config.model_id,
            config_identity=config.config_identity,
            input_messages=tuple(messages),
            max_completion_tokens=config.max_completion_tokens,
            timeout_seconds=config.timeout_seconds,
        )
        _project_after_claim, ack = storage.claim_agent_planner_invocation(project_id, claim_command, now=clock())
        invocation = ack.invocation
        if not ack.claimed:
            return _plan_from_invocation(invocation)
        if _cancelled(cancel_event):
            _settle_before_call_failed(
                storage, project_id, invocation.invocation_id, clock=clock,
                error_code="planner_cancelled_before_call",
                error_message="planner call cancelled before provider request",
            )
            raise AgentLoopPlannerFailed("planner_cancelled_before_call")
        try:
            _assert_still_callable(storage, project_id, turn.id, invocation_id=invocation.invocation_id)
        except StorageConflictError as exc:
            _settle_before_call_failed(
                storage, project_id, invocation.invocation_id, clock=clock,
                error_code="planner_policy_changed_before_call",
                error_message="planner call blocked before provider request",
            )
            raise AgentLoopPlannerFailed("planner_blocked_before_call") from exc
        bounded_chat = config.bounded_chat or self._bounded_chat
        try:
            result = bounded_chat(
                [message.model_dump(mode="json") for message in invocation.input_messages],
                model=invocation.model_id,
                max_completion_tokens=invocation.max_completion_tokens,
                timeout=invocation.timeout_seconds,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 - provider detail must not leak into durable state
            storage.settle_agent_planner_invocation(
                project_id,
                SettleAgentPlannerInvocationCommand(
                    invocation_id=invocation.invocation_id,
                    outcome="failed",
                    result_content=None,
                    provider_model=None,
                    usage=None,
                    error_code="planner_call_failed",
                    error_message="bounded planner call failed",
                ),
                now=clock(),
            )
            raise AgentLoopPlannerFailed("planner_failed") from exc
        usage = AtelierAgentPlannerInvocationUsage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        )
        _project_after_settle, settle_ack = storage.settle_agent_planner_invocation(
            project_id,
            SettleAgentPlannerInvocationCommand(
                invocation_id=invocation.invocation_id,
                outcome="success",
                result_content=result.content,
                provider_model=result.provider_model,
                usage=usage,
            ),
            now=clock(),
        )
        return _plan_from_invocation(settle_ack.invocation)


def _invocation_for_iteration(invocations: list[AtelierAgentPlannerInvocation], iteration_index: int) -> AtelierAgentPlannerInvocation | None:
    return next((item for item in invocations if item.iteration_index == iteration_index), None)


def _build_messages(context: LoopPlannerContext, *, allowed_tools: set[str]) -> list[AtelierAgentPlannerMessage]:
    turn = context.turn
    intent = turn.loop_start_context
    skill_snapshot = intent.skill_snapshot if intent is not None else None
    active_skill = None
    if skill_snapshot is not None:
        active_skill = {
            "name": skill_snapshot.name,
            "version": skill_snapshot.version,
            "title": skill_snapshot.title,
            "description": skill_snapshot.description,
            "category": skill_snapshot.category,
            "prompt_template": skill_snapshot.prompt_template,
            "expected_tools": list(skill_snapshot.expected_tools),
            "content_hash": skill_snapshot.content_hash,
        }
    project_payload = {
        "project_id": context.project.id,
        "revision": context.project.revision,
        "selected_node_id": intent.selected_node_id if intent is not None else None,
        "user_message": intent.user_message if intent is not None else turn.user_message,
        "selected_node_snapshot": _node_payload(
            next((node for node in context.project.nodes if intent is not None and node.id == intent.selected_node_id), None)
        ),
        "nodes": [
            _node_payload(node)
            for node in context.project.nodes
        ],
        "prior_messages": context.messages_history,
        "policy_snapshot": context.project.agent_policy.model_dump(mode="json"),
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "allowed_tool_schemas": _allowed_tool_schemas(allowed_tools),
        "active_skill": active_skill,
        "output_contract": {
            "type": "object",
            "required": ["assistant_message", "tool_calls"],
            "additionalProperties": False,
            "properties": {
                "assistant_message": {"type": "string"},
                "tool_calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["tool_name", "arguments"],
                        "additionalProperties": False,
                        "properties": {
                            "tool_name": {"type": "string", "enum": sorted(allowed_tools)},
                            "arguments": {"type": "object"},
                        },
                    },
                },
                "stop_reason": {"type": "string"},
            },
        },
    }
    return [
        AtelierAgentPlannerMessage(
            role="system",
            content=(
                "You are LumenX Atelier's bounded planner. Return strict JSON with "
                "assistant_message, tool_calls, and optional stop_reason. Use only the tool names and schemas "
                "listed in the user message. Do not invent tools or omit required output fields."
            ),
        ),
        AtelierAgentPlannerMessage(role="user", content=json.dumps(project_payload, sort_keys=True, separators=(",", ":"))),
    ]


def _node_payload(node) -> dict[str, Any] | None:
    if node is None:
        return None
    reference_node_ids = node.data.get("reference_node_ids") if isinstance(node.data, dict) else None
    pending_refs = pending_reference_nodes(node.data.get("pending_reference_nodes")) if isinstance(node.data, dict) else []
    reference_image_urls = node.data.get("reference_image_urls") if isinstance(node.data, dict) else None
    return {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "prompt": node.prompt,
        "status": node.status,
        "media_urls": list(node.media_urls),
        "reference_node_ids": reference_node_ids if isinstance(reference_node_ids, list) else None,
        "pending_reference_nodes": pending_refs or None,
        "reference_image_urls": reference_image_urls if isinstance(reference_image_urls, list) else None,
    }


def _callable_tool_schemas() -> list[dict[str, Any]]:
    registry = list_tool_schemas()
    canvas_schemas = [
        {
            "name": TOOL_CANVAS_CREATE_VIDEO_NODE,
            "description": "Propose creation of one video node on the Atelier canvas; approval is required before mutation.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string", "enum": ["video"]},
                    "title": {"type": "string"},
                    "prompt": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "width": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
            },
            "required_permission": "canvas_write",
            "mutates_canvas": True,
            "requires_approval": True,
            "proposal_only": False,
            "mutation_scope": "canvas_write",
            "tool_schema_version": "atelier.canvas.tools.v1",
        },
        {
            "name": TOOL_CANVAS_UPDATE_NODE_PROMPT,
            "description": "Propose prompt/title changes for one existing Atelier node; approval is required before mutation.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["node_id", "prompt"],
                "properties": {
                    "node_id": {"type": "string", "minLength": 1},
                    "prompt": {"type": "string"},
                    "title": {"type": "string"},
                },
            },
            "required_permission": "canvas_write",
            "mutates_canvas": True,
            "requires_approval": True,
            "proposal_only": False,
            "mutation_scope": "canvas_write",
            "tool_schema_version": "atelier.canvas.tools.v1",
        },
    ]
    return [*registry, *canvas_schemas]


def _allowed_tool_names(policy_allowed_tools: list[str]) -> set[str]:
    callable_names = {schema["name"] for schema in _callable_tool_schemas() if isinstance(schema.get("name"), str)}
    if policy_allowed_tools:
        return callable_names.intersection(policy_allowed_tools)
    return callable_names


def _allowed_tool_schemas(allowed_tools: set[str]) -> list[dict[str, Any]]:
    return [schema for schema in _callable_tool_schemas() if schema.get("name") in allowed_tools]


def _settle_before_call_failed(
    storage: AtelierStorage,
    project_id: str,
    invocation_id: str,
    *,
    clock: Callable[[], float],
    error_code: str,
    error_message: str,
) -> None:
    storage.settle_agent_planner_invocation(
        project_id,
        SettleAgentPlannerInvocationCommand(
            invocation_id=invocation_id,
            outcome="failed",
            result_content=None,
            provider_model=None,
            usage=AtelierAgentPlannerInvocationUsage(prompt_tokens=None, completion_tokens=0, total_tokens=None),
            error_code=error_code,
            error_message=error_message,
        ),
        now=clock(),
    )


def _plan_from_invocation(invocation: AtelierAgentPlannerInvocation) -> DeterministicLoopPlan:
    if invocation.status == "claimed":
        raise StorageConflictError("atelier_agent_model_planner_invocation_unsettled")
    if invocation.outcome != "success":
        raise AgentLoopPlannerFailed("planner_failed")
    try:
        return _parse_plan(invocation.result_content or "", allowed_tools=_allowed_tool_names_for_invocation(invocation))
    except ValueError as exc:
        raise AgentLoopPlannerFailed("planner_parse_failed") from exc


def _parse_plan(content: str, *, allowed_tools: set[str]) -> DeterministicLoopPlan:
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise ValueError("planner result is not JSON") from exc
    if not isinstance(payload, dict) or set(payload) - {"assistant_message", "tool_calls", "stop_reason"}:
        raise ValueError("planner result shape is invalid")
    if not {"assistant_message", "tool_calls"}.issubset(payload):
        raise ValueError("planner result shape is invalid")
    assistant_message = payload.get("assistant_message", "")
    if not isinstance(assistant_message, str):
        raise ValueError("planner assistant_message is invalid")
    stop_reason = payload.get("stop_reason")
    if stop_reason is not None and not isinstance(stop_reason, str):
        raise ValueError("planner stop_reason is invalid")
    tool_calls = payload.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        raise ValueError("planner tool_calls is invalid")
    parsed_calls: list[dict[str, Any]] = []
    for item in tool_calls:
        if not isinstance(item, dict) or set(item) != {"tool_name", "arguments"}:
            raise ValueError("planner tool call shape is invalid")
        tool_name = item.get("tool_name")
        arguments = item.get("arguments")
        if not isinstance(tool_name, str) or tool_name not in allowed_tools or not isinstance(arguments, dict):
            raise ValueError("planner tool call is not allowed")
        if tool_name == _READ_TOOL and arguments:
            raise ValueError("planner readProject arguments must be empty")
        parsed_calls.append({"tool_name": tool_name, "arguments": dict(arguments)})
    return DeterministicLoopPlan(assistant_message=assistant_message, tool_calls=parsed_calls, stop_reason=stop_reason)


def _allowed_tool_names_for_invocation(invocation: AtelierAgentPlannerInvocation) -> set[str]:
    if not invocation.input_messages:
        return _allowed_tool_names([])
    try:
        payload = json.loads(invocation.input_messages[-1].content)
    except (json.JSONDecodeError, TypeError):
        return _allowed_tool_names([])
    if not isinstance(payload, dict):
        return _allowed_tool_names([])
    schemas = payload.get("allowed_tool_schemas")
    if not isinstance(schemas, list):
        return _allowed_tool_names([])
    names = {item.get("name") for item in schemas if isinstance(item, dict) and isinstance(item.get("name"), str)}
    return names.intersection(_allowed_tool_names([]))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("planner result contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"planner result contains non-finite JSON constant: {value}")


def _assert_still_callable(storage: AtelierStorage, project_id: str, turn_id: str, *, invocation_id: str) -> None:
    project = storage.get_project(project_id)
    turn = next((item for item in project.agent_turns if item.id == turn_id), None)
    if turn is None or turn.loop_start_context is None or turn.loop_start_context.planner != "model_adapter":
        raise StorageConflictError("atelier_agent_model_planner_turn_missing")
    if turn.status != "pending" or turn.pending_tool_calls:
        raise StorageConflictError("atelier_agent_model_planner_not_callable")
    invocation = next((item for item in turn.planner_invocations if item.invocation_id == invocation_id), None)
    if invocation is None or invocation.status != "claimed":
        raise StorageConflictError("atelier_agent_model_planner_claim_missing")
    if type(project.agent_policy.max_tokens_per_turn) is not int or project.agent_policy.max_tokens_per_turn < 0:
        raise StorageConflictError("atelier_agent_model_planner_budget_invalid")
    if turn.completion_tokens_used + turn.completion_tokens_reserved > project.agent_policy.max_tokens_per_turn:
        raise StorageConflictError("atelier_agent_model_planner_budget_changed")
    allowed = _allowed_tool_names(project.agent_policy.allowed_tools)
    snapshot = turn.loop_start_context.skill_snapshot
    if snapshot is not None and snapshot.expected_tools:
        allowed = allowed.intersection(snapshot.expected_tools)
        if not allowed:
            raise StorageConflictError("atelier_product_skill_tools_unavailable")
    if not allowed:
        raise StorageConflictError("atelier_agent_model_planner_no_allowed_tools")


def _cancelled(cancel_event: Event | None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())
