"""Loop-owned generation approval/admission stays durable and recoverable."""

import itertools

import pytest

from src.apps.atelier.agent_authorization import TOOL_GENERATION_CREATE_VIDEO_CANDIDATES
from src.apps.atelier.agent_canvas_authorization import TOOL_CANVAS_CREATE_VIDEO_NODE, build_agent_canvas_binding
from src.apps.atelier.agent_canvas_commands import ApplyApprovedAgentCanvasCommand, RecordAgentCanvasApprovalCommand
from src.apps.atelier.agent_commands import AdmitApprovedAgentGenerationCommand, RecordAgentApprovalCommand
from src.apps.atelier.agent_loop import AtelierAgentLoopService, DeterministicLoopPlan
from src.apps.atelier.agent_loop_commands import SaveAgentLoopIterationCommand, StartAgentLoopCommand
from src.apps.atelier.agent_generation_preparation import prepare_agent_generation_call
from src.apps.atelier.agent_turn_commands import StartAgentTurnCommand
from src.apps.atelier.models import AtelierAgentApprovalRecord, AtelierAgentToolCall, AtelierAgentToolStatus
from src.models.provider_recovery import record_with_changes
from src.models.provider_tasks import ProviderHandle, ProviderObservation, ProviderScope, ProviderTaskStatus, QueryCapability
from src.utils.json_storage import StorageConflictError
from src.utils.storage_identity import initialize_storage_identity

pytest_plugins = ("tests.test_atelier_agent_turns",)


def _proposal():
    return {
        "node_id": "video",
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"},
    }


def _start(expected_revision=0, turn_id="loop-turn"):
    return StartAgentLoopCommand(
        expected_revision=expected_revision,
        client_turn_id=turn_id,
        user_message="生成视频候选",
        selected_node_id="video",
        max_iterations=3,
    )




def _standalone_start(expected_revision=0, turn_id="standalone-turn"):
    return StartAgentTurnCommand(
        expected_revision=expected_revision,
        client_turn_id=turn_id,
        user_message="生成视频候选",
        selected_node_id="video",
    )


def _mark_observed(store, attempt, *, status: str, provider_status: ProviderTaskStatus, now: float):
    handle = ProviderHandle(
        provider_model_id=attempt.provider_execution.provider_model_id,
        task_id=f"task-{attempt.attempt_id}",
        scope=ProviderScope.from_dict(attempt.provider_execution.scope),
        credential_ref=attempt.provider_execution.credential_ref,
        query_capability=QueryCapability.BY_TASK_ID,
    )
    project, attempt = store.update_generation_attempt(
        "p",
        attempt_id=attempt.attempt_id,
        expected_attempt_revision=attempt.attempt_revision,
        execution_fingerprint=attempt.provider_execution.fingerprint,
        command=lambda item: (
            setattr(item, "status", "processing"),
            setattr(item, "provider_execution", record_with_changes(item.provider_execution, phase="submitting")),
            setattr(item, "updated_at", now),
        ),
    )
    project, attempt = store.update_generation_attempt(
        "p",
        attempt_id=attempt.attempt_id,
        expected_attempt_revision=attempt.attempt_revision,
        execution_fingerprint=attempt.provider_execution.fingerprint,
        command=lambda item: (
            setattr(item, "status", "processing"),
            setattr(item, "provider_execution", record_with_changes(item.provider_execution, phase="submitted", handle=handle.to_dict())),
            setattr(item, "updated_at", now + 1),
        ),
    )
    observation = ProviderObservation(
        task_id=handle.task_id,
        status=provider_status,
        raw_status=provider_status.value,
        error_code="provider_failed" if provider_status is ProviderTaskStatus.FAILED else None,
        source_handle=handle,
    )
    project, attempt = store.update_generation_attempt(
        "p",
        attempt_id=attempt.attempt_id,
        expected_attempt_revision=attempt.attempt_revision,
        execution_fingerprint=attempt.provider_execution.fingerprint,
        command=lambda item: (
            setattr(item, "status", status),
            setattr(item, "provider_execution", record_with_changes(
                item.provider_execution, phase="observed", handle=handle.to_dict(), observation=observation.to_dict(),
            )),
            setattr(item, "error_code", "provider_failed" if status == "failed" else None),
            setattr(item, "updated_at", now + 2),
        ),
    )
    return project, attempt

