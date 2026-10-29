"""Strict headshot authority/failure boundaries through real Studio HTTP.

Reuse H1's real pipeline/app/Core and the existing external transport fixture.
The only extra fault seams are actual storage commit failure and an original
worker wrapper that mutates a collaborator object then calls the real worker.
No admission result, provider execution result or receipt is manufactured.
"""

import base64
from copy import deepcopy
import hashlib
import json

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import ImageVariant
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_qwen20_studio_image as qwen
from tests import test_studio_headshot_core as headshot
from tests import test_studio_three_view_boundaries as boundaries


runtime = headshot.runtime
transport = headshot.transport
first = headshot.three_view
gpt_sheet = headshot.gpt_sheet
HEADSHOT_IMAGES = {
    "head_shot", "headshot_asset", "headshot_image_url", "avatar_url",
    "headshot_prompt", "headshot_updated_at",
}
REPLACEMENTS = [
    pytest.param("local", "root", id="local-root"),
    pytest.param("series", "owner", id="series-owner"),
    pytest.param("global", "owner", id="global-owner"),
    pytest.param("local", "target", id="local-target"),
]


def raw(**changes):
    return first.raw() | {
        "client_request_id": "headshot-boundary-request", "generation_type": "headshot",
        "prompt": headshot.PROMPT,
    } | changes


def image_fields(target):
    return target.model_dump(mode="json", include=HEADSHOT_IMAGES)


def replace_same_id(item, route, replacement):
    """Collaborator replacement with equal values; preserve actual receipt data."""
    owner = headshot.admission.owner(item, route)
    original = item.scripts["p"] if replacement == "root" else owner.characters[0] if replacement == "target" else owner
    clone = original.model_copy(deep=True)
    assert clone is not original and clone.model_dump() == original.model_dump()
    if replacement == "root":
        item.scripts["p"] = clone
    elif replacement == "target":
        owner.characters[0] = clone
    elif route == "series":
        item.series_store["s"] = clone
    else:
        assert route == "global"
        item.library_store = clone
    boundaries.commit_owners(item)


@pytest.mark.parametrize("change", [
    pytest.param({"seed": None}, id="explicit-null-seed"),
    pytest.param({"watermark": False}, id="explicit-false-watermark"),
    pytest.param({"aspect_ratio": None}, id="explicit-null-aspect"),
    pytest.param({"negative_prompt": "blur"}, id="negative-text"),
    pytest.param({"negative_prompt": " "}, id="negative-whitespace"),
    pytest.param({"quality": "high"}, id="unsupported-quality"),
    pytest.param({"reference_variant_id": "first-b"}, id="selected-id-mismatch"),
    pytest.param({"reference_image_url": "uploads/first-b.png"}, id="selected-url-mismatch"),
])
def test_headshot_invalid_admission_has_no_freeze_receipt_or_paid_call(runtime, transport, change):
    headshot.setup_headshot(runtime)
    gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, "http")
        saved, media = gpt_sheet.persisted(runtime), boundaries.saved_files(runtime)
        result = client.post(first.PUBLIC, json=raw(**change))
        assert result.status_code == 422, result.text
        assert gpt_sheet.persisted(runtime) == saved and boundaries.saved_files(runtime) == media
        assert not headshot.admission.owner(runtime, "local").asset_image_requests
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})
        assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("change", [
    pytest.param({"reference_variant_id": None}, id="reference-null"),
    pytest.param({"negative_prompt": None}, id="negative-absent-to-null"),
    pytest.param({"negative_prompt": ""}, id="negative-absent-to-empty"),
])
def test_headshot_original_key_preserves_raw_presence_without_repeat_payment(runtime, transport, change):
    before = headshot.setup_headshot(runtime)
    gpt_sheet.queue_images(transport, "http", count=1)
    body = raw()
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, "http")
        accepted = client.post(first.PUBLIC, json=body)
        assert accepted.status_code == 200, accepted.text
        headshot.assert_wire(transport, before["reference"], headshot.PROMPT, strict=True)
        original = boundaries.current_record(runtime, "local").model_dump(mode="json")
        assert original["members"][0]["execution"]["phase"] == "completed"
        saved, media = gpt_sheet.persisted(runtime), boundaries.saved_files(runtime)
        count = len(transport["calls"])
        conflict = client.post(first.PUBLIC, json=body | change)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "storage_conflict" and conflict.json()["commit_uncertain"] is False
        assert boundaries.current_record(runtime, "local").model_dump(mode="json") == original
        assert gpt_sheet.persisted(runtime) == saved and boundaries.saved_files(runtime) == media
        read = client.get(first.PUBLIC + "/" + body["client_request_id"])
        replay = client.post(first.PUBLIC, json=body)
        assert read.status_code == replay.status_code == 200
        assert read.json()["reason"] is None and replay.json()["replayed"] is True
        assert read.json()["request"] == replay.json()["request"]
        assert gpt_sheet.persisted(runtime) == saved and len(transport["calls"]) == count
        assert not transport["cli"]


