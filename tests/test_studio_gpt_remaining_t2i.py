"""Existing GPT T2I asset entries share retained Core records.

Real Studio ownership/API/Core/normalization/projection; only external transport
is replaced.
"""

import hashlib
import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import requests

from src.apps.comic_gen import asset_image_execution as execution_module
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.pipeline import ProjectGenerationError
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_worker as worker
from tests import test_studio_full_body_core as bodies
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_scene_prop_bulk as bulk
from tests import test_studio_scene_prop_service as images


runtime = admission.runtime
transport = gpt.transport
PROMPT = "Bounded existing GPT T2I author prompt"
SIZES = {"full_body": "1024x1536", "scene": "1536x1024", "prop": "1024x1024"}
CASES = (
    pytest.param("full_body", "local", gpt.MODELS[0], 2, id="body-local-batch"),
    pytest.param("scene", "local", gpt.MODELS[0], 2, id="scene-local-batch"),
    pytest.param("prop", "local", gpt.MODELS[0], 2, id="prop-local-batch"),
    pytest.param("full_body", "global", gpt.MODELS[1], 1, id="body-global-alias-single"),
    pytest.param("scene", "series", gpt.MODELS[0], 1, id="scene-series-single"),
    pytest.param("prop", "standalone", gpt.MODELS[1], 1, id="prop-standalone-alias-single"),
)


def _setup(item, kind, route="local", model=gpt.MODELS[0]):
    if kind == "full_body":
        root, _, target = bodies.setup(item, route)
    else:
        root, target = images.setup(item, kind, route, selected=True)
    actual_root = item.scripts[root[1]] if root[0] == "project" else item.series_store[root[1]]
    actual_root.model_settings.t2i_model = model
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    return root, target.model_dump(mode="json")


def _body(kind, model=gpt.MODELS[0], count=1):
    return {
        "client_request_id": "remaining-gpt-" + kind, "asset_id": "c",
        "asset_type": "character" if kind == "full_body" else kind,
        "generation_type": "full_body" if kind == "full_body" else "all",
        "model_name": model, "prompt": PROMPT, "apply_style": False, "batch_size": count,
    }


def _path(root):
    return f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/asset_image_requests"


def _target(item, kind, route):
    return getattr(admission.owner(item, route), "characters" if kind == "full_body" else kind + "s")[0]


def _assert_projection(item, kind, route, before, record, count):
    target = _target(item, kind, route)
    ids = [member.variant_id for member in record.members]
    old_id = "body-old" if kind == "full_body" else "old"
    selected = ids[0] if count == 1 else old_id
    history = target.full_body_asset if kind == "full_body" else target.image_asset
    assert history is not None and history.selected_id == selected
    assert {value.id for value in history.variants} == {*ids, old_id}
    assert next(value for value in history.variants if value.id == old_id).is_favorited
    expected_url = next(value.url for value in history.variants if value.id == selected)
    pointer = "full_body_request_id" if kind == "full_body" else "image_request_id"
    assert getattr(target, pointer) == record.id
    if kind == "full_body":
        assert target.full_body is not None and target.reference_sheet is not None
        assert {value.id for value in target.full_body.image_variants} == {*ids, old_id}
        assert target.full_body.selected_image_id == selected
        assert target.full_body_image_url == expected_url
        assert target.full_body_prompt == PROMPT and record.base_prompt == PROMPT
        assert target.reference_sheet.model_dump(mode="json") == before["reference_sheet"]
        assert target.reference_sheet_request_id == before["reference_sheet_request_id"]
        assert target.image_url == before["image_url"] and target.voice_id == before["voice_id"]
        assert [value.model_dump(mode="json") for value in target.full_body.video_variants] == before["full_body"]["video_variants"]
        assert target.full_body.selected_video_id == before["full_body"]["selected_video_id"]
        assert target.full_body.video_prompt == before["full_body"]["video_prompt"]
    else:
        assert target.image_url == expected_url and record.base_prompt is None
        after = target.model_dump(mode="json")
        for field in set(before) - {"image_asset", "image_url", "image_request_id", "status"}:
            assert after[field] == before[field]


