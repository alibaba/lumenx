"""Real Playground admissions choose strict Wan2.7 only for new exact T2I requests."""

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.playground.models import GenerateRequest, PlaygroundAttempt, PlaygroundGeneration
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.models import provider_media_runtime as runtime_module
from src.apps.playground import service as service_module
from src.models.image import WanxImageModel
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_execution import execute_sync_artifact_to_private_file
from src.models.provider_media_intents import build_accepted_sync_artifact_record
from src.models.provider_media_runtime import bind_media_runtime_authority
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.media_probe import probe_image_file
from src.utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION, fingerprint_request
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_wan27_bound_image as bound_http

transport = bound_http.transport
VERSION = "provider-wan27-t2i-bound-http-v1"
MODEL_IDS = ["wan2.7-image", "wan2.7-image-pro", "wan/wan2.7-image#image", "wan/wan2.7-image-pro#image"]


def request(key="wan27-playground", **changes):
    return GenerateRequest.model_validate({
        "client_request_id": key, "mode": "t2i", "model_id": "wan2.7-image-pro",
        "prompt": "A glass moon", **changes,
    })


def service_at(root, **values):
    storage = PlaygroundStorage(root)
    service = PlaygroundService(storage, root, runtime_environment=RuntimeEnvironment({
        "DASHSCOPE_API_KEY": bound_http.SECRET,
        "DASHSCOPE_BASE_URL": bound_http.BASE, "DASHSCOPE_REGION": "cn-beijing",
        **values,
    }))
    return storage, service


def saved_record(receipt):
    assert receipt.generation is not None
    return ProviderExecutionRecord.model_validate(receipt.generation.attempts[0].provider_execution)


