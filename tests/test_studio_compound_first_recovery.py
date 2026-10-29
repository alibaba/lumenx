"""First compound recovery regression; real old HTTP admission, no invented receipts.

The shared fixture replaces external HTTP/CLI and elapsed waits;
pipeline, asset generator, adapter, persistence, restart and recovery remain real.
"""

from copy import deepcopy
import base64
import hashlib
import struct
import json

import requests

from src.apps.comic_gen.models import AssetUnit, ImageAsset
from tests import test_studio_core_image_admission as admission
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_image_known_handle_recovery as known


runtime = known.runtime
transport = known.transport
waiting_clock = known.waiting_clock
GENERATION = "https://api.mulerouter.ai/vendors/openai/v1/gpt-image-2/generation"
QUERY = f"{GENERATION}/{known.TASK}"
PUBLIC = "/projects/p/assets/generate"


def test_legacy_all_keeps_first_paid_handle_across_restart_without_paying_derived_stages(runtime, transport):
    admission.setup(runtime, "local")
    project = runtime.scripts["p"]
    project.model_settings.t2i_model = "gpt-image-2"
    project.model_settings.i2i_model = "gpt-image-2"
    runtime._commit_storage("projects.json")
    fault = {"query_unavailable": True}

    def provider_response(prepared):
        if prepared.method == "POST":
            posts = [call for call, _ in transport["calls"] if call.method == "POST"]
            assert len(posts) == 1 and prepared.url == GENERATION, "Compound issued another paid stage"
            transport["queue"].append(known.receipt("generation"))
        elif prepared.method == "GET" and prepared.url == QUERY:
            transport["queue"].append(
                requests.ConnectionError("synthetic first-stage query outage")
                if fault["query_unavailable"] else known.observation("generation")
            )
        elif prepared.method == "GET" and prepared.url == known.IMAGE:
            transport["queue"].append(known.response(known.PNG))
        else:
            raise AssertionError(f"Unexpected external request: {prepared.method} {prepared.url}")

    transport["on_send"] = provider_response
    with known.client_for(runtime, "generation") as client:
        response = client.post(PUBLIC, json={
            "asset_id": "c", "asset_type": "character", "generation_type": "all",
            "prompt": "A full-body red-coat character", "apply_style": False,
            "negative_prompt": "", "batch_size": 1,
        })
        assert response.status_code == 200, response.text
        task_id = response.json()["_task_id"]
        assert isinstance(task_id, str) and task_id
        assert client.get(f"/tasks/{task_id}").status_code == 200
        calls = [request for request, _ in transport["calls"]]
        assert any(request.method == "GET" and request.url == QUERY for request in calls), "First paid handle was never queried"
        assert [request.url for request in calls if request.method == "POST"] == [GENERATION]
        assert not transport["cli"] and not transport["queue"]

    fault["query_unavailable"] = False
    with known.reopened(runtime) as other, known.client_for(other, "generation") as client:
        restored = client.get(f"/tasks/{task_id}")
        assert restored.status_code == 200, (
            "Original compound task disappeared after a known provider handle was accepted: " + restored.text
        )
        retained = [
            (record, member)
            for record in other.read_committed_script("p").asset_image_requests
            for member in record.members
            if member.execution.handle is not None and member.execution.handle.get("task_id") == known.TASK
        ]
        assert len(retained) == 1, "Original all request did not retain its actual paid provider receipt"
        record, member = retained[0]
        original = member.execution
        handle, fingerprint = deepcopy(original.handle), original.fingerprint
        assert original.phase == "submitted" and original.schema_version == 3
        service = other.asset_image_service
        assert service.recovery_member_state(record.id, original.attempt_id) == "query"
        queried = service.recover_member_once(record.id, original.attempt_id, action="query")
        assert queried.request.members[0].execution.phase == "observed"
        assert service.recovery_member_state(record.id, original.attempt_id) == "fetch"
        recovered = service.recover_member_once(record.id, original.attempt_id, action="fetch")
        completed = recovered.request.members[0].execution
        assert completed.phase == "completed"
        assert completed.handle == handle and completed.fingerprint == fingerprint
        assert completed.attempt_id == original.attempt_id and completed.output_ref is not None
        still_incomplete = client.get(f"/tasks/{task_id}").json()
        assert still_incomplete["status"] == "needs_confirmation"
        assert still_incomplete["progress"] == 33
        assert (other.data_dir / completed.output_ref).is_file()
        target = other.read_committed_script("p").characters[0]
        assert target.three_view_asset is None or not target.three_view_asset.variants
        assert target.headshot_asset is None or not target.headshot_asset.variants
        calls = [request for request, _ in transport["calls"]]
        assert [request.url for request in calls if request.method == "POST"] == [GENERATION]
        assert all(request.url in {QUERY, known.IMAGE} for request in calls if request.method == "GET")
        assert not transport["queue"] and not transport["cli"]


