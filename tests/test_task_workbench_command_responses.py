"""Task/frame acknowledgements are detached and reject a closed writer."""

from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(
        id="p", title="project", original_text="source", created_at=1, updated_at=1,
        frames=[StoryboardFrame(id="f", scene_id="s")],
        video_tasks=[VideoTask(id="v", project_id="p", prompt="move", image_url="image.png")],
    )
    item._save_data()
    yield item
    item.close()


COMMANDS = ["annotation", "workbench", "upload"]


def command(runtime, name):
    if name == "annotation":
        return runtime.annotate_video_task("p", "v", is_starred=True, label="best")
    if name == "workbench":
        return runtime.update_frame_workbench("p", "f", workbench_generate_count=2)
    return runtime.upload_t2i_frame("p", "f", "uploads/new.png")


@pytest.mark.parametrize("name", COMMANDS)
def test_response_mutation_cannot_change_the_live_aggregate(runtime, name):
    result = command(runtime, name)
    saved = runtime.read_committed_script("p").model_dump()
    result.id = "response-only"
    assert runtime.scripts["p"].model_dump() == saved


@pytest.mark.parametrize("name", COMMANDS)
def test_closed_runtime_does_not_mutate_live_state(runtime, name):
    before = runtime.scripts["p"].model_dump()
    runtime.close()
    with pytest.raises(StorageError):
        command(runtime, name)
    assert runtime.scripts["p"].model_dump() == before


@pytest.mark.parametrize("name", COMMANDS)
def test_unexpected_save_failure_is_not_reported_as_success(runtime, monkeypatch, name):
    monkeypatch.setattr(runtime, "_save_data", Mock(side_effect=RuntimeError("unexpected write failure")))
    with pytest.raises(RuntimeError, match="unexpected write failure"):
        command(runtime, name)
