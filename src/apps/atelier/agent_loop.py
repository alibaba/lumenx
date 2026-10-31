"""Durable local Agent loop service for read, canvas and generation iterations."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ...utils.json_storage import StorageConflictError
from .agent_canvas_authorization import TOOL_CANVAS_CREATE_VIDEO_NODE, TOOL_CANVAS_UPDATE_NODE_PROMPT, build_agent_canvas_binding
from .agent_canvas_commands import ApplyApprovedAgentCanvasCommand
from .agent_commands import AdmitApprovedAgentGenerationCommand
from .agent_generation_preparation import prepare_agent_generation_call
from .agent_authorization import TOOL_GENERATION_CREATE_VIDEO_CANDIDATES
from .generation_admission import GenerationAdmissionAck
from .agent_loop_commands import SaveAgentLoopIterationCommand, StartAgentLoopCommand
from .product_skills import CALLABLE_LOOP_TOOL_NAMES, resolve_product_skill_snapshot
from .skill_runtime import resolve_runtime_skill_snapshot
from .models import AtelierAgentToolCall, AtelierAgentToolStatus, AtelierAgentTurn, AtelierProject
from .storage import AtelierStorage

_READ_TOOL = "canvas.readProject"
_CANVAS_TOOLS = {TOOL_CANVAS_CREATE_VIDEO_NODE, TOOL_CANVAS_UPDATE_NODE_PROMPT}
_GENERATION_TOOL = TOOL_GENERATION_CREATE_VIDEO_CANDIDATES
_IMPLEMENTED_TOOLS = {_READ_TOOL, _GENERATION_TOOL, *_CANVAS_TOOLS}
_TERMINAL_ATTEMPT_STATUSES = {"completed", "failed", "cancelled"}

AgentLoopEventType = Literal[
    "loop_started", "loop_start_replayed", "loop_resumed", "iteration_started", "iteration_saved",
    "tool_started", "tool_done", "approval_paused", "canvas_approval_recorded",
    "generation_admitted", "generation_schedule_requested", "generation_schedule_failed",
    "generation_waiting", "loop_terminal", "loop_closed",
]


class AgentLoopTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AgentLoopEventType
    turn_id: str
    iteration: int | None = None
    call_id: str | None = None
    tool_name: str | None = None
    approval_id: str | None = None
    attempt_id: str | None = None
    candidate_id: str | None = None
    status: str | None = None
    decision: str | None = None
    message: str | None = None
    replayed: bool = False

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class AgentLoopNotFound(Exception):
    """The requested Agent loop turn is not present in the project ledger."""


class AgentLoopPlannerFailed(Exception):
    """A model-backed planner invocation settled but cannot grant tool execution."""

    def __init__(self, response: str):
        self.response = response
        super().__init__(response)


class AgentLoopEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    type: AgentLoopEventType
    project_id: str
    turn_id: str
    iteration: int | None = None
    call_id: str | None = None
    tool_name: str | None = None
    approval_id: str | None = None
    attempt_id: str | None = None
    candidate_id: str | None = None
    message: str | None = None
    replayed: bool = False
    ack: "AgentLoopAck | None" = None


@dataclass
class DeterministicLoopPlan:
    assistant_message: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass(frozen=True)
class LoopPlannerContext:
    project: AtelierProject
    turn: AtelierAgentTurn
    messages_history: list[dict[str, Any]]


class AgentLoopAck(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project: AtelierProject
    turn: AtelierAgentTurn
    replayed: bool = False
    events: list[AgentLoopTraceEvent] = Field(default_factory=list)
    generation: GenerationAdmissionAck | None = None
    scheduled: bool = False
    schedule_error: str | None = None


@dataclass
class _ConsumeResult:
    consumed: bool = False
    generation: GenerationAdmissionAck | None = None
    scheduled: bool = False
    schedule_error: str | None = None


class AtelierAgentLoopService:
    """Run a local deterministic loop without HTTP/provider publication."""

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        planner: Callable[[LoopPlannerContext], DeterministicLoopPlan],
        model_planner: Callable[..., DeterministicLoopPlan] | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str],
        server_storage_identity: str,
        credential_provider: Callable[[], Any] | None = None,
        generation_scheduler: Callable[[str, str], Any] | None = None,
        product_skill_resolver: Callable[[str], Any] | None = None,
        platform_skill_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
    ):
        self.storage = storage
        self._planner = planner
        self._model_planner = model_planner
        self._clock = clock
        self._id_factory = id_factory
        self._server_storage_identity = server_storage_identity
        self._credential_provider = credential_provider
        self._generation_scheduler = generation_scheduler
        self._product_skill_resolver = product_skill_resolver or resolve_product_skill_snapshot
        self._platform_skill_resolver = platform_skill_resolver

    def start_loop(self, project_id: str, command: StartAgentLoopCommand) -> AgentLoopAck:
        return _drain_ack(self.iter_start_loop(project_id, command))

    def iter_start_loop(self, project_id: str, command: StartAgentLoopCommand, *, cancel_event: Event | None = None) -> Iterator[AgentLoopEvent]:
        if command.planner not in {"deterministic_core", "model_adapter"}:
            raise ValueError("unsupported planner for local Agent loop")
        if _cancelled(cancel_event):
            return
        replay_project = self.storage.read_agent_loop_start_replay(project_id, command)
        if replay_project is not None:
            turn = _find_turn(replay_project, command.client_turn_id)
            events = [_event("loop_start_replayed", turn.id, replayed=True)]
            yield _stream_event("loop_start_replayed", replay_project.id, turn.id, replayed=True)
            yield self._final_event_for_current_state(project_id, turn.id, events, replayed=True)
            return
        resolved_skill_snapshot = None
        if command.skill_name is not None:
            if command.planner != "model_adapter":
                raise ValueError("atelier_agent_loop_skill_requires_model_adapter")
            platform_manifest = self._platform_skill_resolver(command.skill_name) if self._platform_skill_resolver is not None else None
            resolved_skill_snapshot = resolve_runtime_skill_snapshot(
                command.skill_name,
                platform_manifest=platform_manifest,
                fallback_resolver=self._product_skill_resolver,
            )
            project_for_policy = self.storage.get_project(project_id)
            expected_tools = set(resolved_skill_snapshot.expected_tools)
            if expected_tools - CALLABLE_LOOP_TOOL_NAMES:
                raise ValueError("atelier_product_skill_tools_unavailable")
            policy_tools = set(project_for_policy.agent_policy.allowed_tools) if project_for_policy.agent_policy.allowed_tools else CALLABLE_LOOP_TOOL_NAMES
            if expected_tools and not expected_tools.intersection(policy_tools):
                raise ValueError("atelier_product_skill_tools_unavailable")
        if command.planner == "model_adapter" and self._model_planner is None:
            raise ValueError("model-backed planner is unavailable")
        project, replayed = self.storage.start_agent_loop_turn(project_id, command, now=self._clock(), resolved_skill_snapshot=resolved_skill_snapshot)
        turn = _find_turn(project, command.client_turn_id)
        events = [_event("loop_start_replayed" if replayed else "loop_started", turn.id, replayed=replayed)]
        yield _stream_event(events[-1]["type"], project.id, turn.id, replayed=replayed)
        if _cancelled(cancel_event):
            yield self._final_event("loop_closed", project_id, turn.id, events, replayed=replayed)
            return
        if replayed or _terminal(turn) or turn.status == "waiting_approval":
            yield self._final_event_for_current_state(project_id, turn.id, events, replayed=replayed)
            return
        yield from self._drive_iter(project_id, turn.id, replayed=False, events=events, cancel_event=cancel_event)

    def resume_loop(self, project_id: str, turn_id: str) -> AgentLoopAck:
        return _drain_ack(self.iter_resume_loop(project_id, turn_id))

    def iter_resume_loop(self, project_id: str, turn_id: str, *, cancel_event: Event | None = None) -> Iterator[AgentLoopEvent]:
        project = self.storage.get_project(project_id)
        turn = _find_turn(project, turn_id)
        events = [_event("loop_resumed", turn_id)]
        yield _stream_event("loop_resumed", project.id, turn_id)
        if _cancelled(cancel_event):
            yield self._final_event("loop_closed", project_id, turn.id, events)
            return
        if _terminal(turn):
            events.append(_event("loop_terminal", turn.id, status=turn.status))
            yield self._final_event("loop_terminal", project_id, turn.id, events, replayed=True)
            return
        yield from self._drive_iter(project_id, turn_id, replayed=False, events=events, cancel_event=cancel_event)

    def read_loop(self, project_id: str, turn_id: str, *, replayed: bool = True, events: list[AgentLoopTraceEvent] | None = None) -> AgentLoopAck:
        project = self.storage.get_project(project_id)
        turn = _find_turn(project, turn_id)
        generation = None
        schedule_error = None
        blocked = _blocking_generation(project, turn)
        if blocked is not None:
            call, generation = blocked
            schedule_error = _call_schedule_error(call)
        return AgentLoopAck(
            project=project, turn=turn, replayed=replayed, events=events or [],
            generation=generation, scheduled=False, schedule_error=schedule_error,
        )

    def _drive_iter(self, project_id: str, turn_id: str, *, replayed: bool, events: list[AgentLoopTraceEvent], cancel_event: Event | None = None) -> Iterator[AgentLoopEvent]:
        last_generation: GenerationAdmissionAck | None = None
        last_scheduled = False
        last_schedule_error: str | None = None
        while True:
            project = self.storage.get_project(project_id)
            turn = _find_turn(project, turn_id)
            if _terminal(turn):
                events.append(_event("loop_terminal", turn.id, status=turn.status))
                yield self._final_ack_event("loop_terminal", project, turn, events, replayed=replayed)
                return
            consumed = yield from self._consume_pending_iter(project, turn, events, cancel_event=cancel_event)
            if consumed.consumed:
                last_generation = consumed.generation or last_generation
                last_scheduled = consumed.scheduled
                last_schedule_error = consumed.schedule_error
                if _cancelled(cancel_event):
                    yield self._final_event("loop_closed", project_id, turn.id, events, replayed=replayed)
                    return
                if consumed.schedule_error is not None:
                    project = self.storage.get_project(project_id)
                    turn = _find_turn(project, turn_id)
                    yield self._final_ack_event(
                        "generation_schedule_failed", project, turn, events, replayed=replayed,
                        generation=last_generation, scheduled=False, schedule_error=consumed.schedule_error,
                    )
                    return
                continue
            project = self.storage.get_project(project_id)
            turn = _find_turn(project, turn_id)
            if turn.pending_tool_calls:
                events.append(_event("approval_paused", turn.id))
                yield self._final_ack_event("approval_paused", project, turn, events, replayed=replayed)
                return
            blocked = _blocking_generation(project, turn)
            if blocked is not None:
                call, generation = blocked
                events.append(_event(
                    "generation_waiting", turn.id, call_id=call.call_id,
                    attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id,
                ))
                persisted_schedule_error = _call_schedule_error(call)
                yield self._final_ack_event(
                    "generation_waiting", project, turn, events, replayed=replayed, generation=generation,
                    scheduled=last_scheduled if last_generation is not None and last_generation.attempt.attempt_id == generation.attempt.attempt_id else False,
                    schedule_error=(
                        last_schedule_error
                        if last_generation is not None and last_generation.attempt.attempt_id == generation.attempt.attempt_id
                        else persisted_schedule_error
                    ),
                )
                return
            if turn.iteration_count >= turn.max_iterations:
                if _cancelled(cancel_event):
                    yield self._final_event("loop_closed", project_id, turn.id, events, replayed=replayed)
                    return
                project = self.storage.finish_agent_loop_turn(
                    project_id,
                    expected_revision=project.revision,
                    turn_id=turn.id,
                    status="completed",
                    response="iteration_limit",
                    now=self._clock(),
                )
                turn = _find_turn(project, turn_id)
                events.append(_event("loop_terminal", turn.id, status=turn.status))
                yield self._final_ack_event("loop_terminal", project, turn, events, replayed=replayed)
                return
            events.append(_event("iteration_started", turn.id, iteration=turn.iteration_count + 1))
            yield _stream_event("iteration_started", project.id, turn.id, iteration=turn.iteration_count + 1)
            if _cancelled(cancel_event):
                yield self._final_event("loop_closed", project_id, turn.id, events, replayed=replayed)
                return
            try:
                if turn.loop_start_context is not None and turn.loop_start_context.planner == "model_adapter":
                    if self._model_planner is None:
                        raise ValueError("model-backed planner is unavailable")
                    plan = self._model_planner(
                        LoopPlannerContext(project=project, turn=turn, messages_history=list(turn.messages_history)),
                        storage=self.storage,
                        project_id=project_id,
                        expected_revision=project.revision,
                        iteration_index=turn.iteration_count + 1,
                        clock=self._clock,
                        cancel_event=cancel_event,
                    )
                else:
                    plan = self._planner(LoopPlannerContext(project=project, turn=turn, messages_history=list(turn.messages_history)))
            except AgentLoopPlannerFailed as exc:
                project = self.storage.get_project(project_id)
                turn = _find_turn(project, turn_id)
                failed = self.storage.finish_agent_loop_turn(
                    project_id,
                    expected_revision=project.revision,
                    turn_id=turn.id,
                    status="failed",
                    response=exc.response,
                    now=self._clock(),
                )
                turn = _find_turn(failed, turn_id)
                events.append(_event("loop_terminal", turn.id, status=turn.status))
                yield self._final_ack_event("loop_terminal", failed, turn, events, replayed=replayed)
                return
            project = self.storage.get_project(project_id)
            turn = _find_turn(project, turn_id)
            model_backed_turn = turn.loop_start_context is not None and turn.loop_start_context.planner == "model_adapter"
            try:
                calls = self._prepare_calls(project, turn, plan)
                if _cancelled(cancel_event):
                    yield self._final_event("loop_closed", project_id, turn.id, events, replayed=replayed)
                    return
                project, _ = self.storage.save_agent_loop_iteration(
                    project_id,
                    SaveAgentLoopIterationCommand(
                        expected_revision=project.revision,
                        turn_id=turn.id,
                        iteration_index=turn.iteration_count + 1,
                        planner=turn.loop_start_context.planner if turn.loop_start_context is not None else "deterministic_core",
                        assistant_message=plan.assistant_message,
                        tool_calls=tuple(calls),
                        stop_reason=plan.stop_reason,
                    ),
                    now=self._clock(),
                )
            except (StorageConflictError, ValueError, KeyError, TypeError):
                if not model_backed_turn:
                    raise
                current = self.storage.get_project(project_id)
                current_turn = _find_turn(current, turn_id)
                failed = self.storage.finish_agent_loop_turn(
                    project_id,
                    expected_revision=current.revision,
                    turn_id=current_turn.id,
                    status="failed",
                    response="planner_projection_failed",
                    now=self._clock(),
                )
                turn = _find_turn(failed, turn_id)
                events.append(_event("loop_terminal", turn.id, status=turn.status))
                yield self._final_ack_event("loop_terminal", failed, turn, events, replayed=replayed)
                return
            turn = _find_turn(project, turn_id)
            events.append(_event("iteration_saved", turn.id, iteration=turn.iteration_count))
            yield _stream_event("iteration_saved", project.id, turn.id, iteration=turn.iteration_count)
            if _cancelled(cancel_event):
                yield self._final_event("loop_closed", project_id, turn.id, events, replayed=replayed)
                return
            if turn.pending_tool_calls:
                continue
            if _terminal(turn):
                events.append(_event("loop_terminal", turn.id, status=turn.status))
                yield self._final_ack_event("loop_terminal", project, turn, events, replayed=replayed)
                return

    def _consume_pending_iter(
        self, project: AtelierProject, turn: AtelierAgentTurn, events: list[AgentLoopTraceEvent], *, cancel_event: Event | None = None,
    ) -> Iterator[AgentLoopEvent]:
        if not turn.pending_tool_calls:
            return _ConsumeResult()
        result = _ConsumeResult()
        for item in list(turn.pending_tool_calls):
            if not isinstance(item, dict):
                raise StorageConflictError("atelier_agent_loop_pending_malformed")
            call_id = item.get("call_id")
            call = next((entry for entry in turn.tool_calls if entry.call_id == call_id), None)
            if call is None:
                raise StorageConflictError("atelier_agent_loop_pending_call_missing")
            runnable = call.tool_name == _READ_TOOL or (
                call.tool_name in _CANVAS_TOOLS and call.canvas_approval_record is not None
                and call.canvas_approval_record.decision == "approved" and call.approval_granted
            ) or (
                call.tool_name == _GENERATION_TOOL and call.approval_record is not None
                and call.approval_record.decision == "approved" and call.approval_granted
            )
            if not runnable:
                return result
            events.append(_event("tool_started", turn.id, call_id=call.call_id, tool_name=call.tool_name))
            yield _stream_event("tool_started", project.id, turn.id, call_id=call.call_id, tool_name=call.tool_name)
            if _cancelled(cancel_event):
                return result
            if call.tool_name == _READ_TOOL:
                project, replay = self.storage.apply_agent_loop_read_call(project.id, turn.id, call.call_id, now=self._clock())
                events.append(_event("tool_done", turn.id, call_id=call.call_id, tool_name=call.tool_name, replayed=replay))
                yield _stream_event("tool_done", project.id, turn.id, call_id=call.call_id, tool_name=call.tool_name, replayed=replay)
                result.consumed = True
                continue
            if call.tool_name in _CANVAS_TOOLS:
                ref = call.canvas_approval_binding.canvas_authorization_ref if call.canvas_approval_binding else None
                if ref is None:
                    raise StorageConflictError("atelier_agent_loop_canvas_binding_missing")
                project, replay = self.storage.apply_agent_loop_canvas_command(
                    project.id,
                    ApplyApprovedAgentCanvasCommand(
                        expected_revision=project.revision, turn_id=turn.id, call_id=call.call_id,
                        approval_id=ref.approval_id, authorization_hash=ref.authorization_hash,
                    ),
                    now=self._clock(), server_storage_identity=self._server_storage_identity, node_id_factory=self._id_factory,
                )
                events.append(_event("tool_done", turn.id, call_id=call.call_id, tool_name=call.tool_name, replayed=replay))
                yield _stream_event("tool_done", project.id, turn.id, call_id=call.call_id, tool_name=call.tool_name, replayed=replay)
                result.consumed = True
                continue
            if call.tool_name == _GENERATION_TOOL:
                ref = call.preview_binding.agent_authorization_ref if call.preview_binding else None
                if ref is None:
                    raise StorageConflictError("atelier_agent_loop_generation_binding_missing")
                client_request_id = call.preview_binding.preview_public_record["raw_request"]["client_request_id"]
                project, replay = self.storage.apply_agent_loop_generation_admission(
                    project.id,
                    AdmitApprovedAgentGenerationCommand(
                        expected_revision=project.revision, turn_id=turn.id, call_id=call.call_id,
                        approval_id=ref.approval_id, authorization_hash=ref.authorization_hash, client_request_id=client_request_id,
                    ),
                    now=self._clock(),
                )
                generation = _generation_ack(project, client_request_id, replayed=replay)
                events.append(_event(
                    "generation_admitted", turn.id, call_id=call.call_id,
                    attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id, replayed=replay,
                ))
                yield _stream_event(
                    "generation_admitted", project.id, turn.id, call_id=call.call_id,
                    attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id, replayed=replay,
                )
                if _cancelled(cancel_event):
                    result.generation = generation
                    result.consumed = True
                    return result
                if not replay and generation.new_candidate_ids:
                    schedule_error = self._schedule_generation(project.id, generation, events, turn.id, call.call_id)
                    if schedule_error is not None:
                        project, _ = self.storage.record_agent_loop_generation_schedule_failure(
                            project.id, turn_id=turn.id, call_id=call.call_id, client_request_id=client_request_id,
                            attempt_id=generation.attempt.attempt_id, error_code=schedule_error, now=self._clock(),
                        )
                        generation = _generation_ack(project, client_request_id, replayed=replay)
                        events.append(_event(
                            "generation_schedule_failed", turn.id, call_id=call.call_id,
                            attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id, message=schedule_error,
                        ))
                        yield _stream_event(
                            "generation_schedule_failed", project.id, turn.id, call_id=call.call_id,
                            attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id, message=schedule_error,
                        )
                    else:
                        result.scheduled = True
                        yield _stream_event(
                            "generation_schedule_requested", project.id, turn.id, call_id=call.call_id,
                            attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id,
                        )
                    result.schedule_error = schedule_error
                result.generation = generation
                result.consumed = True
                continue
            raise StorageConflictError("atelier_agent_loop_tool_not_implemented")
        return result

    def _schedule_generation(self, project_id: str, generation: GenerationAdmissionAck, events: list[AgentLoopTraceEvent], turn_id: str, call_id: str) -> str | None:
        if self._generation_scheduler is None:
            return "generation_schedule_unavailable"
        try:
            result = self._generation_scheduler(project_id, generation.attempt.attempt_id)
        except Exception:  # noqa: BLE001 - scheduler failure must stay durable and opaque
            return "generation_schedule_failed"
        if result is False:
            return "generation_schedule_not_accepted"
        events.append(_event(
            "generation_schedule_requested", turn_id, call_id=call_id,
            attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id,
        ))
        return None

    def _final_ack_event(
        self, event_type: str, project: AtelierProject, turn: AtelierAgentTurn, events: list[AgentLoopTraceEvent], *,
        replayed: bool = False, generation: GenerationAdmissionAck | None = None, scheduled: bool = False, schedule_error: str | None = None,
    ) -> AgentLoopEvent:
        history = list(events)
        if not history or history[-1].get("type") != event_type:
            history.append(_event(event_type, turn.id))
        ack = AgentLoopAck(
            project=project, turn=turn, replayed=replayed, events=history,
            generation=generation, scheduled=scheduled, schedule_error=schedule_error,
        )
        return _stream_event(event_type, project.id, turn.id, ack=ack, replayed=replayed)

    def _final_event(self, event_type: str, project_id: str, turn_id: str, events: list[AgentLoopTraceEvent], *, replayed: bool = False) -> AgentLoopEvent:
        project = self.storage.get_project(project_id)
        turn = _find_turn(project, turn_id)
        return self._final_ack_event(event_type, project, turn, events, replayed=replayed)

    def _final_event_for_current_state(self, project_id: str, turn_id: str, events: list[AgentLoopTraceEvent], *, replayed: bool = False) -> AgentLoopEvent:
        project = self.storage.get_project(project_id)
        turn = _find_turn(project, turn_id)
        blocked = _blocking_generation(project, turn)
        if blocked is not None:
            call, generation = blocked
            events.append(_event(
                "generation_waiting", turn.id, call_id=call.call_id,
                attempt_id=generation.attempt.attempt_id, candidate_id=generation.attempt.candidate_id,
            ))
            return self._final_ack_event(
                "generation_waiting", project, turn, events, replayed=replayed,
                generation=generation, schedule_error=_call_schedule_error(call),
            )
        if _terminal(turn):
            events.append(_event("loop_terminal", turn.id, status=turn.status))
            return self._final_ack_event("loop_terminal", project, turn, events, replayed=replayed)
        if turn.pending_tool_calls or turn.status == "waiting_approval":
            events.append(_event("approval_paused", turn.id))
            return self._final_ack_event("approval_paused", project, turn, events, replayed=replayed)
        return self._final_ack_event("loop_closed", project, turn, events, replayed=replayed)

    def _prepare_calls(self, project: AtelierProject, turn: AtelierAgentTurn, plan: DeterministicLoopPlan) -> list[AtelierAgentToolCall]:
        calls: list[AtelierAgentToolCall] = []
        mutation_targets: set[str] = set()
        skill_snapshot = turn.loop_start_context.skill_snapshot if turn.loop_start_context is not None else None
        skill_tools = set(skill_snapshot.expected_tools) if skill_snapshot is not None and skill_snapshot.expected_tools else None
        for raw in plan.tool_calls:
            tool_name = raw.get("tool_name")
            arguments = raw.get("arguments") or {}
            if tool_name not in _IMPLEMENTED_TOOLS:
                raise StorageConflictError("atelier_agent_loop_tool_not_implemented")
            if skill_tools is not None and tool_name not in skill_tools:
                raise StorageConflictError("atelier_product_skill_tool_not_allowed")
            if project.agent_policy.allowed_tools and tool_name not in project.agent_policy.allowed_tools:
                raise StorageConflictError("atelier_agent_loop_tool_not_allowed")
            call_id = self._id_factory()
            if tool_name == _READ_TOOL:
                calls.append(AtelierAgentToolCall(call_id=call_id, tool_name=tool_name, arguments=dict(arguments), status=AtelierAgentToolStatus.PROPOSED))
                continue
            approval_id = self._id_factory()
            if tool_name == _GENERATION_TOOL:
                if self._credential_provider is None:
                    raise StorageConflictError("atelier_agent_loop_generation_credentials_missing")
                client_request_id = self._id_factory()
                calls.append(prepare_agent_generation_call(
                    project, arguments,
                    turn_id=turn.id,
                    call_id=call_id,
                    approval_id=approval_id,
                    client_request_id=client_request_id,
                    expected_revision=project.revision,
                    media_scope=self.storage._media_write_scope(),
                    credential=self._credential_provider,
                    server_storage_identity=self._server_storage_identity,
                    clock=self._clock,
                ))
                continue
            binding = build_agent_canvas_binding(
                project,
                tool_name=tool_name,
                arguments=arguments,
                turn_id=turn.id,
                call_id=call_id,
                approval_id=approval_id,
                server_storage_identity=self._server_storage_identity,
                created_at=self._clock(),
            )
            target_key = binding.canvas_authorization_ref.target_fingerprint
            if target_key in mutation_targets:
                raise StorageConflictError("atelier_agent_loop_conflicting_canvas_batch")
            mutation_targets.add(target_key)
            calls.append(AtelierAgentToolCall(
                call_id=call_id,
                tool_name=tool_name,
                arguments=dict(arguments),
                status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
                approval_required=True,
                approval_granted=False,
                canvas_approval_binding=binding,
                created_at=binding.created_at,
            ))
        return calls


def _find_turn(project: AtelierProject, turn_id: str) -> AtelierAgentTurn:
    turn = next((item for item in project.agent_turns if item.id == turn_id), None)
    if turn is None or turn.loop_start_context is None:
        raise AgentLoopNotFound("atelier_agent_loop_turn_missing")
    return turn


def _terminal(turn: AtelierAgentTurn) -> bool:
    return turn.status in {"completed", "failed", "canceled", "cancelled"}


def _generation_ack(project: AtelierProject, client_request_id: str, *, replayed: bool) -> GenerationAdmissionAck:
    from copy import deepcopy

    receipt = next(receipt for receipt in project.generation_request_receipts if receipt.client_request_id == client_request_id)
    members = receipt.receipt_members
    if len(members) != 1 or receipt.schema_version != 1:
        raise StorageConflictError("atelier_agent_loop_generation_batch_not_supported")
    member = members[0]
    attempt = next(attempt for attempt in project.generation_attempts if attempt.attempt_id == member.attempt_id)
    node = next((node for node in project.nodes if node.id == receipt.node_id), None)
    candidate = None
    if node is not None:
        for field in ("candidates", "deleted_candidates"):
            values = node.data.get(field, [])
            if isinstance(values, list):
                candidate = next((item for item in values if isinstance(item, dict) and item.get("id") == member.candidate_id), None)
                if candidate is not None:
                    break
    return GenerationAdmissionAck(
        project=project, receipt=receipt, attempt=attempt, candidate=deepcopy(candidate),
        replayed=replayed, new_candidate_ids=[] if replayed else [member.candidate_id],
        reason="generation_owner_missing" if node is None else None,
    )


def _blocking_generation(project: AtelierProject, turn: AtelierAgentTurn) -> tuple[AtelierAgentToolCall, GenerationAdmissionAck] | None:
    for call in turn.tool_calls:
        if call.tool_name != _GENERATION_TOOL or not isinstance(call.result_snapshot, dict):
            continue
        client_request_id = call.result_snapshot.get("client_request_id")
        if not isinstance(client_request_id, str):
            continue
        generation = _generation_ack(project, client_request_id, replayed=True)
        if generation.attempt.status not in _TERMINAL_ATTEMPT_STATUSES:
            return call, generation
    return None


def _call_schedule_error(call: AtelierAgentToolCall) -> str | None:
    if isinstance(call.result_snapshot, dict):
        value = call.result_snapshot.get("schedule_error")
        return value if isinstance(value, str) else None
    return None


def _event(event_type: AgentLoopEventType, turn_id: str, **kwargs) -> AgentLoopTraceEvent:
    return AgentLoopTraceEvent(type=event_type, turn_id=turn_id, **kwargs)


def _stream_event(event_type: AgentLoopEventType, project_id: str, turn_id: str, **kwargs) -> AgentLoopEvent:
    return AgentLoopEvent(type=event_type, project_id=project_id, turn_id=turn_id, **kwargs)


def _cancelled(cancel_event: Event | None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def _drain_ack(events: Iterator[AgentLoopEvent]) -> AgentLoopAck:
    last_ack: AgentLoopAck | None = None
    for event in events:
        if event.ack is not None:
            last_ack = event.ack
    if last_ack is None:
        raise StorageConflictError("atelier_agent_loop_no_ack")
    return last_ack