def test_legacy_all_three_http_stages_keep_success_selection_and_reopened_task(runtime, transport):
    admission.setup(runtime, "local")
    project = runtime.scripts["p"]
    project.model_settings.t2i_model = project.model_settings.i2i_model = "gpt-image-2"
    runtime._commit_storage("projects.json")
    for index, operation in enumerate(("generation", "edit", "edit")):
        task_id = gpt.http_task_id(index)
        transport["queue"].extend([
            known.receipt(operation, task_id), known.observation(operation, task_id=task_id), known.response(known.PNG),
        ])
    with known.client_for(runtime, "generation") as client:
        response = client.post(PUBLIC, json={
            "asset_id": "c", "asset_type": "character", "generation_type": "all",
            "prompt": "A full-body red-coat character", "apply_style": False,
            "negative_prompt": "", "batch_size": 1,
        })
        assert response.status_code == 200, response.text
        task_id = response.json()["_task_id"]
        saved = runtime.read_committed_script("p")
        target = saved.characters[0]
        records = saved.asset_image_requests
        assert [record.channel for record in records] == ["full_body", "three_view", "headshot"]
        assert all(record.members[0].execution.phase == "completed" for record in records)
        assert target.is_consistent
        for record, field, legacy_field in zip(
            records, ("full_body", "three_views", "head_shot"),
            ("full_body_asset", "three_view_asset", "headshot_asset"),
        ):
            assert getattr(target, field).selected_image_id == record.members[0].variant_id
            assert getattr(target, legacy_field).selected_id == record.members[0].variant_id
        posts = [request for request, _ in transport["calls"] if request.method == "POST"]
        assert [request.url for request in posts] == [GENERATION, GENERATION.replace("generation", "edit"), GENERATION.replace("generation", "edit")]
        bodies = [json.loads(request.body) for request in posts]
        assert "A full-body red-coat character" in bodies[0]["prompt"]
        assert "Front view, Side view, and Back view" in bodies[1]["prompt"]
        assert "Close-up portrait" in bodies[2]["prompt"]
        first = records[0].members[0].execution
        assert first.output_ref is not None and first.artifact is not None
        selected_bytes = (runtime.data_dir / first.output_ref).read_bytes()
        assert hashlib.sha256(selected_bytes).hexdigest() == first.artifact['sha256']
        assert bodies[1]["images"] == bodies[2]["images"] == ["data:image/png;base64," + base64.b64encode(selected_bytes).decode()]
        assert client.get(f"/tasks/{task_id}").json()["status"] == "completed"
        assert client.get(f"/tasks/{task_id}").json()["progress"] == 100
        assert not transport["queue"] and not transport["cli"]
    calls_before = len(transport["calls"])
    with known.reopened(runtime) as other, known.client_for(other, "generation") as client:
        assert client.get(f"/tasks/{task_id}").json()["status"] == "completed"
        other.process_asset_generation_task(task_id)
        assert len(transport["calls"]) == calls_before


