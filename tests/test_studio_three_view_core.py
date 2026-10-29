"""Private first slice: real Studio HTTP to the existing bound GPT Edit Core.

Prepared outside the repository, not collected or run. The existing transport
fixture replaces external Session.send/CLI and polling sleep only. App, pipeline,
owner storage, request admission, factory, Core worker and PNG decoding stay real.
This first slice covers three project-root owners; broader recovery follows only
after these actual HTTP REDs and the Core prerequisite have been verified.
"""

import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import AssetUnit, ImageAsset, Script, Series
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.media_probe import probe_image_file
from tests import test_studio_core_image_admission as admission
from tests import test_studio_gpt_reference_sheet_core as gpt_sheet
from tests.frozen_image_fixtures import rgb_png_bytes


transport = gpt_sheet.transport
MODELS = ("gpt-image-2", "gpt-image/gpt-image-2#image")
ENTRYPOINT = "studio.asset.three_view.v1"
CONTRACT = "provider-gpt-image2-edit-bound-v1"
PUBLIC = "/projects/p/asset_image_requests"
EDIT = "https://api.mulerouter.ai/vendors/openai/v1/gpt-image-2/edit"
PROMPT = "Keep the red coat; produce front, side and back views."
SELECTED_ID = "selected-c"
SELECTED_REF = "uploads/selected-c.png"
PROTECTED_FIELDS = {
    "reference_sheet", "reference_sheet_request_id", "image_url",
    "full_body", "full_body_asset", "full_body_image_url",
    "full_body_prompt", "full_body_updated_at", "full_body_request_id",
    "head_shot", "headshot_asset", "headshot_image_url", "voice_id",
}


@pytest.fixture
def runtime(tmp_path, transport):
    # Unlike the admission unit fixture, keep every lazy product component real.
    item = ComicGenPipeline(data_dir=tmp_path / "output")
    item.scripts["p"] = Script.model_validate({
        "id": "p", "title": "Three-view first slice", "original_text": "Synthetic",
        "created_at": 1, "updated_at": 1,
    })
    item.series_store["s"] = Series.model_validate({
        "id": "s", "title": "Shared cast", "created_at": 1, "updated_at": 1,
    })
    try:
        yield item
    finally:
        item.close()


def setup(item, route, model):
    admission.setup(item, route)
    project = item.scripts["p"]
    project.model_settings.t2i_model = "wan2.7-image"
    project.model_settings.i2i_model = model
    # A portrait T2I default must not leak into the fixed square Edit contract.
    project.model_settings.character_aspect_ratio = "9:16"
    target = admission.owner(item, route).characters[0]
    sources = {
        "uploads/legacy-a.png": rgb_png_bytes("legacy A", width=16, height=16),
        "uploads/first-b.png": rgb_png_bytes("first B", width=16, height=16),
        SELECTED_REF: rgb_png_bytes("selected C", width=16, height=16),
        "uploads/sheet-old.png": rgb_png_bytes("canonical sheet", width=16, height=16),
    }
    for ref, content in sources.items():
        path = item.data_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    variants = [
        {"id": "first-b", "url": "uploads/first-b.png", "created_at": 1},
        {"id": SELECTED_ID, "url": SELECTED_REF, "created_at": 2,
         "is_uploaded_source": True, "upload_type": "full_body", "is_favorited": True},
    ]
    target.full_body_asset = ImageAsset.model_validate({
        "selected_id": SELECTED_ID, "variants": variants,
    })
    target.full_body = AssetUnit.model_validate({
        "selected_image_id": SELECTED_ID, "image_variants": variants,
        "image_prompt": "Existing full body", "image_updated_at": 1,
        "video_variants": [{"id": "motion-old", "url": "video/history.mp4",
                            "created_at": 1, "source_image_id": SELECTED_ID}],
        "selected_video_id": "motion-old", "video_prompt": "Keep existing motion",
        "video_updated_at": 1,
    })
    target.full_body_image_url = "uploads/legacy-a.png"
    target.full_body_prompt = "Existing full body"
    target.full_body_updated_at = 1
    target.reference_sheet = AssetUnit.model_validate({
        "selected_image_id": "sheet-old", "image_updated_at": 1,
        "image_variants": [{"id": "sheet-old", "url": "uploads/sheet-old.png",
                            "created_at": 1, "is_favorited": True}],
    })
    target.image_url = "uploads/sheet-old.png"
    target.voice_id = "existing-voice"
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    protected = target.model_dump(mode="json", include=PROTECTED_FIELDS)
    assert len({hashlib.sha256(content).hexdigest() for content in sources.values()}) == 4
    return sources[SELECTED_REF], protected


def app_for(item):
    return api.create_app(
        data_dir=item.data_dir, user_data_dir=item.data_dir.parent / "user",
        load_config=False, pipeline_instance=item,
    )


def raw():
    return {
        "client_request_id": "three-view-first-request", "asset_id": "c",
        "asset_type": "character", "generation_type": "three_view",
        "reference_variant_id": SELECTED_ID,
        "reference_image_url": SELECTED_REF, "apply_style": False,
        "prompt": PROMPT, "batch_size": 1,
    }


