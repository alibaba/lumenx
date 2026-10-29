"""Durable local Agent loop mechanics before HTTP/provider wiring."""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.apps.atelier.agent_canvas_authorization import TOOL_CANVAS_CREATE_VIDEO_NODE, TOOL_CANVAS_UPDATE_NODE_PROMPT
from src.apps.atelier.agent_canvas_commands import ApplyApprovedAgentCanvasCommand, RecordAgentCanvasApprovalCommand
from src.apps.atelier.agent_loop import AtelierAgentLoopService, DeterministicLoopPlan
from src.apps.atelier.agent_loop_commands import SaveAgentLoopIterationCommand, StartAgentLoopCommand, fingerprint_agent_loop_start
from src.apps.atelier.models import AtelierAgentToolCall, AtelierAgentToolStatus, AtelierAgentTurn, AtelierNode, AtelierProject
from src.apps.atelier.nodes import CreateNodeCommand, create_node
from src.apps.atelier.agent_canvas_authorization import build_agent_canvas_binding
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.request_identity import fingerprint_request


def seed(store: AtelierStorage) -> None:
    store.create_project(AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[AtelierNode(id="video-1", project_id="p", type="video", title="Old", prompt="old", updated_at=1.0)],
    ))


def add_second_video_node(store: AtelierStorage) -> None:
    revision = store.get_project("p").revision
    store.apply_project_command(
        "p",
        expected_revision=revision,
        command=lambda project: create_node(
            project,
            CreateNodeCommand(expected_revision=revision, type="video", title="Second", prompt="old two"),
            node_id_factory=lambda: "video-2",
            now=2.0,
        ),
    )


def start_command(**kwargs) -> StartAgentLoopCommand:
    return StartAgentLoopCommand(
        expected_revision=kwargs.pop("expected_revision", 0),
        client_turn_id=kwargs.pop("client_turn_id", "turn-loop-1"),
        user_message=kwargs.pop("user_message", "inspect then draft"),
        selected_node_id=kwargs.pop("selected_node_id", "video-1"),
        max_iterations=kwargs.pop("max_iterations", 4),
        **kwargs,
    )


def service(store: AtelierStorage, plans: list[DeterministicLoopPlan], observed: list[list[dict]] | None = None) -> AtelierAgentLoopService:
    calls = iter(plans)

    def planner(ctx):
        plan = next(calls)
        if observed is not None:
            observed.append(list(ctx.messages_history))
        return plan

    return AtelierAgentLoopService(
        store,
        planner=planner,
        clock=itertools.count(10.0).__next__,
        id_factory=(f"id-{idx}" for idx in itertools.count(1)).__next__,
        server_storage_identity="storage-a",
    )


def approval_for(project, *, turn_id="turn-loop-1"):
    turn = next(item for item in project.agent_turns if item.id == turn_id)
    call = next(item for item in turn.tool_calls if item.canvas_approval_binding is not None and item.canvas_approval_record is None)
    ref = call.canvas_approval_binding.canvas_authorization_ref
    return RecordAgentCanvasApprovalCommand(
        expected_revision=project.revision,
        turn_id=turn.id,
        call_id=call.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        decision="approved",
    )


