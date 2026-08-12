from pathlib import Path

import pytest

from src.audio.minimax_tts import (
    MINIMAX_AUDIO_FORMATS,
    MINIMAX_TTS_ENDPOINTS,
    MINIMAX_TTS_MODELS,
    MiniMaxTTSClient,
)
from src.audio.tts import TTSProcessor


class _FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_minimax_catalog_and_regional_endpoints():
    assert MINIMAX_TTS_ENDPOINTS == {
        "global": "https://api.minimax.io/v1/t2a_v2",
        "cn": "https://api.minimaxi.com/v1/t2a_v2",
    }
    assert MINIMAX_TTS_MODELS == (
        "speech-2.8-hd",
        "speech-2.8-turbo",
        "speech-2.6-hd",
        "speech-2.6-turbo",
        "speech-02-hd",
        "speech-02-turbo",
        "speech-01-hd",
        "speech-01-turbo",
    )
    assert MINIMAX_AUDIO_FORMATS == ("mp3", "wav", "flac", "pcm")
    assert MiniMaxTTSClient(api_key="test", region="cn").endpoint == (
        "https://api.minimaxi.com/v1/t2a_v2"
    )


def test_minimax_synthesize_sends_supported_fields_and_decodes_hex(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json, timeout=timeout)
        return _FakeResponse(
            {
                "data": {"audio": b"audio".hex(), "status": 2},
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "trace_id": "trace-123",
            }
        )

    monkeypatch.setattr("src.audio.minimax_tts.requests.post", fake_post)
    output = tmp_path / "speech.flac"
    result = MiniMaxTTSClient(api_key="secret", timeout=15).synthesize(
        "Hello",
        str(output),
        model="speech-2.8-turbo",
        language_boost="English",
        output_format="hex",
        voice_setting={"voice_id": "voice-1", "speed": 1.1},
        pronunciation_dict={"tone": ["LumenX/Lumen X"]},
        audio_setting={"format": "flac", "sample_rate": 32000},
        voice_modify={"pitch": 1},
        subtitle_enable=True,
    )

    assert result == (str(output), 0.0, "trace-123")
    assert output.read_bytes() == b"audio"
    assert captured["url"] == MINIMAX_TTS_ENDPOINTS["global"]
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 15
    assert captured["body"] == {
        "model": "speech-2.8-turbo",
        "text": "Hello",
        "stream": False,
        "language_boost": "English",
        "output_format": "hex",
        "voice_setting": {"voice_id": "voice-1", "speed": 1.1},
        "pronunciation_dict": {"tone": ["LumenX/Lumen X"]},
        "audio_setting": {"format": "flac", "sample_rate": 32000},
        "voice_modify": {"pitch": 1},
        "subtitle_enable": True,
    }


def test_minimax_synthesize_downloads_url_response(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.audio.minimax_tts.requests.post",
        lambda *args, **kwargs: _FakeResponse(
            {
                "data": {"audio": "https://example.com/audio.wav", "status": 2},
                "base_resp": {"status_code": 0},
            }
        ),
    )
    monkeypatch.setattr(
        "src.audio.minimax_tts.requests.get",
        lambda *args, **kwargs: _FakeResponse(content=b"wave"),
    )

    output = tmp_path / "speech.wav"
    MiniMaxTTSClient(api_key="test").synthesize(
        "Hello",
        str(output),
        output_format="url",
        audio_setting={"format": "wav"},
    )
    assert output.read_bytes() == b"wave"


def test_minimax_api_error_does_not_write_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.audio.minimax_tts.requests.post",
        lambda *args, **kwargs: _FakeResponse(
            {
                "data": {"status": 2},
                "base_resp": {"status_code": 1004, "status_msg": "invalid request"},
            }
        ),
    )
    output = tmp_path / "speech.mp3"

    with pytest.raises(RuntimeError, match="invalid request"):
        MiniMaxTTSClient(api_key="test").synthesize("Hello", str(output))
    assert not output.exists()


def test_tts_processor_dispatches_minimax_family(monkeypatch, tmp_path):
    captured = {}

    class FakeMiniMaxClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def synthesize(self, text, output_path, **kwargs):
            captured.update(text=text, output_path=output_path, options=kwargs)
            Path(output_path).write_bytes(b"audio")
            return output_path, 0.0, "trace-456"

    monkeypatch.setattr("src.audio.minimax_tts.MiniMaxTTSClient", FakeMiniMaxClient)
    processor = TTSProcessor(
        minimax_api_key="test-key",
        minimax_region="cn",
    )
    output = tmp_path / "speech.wav"
    result = processor.synthesize(
        "Hello",
        str(output),
        voice="voice-1",
        speech_rate=1.25,
        volume=75,
        model_override="speech-2.8-hd",
        family_override="minimax",
    )

    assert result == (str(output), 0.0, "trace-456")
    assert captured["client"] == {"api_key": "test-key", "region": "cn"}
    assert captured["options"] == {
        "model": "speech-2.8-hd",
        "voice_setting": {"voice_id": "voice-1", "speed": 1.25, "vol": 1.5},
        "audio_setting": {"format": "wav"},
    }