def _approval(ack, *, decision="approved", expected_revision=None):
    call = next(call for call in reversed(ack.turn.tool_calls) if call.preview_binding is not None)
    ref = call.preview_binding.agent_authorization_ref
    return RecordAgentApprovalCommand(
        expected_revision=ack.project.revision if expected_revision is None else expected_revision,
        turn_id=ack.turn.id,
        call_id=call.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        decision=decision,
    )


def _admit_command(project, turn_id="loop-turn"):
    turn = next(turn for turn in project.agent_turns if turn.id == turn_id)
    call = next(call for call in turn.tool_calls if call.tool_name == TOOL_GENERATION_CREATE_VIDEO_CANDIDATES)
    ref = call.preview_binding.agent_authorization_ref
    return AdmitApprovedAgentGenerationCommand(
        expected_revision=project.revision,
        turn_id=turn.id,
        call_id=call.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        client_request_id=call.preview_binding.preview_public_record["raw_request"]["client_request_id"],
    )


def _loop(store, bind, scheduled, root, plans, *, scheduler=None):
    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]
    calls = iter(plans)

    def planner(ctx):
        return next(calls)

    return AtelierAgentLoopService(
        store,
        planner=planner,
        clock=itertools.count(10.0).__next__,
        id_factory=(f"id-{idx}" for idx in itertools.count(1)).__next__,
        server_storage_identity=identity,
        credential_provider=bind,
        generation_scheduler=scheduler if scheduler is not None else (lambda project_id, attempt_id: scheduled.append((project_id, attempt_id))),
    )


def test_storage_rejects_direct_generation_proposal_binding_mismatch_and_prefilled_result(app):
    _executor, store, bind, _scheduled, _image, root = app
    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]
    started, _ = store.start_agent_loop_turn("p", _start(), now=10.0)
    before = store.path.read_bytes()
    wrong_turn_call = prepare_agent_generation_call(
        store.get_project("p"),
        _proposal(),
        turn_id="other-turn",
        call_id="call-1",
        approval_id="approval-1",
        client_request_id="request-1",
        expected_revision=started.revision,
        media_scope=store._media_write_scope(),
        credential=bind,
        server_storage_identity=identity,
        clock=lambda: 11.0,
    )
    with pytest.raises(StorageConflictError, match="binding_mismatch"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=started.revision,
            turn_id="loop-turn",
            iteration_index=1,
            assistant_message="bad",
            tool_calls=(wrong_turn_call,),
        ), now=12.0)
    assert store.path.read_bytes() == before

    correct_call = prepare_agent_generation_call(
        store.get_project("p"),
        _proposal(),
        turn_id="loop-turn",
        call_id="call-2",
        approval_id="approval-2",
        client_request_id="request-2",
        expected_revision=started.revision,
        media_scope=store._media_write_scope(),
        credential=bind,
        server_storage_identity=identity,
        clock=lambda: 12.0,
    ).model_copy(update={"result_snapshot": {"ghost": True}})
    with pytest.raises(StorageConflictError, match="invalid_generation_call"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=started.revision,
            turn_id="loop-turn",
            iteration_index=1,
            assistant_message="bad",
            tool_calls=(correct_call,),
        ), now=13.0)
    assert store.path.read_bytes() == before


def test_loop_generation_preview_saves_zero_attempts_and_waits_for_approval(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
    ])

    ack = loop.start_loop("p", _start())

    assert ack.turn.status == "waiting_approval"
    assert ack.project.generation_attempts == []
    assert ack.project.generation_request_receipts == []
    assert ack.turn.pending_tool_calls == [{
        "call_id": ack.turn.tool_calls[0].call_id,
        "tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
        "requires_approval": True,
        "approval_id": ack.turn.tool_calls[0].preview_binding.agent_authorization_ref.approval_id,
        "authorization_hash": ack.turn.tool_calls[0].preview_binding.agent_authorization_ref.authorization_hash,
    }]
    assert scheduled == []
    assert bind.call_count == 1


