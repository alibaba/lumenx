"""FastAPI boundary for Atelier provider generation routes."""

from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.atelier.generation_admission import AtelierGenerationAdmissionService, GenerationAdmissionAck, GenerationAdmissionBatchMember, GenerationBatchAdmissionAck, GenerationRequestNotFound
from src.apps.atelier.generation_commands import ManualVideoCandidateBatchCommand
from src.apps.atelier.generation_execution import GenerationAttemptAck
from src.apps.atelier.generation_ledger import GENERATION_CANDIDATE_CONTRACT
from src.apps.atelier.generation_selection import GenerationCandidateNotFound
from src.apps.atelier.models import (
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierGenerationRequestReceiptMember,
    AtelierNode,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.apps.atelier.nodes import NodeCommandAck
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import ProviderRecoveryError
from src.models.provider_tasks import ProviderScope, ResolvedCredential


HASH = "a" * 64
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


def _provider_record(attempt_id: str = "attempt-1") -> ProviderExecutionRecord:
    return ProviderExecutionRecord(
        schema_version=1,
        attempt_id=attempt_id,
        phase="accepted",
        provider_model_id="wan2.7-i2v",
        intent={
            "rules_version": "generation-parameters-v1",
            "scope": "parameters-only",
            "model_id": "wan/wan2.7-video#i2v",
            "catalog_mode": "i2v",
            "mode": "i2v",
            "prompt": "Slow sunrise",
            "params": {},
            "sources": {"model_id": "request", "mode": "request", "prompt": "request"},
            "request_fields": ["mode", "model_id", "prompt"],
            "preset_id": None,
            "preset_version": None,
        },
        media=[],
        scope={
            "provider": "dashscope",
            "backend": "dashscope",
            "gateway": "dashscope",
            "base_url": "https://dashscope.aliyuncs.com",
            "region": "cn-beijing",
            "workspace_id": None,
            "account_identity": None,
        },
        credential_ref="credential-ref",
        fingerprint=HASH,
    )


def _ack(*, replayed: bool = False, attempt_id: str = "attempt-1", candidate_id: str = "candidate-1") -> GenerationAdmissionAck:
    candidate = {
        "execution_contract": GENERATION_CANDIDATE_CONTRACT,
        "id": candidate_id,
        "attempt_id": attempt_id,
        "media_kind": "video",
        "status": "pending",
        "video_url": None,
        "error_code": None,
        "created_at": 10.0,
        "updated_at": 10.0,
    }
    project = AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        revision=1,
        nodes=[AtelierNode(id="video", project_id="p", type="video", data={"candidates": [candidate]})],
    )
    attempt = AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id=candidate_id,
        attempt_id=attempt_id,
        attempt_revision=0,
        status="pending",
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
        provider_execution=_provider_record(attempt_id),
        created_at=10.0,
        updated_at=10.0,
    )
    receipt = AtelierGenerationRequestReceipt(
        project_id="p",
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="request-1",
        request_fingerprint="b" * 64,
        node_id="video",
        candidate_id=candidate_id,
        attempt_id=attempt_id,
        execution_fingerprint=HASH,
        base_revision=0,
        committed_revision=1,
        created_at=10.0,
    )
    project.generation_attempts.append(attempt)
    project.generation_request_receipts.append(receipt)
    return GenerationAdmissionAck(
        project=project,
        receipt=receipt,
        attempt=attempt,
        candidate=candidate,
        replayed=replayed,
        new_candidate_ids=[] if replayed else [candidate_id],
    )




