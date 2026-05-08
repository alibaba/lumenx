from __future__ import annotations

import base64
import importlib
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import Script


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+"
    "X2VINQAAAABJRU5ErkJggg=="
)


class FakeScriptProcessor:
    def create_draft_script(self, title: str, text: str) -> Script:
        now = time.time()
        return Script(
            id=f"script-{uuid.uuid4().hex[:8]}",
            title=title,
            original_text=text,
            created_at=now,
            updated_at=now,
        )

    def parse_novel(self, title: str, text: str) -> Script:
        return self.create_draft_script(title, text)


class DummyUploader:
    is_configured = False
    provider = None

    def upload_image(self, *args, **kwargs):
        return None

    def upload_video(self, *args, **kwargs):
        return None

    def upload_file(self, *args, **kwargs):
        return None


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    output_root = tmp_path / "lumenx-e2e-output"
    monkeypatch.setenv("LUMENX_OUTPUT_DIR", str(output_root))
    monkeypatch.setenv("CI", "1")

    with patch("src.apps.comic_gen.pipeline.ScriptProcessor", return_value=FakeScriptProcessor()), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"), \
         patch("src.utils.oss_utils.OSSImageUploader", DummyUploader):
        import src.apps.comic_gen.api as api_module

        reloaded_api = importlib.reload(api_module)

    monkeypatch.setattr(reloaded_api, "OSSImageUploader", DummyUploader)
    import src.utils.oss_utils as oss_utils
    monkeypatch.setattr(oss_utils, "OSSImageUploader", DummyUploader)

    pipeline = reloaded_api.pipeline
    pipeline.data_file = str(output_root / "projects.json")
    pipeline.series_data_file = str(output_root / "series.json")
    pipeline.scripts = {}
    pipeline.series_store = {}

    def fake_generate(*, output_path: str, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(base64.b64decode(PNG_1X1_BASE64))
        return output_path, {}

    pipeline.video_generator = SimpleNamespace(model=SimpleNamespace(generate=fake_generate))

    return TestClient(reloaded_api.app), pipeline, output_root


def _wait_for_completed_task(client: TestClient, project_id: str, task_id: str) -> dict:
    deadline = time.time() + 5
    last_task = None
    while time.time() < deadline:
        response = client.get(f"/projects/{project_id}")
        assert response.status_code == 200
        project = response.json()
        task = next((item for item in project.get("video_tasks", []) if item["id"] == task_id), None)
        if task:
            last_task = task
            if task.get("status") == "completed":
                return task
        time.sleep(0.1)
    raise AssertionError(f"Video task {task_id} did not complete: {last_task}")


def test_local_storyboard_and_video_paths_stay_inside_isolated_output(isolated_client):
    client, pipeline, output_root = isolated_client

    project_response = client.post(
        "/projects?skip_analysis=true",
        json={
            "title": "Local Path Smoke",
            "text": "角色进入分镜，再生成一段本地视频。",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    scene_response = client.post(
        f"/projects/{project_id}/scenes",
        json={
            "name": "街道",
            "description": "夜晚的街道，路灯昏黄。",
        },
    )
    assert scene_response.status_code == 200
    scene = scene_response.json()["scenes"][0]

    frame_response = client.post(
        f"/projects/{project_id}/frames",
        json={
            "scene_id": scene["id"],
            "action_description": "角色从右侧缓慢走入画面。",
            "camera_angle": "medium_shot",
        },
    )
    assert frame_response.status_code == 200
    frame = frame_response.json()["frames"][0]

    upload_response = client.post(
        f"/projects/{project_id}/frames/{frame['id']}/upload_image",
        files={"file": ("frame.png", base64.b64decode(PNG_1X1_BASE64), "image/png")},
    )
    assert upload_response.status_code == 200
    uploaded_project = upload_response.json()
    uploaded_frame = next(item for item in uploaded_project["frames"] if item["id"] == frame["id"])
    assert uploaded_frame["rendered_image_url"].startswith("uploads/")
    assert (output_root / uploaded_frame["rendered_image_url"]).exists()

    video_response = client.post(
        f"/projects/{project_id}/video_tasks",
        json={
            "image_url": uploaded_frame["rendered_image_url"],
            "prompt": "让角色继续向前走两步。",
            "frame_id": frame["id"],
            "model": "wan2.6-i2v",
        },
    )
    assert video_response.status_code == 200
    created_task = video_response.json()[0]
    assert created_task["image_url"].startswith("video_inputs/")
    assert (output_root / created_task["image_url"]).exists()

    completed_task = _wait_for_completed_task(client, project_id, created_task["id"])
    assert completed_task["status"] == "completed"
    assert completed_task["video_url"].startswith("video/")
    assert (output_root / completed_task["video_url"]).exists()
    assert (output_root / "projects.json").exists()

    project_data = client.get(f"/projects/{project_id}").json()
    assert project_data["frames"][0]["rendered_image_url"].startswith("uploads/")
    assert project_data["video_tasks"][0]["image_url"].startswith("video_inputs/")
