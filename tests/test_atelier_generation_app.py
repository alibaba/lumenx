"""The application composes Atelier generation without Studio product calls."""

from dataclasses import replace
import threading
from unittest.mock import Mock

import pytest

from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app, _InjectedProviderRuntimeSelector, _refresh_managed_provider_recovery_runtime
from src.models.provider_runtime import ProviderRecoveryRuntime
from tests.test_atelier_generation_admission import PNG, request
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.models.provider_tasks import ProviderTaskStatus
from tests.test_atelier_generation_execution import FakeProvider




def install_provider_runtime(app, runtime):
    selector = _InjectedProviderRuntimeSelector(runtime)
    app.state.provider_recovery_runtime = runtime
    app.state.provider_runtime_selector = selector
    app.state.pipeline.provider_recovery_runtime = runtime
    app.state.pipeline.provider_runtime_selector = selector

def seed(app, root):
    image = root / "uploads/frame.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG)
    return app.state.atelier_storage.create_project(AtelierProject(
        id="p", title="Independent canvas", created_at=1, updated_at=1,
        nodes=[
            AtelierNode(id="image", project_id="p", type="image", media_urls=["uploads/frame.png"]),
            AtelierNode(id="video", project_id="p", type="video", data={"reference_node_ids": ["image"]}),
        ],
    ))


def test_app_generation_admission_is_independent_of_studio_projects(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        seed(app, tmp_path)
        runtime = app.state.provider_recovery_runtime
        assert isinstance(runtime, ProviderRecoveryRuntime)
        install_provider_runtime(app, replace(runtime, secret_resolver=lambda: "fixture-secret"))
        ack = app.state.atelier_generation_admission.admit("p", "video", request())
        assert ack.attempt.provider_execution.phase == "accepted"
        assert app.state.pipeline.read_committed_projects() == []
        assert app.state.atelier_generation_execution.read_attempt("p", ack.attempt.attempt_id).attempt == ack.attempt
        assert "fixture-secret" not in (tmp_path / "provider_credentials.json").read_text()
        assert not app.state.atelier_generation_dispatcher.is_running


def test_managed_runtime_refresh_updates_both_product_capabilities(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        before = app.state.provider_recovery_runtime
        app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "new-fixture-key"}, persist=lambda *_: None)
        _refresh_managed_provider_recovery_runtime(app)
        after = app.state.provider_recovery_runtime
        assert after is app.state.pipeline.provider_recovery_runtime
        assert after is not before and after.resolve_secret() == "new-fixture-key"
        assert before.secret_resolver() != after.resolve_secret()


