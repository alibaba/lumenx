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
