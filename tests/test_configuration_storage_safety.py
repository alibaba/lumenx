"""Configuration failures must not publish unsaved provider settings."""

import json
import os
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import pytest
from fastapi.testclient import TestClient
from dotenv import dotenv_values

from src.apps.comic_gen import api, pipeline as pipeline_module
from src.utils.json_storage import StorageError


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(pipeline_module, name, Mock())
    pipeline = pipeline_module.ComicGenPipeline(data_dir=tmp_path / "output", script_processor=Mock())
    path = tmp_path / "settings" / "config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"DASHSCOPE_API_KEY": "old-test-key", "QWEN_BASE_URL": "https://old.invalid"}))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-test-key")
    monkeypatch.setenv("QWEN_BASE_URL", "https://old.invalid")
    monkeypatch.setenv("OSS_ENABLE", os.environ.get("OSS_ENABLE", ""))
    app = api.create_app(data_dir=pipeline.data_dir, user_data_dir=path.parent,
                         load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        yield client, path


def test_failed_configuration_save_preserves_active_environment(configuration, monkeypatch):
    client, path = configuration
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise StorageError("injected configuration persistence failure")
    monkeypatch.setattr(api, "save_user_config", fail)
    response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "new-test-key"})
    assert response.status_code >= 400
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"
    assert path.read_bytes() == before


@pytest.mark.parametrize("contents", ['{broken', 'null', '[]', '{"duplicate":"one","duplicate":"two"}'])
def test_corrupt_configuration_is_not_replaced(configuration, contents):
    client, path = configuration
    path.write_text(contents)
    response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "new-test-key"})
    assert response.status_code >= 400
    assert path.read_text() == contents
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"


def test_configuration_symlink_is_not_written_through(configuration):
    client, path = configuration
    target = path.parent / "untouched.json"
    original = path.read_bytes()
    path.rename(target)
    path.symlink_to(target)
    response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "new-test-key"})
    assert response.status_code >= 400
    assert target.read_bytes() == original
    assert path.is_symlink()
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"


def test_updates_and_endpoint_removal_use_one_persisted_replacement(configuration, monkeypatch):
    client, path = configuration
    path.write_text(json.dumps({"DASHSCOPE_API_KEY": "old-test-key", "KLING_BASE_URL": "https://old.invalid", "UNRELATED": "keep"}))
    monkeypatch.setenv("KLING_BASE_URL", "https://old.invalid")
    write = Mock(wraps=api.atomic_write_json)
    monkeypatch.setattr(api, "atomic_write_json", write)
    response = client.post("/config/env", json={
        "DASHSCOPE_API_KEY": "updated-test-key", "endpoint_overrides": {"KLING_BASE_URL": ""},
    })
    assert response.status_code == 200
    assert write.call_count == 1
    assert json.loads(path.read_text()) == {"DASHSCOPE_API_KEY": "updated-test-key", "UNRELATED": "keep"}
    active = client.app.state.runtime_environment.snapshot()
    assert active["DASHSCOPE_API_KEY"] == "updated-test-key"
    assert "KLING_BASE_URL" not in active
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"
    assert os.environ["KLING_BASE_URL"] == "https://old.invalid"


def test_failed_endpoint_removal_preserves_environment_and_file(configuration, monkeypatch):
    client, path = configuration
    monkeypatch.setenv("KLING_BASE_URL", "https://old.invalid")
    path.write_text(json.dumps({"KLING_BASE_URL": "https://old.invalid"}))
    before = path.read_bytes()
    monkeypatch.setattr(api, "atomic_write_json", Mock(side_effect=StorageError("injected before replace")))
    response = client.post("/config/env", json={"endpoint_overrides": {"KLING_BASE_URL": ""}})
    assert response.status_code == 503
    assert response.json()["commit_uncertain"] is False
    assert path.read_bytes() == before
    assert os.environ["KLING_BASE_URL"] == "https://old.invalid"


def test_uncertain_configuration_save_is_reported_without_runtime_publish(configuration, monkeypatch):
    client, path = configuration
    write = api.atomic_write_json
    def uncertain(target, value):
        write(target, value)
        raise StorageError("injected directory fsync failure", commit_uncertain=True)
    monkeypatch.setattr(api, "atomic_write_json", uncertain)
    response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "possibly-saved-test-key"})
    assert response.status_code == 503
    assert response.json()["commit_uncertain"] is True
    assert json.loads(path.read_text())["DASHSCOPE_API_KEY"] == "possibly-saved-test-key"
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"


def test_masked_secret_preserves_value_and_explicit_false_round_trips(configuration):
    client, path = configuration
    response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "••••••••", "OSS_ENABLE": False})
    assert response.status_code == 200
    saved = json.loads(path.read_text())
    assert saved["DASHSCOPE_API_KEY"] == "old-test-key"
    assert saved["OSS_ENABLE"] == "false"
    assert client.app.state.runtime_environment.snapshot()["OSS_ENABLE"] == "false"
    assert client.get("/config/env").json()["OSS_ENABLE"] is False