def test_loop_generation_approval_admits_once_schedules_new_candidate_and_pauses_until_terminal(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
    ])
    preview = loop.start_loop("p", _start())
    approved = store.record_agent_approval("p", _approval(preview), decided_at=30.0)

    ack = loop.resume_loop("p", "loop-turn")

    assert len(ack.project.generation_attempts) == 1
    attempt = ack.project.generation_attempts[0]
    assert attempt.provider_execution.phase == "accepted"
    assert ack.turn.status == "pending"
    assert ack.turn.pending_tool_calls == []
    assert ack.turn.tool_calls[0].status.value == "completed"
    assert ack.turn.messages_history[-1]["tool_call_id"] == ack.turn.tool_calls[0].call_id
    assert ack.events[-1]["type"] == "generation_waiting"
    assert ack.scheduled is True
    assert ack.schedule_error is None
    assert scheduled == [("p", attempt.attempt_id)]
    assert bind.call_count == 1

    replay = loop.resume_loop("p", "loop-turn")
    assert replay.generation.attempt.attempt_id == attempt.attempt_id
    assert replay.scheduled is False
    assert replay.schedule_error is None
    assert scheduled == [("p", attempt.attempt_id)]
    assert len([m for m in replay.turn.messages_history if m.get("tool_call_id") == ack.turn.tool_calls[0].call_id]) == 1

    stale = _admit_command(approved, turn_id="loop-turn").model_copy(update={"expected_revision": 0})
    project, replayed = store.apply_agent_loop_generation_admission("p", stale, now=99.0)
    assert replayed is True
    assert project == replay.project


def test_loop_generation_scheduler_failure_is_durable_and_not_retried(app):
    _executor, store, bind, scheduled, _image, root = app
    attempts: list[str] = []
    observed: list[list[dict]] = []

    def scheduler(project_id, attempt_id):
        attempts.append(attempt_id)
        return False

    plans = iter([
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
        DeterministicLoopPlan("report scheduling failure", [], stop_reason="schedule_failed"),
    ])

    def planner(ctx):
        observed.append(list(ctx.messages_history))
        return next(plans)

    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]
    loop = AtelierAgentLoopService(
        store,
        planner=planner,
        clock=itertools.count(10.0).__next__,
        id_factory=(f"id-{idx}" for idx in itertools.count(1)).__next__,
        server_storage_identity=identity,
        credential_provider=bind,
        generation_scheduler=scheduler,
    )
    preview = loop.start_loop("p", _start())
    store.record_agent_approval("p", _approval(preview), decided_at=30.0)

    ack = loop.resume_loop("p", "loop-turn")
    assert len(ack.project.generation_attempts) == 1
    attempt = ack.project.generation_attempts[0]
    assert attempts == [attempt.attempt_id]
    assert ack.scheduled is False
    assert ack.schedule_error == "generation_schedule_not_accepted"
    assert ack.generation.attempt.status == "pending"
    assert ack.generation.attempt.error_code is None
    assert ack.generation.attempt.provider_execution.phase == "accepted"
    assert ack.turn.tool_calls[0].result_snapshot["schedule_error"] == "generation_schedule_not_accepted"
    assert ack.turn.messages_history[-1]["content"]["schedule_error"] == "generation_schedule_not_accepted"
    assert any(event["type"] == "generation_schedule_failed" and event["message"] == "generation_schedule_not_accepted" for event in ack.events)

    again = loop.resume_loop("p", "loop-turn")
    assert attempts == [attempt.attempt_id]
    assert again.events[-1]["type"] == "generation_waiting"
    assert again.generation.attempt.status == "pending"
    assert again.generation.attempt.provider_execution.phase == "accepted"
    assert again.turn.messages_history[-1]["content"]["schedule_error"] == "generation_schedule_not_accepted"
    assert len(observed) == 1
    assert again.scheduled is False
    assert again.schedule_error == "generation_schedule_not_accepted"



