"""Accepted motion inputs reach the actual adapter's external HTTP payload."""

import json
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import Script
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models import wanx


@pytest.fixture
def motion_http_runtime(tmp_path, monkeypatch):
    calls = []
    media_url = "https://media.invalid/motion-source.png"
    result_url = "https://media.invalid/motion-result.mp4"
    result_bytes = b"synthetic transport bytes; not a media decoding fixture"

    def send(session, request, **kwargs):
        calls.append((request.method, request.url, json.loads(request.body) if request.body else None))
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        if request.method == "POST" and request.url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis":
            response._content = json.dumps({"output": {"task_id": "duration-task"}}).encode()
        elif request.method == "GET" and request.url == "https://dashscope.aliyuncs.com/api/v1/tasks/duration-task":
            response._content = json.dumps({"output": {"task_status": "SUCCEEDED", "video_url": result_url}}).encode()
        elif request.method == "GET" and request.url == result_url:
            response._content = result_bytes
        else:
            raise AssertionError(f"Unexpected external request: {request.method} {request.url}")
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr(wanx.time, "sleep", lambda _: None)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-duration-key")
    monkeypatch.setenv("NETRC", str(tmp_path / "absent-netrc"))
    runtime = ComicGenPipeline(
        data_dir=tmp_path / "output", script_processor=Mock(),
    )
    try:
        runtime.scripts["project"] = Script.model_validate({
            "id": "project", "title": "Duration contract", "original_text": "Synthetic", "created_at": 1, "updated_at": 1,
            "characters": [{"id": "character", "name": "Hero", "description": "Red coat", "full_body_image_url": media_url, "headshot_image_url": media_url}],
            "scenes": [{"id": "scene", "name": "Room", "description": "Quiet", "image_url": media_url}],
            "props": [{"id": "prop", "name": "Book", "description": "Red", "image_url": media_url}],
        })
        runtime._commit_storage("projects.json", "series.json", "library_assets.json")
        app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
        with TestClient(app) as client:
            yield runtime, client, calls, media_url, result_bytes
    finally:
        runtime.close()


@pytest.mark.parametrize("asset_type", ["full_body", "head_shot", "scene", "prop"])
@pytest.mark.parametrize("duration", [5, 10, 15])
def test_motion_http_preserves_duration_through_real_model(motion_http_runtime, asset_type, duration):
    runtime, client, calls, media_url, result_bytes = motion_http_runtime
    response = client.post("/projects/project/assets/generate_motion_ref", json={
        "asset_id": "character" if asset_type in ("full_body", "head_shot") else asset_type,
        "asset_type": asset_type, "prompt": "Slow rotation", "duration": duration,
    })
    assert response.status_code == 200, response.text
    task = client.get("/tasks/" + response.json()["_task_id"])
    assert task.status_code == 200, task.text
    assert task.json()["status"] == "completed"
    submissions = [body for method, _, body in calls if method == "POST"]
    assert len(submissions) == 1
    assert submissions[0]["parameters"]["duration"] == duration
    assert submissions[0]["parameters"]["resolution"] == "720P"
    assert submissions[0]["input"] == {
        "prompt": "Slow rotation", "media": [{"type": "first_frame", "url": media_url}],
    }
    assert not ({"ratio", "shot_type", "audio"} & submissions[0]["parameters"].keys())
    assert [method for method, _, _ in calls] == ["POST", "GET", "GET"]
    files = list((runtime.data_dir / "video").glob("motion_ref_*.mp4"))
    assert len(files) == 1 and files[0].read_bytes() == result_bytes


def test_motion_http_preserves_driving_audio_through_real_model(motion_http_runtime):
    runtime, client, calls, media_url, result_bytes = motion_http_runtime
    audio_url = "https://media.invalid/author-dialogue.wav"
    runtime.video_generator.model.params.update({
        "resolution": "1080p", "seed": 0, "negative_prompt": "No text overlay",
    })

    response = client.post("/projects/project/assets/generate_motion_ref", json={
        "asset_id": "character", "asset_type": "full_body", "prompt": "Speak the supplied dialogue",
        "duration": 10, "audio_url": audio_url,
    })
    assert response.status_code == 200, response.text
    task = client.get("/tasks/" + response.json()["_task_id"])
    assert task.status_code == 200, task.text
    assert task.json()["status"] == "completed"

    submissions = [body for method, _, body in calls if method == "POST"]
    assert len(submissions) == 1
    assert submissions[0] == {
        "model": "wan2.7-i2v",
        "input": {
            "prompt": "Speak the supplied dialogue", "negative_prompt": "No text overlay",
            "media": [
                {"type": "first_frame", "url": media_url},
                {"type": "driving_audio", "url": audio_url},
            ],
        },
        "parameters": {"duration": 10, "prompt_extend": True, "watermark": False, "resolution": "1080P", "seed": 0},
    }
    assert [method for method, _, _ in calls] == ["POST", "GET", "GET"]
    files = list((runtime.data_dir / "video").glob("motion_ref_*.mp4"))
    assert len(files) == 1 and files[0].read_bytes() == result_bytes
    character = runtime.read_committed_script("project").characters[0]
    assert character.full_body is not None
    assert len(character.full_body.video_variants) == 1
    assert character.full_body.video_variants[0].audio_url == audio_url


def test_motion_rejects_audio_for_i2v_adapter_that_does_not_consume_it(motion_http_runtime):
    runtime, _, calls, media_url, _ = motion_http_runtime
    runtime.video_generator.model.params["i2v_model_name"] = "happyhorse-1.0-i2v"
    with pytest.raises(ValueError, match="driving audio is not supported"):
        runtime.video_generator.generate_i2v(
            media_url, "Speak the supplied dialogue", audio_url="https://media.invalid/author-dialogue.wav",
        )
    assert calls == []
