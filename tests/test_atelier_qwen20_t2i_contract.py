"""Atelier Qwen T2I admission and frozen historical execution contracts."""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient
from requests.adapters import BaseAdapter

from src.apps.atelier import generation_commands as commands
from src.apps.atelier import media_generation_preparation as preparation
from src.apps.atelier import storage as storage_module
from src.apps.atelier.media_generation_execution import AtelierMediaGenerationExecutionService
from src.apps.atelier.storage import AtelierStorage
from src.apps.atelier.generation_commands import ManualImageCandidateBatchCommand
from src.apps.atelier.models import AtelierProject
from src.apps.comic_gen.api import create_app
from src.models import provider_media_runtime as runtime_module
from src.models.provider_binding import SOURCE_KEY
from src.models.provider_media_execution import execute_sync_artifact_to_private_file
from src.models.provider_media_runtime import MediaRuntimeAuthority
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import StorageError
from src.utils.media_probe import probe_image_file
from tests import test_qwen20_bound_image as qwen_http
from tests import test_wan27_bound_image as wire

VERSION = "provider-qwen20-t2i-bound-http-v1"
MODELS = ["qwen-image-2.0", "qwen-image-2.0-pro", "qwen/qwen-image-2.0#image", "qwen/qwen-image-2.0-pro#image"]
LEGACY_FILE = Path(__file__).parent / "fixtures" / "atelier_qwen20_5e4e_legacy_wire.json"


@pytest.fixture
def transport(monkeypatch):
    """Only the network adapter is fake; both legacy and bound adapters stay real."""
    original = requests.Session
    state = {"calls": [], "queue": [], "before_send": None}

    class Adapter(BaseAdapter):
        def send(self, request, **kwargs):
            if state["before_send"]:
                state["before_send"](request)
            state["calls"].append((request, kwargs))
            assert state["queue"], "Unexpected HTTP request or retry"
            result = state["queue"].pop(0)
            if isinstance(result, Exception):
                raise result
            result.request, result.url = request, request.url
            return result

        def close(self):
            pass

    def session():
        client = original()
        client.trust_env = False
        client.mount("https://", Adapter())
        client.mount("http://", Adapter())
        return client

    monkeypatch.setattr(requests, "Session", session)
    monkeypatch.setattr(requests.sessions, "Session", session)
    # Legacy downloads mount a fresh retry adapter after Session construction.
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", Adapter.send)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    return state


def payload(**changes):
    return {"expected_revision": 0, "client_request_id": "atelier-qwen20", "model_id": MODELS[-1],
            "mode": "t2i", "prompt": "A glass moon", "media": [], **changes}


def create_project(store):
    store.create_project(AtelierProject.model_validate({
        "id": "p", "title": "Qwen image", "created_at": 1, "updated_at": 1,
        "nodes": [{"id": "image", "project_id": "p", "type": "image"}],
    }))


def configure(app, *, workspace="atelier-qwen-original"):
    app.state.runtime_environment.update({"DASHSCOPE_API_KEY": wire.SECRET,
        "DASHSCOPE_BASE_URL": wire.BASE, "DASHSCOPE_REGION": "cn-beijing",
        "DASHSCOPE_WORKSPACE_ID": workspace}, persist=lambda *_: None)


def queue_success(state, count=1, *, legacy=False):
    state["queue"] = [response for _ in range(count) for response in (
        (wire.response(wire.task()), wire.response(wire.succeeded()), wire.response(wire.PNG))
        if legacy else (wire.response(qwen_http.completed()), wire.response(wire.PNG))
    )]


def posts(state):
    return [request for request, _ in state["calls"] if request.method == "POST"]


def legacy_data():
    raw = LEGACY_FILE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "6a930f5e7b2131a0c74bba1663274df8c7c008b5bf6e6925f95ea8ad601705bd"
    data = json.loads(raw)
    assert data["baseline_commit"] == "5e4e99183850824d77ccad90625ee1d55f62a570"
    return data


