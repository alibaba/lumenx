"""Scene Motion retains project execution and projects inert video history.

Only external HTTP transport is substituted. Synthetic video bytes prove
artifact persistence and copy behavior, not decoded video acceptance.
"""

from copy import deepcopy
import hashlib

from fastapi.testclient import TestClient
import requests

from src.apps.comic_gen.models import Scene
from src.apps.comic_gen.pipeline import ComicGenPipeline
from tests import test_studio_motion_public_commands as public
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = public.runtime
transport = public.transport


def test_public_scene_motion_recovers_original_handle_and_copies_only_inert_history(runtime, transport):
    item, image = runtime
    public.config_params(item, resolution="720P", seed=0)
    source = item.data_dir / "uploads/scene-selected.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(image)
    (source.parent / "scene-other.png").write_bytes(rgb_png_bytes("unselected scene"))
    history = item.data_dir / "video/scene-history.mp4"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b"old scene video history")
    # The existing Character fixture has this same id. Motion association must
    # follow the requested kind, not the first asset found by id.
    character_before = item.scripts["project"].characters[0].model_dump(mode="json")
    scene = Scene.model_validate({
        "id": "hero", "name": "Night street", "description": "Quiet wet pavement",
        "image_url": "uploads/scene-other.png",
        "image_asset": {"selected_id": "scene-selected", "variants": [
            {"id": "scene-selected", "url": "uploads/scene-selected.png"},
        ]},
        "video_assets": [{"id": "scene-old-video", "project_id": "project", "asset_id": "hero",
                          "image_url": "uploads/scene-selected.png", "prompt": "Old scene motion",
                          "status": "completed", "video_url": "video/scene-history.mp4"}],
    })
    item.scripts["project"].scenes.append(scene)
    item._commit_storage("projects.json")
    old_history = scene.video_assets[0].model_dump(mode="json")
    key = "scene-motion-original"
    raw = {**public.COMMAND, "asset_type": "scene", "client_request_id": key,
           "prompt": "A slow pan across the wet pavement"}
    original_path = public.PUBLIC + "/" + key

    def query_outage():
        raise requests.ConnectionError("synthetic scene motion query outage")

    with TestClient(public.app_for(item)) as client:
        mismatch = public.preflight(client, asset_type="scene")
        assert mismatch["eligible"] is False and mismatch["reason"] == "selected_image_mismatch"
        denied = client.post(public.PUBLIC, json={**raw, "expected_target": mismatch["expected_target"]})
        assert denied.status_code == 422 and denied.json()["accepted"] is False
        assert client.post(public.LEGACY_PATH, json=raw).status_code == 422
        assert transport["calls"] == []
        scene.image_url = "uploads/scene-selected.png"
        item._commit_storage("projects.json")
        qualified = public.preflight(client, asset_type="scene")
        assert qualified["eligible"] and qualified["selected_image_id"] == "scene-selected"
        assert qualified["owner"] == {"kind": "script", "id": "project"}
        transport["on_query"] = query_outage
        admitted = client.post(public.PUBLIC, json={**raw, "expected_target": qualified["expected_target"]})
        assert admitted.status_code == 200, admitted.text
        read = client.get(original_path)
        assert read.status_code == 200, read.text
        original = read.json()
        task_id = original["task_id"]
        task = item.read_committed_script("project").video_tasks[0]
        assert task.provider_execution is not None and task.motion_reference is not None
        execution = task.provider_execution.model_dump(mode="json")
        assert execution["phase"] == "submitted" and execution["handle"] is not None
        assert execution["intent"]["params"]["resolution"] == task.resolution == "720p"
        assert execution["intent"]["params"]["seed"] == task.seed == 0
        wire = next(call[2] for call in transport["calls"] if call[0] == "POST" and call[1] == public.SUBMIT)
        assert wire["parameters"] == {
            "duration": 10, "resolution": "720P", "prompt_extend": True, "watermark": False, "seed": 0,
        }
        assert execution["media"][0]["sha256"] == hashlib.sha256(image).hexdigest()
        assert task.motion_reference.channel == original["request"]["asset_type"] == "scene"
        assert task.motion_reference.source_image_id == "scene-selected" and task.frame_id is None
        saved = item.read_committed_script("project")
        assert saved.scenes[0].motion_request_id == task_id
        assert [video.model_dump(mode="json") for video in saved.scenes[0].video_assets] == [old_history]
        assert saved.characters[0].model_dump(mode="json") == character_before
        assert public.submit_count(transport) == 1

    item.close()
    # Same URL with changed bytes cannot replace the already paid frozen input.
    source.write_bytes(rgb_png_bytes("later scene bytes"))
    transport["on_query"] = None
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True)
    try:
        with TestClient(public.app_for(reopened)) as client:
            retained = client.get(original_path)
            assert retained.status_code == 200, retained.text
            assert retained.json()["task"]["provider_execution"]["handle"] == execution["handle"]
            recovered = client.post(original_path + "/recover", json=public.expected_from(original))
            assert recovered.status_code == 200, recovered.text
            result = recovered.json()
            paid = result["task"]["provider_execution"]
            assert paid["phase"] == "completed"
            for field in ("handle", "attempt_id", "fingerprint", "scope", "credential_ref", "intent", "media"):
                assert paid[field] == execution[field]
            assert (reopened.data_dir / paid["artifact"]["relative_path"]).read_bytes() == public.VIDEO
            saved = reopened.read_committed_script("project")
            current = saved.scenes[0]
            assert current.motion_request_id == task_id and current.image_asset is not None
            assert current.image_asset.selected_id == "scene-selected"
            assert current.video_assets[0].model_dump(mode="json") == old_history
            assert [video.id for video in current.video_assets] == ["scene-old-video", task_id]
            projection = current.video_assets[-1]
            assert projection.video_url == result["task"]["video_url"] and projection.status == "completed"
            assert all(getattr(projection, field) is None for field in (
                "provider_name", "provider_task_id", "provider_request_id", "provider_execution", "motion_reference",
            ))
            assert saved.video_tasks[0].provider_execution is not None
            assert saved.characters[0].model_dump(mode="json") == character_before
            status = client.get("/tasks/" + task_id)
            assert status.status_code == 200 and status.json()["asset_type"] == "scene"
            before_calls = deepcopy(transport["calls"])
            replay = client.post(public.LEGACY_PATH, json=raw)
            assert replay.status_code == 200 and replay.json()["_replayed"] is True
            assert transport["calls"] == before_calls and public.submit_count(transport) == 1

            # Package export already rejects cross-kind ambiguous ids. Remove
            # the unrelated fixture Character before this independent-copy check.
            reopened.scripts["project"].characters = []
            reopened._commit_storage("projects.json")
            source_project = reopened.read_committed_script("project").model_dump(mode="json")
            exported = client.get("/projects/project/package/export")
            assert exported.status_code == 200, exported.text
            imported = client.post("/projects/package/import", content=exported.content,
                                   headers={"content-type": "application/zip"})
            assert imported.status_code == 200, imported.text
            copied = imported.json()
            assert copied["id"] != "project" and copied["provider_request_receipts"] == []
            assert copied["scenes"][0]["motion_request_id"] is None
            for copy_task in [*copied["video_tasks"], *copied["scenes"][0]["video_assets"]]:
                assert all(copy_task[field] is None for field in (
                    "provider_name", "provider_task_id", "provider_request_id", "provider_execution", "motion_reference",
                ))
            assert len(copied["scenes"][0]["video_assets"]) == 2
            assert (reopened.data_dir / copied["video_tasks"][0]["video_url"]).read_bytes() == public.VIDEO
            promoted = reopened.promote_asset_to_library("project", "project", "scene", "hero")
            assert promoted.motion_request_id is None and promoted.video_assets == current.video_assets
            forked = reopened.fork_library_asset_to_project(copied["id"], "scene", promoted.id)
            assert forked.motion_request_id is None and forked.video_assets == current.video_assets
            assert reopened.read_committed_script("project").model_dump(mode="json") == source_project
            assert public.submit_count(transport) == 1
    finally:
        reopened.close()
