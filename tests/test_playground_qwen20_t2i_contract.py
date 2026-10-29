"""New Playground Qwen T2I admissions and unmodified pre-migration receipts."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.playground import service as service_module
from src.apps.playground.models import GenerateRequest
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.models import provider_media_runtime as runtime_module
from src.models.provider_execution import ProviderExecutionRecord
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.media_probe import probe_image_file
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_qwen20_bound_image as qwen_http
from tests import test_wan27_bound_image as bound_http

transport = bound_http.transport
VERSION = "provider-qwen20-t2i-bound-http-v1"
MODEL_IDS = ["qwen-image-2.0", "qwen-image-2.0-pro", "qwen/qwen-image-2.0#image", "qwen/qwen-image-2.0-pro#image"]
LEGACY = Path(__file__).parent / "fixtures" / "playground_qwen20_legacy_5e4e9918.json"


def request(key="qwen20-playground", **changes):
    return GenerateRequest.model_validate({
        "client_request_id": key, "mode": "t2i", "model_id": "qwen-image-2.0-pro",
        "prompt": "A glass moon", **changes,
    })


def service_at(root, **values):
    storage = PlaygroundStorage(root)
    service = PlaygroundService(storage, root, runtime_environment=RuntimeEnvironment({
        "DASHSCOPE_API_KEY": bound_http.SECRET,
        "DASHSCOPE_BASE_URL": bound_http.BASE, "DASHSCOPE_REGION": "cn-beijing", **values,
    }))
    return storage, service


def records(receipt):
    assert receipt is not None and receipt.generation is not None
    return [ProviderExecutionRecord.model_validate(item.provider_execution) for item in receipt.generation.attempts]


def queue_results(transport, count=1):
    transport["queue"] = [response for _ in range(count) for response in (
        bound_http.response(qwen_http.completed()), bound_http.response(bound_http.PNG),
    )]


def legacy_wire():
    return json.loads(LEGACY.read_text())


def install_snapshot(root, snapshot, *, include_output=False):
    (root / "playground_history.json").write_text(json.dumps([snapshot["generation"]]))
    if include_output:
        completed = legacy_wire()["snapshots"]["completed"]["generation"]
        path = root / completed["outputs"][0]["media_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(legacy_wire()["output_base64"]))


@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("explicit", [False, True])
def test_exact_new_model_freezes_profile_defaults_and_real_batch_http(tmp_path, transport, monkeypatch, model, explicit):
    storage, service = service_at(tmp_path)
    queue_results(transport, 2)
    monkeypatch.setattr(runtime_module, "get_catalog_accessor", lambda: pytest.fail("bound runtime re-read live catalog"))
    resolve = Mock(wraps=service_module.resolve_media_runtime_authority)
    monkeypatch.setattr(service_module, "resolve_media_runtime_authority", resolve)
    params = {"size": "768*1024", "seed": 0, "watermark": False, "prompt_extend": False, "negative_prompt": ""} if explicit else {}
    body = request(model_id=model, parameters=params, batch_size=2)
    try:
        receipt = service.admit_generation(body)
        accepted = records(receipt)
        expected = params if explicit else {"size": "1280*1280", "watermark": False, "prompt_extend": True}
        for record in accepted:
            assert record.intent["intent_contract_version"] == VERSION
            assert record.intent["params"] == expected
            assert record.intent["preset_id"] == "playground.qwen20.t2i"
            assert record.intent["preset_version"] == "1"
            assert record.intent["sources"]["params.size"] == ("request" if explicit else "catalog")
            for flag in ("watermark", "prompt_extend"):
                assert record.intent["sources"]["params." + flag] == ("request" if explicit else "preset")
                assert ("params." + flag in record.intent["request_fields"]) is explicit
        assert transport["calls"] == []
        service.runtime_environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": bound_http.SECRET, "DASHSCOPE_BASE_URL": "https://changed.example.invalid"})
        service.process_generation(receipt.generation.id)
        completed = storage.read_request(body.client_request_id)
        assert completed.generation.status == "completed"
        assert len(completed.generation.outputs) == 2
        assert all(call.kwargs["image_contract_version"] == VERSION for call in resolve.call_args_list)
        assert [item[0].method for item in transport["calls"]] == ["POST", "GET", "POST", "GET"]
        for index, (record, original) in enumerate(zip(records(completed), accepted)):
            assert record.intent == original.intent and record.fingerprint == original.fingerprint
            assert record.artifact == record.prepared_artifact
            output = tmp_path / completed.generation.outputs[index].media_path
            assert probe_image_file(output).codec_name == "png"
            assert record.artifact["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
            assert record.artifact["size_bytes"] == output.stat().st_size
            submitted = transport["calls"][2 * index][0]
            assert submitted.url == bound_http.BASE + "/api/v1/services/aigc/multimodal-generation/generation"
            assert "X-DashScope-Async" not in submitted.headers
            assert json.loads(submitted.body)["parameters"] == {**expected, "n": 1}
            assert json.loads(submitted.body)["model"] == model.split("/")[-1].split("#")[0]
        monkeypatch.setattr(service, "_image_generation_intent", lambda *_: pytest.fail("replay normalized"))
        monkeypatch.setattr(service, "_resolve_image_authority", lambda *_a, **_k: pytest.fail("replay rebound"))
        assert records(service.admit_generation(body)) == records(completed)
        service.process_generation(receipt.generation.id)
        assert len(transport["calls"]) == 4
        with pytest.raises(StorageConflictError):
            service.admit_generation(body.model_copy(update={"prompt": "different"}))
    finally:
        storage.close()


@pytest.mark.parametrize("model", MODEL_IDS[:2])
def test_real_http_batch_and_lost_ack_reads_original_without_extra_provider_calls(tmp_path, transport, model):
    queue_results(transport, 2)
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        app.state.playground_service.runtime_environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": bound_http.SECRET})
        body = request(model_id=model, batch_size=2).model_dump(mode="json")
        first = client.post("/playground/generate", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["generation"]["attempts"][0]["provider_execution"]["intent"]["intent_contract_version"] == VERSION
        before = (tmp_path / "playground_history.json").read_bytes()
        recovered = client.get("/playground/requests/qwen20-playground")
        assert recovered.status_code == 200
        assert recovered.json()["generation"]["status"] == "completed"
        assert len(recovered.json()["generation"]["outputs"]) == 2
        assert client.get("/playground/requests/qwen20-playground").json() == recovered.json()
        assert (tmp_path / "playground_history.json").read_bytes() == before
        replay = client.post("/playground/generate", json=body)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["generation"]["id"] == first.json()["generation"]["id"]
        assert len(transport["calls"]) == 4


INVALID = [
    {"parameters": {"seed": -1}}, {"parameters": {"seed": 2147483648}},
    {"parameters": {"seed": True}}, {"parameters": {"seed": None}}, {"parameters": {"seed": 1.5}},
    {"parameters": {"watermark": None}}, {"parameters": {"watermark": 0}},
    {"parameters": {"prompt_extend": None}}, {"parameters": {"prompt_extend": 0}},
    {"parameters": {"negative_prompt": None}}, {"parameters": {"negative_prompt": False}},
    {"parameters": {"negative_prompt": "n" * 501}}, {"negative_prompt": "n" * 501},
    {"negative_prompt": "top", "parameters": {"negative_prompt": "nested"}},
    {"negative_prompt": "", "parameters": {"negative_prompt": False}},
    {"parameters": {"size": "1024x1024"}}, {"parameters": {"size": "768*767"}},
    {"parameters": {"size": None}}, {"parameters": {"size": {"w": 1024}}},
    {"parameters": {"quality": "high"}}, {"parameters": {"n": 1}}, {"parameters": {"unknown": False}},
    {"input_media": ["playground/uploads/missing.png"]}, {"prompt": " "}, {"prompt": "x" * 16385},
]


@pytest.mark.parametrize("changes", INVALID)
def test_invalid_new_http_request_never_binds_writes_or_schedules(tmp_path, transport, monkeypatch, changes):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        service = app.state.playground_service
        service.runtime_environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": bound_http.SECRET})
        store = service._provider_credential_store_instance()
        bind, worker = Mock(wraps=store.bind), Mock()
        monkeypatch.setattr(store, "bind", bind)
        monkeypatch.setattr(service, "process_generation", worker)
        before = service.storage.history_path.read_bytes() if service.storage.history_path.exists() else None
        result = client.post("/playground/generate", json=request(**changes).model_dump(mode="json"))
        assert result.status_code == 422, result.text
        assert result.json()["detail"]["code"] == "invalid_playground_generation_request"
        bind.assert_not_called()
        worker.assert_not_called()
        assert not store.path.exists()
        assert (service.storage.history_path.read_bytes() if service.storage.history_path.exists() else None) == before
        assert client.get("/playground/requests/qwen20-playground").status_code == 404
        assert transport["calls"] == []


@pytest.mark.parametrize("changes", [
    {"prompt": "\ud800"}, {"negative_prompt": "\ud800"},
    {"parameters": {"negative_prompt": "\ud800"}}, {"parameters": {"seed": float("nan")}},
])
def test_non_utf8_or_nonfinite_identity_is_input_error_before_any_side_effect(tmp_path, transport, monkeypatch, changes):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        service = app.state.playground_service
        service.runtime_environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": bound_http.SECRET})
        worker = Mock()
        monkeypatch.setattr(service, "process_generation", worker)
        payload = {"client_request_id": "qwen20-playground", "mode": "t2i", "model_id": MODEL_IDS[0], "prompt": "moon", **changes}
        result = client.post("/playground/generate", content=json.dumps(payload), headers={"Content-Type": "application/json"})
        assert result.status_code == 422, result.text
        assert service.storage.read_request("qwen20-playground") is None
        assert not service._provider_credential_store_instance().path.exists()
        worker.assert_not_called()
        assert transport["calls"] == []


@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("mode", ["t2v", "i2v", "r2v", "v2v"])
def test_new_qwen_image_model_rejects_video_mode_after_old_receipt_replay(tmp_path, transport, monkeypatch, model, mode):
    old = next(item for item in legacy_wire()["snapshots"]["wrong_modes"] if item["request"]["model_id"] == model and item["request"]["mode"] == mode)
    install_snapshot(tmp_path, old["receipt"])
    before = (tmp_path / "playground_history.json").read_bytes()
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        service = app.state.playground_service
        monkeypatch.setattr(service, "process_generation", lambda *_: pytest.fail("receipt replay scheduled provider"))
        monkeypatch.setattr(service, "_resolve_image_authority", lambda *_a, **_k: pytest.fail("wrong mode bound image provider"))
        assert client.post("/playground/generate", json=old["request"]).json()["generation"] == old["receipt"]["generation"]
        result = client.post("/playground/generate", json=request(model_id=model, mode=mode).model_dump(mode="json"))
        assert result.status_code == 422, result.text
        assert (tmp_path / "playground_history.json").read_bytes() == before
        assert transport["calls"] == []


@pytest.mark.parametrize("changes,negative", [
    ({}, None), ({"negative_prompt": None}, None), ({"negative_prompt": ""}, ""),
    ({"parameters": {"negative_prompt": ""}}, ""),
    ({"negative_prompt": "", "parameters": {"negative_prompt": ""}}, ""),
    ({"negative_prompt": "avoid", "parameters": {"negative_prompt": "avoid"}}, "avoid"),
])
def test_negative_presence_defaults_and_equal_dual_values_remain_explicit(tmp_path, transport, changes, negative):
    storage, service = service_at(tmp_path)
    try:
        record = records(service.admit_generation(request(**changes)))[0]
        assert record.intent["intent_contract_version"] == VERSION
        assert ("negative_prompt" in record.intent["params"]) is (negative is not None)
        assert ("params.negative_prompt" in record.intent["request_fields"]) is (negative is not None)
        if negative is not None:
            assert record.intent["params"]["negative_prompt"] == negative
            assert record.intent["sources"]["params.negative_prompt"] == "request"
        assert transport["calls"] == []
    finally:
        storage.close()


@pytest.mark.parametrize("model", MODEL_IDS)
def test_existing_qwen_i2i_keeps_untagged_contract_and_media(tmp_path, transport, model):
    source = tmp_path / "playground/uploads/reference.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(bound_http.PNG)
    storage, service = service_at(tmp_path)
    try:
        receipt = service.admit_generation(request(model_id=model, mode="i2i", input_media=["playground/uploads/reference.png"], parameters={"seed": -1}))
        record = records(receipt)[0]
        assert "intent_contract_version" not in record.intent
        assert record.intent["params"]["seed"] == -1
        assert record.intent["preset_id"] is None
        assert len(record.media) == 1
        assert transport["calls"] == []
    finally:
        storage.close()


@pytest.mark.parametrize("phase", ["accepted", "submitting", "prepared", "completed", "old_invalid"])
def test_frozen_untagged_receipt_is_read_and_replayed_without_current_normalization(tmp_path, monkeypatch, phase):
    frozen = legacy_wire()
    snapshot = frozen["snapshots"][phase]
    body = GenerateRequest.model_validate(frozen["old_invalid_request"] if phase == "old_invalid" else frozen["request"])
    install_snapshot(tmp_path, snapshot, include_output=phase in {"prepared", "completed"})
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, tmp_path, runtime_environment=RuntimeEnvironment({}))
    before = storage.history_path.read_bytes()
    monkeypatch.setattr(service, "_image_generation_intent", lambda *_: pytest.fail("legacy normalized"))
    monkeypatch.setattr(service, "_resolve_image_authority", lambda *_a, **_k: pytest.fail("legacy rebound"))
    try:
        read = storage.read_request(body.client_request_id)
        replay = service.admit_generation(body)
        assert read.generation.model_dump(mode="json") == snapshot["generation"]
        assert replay.generation == read.generation and replay.replayed
        assert "intent_contract_version" not in records(replay)[0].intent
        assert storage.history_path.read_bytes() == before
        with pytest.raises(StorageConflictError):
            service.admit_generation(body.model_copy(update={"prompt": "different"}))
    finally:
        storage.close()


@pytest.mark.parametrize("values", [
    {}, {"DASHSCOPE_API_KEY": bound_http.SECRET, "DASHSCOPE_BASE_URL": "https://ws.cn-beijing.maas.aliyuncs.com", "DASHSCOPE_WORKSPACE_ID": "ws"},
    {"DASHSCOPE_API_KEY": bound_http.SECRET, "DASHSCOPE_BASE_URL": "https://dashscope-us.aliyuncs.com"},
])
def test_unavailable_config_keeps_503_classification_without_admission(tmp_path, transport, monkeypatch, values):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        service = app.state.playground_service
        service.runtime_environment = RuntimeEnvironment(values)
        worker = Mock()
        monkeypatch.setattr(service, "process_generation", worker)
        result = client.post("/playground/generate", json=request().model_dump(mode="json"))
        assert result.status_code == 503, result.text
        assert service.storage.read_request("qwen20-playground") is None
        worker.assert_not_called()
        assert transport["calls"] == []


def test_admission_save_failure_never_schedules_provider(tmp_path, transport, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        service = app.state.playground_service
        service.runtime_environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": bound_http.SECRET})
        monkeypatch.setattr(service.storage, "_save_history", Mock(side_effect=StorageError("history write failed")))
        worker = Mock()
        monkeypatch.setattr(service, "process_generation", worker)
        result = client.post("/playground/generate", json=request().model_dump(mode="json"))
        assert result.status_code == 503, result.text
        assert service.storage.read_request("qwen20-playground") is None
        worker.assert_not_called()
        assert transport["calls"] == []


def test_parallel_same_identity_has_one_generation_and_one_batch(tmp_path, transport):
    storage, service = service_at(tmp_path)
    queue_results(transport, 2)
    body = request(batch_size=2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(service.admit_generation, [body, body]))
        assert len({receipt.generation.id for receipt in receipts}) == 1
        assert all(record.intent["intent_contract_version"] == VERSION for receipt in receipts for record in records(receipt))
        service.process_generation(receipts[0].generation.id)
        service.process_generation(receipts[1].generation.id)
        assert len(transport["calls"]) == 4
    finally:
        storage.close()


def test_changed_credential_is_not_silently_used_by_frozen_request(tmp_path, transport):
    storage, service = service_at(tmp_path)
    try:
        receipt = service.admit_generation(request())
        original = records(receipt)[0]
        service.runtime_environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": "changed-secret"})
        service.process_generation(receipt.generation.id)
        current = storage.read_request("qwen20-playground")
        assert current.generation.status == "needs_confirmation"
        frozen = records(current)[0]
        assert frozen.intent == original.intent and frozen.fingerprint == original.fingerprint
        assert frozen.credential_ref == original.credential_ref
        assert transport["calls"] == []
    finally:
        storage.close()


@pytest.mark.parametrize("failure", ["lost_response", "provider_4xx"])
def test_uncertain_submit_close_reopen_does_not_retry(tmp_path, transport, failure):
    import requests
    storage, service = service_at(tmp_path)
    transport["queue"] = [requests.ConnectionError("synthetic lost response") if failure == "lost_response" else bound_http.response({"error": "synthetic"}, 400)]
    try:
        receipt = service.admit_generation(request())
        original = records(receipt)[0]
        assert original.intent["intent_contract_version"] == VERSION
        service.process_generation(receipt.generation.id)
        current = storage.read_request("qwen20-playground")
        assert current.generation.status == "needs_confirmation"
        assert records(current)[0].phase == "submitting"
        assert records(current)[0].prepared_artifact is None
        storage.close()
        storage = PlaygroundStorage(tmp_path)
        restarted = PlaygroundService(storage, tmp_path, runtime_environment=RuntimeEnvironment({}))
        assert restarted.admit_generation(request()).generation == current.generation
        restarted.process_generation(receipt.generation.id)
        assert storage.read_request("qwen20-playground").generation == current.generation
        assert len(transport["calls"]) == 1
    finally:
        storage.close()


def test_prepared_proof_restarts_with_local_only_repair(tmp_path, transport, monkeypatch):
    storage, service = service_at(tmp_path)
    queue_results(transport)
    receipt = service.admit_generation(request())
    original = records(receipt)[0]
    assert original.intent["intent_contract_version"] == VERSION
    update = storage.update_claimed_generation

    def fail_completion(gen):
        if gen.attempts[0].provider_execution["phase"] == "completed":
            raise StorageError("completion write failed")
        return update(gen)

    monkeypatch.setattr(storage, "update_claimed_generation", fail_completion)
    try:
        with pytest.raises(StorageError, match="completion write failed"):
            service.process_generation(receipt.generation.id)
        prepared = records(storage.read_request("qwen20-playground"))[0]
        assert prepared.phase == "submitting" and prepared.prepared_artifact
        storage.close()
        storage = PlaygroundStorage(tmp_path)
        restarted = PlaygroundService(storage, tmp_path, runtime_environment=RuntimeEnvironment({}))
        monkeypatch.setattr(restarted, "_resolve_record_authority", lambda *_: pytest.fail("repair rebound credentials"))
        restarted.process_generation(receipt.generation.id)
        completed = storage.read_request("qwen20-playground")
        assert completed.generation.status == "completed"
        repaired = records(completed)[0]
        assert repaired.intent == original.intent and repaired.fingerprint == original.fingerprint
        assert repaired.artifact == prepared.prepared_artifact
        output = tmp_path / completed.generation.outputs[0].media_path
        assert hashlib.sha256(output.read_bytes()).hexdigest() == repaired.artifact["sha256"]
        assert len(transport["calls"]) == 2
    finally:
        storage.close()