def _batch_ack(*, replayed: bool = False) -> GenerationBatchAdmissionAck:
    first = _ack(attempt_id="attempt-1", candidate_id="candidate-1")
    second_attempt = first.attempt.model_copy(deep=True, update={
        "attempt_id": "attempt-2",
        "candidate_id": "candidate-2",
        "provider_execution": _provider_record("attempt-2"),
    })
    second_candidate = dict(first.candidate or {})
    second_candidate.update({"id": "candidate-2", "attempt_id": "attempt-2"})
    project = first.project.model_copy(deep=True)
    project.generation_attempts = [first.attempt, second_attempt]
    project.nodes[0].data["candidates"] = [first.candidate, second_candidate]
    receipt = AtelierGenerationRequestReceipt(
        schema_version=2,
        project_id="p",
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="request-1",
        request_fingerprint="b" * 64,
        node_id="video",
        members=[
            AtelierGenerationRequestReceiptMember(candidate_id="candidate-1", attempt_id="attempt-1", execution_fingerprint=HASH),
            AtelierGenerationRequestReceiptMember(candidate_id="candidate-2", attempt_id="attempt-2", execution_fingerprint=second_attempt.provider_execution.fingerprint),
        ],
        base_revision=0,
        committed_revision=1,
        created_at=10.0,
    )
    project.generation_request_receipts = [receipt]
    return GenerationBatchAdmissionAck(
        project=project,
        receipt=receipt,
        members=[
            GenerationAdmissionBatchMember(attempt=first.attempt, candidate=first.candidate),
            GenerationAdmissionBatchMember(attempt=second_attempt, candidate=second_candidate),
        ],
        replayed=replayed,
        new_candidate_ids=[] if replayed else ["candidate-1", "candidate-2"],
    )

def _command() -> dict:
    return {
        "expected_revision": 0,
        "client_request_id": "request-1",
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"},
        "params": {},
    }


class AdmissionFake:
    def __init__(self, ack: GenerationAdmissionAck | None = None):
        self.ack = ack or _ack()
        self.storage = self
        self.admitted = []
        self.reads = []

    def get_project(self, project_id: str):
        assert project_id == self.ack.project.id
        return self.ack.project

    def admit(self, project_id: str, node_id: str, command: ManualVideoCandidateBatchCommand):
        self.admitted.append((project_id, node_id, command))
        return self.ack

    def read_request(self, project_id: str, client_request_id: str):
        self.reads.append((project_id, client_request_id))
        return self.ack


class ExecutionFake:
    def __init__(self, ack: GenerationAttemptAck | None = None):
        admission_ack = _ack()
        self.ack = ack or GenerationAttemptAck(
            project=admission_ack.project,
            attempt=admission_ack.attempt,
            candidate=admission_ack.candidate,
        )
        self.reads = []
        self.continues = []
        self.cancels = []

    def read_attempt(self, project_id: str, attempt_id: str):
        self.reads.append((project_id, attempt_id))
        return self.ack

    def continue_attempt(self, project_id: str, attempt_id: str, **kwargs):
        self.continues.append((project_id, attempt_id, kwargs))
        return self.ack

    def cancel_attempt(self, project_id: str, attempt_id: str, **kwargs):
        self.cancels.append((project_id, attempt_id, kwargs))
        return self.ack


class SelectionFake:
    def __init__(self, ack: NodeCommandAck | None = None):
        project = _ack().project
        project.nodes[0].data["selected_candidate_id"] = "candidate-1"
        project.nodes[0].media_urls = ["video/provider-attempt-1.mp4"]
        self.ack = ack or NodeCommandAck(project=project, node_ids=["video"])
        self.selects = []

    def select_video_candidate(self, project_id: str, node_id: str, candidate_id: str, command):
        self.selects.append((project_id, node_id, candidate_id, command))
        return self.ack


class DispatcherFake:
    def __init__(self, *, fail: bool = False, fail_attempts: set[str] | None = None):
        self.fail = fail
        self.fail_attempts = fail_attempts or set()
        self.scheduled = []

    def schedule(self, project_id: str, attempt_id: str) -> None:
        self.scheduled.append((project_id, attempt_id))
        if self.fail or attempt_id in self.fail_attempts:
            raise RuntimeError("provider SECRET backend exploded")


