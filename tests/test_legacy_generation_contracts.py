"""Legacy generation endpoints should not publish placeholder media."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, Script, StoryboardFrame, GenerationStatus


def make_script() -> Script:
    return Script(
        id="p",
        title="legacy",
        original_text="text",
        characters=[Character(id="c", name="Lin", description="", voice_id="voice")],
        frames=[StoryboardFrame(id="f", scene_id="", character_ids=["c"], action_description="boom", dialogue="hello")],
        created_at=1,
        updated_at=1,
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    script = make_script()
    item.scripts[script.id] = script
    item._save_data()
    yield item
    item.close()


@pytest.fixture
def client(runtime):
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime, warmup_demucs=False)
    with TestClient(app) as test_client:
        yield test_client


def test_legacy_generate_video_http_is_unsupported_but_missing_project_stays_404(client):
    response = client.post("/projects/p/generate_video")
    assert response.status_code == 501
    assert "/projects/{id}/video_tasks" in response.json()["detail"]

    missing = client.post("/projects/missing/generate_video")
    assert missing.status_code == 404


def test_legacy_generate_audio_delegates_dialogue_batch_without_sfx_or_bgm(runtime, client, monkeypatch):
    dialogue_calls = []

    def fake_batch(script_id):
        dialogue_calls.append(script_id)
        script = runtime.scripts[script_id]
        script.frames[0].audio_url = "audio/dialogue.mp3"
        script.frames[0].status = GenerationStatus.COMPLETED
        runtime._commit_storage("projects.json")
        return runtime.read_committed_script(script_id), {"generated": 1}, []

    monkeypatch.setattr(runtime, "generate_dialogue_audio_batch", fake_batch)
    monkeypatch.setattr(runtime.audio_generator, "generate_sfx", Mock(side_effect=AssertionError("SFX must not run")))
    monkeypatch.setattr(runtime.audio_generator, "generate_sfx_from_video", Mock(side_effect=AssertionError("V2A SFX must not run")))
    monkeypatch.setattr(runtime.audio_generator, "generate_bgm", Mock(side_effect=AssertionError("BGM must not run")))

    response = client.post("/projects/p/generate_audio")

    assert response.status_code == 200, response.text
    assert dialogue_calls == ["p"]
    assert response.json()["frames"][0]["audio_url"] == "audio/dialogue.mp3"
    assert response.json()["frames"][0].get("sfx_url") is None
    assert not list((runtime.data_dir / "audio" / "sfx").glob("*.mp3"))
    assert not list((runtime.data_dir / "audio" / "bgm").glob("*.mp3"))


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/projects/p/mix/generate_sfx", "SFX generation is not supported"),
        ("/projects/p/mix/generate_bgm", "Generative BGM is not supported"),
    ],
)
def test_legacy_mix_generation_routes_are_unsupported_without_placeholder_files(runtime, client, monkeypatch, path, message):
    monkeypatch.setattr(runtime.audio_generator, "generate_sfx", Mock(side_effect=AssertionError("SFX must not run")))
    monkeypatch.setattr(runtime.audio_generator, "generate_sfx_from_video", Mock(side_effect=AssertionError("V2A SFX must not run")))
    monkeypatch.setattr(runtime.audio_generator, "generate_bgm", Mock(side_effect=AssertionError("BGM must not run")))

    response = client.post(path)

    assert response.status_code == 501
    assert message in response.json()["detail"]
    assert not list((runtime.data_dir / "audio").rglob("*.mp3"))
