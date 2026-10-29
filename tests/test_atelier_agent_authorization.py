"""Typed Agent generation authorization binds preview approval to paid submit rights."""

import base64

import pytest
from pydantic import ValidationError

from src.apps.atelier.agent_authorization import (
    TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
    build_agent_generation_binding,
    build_agent_generation_approval_record,
    fingerprint_agent_generation_request,
    policy_snapshot_hash,
    require_agent_authorization,
    validate_agent_authorization_ledger,
    validate_agent_generation_binding,
)
from src.apps.atelier.generation_commands import CreateVideoCandidateCommand
from src.apps.atelier.generation_preview import build_generation_preview
from src.apps.atelier.models import (
    AtelierAgentApprovalRecord,
    AtelierAgentAuthorizationRef,
    AtelierAgentGenerationBinding,
    AtelierAgentPolicy,
    AtelierAgentToolCall,
    AtelierAgentToolStatus,
    AtelierAgentTurn,
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierNode,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.models import provider_recovery
from src.models.provider_tasks import ProviderHandle, ProviderScope, QueryCapability, ResolvedCredential
from src.utils.json_storage import StorageConflictError, StorageError

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
SCOPE = ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing")


def command():
    return CreateVideoCandidateCommand(
        expected_revision=0,
        client_request_id="agent-request-1",
        model_id="wan/wan2.7-video#i2v",
        mode="i2v",
        prompt="Slow sunrise",
        first_frame={"node_id": "image", "media_url": "uploads/frame.png"},
    )


@pytest.fixture
def preview(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, nodes=[
        AtelierNode(id="image", project_id="p", type="image", status="completed", media_urls=["uploads/frame.png"]),
        AtelierNode(id="video", project_id="p", type="video", data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]}),
    ]))
    image = tmp_path / "uploads/frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG)
    built = build_generation_preview(
        store.get_project("p"),
        "video",
        command(),
        media_scope=store._media_write_scope(),
        credential=ResolvedCredential(SCOPE, "credential-ref", "test-only-secret"),
        server_storage_identity="storage-a",
    )
    yield built
    store.close()


def receipt_for(attempt, ref):
    return AtelierGenerationRequestReceipt(
        project_id=attempt.project_id,
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="agent-request-1",
        request_fingerprint=ref.request_fingerprint,
        node_id=attempt.node_id,
        candidate_id=attempt.candidate_id,
        attempt_id=attempt.attempt_id,
        execution_fingerprint=attempt.provider_execution.fingerprint,
        base_revision=0,
        committed_revision=1,
        created_at=10.0,
    )


def test_agent_authorization_schema_versions_are_strict_integers(preview):
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        policy=AtelierAgentPolicy(updated_at=1.0),
        clock=lambda: 10.0,
    )
    ref_payload = binding.agent_authorization_ref.model_dump(mode="json")
    approval_payload = build_agent_generation_approval_record(
        binding.agent_authorization_ref,
        decision="approved",
        clock=lambda: 11.0,
    ).model_dump(mode="json")
    binding_payload = binding.model_dump(mode="json")
    for model, payload in [
        (AtelierAgentAuthorizationRef, ref_payload),
        (AtelierAgentApprovalRecord, approval_payload),
        (AtelierAgentGenerationBinding, binding_payload),
    ]:
        for bad_version in [True, 1.0]:
            with pytest.raises(ValidationError):
                model(**{**payload, "schema_version": bad_version})
        assert model(**payload).schema_version == 1


def test_binding_static_validation_requires_unexecuted_preview_record(preview):
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        policy=AtelierAgentPolicy(updated_at=1.0),
        clock=lambda: 10.0,
    )
    handle = ProviderHandle(
        provider_model_id=preview.record.provider_model_id,
        task_id="remote-task",
        scope=ProviderScope.from_dict(preview.record.scope),
        credential_ref=preview.record.credential_ref,
        request_id="request",
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=12.0,
    )
    submitted_record = provider_recovery.record_with_changes(
        preview.record,
        phase="submitted",
        handle=handle.to_dict(),
    )
    submitted_binding = binding.model_copy(update={"provider_execution": submitted_record})

    with pytest.raises(StorageError):
        validate_agent_generation_binding(submitted_binding)


