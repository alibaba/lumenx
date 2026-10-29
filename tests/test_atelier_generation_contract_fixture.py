"""Backend-generated Atelier generation wire fixtures for frontend parsers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.apps.atelier.generation_admission import AtelierGenerationAdmissionService
from src.apps.atelier.generation_commands import CreateVideoCandidateCommand, ManualVideoCandidateBatchCommand
from src.apps.atelier.generation_execution import AtelierGenerationExecutionService
from src.apps.atelier.generation_selection import AtelierGenerationSelectionService, SelectVideoCandidateCommand
from src.apps.atelier.models import AtelierAgentPolicy, AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope, ProviderTaskStatus, ResolvedCredential
from tests.test_atelier_generation_execution import FakeProvider, PNG


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "frontend" / "src" / "__tests__" / "fixtures"
FIXTURE_FILES = {
    "accepted": FIXTURE_DIR / "atelier-generation-accepted.json",
    "completed": FIXTURE_DIR / "atelier-generation-completed.json",
    "selected": FIXTURE_DIR / "atelier-generation-selected.json",
    "batch": FIXTURE_DIR / "atelier-generation-batch.json",
    "batch_replayed": FIXTURE_DIR / "atelier-generation-batch-replayed.json",
}
SECRET = "fixture-secret"
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


class FixedCredentialStore:
    def resolve_binding(self, scope: ProviderScope, credential_ref: str, secret: str) -> ResolvedCredential:
        if scope != SCOPE or credential_ref != "credential-ref" or secret != SECRET:
            raise AssertionError("unexpected credential binding")
        return ResolvedCredential(scope, credential_ref, secret)

    def resolve_handle(self, handle, secret: str) -> ResolvedCredential:
        return self.resolve_binding(handle.scope, handle.credential_ref, secret)


def generation_request() -> CreateVideoCandidateCommand:
    return CreateVideoCandidateCommand(
        expected_revision=0,
        client_request_id="request-1",
        model_id="wan/wan2.7-video#i2v",
        mode="i2v",
        prompt="Slow sunrise",
        first_frame={"node_id": "image", "media_url": "uploads/frame.png"},
        params={"duration": 5, "resolution": "1080p"},
    )


def build_generation_contract_payloads(tmp_path: Path) -> dict[str, dict[str, Any]]:
    store = AtelierStorage(tmp_path)
    try:
        (tmp_path / "uploads").mkdir(parents=True)
        (tmp_path / "uploads/frame.png").write_bytes(PNG)
        store.create_project(
            AtelierProject(
                id="project-1",
                title="Generation Contract Canvas",
                agent_policy=AtelierAgentPolicy(updated_at=1.0),
                created_at=1.0,
                updated_at=1.0,
                nodes=[
                    AtelierNode(
                        id="image",
                        project_id="project-1",
                        type="image",
                        status="completed",
                        media_urls=["uploads/frame.png"],
                        created_at=1.0,
                        updated_at=1.0,
                    ),
                    AtelierNode(
                        id="video",
                        project_id="project-1",
                        type="video",
                        data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]},
                        created_at=1.0,
                        updated_at=1.0,
                    ),
                ],
            )
        )
        admission = AtelierGenerationAdmissionService(
            store,
            credential_provider=lambda *, provider_model_id, gateway: ResolvedCredential(SCOPE, "credential-ref", SECRET),
            clock=lambda: 10.0,
            id_factory=iter(["attempt-1", "candidate-1"]).__next__,
        )
        accepted = admission.admit("project-1", "video", generation_request())

        provider = FakeProvider()
        runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
        ticks = iter([20.0, 21.0, 22.0, 23.0, 24.0, 25.0])
        execution = AtelierGenerationExecutionService(
            store,
            runtime_provider=lambda scope: runtime,
            credential_store=FixedCredentialStore(),
            clock=lambda: next(ticks),
        )
        execution.continue_attempt(
            "project-1",
            accepted.attempt.attempt_id,
            candidate_id=accepted.attempt.candidate_id,
            expected_attempt_revision=0,
            execution_fingerprint=accepted.attempt.provider_execution.fingerprint,
        )
        provider.query_status = ProviderTaskStatus.SUCCEEDED
        provider.query_raw_status = "SUCCEEDED"
        provider.query_video_url = "https://provider.example/video.mp4"
        execution.run_attempt("project-1", accepted.attempt.attempt_id)
        completed = execution.run_attempt("project-1", accepted.attempt.attempt_id)

        selection = AtelierGenerationSelectionService(store, clock=lambda: 30.0)
        selected = selection.select_video_candidate(
            "project-1",
            "video",
            accepted.attempt.candidate_id,
            SelectVideoCandidateCommand(expected_revision=1),
        )
        batch_admission = AtelierGenerationAdmissionService(
            store,
            credential_provider=lambda *, provider_model_id, gateway: ResolvedCredential(SCOPE, "credential-ref", SECRET),
            clock=lambda: 40.0,
            id_factory=iter(["attempt-2", "candidate-2", "attempt-3", "candidate-3"]).__next__,
        )
        batch_request = ManualVideoCandidateBatchCommand(**{
            **generation_request().model_dump(mode="json"),
            "expected_revision": 2, "client_request_id": "request-batch", "batch_size": 2,
        })
        batch = batch_admission.admit("project-1", "video", batch_request)
        batch_replayed = batch_admission.read_request("project-1", "request-batch")
        return {
            "accepted": accepted.model_dump(mode="json"),
            "completed": completed.model_dump(mode="json"),
            "selected": selected.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json"),
            "batch_replayed": batch_replayed.model_dump(mode="json"),
        }
    finally:
        store.close()


def _fixture_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_generation_contract_fixtures(tmp_path: Path) -> dict[str, dict[str, Any]]:
    payloads = build_generation_contract_payloads(tmp_path)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        FIXTURE_FILES[name].write_text(_fixture_json(payload))
    return payloads


def test_backend_generated_generation_contract_fixtures_match_committed_json(tmp_path):
    payloads = build_generation_contract_payloads(tmp_path)
    for name, payload in payloads.items():
        assert json.loads(FIXTURE_FILES[name].read_text()) == payload
    fixture_text = "\n".join(FIXTURE_FILES[name].read_text() for name in sorted(FIXTURE_FILES))
    assert SECRET not in fixture_text

    accepted = payloads["accepted"]
    assert accepted["attempt"]["status"] == "pending"
    assert "selected_candidate_id" not in accepted["project"]["nodes"][1]["data"]
    assert accepted["project"]["nodes"][1]["media_urls"] == []

    completed = payloads["completed"]
    assert completed["attempt"]["status"] == "completed"
    assert completed["candidate"]["video_url"] == "video/atelier-candidate-1-attempt-1.mp4"
    assert "selected_candidate_id" not in completed["project"]["nodes"][1]["data"]
    assert completed["project"]["nodes"][1]["media_urls"] == []

    selected = payloads["selected"]
    selected_node = selected["project"]["nodes"][1]
    assert selected["node_ids"] == ["video"]
    assert selected["project"]["revision"] == 2
    assert selected_node["data"]["selected_candidate_id"] == "candidate-1"
    assert selected_node["media_urls"] == ["video/atelier-candidate-1-attempt-1.mp4"]

    batch = payloads["batch"]
    assert "attempt" not in batch and "candidate" not in batch
    assert batch["receipt"]["schema_version"] == 2
    assert [member["attempt"]["attempt_id"] for member in batch["members"]] == ["attempt-2", "attempt-3"]
    assert batch["new_candidate_ids"] == ["candidate-2", "candidate-3"]
    assert [receipt["schema_version"] for receipt in batch["project"]["generation_request_receipts"]] == [1, 2]
    assert payloads["batch_replayed"]["replayed"] is True
    assert payloads["batch_replayed"]["new_candidate_ids"] == []
