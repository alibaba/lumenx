"""The public asset batch retains compound receipts and its original membership."""

from copy import deepcopy
import hashlib
import json

import requests

from src.apps.comic_gen.models import Character
from tests import test_studio_core_image_admission as admission
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_image_known_handle_recovery as known

runtime = known.runtime
transport = known.transport
waiting_clock = known.waiting_clock
BASE = "https://api.mulerouter.ai/vendors/openai/v1/gpt-image-2/"


def test_public_bulk_characters_keep_completed_asset_and_recover_only_interrupted_handle(runtime, transport, monkeypatch):
    admission.setup(runtime, "local")
    project = runtime.scripts["p"]
    project.model_settings.t2i_model = project.model_settings.i2i_model = "gpt-image-2"
    project.characters.extend([
        Character.model_validate({"id": "second", "name": "Second", "description": "blue coat"}),
        Character.model_validate({"id": "third", "name": "Third", "description": "green coat"}),
    ])
    runtime._commit_storage("projects.json")
    third_before = project.characters[2].model_dump(mode="json")
    original_prepare = runtime._prepare_asset_image
    prepared = []

    def capture(*args, **kwargs):
        context = original_prepare(*args, **kwargs)
        prepared.append((context["asset_id"], context["params"]["generation_type"]))
        return context

    monkeypatch.setattr(runtime, "_prepare_asset_image", capture)
    steps = [(operation, gpt.http_task_id(index))
             for index, operation in enumerate(("generation", "edit", "edit", "generation"))]
    queries = {BASE + operation + "/" + task_id: (operation, task_id) for operation, task_id in steps}
    fault = {"query_unavailable": True}
    first_saved = {}
    late_saved = {}

    def provider_response(request):
        if request.method == "POST":
            index = len([call for call, _ in transport["calls"] if call.method == "POST"]) - 1
            assert index < len(steps), "Batch paid for a stage after its interrupted member"
            operation, task_id = steps[index]
            assert request.url == BASE + operation
            if index == 0:
                assert prepared == [("c", "all"), ("second", "all"), ("third", "all")]
                # A newly authored member is not part of this accepted batch.
                with runtime._save_lock:
                    late = Character.model_validate({"id": "late", "name": "Late", "description": "new member"})
                    runtime.scripts["p"].characters.append(late)
                    runtime._commit_storage("projects.json")
                    late_saved.update(late.model_dump(mode="json"))
            elif index == 3:
                first = runtime.read_committed_script("p").characters[0]
                assert first.is_consistent and first.status.value == "completed"
                first_saved.update(first.model_dump(mode="json"))
                assert "Second" in json.loads(request.body)["prompt"]
                assert "blue coat" in json.loads(request.body)["prompt"]
            transport["queue"].append(known.receipt(operation, task_id))
        elif request.method == "GET" and request.url in queries:
            operation, task_id = queries[request.url]
            transport["queue"].append(
                requests.ConnectionError("synthetic second-character query outage")
                if task_id == steps[3][1] and fault["query_unavailable"] else
                known.observation(operation, task_id=task_id)
            )
        elif request.method == "GET" and request.url == known.IMAGE:
            transport["queue"].append(known.response(known.PNG))
        else:
            raise AssertionError(f"Unexpected external request: {request.method} {request.url}")

    transport["on_send"] = provider_response
    with known.client_for(runtime, "generation") as client:
        response = client.post("/projects/p/generate_assets")
        assert response.status_code == 502, response.text
        saved = runtime.read_committed_script("p")
        records = saved.asset_image_requests
        assert [(row.asset_id, row.channel) for row in records] == [
            ("c", "full_body"), ("c", "three_view"), ("c", "headshot"), ("second", "full_body"),
        ]
        assert {row.request_root_scope_id for row in records} == {saved.asset_image_request_scope_id}
        assert all(row.members[0].execution.phase == "completed" for row in records[:3])
        assert records[0].character_all is not None and records[0].character_all.completed
        assert records[3].character_all is not None and not records[3].character_all.completed
        assert records[3].members[0].execution.phase == "submitted"
        assert saved.characters[0].model_dump(mode="json") == first_saved
        assert saved.characters[2].model_dump(mode="json") == third_before
        assert saved.characters[3].model_dump(mode="json") == late_saved
        assert prepared == [("c", "all"), ("second", "all"), ("third", "all"),
                            ("c", "three_view"), ("c", "headshot")]
        original = records[3].members[0].execution.model_copy(deep=True)
        parent_id = records[3].id
        handle = deepcopy(original.handle)
        assert handle is not None and handle["task_id"] == steps[3][1]
        assert len([call for call, _ in transport["calls"] if call.method == "POST"]) == 4

    fault["query_unavailable"] = False
    with known.reopened(runtime) as other, known.client_for(other, "generation") as client:
        service = other.asset_image_service
        assert service.recovery_member_state(parent_id, original.attempt_id) == "query"
        service.recover_member_once(parent_id, original.attempt_id, action="query")
        recovered = service.recover_member_once(parent_id, original.attempt_id, action="fetch")
        completed = recovered.request.members[0].execution
        assert completed.phase == "completed" and completed.handle == handle
        assert completed.attempt_id == original.attempt_id and completed.fingerprint == original.fingerprint
        assert completed.output_ref is not None and completed.artifact is not None
        assert hashlib.sha256((other.data_dir / completed.output_ref).read_bytes()).hexdigest() == completed.artifact["sha256"]
        saved = other.read_committed_script("p")
        assert len(saved.asset_image_requests) == 4
        assert saved.characters[0].model_dump(mode="json") == first_saved
        assert saved.characters[2].model_dump(mode="json") == third_before
        assert saved.characters[3].model_dump(mode="json") == late_saved
        second = saved.characters[1]
        assert second.full_body_asset is not None and second.full_body_asset.selected_id is None
        assert second.full_body is not None and second.full_body.selected_image_id is None
        assert [variant.id for variant in second.full_body_asset.variants] == [original.attempt_id]
        for channel in (second.three_view_asset, second.headshot_asset):
            assert channel is None or (not channel.variants and channel.selected_id is None)
        status = client.get("/tasks/" + parent_id)
        assert status.status_code == 200 and status.json()["status"] == "needs_confirmation"
        assert status.json()["progress"] == 33
        other.process_asset_generation_task(parent_id)
        assert len([call for call, _ in transport["calls"] if call.method == "POST"]) == 4
        assert not transport["queue"] and not transport["cli"]