def test_loop_start_lost_ack_replay_context_and_conflict(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        first_project, first_replayed = store.start_agent_loop_turn("p", start_command(), now=10.0)
        first = first_project.agent_turns[0]
        assert first_replayed is False
        assert first.status == "pending"
        assert first.loop_start_context is not None
        assert first.loop_start_context.user_message == "inspect then draft"
        assert first.loop_start_context.selected_node_id == "video-1"
        assert "skill_snapshot" not in first.loop_start_context.model_dump(mode="json")
        assert fingerprint_agent_loop_start(start_command()) == fingerprint_request({
            "client_turn_id": "turn-loop-1",
            "schema_version": 1,
            "user_message": "inspect then draft",
            "selected_node_id": "video-1",
            "planner": "deterministic_core",
            "skill_name": None,
            "max_iterations": 4,
        }, entrypoint="atelier.agent.loop.start.v1")
        assert first.user_message == first.loop_start_context.user_message
        assert first.skill_name == first.loop_start_context.skill_name
        assert first.max_iterations == first.loop_start_context.max_iterations
        assert first_project.generation_attempts == []
        with pytest.raises(ValidationError, match="frozen_instance"):
            first.loop_start_context.user_message = "mutated"
        with pytest.raises(StorageError, match="Agent execution state|immutable"):
            store.apply_project_command(
                "p",
                expected_revision=first_project.revision,
                command=lambda project: setattr(
                    project.agent_turns[0],
                    "loop_start_context",
                    project.agent_turns[0].loop_start_context.model_copy(update={"user_message": "mutated"}),
                ),
            )

        replay_project, replayed = store.start_agent_loop_turn("p", start_command(expected_revision=999), now=11.0)
        assert replayed is True
        assert replay_project.agent_turns[0] == first

        with pytest.raises(StorageConflictError, match="different content"):
            store.start_agent_loop_turn("p", start_command(expected_revision=999, user_message="different"), now=12.0)
        with pytest.raises(ValueError, match="skill_requires_model_adapter"):
            service(store, []).start_loop("p", start_command(client_turn_id="turn-loop-2", expected_revision=store.get_project("p").revision, skill_name="unknown"))
    finally:
        store.close()



def test_storage_skill_loop_persists_v2_snapshot_and_replays_without_live_registry(tmp_path):
    from src.apps.atelier.product_skills import resolve_product_skill_snapshot

    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        command = start_command(planner="model_adapter", skill_name="story-scout", max_iterations=1)
        snapshot = resolve_product_skill_snapshot("story-scout")
        started, replayed = store.start_agent_loop_turn("p", command, now=10.0, resolved_skill_snapshot=snapshot)
        turn = started.agent_turns[0]
        assert replayed is False
        assert turn.skill_name == "story-scout"
        assert turn.loop_start_context.schema_version == 2
        assert turn.loop_start_context.skill_snapshot == snapshot

        replay_project, replayed = store.start_agent_loop_turn("p", command.model_copy(update={"expected_revision": 999}), now=11.0)
        assert replayed is True
        assert replay_project.agent_turns[0] == turn

        with pytest.raises(StorageConflictError, match="different content"):
            store.start_agent_loop_turn("p", command.model_copy(update={"skill_name": "other-skill", "expected_revision": 999}), now=12.0)
    finally:
        store.close()


def test_storage_rejects_new_skill_loop_without_server_snapshot(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        with pytest.raises(StorageConflictError, match="skill_snapshot_required"):
            store.start_agent_loop_turn("p", start_command(planner="model_adapter", skill_name="story-scout"), now=10.0)
    finally:
        store.close()


def test_skill_loop_final_projection_rejects_tools_outside_frozen_snapshot(tmp_path):
    from src.apps.atelier.product_skills import resolve_product_skill_snapshot

    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        snapshot = resolve_product_skill_snapshot("story-scout")
        command = start_command(planner="model_adapter", skill_name="story-scout", max_iterations=1)
        started, _ = store.start_agent_loop_turn("p", command, now=10.0, resolved_skill_snapshot=snapshot)
        binding = build_agent_canvas_binding(
            started,
            tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
            arguments={"title": "Draft", "prompt": "Rain"},
            turn_id=command.client_turn_id,
            call_id="canvas-call-1",
            approval_id="approval-1",
            server_storage_identity="storage-a",
            created_at=11.0,
        )
        with pytest.raises(StorageConflictError, match="product_skill_tool_not_allowed"):
            store.save_agent_loop_iteration(
                "p",
                SaveAgentLoopIterationCommand(
                    expected_revision=started.revision,
                    turn_id=command.client_turn_id,
                    iteration_index=1,
                    planner="model_adapter",
                    assistant_message="mutate despite read-only skill",
                    tool_calls=(AtelierAgentToolCall(
                        call_id="canvas-call-1",
                        tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
                        arguments={"title": "Draft", "prompt": "Rain"},
                        status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
                        approval_required=True,
                        canvas_approval_binding=binding,
                    ),),
                ),
                now=12.0,
            )
        readback = store.get_project("p").agent_turns[-1]
        assert readback.tool_calls == []
        assert readback.pending_tool_calls == []
    finally:
        store.close()


def test_skill_loop_runtime_rejects_planner_tools_outside_frozen_snapshot(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)

        def model_planner(_context, **_kwargs):
            return DeterministicLoopPlan("mutate", [{"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"title": "Draft", "prompt": "Rain"}}])

        loop = AtelierAgentLoopService(
            store,
            planner=lambda _ctx: (_ for _ in ()).throw(AssertionError("deterministic planner must not run")),
            model_planner=model_planner,
            clock=itertools.count(10.0).__next__,
            id_factory=(f"id-{idx}" for idx in itertools.count(1)).__next__,
            server_storage_identity="storage-a",
        )
        ack = loop.start_loop("p", start_command(planner="model_adapter", skill_name="story-scout", max_iterations=1))
        assert ack.turn.status == "failed"
        assert ack.turn.response == "planner_projection_failed"
        assert ack.turn.tool_calls == []
        assert store.get_project("p").nodes[0].prompt == "old"
    finally:
        store.close()


def test_loop_read_then_canvas_resume_runs_true_second_planner_iteration(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        plans = [
            DeterministicLoopPlan(assistant_message="I will inspect first", tool_calls=[{"tool_name": "canvas.readProject", "arguments": {}}]),
            DeterministicLoopPlan(assistant_message="Create a draft", tool_calls=[{"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"title": "Draft", "prompt": "Rain", "x": 4}}]),
            DeterministicLoopPlan(assistant_message="Observed draft", tool_calls=[], stop_reason="done"),
        ]
        observed: list[list[dict]] = []
        loop = service(store, plans, observed)
        paused = loop.start_loop("p", start_command(expected_revision=store.get_project("p").revision))
        assert paused.turn.status == "waiting_approval"
        assert paused.turn.iteration_count == 2
        assert [call.tool_name for call in paused.turn.tool_calls] == ["canvas.readProject", TOOL_CANVAS_CREATE_VIDEO_NODE]
        assert paused.turn.tool_calls[0].status == AtelierAgentToolStatus.COMPLETED
        assert paused.turn.tool_calls[0].result_snapshot["node_count"] == 1
        assert observed[1] and observed[1][-1]["tool_call_id"] == paused.turn.tool_calls[0].call_id
        assert paused.turn.pending_tool_calls == [{
            "call_id": paused.turn.tool_calls[1].call_id,
            "tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE,
            "approval_id": paused.turn.tool_calls[1].canvas_approval_binding.canvas_authorization_ref.approval_id,
            "authorization_hash": paused.turn.tool_calls[1].canvas_approval_binding.canvas_authorization_ref.authorization_hash,
            "requires_approval": True,
        }]
        replay_project, replayed_iteration = store.save_agent_loop_iteration(
            "p",
            SaveAgentLoopIterationCommand(
                expected_revision=0,
                turn_id="turn-loop-1",
                iteration_index=2,
                assistant_message="Create a draft",
                tool_calls=(paused.turn.tool_calls[1],),
            ),
            now=99.0,
        )
        assert replayed_iteration is True
        assert replay_project.revision == paused.project.revision

        store.record_agent_canvas_approval("p", approval_for(paused.project), decided_at=30.0)
        resumed = loop.resume_loop("p", "turn-loop-1")
        assert resumed.turn.status == "completed"
        assert resumed.turn.response == "done"
        assert resumed.turn.iteration_count == 3
        assert [node.id for node in resumed.project.nodes] == ["video-1", "id-4"]
        assert resumed.project.nodes[-1].prompt == "Rain"
        create_call = next(call for call in resumed.turn.tool_calls if call.tool_name == TOOL_CANVAS_CREATE_VIDEO_NODE)
        assert create_call.status == AtelierAgentToolStatus.COMPLETED
        assert create_call.result_snapshot["node_ids"] == ["id-4"]
        assert resumed.turn.pending_tool_calls == []
        assert observed[2][-1]["tool_call_id"] == create_call.call_id

        replay = loop.resume_loop("p", "turn-loop-1")
        assert replay.replayed is True
        assert [node.id for node in replay.project.nodes] == ["video-1", "id-4"]
        assert len([m for m in replay.turn.messages_history if m.get("tool_call_id") == create_call.call_id]) == 1
    finally:
        store.close()


def test_storage_rejects_iteration_planner_that_differs_from_loop_start_context(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        started, _ = store.start_agent_loop_turn("p", start_command(), now=10.0)
        before = store.path.read_bytes()
        with pytest.raises(StorageConflictError, match="planner_mismatch"):
            store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
                expected_revision=started.revision,
                turn_id="turn-loop-1",
                iteration_index=1,
                planner="model_adapter",
                assistant_message="wrong planner",
                tool_calls=(),
                stop_reason="done",
            ), now=11.0)
        assert store.path.read_bytes() == before
        assert store.get_project("p").revision == started.revision
        assert store.get_project("p").agent_turns[0].iterations == []
    finally:
        store.close()


def test_storage_rejects_direct_iteration_invalid_tools_prefilled_results_and_oversized_batches(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        started, _ = store.start_agent_loop_turn("p", start_command(), now=10.0)
        before = store.path.read_bytes()
        base = {"expected_revision": started.revision, "turn_id": "turn-loop-1", "iteration_index": 1, "assistant_message": "bad"}
        invalid_cases = [
            ("tool_not_implemented", (AtelierAgentToolCall(call_id="bad-1", tool_name="shell.exec"),)),
            ("invalid_read_call", (AtelierAgentToolCall(
                call_id="bad-2",
                tool_name="canvas.readProject",
                status=AtelierAgentToolStatus.COMPLETED,
                result_snapshot={"ghost": True},
            ),)),
            ("too_many_tool_calls", tuple(
                AtelierAgentToolCall(call_id=f"read-{idx}", tool_name="canvas.readProject")
                for idx in range(9)
            )),
        ]
        for match, calls in invalid_cases:
            with pytest.raises(StorageConflictError, match=match):
                store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(**base, tool_calls=calls), now=11.0)
            assert store.path.read_bytes() == before
    finally:
        store.close()


def test_storage_rejects_direct_iteration_canvas_binding_mismatch_and_legacy_authority(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        started, _ = store.start_agent_loop_turn("p", start_command(), now=10.0)
        project = store.get_project("p")
        binding = build_agent_canvas_binding(
            project,
            tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
            arguments={"prompt": "Rain"},
            turn_id="turn-loop-1",
            call_id="different-call",
            approval_id="approval-1",
            server_storage_identity="storage-a",
            created_at=11.0,
        )
        before = store.path.read_bytes()
        with pytest.raises(StorageConflictError, match="binding_mismatch"):
            store.save_agent_loop_iteration("p", SaveAgentLoopIterationCommand(
                expected_revision=started.revision,
                turn_id="turn-loop-1",
                iteration_index=1,
                assistant_message="bad",
                tool_calls=(AtelierAgentToolCall(
                    call_id="call-1",
                    tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
                    status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
                    approval_required=True,
                    canvas_approval_binding=binding,
                ),),
            ), now=11.0)
        assert store.path.read_bytes() == before
        with pytest.raises(StorageConflictError, match="authority_must_use_dedicated_command"):
            store.create_agent_turn("p", AtelierAgentTurn(
                id="legacy-loop",
                project_id="p",
                loop_start_context=started.agent_turns[0].loop_start_context,
            ), expected_revision=started.revision)
        assert store.path.read_bytes() == before
    finally:
        store.close()


def test_loop_mixed_read_and_canvas_consumes_read_before_approval_pause(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        loop = service(store, [DeterministicLoopPlan(assistant_message="Inspect and create", tool_calls=[
            {"tool_name": "canvas.readProject", "arguments": {}},
            {"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "Rain"}},
        ])])
        paused = loop.start_loop("p", start_command())
        assert paused.turn.status == "waiting_approval"
        assert [call.tool_name for call in paused.turn.tool_calls] == ["canvas.readProject", TOOL_CANVAS_CREATE_VIDEO_NODE]
        assert paused.turn.tool_calls[0].status == AtelierAgentToolStatus.COMPLETED
        assert paused.turn.tool_calls[0].result_snapshot["node_ids"] == ["video-1"]
        assert paused.turn.messages_history[-1]["tool_call_id"] == paused.turn.tool_calls[0].call_id
        assert paused.turn.pending_tool_calls == [{
            "call_id": paused.turn.tool_calls[1].call_id,
            "tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE,
            "approval_id": paused.turn.tool_calls[1].canvas_approval_binding.canvas_authorization_ref.approval_id,
            "authorization_hash": paused.turn.tool_calls[1].canvas_approval_binding.canvas_authorization_ref.authorization_hash,
            "requires_approval": True,
        }]
    finally:
        store.close()


def test_loop_denying_one_of_multiple_pending_calls_terminalizes_all_pending(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        add_second_video_node(store)
        loop = service(store, [DeterministicLoopPlan(assistant_message="Update two", tool_calls=[
            {"tool_name": TOOL_CANVAS_UPDATE_NODE_PROMPT, "arguments": {"node_id": "video-1", "prompt": "one"}},
            {"tool_name": TOOL_CANVAS_UPDATE_NODE_PROMPT, "arguments": {"node_id": "video-2", "prompt": "two"}},
        ])])
        paused = loop.start_loop("p", start_command(expected_revision=store.get_project("p").revision))
        assert paused.turn.status == "waiting_approval"
        assert len(paused.turn.pending_tool_calls) == 2
        first_ref = paused.turn.tool_calls[0].canvas_approval_binding.canvas_authorization_ref
        second_ref = paused.turn.tool_calls[1].canvas_approval_binding.canvas_authorization_ref
        denied = store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(
            expected_revision=paused.project.revision,
            turn_id=paused.turn.id,
            call_id=paused.turn.tool_calls[0].call_id,
            approval_id=first_ref.approval_id,
            authorization_hash=first_ref.authorization_hash,
            decision="denied",
        ), decided_at=30.0)
        turn = denied.agent_turns[0]
        assert turn.status == "failed"
        assert turn.pending_tool_calls == []
        assert turn.tool_calls[0].status == AtelierAgentToolStatus.DENIED
        assert turn.tool_calls[1].status == AtelierAgentToolStatus.FAILED
        assert turn.tool_calls[1].result_snapshot["decision"] == "blocked_by_denied"
        ack = loop.resume_loop("p", "turn-loop-1")
        assert ack.replayed is True
        assert [node.id for node in ack.project.nodes] == ["video-1", "video-2"]
        assert [node.prompt for node in ack.project.nodes] == ["old", "old two"]
        with pytest.raises(StorageConflictError, match="turn_not_active"):
            store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(
                expected_revision=denied.revision,
                turn_id=paused.turn.id,
                call_id=paused.turn.tool_calls[1].call_id,
                approval_id=second_ref.approval_id,
                authorization_hash=second_ref.authorization_hash,
                decision="approved",
            ), decided_at=31.0)
    finally:
        store.close()


def test_loop_rejects_conflicting_same_snapshot_mutation_batch_before_iteration_save(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        before = store.path.read_bytes()
        loop = service(store, [DeterministicLoopPlan(assistant_message="Create two", tool_calls=[
            {"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "Rain"}},
            {"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "Snow"}},
        ])])
        with pytest.raises(StorageConflictError, match="conflicting_canvas_batch"):
            loop.start_loop("p", start_command())
        project = store.get_project("p")
        assert store.path.read_bytes() != before
        assert project.agent_turns[0].status == "pending"
        assert project.agent_turns[0].iterations == []
        assert project.agent_turns[0].tool_calls == []
        assert project.nodes[0].prompt == "old"
    finally:
        store.close()


def test_loop_allows_distinct_update_targets_in_one_iteration(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        add_second_video_node(store)
        loop = service(store, [
            DeterministicLoopPlan(assistant_message="Update both", tool_calls=[
                {"tool_name": TOOL_CANVAS_UPDATE_NODE_PROMPT, "arguments": {"node_id": "video-1", "prompt": "one"}},
                {"tool_name": TOOL_CANVAS_UPDATE_NODE_PROMPT, "arguments": {"node_id": "video-2", "prompt": "two"}},
            ]),
            DeterministicLoopPlan(assistant_message="done", tool_calls=[], stop_reason="done"),
        ])
        paused = loop.start_loop("p", start_command(expected_revision=store.get_project("p").revision))
        assert paused.turn.status == "waiting_approval"
        assert len(paused.turn.pending_tool_calls) == 2
        store.record_agent_canvas_approval("p", approval_for(paused.project), decided_at=30.0)
        one_done = loop.resume_loop("p", "turn-loop-1")
        assert one_done.turn.status == "waiting_approval"
        assert one_done.project.nodes[0].prompt == "one"
        assert one_done.project.nodes[1].prompt == "old two"
        store.record_agent_canvas_approval("p", approval_for(one_done.project), decided_at=40.0)
        completed = loop.resume_loop("p", "turn-loop-1")
        assert completed.turn.status == "completed"
        assert [node.prompt for node in completed.project.nodes] == ["one", "two"]
    finally:
        store.close()


def test_loop_update_replay_and_save_failure_preserves_pending(tmp_path, monkeypatch):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        loop = service(store, [
            DeterministicLoopPlan(assistant_message="Update", tool_calls=[{"tool_name": TOOL_CANVAS_UPDATE_NODE_PROMPT, "arguments": {"node_id": "video-1", "prompt": "new"}}]),
            DeterministicLoopPlan(assistant_message="done", tool_calls=[], stop_reason="done"),
        ])
        paused = loop.start_loop("p", start_command())
        store.record_agent_canvas_approval("p", approval_for(paused.project), decided_at=20.0)
        before = store.path.read_bytes()
        monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", lambda *args, **kwargs: (_ for _ in ()).throw(StorageError("disk full")))
        with pytest.raises(StorageError):
            loop.resume_loop("p", "turn-loop-1")
        assert store.path.read_bytes() == before
        monkeypatch.undo()

        resumed = loop.resume_loop("p", "turn-loop-1")
        assert resumed.project.nodes[0].prompt == "new"
        replay = loop.resume_loop("p", "turn-loop-1")
        assert replay.replayed is True
        assert replay.project.nodes[0].prompt == "new"
    finally:
        store.close()


def test_loop_denial_revocation_and_cancel_do_not_plan_again_or_mutate(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        plan = DeterministicLoopPlan(assistant_message="Create", tool_calls=[{"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "Rain"}}])
        loop = service(store, [plan])
        paused = loop.start_loop("p", start_command())
        denied = store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(**{**approval_for(paused.project).model_dump(), "decision": "denied"}), decided_at=30.0)
        assert denied.agent_turns[0].status == "failed"
        denied_ack = loop.resume_loop("p", "turn-loop-1")
        assert denied_ack.replayed is True
        assert [node.id for node in denied_ack.project.nodes] == ["video-1"]
        with pytest.raises(StorageConflictError, match="turn_terminal"):
            store.finish_agent_loop_turn(
                "p",
                expected_revision=denied.revision,
                turn_id="turn-loop-1",
                status="completed",
                response="resurrect",
                now=31.0,
            )

        loop2 = service(store, [DeterministicLoopPlan(assistant_message="Create", tool_calls=[{"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"prompt": "Snow"}}])])
        loop2.start_loop("p", start_command(client_turn_id="turn-loop-2", expected_revision=denied.revision))
        cancelled = store.cancel_agent_turn("p", "turn-loop-2", cancelled_at=40.0)
        assert cancelled.agent_turns[-1].status == "canceled"
        ack = loop2.resume_loop("p", "turn-loop-2")
        assert ack.replayed is True
        assert [node.id for node in ack.project.nodes] == ["video-1"]
    finally:
        store.close()


def test_completed_loop_canvas_replay_requires_exact_authorization_tuple(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        loop = service(store, [
            DeterministicLoopPlan(assistant_message="Update", tool_calls=[{"tool_name": TOOL_CANVAS_UPDATE_NODE_PROMPT, "arguments": {"node_id": "video-1", "prompt": "new"}}]),
            DeterministicLoopPlan(assistant_message="done", tool_calls=[], stop_reason="done"),
        ])
        paused = loop.start_loop("p", start_command())
        approved_command = approval_for(paused.project)
        store.record_agent_canvas_approval("p", approved_command, decided_at=20.0)
        completed = loop.resume_loop("p", "turn-loop-1")
        before = store.path.read_bytes()
        with pytest.raises(StorageConflictError, match="replay_mismatch"):
            store.apply_agent_loop_canvas_command(
                "p",
                ApplyApprovedAgentCanvasCommand(
                    expected_revision=999,
                    turn_id=approved_command.turn_id,
                    call_id=approved_command.call_id,
                    approval_id=approved_command.approval_id,
                    authorization_hash="0" * 64,
                ),
                now=99.0,
                server_storage_identity="storage-a",
            )
        assert store.path.read_bytes() == before
        exact_replay, replayed = store.apply_agent_loop_canvas_command(
            "p",
            ApplyApprovedAgentCanvasCommand(
                expected_revision=999,
                turn_id=approved_command.turn_id,
                call_id=approved_command.call_id,
                approval_id=approved_command.approval_id,
                authorization_hash=approved_command.authorization_hash,
            ),
            now=99.0,
            server_storage_identity="storage-a",
        )
        assert replayed is True
        assert exact_replay == completed.project
    finally:
        store.close()


def test_agent_loop_wire_fixture_export_for_frontend(tmp_path):
    store = AtelierStorage(tmp_path / "data")
    try:
        seed(store)
        plans = [
            DeterministicLoopPlan(assistant_message="Inspect", tool_calls=[{"tool_name": "canvas.readProject", "arguments": {}}]),
            DeterministicLoopPlan(assistant_message="Create", tool_calls=[{"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "arguments": {"title": "Draft", "prompt": "Rain"}}]),
            DeterministicLoopPlan(assistant_message="Done", tool_calls=[], stop_reason="done"),
        ]
        loop = service(store, plans)
        paused = loop.start_loop("p", start_command())
        approved = store.record_agent_canvas_approval("p", approval_for(paused.project), decided_at=30.0)
        completed = loop.resume_loop("p", "turn-loop-1")
        payload = {
            "schema_version": 1,
            "paused": paused.project.model_dump(mode="json"),
            "approved": approved.model_dump(mode="json"),
            "completed": completed.project.model_dump(mode="json"),
        }
        output = Path(os.environ.get("LUMENX_TEST_ISOLATION_ROOT", str(tmp_path))) / "agent-loop-wire.json"
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert output.is_file()
        print(f"AGENT_LOOP_FIXTURE={output}")
    finally:
        store.close()
