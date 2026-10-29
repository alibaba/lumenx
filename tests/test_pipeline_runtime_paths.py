import sys
import threading
import time
import types
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask
from src.apps.comic_gen.pipeline import ComicGenPipeline


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00"
    b"\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _script(script_id="script-1", frame_id="frame-1"):
    now = time.time()
    frame = StoryboardFrame(
        id=frame_id,
        scene_id="scene-1",
        action_description="A frame",
        rendered_image_url="storyboard/frame.png",
    )
    return Script(
        id=script_id,
        title="Runtime Paths",
        original_text="demo",
        frames=[frame],
        created_at=now,
        updated_at=now,
    )


def _pipeline(output_root: Path, script: Script):
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._closed = False
    pipeline._storage_failed = None
    pipeline._save_lock = threading.RLock()
    pipeline._video_task_runs = {}
    pipeline.data_dir = output_root.expanduser().resolve()
    pipeline.data_dir.mkdir(parents=True, exist_ok=True)
    pipeline.scripts = {script.id: script}
    pipeline.series_store = {}
    pipeline.library_store = SimpleNamespace(characters=[], scenes=[], props=[])
    pipeline._committed = {
        "projects.json": {script.id: script.model_dump(mode="json")},
        "series.json": {},
        "library_assets.json": {"characters": [], "scenes": [], "props": [], "revision": 0},
    }

    def commit(*names):
        for name in names:
            if name == "projects.json":
                pipeline._committed[name] = {
                    key: value.model_dump(mode="json") for key, value in pipeline.scripts.items()
                }
            elif name == "series.json":
                pipeline._committed[name] = {}
            elif name == "library_assets.json":
                pipeline._committed[name] = {"characters": [], "scenes": [], "props": [], "revision": 0}

    pipeline._commit_storage = commit
    pipeline._save_data = lambda: commit("projects.json")
    pipeline.assert_storage_available = lambda: None
    pipeline.read_committed_script = lambda script_id: Script.model_validate(
        deepcopy(pipeline._committed["projects.json"][script_id])
    )
    pipeline._kling_model = None
    pipeline._vidu_model = None
    pipeline._mulerouter_video_model = None
    pipeline.video_generator = SimpleNamespace(model=SimpleNamespace(generate=None))
    pipeline.get_script = lambda script_id: pipeline.scripts.get(script_id)
    return pipeline


def test_create_video_task_snapshots_from_data_dir_when_cwd_differs(tmp_path, monkeypatch):
    output_root = tmp_path / "owned-output"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    source = output_root / "storyboard" / "frame.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(PNG_BYTES)
    (cwd / "output" / "storyboard").mkdir(parents=True)
    (cwd / "output" / "storyboard" / "frame.png").write_bytes(b"wrong")

    script = _script()
    pipeline = _pipeline(output_root, script)

    _, task_id = pipeline.create_video_task(
        script.id,
        "storyboard/frame.png",
        "make it move",
    )
    task = next(t for t in script.video_tasks if t.id == task_id)

    assert task.image_url.startswith("video_inputs/")
    snapshot = output_root / task.image_url
    assert snapshot.exists()
    assert snapshot.read_bytes() == PNG_BYTES
    assert not (cwd / task.image_url).exists()


def test_create_video_task_rejects_snapshot_traversal(tmp_path):
    script = _script()
    pipeline = _pipeline(tmp_path / "owned-output", script)

    with pytest.raises(ValueError, match="escapes base directory"):
        pipeline.create_video_task(script.id, "../outside.png", "bad")

    assert script.video_tasks == []


def test_process_video_task_uses_data_dir_for_input_and_output(tmp_path, monkeypatch):
    output_root = tmp_path / "owned-output"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    image = output_root / "storyboard" / "frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_BYTES)

    script = _script()
    task = VideoTask(
        id="task-1",
        project_id=script.id,
        frame_id=script.frames[0].id,
        image_url="storyboard/frame.png",
        prompt="move",
        status="pending",
    )
    script.video_tasks = [task]
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(b"video")
        return kwargs["output_path"], 0.0

    pipeline = _pipeline(output_root, script)
    pipeline.video_generator = SimpleNamespace(model=SimpleNamespace(generate=fake_generate))

    pipeline.process_video_task(script.id, task.id)

    saved = pipeline.read_committed_script(script.id).video_tasks[0]
    assert saved.status == "completed"
    assert saved.video_url.startswith("video/video_task-1_")
    assert saved.video_url.endswith(".mp4")
    assert (output_root / saved.video_url).exists()
    assert Path(captured["img_path"]) == image.resolve()
    assert Path(captured["output_path"]) == output_root / saved.video_url
    assert not (cwd / "output" / saved.video_url).exists()


def test_process_video_task_marks_traversal_failed(tmp_path):
    script = _script()
    task = VideoTask(
        id="task-escape",
        project_id=script.id,
        frame_id=script.frames[0].id,
        image_url="../outside.png",
        prompt="move",
        status="pending",
    )
    script.video_tasks = [task]
    pipeline = _pipeline(tmp_path / "owned-output", script)

    pipeline.process_video_task(script.id, task.id)

    assert task.status == "failed"
    assert task.video_url is None


def test_extract_last_frame_reads_and_writes_data_dir(tmp_path, monkeypatch):
    output_root = tmp_path / "owned-output"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    video = output_root / "video" / "clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")

    script = _script()
    task = VideoTask(
        id="video-task-1",
        project_id=script.id,
        frame_id=script.frames[0].id,
        image_url="storyboard/frame.png",
        prompt="move",
        status="completed",
        video_url="video/clip.mp4",
    )
    script.video_tasks = [task]
    pipeline = _pipeline(output_root, script)

    oss_module = types.ModuleType("src.utils.oss_utils")

    class OSSImageUploader:
        def upload_image(self, _path):
            return None

    oss_module.OSSImageUploader = OSSImageUploader
    monkeypatch.setitem(sys.modules, "src.utils.oss_utils", oss_module)
    monkeypatch.setattr("src.apps.comic_gen.pipeline.get_ffmpeg_path", lambda: "/usr/bin/ffmpeg")

    def fake_run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(PNG_BYTES)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("src.apps.comic_gen.pipeline.subprocess.run", fake_run)

    result = pipeline.extract_last_frame(script.id, script.frames[0].id, task.id)

    frame = result.frames[0]
    assert frame.rendered_image_url.startswith("storyboard/")
    extracted = output_root / frame.rendered_image_url
    assert extracted.exists()
    assert extracted.read_bytes() == PNG_BYTES
    assert not (cwd / frame.rendered_image_url).exists()


def test_extract_last_frame_rejects_absolute_path_outside_data_dir(tmp_path):
    script = _script()
    task = VideoTask(
        id="video-task-escape",
        project_id=script.id,
        frame_id=script.frames[0].id,
        image_url="storyboard/frame.png",
        prompt="move",
        status="completed",
        video_url="/etc/passwd",
    )
    script.video_tasks = [task]
    pipeline = _pipeline(tmp_path / "owned-output", script)

    with pytest.raises(ValueError, match="outside managed output"):
        pipeline.extract_last_frame(script.id, script.frames[0].id, task.id)
