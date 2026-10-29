"""Image routes preserve validation, generation and storage failure contracts."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.mark.parametrize("entry,method,body", [
    ("generate_storyboard", "generate_storyboard", None),
    ("storyboard/render", "generate_storyboard_render", {"frame_id": "f", "prompt": "image"}),
])
@pytest.mark.parametrize("error,status", [
    (module.CommandValidationError("Invalid image inputs"), 422),
    (module.ProjectGenerationError("Invalid image result"), 502),
    (module.ProjectContextError("Target missing", not_found=True), 404),
    (module.ProjectContextError("Invalid target context"), 422),
    (StorageConflictError("Image inputs changed"), 409),
    (StorageError("Storage unavailable"), 503),
])
def test_image_error_contract(tmp_path, monkeypatch, entry, method, body, error, status):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    try:
        monkeypatch.setattr(runtime, method, Mock(side_effect=error))
        app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
        with TestClient(app) as client:
            response = client.post(f"/projects/p/{entry}", json=body)
            assert response.status_code == status, response.text
            # Domain validation/provider result failures do not disable storage.
            runtime.assert_storage_available()
    finally:
        runtime.close()


@pytest.mark.parametrize("batch_size", [True, 0, -1, 1.5, "2"])
def test_render_rejects_non_positive_or_non_integer_batch_before_command(tmp_path, monkeypatch, batch_size):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    command = Mock(side_effect=AssertionError("Invalid batch must not reach generation"))
    monkeypatch.setattr(runtime, "generate_storyboard_render", command)
    try:
        app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
        with TestClient(app) as client:
            response = client.post("/projects/p/storyboard/render", json={
                "frame_id": "f", "prompt": "image", "batch_size": batch_size,
            })
            assert response.status_code == 422, response.text
            command.assert_not_called()
    finally:
        runtime.close()