def _client(*, admission=None, execution=None, dispatcher=None, selection=None, judgment=None) -> TestClient:
    from src.apps.atelier.generation_api import router

    app = FastAPI()
    app.include_router(router)
    if admission is not None:
        app.state.atelier_generation_admission = admission
    if execution is not None:
        app.state.atelier_generation_execution = execution
    if dispatcher is not None:
        app.state.atelier_generation_dispatcher = dispatcher
    if selection is not None:
        app.state.atelier_generation_selection = selection
    if judgment is not None:
        app.state.atelier_candidate_judgment = judgment
    return TestClient(app)


def test_create_video_candidate_schedules_new_attempt_only():
    admission = AdmissionFake(_ack())
    dispatcher = DispatcherFake()
    client = _client(admission=admission, dispatcher=dispatcher)

    response = client.post("/projects/p/nodes/video/video_candidates", json=_command())

    assert response.status_code == 200, response.text
    assert admission.admitted[0][0:2] == ("p", "video")
    assert dispatcher.scheduled == [("p", "attempt-1")]
    assert response.json()["new_candidate_ids"] == ["candidate-1"]


def test_create_video_candidate_with_real_admission_schedules_committed_attempt(tmp_path):
    store = AtelierStorage(tmp_path)
    (tmp_path / "uploads").mkdir(parents=True)
    (tmp_path / "uploads/frame.png").write_bytes(PNG)
    store.create_project(
        AtelierProject(
            id="p",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            nodes=[
                AtelierNode(id="image", project_id="p", type="image", status="completed", media_urls=["uploads/frame.png"]),
                AtelierNode(
                    id="video",
                    project_id="p",
                    type="video",
                    data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]},
                ),
            ],
        )
    )
    admission = AtelierGenerationAdmissionService(
        store,
        credential_provider=lambda *, provider_model_id, gateway: ResolvedCredential(SCOPE, "credential-ref", "secret"),
        clock=lambda: 10.0,
        id_factory=iter(["attempt-1", "candidate-1"]).__next__,
    )
    dispatcher = DispatcherFake()
    client = _client(admission=admission, dispatcher=dispatcher)

    response = client.post("/projects/p/nodes/video/video_candidates", json=_command())

    assert response.status_code == 200, response.text
    assert dispatcher.scheduled == [("p", "attempt-1")]
    saved = store.get_project("p")
    assert saved.generation_attempts[0].attempt_id == "attempt-1"
    assert saved.nodes[1].data["candidates"][0]["id"] == "candidate-1"
    store.close()


def test_replayed_video_candidate_does_not_schedule():
    admission = AdmissionFake(_ack(replayed=True))
    dispatcher = DispatcherFake()
    client = _client(admission=admission, dispatcher=dispatcher)

    response = client.post("/projects/p/nodes/video/video_candidates", json=_command())

    assert response.status_code == 200, response.text
    assert dispatcher.scheduled == []
    assert response.json()["replayed"] is True


def test_create_schedule_failure_returns_fixed_503_without_provider_text():
    client = _client(admission=AdmissionFake(_ack()), dispatcher=DispatcherFake(fail=True))

    response = client.post("/projects/p/nodes/video/video_candidates", json=_command())

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "atelier_generation_dispatch_unavailable"
    assert detail["client_request_id"] == "request-1"
    assert detail["attempt_id"] == "attempt-1"
    assert "SECRET" not in response.text


def test_request_readback_uses_admission_only_and_does_not_schedule():
    admission = AdmissionFake(_ack(replayed=True))
    dispatcher = DispatcherFake()
    client = _client(admission=admission, dispatcher=dispatcher)

    response = client.get("/projects/p/generation/requests/request-1")

    assert response.status_code == 200, response.text
    assert admission.reads == [("p", "request-1")]
    assert dispatcher.scheduled == []
    assert response.json()["replayed"] is True