def test_loop_generation_scheduler_exception_preserves_attempt_for_recovery_and_readback(app):
    _executor, store, bind, scheduled, _image, root = app
    attempts: list[str] = []

    def scheduler(project_id, attempt_id):
        attempts.append(attempt_id)
        raise RuntimeError("after enqueue unknown")

    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
    ], scheduler=scheduler)
    preview = loop.start_loop("p", _start())
    store.record_agent_approval("p", _approval(preview), decided_at=30.0)

    ack = loop.resume_loop("p", "loop-turn")
    attempt = ack.project.generation_attempts[0]
    assert attempts == [attempt.attempt_id]
    assert ack.schedule_error == "generation_schedule_failed"
    assert ack.generation.attempt.status == "pending"
    assert ack.generation.attempt.provider_execution.phase == "accepted"
    assert ack.turn.tool_calls[0].result_snapshot["schedule_error"] == "generation_schedule_failed"

    restarted = _loop(store, bind, scheduled, root, [], scheduler=scheduler)
    readback = store.get_project("p")
    readback_turn = next(turn for turn in readback.agent_turns if turn.id == "loop-turn")
    assert readback_turn.tool_calls[0].result_snapshot["schedule_error"] == "generation_schedule_failed"
    replay = restarted.resume_loop("p", "loop-turn")
    assert attempts == [attempt.attempt_id]
    assert replay.events[-1]["type"] == "generation_waiting"
    assert replay.generation.attempt.attempt_id == attempt.attempt_id
    assert replay.generation.attempt.status == "pending"

def test_loop_generation_terminal_attempt_allows_next_planner_iteration(app):
    _executor, store, bind, scheduled, _image, root = app
    observed: list[list[dict]] = []
    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]
    plans = iter([
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
        DeterministicLoopPlan("done after terminal", [], stop_reason="done"),
    ])

    def planner(ctx):
        observed.append(list(ctx.messages_history))
        return next(plans)

    loop = AtelierAgentLoopService(
        store,
        planner=planner,
        clock=itertools.count(10.0).__next__,
        id_factory=(f"id-{idx}" for idx in itertools.count(1)).__next__,
        server_storage_identity=identity,
        credential_provider=bind,
        generation_scheduler=lambda project_id, attempt_id: scheduled.append((project_id, attempt_id)),
    )
    preview = loop.start_loop("p", _start())
    store.record_agent_approval("p", _approval(preview), decided_at=30.0)
    waiting = loop.resume_loop("p", "loop-turn")
    _mark_observed(store, waiting.project.generation_attempts[0], status="failed", provider_status=ProviderTaskStatus.FAILED, now=99.0)

    completed = loop.resume_loop("p", "loop-turn")

    assert completed.turn.status == "completed"
    assert completed.turn.response == "done"
    assert len(observed) == 2
    assert observed[1][-1]["tool_call_id"] == preview.turn.tool_calls[0].call_id


def test_loop_generation_needs_confirmation_observed_still_blocks(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
        DeterministicLoopPlan("must not run", [], stop_reason="bad"),
    ])
    preview = loop.start_loop("p", _start())
    store.record_agent_approval("p", _approval(preview), decided_at=30.0)
    waiting = loop.resume_loop("p", "loop-turn")
    _mark_observed(store, waiting.project.generation_attempts[0], status="needs_confirmation", provider_status=ProviderTaskStatus.UNKNOWN, now=99.0)

    blocked = loop.resume_loop("p", "loop-turn")

    assert blocked.turn.status == "pending"
    assert blocked.events[-1]["type"] == "generation_waiting"
    assert blocked.generation.attempt.status == "needs_confirmation"


