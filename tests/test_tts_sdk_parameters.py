"""Use the installed SDK constructor/request builder; replace only synthesis I/O."""
import json
from pathlib import Path

import dashscope
import pytest
from dashscope.audio.tts_v2 import SpeechSynthesizer

from src.audio.tts import TTSProcessor


@pytest.mark.parametrize("voice,instructions,expected", [
    ("longanyang", "温柔地说", "温柔地说"),
    ("longanyang", None, None),
    ("longanyang", "", None),
    ("longxiaochun_v2", "温柔地说", None),
])
def test_cosyvoice_instruction_reaches_sdk_request(tmp_path, monkeypatch, voice, instructions, expected):
    monkeypatch.setattr(dashscope, "api_key", "synthetic-offline-key")
    requests = []

    def synthesize(synthesizer, text):
        requests.append((text, json.loads(synthesizer.request.get_start_request())["payload"]))
        return b"synthetic audio"

    monkeypatch.setattr(SpeechSynthesizer, "call", synthesize)
    tts = TTSProcessor.__new__(TTSProcessor)
    tts.model = "cosyvoice-v2"
    tts.voice = voice
    path = tmp_path / "audio.mp3"
    result = tts.synthesize("hello", str(path), voice=voice, instructions=instructions,
                            speech_rate=1.25, pitch_rate=0.8, volume=0)
    assert len(requests) == 1
    text, payload = requests[0]
    assert text == "hello"
    params = payload["parameters"]
    assert params.get("instruction") == expected
    assert "instructions" not in params
    assert (params["rate"], params["pitch"], params["volume"]) == (1.25, 0.8, 0)
    assert payload["model"] == ("cosyvoice-v3-flash" if voice == "longanyang" else "cosyvoice-v2")
    assert result[0] == str(path) and Path(result[0]).read_bytes() == b"synthetic audio"
