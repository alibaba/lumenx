"""Merge publication must preserve edits and only publish validated output."""
import subprocess
from pathlib import Path

import pytest

from src.apps.comic_gen import pipeline as pipeline_module
from src.apps.comic_gen.models import StoryboardFrame
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_dub_source_provenance import runtime as dub_runtime

runtime = dub_runtime


def test_merge_fails_before_ffmpeg_when_selected_task_is_missing(runtime):
    item, state = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR")
    script.merged_video_url = "video/prior.mp4"
    script.frames.append(StoryboardFrame(id="f2", scene_id="", selected_video_id="missing-task"))
    item._save_data()

    with pytest.raises(ValueError, match="missing-task"):
        item.merge_videos("p")

    assert item.read_committed_script("p").merged_video_url == "video/prior.mp4"
    assert state.calls == []


def test_merge_fails_before_ffmpeg_when_selected_video_file_is_missing(runtime):
    item, state = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR")
    script.merged_video_url = "video/prior.mp4"
    script.frames.append(StoryboardFrame(id="f2", scene_id="", selected_video_id="B"))
    task_b = next(task for task in script.video_tasks if task.id == "B")
    task_b.frame_id = "f2"
    (item.data_dir / "video/B.mp4").unlink()
    item._save_data()

    with pytest.raises(ValueError, match="f2.*file is missing"):
        item.merge_videos("p")

    assert item.read_committed_script("p").merged_video_url == "video/prior.mp4"
    assert state.calls == []


@pytest.mark.parametrize("task_mutation,match", [
    (lambda task: setattr(task, "status", "processing"), "incomplete"),
    (lambda task: setattr(task, "frame_id", "other-frame"), "no longer belongs"),
])
def test_merge_rejects_invalid_selected_task_before_ffmpeg(runtime, task_mutation, match):
    item, state = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR")
    script.merged_video_url = "video/prior.mp4"
    script.frames.append(StoryboardFrame(id="f2", scene_id="", selected_video_id="B"))
    task_b = next(task for task in script.video_tasks if task.id == "B")
    task_b.frame_id = "f2"
    task_mutation(task_b)
    item._save_data()

    with pytest.raises(ValueError, match=f"f2.*{match}"):
        item.merge_videos("p")

    assert item.read_committed_script("p").merged_video_url == "video/prior.mp4"
    assert state.calls == []


def test_merge_rejects_remote_selected_source_before_ffmpeg(runtime):
    item, state = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR")
    script.merged_video_url = "video/prior.mp4"
    script.frames.append(StoryboardFrame(id="f2", scene_id="", selected_video_id="B"))
    task_b = next(task for task in script.video_tasks if task.id == "B")
    task_b.frame_id = "f2"
    task_b.video_url = "https://media.invalid/B.mp4"
    item._save_data()

    with pytest.raises(ValueError, match="f2.*managed local file"):
        item.merge_videos("p")

    assert item.read_committed_script("p").merged_video_url == "video/prior.mp4"
    assert state.calls == []


def test_merge_success_uses_all_frames_in_story_order(runtime):
    item, state = runtime
    script = item.scripts["p"]
    script.frames.append(StoryboardFrame(id="f2", scene_id="", selected_video_id="B"))
    task_b = next(task for task in script.video_tasks if task.id == "B")
    task_b.frame_id = "f2"
    item._save_data()

    result = item.merge_videos("p")

    assert result.merged_video_url
    assert state.merge_sources == [str(item.data_dir / "video/A.mp4"), str(item.data_dir / "video/B.mp4")]


def test_merge_requires_every_frame_to_have_render_source(runtime):
    item, state = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR")
    script.merged_video_url = "video/prior.mp4"
    script.frames.append(StoryboardFrame(id="f2", scene_id=""))
    item._save_data()

    with pytest.raises(ValueError, match="f2"):
        item.merge_videos("p")

    assert item.read_committed_script("p").merged_video_url == "video/prior.mp4"
    assert state.calls == []


@pytest.mark.parametrize("change", ["selection", "frame", "project", "bgm", "mix", "result"] )
def test_render_dependency_change_cannot_publish_stale_merge(runtime, change):
    item, state = runtime

    def edit():
        script = item.scripts["p"]
        if change == "selection":
            item.select_video_for_frame("p", "f", "B")
        elif change == "frame":
            script.frames[0] = script.frames[0].model_copy(deep=True)
        elif change == "project":
            item.scripts["p"] = script.model_copy(deep=True)
        elif change == "bgm":
            script.bgm_url = "audio/new.mp3"
        elif change == "mix":
            script.mix_settings = {"dialogue": 33, "bgm": 20, "sfx": 10}
        else:
            script.merged_video_url = "video/newer.mp4"
        item._commit_storage("projects.json")

    state.hook = edit
    with pytest.raises(StorageConflictError):
        item.merge_videos("p")
    saved = item.read_committed_script("p")
    assert saved.merged_video_url == ("video/newer.mp4" if change == "result" else None)
    assert list((item.data_dir / "video").glob("merged_*.mp4")), "retain rendered media after conflict"


