"""Existing portrait semantics and strict Core HTTP behavior.

Reuse real app/pipeline/storage/worker fixtures. Only the inherited GPT fixture
replaces external Session.send/CLI and adapter polling sleep. Selected-local GPT
old-route requests require the same bound Edit and receipt as the strict entry.
Legitimate local GPT reference fallbacks now share the strict Edit and receipt contract.
"""

import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import AssetUnit, ImageAsset
from src.utils.media_probe import probe_image_file
from src.utils.system_check import get_ffmpeg_path
from tests import test_studio_core_image_admission as admission
from tests import test_studio_gpt_reference_sheet_core as gpt_sheet
from tests import test_studio_three_view_boundaries as boundary
from tests import test_studio_three_view_core as three_view
from tests.frozen_image_fixtures import rgb_png_bytes


runtime = three_view.runtime
transport = gpt_sheet.transport
PROMPT = "Keep Alice's face and red collar; crop to face and shoulders."
DEFAULT_PROMPT = (
    "Close-up portrait of the SAME character Alice. red coat. STRICTLY MAINTAIN "
    "the SAME face, hairstyle, skin tone, and facial features as the reference "
    "image. Zoom in on face and shoulders, detailed facial features, neutral "
    "expression, looking at viewer, high quality, masterpiece."
)
STYLE = "softbox portrait lighting"
OLD_ID = "portrait-old"
OLD_REF = "uploads/portrait-old.png"
MOTION_FIELDS = {"selected_video_id", "video_variants", "video_prompt", "video_updated_at"}
PROTECTED_FIELDS = {
    "id", "name", "description", "reference_sheet", "reference_sheet_request_id", "image_url",
    "full_body", "full_body_asset", "full_body_image_url", "full_body_request_id",
    "full_body_prompt", "full_body_updated_at", "full_body_motion_request_id",
    "three_views", "three_view_asset", "three_view_image_url", "three_view_request_id",
    "three_view_prompt", "three_view_updated_at", "voice_id",
}
SOURCES = {
    "selected": three_view.SELECTED_REF,
    "legacy-url": "uploads/legacy-a.png",
    "uploaded-three-view": "uploads/three-upload-first.png",
    "uploaded-headshot": "uploads/face-upload-first.png",
}


def setup_headshot(item, route="local", model=three_view.MODELS[0], *, source="selected", three_view_current=True):
    selected, _ = three_view.setup(item, route, model)
    target = admission.owner(item, route).characters[0]
    source_refs = [OLD_REF, "uploads/three-upload-first.png", "uploads/three-upload-second.png",
                   "uploads/face-upload-first.png", "uploads/face-upload-second.png"]
    for ref in source_refs:
        path = item.data_dir / ref
        path.write_bytes(rgb_png_bytes(ref, width=16, height=16))
    views = [
        {"id": "three-first", "url": source_refs[1], "created_at": 1,
         "is_uploaded_source": source != "uploaded-headshot", "upload_type": "three_views"},
        {"id": "three-second", "url": source_refs[2], "created_at": 2,
         "is_uploaded_source": source != "uploaded-headshot", "upload_type": "three_views"},
    ]
    target.three_view_asset = ImageAsset.model_validate({"selected_id": "three-second", "variants": views})
    target.three_views = AssetUnit.model_validate({
        "selected_image_id": "three-second", "image_variants": views,
        "image_prompt": "Existing views", "image_updated_at": 1,
    })
    target.three_view_image_url = source_refs[2]
    target.three_view_prompt = "Existing views"
    faces = [
        {"id": OLD_ID, "url": OLD_REF, "created_at": 1, "is_favorited": True,
         "prompt_used": "Previous portrait"},
        {"id": "face-first", "url": source_refs[3], "created_at": 2,
         "is_uploaded_source": True, "upload_type": "head_shot"},
        {"id": "face-second", "url": source_refs[4], "created_at": 3,
         "is_uploaded_source": True, "upload_type": "head_shot"},
    ]
    target.headshot_asset = ImageAsset.model_validate({"selected_id": OLD_ID, "variants": faces})
    target.head_shot = AssetUnit.model_validate({
        "selected_image_id": OLD_ID, "image_variants": faces,
        "image_prompt": "Previous portrait", "image_updated_at": 1,
        "selected_video_id": "face-motion-old", "video_prompt": "Keep speaking motion",
        "video_updated_at": 1, "video_variants": [{
            "id": "face-motion-old", "url": "video/face-motion-old.mp4", "created_at": 1,
            "audio_url": "audio/face-voice-old.wav", "source_image_id": OLD_ID,
            "is_favorited": True,
        }],
    })
    target.headshot_image_url = target.avatar_url = OLD_REF
    target.headshot_prompt = "Previous portrait"
    target.full_body_updated_at = 2
    target.three_view_updated_at = 3 if three_view_current else 1
    target.headshot_updated_at = 1
    target.is_consistent = False
    assert target.full_body_asset is not None and target.full_body is not None
    if source != "selected":
        target.full_body_asset.selected_id = target.full_body.selected_image_id = None
    if source in {"uploaded-three-view", "uploaded-headshot"}:
        target.full_body_image_url = None
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    # A, B and C are genuinely different references, not three labels for one PNG.
    abc = [(item.data_dir / ref).read_bytes() for ref in (
        "uploads/legacy-a.png", "uploads/first-b.png", three_view.SELECTED_REF,
    )]
    assert len({hashlib.sha256(content).hexdigest() for content in abc}) == 3
    assert abc[2] == selected
    return {
        "reference": (item.data_dir / SOURCES[source]).read_bytes(),
        "protected": target.model_dump(mode="json", include=PROTECTED_FIELDS),
        "motion": target.head_shot.model_dump(mode="json", include=MOTION_FIELDS),
        "old_faces": {v.id: v.model_dump(mode="json") for v in target.headshot_asset.variants},
    }


