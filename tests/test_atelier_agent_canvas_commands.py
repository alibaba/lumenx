"""Approved Agent canvas commands commit local mutations atomically."""

import json
import os
from pathlib import Path

import pytest

from src.apps.atelier.agent_canvas_authorization import (
    TOOL_CANVAS_CREATE_VIDEO_NODE,
    TOOL_CANVAS_UPDATE_NODE_PROMPT,
    build_agent_canvas_approval_record,
    build_agent_canvas_binding,
)
from src.apps.atelier.agent_canvas_commands import ApplyApprovedAgentCanvasCommand, RecordAgentCanvasApprovalCommand
from src.apps.atelier.models import (
    AtelierAgentToolCall,
    AtelierAgentToolStatus,
    AtelierAgentTurn,
    AtelierNode,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


def seed(store: AtelierStorage) -> AtelierProject:
    return store.create_project(AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[AtelierNode(id="video-1", project_id="p", type="video", title="Old", prompt="old", updated_at=1.0)],
    ))


def add_pending_call(store: AtelierStorage, *, tool_name: str, arguments: dict, call_id: str = "call-1", decision: str = "approved"):
    p = store.get_project("p")
    binding = build_agent_canvas_binding(
        p,
        tool_name=tool_name,
        arguments=arguments,
        turn_id="turn-1",
        call_id=call_id,
        approval_id="approval-1",
        server_storage_identity="storage-a",
        clock=lambda: 10.0,
    )
    turn = AtelierAgentTurn(
        id="turn-1",
        project_id="p",
        status="waiting_approval",
        pending_tool_calls=[{"call_id": call_id, "tool_name": tool_name}],
        tool_calls=[AtelierAgentToolCall(
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
            approval_required=True,
            approval_granted=False,
            canvas_approval_binding=binding,
        )],
    )
    saved = store.create_agent_turn("p", turn, expected_revision=p.revision)
    ref = binding.canvas_authorization_ref
    first_decision = "approved" if decision == "revoked" else decision
    approved = store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(
        expected_revision=saved.revision,
        turn_id=ref.turn_id,
        call_id=ref.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        decision=first_decision,
    ), decided_at=11.0)
    if decision == "revoked":
        store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(
            expected_revision=approved.revision,
            turn_id=ref.turn_id,
            call_id=ref.call_id,
            approval_id=ref.approval_id,
            authorization_hash=ref.authorization_hash,
            decision="revoked",
        ), decided_at=12.0)
    return binding


def command(binding, *, expected_revision=2):
    ref = binding.canvas_authorization_ref
    return ApplyApprovedAgentCanvasCommand(
        expected_revision=expected_revision,
        turn_id=ref.turn_id,
        call_id=ref.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
    )


