"""Studio image handle checkpoints with actual Core/storage and external HTTP only.

Prepared source, not collected/run. App close/reopen is not an OS process kill.
The virtual clock removes waiting only; receipts/proofs are produced by real code.
"""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any
import zipfile

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen import asset_image_execution as execution
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import AssetUnit, ImageAsset, ImageVariant, Script
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models import mulerouter, wan27_image_http
from src.models.provider_tasks import ProviderQueryError
from src.models.provider_recovery import ProviderRecoveryError
from src.models.provider_media_execution import ProviderMediaExecutionError
from src.utils import media_versions
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_models as old_history
from tests import test_studio_core_image_worker as old_worker
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_three_view_core as edit
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan27_bound_image import response

transport = gpt.transport
runtime = edit.runtime
TASK = "b174971e-7708-4ffa-90f6-1d92131551cb"
IMAGE = "https://images.example.invalid/studio-original.png"
PNG = rgb_png_bytes("known original result", width=16, height=16)
KEY = "b3.original.request"
OPERATIONS = ("wan", "generation", "edit")


@pytest.fixture(autouse=True)
def waiting_clock(monkeypatch):
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    simulated = SimpleNamespace(time=time.time, monotonic=lambda: clock["now"], sleep=sleep)
    for module in (execution, wan27_image_http, mulerouter):
        monkeypatch.setattr(module, "time", simulated)
    return clock


def setup(item, operation="generation", route="local", *, selected=True):
    if operation == "edit":
        edit.setup(item, route, "gpt-image-2")
    else:
        admission.setup(item, route)
        root = item.series_store["s"] if route == "standalone" else item.scripts["p"]
        root.model_settings.t2i_model = "wan2.7-image" if operation == "wan" else "gpt-image-2"
    target = admission.owner(item, route).characters[0]
    path = item.data_dir / "uploads/b3-old.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rgb_png_bytes("old selection", width=16, height=16))
    variant = {"id": "old", "url": "uploads/b3-old.png", "created_at": 1}
    unit = AssetUnit.model_validate({"image_variants": [variant], "selected_image_id": "old" if selected else None,
                                    "image_prompt": "Original author", "image_updated_at": 1,
                                    "video_prompt": "Preserved independent motion"})
    if operation == "edit":
        target.three_views = unit
        target.three_view_asset = ImageAsset.model_validate({"variants": [variant], "selected_id": "old" if selected else None})
        target.three_view_prompt = "Original author"
    else:
        target.reference_sheet = unit
        target.image_url = variant["url"] if selected else None
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    return target


def body(operation="generation", *, batch_size=1):
    result = dict(client_request_id=KEY, asset_id="c", asset_type="character",
                  generation_type="reference_sheet", prompt="New author prompt", apply_style=False,
                  batch_size=batch_size)
    if operation == "edit":
        result.update(generation_type="three_view", reference_variant_id=edit.SELECTED_ID,
                      reference_image_url=edit.SELECTED_REF)
    if operation == "wan":
        result.update(seed=0, watermark=False)
    return result


@contextmanager
def client_for(item, operation, *, configure=True):
    with TestClient(edit.app_for(item)) as client:
        if configure:
            if operation == "wan":
                http.enable_fake_key(client.app)
            else:
                gpt.configure(client.app, "http")
        yield client


@contextmanager
def reopened(item):
    item.close()
    other = ComicGenPipeline(data_dir=item.data_dir, runtime_environment=RuntimeEnvironment({}))
    try:
        yield other
    finally:
        other.close()


def receipt(operation, task_id=TASK):
    return response({"output": {"task_id": task_id, "task_status": "PENDING"}} if operation == "wan"
                    else {"task_info": {"id": task_id, "status": "pending"}}, status=200 if operation == "wan" else 202)


def observation(operation, *, pending=False, failed=False, task_id=TASK):
    if operation == "wan":
        output: dict[str, Any] = {"task_id": task_id, "task_status": "FAILED" if failed else "RUNNING" if pending else "SUCCEEDED"}
        if not pending and not failed:
            output["choices"] = [{"message": {"role": "assistant", "content": [{"type": "image", "image": IMAGE}]}}]
        return response({"output": output})
    payload: dict[str, Any] = {"task_info": {"id": task_id, "status": "failed" if failed else "processing" if pending else "completed"}}
    if not pending and not failed:
        payload["images"] = [IMAGE]
    return response(payload)


def disk(item, route="local"):
    return old_worker.disk_request(item, route)


def counts(state):
    calls = [request for request, _kwargs in state["calls"]]
    return sum(r.method == "POST" for r in calls), sum(r.method == "GET" and r.url != IMAGE for r in calls), sum(r.url == IMAGE for r in calls)


def unknown(client, item, state, operation="generation", *, route="local", batch_size=1):
    state["queue"].extend([receipt(operation), requests.ConnectionError("synthetic query unavailable")])
    path = "/series/s/asset_image_requests" if route == "standalone" else "/projects/p/asset_image_requests"
    accepted = client.post(path, json=body(operation, batch_size=batch_size))
    assert accepted.status_code == 200, accepted.text
    saved = disk(item, route)
    record = saved["members"][0]["execution"]
    assert record["schema_version"] == 3 and record["phase"] == "submitted"
    assert record["handle"]["task_id"] == TASK
    return saved


def resume(item, state, saved, operation="generation"):
    member = saved["members"][0]["execution"]
    assert item.asset_image_service.recovery_member_state(saved["id"], member["attempt_id"]) == "query"
    state["queue"].append(observation(operation))
    item.asset_image_service.recover_member_once(saved["id"], member["attempt_id"])
    assert item.asset_image_service.recovery_member_state(saved["id"], member["attempt_id"]) == "fetch"
    state["queue"].append(response(PNG))
    return item.asset_image_service.recover_member_once(saved["id"], member["attempt_id"])


def assert_png(item, record):
    proof = record["prepared_artifact"]
    content = (item.data_dir / record["output_ref"]).read_bytes()
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert (hashlib.sha256(content).hexdigest(), len(content)) == (proof["sha256"], proof["size_bytes"])
    assert proof["attempt_id"] == record["attempt_id"] and proof["handle"] == record["handle"]
    assert proof["output_ref"] == record["output_ref"]
    return content