def saved_owner(item, route):
    if route == "local":
        return item._load_data()["p"]
    if route == "series":
        return item._load_series_data()["s"]
    return item._load_library_data()


def assert_wire(state, reference, prompt, *, strict=False):
    assert [(request.method, request.url) for request, _ in state["calls"]] == [
        ("POST", three_view.EDIT), ("GET", three_view.EDIT + "/" + gpt_sheet.http_task_id()),
        ("GET", gpt_sheet.IMAGE_URL),
    ]
    body = json.loads(state["calls"][0][0].body)
    encoded = "data:image/png;base64," + base64.b64encode(reference).decode()
    if strict:
        assert body == {"prompt": prompt, "size": "1024x1024", "n": 1, "images": [encoded]}
    else:
        # Preserve business semantics across the intentionally separate wire fix.
        assert ("image" in body) != ("images" in body)
        assert (body["images"] if "images" in body else [body["image"]]) == [encoded]
        assert body["prompt"] == prompt and body["size"] == "1024x1024" and body["n"] == 1
    assert not state["queue"] and not state["cli_queue"] and not state["cli"]


def assert_provider_image_pixels(output):
    """Core normalizes PNG encoding; decoded provider pixels must remain exact."""
    # Keep both inputs and decoded outputs inside the isolated media root.
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        directory = Path(temporary)
        expected_png = directory / "expected.png"
        expected_png.write_bytes(gpt_sheet.PNG)

        def decode(source, name):
            decoded = directory / name
            subprocess.run(
                [get_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-i", str(source), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(decoded)],
                capture_output=True, timeout=10, check=True,
            )
            return decoded.read_bytes()

        actual, expected = decode(output, "actual.rgb"), decode(expected_png, "expected.rgb")
    assert len(actual) == 16 * 16 * 3 and actual == expected


def assert_projection(item, route, before, base_prompt, effective_prompt, *, normalized=False):
    owner = saved_owner(item, route)
    target = owner.characters[0]
    assert target.model_dump() == admission.owner(item, route).characters[0].model_dump()
    assert target.status.value == "completed"
    assert target.headshot_asset is not None and target.head_shot is not None
    legacy = {v.id: v.model_dump(mode="json") for v in target.headshot_asset.variants}
    units = {v.id: v.model_dump(mode="json") for v in target.head_shot.image_variants}
    assert len(legacy) == len(units) == len(before["old_faces"]) + 1 and legacy == units
    assert {key: legacy[key] for key in before["old_faces"]} == before["old_faces"]
    selected = target.headshot_asset.selected_id
    assert selected is not None and selected not in before["old_faces"]
    assert selected == target.head_shot.selected_image_id
    assert target.headshot_image_url == target.avatar_url == legacy[selected]["url"]
    assert target.headshot_prompt == base_prompt and legacy[selected]["prompt_used"] == effective_prompt
    output = item.data_dir / legacy[selected]["url"]
    if normalized:
        assert_provider_image_pixels(output)
    else:
        assert output.read_bytes() == gpt_sheet.PNG
    decoded = probe_image_file(output)
    assert (decoded.width, decoded.height) == (16, 16)
    assert target.model_dump(mode="json", include=PROTECTED_FIELDS) == before["protected"]
    assert target.head_shot.model_dump(mode="json", include=MOTION_FIELDS) == before["motion"]
    return target


def assert_old_route_core_receipt(item, route, target, before, task_id, base_prompt, expected_consistency, client, state):
    owner = saved_owner(item, route)
    assert len(owner.asset_image_requests) == 1
    record = owner.asset_image_requests[0]
    assert (record.entrypoint, record.asset_type, record.channel) == (
        "studio.asset.headshot.v1", "character", "headshot",
    )
    assert (record.request_root_kind, record.request_root_id) == ("project", "p")
    assert (record.owner_kind, record.owner_id) == {
        "local": ("script", "p"), "series": ("series", "s"), "global": ("global", "library"),
    }[route]
    assert record.id == task_id == target.headshot_request_id
    assert list(item.asset_generation_tasks) == [task_id]
    assert item.asset_generation_tasks[task_id]["_core_image_request_id"] == record.id
    assert "_context" not in item.asset_generation_tasks[task_id]
    assert record.base_prompt == base_prompt and len(record.members) == 1
    member = record.members[0]
    execution = member.execution
    assert execution.phase == "completed" and execution.provider_model_id == "gpt-image-2"
    assert execution.intent["intent_contract_version"] == three_view.CONTRACT
    assert execution.intent["model_id"] == three_view.MODELS[1] and execution.intent["mode"] == "i2i"
    assert execution.intent["params"] == {"size": "1024x1024"}
    assert len(execution.media) == 1
    media = execution.media[0]
    assert media["role"] == "reference_image" and media["ordinal"] == 0
    assert media["sha256"] == hashlib.sha256(before["reference"]).hexdigest()
    assert media["size_bytes"] == len(before["reference"]) and media["declared_media_type"] == "image/png"
    assert execution.artifact is not None and execution.output_ref is not None
    assert hashlib.sha256((item.data_dir / execution.output_ref).read_bytes()).hexdigest() == execution.artifact["sha256"]
    assert target.headshot_asset is not None and target.head_shot is not None
    assert target.headshot_asset.selected_id == target.head_shot.selected_image_id == member.variant_id
    assert target.headshot_image_url == target.avatar_url == execution.output_ref
    assert target.head_shot.image_prompt == target.headshot_prompt == base_prompt
    assert target.is_consistent is expected_consistency
    assert len(item._load_data()["p"].asset_image_requests) == (route == "local")
    assert len(item._load_series_data()["s"].asset_image_requests) == (route == "series")
    assert len(item._load_library_data().asset_image_requests) == (route == "global")
    saved, calls = gpt_sheet.persisted(item), len(state["calls"])
    readback = client.get(three_view.PUBLIC + "/" + record.client_request_id)
    assert readback.status_code == 200, readback.text
    assert readback.json()["reason"] is None and readback.json()["request"]["id"] == record.id
    assert [value["status"] for value in readback.json()["request"]["members"]] == ["completed"]
    assert len(state["calls"]) == calls and gpt_sheet.persisted(item) == saved
    assert "credential_ref" not in json.dumps(readback.json())


def generate_existing(item, state, before, *, prompt=PROMPT, style=None, route="local", core=False, expected_consistency=False):
    gpt_sheet.queue_images(state, "http", count=1)
    app = three_view.app_for(item)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        body = {
            "asset_id": "c", "asset_type": "character", "generation_type": "headshot",
            "apply_style": style is not None, "batch_size": 1,
        }
        if prompt is not None:
            body["prompt"] = prompt
        if style is not None:
            body["style_prompt"] = style
        accepted = client.post("/projects/p/assets/generate", json=body)
        assert accepted.status_code == 200, accepted.text
        task_id = accepted.json()["_task_id"]
        status = client.get("/tasks/" + task_id)
        assert status.status_code == 200, status.text
        assert status.json()["status"] == "completed", status.text
        base_prompt = prompt or DEFAULT_PROMPT
        effective = base_prompt if not style or style in base_prompt else f"{base_prompt}, {style}"
        assert_wire(state, before["reference"], effective, strict=core)
        target = assert_projection(item, route, before, base_prompt, effective, normalized=core)
        if core:
            assert_old_route_core_receipt(
                item, route, target, before, task_id, base_prompt, expected_consistency, client, state,
            )
        else:
            assert not saved_owner(item, route).asset_image_requests
            assert "_core_image_request_id" not in item.asset_generation_tasks[task_id]
        return target


@pytest.mark.parametrize("source", SOURCES)
def test_existing_headshot_reference_priority_and_avatar_dual_history(runtime, transport, source):
    before = setup_headshot(runtime, source=source)
    generate_existing(runtime, transport, before, core=True)


@pytest.mark.parametrize("prompt", [None, PROMPT, PROMPT + ", " + STYLE],
                         ids=["default-template", "authored-with-style", "style-already-authored"])
def test_existing_headshot_preserves_authored_prompt_and_one_style_suffix(runtime, transport, prompt):
    before = setup_headshot(runtime)
    target = generate_existing(runtime, transport, before, prompt=prompt, style=STYLE, core=True)
    assert target.headshot_asset is not None
    selected = next(v for v in target.headshot_asset.variants if v.id == target.headshot_asset.selected_id)
    assert selected.prompt_used is not None and selected.prompt_used.count(STYLE) == 1


@pytest.mark.parametrize("three_view_current", [True, False], ids=["views-current", "views-stale"])
def test_local_fallback_headshot_preserves_consistency_without_a_timestamp_only_claim(runtime, transport, three_view_current):
    # Every migrated local GPT source preserves the existing consistency decision;
    # derived timestamps alone do not prove the neighboring batch or selected source.
    before = setup_headshot(runtime, source="legacy-url", three_view_current=three_view_current)
    target = generate_existing(runtime, transport, before, core=True)
    assert target.headshot_updated_at >= target.full_body_updated_at == 2
    assert target.three_view_updated_at == (3 if three_view_current else 1)
    assert target.is_consistent is False


@pytest.mark.parametrize("route,model", [
    pytest.param("local", three_view.MODELS[0], id="local-short-alias"),
    pytest.param("series", three_view.MODELS[1], id="series-full-alias"),
    pytest.param("global", three_view.MODELS[0], id="global-short-alias"),
])
def test_strict_headshot_http_uses_saved_i2i_selected_c_and_original_owner(runtime, transport, route, model):
    before = setup_headshot(runtime, route, model)
    gpt_sheet.queue_images(transport, "http", count=1)
    app = three_view.app_for(runtime)
    body = {
        "client_request_id": "headshot-first-request", "asset_id": "c", "asset_type": "character",
        "generation_type": "headshot", "reference_variant_id": three_view.SELECTED_ID,
        "reference_image_url": three_view.SELECTED_REF, "apply_style": False,
        "prompt": PROMPT, "batch_size": 1,
    }
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        accepted = client.post(three_view.PUBLIC, json=body)
        # Strict admission must retain the original request and owner throughout execution.
        assert accepted.status_code == 200, accepted.text
        assert [member["status"] for member in accepted.json()["request"]["members"]] == ["pending"]
        assert_wire(transport, before["reference"], PROMPT, strict=True)
        target = assert_projection(runtime, route, before, PROMPT, PROMPT, normalized=True)
        owner = saved_owner(runtime, route)
        assert len(owner.asset_image_requests) == 1
        record = owner.asset_image_requests[0]
        assert (record.entrypoint, record.asset_type, record.channel) == (
            "studio.asset.headshot.v1", "character", "headshot",
        )
        assert (record.request_root_kind, record.request_root_id) == ("project", "p")
        assert (record.owner_kind, record.owner_id) == {
            "local": ("script", "p"), "series": ("series", "s"), "global": ("global", "library"),
        }[route]
        assert record.id == accepted.json()["request"]["id"]
        assert getattr(target, "headshot_request_id", None) == record.id
        assert record.base_prompt == PROMPT and len(record.members) == 1
        member = record.members[0]
        execution = member.execution
        assert execution.phase == "completed" and execution.provider_model_id == "gpt-image-2"
        assert execution.intent["intent_contract_version"] == three_view.CONTRACT
        assert execution.intent["model_id"] == three_view.MODELS[1] and execution.intent["mode"] == "i2i"
        assert execution.intent["params"] == {"size": "1024x1024"}
        assert len(execution.media) == 1
        media = execution.media[0]
        assert media["role"] == "reference_image" and media["ordinal"] == 0
        assert media["sha256"] == hashlib.sha256(before["reference"]).hexdigest()
        assert media["size_bytes"] == len(before["reference"]) and media["declared_media_type"] == "image/png"
        assert execution.artifact is not None and execution.output_ref is not None
        assert hashlib.sha256((runtime.data_dir / execution.output_ref).read_bytes()).hexdigest() == execution.artifact["sha256"]
        assert target.headshot_asset is not None and target.head_shot is not None
        assert target.headshot_asset.selected_id == target.head_shot.selected_image_id == member.variant_id
        assert target.headshot_image_url == target.avatar_url == execution.output_ref
        assert target.head_shot.image_prompt == target.headshot_prompt == PROMPT
        # Legacy timestamps do not prove the selected three-view batch is complete.
        # Keep the existing False without changing neighboring receipt guards.
        assert target.is_consistent is False
        assert len(runtime._load_data()["p"].asset_image_requests) == (route == "local")
        assert len(runtime._load_series_data()["s"].asset_image_requests) == (route == "series")
        assert len(runtime._load_library_data().asset_image_requests) == (route == "global")
        saved = gpt_sheet.persisted(runtime)
        calls = len(transport["calls"])
        readback = client.get(three_view.PUBLIC + "/" + body["client_request_id"])
        replay = client.post(three_view.PUBLIC, json=body)
        assert readback.status_code == replay.status_code == 200
        assert readback.json()["reason"] is None and replay.json()["replayed"] is True
        assert [member["status"] for member in readback.json()["request"]["members"]] == ["completed"]
        assert replay.json()["request"] == readback.json()["request"]
        assert len(transport["calls"]) == calls and gpt_sheet.persisted(runtime) == saved
        assert "credential_ref" not in json.dumps(readback.json())


@pytest.mark.parametrize("route,model,consistent", [
    pytest.param("series", three_view.MODELS[1], True, id="series-full-alias-prior-true"),
    pytest.param("global", three_view.MODELS[0], False, id="global-short-alias-prior-false"),
])
def test_existing_headshot_uses_core_at_the_original_shared_owner(runtime, transport, route, model, consistent):
    before = setup_headshot(runtime, route, model)
    admission.owner(runtime, route).characters[0].is_consistent = consistent
    boundary.commit_owners(runtime)
    generate_existing(runtime, transport, before, route=route, core=True, expected_consistency=consistent)


@pytest.mark.parametrize("case,extra,status,reason", [
    pytest.param("missing", {}, 422, "studio_headshot_selected_reference_missing", id="dangling-selected"),
    pytest.param("duplicate-same", {}, 422, "studio_headshot_selected_reference_ambiguous", id="duplicate-same-url"),
    pytest.param("duplicate-different", {}, 422, "studio_headshot_selected_reference_ambiguous", id="duplicate-different-url"),
    pytest.param("command", {"model_name": "wan2.7-image"}, 422,
                 "Headshot model must match the saved I2I model", id="explicit-other-model"),
    pytest.param("command", {"reference_image_url": "uploads/legacy-a.png"}, 422,
                 "Headshot reference must match the selected full-body image", id="explicit-other-reference"),
    pytest.param("command", {"negative_prompt": "blurred face"}, 404,
                 "studio_gpt_image_negative_prompt_not_supported", id="core-negative-rejection"),
    pytest.param("command", {"aspect_ratio": "9:16"}, 404,
                 "studio_headshot_parameter_not_supported", id="core-aspect-rejection"),
])
def test_existing_headshot_rejection_never_falls_back_to_paid_legacy(runtime, transport, case, extra, status, reason):
    setup_headshot(runtime)
    target = admission.owner(runtime, "local").characters[0]
    assert target.full_body_asset is not None and target.full_body is not None
    if case == "missing":
        target.full_body_asset.selected_id = target.full_body.selected_image_id = "missing-choice"
    elif case.startswith("duplicate"):
        reference = next(value for value in target.full_body_asset.variants if value.id == three_view.SELECTED_ID)
        duplicate = reference.model_copy(deep=True)
        if case == "duplicate-different":
            duplicate.url = "uploads/first-b.png"
        target.full_body_asset.variants.append(duplicate)
    boundary.commit_owners(runtime)
    # A valid paid result is ready; refusal must leave it entirely unconsumed.
    gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(three_view.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, "http")
        saved, frozen = gpt_sheet.persisted(runtime), boundary.saved_files(runtime)
        pending = list(transport["queue"])
        response = client.post("/projects/p/assets/generate", json={
            "asset_id": "c", "asset_type": "character", "generation_type": "headshot",
            "prompt": PROMPT, "apply_style": False, "batch_size": 1, **extra,
        })
        assert response.status_code == status, response.text
        assert response.json()["detail"] == reason
        assert gpt_sheet.persisted(runtime) == saved and boundary.saved_files(runtime) == frozen
        assert not transport["calls"] and not transport["cli"] and transport["queue"] == pending
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})
        assert not saved_owner(runtime, "local").asset_image_requests


