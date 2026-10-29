"""Typed Agent canvas authorization is separate from paid generation authority."""

import pytest
from pydantic import ValidationError

from src.apps.atelier.agent_canvas_authorization import (
    TOOL_CANVAS_CREATE_VIDEO_NODE,
    TOOL_CANVAS_UPDATE_NODE_PROMPT,
    build_agent_canvas_approval_record,
    build_agent_canvas_binding,
    policy_snapshot_hash,
    require_approved_canvas_call,
    validate_agent_canvas_binding,
)
from src.apps.atelier.models import (
    AtelierAgentCanvasApprovalRecord,
    AtelierAgentCanvasAuthorizationRef,
    AtelierAgentCanvasBinding,
    AtelierAgentPolicy,
    AtelierAgentToolCall,
    AtelierAgentToolStatus,
    AtelierAgentTurn,
    AtelierNode,
    AtelierProject,
)
from src.utils.json_storage import StorageConflictError, StorageError


def project(*, policy: AtelierAgentPolicy | None = None) -> AtelierProject:
    return AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        agent_policy=policy or AtelierAgentPolicy(updated_at=99.0),
        nodes=[AtelierNode(id="n1", project_id="p", type="video", title="Old", prompt="old", updated_at=1.0)],
    )


def test_canvas_binding_schema_is_strict_secret_free_and_omitted_when_absent():
    p = project()
    binding = build_agent_canvas_binding(
        p,
        tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
        arguments={"prompt": "Create a rainy take", "x": 10},
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        server_storage_identity="storage-a",
        clock=lambda: 10.0,
    )
    payload = binding.model_dump(mode="json")
    for model, model_payload in [
        (AtelierAgentCanvasAuthorizationRef, payload["canvas_authorization_ref"]),
        (AtelierAgentCanvasBinding, payload),
        (AtelierAgentCanvasApprovalRecord, build_agent_canvas_approval_record(binding.canvas_authorization_ref, decision="approved", clock=lambda: 11.0).model_dump(mode="json")),
    ]:
        with pytest.raises(ValidationError):
            model(**{**model_payload, "schema_version": 1.0})
        with pytest.raises(ValidationError):
            model(**{**model_payload, "extra": True})

    assert binding.canvas_authorization_ref.tool_name == TOOL_CANVAS_CREATE_VIDEO_NODE
    assert binding.canvas_authorization_ref.policy_snapshot_hash == policy_snapshot_hash(AtelierAgentPolicy(updated_at=123.0))
    validate_agent_canvas_binding(binding)
    rendered = binding.model_dump_json() + repr(binding)
    assert "secret" not in rendered.lower()
    dumped = AtelierAgentToolCall(call_id="call-0", tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE).model_dump(mode="json")
    assert "canvas_approval_binding" not in dumped
    assert "canvas_approval_record" not in dumped


def test_canvas_authorization_requires_exact_approved_tuple_policy_storage_and_target():
    p = project(policy=AtelierAgentPolicy(allowed_tools=[TOOL_CANVAS_UPDATE_NODE_PROMPT], updated_at=1.0))
    binding = build_agent_canvas_binding(
        p,
        tool_name=TOOL_CANVAS_UPDATE_NODE_PROMPT,
        arguments={"node_id": "n1", "prompt": "new"},
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        server_storage_identity="storage-a",
        clock=lambda: 10.0,
    )
    approval = build_agent_canvas_approval_record(binding.canvas_authorization_ref, decision="approved", clock=lambda: 11.0)
    p.agent_turns = [AtelierAgentTurn(
        id="turn-1",
        project_id="p",
        status="waiting_approval",
        tool_calls=[AtelierAgentToolCall(
            call_id="call-1",
            tool_name=TOOL_CANVAS_UPDATE_NODE_PROMPT,
            arguments={"node_id": "n1", "prompt": "new"},
            status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
            approval_required=True,
            approval_granted=True,
            canvas_approval_binding=binding,
            canvas_approval_record=approval,
        )],
    )]
    assert require_approved_canvas_call(p, turn_id="turn-1", call_id="call-1", approval_id="approval-1", authorization_hash=approval.authorization_hash, server_storage_identity="storage-a") == binding

    p.agent_turns[0].tool_calls[0].canvas_approval_record = build_agent_canvas_approval_record(binding.canvas_authorization_ref, decision="revoked", clock=lambda: 12.0)
    with pytest.raises(StorageConflictError, match="not_approved"):
        require_approved_canvas_call(p, turn_id="turn-1", call_id="call-1", approval_id="approval-1", authorization_hash=approval.authorization_hash, server_storage_identity="storage-a")
    p.agent_turns[0].tool_calls[0].canvas_approval_record = approval
    with pytest.raises(StorageConflictError, match="storage"):
        require_approved_canvas_call(p, turn_id="turn-1", call_id="call-1", approval_id="approval-1", authorization_hash=approval.authorization_hash, server_storage_identity="storage-b")
    p.nodes[0].prompt = "changed"
    with pytest.raises(StorageConflictError, match="target_changed"):
        require_approved_canvas_call(p, turn_id="turn-1", call_id="call-1", approval_id="approval-1", authorization_hash=approval.authorization_hash, server_storage_identity="storage-a")


@pytest.mark.parametrize("bad_args", [
    {"prompt": "x", "x": float("inf")},
    {"prompt": "x", "width": -1},
    {"prompt": "x", "type": "image"},
])
def test_create_video_node_rejects_invalid_geometry_or_type(bad_args):
    with pytest.raises(ValueError):
        build_agent_canvas_binding(
            project(),
            tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
            arguments=bad_args,
            turn_id="turn-1",
            call_id="call-1",
            approval_id="approval-1",
            server_storage_identity="storage-a",
            clock=lambda: 10.0,
        )


def test_canvas_authorization_rejects_unknown_tool_and_policy():
    p = project(policy=AtelierAgentPolicy(allowed_tools=["canvas.readProject"], updated_at=1.0))
    with pytest.raises(ValueError):
        build_agent_canvas_binding(
            p,
            tool_name="canvas.legacy",
            arguments={},
            turn_id="turn-1",
            call_id="call-1",
            approval_id="approval-1",
            server_storage_identity="storage-a",
            clock=lambda: 10.0,
        )
    binding = build_agent_canvas_binding(
        project(),
        tool_name=TOOL_CANVAS_CREATE_VIDEO_NODE,
        arguments={"prompt": "Create"},
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        server_storage_identity="storage-a",
        clock=lambda: 10.0,
    )
    assert binding.canvas_authorization_ref.tool_name == TOOL_CANVAS_CREATE_VIDEO_NODE
    with pytest.raises(StorageError):
        validate_agent_canvas_binding(binding.model_copy(update={"normalized_arguments": {"prompt": "tampered"}}))