@pytest.mark.parametrize("value", ["bad\0setting", "\ud800"])
def test_invalid_environment_text_is_rejected_before_save(configuration, value):
    client, path = configuration
    before = path.read_bytes()
    response = client.post("/config/env", content=json.dumps({"DASHSCOPE_API_KEY": value}), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert path.read_bytes() == before
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"


def test_dotenv_batch_preserves_comments_unrelated_entries_and_quoting(configuration, monkeypatch):
    client, path = configuration
    dotenv = path.with_name("settings.env")
    dotenv.write_text("# keep comment\nUNRELATED='${UNCHANGED}'\nKLING_BASE_URL='https://old.invalid'\n")
    monkeypatch.setattr(api, "get_user_config_path", lambda **kwargs: str(dotenv))
    monkeypatch.setenv("KLING_BASE_URL", "https://old.invalid")
    replacement = "quoted'value\nnext line"
    response = client.post("/config/env", json={"DASHSCOPE_API_KEY": replacement, "endpoint_overrides": {"KLING_BASE_URL": ""}})
    assert response.status_code == 200
    assert "# keep comment" in dotenv.read_text()
    parsed = dotenv_values(stream=StringIO(dotenv.read_text()), interpolate=False)
    assert parsed == {"UNRELATED": "${UNCHANGED}", "DASHSCOPE_API_KEY": replacement}
    assert client.app.state.runtime_environment.snapshot()["DASHSCOPE_API_KEY"] == replacement
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"
    assert not list(path.parent.glob(".lumenx-config-*"))


def test_dotenv_staging_failure_cannot_partially_save_or_publish(configuration, monkeypatch):
    client, path = configuration
    dotenv = path.with_name("settings.env")
    dotenv.write_text("# preserve\nDASHSCOPE_API_KEY='old-test-key'\n")
    before = dotenv.read_bytes()
    monkeypatch.setattr(api, "get_user_config_path", lambda **kwargs: str(dotenv))
    set_key = api.set_key
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second key failure")
        return set_key(*args, **kwargs)
    monkeypatch.setattr(api, "set_key", fail_second)
    response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "new-test-key", "OSS_ENDPOINT": "new.invalid"})
    assert response.status_code == 503
    assert dotenv.read_bytes() == before
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"
    assert not list(path.parent.glob(".lumenx-config-*"))


def test_concurrent_config_commands_preserve_independent_fields(configuration, monkeypatch):
    client, path = configuration
    monkeypatch.setenv("VIDU_API_KEY", "old-test-key")
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda body: client.post("/config/env", json=body), [
            {"DASHSCOPE_API_KEY": "new-dashscope-test-key"}, {"VIDU_API_KEY": "new-vidu-test-key"},
        ]))
    assert [response.status_code for response in responses] == [200, 200]
    stored = json.loads(path.read_text())
    active = client.app.state.runtime_environment.snapshot()
    assert stored["DASHSCOPE_API_KEY"] == active["DASHSCOPE_API_KEY"] == "new-dashscope-test-key"
    assert stored["VIDU_API_KEY"] == active["VIDU_API_KEY"] == "new-vidu-test-key"
    assert os.environ["DASHSCOPE_API_KEY"] == os.environ["VIDU_API_KEY"] == "old-test-key"


def test_saved_empty_value_remains_cleared_when_loading_over_inherited_environment(configuration, monkeypatch):
    client, path = configuration
    assert client.post("/config/env", json={"DASHSCOPE_API_KEY": ""}).status_code == 200
    monkeypatch.setenv("DASHSCOPE_API_KEY", "inherited-old-test-key")
    loaded = api.load_user_config(user_data_dir=path.parent)
    assert loaded["DASHSCOPE_API_KEY"] == ""
    assert client.app.state.runtime_environment.snapshot()["DASHSCOPE_API_KEY"] == ""
    assert os.environ["DASHSCOPE_API_KEY"] == "inherited-old-test-key"


@pytest.mark.parametrize("contents", [
    '{broken', 'null', '[]',
    '{"DASHSCOPE_API_KEY":"new-test-key","OSS_ENABLE":true}',
    '{"DASHSCOPE_API_KEY":"new-test-key","INVALID=KEY":"text"}',
])
def test_invalid_startup_configuration_is_not_partially_applied(configuration, contents):
    _, path = configuration
    path.write_text(contents)
    with pytest.raises(StorageError):
        api.load_user_config(user_data_dir=path.parent)
    assert os.environ["DASHSCOPE_API_KEY"] == "old-test-key"
    assert path.read_text() == contents