@pytest.mark.parametrize("operation", OPERATIONS)
def test_first_get_reads_durable_handle_and_new_app_recovers_only_original(runtime, transport, operation):
    target = setup(runtime, operation)
    original_alias = target.image_url
    observed_disk = []

    def before_query(request):
        if request.method == "GET":
            saved = disk(runtime)["members"][0]["execution"]
            assert saved["schema_version"] == 3 and saved["phase"] == "submitted"
            assert saved["handle"]["task_id"] == TASK
            observed_disk.append(deepcopy(saved))

    transport["on_send"] = before_query
    with client_for(runtime, operation) as client:
        saved = unknown(client, runtime, transport, operation)
    assert len(observed_disk) == 1 and counts(transport) == (1, 1, 0)
    transport["on_send"] = None
    with reopened(runtime) as other, client_for(other, operation) as client:
        assert other.asset_image_service.run_original_request(saved["id"]) is None
        result = resume(other, transport, saved, operation)
        finished = result.request.members[0].execution.model_dump(mode="json")
        assert finished["phase"] == "completed" and finished["artifact"] == finished["prepared_artifact"]
        assert finished["handle"] == observed_disk[0]["handle"]
        assert finished["fingerprint"] == observed_disk[0]["fingerprint"]
        assert_png(other, finished)
        character = other.scripts["p"].characters[0]
        unit = character.three_views if operation == "edit" else character.reference_sheet
        assert unit is not None and unit.selected_image_id == "old"
        assert unit.image_prompt == "Original author" and unit.video_prompt == "Preserved independent motion"
        assert character.image_url == original_alias
        before = disk(other)
        read = client.get(f"/projects/p/asset_image_requests/{KEY}")
        replay = client.post("/projects/p/asset_image_requests", json=body(operation))
        assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        assert disk(other) == before and len(other.scripts["p"].asset_image_requests) == 1
    assert counts(transport) == (1, 2, 1) and not transport["queue"]


@pytest.mark.parametrize("operation", ["wan", "generation"])
def test_original_worker_waits_with_one_post_and_preserves_normal_selection(runtime, transport, waiting_clock, operation):
    setup(runtime, operation, selected=False)
    transport["queue"].extend([receipt(operation), observation(operation, pending=True), observation(operation), response(PNG)])
    with client_for(runtime, operation) as client:
        response_ack = client.post("/projects/p/asset_image_requests", json=body(operation))
        assert response_ack.status_code == 200
        saved = disk(runtime)
        record = saved["members"][0]["execution"]
        assert record["phase"] == "completed"
        unit = runtime.scripts["p"].characters[0].reference_sheet
        assert unit is not None and unit.selected_image_id == record["attempt_id"]
        assert_png(runtime, record)
    assert counts(transport) == (1, 2, 1)
    assert waiting_clock["now"] == (20 if operation == "wan" else 40)


@pytest.mark.parametrize("operation,loss", [("generation", "intent"), ("edit", "media"), ("edit", "version"), ("edit", "file_only")])
def test_missing_material_queries_without_rebuilding_or_projecting(runtime, transport, monkeypatch, operation, loss):
    setup(runtime, operation)
    with client_for(runtime, operation) as client:
        saved = unknown(client, runtime, transport, operation)
        retained = runtime.scripts["p"].asset_image_requests[0].members[0].execution
        fingerprint, handle = retained.fingerprint, deepcopy(retained.handle)
        if loss == "intent":
            retained.intent = {}
        elif loss == "media":
            retained.media = []
        elif loss == "version":
            del retained.intent["intent_contract_version"]
        if operation == "edit":
            frozen = saved["members"][0]["execution"]["media"][0]["sha256"]
            (runtime.data_dir / edit.SELECTED_REF).unlink()
            (runtime.data_dir / "media_versions" / "sha256" / frozen[:2] / frozen).unlink()
        runtime._commit_storage("projects.json")
        preserved = disk(runtime)["members"][0]["execution"]
    with reopened(runtime) as other, client_for(other, operation):
        def cannot_rebuild(*_args, **_kwargs):
            raise AssertionError("Recovery tried to reconstruct paid inputs")
        monkeypatch.setattr(execution, "submit_async_image", cannot_rebuild)
        before_target = other.scripts["p"].characters[0].model_dump(mode="json")
        result = resume(other, transport, saved, operation)
        after = result.request.members[0].execution.model_dump(mode="json")
        assert after["fingerprint"] == fingerprint and after["handle"] == handle
        assert after["intent"] == preserved["intent"] and after["media"] == preserved["media"]
        assert_png(other, after)
        if loss != "file_only":
            assert after["phase"] == "observed" and after["artifact"] is None
            assert result.reason == "studio_image_material_incomplete"
            assert other.asset_image_service.recovery_member_state(saved["id"], after["attempt_id"]) == "blocked"
            assert other.scripts["p"].characters[0].model_dump(mode="json") == before_target
        else:
            assert after["phase"] == "completed"
        assert disk(other)["members"][0]["execution"] == after
    assert counts(transport) == (1, 2, 1)


@pytest.mark.parametrize("change", ["mode", "null_mode", "extra_contract", "params_null", "size", "full_hash", "tuple", "duplicate", "batch_conflict"])
def test_missing_field_does_not_hide_present_contradictions(runtime, transport, change):
    setup(runtime)
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport)
        candidate = deepcopy(saved)
        record = candidate["members"][0]["execution"]
        if change != "full_hash":
            del record["intent"]["prompt"]
        if change == "mode":
            record["intent"]["mode"] = "i2i"
        elif change == "null_mode":
            record["intent"]["mode"] = None
        elif change == "extra_contract":
            record["intent"]["intent_contract_version"] = None
        elif change == "params_null":
            record["intent"]["params"] = None
        elif change == "size":
            record["intent"]["params"]["size"] = "768x768"
        elif change == "full_hash":
            record["fingerprint"] = "0" * 64
        elif change == "tuple":
            candidate.update(entrypoint="studio.asset.headshot.v1", channel="headshot", base_prompt="A")
        elif change == "duplicate":
            candidate["members"].append(deepcopy(candidate["members"][0]))
        else:
            second = deepcopy(candidate["members"][0])
            second["variant_id"] = second["execution"]["attempt_id"] = "another-attempt"
            second["execution"]["output_ref"] = "assets/studio/another-attempt.png"
            second["execution"]["intent"]["params"]["size"] = "1536x1024"
            candidate["members"].append(second)
        before = counts(transport)
        with pytest.raises(ValueError):
            AssetImageRequestRecord.model_validate(candidate)
        assert counts(transport) == before and disk(runtime) == saved


@pytest.mark.parametrize("change", ["edit_version", "reference_role"])
def test_edit_missing_prompt_does_not_hide_version_or_reference_conflict(runtime, transport, change):
    setup(runtime, "edit")
    with client_for(runtime, "edit") as client:
        saved = unknown(client, runtime, transport, "edit")
        candidate = deepcopy(saved)
        record = candidate["members"][0]["execution"]
        del record["intent"]["prompt"]
        if change == "edit_version":
            record["intent"]["intent_contract_version"] = "provider-gpt-image2-edit-bound-v2"
        else:
            record["media"][0]["role"] = "first_frame"
        before = counts(transport)
        with pytest.raises(ValueError):
            AssetImageRequestRecord.model_validate(candidate)
        assert counts(transport) == before and disk(runtime) == saved