def test_attempt_schema_versions_enforce_agent_ref(preview):
    ref = build_agent_generation_binding(
        preview,
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        policy=AtelierAgentPolicy(updated_at=999.0),
        clock=lambda: 10.0,
    ).agent_authorization_ref

    with pytest.raises(ValidationError):
        AtelierGenerationAttempt(
            schema_version=1,
            project_id="p",
            node_id="video",
            candidate_id="candidate-1",
            attempt_id="attempt-1",
            status="pending",
            first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
            provider_execution=preview.record.model_copy(update={"attempt_id": "attempt-1"}),
            agent_authorization_ref=ref,
            created_at=1.0,
            updated_at=1.0,
        )
    with pytest.raises(ValidationError):
        AtelierGenerationAttempt(
            schema_version=2,
            project_id="p",
            node_id="video",
            candidate_id="candidate-1",
            attempt_id="attempt-1",
            status="pending",
            first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
            provider_execution=preview.record.model_copy(update={"attempt_id": "attempt-1"}),
            created_at=1.0,
            updated_at=1.0,
        )
    manual = AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id="candidate-1",
        attempt_id="attempt-1",
        status="pending",
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
        provider_execution=preview.record.model_copy(update={"attempt_id": "attempt-1"}),
        created_at=1.0,
        updated_at=1.0,
    )
    assert manual.model_dump(mode="json")["schema_version"] == 1
    assert "agent_authorization_ref" not in manual.model_dump(mode="json")


def test_binding_hash_is_stable_secret_free_and_policy_updated_at_independent(preview):
    first = build_agent_generation_binding(
        preview,
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        policy=AtelierAgentPolicy(updated_at=1.0),
        clock=lambda: 10.0,
    )
    second = build_agent_generation_binding(
        preview,
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        policy=AtelierAgentPolicy(updated_at=999.0),
        clock=lambda: 10.0,
    )
    assert first == second
    assert first.agent_authorization_ref.authorization_hash == second.agent_authorization_ref.authorization_hash
    assert first.agent_authorization_ref.request_fingerprint == fingerprint_agent_generation_request(
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        authorization_hash=first.agent_authorization_ref.authorization_hash,
    )
    assert first.agent_authorization_ref.tool_name == TOOL_GENERATION_CREATE_VIDEO_CANDIDATES
    assert policy_snapshot_hash(AtelierAgentPolicy(updated_at=1.0)) == policy_snapshot_hash(AtelierAgentPolicy(updated_at=999.0))
    rendered = first.model_dump_json() + repr(first)
    assert "test-only-secret" not in rendered
    validate_agent_generation_binding(first)


def test_require_agent_authorization_checks_live_turn_approval_policy_and_storage(preview):
    policy = AtelierAgentPolicy(updated_at=999.0)
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        policy=policy,
        clock=lambda: 10.0,
    )
    approval = build_agent_generation_approval_record(binding.agent_authorization_ref, decision="approved", clock=lambda: 11.0)
    attempt = AtelierGenerationAttempt(
        schema_version=2,
        project_id="p",
        node_id="video",
        candidate_id="candidate-1",
        attempt_id="attempt-1",
        status="pending",
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
        provider_execution=preview.record.model_copy(update={"attempt_id": "attempt-1"}),
        agent_authorization_ref=binding.agent_authorization_ref,
        created_at=1.0,
        updated_at=1.0,
    )
    project = AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        agent_policy=policy,
        agent_turns=[AtelierAgentTurn(
            id="turn-1",
            project_id="p",
            status="waiting_approval",
            tool_calls=[AtelierAgentToolCall(
                call_id="call-1",
                tool_name=TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
                status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
                preview_binding=binding,
                approval_record=approval,
                approval_granted=True,
            )],
        )],
        generation_request_receipts=[receipt_for(attempt, binding.agent_authorization_ref)],
    )
    require_agent_authorization(project, attempt, server_storage_identity="storage-a")

    with pytest.raises(StorageConflictError):
        require_agent_authorization(project, attempt, server_storage_identity="storage-b")
    project.agent_turns[0].tool_calls[0].approval_record = build_agent_generation_approval_record(
        binding.agent_authorization_ref, decision="revoked", clock=lambda: 12.0,
    )
    with pytest.raises(StorageConflictError):
        require_agent_authorization(project, attempt, server_storage_identity="storage-a")
    project.agent_turns[0].tool_calls[0].approval_record = approval
    project.agent_policy.allowed_tools = ["canvas.readProject"]
    with pytest.raises(StorageConflictError):
        require_agent_authorization(project, attempt, server_storage_identity="storage-a")
    project.agent_policy = policy.model_copy(update={"enable_reviewer": True})
    with pytest.raises(StorageConflictError):
        require_agent_authorization(project, attempt, server_storage_identity="storage-a")


