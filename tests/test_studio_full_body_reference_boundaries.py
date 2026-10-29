"""Reference full-body failure boundaries with real app, storage and Core.

Reuse the existing external provider transport fixture. Fault seams mutate a
source or owner at a real worker boundary; they always call the original worker.
"""

import base64
import hashlib
import json

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.models import GenerationStatus
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_full_body_reference_core as full_body
from tests import test_studio_headshot_core as headshot
from tests import test_studio_headshot_boundaries as portrait_boundaries
from tests import test_studio_three_view_boundaries as boundaries


runtime = headshot.runtime
transport = headshot.transport
first = headshot.three_view
gpt_sheet = headshot.gpt_sheet
REFERENCE = "uploads/three-upload-first.png"
PROMPT = "Full-body Alice in her red coat, standing on white."
IMAGE_FIELDS = {
    "full_body", "full_body_asset", "full_body_image_url", "full_body_prompt", "full_body_updated_at",
}


def raw(**changes):
    return {
        "client_request_id": "full-body-reference-boundary", "asset_id": "c",
        "asset_type": "character", "generation_type": "full_body",
        "reference_variant_id": "three-first", "reference_image_url": REFERENCE,
        "prompt": PROMPT, "apply_style": False,
    } | changes


def setup(item, route="local"):
    headshot.setup_headshot(item, route)
    return (item.data_dir / REFERENCE).read_bytes()


def image_fields(item, route="local"):
    return boundaries.current_target(item, route).model_dump(mode="json", include=IMAGE_FIELDS)


@pytest.mark.parametrize("change", [
    {"seed": None}, {"watermark": False}, {"negative_prompt": "blur"},
    {"aspect_ratio": "2:3"}, {"quality": "high"},
    {"reference_variant_id": "three-second"},
    {"reference_image_url": "uploads/three-upload-second.png"},
    {"model_name": "wan2.7-image"},
])
def test_invalid_reference_request_does_not_freeze_bind_or_pay(runtime, transport, change):
    setup(runtime)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        saved, media = gpt_sheet.persisted(runtime), boundaries.saved_files(runtime)
        response = client.post(first.PUBLIC, json=raw(**change))
        assert response.status_code == 422, response.text
        assert gpt_sheet.persisted(runtime) == saved and boundaries.saved_files(runtime) == media
        assert not boundaries.current_target(runtime, "local").full_body_request_id
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})
        assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "empty", "whitespace", "unknown", "outside", "blob", "data"])
@pytest.mark.parametrize("legacy", [False, True], ids=["strict", "legacy"])
def test_invalid_preferred_uploaded_source_cannot_fall_back(runtime, transport, mutation, legacy):
    setup(runtime)
    target = boundaries.current_target(runtime, "local")
    if mutation == "missing":
        (runtime.data_dir / REFERENCE).unlink()
    elif mutation == "duplicate":
        target.three_view_asset.variants.append(target.three_view_asset.variants[0].model_copy(deep=True))
    else:
        target.three_view_asset.variants[0].url = {
            "empty": "", "whitespace": "   ", "unknown": "unmanaged.png",
            "outside": "/synthetic-outside-root.png", "blob": "blob:synthetic-local-reference",
            "data": "data:image/png;base64,c3ludGhldGlj",
        }[mutation]
    boundaries.commit_owners(runtime)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        saved, media = gpt_sheet.persisted(runtime), boundaries.saved_files(runtime)
        body = raw()
        if legacy:
            body = {key: value for key, value in body.items()
                    if key not in {"client_request_id", "reference_variant_id", "reference_image_url"}}
        result = client.post("/projects/p/assets/generate" if legacy else first.PUBLIC, json=body)
        # The existing legacy endpoint maps plain media ValueError to 404.
        expected_status = 404 if legacy and mutation == "missing" else 422
        assert result.status_code == expected_status, result.text
        assert gpt_sheet.persisted(runtime) == saved and boundaries.saved_files(runtime) == media
        assert not runtime.asset_generation_tasks and not transport["calls"] and not transport["cli"]


