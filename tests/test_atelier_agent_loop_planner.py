"""Production deterministic loop planning against supplied committed snapshots."""

import itertools

import pytest

from src.apps.atelier.agent_loop import AtelierAgentLoopService, LoopPlannerContext
from src.apps.atelier.agent_loop_planner import plan_deterministic_loop
from src.apps.atelier.agent_service import plan_for_project
from src.apps.atelier.models import AtelierAgentToolCall, AtelierAgentToolStatus
from src.models.provider_tasks import ProviderTaskStatus
from src.utils.json_storage import StorageConflictError
from src.utils.storage_identity import initialize_storage_identity
from tests.test_atelier_agent_loop_generation import _approval, _mark_observed, _start

pytest_plugins = ("tests.test_atelier_agent_turns",)


def _context(store, **kwargs):
    project, _ = store.start_agent_loop_turn("p", _start(), now=2.0)
    turn = project.agent_turns[0].model_copy(update=kwargs)
    return LoopPlannerContext(project=project, turn=turn, messages_history=list(turn.messages_history))


def _read_call():
    return AtelierAgentToolCall(call_id="read", tool_name="canvas.readProject", status=AtelierAgentToolStatus.COMPLETED)


def test_initial_planner_only_proposes_read_without_mutation_or_credentials(app):
    _, store, bind, scheduled, _, _ = app
    context = _context(store)
    before = context.project.model_dump(mode="json")
    plan = plan_deterministic_loop(context)
    assert plan.tool_calls == [{"tool_name": "canvas.readProject", "arguments": {}}]
    assert plan.stop_reason is None
    assert context.project.model_dump(mode="json") == before
    assert bind.call_count == 0 and scheduled == []


def test_after_read_reuses_snapshot_proposal_with_exact_start_intent(app):
    _, store, bind, scheduled, _, _ = app
    context = _context(store, tool_calls=[_read_call()])
    before = store.path.read_bytes()
    expected = plan_for_project(context.project, user_message=context.turn.user_message, selected_node_id="video")
    plan = plan_deterministic_loop(context)
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0]["tool_name"] == expected.tool_calls[0]["tool_name"]
    assert plan.tool_calls[0]["arguments"] == expected.tool_calls[0]["arguments"]
    assert store.path.read_bytes() == before
    assert bind.call_count == 0 and scheduled == []


def test_missing_first_frame_stops_with_explanation_and_no_generation(app):
    _, store, bind, scheduled, _, _ = app
    context = _context(store, tool_calls=[_read_call()])
    context.project.nodes[1].data = {}
    plan = plan_deterministic_loop(context)
    assert plan.stop_reason == "blocked"
    assert "first frame" in plan.assistant_message
    assert plan.tool_calls == [] and bind.call_count == 0 and scheduled == []


def test_snapshot_helper_matches_existing_proposal_and_does_not_need_storage(app):
    _, store, _, _, _, _ = app
    project = store.get_project("p")
    store.close()
    plan = plan_for_project(project, user_message="generate video", selected_node_id="video")
    assert plan.status == "ready"
    assert plan.tool_calls[0]["arguments"]["prompt"] == "Slow sunrise"


def test_existing_generation_without_terminal_evidence_never_reproposes(app):
    _, store, _, _, _, _ = app
    call = AtelierAgentToolCall(call_id="generation", tool_name="generation.createVideoCandidates", result_snapshot={"status": "completed", "attempt_id": "missing"})
    context = _context(store, tool_calls=[_read_call(), call])
    with pytest.raises(StorageConflictError, match="generation_evidence"):
        plan_deterministic_loop(context)


def test_actual_loop_uses_read_proposal_approval_and_typed_failed_observation_once(app):
    _, store, bind, scheduled, _, root = app
    loop = AtelierAgentLoopService(
        store, planner=plan_deterministic_loop,
        clock=itertools.count(10.0).__next__, id_factory=(f"planner-{i}" for i in itertools.count()).__next__,
        server_storage_identity=initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"],
        credential_provider=bind, generation_scheduler=lambda p, a: scheduled.append((p, a)),
    )
    paused = loop.start_loop("p", _start())
    assert paused.turn.status == "waiting_approval" and paused.turn.iteration_count == 2
    assert [call.tool_name for call in paused.turn.tool_calls] == ["canvas.readProject", "generation.createVideoCandidates"]
    store.record_agent_approval("p", _approval(paused), decided_at=20.0)
    waiting = loop.resume_loop("p", paused.turn.id)
    assert waiting.generation is not None and len(scheduled) == 1
    context = LoopPlannerContext(project=waiting.project, turn=waiting.turn, messages_history=waiting.turn.messages_history)
    with pytest.raises(StorageConflictError, match="generation_not_terminal"):
        plan_deterministic_loop(context)
    _mark_observed(store, waiting.generation.attempt, status="failed", provider_status=ProviderTaskStatus.FAILED, now=30.0)
    finished = loop.resume_loop("p", paused.turn.id)
    assert finished.turn.status == "completed" and finished.turn.iteration_count == 3
    assert "failed" in finished.turn.response
    assert len(scheduled) == 1 and len(finished.project.generation_attempts) == 1
    assert not finished.project.nodes[1].data.get("selected_candidate_id")
    assert loop.resume_loop("p", paused.turn.id).replayed is True
