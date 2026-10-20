"""Regression tests for path safety around the ffmpeg pipeline.

Added with the CodeQL security fixes (py/path-injection and
py/command-line-injection in pipeline.py): every path that reaches an
ffmpeg subprocess call must pass through _safe_resolve_path or an
equivalent output/-containment guard. These tests pin both directions:
legitimate (multi-level) paths keep working, traversal attempts are
rejected before any subprocess runs.
"""

import os
import shutil
import subprocess
import time
import uuid

import pytest
from unittest.mock import patch

from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask
from src.apps.comic_gen.pipeline import ComicGenPipeline, _safe_resolve_path
from src.utils.system_check import get_ffmpeg_path


# ---------------------------------------------------------------------------
# _safe_resolve_path — the shared guard for all ffmpeg path arguments
# ---------------------------------------------------------------------------

class TestSafeResolvePath:
    def test_allows_simple_filename(self, tmp_path):
        base = str(tmp_path)
        resolved = _safe_resolve_path(base, "video.mp4")
        assert resolved == os.path.join(os.path.realpath(base), "video.mp4")

    def test_allows_nested_multilevel_path(self, tmp_path):
        # Legit refs like assets/characters/xxx.png must NOT be rejected
        base = str(tmp_path)
        resolved = _safe_resolve_path(base, "assets/characters/char_001.png")
        assert resolved.startswith(os.path.realpath(base) + os.sep)
        assert resolved.endswith(os.path.join("assets", "characters", "char_001.png"))

    def test_rejects_parent_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_resolve_path(str(tmp_path), "../../etc/passwd")

    def test_rejects_absolute_path_outside_base(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_resolve_path(str(tmp_path), "/etc/passwd")

    def test_rejects_sneaky_traversal_through_valid_prefix(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_resolve_path(str(tmp_path), "video/../../outside.mp4")


# ---------------------------------------------------------------------------
# extract_last_frame — absolute-path branch containment + real ffmpeg run
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(tmp_path):
    """Pipeline with temp data files and mocked generators (no real IO)."""
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.library_data_file = str(tmp_path / "library_assets.json")
    p.scripts = {}
    p.series_store = {}
    return p


def _make_script_with_video_task(video_url: str) -> Script:
    now = time.time()
    frame = StoryboardFrame(
        id=f"frame{uuid.uuid4().hex[:8]}",
        scene_id=str(uuid.uuid4()),
        character_ids=[],
        action_description="test frame",
    )
    script_id = str(uuid.uuid4())
    task = VideoTask(
        id=str(uuid.uuid4()),
        project_id=script_id,
        frame_id=frame.id,
        image_url="storyboard/test.png",
        prompt="test",
        status="completed",
        video_url=video_url,
    )
    return Script(
        id=script_id,
        title="ffmpeg safety",
        original_text="test",
        characters=[],
        scenes=[],
        frames=[frame],
        video_tasks=[task],
        created_at=now,
        updated_at=now,
    )


class TestExtractLastFramePathContainment:
    def test_absolute_path_outside_output_is_rejected(self, pipeline, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        os.makedirs("output", exist_ok=True)
        script = _make_script_with_video_task("/etc/passwd")
        pipeline.scripts[script.id] = script
        task = script.video_tasks[0]

        with pytest.raises(ValueError, match="outside managed output"):
            pipeline.extract_last_frame(script.id, script.frames[0].id, task.id)

    def test_traversal_relative_path_is_rejected(self, pipeline, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        os.makedirs("output", exist_ok=True)
        script = _make_script_with_video_task("../../etc/passwd")
        pipeline.scripts[script.id] = script
        task = script.video_tasks[0]

        with pytest.raises(ValueError, match="escapes base directory"):
            pipeline.extract_last_frame(script.id, script.frames[0].id, task.id)

    @pytest.mark.skipif(
        get_ffmpeg_path() is None and shutil.which("ffmpeg") is None,
        reason="ffmpeg not available",
    )
    def test_legit_relative_video_extracts_frame(self, pipeline, monkeypatch, tmp_path):
        """Happy path: a managed output/video file passes the guard and the
        real ffmpeg invocation still works end-to-end after the fix."""
        ffmpeg = get_ffmpeg_path() or shutil.which("ffmpeg")
        monkeypatch.chdir(tmp_path)
        os.makedirs("output/video", exist_ok=True)
        os.makedirs("output/storyboard", exist_ok=True)

        video_file = os.path.join("output", "video", "tiny.mp4")
        gen = subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.5",
             "-pix_fmt", "yuv420p", video_file],
            capture_output=True, timeout=60,
        )
        assert gen.returncode == 0, gen.stderr.decode()[:400]

        script = _make_script_with_video_task("video/tiny.mp4")
        pipeline.scripts[script.id] = script
        task = script.video_tasks[0]

        result = pipeline.extract_last_frame(script.id, script.frames[0].id, task.id)

        frame = result.frames[0]
        assert frame.rendered_image_url, "extracted frame should be recorded"
        extracted = os.path.join("output", frame.rendered_image_url)
        assert os.path.exists(extracted)
        assert os.path.getsize(extracted) > 0