def test_original_frozen_bytes_survive_source_file_replacement(runtime, transport, monkeypatch):
    source = setup(runtime)
    gpt_sheet.queue_images(transport, "http", count=1)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        service = runtime.asset_image_service
        original_worker = service.run_original_request

        def replace_then_run(request_id):
            assert boundaries.current_record(runtime, "local").members[0].execution.phase == "accepted"
            (runtime.data_dir / REFERENCE).write_bytes((runtime.data_dir / "uploads/three-upload-second.png").read_bytes())
            assert (runtime.data_dir / REFERENCE).read_bytes() != source
            return original_worker(request_id)

        monkeypatch.setattr(service, "run_original_request", replace_then_run)
        response = client.post(first.PUBLIC, json=raw())
        assert response.status_code == 200, response.text
        record = boundaries.current_record(runtime, "local")
        assert record.members[0].execution.phase == "completed"
        assert len(boundaries.paid_posts(transport)) == 1
        assert json.loads(boundaries.paid_posts(transport)[0].body)["images"] == [
            "data:image/png;base64," + base64.b64encode(source).decode(),
        ]
        assert not transport["queue"] and not transport["cli"]


def test_changed_source_identity_with_reference_still_present_cannot_pay(runtime, transport, monkeypatch):
    setup(runtime)
    before = image_fields(runtime)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        service = runtime.asset_image_service
        original_worker = service.run_original_request

        def change_then_run(request_id):
            with runtime._save_lock:
                target = boundaries.current_target(runtime, "local")
                target.three_view_asset.variants[0].is_uploaded_source = False
                assert target.three_view_asset.variants[1].is_uploaded_source
                boundaries.commit_owners(runtime)
            return original_worker(request_id)

        monkeypatch.setattr(service, "run_original_request", change_then_run)
        response = client.post(first.PUBLIC, json=raw())
        assert response.status_code == 200, response.text
        record = boundaries.current_record(runtime, "local")
        assert record.members[0].execution.phase == "cancelled"
        assert image_fields(runtime) == before
        read = client.get(first.PUBLIC + "/" + raw()["client_request_id"])
        assert read.status_code == 200 and read.json()["reason"] == "studio_image_request_inputs_changed"
        assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("route,replacement", portrait_boundaries.REPLACEMENTS)
def test_replaced_original_owner_or_target_cannot_pay(runtime, transport, monkeypatch, route, replacement):
    setup(runtime, route)
    before = image_fields(runtime, route)
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
                portrait_boundaries.replace_same_id(runtime, route, replacement)
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
        assert image_fields(runtime, route) == before
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


def test_unknown_reference_batch_retains_original_key_after_new_app(runtime, transport):
    setup(runtime)
    before = image_fields(runtime)
    body = raw(batch_size=2)
    transport["queue"].append(requests.ConnectionError("synthetic full-body response lost after submit"))
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        response = client.post(first.PUBLIC, json=body)
        assert response.status_code == 200, response.text
        record = boundaries.current_record(runtime, "local")
        assert [member.execution.phase for member in record.members] == ["submitting", "cancelled"]
        assert image_fields(runtime) == before and len(transport["calls"]) == 1
        # Original raw identity is checked before current source/model preparation.
        target = boundaries.current_target(runtime, "local")
        target.three_view_asset.variants[0].url = ""
        runtime.scripts["p"].model_settings.i2i_model = "wan2.6-image"
        boundaries.commit_owners(runtime)
        saved = gpt_sheet.persisted(runtime)
        replay = client.post(first.PUBLIC, json=body)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["reason"] == "studio_image_request_inputs_changed"
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
            assert read.json()["request"] == replay.json()["request"]
            assert [member["status"] for member in read.json()["request"]["members"]] == ["needs_confirmation", "cancelled"]
            assert gpt_sheet.persisted(reopened) == saved and image_fields(reopened) == before
            assert len(transport["calls"]) == 1 and not transport["cli"]
    finally:
        reopened.close()