def test_admission_runtime_or_secret_unavailable_returns_fixed_503_without_provider_reason():
    class UnavailableAdmission(AdmissionFake):
        def __init__(self, reason: str):
            super().__init__()
            self.reason = reason

        def admit(self, project_id: str, node_id: str, command: ManualVideoCandidateBatchCommand):
            raise ProviderRecoveryError(self.reason)

    for reason in ("provider_recovery_runtime_missing", "provider_secret_unavailable"):
        dispatcher = DispatcherFake()
        client = _client(admission=UnavailableAdmission(reason), dispatcher=dispatcher)

        response = client.post("/projects/p/nodes/video/video_candidates", json=_command())

        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "atelier_generation_unavailable",
            "detail": "Atelier generation runtime is unavailable",
        }
        assert reason not in response.text
        assert dispatcher.scheduled == []


def test_execution_runtime_missing_returns_fixed_503_without_provider_reason():
    class RuntimeMissingExecution(ExecutionFake):
        def continue_attempt(self, project_id: str, attempt_id: str, **kwargs):
            raise ValueError("atelier_provider_recovery_runtime_missing")

    client = _client(execution=RuntimeMissingExecution())
    identity = {"candidate_id": "candidate-1", "expected_attempt_revision": 0, "execution_fingerprint": HASH}

    response = client.post("/projects/p/generation/attempts/attempt-1/continue", json=identity)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "atelier_generation_unavailable",
        "detail": "Atelier generation runtime is unavailable",
    }
    assert "atelier_provider_recovery_runtime_missing" not in response.text


def test_select_route_calls_selection_service_and_never_dispatches():
    selection = SelectionFake()
    dispatcher = DispatcherFake()
    client = _client(selection=selection, dispatcher=dispatcher)

    response = client.post(
        "/projects/p/nodes/video/video_candidates/candidate-1/select",
        json={"expected_revision": 1},
    )

    assert response.status_code == 200, response.text
    assert response.json()["node_ids"] == ["video"]
    assert selection.selects[0][0:3] == ("p", "video", "candidate-1")
    assert selection.selects[0][3].expected_revision == 1
    assert dispatcher.scheduled == []


