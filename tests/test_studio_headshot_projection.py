"""Protect existing headshot choices across unequal image containers.

Reuses the real H1 fixture and normalized-pixel proof helper. App, admission, storage, Core worker
and image proof remain real. Only the inherited external transport is replaced;
its second paid POST observes the already committed partial projection.
"""

import base64
import hashlib
import json

import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import ImageAsset, ImageVariant
from tests import test_studio_headshot_core as headshot
from tests import test_studio_three_view_boundaries as boundaries


runtime = headshot.runtime
transport = headshot.transport
first = headshot.three_view
gpt_sheet = headshot.gpt_sheet
MODERN_ID = "headshot-u"
MODERN_EXTRA_ID = "headshot-m"
LEGACY_ID = "headshot-l"
MODERN_REF = "uploads/headshot-u.png"


def seed_unaligned_history(item, legacy_shape, *, consistent=False):
    before = headshot.setup_headshot(item)
    target = headshot.admission.owner(item, "local").characters[0]
    assert target.head_shot is not None
    old = [ImageVariant.model_validate({
        "id": MODERN_ID, "url": MODERN_REF, "created_at": 10,
        "prompt_used": "The user's selected portrait", "is_favorited": True,
    })]
    if legacy_shape == "disjoint":
        old.extend([
            ImageVariant.model_validate({
                "id": MODERN_EXTRA_ID, "url": "uploads/headshot-m.png", "created_at": 11,
                "prompt_used": "Modern history", "is_uploaded_source": True, "upload_type": "head_shot",
            }),
            ImageVariant.model_validate({
                "id": LEGACY_ID, "url": "uploads/headshot-l.png", "created_at": 12,
                "prompt_used": "Legacy history", "is_favorited": True,
                "is_uploaded_source": True, "upload_type": "head_shot",
            }),
        ])
    for value in old:
        (item.data_dir / value.url).write_bytes(headshot.rgb_png_bytes(value.id, width=16, height=16))
    target.head_shot.image_variants = [value.model_copy(deep=True) for value in old if value.id != LEGACY_ID]
    target.head_shot.selected_image_id = MODERN_ID
    # Establish a valid pre-request motion association, then protect it verbatim.
    target.head_shot.video_variants[0].source_image_id = MODERN_ID
    if legacy_shape == "absent":
        target.headshot_asset = None
    elif legacy_shape == "empty":
        target.headshot_asset = ImageAsset.model_validate({})
    else:
        assert legacy_shape == "disjoint"
        target.headshot_asset = ImageAsset(
            selected_id=LEGACY_ID,
            variants=[value.model_copy(deep=True) for value in old if value.id == LEGACY_ID],
        )
    target.headshot_image_url = target.avatar_url = MODERN_REF
    target.is_consistent = consistent
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    before.update({
        "old_faces": {value.id: value.model_dump(mode="json") for value in old},
        "old_files": {value.url: (item.data_dir / value.url).read_bytes() for value in old},
        "motion": target.head_shot.model_dump(mode="json", include=headshot.MOTION_FIELDS),
        "consistent": consistent,
        "image_updated_at": target.head_shot.image_updated_at,
    })
    assert headshot.saved_owner(item, "local").characters[0].model_dump() == target.model_dump()
    return before