def test_loop_generation_cancelled_provider_phase_allows_next_planner_iteration(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
        DeterministicLoopPlan("done after cancel", [], stop_reason="done"),
    ])
    preview = loop.start_loop("p", _start())
    store.record_agent_approval("p", _approval(preview), decided_at=30.0)
    waiting = loop.resume_loop("p", "loop-turn")
    attempt = waiting.project.generation_attempts[0]
    store.update_generation_attempt(
        "p",
        attempt_id=attempt.attempt_id,
        expected_attempt_revision=attempt.attempt_revision,
        execution_fingerprint=attempt.provider_execution.fingerprint,
        command=lambda item: (
            setattr(item, "status", "cancelled"),
            setattr(item, "provider_execution", record_with_changes(item.provider_execution, phase="cancelled")),
            setattr(item, "updated_at", 99.0),
        ),
    )

    completed = loop.resume_loop("p", "loop-turn")

    assert completed.turn.status == "completed"
    assert completed.turn.response == "done"


def test_loop_generation_wrong_identity_and_denial_save_no_work(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
    ])
    preview = loop.start_loop("p", _start())
    before = store.path.read_bytes()
    wrong = _admit_command(preview.project, turn_id="loop-turn").model_copy(update={"authorization_hash": "0" * 64})
    with pytest.raises(StorageConflictError):
        store.apply_agent_loop_generation_admission("p", wrong, now=20.0)
    assert store.path.read_bytes() == before
    assert store.get_project("p").generation_attempts == []

    denied = store.record_agent_approval("p", _approval(preview, decision="denied"), decided_at=30.0)
    ack = loop.resume_loop("p", "loop-turn")
    assert ack.replayed is True
    assert ack.turn.status == "failed"
    assert ack.project.generation_attempts == []
    assert scheduled == []
    assert denied.agent_turns[0].pending_tool_calls == []


def test_loop_denial_revoke_clear_all_pending_for_mixed_generation_canvas_and_read(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("mixed", [
            {"tool_name": "canvas.readProject", "arguments": {}},
            {"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()},
            {"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "new", "x": 5, "y": 6}},
        ]),
    ])
    preview = loop.start_loop("p", _start())
    project = store.get_project("p")
    turn = next(turn for turn in project.agent_turns if turn.id == "loop-turn")
    assert [call.tool_name for call in turn.tool_calls] == ["canvas.readProject", TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, TOOL_CANVAS_CREATE_VIDEO_NODE]
    assert turn.tool_calls[0].status == AtelierAgentToolStatus.COMPLETED
    assert len(turn.pending_tool_calls) == 2

    denied = store.record_agent_approval("p", _approval(preview, decision="denied", expected_revision=project.revision), decided_at=30.0)
    denied_turn = next(turn for turn in denied.agent_turns if turn.id == "loop-turn")

    assert denied_turn.status == "failed"
    assert denied_turn.pending_tool_calls == []
    assert [call.status for call in denied_turn.tool_calls] == [
        AtelierAgentToolStatus.COMPLETED,
        AtelierAgentToolStatus.DENIED,
        AtelierAgentToolStatus.FAILED,
    ]
    assert loop.resume_loop("p", "loop-turn").replayed is True
    assert scheduled == []


def test_loop_revocation_clears_all_pending_for_mixed_generation_canvas_and_read(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("mixed", [
            {"tool_name": "canvas.readProject", "arguments": {}},
            {"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()},
            {"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "new", "x": 5, "y": 6}},
        ]),
    ])
    preview = loop.start_loop("p", _start())
    project = store.record_agent_approval("p", _approval(preview, expected_revision=store.get_project("p").revision), decided_at=30.0)
    revoked = store.record_agent_approval("p", _approval(preview, decision="revoked", expected_revision=project.revision), decided_at=31.0)
    revoked_turn = next(turn for turn in revoked.agent_turns if turn.id == "loop-turn")

    assert revoked_turn.status == "canceled"
    assert revoked_turn.pending_tool_calls == []
    assert [call.status for call in revoked_turn.tool_calls] == [
        AtelierAgentToolStatus.COMPLETED,
        AtelierAgentToolStatus.DENIED,
        AtelierAgentToolStatus.FAILED,
    ]
    assert scheduled == []


def test_standalone_and_loop_generation_admission_finalizers_cannot_cross(app):
    executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
    ])
    preview = loop.start_loop("p", _start())
    approved = store.record_agent_approval("p", _approval(preview), decided_at=30.0)
    loop_command = _admit_command(approved, turn_id="loop-turn")
    before = store.path.read_bytes()
    with pytest.raises(StorageConflictError, match="loop_finalizer_required"):
        store.apply_agent_generation_admission("p", loop_command, now=40.0)
    assert store.path.read_bytes() == before

    standalone = executor.start_turn("p", _standalone_start(expected_revision=approved.revision))
    standalone = executor.approve_call("p", RecordAgentApprovalCommand(
        **{**_approval(standalone).model_dump(), "expected_revision": standalone.project.revision}
    ))
    standalone_command = AdmitApprovedAgentGenerationCommand(
        expected_revision=standalone.project.revision,
        turn_id=standalone.turn.id,
        call_id=standalone.call.call_id,
        approval_id=standalone.call.preview_binding.agent_authorization_ref.approval_id,
        authorization_hash=standalone.call.preview_binding.agent_authorization_ref.authorization_hash,
        client_request_id=standalone.call.preview_binding.preview_public_record["raw_request"]["client_request_id"],
    )
    before = store.path.read_bytes()
    with pytest.raises(StorageConflictError, match="loop_turn_missing"):
        store.apply_agent_loop_generation_admission("p", standalone_command, now=41.0)
    assert store.path.read_bytes() == before



