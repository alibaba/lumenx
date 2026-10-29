"""API read endpoints must render committed snapshots, not dirty live state."""
import asyncio
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, ModelSettings, PromptConfig, Script, Series, StoryboardFrame, VideoTask


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    processor = Mock()
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=processor)
    previous = Script(
        id="prev",
        title="Previous stored title",
        original_text="stored previous text " * 60,
        series_id="series",
        episode_number=1,
        created_at=1,
        updated_at=1,
        characters=[Character(id="hero", name="Hero", description="stored hero")],
        frames=[StoryboardFrame(id="pf", scene_id="", character_ids=["hero"], action_description="stored ending", video_url="video/stored.mp4")],
    )
    current = Script(
        id="current",
        title="Current",
        original_text="stored current text",
        series_id="series",
        episode_number=2,
        created_at=1,
        updated_at=1,
        characters=[Character(id="local-hero", name="Hero", description="local stored")],
        scenes=[],
        props=[],
        frames=[StoryboardFrame(id="cf", scene_id="", character_ids=["hero"], action_description="stored current frame")],
        prompt_config=PromptConfig(
            video_polish="episode committed video prompt",
            style_analysis="episode committed style prompt",
            polish_model="episode-committed-model",
        ),
        next_hook_cache="stored hook",
        next_hook_revision="stored-revision",
        last_episode_summary_cache="stored summary",
        last_episode_summary_revision=api._prev_text_revision(previous.original_text),
        video_tasks=[VideoTask(id="task", project_id="current", frame_id="cf", image_url="img.png", prompt="go", status="pending")],
    )
    item.scripts[previous.id] = previous
    item.scripts[current.id] = current
    item.series_store["series"] = Series(
        id="series",
        title="Stored series",
        created_at=1,
        updated_at=1,
        episode_ids=["prev", "current"],
        characters=[Character(id="hero", name="Hero", description="series stored")],
        prompt_config=PromptConfig(
            video_polish="series committed video prompt",
            r2v_polish="series committed r2v prompt",
            polish_model="series-committed-model",
        ),
        model_settings=ModelSettings(t2i_model="stored-t2i", r2v_model="stored-r2v"),
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    with TestClient(api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)) as client:
        yield item, client, processor
    item.close()


def test_series_read_endpoints_ignore_dirty_live_series(runtime):
    item, client, _ = runtime
    series = item.series_store["series"]
    series.prompt_config.video_polish = "DIRTY prompt"
    series.model_settings.t2i_model = "dirty-t2i"
    series.characters[0].name = "Dirty Hero"
    series.characters.append(Character(id="dirty", name="Dirty Extra", description="not saved"))

    prompt = client.get("/series/series/prompt_config")
    settings = client.get("/series/series/model_settings")
    assets = client.get("/series/series/assets")

    assert prompt.status_code == 200, prompt.text
    assert settings.status_code == 200, settings.text
    assert assets.status_code == 200, assets.text
    assert prompt.json()["prompt_config"]["video_polish"] == "series committed video prompt"
    assert settings.json()["t2i_model"] == "stored-t2i"
    assert [c["name"] for c in assets.json()["characters"]] == ["Hero"]


def test_project_series_read_endpoints_use_one_committed_capture(runtime):
    item, client, _ = runtime
    current = item.scripts["current"]
    previous = item.scripts["prev"]
    series = item.series_store["series"]
    current.characters[0].name = "Dirty Local"
    current.next_hook_cache = "DIRTY hook"
    current.last_episode_summary_cache = "DIRTY summary"
    previous.original_text = "DIRTY previous text"
    previous.frames[0].video_url = "video/dirty.mp4"
    series.characters[0].name = "Dirty Series"
    series.episode_ids = ["current"]

    reconcile = client.get("/projects/current/reconcile/suggestions")
    previous_response = client.get("/projects/current/previous_episode")
    appearances = client.get("/series/series/characters/hero/appearances")
    hook = client.get("/projects/current/next_hook")

    assert reconcile.status_code == 200, reconcile.text
    assert previous_response.status_code == 200, previous_response.text
    assert appearances.status_code == 200, appearances.text
    assert hook.status_code == 200, hook.text
    assert reconcile.json()["characters"][0]["local_name"] == "Hero"
    assert reconcile.json()["characters"][0]["suggested_series_name"] == "Hero"
    assert previous_response.json()["has_previous"] is True
    assert "stored previous text" in previous_response.json()["raw_snippet"]
    assert previous_response.json()["last_frames"][0]["video_url"] == "video/stored.mp4"
    assert appearances.json()["character"]["name"] == "Hero"
    assert appearances.json()["appearances"] == [
        {"episode_id": "prev", "episode_number": 1, "episode_title": "Previous stored title", "frame_count": 1},
        {"episode_id": "current", "episode_number": 2, "episode_title": "Current", "frame_count": 1},
    ]
    assert hook.json()["hook"] == "stored hook"


def test_previous_episode_uses_single_committed_context_boundary(runtime, monkeypatch):
    item, client, _ = runtime
    calls = []
    script = item.read_committed_script("current")
    _series, episodes = item.read_committed_series_view("series")
    series = _series
    previous = next(episode for episode in episodes if episode.id == "prev")

    def fake_context(script_id):
        calls.append(script_id)
        item.scripts["prev"].original_text = "DIRTY after context"
        return script, series, previous

    monkeypatch.setattr(item, "read_previous_episode_context", fake_context)
    monkeypatch.setattr(item, "read_committed_project_view", Mock(side_effect=AssertionError("second project read must not run")))
    monkeypatch.setattr(item, "read_committed_series_view", Mock(side_effect=AssertionError("second series read must not run")))

    response = client.get("/projects/current/previous_episode")

    assert response.status_code == 200, response.text
    assert calls == ["current"]
    assert "stored previous text" in response.json()["raw_snippet"]


