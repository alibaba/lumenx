"""Real HTTP proof for the existing Qwen pure-T2I full-body channel.

Only the external HTTP transport is replaced; Studio ownership, Core execution,
the model factory, bound Qwen adapter and persisted readback remain real.
"""

from copy import deepcopy
import hashlib
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import requests

from src.apps.comic_gen import asset_image_execution as execution_module
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from tests import test_qwen20_studio_image as qwen
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_worker as worker
from tests import test_studio_full_body_core as full_body


runtime = admission.runtime
bound_transport = worker.bound_transport


@pytest.mark.parametrize("route", admission.ROUTES)
@pytest.mark.parametrize("model", qwen.core.MODELS)
def test_real_http_qwen_full_body_keeps_owner_proof_and_readonly_reopen(
    runtime, bound_transport, route, model, monkeypatch,
):
    root, owner, target = full_body.setup(runtime, route, uploaded_body=True)
    root_object = runtime.series_store[root[1]] if root[0] == "series" else runtime.scripts[root[1]]
    root_object.model_settings.t2i_model = model
    root_object.art_direction = qwen.style()
    target.full_body.image_prompt = "previous asset-unit prompt"
    before_sheet = target.reference_sheet.model_dump(mode="json")
    before_motion = deepcopy(target.full_body.video_variants)
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    qwen.queue_images(bound_transport, count=2)
    path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/asset_image_requests"
    command = full_body.full_body_command(
        batch_size=2, seed=0, prompt="Authored body pose",
        apply_style=True, negative_prompt="draft-negative",
    )
    raw = command.model_dump(exclude_unset=True)

    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        accepted = client.post(path, json=raw)
        assert accepted.status_code == 200, accepted.text
        record = owner.asset_image_requests[0]
        assert record.id == accepted.json()["request"]["id"]
        assert (record.entrypoint, record.asset_type, record.channel) == (
            full_body.ENTRYPOINT, "character", "full_body",
        )
        assert record.base_prompt == "Authored body pose"
        assert target.full_body_request_id == record.id
        assert target.reference_sheet_request_id == full_body.REQUEST_ID
        assert target.reference_sheet.model_dump(mode="json") == before_sheet
        assert target.full_body_asset.selected_id == "body-old"
        assert target.full_body.selected_image_id == "body-old"
        original_body = next(variant for variant in target.full_body_asset.variants if variant.id == "body-old")
        assert original_body.is_favorited and original_body.is_uploaded_source
        assert target.full_body.video_variants == before_motion
        assert target.full_body.selected_video_id == "motion"
        assert target.full_body.video_prompt == "keep motion"
        assert target.full_body_prompt == target.full_body.image_prompt == record.base_prompt
        assert all(member.execution.intent["intent_contract_version"] == qwen.core.VERSION for member in record.members)
        assert all(member.execution.media == [] for member in record.members)

        payloads = qwen.posts(bound_transport)
        assert len(payloads) == 2
        for payload in payloads:
            assert payload["model"] == model
            assert payload["input"]["messages"] == [{
                "role": "user", "content": [{"text": "Authored body pose, style-positive"}],
            }]
            assert payload["parameters"] == {
                "size": "576*1024", "n": 1, "seed": 0, "watermark": False,
                "prompt_extend": True, "negative_prompt": "draft-negative, style-negative",
            }
        submitted = [request for request, _ in bound_transport["calls"] if request.method == "POST"]
        fetched = [request for request, _ in bound_transport["calls"] if request.method == "GET"]
        assert len(submitted) == len(fetched) == 2
        assert all(request.url.endswith("/multimodal-generation/generation") for request in submitted)
        assert all("X-DashScope-Async" not in request.headers for request in submitted)
        assert all("Authorization" not in request.headers for request in fetched)
        assert len(bound_transport["calls"]) == 4
        for member in record.members:
            execution = member.execution
            assert execution.phase == "completed"
            assert execution.prepared_artifact == execution.artifact
            content = (runtime.data_dir / execution.output_ref).read_bytes()
            assert content.startswith(b"\x89PNG")
            assert execution.artifact["sha256"] == hashlib.sha256(content).hexdigest()

        before = {name: (runtime.data_dir / name).read_bytes()
                  for name in ("projects.json", "series.json", "library_assets.json")}
        read = client.get(path + "/request-1")
        assert read.status_code == 200 and read.json()["replayed"]
        assert all(member["status"] == "completed" for member in read.json()["request"]["members"])
        client.app.state.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", Mock(side_effect=AssertionError("replay rebound")))
        replay = client.post(path, json=raw)
        assert replay.status_code == 200 and replay.json()["request"] == read.json()["request"]
        assert before == {name: (runtime.data_dir / name).read_bytes() for name in before}

    with worker.restarted(runtime) as reopened:
        assert reopened.asset_image_service.run_original_request(record.id) is None
        restored = reopened.asset_image_service.read_request(*root, "request-1")
        assert restored.request.model_dump(mode="json") == record.model_dump(mode="json")
        assert reopened.asset_image_service.request_state(restored.request)["status"] == "completed"
        assert reopened.asset_image_service.admit(*root, command).replayed
        assert admission.owner(reopened, route).characters[0].reference_sheet.model_dump(mode="json") == before_sheet
    assert len(bound_transport["calls"]) == 4


