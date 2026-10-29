"""Synchronous Atelier Agent turn executor entrypoints."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...models.provider_recovery import ProviderRecoveryError
from ...models.provider_tasks import ResolvedCredential
from ...utils.json_storage import StorageConflictError
from ...utils.request_identity import fingerprint_request
from ...utils.storage_identity import initialize_storage_identity
from .agent_commands import AdmitApprovedAgentGenerationCommand, RecordAgentApprovalCommand
from .agent_generation_preparation import prepare_agent_generation_call
from .agent_service import AtelierAgentReadOnlyService
from .agent_tools import GENERATION_PROPOSAL_CONTRACT_VERSION
from .agent_turn_commands import CancelAgentTurnCommand, StartAgentTurnCommand
from .generation_admission import GenerationAdmissionAck, _ack as _generation_ack
from .models import AtelierAgentToolCall, AtelierAgentToolStatus, AtelierAgentTurn, AtelierProject
from .storage import AtelierStorage

_AGENT_TURN_START_ENTRYPOINT = "atelier.agent.turn.start.v1"
_GENERATION_TOOL = "generation.createVideoCandidates"


class AgentTurnNotFound(ValueError):
    """Requested Agent turn does not exist in the project."""


class AgentStartRejected(ValueError):
    """Agent start request failed before any turn or generation was saved."""


class AgentExecutorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "turn_start_replayed",
        "turn_preview_prepared",
        "turn_pending_saved",
        "approval_recorded",
        "generation_admitted",
        "generation_schedule_requested",
        "generation_schedule_failed",
        "turn_cancelled",
    ]
    project_id: str
    turn_id: str
    call_id: str | None = None
    approval_id: str | None = None
    attempt_id: str | None = None
    candidate_id: str | None = None
    message: str | None = None


class AgentTurnAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    turn: AtelierAgentTurn
    call: AtelierAgentToolCall | None = None
    generation: GenerationAdmissionAck | None = None
    events: list[AgentExecutorEvent] = Field(default_factory=list)
    replayed: bool = False
    scheduled: bool = False
    schedule_error: str | None = None


class AgentStreamEvent(BaseModel):
    """Stream-only event envelope for live Agent turn execution."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "turn_start_replayed",
        "turn_planning_started",
        "turn_plan_ready",
        "turn_preview_prepared",
        "turn_pending_saved",
        "approval_recorded",
        "generation_admitted",
        "generation_schedule_requested",
        "generation_schedule_failed",
        "turn_cancelled",
        "turn_terminal",
        "turn_closed",
    ]
    project_id: str
    turn_id: str | None = None
    call_id: str | None = None
    approval_id: str | None = None
    attempt_id: str | None = None
    candidate_id: str | None = None
    message: str | None = None
    ack: AgentTurnAck | None = None


def fingerprint_agent_turn_start(*, client_turn_id: str, user_message: str, selected_node_id: str | None) -> str:
    return fingerprint_request(
        {
            "client_turn_id": client_turn_id,
            "user_message": user_message,
            "selected_node_id": selected_node_id,
        },
        entrypoint=_AGENT_TURN_START_ENTRYPOINT,
    )


