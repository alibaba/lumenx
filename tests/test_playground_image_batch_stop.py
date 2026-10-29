"""A known ambiguous member stops this Playground batch without losing its receipt."""

import hashlib

from fastapi.testclient import TestClient
import pytest
import requests

from src.apps.comic_gen.api import create_app
from src.utils.media_probe import probe_image_file
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_qwen20_bound_image as qwen_http
from tests import test_wan27_bound_image as bound_http


transport = bound_http.transport
MODELS = ("qwen-image-2.0", "qwen-image-2.0-pro")
VERSION = "provider-qwen20-t2i-bound-http-v1"


def payload(model):
    return {
        "client_request_id": "qwen-batch-stop",
        "mode": "t2i",
        "model_id": model,
        "prompt": "A glass moon",
        "batch_size": 2,
        "parameters": {
            "size": "1024*1024", "seed": 0, "watermark": False,
            "prompt_extend": False, "negative_prompt": "",
        },
    }


def configure(app):
    app.state.playground_service.runtime_environment = RuntimeEnvironment({
        "DASHSCOPE_API_KEY": bound_http.SECRET,
        "DASHSCOPE_BASE_URL": bound_http.BASE,
        "DASHSCOPE_REGION": "cn-beijing",
    })


def read_generation(client):
    response = client.get("/playground/requests/qwen-batch-stop")
    assert response.status_code == 200, response.text
    return response.json()["generation"]


def assert_original_identity(admitted, current):
    for name in ("id", "client_request_id", "request_contract_version", "request_fingerprint"):
        assert current[name] == admitted[name]
    assert len(current["attempts"]) == len(admitted["attempts"]) == 2
    for original, attempt in zip(admitted["attempts"], current["attempts"], strict=True):
        assert (attempt["attempt_id"], attempt["ordinal"]) == (original["attempt_id"], original["ordinal"])
        old, record = original["provider_execution"], attempt["provider_execution"]
        assert old["phase"] == "accepted"
        assert record["intent"]["intent_contract_version"] == VERSION
        for name in ("intent", "fingerprint", "provider_model_id", "scope", "credential_ref", "output_ref"):
            assert record[name] == old[name]


def assert_read_and_replay_preserve(client, root, body, expected, transport, methods):
    before = (root / "playground_history.json").read_bytes()
    assert read_generation(client) == expected
    replay = client.post("/playground/generate", json=body)
    assert replay.status_code == 200 and replay.json()["replayed"], replay.text
    assert replay.json()["generation"] == expected
    assert read_generation(client) == expected
    assert (root / "playground_history.json").read_bytes() == before
    assert [request.method for request, _ in transport["calls"]] == methods


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("failure", ["lost_response", "credentials_removed", "credentials_changed"])
def test_first_unknown_member_keeps_remaining_members_unstarted_after_http_and_reopen(
    tmp_path, monkeypatch, transport, model, failure,
):
    body = payload(model)
    methods = ["POST"] if failure == "lost_response" else []
    transport["queue"] = []
    if failure == "lost_response":
        transport["queue"] = [requests.ConnectionError("synthetic lost batch response")]
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        configure(app)
        service = app.state.playground_service
        if failure != "lost_response":
            admit = service.storage.admit_generation

            def change_credentials_after_real_admission(generation):
                receipt = admit(generation)
                assert all(item.provider_execution["phase"] == "accepted" for item in receipt.generation.attempts)
                if failure == "credentials_removed":
                    service.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)
                else:
                    service.runtime_environment.update({"DASHSCOPE_API_KEY": "changed-synthetic-secret"}, persist=lambda *_: None)
                return receipt

            monkeypatch.setattr(service.storage, "admit_generation", change_credentials_after_real_admission)

        # TestClient runs the real background worker before returning. A normal
        # unknown outcome must not escape as a conflict on the next member.
        response = client.post("/playground/generate", json=body)
        assert response.status_code == 200, response.text
        admitted = response.json()["generation"]
        current = read_generation(client)
        assert_original_identity(admitted, current)
        assert current["status"] == "needs_confirmation" and current["error"] is None
        assert current["outputs"] == []
        first, second = current["attempts"]
        assert first["status"] == "needs_confirmation"
        assert first["provider_execution"]["phase"] == "submitting"
        assert first["provider_execution"]["prepared_artifact"] is None
        assert first["provider_execution"]["artifact"] is None
        # The existing generation claim marks every member processing. The
        # untouched accepted execution record proves this member never started.
        assert second["status"] == "processing" and second["error"] is None
        assert second["provider_execution"] == admitted["attempts"][1]["provider_execution"]
        assert not list((tmp_path / "playground/images").glob("*.png"))
        assert_read_and_replay_preserve(client, tmp_path, body, current, transport, methods)

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    reopened = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(reopened) as client:
        assert not reopened.state.playground_service.runtime_environment.snapshot().get("DASHSCOPE_API_KEY")
        assert_read_and_replay_preserve(client, tmp_path, body, current, transport, methods)
    assert transport["queue"] == []


