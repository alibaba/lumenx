"""Asset batch HTTP errors preserve domain and storage failure distinctions."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Script
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.mark.parametrize("error,status", [
    (module.CommandValidationError("Invalid asset image inputs"), 422),
    (module.ProjectGenerationError("Invalid asset image result"), 502),
    (module.ProjectContextError("Target missing", not_found=True), 404),
    (module.ProjectContextError("Invalid target context"), 422),
    (StorageConflictError("Asset inputs changed"), 409),
    (StorageError("Storage unavailable"), 503),
])
def test_asset_image_error_contract(tmp_path, monkeypatch, error, status):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    try:
        runtime.scripts["p"] = Script(id="p", title="Project", original_text="Script", created_at=1, updated_at=1)
        runtime._save_data()
        command = Mock(side_effect=error)
        monkeypatch.setattr(runtime, "generate_assets", command)
        app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
        with TestClient(app) as client:
            response = client.post("/projects/p/generate_assets")
            assert response.status_code == status, response.text
            command.assert_called_once_with("p")
            runtime.assert_storage_available()
    finally:
        runtime.close()