def seed_legacy(root, stage):
    fixture = legacy_data()["stages"][stage]
    root.mkdir(parents=True, exist_ok=True)
    (root / "atelier_projects.json").write_bytes(base64.b64decode(fixture["storage_base64"]))
    for name, encoded in fixture["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    return fixture


@contextmanager
def original_legacy_authority(record):
    # The fixture excludes the synthetic credential store. Supply its original
    # authority for execution tests, without normalizing or relabeling the record.
    scope = ProviderScope.from_dict(record.scope)
    yield MediaRuntimeAuthority("image", record.provider_model_id, scope, record.credential_ref, SOURCE_KEY,
        ResolvedCredential(scope, record.credential_ref, wire.SECRET))


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("explicit", [False, True])
def test_new_intent_freezes_qwen_defaults_and_explicit_false_zero_empty(model, explicit):
    params = {"size": "768*1024", "seed": 0, "watermark": False, "prompt_extend": False, "negative_prompt": ""} if explicit else {}
    intent = commands.normalize_image_candidate_request(ManualImageCandidateBatchCommand.model_validate(payload(model_id=model, params=params)))
    assert intent.params == (params if explicit else {"size": "1280*1280", "watermark": False, "prompt_extend": True})
    for key in ("watermark", "prompt_extend"):
        assert intent.sources[f"params.{key}"] == ("request" if explicit else "preset")
        assert (f"params.{key}" in intent.request_fields) is explicit
    assert intent.sources["params.size"] == ("request" if explicit else "catalog")
    assert (intent.preset_id, intent.preset_version) == ("atelier.qwen20.t2i", "1")
    assert intent.model_id == (model if "/" in model else f"qwen/{model}#image")
    assert commands.image_contract_options(intent) == {"image_contract_version": VERSION}


INVALID = [
    {"params": {"negative_prompt": "x" * 501}}, {"params": {"negative_prompt": None}},
    {"params": {"negative_prompt": False}}, {"params": {"prompt_extend": None}},
    {"params": {"prompt_extend": 0}}, {"params": {"seed": -1}},
    {"params": {"seed": 2147483648}}, {"params": {"seed": None}},
    {"params": {"seed": False}}, {"params": {"seed": 1.5}},
    {"params": {"watermark": 0}}, {"params": {"watermark": None}},
    {"params": {"size": "1024x1024"}}, {"params": {"size": "768*767"}},
    {"params": {"n": 1}}, {"params": {"quality": "high"}},
    {"params": {"unknown": "value"}}, {"prompt": " "}, {"prompt": "汉" * 5462},
    {"media": [{"role": "reference_image", "ordinal": 0, "node_id": "missing", "media_url": "uploads/missing.png"}]},
]


@pytest.mark.parametrize("changes", INVALID)
def test_invalid_new_http_request_has_zero_bind_provider_and_durable_changes(tmp_path, transport, monkeypatch, changes):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app, raise_server_exceptions=False) as http:
        configure(app, workspace="")
        create_project(app.state.atelier_storage)
        before = app.state.atelier_storage.path.read_bytes()
        binding = Mock(wraps=app.state.provider_credential_store.bind)
        monkeypatch.setattr(app.state.provider_credential_store, "bind", binding)
        result = http.post("/atelier/projects/p/nodes/image/image_candidates", json=payload(**changes))
        assert result.status_code == 422, result.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert app.state.atelier_storage.path.read_bytes() == before
        binding.assert_not_called()
        assert transport["calls"] == []


@pytest.mark.parametrize("changes", [{"prompt": "bad\ud800"}, {"params": {"negative_prompt": "bad\ud800"}}])
def test_invalid_unicode_is_rejected_by_pure_preview_before_binding(changes):
    with pytest.raises(IntentValidationError):
        commands.normalize_image_candidate_request(ManualImageCandidateBatchCommand.model_validate(payload(**changes)))


@pytest.mark.parametrize("changes", [
    {"prompt": "bad\ud800"}, {"prompt": "bad\udfff"},
    {"params": {"negative_prompt": "bad\ud800"}}, {"params": {"negative_prompt": "bad\udfff"}},
    {"params": {"seed": float("nan")}}, {"params": {"negative_prompt": float("inf")}},
])
def test_raw_http_surrogates_and_nonfinite_values_are_422_without_effects(tmp_path, transport, monkeypatch, changes):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app, raise_server_exceptions=False) as http:
        configure(app)
        create_project(app.state.atelier_storage)
        before = app.state.atelier_storage.path.read_bytes()
        binding = Mock(wraps=app.state.provider_credential_store.bind)
        monkeypatch.setattr(app.state.provider_credential_store, "bind", binding)
        result = http.post("/atelier/projects/p/nodes/image/image_candidates",
            content=json.dumps(payload(**changes), ensure_ascii=True, allow_nan=True).encode("ascii"),
            headers={"Content-Type": "application/json"})
        assert result.status_code == 422, result.text
        assert app.state.atelier_storage.path.read_bytes() == before
        binding.assert_not_called()
        assert transport["calls"] == []