def test_loop_generation_finalizer_keeps_waiting_approval_for_remaining_sibling(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("mixed", [
            {"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()},
            {"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "new", "x": 5, "y": 6}},
        ]),
    ])
    preview = loop.start_loop("p", _start())
    store.record_agent_approval("p", _approval(preview), decided_at=30.0)

    paused = loop.resume_loop("p", "loop-turn")

    assert paused.events[-1]["type"] == "approval_paused"
    assert paused.turn.status == "waiting_approval"
    assert len(paused.turn.pending_tool_calls) == 1
    assert paused.turn.pending_tool_calls[0]["tool_name"] == TOOL_CANVAS_CREATE_VIDEO_NODE
    assert scheduled == [("p", paused.project.generation_attempts[0].attempt_id)]


def test_storage_rejects_new_iteration_while_loop_generation_attempt_nonterminal(app):
    _executor, store, bind, scheduled, _image, root = app
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("Prepare generation", [{"tool_name": TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, "arguments": _proposal()}]),
    ])
    preview = loop.start_loop("p", _start())
    approved = store.record_agent_approval("p", _approval(preview), decided_at=30.0)
    admitted, _ = store.apply_agent_loop_generation_admission("p", _admit_command(approved), now=40.0)
    before = store.path.read_bytes()

    with pytest.raises(StorageConflictError, match="generation_not_terminal"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=admitted.revision,
            turn_id="loop-turn",
            iteration_index=2,
            assistant_message="bad",
            tool_calls=(AtelierAgentToolCall(call_id="read-after-pending", tool_name="canvas.readProject"),),
        ), now=41.0)
    assert store.path.read_bytes() == before