def test_style_analysis_uses_committed_prompt_config(runtime):
    item, _client, processor = runtime
    item.scripts["current"].prompt_config.style_analysis = "DIRTY style prompt"
    processor.analyze_script_for_styles.return_value = [{"id": "ok"}]

    result = asyncio.run(api.analyze_script_for_styles("current", api.AnalyzeStyleRequest(script_text="body"), pipeline=item))

    assert result == {"recommendations": [{"id": "ok"}]}
    processor.analyze_script_for_styles.assert_called_once_with("body", "episode committed style prompt")


def test_polish_helpers_use_committed_prompt_and_model(runtime):
    item, _client, _ = runtime
    item.scripts["current"].prompt_config.video_polish = "DIRTY episode prompt"
    item.scripts["current"].prompt_config.polish_model = "DIRTY episode model"
    item.series_store["series"].prompt_config.r2v_polish = "DIRTY series prompt"
    item.series_store["series"].prompt_config.polish_model = "DIRTY series model"

    assert api._get_custom_prompt(item, "current", "video_polish") == "episode committed video prompt"
    assert api._get_custom_prompt(item, "current", "r2v_polish") == "series committed r2v prompt"
    assert api._get_polish_model_for_project(item, "current") == "episode-committed-model"


def test_polish_model_falls_back_to_committed_series_model(runtime):
    item, _client, _ = runtime
    item.scripts["current"].prompt_config.polish_model = ""
    item._commit_storage("projects.json")
    item.scripts["current"].prompt_config.polish_model = "DIRTY episode model"
    item.series_store["series"].prompt_config.polish_model = "DIRTY series model"

    assert api._get_polish_model_for_project(item, "current") == "series-committed-model"


def test_cancel_video_task_returns_committed_task_snapshot(runtime, monkeypatch):
    item, client, _ = runtime

    def fake_cancel(script_id, task_id, error):
        task = item.scripts[script_id].video_tasks[0]
        task.status = "failed"
        task.error = error
        item._commit_storage("projects.json")
        task.error = "DIRTY live error"
        return True

    monkeypatch.setattr(item, "mark_video_task_failed", fake_cancel)

    response = client.post("/projects/current/video_tasks/task/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "Canceled by user"


def test_project_prompt_config_ignores_dirty_live_script(runtime):
    item, client, _ = runtime
    item.scripts["current"].prompt_config.video_polish = "DIRTY project prompt"
    item.scripts["current"].prompt_config.style_analysis = "DIRTY style prompt"

    response = client.get("/projects/current/prompt_config")

    assert response.status_code == 200, response.text
    assert response.json()["prompt_config"]["video_polish"] == "episode committed video prompt"
    assert response.json()["prompt_config"]["style_analysis"] == "episode committed style prompt"


def test_completed_asset_task_status_attaches_committed_script(runtime, monkeypatch):
    item, client, _ = runtime
    item.scripts["current"].title = "DIRTY live title"
    item.scripts["current"].prompt_config.video_polish = "DIRTY live prompt"

    monkeypatch.setattr(item, "get_asset_generation_task_status", lambda task_id: {
        "id": task_id,
        "script_id": "current",
        "status": "completed",
    })

    response = client.get("/tasks/task-asset")

    assert response.status_code == 200, response.text
    script = json.loads(response.json()["script"])
    assert script["title"] == "Current"
    assert script["prompt_config"]["video_polish"] == "episode committed video prompt"


def test_library_http_read_uses_committed_version_and_assets(runtime):
    item, client, _ = runtime
    saved = item.create_library_asset("character", {"name": "Stored", "description": "saved"})
    revision = item._committed["library_assets.json"]["revision"]
    item.library_store.characters[0].name = "DIRTY"
    item.library_store.characters.append(Character(id="dirty", name="Unsaved", description="not saved"))
    response = client.get("/library/assets")
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == revision
    assert [(asset["id"], asset["name"]) for asset in response.json()["characters"]] == [(saved.id, "Stored")]


def test_library_public_read_is_detached_from_live_and_committed_state(runtime):
    item, _, _ = runtime
    item.create_library_asset("character", {"name": "Stored", "description": "saved"})
    read = item.list_library_assets()
    read.characters[0].name = "client mutation"
    assert item.library_store.characters[0].name == "Stored"
    assert item.list_library_assets().characters[0].name == "Stored"
    item.library_store.characters[0].name = "later live mutation"
    assert read.characters[0].name == "client mutation"


def test_library_response_revision_and_assets_share_one_capture(runtime, monkeypatch):
    item, client, _ = runtime
    first = item.create_library_asset("character", {"name": "First", "description": "saved"})
    before = item._committed["library_assets.json"]["revision"]
    capture = item.list_library_assets
    def read_then_commit():
        snapshot = capture()
        item.create_library_asset("character", {"name": "Second", "description": "later save"})
        return snapshot
    monkeypatch.setattr(item, "list_library_assets", read_then_commit)
    response = client.get("/library/assets")
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == before
    assert [asset["id"] for asset in response.json()["characters"]] == [first.id]
    assert item._committed["library_assets.json"]["revision"] > before