def setup_qwen(runtime, route="local", model="qwen-image-2.0"):
    root, owner, target = full_body.setup(runtime, route, uploaded_body=True)
    root_object = runtime.series_store[root[1]] if root[0] == "series" else runtime.scripts[root[1]]
    root_object.model_settings.t2i_model = model
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    return root, owner, target


@pytest.mark.parametrize("route", ("local", "standalone"))
@pytest.mark.parametrize("model", qwen.MODELS)
def test_existing_http_entry_uses_original_core_with_each_qwen_id(runtime, bound_transport, route, model):
    root, owner, _ = setup_qwen(runtime, route)
    qwen.queue_images(bound_transport)
    path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/assets/generate"
    raw = full_body.full_body_command(model_name=model, negative_prompt="draft").model_dump(exclude_unset=True)
    raw.pop("client_request_id")
    raw["asset_type"] = "character"
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        accepted = client.post(path, json=raw)
        assert accepted.status_code == 200, accepted.text
        assert len(owner.asset_image_requests) == 1
        record = owner.asset_image_requests[0]
        assert accepted.json()["_task_id"] == record.id
        assert record.channel == "full_body"
        expected = "qwen-image-2.0-pro" if "2.0-pro" in model else "qwen-image-2.0"
        assert record.members[0].execution.provider_model_id == expected
        assert record.members[0].execution.phase == "completed"
        assert qwen.posts(bound_transport)[0]["model"] == expected
        assert len(bound_transport["calls"]) == 2
        runtime.process_asset_generation_task(record.id)
        assert len(bound_transport["calls"]) == 2