def exercise_headshot(item, state, before, *, batch_size, unknown=False):
    partial = []

    def observe_second_post(request):
        if request.method == "POST" and len(boundaries.paid_posts(state)) == 2:
            # Read the disk snapshot; do not mutate or assert inside transport.
            partial.append(headshot.saved_owner(item, "local"))

    state["on_send"] = observe_second_post
    gpt_sheet.queue_images(state, "http", count=batch_size - int(unknown))
    if unknown:
        state["queue"].append(requests.ConnectionError("synthetic second response lost after submit"))
    raw = {
        "client_request_id": "headshot-projection-request", "asset_id": "c", "asset_type": "character",
        "generation_type": "headshot", "reference_variant_id": first.SELECTED_ID,
        "reference_image_url": first.SELECTED_REF, "apply_style": False,
        "prompt": headshot.PROMPT, "batch_size": batch_size,
    }
    app = first.app_for(item)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        assert [value["status"] for value in accepted.json()["request"]["members"]] == ["pending"] * batch_size
        owner = headshot.saved_owner(item, "local")
        assert owner.model_dump() == headshot.admission.owner(item, "local").model_dump()
        assert len(owner.asset_image_requests) == 1
        record = owner.asset_image_requests[0]
        assert record.id == accepted.json()["request"]["id"]
        assert (record.entrypoint, record.asset_type, record.channel) == (
            "studio.asset.headshot.v1", "character", "headshot",
        )
        assert (record.request_root_kind, record.request_root_id, record.owner_kind, record.owner_id) == (
            "project", "p", "script", "p",
        )
        assert record.base_prompt == headshot.PROMPT
        expected_phases = ["completed"] * (batch_size - int(unknown)) + (["submitting"] if unknown else [])
        assert [member.execution.phase for member in record.members] == expected_phases
        if unknown:
            unfinished = record.members[-1].execution
            assert unfinished.prepared_artifact is None and unfinished.artifact is None
            assert not (item.data_dir / unfinished.output_ref).exists()
        expected_wire = []
        for index in range(batch_size):
            expected_wire.append(("POST", first.EDIT))
            if not unknown or index < batch_size - 1:
                expected_wire.extend([("GET", first.EDIT + "/" + gpt_sheet.http_task_id(index)), ("GET", gpt_sheet.IMAGE_URL)])
        assert [(request.method, request.url) for request, _ in state["calls"]] == expected_wire
        encoded = "data:image/png;base64," + base64.b64encode(before["reference"]).decode()
        posts = boundaries.paid_posts(state)
        assert len(posts) == batch_size
        assert [json.loads(request.body) for request in posts] == [{
            "prompt": headshot.PROMPT, "size": "1024x1024", "n": 1, "images": [encoded],
        }] * batch_size
        for member in record.members:
            execution = member.execution
            assert execution.provider_model_id == "gpt-image-2"
            assert execution.intent["intent_contract_version"] == first.CONTRACT
            assert execution.intent["model_id"] == first.MODELS[1] and execution.intent["mode"] == "i2i"
            assert execution.intent["params"] == {"size": "1024x1024"}
            assert len(execution.media) == 1
            media = execution.media[0]
            assert (media["role"], media["ordinal"], media["declared_media_type"]) == ("reference_image", 0, "image/png")
            assert media["sha256"] == hashlib.sha256(before["reference"]).hexdigest()
            assert media["size_bytes"] == len(before["reference"])
        persisted = gpt_sheet.persisted(item)
        call_count = len(state["calls"])
        observed = client.get(first.PUBLIC + "/" + raw["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw)
        assert observed.status_code == replay.status_code == 200
        expected_statuses = ["completed"] * (batch_size - int(unknown)) + (["needs_confirmation"] if unknown else [])
        assert [member["status"] for member in observed.json()["request"]["members"]] == expected_statuses
        assert replay.json()["replayed"] is True
        assert replay.json()["request"] == observed.json()["request"]
        if not unknown:
            assert observed.json()["reason"] is None
        assert len(state["calls"]) == call_count and gpt_sheet.persisted(item) == persisted
        assert "credential_ref" not in json.dumps(observed.json())
        assert not state["queue"] and not state["cli_queue"] and not state["cli"]
    assert len(partial) == (1 if batch_size == 2 else 0)
    return partial, owner


def assert_projection(item, owner, before, selected_id):
    target = owner.characters[0]
    assert len(owner.asset_image_requests) == 1
    record = owner.asset_image_requests[0]
    assert target.head_shot is not None and target.headshot_asset is not None
    assert target.head_shot.selected_image_id == target.headshot_asset.selected_id == selected_id
    completed = [member for member in record.members if member.execution.phase == "completed"]
    expected_ids = set(before["old_faces"]) | {member.variant_id for member in completed}
    modern = {value.id: value.model_dump(mode="json") for value in target.head_shot.image_variants}
    legacy = {value.id: value.model_dump(mode="json") for value in target.headshot_asset.variants}
    assert len(target.head_shot.image_variants) == len(target.headshot_asset.variants) == len(expected_ids)
    assert set(modern) == set(legacy) == expected_ids and modern == legacy
    assert {key: modern[key] for key in before["old_faces"]} == before["old_faces"]
    assert target.headshot_image_url == target.avatar_url == modern[selected_id]["url"]
    assert target.headshot_prompt == target.head_shot.image_prompt == headshot.PROMPT
    assert target.headshot_updated_at == record.created_at
    assert getattr(target, "headshot_request_id", None) == record.id
    assert target.model_dump(mode="json", include=headshot.PROTECTED_FIELDS) == before["protected"]
    assert target.head_shot.model_dump(mode="json", include=headshot.MOTION_FIELDS) == before["motion"]
    # A partial timestamp does not prove the selected derived images agree.
    assert target.is_consistent is before["consistent"]
    if selected_id == MODERN_ID:
        assert target.avatar_url == MODERN_REF
        assert target.head_shot.image_updated_at == before["image_updated_at"]
    assert {ref: (item.data_dir / ref).read_bytes() for ref in before["old_files"]} == before["old_files"]
    for member in completed:
        assert member.execution.artifact == member.execution.prepared_artifact
        boundaries.prove_paid_file(item, member.execution)
        headshot.assert_provider_image_pixels(item.data_dir / member.execution.output_ref)
        assert modern[member.variant_id]["url"] == member.execution.output_ref
        assert modern[member.variant_id]["prompt_used"] == headshot.PROMPT
    return target, record


def test_partial_unknown_preserves_modern_selection_with_absent_legacy(runtime, transport):
    before = seed_unaligned_history(runtime, "absent")
    partial, final = exercise_headshot(runtime, transport, before, batch_size=2, unknown=True)
    first_target, first_record = assert_projection(runtime, partial[0], before, MODERN_ID)
    assert first_target.status == "processing"
    assert [member.execution.phase for member in first_record.members] == ["completed", "submitting"]
    target, record = assert_projection(runtime, final, before, MODERN_ID)
    assert target.status == "needs_confirmation"
    assert record.members[0].model_dump() == first_record.members[0].model_dump()


def test_completed_batch_preserves_modern_selection_with_empty_legacy(runtime, transport):
    before = seed_unaligned_history(runtime, "empty")
    partial, final = exercise_headshot(runtime, transport, before, batch_size=2)
    first_target, first_record = assert_projection(runtime, partial[0], before, MODERN_ID)
    assert first_target.status == "processing"
    assert [member.execution.phase for member in first_record.members] == ["completed", "submitting"]
    target, record = assert_projection(runtime, final, before, MODERN_ID)
    assert target.status == "completed"
    assert record.members[0].model_dump() == first_record.members[0].model_dump()


def test_single_completion_preserves_both_old_histories_and_motion(runtime, transport):
    before = seed_unaligned_history(runtime, "disjoint")
    _, final = exercise_headshot(runtime, transport, before, batch_size=1)
    generated_id = final.asset_image_requests[0].members[0].variant_id
    assert generated_id not in before["old_faces"]
    target, _ = assert_projection(runtime, final, before, generated_id)
    assert target.status == "completed"


def test_batch_prefers_modern_selection_when_legacy_disagrees(runtime, transport):
    before = seed_unaligned_history(runtime, "disjoint", consistent=True)
    partial, final = exercise_headshot(runtime, transport, before, batch_size=2)
    first_target, first_record = assert_projection(runtime, partial[0], before, MODERN_ID)
    assert first_target.status == "processing"
    assert [member.execution.phase for member in first_record.members] == ["completed", "submitting"]
    target, record = assert_projection(runtime, final, before, MODERN_ID)
    assert target.status == "completed"
    assert record.members[0].model_dump() == first_record.members[0].model_dump()
