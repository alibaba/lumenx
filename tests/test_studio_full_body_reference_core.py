"""Real HTTP full-body reverse references use the bound GPT Edit Core.

Only the inherited external GPT transport is replaced. Pipeline, admission,
worker, Core, factory, image normalization, storage and public wire stay real.
Full-body Edit requests retain their explicit v2 contract and original owner.
"""

import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import ImageVariant
from src.utils.media_probe import probe_image_file
from tests import test_studio_core_image_admission as admission
from tests import test_studio_gpt_reference_sheet_core as gpt_sheet
from tests import test_studio_headshot_core as headshot
from tests import test_studio_three_view_boundaries as boundary
from tests import test_studio_three_view_core as three_view


runtime = three_view.runtime
transport = gpt_sheet.transport
CONTRACT = "provider-gpt-image2-edit-bound-v2"
ENTRYPOINT = "studio.asset.full_body.v1"
OLD_ROUTE = "/projects/p/assets/generate"
GENERATION = "https://api.mulerouter.ai/vendors/openai/v1/gpt-image-2/generation"
PROMPT = "Alice standing at ease, full body, keep the red coat and leather boots."
STYLE = "silver rim lighting"
REVERSE = (
    "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, skin tone, "
    "and clothing as the reference image. "
)
DEFAULT_PROMPT = (
    "Full body character design of Alice, concept art. red coat. Standing pose, "
    "neutral expression, no emotion, looking at viewer. Clean white background, "
    "isolated, no other objects, no scenery, simple background, high quality, masterpiece."
)
PROTECTED = {
    "id", "name", "description", "base_character_id", "reference_sheet",
    "reference_sheet_request_id", "image_url", "three_views", "three_view_asset",
    "three_view_image_url", "three_view_prompt", "three_view_updated_at", "three_view_request_id",
    "head_shot", "headshot_asset", "headshot_image_url", "headshot_prompt",
    "headshot_updated_at", "headshot_request_id", "avatar_url", "voice_id",
    "full_body_motion_request_id",
}


def setup_reference(item, route="local", model=three_view.MODELS[0], *, source="three_view",
                    saved_ratio="9:16", unselected_source=False, many_history=False):
    headshot.setup_headshot(
        item, route, model,
        source="uploaded-three-view" if source == "three_view" else "uploaded-headshot",
    )
    target = admission.owner(item, route).characters[0]
    assert target.full_body_asset is not None and target.full_body is not None
    assert target.three_view_asset is not None and target.three_views is not None
    # Existing uploaded full-body C is deliberately not a reverse input.
    target.full_body_asset.selected_id = target.full_body.selected_image_id = three_view.SELECTED_ID
    target.full_body_image_url = three_view.SELECTED_REF
    item.scripts["p"].model_settings.character_aspect_ratio = saved_ratio
    # First ordinary image is also not a reverse input. The first *uploaded*
    # three-view must win over both the selected second view and uploaded faces.
    ordinary = ImageVariant.model_validate(dict(id="three-ordinary", url="uploads/legacy-a.png", created_at=0))
    target.three_view_asset.variants.insert(0, ordinary.model_copy(deep=True))
    target.three_views.image_variants.insert(0, ordinary.model_copy(deep=True))
    if unselected_source:
        target.three_view_asset.selected_id = target.three_views.selected_image_id = None
    # Each representation owns an old entry absent from the other; reconciliation
    # must preserve their union without rewriting prompt/favorite metadata.
    target.full_body_asset.variants.append(ImageVariant.model_validate(dict(
        id="body-legacy-only", url="uploads/legacy-a.png", created_at=3,
        prompt_used="Legacy body prompt without reverse prefix", is_favorited=True,
    )))
    target.full_body.image_variants.append(ImageVariant.model_validate(dict(
        id="body-modern-only", url="uploads/first-b.png", created_at=4,
        prompt_used="Modern body prompt to retain", is_favorited=True,
    )))
    if many_history:
        for index in range(11):
            variant = ImageVariant.model_validate(dict(
                id=f"body-history-{index}", url="uploads/legacy-a.png", created_at=index + 5,
                prompt_used=f"Historical body prompt {index}",
            ))
            target.full_body_asset.variants.append(variant.model_copy(deep=True))
            target.full_body.image_variants.append(variant.model_copy(deep=True))
    reference_id, reference_url = (
        ("three-first", "uploads/three-upload-first.png") if source == "three_view"
        else ("face-first", "uploads/face-upload-first.png")
    )
    old = {}
    for variants in (target.full_body_asset.variants, target.full_body.image_variants):
        for variant in variants:
            value = variant.model_dump(mode="json")
            assert variant.id not in old or old[variant.id] == value
            old[variant.id] = value
    boundary.commit_owners(item)
    reference = (item.data_dir / reference_url).read_bytes()
    assert reference != (item.data_dir / three_view.SELECTED_REF).read_bytes()
    return {
        "reference_id": reference_id, "reference_url": reference_url, "reference": reference,
        "old": old, "selected": three_view.SELECTED_ID,
        "protected": target.model_dump(mode="json", include=PROTECTED),
        "motion": target.full_body.model_dump(mode="json", include=headshot.MOTION_FIELDS),
    }