@pytest.mark.parametrize("failure", ["lost_ack", "handle_save"])
def test_uncertain_submit_or_handle_save_failure_never_queries_or_resubmits(runtime, transport, monkeypatch, failure):
    setup(runtime)
    with client_for(runtime, "generation"):
        command = execution.AssetImageRequestCommand.model_validate(body())
        accepted = runtime.asset_image_service.admit("project", "p", command)
        transport["queue"].append(requests.ConnectionError("lost ACK") if failure == "lost_ack" else receipt("generation"))
        real_commit = runtime._storage.commit
        original_error = StorageError("synthetic handle checkpoint failure")

        def commit(payload):
            records = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
            if failure == "handle_save" and records and records[0]["members"][0]["execution"]["phase"] == "submitted":
                raise original_error
            return real_commit(payload)

        monkeypatch.setattr(runtime._storage, "commit", commit)
        if failure == "handle_save":
            with pytest.raises(StorageError) as caught:
                runtime.asset_image_service.run_original_request(accepted.request.id)
            assert caught.value is original_error
        else:
            runtime.asset_image_service.run_original_request(accepted.request.id)
        record = disk(runtime)["members"][0]["execution"]
        assert record["phase"] == "submitting" and record["handle"] is None
    with reopened(runtime) as other, client_for(other, "generation") as client:
        assert other.asset_image_service.run_original_request(accepted.request.id) is None
        other.asset_image_service.recover_member_once(accepted.request.id, record["attempt_id"])
        assert client.post("/projects/p/asset_image_requests", json=body()).json()["replayed"] is True
        assert disk(other)["members"][0]["execution"] == record
    assert counts(transport) == (1, 0, 0)


@pytest.mark.parametrize("change", ["author", "owner", "member", "token"])
def test_late_ack_keeps_original_receipt_but_never_writes_replacement(runtime, transport, change):
    setup(runtime)
    protected = {}

    def on_post(request):
        if request.method != "POST":
            return
        owner = runtime.scripts["p"]
        saved = owner.asset_image_requests[0]
        protected["old_owner"] = owner
        protected["old_member"] = saved.members[0]
        protected["old_task"] = runtime.asset_generation_tasks[saved.id]
        if change == "author":
            owner.characters[0].reference_sheet.image_prompt = "Current author draft"
        elif change == "owner":
            runtime.scripts["p"] = Script.model_validate(owner.model_dump(mode="json"))
        elif change == "member":
            saved.members[0] = saved.members[0].model_copy(deep=True)
        else:
            new_token = object()
            runtime._asset_image_runs[("script", "p", "character", "c")] = new_token
            protected["token"] = new_token
            replacement = dict(runtime.asset_generation_tasks[saved.id], _token=new_token, status="processing")
            runtime.asset_generation_tasks[saved.id] = replacement
            protected["new_task"] = replacement
        runtime._commit_storage("projects.json")
        protected["disk"] = deepcopy(disk(runtime))
        protected["owner_bytes"] = (runtime.data_dir / "projects.json").read_bytes()
        protected["owner_dump"] = runtime.scripts["p"].model_dump(mode="json")

    transport["on_send"] = on_post
    transport["queue"].append(receipt("generation"))
    if change == "author":
        transport["queue"].extend([observation("generation"), response(PNG)])
    with client_for(runtime, "generation") as client:
        ack = client.post("/projects/p/asset_image_requests", json=body(batch_size=2))
        assert ack.status_code == 200
        saved = disk(runtime)
        if change == "author":
            assert saved["members"][0]["execution"]["handle"]["task_id"] == TASK
            assert saved["members"][0]["execution"]["phase"] == "observed"
            assert_png(runtime, saved["members"][0]["execution"])
            target = runtime.scripts["p"].characters[0]
            assert target.reference_sheet.image_prompt == "Current author draft"
            assert [v.id for v in target.reference_sheet.image_variants] == ["old"]
        else:
            assert saved == protected["disk"]
            assert (runtime.data_dir / "projects.json").read_bytes() == protected["owner_bytes"]
            assert runtime.scripts["p"].model_dump(mode="json") == protected["owner_dump"]
            assert protected["old_member"].execution.phase == "submitting"
            assert protected["old_member"].execution.handle is None
            assert counts(transport) == (1, 0, 0)
            if change == "token":
                assert runtime._asset_image_runs[("script", "p", "character", "c")] is protected["token"]
                assert runtime.asset_generation_tasks[saved["id"]] is protected["new_task"]
                assert protected["new_task"]["status"] == "processing"
                assert protected["old_task"]["status"] == "processing"
            else:
                assert protected["old_task"]["status"] == "needs_confirmation"
                assert ("script", "p", "character", "c") not in runtime._asset_image_runs
    transport["on_send"] = None


@pytest.mark.parametrize("selected", [True, False])
def test_prepared_local_repair_needs_no_credentials_and_never_selects(runtime, transport, monkeypatch, selected):
    setup(runtime, selected=selected)
    transport["queue"].extend([receipt("generation"), observation("generation"), response(PNG)])
    with client_for(runtime, "generation"):
        accepted = runtime.asset_image_service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(body()))
        real_commit = runtime._storage.commit

        def fail_completed(payload):
            rows = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
            if rows and rows[0]["members"][0]["execution"]["phase"] == "completed":
                raise StorageError("synthetic final checkpoint failure")
            return real_commit(payload)

        monkeypatch.setattr(runtime._storage, "commit", fail_completed)
        with pytest.raises(StorageError):
            runtime.asset_image_service.run_original_request(accepted.request.id)
        saved = disk(runtime)
        assert saved["members"][0]["execution"]["phase"] == "observed"
        content = assert_png(runtime, saved["members"][0]["execution"])
    calls = counts(transport)
    with reopened(runtime) as other:
        def blocked(*_args, **_kwargs): raise AssertionError("Local proof repair resolved credentials")
        monkeypatch.setattr(other.asset_image_service, "_resolve_query_authority", blocked)
        assert other.asset_image_service.recovery_member_state(saved["id"], saved["members"][0]["execution"]["attempt_id"]) == "repair"
        result = other.asset_image_service.recover_member_once(saved["id"], saved["members"][0]["execution"]["attempt_id"])
        repaired = result.request.members[0].execution
        assert other.asset_image_service.recovery_member_state(saved["id"], repaired.attempt_id) == "terminal"
        assert repaired.phase == "completed" and repaired.artifact == repaired.prepared_artifact
        assert repaired.output_ref is not None
        unit = other.scripts["p"].characters[0].reference_sheet
        assert unit is not None
        assert unit.selected_image_id == ("old" if selected else None)
        assert unit.image_prompt == "Original author" and unit.image_updated_at == 1
        assert (other.data_dir / repaired.output_ref).read_bytes() == content
        before = disk(other)
        other.asset_image_service.recover_member_once(saved["id"], repaired.attempt_id)
        assert disk(other) == before
    assert counts(transport) == calls