@pytest.mark.parametrize("model", MODELS)
def test_qwen_i2i_keeps_generic_parameters_without_new_t2i_preset_or_profile(model):
    command = ManualImageCandidateBatchCommand.model_validate(payload(model_id=model, mode="i2i",
        media=[{"role": "reference_image", "ordinal": 0, "node_id": "reference", "media_url": "uploads/reference.png"}],
        params={"seed": -1, "negative_prompt": "x" * 501, "prompt_extend": False}))
    intent = commands.normalize_image_candidate_request(command)
    assert commands.image_contract_options(intent) == {}
    assert intent.preset_id is None and intent.preset_version is None
    assert intent.params["seed"] == -1 and intent.params["negative_prompt"] == "x" * 501
    assert intent.params["prompt_extend"] is False
    assert commands.strict_qwen20_image_provider_model(intent.model_id, intent.mode) is None


def test_one_preview_and_frozen_route_are_shared_by_binding_and_all_members(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        configure(app)
        create_project(app.state.atelier_storage)
        service = app.state.atelier_media_generation_admission
        normalize = Mock(wraps=commands.normalize_generation_intent)
        monkeypatch.setattr(commands, "normalize_generation_intent", normalize)
        monkeypatch.setattr(runtime_module, "get_catalog_accessor", lambda: pytest.fail("strict authority re-read catalog"))
        bind, seen = service._authority_provider, []
        @contextmanager
        def authority(**kwargs):
            with bind(**kwargs) as bound:
                seen.append(kwargs["preview"])
                monkeypatch.setattr(commands, "get_catalog_accessor", lambda: pytest.fail("after-bind command catalog re-read"))
                monkeypatch.setattr(preparation, "get_catalog_accessor", lambda: pytest.fail("after-bind provider route re-read"))
                yield bound
        service._authority_provider = authority
        ack = service.admit_image("p", "image", ManualImageCandidateBatchCommand.model_validate(payload(batch_size=2)))
        assert normalize.call_count == len(seen) == 1
        assert len(ack.members) == 2
        for member in ack.members:
            record = member.attempt.provider_execution
            assert record.intent == {**seen[0].intent.to_dict(), "intent_contract_version": VERSION}
            assert record.provider_model_id == "qwen-image-2.0-pro"
        assert len({member.attempt.provider_execution.fingerprint for member in ack.members}) == 2


@pytest.mark.parametrize("model", MODELS)
def test_http_batch_uses_real_sync_adapter_after_durable_admission_and_never_selects(tmp_path, transport, monkeypatch, model):
    queue_success(transport, 2)
    resolve = Mock(wraps=runtime_module.resolve_media_runtime_authority)
    monkeypatch.setattr(runtime_module, "resolve_media_runtime_authority", resolve)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    body = payload(model_id=model, batch_size=2, params={"size": "768*1024", "seed": 0, "watermark": False,
                                                       "prompt_extend": False, "negative_prompt": ""})
    with TestClient(app) as http:
        configure(app, workspace="")
        create_project(app.state.atelier_storage)
        def before_send(request):
            if request.method == "POST":
                saved = json.loads(app.state.atelier_storage.path.read_text())["p"]
                assert len(saved["generation_attempts"]) == 2
                assert len(saved["generation_request_receipts"]) == 1
                assert any(a["provider_execution"]["phase"] == "submitting" for a in saved["generation_attempts"])
        transport["before_send"] = before_send
        admitted = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body)
        assert admitted.status_code == 200, admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        result = http.get("/atelier/projects/p/generation/requests/atelier-qwen20").json()
        assert [m["attempt"]["status"] for m in result["members"]] == ["completed", "completed"], result
        assert result["project"]["nodes"][0]["media_urls"] == []
        for member in result["members"]:
            record = member["attempt"]["provider_execution"]
            assert record["intent"]["intent_contract_version"] == VERSION
            assert record["artifact"] == record["prepared_artifact"]
            path = tmp_path / record["output_ref"]
            assert probe_image_file(path).codec_name == "png"
            assert record["artifact"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert len(transport["calls"]) == 4 and len(posts(transport)) == 2
        for submitted in posts(transport):
            assert submitted.url == wire.BASE + "/api/v1/services/aigc/multimodal-generation/generation"
            assert "X-DashScope-Async" not in submitted.headers
            assert json.loads(submitted.body) == {"model": model.split("/")[-1].split("#")[0],
                "input": {"messages": [{"role": "user", "content": [{"text": body["prompt"]}]}]},
                "parameters": {"n": 1, **body["params"]}}
        assert all(request.url == wire.RESULT_URL for request, _ in transport["calls"] if request.method == "GET")
        assert all(call.kwargs["image_contract_version"] == VERSION for call in resolve.call_args_list)
        replay = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["members"] == result["members"]
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert len(posts(transport)) == 2
        assert http.post("/atelier/projects/p/nodes/image/image_candidates", json={**body, "prompt": "changed"}).status_code == 409


def test_save_failure_never_dispatches_or_publishes_receipt(tmp_path, transport, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app, raise_server_exceptions=False) as http:
        configure(app, workspace="")
        create_project(app.state.atelier_storage)
        original = storage_module.atomic_write_json
        before = app.state.atelier_storage.path.read_bytes()
        attempts = []
        def fail_admission(path, value):
            if value["p"]["generation_attempts"]:
                attempts.append(value)
                raise StorageError("synthetic admission commit failure")
            return original(path, value)
        monkeypatch.setattr(storage_module, "atomic_write_json", fail_admission)
        result = http.post("/atelier/projects/p/nodes/image/image_candidates", json=payload())
        assert result.status_code == 503, result.text
        assert len(attempts) == 1
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert app.state.atelier_storage.path.read_bytes() == before
        # A failed durable write intentionally leaves this store unavailable.
        assert http.get("/atelier/projects/p/generation/requests/atelier-qwen20").status_code == 503
        assert transport["calls"] == []
    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(reopened) as http:
        assert http.get("/atelier/projects/p/generation/requests/atelier-qwen20").status_code == 404


def test_same_request_concurrently_admits_one_batch(tmp_path, transport):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        configure(app)
        create_project(app.state.atelier_storage)
        service = app.state.atelier_media_generation_admission
        command = ManualImageCandidateBatchCommand.model_validate(payload(batch_size=2))
        with ThreadPoolExecutor(max_workers=2) as pool:
            acks = list(pool.map(lambda _: service.admit_image("p", "image", command), range(2)))
        assert sorted(ack.replayed for ack in acks) == [False, True]
        assert acks[0].receipt == acks[1].receipt
        project = app.state.atelier_storage.get_project("p")
        assert len(project.generation_attempts) == 2 and len(project.generation_request_receipts) == 1
        assert all(a.provider_execution.intent["intent_contract_version"] == VERSION for a in project.generation_attempts)
        assert transport["calls"] == []


def test_execution_uses_original_scope_after_runtime_and_catalog_change(tmp_path, transport, monkeypatch):
    queue_success(transport)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        configure(app)
        create_project(app.state.atelier_storage)
        ack = app.state.atelier_media_generation_admission.admit_image("p", "image", ManualImageCandidateBatchCommand.model_validate(payload()))
        record = ack.members[0].attempt.provider_execution
        app.state.runtime_environment.update({"DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com",
            "DASHSCOPE_REGION": "ap-southeast-1", "DASHSCOPE_WORKSPACE_ID": "changed"}, persist=lambda *_: None)
        for module in (commands, preparation, runtime_module):
            monkeypatch.setattr(module, "get_catalog_accessor", lambda: pytest.fail("execution re-read catalog"))
        result = app.state.atelier_media_generation_execution.run_attempt("p", ack.members[0].attempt.attempt_id)
        assert result.attempt.status == "completed"
        assert result.attempt.provider_execution.fingerprint == record.fingerprint
        assert result.attempt.provider_execution.scope == record.scope
        submitted, = posts(transport)
        assert submitted.url.startswith(wire.BASE + "/")
        assert submitted.headers["X-DashScope-WorkSpace"] == "atelier-qwen-original"


@pytest.mark.parametrize("stage", ["accepted", "completed", "submitting"])
def test_old_wire_reopens_and_http_replays_before_new_validation_or_binding(tmp_path, transport, monkeypatch, stage):
    fixture = seed_legacy(tmp_path, stage)
    original_bytes = (tmp_path / "atelier_projects.json").read_bytes()
    store = AtelierStorage(tmp_path)
    store.close()
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        forbidden = Mock(side_effect=AssertionError("historical replay must not prepare or bind"))
        monkeypatch.setattr(commands, "normalize_generation_intent", forbidden)
        app.state.atelier_media_generation_admission._authority_provider = forbidden
        result = http.get("/atelier/projects/p/generation/requests/atelier-qwen20")
        assert result.status_code == 200 and result.json() == fixture["readback"]
        replay = http.post("/atelier/projects/p/nodes/image/image_candidates", json={**fixture["request"], "expected_revision": 999})
        assert replay.status_code == 200 and replay.json() == fixture["readback"]
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert app.state.atelier_storage.path.read_bytes() == original_bytes
        assert transport["calls"] == []
        forbidden.assert_not_called()
        assert http.post("/atelier/projects/p/nodes/image/image_candidates", json={**fixture["request"], "prompt": "changed"}).status_code == 409
        for name, encoded in fixture["files"].items():
            assert (tmp_path / name).read_bytes() == base64.b64decode(encoded)


def test_old_accepted_qwen_still_uses_original_real_async_adapter(tmp_path, transport):
    fixture = seed_legacy(tmp_path, "accepted")
    queue_success(transport, legacy=True)
    store = AtelierStorage(tmp_path)
    try:
        service = AtelierMediaGenerationExecutionService(store, authority_resolver=original_legacy_authority)
        original = fixture["readback"]["members"][0]["attempt"]
        done = service.run_attempt("p", original["attempt_id"])
        assert done.attempt.status == "completed"
        assert done.attempt.provider_execution.intent == original["provider_execution"]["intent"]
        assert done.attempt.provider_execution.fingerprint == original["provider_execution"]["fingerprint"]
        assert len(transport["calls"]) == 3
        submitted, = posts(transport)
        assert submitted.url.endswith("/image-generation/generation")
        assert submitted.headers["X-DashScope-Async"] == "enable"
        assert json.loads(submitted.body)["parameters"]["seed"] == -1
        assert len(json.loads(submitted.body)["parameters"]["negative_prompt"]) == 501
        assert transport["calls"][1][0].url.endswith("/tasks/task_123-abc")
        assert done.project.nodes[0].media_urls == []
    finally:
        store.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_prepared_png_repairs_after_close_reopen_without_provider(tmp_path, transport, monkeypatch, legacy):
    if legacy:
        seed_legacy(tmp_path, "accepted")
    queue_success(transport, legacy=legacy)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        configure(app)
        store = app.state.atelier_storage
        if legacy:
            attempt = store.get_project("p").generation_attempts[0]
            service = AtelierMediaGenerationExecutionService(store, authority_resolver=original_legacy_authority)
        else:
            create_project(store)
            attempt = app.state.atelier_media_generation_admission.admit_image("p", "image", ManualImageCandidateBatchCommand.model_validate(payload())).members[0].attempt
            service = app.state.atelier_media_generation_execution
        with monkeypatch.context() as patch:
            patch.setattr(service, "_complete", Mock(side_effect=StorageError("completion interrupted")))
            with pytest.raises(StorageError):
                service.run_attempt("p", attempt.attempt_id)
        prepared = store.get_project("p").generation_attempts[0]
        assert prepared.provider_execution.prepared_artifact is not None
        output = tmp_path / prepared.provider_execution.output_ref
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        assert len(posts(transport)) == 1
    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(reopened):
        assert not reopened.state.runtime_environment.snapshot().get("DASHSCOPE_API_KEY")
        service = reopened.state.atelier_media_generation_execution
        service._authority_resolver = Mock(side_effect=AssertionError("repair must not resolve credentials"))
        done = service.run_attempt("p", attempt.attempt_id)
        assert done.attempt.status == "completed"
        assert done.attempt.provider_execution.fingerprint == prepared.provider_execution.fingerprint
        assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
        assert done.project.nodes[0].media_urls == []
        assert len(posts(transport)) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_unknown_submission_reopens_without_second_post(tmp_path, transport, legacy):
    if legacy:
        fixture = seed_legacy(tmp_path, "submitting")
        body = fixture["request"]
    else:
        transport["queue"] = [requests.ConnectionError("synthetic lost sync ACK")]
        app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
        body = payload()
        with TestClient(app) as http:
            configure(app)
            create_project(app.state.atelier_storage)
            assert http.post("/atelier/projects/p/nodes/image/image_candidates", json=body).status_code == 200
            assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
            before = http.get("/atelier/projects/p/generation/requests/atelier-qwen20").json()
            assert before["members"][0]["attempt"]["provider_execution"]["intent"]["intent_contract_version"] == VERSION
    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(reopened) as http:
        restored = http.get("/atelier/projects/p/generation/requests/atelier-qwen20").json()
        attempt = restored["members"][0]["attempt"]
        assert attempt["status"] == "needs_confirmation" and attempt["provider_execution"]["phase"] == "submitting"
        assert http.post("/atelier/projects/p/nodes/image/image_candidates", json=body).json()["replayed"]
        result = reopened.state.atelier_media_generation_execution.run_attempt("p", attempt["attempt_id"])
        assert result.attempt.status == "needs_confirmation"
        assert len(posts(transport)) == (0 if legacy else 1)


def test_verified_private_png_survives_failed_prepared_save_and_never_resubmits(tmp_path, transport, monkeypatch):
    queue_success(transport)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    results = []
    with TestClient(app):
        configure(app)
        create_project(app.state.atelier_storage)
        attempt = app.state.atelier_media_generation_admission.admit_image("p", "image", ManualImageCandidateBatchCommand.model_validate(payload())).members[0].attempt
        service = app.state.atelier_media_generation_execution
        def execute(*args, **kwargs):
            result = execute_sync_artifact_to_private_file(*args, **kwargs)
            results.append(result)
            return result
        service._executor = execute
        original = storage_module.atomic_write_json
        def fail_prepared(path, value):
            if value["p"]["generation_attempts"][0]["provider_execution"]["prepared_artifact"]:
                raise StorageError("prepared save interrupted")
            return original(path, value)
        with monkeypatch.context() as patch:
            patch.setattr(storage_module, "atomic_write_json", fail_prepared)
            with pytest.raises(StorageError):
                service.run_attempt("p", attempt.attempt_id)
        assert len(results) == 1 and results[0].path.is_file()
        assert probe_image_file(results[0].path).codec_name == "png"
        assert len(posts(transport)) == 1
        assert not (tmp_path / attempt.provider_execution.output_ref).exists()
    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(reopened):
        result = reopened.state.atelier_media_generation_execution.run_attempt("p", attempt.attempt_id)
        assert result.attempt.status == "needs_confirmation" and result.attempt.output_ref is None
        assert results[0].path.is_file()
        assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
        assert len(posts(transport)) == 1


def test_actual_strict_draft_admission_complete_and_partial_wire(tmp_path, transport):
    first_key, partial_key = "generation-11111111-1111-4111-8111-111111111111", "generation-22222222-2222-4222-8222-222222222222"
    params = {"size": "1280*1280", "seed": 0, "watermark": False, "prompt_extend": False, "negative_prompt": ""}
    queue_success(transport, 3)
    transport["queue"].append(requests.ConnectionError("synthetic ambiguous submit"))
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        configure(app)
        create_project(app.state.atelier_storage)
        initial = http.get("/atelier/projects/p").json()
        draft = {"expected_revision": 0, "media_kind": "image", "model_id": MODELS[-1], "mode": "t2i", "prompt": "A glass moon", "params": params, "media": [], "batch_size": 2}
        saved = http.put("/atelier/projects/p/nodes/image/generation_draft", json=draft)
        assert saved.status_code == 200, saved.text
        body = payload(expected_revision=saved.json()["project"]["revision"], client_request_id=first_key, batch_size=2, params=params)
        admitted = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body)
        assert admitted.status_code == 200, admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        completed = http.get("/atelier/projects/p/generation/requests/" + first_key).json()
        assert [m["attempt"]["status"] for m in completed["members"]] == ["completed", "completed"]
        replayed = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body).json()
        assert replayed["replayed"]
        partial_body = payload(expected_revision=completed["project"]["revision"], client_request_id=partial_key, batch_size=2, params=params)
        partial_admitted = http.post("/atelier/projects/p/nodes/image/image_candidates", json=partial_body)
        assert partial_admitted.status_code == 200, partial_admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        partial = http.get("/atelier/projects/p/generation/requests/" + partial_key).json()
        assert [m["attempt"]["status"] for m in partial["members"]] == ["completed", "needs_confirmation"]
        assert len(posts(transport)) == 4
        data = {"initial_project": initial, "draft_request": draft, "draft_ack": saved.json(), "request": body,
            "admitted": admitted.json(), "completed": completed, "replayed": replayed,
            "partial_request": partial_body, "partial_admitted": partial_admitted.json(), "partial": partial}
        print("ATELIER_QWEN20_STRICT_WIRE=" + json.dumps(data, sort_keys=True))
