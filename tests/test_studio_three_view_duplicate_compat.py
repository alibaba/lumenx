"""Private compatibility follow-up; not collected or applied to the repository.

Uses the real original receipt, then changes only the saved source selection.
The old route must reject with its existing CommandValidationError HTTP 422.
"""
import pytest
from fastapi.testclient import TestClient
from tests import test_studio_three_view_boundaries as boundary
from tests import test_studio_three_view_core as first

runtime = first.runtime
transport = first.transport


def add_duplicate(item, route, same_url):
    target = boundary.current_target(item, route)
    original = next(v for v in target.full_body_asset.variants if v.id == first.SELECTED_ID)
    duplicate = original.model_copy(deep=True)
    if not same_url:
        duplicate.url = "uploads/first-b.png"
    target.full_body_asset.variants.append(duplicate)
    boundary.commit_owners(item)


@pytest.mark.parametrize("same_url", [False, True])
def test_existing_generate_duplicate_source_cannot_fall_back_to_paid_legacy(runtime, transport, same_url):
    first.setup(runtime, "local", first.MODELS[1])
    add_duplicate(runtime, "local", same_url)
    first.gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        before, frozen = first.gpt_sheet.persisted(runtime), boundary.saved_files(runtime)
        raw = first.raw()
        raw.pop("client_request_id")
        raw.pop("reference_variant_id")
        result = client.post("/projects/p/assets/generate", json=raw)
        assert result.status_code == 422, result.text
        assert first.gpt_sheet.persisted(runtime) == before
        assert boundary.saved_files(runtime) == frozen
        assert not transport["calls"] and not transport["cli"]
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})


@pytest.mark.parametrize("same_url", [False, True])
def test_original_receipt_get_and_replay_survive_new_duplicate_source_without_writes(runtime, transport, same_url):
    selected, protected = first.setup(runtime, "global", first.MODELS[1])
    first.gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        raw = first.raw()
        result = client.post(first.PUBLIC, json=raw)
        assert result.status_code == 200, result.text
        first.assert_real_edit_wire(transport, selected)
        record = first.assert_completed(runtime, "global", selected, protected)
        original = record.model_dump(mode="json")
        original_images = boundary.image_fields(boundary.current_target(runtime, "global"))
        add_duplicate(runtime, "global", same_url)
        before, frozen = first.gpt_sheet.persisted(runtime), boundary.saved_files(runtime)
        readback = client.get(first.PUBLIC + "/" + raw["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw)
        assert readback.status_code == replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["request"] == readback.json()["request"]
        assert readback.json()["reason"] == "studio_image_request_inputs_changed"
        assert [m["status"] for m in readback.json()["request"]["members"]] == ["completed"]
        assert boundary.current_record(runtime, "global").model_dump(mode="json") == original
        assert boundary.image_fields(boundary.current_target(runtime, "global")) == original_images
        assert first.gpt_sheet.persisted(runtime) == before and boundary.saved_files(runtime) == frozen
        assert len(transport["calls"]) == 3 and len(boundary.paid_posts(transport)) == 1