def test_missing_prompt_package_preserves_paid_png_and_strips_authority(runtime, transport):
    setup(runtime)
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport)
        runtime.scripts["p"].asset_image_requests[0].members[0].execution.intent = {}
        runtime._commit_storage("projects.json")
    with reopened(runtime) as other, client_for(other, "generation"):
        result = resume(other, transport, saved)
        record = result.request.members[0].execution
        original = other.read_committed_script("p").model_dump(mode="json")
        original_bytes = assert_png(other, record.model_dump(mode="json"))
        assert record.output_ref is not None
        old_bytes = (other.data_dir / "uploads/b3-old.png").read_bytes()
        assert old_bytes != original_bytes
        artifact = other.export_project_package("p")
        with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
            script = json.loads(archive.read("project.json"))["script"]
            assert not script["asset_image_requests"] and script.get("asset_image_request_scope_id") is None
            unit = script["characters"][0]["reference_sheet"]
            paid = next(value for value in unit["image_variants"] if value["id"] == record.attempt_id)
            assert "prompt_used" not in paid
            assert ImageVariant.model_validate(paid).prompt_used is None and unit["selected_image_id"] == "old"
            assert (unit["image_prompt"], unit["video_prompt"]) == (
                original["characters"][0]["reference_sheet"]["image_prompt"],
                original["characters"][0]["reference_sheet"]["video_prompt"])
            assert archive.read("media/" + record.output_ref) == original_bytes
            assert TASK not in archive.read("project.json").decode()
        imported = other.import_project_package(artifact.data)
        assert imported.id != "p" and not imported.asset_image_requests
        assert imported.characters[0].reference_sheet_request_id is None
        imported_unit = imported.characters[0].reference_sheet
        assert imported_unit is not None and len(imported_unit.image_variants) == 2
        assert imported_unit.selected_image_id is not None
        assert any(value.id == imported_unit.selected_image_id for value in imported_unit.image_variants)
        imported_paid = next(value for value in imported_unit.image_variants
                             if value.id != imported_unit.selected_image_id)
        imported_selected = next(value for value in imported_unit.image_variants
                                 if value.id == imported_unit.selected_image_id)
        assert (other.data_dir / imported_selected.url).read_bytes() == old_bytes
        assert (other.data_dir / imported_paid.url).read_bytes() == original_bytes
        assert imported_paid.prompt_used is None
        assert (imported_unit.image_prompt, imported_unit.video_prompt) == (unit["image_prompt"], unit["video_prompt"])
        again = other.export_project_package(imported.id)
        with zipfile.ZipFile(io.BytesIO(again.data)) as archive:
            assert original_bytes in [archive.read(name) for name in archive.namelist() if name.startswith("media/")]
        assert other.read_committed_script("p").model_dump(mode="json") == original
    assert counts(transport) == (1, 2, 1)


def test_series_partial_batch_recovery_never_submits_unstarted_sibling(runtime, transport):
    setup(runtime, route="series")
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport, route="series", batch_size=2)
        assert saved["members"][1]["execution"]["phase"] == "cancelled"
    with reopened(runtime) as other, client_for(other, "generation"):
        result = resume(other, transport, saved)
        assert result.request.members[0].execution.phase == "completed"
        sibling = result.request.members[1].execution
        other.asset_image_service.recover_member_once(saved["id"], sibling.attempt_id)
        assert disk(other, "series")["members"][1]["execution"]["phase"] == "cancelled"
        assert not other.scripts["p"].asset_image_requests
    assert counts(transport) == (1, 2, 1)


@pytest.mark.parametrize("failure", ["missing_id", "wrong_id", "503", "failed"])
def test_query_failure_keeps_original_handle_and_never_submits(runtime, transport, failure):
    setup(runtime)
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport)
    with reopened(runtime) as other, client_for(other, "generation"):
        first = saved["members"][0]["execution"]
        payload = (response({"task_info": {"status": "completed"}, "images": [IMAGE]}) if failure == "missing_id" else
                   observation("generation", task_id="40b86202-3b43-40e4-8b93-2db6e8832e12") if failure == "wrong_id" else
                   response({}, status=503) if failure == "503" else observation("generation", failed=True))
        transport["queue"].append(payload)
        if failure == "failed":
            result = other.asset_image_service.recover_member_once(saved["id"], first["attempt_id"])
            observed = result.request.members[0].execution.observation
            assert observed is not None and observed["status"] == "failed"
            assert other.asset_image_service.recovery_member_state(saved["id"], first["attempt_id"]) == "terminal"
            status = other.asset_image_service.request_state(result.request)
            assert status["members"][0]["reason"] == "provider_image_remote_failed"
        else:
            with pytest.raises(ProviderQueryError):
                other.asset_image_service.recover_member_once(saved["id"], first["attempt_id"])
        after = disk(other)["members"][0]["execution"]
        assert after["handle"] == first["handle"] and after["fingerprint"] == first["fingerprint"]
        assert after["attempt_id"] == first["attempt_id"] and after["artifact"] is None
    assert counts(transport) == (1, 2, 0)


@pytest.mark.parametrize("phase", ["accepted", "submitting"])
def test_fixed_schema2_unknown_history_does_not_gain_image_query_authority(runtime, transport, phase):
    # Explicit old builder fixture; do not replace this with a new schema3 admission.
    raw = old_history.request_payload()
    raw["members"][0]["execution"] = old_history.execution_payload(phase=phase)
    record = AssetImageRequestRecord.model_validate(raw)
    assert record.members[0].execution.schema_version == 2
    project = Script.model_validate(dict(id="project-root", title="Old", original_text="Synthetic", created_at=1, updated_at=1,
                                         asset_image_request_scope_id=raw["request_root_scope_id"], asset_image_requests=[raw]))
    runtime.scripts = {project.id: project}
    runtime._commit_storage("projects.json")
    before = record.model_dump(mode="json")
    result = runtime.asset_image_service.recover_member_once(record.id, record.members[0].execution.attempt_id)
    assert result.request.model_dump(mode="json") == before and not transport["calls"]
    assert result.request.members[0].execution.handle is None
    assert runtime.asset_image_service.recovery_member_state(record.id, record.members[0].execution.attempt_id) == "unknown"


def test_fixed_schema2_http_unknown_json_remains_queryless(runtime, transport):
    # Unedited actual old test output; never regenerate it with a current builder.
    fixture = Path(__file__).with_name("fixtures") / "studio_image_schema2_http_unknown.json"
    original_bytes = fixture.read_bytes()
    assert hashlib.sha256(original_bytes).hexdigest() == "2987d4220a749fbe740e84765369bd78f8a64f04c56e32c6e6637e6214daddfc"
    raw = json.loads(original_bytes)
    original = raw["p"]["asset_image_requests"][0]
    assert [m["execution"]["phase"] for m in original["members"]] == ["submitting", "cancelled"]
    assert all(m["execution"]["schema_version"] == 2 for m in original["members"])
    runtime.scripts = {"p": Script.model_validate(raw["p"])}
    runtime._commit_storage("projects.json")
    before_bytes = (runtime.data_dir / "projects.json").read_bytes()
    for member in original["members"]:
        result = runtime.asset_image_service.recover_member_once(original["id"], member["execution"]["attempt_id"])
        assert result.request.model_dump(mode="json") == original
    assert runtime.asset_image_service.run_original_request(original["id"]) is None
    assert (runtime.data_dir / "projects.json").read_bytes() == before_bytes
    assert not transport["calls"] and not transport["cli"]
    assert fixture.read_bytes() == original_bytes