def strict_body(before, **changes):
    return {
        "client_request_id": "full-body-reference-request", "asset_id": "c", "asset_type": "character",
        "generation_type": "full_body", "reference_variant_id": before["reference_id"],
        "reference_image_url": before["reference_url"], "prompt": PROMPT,
        "apply_style": False, "batch_size": 1,
    } | changes


def assert_edit_wire(state, reference, effective, size, count):
    expected_calls = []
    for index in range(count):
        expected_calls.extend([
            ("POST", three_view.EDIT), ("GET", three_view.EDIT + "/" + gpt_sheet.http_task_id(index)),
            ("GET", gpt_sheet.IMAGE_URL),
        ])
    assert [(request.method, request.url) for request, _ in state["calls"]] == expected_calls
    encoded = "data:image/png;base64," + base64.b64encode(reference).decode()
    posts = [json.loads(request.body) for request, _ in state["calls"] if request.method == "POST"]
    assert posts == [{"prompt": effective, "size": size, "n": 1, "images": [encoded]}] * count
    assert effective.count(REVERSE.strip()) == 1
    assert not state["queue"] and not state["cli_queue"] and not state["cli"]


def assert_completed(item, route, before, *, base_prompt, effective, size, count):
    owner = headshot.saved_owner(item, route)
    assert len(owner.asset_image_requests) == 1
    record = owner.asset_image_requests[0]
    assert (record.entrypoint, record.asset_type, record.channel) == (ENTRYPOINT, "character", "full_body")
    assert (record.request_root_kind, record.request_root_id) == ("project", "p")
    assert (record.owner_kind, record.owner_id) == {
        "local": ("script", "p"), "series": ("series", "s"), "global": ("global", "library"),
    }[route]
    assert record.base_prompt == base_prompt and len(record.members) == count
    target = owner.characters[0]
    assert target.model_dump() == admission.owner(item, route).characters[0].model_dump()
    assert target.full_body_request_id == record.id and target.status.value == "completed"
    assert target.full_body_asset is not None and target.full_body is not None
    legacy = {v.id: v.model_dump(mode="json") for v in target.full_body_asset.variants}
    modern = {v.id: v.model_dump(mode="json") for v in target.full_body.image_variants}
    ids = {member.variant_id for member in record.members}
    assert len(legacy) == len(target.full_body_asset.variants)
    assert len(modern) == len(target.full_body.image_variants)
    assert set(legacy) == set(before["old"]) | ids and legacy == modern
    assert {key: legacy[key] for key in before["old"]} == before["old"]
    selected = record.members[0].variant_id if count == 1 else before["selected"]
    assert target.full_body_asset.selected_id == target.full_body.selected_image_id == selected
    assert target.full_body_image_url == legacy[selected]["url"]
    assert target.full_body_prompt == target.full_body.image_prompt == base_prompt
    assert target.full_body_updated_at == record.created_at and target.is_consistent is False
    assert target.model_dump(mode="json", include=PROTECTED) == before["protected"]
    assert target.full_body.model_dump(mode="json", include=headshot.MOTION_FIELDS) == before["motion"]
    for member in record.members:
        execution = member.execution
        assert execution.phase == "completed" and execution.provider_model_id == "gpt-image-2"
        assert execution.intent["intent_contract_version"] == CONTRACT
        assert execution.intent["model_id"] == three_view.MODELS[1] and execution.intent["mode"] == "i2i"
        assert execution.intent["prompt"] == effective and execution.intent["params"] == {"size": size}
        assert execution.media == record.members[0].execution.media and len(execution.media) == 1
        media = execution.media[0]
        assert (media["role"], media["ordinal"], media["declared_media_type"]) == ("reference_image", 0, "image/png")
        assert media["sha256"] == hashlib.sha256(before["reference"]).hexdigest()
        assert media["size_bytes"] == len(before["reference"])
        assert execution.output_ref is not None and execution.artifact is not None
        output = item.data_dir / execution.output_ref
        assert hashlib.sha256(output.read_bytes()).hexdigest() == execution.artifact["sha256"]
        assert legacy[member.variant_id]["url"] == execution.output_ref
        # New Core variants record the actual wire prompt. Existing historical
        # variants above retain their original prompt_used bytes, including legacy omissions.
        assert legacy[member.variant_id]["prompt_used"] == effective
        decoded = probe_image_file(output)
        assert (decoded.width, decoded.height) == (16, 16)
        headshot.assert_provider_image_pixels(output)
    for owner_route in ("local", "series", "global"):
        stored = headshot.saved_owner(item, owner_route)
        assert len(stored.asset_image_requests) == (owner_route == route)
        assert len(stored.characters) == (owner_route == route)
    return record