def test_legacy_all_later_unknown_stage_preserves_candidates_and_previous_selection(runtime, transport):
    admission.setup(runtime, "local")
    project = runtime.scripts["p"]
    project.model_settings.t2i_model = project.model_settings.i2i_model = "gpt-image-2"
    target = project.characters[0]
    variant = {"id": "old-full", "url": "uploads/old-full.png", "created_at": 1}
    source = runtime.data_dir / "uploads/old-full.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(known.PNG)
    target.full_body = AssetUnit.model_validate({"image_variants": [variant], "selected_image_id": "old-full"})
    target.full_body_asset = ImageAsset.model_validate({"variants": [variant], "selected_id": "old-full"})
    target.full_body_image_url = target.image_url = variant["url"]
    runtime._commit_storage("projects.json")
    transport["queue"].extend([
        known.receipt("generation", gpt.http_task_id(0)),
        known.observation("generation", task_id=gpt.http_task_id(0)), known.response(known.PNG),
        known.receipt("edit", gpt.http_task_id(1)),
    ])

    captured = {}

    def fail_second_query(prepared):
        if prepared.method == "GET" and prepared.url.endswith(gpt.http_task_id(1)):
            child = runtime.read_committed_script("p").asset_image_requests[1]
            captured.setdefault("input_guard", child.input_guard)
            captured.setdefault("request_fingerprint", child.request_fingerprint)
            captured.setdefault("execution", child.members[0].execution.model_dump(mode="json"))
            transport["queue"].append(requests.ConnectionError("synthetic derived-stage query outage"))

    transport["on_send"] = fail_second_query
    with known.client_for(runtime, "generation") as client:
        response = client.post(PUBLIC, json={
            "asset_id": "c", "asset_type": "character", "generation_type": "all",
            "prompt": "A full-body red-coat character", "apply_style": False,
            "negative_prompt": "", "batch_size": 1,
        })
        assert response.status_code == 200, response.text
        task_id = response.json()["_task_id"]
        task = client.get("/tasks/" + task_id).json()
        assert task["status"] == "needs_confirmation"
        saved = runtime.read_committed_script("p")
        target = saved.characters[0]
        assert len(saved.asset_image_requests) == 2
        child = saved.asset_image_requests[1]
        assert child.request_fingerprint == captured["request_fingerprint"]
        assert child.members[0].execution.model_dump(mode="json") == captured["execution"]
        handle = child.members[0].execution.handle
        assert handle is not None and handle["task_id"] == gpt.http_task_id(1)
        assert target.full_body_asset is not None and target.full_body is not None
        assert target.full_body_asset.selected_id == target.full_body.selected_image_id == "old-full"
        assert target.full_body_image_url == target.image_url == variant["url"]
        assert len(target.full_body_asset.variants) == 2
        assert target.headshot_asset is None or not target.headshot_asset.variants
        assert len([request for request, _ in transport["calls"] if request.method == "POST"]) == 2
        assert not transport["queue"] and not transport["cli"]

    def recovered_second_result(prepared):
        if prepared.method == "GET" and prepared.url == GENERATION.replace("generation", "edit") + "/" + gpt.http_task_id(1):
            transport["queue"].append(known.observation("edit", task_id=gpt.http_task_id(1)))
        elif prepared.method == "GET" and prepared.url == known.IMAGE:
            transport["queue"].append(known.response(known.PNG))
        else:
            raise AssertionError(f"Unexpected recovery request: {prepared.method} {prepared.url}")

    transport["on_send"] = recovered_second_result
    with known.reopened(runtime) as other, known.client_for(other, "generation") as client:
        retained = other.read_committed_script("p").asset_image_requests[1]
        original = retained.members[0].execution
        service = other.asset_image_service
        assert service.recovery_member_state(retained.id, original.attempt_id) == "query"
        queried = service.recover_member_once(retained.id, original.attempt_id, action="query")
        assert queried.request.members[0].execution.phase == "observed"
        assert service.recovery_member_state(retained.id, original.attempt_id) == "fetch"
        fetched = service.recover_member_once(retained.id, original.attempt_id, action="fetch")
        recovered = fetched.request.members[0].execution
        assert recovered.phase == "completed"
        assert fetched.request.input_guard == child.input_guard
        assert fetched.request.request_fingerprint == child.request_fingerprint
        for field in ("handle", "attempt_id", "fingerprint", "scope", "credential_ref", "intent", "media", "output_ref"):
            assert getattr(recovered, field) == captured["execution"][field]
        assert recovered.output_ref is not None and recovered.artifact is not None
        output = (other.data_dir / recovered.output_ref).read_bytes()
        assert hashlib.sha256(output).hexdigest() == recovered.artifact['sha256']
        assert output.startswith(b'\x89PNG\r\n\x1a\n') and struct.unpack('>II', output[16:24]) == (16, 16)
        saved = other.read_committed_script("p")
        target = saved.characters[0]
        assert target.full_body_asset is not None and target.full_body is not None
        assert target.full_body_asset.selected_id == target.full_body.selected_image_id == "old-full"
        assert target.full_body_image_url == target.image_url == variant["url"]
        assert target.three_view_asset is not None and target.three_views is not None
        assert target.three_view_asset.selected_id is None and target.three_views.selected_image_id is None
        assert [value.id for value in target.three_view_asset.variants] == [original.attempt_id]
        assert [value.id for value in target.three_views.image_variants] == [original.attempt_id]
        assert target.headshot_asset is None or not target.headshot_asset.variants
        assert len(saved.asset_image_requests) == 2
        task = client.get("/tasks/" + task_id).json()
        assert task["status"] == "needs_confirmation" and task["progress"] == 66
        assert len([request for request, _ in transport["calls"] if request.method == "POST"]) == 2
        assert not transport["queue"] and not transport["cli"]


