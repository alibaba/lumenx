import sys
from types import SimpleNamespace

import pytest

from src.models.qwen_vl import QwenVLModel


@pytest.fixture
def clients(monkeypatch):
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(settings=kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=create))
    return created


def test_multimodal_clients_keep_separate_explicit_configuration(monkeypatch, clients):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example")
    first_config = {"DASHSCOPE_API_KEY": "first-example", "DASHSCOPE_BASE_URL": "https://first.example.invalid/"}
    first = QwenVLModel({}, env=first_config)
    second = QwenVLModel({}, env={"DASHSCOPE_API_KEY": "second-example", "DASHSCOPE_BASE_URL": "https://second.example.invalid"})
    first_config.update(DASHSCOPE_API_KEY="changed-example", DASHSCOPE_BASE_URL="https://changed.example.invalid")
    first_client = first._get_client()
    assert first_client.settings == {"api_key": "first-example", "base_url": "https://first.example.invalid/compatible-mode/v1", "timeout": 120.0}
    assert second._get_client().settings["api_key"] == "second-example"
    assert second._get_client().settings["base_url"] == "https://second.example.invalid/compatible-mode/v1"
    assert first._get_client() is first_client
    assert len(clients) == 2


@pytest.mark.parametrize("env", [{}, {"DASHSCOPE_API_KEY": ""}])
def test_empty_explicit_multimodal_credentials_fail_before_sdk(monkeypatch, clients, env):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-example")
    model = QwenVLModel({}, env=env)
    assert not model.api_key
    with pytest.raises(RuntimeError, match="configured"):
        model._get_client()
    assert clients == []


def test_multimodal_standalone_constructor_uses_process_config(monkeypatch, clients):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "standalone-example")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://standalone.example.invalid")
    model = QwenVLModel({"params": {"model_name": "test-model"}})
    assert model.model_name == "test-model"
    assert model._get_client().settings["api_key"] == "standalone-example"
    assert model._get_client().settings["base_url"] == "https://standalone.example.invalid/compatible-mode/v1"