def assert_public_read_is_stable(client, item, state, record, *, replay=None):
    stored, calls = gpt_sheet.persisted(item), len(state["calls"])
    read = client.get(three_view.PUBLIC + "/" + record.client_request_id)
    assert read.status_code == 200, read.text
    wire = read.json()
    assert wire["reason"] is None and wire["request"]["id"] == record.id
    assert [m["status"] for m in wire["request"]["members"]] == ["completed"] * len(record.members)
    assert "credential_ref" not in json.dumps(wire)
    if replay is not None:
        repeated = client.post(three_view.PUBLIC, json=replay)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["replayed"] is True and repeated.json()["request"] == wire["request"]
    assert len(state["calls"]) == calls and gpt_sheet.persisted(item) == stored


@pytest.mark.parametrize("route,model,source,saved_ratio,requested_ratio,size,count,unselected,style", [
    pytest.param("local", three_view.MODELS[0], "three_view", "9:16", None, "1024x1536", 1, False, STYLE,
                 id="local-first-uploaded-view-default-portrait-short-id"),
    pytest.param("series", three_view.MODELS[1], "headshot", "9:16", None, "1024x1536", 2, False, None,
                 id="series-uploaded-headshot-batch2-large-history-full-id"),
    pytest.param("global", three_view.MODELS[0], "three_view", "9:16", "1:1", "1024x1024", 1, True, None,
                 id="global-no-selected-view-explicit-square"),
    pytest.param("local", three_view.MODELS[1], "three_view", "4:3", None, "1536x1024", 1, False, None,
                 id="local-saved-landscape-full-id"),
])
def test_strict_full_body_reference_uses_saved_i2i_owner_size_and_history(
    runtime, transport, route, model, source, saved_ratio, requested_ratio, size, count, unselected, style,
):
    before = setup_reference(runtime, route, model, source=source, saved_ratio=saved_ratio,
                             unselected_source=unselected, many_history=count > 1)
    body = strict_body(before, batch_size=count)
    if requested_ratio is not None:
        body["aspect_ratio"] = requested_ratio
    if style is not None:
        body.update(apply_style=True, style_prompt=style)
    effective = REVERSE + PROMPT + (f", {style}" if style else "")
    gpt_sheet.queue_images(transport, "http", count=count)
    app = three_view.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        result = client.post(three_view.PUBLIC, json=body)
        assert result.status_code == 200, result.text
        assert [m["status"] for m in result.json()["request"]["members"]] == ["pending"] * count
        assert_edit_wire(transport, before["reference"], effective, size, count)
        record = assert_completed(runtime, route, before, base_prompt=PROMPT, effective=effective, size=size, count=count)
        assert result.json()["request"]["id"] == record.id
        assert runtime.scripts["p"].model_settings.i2i_model == model
        assert runtime.scripts["p"].model_settings.t2i_model == "wan2.7-image"
        assert_public_read_is_stable(client, runtime, transport, record, replay=body)