def _assert_frozen(record, kind):
    expected_tuple = ("studio.asset.full_body.v1", "character", "full_body") if kind == "full_body" else (
        "studio.asset.scene_prop.v1", kind, "image",
    )
    assert (record.entrypoint, record.asset_type, record.channel) == expected_tuple
    # This is an actual admitted record, not a fabricated valid execution fixture.
    raw = record.model_dump(mode="json")
    assert AssetImageRequestRecord.model_validate_json(json.dumps(raw)).model_dump(mode="json") == raw
    for member in record.members:
        execution = member.execution
        assert execution.provider_model_id == "gpt-image-2"
        assert execution.intent["model_id"] == gpt.MODELS[1]
        assert execution.intent["mode"] == "t2i" and execution.media == []
        assert "intent_contract_version" not in execution.intent
        assert execution.intent["prompt"] == PROMPT
        assert execution.intent["params"] == {"size": SIZES[kind]}


@pytest.mark.parametrize("kind,route,model,count", CASES)
def test_new_tuples_keep_real_owners_wire_history_and_selection(runtime, transport, kind, route, model, count):
    root, before = _setup(runtime, kind, route, model)
    gpt.queue_images(transport, "http", count=count)
    path = _path(root)
    body = _body(kind, model, count)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        gpt.configure(app, "http")
        accepted = client.post(path, json=body)
        assert accepted.status_code == 200, accepted.text
        assert [row["status"] for row in accepted.json()["request"]["members"]] == ["pending"] * count
        owner = admission.owner(runtime, route)
        assert len(owner.asset_image_requests) == 1
        record = owner.asset_image_requests[0]
        assert record.id == accepted.json()["request"]["id"]
        assert (record.request_root_kind, record.request_root_id) == root
        assert record.owner_kind == {"local": "script", "global": "global", "series": "series", "standalone": "series"}[route]
        assert record.owner_id == {"local": "p", "global": "library", "series": "s", "standalone": "s"}[route]
        _assert_frozen(record, kind)
        _assert_projection(runtime, kind, route, before, record, count)
        assert all(member.execution.phase == "completed" for member in record.members)
        for member in record.members:
            execution = member.execution
            assert execution.output_ref is not None and execution.artifact is not None
            assert execution.artifact == execution.prepared_artifact
            content = (runtime.data_dir / execution.output_ref).read_bytes()
            assert content.startswith(b"\x89PNG\r\n\x1a\n")
            assert hashlib.sha256(content).hexdigest() == execution.artifact["sha256"]
        posts = [json.loads(request.body) for request, _ in transport["calls"] if request.method == "POST"]
        assert posts == [{"prompt": PROMPT, "size": SIZES[kind], "quality": "high", "n": 1}] * count
        assert len(transport["calls"]) == 3 * count
        assert not transport["queue"] and not transport["cli"] and not transport["cli_queue"]
        app.state.runtime_environment.update({}, remove_keys=("MULEROUTER_API_KEY",), persist=lambda *_: None)
        persisted, calls = gpt.persisted(runtime), len(transport["calls"])
        observed = client.get(path + "/" + body["client_request_id"])
        replayed = client.post(path, json=body)
        assert observed.status_code == replayed.status_code == 200
        assert replayed.json()["replayed"] is True
        assert replayed.json()["request"] == observed.json()["request"]
        assert [row["status"] for row in observed.json()["request"]["members"]] == ["completed"] * count
        assert "credential_ref" not in json.dumps(observed.json())
        assert gpt.persisted(runtime) == persisted and len(transport["calls"]) == calls


@pytest.mark.parametrize("kind", ("full_body", "scene", "prop"))
def test_original_asset_post_uses_retained_gpt_t2i_request(runtime, transport, kind):
    _, before = _setup(runtime, kind)
    body = _body(kind)
    body.pop("client_request_id")
    gpt.queue_images(transport, "http", count=1)
    with TestClient(http.real_app(runtime)) as client:
        gpt.configure(client.app, "http")
        generated = client.post("/projects/p/assets/generate", json=body)
        assert generated.status_code == 200, generated.text
        task_id = generated.json()["_task_id"]
        status = client.get("/tasks/" + task_id)
        assert status.status_code == 200 and status.json()["status"] == "completed", status.text
        # The original API must retain a receipt alongside its generated image.
        records = runtime.read_committed_script("p").asset_image_requests
        assert len(records) == 1, "Existing GPT asset entry bypassed retained Core receipt"
        record = records[0]
        assert record.id == task_id
        assert runtime.asset_generation_tasks[task_id]["_core_image_request_id"] == record.id
        _assert_frozen(record, kind)
        _assert_projection(runtime, kind, "local", before, record, 1)
        observed = client.get("/projects/p/asset_image_requests/" + record.client_request_id)
        assert observed.status_code == 200
        assert observed.json()["request"]["members"][0]["status"] == "completed"
    assert [request.method for request, _ in transport["calls"]] == ["POST", "GET", "GET"]
    assert json.loads(transport["calls"][0][0].body) == {"prompt": PROMPT, "size": SIZES[kind], "quality": "high", "n": 1}
    assert not transport["queue"] and not transport["cli"]