def test_create_video_node_commits_result_message_pending_removal_and_replays(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        binding = add_pending_call(store, tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE, arguments={"title": "Take", "prompt": "Rain", "x": 12})
        saved, replayed = store.apply_agent_canvas_command("p", command(binding), now=20.0, node_id_factory=lambda: "video-2", server_storage_identity="storage-a")
        assert replayed is False
        assert saved.revision == 3
        assert [node.id for node in saved.nodes] == ["video-1", "video-2"]
        created = saved.nodes[-1]
        assert created.type == "video" and created.created_by == "agent" and created.prompt == "Rain"
        call = saved.agent_turns[0].tool_calls[0]
        assert call.status == AtelierAgentToolStatus.COMPLETED
        assert call.result_snapshot == {"tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE, "node_ids": ["video-2"], "changed_fields": ["nodes"], "committed_revision": 3}
        assert saved.agent_turns[0].pending_tool_calls == []
        assert saved.agent_turns[0].messages_history[-1]["role"] == "tool"

        again, replayed_again = store.apply_agent_canvas_command("p", command(binding, expected_revision=99), now=21.0, node_id_factory=lambda: "video-3", server_storage_identity="storage-a")
        assert replayed_again is True
        assert [node.id for node in again.nodes] == ["video-1", "video-2"]
        assert again.agent_turns[0].messages_history == saved.agent_turns[0].messages_history
    finally:
        store.close()


def test_update_node_prompt_uses_existing_node_semantics_and_rejects_stale_target(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        binding = add_pending_call(store, tool_name=TOOL_CANVAS_UPDATE_NODE_PROMPT, arguments={"node_id": "video-1", "prompt": "new prompt"})
        saved, replayed = store.apply_agent_canvas_command("p", command(binding), now=20.0, server_storage_identity="storage-a")
        assert replayed is False
        assert saved.nodes[0].prompt == "new prompt"
        assert saved.agent_turns[0].tool_calls[0].result_snapshot["node_ids"] == ["video-1"]
    finally:
        store.close()


def test_revoked_canvas_call_finishes_pending_and_cannot_mutate(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        binding = add_pending_call(store, tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE, arguments={"prompt": "Rain"}, decision="revoked")
        before = store.path.read_bytes()
        project = store.get_project("p")
        turn = project.agent_turns[0]
        call = turn.tool_calls[0]
        assert turn.status == "canceled"
        assert turn.pending_tool_calls == []
        assert call.status == AtelierAgentToolStatus.DENIED
        assert call.approval_granted is False
        assert call.canvas_approval_record is not None
        assert call.canvas_approval_record.decision == "revoked"
        assert call.result_snapshot == {
            "tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE,
            "decision": "revoked",
            "node_ids": [],
            "changed_fields": [],
            "committed_revision": 3,
        }
        assert turn.messages_history == [{"role": "tool", "tool_call_id": "call-1", "name": TOOL_CANVAS_CREATE_VIDEO_NODE, "content": call.result_snapshot}]
        with pytest.raises(StorageConflictError, match="turn_not_active"):
            store.apply_agent_canvas_command("p", command(binding, expected_revision=3), now=20.0, node_id_factory=lambda: "video-2", server_storage_identity="storage-a")
        assert store.path.read_bytes() == before
    finally:
        store.close()




def test_preseeded_canvas_approval_record_without_granted_bit_cannot_mutate(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        p = store.get_project("p")
        binding = build_agent_canvas_binding(
            p,
            tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
            arguments={"prompt": "Rain"},
            turn_id="turn-1",
            call_id="call-1",
            approval_id="approval-1",
            server_storage_identity="storage-a",
            clock=lambda: 10.0,
        )
        turn = AtelierAgentTurn(
            id="turn-1",
            project_id="p",
            status="waiting_approval",
            tool_calls=[AtelierAgentToolCall(
                call_id="call-1",
                tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
                arguments={"prompt": "Rain"},
                status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
                canvas_approval_binding=binding,
                canvas_approval_record=build_agent_canvas_approval_record(binding.canvas_authorization_ref, decision="approved", clock=lambda: 11.0),
            )],
        )
        store.create_agent_turn("p", turn, expected_revision=p.revision)
        before = store.path.read_bytes()
        with pytest.raises(StorageConflictError, match="approval_not_granted"):
            store.apply_agent_canvas_command("p", command(binding, expected_revision=1), now=20.0, node_id_factory=lambda: "video-2", server_storage_identity="storage-a")
        assert store.path.read_bytes() == before
    finally:
        store.close()

def test_failed_canvas_command_keeps_bytes_and_plain_project_commands_cannot_touch_agent_ledger(tmp_path, monkeypatch):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        binding = add_pending_call(store, tool_name=TOOL_CANVAS_UPDATE_NODE_PROMPT, arguments={"node_id": "video-1", "prompt": "new"})
        store.apply_project_command("p", expected_revision=2, command=lambda project: project.nodes.clear())
        before = store.path.read_bytes()
        with pytest.raises(StorageConflictError):
            store.apply_agent_canvas_command("p", command(binding, expected_revision=3), now=20.0, server_storage_identity="storage-a")
        assert store.path.read_bytes() == before

        with pytest.raises(StorageError, match="Agent execution state"):
            store.apply_project_command("p", expected_revision=store.get_project("p").revision, command=lambda project: project.agent_turns.clear())
    finally:
        store.close()


def test_denied_canvas_call_finishes_pending_once_and_never_mutates(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        binding = add_pending_call(store, tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE, arguments={"prompt": "Rain"}, decision="denied")
        project = store.get_project("p")
        before = store.path.read_bytes()
        turn = project.agent_turns[0]
        call = turn.tool_calls[0]
        assert project.revision == 2
        assert turn.status == "failed"
        assert turn.completed_at == 11.0
        assert turn.pending_tool_calls == []
        assert call.status == AtelierAgentToolStatus.DENIED
        assert call.completed_at == 11.0
        assert call.approval_granted is False
        assert call.canvas_approval_record is not None
        assert call.canvas_approval_record.decision == "denied"
        assert call.result_snapshot == {
            "tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE,
            "decision": "denied",
            "node_ids": [],
            "changed_fields": [],
            "committed_revision": 2,
        }
        assert turn.messages_history == [{"role": "tool", "tool_call_id": "call-1", "name": TOOL_CANVAS_CREATE_VIDEO_NODE, "content": call.result_snapshot}]

        ref = binding.canvas_authorization_ref
        repeated = store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(
            expected_revision=999,
            turn_id=ref.turn_id,
            call_id=ref.call_id,
            approval_id=ref.approval_id,
            authorization_hash=ref.authorization_hash,
            decision="denied",
        ), decided_at=99.0)
        assert repeated.revision == project.revision
        assert store.path.read_bytes() == before
        with pytest.raises(StorageConflictError, match="turn_not_active"):
            store.apply_agent_canvas_command("p", command(binding, expected_revision=2), now=20.0, node_id_factory=lambda: "video-2", server_storage_identity="storage-a")
        assert store.path.read_bytes() == before
    finally:
        store.close()


@pytest.mark.parametrize("status", ["failed", "canceled", "cancelled"])
def test_approved_canvas_command_rejects_terminal_turns_without_mutating(tmp_path, status):
    base = AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[AtelierNode(id="video-1", project_id="p", type="video", title="Old", prompt="old", updated_at=1.0)],
    )
    binding = build_agent_canvas_binding(
        base,
        tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
        arguments={"prompt": "Rain"},
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        server_storage_identity="storage-a",
        clock=lambda: 10.0,
    )
    base.agent_turns.append(AtelierAgentTurn(
        id="turn-1",
        project_id="p",
        status=status,
        completed_at=9.0,
        pending_tool_calls=[{"call_id": "call-1", "tool_name": TOOL_CANVAS_CREATE_VIDEO_NODE}],
        tool_calls=[AtelierAgentToolCall(
            call_id="call-1",
            tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
            arguments={"prompt": "Rain"},
            status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
            approval_required=True,
            canvas_approval_binding=binding,
        )],
    ))
    store = AtelierStorage(tmp_path)
    try:
        store.create_project(base)
        before = store.path.read_bytes()
        ref = binding.canvas_authorization_ref
        with pytest.raises(StorageConflictError, match="turn_not_active"):
            store.record_agent_canvas_approval("p", RecordAgentCanvasApprovalCommand(
                expected_revision=0,
                turn_id=ref.turn_id,
                call_id=ref.call_id,
                approval_id=ref.approval_id,
                authorization_hash=ref.authorization_hash,
                decision="approved",
            ), decided_at=11.0)
        assert store.path.read_bytes() == before
    finally:
        store.close()


def _export_applied_canvas_project(tmp_path: Path, *, tool_name: str, arguments: dict, node_id: str | None = None) -> dict:
    store = AtelierStorage(tmp_path)
    try:
        seed(store)
        binding = add_pending_call(store, tool_name=tool_name, arguments=arguments)
        saved, replayed = store.apply_agent_canvas_command(
            "p",
            command(binding),
            now=20.0,
            node_id_factory=(lambda: node_id or "video-2"),
            server_storage_identity="storage-a",
        )
        assert replayed is False
        return saved.model_dump(mode="json")
    finally:
        store.close()


def test_agent_canvas_wire_fixture_export_for_frontend(tmp_path):
    isolation_root = Path(os.environ.get("LUMENX_TEST_ISOLATION_ROOT", str(tmp_path)))
    payload = {
        "schema_version": 1,
        "create_approved_applied": _export_applied_canvas_project(
            tmp_path / "create",
            tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
            arguments={"title": "Take", "prompt": "Rain", "x": 12.0, "y": 24.0, "width": 640.0, "height": 360.0},
            node_id="video-2",
        ),
        "update_approved_applied": _export_applied_canvas_project(
            tmp_path / "update",
            tool_name=TOOL_CANVAS_UPDATE_NODE_PROMPT,
            arguments={"node_id": "video-1", "title": "Revised", "prompt": "new prompt"},
        ),
    }
    output = isolation_root / "agent-canvas-wire.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert output.is_file()
    print(f"AGENT_CANVAS_FIXTURE={output}")
