"""Atelier new strict T2I and unchanged frozen image history, with offline HTTP."""

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.atelier import generation_commands as commands
from src.apps.atelier import media_generation_preparation as preparation
from src.apps.atelier import storage as storage_module
from src.apps.atelier.generation_commands import ManualImageCandidateBatchCommand
from src.apps.atelier.media_generation_admission import AtelierMediaGenerationAdmissionService
from src.apps.atelier.media_generation_execution import AtelierMediaGenerationExecutionService
from src.apps.atelier.models import AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.apps.comic_gen.api import create_app
from src.models import provider_media_runtime as runtime_module
from src.models.image import WanxImageModel
from src.models.provider_binding import SOURCE_KEY
from src.models.provider_media_execution import execute_sync_artifact_to_private_file
from src.models.provider_media_runtime import MediaRuntimeAuthority
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.media_probe import probe_image_file
from tests import test_wan27_bound_image as bound_http

transport = bound_http.transport
VERSION = "provider-wan27-t2i-bound-http-v1"
MODELS = ["wan2.7-image", "wan2.7-image-pro", "wan/wan2.7-image#image", "wan/wan2.7-image-pro#image"]
LEGACY_FILE = Path(__file__).parent / "fixtures" / "atelier_wan27_legacy_wire.json"


def payload(**changes):
    return {"expected_revision": 0, "client_request_id": "atelier-wan27", "model_id": MODELS[-1],
            "mode": "t2i", "prompt": "A glass moon", "media": [], **changes}


def create_project(store):
    store.create_project(AtelierProject.model_validate({
        "id": "p", "title": "Strict image", "created_at": 1, "updated_at": 1,
        "nodes": [{"id": "image", "project_id": "p", "type": "image"}],
    }))


def configure(app):
    app.state.runtime_environment.update({"DASHSCOPE_API_KEY": bound_http.SECRET,
        "DASHSCOPE_BASE_URL": bound_http.BASE, "DASHSCOPE_REGION": "cn-beijing",
        "DASHSCOPE_WORKSPACE_ID": "atelier-original"}, persist=lambda *_: None)


def queue_success(state, count=1):
    state["queue"] = [item for _ in range(count) for item in (
        bound_http.response(bound_http.task()), bound_http.response(bound_http.succeeded()), bound_http.response(bound_http.PNG))]


def posts(state):
    return [item[0] for item in state["calls"] if item[0].method == "POST"]


def legacy_data():
    return json.loads(LEGACY_FILE.read_text())


def seed_legacy(root, *, accepted=False):
    data = legacy_data()
    root.mkdir(parents=True, exist_ok=True)
    project = data["accepted_project"] if accepted else data["completed"]["project"]
    (root / "atelier_projects.json").write_text(json.dumps({project["id"]: project}) + "\n")
    if not accepted:
        for name, encoded in data["files"].items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded))
    return data


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("explicit", [False, True])
def test_new_intent_freezes_only_supported_values_and_default_sources(model, explicit):
    params = {"size": "768*1024", "seed": 0, "watermark": False} if explicit else {}
    intent = commands.normalize_image_candidate_request(ManualImageCandidateBatchCommand.model_validate(payload(model_id=model, params=params)))
    assert intent.params == (params if explicit else {"size": "1280*1280", "watermark": False})
    assert intent.sources["params.watermark"] == ("request" if explicit else "preset")
    assert intent.sources["params.size"] == ("request" if explicit else "catalog")
    assert ("params.watermark" in intent.request_fields) is explicit
    assert (intent.preset_id, intent.preset_version) == ("atelier.wan27.t2i", "1")
    assert intent.model_id == (model if "/" in model else f"wan/{model}#image")


INVALID = [
    {"params": {"negative_prompt": ""}}, {"params": {"negative_prompt": "avoid"}},
    {"params": {"prompt_extend": False}}, {"params": {"prompt_extend": True}},
    {"params": {"negative_prompt": None}}, {"params": {"prompt_extend": None}},
    {"params": {"seed": -1}}, {"params": {"seed": 2147483648}}, {"params": {"seed": None}},
    {"params": {"seed": False}}, {"params": {"watermark": 0}}, {"params": {"watermark": None}},
    {"params": {"size": "1024x1024"}}, {"params": {"size": "768*767"}},
    {"params": {"n": 1}}, {"params": {"quality": "high"}},
    {"prompt": " "}, {"prompt": "x" * 5001},
    {"media": [{"role": "reference_image", "ordinal": 0, "node_id": "missing", "media_url": "uploads/missing.png"}]},
]