@pytest.mark.parametrize("same_url", [False, True], ids=["different-url", "same-url"])
def test_headshot_duplicate_selected_id_never_falls_back_or_pays(runtime, transport, same_url):
    headshot.setup_headshot(runtime)
    target = boundaries.current_target(runtime, "local")
    original = next(value for value in target.full_body_asset.variants if value.id == first.SELECTED_ID)
    duplicate = original.model_copy(deep=True)
    if not same_url:
        duplicate.url = "uploads/first-b.png"
    target.full_body_asset.variants.append(duplicate)
    boundaries.commit_owners(runtime)
    gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, "http")
        saved, media = gpt_sheet.persisted(runtime), boundaries.saved_files(runtime)
        result = client.post(first.PUBLIC, json=raw())
        assert result.status_code == 422, result.text
        assert result.json()["detail"] == "studio_headshot_selected_reference_ambiguous"
        assert gpt_sheet.persisted(runtime) == saved and boundaries.saved_files(runtime) == media
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})
        assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("route,replacement", REPLACEMENTS)
def test_headshot_original_object_replacement_before_worker_cannot_pay(runtime, transport, monkeypatch, route, replacement):
    before = headshot.setup_headshot(runtime, route)
    expected_images = image_fields(boundaries.current_target(runtime, route))
    gpt_sheet.queue_images(transport, "http", count=1)
    queued = list(transport["queue"])
    captured = {}
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        service = runtime.asset_image_service
        original_worker = service.run_original_request

        def replace_then_run(request_id):
            with runtime._save_lock:
                original_owner = headshot.admission.owner(runtime, route)
                original_record = boundaries.current_record(runtime, route)
                assert original_record.id == request_id
                assert original_record.members[0].execution.phase == "accepted"
                captured.update(original_owner=original_owner, original_record=original_record,
                                original_record_value=original_record.model_dump(mode="json"))
                replace_same_id(runtime, route, replacement)
                captured.update(owner=headshot.admission.owner(runtime, route),
                                target=boundaries.current_target(runtime, route),
                                target_value=boundaries.current_target(runtime, route).model_dump(mode="json"),
                                owner_value=headshot.admission.owner(runtime, route).model_dump(mode="json"),
                                disk=gpt_sheet.persisted(runtime), media=boundaries.saved_files(runtime))
            return original_worker(request_id)

        monkeypatch.setattr(service, "run_original_request", replace_then_run)
        response = client.post(first.PUBLIC, json=raw())
        assert response.status_code == 200, response.text
        record = boundaries.current_record(runtime, route)
        execution = record.members[0].execution
        replaced_receipt_owner = replacement != "target"
        assert execution.phase == ("accepted" if replaced_receipt_owner else "cancelled")
        assert execution.schema_version == 3
        assert execution.handle is execution.observation is execution.prepared_artifact is execution.artifact is None
        assert not (runtime.data_dir / execution.output_ref).exists()
        if replaced_receipt_owner:
            # Equal serialized IDs do not transfer the first admission's claim.
            assert captured["owner"] is not captured["original_owner"]
            assert record is not captured["original_record"]
            assert captured["owner"].model_dump(mode="json") == captured["owner_value"]
            assert captured["original_record"].model_dump(mode="json") == captured["original_record_value"]
            assert gpt_sheet.persisted(runtime) == captured["disk"]
        else:
            assert captured["owner"] is captured["original_owner"]
            assert record is captured["original_record"]
        assert boundaries.current_target(runtime, route) is captured["target"]
        assert captured["target"].model_dump(mode="json") == captured["target_value"]
        assert image_fields(captured["target"]) == expected_images
        assert captured["target"].model_dump(mode="json", include=headshot.PROTECTED_FIELDS) == before["protected"]
        assert boundaries.saved_files(runtime) == captured["media"]
        saved = gpt_sheet.persisted(runtime)
        read = client.get(first.PUBLIC + "/" + raw()["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw())
        assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        assert read.json()["request"] == replay.json()["request"]
        assert read.json()["request"]["members"][0]["status"] == ("needs_confirmation" if replaced_receipt_owner else "cancelled")
        assert original_worker(record.id) is None
        assert gpt_sheet.persisted(runtime) == saved
        assert not getattr(runtime, "_asset_image_runs", {})
        assert not transport["calls"] and not transport["cli"] and transport["queue"] == queued


@pytest.mark.parametrize("route,replacement", REPLACEMENTS)
def test_headshot_original_object_replacement_during_post_cannot_project(runtime, transport, route, replacement):
    before = headshot.setup_headshot(runtime, route)
    expected_images = image_fields(boundaries.current_target(runtime, route))
    gpt_sheet.queue_images(transport, "http", count=1)
    queued = list(transport["queue"])
    captured = {}

    def change_during_post(request):
        if request.method != "POST":
            return
        transport["on_send"] = None
        with runtime._save_lock:
            record = boundaries.current_record(runtime, route)
            assert record.members[0].execution.phase == "submitting"
            captured.update(original_owner=headshot.admission.owner(runtime, route), original_record=record,
                            original_record_value=record.model_dump(mode="json"))
            replace_same_id(runtime, route, replacement)
            captured.update(owner=headshot.admission.owner(runtime, route),
                            owner_value=headshot.admission.owner(runtime, route).model_dump(mode="json"),
                            target=boundaries.current_target(runtime, route),
                            target_value=boundaries.current_target(runtime, route).model_dump(mode="json"),
                            disk=gpt_sheet.persisted(runtime), media=boundaries.saved_files(runtime))

    transport["on_send"] = change_during_post
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        response = client.post(first.PUBLIC, json=raw())
        assert response.status_code == 200, response.text
        record = boundaries.current_record(runtime, route)
        assert captured["original_record"].id == record.id
        execution = record.members[0].execution
        assert execution.schema_version == 3 and execution.artifact is None
        replaced_receipt_owner = replacement in {"root", "owner"}
        if replaced_receipt_owner:
            # The ACK arrives after the owning aggregate was replaced. Neither
            # the replacement nor the detached original acquires a new handle.
            assert captured["owner"] is not captured["original_owner"]
            assert record is not captured["original_record"]
            assert execution.phase == "submitting"
            assert execution.handle is execution.observation is execution.prepared_artifact is None
            assert captured["original_record"].model_dump(mode="json") == captured["original_record_value"]
            assert captured["owner"].model_dump(mode="json") == captured["owner_value"]
            assert gpt_sheet.persisted(runtime) == captured["disk"]
            assert not (runtime.data_dir / execution.output_ref).exists()
            assert [(r.method, r.url) for r, _ in transport["calls"]] == [("POST", first.EDIT)]
            assert transport["queue"] == queued[1:]
        else:
            # Target/source edits revoke projection, not the actual owner's receipt.
            assert captured["owner"] is captured["original_owner"]
            assert record is captured["original_record"]
            assert execution.phase == "observed"
            assert execution.handle is not None and execution.handle["task_id"] == gpt_sheet.http_task_id()
            assert execution.observation is not None and execution.observation["status"] == "succeeded"
            assert execution.observation["source_handle"] == execution.handle
            boundaries.prove_paid_file(runtime, execution)
            assert execution.prepared_artifact["handle"] == execution.handle
            assert execution.prepared_artifact["attempt_id"] == execution.attempt_id
            assert execution.prepared_artifact["output_ref"] == execution.output_ref
            assert [(r.method, r.url) for r, _ in transport["calls"]] == [
                ("POST", first.EDIT), ("GET", first.EDIT + "/" + gpt_sheet.http_task_id()),
                ("GET", gpt_sheet.IMAGE_URL),
            ]
            assert not transport["queue"]
        assert boundaries.current_target(runtime, route) is captured["target"]
        assert captured["target"].model_dump(mode="json") == captured["target_value"]
        assert image_fields(captured["target"]) == expected_images
        assert captured["target"].model_dump(mode="json", include=headshot.PROTECTED_FIELDS) == before["protected"]
        assert boundaries.saved_files(runtime) == captured["media"]
        saved, count = gpt_sheet.persisted(runtime), len(transport["calls"])
        read = client.get(first.PUBLIC + "/" + raw()["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw())
        assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        assert read.json()["request"] == replay.json()["request"]
        assert read.json()["request"]["members"][0]["status"] == "needs_confirmation"
        assert runtime.asset_image_service.run_original_request(record.id) is None
        assert gpt_sheet.persisted(runtime) == saved and len(transport["calls"]) == count
        assert not getattr(runtime, "_asset_image_runs", {})
        assert len(boundaries.paid_posts(transport)) == 1 and not transport["cli"]
        assert json.loads(boundaries.paid_posts(transport)[0].body) == {
            "prompt": headshot.PROMPT, "size": "1024x1024", "n": 1,
            "images": ["data:image/png;base64," + base64.b64encode(before["reference"]).decode()],
        }
        assert transport["queue"] == (queued[1:] if replaced_receipt_owner else [])


def test_headshot_unknown_paid_ack_stops_batch_and_cannot_resubmit_after_reopen(runtime, transport):
    headshot.setup_headshot(runtime)
    expected_images = image_fields(boundaries.current_target(runtime, "local"))
    transport["queue"].append(requests.ConnectionError("synthetic headshot response lost after submit"))
    body = raw(batch_size=4)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, "http")
        accepted = client.post(first.PUBLIC, json=body)
        assert accepted.status_code == 200, accepted.text
        record = boundaries.current_record(runtime, "local")
        assert [member.execution.phase for member in record.members] == ["submitting", "cancelled", "cancelled", "cancelled"]
        assert all(member.execution.prepared_artifact is None and member.execution.artifact is None for member in record.members)
        assert image_fields(boundaries.current_target(runtime, "local")) == expected_images
        assert boundaries.current_target(runtime, "local").status == "needs_confirmation"
        assert len(boundaries.paid_posts(transport)) == len(transport["calls"]) == 1
        original = record.model_dump(mode="json")
        saved = gpt_sheet.persisted(runtime)
        read = client.get(first.PUBLIC + "/" + body["client_request_id"])
        replay = client.post(first.PUBLIC, json=body)
        assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        assert [member["status"] for member in read.json()["request"]["members"]] == ["needs_confirmation", "cancelled", "cancelled", "cancelled"]
        assert gpt_sheet.persisted(runtime) == saved and len(transport["calls"]) == 1
    runtime.close()
    reopened = ComicGenPipeline(data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}))
    try:
        app = first.app_for(reopened)
        with TestClient(app) as client:
            app.state.runtime_environment.update({}, remove_keys={
                "DASHSCOPE_API_KEY", "MULEROUTER_API_KEY", "MULERUN_TOKEN",
            }, persist=lambda *_: None)
            saved = gpt_sheet.persisted(reopened)
            read = client.get(first.PUBLIC + "/" + body["client_request_id"])
            replay = client.post(first.PUBLIC, json=body)
            assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
            assert [member["status"] for member in read.json()["request"]["members"]] == ["needs_confirmation", "cancelled", "cancelled", "cancelled"]
            assert boundaries.current_record(reopened, "local").model_dump(mode="json") == original
            assert reopened.asset_image_service.run_original_request(record.id) is None
            assert not reopened.asset_generation_tasks and not getattr(reopened, "_asset_image_runs", {})
            assert gpt_sheet.persisted(reopened) == saved
            assert len(transport["calls"]) == 1 and not transport["cli"]
    finally:
        reopened.close()


def test_headshot_prepared_png_repairs_in_new_app_without_credentials_or_provider(runtime, transport, monkeypatch):
    before = headshot.setup_headshot(runtime)
    target = boundaries.current_target(runtime, "local")
    author = target.model_dump(mode="json", exclude={"headshot_asset", "head_shot", "headshot_request_id", "status"})
    legacy = target.headshot_asset.model_dump(mode="json")
    modern = target.head_shot.model_dump(mode="json")
    old_bytes = {value["url"]: (runtime.data_dir / value["url"]).read_bytes() for value in before["old_faces"].values()}
    gpt_sheet.queue_images(transport, "http", count=1)
    commit = runtime._storage.commit
    failures = []

    def fail_completed_checkpoint(payload):
        records = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        if records and records[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append("completed")
            raise StorageError("synthetic headshot completed checkpoint failure")
        return commit(payload)

    body = raw()
    with TestClient(first.app_for(runtime), raise_server_exceptions=False) as client:
        gpt_sheet.configure(client.app, "http")
        monkeypatch.setattr(runtime._storage, "commit", fail_completed_checkpoint)
        accepted = client.post(first.PUBLIC, json=body)
        assert accepted.status_code == 200, accepted.text
        assert failures == ["completed"]
        saved = json.loads((runtime.data_dir / "projects.json").read_bytes())["p"]["asset_image_requests"][0]
        execution = saved["members"][0]["execution"]
        assert execution["schema_version"] == 3 and execution["phase"] == "observed" and execution["artifact"] is None
        assert execution["handle"]["task_id"] == gpt_sheet.http_task_id()
        assert execution["observation"]["status"] == "succeeded"
        assert execution["observation"]["source_handle"] == execution["handle"]
        proof = execution["prepared_artifact"]
        assert proof is not None
        assert proof["handle"] == execution["handle"] and proof["attempt_id"] == execution["attempt_id"]
        assert proof["output_ref"] == execution["output_ref"]
        paid_bytes = (runtime.data_dir / execution["output_ref"]).read_bytes()
        assert len(paid_bytes) == proof["size_bytes"] and hashlib.sha256(paid_bytes).hexdigest() == proof["sha256"]
        headshot.assert_wire(transport, before["reference"], headshot.PROMPT, strict=True)
    count = len(transport["calls"])
    runtime.close()
    reopened = ComicGenPipeline(data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}), recover_orphan_tasks=True)
    try:
        app = first.app_for(reopened)
        with TestClient(app) as client:
            app.state.runtime_environment.update({}, remove_keys={
                "DASHSCOPE_API_KEY", "MULEROUTER_API_KEY", "MULERUN_TOKEN",
            }, persist=lambda *_: None)
            assert not app.state.runtime_environment.snapshot().get("MULEROUTER_API_KEY")
            assert not app.state.runtime_environment.snapshot().get("MULERUN_TOKEN")
            target = boundaries.current_target(reopened, "local")
            record = boundaries.current_record(reopened, "local")
            member, repaired = record.members[0], record.members[0].execution
            assert record.id == saved["id"] and record.request_fingerprint == saved["request_fingerprint"]
            assert getattr(target, "headshot_request_id", None) == record.id and target.is_consistent is False
            assert target.model_dump(mode="json", exclude={"headshot_asset", "head_shot", "headshot_request_id", "status"}) == author
            assert target.model_dump(mode="json", include=headshot.PROTECTED_FIELDS) == before["protected"]
            assert target.head_shot.model_dump(mode="json", include=headshot.MOTION_FIELDS) == before["motion"]
            assert repaired.schema_version == 3 and repaired.phase == "completed"
            assert repaired.artifact == repaired.prepared_artifact == proof
            assert repaired.handle == execution["handle"] and repaired.observation == execution["observation"]
            assert repaired.intent == execution["intent"] and repaired.media == execution["media"]
            assert repaired.intent["prompt"] == headshot.PROMPT
            assert repaired.intent["intent_contract_version"] == first.CONTRACT
            assert repaired.media[0]["sha256"] == hashlib.sha256(before["reference"]).hexdigest()
            assert repaired.media[0]["size_bytes"] == len(before["reference"])
            assert (reopened.data_dir / execution["output_ref"]).read_bytes() == paid_bytes
            expected_variant = ImageVariant.model_validate({
                "id": member.variant_id, "url": repaired.output_ref,
                "created_at": record.created_at, "prompt_used": repaired.intent["prompt"],
            }).model_dump(mode="json")
            for actual, previous, key in [
                (target.headshot_asset.model_dump(mode="json"), legacy, "variants"),
                (target.head_shot.model_dump(mode="json"), modern, "image_variants"),
            ]:
                assert {k: v for k, v in actual.items() if k != key} == {k: v for k, v in previous.items() if k != key}
                assert actual[key][:-1] == previous[key]
                assert len(actual[key]) == len(previous[key]) + 1 and actual[key][-1] == expected_variant
            assert target.headshot_asset.selected_id == target.head_shot.selected_image_id == headshot.OLD_ID
            assert {ref: (reopened.data_dir / ref).read_bytes() for ref in old_bytes} == old_bytes
            headshot.assert_provider_image_pixels(reopened.data_dir / repaired.output_ref)
            persisted = gpt_sheet.persisted(reopened)
            read = client.get(first.PUBLIC + "/" + body["client_request_id"])
            replay = client.post(first.PUBLIC, json=body)
            assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
            assert read.json()["request"] == replay.json()["request"]
            assert read.json()["reason"] is None and read.json()["request"]["members"][0]["status"] == "completed"
            reopened.asset_image_service.repair_request("project", "p", body["client_request_id"])
            assert reopened.asset_image_service.run_original_request(record.id) is None
            assert gpt_sheet.persisted(reopened) == persisted and len(transport["calls"]) == count
            assert len(boundaries.paid_posts(transport)) == 1
            assert not transport["queue"] and not transport["cli"]
    finally:
        reopened.close()


