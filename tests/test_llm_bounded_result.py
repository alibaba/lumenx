import sys
from types import SimpleNamespace

import pytest

from src.apps.comic_gen.llm_adapter import LLMAdapter
from src.core.compute_billing import bind_compute_billing, reset_compute_billing


class FakeCompletionClient:
    def __init__(self, *, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.options = []
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **options):
        self.options.append(options)
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        if not self.responses:
            raise AssertionError("unexpected completion request")
        response_item = self.responses.pop(0)
        if isinstance(response_item, Exception):
            raise response_item
        return response_item


def response(*, content="ok", usage=None, model=None):
    return SimpleNamespace(
        model=model,
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


def install_openai(monkeypatch, client):
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return client

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=create))
    return created


def configured_adapter():
    return LLMAdapter(env={"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "owned-key"})


def test_chat_result_reserves_and_settles_platform_credits(monkeypatch):
    client = FakeCompletionClient(responses=[response(content="ok", model="provider-model", usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})])
    install_openai(monkeypatch, client)
    events = []
    token = bind_compute_billing({
        "mode": "platform_credits",
        "reserve": lambda *args: events.append(("reserve", args)) or {"reservation_id": "res-1"},
        "settle": lambda *args: events.append(("settle", args)) or {"status": "settled"},
        "release": lambda *args: events.append(("release", args)),
    })
    try:
        result = configured_adapter().chat_result([{"role": "user", "content": "hello"}], model="requested-model", max_completion_tokens=12, timeout=5)
    finally:
        reset_compute_billing(token)
    assert result.total_tokens == 7
    assert events[0][0] == "reserve"
    assert events[1][0] == "settle"
    assert events[1][1][0] == "res-1"
    assert events[1][1][-2:] == (3, 4)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": ""},
        {"model": "   "},
        {"model": None},
        {"max_completion_tokens": 0},
        {"max_completion_tokens": -1},
        {"max_completion_tokens": True},
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": float("inf")},
        {"timeout": True},
        {"response_format": "json"},
        {"response_format": {"type": "json_schema"}},
        {"response_format": {"type": "json_object", "schema": {}}},
        {"messages": ["hello"]},
        {"messages": [{"role": "user", "content": "hello", "name": "extra"}]},
        {"messages": [{"role": "developer", "content": "hello"}]},
        {"messages": [{"role": "tool", "content": "hello"}]},
        {"messages": [{"role": "user", "content": ["hello"]}]},
        {"messages": [{"role": "user"}]},
        {"messages": [{"content": "hello"}]},
    ],
)
def test_chat_result_rejects_invalid_arguments_before_client_creation(monkeypatch, kwargs):
    client = FakeCompletionClient()
    created = install_openai(monkeypatch, client)
    params = {
        "messages": [{"role": "user", "content": "hello"}],
        "model": "explicit-model",
        "max_completion_tokens": 12,
        "timeout": 3,
    }
    params.update(kwargs)

    with pytest.raises(ValueError):
        configured_adapter().chat_result(**params)

    assert created == []
    assert client.options == []
    assert client.requests == []


def test_chat_result_sends_explicit_cap_timeout_zero_retries_and_response_format(monkeypatch):
    client = FakeCompletionClient(
        responses=[response(content='{"ok": true}', model="provider-model", usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})]
    )
    created = install_openai(monkeypatch, client)

    result = configured_adapter().chat_result(
        [{"role": "user", "content": "hello"}],
        model="requested-model",
        max_completion_tokens=12,
        timeout=5.5,
        response_format={"type": "json_object"},
    )

    assert len(created) == 1
    assert client.options == [{"timeout": 5.5, "max_retries": 0}]
    assert client.requests == [
        {
            "model": "requested-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 12,
            "response_format": {"type": "json_object"},
        }
    ]
    assert result.content == '{"ok": true}'
    assert result.requested_model == "requested-model"
    assert result.provider_model == "provider-model"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4
    assert result.total_tokens == 7


def test_chat_result_accepts_text_response_format(monkeypatch):
    client = FakeCompletionClient(responses=[response(content="plain text")])
    install_openai(monkeypatch, client)

    result = configured_adapter().chat_result(
        [{"role": "system", "content": "answer briefly"}, {"role": "assistant", "content": "ready"}],
        model="requested-model",
        max_completion_tokens=12,
        timeout=5.5,
        response_format={"type": "text"},
    )

    assert result.content == "plain text"
    assert client.requests[0]["response_format"] == {"type": "text"}