def test_title_edit_during_merge_coexists_and_response_is_detached(runtime):
    item, state = runtime
    def edit():
        item.scripts["p"].title = "Edited during render"
        item._commit_storage("projects.json")
    state.hook = edit
    result = item.merge_videos("p")
    assert result.title == "Edited during render"
    assert result.merged_video_url
    result.title = "caller mutation"
    assert item.scripts["p"].title == "Edited during render"


@pytest.mark.parametrize("kind", ["absent", "empty"] )
def test_missing_or_empty_output_does_not_publish_success(runtime, kind):
    item, state = runtime
    def corrupt():
        output = Path(state.calls[-1][-1])
        if kind == "absent":
            output.unlink()
        else:
            output.write_bytes(b"")
    state.hook = corrupt
    with pytest.raises(RuntimeError):
        item.merge_videos("p")
    assert item.scripts["p"].merged_video_url is None
    assert item.read_committed_script("p").merged_video_url is None


def test_repeated_merge_does_not_overwrite_previous_artifact(runtime):
    item, state = runtime
    first = item.merge_videos("p").merged_video_url
    prior = item._output_ref_path(first)
    prior_bytes = Path(prior).read_bytes()
    item.select_video_for_frame("p", "f", "B")
    second = item.merge_videos("p").merged_video_url
    assert first != second
    assert Path(prior).read_bytes() == prior_bytes
    lists = [c[c.index("-i") + 1] for c in state.calls if "concat" in c]
    assert len(set(lists)) == 2
    assert all(not Path(p).exists() for p in lists)


def test_storage_failure_keeps_previous_reference_and_rendered_file(runtime, monkeypatch):
    item, state = runtime
    before = item.read_committed_script("p").model_dump()
    def fail(payloads):
        raise StorageError("synthetic merge save failure")
    state.hook = lambda: monkeypatch.setattr(item._storage, "commit", fail)
    with pytest.raises(StorageError):
        item.merge_videos("p")
    assert item.scripts["p"].model_dump() == before
    assert list((item.data_dir / "video").glob("merged_*.mp4"))


def test_closed_runtime_cannot_publish_rendered_output(runtime):
    item, state = runtime
    state.hook = item.close
    with pytest.raises(StorageError):
        item.merge_videos("p")


def test_ffmpeg_runs_without_storage_lock(runtime):
    from threading import Thread
    item, state = runtime
    completed = []
    def edit():
        with item._save_lock:
            item.scripts["p"].title = "parallel edit"
            item._commit_storage("projects.json")
            completed.append(True)
    def while_rendering():
        thread = Thread(target=edit)
        thread.start()
        thread.join(timeout=2)
        assert completed, "FFmpeg must not hold the storage lock"
    state.hook = while_rendering
    assert item.merge_videos("p").title == "parallel edit"


def test_missing_configured_bgm_fails_and_preserves_previous_merge(runtime):
    item, state = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR")
    script.merged_video_url = "video/prior.mp4"
    script.bgm_url = "audio/missing-bgm.wav"
    script.mix_settings = {"dialogue": 80, "bgm": 35, "sfx": 60}
    item._save_data()

    with pytest.raises(RuntimeError, match="BGM.*missing"):
        item.merge_videos("p")

    assert item.read_committed_script("p").merged_video_url == "video/prior.mp4"
    assert state.merge_sources == [str(item.data_dir / "video/A.mp4")]


def test_bgm_ffmpeg_failure_fails_and_preserves_previous_merge(runtime, monkeypatch):
    item, _ = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR")
    bgm = item.data_dir / "audio/bgm.wav"
    bgm.parent.mkdir(parents=True, exist_ok=True)
    bgm.write_bytes(b"BGM" * 200)
    script.merged_video_url = "video/prior.mp4"
    script.bgm_url = "audio/bgm.wav"
    script.mix_settings = {"dialogue": 80, "bgm": 35, "sfx": 60}
    item._save_data()
    original_run = pipeline_module.subprocess.run

    def fail_bgm_mux(command, **kwargs):
        if "-filter_complex" in command:
            raise subprocess.CalledProcessError(1, command, stderr=b"synthetic mux failure")
        return original_run(command, **kwargs)

    monkeypatch.setattr(pipeline_module.subprocess, "run", fail_bgm_mux)

    with pytest.raises(RuntimeError, match="BGM.*failed"):
        item.merge_videos("p")

    assert item.read_committed_script("p").merged_video_url == "video/prior.mp4"


def test_merge_persists_a_managed_reference_that_http_can_serve(runtime):
    from fastapi.testclient import TestClient
    from src.apps.comic_gen import api
    item, _ = runtime
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    with TestClient(app) as client:
        response = client.post("/projects/p/merge")
        assert response.status_code == 200, response.text
        reference = response.json()["merged_video_url"]
        assert reference.startswith("video/")
        assert Path(item._output_ref_path(reference)).is_file()
        media = client.get("/files/" + reference)
        assert media.status_code == 200
        assert media.content.startswith(b"CONCAT:")