@pytest.mark.parametrize("three_view_unknown", [False, True], ids=["complete-three-view", "partial-unknown-three-view"])
def test_headshot_cannot_invalidate_original_full_body_and_three_view_receipts(runtime, transport, three_view_unknown):
    headshot.setup_headshot(runtime)
    target = boundaries.current_target(runtime, "local")
    # Before any admission, establish the pure-T2I full-body fixture without
    # uploaded derived-reference flags. Never rewrite a persisted receipt/guard.
    for container in (target.three_view_asset, target.headshot_asset):
        for value in container.variants:
            value.is_uploaded_source = False
    for container in (target.three_views, target.head_shot):
        for value in container.image_variants:
            value.is_uploaded_source = False
    runtime.scripts["p"].model_settings.t2i_model = "qwen-image-2.0"
    boundaries.commit_owners(runtime)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "synthetic-headshot-neighbor-key"}, persist=lambda *_: None)
        qwen.queue_images(transport, count=1)
        full_body = {
            "client_request_id": "neighbor-full-body", "asset_id": "c", "asset_type": "character",
            "generation_type": "full_body", "apply_style": False, "prompt": "Original full-body author prompt", "batch_size": 1,
        }
        accepted = client.post(first.PUBLIC, json=full_body)
        assert accepted.status_code == 200, accepted.text
        records = runtime.scripts["p"].asset_image_requests
        assert len(records) == 1 and records[0].channel == "full_body"
        full_record = records[0]
        assert full_record.members[0].execution.phase == "completed"
        assert len(transport["calls"]) == 2 and qwen.posts(transport)[0]["model"] == "qwen-image-2.0"
        boundaries.prove_paid_file(runtime, full_record.members[0].execution)
        target = boundaries.current_target(runtime, "local")
        source_id, source_ref = target.full_body_asset.selected_id, target.full_body_image_url
        assert source_id == full_record.members[0].variant_id and source_ref == full_record.members[0].execution.output_ref
        source_bytes = (runtime.data_dir / source_ref).read_bytes()
        three_body = first.raw() | {
            "client_request_id": "neighbor-three-view", "reference_variant_id": source_id,
            "reference_image_url": source_ref, "batch_size": 2 if three_view_unknown else 1,
        }
        gpt_sheet.queue_images(transport, "http", count=1)
        if three_view_unknown:
            transport["queue"].append(requests.ConnectionError("synthetic neighboring three-view ACK lost"))
        accepted = client.post(first.PUBLIC, json=three_body)
        assert accepted.status_code == 200, accepted.text
        assert len(records) == 2
        three_record = records[1]
        assert three_record.channel == "three_view"
        assert [member.execution.phase for member in three_record.members] == (["completed", "submitting"] if three_view_unknown else ["completed"])
        assert target.is_consistent is False
        before_records = deepcopy([record.model_dump(mode="json") for record in records])
        before_pointers = (target.full_body_request_id, target.three_view_request_id)
        before_selection = (target.full_body_asset.selected_id, target.three_view_asset.selected_id)
        before_reads = [client.get(first.PUBLIC + "/" + body["client_request_id"]) for body in (full_body, three_body)]
        assert all(read.status_code == 200 and read.json()["reason"] is None for read in before_reads)
        before_public = [read.json()["request"] for read in before_reads]
        gpt_sheet.queue_images(transport, "http", count=1)
        head_body = raw(reference_variant_id=source_id, reference_image_url=source_ref)
        accepted = client.post(first.PUBLIC, json=head_body)
        assert accepted.status_code == 200, accepted.text
        assert len(records) == 3 and records[2].channel == "headshot"
        head_record = records[2]
        assert head_record.members[0].execution.phase == "completed"
        assert head_record.members[0].execution.media[0]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
        boundaries.prove_paid_file(runtime, head_record.members[0].execution)
        assert json.loads(boundaries.paid_posts(transport)[-1].body) == {
            "prompt": headshot.PROMPT, "size": "1024x1024", "n": 1,
            "images": ["data:image/png;base64," + base64.b64encode(source_bytes).decode()],
        }
        assert target.is_consistent is False
        assert [record.model_dump(mode="json") for record in records[:2]] == before_records
        assert (target.full_body_request_id, target.three_view_request_id) == before_pointers
        assert (target.full_body_asset.selected_id, target.three_view_asset.selected_id) == before_selection
        assert getattr(target, "headshot_request_id", None) == head_record.id
        persisted, count = gpt_sheet.persisted(runtime), len(transport["calls"])
        for index, body in enumerate((full_body, three_body)):
            read = client.get(first.PUBLIC + "/" + body["client_request_id"])
            replay = client.post(first.PUBLIC, json=body)
            assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
            assert read.json()["reason"] is None and replay.json()["reason"] is None
            assert read.json()["request"] == replay.json()["request"] == before_public[index]
        assert [record.model_dump(mode="json") for record in records[:2]] == before_records
        assert gpt_sheet.persisted(runtime) == persisted and len(transport["calls"]) == count
        assert len(boundaries.paid_posts(transport)) == (4 if three_view_unknown else 3)
        assert not transport["queue"] and not transport["cli_queue"] and not transport["cli"]
