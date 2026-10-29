from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import dashscope
import pytest
from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
from dashscope.api_entities.http_request import HttpRequest
from dashscope.audio.tts_v2 import SpeechSynthesizer

from src.audio.tts import TTSProcessor


@pytest.fixture(autouse=True)
def stable_dashscope_user_agent(monkeypatch):
    monkeypatch.setattr("platform.platform", lambda: "test-platform")
    monkeypatch.setattr("platform.processor", lambda: "test-cpu")


def _response(url="https://offline.invalid/audio.mp3", request_id="qwen-request"):
    return SimpleNamespace(
        output=SimpleNamespace(audio=SimpleNamespace(url=url)),
        request_id=request_id,
    )


def test_constructor_snapshots_explicit_env_without_mutating_dashscope_global(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-env-key")
    monkeypatch.setattr(dashscope, "api_key", "ambient-global-key")
    config = {"DASHSCOPE_API_KEY": "owned-key"}

    processor = TTSProcessor(env=config)
    config["DASHSCOPE_API_KEY"] = "mutated-key"

    assert processor.api_key == "owned-key"
    assert dashscope.api_key == "ambient-global-key"


def test_empty_explicit_env_prepares_but_fails_before_cosyvoice_sdk(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-env-key")
    monkeypatch.setattr(dashscope, "api_key", "ambient-global-key")
    constructed = []

    def forbidden_constructor(self, **kwargs):  # pragma: no cover - proves fail-fast if not called
        constructed.append(kwargs)

    monkeypatch.setattr(SpeechSynthesizer, "__init__", forbidden_constructor)
    processor = TTSProcessor(env={})
    prepared = processor.prepare("hello", voice="longanyang")

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        processor.execute(prepared, str(tmp_path / "audio.mp3"))

    assert constructed == []
    assert dashscope.api_key == "ambient-global-key"
    assert not list(tmp_path.iterdir())


def test_empty_explicit_env_fails_before_qwen_sdk_or_download(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-env-key")
    monkeypatch.setattr(dashscope, "api_key", "ambient-global-key")
    qwen_calls = []
    downloads = []
    monkeypatch.setattr(dashscope.MultiModalConversation, "call", lambda **kwargs: qwen_calls.append(kwargs))
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: downloads.append((args, kwargs)))

    processor = TTSProcessor(env={})
    prepared = processor.prepare("hello", voice="Cherry")

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        processor.execute(prepared, str(tmp_path / "qwen.mp3"))

    assert qwen_calls == []
    assert downloads == []
    assert dashscope.api_key == "ambient-global-key"


def test_cosyvoice_constructor_failure_restores_dashscope_globals(monkeypatch, tmp_path):
    monkeypatch.setattr(dashscope, "api_key", "ambient-global-key")
    monkeypatch.setattr(dashscope, "base_websocket_api_url", "wss://ambient.example/ws", raising=False)
    seen = {}

    def failing_constructor(self, **kwargs):
        seen["api_key"] = dashscope.api_key
        seen["url"] = kwargs.get("url")
        raise RuntimeError("constructor exploded")

    monkeypatch.setattr(SpeechSynthesizer, "__init__", failing_constructor)
    processor = TTSProcessor(env={
        "DASHSCOPE_API_KEY": "owned-key",
        "DASHSCOPE_TTS_WEBSOCKET_URL": "wss://owned.example/ws",
    })
    prepared = processor.prepare("hello", voice="longanyang")

    with pytest.raises(RuntimeError, match="constructor exploded"):
        processor.execute(prepared, str(tmp_path / "audio.mp3"))

    assert seen == {"api_key": "owned-key", "url": "wss://owned.example/ws"}
    assert dashscope.api_key == "ambient-global-key"
    assert dashscope.base_websocket_api_url == "wss://ambient.example/ws"


def test_explicit_key_only_cosyvoice_uses_default_url_not_ambient_websocket_global(monkeypatch, tmp_path):
    monkeypatch.setattr(dashscope, "api_key", "ambient-global-key")
    monkeypatch.setattr(dashscope, "base_websocket_api_url", "wss://ambient.example/ws", raising=False)
    captured = {}

    def constructor(self, **kwargs):
        self.captured_key = dashscope.api_key
        self.captured_url = kwargs.get("url")
        captured.update(key=self.captured_key, url=self.captured_url)

    monkeypatch.setattr(SpeechSynthesizer, "__init__", constructor)
    monkeypatch.setattr(SpeechSynthesizer, "call", lambda self, text: b"audio")
    monkeypatch.setattr(SpeechSynthesizer, "get_last_request_id", lambda self: "request-id")
    monkeypatch.setattr(SpeechSynthesizer, "get_first_package_delay", lambda self: 0.0)

    processor = TTSProcessor(env={"DASHSCOPE_API_KEY": "owned-key"})
    processor.execute(processor.prepare("hello", voice="longanyang"), str(tmp_path / "audio.mp3"))

    assert captured == {
        "key": "owned-key",
        "url": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
    }
    assert dashscope.api_key == "ambient-global-key"
    assert dashscope.base_websocket_api_url == "wss://ambient.example/ws"


def test_concurrent_cosyvoice_calls_keep_captured_keys_and_unlock_before_call(monkeypatch, tmp_path):
    monkeypatch.setattr(dashscope, "api_key", "ambient-global-key")
    monkeypatch.setattr(dashscope, "base_websocket_api_url", "wss://ambient.example/ws", raising=False)
    barrier = Barrier(2)
    constructed = []
    calls = []

    def constructor(self, **kwargs):
        self.captured_key = dashscope.api_key
        self.captured_url = kwargs.get("url")
        self.request_id = f"request-{self.captured_key}"
        constructed.append((self.captured_key, self.captured_url))

    def call(self, text):
        barrier.wait(timeout=2)
        calls.append((text, self.captured_key, self.captured_url, dashscope.api_key, dashscope.base_websocket_api_url))
        return f"audio-{self.captured_key}".encode()

    monkeypatch.setattr(SpeechSynthesizer, "__init__", constructor)
    monkeypatch.setattr(SpeechSynthesizer, "call", call)
    monkeypatch.setattr(SpeechSynthesizer, "get_last_request_id", lambda self: self.request_id)
    monkeypatch.setattr(SpeechSynthesizer, "get_first_package_delay", lambda self: 0.125)

    first = TTSProcessor(env={"DASHSCOPE_API_KEY": "key-a", "DASHSCOPE_TTS_WEBSOCKET_URL": "wss://a.example/ws"})
    second = TTSProcessor(env={"DASHSCOPE_API_KEY": "key-b", "DASHSCOPE_TTS_WEBSOCKET_URL": "wss://b.example/ws"})
    prepared_a = first.prepare("text-a", voice="longanyang")
    prepared_b = second.prepare("text-b", voice="longanyang")

    with ThreadPoolExecutor(max_workers=2) as pool:
        result_a = pool.submit(first.execute, prepared_a, str(tmp_path / "a.mp3"))
        result_b = pool.submit(second.execute, prepared_b, str(tmp_path / "b.mp3"))
        outputs = {result_a.result(timeout=3)[0], result_b.result(timeout=3)[0]}

    assert outputs == {str(tmp_path / "a.mp3"), str(tmp_path / "b.mp3")}
    assert sorted(constructed) == [("key-a", "wss://a.example/ws"), ("key-b", "wss://b.example/ws")]
    assert sorted((text, key, url) for text, key, url, _, _ in calls) == [
        ("text-a", "key-a", "wss://a.example/ws"),
        ("text-b", "key-b", "wss://b.example/ws"),
    ]
    assert all(global_key == "ambient-global-key" and global_url == "wss://ambient.example/ws"
               for _, _, _, global_key, global_url in calls)
    assert dashscope.api_key == "ambient-global-key"
    assert dashscope.base_websocket_api_url == "wss://ambient.example/ws"
    assert Path(tmp_path / "a.mp3").read_bytes() == b"audio-key-a"
    assert Path(tmp_path / "b.mp3").read_bytes() == b"audio-key-b"


def test_qwen3_key_only_env_uses_default_http_base_not_ambient_global(monkeypatch, tmp_path):
    monkeypatch.setattr(dashscope, "api_key", "ambient-global-key")
    monkeypatch.setattr(dashscope, "base_http_api_url", "https://ambient.example/api/v1", raising=False)
    captured = {}
    downloads = []

    def request_call(self):
        captured["url"] = self.url
        captured["api_key"] = self.headers["Authorization"].removeprefix("Bearer ")
        return DashScopeAPIResponse(
            status_code=200, request_id="owned-request",
            output={"audio": {"url": "https://offline.invalid/audio.mp3"}},
        )

    def download(url, **kwargs):
        downloads.append((url, kwargs))
        return SimpleNamespace(content=b"qwen audio", raise_for_status=lambda: None)

    monkeypatch.setattr("platform.processor", lambda: "test-cpu")
    monkeypatch.setattr(HttpRequest, "call", request_call)
    monkeypatch.setattr("requests.get", download)
    processor = TTSProcessor(env={"DASHSCOPE_API_KEY": "owned-qwen-key"})
    prepared = processor.prepare("hello", voice="Cherry")

    result = processor.execute(prepared, str(tmp_path / "qwen.mp3"))

    assert result == (str(tmp_path / "qwen.mp3"), 0.0, "owned-request")
    assert captured == {
        "api_key": "owned-qwen-key",
        "url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    }
    assert dashscope.api_key == "ambient-global-key"
    assert dashscope.base_http_api_url == "https://ambient.example/api/v1"
    assert downloads == [("https://offline.invalid/audio.mp3", {"timeout": 30})]
    assert Path(result[0]).read_bytes() == b"qwen audio"


def test_qwen3_configured_host_base_resolves_service_path_without_url_parameter(monkeypatch, tmp_path):
    captured = {}
    downloads = []

    def request_call(self):
        captured["url"] = self.url
        captured["api_key"] = self.headers["Authorization"].removeprefix("Bearer ")
        captured["parameters"] = dict(self.data.parameters)
        return DashScopeAPIResponse(
            status_code=200, request_id="owned-request",
            output={"audio": {"url": "https://offline.invalid/audio.mp3"}},
        )

    def download(url, **kwargs):
        downloads.append((url, kwargs))
        return SimpleNamespace(content=b"qwen audio", raise_for_status=lambda: None)

    monkeypatch.setattr(HttpRequest, "call", request_call)
    monkeypatch.setattr("requests.get", download)
    processor = TTSProcessor(env={
        "DASHSCOPE_API_KEY": "owned-qwen-key",
        "DASHSCOPE_BASE_URL": "https://owned.example.invalid",
    })

    processor.execute(processor.prepare("hello", voice="Cherry"), str(tmp_path / "qwen.mp3"))

    assert captured["api_key"] == "owned-qwen-key"
    assert captured["url"] == "https://owned.example.invalid/api/v1/services/aigc/multimodal-generation/generation"
    assert "url" not in captured["parameters"]
    assert downloads == [("https://offline.invalid/audio.mp3", {"timeout": 30})]
