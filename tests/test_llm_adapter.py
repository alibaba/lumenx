import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.comic_gen.llm_adapter import LLMAdapter


@pytest.fixture
def minimax_client(monkeypatch):
    captured = {}
    create = Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="MiniMax response"))]
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def build_client(**kwargs):
        captured["kwargs"] = kwargs
        captured["client"] = client
        return client

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=build_client))
    return captured


def test_minimax_configuration_requires_its_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    adapter = LLMAdapter()

    assert adapter.is_configured is False

    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    assert adapter.is_configured is True


def test_minimax_uses_global_defaults(monkeypatch, minimax_client):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    messages = [{"role": "user", "content": "Hello"}]

    result = LLMAdapter().chat(messages)

    assert result == "MiniMax response"
    assert minimax_client["kwargs"] == {
        "api_key": "test-key",
        "base_url": "https://api.minimax.io/v1",
    }
    minimax_client["client"].chat.completions.create.assert_called_once_with(
        model="MiniMax-M3",
        messages=messages,
    )


def test_minimax_supports_china_endpoint_and_secondary_model(monkeypatch, minimax_client):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M2.7")
    messages = [{"role": "user", "content": "Hello"}]

    LLMAdapter().chat(messages, response_format={"type": "json_object"})

    assert minimax_client["kwargs"]["base_url"] == "https://api.minimaxi.com/v1"
    minimax_client["client"].chat.completions.create.assert_called_once_with(
        model="MiniMax-M2.7",
        messages=messages,
        response_format={"type": "json_object"},
    )


def test_minimax_documents_regional_protocol_endpoints():
    env_example = (Path(__file__).parents[1] / ".env.example").read_text()

    assert "https://api.minimax.io/v1" in env_example
    assert "https://api.minimaxi.com/v1" in env_example
    assert "https://api.minimax.io/anthropic" in env_example
    assert "https://api.minimaxi.com/anthropic" in env_example
    assert "MiniMax-M3" in env_example
    assert "MiniMax-M2.7" in env_example
