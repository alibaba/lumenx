"""Decode real Studio dub previews; synthetic media never calls a paid provider."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask
from src.apps.comic_gen import pipeline as pipeline_module
from tests import test_merge_media_integration as media_tests
from tests.test_merge_media_integration import _make_clip, _make_tone, _probe_json, _sample_rgb

ffmpeg_tools = media_tests.ffmpeg_tools
real_pipeline = media_tests.real_pipeline


@pytest.mark.parametrize("dialogue_duration", [0.2, 1.2], ids=["short-dialogue", "long-dialogue"])
@pytest.mark.parametrize("offset_ms", [0, 200], ids=["no-offset", "delayed"])
def test_real_no_background_dub_ends_with_selected_video(
    real_pipeline, ffmpeg_tools, tmp_path, monkeypatch, dialogue_duration, offset_ms,
):
    item = real_pipeline
    ffmpeg, ffprobe = ffmpeg_tools
    video, audio = item.data_dir / "video/source.mp4", item.data_dir / "audio/dialogue.wav"
    _make_clip(ffmpeg, video, color="blue", freq=330, duration=0.6)
    _make_tone(ffmpeg, audio, freq=880, duration=dialogue_duration)
    item.scripts["p"] = Script.model_validate({
        "id": "p", "title": "Finite dub preview", "original_text": "Synthetic tone",
        "created_at": 1, "updated_at": 1,
        "frames": [StoryboardFrame.model_validate({
            "id": "f", "scene_id": "", "dialogue": "Synthetic tone",
            "audio_url": "audio/dialogue.wav", "selected_video_id": "A",
            "video_url": "video/source.mp4",
        })],
        "video_tasks": [VideoTask.model_validate({
            "id": "A", "project_id": "p", "frame_id": "f", "image_url": "",
            "prompt": "Synthetic blue clip", "status": "completed", "video_url": "video/source.mp4",
        })],
    })
    item._save_data()
    monkeypatch.setattr(item, "_separate_background_audio", lambda *_args: None)
    # Bound only the observation deadline; execute the unchanged real FFmpeg command.
    real_run = subprocess.run

    def bounded_run(command, **kwargs):
        return real_run(command, **{**kwargs, "timeout": min(kwargs.get("timeout", 5), 5)})

    monkeypatch.setattr(pipeline_module, "subprocess", SimpleNamespace(
        run=bounded_run, CalledProcessError=subprocess.CalledProcessError,
        TimeoutExpired=subprocess.TimeoutExpired,
    ))

    result = item.preview_dub("p", "f", "A", offset_ms=offset_ms)

    frame = result.frames[0]
    assert frame.preview_video_url is not None
    output = item.data_dir / frame.preview_video_url
    metadata = _probe_json(ffprobe, output)
    assert {stream["codec_type"] for stream in metadata["streams"]} == {"audio", "video"}
    duration = float(metadata["format"]["duration"])
    expected = float(_probe_json(ffprobe, video)["format"]["duration"])
    (tmp_path / "measurement.json").write_text(json.dumps({"expected": expected, "actual": duration}))
    assert duration == pytest.approx(expected, abs=0.1), metadata
    red, green, blue = _sample_rgb(ffmpeg, output, 0.25, tmp_path)
    assert blue > 180 and red < 40 and green < 40
    assert frame.dub_preview_source is not None
    assert frame.dub_preview_source.task_id == "A"
    assert frame.dub_preview_source.offset_ms == offset_ms
    assert frame.dubbed_video_url is None
    stored = item.read_committed_script("p")
    assert stored.frames[0].preview_video_url == frame.preview_video_url
    assert Path(output).is_file()