@pytest.mark.parametrize("reference", ("base", "three_view", "headshot"))
@pytest.mark.parametrize("model", qwen.core.MODELS)
def test_strict_full_body_rejects_reference_sources_before_binding(runtime, bound_transport, monkeypatch, reference, model):
    _, owner, target = setup_qwen(runtime, model=model)
    full_body.add_reference(target, reference)
    runtime._commit_storage("projects.json")
    binder = Mock(side_effect=AssertionError("reference-bearing full body reached T2I binding"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        before = deepcopy(runtime._committed)
        response = client.post("/projects/p/asset_image_requests", json=full_body.full_body_command().model_dump(exclude_unset=True))
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "studio_image_reference_not_supported"
        assert runtime._committed == before and not runtime.asset_generation_tasks
        assert owner.asset_image_requests == []
    binder.assert_not_called()
    assert bound_transport["calls"] == []


@pytest.mark.parametrize("reference", ("", "uploads/reference.png"))
def test_explicit_reference_url_never_becomes_qwen_full_body_t2i(runtime, bound_transport, monkeypatch, reference):
    setup_qwen(runtime)
    binder = Mock(side_effect=AssertionError("explicit reference reached T2I binding"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        before = deepcopy(runtime._committed)
        response = client.post("/projects/p/asset_image_requests", json=full_body.full_body_command(
            reference_image_url=reference,
        ).model_dump(exclude_unset=True))
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "studio_image_reference_not_supported"
        assert runtime._committed == before and not runtime.asset_generation_tasks
    binder.assert_not_called()
    assert bound_transport["calls"] == []


@pytest.mark.parametrize("model", qwen.core.MODELS)
@pytest.mark.parametrize("negative,style_negative,apply_style,status", (
    ("😀" * 500, "ignored", False, 200),
    ("😀" * 497, "x", True, 200),
    ("字" * 501, "", False, 422),
    ("😀" * 498, "x", True, 422),
), ids=("draft-500", "inherited-500", "draft-501", "inherited-501"))
def test_effective_full_body_negative_uses_root_inheritance_and_codepoints(
    runtime, bound_transport, monkeypatch, model, negative, style_negative, apply_style, status,
):
    _, owner, _ = setup_qwen(runtime, "global", model)
    runtime.scripts["p"].series_id = "s"
    runtime.series_store["s"].art_direction = qwen.style(style_negative, "inherited-positive")
    runtime._commit_storage("projects.json", "series.json")
    binder = Mock(wraps=execution_module.bind_media_runtime_authority)
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    if status == 200:
        qwen.queue_images(bound_transport)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        before = deepcopy(runtime._committed)
        response = client.post("/projects/p/asset_image_requests", json=full_body.full_body_command(
            prompt="Authored pose", negative_prompt=negative, apply_style=apply_style,
        ).model_dump(exclude_unset=True))
        assert response.status_code == status, response.text
        if status == 422:
            assert response.json()["detail"] == "studio_image_negative_prompt_exceeds_500_characters"
            assert runtime._committed == before and not runtime.asset_generation_tasks
            assert owner.asset_image_requests == [] and bound_transport["calls"] == []
            binder.assert_not_called()
        else:
            binder.assert_called_once()
            record = owner.asset_image_requests[0]
            effective = f"{negative}, {style_negative}" if apply_style else negative
            assert len(effective) == 500
            assert record.owner_kind == "global" and record.owner_id == "library"
            assert record.base_prompt == "Authored pose"
            assert len(record.members) == 1 and record.members[0].execution.phase == "completed"
            assert record.members[0].execution.intent["prompt"] == "Authored pose" + (", inherited-positive" if apply_style else "")
            assert record.members[0].execution.intent["params"]["negative_prompt"] == effective
            assert qwen.posts(bound_transport)[0]["parameters"]["negative_prompt"] == effective
            assert len(bound_transport["calls"]) == 2


@pytest.mark.parametrize("model", qwen.core.MODELS)
def test_same_full_body_request_key_cannot_change_channel_or_payload(runtime, bound_transport, monkeypatch, model):
    _, owner, _ = setup_qwen(runtime, model=model)
    qwen.queue_images(bound_transport, count=2)
    raw = full_body.full_body_command(batch_size=2, negative_prompt="draft").model_dump(exclude_unset=True)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        accepted = client.post("/projects/p/asset_image_requests", json=raw)
        assert accepted.status_code == 200, accepted.text
        before = deepcopy(runtime._committed)
        binder = Mock(side_effect=AssertionError("conflicting raw request rebound"))
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
        for change in ({"generation_type": "reference_sheet"}, {"batch_size": 1}, {"negative_prompt": None}, {"prompt": "other"}):
            response = client.post("/projects/p/asset_image_requests", json=raw | change)
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "storage_conflict"
            assert response.json()["commit_uncertain"] is False
        assert runtime._committed == before and len(owner.asset_image_requests) == 1
        binder.assert_not_called()
    assert len(bound_transport["calls"]) == 4


@pytest.mark.parametrize("model", qwen.core.MODELS)
def test_qwen_full_body_history_requires_its_own_frozen_base_prompt(model):
    raw = qwen.raw_request(model=model)
    raw.update(entrypoint=full_body.ENTRYPOINT, channel="full_body", base_prompt="Authored pose")
    record = AssetImageRequestRecord.model_validate(raw)
    assert record.model_dump(mode="json") == raw
    assert AssetImageRequestRecord.model_validate_json(record.model_dump_json()) == record
    for base in (None, "", "   "):
        with pytest.raises(ValueError, match="Full body history requires"):
            AssetImageRequestRecord.model_validate(raw | {"base_prompt": base})
    with pytest.raises(ValueError):
        AssetImageRequestRecord.model_validate(raw | {"channel": "reference_sheet"})


@pytest.mark.parametrize("model", qwen.core.MODELS)
def test_unknown_qwen_full_body_stops_original_siblings_and_reopen_never_resubmits(runtime, bound_transport, model):
    root, owner, target = setup_qwen(runtime, model=model)
    before_images = target.full_body_asset.model_dump(mode="json")
    bound_transport["queue"] = [requests.Timeout("synthetic response lost")]
    command = full_body.full_body_command(batch_size=2)
    raw = command.model_dump(exclude_unset=True)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        accepted = client.post("/projects/p/asset_image_requests", json=raw)
        assert accepted.status_code == 200, accepted.text
        record = owner.asset_image_requests[0]
        assert [member.execution.phase for member in record.members] == ["submitting", "cancelled"]
        assert runtime.asset_image_service.request_state(record)["status"] == "needs_confirmation"
        assert target.full_body_asset.model_dump(mode="json") == before_images
        assert runtime.asset_image_service.run_original_request(record.id) is None
    assert len(qwen.posts(bound_transport)) == 1 and len(bound_transport["calls"]) == 1
    with worker.restarted(runtime) as reopened:
        assert reopened.asset_image_service.run_original_request(record.id) is None
        assert reopened.asset_image_service.admit(*root, command).replayed
        restored = reopened.asset_image_service.read_request(*root, "request-1")
        assert reopened.asset_image_service.request_state(restored.request)["status"] == "needs_confirmation"
    assert len(bound_transport["calls"]) == 1