@pytest.mark.parametrize("route", ["global", "standalone"])
def test_recovery_keeps_the_actual_shared_owner(runtime, transport, route):
    setup(runtime, route=route)
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport, route=route)
    with reopened(runtime) as other, client_for(other, "generation"):
        result = resume(other, transport, saved)
        assert result.request.members[0].execution.phase == "completed"
        assert disk(other, route)["id"] == saved["id"]
        assert not other.scripts["p"].asset_image_requests
        owner = admission.owner(other, route)
        unit = owner.characters[0].reference_sheet
        assert unit is not None
        assert len(owner.asset_image_requests) == 1 and unit.selected_image_id == "old"
    assert counts(transport) == (1, 2, 1)


@pytest.mark.parametrize("change", ["owner", "closed"])
def test_late_recovery_get_cannot_write_replacement_or_closed_runtime(runtime, transport, change):
    setup(runtime)
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport)
    with reopened(runtime) as other, client_for(other, "generation"):
        before = disk(other)

        def late(request):
            if request.method != "GET":
                return
            if change == "owner":
                other.scripts["p"] = Script.model_validate(other.scripts["p"].model_dump(mode="json"))
                other._commit_storage("projects.json")
            else:
                other.close()

        transport["on_send"] = late
        transport["queue"].append(observation("generation"))
        with pytest.raises(StorageError):
            other.asset_image_service.recover_member_once(saved["id"], saved["members"][0]["execution"]["attempt_id"])
        assert disk(other) == before and counts(transport) == (1, 2, 0)
        transport["on_send"] = None


def test_prepared_missing_final_rejects_changed_refetched_png(runtime, transport, monkeypatch):
    setup(runtime)
    transport["queue"].extend([receipt("generation"), observation("generation"), response(PNG)])
    with client_for(runtime, "generation"):
        accepted = runtime.asset_image_service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(body()))
        attempt = accepted.request.members[0].execution.attempt_id
        initial_unit = runtime.read_committed_script("p").characters[0].reference_sheet
        expected_author = (initial_unit.selected_image_id, initial_unit.image_prompt, initial_unit.video_prompt)

        def assert_authorship(item):
            unit = item.read_committed_script("p").characters[0].reference_sheet
            assert (unit.selected_image_id, unit.image_prompt, unit.video_prompt) == expected_author

        link = media_versions.os.link
        failure = OSError("synthetic final publication failure after prepared checkpoint")

        def cannot_publish(source, destination, *args, **kwargs):
            if destination == f"{attempt}.png":
                raise failure
            return link(source, destination, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(media_versions.os, "link", cannot_publish)
            with pytest.raises(StorageError, match="Cannot publish downloaded media") as caught:
                runtime.asset_image_service.run_original_request(accepted.request.id)
            assert caught.value.commit_uncertain is False
            assert caught.value.__cause__ is failure
        saved = disk(runtime)
        record = saved["members"][0]["execution"]
        assert record["prepared_artifact"] is not None and record["phase"] == "observed"
        assert record["artifact"] is None and counts(transport) == (1, 1, 1)
        proof = record["prepared_artifact"]
        assert (proof["attempt_id"], proof["handle"], proof["output_ref"]) == (attempt, record["handle"], record["output_ref"])
        assert not (runtime.data_dir / record["output_ref"]).exists()
        assert_authorship(runtime)
    with reopened(runtime) as other, client_for(other, "generation"):
        assert_authorship(other)
        before_owner = (other.data_dir / "projects.json").read_bytes()
        transport["queue"].append(response(rgb_png_bytes("different paid bytes", width=32, height=32)))
        with pytest.raises(ProviderRecoveryError, match="changed_after_prepare"):
            other.asset_image_service.recover_member_once(saved["id"], attempt)
        assert disk(other)["members"][0]["execution"] == record
        assert (other.data_dir / "projects.json").read_bytes() == before_owner
        assert_authorship(other)
        assert not (other.data_dir / record["output_ref"]).exists()
        transport["queue"].append(response(PNG))
        recovered = other.asset_image_service.recover_member_once(saved["id"], attempt)
        assert recovered.request.members[0].execution.phase == "completed"
        assert recovered.request.members[0].execution.prepared_artifact == record["prepared_artifact"]
        recovered_record = recovered.request.members[0].execution.model_dump(mode="json")
        assert recovered_record["artifact"] == recovered_record["prepared_artifact"]
        assert_png(other, recovered_record)
        assert_authorship(other)
    assert counts(transport) == (1, 1, 3) and not transport["queue"]


def test_invalid_png_retains_original_success_observation_without_new_submit(runtime, transport):
    setup(runtime)
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport)
    with reopened(runtime) as other, client_for(other, "generation"):
        attempt = saved["members"][0]["execution"]["attempt_id"]
        transport["queue"].append(observation("generation"))
        other.asset_image_service.recover_member_once(saved["id"], attempt)
        before = disk(other)
        transport["queue"].append(response(b"not an image"))
        with pytest.raises(ProviderMediaExecutionError):
            other.asset_image_service.recover_member_once(saved["id"], attempt)
        assert disk(other) == before
        record = before["members"][0]["execution"]
        assert record["prepared_artifact"] is None and record["handle"]["task_id"] == TASK
        assert not (other.data_dir / record["output_ref"]).exists()
        transport["queue"].append(response(PNG))
        result = other.asset_image_service.recover_member_once(saved["id"], attempt)
        assert result.request.members[0].execution.phase == "completed"
    assert counts(transport) == (1, 2, 2)


def test_normal_original_batch_keeps_two_distinct_members_and_old_selection(runtime, transport):
    setup(runtime)
    second = "9ae6c9cf-4e3b-4dd4-8805-2d477b29a6db"
    for task_id in (TASK, second):
        transport["queue"].extend([receipt("generation", task_id), observation("generation", task_id=task_id), response(PNG)])
    with client_for(runtime, "generation") as client:
        result = client.post("/projects/p/asset_image_requests", json=body(batch_size=2))
        assert result.status_code == 200
        saved = disk(runtime)
        records = [member["execution"] for member in saved["members"]]
        assert [value["handle"]["task_id"] for value in records] == [TASK, second]
        assert len({value["attempt_id"] for value in records}) == 2
        assert all(value["phase"] == "completed" for value in records)
        for value in records:
            assert_png(runtime, value)
        assert runtime.scripts["p"].characters[0].reference_sheet.selected_image_id == "old"
        assert not runtime._asset_image_runs
    assert counts(transport) == (2, 2, 2)


def test_original_wait_stops_after_storage_closes_without_get(runtime, transport, monkeypatch, waiting_clock):
    setup(runtime)
    transport["queue"].append(receipt("generation"))
    with client_for(runtime, "generation"):
        accepted = runtime.asset_image_service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(body()))
        sleep = execution.time.sleep

        def close_during_wait(seconds):
            sleep(seconds)
            runtime.close()

        monkeypatch.setattr(execution.time, "sleep", close_during_wait)
        with pytest.raises(StorageError):
            runtime.asset_image_service.run_original_request(accepted.request.id)
        saved = disk(runtime)["members"][0]["execution"]
        assert saved["phase"] == "submitted" and saved["handle"]["task_id"] == TASK
    assert counts(transport) == (1, 0, 0) and waiting_clock["now"] <= 0.25


