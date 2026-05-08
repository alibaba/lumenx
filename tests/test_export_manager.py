from __future__ import annotations

import time
from pathlib import Path

from src.apps.comic_gen.export import ExportManager
from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask


def _make_script() -> Script:
    now = time.time()
    return Script(
        id="project-1",
        title="Export Test",
        original_text="",
        characters=[],
        scenes=[],
        props=[],
        frames=[
            StoryboardFrame(id="frame-1", scene_id="scene-1", dialogue="第一句台词", selected_video_id="video-1"),
            StoryboardFrame(id="frame-2", scene_id="scene-1", dialogue="第二句台词", selected_video_id="video-2"),
        ],
        video_tasks=[
            VideoTask(id="video-1", project_id="project-1", image_url="cover.png", prompt="clip 1", duration=4),
            VideoTask(id="video-2", project_id="project-1", image_url="cover.png", prompt="clip 2", duration=6),
        ],
        created_at=now,
        updated_at=now,
    )


class RecordingExportManager(ExportManager):
    def __init__(self, config=None):
        super().__init__(config)
        self.ffmpeg_calls = []

    def _run_ffmpeg(self, args, *, cwd=None, timeout=600):
        self.ffmpeg_calls.append(list(args))
        output_path = (self.project_root / Path(args[-1])).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake transcoded video")

    def _build_audio_mix(self, script, input_video, work_dir):
        return None


def test_build_srt_uses_frame_durations(tmp_path):
    manager = ExportManager({"output_dir": str(tmp_path / "export")})
    script = _make_script()
    subtitle_path = Path(tmp_path / "export" / "project-1" / "sample.srt")

    manager._build_srt(script, subtitle_path)

    content = subtitle_path.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:04,000" in content
    assert "00:00:04,000 --> 00:00:10,000" in content
    assert "第一句台词" in content
    assert "第二句台词" in content


def test_smoke_render_project_transcodes_video_and_returns_subtitle(tmp_path):
    manager = RecordingExportManager({"output_dir": "export"})
    manager.project_root = tmp_path
    manager.output_dir = manager._resolve_workspace_path("export")
    manager.output_dir.mkdir(parents=True, exist_ok=True)
    input_video = tmp_path / "merged" / "input.mp4"
    input_video.parent.mkdir(parents=True, exist_ok=True)
    input_video.write_bytes(b"fake merged video")

    script = _make_script()
    script.merged_video_url = str(input_video)

    result = manager.render_project(
        script,
        {"resolution": "720p", "format": "mp4", "subtitles": "srt"},
    )

    assert manager.ffmpeg_calls
    render_args = manager.ffmpeg_calls[-1]
    assert "-vf" in render_args
    video_filter = render_args[render_args.index("-vf") + 1]
    assert "scale=1280:720" in video_filter
    assert "pad=1280:720" in video_filter
    assert result["format"] == "mp4"
    assert result["resolution"] == "720p"
    assert result["url"].endswith(".mp4")
    assert result["subtitle_url"].endswith(".srt")
    assert list(manager.output_dir.glob("project-1_*.mp4"))
    assert list((manager.output_dir / script.id).glob("*/project-1_*.srt"))
