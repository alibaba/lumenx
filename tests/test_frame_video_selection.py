"""A frame may only pin a playable completed task owned by that frame."""

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from tests.test_short_domain_commands import pipeline as storage_pipeline

pipeline = storage_pipeline


@pytest.mark.parametrize("patch", [
    {"frame_id": "f2"},
    {"project_id": "other"},
    {"status": "pending"},
    {"status": "processing"},
    {"status": "failed"},
    {"video_url": None},
    {"video_url": ""},
    {"video_url": "   "},
])
def test_select_rejects_unusable_or_foreign_task_without_mutating_project(pipeline, patch):
    task = pipeline.scripts["p"].video_tasks[0]
    for key, value in patch.items():
        setattr(task, key, value)
    pipeline._commit_storage("projects.json")
    before = pipeline.read_committed_script("p").model_dump()
    disk = (pipeline.data_dir / "projects.json").read_bytes()

    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.post("/projects/p/frames/f1/select_video", json={"video_id": "v1"})

        assert response.status_code == 404, response.text
        assert pipeline.read_committed_script("p").model_dump() == before
        assert (pipeline.data_dir / "projects.json").read_bytes() == disk
