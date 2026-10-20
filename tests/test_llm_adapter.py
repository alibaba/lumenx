import json
from pathlib import Path

import httpx
import openai
import pytest

from src.apps.comic_gen.llm_adapter import LLMAdapter


@pytest.fixture
def minimax_transport(monkeypatch):
    """Capture real SDK requests without allowing network access."""
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    captured = {"requests": [], "content": "MiniMax response", "status": 200}
    sdk_client = openai.OpenAI
    clients = []

    def respond(request):
        captured["requests"].append(request)
        if captured["status"] != 200:
            return httpx.Response(
                captured["status"],
                json={"error": {"message": "model not found", "type": "invalid_request_error"}},
            )
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": json.loads(request.content)["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": captured["content"]},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def build_client(**kwargs):
        client = sdk_client(
            **kwargs,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
            max_retries=0,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(openai, "OpenAI", build_client)
    yield captured
    for client in clients:
        client.close()


def test_minimax_configuration_requires_its_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    adapter = LLMAdapter()
    assert adapter.is_configured is False
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    assert adapter.is_configured is True


@pytest.mark.parametrize(
    "base_url",
    [
        None,
        "https://api.minimax.cn/v1/",
        "https://api.minimaxi.com/v1/",
        "https://custom.example/minimax/v1/",
    ],
)
@pytest.mark.parametrize(
    "saved_model,request_model,expected_model",
    [
        (None, None, "MiniMax-M3.1-Flash"),
        ("MiniMax-M3", None, "MiniMax-M3"),
        ("MiniMax-M2.7", None, "MiniMax-M2.7"),
        ("custom-saved-model", None, "custom-saved-model"),
        ("MiniMax-M3", "MiniMax-M3.1-Flash", "MiniMax-M3.1-Flash"),
        (None, "MiniMax-M3", "MiniMax-M3"),
        ("MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7"),
        ("MiniMax-M3", "custom-request-model", "custom-request-model"),
    ],
)
def test_minimax_serialized_requests(
    monkeypatch, minimax_transport, base_url, saved_model, request_model, expected_model
):
    if base_url:
        monkeypatch.setenv("MINIMAX_BASE_URL", base_url)
    if saved_model:
        monkeypatch.setenv("MINIMAX_MODEL", saved_model)
    messages = [
        {"role": "system", "content": "Return JSON."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this frame."},
                {"type": "image_url", "image_url": {"url": "https://example.com/frame.png"}},
            ],
        },
    ]
    result = LLMAdapter().chat(
        messages, model=request_model, response_format={"type": "json_object"}
    )
    assert result == "MiniMax response"
    assert len(minimax_transport["requests"]) == 1
    request = minimax_transport["requests"][0]
    expected_base = (base_url or "https://api.minimax.io/v1").rstrip("/")
    assert str(request.url) == f"{expected_base}/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    # No thinking/effort override: let the service use its supported defaults.
    assert json.loads(request.content) == {
        "model": expected_model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }


def test_script_processor_uses_flash_at_request_boundary(minimax_transport):
    from src.apps.comic_gen.llm import ScriptProcessor

    minimax_transport["content"] = json.dumps({"characters": [], "scenes": []})
    script = ScriptProcessor().parse_novel("Example", "A traveler arrives.")
    assert script.title == "Example"
    assert len(minimax_transport["requests"]) == 1
    payload = json.loads(minimax_transport["requests"][0].content)
    assert payload["model"] == "MiniMax-M3.1-Flash"
    assert "A traveler arrives." in payload["messages"][0]["content"]
    assert set(payload) == {"model", "messages"}


def test_minimax_does_not_fallback_on_missing_model(minimax_transport):
    minimax_transport["status"] = 404
    with pytest.raises(RuntimeError, match="MiniMax API error:.*model not found"):
        LLMAdapter().chat([{"role": "user", "content": "Hello"}])
    assert len(minimax_transport["requests"]) == 1
    assert json.loads(minimax_transport["requests"][0].content)["model"] == "MiniMax-M3.1-Flash"


@pytest.mark.parametrize(
    "provider,key_env,expected_model,expected_url",
    [
        (
            None,
            "DASHSCOPE_API_KEY",
            "qwen3.7-plus",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        ("openai", "OPENAI_API_KEY", "gpt-4o", "https://api.openai.com/v1"),
    ],
)
def test_other_provider_defaults_unchanged(
    monkeypatch, minimax_transport, provider, key_env, expected_model, expected_url
):
    if provider:
        monkeypatch.setenv("LLM_PROVIDER", provider)
    else:
        monkeypatch.delenv("LLM_PROVIDER")
    monkeypatch.setenv(key_env, "test-key")
    for name in ("DASHSCOPE_BASE_URL", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    messages = [{"role": "user", "content": "Hello"}]
    assert LLMAdapter().chat(messages) == "MiniMax response"
    request = minimax_transport["requests"][0]
    assert str(request.url) == f"{expected_url}/chat/completions"
    assert json.loads(request.content) == {"model": expected_model, "messages": messages}


def test_minimax_documents_current_models_and_regional_protocol_endpoints():
    env_example = (Path(__file__).parents[1] / ".env.example").read_text()
    assert "https://api.minimax.io/v1" in env_example
    assert "https://api.minimax.cn/v1" in env_example
    assert "https://api.minimax.io/anthropic" in env_example
    assert "https://api.minimax.cn/anthropic" in env_example
    assert "MINIMAX_MODEL=MiniMax-M3.1-Flash" in env_example
    assert "Current models: MiniMax-M3.1-Flash (default), MiniMax-M3" in env_example
    assert "MiniMax-M2.7" not in env_example
    assert "LLMAdapter itself uses only the OpenAI-compatible endpoints above" in env_example