def test_generation_service_and_dispatcher_are_sealed_on_app_shutdown(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        seed(app, tmp_path)
        admission = app.state.atelier_generation_admission
        dispatcher = app.state.atelier_generation_dispatcher
    from src.utils.json_storage import StorageConflictError
    with pytest.raises(StorageConflictError):
        admission.admit("p", "video", request())
    with pytest.raises(StorageConflictError):
        dispatcher.schedule("p", "attempt")
    assert not hasattr(app.state, "atelier_generation_execution")
    assert not hasattr(app.state, "provider_recovery_runtime")


def test_real_app_http_receipt_and_original_attempt_progress(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed(app, tmp_path)
        provider = FakeProvider()
        install_provider_runtime(app, replace(app.state.provider_recovery_runtime, provider=provider, secret_resolver=lambda: "fixture-secret"))
        schedule = Mock(return_value=True)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", schedule)
        path = "/atelier/projects/p/nodes/video/video_candidates"
        payload = request().model_dump(exclude_unset=True)
        created = client.post(path, json=payload)
        assert created.status_code == 200, created.text
        ack = created.json()
        attempt_id = ack["attempt"]["attempt_id"]
        schedule.assert_called_once_with("p", attempt_id)
        assert provider.submits == []
        replay = client.post(path, json=payload)
        assert replay.status_code == 200 and replay.json()["new_candidate_ids"] == []
        assert schedule.call_count == 1
        readback = client.get("/atelier/projects/p/generation/requests/request-1")
        assert readback.status_code == 200 and readback.json()["receipt"] == ack["receipt"]
        attempt_path = f"/atelier/projects/p/generation/attempts/{attempt_id}"
        provider.query_status = ProviderTaskStatus.SUCCEEDED
        provider.query_raw_status = "SUCCEEDED"
        provider.query_video_url = "https://provider.example/video.mp4"
        for expected in ["submitted", "observed", "completed"]:
            current = client.get(attempt_path).json()["attempt"]
            result = client.post(attempt_path + "/continue", json={
                "candidate_id": current["candidate_id"],
                "expected_attempt_revision": current["attempt_revision"],
                "execution_fingerprint": current["provider_execution"]["fingerprint"],
            })
            assert result.status_code == 200, result.text
            assert result.json()["attempt"]["provider_execution"]["phase"] == expected
        final = result.json()
        assert len(provider.submits) == len(provider.queries) == len(provider.fetches) == 1
        assert final["project"]["revision"] == 1
        node = next(n for n in final["project"]["nodes"] if n["id"] == "video")
        assert node["media_urls"] == [] and "selected_candidate_id" not in node["data"]
        assert client.get("/files/" + final["attempt"]["output_ref"]).content == provider.fetch_bytes
        assert app.state.pipeline.read_committed_projects() == []
        assert "fixture-secret" not in result.text
        chosen = client.post(path + f"/{final['attempt']['candidate_id']}/select", json={"expected_revision": 1})
        assert chosen.status_code == 200, chosen.text
        selected_project = chosen.json()["project"]
        selected_node = next(n for n in selected_project["nodes"] if n["id"] == "video")
        assert selected_node["data"]["selected_candidate_id"] == final["attempt"]["candidate_id"]
        assert selected_node["media_urls"] == [final["attempt"]["output_ref"]]
        assert selected_project["revision"] == 2
        assert selected_project["generation_attempts"] == final["project"]["generation_attempts"]
        assert len(provider.submits) == len(provider.queries) == len(provider.fetches) == 1


def test_saved_request_survives_dispatch_failure_and_replay_does_not_reschedule(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed(app, tmp_path)
        install_provider_runtime(app, replace(app.state.provider_recovery_runtime, secret_resolver=lambda: "fixture-secret"))
        schedule = Mock(side_effect=RuntimeError("private worker detail"))
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", schedule)
        path = "/atelier/projects/p/nodes/video/video_candidates"
        payload = request().model_dump(exclude_unset=True)
        failed = client.post(path, json=payload)
        assert failed.status_code == 503 and "private worker detail" not in failed.text
        assert failed.json()["detail"]["client_request_id"] == "request-1"
        restored = client.get("/atelier/projects/p/generation/requests/request-1")
        assert restored.status_code == 200 and restored.json()["attempt"]["status"] == "pending"
        replay = client.post(path, json=payload)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert schedule.call_count == 1


@pytest.mark.parametrize("field,value", [("expected_revision", float("nan")), ("expected_revision", True), ("credential_ref", "forged")])
def test_real_app_rejects_invalid_generation_before_storage(tmp_path, field, value):
    import json
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed(app, tmp_path)
        payload = request().model_dump(exclude_unset=True)
        payload[field] = value
        response = client.post("/atelier/projects/p/nodes/video/video_candidates", content=json.dumps(payload), headers={"Content-Type": "application/json"})
        assert response.status_code == 422, response.text
        assert app.state.atelier_storage.get_project("p").generation_attempts == []


def test_remote_generation_endpoints_require_access_authority(tmp_path):
    token = "test-generation-access-token-1234567890123456"
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False, access_mode="remote", access_token=token)
    with TestClient(app) as client:
        seed(app, tmp_path)
        denied = client.post("/atelier/projects/p/nodes/video/video_candidates", json=request().model_dump())
        assert denied.status_code == 401
        assert app.state.atelier_storage.get_project("p").generation_attempts == []
        read = client.get("/atelier/projects/p/generation/requests/absent", headers={"Authorization": f"Bearer {token}"})
        assert read.status_code == 404


def test_real_app_dispatcher_advances_new_attempt_to_completed(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed(app, tmp_path)
        provider = FakeProvider()
        provider.query_status = ProviderTaskStatus.SUCCEEDED
        provider.query_raw_status = "SUCCEEDED"
        provider.query_video_url = "https://provider.example/video.mp4"
        install_provider_runtime(app, replace(app.state.provider_recovery_runtime, provider=provider, secret_resolver=lambda: "fixture-secret"))
        dispatcher = app.state.atelier_generation_dispatcher
        dispatcher._interval_seconds = 0.01
        completed = threading.Event()
        store = app.state.atelier_storage
        original = store._commit
        def observed_commit(projects):
            result = original(projects)
            if any(a.status == "completed" for a in result["p"].generation_attempts):
                completed.set()
            return result
        monkeypatch.setattr(store, "_commit", observed_commit)
        response = client.post("/atelier/projects/p/nodes/video/video_candidates", json=request().model_dump(exclude_unset=True))
        assert response.status_code == 200, response.text
        assert completed.wait(3), "Owned dispatcher did not finish submit/query/fetch"
        readback = client.get("/atelier/projects/p/generation/requests/request-1").json()
        assert readback["attempt"]["status"] == "completed"
        assert len(provider.submits) == len(provider.queries) == len(provider.fetches) == 1


def test_two_app_generation_bindings_and_runtime_refresh_stay_separate(tmp_path):
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    a = create_app(data_dir=root_a, load_config=False, recover_orphan_tasks=False)
    b = create_app(data_dir=root_b, load_config=False, recover_orphan_tasks=False)
    with TestClient(a), TestClient(b):
        for app, root, secret in [(a, root_a, "fixture-a"), (b, root_b, "fixture-b")]:
            seed(app, root)
            app.state.runtime_environment.update({"DASHSCOPE_API_KEY": secret}, persist=lambda *_: None)
            _refresh_managed_provider_recovery_runtime(app)
        ack_a = a.state.atelier_generation_admission.admit("p", "video", request())
        ack_b = b.state.atelier_generation_admission.admit("p", "video", request())
        assert ack_a.attempt.provider_execution.credential_ref != ack_b.attempt.provider_execution.credential_ref
        before_b = (root_b / "provider_credentials.json").read_bytes()
        a.state.runtime_environment.update({"DASHSCOPE_API_KEY": "fixture-a-new"}, persist=lambda *_: None)
        _refresh_managed_provider_recovery_runtime(a)
        assert b.state.provider_recovery_runtime.resolve_secret() == "fixture-b"
        assert (root_b / "provider_credentials.json").read_bytes() == before_b
        replay = a.state.atelier_generation_admission.admit("p", "video", request())
        assert replay.attempt.provider_execution.credential_ref == ack_a.attempt.provider_execution.credential_ref
