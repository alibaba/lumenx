from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import ImageAsset, ImageVariant, Script, StoryboardFrame, VideoTask
from src.utils.json_storage import StorageConflictError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    video = item.data_dir / "video" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    upload = item.data_dir / "uploads" / "manual.png"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"png")
    item.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        frames=[
            StoryboardFrame(
                id="frame",
                scene_id="scene",
                rendered_image_url="storyboard/old-a.png",
                image_url="storyboard/old-a.png",
                rendered_image_asset=ImageAsset(
                    selected_id="old-a",
                    variants=[
                        ImageVariant(id="old-a", url="storyboard/old-a.png", created_at=1),
                        ImageVariant(id="old-b", url="storyboard/old-b.png", created_at=2),
                    ],
                ),
            ),
            StoryboardFrame(id="other", scene_id="scene"),
        ],
        video_tasks=[
            VideoTask(
                id="task",
                project_id="p",
                frame_id="frame",
                image_url="storyboard/source.png",
                prompt="video",
                status="completed",
                video_url="video/clip.mp4",
            ),
            VideoTask(
                id="other-task",
                project_id="p",
                frame_id="other",
                image_url="storyboard/source.png",
                prompt="video",
                status="completed",
                video_url="video/clip.mp4",
            ),
        ],
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    state = SimpleNamespace(on_ffmpeg=None, on_upload=None, ffmpeg_calls=[], upload_calls=[])

    def fake_ffmpeg_path():
        return "/fake/ffmpeg"

    monkeypatch.setattr(module, "get_ffmpeg_path", fake_ffmpeg_path)

    def fake_run(cmd, **_kwargs):
        state.ffmpeg_calls.append(cmd)
        if state.on_ffmpeg:
            state.on_ffmpeg()
        out = cmd[-1]
        with open(out, "wb") as handle:
            handle.write(b"jpg")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    class FakeUploader:
        def upload_image(self, path):
            state.upload_calls.append(path)
            if state.on_upload:
                return state.on_upload(path)
            return None

    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", FakeUploader)
    try:
        yield item, state
    finally:
        item.close()


def frame(script, frame_id="frame"):
    return next(candidate for candidate in script.frames if candidate.id == frame_id)


def test_extract_last_frame_rejects_video_task_from_another_frame(runtime):
    item, state = runtime

    with pytest.raises(ValueError, match="does not belong to frame"):
        item.extract_last_frame("p", "frame", "other-task")

    assert state.ffmpeg_calls == []
    saved = item.read_committed_script("p")
    assert frame(saved).rendered_image_asset.selected_id == "old-a"
    assert [variant.id for variant in frame(saved).rendered_image_asset.variants] == ["old-a", "old-b"]


def test_extract_last_frame_rejects_late_video_url_change(runtime):
    item, state = runtime

    def change_video_url():
        item.scripts["p"].video_tasks[0].video_url = "video/replacement.mp4"
        item._commit_storage("projects.json")

    state.on_ffmpeg = change_video_url

    with pytest.raises(StorageConflictError):
        item.extract_last_frame("p", "frame", "task")

    saved = item.read_committed_script("p")
    assert saved.video_tasks[0].video_url == "video/replacement.mp4"
    assert [variant.id for variant in frame(saved).rendered_image_asset.variants] == ["old-a", "old-b"]


@pytest.mark.parametrize("command", ["extract", "upload"])
def test_frame_image_commands_reject_late_target_image_change(runtime, command):
    item, state = runtime

    def change_image_target(_path=None):
        current = item.scripts["p"].frames[0]
        current.rendered_image_asset.selected_id = "old-b"
        current.rendered_image_url = "storyboard/old-b.png"
        current.image_url = "storyboard/old-b.png"
        item._commit_storage("projects.json")
        return None

    state.on_upload = change_image_target
    if command == "extract":
        with pytest.raises(StorageConflictError):
            item.extract_last_frame("p", "frame", "task")
    else:
        with pytest.raises(StorageConflictError):
            item.upload_frame_image("p", "frame", "uploads/manual.png")

    saved = item.read_committed_script("p")
    target = frame(saved)
    assert target.rendered_image_asset.selected_id == "old-b"
    assert target.rendered_image_url == "storyboard/old-b.png"
    assert [variant.id for variant in target.rendered_image_asset.variants] == ["old-a", "old-b"]


@pytest.mark.parametrize("command", ["extract", "upload"])
def test_frame_image_commands_allow_unrelated_metadata_edit(runtime, command):
    item, state = runtime

    def edit_unrelated(_path=None):
        current = item.scripts["p"].frames[0]
        current.is_video_pinned = True
        current.rendered_image_asset.variants[1].is_favorited = True
        current.rendered_image_asset.variants[1].prompt_used = "favorite metadata edit"
        item._commit_storage("projects.json")
        return None

    if command == "extract":
        state.on_upload = edit_unrelated
        result = item.extract_last_frame("p", "frame", "task")
    else:
        state.on_upload = edit_unrelated
        result = item.upload_frame_image("p", "frame", "uploads/manual.png")

    target = frame(result)
    assert target.is_video_pinned is True
    old_b = next(variant for variant in target.rendered_image_asset.variants if variant.id == "old-b")
    assert old_b.is_favorited is True
    assert old_b.prompt_used == "favorite metadata edit"
    assert target.rendered_image_asset.selected_id not in {"old-a", "old-b"}
    assert len(target.rendered_image_asset.variants) == 3
    committed = item.read_committed_script("p")
    assert frame(committed).model_dump(mode="json") == target.model_dump(mode="json")