def test_select_route_missing_service_returns_fixed_503():
    response = _client().post(
        "/projects/p/nodes/video/video_candidates/candidate-1/select",
        json={"expected_revision": 1},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "atelier_generation_unavailable"


def test_select_route_missing_candidate_is_fixed_404_without_internal_text():
    class MissingSelection(SelectionFake):
        def select_video_candidate(self, project_id: str, node_id: str, candidate_id: str, command):
            raise GenerationCandidateNotFound("SECRET candidate missing")

    response = _client(selection=MissingSelection()).post(
        "/projects/p/nodes/video/video_candidates/candidate-1/select",
        json={"expected_revision": 1},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "atelier_generation_candidate_not_found"
    assert "SECRET" not in response.text


def test_attempt_readback_continue_and_cancel_call_execution_without_dispatch():
    execution = ExecutionFake()
    dispatcher = DispatcherFake()
    client = _client(execution=execution, dispatcher=dispatcher)
    identity = {"candidate_id": "candidate-1", "expected_attempt_revision": 0, "execution_fingerprint": HASH}

    read = client.get("/projects/p/generation/attempts/attempt-1")
    cont = client.post("/projects/p/generation/attempts/attempt-1/continue", json=identity)
    cancel = client.post("/projects/p/generation/attempts/attempt-1/cancel", json=identity)

    assert read.status_code == cont.status_code == cancel.status_code == 200
    assert execution.reads == [("p", "attempt-1")]
    assert execution.continues == [("p", "attempt-1", identity)]
    assert execution.cancels == [("p", "attempt-1", identity)]
    assert dispatcher.scheduled == []


def test_missing_receipt_does_not_call_admission_or_schedule():
    admission = AdmissionFake()
    admission.ack.project.generation_request_receipts = []
    dispatcher = DispatcherFake()
    response = _client(admission=admission, dispatcher=dispatcher).get("/projects/p/generation/requests/absent")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "atelier_generation_request_not_found"
    assert admission.reads == []
    assert dispatcher.scheduled == []


def test_missing_services_and_missing_records_are_typed_errors():
    assert _client().post("/projects/p/nodes/video/video_candidates", json=_command()).status_code == 503
    assert _client().get("/projects/p/generation/attempts/attempt-1").status_code == 503

    class MissingAdmission(AdmissionFake):
        def read_request(self, project_id: str, client_request_id: str):
            raise GenerationRequestNotFound("missing SECRET request")

    class MissingExecution(ExecutionFake):
        def read_attempt(self, project_id: str, attempt_id: str):
            raise ValueError("atelier_generation_attempt_not_found")

    request_response = _client(admission=MissingAdmission()).get("/projects/p/generation/requests/request-1")
    attempt_response = _client(execution=MissingExecution()).get("/projects/p/generation/attempts/attempt-1")

    assert request_response.status_code == 404
    assert request_response.json()["detail"]["code"] == "atelier_generation_request_not_found"
    assert "SECRET" not in request_response.text
    assert attempt_response.status_code == 404
    assert attempt_response.json()["detail"]["code"] == "atelier_generation_attempt_not_found"


def test_strict_request_bodies_reject_extra_or_coerced_identity_fields():
    client = _client(admission=AdmissionFake(), execution=ExecutionFake(), dispatcher=DispatcherFake())

    create = _command()
    create["unexpected"] = True
    assert client.post("/projects/p/nodes/video/video_candidates", json=create).status_code == 422

    identity = {"candidate_id": "candidate-1", "expected_attempt_revision": "0", "execution_fingerprint": HASH}
    assert client.post("/projects/p/generation/attempts/attempt-1/continue", json=identity).status_code == 422

    select = {"expected_revision": "1"}
    assert client.post("/projects/p/nodes/video/video_candidates/candidate-1/select", json=select).status_code == 422
    select = {"expected_revision": 1, "unexpected": True}
    assert client.post("/projects/p/nodes/video/video_candidates/candidate-1/select", json=select).status_code == 422


def test_create_video_candidate_batch_schedules_each_new_attempt_and_keeps_batch_ack_shape():
    admission = AdmissionFake(_batch_ack())
    dispatcher = DispatcherFake()
    client = _client(admission=admission, dispatcher=dispatcher)
    payload = {**_command(), "batch_size": 2}

    response = client.post("/projects/p/nodes/video/video_candidates", json=payload)

    assert response.status_code == 200, response.text
    assert dispatcher.scheduled == [("p", "attempt-1"), ("p", "attempt-2")]
    body = response.json()
    assert "attempt" not in body and "candidate" not in body
    assert [member["attempt"]["attempt_id"] for member in body["members"]] == ["attempt-1", "attempt-2"]
    assert body["receipt"]["schema_version"] == 2


def test_create_video_candidate_batch_schedule_failure_continues_and_reports_all_members():
    admission = AdmissionFake(_batch_ack())
    dispatcher = DispatcherFake(fail_attempts={"attempt-1"})
    client = _client(admission=admission, dispatcher=dispatcher)

    response = client.post("/projects/p/nodes/video/video_candidates", json={**_command(), "batch_size": 2})

    assert response.status_code == 503
    assert dispatcher.scheduled == [("p", "attempt-1"), ("p", "attempt-2")]
    detail = response.json()["detail"]
    assert detail["attempt_id"] == "attempt-1"
    assert detail["members"] == [
        {"candidate_id": "candidate-1", "attempt_id": "attempt-1"},
        {"candidate_id": "candidate-2", "attempt_id": "attempt-2"},
    ]
    assert detail["scheduled_attempt_ids"] == ["attempt-2"]
    assert detail["failed_attempts"] == [{"attempt_id": "attempt-1", "error_code": "atelier_generation_dispatch_unavailable"}]
    assert "SECRET" not in response.text
