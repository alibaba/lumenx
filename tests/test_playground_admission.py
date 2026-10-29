from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.playground.models import GenerateRequest, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.utils.json_storage import StorageConflictError


def request(request_id="request-1", **changes):
    payload = {
        "client_request_id": request_id,
        "mode": "t2v",
        "model_id": "fake",
        "prompt": "A clean test image",
        "parameters": {"seed": 0, "prompt_extend": False},
        "batch_size": 2,
    }
    payload.update(changes)
    return GenerateRequest(**payload)


def runtime(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    return storage, PlaygroundService(storage, data_dir=tmp_path)


def test_same_playground_request_replays_without_new_generation(tmp_path):
    storage, service = runtime(tmp_path)
    first = service.admit_generation(request())
    replay = service.admit_generation(request())

    assert first.replayed is False
    assert replay.replayed is True
    assert first.generation is not None and replay.generation is not None
    assert replay.generation.id == first.generation.id
    assert len(storage.list_history()) == 1
    assert [attempt.ordinal for attempt in replay.generation.attempts] == [0, 1]


def test_same_playground_request_id_cannot_change_intent(tmp_path):
    _, service = runtime(tmp_path)
    service.admit_generation(request())

    with pytest.raises(StorageConflictError, match="different request"):
        service.admit_generation(request(prompt="A changed prompt"))


def test_concurrent_same_playground_request_is_admitted_once(tmp_path):
    storage, service = runtime(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: service.admit_generation(request()), range(2)))

    assert all(item.generation is not None for item in receipts)
    generations = [item.generation for item in receipts if item.generation is not None]
    assert len({item.id for item in generations}) == 1
    assert sorted(item.replayed for item in receipts) == [False, True]
    assert len(storage.list_history()) == 1


def test_deleted_playground_request_returns_tombstone_and_cannot_resurrect(tmp_path):
    storage, service = runtime(tmp_path)
    accepted = service.admit_generation(request(request_id="delete-me"))
    assert accepted.generation is not None
    assert storage.delete_generation(accepted.generation.id)

    receipt = storage.read_request("delete-me")
    assert receipt is not None and receipt.tombstone is not None
    assert receipt.request_status == "deleted"
    assert receipt.tombstone.generation_id == accepted.generation.id
    assert storage.list_history() == []
    replay = service.admit_generation(request(request_id="delete-me"))
    assert replay.request_status == "deleted"