def test_static_ledger_allows_pending_preview_before_approval(preview):
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-pending",
        call_id="call-pending",
        approval_id="approval-pending",
        policy=AtelierAgentPolicy(updated_at=1.0),
        clock=lambda: 10.0,
    )
    project = AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        agent_turns=[AtelierAgentTurn(
            id="turn-pending",
            project_id="p",
            status="waiting_approval",
            tool_calls=[AtelierAgentToolCall(
                call_id="call-pending",
                tool_name=TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
                status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
                preview_binding=binding,
            )],
        )],
    )
    validate_agent_authorization_ledger(project)


def test_static_ledger_requires_approval_once_attempt_exists(preview):
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-pending",
        call_id="call-pending",
        approval_id="approval-pending",
        policy=AtelierAgentPolicy(updated_at=1.0),
        clock=lambda: 10.0,
    )
    attempt = AtelierGenerationAttempt(
        schema_version=2,
        project_id="p",
        node_id="video",
        candidate_id="candidate-1",
        attempt_id="attempt-1",
        status="pending",
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
        provider_execution=preview.record,
        agent_authorization_ref=binding.agent_authorization_ref,
        created_at=1.0,
        updated_at=1.0,
    )
    project = AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        agent_turns=[AtelierAgentTurn(
            id="turn-pending",
            project_id="p",
            status="waiting_approval",
            tool_calls=[AtelierAgentToolCall(
                call_id="call-pending",
                tool_name=TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
                status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
                preview_binding=binding,
            )],
        )],
        generation_attempts=[attempt],
        generation_request_receipts=[receipt_for(attempt, binding.agent_authorization_ref)],
    )
    with pytest.raises(StorageError):
        validate_agent_authorization_ledger(project)


def test_static_ledger_rejects_agent_attempt_with_mismatched_provider_identity(preview):
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-identity",
        call_id="call-identity",
        approval_id="approval-identity",
        policy=AtelierAgentPolicy(updated_at=1.0),
        clock=lambda: 10.0,
    )
    approval = build_agent_generation_approval_record(binding.agent_authorization_ref, decision="approved", clock=lambda: 11.0)
    mismatched_record = provider_recovery.record_with_changes(
        preview.record,
        intent={**preview.record.intent, "params": {**preview.record.intent.get("params", {}), "prompt": "different approved payload"}},
    )
    attempt = AtelierGenerationAttempt(
        schema_version=2,
        project_id="p",
        node_id="video",
        candidate_id="candidate-1",
        attempt_id="attempt-1",
        status="pending",
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
        provider_execution=mismatched_record,
        agent_authorization_ref=binding.agent_authorization_ref,
        created_at=1.0,
        updated_at=1.0,
    )
    project = AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        agent_turns=[AtelierAgentTurn(
            id="turn-identity",
            project_id="p",
            status="completed",
            tool_calls=[AtelierAgentToolCall(
                call_id="call-identity",
                tool_name=TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
                status=AtelierAgentToolStatus.COMPLETED,
                preview_binding=binding,
                approval_record=approval,
                approval_granted=True,
            )],
        )],
        generation_attempts=[attempt],
        generation_request_receipts=[receipt_for(attempt, binding.agent_authorization_ref)],
    )

    with pytest.raises(StorageError):
        validate_agent_authorization_ledger(project)


def test_manual_attempt_does_not_require_agent_authorization(preview):
    attempt = AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id="candidate-1",
        attempt_id="attempt-1",
        status="pending",
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
        provider_execution=preview.record.model_copy(update={"attempt_id": "attempt-1"}),
        created_at=1.0,
        updated_at=1.0,
    )
    require_agent_authorization(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0), attempt, server_storage_identity="")