@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("explicit", [False, True])
def test_real_service_adapter_freezes_defaults_and_publishes_once(tmp_path, transport, monkeypatch, model, explicit):
    storage, service = service_at(tmp_path)
    # The strict runtime uses the accepted identity, not a second live catalog lookup.
    monkeypatch.setattr(runtime_module, "get_catalog_accessor", lambda: pytest.fail("runtime re-read catalog"))
    resolve = Mock(wraps=service_module.resolve_media_runtime_authority)
    monkeypatch.setattr(service_module, "resolve_media_runtime_authority", resolve)
    params = {"seed": 0, "watermark": False, "size": "768*1024"} if explicit else {}
    body = request(model_id=model, parameters=params)
    try:
        first = service.admit_generation(body)
        accepted = saved_record(first)
        assert accepted.intent["intent_contract_version"] == VERSION
        assert accepted.intent["params"] == (params if explicit else {"size": "1280*1280", "watermark": False})
        sources = accepted.intent["sources"]
        assert sources["params.watermark"] == ("request" if explicit else "preset")
        assert sources["params.size"] == ("request" if explicit else "catalog")
        assert ("params.watermark" in accepted.intent["request_fields"]) is explicit
        assert accepted.intent["preset_id"] and accepted.intent["preset_version"]
        assert accepted.scope["workspace_id"] is None
        assert transport["calls"] == []
        assert first.generation is not None
        service.runtime_environment = RuntimeEnvironment({
            "DASHSCOPE_API_KEY": bound_http.SECRET,
            "DASHSCOPE_BASE_URL": "https://changed.example.invalid",
            "DASHSCOPE_WORKSPACE_ID": "changed-workspace",
        })
        service.process_generation(first.generation.id)
        assert resolve.call_args.kwargs["image_contract_version"] == VERSION
        completed = storage.read_request(body.client_request_id)
        assert completed is not None and completed.generation is not None
        assert completed.generation.status == "completed"
        record = saved_record(completed)
        assert record.fingerprint == accepted.fingerprint
        assert record.artifact == record.prepared_artifact
        assert len(completed.generation.outputs) == 1
        output = tmp_path / completed.generation.outputs[0].media_path
        assert probe_image_file(output).codec_name == "png"
        assert record.artifact is not None
        assert record.artifact["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        assert record.artifact["size_bytes"] == output.stat().st_size
        assert [item[0].method for item in transport["calls"]] == ["POST", "GET", "GET"]
        submitted = json.loads(transport["calls"][0][0].body)
        assert submitted["parameters"] == {**accepted.intent["params"], "n": 1}
        assert "X-DashScope-WorkSpace" not in transport["calls"][0][0].headers
        monkeypatch.setattr(service, "_image_generation_intent", lambda *_: pytest.fail("replay normalized"))
        service.runtime_environment = RuntimeEnvironment({})
        replay = service.admit_generation(body)
        assert replay.replayed and saved_record(replay) == record
        service.process_generation(first.generation.id)
        assert len(transport["calls"]) == 3
        with pytest.raises(StorageConflictError):
            service.admit_generation(body.model_copy(update={"prompt": "different"}))
    finally:
        storage.close()


INVALID = [
    {"negative_prompt": ""}, {"negative_prompt": "avoid"},
    {"parameters": {"negative_prompt": None}}, {"parameters": {"negative_prompt": ""}},
    {"parameters": {"negativePrompt": "avoid"}},
    {"parameters": {"prompt_extend": False}}, {"parameters": {"prompt_extend": None}},
    {"parameters": {"promptExtend": False}},
    {"parameters": {"seed": -1}}, {"parameters": {"seed": None}},
    {"parameters": {"seed": 2147483648}}, {"parameters": {"seed": True}},
    {"parameters": {"watermark": None}}, {"parameters": {"watermark": 0}},
    {"parameters": {"size": "1024x1024"}}, {"parameters": {"size": "768*767"}},
    {"parameters": {"quality": "high"}}, {"parameters": {"n": 1}},
    {"parameters": {"unknown": False}}, {"input_media": ["playground/uploads/missing.png"]},
    {"prompt": " "}, {"prompt": "x" * 5001},
]


@pytest.mark.parametrize("changes", INVALID)
def test_invalid_new_request_http_rejects_before_binding_or_receipt(tmp_path, transport, monkeypatch, changes):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        service = app.state.playground_service
        service.runtime_environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": bound_http.SECRET})
        store = service._provider_credential_store_instance()
        bind = Mock(wraps=store.bind)
        monkeypatch.setattr(store, "bind", bind)
        worker = Mock()
        monkeypatch.setattr(service, "process_generation", worker)
        response = client.post("/playground/generate", json=request(**changes).model_dump(mode="json"))
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_playground_generation_request"
        bind.assert_not_called()
        worker.assert_not_called()
        assert not store.path.exists()
        assert client.get("/playground/history").json() == []
        assert client.get("/playground/requests/wan27-playground").status_code == 404
        assert transport["calls"] == []


@pytest.mark.parametrize("values", [
    {}, {"DASHSCOPE_API_KEY": bound_http.SECRET, "DASHSCOPE_BASE_URL": "https://ws.cn-beijing.maas.aliyuncs.com", "DASHSCOPE_WORKSPACE_ID": "ws"},
    {"DASHSCOPE_API_KEY": bound_http.SECRET, "DASHSCOPE_BASE_URL": "https://dashscope-us.aliyuncs.com"},
])
def test_unavailable_configuration_is_not_an_input_error(tmp_path, transport, monkeypatch, values):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        service = app.state.playground_service
        service.runtime_environment = RuntimeEnvironment(values)
        monkeypatch.setattr(service, "process_generation", Mock())
        store = service._provider_credential_store_instance()
        bind = Mock(wraps=store.bind)
        monkeypatch.setattr(store, "bind", bind)
        response = client.post("/playground/generate", json=request().model_dump(mode="json"))
        assert response.status_code == 503, response.text
        bind.assert_not_called()
        assert service.storage.read_request("wan27-playground") is None
        assert transport["calls"] == []


def legacy_admission(service, body):
    """Persist a pre-migration frozen record without using today's admission policy."""
    params = {**(body.parameters or {})}
    if body.negative_prompt is not None:
        params["negative_prompt"] = body.negative_prompt
    intent = normalize_generation_intent(
        {"mode": body.mode.value, "model_id": body.model_id, "prompt": body.prompt, "params": params},
        allowed_modes={"t2i"}, allowed_parameters={"size", "seed", "watermark", "negative_prompt", "prompt_extend"},
    )
    with bind_media_runtime_authority(service.runtime_environment, service._provider_credential_store_instance(), image_intent=intent) as authority:
        record = build_accepted_sync_artifact_record(
            intent=intent, provider_model_id=authority.provider_model_id, scope=authority.scope,
            credential_ref=authority.credential_ref, media=[], media_kind="image",
            attempt_id=body.client_request_id + "-0", output_ref="playground/images/legacy.png",
        )
    gen = PlaygroundGeneration(
        **body.model_dump(exclude={"input_media", "parameters", "batch_size"}),
        id="legacy-generation", schema_version=1, input_media=body.input_media or [],
        parameters=body.parameters or {}, batch_size=1,
        request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
        request_fingerprint=fingerprint_request(body.identity_payload(), entrypoint="playground.generate"),
        status="pending", created_at="2026-10-01T00:00:00Z",
        attempts=[PlaygroundAttempt(attempt_id=record.attempt_id, ordinal=0, status="pending", error=None, provider_execution=record.model_dump(mode="json"))],
    )
    return service.storage.admit_generation(gen)


def test_old_untagged_wan_record_replays_and_executes_legacy_unchanged(tmp_path, transport, monkeypatch):
    storage, service = service_at(tmp_path)
    body = request(negative_prompt="legacy negative", parameters={"seed": -1, "prompt_extend": False})
    calls = []
    resolve = Mock(wraps=service_module.resolve_media_runtime_authority)
    monkeypatch.setattr(service_module, "resolve_media_runtime_authority", resolve)

    def legacy_generate(_self, prompt, path, **kwargs):
        calls.append((prompt, kwargs))
        Path(path).write_bytes(bound_http.PNG)

    monkeypatch.setattr(WanxImageModel, "generate", legacy_generate)
    try:
        first = legacy_admission(service, body)
        original = saved_record(first)
        assert "intent_contract_version" not in original.intent
        monkeypatch.setattr(service, "_image_generation_intent", lambda *_: pytest.fail("legacy re-normalized"))
        monkeypatch.setattr(service, "_resolve_image_authority", lambda *_: pytest.fail("legacy rebound"))
        assert saved_record(service.admit_generation(body)) == original
        assert first.generation is not None
        service.process_generation(first.generation.id)
        completed = storage.read_request(body.client_request_id)
        assert completed is not None and completed.generation is not None
        assert completed.generation.status == "completed"
        frozen = saved_record(completed)
        assert frozen.intent == original.intent and frozen.fingerprint == original.fingerprint
        assert calls[0][1]["seed"] == -1 and calls[0][1]["prompt_extend"] is False
        assert calls[0][1]["negative_prompt"] == "legacy negative"
        assert "image_contract_version" not in resolve.call_args.kwargs
        assert transport["calls"] == []
    finally:
        storage.close()


@pytest.mark.parametrize("strict", [False, True])
def test_prepared_completion_repair_restarts_without_binding_or_resubmitting(tmp_path, transport, monkeypatch, strict):
    storage, service = service_at(tmp_path)
    body = request(parameters={"watermark": False})
    receipt = service.admit_generation(body) if strict else legacy_admission(service, body)
    original = saved_record(receipt)
    assert receipt.generation is not None
    calls = []

    def legacy_generate(_self, _prompt, path, **_kwargs):
        calls.append(path)
        Path(path).write_bytes(bound_http.PNG)

    monkeypatch.setattr(WanxImageModel, "generate", legacy_generate if not strict else lambda *_a, **_k: pytest.fail("strict used legacy"))
    update = storage.update_claimed_generation

    def fail_completion(gen):
        if gen.attempts[0].provider_execution["phase"] == "completed":
            raise StorageError("completion save failed")
        return update(gen)

    monkeypatch.setattr(storage, "update_claimed_generation", fail_completion)
    with pytest.raises(StorageError, match="completion save failed"):
        service.process_generation(receipt.generation.id)
    snapshot = storage.read_request(body.client_request_id)
    assert snapshot is not None
    prepared = saved_record(snapshot)
    assert prepared.phase == "submitting" and prepared.prepared_artifact
    before_calls = len(transport["calls"]), len(calls)
    monkeypatch.setattr(storage, "update_claimed_generation", update)
    storage.close()
    storage = PlaygroundStorage(tmp_path)
    restarted = PlaygroundService(storage, tmp_path, runtime_environment=RuntimeEnvironment({}))
    monkeypatch.setattr(restarted, "_resolve_record_authority", lambda *_: pytest.fail("repair resolved credentials"))
    restarted.process_generation(receipt.generation.id)
    repaired = storage.read_request(body.client_request_id)
    assert repaired is not None and repaired.generation is not None
    assert repaired.generation.status == "completed"
    result = saved_record(repaired)
    assert result.intent == original.intent and result.fingerprint == original.fingerprint
    assert result.artifact == prepared.prepared_artifact
    assert before_calls == (len(transport["calls"]), len(calls))
    storage.close()


def test_strict_provider_ambiguity_replay_and_restart_never_retry(tmp_path, transport):
    storage, service = service_at(tmp_path)
    transport["queue"] = [bound_http.response({"error": "not a receipt"}, 503)]
    body = request()
    try:
        receipt = service.admit_generation(body)
        assert receipt.generation is not None
        service.process_generation(receipt.generation.id)
        result = storage.read_request(body.client_request_id)
        assert result is not None and result.generation is not None
        assert result.generation.status == "needs_confirmation"
        assert saved_record(result).phase == "submitting"
        assert [item[0].method for item in transport["calls"]] == ["POST"]
        assert service.admit_generation(body).replayed
        storage.close()
        storage = PlaygroundStorage(tmp_path)
        assert saved_record(storage.read_request(body.client_request_id)) == saved_record(result)
        restarted = PlaygroundService(storage, tmp_path, runtime_environment=RuntimeEnvironment({}))
        restarted.process_generation(receipt.generation.id)
        recovered = storage.read_request(body.client_request_id)
        assert recovered is not None and recovered.generation is not None
        assert recovered.generation.status == "needs_confirmation"
        assert saved_record(recovered) == saved_record(result)
        assert len(transport["calls"]) == 1
    finally:
        storage.close()


def test_strict_prepared_save_failure_retains_verified_private_png(tmp_path, transport, monkeypatch):
    storage, service = service_at(tmp_path)
    original_executor = execute_sync_artifact_to_private_file
    original_update = storage.update_claimed_generation
    results = []

    def execute(*args, **kwargs):
        result = original_executor(*args, **kwargs)
        results.append(result)
        return result

    def fail_prepared(gen):
        record = gen.attempts[0].provider_execution or {}
        if record.get("prepared_artifact") is not None:
            raise StorageError("prepared save failed")
        return original_update(gen)

    monkeypatch.setattr(service, "_core_media_executor", execute)
    monkeypatch.setattr(storage, "update_claimed_generation", fail_prepared)
    body = request()
    receipt = service.admit_generation(body)
    assert receipt.generation is not None
    try:
        with pytest.raises(StorageError, match="prepared save failed"):
            service.process_generation(receipt.generation.id)
        assert len(results) == 1
        result = results[0]
        assert result.path.is_file()
        assert hashlib.sha256(result.path.read_bytes()).hexdigest() == result.sha256
        assert probe_image_file(result.path).codec_name == "png"
        latest = storage.read_request(body.client_request_id)
        assert latest is not None
        record = saved_record(latest)
        assert record.intent["intent_contract_version"] == VERSION
        assert record.phase == "submitting" and record.prepared_artifact is None
        assert record.output_ref is not None and not (tmp_path / record.output_ref).exists()
        monkeypatch.setattr(storage, "update_claimed_generation", original_update)
        storage.close()
        storage = PlaygroundStorage(tmp_path)
        assert saved_record(storage.read_request(body.client_request_id)) == record
        restarted = PlaygroundService(storage, tmp_path, runtime_environment=RuntimeEnvironment({}))
        restarted.process_generation(receipt.generation.id)
        recovered = storage.read_request(body.client_request_id)
        assert recovered is not None and recovered.generation is not None
        assert recovered.generation.status == "needs_confirmation"
        assert saved_record(recovered) == record
        assert result.path.is_file()  # Restart does not scan or adopt retained scratch.
        assert len(transport["calls"]) == 3
    finally:
        for result in results:
            if result.path.exists():
                result.path.unlink()
                result.path.parent.rmdir()
        storage.close()


def test_http_batch_uses_strict_adapter_and_replays_without_extra_posts(tmp_path, transport):
    transport["queue"] = [item for _ in range(2) for item in (
        bound_http.response(bound_http.task()), bound_http.response(bound_http.succeeded()),
        bound_http.response(bound_http.PNG),
    )]
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        app.state.playground_service.runtime_environment = RuntimeEnvironment({
            "DASHSCOPE_API_KEY": bound_http.SECRET,
            "DASHSCOPE_WORKSPACE_ID": "original-workspace",
        })
        body = request(batch_size=2, parameters={"seed": 0, "watermark": False}).model_dump(mode="json")
        accepted = client.post("/playground/generate", json=body)
        assert accepted.status_code == 200, accepted.text
        read = client.get("/playground/requests/wan27-playground")
        assert read.status_code == 200
        generation = read.json()["generation"]
        assert generation["status"] == "completed"
        assert len(generation["outputs"]) == 2
        assert len({item["attempt_id"] for item in generation["attempts"]}) == 2
        for attempt, output in zip(generation["attempts"], generation["outputs"]):
            record = attempt["provider_execution"]
            assert record["intent"]["intent_contract_version"] == VERSION
            assert record["scope"]["workspace_id"] == "original-workspace"
            assert record["artifact"]["output_ref"] == output["media_path"]
            assert client.get("/files/" + output["media_path"]).content.startswith(b"\x89PNG")
        replay = client.post("/playground/generate", json=body)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert [item[0].method for item in transport["calls"]] == ["POST", "GET", "GET"] * 2
        assert all(item[0].headers["X-DashScope-WorkSpace"] == "original-workspace"
                   for item in transport["calls"] if item[0].method == "POST")


def test_null_top_negative_matches_omission_and_explicit_max_values_survive(tmp_path, transport):
    storage, service = service_at(tmp_path)
    body = request(parameters={"seed": 2147483647, "watermark": True}, input_media=[])
    try:
        receipt = service.admit_generation(body)
        record = saved_record(receipt)
        assert record.intent["params"]["seed"] == 2147483647
        assert record.intent["params"]["watermark"] is True
        assert record.intent["sources"]["params.watermark"] == "request"
        assert service.admit_generation(body.model_copy(update={"negative_prompt": None})).replayed
        assert transport["calls"] == []
    finally:
        storage.close()