@pytest.mark.parametrize("route,replacement", [*portrait_boundaries.REPLACEMENTS, ("local", "source")])
def test_changed_target_after_paid_post_retains_proof_without_wrong_projection(runtime, transport, route, replacement):
    setup(runtime, route)
    before = image_fields(runtime, route)
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
            if replacement == "source":
                boundaries.current_target(runtime, route).three_view_asset.variants[0].is_uploaded_source = False
                boundaries.commit_owners(runtime)
            else:
                portrait_boundaries.replace_same_id(runtime, route, replacement)
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
        assert image_fields(runtime, route) == before
        assert boundaries.saved_files(runtime) == captured["media"]
        saved, count = gpt_sheet.persisted(runtime), len(transport["calls"])
        read = client.get(first.PUBLIC + "/" + raw()["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw())
        assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        assert read.json()["request"] == replay.json()["request"]
        assert read.json()["request"]["members"][0]["status"] == "needs_confirmation"
        if replacement == "source":
            assert read.json()["reason"] == "studio_image_request_inputs_changed"
        assert runtime.asset_image_service.run_original_request(record.id) is None
        assert gpt_sheet.persisted(runtime) == saved and len(transport["calls"]) == count
        assert not getattr(runtime, "_asset_image_runs", {})
        assert len(boundaries.paid_posts(transport)) == 1 and not transport["cli"]
        assert transport["queue"] == (queued[1:] if replaced_receipt_owner else [])


def test_prepared_full_body_repairs_in_new_app_without_credentials_or_provider(runtime, transport, monkeypatch):
    before = full_body.setup_reference(runtime)
    original_target = boundaries.current_target(runtime, "local")
    author = original_target.model_dump(mode="json", exclude={"full_body", "full_body_asset", "full_body_request_id", "status"})
    legacy = original_target.full_body_asset.model_dump(mode="json")
    modern = original_target.full_body.model_dump(mode="json")
    old_bytes = {value["url"]: (runtime.data_dir / value["url"]).read_bytes() for value in before["old"].values()}
    body = full_body.strict_body(before)
    gpt_sheet.queue_images(transport, "http", count=1)
    commit = runtime._storage.commit
    failures = []

    def fail_completed_checkpoint(payload):
        records = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        if records and records[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append("completed")
            raise StorageError("synthetic full-body completed checkpoint failure")
        return commit(payload)

    app = first.app_for(runtime)
    with TestClient(app, raise_server_exceptions=False) as client:
        gpt_sheet.configure(app, "http")
        monkeypatch.setattr(runtime._storage, "commit", fail_completed_checkpoint)
        response = client.post(first.PUBLIC, json=body)
        assert response.status_code == 200, response.text
        assert failures == ["completed"]
        stored = json.loads((runtime.data_dir / "projects.json").read_bytes())["p"]["asset_image_requests"][0]
        execution = stored["members"][0]["execution"]
        assert execution["schema_version"] == 3 and execution["phase"] == "observed" and execution["artifact"] is None
        assert execution["handle"]["task_id"] == gpt_sheet.http_task_id()
        assert execution["observation"]["status"] == "succeeded"
        assert execution["observation"]["source_handle"] == execution["handle"]
        assert execution["prepared_artifact"]["handle"] == execution["handle"]
        assert execution["prepared_artifact"]["attempt_id"] == execution["attempt_id"]
        assert execution["prepared_artifact"]["output_ref"] == execution["output_ref"]
        paid_bytes = boundaries.prove_paid_file(runtime, boundaries.current_record(runtime, "local").members[0].execution)
    count = len(transport["calls"])
    assert count == 3 and len(boundaries.paid_posts(transport)) == 1
    assert not transport["queue"] and not transport["cli"]
    runtime.close()
    reopened = ComicGenPipeline(data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}), recover_orphan_tasks=True)
    try:
        app = first.app_for(reopened)
        with TestClient(app) as client:
            app.state.runtime_environment.update({}, remove_keys={
                "DASHSCOPE_API_KEY", "MULEROUTER_API_KEY", "MULERUN_TOKEN",
            }, persist=lambda *_: None)
            saved_owner = headshot.saved_owner(reopened, "local")
            assert len(saved_owner.asset_image_requests) == 1
            record = saved_owner.asset_image_requests[0]
            member = record.members[0]
            repaired = member.execution
            assert record.id == stored["id"] and record.request_fingerprint == stored["request_fingerprint"]
            assert (record.entrypoint, record.asset_type, record.channel) == (full_body.ENTRYPOINT, "character", "full_body")
            assert (record.request_root_kind, record.request_root_id, record.owner_kind, record.owner_id) == ("project", "p", "script", "p")
            assert record.base_prompt == full_body.PROMPT
            assert repaired.fingerprint == execution["fingerprint"]
            assert repaired.scope == execution["scope"] and repaired.credential_ref == execution["credential_ref"]
            assert repaired.phase == "completed" and repaired.schema_version == 3
            assert repaired.artifact == repaired.prepared_artifact == execution["prepared_artifact"]
            assert repaired.handle == execution["handle"] and repaired.observation == execution["observation"]
            assert repaired.media == execution["media"] and repaired.intent == execution["intent"]
            assert repaired.intent["intent_contract_version"] == full_body.CONTRACT
            assert repaired.intent["prompt"] == full_body.REVERSE + full_body.PROMPT
            assert repaired.intent["params"] == {"size": "1024x1536"}
            assert repaired.media[0]["sha256"] == hashlib.sha256(before["reference"]).hexdigest()
            assert repaired.media[0]["size_bytes"] == len(before["reference"])
            assert (reopened.data_dir / execution["output_ref"]).read_bytes() == paid_bytes
            target = saved_owner.characters[0]
            assert target.model_dump() == boundaries.current_target(reopened, "local").model_dump()
            assert isinstance(target.status, GenerationStatus)
            assert target.full_body_request_id == record.id and target.status.value == "completed"
            assert target.model_dump(mode="json", exclude={"full_body", "full_body_asset", "full_body_request_id", "status"}) == author
            # Recovery appends to each existing history without reconciling the
            # two historical containers or replacing any author selection.
            expected_variant = full_body.ImageVariant.model_validate({
                "id": member.variant_id, "url": repaired.output_ref,
                "created_at": record.created_at, "prompt_used": repaired.intent["prompt"],
            }).model_dump(mode="json")
            assert target.full_body_asset is not None and target.full_body is not None
            for actual, previous, history_key in [
                (target.full_body_asset.model_dump(mode="json"), legacy, "variants"),
                (target.full_body.model_dump(mode="json"), modern, "image_variants"),
            ]:
                assert {k: v for k, v in actual.items() if k != history_key} == {k: v for k, v in previous.items() if k != history_key}
                assert actual[history_key][:-1] == previous[history_key]
                assert len(actual[history_key]) == len(previous[history_key]) + 1
                assert actual[history_key][-1] == expected_variant
            assert target.full_body_asset.selected_id == target.full_body.selected_image_id == before["selected"]
            assert target.full_body.model_dump(mode="json", include=headshot.MOTION_FIELDS) == before["motion"]
            assert target.model_dump(mode="json", include=full_body.PROTECTED) == before["protected"]
            assert {ref: (reopened.data_dir / ref).read_bytes() for ref in old_bytes} == old_bytes
            assert repaired.output_ref is not None
            headshot.assert_provider_image_pixels(reopened.data_dir / repaired.output_ref)
            full_body.assert_public_read_is_stable(client, reopened, transport, record, replay=body)
            stable = gpt_sheet.persisted(reopened)
            reopened.asset_image_service.repair_request("project", "p", body["client_request_id"])
            assert gpt_sheet.persisted(reopened) == stable
            assert reopened.asset_image_service.run_original_request(record.id) is None
            assert len(transport["calls"]) == count and len(boundaries.paid_posts(transport)) == 1
            assert not transport["queue"] and not transport["cli"]
    finally:
        reopened.close()


@pytest.mark.parametrize("original,rewritten", [
    ("full_body", "headshot"), ("full_body", "three_view"),
    ("full_body", "foreign-series-owner"), ("headshot", "full_body"),
])
def test_valid_edit_execution_cannot_be_relabelled_across_studio_contracts(runtime, transport, original, rewritten):
    setup(runtime)
    gpt_sheet.queue_images(transport, "http", count=1)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        body = raw(aspect_ratio="1:1") if original == "full_body" else portrait_boundaries.raw()
        response = client.post(first.PUBLIC, json=body)
        assert response.status_code == 200, response.text
        record = boundaries.current_record(runtime, "local")
        assert record.members[0].execution.phase == "completed"
        assert record.members[0].execution.schema_version == 3
        assert record.members[0].execution.intent["params"] == {"size": "1024x1024"}
        original_value = record.model_dump()
        assert AssetImageRequestRecord.model_validate(original_value).model_dump() == original_value
        changed = record.model_dump()
        if rewritten == "foreign-series-owner":
            changed.update(request_root_kind="series", request_root_id="s", request_root_series_id=None,
                           owner_kind="series", owner_id="other-series")
        else:
            changed.update(entrypoint=f"studio.asset.{rewritten}.v1", channel=rewritten)
        saved, calls = gpt_sheet.persisted(runtime), len(transport["calls"])
        error = ("Invalid Studio image standalone series owner" if rewritten == "foreign-series-owner"
                 else "Unsupported async Studio Edit tuple")
        with pytest.raises(ValueError, match=error):
            AssetImageRequestRecord.model_validate(changed)
        assert record.model_dump() == original_value and gpt_sheet.persisted(runtime) == saved
        assert len(transport["calls"]) == calls and len(boundaries.paid_posts(transport)) == 1
        assert not transport["queue"] and not transport["cli"]


def test_source_change_after_first_completed_member_prevents_second_payment(runtime, transport, monkeypatch):
    setup(runtime)
    gpt_sheet.queue_images(transport, "http", count=1)
    complete = runtime.asset_image_service._complete_member
    observations = []

    def complete_then_change(original, execution, token):
        result = complete(original, execution, token)
        record = boundaries.current_record(runtime, "local")
        if not observations and record.members[0].execution.phase == "completed":
            persisted = json.loads((runtime.data_dir / "projects.json").read_bytes())["p"]["asset_image_requests"][0]
            assert persisted["members"][0]["execution"]["phase"] == "completed"
            target = boundaries.current_target(runtime, "local")
            assert any(v.id == record.members[0].variant_id for v in target.full_body_asset.variants)
            target.three_view_asset.variants[0].is_uploaded_source = False
            boundaries.commit_owners(runtime)
            observations.append(record.id)
        return result

    monkeypatch.setattr(runtime.asset_image_service, "_complete_member", complete_then_change)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        gpt_sheet.configure(app, "http")
        body = raw(batch_size=2)
        response = client.post(first.PUBLIC, json=body)
        assert response.status_code == 200, response.text
        record = boundaries.current_record(runtime, "local")
        assert observations == [record.id]
        assert [member.execution.phase for member in record.members] == ["completed", "cancelled"]
        boundaries.prove_paid_file(runtime, record.members[0].execution)
        assert record.members[0].execution.artifact is not None
        assert record.members[1].execution.prepared_artifact is None
        saved, count = gpt_sheet.persisted(runtime), len(transport["calls"])
        read = client.get(first.PUBLIC + "/" + body["client_request_id"])
        replay = client.post(first.PUBLIC, json=body)
        assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        assert read.json()["reason"] == "studio_image_request_inputs_changed"
        assert [member["status"] for member in read.json()["request"]["members"]] == ["completed", "cancelled"]
        assert gpt_sheet.persisted(runtime) == saved and len(transport["calls"]) == count
        assert len(boundaries.paid_posts(transport)) == 1 and not transport["queue"] and not transport["cli"]
