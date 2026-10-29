"""The editable R2V visual description must survive copy and reload."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, Script, Series, StoryboardFrame
from src.apps.comic_gen.prompt_assembly import assemble_prompt
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(
        id="p", title="project", original_text="original", created_at=1, updated_at=1,
        series_id="s", frames=[StoryboardFrame(
            id="f", scene_id="", action_description="coarse action summary",
            visual_description="old refined description", assembled_prompt="old assembled",
            character_ids=["c"],
        )],
    )
    item.series_store["s"] = Series(
        id="s", title="series", created_at=1, updated_at=1, episode_ids=["p"],
        characters=[Character(id="c", name="shared character", description="blue coat")],
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    yield item
    item.close()


@pytest.mark.parametrize("visual", ["new manual description", ""])
@pytest.mark.parametrize("source", ["series", "global"])
def test_visual_edit_updates_assembly_preserves_summary_and_survives_copy_reload(runtime, visual, source):
    if source == "global":
        runtime.library_store.characters = runtime.series_store["s"].characters
        runtime.series_store["s"].characters = []
        runtime._commit_storage("series.json", "library_assets.json")
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/projects/p/frames/update", json={"frame_id": "f", "expected_revision": runtime.read_committed_script("p").revision, "visual_description": visual})
        assert response.status_code == 200, response.text
        frame = response.json()["frames"][0]
        assert frame["visual_description"] == visual
        assert frame["action_description"] == "coarse action summary"
        expected = assemble_prompt(runtime.scripts["p"].frames[0], runtime.resolve_episode_assets(runtime.scripts["p"])["characters"])
        assert frame["assembled_prompt"] == expected and "shared character" in expected
        assert "old refined description" not in frame["assembled_prompt"]
        assert response.json()["revision"] == 1
        copy = client.post("/projects/p/frames/copy", json={"frame_id": "f"})
        assert copy.status_code == 200, copy.text
        assert [f["visual_description"] for f in copy.json()["frames"]] == [visual, visual]
    restored = module.ComicGenPipeline(data_dir=runtime.data_dir, script_processor=Mock())
    try:
        assert [f.visual_description for f in restored.get_script("p").frames] == [visual, visual]
        assert all(f.action_description == "coarse action summary" for f in restored.get_script("p").frames)
    finally:
        restored.close()


@pytest.mark.parametrize("payload", [{}, {"visual_description": None}])
def test_omitted_or_null_visual_preserves_existing_edit(runtime, payload):
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/projects/p/frames/update", json={"frame_id": "f", "expected_revision": runtime.read_committed_script("p").revision, "dialogue": "hello", **payload})
        assert response.status_code == 200
        assert response.json()["frames"][0]["visual_description"] == "old refined description"


def test_invalid_visual_is_rejected_before_other_fields_change(runtime):
    before = (runtime.data_dir / "projects.json").read_bytes()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/projects/p/frames/update", json={
            "frame_id": "f", "expected_revision": runtime.read_committed_script("p").revision, "dialogue": "not saved", "visual_description": [],
        })
        assert response.status_code == 422
        assert (runtime.data_dir / "projects.json").read_bytes() == before
        runtime.assert_storage_available()


def test_visual_save_failure_reports_unavailable_and_restores_committed_fields(runtime, monkeypatch):
    before = (runtime.data_dir / "projects.json").read_bytes()
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk full")))
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/projects/p/frames/update", json={"frame_id": "f", "expected_revision": runtime.read_committed_script("p").revision, "visual_description": "new"})
        assert response.status_code == 503
        assert (runtime.data_dir / "projects.json").read_bytes() == before
        assert runtime.scripts["p"].model_dump(mode="json") == json.loads(before)["p"]


def test_assembly_failure_cannot_leak_part_of_the_edit(runtime, monkeypatch):
    from src.apps.comic_gen import prompt_assembly

    before = (runtime.data_dir / "projects.json").read_bytes()
    monkeypatch.setattr(prompt_assembly, "assemble_prompt", Mock(side_effect=RuntimeError("assembly failed")))
    with pytest.raises(RuntimeError, match="assembly failed"):
        runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, visual_description="new", dialogue="new dialogue")
    assert runtime.scripts["p"].model_dump(mode="json") == json.loads(before)["p"]
    assert (runtime.data_dir / "projects.json").read_bytes() == before
    runtime.assert_storage_available()


@pytest.mark.parametrize("visual,expected", [(None, "coarse action summary"), ("", "")])
def test_assembly_distinguishes_unset_and_explicit_empty_visual(visual, expected):
    frame = StoryboardFrame(id="f", scene_id="", action_description="coarse action summary",
                            visual_description=visual, camera_angle="", character_ids=[])
    assert assemble_prompt(frame, []) == expected


def test_clearing_visual_without_supplements_never_revives_action_summary(runtime):
    frame = runtime.scripts["p"].frames[0]
    frame.character_ids = []
    frame.camera_angle = ""
    runtime._save_data()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/projects/p/frames/update", json={"frame_id": "f", "expected_revision": runtime.read_committed_script("p").revision, "visual_description": ""})
        assert response.status_code == 200, response.text
        frame = response.json()["frames"][0]
        assert frame["visual_description"] == "" and frame["assembled_prompt"] == ""
        assert frame["action_description"] == "coarse action summary"
        saved = json.loads((runtime.data_dir / "projects.json").read_text())["p"]["frames"][0]
        assert saved["assembled_prompt"] == ""