@pytest.mark.parametrize("model", MODELS)
def test_completed_member_keeps_png_and_proof_when_next_member_is_unknown(tmp_path, monkeypatch, transport, model):
    body = payload(model)
    transport["queue"] = [
        bound_http.response(qwen_http.completed()), bound_http.response(bound_http.PNG),
        requests.ConnectionError("synthetic second member response lost"),
    ]
    methods = ["POST", "GET", "POST"]
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        configure(app)
        response = client.post("/playground/generate", json=body)
        assert response.status_code == 200, response.text
        admitted = response.json()["generation"]
        current = read_generation(client)
        assert_original_identity(admitted, current)
        assert current["status"] == "needs_confirmation" and current["error"] is None
        first, second = current["attempts"]
        assert first["status"] == "completed"
        record = first["provider_execution"]
        assert record["phase"] == "completed"
        assert record["artifact"] == record["prepared_artifact"]
        assert len(current["outputs"]) == 1
        output = current["outputs"][0]
        assert output["attempt_id"] == first["attempt_id"]
        assert output["media_path"] == record["output_ref"] == record["artifact"]["relative_path"]
        image = tmp_path / output["media_path"]
        assert probe_image_file(image).codec_name == "png"
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        assert digest == record["artifact"]["sha256"]
        assert image.stat().st_size == record["artifact"]["size_bytes"]
        assert second["status"] == "needs_confirmation"
        assert second["provider_execution"]["phase"] == "submitting"
        assert second["provider_execution"]["prepared_artifact"] is None
        assert second["provider_execution"]["artifact"] is None
        assert_read_and_replay_preserve(client, tmp_path, body, current, transport, methods)

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    reopened = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(reopened) as client:
        assert not reopened.state.playground_service.runtime_environment.snapshot().get("DASHSCOPE_API_KEY")
        assert_read_and_replay_preserve(client, tmp_path, body, current, transport, methods)
        assert hashlib.sha256(image.read_bytes()).hexdigest() == digest
        assert probe_image_file(image).codec_name == "png"
    assert transport["queue"] == []


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("boundary", ["last_submit", "after_unknown_read"])
def test_terminal_change_wins_at_batch_stop_boundary(tmp_path, monkeypatch, transport, model, boundary):
    body = payload(model)
    methods = ["POST", "GET", "POST"] if boundary == "last_submit" else ["POST"]
    terminal = []
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        configure(app)
        storage = app.state.playground_service.storage

        def end_generation(current):
            winner = current.model_copy(deep=True)
            winner.status = "completed"
            winner.error = None
            storage.update_generation(winner)
            terminal.append(storage.read_request(body["client_request_id"]).generation.model_dump(mode="json"))

        if boundary == "last_submit":
            class Responses(list):
                def pop(self, index=0):
                    if len(self) == 1:
                        assert transport["calls"][-1][0].method == "POST"
                        end_generation(storage.read_request(body["client_request_id"]).generation)
                    return super().pop(index)

            transport["queue"] = Responses([
                bound_http.response(qwen_http.completed()), bound_http.response(bound_http.PNG),
                requests.ConnectionError("synthetic response lost after terminal change"),
            ])
        else:
            transport["queue"] = [requests.ConnectionError("synthetic unknown result")]
            read = storage.get_generation

            def end_after_unknown_snapshot(generation_id):
                current = read(generation_id)
                if current is not None and current.status == "needs_confirmation" and not terminal:
                    end_generation(current)
                return current

            monkeypatch.setattr(storage, "get_generation", end_after_unknown_snapshot)

        response = client.post("/playground/generate", json=body)
        assert response.status_code == 200, response.text
        assert len(terminal) == 1
        current = read_generation(client)
        assert current == terminal[0] and current["status"] == "completed"
        images = {}
        for output in current["outputs"]:
            image = tmp_path / output["media_path"]
            assert probe_image_file(image).codec_name == "png"
            images[image] = hashlib.sha256(image.read_bytes()).hexdigest()
            assert images[image] == current["attempts"][0]["provider_execution"]["artifact"]["sha256"]
        assert len(images) == (1 if boundary == "last_submit" else 0)
        assert_read_and_replay_preserve(client, tmp_path, body, current, transport, methods)

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    reopened = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(reopened) as client:
        assert not reopened.state.playground_service.runtime_environment.snapshot().get("DASHSCOPE_API_KEY")
        assert_read_and_replay_preserve(client, tmp_path, body, current, transport, methods)
        for image, digest in images.items():
            assert hashlib.sha256(image.read_bytes()).hexdigest() == digest
    assert transport["queue"] == []