@pytest.mark.parametrize("kind", ("full_body", "scene", "prop"))
def test_unknown_original_post_reopens_and_replays_without_live_binding(runtime, transport, kind):
    _setup(runtime, kind)
    body = _body(kind, count=2)
    path = "/projects/p/asset_image_requests"
    transport["queue"] = [requests.ConnectionError("Synthetic submit acknowledgement lost")]
    with TestClient(http.real_app(runtime)) as client:
        gpt.configure(client.app, "http")
        accepted = client.post(path, json=body)
        assert accepted.status_code == 200, accepted.text
        record = runtime.read_committed_script("p").asset_image_requests[0]
        assert [member.execution.phase for member in record.members] == ["submitting", "cancelled"]
        assert all(member.execution.prepared_artifact is None for member in record.members)
        _assert_frozen(record, kind)
        original = record.model_dump(mode="json")
        runtime.scripts["p"].model_settings.t2i_model = "wan2.7-image"
        runtime._commit_storage("projects.json")
    assert len(transport["calls"]) == 1 and transport["calls"][0][0].method == "POST"
    with worker.restarted(runtime) as restored:
        restored_app = http.real_app(restored)
        with TestClient(restored_app) as client:
            # The original journal remains authoritative with no current GPT key.
            assert "MULEROUTER_API_KEY" not in restored_app.state.runtime_environment.snapshot()
            assert restored.scripts["p"].asset_image_requests[0].model_dump(mode="json") == original
            before = gpt.persisted(restored)
            observed = client.get(path + "/" + body["client_request_id"])
            replayed = client.post(path, json=body)
            assert observed.status_code == replayed.status_code == 200
            assert replayed.json()["replayed"] is True
            assert replayed.json()["request"] == observed.json()["request"]
            assert [row["status"] for row in observed.json()["request"]["members"]] == ["needs_confirmation", "cancelled"]
            assert restored.asset_image_service.run_original_request(record.id) is None
            assert gpt.persisted(restored) == before
    assert len(transport["calls"]) == 1 and not transport["queue"] and not transport["cli"]


@pytest.mark.parametrize("kind,extra,reason", (
    pytest.param("full_body", {"seed": 0}, "studio_gpt_image_parameter_not_supported", id="body-seed-zero"),
    pytest.param("scene", {"watermark": False}, "studio_gpt_image_parameter_not_supported", id="scene-watermark-false"),
    pytest.param("prop", {"negative_prompt": " "}, "studio_gpt_image_negative_prompt_not_supported", id="prop-authored-negative"),
    pytest.param("scene", {"reference_image_url": "uploads/ref.png"}, "studio_image_reference_not_supported", id="scene-reference"),
    pytest.param("prop", {"seed": None}, "seed must be omitted", id="prop-null-seed"),
    pytest.param("full_body", {"watermark": None}, "bool_type", id="body-null-watermark"),
))
def test_unsupported_gpt_presence_is_rejected_before_binding_or_paid_work(runtime, transport, monkeypatch, kind, extra, reason):
    _setup(runtime, kind)
    with TestClient(http.real_app(runtime)) as client:
        gpt.configure(client.app, "http")
        before = gpt.persisted(runtime)
        binder = Mock(side_effect=AssertionError("Invalid request reached credential binding"))
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
        rejected = client.post("/projects/p/asset_image_requests", json=_body(kind) | extra)
        assert rejected.status_code == 422, rejected.text
        assert reason in rejected.text
        binder.assert_not_called()
        assert gpt.persisted(runtime) == before
        assert runtime.scripts["p"].asset_image_requests == [] and runtime.asset_generation_tasks == {}
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("kind", ("full_body", "scene", "prop"))
def test_synchronous_existing_entry_retains_gpt_receipt_and_pixels(runtime, transport, kind):
    _, before = _setup(runtime, kind)
    gpt.configure_http_runtime(runtime)
    gpt.queue_images(transport, "http", count=1)
    body = _body(kind)
    body.pop("client_request_id")
    generated = runtime.generate_asset("p", **body)
    assert generated.id == "p"
    records = runtime.read_committed_script("p").asset_image_requests
    assert len(records) == 1 and records[0].members[0].execution.phase == "completed"
    _assert_frozen(records[0], kind)
    _assert_projection(runtime, kind, "local", before, records[0], 1)
    assert [request.method for request, _ in transport["calls"]] == ["POST", "GET", "GET"]
    assert not transport["queue"] and not transport["cli"]


