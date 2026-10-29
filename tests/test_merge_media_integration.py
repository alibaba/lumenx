"""Real FFmpeg merge smoke tests with synthetic media only."""

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as pipeline_module
from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask


def _tool(name: str) -> str:
    env_name = "LUMENX_TEST_FFMPEG" if name == "ffmpeg" else "LUMENX_TEST_FFPROBE"
    path = os.environ.get(env_name) or shutil.which(name)
    if not path:
        pytest.skip(f"{name} not available")
    return path


@pytest.fixture
def ffmpeg_tools():
    return _tool("ffmpeg"), _tool("ffprobe")


@pytest.fixture
def real_pipeline(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(pipeline_module, name, Mock())
    item = pipeline_module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    yield item
    item.close()


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        capture_output=capture,
        timeout=30,
    )


def _make_clip(ffmpeg: str, path: Path, *, color: str, freq: int, duration: float = 0.6) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lavfi = (
        f"color=c={color}:s=64x64:d={duration}:r=25[out0];"
        f"sine=frequency={freq}:duration={duration}:sample_rate=44100[out1]"
    )
    _run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", lavfi,
        "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac",
        str(path),
    ])


def _make_tone(ffmpeg: str, path: Path, *, freq: int, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}:sample_rate=44100",
        "-c:a", "pcm_s16le", str(path),
    ])


def _probe_json(ffprobe: str, path: Path) -> dict:
    result = _run([
        ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(path),
    ], capture=True)
    return json.loads(result.stdout.decode("utf-8"))


def _sample_rgb(ffmpeg: str, path: Path, seconds: float, tmp_path: Path) -> tuple[int, int, int]:
    sample_path = tmp_path / f"sample-{path.stem}-{int(seconds * 1000)}.rgb"
    _run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(path),
        "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(sample_path),
    ])
    data = sample_path.read_bytes()
    assert len(data) == 3
    return tuple(data)


def _decode_pcm(ffmpeg: str, path: Path, tmp_path: Path) -> list[float]:
    sample_path = tmp_path / f"audio-{path.stem}.s16le"
    _run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", "44100", "-f", "s16le", str(sample_path),
    ])
    data = sample_path.read_bytes()
    return [int.from_bytes(data[i:i + 2], "little", signed=True) / 32768.0 for i in range(0, len(data), 2)]


def _goertzel_power(samples: list[float], sample_rate: int, freq: float) -> float:
    # Use the middle window to avoid encoder/concat boundary transients.
    window = samples[sample_rate // 5: sample_rate]
    k = round(len(window) * freq / sample_rate)
    omega = 2.0 * math.pi * k / len(window)
    coeff = 2.0 * math.cos(omega)
    q0 = q1 = q2 = 0.0
    for sample in window:
        q0 = coeff * q1 - q2 + sample
        q2 = q1
        q1 = q0
    return q1 * q1 + q2 * q2 - coeff * q1 * q2


def _install_script(item: pipeline_module.ComicGenPipeline, frames: list[StoryboardFrame], tasks: list[VideoTask], *, bgm_url=None, mix_settings=None) -> None:
    item.scripts["p"] = Script(
        id="p",
        title="real media merge",
        original_text="source",
        created_at=1,
        updated_at=1,
        frames=frames,
        video_tasks=tasks,
        bgm_url=bgm_url,
        mix_settings=mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60},
    )
    item._save_data()


def test_real_merge_preserves_story_order_duration_and_streams(real_pipeline, ffmpeg_tools, tmp_path):
    ffmpeg, ffprobe = ffmpeg_tools
    item = real_pipeline
    _make_clip(ffmpeg, item.data_dir / "video/red.mp4", color="red", freq=440)
    _make_clip(ffmpeg, item.data_dir / "video/blue.mp4", color="blue", freq=880)
    _install_script(
        item,
        frames=[
            StoryboardFrame(id="f1", scene_id="", selected_video_id="A"),
            StoryboardFrame(id="f2", scene_id="", selected_video_id="B"),
        ],
        tasks=[
            VideoTask(id="A", project_id="p", frame_id="f1", image_url="", prompt="", status="completed", video_url="video/red.mp4"),
            VideoTask(id="B", project_id="p", frame_id="f2", image_url="", prompt="", status="completed", video_url="video/blue.mp4"),
        ],
    )

    merged = item.merge_videos("p")
    output = Path(item._output_ref_path(merged.merged_video_url))
    probe = _probe_json(ffprobe, output)
    stream_types = {stream["codec_type"] for stream in probe["streams"]}

    assert {"video", "audio"} <= stream_types
    assert 1.05 <= float(probe["format"]["duration"]) <= 1.45
    first = _sample_rgb(ffmpeg, output, 0.2, tmp_path)
    second = _sample_rgb(ffmpeg, output, 0.85, tmp_path)
    assert first[0] > first[2] + 40, first
    assert second[2] > second[0] + 20, second