@pytest.mark.parametrize("claim", ["original", "recovery"])
def test_recovery_state_reports_busy_without_network_or_releasing_another_claim(runtime, transport, claim):
    setup(runtime)
    with client_for(runtime, "generation") as client:
        if claim == "original":
            accepted = runtime.asset_image_service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(body()))
            saved = disk(runtime)
            assert accepted.request.id == saved["id"]
        else:
            saved = unknown(client, runtime, transport)
            runtime._asset_image_runs[("script", "p", "character", "c")] = object()
        key = ("script", "p", "character", "c")
        token = runtime._asset_image_runs[key]
        before_bytes = (runtime.data_dir / "projects.json").read_bytes()
        before_calls = counts(transport)
        attempt = saved["members"][0]["execution"]["attempt_id"]
        assert runtime.asset_image_service.recovery_member_state(saved["id"], attempt) == "busy"
        result = runtime.asset_image_service.recover_member_once(saved["id"], attempt)
        assert result.request.model_dump(mode="json") == saved
        assert runtime._asset_image_runs[key] is token
        assert (runtime.data_dir / "projects.json").read_bytes() == before_bytes
        assert counts(transport) == before_calls


def test_real_recovery_claim_is_visible_and_cannot_release_its_replacement(runtime, transport):
    setup(runtime)
    with client_for(runtime, "generation") as client:
        saved = unknown(client, runtime, transport)
    with reopened(runtime) as other, client_for(other, "generation"):
        attempt = saved["members"][0]["execution"]["attempt_id"]
        key = ("script", "p", "character", "c")
        replacement = object()
        before_bytes = (other.data_dir / "projects.json").read_bytes()

        def during_query(request):
            assert request.method == "GET"
            original_token = other._asset_image_runs[key]
            assert other.asset_image_service.recovery_member_state(saved["id"], attempt) == "busy"
            nested = other.asset_image_service.recover_member_once(saved["id"], attempt)
            assert nested.request.model_dump(mode="json") == saved
            assert other._asset_image_runs[key] is original_token
            other._asset_image_runs[key] = replacement

        transport["on_send"] = during_query
        transport["queue"].append(observation("generation"))
        with pytest.raises(StorageError):
            other.asset_image_service.recover_member_once(saved["id"], attempt)
        assert other._asset_image_runs[key] is replacement
        assert (other.data_dir / "projects.json").read_bytes() == before_bytes
        assert counts(transport) == (1, 2, 0)
        transport["on_send"] = None


@pytest.mark.parametrize("operation,limit,seconds", [("wan", 60, 600), ("generation", 45, 900)])
def test_original_wait_is_bounded_and_keeps_pending_handle(runtime, transport, waiting_clock, operation, limit, seconds):
    setup(runtime, operation)
    transport["queue"].append(receipt(operation))
    transport["queue"].extend(observation(operation, pending=True) for _ in range(limit))
    with client_for(runtime, operation) as client:
        accepted = client.post("/projects/p/asset_image_requests", json=body(operation))
        assert accepted.status_code == 200
        saved = disk(runtime)
        record = saved["members"][0]["execution"]
        assert record["phase"] == "observed" and record["handle"]["task_id"] == TASK
        assert record["prepared_artifact"] is None and record["artifact"] is None
        assert runtime.asset_image_service.recovery_member_state(saved["id"], record["attempt_id"]) == "query"
        assert not runtime._asset_image_runs
    assert counts(transport) == (1, limit, 0) and not transport["queue"]
    assert waiting_clock["now"] == seconds


def test_owner_replacement_before_worker_cannot_pay_or_change_replacement(runtime, transport):
    setup(runtime)
    with client_for(runtime, "generation"):
        accepted = runtime.asset_image_service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(body(batch_size=2)))
        original = runtime.scripts["p"]
        original_task = runtime.asset_generation_tasks[accepted.request.id]
        runtime.scripts["p"] = Script.model_validate(original.model_dump(mode="json"))
        runtime._commit_storage("projects.json")
        before_bytes = (runtime.data_dir / "projects.json").read_bytes()
        before_dump = runtime.scripts["p"].model_dump(mode="json")
        runtime.asset_image_service.run_original_request(accepted.request.id)
        assert (runtime.data_dir / "projects.json").read_bytes() == before_bytes
        assert runtime.scripts["p"].model_dump(mode="json") == before_dump
        assert all(m.execution.phase == "accepted" for m in original.asset_image_requests[0].members)
        assert all(m.execution.phase == "accepted" for m in runtime.scripts["p"].asset_image_requests[0].members)
        assert original_task["status"] == "needs_confirmation" and not runtime._asset_image_runs
        assert not transport["calls"] and not transport["cli"]


# r2 additions: schedule identity replacement at the real outer lock release.
def _replace_after_recovery_claim(item, monkeypatch, change):
    real_lock = item._save_lock
    key = ("script", "p", "character", "c")
    state = {"fired": False, "depth": 0}

    class ClaimReleaseLock:
        def __enter__(self):
            real_lock.acquire()
            state["depth"] += 1
            return self

        def __exit__(self, exc_type, exc, traceback):
            state["depth"] -= 1
            real_lock.release()
            if state["depth"] or state["fired"] or key not in getattr(item, "_asset_image_runs", {}):
                return False
            state["fired"] = True
            # A deterministic schedule after unlock, not mutation inside an RLock.
            with real_lock:
                owner = item.scripts["p"]
                original = owner.asset_image_requests[0]
                state["old_request"] = original
                state["old_request_dump"] = original.model_dump(mode="json")
                state["old_member"] = original.members[0]
                state["old_execution"] = original.members[0].execution.model_dump(mode="json")
                state["token"] = item._asset_image_runs[key]
                owner_dump = owner.model_dump(mode="json")
                if change == "owner":
                    item.scripts["p"] = Script.model_validate(owner_dump)
                    assert item.scripts["p"] is not owner
                else:
                    owner.asset_image_requests[0] = AssetImageRequestRecord.model_validate(state["old_request_dump"])
                    assert owner.asset_image_requests[0] is not original
                assert item.scripts["p"].model_dump(mode="json") == owner_dump
                item._commit_storage("projects.json")
                state["replacement"] = item.scripts["p"]
                state["replacement_dump"] = item.scripts["p"].model_dump(mode="json")
                state["replacement_bytes"] = (item.data_dir / "projects.json").read_bytes()
            return False

    monkeypatch.setattr(item, "_save_lock", ClaimReleaseLock())
    return state


