from src.audio.tts import (
    DEFAULT_OPENAI_TTS_MODEL,
    OPENAI_COMPATIBLE_VOICES,
    TTSProcessor,
)


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return {"error": "unexpected"}

    def iter_content(self, chunk_size=8192):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]


def test_openai_tts_uses_audio_speech_endpoint(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse(
            b"fake-mp3-bytes",
            headers={"x-request-id": "req_openai_tts_123"},
        )

    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TTS_API_KEY", "tts-key")
    monkeypatch.setenv("OPENAI_TTS_BASE_URL", "https://audio.example.com/v1")
    monkeypatch.setenv("OPENAI_TTS_MODEL", DEFAULT_OPENAI_TTS_MODEL)
    monkeypatch.setattr("src.audio.tts.requests.post", fake_post)

    processor = TTSProcessor(voice="alloy")
    output_path = tmp_path / "line.mp3"

    generated_path, duration_ms, request_id = processor.synthesize(
        "你好，世界",
        str(output_path),
        voice="nova",
        speech_rate=1.25,
        pitch_rate=0.9,
        volume=40,
    )

    assert generated_path == str(output_path)
    assert duration_ms >= 0
    assert request_id == "req_openai_tts_123"
    assert output_path.read_bytes() == b"fake-mp3-bytes"
    assert captured["url"] == "https://audio.example.com/v1/audio/speech"
    assert captured["json"]["model"] == DEFAULT_OPENAI_TTS_MODEL
    assert captured["json"]["voice"] == "nova"
    assert captured["json"]["input"] == "你好，世界"
    assert captured["json"]["response_format"] == "mp3"
    assert captured["json"]["speed"] == 1.25
    assert captured["stream"] is True


def test_openai_tts_lists_openai_compatible_voices(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    processor = TTSProcessor(voice="alloy")

    voices = processor.list_voices()

    assert voices["alloy"]["model_id"] == "alloy"
    assert voices["alloy"]["model"] == DEFAULT_OPENAI_TTS_MODEL
    assert set(OPENAI_COMPATIBLE_VOICES).issubset(set(voices))
