"""Headshot Motion binds its selected frame and retains its original request.

Only external HTTP transport is substituted. Synthetic video bytes establish
request and artifact behavior, not video decoding acceptance.
"""

import base64
import hashlib

from fastapi.testclient import TestClient

from src.apps.comic_gen.models import AssetUnit
from tests import test_studio_motion_public_commands as public
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = public.runtime
transport = public.transport


def test_public_headshot_motion_binds_its_own_selected_frame_and_replays_original(runtime, transport):
    item, full_body_image = runtime
    target = item.scripts["project"].characters[0]
    assert target.full_body is not None
    original_full_body = target.full_body.model_dump(mode="json")
    head_image = rgb_png_bytes("selected-headshot-motion-frame")
    assert head_image != full_body_image
    head_source = "data:image/png;base64," + base64.b64encode(head_image).decode()
    target.head_shot = AssetUnit.model_validate({
        "image_variants": [{"id": "head-selected", "url": head_source}],
        "selected_image_id": "head-selected",
        "video_variants": [{"id": "head-old-video", "url": "video/head-history.mp4"}],
        "selected_video_id": "head-old-video",
    })
    target.headshot_image_url = head_source
    item._commit_storage("projects.json")
    with TestClient(public.app_for(item)) as client:
        qualified = public.preflight(client, asset_type="head_shot")
        body = {**public.COMMAND, "asset_type": "head_shot", "client_request_id": "headshot-motion-request",
                "expected_target": qualified["expected_target"]}
        admitted = client.post(public.PUBLIC, json=body)
        assert admitted.status_code == 200, admitted.text
        assert qualified["eligible"] is True and qualified["selected_image_id"] == "head-selected"
        assert qualified["source_kind"] == "data_uri"
        read = client.get(public.PUBLIC + "/" + body["client_request_id"])
        assert read.status_code == 200, read.text
        result = read.json()
        task, receipt = result["task"], result["request"]
        assert receipt["asset_type"] == task["motion_reference"]["channel"] == "head_shot"
        assert task["motion_reference"]["source_image_id"] == "head-selected"
        assert task["frame_id"] is None
        execution = task["provider_execution"]
        assert execution["phase"] == "completed" and execution["prepared_artifact"] == execution["artifact"]
        assert execution["media"][0]["sha256"] == hashlib.sha256(head_image).hexdigest()
        assert (item.data_dir / execution["artifact"]["relative_path"]).read_bytes() == public.VIDEO
        wire = next(call[2] for call in transport["calls"] if call[0] == "POST" and call[1] == public.SUBMIT)
        assert wire["model"] == "wan2.7-i2v"
        assert wire["input"] == {"prompt": body["prompt"], "media": [{"type": "first_frame", "url": head_source}]}
        assert wire["parameters"] == {"duration": 10, "resolution": "720P", "prompt_extend": True, "watermark": False}
        current = item.scripts["project"].characters[0]
        assert current.full_body is not None and current.head_shot is not None
        assert current.full_body.model_dump(mode="json") == original_full_body
        assert current.full_body_motion_request_id is None
        assert current.head_shot_motion_request_id == task["id"]
        assert current.head_shot.selected_video_id == "head-old-video"
        assert [variant.id for variant in current.head_shot.video_variants] == ["head-old-video", task["id"]]
        assert current.head_shot.video_variants[-1].source_image_id == "head-selected"
        status = client.get("/tasks/" + task["id"])
        assert status.status_code == 200 and status.json()["asset_type"] == "head_shot"
        before_calls = list(transport["calls"])
        replay = client.post(public.LEGACY_PATH, json={key: value for key, value in body.items() if key != "expected_target"})
        assert replay.status_code == 200, replay.text
        assert replay.json()["_replayed"] is True and replay.json()["_task_id"] == task["id"]
        assert transport["calls"] == before_calls and public.submit_count(transport) == 1