def test_compound_query_outage_does_not_adopt_author_edits_into_its_rollback_guard(runtime, transport):
    admission.setup(runtime, "local")
    project = runtime.scripts["p"]
    project.model_settings.t2i_model = project.model_settings.i2i_model = "gpt-image-2"
    runtime._commit_storage("projects.json")
    transport["queue"].extend([
        known.receipt("generation", gpt.http_task_id(0)),
        known.observation("generation", task_id=gpt.http_task_id(0)), known.response(known.PNG),
        known.receipt("edit", gpt.http_task_id(1)),
    ])
    authored = {}

    def edit_neighbor_during_query(prepared):
        if prepared.method == "GET" and prepared.url.endswith(gpt.http_task_id(1)):
            if not authored:
                with runtime._save_lock:
                    target = runtime.scripts["p"].characters[0]
                    target.voice_id = "new-author-voice"
                    runtime._commit_storage("projects.json")
                    authored.update(target=target.model_dump(mode="json"),
                                    input_guard=runtime.read_committed_script("p").asset_image_requests[1].input_guard)
            transport["queue"].append(requests.ConnectionError("synthetic derived-stage query outage"))

    transport["on_send"] = edit_neighbor_during_query
    with known.client_for(runtime, "generation") as client:
        response = client.post(PUBLIC, json={
            "asset_id": "c", "asset_type": "character", "generation_type": "all",
            "prompt": "A full-body red-coat character", "apply_style": False,
            "negative_prompt": "", "batch_size": 1,
        })
        assert response.status_code == 200, response.text
        saved = runtime.read_committed_script("p")
        target = saved.characters[0]
        assert authored and target.model_dump(mode="json") == authored["target"]
        # An unrelated author edit cannot become permission to reset the
        # full-body selection or rebase a child's publication precondition.
        assert target.full_body_asset is not None
        assert target.full_body_asset.selected_id == saved.asset_image_requests[0].members[0].variant_id
        assert saved.asset_image_requests[1].input_guard == authored["input_guard"]
        assert saved.asset_image_requests[1].members[0].execution.phase == "submitted"
        assert client.get("/tasks/" + response.json()["_task_id"]).json()["status"] == "needs_confirmation"
        assert len([request for request, _ in transport["calls"] if request.method == "POST"]) == 2
        assert not transport["queue"] and not transport["cli"]
