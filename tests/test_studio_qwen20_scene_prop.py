"""Qwen scene/prop T2I retains Studio ownership, text and original receipts.

The success path runs real HTTP routes, Core, factory and the frozen provider;
only external HTTP transport is substituted. Uploaded history is not an input.
"""

from copy import deepcopy
import hashlib
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import requests

from src.apps.comic_gen import asset_image_execution as execution_module
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.pipeline import ComicGenPipeline
from tests import test_qwen20_studio_image as qwen
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_worker as worker
from tests import test_studio_scene_prop_bulk as bulk
from tests import test_studio_scene_prop_service as scene


runtime = admission.runtime
bound_transport = worker.bound_transport
ENTRYPOINT = "studio.asset.scene_prop.v1"
OWNER_MODELS = [
    (route, model) for route in admission.ROUTES for model in qwen.core.MODELS
] + [
    (route, f"qwen/{model}#image")
    for route in ("local", "standalone") for model in qwen.core.MODELS
]


def setup(item, kind, route="local", model="qwen-image-2.0"):
    root, target = scene.setup(item, kind, route, selected=True)
    root_object = item.series_store[root[1]] if root[0] == "series" else item.scripts[root[1]]
    root_object.model_settings.t2i_model = model
    target.image_asset.variants[0].is_uploaded_source = True
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    return root, admission.owner(item, route), target


@pytest.mark.parametrize("kind", scene.KINDS)
@pytest.mark.parametrize("route,model", OWNER_MODELS)
def test_real_http_qwen_scene_prop_keeps_uploaded_history_and_original_proof(
    runtime, bound_transport, monkeypatch, kind, route, model,
):
    root, owner, target = setup(runtime, kind, route, model)
    root_object = runtime.series_store[root[1]] if root[0] == "series" else runtime.scripts[root[1]]
    root_object.art_direction = qwen.style()
    runtime._commit_storage("projects.json", "series.json")
    before_target = target.model_dump(mode="json")
    qwen.queue_images(bound_transport, count=2)
    path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/asset_image_requests"
    command = scene.command(kind, batch_size=2, seed=0, prompt="Authored composition",
                            apply_style=True, negative_prompt="draft-negative")
    raw = command.model_dump(exclude_unset=True)

    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        response = client.post(path, json=raw)
        assert response.status_code == 200, response.text
        record = owner.asset_image_requests[0]
        assert (record.entrypoint, record.asset_type, record.channel) == (ENTRYPOINT, kind, "image")
        assert record.id == response.json()["request"]["id"] == target.image_request_id
        assert "base_prompt" not in record.model_dump(mode="json")
        assert target.image_asset.selected_id == "old" and target.image_url == "uploads/old.png"
        original = next(variant for variant in target.image_asset.variants if variant.id == "old")
        assert original.is_favorited and original.is_uploaded_source
        after_target = target.model_dump(mode="json")
        for field in set(before_target) - {"image_asset", "image_url", "image_request_id", "status"}:
            assert after_target[field] == before_target[field]
        assert [variant.id for variant in target.image_asset.variants] == [
            *[member.variant_id for member in reversed(record.members)], "old",
        ]
        payloads = qwen.posts(bound_transport)
        assert len(payloads) == 2
        expected_model = model.split("/")[-1].split("#")[0]
        for payload in payloads:
            assert payload["model"] == expected_model
            assert payload["input"]["messages"] == [{
                "role": "user", "content": [{"text": "Authored composition, style-positive"}],
            }]
            assert payload["parameters"] == {
                "size": "1024*576" if kind == "scene" else "1024*1024",
                "n": 1, "seed": 0, "watermark": False, "prompt_extend": True,
                "negative_prompt": "draft-negative, style-negative",
            }
        assert len(bound_transport["calls"]) == 4  # Two synchronous submits and two fetches; no polling.
        for member in record.members:
            execution = member.execution
            assert execution.phase == "completed" and execution.media == []
            assert execution.intent["intent_contract_version"] == qwen.core.VERSION
            assert execution.prepared_artifact == execution.artifact
            content = (runtime.data_dir / execution.output_ref).read_bytes()
            assert content.startswith(b"\x89PNG")
            assert execution.artifact["sha256"] == hashlib.sha256(content).hexdigest()
            assert execution.intent["prompt"] == "Authored composition, style-positive"
        assert [variant.prompt_used for variant in target.image_asset.variants[:-1]] == [
            "Authored composition, style-positive", "Authored composition, style-positive",
        ]
        before_disk = {file.name: file.read_bytes() for file in runtime.data_dir.glob("*.json")}
        read = client.get(path + f"/{kind}-request")
        assert read.status_code == 200, read.text
        assert all(member["status"] == "completed" for member in read.json()["request"]["members"])
        client.app.state.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", Mock(side_effect=AssertionError("replay rebound")))
        replay = client.post(path, json=raw)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["request"] == read.json()["request"]
        assert before_disk == {file.name: file.read_bytes() for file in runtime.data_dir.glob("*.json")}
    with worker.restarted(runtime) as reopened:
        assert reopened.asset_image_service.run_original_request(record.id) is None
        restored = reopened.asset_image_service.read_request(*root, f"{kind}-request")
        assert restored.request.model_dump(mode="json") == record.model_dump(mode="json")
        assert reopened.asset_image_service.admit(*root, command).replayed
    assert len(bound_transport["calls"]) == 4