@pytest.mark.parametrize("image_path", ["uploads/missing.png", "uploads/empty.png"])
def test_upload_frame_image_rejects_missing_or_empty_file_before_oss(runtime, image_path):
    item, state = runtime
    if image_path.endswith("empty.png"):
        empty = item.data_dir / "uploads" / "empty.png"
        empty.write_bytes(b"")

    with pytest.raises(ValueError, match="not found or empty"):
        item.upload_frame_image("p", "frame", image_path)

    assert state.upload_calls == []
    saved = item.read_committed_script("p")
    assert [variant.id for variant in frame(saved).rendered_image_asset.variants] == ["old-a", "old-b"]


def test_upload_image_api_removes_just_created_file_when_frame_missing(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    runtime.scripts["p"] = Script(id="p", title="project", original_text="source", created_at=1, updated_at=1, frames=[])
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    try:
        app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime, warmup_demucs=False)
        with TestClient(app) as client:
            response = client.post(
                "/projects/p/frames/missing/upload_image",
                files={"file": ("manual.png", b"png", "image/png")},
            )
        assert response.status_code == 404, response.text
        assert list((runtime.data_dir / "uploads").glob("*.png")) == []
    finally:
        runtime.close()


def test_upload_image_api_preserves_committed_file_when_signing_fails(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    runtime.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        frames=[StoryboardFrame(id="frame", scene_id="scene")],
    )
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")

    def fail_signing(_payload):
        raise ValueError("synthetic signing failure")

    monkeypatch.setattr(api, "signed_response", fail_signing)
    try:
        app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime, warmup_demucs=False)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/projects/p/frames/frame/upload_image",
                files={"file": ("manual.png", b"png", "image/png")},
            )
            assert response.status_code == 500, response.text
            uploads = list((runtime.data_dir / "uploads").glob("*.png"))
            assert len(uploads) == 1
            assert uploads[0].read_bytes() == b"png"
            saved = runtime.read_committed_script("p")
            assert frame(saved).rendered_image_url == f"uploads/{uploads[0].name}"
    finally:
        runtime.close()


def test_upload_image_api_preserves_local_file_on_storage_conflict(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    runtime.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        frames=[StoryboardFrame(id="frame", scene_id="scene")],
    )
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    monkeypatch.setattr(
        runtime,
        "upload_frame_image",
        Mock(side_effect=StorageConflictError("frame image target changed")),
    )
    try:
        app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime, warmup_demucs=False)
        with TestClient(app) as client:
            response = client.post(
                "/projects/p/frames/frame/upload_image",
                files={"file": ("manual.png", b"png", "image/png")},
            )
        assert response.status_code == 409, response.text
        uploads = list((runtime.data_dir / "uploads").glob("*.png"))
        assert len(uploads) == 1
        assert uploads[0].read_bytes() == b"png"
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["extract", "upload"])
@pytest.mark.parametrize("target", ["frame", "project"])
def test_same_id_replacement_during_image_work_keeps_new_owner_clean(runtime, command, target):
    item, state = runtime
    def replace(_path=None):
        if target == "project":
            item.scripts["p"] = item.scripts["p"].model_copy(deep=True)
        else:
            item.scripts["p"].frames[0] = item.scripts["p"].frames[0].model_copy(deep=True)
        item.scripts["p"].title = "replacement owner"
        item._commit_storage("projects.json")
    state.on_upload = replace
    with pytest.raises(StorageConflictError):
        if command == "extract":
            item.extract_last_frame("p", "frame", "task")
        else:
            item.upload_frame_image("p", "frame", "uploads/manual.png")
    saved = item.read_committed_script("p")
    assert saved.title == "replacement owner"
    assert saved.frames[0].rendered_image_url == "storyboard/old-a.png"
    assert len(saved.frames[0].rendered_image_asset.variants) == 2


@pytest.mark.parametrize("command", ["extract", "upload"])
@pytest.mark.parametrize("failure", ["closed", "save"])
def test_unwritable_runtime_keeps_prior_images_and_private_media(runtime, monkeypatch, command, failure):
    from src.utils.json_storage import StorageError
    item, state = runtime
    before = item.read_committed_script("p").model_dump()
    def fail_commit(_payloads):
        raise StorageError("synthetic image commit failure")
    def interrupt(_path=None):
        if failure == "closed":
            item.close()
        else:
            monkeypatch.setattr(item._storage, "commit", fail_commit)
    state.on_upload = interrupt
    with pytest.raises(StorageError):
        if command == "extract":
            item.extract_last_frame("p", "frame", "task")
        else:
            item.upload_frame_image("p", "frame", "uploads/manual.png")
    assert item.scripts["p"].model_dump() == before
    if command == "extract":
        assert list((item.data_dir / "storyboard").glob("frame_frame_lastframe_*.jpg"))
    else:
        assert (item.data_dir / "uploads/manual.png").read_bytes() == b"png"
