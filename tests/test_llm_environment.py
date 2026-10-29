import sys
from types import SimpleNamespace

import pytest

from src.apps.comic_gen.llm_adapter import LLMAdapter


@pytest.fixture
def clients(monkeypatch):
    created = []

    def create(**kwargs):
        client = SimpleNamespace(settings=kwargs)
        created.append(client)
        return client

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=create))
    return created


def test_explicit_dashscope_clients_keep_their_own_credentials_and_endpoints(monkeypatch, clients):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example-key")
    first = LLMAdapter(env={"DASHSCOPE_API_KEY": "first-example-key", "DASHSCOPE_BASE_URL": "https://a.example.invalid/"})
    second = LLMAdapter(env={"DASHSCOPE_API_KEY": "second-example-key", "DASHSCOPE_BASE_URL": "https://b.example.invalid"})
    assert first.is_configured and second.is_configured
    assert first.provider == second.provider == "dashscope"
    first_client = first._get_client()
    second_client = second._get_client()
    assert first._get_client() is first_client
    assert first_client.settings == {"api_key": "first-example-key", "base_url": "https://a.example.invalid/compatible-mode/v1"}
    assert second_client.settings == {"api_key": "second-example-key", "base_url": "https://b.example.invalid/compatible-mode/v1"}
    assert len(clients) == 2


@pytest.mark.parametrize("env", [{}, {"LLM_PROVIDER": "openai"}, {"DASHSCOPE_API_KEY": ""}])
def test_empty_explicit_credentials_cannot_fall_back_to_sdk_environment(monkeypatch, clients, env):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-example")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-dashscope-example")
    adapter = LLMAdapter(env=env)
    assert not adapter.is_configured
    with pytest.raises(RuntimeError, match="configured"):
        adapter._get_client()
    assert clients == []


def test_explicit_openai_configuration_is_snapshotted_before_client_creation(monkeypatch, clients):
    monkeypatch.setenv("OPENAI_MODEL", "ambient-model")
    config = {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "owned-example-key", "OPENAI_BASE_URL": "https://owned.example.invalid/v1", "OPENAI_MODEL": "owned-model"}
    adapter = LLMAdapter(env=config)
    config.update(OPENAI_API_KEY="changed-example-key", OPENAI_MODEL="changed-model")
    assert adapter._get_default_model() == "owned-model"
    assert adapter._get_client().settings == {"api_key": "owned-example-key", "base_url": "https://owned.example.invalid/v1"}


def test_explicit_openai_defaults_do_not_borrow_ambient_values(monkeypatch, clients):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ambient.example.invalid")
    monkeypatch.setenv("OPENAI_MODEL", "ambient-model")
    adapter = LLMAdapter(env={"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "owned-example-key"})
    assert adapter._get_default_model() == "gpt-4o"
    assert adapter._get_client().settings["base_url"] == "https://api.openai.com/v1"


def test_standalone_constructor_retains_environment_compatibility(monkeypatch, clients):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "standalone-example-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://standalone.example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "standalone-model")
    adapter = LLMAdapter()
    assert adapter.is_configured
    assert adapter._get_default_model() == "standalone-model"
    assert adapter._get_client().settings == {"api_key": "standalone-example-key", "base_url": "https://standalone.example.invalid/v1"}
