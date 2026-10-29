import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import DubSource, GenerationStatus, Script, StoryboardFrame, UnknownDubSource, VideoTask
from src.utils.json_storage import StorageError


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    frame = StoryboardFrame(
        id="f1",
        scene_id="s1",
        action_description="original",
        audio_url="audio/f1.mp3",
        selected_video_id="task-complete",
        final_take_id="task-complete",
        dubbed_video_task_id="task-complete",
        is_video_pinned=True,
        dub_preview_source=DubSource(
            schema_version=1,
            task_id="task-complete",
            video_url="video/f1.mp4",
            audio_url="audio/f1.mp3",
            offset_ms=25,
        ),
        dub_applied_source=DubSource(
            schema_version=1,
            task_id="task-complete",
            video_url="video/f1.mp4",
            audio_url="audio/f1.mp3",
            offset_ms=25,
        ),
        preview_video_url="video/preview_f1.mp4",
        dubbed_video_url="video/dubbed_f1.mp4",
        status=GenerationStatus.COMPLETED,
    )
    script = Script(id="p", title="Project", original_text="text", frames=[frame], created_at=1, updated_at=1)
    script.video_tasks = [
        VideoTask(
            id="task-complete",
            project_id="p",
            frame_id="f1",
            image_url="storyboard/f1.png",
            prompt="prompt",
            status=GenerationStatus.COMPLETED,
            video_url="video/f1.mp4",
            provider_name="dashscope",
            provider_task_id="provider-task",
            provider_request_id="provider-request",
            created_at=1,
        ),
        VideoTask(
            id="task-second",
            project_id="p",
            frame_id="f1",
            image_url="storyboard/f1b.png",
            prompt="prompt b",
            status=GenerationStatus.COMPLETED,
            video_url="video/f1b.mp4",
            provider_name="dashscope",
            provider_task_id="provider-task-b",
            provider_request_id="provider-request-b",
            created_at=2,
        ),
        VideoTask(
            id="task-processing",
            project_id="p",
            frame_id="f1",
            image_url="storyboard/f1.png",
            prompt="prompt",
            status="processing",
            video_url="video/pending.mp4",
            provider_task_id="live-provider-task",
        ),
        VideoTask(
            id="task-other-frame",
            project_id="p",
            frame_id="other",
            image_url="storyboard/other.png",
            prompt="prompt",
            status=GenerationStatus.COMPLETED,
            video_url="video/other.mp4",
        ),
    ]
    item.scripts["p"] = script
    item._commit_storage("projects.json")
    try:
        yield item
    finally:
        item.close()


def copied_frame(result):
    return next(frame for frame in result.frames if frame.id != "f1")


def cloned_tasks_for(result, frame_id):
    return [task for task in result.video_tasks if task.frame_id == frame_id]


def test_copy_frame_clones_completed_media_tasks_and_rewrites_frame_owned_ids(pipeline):
    original_dump = deepcopy(pipeline.scripts["p"].model_dump())

    result = pipeline.copy_frame("p", "f1")

    clone = copied_frame(result)
    assert clone.id != "f1"
    assert clone.locked is False
    assert clone.is_video_pinned is True
    assert clone.selected_video_id and clone.selected_video_id not in {"task-complete", "task-second"}
    assert clone.final_take_id == clone.selected_video_id
    assert clone.dubbed_video_task_id == clone.selected_video_id
    assert clone.dub_preview_source.task_id == clone.selected_video_id
    assert clone.dub_applied_source.task_id == clone.selected_video_id
    assert clone.dub_preview_source.video_url == "video/f1.mp4"
    assert clone.dub_preview_source.audio_url == "audio/f1.mp3"

    cloned_tasks = cloned_tasks_for(result, clone.id)
    assert [task.video_url for task in cloned_tasks] == ["video/f1.mp4", "video/f1b.mp4"]
    selected_clone = next(task for task in cloned_tasks if task.id == clone.selected_video_id)
    assert selected_clone.video_url == "video/f1.mp4"
    assert selected_clone.status == GenerationStatus.COMPLETED
    assert selected_clone.provider_task_id is None
    assert selected_clone.provider_request_id is None
    assert selected_clone.provider_name is None
    assert all(task.id not in {"task-complete", "task-second", "task-processing"} for task in cloned_tasks)

    original = next(frame for frame in result.frames if frame.id == "f1")
    assert original.selected_video_id == original_dump["frames"][0]["selected_video_id"]
    assert next(task for task in result.video_tasks if task.id == "task-complete").frame_id == "f1"
    assert not any(task.id == "task-processing" and task.frame_id == clone.id for task in result.video_tasks)


@pytest.mark.parametrize("field", ["dub_preview_source", "dub_applied_source"])
def test_copy_frame_preserves_mismatched_dub_source_without_mapping_to_clone_task(pipeline, field):
    frame = pipeline.scripts["p"].frames[0]
    setattr(
        frame,
        field,
        DubSource(
            schema_version=1,
            task_id="task-complete",
            video_url="video/not-the-task.mp4",
            audio_url="audio/f1.mp3",
            offset_ms=25,
        ),
    )
    pipeline._commit_storage("projects.json")

    result = pipeline.copy_frame("p", "f1")

    clone = copied_frame(result)
    source = getattr(clone, field)
    assert clone.selected_video_id != "task-complete"
    assert source.task_id == "task-complete"
    assert source.video_url == "video/not-the-task.mp4"
    other_source = clone.dub_applied_source if field == "dub_preview_source" else clone.dub_preview_source
    assert other_source.task_id == clone.selected_video_id


