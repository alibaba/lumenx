"""Standalone series Edit: existing real app/Core and external transport fixture.

Close/reopen checks the application lifecycle. Owner storage, admission, Core,
image handling and recovery remain real; only external transport is replaced.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from tests import test_studio_derived_local_fallback_core as fallback
from tests import test_studio_full_body_reference_core as full_body
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_headshot_core as headshot
from tests import test_studio_image_known_handle_recovery as recovery
from tests import test_studio_image_recovery_dispatcher as dispatch
from tests import test_studio_three_view_core as edit


runtime = edit.runtime
transport = gpt.transport
PUBLIC = "/series/s/asset_image_requests"
CHANNELS = ("three_view", "headshot", "full_body")
HISTORY = {
    "three_view": ("three_view_asset", "three_views"),
    "headshot": ("headshot_asset", "head_shot"),
    "full_body": ("full_body_asset", "full_body"),
}


def setup_series(item, channel):
    if channel == "full_body":
        source = full_body.setup_reference(item, "standalone", edit.MODELS[1])
        raw = full_body.strict_body(source)
    elif channel == "headshot":
        _, reference = fallback.setup(item, channel, "other-upload", "standalone")
        raw = fallback.body(channel, reference, apply_style=False)
    else:
        headshot.setup_headshot(item, "standalone", edit.MODELS[1])
        raw = edit.raw()
    root = item.series_store["s"]
    root.model_settings.t2i_model = "wan2.7-image"
    root.model_settings.i2i_model = edit.MODELS[1]
    root.model_settings.character_aspect_ratio = "9:16"
    # An unrelated project's different I2I setting must not decide this request.
    item.scripts["p"].model_settings.i2i_model = "qwen-image-2.0"
    assert item.scripts["p"].series_id is None and root.episode_ids == []
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    reference = (item.data_dir / raw["reference_image_url"]).read_bytes()
    other_owners = {name: (item.data_dir / name).read_bytes()
                    for name in ("projects.json", "library_assets.json")}
    return raw, reference, other_owners


def series_request(item):
    root, _ = item.read_committed_series_view("s")
    assert root is not None and len(root.asset_image_requests) == 1
    return root, root.asset_image_requests[0]


def history(target, channel):
    images, unit = (getattr(target, field) for field in HISTORY[channel])
    assert images is not None and unit is not None
    return images, unit


def assert_other_owners_unchanged(item, before):
    assert {name: (item.data_dir / name).read_bytes() for name in before} == before
    assert not item.scripts["p"].asset_image_requests
    assert not item.library_store.asset_image_requests


def assert_completed(item, channel, reference, before, *, size):
    root, record = series_request(item)
    assert (record.request_root_kind, record.request_root_id, record.request_root_series_id) == ("series", "s", None)
    assert (record.owner_kind, record.owner_id, record.channel) == ("series", "s", channel)
    assert len(record.members) == 1
    execution = record.members[0].execution
    assert execution.phase == "completed" and execution.provider_model_id == "gpt-image-2"
    assert execution.intent["model_id"] == root.model_settings.i2i_model == edit.MODELS[1]
    assert root.model_settings.t2i_model == "wan2.7-image"
    assert execution.intent["params"] == {"size": size}
    assert execution.intent["intent_contract_version"] == (full_body.CONTRACT if channel == "full_body" else edit.CONTRACT)
    assert execution.media[0]["sha256"] == hashlib.sha256(reference).hexdigest()
    fallback.assert_bound_proof(item, execution)
    target = root.characters[0]
    images, unit = history(target, channel)
    assert images.selected_id == unit.selected_image_id == record.members[0].variant_id
    assert {v.id: v.model_dump(mode="json") for v in images.variants} == {
        v.id: v.model_dump(mode="json") for v in unit.image_variants
    }
    assert getattr(target, channel + "_image_url") == execution.output_ref
    assert getattr(target, channel + "_request_id") == record.id
    assert_other_owners_unchanged(item, before)
    return record


@pytest.mark.parametrize("channel", CHANNELS)
def test_series_strict_edit_uses_own_model_owner_and_original_key(runtime, transport, channel):
    raw, reference, before = setup_series(runtime, channel)
    gpt.queue_images(transport, "http", count=1)
    with TestClient(edit.app_for(runtime)) as client:
        gpt.configure(client.app, "http")
        accepted = client.post(PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        size = "1024x1536" if channel == "full_body" else "1024x1024"
        record = assert_completed(runtime, channel, reference, before, size=size)
        if channel == "full_body":
            full_body.assert_edit_wire(transport, reference, full_body.REVERSE + raw["prompt"], size, 1)
        else:
            headshot.assert_wire(transport, reference, raw["prompt"], strict=True)
        persisted = gpt.persisted(runtime)
        read = client.get(PUBLIC + "/" + raw["client_request_id"])
        replay = client.post(PUBLIC, json=raw)
        assert read.status_code == replay.status_code == 200
        assert replay.json()["replayed"] is True and read.json()["reason"] is None
        assert replay.json()["request"] == read.json()["request"]
        assert read.json()["request"]["id"] == record.id
        assert gpt.persisted(runtime) == persisted and len(transport["calls"]) == 3


@pytest.mark.parametrize("channel", CHANNELS)
def test_series_legacy_entry_enters_same_core_and_full_body_keeps_requested_ratio(runtime, transport, channel):
    raw, reference, before = setup_series(runtime, channel)
    body = {key: value for key, value in raw.items()
            if key not in {"client_request_id", "reference_variant_id", "reference_source"}}
    size = "1024x1024"
    if channel == "full_body":
        body["aspect_ratio"] = "16:9"
        body["model_name"] = "wan2.7-image"  # Old UI's T2I default remains tolerated.
        size = "1536x1024"
    gpt.queue_images(transport, "http", count=1)
    with TestClient(edit.app_for(runtime)) as client:
        gpt.configure(client.app, "http")
        accepted = client.post("/series/s/assets/generate", json=body)
        assert accepted.status_code == 200, accepted.text
        record = assert_completed(runtime, channel, reference, before, size=size)
        assert accepted.json()["_task_id"] == record.id
        task = client.get("/tasks/" + record.id)
        assert task.status_code == 200 and task.json()["status"] == "completed"
        assert json.loads(transport["calls"][0][0].body)["size"] == size
        assert transport["calls"][0][0].url == edit.EDIT
        assert len(transport["calls"]) == 3 and not transport["queue"]


def test_series_edit_reopens_original_handle_without_repaying_or_selecting(runtime, transport):
    raw, _, before = setup_series(runtime, "three_view")
    target = runtime.series_store["s"].characters[0]
    images, unit = history(target, "three_view")
    selected = images.selected_id, unit.selected_image_id, target.three_view_image_url, unit.image_prompt
    transport["queue"].extend([recovery.receipt("edit"), requests.ConnectionError("synthetic query unavailable")])
    with TestClient(edit.app_for(runtime)) as client:
        gpt.configure(client.app, "http")
        accepted = client.post(PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        _, original = series_request(runtime)
        execution = original.members[0].execution
        assert execution.phase == "submitted" and execution.handle is not None
        saved_handle, fingerprint = deepcopy(execution.handle), execution.fingerprint
    with recovery.reopened(runtime) as other, TestClient(edit.app_for(other)) as client:
        gpt.configure(client.app, "http")
        worker = dispatch.dispatcher(other)
        assert other.asset_image_service.run_original_request(original.id) is None
        transport["queue"].extend([recovery.observation("edit"), recovery.response(recovery.PNG)])
        assert worker.run_once() == ("image_queried", original.id, execution.attempt_id)
        assert worker.run_once() == ("image_fetched", original.id, execution.attempt_id)
        assert worker.run_once() is None
        root, finished = series_request(other)
        result = finished.members[0].execution
        assert result.phase == "completed" and result.handle == saved_handle and result.fingerprint == fingerprint
        recovery.assert_png(other, result.model_dump(mode="json"))
        target = root.characters[0]
        images, unit = history(target, "three_view")
        assert (images.selected_id, unit.selected_image_id, target.three_view_image_url, unit.image_prompt) == selected
        assert result.attempt_id in {variant.id for variant in images.variants}
        persisted = gpt.persisted(other)
        replay = client.post(PUBLIC, json=raw)
        read = client.get(PUBLIC + "/" + raw["client_request_id"])
        assert replay.status_code == read.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["request"] == read.json()["request"] and gpt.persisted(other) == persisted
        assert_other_owners_unchanged(other, before)
    assert recovery.counts(transport) == (1, 2, 1) and not transport["queue"]


def test_series_paid_full_body_result_preserves_later_author_selection(runtime, transport):
    raw, _, before = setup_series(runtime, "full_body")
    captured: dict[str, Any] = {}
    gpt.queue_images(transport, "http", count=1)

    def select_existing_body(request):
        if request.method != "GET" or request.url != gpt.IMAGE_URL:
            return
        transport["on_send"] = None
        with runtime._save_lock:
            target = runtime.series_store["s"].characters[0]
            images, unit = history(target, "full_body")
            runtime._set_selected_image(images, "first-b")
            runtime._set_selected_image(unit, "first-b")
            runtime._sync_variant_urls(target, "character", "full_body")
            runtime._save_series_data_unlocked()
            captured["target"] = target.model_dump(mode="json")

    transport["on_send"] = select_existing_body
    with TestClient(edit.app_for(runtime)) as client:
        gpt.configure(client.app, "http")
        accepted = client.post(PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        root, record = series_request(runtime)
        execution = record.members[0].execution
        assert captured and root.characters[0].model_dump(mode="json") == captured["target"]
        assert execution.phase == "observed" and execution.artifact is None
        fallback.assert_bound_proof(runtime, execution)
        assert record.members[0].variant_id not in {v.id for v in history(root.characters[0], "full_body")[0].variants}
        persisted = gpt.persisted(runtime)
        replay = client.post(PUBLIC, json=raw)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["request"]["members"][0]["status"] == "needs_confirmation"
        runtime.asset_image_service.repair_request("series", "s", raw["client_request_id"])
        assert gpt.persisted(runtime) == persisted
        assert_other_owners_unchanged(runtime, before)
    assert len(transport["calls"]) == 3 and not transport["queue"]


def test_series_sync_edit_retains_own_root_and_frozen_reference(runtime, transport, monkeypatch):
    raw, reference, before = setup_series(runtime, "headshot")
    gpt.queue_images(transport, "token", count=1)
    external_run = gpt.mulerouter.subprocess.run
    observed = []

    def inspect_cli(argv, **options):
        refs = json.loads(argv[argv.index("--images") + 1])
        observed.append([Path(ref).read_bytes() for ref in refs])
        return external_run(argv, **options)

    monkeypatch.setattr(gpt.mulerouter, "shutil", SimpleNamespace(which=lambda _name: "/synthetic-test-bin/mulerun"))
    monkeypatch.setattr(gpt.mulerouter.subprocess, "run", inspect_cli)
    with TestClient(edit.app_for(runtime)) as client:
        gpt.configure(client.app, "token")
        accepted = client.post(PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        record = assert_completed(runtime, "headshot", reference, before, size="1024x1024")
        assert record.members[0].execution.schema_version == 2
        assert observed == [[reference]] and len(transport["cli"]) == 1
        assert [(r.method, r.url) for r, _ in transport["calls"]] == [("GET", gpt.IMAGE_URL)]
        assert not transport["queue"] and not transport["cli_queue"]