def assert_real_edit_wire(state, selected):
    assert [(request.method, request.url) for request, _ in state["calls"]] == [
        ("POST", EDIT), ("GET", EDIT + "/" + gpt_sheet.http_task_id()), ("GET", gpt_sheet.IMAGE_URL),
    ]
    assert json.loads(state["calls"][0][0].body) == {
        "prompt": PROMPT, "size": "1024x1024", "n": 1,
        "images": ["data:image/png;base64," + base64.b64encode(selected).decode()],
    }
    assert not state["queue"] and not state["cli_queue"] and not state["cli"]


def assert_completed(item, route, selected, protected):
    owner = admission.owner(item, route)
    assert len(owner.asset_image_requests) == 1
    record = owner.asset_image_requests[0]
    assert (record.entrypoint, record.asset_type, record.channel) == (
        ENTRYPOINT, "character", "three_view",
    )
    assert (record.request_root_kind, record.request_root_id) == ("project", "p")
    assert (record.owner_kind, record.owner_id) == {
        "local": ("script", "p"), "series": ("series", "s"),
        "global": ("global", "library"),
    }[route]
    assert record.base_prompt == PROMPT and len(record.members) == 1
    member = record.members[0]
    execution = member.execution
    assert execution.phase == "completed"
    assert execution.provider_model_id == "gpt-image-2"
    assert execution.intent["intent_contract_version"] == CONTRACT
    assert execution.intent["model_id"] == MODELS[1]
    assert execution.intent["mode"] == "i2i"
    assert execution.intent["params"] == {"size": "1024x1024"}
    assert len(execution.media) == 1
    media = execution.media[0]
    assert media["role"] == "reference_image" and media["ordinal"] == 0
    assert media["sha256"] == hashlib.sha256(selected).hexdigest()
    assert media["size_bytes"] == len(selected)
    assert media["declared_media_type"] == "image/png"
    output = item.data_dir / execution.output_ref
    assert execution.artifact is not None
    assert hashlib.sha256(output.read_bytes()).hexdigest() == execution.artifact["sha256"]
    decoded = probe_image_file(output)
    assert (decoded.width, decoded.height) == (16, 16)
    target = owner.characters[0]
    assert getattr(target, "three_view_request_id", None) == record.id
    assert target.three_view_asset is not None and target.three_views is not None
    assert target.three_view_asset.selected_id == target.three_views.selected_image_id == member.variant_id
    assert target.three_view_image_url == execution.output_ref
    assert target.three_view_prompt == target.three_views.image_prompt == PROMPT
    assert target.model_dump(mode="json", include=PROTECTED_FIELDS) == protected
    assert len(item.scripts["p"].asset_image_requests) == (route == "local")
    assert len(item.series_store["s"].asset_image_requests) == (route == "series")
    assert len(item.library_store.asset_image_requests) == (route == "global")
    return record


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("route", ("local", "series", "global"))
def test_project_owner_three_view_uses_saved_i2i_and_selected_c_in_real_edit(runtime, transport, route, model):
    selected, protected = setup(runtime, route, model)
    gpt_sheet.queue_images(transport, "http", count=1)
    app = app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        # No model override: the saved I2I selection must decide this contract.
        body = raw()
        accepted = client.post(PUBLIC, json=body)
        assert accepted.status_code == 200, accepted.text
        assert [m["status"] for m in accepted.json()["request"]["members"]] == ["pending"]
        assert_real_edit_wire(transport, selected)
        record = assert_completed(runtime, route, selected, protected)
        assert record.id == accepted.json()["request"]["id"]
        calls = len(transport["calls"])
        before = gpt_sheet.persisted(runtime)
        readback = client.get(PUBLIC + "/" + body["client_request_id"])
        replay = client.post(PUBLIC, json=body)
        assert readback.status_code == replay.status_code == 200
        assert readback.json()["reason"] is None and replay.json()["replayed"] is True
        assert [m["status"] for m in readback.json()["request"]["members"]] == ["completed"]
        assert replay.json()["request"] == readback.json()["request"]
        assert len(transport["calls"]) == calls and gpt_sheet.persisted(runtime) == before
        assert "credential_ref" not in json.dumps(readback.json())


def test_existing_three_view_http_routes_saved_i2i_to_core_without_changing_task_shape(runtime, transport):
    selected, protected = setup(runtime, "local", MODELS[1])
    gpt_sheet.queue_images(transport, "http", count=1)
    app = app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        accepted = client.post("/projects/p/assets/generate", json={
            "asset_id": "c", "asset_type": "character", "generation_type": "three_view",
            "apply_style": False, "prompt": PROMPT, "batch_size": 1,
        })
        assert accepted.status_code == 200, accepted.text
        # Read the actual old-entry transport before looking for a new receipt.
        # An unsupported strict tuple alone cannot prove the old paid wire changed.
        assert_real_edit_wire(transport, selected)
        record = assert_completed(runtime, "local", selected, protected)
        assert accepted.json()["_task_id"] == record.id
        status = client.get("/tasks/" + record.id)
        assert status.status_code == 200, status.text
        assert status.json()["status"] == "completed"
        assert runtime.scripts["p"].model_settings.t2i_model == "wan2.7-image"
        assert runtime.scripts["p"].model_settings.i2i_model == MODELS[1]