@pytest.mark.parametrize("kind", scene.KINDS)
@pytest.mark.parametrize("negative,style_negative,apply_style,status", (
    ("😀" * 500, "ignored", False, 200),
    ("😀" * 497, "x", True, 200),
    ("字" * 501, "", False, 422),
    ("😀" * 498, "x", True, 422),
), ids=("draft-500", "inherited-500", "draft-501", "inherited-501"))
def test_qwen_scene_prop_negative_uses_request_root_and_codepoints(
    runtime, bound_transport, monkeypatch, kind, negative, style_negative, apply_style, status,
):
    _, owner, _ = setup(runtime, kind, "global", "qwen-image-2.0-pro")
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
        response = client.post("/projects/p/asset_image_requests", json=scene.command(
            kind, prompt="Authored scene or prop", negative_prompt=negative, apply_style=apply_style,
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
            assert (record.owner_kind, record.owner_id) == ("global", "library")
            effective = f"{negative}, {style_negative}" if apply_style else negative
            assert len(effective) == 500
            intent = record.members[0].execution.intent
            assert intent["prompt"] == "Authored scene or prop" + (", inherited-positive" if apply_style else "")
            assert intent["params"]["negative_prompt"] == effective
            assert qwen.posts(bound_transport)[0]["parameters"]["negative_prompt"] == effective
            assert len(bound_transport["calls"]) == 2


@pytest.mark.parametrize("kind", scene.KINDS)
@pytest.mark.parametrize("reference", ["", "uploads/reference.png"])
def test_qwen_scene_prop_explicit_reference_is_rejected_before_binding(
    runtime, bound_transport, monkeypatch, kind, reference,
):
    setup(runtime, kind)
    binder = Mock(side_effect=AssertionError("reference input bound as T2I"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        before = deepcopy(runtime._committed)
        response = client.post("/projects/p/asset_image_requests", json=scene.command(
            kind, reference_image_url=reference,
        ).model_dump(exclude_unset=True))
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "studio_image_reference_not_supported"
        assert runtime._committed == before and not runtime.asset_generation_tasks
    binder.assert_not_called()
    assert bound_transport["calls"] == []


@pytest.mark.parametrize("kind", scene.KINDS)
def test_qwen_scene_prop_same_key_cannot_change_kind_text_count_or_negative_presence(
    runtime, bound_transport, monkeypatch, kind,
):
    _, owner, _ = setup(runtime, kind)
    qwen.queue_images(bound_transport)
    raw = scene.command(kind, prompt="Authored", negative_prompt="draft").model_dump(exclude_unset=True)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        accepted = client.post("/projects/p/asset_image_requests", json=raw)
        assert accepted.status_code == 200, accepted.text
        before = deepcopy(runtime._committed)
        binder = Mock(side_effect=AssertionError("changed request rebound"))
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
        changed = [raw | delta for delta in (
            {"asset_type": "prop" if kind == "scene" else "scene"},
            {"prompt": "Other"}, {"batch_size": 2}, {"negative_prompt": None},
        )]
        changed.append({key: value for key, value in raw.items() if key != "negative_prompt"})
        for body in changed:
            response = client.post("/projects/p/asset_image_requests", json=body)
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "storage_conflict"
            assert response.json()["commit_uncertain"] is False
        assert runtime._committed == before and len(owner.asset_image_requests) == 1
        binder.assert_not_called()
    assert len(bound_transport["calls"]) == 2


@pytest.mark.parametrize("kind", scene.KINDS)
@pytest.mark.parametrize("model", qwen.core.MODELS)
def test_qwen_scene_prop_history_roundtrip_retains_exact_existing_shape(kind, model):
    raw = qwen.raw_request(model=model)
    raw.update(entrypoint=ENTRYPOINT, asset_type=kind, channel="image")
    record = AssetImageRequestRecord.model_validate(raw)
    assert record.model_dump(mode="json") == raw
    assert AssetImageRequestRecord.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValueError, match="This image history cannot carry a frozen base prompt"):
        AssetImageRequestRecord.model_validate(raw | {"base_prompt": "not a full body"})


@pytest.mark.parametrize("kind", scene.KINDS)
@pytest.mark.parametrize("model", qwen.MODELS)
def test_existing_qwen_scene_prop_models_use_core_for_only_the_main_channel(kind, model):
    assert ComicGenPipeline._is_core_asset_image(kind, "all", model)
    assert not ComicGenPipeline._is_core_asset_image(kind, "full_body", model)
    assert not ComicGenPipeline._is_core_asset_image(kind, "reference_sheet", model)


@pytest.mark.parametrize("kind", scene.KINDS)
def test_unknown_qwen_scene_prop_retains_original_request_without_restart_resubmit(runtime, bound_transport, kind):
    root, owner, target = setup(runtime, kind, model="qwen-image-2.0-pro")
    before_images = target.image_asset.model_dump(mode="json")
    bound_transport["queue"] = [requests.Timeout("synthetic response lost")]
    command = scene.command(kind, batch_size=2)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        accepted = client.post("/projects/p/asset_image_requests", json=command.model_dump(exclude_unset=True))
        assert accepted.status_code == 200, accepted.text
        record = owner.asset_image_requests[0]
        assert [member.execution.phase for member in record.members] == ["submitting", "cancelled"]
        assert runtime.asset_image_service.request_state(record)["status"] == "needs_confirmation"
        assert target.image_asset.model_dump(mode="json") == before_images
    with worker.restarted(runtime) as reopened:
        assert reopened.asset_image_service.run_original_request(record.id) is None
        assert reopened.asset_image_service.admit(*root, command).replayed
        restored = reopened.asset_image_service.read_request(*root, f"{kind}-request")
        assert reopened.asset_image_service.request_state(restored.request)["status"] == "needs_confirmation"
    assert len(qwen.posts(bound_transport)) == len(bound_transport["calls"]) == 1


@pytest.mark.parametrize("kind", scene.KINDS)
@pytest.mark.parametrize("route", ("local", "standalone"))
@pytest.mark.parametrize("model", qwen.MODELS)
def test_old_http_qwen_scene_prop_runs_the_original_core_task_once(runtime, bound_transport, kind, route, model):
    root, owner, target = setup(runtime, kind, route, model)
    qwen.queue_images(bound_transport)
    path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/assets/generate"
    raw = scene.command(kind, model_name=model, prompt="Authored through old entry",
                        negative_prompt="draft").model_dump(exclude_unset=True)
    raw.pop("client_request_id")
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        response = client.post(path, json=raw)
        assert response.status_code == 200, response.text
        assert len(owner.asset_image_requests) == 1
        record = owner.asset_image_requests[0]
        assert response.json()["_task_id"] == record.id == target.image_request_id
        assert (record.entrypoint, record.asset_type, record.channel) == (ENTRYPOINT, kind, "image")
        execution = record.members[0].execution
        assert execution.phase == "completed"
        assert execution.provider_model_id == model.split("/")[-1].split("#")[0]
        assert qwen.posts(bound_transport)[0]["input"]["messages"][0]["content"] == [
            {"text": "Authored through old entry"},
        ]
        assert qwen.posts(bound_transport)[0]["parameters"]["negative_prompt"] == "draft"
        for _ in range(2):
            read = client.get(f"/tasks/{record.id}")
            assert read.status_code == 200 and read.json()["status"] == "completed"
            assert read.json()["asset_type"] == kind
        runtime.process_asset_generation_task(record.id)
        assert len(bound_transport["calls"]) == 2


@pytest.mark.parametrize("kind", scene.KINDS)
@pytest.mark.parametrize("reference", ["", "uploads/reference.png"])
def test_old_http_qwen_reference_rejects_without_falling_back(runtime, bound_transport, monkeypatch, kind, reference):
    _, owner, _ = setup(runtime, kind)
    binder = Mock(side_effect=AssertionError("invalid legacy request reached binding"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        before = deepcopy(runtime._committed)
        response = client.post("/projects/p/assets/generate", json={
            "asset_id": "c", "asset_type": kind, "generation_type": "all",
            "reference_image_url": reference, "apply_style": False,
        })
        # The existing legacy endpoint maps ValueError to 404; keep its API contract.
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "studio_image_reference_not_supported"
        assert runtime._committed == before and not runtime.asset_generation_tasks
        assert owner.asset_image_requests == []
    binder.assert_not_called()
    assert bound_transport["calls"] == []


@pytest.mark.parametrize("model", qwen.MODELS)
@pytest.mark.parametrize("outcome", ("completed", "unknown"))
def test_real_http_qwen_bulk_uses_prepared_scene_prop_and_stops_before_unknown_sibling(
    runtime, bound_transport, model, outcome,
):
    project = bulk.setup_bulk(runtime)
    project.model_settings.t2i_model = model
    project.art_direction = qwen.style("bulk-negative", "bulk-positive")
    runtime._commit_storage("projects.json")
    if outcome == "unknown":
        bound_transport["queue"] = [requests.Timeout("synthetic bulk response lost")]
    else:
        qwen.queue_images(bound_transport, count=2)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        response = client.post("/projects/p/generate_assets")
        assert response.status_code == (200 if outcome == "completed" else 502), response.text
        records = project.asset_image_requests
        assert len(records) == (2 if outcome == "completed" else 1)
        assert {record.request_root_scope_id for record in records} == {project.asset_image_request_scope_id}
        expected_prompts = [
            "Scene Concept Art: First. original first. High quality, detailed. bulk-positive",
            "Prop Design: Second. original second. Isolated on white background, high quality, detailed. bulk-positive",
        ]
        expected_size = ["1024*576", "1024*1024"]
        payloads = qwen.posts(bound_transport)
        assert len(payloads) == len(records)
        for index, (record, payload) in enumerate(zip(records, payloads, strict=True)):
            assert (record.entrypoint, record.channel) == (ENTRYPOINT, "image")
            assert record.asset_type == ("scene" if index == 0 else "prop")
            assert record.members[0].execution.intent["prompt"] == expected_prompts[index]
            assert payload["model"] == model.split("/")[-1].split("#")[0]
            assert payload["input"]["messages"][0]["content"] == [{"text": expected_prompts[index]}]
            assert payload["parameters"]["negative_prompt"] == "bulk-negative"
            assert payload["parameters"]["size"] == expected_size[index]
        if outcome == "completed":
            assert [response.json()[kind + "s"][0]["status"] for kind in scene.KINDS] == ["completed"] * 2
            assert all(record.members[0].execution.phase == "completed" for record in records)
            assert all((runtime.data_dir / record.members[0].execution.output_ref).is_file() for record in records)
            assert len(bound_transport["calls"]) == 4
        else:
            assert project.scenes[0].status == "needs_confirmation"
            assert project.props[0].image_request_id is None
            assert project.props[0].status == "pending"
            assert len(bound_transport["calls"]) == 1
        before = [record.model_dump(mode="json") for record in records]
    with worker.restarted(runtime) as reopened:
        assert [record.model_dump(mode="json") for record in reopened.scripts["p"].asset_image_requests] == before
        for record in records:
            assert reopened.asset_image_service.run_original_request(record.id) is None
    assert len(bound_transport["calls"]) == (4 if outcome == "completed" else 1)


@pytest.mark.parametrize("kind", scene.KINDS)
def test_qwen_scene_prop_partial_batch_keeps_verified_png_and_original_choice(runtime, bound_transport, kind):
    root, owner, target = setup(runtime, kind, model="qwen-image-2.0-pro")
    qwen.queue_images(bound_transport)
    bound_transport["queue"].append(requests.Timeout("synthetic second response lost"))
    command = scene.command(kind, batch_size=3)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        response = client.post("/projects/p/asset_image_requests", json=command.model_dump(exclude_unset=True))
        assert response.status_code == 200, response.text
        record = owner.asset_image_requests[0]
        assert [member.execution.phase for member in record.members] == ["completed", "submitting", "cancelled"]
        assert target.image_asset.selected_id == "old" and target.image_url == "uploads/old.png"
        first = record.members[0].execution
        data = (runtime.data_dir / first.output_ref).read_bytes()
        assert first.artifact == first.prepared_artifact
        assert first.artifact["sha256"] == hashlib.sha256(data).hexdigest()
        assert [variant.id for variant in target.image_asset.variants] == [record.members[0].variant_id, "old"]
        before = record.model_dump(mode="json")
    with worker.restarted(runtime) as reopened:
        assert reopened.asset_image_service.run_original_request(record.id) is None
        assert reopened.asset_image_service.admit(*root, command).replayed
        assert reopened.asset_image_service.read_request(*root, f"{kind}-request").request.model_dump(mode="json") == before
        assert (reopened.data_dir / first.output_ref).read_bytes() == data
    assert len(qwen.posts(bound_transport)) == 2 and len(bound_transport["calls"]) == 3
