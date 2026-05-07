from __future__ import annotations

import wave
from pathlib import Path

from src.apps.comic_gen.audio import AudioGenerator
from src.apps.comic_gen.models import StoryboardFrame


def _make_frame(**overrides):
    defaults = dict(id="frame-1", scene_id="scene-1", action_description="门被猛地推开，脚步急促冲入房间")
    defaults.update(overrides)
    return StoryboardFrame(**defaults)


def test_generate_sfx_creates_real_wav(tmp_path):
    generator = AudioGenerator({
        "project_root": str(tmp_path),
        "output_dir": str(tmp_path / "output" / "audio"),
    })
    frame = _make_frame()

    updated = generator.generate_sfx(frame, duration=1.2)

    output_path = Path(tmp_path / "output" / "audio" / "sfx" / "frame-1.wav")
    assert updated.sfx_url == "audio/sfx/frame-1.wav"
    assert output_path.exists()

    with wave.open(str(output_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() > 0


def test_generate_bgm_creates_expected_duration(tmp_path):
    generator = AudioGenerator({
        "project_root": str(tmp_path),
        "output_dir": str(tmp_path / "output" / "audio"),
    })
    frame = _make_frame(action_description="温暖的家庭清晨", dialogue="我们终于到家了")

    updated = generator.generate_bgm(frame, duration=2.0, context="温暖的家庭清晨")

    output_path = Path(tmp_path / "output" / "audio" / "bgm" / "frame-1.wav")
    assert updated.bgm_url == "audio/bgm/frame-1.wav"
    assert output_path.exists()

    with wave.open(str(output_path), "rb") as wav_file:
        duration = wav_file.getnframes() / wav_file.getframerate()
        assert 1.8 <= duration <= 2.2
