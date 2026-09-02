"""Regression tests for dialogue TTS generation and batch accounting."""

import json
import os
import time

import pytest

from src.audio.tts import TTSProcessor
from src.apps.comic_gen.models import (
    Character,
    GenerationStatus,
    Script,
    StoryboardFrame,
)


@pytest.fixture
def comic_api():
    """Import the API without letting its .env bootstrap leak across tests."""
    environment = os.environ.copy()
    try:
        from src.apps.comic_gen import api
        yield api
    finally:
        os.environ.clear()
        os.environ.update(environment)


def test_cosyvoice_normalizes_and_uses_singular_instruction(monkeypatch, tmp_path):
    """Strict v3 voices need canonical emotion text in singular ``instruction``."""
    import dashscope.audio.tts_v2 as tts_v2

    captured = {}

    class FakeSpeechSynthesizer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def call(self, text):
            captured["text"] = text
            return b"fake-mp3"

        def get_last_request_id(self):
            return "request-1"

        def get_first_package_delay(self):
            return 12

    monkeypatch.setattr(tts_v2, "SpeechSynthesizer", FakeSpeechSynthesizer)

    processor = object.__new__(TTSProcessor)
    processor.model = "cosyvoice-v3-flash"
    processor.voice = "longanyang"
    output_path = tmp_path / "dialogue.mp3"

    processor._synthesize_cosyvoice(
        "师父，盒子里的经书不见了！",
        str(output_path),
        voice="longanyang",
        instructions="情绪：焦急；演绎：急促、音量偏高、语气紧张",
    )

    assert captured["instruction"] == "你说话的情感是fearful。"
    assert "instructions" not in captured
    assert output_path.read_bytes() == b"fake-mp3"


def test_cosyvoice_omits_unsupported_free_form_for_strict_voice(monkeypatch, tmp_path):
    """An unknown free-form direction must not turn into DashScope error 428."""
    import dashscope.audio.tts_v2 as tts_v2

    captured = {}

    class FakeSpeechSynthesizer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def call(self, text):
            return b"fake-mp3"

        def get_last_request_id(self):
            return "request-2"

        def get_first_package_delay(self):
            return 10

    monkeypatch.setattr(tts_v2, "SpeechSynthesizer", FakeSpeechSynthesizer)

    processor = object.__new__(TTSProcessor)
    processor.model = "cosyvoice-v3-flash"
    processor.voice = "longanyang"

    processor._synthesize_cosyvoice(
        "测试",
        str(tmp_path / "dialogue.mp3"),
        voice="longanyang",
        instructions="像电影预告片一样演绎",
    )

    assert "instruction" not in captured
    assert "instructions" not in captured


def test_strict_voice_prefers_explicit_emotion_over_delivery_keywords():
    processor = object.__new__(TTSProcessor)
    processor.model = "cosyvoice-v3-flash"
    processor.voice = "longanyang"

    result = processor._normalize_cosyvoice_instruction(
        "longanyang",
        "情绪：平静；演绎：兴奋地加快语速",
    )

    assert result == "你说话的情感是neutral。"


def test_strict_voice_preserves_canonical_instruction():
    processor = object.__new__(TTSProcessor)
    processor.model = "cosyvoice-v3-flash"
    processor.voice = "longanyang"

    result = processor._normalize_cosyvoice_instruction(
        "longanyang",
        "你说话的情感是HAPPY。",
    )

    assert result == "你说话的情感是happy。"


def test_free_form_cosyvoice_instruction_passes_through():
    processor = object.__new__(TTSProcessor)
    processor.model = "cosyvoice-v3.5-plus"
    processor.voice = "custom-voice"

    result = processor._normalize_cosyvoice_instruction(
        "custom-voice",
        "像电影预告片一样，低沉而有力量地说。",
    )

    assert result == "像电影预告片一样，低沉而有力量地说。"


def test_batch_counts_internal_tts_failure_as_failed(monkeypatch, comic_api):
    """A swallowed TTS exception must not be reported to the UI as generated."""
    character = Character(
        id="character-1",
        name="阿石",
        description="少年行脚僧",
        voice_id="longanyang",
    )
    frame = StoryboardFrame(
        id="frame-1",
        scene_id="scene-1",
        character_ids=[character.id],
        dialogue="师父，盒子里的经书不见了！",
        speaker=character.name,
    )
    now = time.time()
    script = Script(
        id="script-1",
        title="测试",
        original_text="测试",
        characters=[character],
        frames=[frame],
        created_at=now,
        updated_at=now,
    )

    class FakePipeline:
        def get_script(self, script_id):
            assert script_id == script.id
            return script

        def generate_dialogue_line(self, script_id, frame_id):
            assert script_id == script.id
            assert frame_id == frame.id
            frame.status = GenerationStatus.FAILED
            frame.audio_url = None
            frame.audio_error = "simulated TTS failure"
            return script

    class LocalOnlyUploader:
        is_configured = False

    monkeypatch.setattr(comic_api, "pipeline", FakePipeline())
    monkeypatch.setattr(comic_api, "OSSImageUploader", LocalOnlyUploader)

    response = comic_api.generate_dialogue_audio_batch(script.id)
    payload = json.loads(response.body)

    assert payload["_batch_stats"] == {
        "generated": 0,
        "skipped": 0,
        "failed": 1,
        "no_voice": 0,
    }