def test_standalone_canvas_finalizer_rejects_loop_canvas_call(app):
    _executor, store, bind, scheduled, _image, root = app
    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]
    loop = _loop(store, bind, scheduled, root, [
        DeterministicLoopPlan("canvas", [{"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "new", "x": 5, "y": 6}}]),
    ])
    paused = loop.start_loop("p", _start())
    call = paused.turn.tool_calls[0]
    ref = call.canvas_approval_binding.canvas_authorization_ref
    approved = store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(
        expected_revision=paused.project.revision,
        turn_id=paused.turn.id,
        call_id=call.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        decision="approved",
    ), decided_at=30.0)
    before = store.path.read_bytes()

    with pytest.raises(StorageConflictError, match="canvas_loop_finalizer_required"):
        store.apply_agent_canvas_command("p", ApplyApprovedAgentCanvasCommand(
            expected_revision=approved.revision,
            turn_id=paused.turn.id,
            call_id=call.call_id,
            approval_id=ref.approval_id,
            authorization_hash=ref.authorization_hash,
        ), now=31.0, server_storage_identity=identity)
    assert store.path.read_bytes() == before

def test_storage_rejects_duplicate_call_ids_and_foreign_authority_on_read_canvas(app):
    _executor, store, bind, _scheduled, _image, root = app
    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]
    started, _ = store.start_agent_loop_turn("p", _start(), now=10.0)
    duplicate = AtelierAgentToolCall(call_id="dup", tool_name="canvas.readProject", status=AtelierAgentToolStatus.PROPOSED)
    before = store.path.read_bytes()
    with pytest.raises(StorageConflictError, match="call_id_conflict"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=started.revision,
            turn_id="loop-turn",
            iteration_index=1,
            assistant_message="bad",
            tool_calls=(duplicate, duplicate.model_copy(deep=True)),
        ), now=12.0)
    assert store.path.read_bytes() == before

    generation_call = prepare_agent_generation_call(
        store.get_project("p"),
        _proposal(),
        turn_id="loop-turn",
        call_id="generation-call",
        approval_id="approval-1",
        client_request_id="request-1",
        expected_revision=started.revision,
        media_scope=store._media_write_scope(),
        credential=bind,
        server_storage_identity=identity,
        clock=lambda: 11.0,
    )
    read_with_authority = AtelierAgentToolCall(
        call_id="read-call",
        tool_name="canvas.readProject",
        status=AtelierAgentToolStatus.PROPOSED,
        preview_binding=generation_call.preview_binding,
    )
    with pytest.raises(StorageConflictError, match="invalid_read_call"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=started.revision,
            turn_id="loop-turn",
            iteration_index=1,
            assistant_message="bad",
            tool_calls=(read_with_authority,),
        ), now=13.0)
    assert store.path.read_bytes() == before

    read_with_record = AtelierAgentToolCall(
        call_id="read-record-call",
        tool_name="canvas.readProject",
        status=AtelierAgentToolStatus.PROPOSED,
        approval_record=AtelierAgentApprovalRecord(
            approval_id="approval-record", authorization_hash="0" * 64, decision="approved", decided_at=12.0,
        ),
    )
    with pytest.raises(StorageConflictError, match="invalid_read_call"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=started.revision,
            turn_id="loop-turn",
            iteration_index=1,
            assistant_message="bad",
            tool_calls=(read_with_record,),
        ), now=13.5)
    assert store.path.read_bytes() == before

    canvas_binding = build_agent_canvas_binding(
        store.get_project("p"),
        tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
        arguments={"prompt": "new", "x": 1, "y": 2},
        turn_id="loop-turn",
        call_id="canvas-call",
        approval_id="approval-2",
        server_storage_identity=identity,
        created_at=12.0,
    )
    canvas_with_foreign_authority = AtelierAgentToolCall(
        call_id="canvas-call",
        tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
        arguments={"prompt": "new", "x": 1, "y": 2},
        status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
        approval_required=True,
        canvas_approval_binding=canvas_binding,
        preview_binding=generation_call.preview_binding,
    )
    with pytest.raises(StorageConflictError, match="invalid_canvas_call"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=started.revision,
            turn_id="loop-turn",
            iteration_index=1,
            assistant_message="bad",
            tool_calls=(canvas_with_foreign_authority,),
        ), now=14.0)
    assert store.path.read_bytes() == before

    canvas_with_foreign_record = canvas_with_foreign_authority.model_copy(update={
        "preview_binding": None,
        "approval_record": AtelierAgentApprovalRecord(
            approval_id="approval-record", authorization_hash="0" * 64, decision="approved", decided_at=12.0,
        ),
    })
    with pytest.raises(StorageConflictError, match="invalid_canvas_call"):
        store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
            expected_revision=started.revision,
            turn_id="loop-turn",
            iteration_index=1,
            assistant_message="bad",
            tool_calls=(canvas_with_foreign_record,),
        ), now=14.5)
    assert store.path.read_bytes() == before