def test_chat_result_unknown_usage_is_none_not_zero(monkeypatch):
    client = FakeCompletionClient(responses=[response(usage=None)])
    install_openai(monkeypatch, client)

    result = configured_adapter().chat_result(
        [{"role": "user", "content": "hello"}],
        model="requested-model",
        max_completion_tokens=1,
        timeout=1,
    )

    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    assert result.total_tokens is None


def test_chat_result_preserves_actual_zero_usage(monkeypatch):
    client = FakeCompletionClient(
        responses=[response(usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))]
    )
    install_openai(monkeypatch, client)

    result = configured_adapter().chat_result(
        [{"role": "user", "content": "hello"}],
        model="requested-model",
        max_completion_tokens=1,
        timeout=1,
    )

    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.total_tokens == 0


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": -1, "completion_tokens": 0, "total_tokens": 0},
        {"prompt_tokens": "1", "completion_tokens": 0, "total_tokens": 0},
        {"prompt_tokens": True, "completion_tokens": 0, "total_tokens": 0},
    ],
)
def test_chat_result_rejects_invalid_reported_usage_without_retry(monkeypatch, usage):
    client = FakeCompletionClient(responses=[response(usage=usage)])
    install_openai(monkeypatch, client)

    with pytest.raises(RuntimeError, match="malformed"):
        configured_adapter().chat_result(
            [{"role": "user", "content": "hello"}],
            model="requested-model",
            max_completion_tokens=1,
            timeout=1,
        )

    assert len(client.requests) == 1


@pytest.mark.parametrize("bad_response", [response(content=""), response(content="   "), SimpleNamespace(choices=[])])
def test_chat_result_rejects_malformed_or_empty_content_without_retry(monkeypatch, bad_response):
    client = FakeCompletionClient(responses=[bad_response])
    install_openai(monkeypatch, client)

    with pytest.raises(RuntimeError, match="malformed"):
        configured_adapter().chat_result(
            [{"role": "user", "content": "hello"}],
            model="requested-model",
            max_completion_tokens=1,
            timeout=1,
        )

    assert len(client.requests) == 1


def test_chat_result_failure_is_single_call_no_fallback_and_sanitized(monkeypatch):
    client = FakeCompletionClient(error=RuntimeError("raw provider secret owned-key"))
    install_openai(monkeypatch, client)

    with pytest.raises(RuntimeError) as exc_info:
        configured_adapter().chat_result(
            [{"role": "user", "content": "hello"}],
            model="requested-model",
            max_completion_tokens=1,
            timeout=1,
        )

    assert len(client.requests) == 1
    assert "owned-key" not in str(exc_info.value)
    assert "raw provider" not in str(exc_info.value)
    assert "bounded chat failed" in str(exc_info.value)


def test_chat_result_client_creation_failure_is_opaque(monkeypatch):
    def create(**_kwargs):
        raise RuntimeError("sdk init leaked https://sensitive.example.invalid and owned-key")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=create))

    with pytest.raises(RuntimeError) as exc_info:
        configured_adapter().chat_result(
            [{"role": "user", "content": "hello"}],
            model="requested-model",
            max_completion_tokens=1,
            timeout=1,
        )

    message = str(exc_info.value)
    assert "bounded chat failed" in message
    assert "sensitive.example" not in message
    assert "owned-key" not in message


def test_chat_result_uses_explicit_environment_without_ambient_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key")
    client = FakeCompletionClient(responses=[response()])
    created = install_openai(monkeypatch, client)

    configured_adapter().chat_result(
        [{"role": "user", "content": "hello"}],
        model="requested-model",
        max_completion_tokens=1,
        timeout=1,
    )

    assert created == [{"api_key": "owned-key", "base_url": "https://api.openai.com/v1"}]


def test_legacy_chat_fallback_and_string_return_stay_unchanged(monkeypatch):
    client = FakeCompletionClient(
        responses=[
            RuntimeError("model not found"),
            response(content="legacy-ok"),
        ]
    )
    install_openai(monkeypatch, client)
    adapter = LLMAdapter(env={"DASHSCOPE_API_KEY": "dashscope-key"})

    assert adapter.chat([{"role": "user", "content": "hello"}]) == "legacy-ok"
    assert [request["model"] for request in client.requests] == ["qwen3.7-plus", "qwen3.6-plus"]