def _prepared_repair_image(item, transport, monkeypatch):
    # Reuse the real submit/query/fetch/checkpoint path, not a constructed proof.
    transport["queue"].extend([receipt("generation"), observation("generation"), response(PNG)])
    with client_for(item, "generation"):
        accepted = item.asset_image_service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(body()))
        real_commit = item._storage.commit

        def fail_completed(payload):
            rows = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
            if rows and rows[0]["members"][0]["execution"]["phase"] == "completed":
                raise StorageError("synthetic final checkpoint failure")
            return real_commit(payload)

        with monkeypatch.context() as patch:
            patch.setattr(item._storage, "commit", fail_completed)
            with pytest.raises(StorageError, match="synthetic final checkpoint failure"):
                item.asset_image_service.run_original_request(accepted.request.id)
        saved = disk(item)
        record = saved["members"][0]["execution"]
        assert record["phase"] == "observed" and record["artifact"] is None
        content = assert_png(item, record)
    return saved, content


@pytest.mark.parametrize("operation", ["query", "repair"])
@pytest.mark.parametrize("change", ["owner", "request"])
def test_recovery_claim_binds_identity_before_unlock(runtime, transport, monkeypatch, operation, change):
    setup(runtime)
    if operation == "query":
        with client_for(runtime, "generation") as client:
            saved = unknown(client, runtime, transport)
        content = None
    else:
        saved, content = _prepared_repair_image(runtime, transport, monkeypatch)
    calls = counts(transport)
    with reopened(runtime) as other, client_for(other, "generation", configure=operation == "query"):
        member = saved["members"][0]["execution"]
        waiting = observation("generation") if operation == "query" else None
        if waiting is not None:
            transport["queue"].append(waiting)
        with monkeypatch.context() as patch:
            state = _replace_after_recovery_claim(other, patch, change)
            with pytest.raises(StorageConflictError, match="studio_image_async_owner_changed"):
                if operation == "query":
                    other.asset_image_service.recover_member_once(saved["id"], member["attempt_id"])
                else:
                    other.asset_image_service.repair_request("project", "p", KEY)
        assert state["fired"]
        assert counts(transport) == calls
        assert transport["queue"] == ([waiting] if waiting is not None else [])
        assert other.scripts["p"] is state["replacement"]
        assert other.scripts["p"].model_dump(mode="json") == state["replacement_dump"]
        assert (other.data_dir / "projects.json").read_bytes() == state["replacement_bytes"]
        assert state["old_request"].model_dump(mode="json") == state["old_request_dump"]
        assert state["old_member"].execution.model_dump(mode="json") == state["old_execution"]
        assert ("script", "p", "character", "c") not in other._asset_image_runs
        if content is not None:
            assert (other.data_dir / member["output_ref"]).read_bytes() == content


def test_repair_request_completes_original_prepared_identity_without_credentials(runtime, transport, monkeypatch):
    setup(runtime)
    saved, content = _prepared_repair_image(runtime, transport, monkeypatch)
    calls = counts(transport)
    with reopened(runtime) as other:
        def no_credentials(*_args, **_kwargs):
            raise AssertionError("Local repair resolved provider credentials")
        monkeypatch.setattr(other.asset_image_service, "_resolve_query_authority", no_credentials)
        owner = other.scripts["p"]
        original = owner.asset_image_requests[0]
        member = original.members[0]
        result = other.asset_image_service.repair_request("project", "p", KEY)
        assert other.scripts["p"] is owner and owner.asset_image_requests[0] is original
        assert original.members[0] is member
        assert member.execution.phase == "completed" and member.execution.artifact == member.execution.prepared_artifact
        assert result.request.model_dump(mode="json") == disk(other)
        unit = owner.characters[0].reference_sheet
        assert unit is not None
        assert unit.selected_image_id == "old" and unit.image_prompt == "Original author"
        assert unit.image_updated_at == 1 and unit.video_prompt == "Preserved independent motion"
        assert member.execution.output_ref is not None
        assert (other.data_dir / member.execution.output_ref).read_bytes() == content
        assert member.execution.handle == saved["members"][0]["execution"]["handle"]
        before_bytes = (other.data_dir / "projects.json").read_bytes()
        other.asset_image_service.repair_request("project", "p", KEY)
        assert (other.data_dir / "projects.json").read_bytes() == before_bytes
        assert not other._asset_image_runs
    assert counts(transport) == calls


@pytest.mark.parametrize("operation", ["query", "repair"])
def test_recovery_context_failure_releases_only_its_new_claim(runtime, transport, monkeypatch, operation):
    setup(runtime)
    if operation == "query":
        with client_for(runtime, "generation") as client:
            saved = unknown(client, runtime, transport)
    else:
        saved, _ = _prepared_repair_image(runtime, transport, monkeypatch)
    calls = counts(transport)
    with reopened(runtime) as other:
        sentinel = StorageConflictError("synthetic context capture failure")
        before_bytes = (other.data_dir / "projects.json").read_bytes()
        before_dump = other.scripts["p"].model_dump(mode="json")

        def fail_context(*_args, **_kwargs):
            raise sentinel

        monkeypatch.setattr(other.asset_image_service, "_async_context", fail_context)
        with pytest.raises(StorageConflictError) as caught:
            if operation == "query":
                other.asset_image_service.recover_member_once(saved["id"], saved["members"][0]["execution"]["attempt_id"])
            else:
                other.asset_image_service.repair_request("project", "p", KEY)
        assert caught.value is sentinel
        assert ("script", "p", "character", "c") not in other._asset_image_runs
        assert (other.data_dir / "projects.json").read_bytes() == before_bytes
        assert other.scripts["p"].model_dump(mode="json") == before_dump
    assert counts(transport) == calls



def test_wan_canceled_observation_stops_worker_and_remains_terminal_after_reopen(runtime, transport, monkeypatch):
    setup(runtime, "wan")
    transport["queue"].extend([
        receipt("wan"),
        response({"output": {"task_id": TASK, "task_status": "CANCELED"}}),
    ])
    with client_for(runtime, "wan") as client:
        accepted = client.post("/projects/p/asset_image_requests", json=body("wan"))
        assert accepted.status_code == 200, accepted.text
        # A second GET is a failure even if the worker catches the transport error.
        assert counts(transport) == (1, 1, 0) and not transport["queue"]
        saved = disk(runtime)
        record = saved["members"][0]["execution"]
        assert record["phase"] == "observed" and record["handle"]["task_id"] == TASK
        assert record["observation"]["status"] == "canceled"
        assert record["observation"]["raw_status"] == "CANCELED"
        assert record["observation"]["source_handle"] == record["handle"]
        assert record["prepared_artifact"] is None and record["artifact"] is None
        assert runtime.asset_image_service.recovery_member_state(saved["id"], record["attempt_id"]) == "terminal"
        status = runtime.asset_image_service.read_task_status(saved["id"])
        assert status is not None and status["members"][0]["reason"] == "provider_image_remote_canceled"
    with reopened(runtime) as other:
        def no_credentials(*_args, **_kwargs):
            raise AssertionError("Canceled image task must not resolve provider credentials")
        monkeypatch.setattr(other.asset_image_service, "_resolve_query_authority", no_credentials)
        before_bytes = (other.data_dir / "projects.json").read_bytes()
        assert other.asset_image_service.recovery_member_state(saved["id"], record["attempt_id"]) == "terminal"
        result = other.asset_image_service.recover_member_once(saved["id"], record["attempt_id"])
        assert result.request.members[0].execution.model_dump(mode="json") == record
        status = other.asset_image_service.read_task_status(saved["id"])
        assert status is not None and status["members"][0]["reason"] == "provider_image_remote_canceled"
        assert (other.data_dir / "projects.json").read_bytes() == before_bytes
        assert not other._asset_image_runs
    assert counts(transport) == (1, 1, 0) and not transport["cli"]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("batch_size", [1, 2])