@pytest.mark.parametrize("authored,style,base,effective", [
    pytest.param(None, None, DEFAULT_PROMPT, REVERSE + DEFAULT_PROMPT, id="absent-author-prompt-default-template"),
    pytest.param(REVERSE + PROMPT + ", " + STYLE, STYLE, REVERSE + PROMPT + ", " + STYLE,
                 REVERSE + PROMPT + ", " + STYLE, id="authored-prefix-and-style-not-duplicated"),
])
def test_full_body_reference_keeps_base_and_effective_prompt_separate(runtime, transport, authored, style, base, effective):
    before = setup_reference(runtime)
    body = strict_body(before)
    if authored is None:
        del body["prompt"]
    else:
        body["prompt"] = authored
    if style is not None:
        body.update(apply_style=True, style_prompt=style)
    gpt_sheet.queue_images(transport, "http", count=1)
    app = three_view.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        result = client.post(three_view.PUBLIC, json=body)
        assert result.status_code == 200, result.text
        assert_edit_wire(transport, before["reference"], effective, "1024x1536", 1)
        record = assert_completed(runtime, "local", before, base_prompt=base, effective=effective, size="1024x1536", count=1)
        assert_public_read_is_stable(client, runtime, transport, record, replay=body)


@pytest.mark.parametrize("route,model,source,model_override,ratio,size", [
    pytest.param("local", three_view.MODELS[0], "three_view", None, None, "1024x1536", id="local-omitted-model"),
    pytest.param("series", three_view.MODELS[1], "headshot", "wan2.7-image", None, "1024x1536",
                 id="series-old-frontend-t2i-placeholder"),
    pytest.param("global", three_view.MODELS[1], "three_view", three_view.MODELS[1], "16:9", "1536x1024",
                 id="global-explicit-saved-i2i-landscape"),
])
def test_legacy_full_body_http_enters_same_core_at_original_owner(
    runtime, transport, route, model, source, model_override, ratio, size,
):
    before = setup_reference(runtime, route, model, source=source)
    body = {"asset_id": "c", "asset_type": "character", "generation_type": "full_body",
            "prompt": PROMPT, "apply_style": False, "batch_size": 1}
    if model_override is not None:
        body["model_name"] = model_override
    if ratio is not None:
        body["aspect_ratio"] = ratio
    gpt_sheet.queue_images(transport, "http", count=1)
    app = three_view.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        result = client.post(OLD_ROUTE, json=body)
        assert result.status_code == 200, result.text
        task_id = result.json()["_task_id"]
        status = client.get("/tasks/" + task_id)
        assert status.status_code == 200 and status.json()["status"] == "completed", status.text
        assert_edit_wire(transport, before["reference"], REVERSE + PROMPT, size, 1)
        record = assert_completed(runtime, route, before, base_prompt=PROMPT, effective=REVERSE + PROMPT, size=size, count=1)
        assert record.id == task_id
        assert runtime.asset_generation_tasks[task_id]["_core_image_request_id"] == record.id
        assert "_context" not in runtime.asset_generation_tasks[task_id]
        assert_public_read_is_stable(client, runtime, transport, record)