@pytest.mark.parametrize("changes", INVALID)
def test_invalid_new_http_request_has_no_binding_or_durable_side_effect(tmp_path, transport, monkeypatch, changes):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app, raise_server_exceptions=False) as http:
        configure(app)
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


def test_one_preview_is_shared_by_binding_preparation_and_every_batch_member(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        configure(app)
        create_project(app.state.atelier_storage)
        service = app.state.atelier_media_generation_admission
        normalize = Mock(wraps=commands.normalize_generation_intent)
        monkeypatch.setattr(commands, "normalize_generation_intent", normalize)
        monkeypatch.setattr(runtime_module, "get_catalog_accessor", lambda: pytest.fail("strict authority re-read catalog"))
        bind = service._authority_provider
        seen = []
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
            assert record.provider_model_id == "wan2.7-image-pro"
        assert len({member.attempt.provider_execution.fingerprint for member in ack.members}) == 2


@pytest.mark.parametrize("model", ["wan/wan2.7-image#image", "wan/wan2.7-image-pro#image"])
def test_public_batch_uses_real_strict_adapter_once_per_member(tmp_path, transport, monkeypatch, model):
    queue_success(transport, 2)
    resolve = Mock(wraps=runtime_module.resolve_media_runtime_authority)
    monkeypatch.setattr(runtime_module, "resolve_media_runtime_authority", resolve)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    body = payload(model_id=model, batch_size=2, params={"size": "768*1024", "seed": 0, "watermark": False})
    with TestClient(app) as http:
        configure(app)
        create_project(app.state.atelier_storage)
        admitted = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body)
        assert admitted.status_code == 200, admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        result = http.get("/atelier/projects/p/generation/requests/atelier-wan27").json()
        assert [m["attempt"]["status"] for m in result["members"]] == ["completed", "completed"]
        assert result["project"]["nodes"][0]["media_urls"] == []
        for member in result["members"]:
            record = member["attempt"]["provider_execution"]
            assert record["intent"]["intent_contract_version"] == VERSION
            assert record["artifact"] == record["prepared_artifact"]
            path = tmp_path / record["output_ref"]
            assert probe_image_file(path).codec_name == "png"
            assert record["artifact"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert len(posts(transport)) == 2
        for submitted in posts(transport):
            assert submitted.headers["X-DashScope-WorkSpace"] == "atelier-original"
            assert json.loads(submitted.body)["parameters"] == {"n": 1, **body["params"]}
        assert all(call.kwargs["image_contract_version"] == VERSION for call in resolve.call_args_list)
        replay = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["members"] == result["members"]
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert len(posts(transport)) == 2
        conflict = http.post("/atelier/projects/p/nodes/image/image_candidates", json={**body, "prompt": "different"})
        assert conflict.status_code == 409


def test_execution_keeps_original_scope_after_runtime_and_catalog_change(tmp_path, transport, monkeypatch):
    queue_success(transport)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        configure(app)
        create_project(app.state.atelier_storage)
        ack = app.state.atelier_media_generation_admission.admit_image(
            "p", "image", ManualImageCandidateBatchCommand.model_validate(payload()))
        record = ack.members[0].attempt.provider_execution
        app.state.runtime_environment.update({
            "DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com",
            "DASHSCOPE_REGION": "ap-southeast-1", "DASHSCOPE_WORKSPACE_ID": "changed-workspace",
        }, persist=lambda *_: None)
        for module in (commands, preparation, runtime_module):
            monkeypatch.setattr(module, "get_catalog_accessor", lambda: pytest.fail("execution re-read catalog"))
        result = app.state.atelier_media_generation_execution.run_attempt("p", ack.members[0].attempt.attempt_id)
        assert result.attempt.status == "completed"
        assert result.attempt.provider_execution.fingerprint == record.fingerprint
        assert result.attempt.provider_execution.scope == record.scope
        submitted, = posts(transport)
        assert submitted.url.startswith(bound_http.BASE + "/")
        assert submitted.headers["X-DashScope-WorkSpace"] == "atelier-original"


@pytest.mark.parametrize("version", ["absent", None, "unknown", VERSION])
def test_composition_resolver_preserves_missing_versus_present_contract(tmp_path, monkeypatch, version):
    marker = RuntimeError("resolver inspected")
    resolver = Mock(side_effect=marker)
    monkeypatch.setattr(runtime_module, "resolve_media_runtime_authority", resolver)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        record = SimpleNamespace(media_kind="image", provider_model_id="wan2.7-image-pro",
            scope=ProviderScope("dashscope", "dashscope", "dashscope", bound_http.BASE, "cn-beijing").to_dict(),
            credential_ref="original", intent={} if version == "absent" else {"intent_contract_version": version})
        with pytest.raises(RuntimeError) as caught:
            app.state.atelier_media_generation_execution._authority_resolver(record)
        assert caught.value is marker
        assert ("image_contract_version" in resolver.call_args.kwargs) is (version != "absent")
        if version != "absent":
            assert resolver.call_args.kwargs["image_contract_version"] == version


def test_old_unsupported_request_reopens_and_replays_before_any_preparation(tmp_path, monkeypatch):
    fixture = seed_legacy(tmp_path)
    store = AtelierStorage(tmp_path)
    store.close()
    reopened = AtelierStorage(tmp_path)
    forbidden = Mock(side_effect=AssertionError("historical replay must not prepare or bind"))
    monkeypatch.setattr(commands, "normalize_generation_intent", forbidden)
    service = AtelierMediaGenerationAdmissionService(reopened, authority_provider=forbidden)
    try:
        body = ManualImageCandidateBatchCommand.model_validate(fixture["request"])
        original = reopened.get_project("legacy-project").model_dump(mode="json")
        replay = service.admit_image("legacy-project", "legacy-image", body.model_copy(update={"expected_revision": 999}))
        assert replay.replayed and replay.new_candidate_ids == []
        assert replay.project.model_dump(mode="json") == original
        assert "intent_contract_version" not in replay.members[0].attempt.provider_execution.intent
        assert replay.members[0].attempt.provider_execution.intent["params"]["seed"] == -1
        assert replay.members[0].attempt.provider_execution.intent["params"]["prompt_extend"] is False
        assert replay.members[0].attempt.provider_execution.fingerprint == fixture["completed"]["members"][0]["attempt"]["provider_execution"]["fingerprint"]
        forbidden.assert_not_called()
        with pytest.raises(StorageConflictError):
            service.admit_image("legacy-project", "legacy-image", body.model_copy(update={"prompt": "different"}))
    finally:
        reopened.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_prepared_png_repairs_after_real_store_close_reopen_without_credentials(tmp_path, transport, monkeypatch, legacy):
    if legacy:
        seed_legacy(tmp_path, accepted=True)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    calls = []
    with TestClient(app):
        configure(app)
        store = app.state.atelier_storage
        if legacy:
            project_id = "legacy-project"
            attempt = store.get_project(project_id).generation_attempts[0]
            @contextmanager
            def authority(record):
                scope = ProviderScope.from_dict(record.scope)
                yield MediaRuntimeAuthority("image", record.provider_model_id, scope, record.credential_ref, SOURCE_KEY,
                    ResolvedCredential(scope, record.credential_ref, bound_http.SECRET))
            def generate(self, prompt, output, **kwargs):
                calls.append(kwargs)
                Path(output).write_bytes(bound_http.PNG)
            monkeypatch.setattr(WanxImageModel, "generate", generate)
            service = AtelierMediaGenerationExecutionService(store, authority_resolver=authority)
        else:
            create_project(store)
            project_id = "p"
            attempt = app.state.atelier_media_generation_admission.admit_image("p", "image",
                ManualImageCandidateBatchCommand.model_validate(payload())).members[0].attempt
            service = app.state.atelier_media_generation_execution
        with monkeypatch.context() as patch:
            patch.setattr(service, "_complete", Mock(side_effect=StorageError("completion interrupted")))
            with pytest.raises(StorageError):
                service.run_attempt(project_id, attempt.attempt_id)
        prepared = store.get_project(project_id).generation_attempts[0]
        assert prepared.provider_execution.prepared_artifact is not None
        output = tmp_path / prepared.provider_execution.output_ref
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        assert len(calls) == (1 if legacy else 0)
        assert len(posts(transport)) == (0 if legacy else 1)
    # Remove only the synthetic ambient key installed by the transport fixture.
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    # Fresh application reopens the disk store without provider credentials.
    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(reopened):
        assert not reopened.state.runtime_environment.snapshot().get("DASHSCOPE_API_KEY")
        service = reopened.state.atelier_media_generation_execution
        service._authority_resolver = Mock(side_effect=AssertionError("prepared repair must not resolve"))
        done = service.run_attempt(project_id, attempt.attempt_id)
        assert done.attempt.status == "completed"
        assert done.attempt.provider_execution.fingerprint == prepared.provider_execution.fingerprint
        assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
        assert done.project.nodes[0].media_urls == []
        assert len(calls) == (1 if legacy else 0)
        assert len(posts(transport)) == (0 if legacy else 1)


def test_strict_submit_ambiguity_reopens_without_a_second_post(tmp_path, transport):
    transport["queue"] = [requests.ConnectionError("synthetic lost submit acknowledgement")]
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        configure(app)
        create_project(app.state.atelier_storage)
        admitted = http.post("/atelier/projects/p/nodes/image/image_candidates", json=payload())
        assert admitted.status_code == 200, admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        before = http.get("/atelier/projects/p/generation/requests/atelier-wan27").json()
        attempt = before["members"][0]["attempt"]
        assert attempt["status"] == "needs_confirmation"
        assert attempt["provider_execution"]["intent"]["intent_contract_version"] == VERSION
        assert attempt["provider_execution"]["phase"] == "submitting"
        assert len(posts(transport)) == 1
    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(reopened) as http:
        restored = http.get("/atelier/projects/p/generation/requests/atelier-wan27").json()
        assert restored["members"] == before["members"]
        replay = http.post("/atelier/projects/p/nodes/image/image_candidates", json=payload())
        assert replay.status_code == 200 and replay.json()["replayed"]
        resumed = reopened.state.atelier_media_generation_execution.run_attempt("p", attempt["attempt_id"])
        assert resumed.attempt.status == "needs_confirmation"
        assert len(posts(transport)) == 1


def test_strict_verified_private_png_survives_failed_prepared_commit_and_reopen(tmp_path, transport, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    results = []
    with TestClient(app):
        configure(app)
        create_project(app.state.atelier_storage)
        attempt = app.state.atelier_media_generation_admission.admit_image("p", "image",
            ManualImageCandidateBatchCommand.model_validate(payload())).members[0].attempt
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


def test_export_actual_strict_draft_admission_and_partial_unknown_wire(tmp_path, transport):
    request_key = "generation-11111111-1111-4111-8111-111111111111"
    unknown_key = "generation-22222222-2222-4222-8222-222222222222"
    params = {"size": "1280*1280", "seed": 0, "watermark": False}
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
        body = payload(expected_revision=saved.json()["project"]["revision"], client_request_id=request_key, batch_size=2, params=params)
        admitted = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body)
        assert admitted.status_code == 200, admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        completed = http.get("/atelier/projects/p/generation/requests/" + request_key).json()
        assert [m["attempt"]["status"] for m in completed["members"]] == ["completed", "completed"]
        replayed = http.post("/atelier/projects/p/nodes/image/image_candidates", json=body).json()
        assert replayed["replayed"]
        unknown_body = payload(expected_revision=completed["project"]["revision"], client_request_id=unknown_key, batch_size=2, params=params)
        unknown_admitted = http.post("/atelier/projects/p/nodes/image/image_candidates", json=unknown_body)
        assert unknown_admitted.status_code == 200, unknown_admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        unknown = http.get("/atelier/projects/p/generation/requests/" + unknown_key).json()
        assert [m["attempt"]["status"] for m in unknown["members"]] == ["completed", "needs_confirmation"]
        assert len(posts(transport)) == 4
        data = {"initial_project": initial, "draft_request": draft, "draft_ack": saved.json(), "request": body,
                "admitted": admitted.json(), "completed": completed, "replayed": replayed,
                "unknown_request": unknown_body, "unknown_admitted": unknown_admitted.json(), "unknown": unknown}
        destination = Path(os.environ["LUMENX_TEST_ISOLATION_ROOT"]) / "atelier-wan27-strict-wire.json"
        destination.write_text(json.dumps(data, indent=2) + "\n")