def test_original_active_worker_public_members_remain_processing(runtime, transport, operation, interrupted, batch_size):
    from src.apps.comic_gen.asset_image_wire import asset_image_request_wire

    setup(runtime, operation)
    snapshots = []
    if batch_size == 2:
        first_task = "665918c1-2f66-4e4d-9d7b-0e3b59aec478"
        transport["queue"].extend([
            receipt(operation, task_id=first_task),
            observation(operation, task_id=first_task), response(PNG),
        ])
    transport["queue"].extend([
        receipt(operation), observation(operation, pending=True),
        requests.ConnectionError("query interrupted after retained running observation")
        if interrupted else observation(operation),
    ])
    if not interrupted:
        transport["queue"].append(response(PNG))

    def observe_before_external_response(request):
        if request.method != "GET":
            return
        saved = disk(runtime)["members"][-1]["execution"]
        if saved["phase"] == "accepted":
            return
        service = runtime.asset_image_service
        result = service.read_request("project", "p", KEY)
        state = service.request_state(result.request)
        wire = asset_image_request_wire(result, state)
        snapshots.append((saved, deepcopy(state), wire))

    transport["on_send"] = observe_before_external_response
    with client_for(runtime, operation) as client:
        accepted = client.post("/projects/p/asset_image_requests", json=body(operation, batch_size=batch_size))
        assert accepted.status_code == 200, accepted.text
        transport["on_send"] = None
        assert not getattr(runtime, "_asset_image_runs", {})
        saved = disk(runtime)
        final = client.get(f"/projects/p/asset_image_requests/{KEY}")
        assert final.status_code == 200, final.text

    assert [entry[0]["phase"] for entry in snapshots] == (
        ["submitted", "observed"] if interrupted else ["submitted", "observed", "observed"])
    assert [entry[0]["observation"]["status"] if entry[0]["observation"] else None
            for entry in snapshots] == ([None, "running"] if interrupted else [None, "running", "succeeded"])
    for execution_record, state, wire in snapshots:
        assert execution_record["schema_version"] == 3
        assert execution_record["handle"]["task_id"] == TASK
        if execution_record["observation"] is not None:
            assert execution_record["observation"]["source_handle"] == execution_record["handle"]
        assert state["status"] == "processing"
        assert state["members"][-1]["status"] == "processing"
        assert state["members"][-1]["reason"] is None
        assert wire["request"]["members"][-1]["status"] == "processing"
        assert wire["request"]["members"][-1]["reason"] is None
        assert "output_ref" not in wire["request"]["members"][-1]
    assert not getattr(runtime, "_asset_image_runs", {})
    execution_record = saved["members"][-1]["execution"]
    assert execution_record["handle"]["task_id"] == TASK
    member = final.json()["request"]["members"][-1]
    assert all(m["status"] == "completed" for m in final.json()["request"]["members"][:-1])
    if interrupted:
        assert execution_record["phase"] == "observed"
        assert execution_record["observation"]["status"] == "running"
        assert execution_record["artifact"] is None and execution_record["prepared_artifact"] is None
        assert member["status"] == "needs_confirmation"
        assert member["reason"] == "provider_image_query_pending"
        assert counts(transport) == (batch_size, batch_size + 1, batch_size - 1)
    else:
        assert execution_record["phase"] == "completed"
        assert execution_record["artifact"] == execution_record["prepared_artifact"]
        assert execution_record["observation"]["status"] == "succeeded"
        assert execution_record["observation"]["source_handle"] == execution_record["handle"]
        assert member["status"] == "completed" and member["reason"] is None
        assert member["output_ref"] == execution_record["output_ref"]
        assert_png(runtime, execution_record)
        assert counts(transport) == (batch_size, batch_size + 1, batch_size)
    assert not transport["queue"]


@pytest.mark.parametrize("operation,terminal", [
    ("wan", "failed"), ("generation", "failed"), ("edit", "failed"), ("wan", "canceled"),
])
def test_real_terminal_observation_never_becomes_processing_with_original_claim(
        runtime, transport, monkeypatch, operation, terminal):
    from src.apps.comic_gen.asset_image_wire import asset_image_request_wire

    setup(runtime, operation)
    terminal_response = (observation(operation, failed=True) if terminal == "failed" else
                         response({"output": {"task_id": TASK, "task_status": "CANCELED"}}))
    transport["queue"].extend([receipt(operation), terminal_response])
    service = runtime.asset_image_service
    save = service._save_async_execution
    snapshots = []

    def observe_real_checkpoint(context, **changes):
        # Observe only after the real storage commit. This wrapper neither
        # creates a claim nor changes the recorded provider response.
        result = save(context, **changes)
        observed = result.observation
        if observed is not None and observed["status"] == terminal:
            saved = disk(runtime)
            request = service.read_request("project", "p", KEY)
            task = service._active_task(request.request)
            state = service.request_state(request.request)
            snapshots.append((saved, deepcopy(task), asset_image_request_wire(request, state)))
        return result

    monkeypatch.setattr(service, "_save_async_execution", observe_real_checkpoint)
    with client_for(runtime, operation) as client:
        ack = client.post("/projects/p/asset_image_requests", json=body(operation))
        assert ack.status_code == 200, ack.text
        assert not getattr(runtime, "_asset_image_runs", {})
        final = client.get(f"/projects/p/asset_image_requests/{KEY}")
        assert final.status_code == 200
    assert len(snapshots) == 1
    saved, task, wire = snapshots[0]
    execution_record = saved["members"][0]["execution"]
    assert task is not None and task["status"] == "processing"
    assert task["_active_attempt_id"] == execution_record["attempt_id"]
    assert execution_record["schema_version"] == 3 and execution_record["phase"] == "observed"
    assert execution_record["handle"]["task_id"] == TASK
    assert execution_record["observation"]["status"] == terminal
    assert execution_record["observation"]["source_handle"] == execution_record["handle"]
    assert execution_record["artifact"] is execution_record["prepared_artifact"] is None
    assert not (runtime.data_dir / execution_record["output_ref"]).exists()
    for value in (wire, final.json()):
        member = value["request"]["members"][0]
        assert member["status"] == "needs_confirmation"
        assert member["reason"] == "provider_image_remote_" + terminal
        assert "output_ref" not in member
    assert disk(runtime) == saved
    assert counts(transport) == (1, 1, 0) and not transport["queue"]