@pytest.mark.parametrize("case,path,status", [
    pytest.param("selected-second", three_view.PUBLIC, 422, id="strict-selected-second-is-not-first-upload"),
    pytest.param("strict-t2i", three_view.PUBLIC, 422, id="strict-cannot-use-legacy-t2i-placeholder"),
    pytest.param("legacy-unrelated-model", OLD_ROUTE, 422, id="legacy-explicit-conflict-is-not-placeholder"),
    pytest.param("missing-priority-file", OLD_ROUTE, 404, id="missing-view-must-not-pay-for-headshot-fallback"),
])
def test_full_body_reference_invalid_command_never_falls_back_or_pays(runtime, transport, case, path, status):
    before = setup_reference(runtime)
    body = strict_body(before) if path == three_view.PUBLIC else {
        "asset_id": "c", "asset_type": "character", "generation_type": "full_body",
        "prompt": PROMPT, "apply_style": False, "batch_size": 1,
    }
    if case == "selected-second":
        body.update(reference_variant_id="three-second", reference_image_url="uploads/three-upload-second.png")
    elif case == "strict-t2i":
        body["model_name"] = "wan2.7-image"
    elif case == "legacy-unrelated-model":
        body["model_name"] = "qwen-image-2.0"
    else:
        (runtime.data_dir / before["reference_url"]).unlink()
        assert (runtime.data_dir / "uploads/face-upload-first.png").is_file()
    gpt_sheet.queue_images(transport, "http", count=1)
    app = three_view.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        stored, frozen = gpt_sheet.persisted(runtime), boundary.saved_files(runtime)
        queued = list(transport["queue"])
        result = client.post(path, json=body)
        assert result.status_code == status, result.text
        assert result.json()["detail"]
        assert gpt_sheet.persisted(runtime) == stored and boundary.saved_files(runtime) == frozen
        assert not transport["calls"] and not transport["cli"] and transport["queue"] == queued
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})
        assert not headshot.saved_owner(runtime, "local").asset_image_requests


def test_zero_reverse_reference_original_gpt_full_body_keeps_t2i_wire_and_receipt(runtime, transport):
    setup_reference(runtime)
    target = admission.owner(runtime, "local").characters[0]
    assert target.three_view_asset is not None and target.three_views is not None
    assert target.headshot_asset is not None and target.head_shot is not None
    assert target.full_body_asset is not None
    for variants in (target.three_view_asset.variants, target.three_views.image_variants,
                     target.headshot_asset.variants, target.head_shot.image_variants):
        for variant in variants:
            variant.is_uploaded_source = False
    assert target.base_character_id is None
    assert any(v.is_uploaded_source for v in target.full_body_asset.variants)
    runtime.scripts["p"].model_settings.t2i_model = three_view.MODELS[0]
    runtime.scripts["p"].model_settings.i2i_model = "wan2.7-image"
    boundary.commit_owners(runtime)
    gpt_sheet.queue_images(transport, "http", count=1)
    app = three_view.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        result = client.post(OLD_ROUTE, json={
            "asset_id": "c", "asset_type": "character", "generation_type": "full_body",
            "prompt": PROMPT, "apply_style": False, "batch_size": 1,
        })
        assert result.status_code == 200, result.text
        task_id = result.json()["_task_id"]
        status = client.get("/tasks/" + task_id)
        assert status.status_code == 200 and status.json()["status"] == "completed", status.text
        assert [(r.method, r.url) for r, _ in transport["calls"]] == [
            ("POST", GENERATION), ("GET", GENERATION + "/" + gpt_sheet.http_task_id()), ("GET", gpt_sheet.IMAGE_URL),
        ]
        assert json.loads(transport["calls"][0][0].body) == {
            "prompt": PROMPT, "size": "1024x1536", "quality": "high", "n": 1,
        }
        assert not transport["queue"] and not transport["cli"] and not transport["cli_queue"]
        saved = headshot.saved_owner(runtime, "local")
        assert len(saved.asset_image_requests) == 1
        record = saved.asset_image_requests[0]
        assert record.id == task_id == saved.characters[0].full_body_request_id
        assert runtime.asset_generation_tasks[task_id]["_core_image_request_id"] == record.id
        execution = record.members[0].execution
        assert execution.phase == "completed" and execution.intent["mode"] == "t2i"
        assert execution.media == [] and "intent_contract_version" not in execution.intent
        assert saved.characters[0].full_body_prompt == PROMPT
        reference = saved.characters[0].full_body_image_url
        assert reference is not None
        output = runtime.data_dir / reference
        assert execution.artifact is not None
        assert hashlib.sha256(output.read_bytes()).hexdigest() == execution.artifact["sha256"]
        headshot.assert_provider_image_pixels(output)