def test_copy_frame_keeps_unmapped_dub_sources_detached_from_original(pipeline):
    frame = pipeline.scripts["p"].frames[0]
    frame.dub_preview_source = DubSource(
        schema_version=1,
        task_id="task-complete",
        video_url="video/not-the-task.mp4",
        audio_url="audio/f1.mp3",
        offset_ms=25,
    )
    frame.dub_applied_source = UnknownDubSource(schema_version=2, raw={"nested": ["original"]})
    pipeline._commit_storage("projects.json")

    pipeline.copy_frame("p", "f1")

    live = pipeline.scripts["p"]
    original = next(candidate for candidate in live.frames if candidate.id == "f1")
    clone = next(candidate for candidate in live.frames if candidate.id != "f1")
    assert clone.dub_preview_source is not original.dub_preview_source
    assert clone.dub_applied_source is not original.dub_applied_source

    clone.dub_preview_source.video_url = "video/copy-only.mp4"
    clone.dub_applied_source.model_extra["raw"]["nested"].append("copy-only")

    assert original.dub_preview_source.video_url == "video/not-the-task.mp4"
    assert original.dub_applied_source.model_extra["raw"] == {"nested": ["original"]}


def test_copy_frame_keeps_unknown_selected_ids_from_defaulting_to_other_cloned_tasks(pipeline):
    frame = pipeline.scripts["p"].frames[0]
    frame.selected_video_id = "deleted-task"
    frame.final_take_id = "deleted-task"
    frame.dubbed_video_task_id = "deleted-task"
    frame.dub_preview_source = DubSource(
        schema_version=1,
        task_id="deleted-task",
        video_url="video/deleted.mp4",
        audio_url="audio/f1.mp3",
        offset_ms=0,
    )
    pipeline._commit_storage("projects.json")

    result = pipeline.copy_frame("p", "f1")

    clone = copied_frame(result)
    assert clone.selected_video_id == "deleted-task"
    assert clone.final_take_id == "deleted-task"
    assert clone.dubbed_video_task_id == "deleted-task"
    assert clone.dub_preview_source.task_id == "deleted-task"
    assert [task.video_url for task in cloned_tasks_for(result, clone.id)] == ["video/f1.mp4", "video/f1b.mp4"]
    assert pipeline.resolve_frame_video_for_render(clone, result.video_tasks) is None


def test_copy_frame_without_explicit_selection_keeps_default_candidate_behavior(pipeline):
    frame = pipeline.scripts["p"].frames[0]
    frame.selected_video_id = None
    frame.final_take_id = None
    frame.dubbed_video_task_id = None
    frame.is_video_pinned = False
    frame.dub_preview_source = None
    frame.dub_applied_source = None
    pipeline._commit_storage("projects.json")

    result = pipeline.copy_frame("p", "f1")

    clone = copied_frame(result)
    assert clone.selected_video_id is None
    assert clone.final_take_id is None
    assert clone.dubbed_video_task_id is None
    assert clone.is_video_pinned is False
    assert [task.video_url for task in cloned_tasks_for(result, clone.id)] == ["video/f1.mp4", "video/f1b.mp4"]
    assert pipeline.resolve_frame_video_for_render(clone, result.video_tasks) == "video/f1.mp4"


def test_copy_frame_commit_failure_restores_committed_state(pipeline, monkeypatch):
    before = pipeline.scripts["p"].model_dump()
    disk = (pipeline.data_dir / "projects.json").read_bytes()
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))

    with pytest.raises(StorageError, match="disk unavailable"):
        pipeline.copy_frame("p", "f1")

    assert pipeline.scripts["p"].model_dump() == before
    assert (pipeline.data_dir / "projects.json").read_bytes() == disk
    with pytest.raises(StorageError):
        pipeline.assert_storage_available()


def test_copy_frame_persists_cloned_tasks_on_readback(pipeline, monkeypatch):
    result = pipeline.copy_frame("p", "f1")
    clone = copied_frame(result)
    expected = json.loads((pipeline.data_dir / "projects.json").read_text())["p"]

    pipeline.close()
    restored = module.ComicGenPipeline(data_dir=pipeline.data_dir, script_processor=Mock(), warmup_demucs=False)
    try:
        saved = restored.get_script("p")
        assert saved.model_dump(mode="json") == expected
        saved_clone = next(frame for frame in saved.frames if frame.id == clone.id)
        saved_task = next(task for task in saved.video_tasks if task.id == saved_clone.selected_video_id)
        assert saved_task.frame_id == saved_clone.id
        assert saved_task.video_url == "video/f1.mp4"
        assert saved_task.provider_task_id is None
        assert saved_task.provider_request_id is None
    finally:
        restored.close()