@pytest.mark.parametrize("model,reference", [
    pytest.param(three_view.MODELS[0], "https://example.invalid/selected-c.png", id="unique-remote-gpt"),
    pytest.param("wan2.7-image", three_view.SELECTED_REF, id="other-saved-i2i-with-gpt-t2i"),
])
def test_existing_headshot_unmigrated_inputs_keep_legacy_admission(runtime, transport, model, reference):
    setup_headshot(runtime, model=model)
    target = admission.owner(runtime, "local").characters[0]
    assert target.full_body_asset is not None and target.full_body is not None
    for variants in (target.full_body_asset.variants, target.full_body.image_variants):
        selected = next(value for value in variants if value.id == three_view.SELECTED_ID)
        selected.url = reference
    if model not in three_view.MODELS:
        runtime.scripts["p"].model_settings.t2i_model = three_view.MODELS[1]
    boundary.commit_owners(runtime)
    # Real enqueue only: remote adapter/download behavior is outside this claim.
    _, task_id = runtime.create_asset_generation_task(
        "p", "c", "character", generation_type="headshot", prompt=PROMPT, apply_style=False,
    )
    assert list(runtime.asset_generation_tasks) == [task_id]
    task = runtime.asset_generation_tasks[task_id]
    assert task["status"] == "pending" and "_core_image_request_id" not in task
    context = task["_context"]
    assert context["owner"] == ("script", "p") and context["key"] == ("script", "p", "character", "c")
    assert context["root_identity"] is runtime.scripts["p"] and context["target_identity"] is target
    assert context["options"]["i2i_model_name"] == model
    assert runtime._asset_image_runs[context["key"]] is task["_token"]
    saved = saved_owner(runtime, "local")
    assert not saved.asset_image_requests and saved.characters[0].headshot_request_id is None
    saved_full_body = saved.characters[0].full_body_asset
    assert saved_full_body is not None
    assert next(value.url for value in saved_full_body.variants if value.id == saved_full_body.selected_id) == reference
    assert not transport["calls"] and not transport["cli"]
