"""Dub provenance/control-flow regressions with synthetic bytes and fake FFmpeg."""

import json
import subprocess
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import DubSource, Script, StoryboardFrame, VideoTask
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    state = SimpleNamespace(now=1000, calls=[], merge_sources=[], hook=None, separate=False)
    for ref, data in {
        "video/A.mp4": b"TAKE-A",
        "video/B.mp4": b"TAKE-B",
        "video/foreign.mp4": b"OTHER-FRAME",
        "audio/line.mp3": b"AUDIO-OLD" * 200,
        "audio/new.mp3": b"AUDIO-NEW" * 200,
    }.items():
        path = item.data_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    item.scripts["p"] = Script(
        id="p",
        title="dub probe",
        original_text="source",
        created_at=1,
        updated_at=1,
        frames=[
            StoryboardFrame(
                id="f",
                scene_id="",
                dialogue="hello",
                audio_url="audio/line.mp3",
                selected_video_id="A",
                video_url="video/A.mp4",
            )
        ],
        video_tasks=[
            VideoTask(
                id=key,
                project_id="p",
                frame_id="other" if key == "foreign" else "f",
                image_url="",
                prompt="",
                status="completed",
                video_url=f"video/{key}.mp4",
            )
            for key in ("A", "B", "foreign")
        ],
    )
    item._save_data()
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: state.now))
    monkeypatch.setattr(module, "get_ffmpeg_path", lambda: "/offline/ffmpeg")

    def separate(video_path, work_dir):
        if not state.separate:
            return None
        path = Path(work_dir) / "background.wav"
        path.write_bytes(b"BG:" + Path(video_path).read_bytes())
        return str(path)

    monkeypatch.setattr(item, "_separate_background_audio", separate)

    def run(command, **kwargs):
        state.calls.append(command)
        if "-version" in command:
            return SimpleNamespace(returncode=0, stdout="offline fake ffmpeg", stderr="")
        if command[-3:] == ["-f", "null", "-"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        inputs = [Path(command[index + 1]) for index, value in enumerate(command) if value == "-i"]
        if "concat" in command:
            paths = [line.removeprefix("file '").removesuffix("'") for line in inputs[0].read_text().splitlines()]
            state.merge_sources = paths
            data = b"CONCAT:" + b"|".join(Path(path).read_bytes() for path in paths)
        else:
            data = b"MUX:" + b"|".join(path.read_bytes() for path in inputs)
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        if state.hook and output.suffix == ".mp4":
            hook, state.hook = state.hook, None
            hook()
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        module,
        "subprocess",
        SimpleNamespace(
            run=run,
            CalledProcessError=subprocess.CalledProcessError,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    yield item, state
    item.close()


def preview(runtime, task="A", offset_ms=25):
    item, state = runtime
    result = item.preview_dub("p", "f", task, offset_ms=offset_ms)
    state.now += 1
    return result.frames[0].preview_video_url


def applied_a(runtime):
    item, _ = runtime
    preview(runtime)
    item.apply_dub("p", "f")
    return item.read_committed_script("p").frames[0]


def test_applied_a_then_preview_b_must_not_relabel_applied_a(runtime):
    item, _ = runtime
    first = applied_a(runtime)
    item.select_video_for_frame("p", "f", "B")
    preview(runtime, "B")
    saved = item.read_committed_script("p").frames[0]
    assert saved.dubbed_video_url == first.dubbed_video_url
    assert saved.dubbed_video_task_id == "A", "preview must have its own source identity"


def test_merge_uses_selected_b_instead_of_applied_audio_from_a(runtime):
    item, state = runtime
    applied_a(runtime)
    item.select_video_for_frame("p", "f", "B")
    item.merge_videos("p")
    assert state.merge_sources == [str(item.data_dir / "video/B.mp4")]


def test_merge_uses_applied_dub_only_when_source_matches_current_take(runtime):
    item, state = runtime
    applied = applied_a(runtime)
    item.merge_videos("p")
    assert state.merge_sources == [str(item.data_dir / applied.dubbed_video_url)]


def test_preview_explicit_b_does_not_select_b_and_apply_waits_for_matching_take(runtime):
    item, _ = runtime
    preview(runtime, "B")
    saved = item.read_committed_script("p").frames[0]
    assert saved.selected_video_id == "A"
    assert saved.dub_preview_source.task_id == "B"
    with pytest.raises(StorageConflictError):
        item.apply_dub("p", "f")
    item.select_video_for_frame("p", "f", "B")
    applied = item.apply_dub("p", "f").frames[0]
    assert applied.selected_video_id == "B"
    assert applied.dubbed_video_task_id == "B"
    assert applied.dub_applied_source.task_id == "B"


def test_preview_different_offset_does_not_invalidate_applied_take_or_legacy_offset(runtime):
    item, _ = runtime
    applied = applied_a(runtime)
    assert applied.dub_offset_ms == 25
    item.select_video_for_frame("p", "f", "B")
    preview_b = preview(runtime, "B", offset_ms=100)
    saved = item.read_committed_script("p").frames[0]
    assert saved.dubbed_video_url == applied.dubbed_video_url
    assert saved.dub_applied_source.task_id == "A"
    assert saved.dub_applied_source.offset_ms == 25
    assert saved.dub_preview_source.task_id == "B"
    assert saved.dub_preview_source.offset_ms == 100
    assert saved.dub_offset_ms == 25, "legacy field remains the applied compatibility offset"

    item.select_video_for_frame("p", "f", "A")
    current = item.read_committed_script("p").frames[0]
    assert item.resolve_frame_video_for_render(current, item.read_committed_script("p").video_tasks) == applied.dubbed_video_url
    assert item.resolve_frame_video_for_render(current, item.read_committed_script("p").video_tasks, allow_preview=True) == applied.dubbed_video_url

    item.select_video_for_frame("p", "f", "B")
    current = item.read_committed_script("p").frames[0]
    assert item.resolve_frame_video_for_render(current, item.read_committed_script("p").video_tasks) == "video/B.mp4"
    assert item.resolve_frame_video_for_render(current, item.read_committed_script("p").video_tasks, allow_preview=True) == preview_b


def test_apply_copies_preview_offset_to_applied_legacy_field(runtime):
    item, _ = runtime
    item.select_video_for_frame("p", "f", "B")
    preview(runtime, "B", offset_ms=100)
    applied = item.apply_dub("p", "f").frames[0]
    assert applied.dubbed_video_task_id == "B"
    assert applied.dub_applied_source.offset_ms == 100
    assert applied.dub_offset_ms == 100


@pytest.mark.parametrize("bad_offset", [-1, 1.5, "25", True])
def test_preview_rejects_non_strict_nonnegative_integer_offset_before_media(runtime, bad_offset):
    item, state = runtime
    with pytest.raises(ValueError):
        item.preview_dub("p", "f", "A", offset_ms=bad_offset)
    assert not state.calls


@pytest.mark.parametrize("payload", [
    {"video_task_id": "A", "offset_ms": "25"},
    {"video_task_id": "A", "offset_ms": -1},
])
def test_preview_route_rejects_non_strict_nonnegative_offset_without_media(runtime, payload):
    item, state = runtime
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    with TestClient(app) as client:
        response = client.post("/projects/p/frames/f/dub/preview", json=payload)
    assert response.status_code == 422, response.text
    assert not state.calls


@pytest.mark.parametrize("field", ["task_id", "video_url", "audio_url"])
def test_dub_source_requires_nonempty_input_identity(field):
    payload = dict(schema_version=1, task_id="A", video_url="video/A.mp4", audio_url="audio/line.mp3", offset_ms=0)
    payload[field] = ""
    with pytest.raises(ValidationError):
        DubSource(**payload)


def test_dub_source_requires_schema_version_and_rejects_malformed_v1():
    with pytest.raises(ValidationError):
        DubSource(task_id="A", video_url="video/A.mp4", audio_url="audio/line.mp3", offset_ms=25)
    with pytest.raises(ValidationError):
        DubSource(schema_version=1, task_id="A", video_url="video/A.mp4", audio_url="audio/line.mp3", offset_ms=-1)
    with pytest.raises(ValidationError):
        DubSource(schema_version=1, task_id="A", video_url="video/A.mp4", audio_url="audio/line.mp3", offset_ms=25, extra="x")


def test_no_version_dub_source_payload_is_not_loaded_as_trusted_v1(runtime):
    item, _ = runtime
    payload = item.read_committed_script("p").model_dump(mode="json")
    payload["frames"][0]["dub_applied_source"] = {
        "task_id": "A",
        "video_url": "video/A.mp4",
        "audio_url": "audio/line.mp3",
        "offset_ms": 25,
    }
    with pytest.raises(ValidationError):
        Script.model_validate(payload)


def test_legacy_dub_without_source_is_unknown_and_not_used_for_merge(runtime):
    item, state = runtime
    legacy = item.data_dir / "video/legacy-dub.mp4"
    legacy.write_bytes(b"LEGACY-DUB")
    with item._save_lock:
        frame = item.scripts["p"].frames[0]
        frame.dubbed_video_url = "video/legacy-dub.mp4"
        frame.dubbed_video_task_id = "A"
        frame.dub_applied_source = None
        item._save_data()
    item.merge_videos("p")
    assert state.merge_sources == [str(item.data_dir / "video/A.mp4")]


def test_future_dub_source_is_preserved_but_not_trusted_for_merge(runtime):
    item, state = runtime
    future = item.data_dir / "video/future-dub.mp4"
    future.write_bytes(b"FUTURE-DUB")
    payload = item.read_committed_script("p").model_dump(mode="json")
    payload["frames"][0]["dubbed_video_url"] = "video/future-dub.mp4"
    payload["frames"][0]["dubbed_video_task_id"] = "A"
    payload["frames"][0]["dub_applied_source"] = {
        "schema_version": 2,
        "task_id": "A",
        "video_url": "video/A.mp4",
        "audio_url": "audio/line.mp3",
        "offset_ms": 25,
        "future_field": "opaque",
    }
    item.scripts["p"] = Script.model_validate(payload)
    item._save_data()
    saved = item.read_committed_script("p").frames[0]
    assert saved.dub_applied_source.schema_version == 2
    item.merge_videos("p")
    assert state.merge_sources == [str(item.data_dir / "video/A.mp4")]


def test_cross_frame_video_task_is_rejected_before_media_processing(runtime):
    item, state = runtime
    before = item.read_committed_script("p").model_dump()
    with pytest.raises(ValueError):
        item.preview_dub("p", "f", "foreign")
    assert not state.calls
    assert item.read_committed_script("p").model_dump() == before


def test_same_second_preview_never_overwrites_applied_media(runtime):
    item, state = runtime
    first = applied_a(runtime)
    path = item.data_dir / first.dubbed_video_url
    original = path.read_bytes()
    state.now = 1000
    item.select_video_for_frame("p", "f", "B")
    preview(runtime, "B")
    assert path.read_bytes() == original
    assert item.read_committed_script("p").frames[0].preview_video_url != first.dubbed_video_url


@pytest.mark.parametrize("operation", ["preview", "apply", "revert"])
def test_failed_save_does_not_delete_previously_committed_media(runtime, monkeypatch, operation):
    item, _ = runtime
    first = applied_a(runtime)
    item.select_video_for_frame("p", "f", "B")
    preview_url = preview(runtime, "B")
    paths = [item.data_dir / first.dubbed_video_url, item.data_dir / preview_url]
    original = {path: path.read_bytes() for path in paths}
    before = (item.data_dir / "projects.json").read_bytes()
    monkeypatch.setattr(item._storage, "commit", Mock(side_effect=StorageError("offline disk full")))
    with pytest.raises(StorageError):
        if operation == "preview":
            preview(runtime, "B")
        elif operation == "apply":
            item.apply_dub("p", "f")
        else:
            item.revert_dub("p", "f")
    assert (item.data_dir / "projects.json").read_bytes() == before
    assert item.scripts["p"].model_dump(mode="json") == json.loads(before)["p"]
    assert all(path.exists() and path.read_bytes() == data for path, data in original.items())


def test_failed_preview_does_not_overwrite_committed_background_cache(runtime, monkeypatch):
    item, state = runtime
    state.separate = True
    applied_a(runtime)
    first = item.read_committed_script("p").frames[0]
    cache = item.data_dir / first.bg_audio_url
    original = cache.read_bytes()
    item.select_video_for_frame("p", "f", "B")
    monkeypatch.setattr(item._storage, "commit", Mock(side_effect=StorageError("offline disk full")))
    with pytest.raises(StorageError):
        preview(runtime, "B")
    assert cache.read_bytes() == original


def test_apply_rejects_preview_after_selected_take_changes(runtime):
    item, _ = runtime
    preview(runtime, "A")
    item.select_video_for_frame("p", "f", "B")
    with pytest.raises(StorageConflictError):
        item.apply_dub("p", "f")


@pytest.mark.parametrize("change", ["take", "audio"])
def test_late_preview_rejects_changed_inputs_and_preserves_created_file(runtime, change):
    item, state = runtime

    def modify():
        errors = []

        def command():
            try:
                if change == "take":
                    item.select_video_for_frame("p", "f", "B")
                else:
                    with item._save_lock:
                        item.scripts["p"].frames[0].audio_url = "audio/new.mp3"
                        item._save_data()
            except BaseException as exc:
                errors.append(exc)

        thread = Thread(target=command, daemon=True)
        thread.start()
        thread.join(2)
        assert not thread.is_alive(), "media processing must not hold the command lock"
        if errors:
            raise errors[0]

    state.hook = modify
    with pytest.raises(StorageConflictError):
        preview(runtime, "A")
    assert list((item.data_dir / "video").glob("preview_*.mp4"))
    assert item.read_committed_script("p").frames[0].preview_video_url is None


@pytest.mark.parametrize("operation", ["preview", "apply", "revert"])
def test_closed_runtime_does_not_delete_existing_media(runtime, operation):
    item, _ = runtime
    first = applied_a(runtime)
    preview_url = preview(runtime)
    paths = [item.data_dir / first.dubbed_video_url, item.data_dir / preview_url]
    original = {path: path.read_bytes() for path in paths}
    item.close()
    with pytest.raises(StorageError):
        if operation == "preview":
            preview(runtime)
        elif operation == "apply":
            item.apply_dub("p", "f")
        else:
            item.revert_dub("p", "f")
    assert all(path.exists() and path.read_bytes() == data for path, data in original.items())
