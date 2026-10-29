"""Agent generation attempts need a live approved binding before paid submit."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from src.apps.atelier.agent_authorization import (
    TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
    build_agent_generation_approval_record,
    build_agent_generation_binding,
)
from src.apps.atelier.agent_commands import RecordAgentApprovalCommand
from src.apps.atelier.generation_commands import CreateVideoCandidateCommand
from src.apps.atelier.generation_execution import AtelierGenerationExecutionService
from src.apps.atelier.generation_preview import build_generation_preview
from src.apps.atelier.models import (
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
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderArtifact, ProviderHandle, ProviderObservation, ProviderScope, ProviderTaskStatus, QueryCapability
from src.utils.json_storage import StorageConflictError
from src.utils.media_versions import MediaVersionError

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
SECRET = "fake"
pytest_plugins = ("tests.test_atelier_agent_admission",)

SCOPE = ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing")


class SubmitProvider:
    def __init__(self):
        self.submits = []
        self.queries = []
        self.fetches = []
        self.query_status = ProviderTaskStatus.RUNNING
        self.query_raw_status = "RUNNING"
        self.query_video_url = None
        self.fetch_bytes = b"atelier integration video bytes"

    def submit(self, request, *, credential, on_submitted):
        self.submits.append((request, credential))
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id,
            task_id="remote-task-1",
            scope=request.scope,
            credential_ref=request.credential_ref,
            request_id="request-1",
            query_capability=QueryCapability.BY_TASK_ID,
            submitted_at=123.0,
        )
        on_submitted(handle)
        return handle

    def query(self, handle, *, credential):
        self.queries.append((handle, credential))
        return ProviderObservation(
            task_id=handle.task_id,
            status=self.query_status,
            raw_status=self.query_raw_status,
            request_id="query-request-1",
            video_url=self.query_video_url,
            source_handle=handle,
        )

    def fetch(
        self,
        handle,
        observation,
        *,
        output_root,
        output_ref,
        writer_lock,
        storage_guard,
        max_bytes,
        on_prepared=None,
        publish_policy="replace",
    ):
        self.fetches.append((handle, observation, output_ref, publish_policy))
        digest = hashlib.sha256(self.fetch_bytes).hexdigest()
        artifact = ProviderArtifact(handle, output_ref, digest, len(self.fetch_bytes))
        if on_prepared is not None:
            on_prepared(artifact)
        target = output_root / output_ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.fetch_bytes)
        return artifact


def command(media_url: str = "uploads/frame.png"):
    return CreateVideoCandidateCommand(
        expected_revision=0,
        client_request_id="agent-request-1",
        model_id="wan/wan2.7-video#i2v",
        mode="i2v",
        prompt="Slow sunrise",
        first_frame={"node_id": "image", "media_url": media_url},
    )


def _candidate(status="pending"):
    return {
        "execution_contract": "atelier-provider-video-v1",
        "id": "candidate-1",
        "attempt_id": "attempt-1",
        "media_kind": "video",
        "status": status,
        "video_url": None,
        "error_code": None,
        "created_at": 10.0,
        "updated_at": 10.0,
    }


def _receipt(record, ref):
    return AtelierGenerationRequestReceipt(
        project_id="p",
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="agent-request-1",
        request_fingerprint=ref.request_fingerprint,
        node_id="video",
        candidate_id="candidate-1",
        attempt_id="attempt-1",
        execution_fingerprint=record.fingerprint,
        base_revision=0,
        committed_revision=1,
        created_at=10.0,
    )


def _prepare_project(tmp_path: Path, *, is_approved: bool):
    data = tmp_path / "data"
    store = AtelierStorage(data)
    image = data / "uploads/frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG)
    store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, nodes=[
        AtelierNode(id="image", project_id="p", type="image", status="completed", media_urls=["uploads/frame.png"]),
        AtelierNode(id="video", project_id="p", type="video", data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]}),
    ]))
    credentials = ProviderCredentialStore(data, writer_lock=store._writer_lock, runtime_lock=store._lock)
    credential = credentials.bind(SCOPE, SECRET)
    preview = build_generation_preview(
        store.get_project("p"),
        "video",
        command(),
        media_scope=store._media_write_scope(),
        credential=credential,
        server_storage_identity="storage-a",
        attempt_id="attempt-1",
    )
    record = preview.record
    store.close()
    policy = AtelierAgentPolicy(updated_at=999.0)
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        policy=policy,
        clock=lambda: 20.0,
    )
    approval = build_agent_generation_approval_record(
        binding.agent_authorization_ref,
        decision="approved" if is_approved else "denied",
        clock=lambda: 21.0,
    )
    project = AtelierProject(
        id="p",
        title="Canvas",
        revision=1,
        created_at=1.0,
        updated_at=1.0,
        agent_policy=policy,
        nodes=[AtelierNode(id="video", project_id="p", type="video", data={"candidates": [_candidate()]})],
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
        generation_attempts=[AtelierGenerationAttempt(
            schema_version=2,
            project_id="p",
            node_id="video",
            candidate_id="candidate-1",
            attempt_id="attempt-1",
            status="pending",
            first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
            provider_execution=record,
            agent_authorization_ref=binding.agent_authorization_ref,
            created_at=10.0,
            updated_at=10.0,
        )],
        generation_request_receipts=[_receipt(record, binding.agent_authorization_ref)],
    )
    (data / "atelier_projects.json").write_text(json.dumps({"p": project.model_dump(mode="json")}), encoding="utf-8")
    return data


def _service(data_dir: Path, provider: SubmitProvider):
    store = AtelierStorage(data_dir)
    credentials = ProviderCredentialStore(data_dir, writer_lock=store._writer_lock, runtime_lock=store._lock)
    credentials.bind(SCOPE, SECRET)
    runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
    service = AtelierGenerationExecutionService(
        store,
        runtime_provider=lambda scope: runtime,
        credential_store=credentials,
        clock=lambda: 200.0,
        server_storage_identity="storage-a",
    )
    return service, store


def _attempt(store: AtelierStorage, attempt_id: str = "attempt-1"):
    return next(item for item in store.get_project("p").generation_attempts if item.attempt_id == attempt_id)


def _service_for_store(store: AtelierStorage, provider: SubmitProvider, *, secret: str = "test-only-secret"):
    credentials = ProviderCredentialStore(store.data_dir, writer_lock=store._writer_lock, runtime_lock=store._lock)
    runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: secret, provider=provider, max_bytes=1024 * 1024)
    return AtelierGenerationExecutionService(
        store,
        runtime_provider=lambda scope: runtime,
        credential_store=credentials,
        clock=lambda: 200.0,
    )


def _admit_approved(approved):
    store, command, _ = approved
    project, replayed = store.apply_agent_generation_admission("p", command, now=12.0)
    assert not replayed
    return store, command, project.generation_attempts[0]


def _continue_attempt(service: AtelierGenerationExecutionService, attempt_id: str):
    attempt = _attempt(service.storage, attempt_id)
    return service.continue_attempt(
        "p",
        attempt.attempt_id,
        candidate_id=attempt.candidate_id,
        expected_attempt_revision=attempt.attempt_revision,
        execution_fingerprint=attempt.provider_execution.fingerprint,
    )


def _revoke_agent_call(store: AtelierStorage, command, *, decided_at: float = 13.0):
    revoke = RecordAgentApprovalCommand(**command.model_dump(exclude={"client_request_id"}), decision="revoked")
    return store.record_agent_approval("p", revoke, decided_at=decided_at)


def test_agent_submit_requires_approved_authorization_before_provider_post(tmp_path):
    data = _prepare_project(tmp_path, is_approved=False)
    provider = SubmitProvider()
    service, store = _service(data, provider)
    try:
        attempt = store.get_project("p").generation_attempts[0]
        with pytest.raises(StorageConflictError):
            service.continue_attempt(
                "p",
                "attempt-1",
                candidate_id="candidate-1",
                expected_attempt_revision=0,
                execution_fingerprint=attempt.provider_execution.fingerprint,
            )
        assert provider.submits == []
        assert store.get_project("p").generation_attempts[0].provider_execution.phase == "accepted"
    finally:
        store.close()


def test_agent_submit_with_approved_authorization_can_post(tmp_path):
    data = _prepare_project(tmp_path, is_approved=True)
    provider = SubmitProvider()
    service, store = _service(data, provider)
    try:
        attempt = store.get_project("p").generation_attempts[0]
        ack = service.continue_attempt(
            "p",
            "attempt-1",
            candidate_id="candidate-1",
            expected_attempt_revision=0,
            execution_fingerprint=attempt.provider_execution.fingerprint,
        )
        assert len(provider.submits) == 1
        assert ack.attempt.provider_execution.phase == "submitted"
        assert ack.attempt.schema_version == 2
    finally:
        store.close()


def test_actual_agent_admission_submit_uses_placeholder_converted_attempt(approved):
    store, _, attempt = _admit_approved(approved)
    provider = SubmitProvider()
    service = _service_for_store(store, provider)

    ack = _continue_attempt(service, attempt.attempt_id)

    assert len(provider.submits) == 1
    assert ack.attempt.provider_execution.phase == "submitted"
    assert ack.attempt.provider_execution.attempt_id == attempt.attempt_id
    assert ack.attempt.provider_execution.attempt_id != "preview-attempt"


@pytest.mark.parametrize("mutation", ["cancel", "revoke", "policyedit"])
def test_actual_agent_admission_guard_rejects_late_authority_changes_before_submit(approved, mutation):
    store, command, attempt = _admit_approved(approved)
    if mutation == "cancel":
        store.cancel_agent_turn("p", "turn", cancelled_at=13.0)
    elif mutation == "revoke":
        _revoke_agent_call(store, command)
    else:
        current_revision = store.get_project("p").revision
        store.apply_project_command(
            "p",
            expected_revision=current_revision,
            command=lambda project: setattr(project.agent_policy, "allowed_tools", ["canvas.readProject"]),
        )
    provider = SubmitProvider()
    service = _service_for_store(store, provider)

    with pytest.raises(StorageConflictError):
        _continue_attempt(service, attempt.attempt_id)

    assert provider.submits == []
    assert _attempt(store, attempt.attempt_id).provider_execution.phase == "accepted"


def test_actual_agent_admission_race_rechecks_approval_inside_submit_claim(approved, monkeypatch):
    store, command, attempt = _admit_approved(approved)
    provider = SubmitProvider()
    service = _service_for_store(store, provider)
    original = store.update_generation_attempt
    revoked = {"done": False}

    def revoke_after_local_prep_before_claim(*args, project_guard=None, **kwargs):
        if project_guard is not None and not revoked["done"]:
            revoked["done"] = True
            _revoke_agent_call(store, command)
        return original(*args, project_guard=project_guard, **kwargs)

    monkeypatch.setattr(store, "update_generation_attempt", revoke_after_local_prep_before_claim)
    with pytest.raises(StorageConflictError):
        _continue_attempt(service, attempt.attempt_id)

    assert revoked["done"]
    assert provider.submits == []
    assert _attempt(store, attempt.attempt_id).provider_execution.phase == "accepted"


def test_actual_agent_admission_revocation_after_saved_handle_still_allows_recovery_io(approved):
    store, command, attempt = _admit_approved(approved)
    provider = SubmitProvider()
    service = _service_for_store(store, provider)
    _continue_attempt(service, attempt.attempt_id)
    assert _attempt(store, attempt.attempt_id).provider_execution.phase == "submitted"

    _revoke_agent_call(store, command)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://provider.example/video.mp4"
    observed = service.run_attempt("p", attempt.attempt_id)
    completed = service.run_attempt("p", attempt.attempt_id)

    assert observed.attempt.provider_execution.phase == "observed"
    assert completed.attempt.status == "completed"
    assert len(provider.submits) == 1
    assert len(provider.queries) == 1
    assert len(provider.fetches) == 1


def test_actual_agent_admission_missing_credentials_stays_accepted_and_zero_submit(approved):
    store, _, attempt = _admit_approved(approved)
    (store.data_dir / "provider_credentials.json").unlink()
    provider = SubmitProvider()
    service = _service_for_store(store, provider)

    ack = _continue_attempt(service, attempt.attempt_id)

    assert provider.submits == []
    assert ack.attempt.status == "needs_confirmation"
    assert ack.attempt.provider_execution.phase == "accepted"


def test_actual_agent_admission_missing_fixed_blob_stays_accepted_and_zero_submit(approved):
    store, _, attempt = _admit_approved(approved)
    media_hash = attempt.provider_execution.media[0]["sha256"]
    (store.data_dir / "media_versions" / "sha256" / media_hash[:2] / media_hash).unlink()
    provider = SubmitProvider()
    service = _service_for_store(store, provider)

    with pytest.raises(MediaVersionError):
        _continue_attempt(service, attempt.attempt_id)

    assert provider.submits == []
    assert _attempt(store, attempt.attempt_id).provider_execution.phase == "accepted"
