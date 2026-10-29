"""Real silent-video inputs must not be mistaken for failed audio processing."""

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from src.apps.comic_gen.models import StoryboardFrame, VideoTask
from src.apps.comic_gen import pipeline as pipeline_module
from tests.test_merge_media_integration import (
    _decode_pcm, _goertzel_power, _install_script, _make_tone, _probe_json, _run,
    ffmpeg_tools, real_pipeline,
)

# Reuse the isolated real-media fixtures without changing their ownership.
__all__ = ["ffmpeg_tools", "real_pipeline"]


@pytest.mark.parametrize("with_bgm", [False, True])
def test_silent_video_can_apply_supported_mix(real_pipeline, ffmpeg_tools, tmp_path, with_bgm):
    ffmpeg, ffprobe = ffmpeg_tools
    item = real_pipeline
    clip = item.data_dir / "video/silent.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=25",
        "-an", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(clip),
    ])
    if with_bgm:
        _make_tone(ffmpeg, item.data_dir / "audio/bgm.wav", freq=1000)
    _install_script(
        item,
        [StoryboardFrame(id="f1", scene_id="", selected_video_id="A")],
        [VideoTask(id="A", project_id="p", frame_id="f1", image_url="", prompt="", status="completed", video_url="video/silent.mp4")],
        bgm_url="audio/bgm.wav" if with_bgm else None,
        mix_settings={"dialogue": 50, "bgm": 100, "sfx": 60},
    )
    merged = item.merge_videos("p")
    output = Path(item._output_ref_path(merged.merged_video_url))
    probe = _probe_json(ffprobe, output)
    assert .9 <= float(probe["format"]["duration"]) <= 1.2
    assert "video" in {stream["codec_type"] for stream in probe["streams"]}
    if with_bgm:
        assert _goertzel_power(_decode_pcm(ffmpeg, output, tmp_path), 44100, 1000) > 1


@pytest.mark.parametrize("stderr", [b"Invalid data found when processing input", b"Permission denied"])
def test_audio_probe_does_not_treat_other_errors_as_silence(real_pipeline, monkeypatch, stderr):
    monkeypatch.setattr(pipeline_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=stderr))
    with pytest.raises(RuntimeError, match="audio stream check failed"):
        real_pipeline._merge_video_has_audio("unused.mp4", "unused-ffmpeg")


def test_audio_probe_timeout_does_not_fall_back_to_silence(real_pipeline, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 30)
    monkeypatch.setattr(pipeline_module.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="audio stream check timed out"):
        real_pipeline._merge_video_has_audio("unused.mp4", "unused-ffmpeg")