def test_playground_generate_http_returns_receipt_and_readback_without_reschedule(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    calls = []
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.playground_service, "process_generation", lambda generation_id: calls.append(generation_id))
        body = request(request_id="http-request").model_dump(mode="json", exclude_none=True)
        first = client.post("/playground/generate", json=body)
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["schema_version"] == 1
        assert first_body["replayed"] is False
        assert first_body["request_status"] == "accepted"
        assert calls == [first_body["generation"]["id"]]

        replay = client.post("/playground/generate", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert calls == [first_body["generation"]["id"]]

        readback = client.get("/playground/requests/http-request")
        assert readback.status_code == 200, readback.text
        assert readback.json()["generation"]["id"] == first_body["generation"]["id"]


def test_playground_generate_http_conflicts_on_reused_id_with_changed_body(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.playground_service, "process_generation", lambda _: None)
        original = request(request_id="conflict-request").model_dump(mode="json", exclude_none=True)
        changed = {**original, "prompt": "Changed"}
        assert client.post("/playground/generate", json=original).status_code == 200
        conflict = client.post("/playground/generate", json=changed)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "playground_request_conflict"


def test_worker_merge_preserves_attempt_identity_and_partial_failures(tmp_path):
    storage, service = runtime(tmp_path)
    receipt = service.admit_generation(request(request_id="partial-request"))
    assert receipt.generation is not None
    claimed = storage.claim_generation_for_processing(receipt.generation.id)
    assert claimed is not None

    claimed.status = "completed"
    claimed.attempts[0].status = "completed"
    claimed.attempts[1].status = "failed"
    claimed.attempts[1].error = "provider failed"
    from src.apps.playground.models import PlaygroundOutput
    claimed.outputs.append(PlaygroundOutput(
        id="out-1",
        media_path="playground/images/out-1.png",
        media_type="image",
        thumbnail_path=None,
        saved_to_library=False,
        attempt_id=claimed.attempts[0].attempt_id,
    ))

    assert storage.update_claimed_generation(claimed) is True
    saved = storage.get_generation(receipt.generation.id)
    assert saved is not None
    assert saved.status == "completed"
    assert [(item.ordinal, item.attempt_id, item.status, item.error) for item in saved.attempts] == [
        (0, claimed.attempts[0].attempt_id, "completed", None),
        (1, claimed.attempts[1].attempt_id, "failed", "provider failed"),
    ]
    assert [output.attempt_id for output in saved.outputs] == [claimed.attempts[0].attempt_id]
    replay = storage.read_request("partial-request")
    assert replay is not None and replay.generation is not None
    assert replay.generation.attempts[1].status == "failed"


def test_worker_merge_ignores_unknown_attempts_and_terminal_regression(tmp_path):
    storage, service = runtime(tmp_path)
    receipt = service.admit_generation(request(request_id="stale-worker-request", batch_size=1))
    assert receipt.generation is not None
    claimed = storage.claim_generation_for_processing(receipt.generation.id)
    assert claimed is not None
    claimed.status = "completed"
    claimed.attempts[0].status = "completed"
    from src.apps.playground.models import PlaygroundAttempt, PlaygroundOutput
    claimed.outputs.append(PlaygroundOutput(
        id="out-1",
        media_path="playground/images/out-1.png",
        media_type="image",
        thumbnail_path=None,
        saved_to_library=False,
        attempt_id=claimed.attempts[0].attempt_id,
    ))
    assert storage.update_claimed_generation(claimed) is True

    stale = claimed.model_copy(deep=True)
    stale.status = "processing"
    stale.attempts[0].status = "processing"
    stale.attempts.append(PlaygroundAttempt(
        attempt_id="unknown-attempt",
        ordinal=99,
        status="completed",
        error=None,
        provider_execution={"phase": "completed"},
    ))
    stale.outputs.append(PlaygroundOutput(
        id="unknown-output",
        media_path="playground/images/unknown.png",
        media_type="image",
        thumbnail_path=None,
        saved_to_library=False,
        attempt_id="unknown-attempt",
    ))

    storage.update_generation(stale)
    saved = storage.get_generation(receipt.generation.id)
    assert saved is not None
    assert saved.status == "completed"
    assert [(item.ordinal, item.attempt_id, item.status) for item in saved.attempts] == [
        (0, claimed.attempts[0].attempt_id, "completed")
    ]
    assert [output.id for output in saved.outputs] == ["out-1"]

    stale.outputs.append(PlaygroundOutput(
        id="late-known-output", media_path="playground/images/late.png", media_type="image",
        thumbnail_path=None,
        saved_to_library=False,
        attempt_id=claimed.attempts[0].attempt_id,
    ))
    storage.update_generation(stale)
    after_late_output = storage.get_generation(receipt.generation.id)
    assert after_late_output is not None
    assert after_late_output.status == "completed"
    assert after_late_output.attempts[0].status == "completed"


def test_playground_generate_http_rejects_invalid_client_request_id_without_schedule(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    calls = []
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.playground_service, "process_generation", lambda generation_id: calls.append(generation_id))
        base = request(request_id="valid-request").model_dump(mode="json", exclude_none=True)
        for bad_id in ("", "   ", "x" * 129):
            response = client.post("/playground/generate", json={**base, "client_request_id": bad_id})
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "invalid_playground_request_id"
        bad_type = client.post("/playground/generate", json={**base, "client_request_id": 123})
        assert bad_type.status_code == 422, bad_type.text
        assert calls == []


@pytest.mark.parametrize("changes", [
    {"model_id": "unknown-image"},
    {"parameters": {"seed": True}},
    {"mode": "i2i", "input_media": []},
    {"mode": "i2i", "input_media": ["playground/uploads/missing.png"]},
])
def test_image_admission_invalid_intent_is_422_without_receipt_or_worker(tmp_path, monkeypatch, changes):
    app = create_app(data_dir=tmp_path, load_config=False)
    calls = []
    with TestClient(app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(app.state.playground_service, "process_generation", calls.append)
        body = request(mode="t2i", model_id="wan2.7-image-pro", parameters={"seed": 0}).model_dump(mode="json", exclude_none=True)
        body.update(changes)
        response = client.post("/playground/generate", json=body)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_playground_generation_request"
        assert calls == []
        assert client.get("/playground/history").json() == []
        assert client.get("/playground/requests/request-1").status_code == 404


def test_image_admission_without_credentials_is_unavailable_and_not_accepted(tmp_path, monkeypatch):
    from src.utils.runtime_environment import RuntimeEnvironment

    app = create_app(data_dir=tmp_path, load_config=False)
    calls = []
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.playground_service.runtime_environment = RuntimeEnvironment({})
        monkeypatch.setattr(app.state.playground_service, "process_generation", calls.append)
        body = request(mode="t2i", model_id="wan2.7-image-pro", parameters={"seed": 0}).model_dump(mode="json", exclude_none=True)
        response = client.post("/playground/generate", json=body)
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "playground_generation_unavailable"
        assert calls == []
        assert client.get("/playground/history").json() == []


@pytest.mark.parametrize("failure_type", ["authority", "binding", "unexpected"])
def test_admission_error_mapping_is_typed_and_does_not_echo_private_details(tmp_path, monkeypatch, failure_type):
    from src.models.provider_media_runtime import MediaRuntimeAuthorityError
    from src.models.provider_tasks import ProviderBindingError

    errors = {"authority": MediaRuntimeAuthorityError, "binding": ProviderBindingError, "unexpected": RuntimeError}
    app = create_app(data_dir=tmp_path, load_config=False)
    calls = []
    with TestClient(app, raise_server_exceptions=False) as client:
        def fail(_request):
            raise errors[failure_type]("private-configuration-sentinel")
        monkeypatch.setattr(app.state.playground_service, "admit_generation", fail)
        monkeypatch.setattr(app.state.playground_service, "process_generation", calls.append)
        response = client.post("/playground/generate", json=request().model_dump(mode="json", exclude_none=True))
        assert response.status_code == (500 if failure_type == "unexpected" else 503)
        assert "private-configuration-sentinel" not in response.text
        assert calls == []
        assert client.get("/playground/history").json() == []


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_legacy_worker_with_new_output_cannot_reopen_terminal_generation(tmp_path, terminal_status):
    from src.apps.playground.models import PlaygroundGeneration, PlaygroundOutput

    storage, _ = runtime(tmp_path)
    legacy = PlaygroundGeneration(
        schema_version=1, client_request_id=None, request_contract_version=None,
        request_fingerprint=None, negative_prompt=None, batch_size=1, error=None,
        id="legacy-terminal", mode=PlaygroundMode.T2V, model_id="wan2.7-t2v", prompt="test",
        created_at="2026-10-13T00:00:00Z", status=terminal_status,
    )
    storage.add_generation(legacy)
    stale = legacy.model_copy(deep=True)
    stale.status = "processing"
    stale.outputs.append(PlaygroundOutput(
        id="late-output", media_path="playground/videos/late.mp4", media_type="video",
        thumbnail_path=None, saved_to_library=False, attempt_id=None,
    ))
    storage.update_generation(stale)
    saved = storage.get_generation(legacy.id)
    assert saved is not None
    assert saved.status == terminal_status