class AtelierAgentExecutor:
    """Compose readonly planning, preview binding, approval and admission."""

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        credential_provider: Callable[[], ResolvedCredential],
        generation_scheduler: Callable[[str, str], None] | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        server_storage_identity: Callable[[], str] | str | None = None,
    ):
        self.storage = storage
        self._credential_provider = credential_provider
        self._generation_scheduler = generation_scheduler
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._server_storage_identity = server_storage_identity
        self._default_storage_identity_value: str | None = None
        self._default_storage_identity_lock = threading.Lock()
        self._readonly = AtelierAgentReadOnlyService(storage)
        self._start_lock = threading.Lock()

    def start_turn(self, project_id: str, command: StartAgentTurnCommand) -> AgentTurnAck:
        with self._start_lock:
            return _drain_terminal_ack(self.iter_start_turn(project_id, command))

    def iter_start_turn(
        self,
        project_id: str,
        command: StartAgentTurnCommand,
        *,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[AgentStreamEvent]:
        fingerprint = fingerprint_agent_turn_start(
            client_turn_id=command.client_turn_id,
            user_message=command.user_message,
            selected_node_id=command.selected_node_id,
        )
        replay = self._read_existing_start(project_id, command.client_turn_id, fingerprint)
        if replay is not None:
            yield _stream_event("turn_start_replayed", project_id, turn_id=replay.turn.id)
            yield _terminal_event(project_id, replay)
            return
        yield _stream_event("turn_planning_started", project_id, turn_id=command.client_turn_id)
        if _cancelled(cancel_event):
            yield _stream_event("turn_closed", project_id, turn_id=command.client_turn_id, message="agent_stream_cancelled")
            return
        try:
            plan = self._readonly.plan(
                project_id,
                user_message=command.user_message,
                selected_node_id=command.selected_node_id,
            )
            if plan.status != "ready" or not plan.tool_calls:
                raise AgentStartRejected(plan.reason or "Atelier Agent planner is blocked")
            proposal = _generation_proposal(plan.tool_calls[0])
        except AgentStartRejected:
            raise
        except ValueError as exc:
            raise AgentStartRejected("Atelier Agent start request could not be prepared") from exc
        yield _stream_event("turn_plan_ready", project_id, turn_id=command.client_turn_id)
        if _cancelled(cancel_event):
            yield _stream_event("turn_closed", project_id, turn_id=command.client_turn_id, message="agent_stream_cancelled")
            return
        call_id = self._id_factory()
        approval_id = self._id_factory()
        client_request_id = self._id_factory()
        project = self.storage.get_project(project_id)
        try:
            prepared_call = prepare_agent_generation_call(
                project, proposal,
                turn_id=command.client_turn_id,
                call_id=call_id,
                approval_id=approval_id,
                client_request_id=client_request_id,
                expected_revision=command.expected_revision,
                media_scope=self.storage._media_write_scope(),
                credential=self._credential_provider,
                server_storage_identity=self._resolve_server_storage_identity(),
                clock=self._clock,
            )
        except ProviderRecoveryError:
            raise
        except ValueError as exc:
            raise AgentStartRejected("Atelier Agent start request could not be prepared") from exc
        yield _stream_event("turn_preview_prepared", project_id, turn_id=command.client_turn_id, call_id=call_id, approval_id=approval_id)
        if _cancelled(cancel_event):
            yield _stream_event("turn_closed", project_id, turn_id=command.client_turn_id, call_id=call_id, approval_id=approval_id, message="agent_stream_cancelled")
            return
        turn = AtelierAgentTurn(
            id=command.client_turn_id,
            project_id=project_id,
            user_message=command.user_message,
            status="waiting_approval",
            tool_calls=[prepared_call],
            start_request_fingerprint=fingerprint,
            created_at=prepared_call.created_at,
        )
        try:
            saved = self.storage.create_agent_turn(project_id, turn, expected_revision=command.expected_revision)
        except StorageConflictError:
            replay = self._read_existing_start(project_id, command.client_turn_id, fingerprint)
            if replay is not None:
                yield _stream_event("turn_start_replayed", project_id, turn_id=replay.turn.id)
                yield _terminal_event(project_id, replay)
                return
            raise
        saved_turn = _find_turn(saved, command.client_turn_id)
        saved_call = _find_call(saved_turn, call_id)
        ack = AgentTurnAck(
            project=saved,
            turn=saved_turn,
            call=saved_call,
            events=[
                _event("turn_preview_prepared", project_id, saved_turn.id, call_id=call_id, approval_id=approval_id),
                _event("turn_pending_saved", project_id, saved_turn.id, call_id=call_id, approval_id=approval_id),
            ],
        )
        yield _stream_event("turn_pending_saved", project_id, turn_id=saved_turn.id, call_id=call_id, approval_id=approval_id)
        yield _terminal_event(project_id, ack)

    def read_turn(self, project_id: str, turn_id: str) -> AgentTurnAck:
        project = self.storage.get_project(project_id)
        turn = _find_turn_or_none(project, turn_id)
        if turn is None:
            raise AgentTurnNotFound("Atelier Agent turn not found")
        return AgentTurnAck(project=project, turn=turn, call=_primary_call(turn))

    def approve_call(self, project_id: str, command: RecordAgentApprovalCommand) -> AgentTurnAck:
        return _drain_terminal_ack(self.iter_approve_call(project_id, command))

    def iter_approve_call(
        self,
        project_id: str,
        command: RecordAgentApprovalCommand,
        *,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[AgentStreamEvent]:
        if _cancelled(cancel_event):
            yield _stream_event("turn_closed", project_id, turn_id=command.turn_id, call_id=command.call_id, approval_id=command.approval_id, message="agent_stream_cancelled")
            return
        try:
            project = self.storage.record_agent_approval(project_id, command, decided_at=self._clock())
            replayed = False
        except StorageConflictError:
            replay = self._read_existing_approval(project_id, command)
            if replay is None:
                raise
            project, replayed = replay, True
        turn = _find_turn(project, command.turn_id)
        call = _find_call(turn, command.call_id)
        ack = AgentTurnAck(
            project=project,
            turn=turn,
            call=call,
            events=[_event("approval_recorded", project_id, turn.id, call_id=call.call_id, approval_id=command.approval_id)],
            replayed=replayed,
        )
        yield _stream_event("approval_recorded", project_id, turn_id=turn.id, call_id=call.call_id, approval_id=command.approval_id)
        yield _terminal_event(project_id, ack)

    def continue_call(self, project_id: str, command: AdmitApprovedAgentGenerationCommand) -> AgentTurnAck:
        return _drain_terminal_ack(self.iter_continue_call(project_id, command))

    def iter_continue_call(
        self,
        project_id: str,
        command: AdmitApprovedAgentGenerationCommand,
        *,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[AgentStreamEvent]:
        if _cancelled(cancel_event):
            yield _stream_event("turn_closed", project_id, turn_id=command.turn_id, call_id=command.call_id, approval_id=command.approval_id, message="agent_stream_cancelled")
            return
        project, replayed = self.storage.apply_agent_generation_admission(project_id, command, now=self._clock())
        generation = _generation_ack(project, command.client_request_id, replayed=replayed)
        turn = _find_turn(project, command.turn_id)
        call = _find_call(turn, command.call_id)
        events = [_event("generation_admitted", project_id, turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id)]
        if replayed:
            ack = AgentTurnAck(
                project=project,
                turn=turn,
                call=call,
                generation=generation,
                events=events,
                replayed=True,
                scheduled=False,
                schedule_error=None,
            )
            yield _terminal_event(project_id, ack)
            return
        yield _stream_event("generation_admitted", project_id, turn_id=turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id)
        scheduled = False
        schedule_error = None
        if generation.new_candidate_ids:
            if _cancelled(cancel_event):
                schedule_error = "generation_schedule_cancelled"
                events.append(_event("generation_schedule_failed", project_id, turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error))
                yield _stream_event("generation_schedule_failed", project_id, turn_id=turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error)
            elif self._generation_scheduler is None:
                schedule_error = "generation_schedule_unavailable"
                events.append(_event("generation_schedule_failed", project_id, turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error))
                yield _stream_event("generation_schedule_failed", project_id, turn_id=turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error)
            else:
                try:
                    schedule_result = self._generation_scheduler(project_id, generation.attempt.attempt_id)
                    if schedule_result is False:
                        schedule_error = "generation_schedule_not_accepted"
                        events.append(_event("generation_schedule_failed", project_id, turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error))
                        yield _stream_event("generation_schedule_failed", project_id, turn_id=turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error)
                    else:
                        scheduled = True
                        events.append(_event("generation_schedule_requested", project_id, turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id))
                        yield _stream_event("generation_schedule_requested", project_id, turn_id=turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id)
                except Exception:  # noqa: BLE001 - scheduler failure must not leak internals or roll back durable admission
                    schedule_error = "generation_schedule_failed"
                    events.append(_event("generation_schedule_failed", project_id, turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error))
                    yield _stream_event("generation_schedule_failed", project_id, turn_id=turn.id, call_id=call.call_id, approval_id=command.approval_id, attempt_id=generation.attempt.attempt_id, candidate_id=generation.receipt.candidate_id, message=schedule_error)
        ack = AgentTurnAck(
            project=project,
            turn=turn,
            call=call,
            generation=generation,
            events=events,
            replayed=False,
            scheduled=scheduled,
            schedule_error=schedule_error,
        )
        yield _terminal_event(project_id, ack)

    def cancel_turn(self, project_id: str, command: CancelAgentTurnCommand) -> AgentTurnAck:
        project = self.storage.cancel_agent_turn(project_id, command.turn_id, cancelled_at=self._clock())
        turn = _find_turn(project, command.turn_id)
        return AgentTurnAck(
            project=project,
            turn=turn,
            call=_primary_call(turn),
            events=[_event("turn_cancelled", project_id, turn.id)],
        )

    def _read_existing_start(self, project_id: str, turn_id: str, fingerprint: str) -> AgentTurnAck | None:
        project = self.storage.get_project(project_id)
        turn = _find_turn_or_none(project, turn_id)
        if turn is None:
            return None
        if turn.start_request_fingerprint != fingerprint:
            raise StorageConflictError("Atelier Agent turn id already belongs to different content")
        return AgentTurnAck(
            project=project,
            turn=turn,
            call=_primary_call(turn),
            replayed=True,
            events=[_event("turn_start_replayed", project_id, turn.id)],
        )

    def _read_existing_approval(self, project_id: str, command: RecordAgentApprovalCommand) -> AtelierProject | None:
        if command.decision != "approved":
            return None
        project = self.storage.get_project(project_id)
        turn = _find_turn_or_none(project, command.turn_id)
        if turn is None:
            return None
        call = next((item for item in turn.tool_calls if item.call_id == command.call_id), None)
        record = call.approval_record if call is not None else None
        binding = call.preview_binding if call is not None else None
        ref = binding.agent_authorization_ref if binding is not None else None
        if (
            record is not None
            and ref is not None
            and record.decision == "approved"
            and record.approval_id == command.approval_id
            and record.authorization_hash == command.authorization_hash
            and ref.approval_id == command.approval_id
            and ref.authorization_hash == command.authorization_hash
            and call.approval_granted is True
            and turn.status not in {"canceled", "cancelled", "failed"}
            and call.status not in {AtelierAgentToolStatus.DENIED, AtelierAgentToolStatus.FAILED}
        ):
            return project
        return None

    def _resolve_server_storage_identity(self) -> str:
        if self._server_storage_identity is None:
            with self._default_storage_identity_lock:
                if self._default_storage_identity_value is None:
                    self._default_storage_identity_value = _default_storage_identity(self.storage)
                return self._default_storage_identity_value
        value = self._server_storage_identity() if callable(self._server_storage_identity) else self._server_storage_identity
        if not isinstance(value, str) or not value:
            raise ValueError("Atelier Agent executor requires a server storage identity")
        return value


def _generation_proposal(call: dict) -> dict:
    if call.get("tool_name") != _GENERATION_TOOL or call.get("contract_version") != GENERATION_PROPOSAL_CONTRACT_VERSION:
        raise ValueError("Atelier Agent planner returned an unsupported tool call")
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("Atelier Agent planner returned invalid generation arguments")
    return deepcopy(arguments)


def _find_turn(project: AtelierProject, turn_id: str) -> AtelierAgentTurn:
    turn = _find_turn_or_none(project, turn_id)
    if turn is None:
        raise AgentTurnNotFound("Atelier Agent turn not found")
    return turn


def _find_turn_or_none(project: AtelierProject, turn_id: str) -> AtelierAgentTurn | None:
    return next((turn for turn in project.agent_turns if turn.id == turn_id), None)


def _find_call(turn: AtelierAgentTurn, call_id: str) -> AtelierAgentToolCall:
    call = next((call for call in turn.tool_calls if call.call_id == call_id), None)
    if call is None:
        raise AgentTurnNotFound("Atelier Agent call not found")
    return call


def _primary_call(turn: AtelierAgentTurn) -> AtelierAgentToolCall | None:
    return turn.tool_calls[0] if turn.tool_calls else None


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _drain_terminal_ack(events: Iterator[AgentStreamEvent]) -> AgentTurnAck:
    terminal: AgentStreamEvent | None = None
    for event in events:
        terminal = event
    if terminal is None or terminal.type != "turn_terminal" or terminal.ack is None:
        raise RuntimeError("Atelier Agent stream ended before terminal ACK")
    return terminal.ack


def _stream_event(event_type: str, project_id: str, **kwargs) -> AgentStreamEvent:
    return AgentStreamEvent(type=event_type, project_id=project_id, **kwargs)


def _terminal_event(project_id: str, ack: AgentTurnAck) -> AgentStreamEvent:
    return AgentStreamEvent(type="turn_terminal", project_id=project_id, turn_id=ack.turn.id, ack=ack)


def _event(event_type: str, project_id: str, turn_id: str, **kwargs) -> AgentExecutorEvent:
    return AgentExecutorEvent(type=event_type, project_id=project_id, turn_id=turn_id, **kwargs)


def _default_storage_identity(storage: AtelierStorage) -> str:
    identity = initialize_storage_identity(storage.data_dir, writer_lock=storage._writer_lock)
    storage_id = identity.get("storage_id")
    if not isinstance(storage_id, str) or not storage_id:
        raise ValueError("Atelier Agent executor requires a server storage identity")
    return storage_id
