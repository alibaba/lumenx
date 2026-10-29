"""Full-body motion uses real HTTP, scoped Core and durable original receipts.

Only the external HTTP transport is replaced. Video bytes are a transport
fixture, not a claim of media decoding or a live provider acceptance run.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import threading
import zipfile

import httpx
import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import Script, Series, VideoTask
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.provider_recovery_dispatcher import StudioProviderRecoveryDispatcher
from src.models import wanx
from src.utils.json_storage import StorageError
from tests.frozen_image_fixtures import rgb_png_bytes


SUBMIT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
TASK = "https://dashscope.aliyuncs.com/api/v1/tasks/motion-original"
RESULT = "https://media.invalid/motion-durable.mp4"
VIDEO = b"synthetic motion transport artifact; not decoded video"
COMMAND = {
    "asset_id": "hero", "asset_type": "full_body", "prompt": "One slow rotation",
    "duration": 10, "batch_size": 1, "client_request_id": "motion-request",
}
PATH = "/projects/project/assets/generate_motion_ref"


@pytest.fixture
def transport(monkeypatch, tmp_path):
    state = {"calls": [], "lose_submit": False, "status": "SUCCEEDED", "upload_bodies": []}

    def send(_session, request, **_kwargs):
        body = json.loads(request.body) if request.body and request.headers.get("Content-Type", "").startswith("application/json") else None
        state["calls"].append((request.method, request.url, body, dict(request.headers)))
        response = requests.Response()
        response.status_code = 200
        response.url, response.request = request.url, request
        if request.method == "GET" and request.url.startswith("https://dashscope.aliyuncs.com/api/v1/uploads?"):
            response._content = json.dumps({"output": {
                "upload_host": "https://upload.invalid/motion-reference",
                "upload_file_path": "motion-fixture/current.png",
            }}).encode()
        elif request.method == "POST" and request.url == "https://upload.invalid/motion-reference":
            state["upload_bodies"].append(request.body)
            response.status_code, response._content = 204, b""
        elif request.method == "POST" and request.url == SUBMIT:
            if state["lose_submit"]:
                raise requests.ConnectionError("synthetic lost submit acknowledgement")
            if state.get("on_submit"):
                state["on_submit"]()
            response._content = json.dumps({"output": {"task_id": "motion-original"}}).encode()
        elif request.method == "GET" and request.url == TASK:
            if state.get("on_query"):
                state["on_query"]()
            response._content = json.dumps({"output": {
                "task_status": state["status"], "video_url": RESULT,
            }}).encode()
        elif request.method == "GET" and request.url == RESULT:
            if state.get("on_fetch"):
                state["on_fetch"]()
            response._content = VIDEO
            response.headers["Content-Type"] = "video/mp4"
            response.headers["Content-Length"] = str(len(VIDEO))
        else:
            raise AssertionError(f"Unexpected external transport: {request.method} {request.url}")
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr(wanx.time, "sleep", lambda _: None)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-motion-secret")
    monkeypatch.setenv("NETRC", str(tmp_path / "absent-netrc"))
    return state


@pytest.fixture
def runtime(tmp_path, transport):
    item = ComicGenPipeline(data_dir=tmp_path / "output")
    image = rgb_png_bytes("selected-motion-frame")
    source = "data:image/png;base64," + base64.b64encode(image).decode()
    item.scripts["project"] = Script.model_validate({
        "id": "project", "title": "Motion durability", "original_text": "Synthetic",
        "created_at": 1, "updated_at": 1,
        "characters": [{
            "id": "hero", "name": "Hero", "description": "Red coat",
            "full_body_image_url": "https://media.invalid/unselected.png",
            "full_body": {
                "image_variants": [{"id": "selected-image", "url": source}],
                "selected_image_id": "selected-image",
                "video_variants": [{"id": "selected-video", "url": "video/history.mp4"}],
                "selected_video_id": "selected-video",
            },
        }],
    })
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    try:
        yield item, image
    finally:
        item.close()


def app_for(item):
    return api.create_app(
        data_dir=item.data_dir, user_data_dir=item.data_dir.parent / "user",
        load_config=False, pipeline_instance=item,
    )


def test_real_motion_http_creates_durable_scoped_task_and_preserves_wire(runtime, transport):
    item, image = runtime
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        task_id = response.json()["_task_id"]
        assert len(item.scripts["project"].video_tasks) == 1
        task = item.scripts["project"].video_tasks[0]
        assert task.id == task_id and task.frame_id is None and task.asset_id == "hero"
        assert task.provider_execution.phase == "completed"
        assert task.provider_execution.prepared_artifact == task.provider_execution.artifact
        assert task.provider_execution.media[0]["sha256"] == hashlib.sha256(image).hexdigest()
        assert (item.data_dir / task.video_url).read_bytes() == VIDEO
        assert task.provider_execution.scope["base_url"] == "https://dashscope.aliyuncs.com"
        assert task.provider_execution.credential_ref
        assert task.provider_execution.intent["params"]["resolution"] == "720p"
        status = client.get("/tasks/" + task_id)
        assert status.status_code == 200 and status.json()["status"] == "completed"
        target = item.scripts["project"].characters[0]
        assert target.full_body.selected_video_id == "selected-video"
        assert len(target.full_body.video_variants) == 2
        assert target.full_body.video_variants[-1].source_image_id == "selected-image"
        assert response.json()["_client_request_id"] == COMMAND["client_request_id"]
    assert [call[0] for call in transport["calls"]] == ["POST", "GET", "GET"]
    body = transport["calls"][0][2]
    assert body["model"] == "wan2.7-i2v"
    assert body["input"] == {"prompt": "One slow rotation", "media": [{
        "type": "first_frame", "url": "data:image/png;base64," + base64.b64encode(image).decode(),
    }]}
    assert body["parameters"] == {
        "duration": 10, "resolution": "720P", "prompt_extend": True, "watermark": False,
    }


def test_original_motion_key_reads_and_replays_without_credentials_or_new_submit(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        first = client.post(PATH, json=COMMAND)
        assert first.status_code == 200, first.text
        task_id = first.json()["_task_id"]
        calls = list(transport["calls"])
        before = {path.name: path.read_bytes() for path in item.data_dir.glob("*.json")}
        cleared = client.post("/config/env", json={"DASHSCOPE_API_KEY": ""})
        assert cleared.status_code == 200, cleared.text
        read = client.get("/projects/project/motion_ref_requests/motion-request")
        assert read.status_code == 200, read.text
        assert read.json()["task_id"] == task_id
        replay = client.post(PATH, json=COMMAND)
        assert replay.status_code == 200, replay.text
        assert replay.json()["_task_id"] == task_id and replay.json()["_replayed"] is True
        conflict = client.post(PATH, json={**COMMAND, "duration": 15})
        assert conflict.status_code == 409, conflict.text
        assert before == {path.name: path.read_bytes() for path in item.data_dir.glob("*.json")}
        assert transport["calls"] == calls


def test_lost_submit_ack_is_durable_unknown_and_same_key_never_retries(runtime, transport):
    item, _ = runtime
    transport["lose_submit"] = True
    with TestClient(app_for(item)) as client:
        first = client.post(PATH, json=COMMAND)
        assert first.status_code == 200, first.text
        status = client.get("/tasks/" + first.json()["_task_id"])
        assert status.json()["status"] == "needs_confirmation"
        record = item.scripts["project"].video_tasks[0].provider_execution
        assert record.phase == "submitting" and record.handle is None
        assert client.post(PATH, json=COMMAND).json()["_task_id"] == first.json()["_task_id"]
        assert client.get("/projects/project/motion_ref_requests/motion-request").status_code == 200
    assert [call[0] for call in transport["calls"]] == ["POST"]


def move_owner(item, route):
    target = item.scripts["project"].characters[0]
    if route == "script":
        return target
    item.scripts["project"].characters.clear()
    if route == "series":
        item.series_store["series"] = Series.model_validate({
            "id": "series", "title": "Inherited", "created_at": 1, "updated_at": 1,
            "episode_ids": ["project"], "characters": [target],
        })
        item.scripts["project"].series_id = "series"
        target = item.series_store["series"].characters[0]
    else:
        item.library_store.characters.append(target)
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    return target


@pytest.mark.parametrize("route", ["script", "series", "global"])
@pytest.mark.parametrize("source_type", ["inline", "local"])
def test_motion_execution_and_projection_use_actual_asset_owner(runtime, transport, route, source_type):
    item, image = runtime
    target = move_owner(item, route)
    if source_type == "local":
        source = item.data_dir / "uploads/full-body.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(image)
        target.full_body.image_variants[0].url = "uploads/full-body.png"
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    original = target.full_body.video_variants[0].model_dump(mode="json")
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        task = item.scripts["project"].video_tasks[0]
        assert task.provider_execution.phase == "completed"
        assert (task.motion_reference.owner_kind, task.motion_reference.owner_id) == (
            route, {"script": "project", "series": "series", "global": "library"}[route],
        )
        assert target.full_body_motion_request_id == task.id
        assert target.full_body.video_variants[0].model_dump(mode="json") == original
        assert target.full_body.selected_video_id == "selected-video"
        assert len(target.full_body.video_variants) == 2
        assert target.full_body.video_variants[-1].id == task.id
        assert not item.video_generation_tasks
        receipt = item.scripts["project"].provider_request_receipts[0]
        assert receipt.entrypoint == "studio.motion_reference.v1" and "frame_id" not in receipt.model_dump()
        assert [entry[0] for entry in transport["calls"]] == ["POST", "GET", "GET"]


def test_new_app_recovers_only_original_scoped_query_and_fetch(runtime, transport):
    item, image = runtime
    target = move_owner(item, "series")
    local = item.data_dir / "uploads/current.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(image)
    target.full_body.image_variants[0].url = "uploads/current.png"
    item._commit_storage("projects.json", "series.json")
    transport["status"] = "RUNNING"
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        task_id = response.json()["_task_id"]
        execution = item.scripts["project"].video_tasks[0].provider_execution.model_dump(mode="json")
        assert execution["handle"] is not None
    item.close()
    # Later source bytes and current product configuration cannot alter the paid input.
    local.write_bytes(rgb_png_bytes("replacement-after-submit"))
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True,
                               config={"video": {"model": {"params": {"i2v_model_name": "unsupported-new-default"}}}})
    try:
        transport["status"] = "SUCCEEDED"
        with TestClient(app_for(reopened)) as client:
            dispatcher = StudioProviderRecoveryDispatcher(reopened, transient_backoff_seconds=0)
            assert dispatcher.run_once() == ("queried", "project", task_id)
            assert dispatcher.run_once() == ("fetched", "project", task_id)
            assert dispatcher.run_once() is None
            task = reopened.scripts["project"].video_tasks[0]
            assert task.provider_execution is not None
            assert task.provider_execution.phase == "completed"
            assert task.provider_execution.fingerprint == execution["fingerprint"]
            assert task.provider_execution.credential_ref == execution["credential_ref"]
            assert task.provider_execution.media == execution["media"]
            assert task.motion_reference is not None
            assert task.motion_reference.owner_kind == "series"
            assert reopened.series_store["series"].characters[0].full_body is not None
            assert len(reopened.series_store["series"].characters[0].full_body.video_variants) == 2
            assert client.get("/tasks/" + task_id).json()["status"] == "completed"
            assert client.post(PATH, json=COMMAND).json()["_replayed"] is True
        assert [call[0] for call in transport["calls"]] == ["POST", "GET", "GET", "GET"]
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["selection", "video_selection", "description", "owner", "pointer", "deleted"])
def test_late_paid_bytes_survive_without_overwriting_changed_asset(runtime, transport, change):
    item, _ = runtime
    target = item.scripts["project"].characters[0]
    original = target.full_body.video_variants[0].model_dump(mode="json")

    def change_target():
        if change == "selection":
            target.full_body.selected_image_id = None
        elif change == "video_selection":
            target.full_body.selected_video_id = None
        elif change == "description":
            target.description = "New author intent"
        elif change == "pointer":
            target.full_body_motion_request_id = "d4f8a3d8-bf47-4f50-8400-e1ac58729202"
        else:
            item.scripts["project"].characters.clear()
            if change == "owner":
                item.library_store.characters.append(target)
        item._commit_storage("projects.json", "library_assets.json")

    transport["on_fetch"] = change_target
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        task = item.scripts["project"].video_tasks[0]
        assert task.provider_execution.phase == "completed"
        assert task.provider_execution.artifact == task.provider_execution.prepared_artifact
        assert (item.data_dir / task.video_url).read_bytes() == VIDEO
        assert task.status == "needs_confirmation"
        assert task.error.startswith("motion_reference_")
        assert [variant.model_dump(mode="json") for variant in target.full_body.video_variants] == [original]
        assert client.get("/tasks/" + task.id).json()["status"] == "needs_confirmation"
        assert client.post(PATH, json=COMMAND).json()["_replayed"] is True
    assert [call[0] for call in transport["calls"]] == ["POST", "GET", "GET"]


def test_prepared_published_bytes_repair_after_new_app_without_credentials(runtime, transport, monkeypatch):
    item, _ = runtime
    real_commit = item._storage.commit

    def fail_final(payloads):
        tasks = payloads.get("projects.json", {}).get("project", {}).get("video_tasks", [])
        if any(task.get("provider_execution", {}).get("phase") == "completed" for task in tasks):
            raise StorageError("synthetic final commit failure")
        return real_commit(payloads)

    monkeypatch.setattr(item._storage, "commit", fail_final)
    with TestClient(app_for(item), raise_server_exceptions=False) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
    saved = json.loads((item.data_dir / "projects.json").read_text())["project"]["video_tasks"][0]
    execution = saved["provider_execution"]
    assert execution["prepared_artifact"] is not None and execution["artifact"] is None
    assert (item.data_dir / execution["prepared_artifact"]["relative_path"]).read_bytes() == VIDEO
    calls = list(transport["calls"])
    item.close()
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True)
    try:
        with TestClient(app_for(reopened)):
            dispatcher = StudioProviderRecoveryDispatcher(reopened)
            assert dispatcher.run_once() == ("repaired", "project", saved["id"])
            assert dispatcher.run_once() is None
            task = reopened.scripts["project"].video_tasks[0]
            assert task.provider_execution is not None
            assert task.status == "completed" and task.provider_execution.phase == "completed"
            assert reopened.scripts["project"].characters[0].full_body is not None
            assert len(reopened.scripts["project"].characters[0].full_body.video_variants) == 2
            reopened.fetch_provider_video_task("project", saved["id"])
            assert len(reopened.scripts["project"].characters[0].full_body.video_variants) == 2
        assert transport["calls"] == calls
    finally:
        reopened.close()


@pytest.mark.parametrize("duration", [1, 16])
def test_unsupported_motion_duration_is_rejected_before_binding_or_submit(runtime, transport, duration, monkeypatch):
    item, _ = runtime
    binds = []
    original = item._provider_credential_store.bind

    def observe_bind(*args, **kwargs):
        binds.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(item._provider_credential_store, "bind", observe_bind)
    before = {path.name: path.read_bytes() for path in item.data_dir.glob("*.json")}
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json={**COMMAND, "duration": duration})
        assert response.status_code == 422, response.text
    assert binds == [] and transport["calls"] == []
    assert before == {path.name: path.read_bytes() for path in item.data_dir.glob("*.json")}


def test_remote_legacy_uses_current_provider_wire_without_false_durable_receipt(runtime, transport):
    item, _ = runtime
    target = item.scripts["project"].characters[0]
    target.full_body.image_variants[0].url = "https://media.invalid/legacy-reference.png"
    item._commit_storage("projects.json")
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        assert client.get("/tasks/" + response.json()["_task_id"]).json()["status"] == "completed"
        assert item.scripts["project"].provider_request_receipts == []
        assert item.scripts["project"].video_tasks == []
    assert transport["calls"][0][2] == {
        "model": "wan2.7-i2v", "input": {
            "prompt": "One slow rotation", "media": [{"type": "first_frame", "url": "https://media.invalid/legacy-reference.png"}],
        }, "parameters": {"duration": 10, "resolution": "720P", "prompt_extend": True, "watermark": False},
    }


def test_completed_unprojected_request_does_not_permanently_block_explicit_new_action(runtime, transport):
    item, _ = runtime
    target = item.scripts["project"].characters[0]

    def edit_after_submit():
        target.description = "New authored intent"
        item._commit_storage("projects.json")

    transport["on_fetch"] = edit_after_submit
    with TestClient(app_for(item)) as client:
        first = client.post(PATH, json=COMMAND)
        assert first.status_code == 200, first.text
        first_id = first.json()["_task_id"]
        assert item.scripts["project"].video_tasks[0].status == "needs_confirmation"
        transport.pop("on_fetch")
        second = client.post(PATH, json={**COMMAND, "client_request_id": "explicit-new-action"})
        assert second.status_code == 200, second.text
        assert second.json()["_task_id"] != first_id
        assert len(target.full_body.video_variants) == 2
        assert target.full_body.video_variants[-1].id == second.json()["_task_id"]
        assert client.get("/projects/project/motion_ref_requests/motion-request").json()["task"]["provider_execution"]["phase"] == "completed"
    assert len([call for call in transport["calls"] if call[0] == "POST"]) == 2


def test_shared_owner_pending_motion_blocks_a_second_project_without_resubmit(runtime, transport):
    item, _ = runtime
    move_owner(item, "global")
    item.scripts["second"] = Script.model_validate({
        "id": "second", "title": "Other project", "original_text": "S", "created_at": 1, "updated_at": 1,
    })
    item._commit_storage("projects.json")
    transport["status"] = "RUNNING"
    with TestClient(app_for(item)) as client:
        first = client.post(PATH, json=COMMAND)
        assert first.status_code == 200, first.text
        second = client.post("/projects/second/assets/generate_motion_ref", json={
            **COMMAND, "client_request_id": "other-project-action",
        })
        assert second.status_code == 409, second.text
        assert item.scripts["second"].video_tasks == []
        assert item.scripts["second"].provider_request_receipts == []
    assert [call[0] for call in transport["calls"]] == ["POST", "GET"]


def test_mid_submit_credential_change_preserves_original_ref_and_stops_query(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        def change_current_key():
            changed = client.post("/config/env", json={"DASHSCOPE_API_KEY": "synthetic-different-key"})
            assert changed.status_code == 200, changed.text
        transport["on_submit"] = change_current_key
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        task = item.scripts["project"].video_tasks[0]
        original_ref = task.provider_execution.credential_ref
        assert task.provider_execution.handle is not None
        assert task.status == "needs_confirmation" and task.error == "provider_credentials_required"
        assert [call[0] for call in transport["calls"]] == ["POST"]
        assert client.get("/projects/project/motion_ref_requests/motion-request").status_code == 200
        assert client.post(PATH, json=COMMAND).json()["_replayed"] is True
        restored = client.post("/config/env", json={"DASHSCOPE_API_KEY": "synthetic-motion-secret"})
        assert restored.status_code == 200, restored.text
        dispatcher = StudioProviderRecoveryDispatcher(item, transient_backoff_seconds=0)
        # Credentials-required is deliberately not an automatic retry state.
        assert dispatcher.run_once() is None
        item.query_provider_video_task("project", task.id)
        assert dispatcher.run_once() == ("fetched", "project", task.id)
        assert task.provider_execution.credential_ref == original_ref
    assert [call[0] for call in transport["calls"]] == ["POST", "GET", "GET"]


def test_deleted_motion_task_keeps_original_tombstone_and_bytes(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        task = item.scripts["project"].video_tasks[0]
        item.delete_asset_video("project", "hero", "character", task.id)
        assert (item.data_dir / task.video_url).read_bytes() == VIDEO
        read = client.get("/projects/project/motion_ref_requests/motion-request")
        assert read.json()["task"] is None and read.json()["reason"] == "task_deleted"
        replay = client.post(PATH, json=COMMAND)
        assert replay.json()["_task_id"] == task.id and replay.json()["_reason"] == "task_deleted"
        assert item.scripts["project"].video_tasks == []
    assert [call[0] for call in transport["calls"]] == ["POST", "GET", "GET"]


def test_lost_submit_ack_new_app_only_reads_original_key(runtime, transport):
    item, _ = runtime
    transport["lose_submit"] = True
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 200, response.text
        task_id = response.json()["_task_id"]
    item.close()
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True)
    try:
        with TestClient(app_for(reopened)) as client:
            dispatcher = StudioProviderRecoveryDispatcher(reopened)
            assert dispatcher.run_once() is None
            assert client.get("/tasks/" + task_id).json()["status"] == "needs_confirmation"
            assert client.get("/projects/project/motion_ref_requests/motion-request").json()["task_id"] == task_id
            assert client.post(PATH, json=COMMAND).json()["_task_id"] == task_id
            task = reopened.scripts["project"].video_tasks[0]
            assert task.provider_execution is not None
            forbidden = client.post(f"/projects/project/provider_video_tasks/{task_id}/continue", json={
                "expected_attempt_id": task.provider_execution.attempt_id,
                "expected_fingerprint": task.provider_execution.fingerprint,
            })
            assert forbidden.status_code == 409, forbidden.text
        assert [call[0] for call in transport["calls"]] == ["POST"]
    finally:
        reopened.close()


def test_old_callers_receive_a_durable_generated_key_without_response_shape_break(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        raw = {key: value for key, value in COMMAND.items() if key != "client_request_id"}
        response = client.post(PATH, json=raw)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == "project" and response.json()["characters"][0]["id"] == "hero"
        key = response.json()["_client_request_id"]
        assert key and response.json()["_replayed"] is False
        assert client.get("/projects/project/motion_ref_requests/" + key).json()["task_id"] == response.json()["_task_id"]


@pytest.mark.parametrize("bad_ref", ["uploads/missing.png", "uploads/../outside.png"])
def test_declared_local_failure_never_falls_back_to_legacy_paid_route(runtime, transport, bad_ref):
    item, _ = runtime
    item.scripts["project"].characters[0].full_body.image_variants[0].url = bad_ref
    item._commit_storage("projects.json")
    before = {path.name: path.read_bytes() for path in item.data_dir.glob("*.json")}
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json=COMMAND)
        assert response.status_code == 422, response.text
    assert before == {path.name: path.read_bytes() for path in item.data_dir.glob("*.json")}
    assert transport["calls"] == [] and item.video_generation_tasks == {}


@pytest.mark.parametrize("channel", ["full_body", "head_shot"])
def test_package_and_asset_copies_keep_video_history_but_no_motion_execution_right(runtime, transport, channel):
    item, image = runtime
    target = item.scripts["project"].characters[0]
    assert target.full_body is not None
    source = item.data_dir / "uploads/full-body.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(image)
    history = item.data_dir / "video/history.mp4"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b"synthetic existing history")
    target.full_body.image_variants[0].url = "uploads/full-body.png"
    target.full_body_image_url = "uploads/full-body.png"
    if channel == "head_shot":
        target.head_shot = target.full_body.model_copy(deep=True)
        target.headshot_image_url = "uploads/full-body.png"
    unit = getattr(target, channel)
    pointer = f"{channel}_motion_request_id"
    item._commit_storage("projects.json")
    with TestClient(app_for(item)) as client:
        response = client.post(PATH, json={**COMMAND, "asset_type": channel})
        assert response.status_code == 200, response.text
        original = item.read_committed_script("project").model_dump(mode="json")
        exported = client.get("/projects/project/package/export")
        assert exported.status_code == 200, exported.text
        imported = client.post("/projects/package/import", content=exported.content,
                               headers={"content-type": "application/zip"})
        assert imported.status_code == 200, imported.text
        copy = imported.json()
        assert copy["id"] != "project" and copy["provider_request_receipts"] == []
        assert copy["characters"][0][pointer] is None
        assert copy["video_tasks"][0]["provider_execution"] is None
        assert copy["video_tasks"][0]["motion_reference"] is None
        assert len(copy["characters"][0][channel]["video_variants"]) == 2
        assert (item.data_dir / copy["video_tasks"][0]["video_url"]).read_bytes() == VIDEO
        promoted = item.promote_asset_to_library("project", "project", "character", "hero")
        assert getattr(promoted, pointer) is None
        assert getattr(promoted, channel).video_variants == unit.video_variants
        fork = item.fork_library_asset_to_project(copy["id"], "character", promoted.id)
        assert getattr(fork, pointer) is None
        assert getattr(fork, channel).video_variants == unit.video_variants
        assert item.read_committed_script("project").model_dump(mode="json") == original
    assert [call[0] for call in transport["calls"]] == ["POST", "GET", "GET"]


def managed_cross_branch_source(item, image):
    """The identical selected bytes are usable by Core and the real legacy uploader."""
    source = item.data_dir / "uploads/cross-branch.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(image)
    item.scripts["project"].characters[0].full_body.image_variants[0].url = "uploads/cross-branch.png"
    item._commit_storage("projects.json")


@pytest.mark.parametrize("legacy_options", [
    {"batch_size": 2},
    {"audio_url": "https://media.invalid/driving.wav"},
], ids=["batch", "driving-audio"])
def test_pending_durable_motion_blocks_legacy_branch_at_same_owner(runtime, transport, legacy_options):
    item, image = runtime
    managed_cross_branch_source(item, image)
    transport["status"] = "RUNNING"
    with TestClient(app_for(item)) as client:
        first = client.post(PATH, json=COMMAND)
        assert first.status_code == 200, first.text
        task = item.scripts["project"].video_tasks[0]
        assert task.status == "processing" and task.provider_execution.handle is not None
        original = task.provider_execution.model_dump(mode="json")
        # The first record stays pending; a second legacy POST would complete
        # deterministically, making a duplicate paid submission count observable.
        transport["status"] = "SUCCEEDED"
        second = client.post(PATH, json={
            **COMMAND, **legacy_options, "client_request_id": "legacy-branch-action",
        })
        submissions = [call[2] for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]
        if len(submissions) > 1:
            # Diagnose a genuine provider POST, not a legacy media setup failure.
            assert image in transport["upload_bodies"][0]
            assert submissions[1]["input"]["img_url"] == "oss://motion-fixture/current.png"
            assert submissions[1]["parameters"]["audio"] is True
            assert submissions[1]["parameters"]["shot_type"] == "single"
        assert len(submissions) == 1, (second.status_code, submissions)
        assert second.status_code == 409, second.text
        assert task.provider_execution.model_dump(mode="json") == original
        assert item.video_generation_tasks == {}


def test_pending_legacy_motion_already_blocks_durable_branch_at_same_owner(runtime, transport):
    item, image = runtime
    managed_cross_branch_source(item, image)
    started, release = threading.Event(), threading.Event()

    def hold_first_legacy_submit():
        if not started.is_set():
            started.set()
            assert release.wait(timeout=10), "Test did not release its own synthetic HTTP transport"

    transport["on_submit"] = hold_first_legacy_submit
    with TestClient(app_for(item)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, PATH, json={
                **COMMAND, "batch_size": 2, "client_request_id": "legacy-first",
            })
            try:
                assert started.wait(timeout=5), "Legacy request did not reach external HTTP transport"
                second = client.post(PATH, json={**COMMAND, "client_request_id": "durable-second"})
                assert second.status_code == 409, second.text
                submissions = [call for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]
                assert len(submissions) == 1
                assert item.scripts["project"].video_tasks == []
                assert item.scripts["project"].provider_request_receipts == []
            finally:
                release.set()
            response = first.result(timeout=10)
            assert response.status_code == 200, response.text
            assert client.get("/tasks/" + response.json()["_task_id"]).json()["status"] == "completed"
    submissions = [call[2] for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]
    assert len(submissions) == 2  # The explicitly requested legacy batch, with no Core submission.
    assert all(body["input"]["media"] == [{"type": "first_frame", "url": "oss://motion-fixture/current.png"}] for body in submissions)
    assert image in transport["upload_bodies"][0]


def real_extraction_transport(monkeypatch, on_request=None):
    """Keep ScriptProcessor and OpenAI client real; replace only external HTTP."""
    monkeypatch.setattr("platform.platform", lambda: "test-platform")
    monkeypatch.setattr("platform.processor", lambda: "test-cpu")
    from openai import _base_client

    sdk_http = getattr(_base_client, "httpx2", httpx)
    calls = []

    def handle_request(_transport, request):
        assert request.method == "POST"
        assert str(request.url) == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        calls.append(json.loads(request.content))
        if on_request is not None:
            on_request()
        return sdk_http.Response(200, request=request, json={
            "id": "synthetic-extraction", "object": "chat.completion", "created": 1,
            "model": "qwen3.6-plus", "choices": [{
                "index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps({"characters": [], "scenes": [], "props": [], "frames": []}),
                },
            }], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(sdk_http.HTTPTransport, "handle_request", handle_request)
    return calls


def test_reparse_rejects_while_original_motion_submit_awaits_handle(runtime, transport, monkeypatch):
    item, _ = runtime
    extraction_calls = real_extraction_transport(monkeypatch)
    transport["status"] = "RUNNING"
    submitted, release = threading.Event(), threading.Event()

    def hold_external_submit():
        submitted.set()
        assert release.wait(timeout=10), "Test did not release its own provider transport"

    transport["on_submit"] = hold_external_submit
    with TestClient(app_for(item)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, PATH, json=COMMAND)
            try:
                assert submitted.wait(timeout=5), "Motion did not reach provider POST"
                original_script = item.scripts["project"]
                original_task = original_script.video_tasks[0]
                assert original_task.provider_execution.phase == "submitting"
                assert original_task.provider_execution.handle is None
                assert ("project", original_task.id) in item._video_task_runs
                revision = client.get("/projects/project").json()["revision"]
                before = (item.data_dir / "projects.json").read_bytes()
                reparse = client.put("/projects/project/reparse", json={
                    "text": "A new story while the first receipt is in flight",
                    "expected_revision": revision,
                })
                unchanged = item.scripts["project"] is original_script
                saved_unchanged = (item.data_dir / "projects.json").read_bytes() == before
            finally:
                release.set()
            response = first.result(timeout=10)
        assert reparse.status_code == 409, reparse.text
        assert unchanged and saved_unchanged
        assert extraction_calls == []  # Reject before spending an extraction request.
        assert response.status_code == 200, response.text
        assert item.scripts["project"].video_tasks[0] is original_task
        assert original_task.provider_execution.handle is not None
        assert original_task.provider_execution.phase == "observed"
        transport["status"] = "SUCCEEDED"
        dispatcher = StudioProviderRecoveryDispatcher(item, transient_backoff_seconds=0)
        assert dispatcher.run_once() == ("queried", "project", original_task.id)
        assert dispatcher.run_once() == ("fetched", "project", original_task.id)
        recovered = client.get("/projects/project/motion_ref_requests/motion-request")
        assert recovered.json()["task"]["provider_execution"]["phase"] == "completed"
        assert (item.data_dir / original_task.video_url).read_bytes() == VIDEO
    assert len([call for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]) == 1


def test_reparse_rechecks_active_motion_after_llm_without_a_revision_change(runtime, transport, monkeypatch):
    item, _ = runtime
    transport["status"] = "RUNNING"
    parsing, release_parse = threading.Event(), threading.Event()
    querying, release_query = threading.Event(), threading.Event()

    def hold_llm():
        parsing.set()
        assert release_parse.wait(timeout=10), "Test did not release external LLM transport"

    def hold_query():
        querying.set()
        assert release_query.wait(timeout=10), "Test did not release provider query transport"

    extraction_calls = real_extraction_transport(monkeypatch, hold_llm)
    with TestClient(app_for(item)) as client:
        created = client.post(PATH, json=COMMAND)
        assert created.status_code == 200, created.text
        task_id = created.json()["_task_id"]
        original_script = item.scripts["project"]
        original_task = original_script.video_tasks[0]
        revision = client.get("/projects/project").json()["revision"]
        before = (item.data_dir / "projects.json").read_bytes()
        with ThreadPoolExecutor(max_workers=2) as pool:
            parse_result = pool.submit(client.put, "/projects/project/reparse", json={
                "text": "The LLM is outside the save lock", "expected_revision": revision,
            })
            query_result = None
            try:
                assert parsing.wait(timeout=5), "Actual reparse did not reach external LLM"
                transport["on_query"] = hold_query
                query_result = pool.submit(item.query_provider_video_task, "project", task_id)
                assert querying.wait(timeout=5), "Original handle query did not reach external HTTP"
                assert ("project", task_id) in item._video_task_runs
                assert client.get("/projects/project").json()["revision"] == revision
                release_parse.set()
                reparsed = parse_result.result(timeout=5)
                unchanged = item.scripts["project"] is original_script
                saved_unchanged = (item.data_dir / "projects.json").read_bytes() == before
            finally:
                release_parse.set()
                release_query.set()
            query_error = query_result.exception(timeout=10) if query_result is not None else None
        assert reparsed.status_code == 409, reparsed.text
        assert unchanged and saved_unchanged and len(extraction_calls) == 1
        assert query_error is None
        assert item.scripts["project"].video_tasks[0] is original_task
        assert original_task.provider_execution.handle is not None
        transport.pop("on_query")
        transport["status"] = "SUCCEEDED"
        dispatcher = StudioProviderRecoveryDispatcher(item, transient_backoff_seconds=0)
        assert dispatcher.run_once() == ("queried", "project", task_id)
        assert dispatcher.run_once() == ("fetched", "project", task_id)
        assert original_task.provider_execution.phase == "completed"
        assert (item.data_dir / original_task.video_url).read_bytes() == VIDEO
    assert len([call for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]) == 1


@pytest.mark.parametrize("route", ["script", "series", "global"])
def test_real_reparse_preserves_motion_receipt_scope_and_original_recovery(runtime, transport, monkeypatch, route):
    item, _ = runtime
    target = move_owner(item, route)
    # Retention is bounded to this new motion history; ordinary old replacement
    # policy for unrelated legacy frame tasks is not silently expanded.
    item.scripts["project"].video_tasks.append(VideoTask.model_validate({
        "id": "old-frame-video", "project_id": "project", "frame_id": "old-frame",
        "image_url": "uploads/old-frame.png", "prompt": "Old frame",
    }))
    item._commit_storage("projects.json")
    extraction_calls = real_extraction_transport(monkeypatch)
    transport["status"] = "RUNNING"
    with TestClient(app_for(item)) as client:
        first = client.post(PATH, json=COMMAND)
        assert first.status_code == 200, first.text
        task_id = first.json()["_task_id"]
        task = next(task for task in item.scripts["project"].video_tasks if task.id == task_id)
        original_execution = task.provider_execution.model_dump(mode="json")
        original_receipt = item.scripts["project"].provider_request_receipts[0].model_dump(mode="json")
        revision = client.get("/projects/project").json()["revision"]
        replaced = client.put("/projects/project/reparse", json={
            "text": "A different story with no local entities", "expected_revision": revision,
        })
        assert replaced.status_code == 200, replaced.text
        assert len(extraction_calls) == 1
        assert replaced.json()["characters"] == [] and replaced.json()["frames"] == []
        read = client.get("/projects/project/motion_ref_requests/motion-request")
        assert read.status_code == 200, read.text
        assert read.json()["task"]["provider_execution"] == original_execution
        assert read.json()["request"] == original_receipt
        assert [task["id"] for task in replaced.json()["video_tasks"]] == [task_id]
        assert client.post(PATH, json=COMMAND).json()["_task_id"] == task_id
        if route != "script":
            assert target.full_body_motion_request_id == task_id
            second = client.post(PATH, json={**COMMAND, "client_request_id": "after-reparse-new-request"})
            assert second.status_code == 409, second.text
        assert len([call for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]) == 1
    item.close()
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True)
    try:
        transport["status"] = "SUCCEEDED"
        with TestClient(app_for(reopened)) as client:
            dispatcher = StudioProviderRecoveryDispatcher(reopened, transient_backoff_seconds=0)
            assert dispatcher.run_once() == ("queried", "project", task_id)
            assert dispatcher.run_once() == ("fetched", "project", task_id)
            read = client.get("/projects/project/motion_ref_requests/motion-request").json()
            result = read["task"]["provider_execution"]
            assert result["phase"] == "completed"
            assert result["scope"] == original_execution["scope"]
            assert result["credential_ref"] == original_execution["credential_ref"]
            assert result["fingerprint"] == original_execution["fingerprint"]
            assert (reopened.data_dir / read["task"]["video_url"]).read_bytes() == VIDEO
            if route == "script":
                assert read["task"]["status"] == "needs_confirmation"
                assert reopened.scripts["project"].characters == []
            else:
                assert read["task"]["status"] == "completed"
        assert len([call for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]) == 1
    finally:
        reopened.close()


def test_real_package_keeps_completed_unprojected_motion_as_inert_unselected_history(runtime, transport):
    item, image = runtime
    managed_cross_branch_source(item, image)
    target = item.scripts["project"].characters[0]
    target.full_body_image_url = "uploads/cross-branch.png"
    history = item.data_dir / "video/history.mp4"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b"synthetic old selected video")
    item._commit_storage("projects.json")

    def change_author_intent():
        target.description = "Changed while the provider was finishing"
        item._commit_storage("projects.json")

    transport["on_fetch"] = change_author_intent
    with TestClient(app_for(item)) as client:
        first = client.post(PATH, json=COMMAND)
        assert first.status_code == 200, first.text
        task = item.scripts["project"].video_tasks[0]
        assert task.provider_execution.phase == "completed" and task.status == "needs_confirmation"
        assert task.provider_execution.artifact == task.provider_execution.prepared_artifact
        assert len(target.full_body.video_variants) == 1
        original = item.read_committed_script("project").model_dump(mode="json")
        exported = client.get("/projects/project/package/export")
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            member = "media/" + task.video_url
            assert member in package.namelist(), "Paid video omitted because product projection needs confirmation"
            assert package.read(member) == VIDEO
        imported = client.post("/projects/package/import", content=exported.content,
                               headers={"content-type": "application/zip"})
        assert imported.status_code == 200, imported.text
        copied = imported.json()
        history_task = copied["video_tasks"][0]
        assert history_task["video_url"]
        assert (item.data_dir / history_task["video_url"]).read_bytes() == VIDEO
        assert history_task["provider_execution"] is None and history_task["motion_reference"] is None
        assert copied["provider_request_receipts"] == []
        assert copied["characters"][0]["full_body_motion_request_id"] is None
        assert history_task["status"] == "needs_confirmation"
        copied_unit = copied["characters"][0]["full_body"]
        assert len(copied_unit["video_variants"]) == 1
        assert copied_unit["selected_video_id"] == copied_unit["video_variants"][0]["id"]
        reexported = client.get(f"/projects/{copied['id']}/package/export")
        assert reexported.status_code == 200, reexported.text
        with zipfile.ZipFile(io.BytesIO(reexported.content)) as package:
            assert package.read("media/" + history_task["video_url"]) == VIDEO
        assert item.read_committed_script("project").model_dump(mode="json") == original
    assert [call[0] for call in transport["calls"]] == ["POST", "GET", "GET"]


def test_reparsed_local_motion_history_survives_package_round_trip_without_an_asset(runtime, transport, monkeypatch):
    item, image = runtime
    managed_cross_branch_source(item, image)
    real_extraction_transport(monkeypatch)
    transport["status"] = "RUNNING"
    with TestClient(app_for(item)) as client:
        created = client.post(PATH, json=COMMAND)
        assert created.status_code == 200, created.text
        task_id = created.json()["_task_id"]
        revision = client.get("/projects/project").json()["revision"]
        reparsed = client.put("/projects/project/reparse", json={
            "text": "A replacement story without the original local character",
            "expected_revision": revision,
        })
        assert reparsed.status_code == 200, reparsed.text
        assert reparsed.json()["characters"] == []
    item.close()
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True)
    try:
        transport["status"] = "SUCCEEDED"
        with TestClient(app_for(reopened)) as client:
            dispatcher = StudioProviderRecoveryDispatcher(reopened, transient_backoff_seconds=0)
            assert dispatcher.run_once() == ("queried", "project", task_id)
            assert dispatcher.run_once() == ("fetched", "project", task_id)
            original = reopened.read_committed_script("project").model_dump(mode="json")
            task = original["video_tasks"][0]
            assert task["provider_execution"]["phase"] == "completed"
            assert task["status"] == "needs_confirmation" and task["asset_id"] == "hero"
            assert task["error"] == "motion_reference_owner_changed"
            exported = client.get("/projects/project/package/export")
            assert exported.status_code == 200, exported.text
            with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
                assert package.read("media/" + task["video_url"]) == VIDEO
            imported = client.post("/projects/package/import", content=exported.content,
                                   headers={"content-type": "application/zip"})
            assert imported.status_code == 200, imported.text
            copied = imported.json()
            assert copied["characters"] == copied["scenes"] == copied["props"] == copied["frames"] == []
            assert copied["provider_request_receipts"] == []
            assert len(copied["video_tasks"]) == 1
            history = copied["video_tasks"][0]
            assert history["asset_id"] is None and history["frame_id"] is None
            assert history["status"] == "needs_confirmation" and history["is_starred"] is False
            assert all(history[key] is None for key in (
                "provider_execution", "motion_reference", "provider_name", "provider_task_id", "provider_request_id",
            ))
            assert (reopened.data_dir / history["video_url"]).read_bytes() == VIDEO
            reexported = client.get(f"/projects/{copied['id']}/package/export")
            assert reexported.status_code == 200, reexported.text
            with zipfile.ZipFile(io.BytesIO(reexported.content)) as package:
                assert package.read("media/" + history["video_url"]) == VIDEO
            assert reopened.read_committed_script("project").model_dump(mode="json") == original
        assert len([call for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("state", ["pending", "unknown", "ordinary"])
def test_package_still_rejects_unfinished_or_ordinary_missing_asset_tasks(runtime, transport, monkeypatch, state):
    item, image = runtime
    managed_cross_branch_source(item, image)
    real_extraction_transport(monkeypatch)
    with TestClient(app_for(item)) as client:
        if state == "ordinary":
            item.scripts["project"].characters = []
            item.scripts["project"].video_tasks = [VideoTask.model_validate({
                "id": "ordinary-missing-owner", "project_id": "project", "asset_id": "hero",
                "image_url": "uploads/cross-branch.png", "prompt": "Ordinary legacy history",
                "status": "completed", "video_url": "video/ordinary.mp4",
            })]
            item._commit_storage("projects.json")
        else:
            transport["status"], transport["lose_submit"] = "RUNNING", state == "unknown"
            created = client.post(PATH, json=COMMAND)
            assert created.status_code == 200, created.text
            revision = client.get("/projects/project").json()["revision"]
            reparsed = client.put("/projects/project/reparse", json={
                "text": "A replacement story with no local character", "expected_revision": revision,
            })
            assert reparsed.status_code == 200, reparsed.text
            record = reparsed.json()["video_tasks"][0]["provider_execution"]
            assert record["artifact"] is None
            assert record["phase"] == ("submitting" if state == "unknown" else "observed")
        before = (item.data_dir / "projects.json").read_bytes()
        rejected = client.get("/projects/project/package/export")
        assert rejected.status_code == 422 and "Missing or ambiguous packaged task asset" in rejected.text
        assert (item.data_dir / "projects.json").read_bytes() == before
        if state == "ordinary":
            raw = item.scripts["project"].model_dump(mode="json")
            raw["characters"] = [{"id": "hero", "name": "Duplicate character", "description": "One owner"}]
            raw["props"] = [{"id": "hero", "name": "Duplicate prop", "description": "Another owner"}]
            item.scripts["project"] = Script.model_validate(raw)
            item._commit_storage("projects.json")
            rejected = client.get("/projects/project/package/export")
            assert rejected.status_code == 422 and "Missing or ambiguous packaged task asset" in rejected.text
    expected_posts = 0 if state == "ordinary" else 1
    assert len([call for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT]) == expected_posts