@pytest.mark.parametrize("outcome", ("completed", "unknown"))
def test_bulk_existing_gpt_assets_share_receipts_and_stop_after_unknown(runtime, transport, outcome):
    gpt.setup(runtime)
    project = bulk.setup_bulk(runtime)
    project.characters = []
    runtime._commit_storage("projects.json")
    gpt.queue_images(transport, "http", count=2)
    if outcome == "unknown":
        transport["queue"] = [requests.ConnectionError("Synthetic first bulk acknowledgement lost")]
    app = http.real_app(runtime)
    with TestClient(app) as client:
        gpt.configure(app, "http")
        generated = client.post("/projects/p/generate_assets")
        assert generated.status_code == (200 if outcome == "completed" else 502), generated.text
        saved = runtime.read_committed_script("p")
        assert saved is not None
        records = saved.asset_image_requests
        expected = [("scene", "first"), ("prop", "second")] if outcome == "completed" else [("scene", "first")]
        assert [(row.asset_type, row.asset_id) for row in records] == expected
        assert {row.request_root_scope_id for row in records} == {saved.asset_image_request_scope_id}
        for record in records:
            member = record.members[0]
            assert member.execution.intent["params"] == {"size": SIZES[record.asset_type]}
            assert member.execution.media == [] and "intent_contract_version" not in member.execution.intent
            assert member.execution.phase == ("completed" if outcome == "completed" else "submitting")
        if outcome == "completed":
            assert generated.json()["scenes"][0]["status"] == generated.json()["props"][0]["status"] == "completed"
            assert saved.scenes[0].image_request_id == records[0].id
            assert saved.props[0].image_request_id == records[1].id
        else:
            assert saved.props[0].image_request_id is None and saved.props[0].image_url is None
    assert [r.method for r, _ in transport["calls"]] == (["POST", "GET", "GET"] * 2 if outcome == "completed" else ["POST"])
    assert not transport["queue"] and not transport["cli"]
    if outcome == "unknown":
        with worker.restarted(runtime) as restored:
            assert len(restored.scripts["p"].asset_image_requests) == 1
            assert restored.scripts["p"].props[0].image_request_id is None
            assert restored.asset_image_service.run_original_request(records[0].id) is None
        assert len(transport["calls"]) == 1


def test_synchronous_unknown_gpt_body_keeps_receipt_without_reporting_success(runtime, transport):
    _, before = _setup(runtime, "full_body")
    gpt.configure_http_runtime(runtime)
    transport["queue"] = [requests.ConnectionError("Synthetic synchronous acknowledgement lost")]
    body = _body("full_body")
    body.pop("client_request_id")
    with pytest.raises(ProjectGenerationError, match="not confirmed"):
        runtime.generate_asset("p", **body)
    saved = runtime.read_committed_script("p")
    assert saved is not None and len(saved.asset_image_requests) == 1
    record = saved.asset_image_requests[0]
    assert record.members[0].execution.phase == "submitting"
    assert saved.characters[0].full_body_asset is not None
    assert saved.characters[0].full_body_asset.model_dump(mode="json") == before["full_body_asset"]
    runtime.process_asset_generation_task(record.id)
    assert runtime.get_asset_generation_task_status(record.id)["status"] == "needs_confirmation"
    assert [request.method for request, _ in transport["calls"]] == ["POST"]
    assert not transport["queue"] and not transport["cli"]
