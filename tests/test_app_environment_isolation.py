"""Real app assembly must not publish provider configuration process-wide."""

import json
import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api


@contextmanager
def application(tmp_path, name, settings):
    user_root = tmp_path / name / "user"
    user_root.mkdir(parents=True)
    (user_root / "config.json").write_text(json.dumps(settings))
    app = api.create_app(
        data_dir=tmp_path / name / "output", user_data_dir=user_root,
        access_mode="local", recover_orphan_tasks=False, warmup_demucs=False,
    )
    with TestClient(app) as client:
        yield app, client


def settings(name):
    return {"DASHSCOPE_API_KEY": f"example-{name}", "DASHSCOPE_BASE_URL": f"https://{name}.invalid", "OSS_BASE_PATH": name}


def test_two_app_instances_keep_config_and_process_environment_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_check_mulerun_cli_status", lambda request: False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example")
    monkeypatch.setenv("OSS_BASE_PATH", "ambient")
    before = dict(os.environ)
    with application(tmp_path, "first", settings("first")) as (_, first):
        original = first.get("/config/env").json()
        with application(tmp_path, "second", settings("second")) as (_, second):
            assert second.get("/config/env").json()["OSS_BASE_PATH"] == "second"
            assert first.get("/config/env").json() == original
            assert os.environ == before


def test_empty_owned_configuration_cannot_borrow_ambient_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_check_mulerun_cli_status", lambda request: False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example")
    monkeypatch.setenv("OSS_BASE_PATH", "ambient")
    with application(tmp_path, "empty", {}) as (app, client):
        result = client.get("/config/env").json()
        assert result["DASHSCOPE_API_KEY"] == ""
        assert result["OSS_BASE_PATH"] == ""
        assert result["secrets_configured"]["DASHSCOPE_API_KEY"] is False
        assert not app.state.pipeline.script_processor.is_configured
        with pytest.raises(RuntimeError, match="configured"):
            _ = app.state.pipeline.video_generator.model.api_key


def test_app_update_retires_only_its_clients_after_save(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_check_mulerun_cli_status", lambda request: False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example")
    with application(tmp_path, "first", settings("first")) as (first_app, first):
        with application(tmp_path, "second", settings("second")) as (second_app, second):
            original = first_app.state.pipeline.video_generator.model
            other = second_app.state.pipeline.video_generator.model
            assert original.api_key == "example-first"
            assert other.api_key == "example-second"
            response = first.post("/config/env", json={"DASHSCOPE_API_KEY": "updated-example", "endpoint_overrides": {"DASHSCOPE_BASE_URL": ""}})
            assert response.status_code == 200, response.text
            assert first_app.state.pipeline.video_generator.model.api_key == "updated-example"
            assert first_app.state.pipeline.video_generator.model is not original
            assert original.api_key == "example-first"
            assert second_app.state.pipeline.video_generator.model is other
            assert second.get("/config/env").json()["OSS_BASE_PATH"] == "second"
            assert "DASHSCOPE_BASE_URL" not in first.get("/config/env").json()["endpoint_overrides"]
            assert os.environ["DASHSCOPE_API_KEY"] == "ambient-example"


def test_owned_uploader_and_generation_components_share_app_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_check_mulerun_cli_status", lambda request: False)
    with application(tmp_path, "first", settings("first")) as (app, _):
        pipeline = app.state.pipeline
        assert pipeline.asset_generator.model.api_key == "example-first"
        assert pipeline.storyboard_generator.model.api_key == "example-first"
        assert pipeline.audio_generator.tts.api_key == "example-first"
        assert pipeline.script_processor.llm._env["DASHSCOPE_API_KEY"] == "example-first"
        assert pipeline.oss_uploader().base_path == "first"
        assert pipeline.oss_uploader().output_root == str(pipeline.data_dir)


def test_storyboard_collects_references_in_its_own_oss_namespace(tmp_path, monkeypatch):
    from pathlib import Path
    from src.apps.comic_gen.models import Character, Scene, StoryboardFrame

    monkeypatch.setenv("OSS_BASE_PATH", "ambient")
    captured = []
    with application(tmp_path, "first", settings("first")) as (app, _):
        generator = app.state.pipeline.storyboard_generator

        def generate(prompt, output_path, **kwargs):
            captured.extend(kwargs["ref_image_paths"])
            Path(output_path).write_bytes(b"synthetic image")
            return output_path, 0.0

        monkeypatch.setattr(generator.model, "generate", generate)
        character = Character(id="hero", name="Hero", description="hero", image_url="first/hero.png")
        scene = Scene(id="scene", name="Scene", description="scene", image_url="first/scene.png")
        frame = StoryboardFrame(id="frame", character_ids=["hero"], scene_id="scene", description="test")
        generator.generate_frame(frame, [character], scene)

    assert set(captured) == {"first/hero.png", "first/scene.png"}