def test_real_no_bgm_dialogue_zero_mutes_output_tone(real_pipeline, ffmpeg_tools, tmp_path):
    ffmpeg, _ = ffmpeg_tools
    item = real_pipeline
    _make_clip(ffmpeg, item.data_dir / "video/dialogue-zero.mp4", color="yellow", freq=440, duration=1.0)
    _install_script(
        item,
        frames=[StoryboardFrame(id="f1", scene_id="", selected_video_id="A")],
        tasks=[VideoTask(id="A", project_id="p", frame_id="f1", image_url="", prompt="", status="completed", video_url="video/dialogue-zero.mp4")],
        mix_settings={"dialogue": 100, "bgm": 35, "sfx": 60},
    )

    full = item.merge_videos("p")
    full_samples = _decode_pcm(ffmpeg, Path(item._output_ref_path(full.merged_video_url)), tmp_path)
    full_power = _goertzel_power(full_samples, 44100, 440)

    item.scripts["p"].mix_settings = {"dialogue": 0, "bgm": 35, "sfx": 60}
    item._save_data()
    muted = item.merge_videos("p")
    muted_samples = _decode_pcm(ffmpeg, Path(item._output_ref_path(muted.merged_video_url)), tmp_path)
    muted_power = _goertzel_power(muted_samples, 44100, 440)

    assert full_power > 1.0
    assert muted_power < full_power * 0.05, {"full": full_power, "muted": muted_power}


def test_real_no_bgm_dialogue_half_reduces_output_tone(real_pipeline, ffmpeg_tools, tmp_path):
    ffmpeg, _ = ffmpeg_tools
    item = real_pipeline
    _make_clip(ffmpeg, item.data_dir / "video/dialogue-half.mp4", color="purple", freq=660, duration=1.0)
    _install_script(
        item,
        frames=[StoryboardFrame(id="f1", scene_id="", selected_video_id="A")],
        tasks=[VideoTask(id="A", project_id="p", frame_id="f1", image_url="", prompt="", status="completed", video_url="video/dialogue-half.mp4")],
        mix_settings={"dialogue": 100, "bgm": 35, "sfx": 60},
    )

    full = item.merge_videos("p")
    full_samples = _decode_pcm(ffmpeg, Path(item._output_ref_path(full.merged_video_url)), tmp_path)
    full_power = _goertzel_power(full_samples, 44100, 660)

    item.scripts["p"].mix_settings = {"dialogue": 50, "bgm": 35, "sfx": 60}
    item._save_data()
    half = item.merge_videos("p")
    half_samples = _decode_pcm(ffmpeg, Path(item._output_ref_path(half.merged_video_url)), tmp_path)
    half_power = _goertzel_power(half_samples, 44100, 660)

    assert full_power > 1.0
    assert full_power * 0.08 < half_power < full_power * 0.6, {"full": full_power, "half": half_power}


def test_real_bgm_mix_mutes_dialogue_and_keeps_bgm_energy(real_pipeline, ffmpeg_tools, tmp_path):
    ffmpeg, _ = ffmpeg_tools
    item = real_pipeline
    _make_clip(ffmpeg, item.data_dir / "video/dialogue.mp4", color="green", freq=440, duration=1.0)
    _make_tone(ffmpeg, item.data_dir / "audio/bgm.wav", freq=1000, duration=1.0)
    _install_script(
        item,
        frames=[StoryboardFrame(id="f1", scene_id="", selected_video_id="A")],
        tasks=[VideoTask(id="A", project_id="p", frame_id="f1", image_url="", prompt="", status="completed", video_url="video/dialogue.mp4")],
        bgm_url="audio/bgm.wav",
        mix_settings={"dialogue": 0, "bgm": 100, "sfx": 60},
    )

    merged = item.merge_videos("p")
    output = Path(item._output_ref_path(merged.merged_video_url))
    samples = _decode_pcm(ffmpeg, output, tmp_path)
    dialogue_power = _goertzel_power(samples, 44100, 440)
    bgm_power = _goertzel_power(samples, 44100, 1000)

    assert bgm_power > 1.0
    assert bgm_power > dialogue_power * 8, {"bgm": bgm_power, "dialogue": dialogue_power}
