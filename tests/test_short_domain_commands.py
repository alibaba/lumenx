"""Regression coverage for short asset, frame, and take-selection commands."""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import (
    CameraMovementData, Character, GenerationStatus, ImageAsset, ImageVariant,
    Prop, Scene, Script, Series, StoryboardFrame, VideoTask,
)
from src.utils.json_storage import StorageConflictError, StorageError


COMMANDS = [
    "sync_descriptions_from_script_entities", "add_character", "delete_character",
    "add_scene", "delete_scene", "toggle_asset_lock", "toggle_asset_starred",
    "update_asset_image", "update_asset_description", "update_asset_attributes",
    "add_prop", "delete_prop", "toggle_frame_lock", "update_frame", "add_frame",
    "copy_frame", "delete_frame", "reorder_frames", "select_video_for_frame",
    "auto_select_latest_video", "unpin_video",
]


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(
        id="p", title="project", original_text="original", created_at=1, updated_at=1,
        series_id="s",
        characters=[Character(id="c1", name="character", description="old", full_body_prompt="saved prompt")],
        scenes=[Scene(id="s1", name="scene", description="old")],
        props=[Prop(id="prop1", name="prop", description="old")],
        frames=[
            StoryboardFrame(
                id="f1", scene_id="s1", character_ids=["c1"], prop_ids=["prop1"],
                camera_movement_structured=CameraMovementData(primary="pan", description="old move"),
                composition_data={"layers": [{"name": "original"}]},
                image_asset=ImageAsset(selected_id="image1", variants=[ImageVariant(id="image1", url="image.png")]),
                t2i_image_urls=["image.png"], image_url="image.png", locked=True,
            ),
            StoryboardFrame(id="f2", scene_id="s1"),
        ],
        video_tasks=[
            VideoTask(id="v1", project_id="p", frame_id="f1", image_url="image.png", prompt="video", status=GenerationStatus.COMPLETED, video_url="old.mp4", created_at=1),
            VideoTask(id="v2", project_id="p", frame_id="f1", image_url="image.png", prompt="video", status=GenerationStatus.COMPLETED, video_url="new.mp4", created_at=2),
        ],
    )
    item.series_store["s"] = Series(id="s", title="series", created_at=1, updated_at=1, episode_ids=["p"])
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    yield item
    item.close()


def _prepare(pipeline, command):
    if command == "unpin_video":
        pipeline.scripts["p"].frames[0].is_video_pinned = True
        pipeline._save_data()


def _run(pipeline, command):
    args = {
        "sync_descriptions_from_script_entities": (),
        "add_character": ("new character", "new"), "delete_character": ("c1",),
        "add_scene": ("new scene", "new"), "delete_scene": ("s1",),
        "toggle_asset_lock": ("c1", "character"), "toggle_asset_starred": ("c1", "character"),
        "update_asset_image": ("c1", "character", "new.png"),
        "update_asset_description": ("c1", "character", "new"),
        "update_asset_attributes": ("c1", "character", {"name": "renamed", "description": "new"}),
        "add_prop": ("new prop", "new"), "delete_prop": ("prop1",),
        "toggle_frame_lock": ("f1",), "update_frame": ("f1",), "add_frame": (),
        "copy_frame": ("f1",), "delete_frame": ("f2",),
        "reorder_frames": (["f2", "f1"],), "select_video_for_frame": ("f1", "v1"),
        "auto_select_latest_video": ("f1",), "unpin_video": ("f1",),
    }[command]
    kwargs = {"action_description": "edited", "camera_movement_description": "new move", "expected_revision": pipeline.scripts["p"].revision} if command == "update_frame" else {}
    return getattr(pipeline, command)("p", *args, **kwargs)


def _disk(pipeline):
    return {name: (pipeline.data_dir / name).read_bytes() for name in (
        "projects.json", "series.json", "library_assets.json",
    )}


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("state", ["closed", "failed"])
def test_unavailable_runtime_rejects_command_before_mutation(pipeline, command, state):
    _prepare(pipeline, command)
    original = pipeline.scripts["p"]
    before, disk = original.model_dump(), _disk(pipeline)
    if state == "closed":
        pipeline.close()
    else:
        pipeline._storage_failed = StorageError("previous failure")
    with pytest.raises(StorageError):
        _run(pipeline, command)
    assert original.model_dump() == before
    assert pipeline.scripts["p"].model_dump() == before
    assert _disk(pipeline) == disk


@pytest.mark.parametrize("command", COMMANDS)
def test_command_failure_restores_committed_state(pipeline, monkeypatch, command):
    _prepare(pipeline, command)
    before, disk = pipeline.scripts["p"].model_dump(), _disk(pipeline)
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))
    with pytest.raises(StorageError):
        _run(pipeline, command)
    assert pipeline.scripts["p"].model_dump() == before
    assert _disk(pipeline) == disk
    with pytest.raises(StorageError):
        pipeline.assert_storage_available()


@pytest.mark.parametrize("command", COMMANDS)
def test_command_returns_a_detached_committed_response(pipeline, command):
    _prepare(pipeline, command)
    result = _run(pipeline, command)
    saved = json.loads((pipeline.data_dir / "projects.json").read_text())["p"]
    assert result.model_dump(mode="json") == saved
    result.title = "local response edit"
    result.frames[0].character_ids.append("response-only")
    assert pipeline.scripts["p"].title == "project"
    assert "response-only" not in pipeline.scripts["p"].frames[0].character_ids


def _overlap(first, second, paused, release):
    started = Event()
    def run_second():
        started.set()
        return second()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first)
        try:
            assert paused.wait(5)
            second_result = pool.submit(run_second)
            assert started.wait(5)
            try:
                second_result.result(timeout=0.2)
            except TimeoutError:
                pass
        finally:
            release.set()
        first_result.result(timeout=5)
        second_result.result(timeout=5)


def _pause_first_iteration(values, paused, release):
    class ObservedList(list):
        did_pause = False
        def __iter__(self):
            snapshot = list(list.__iter__(self))
            if not self.did_pause:
                self.did_pause = True
                paused.set()
                assert release.wait(5)
            return iter(snapshot)
    return ObservedList(values)


def test_manual_pick_wins_against_an_inflight_automatic_pick(pipeline):
    script = pipeline.scripts["p"]
    paused, release = Event(), Event()
    script.video_tasks = _pause_first_iteration(script.video_tasks, paused, release)
    _overlap(
        lambda: pipeline.auto_select_latest_video("p", "f1"),
        lambda: pipeline.select_video_for_frame("p", "f1", "v1"), paused, release,
    )
    frame = pipeline.scripts["p"].frames[0]
    assert (frame.selected_video_id, frame.video_url, frame.is_video_pinned) == ("v1", "old.mp4", True)
    saved = json.loads((pipeline.data_dir / "projects.json").read_text())["p"]["frames"][0]
    assert (saved["selected_video_id"], saved["video_url"], saved["is_video_pinned"]) == ("v1", "old.mp4", True)


@pytest.mark.parametrize("case", ["pinned", "no_tasks", "already_selected", "already_unpinned"])
def test_selection_noops_still_gate_runtime_and_return_detached_state(pipeline, monkeypatch, case):
    script = pipeline.scripts["p"]
    frame = script.frames[0]
    command = pipeline.auto_select_latest_video
    if case == "pinned":
        frame.is_video_pinned = True
    elif case == "no_tasks":
        script.video_tasks = []
    elif case == "already_selected":
        frame.selected_video_id, frame.video_url = "v2", "new.mp4"
    else:
        command = pipeline.unpin_video
    pipeline._save_data()
    before = script.model_dump()
    commit = Mock(wraps=pipeline._storage.commit)
    monkeypatch.setattr(pipeline._storage, "commit", commit)
    result = command("p", "f1")
    result.frames[0].character_ids.append("response-only")
    assert script.model_dump() == before
    commit.assert_not_called()
    pipeline.close()
    with pytest.raises(StorageError):
        command("p", "f1")


@pytest.mark.parametrize("change", ["add", "delete"])
def test_reorder_does_not_lose_a_concurrent_frame_change(pipeline, change):
    script = pipeline.scripts["p"]
    paused, release = Event(), Event()
    script.frames = _pause_first_iteration(script.frames, paused, release)
    _overlap(
        lambda: pipeline.reorder_frames("p", ["f2", "f1"]),
        lambda: pipeline.add_frame("p") if change == "add" else pipeline.delete_frame("p", "f2"),
        paused, release,
    )
    ids = [frame.id for frame in pipeline.scripts["p"].frames]
    if change == "add":
        assert len(ids) == 3 and ids[:2] == ["f2", "f1"]
    else:
        assert ids == ["f1"]
    saved = json.loads((pipeline.data_dir / "projects.json").read_text())["p"]
    assert [frame["id"] for frame in saved["frames"]] == ids


@pytest.mark.parametrize("ids", [["f1"], ["f1", "f1"], ["f1", "unknown"], []])
def test_reorder_requires_the_complete_unique_current_frame_set(pipeline, ids):
    before, disk = pipeline.scripts["p"].model_dump(), _disk(pipeline)
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put("/projects/p/frames/reorder", json={"frame_ids": ids})
        assert response.status_code == 409, response.text
        assert pipeline.scripts["p"].model_dump() == before
        assert _disk(pipeline) == disk
        pipeline.assert_storage_available()


def test_reorder_old_list_after_new_frame_is_a_conflict(pipeline):
    pipeline.add_frame("p")
    before, disk = pipeline.scripts["p"].model_dump(), _disk(pipeline)
    with pytest.raises(StorageConflictError):
        pipeline.reorder_frames("p", ["f2", "f1"])
    assert pipeline.scripts["p"].model_dump() == before
    assert _disk(pipeline) == disk


def test_reorder_empty_project_accepts_empty_list(pipeline):
    pipeline.scripts["p"].frames = []
    pipeline._save_data()
    assert pipeline.reorder_frames("p", []).frames == []


@pytest.mark.parametrize("insert_at,copy_index", [(None, 1), (0, 0), (100, 1)])
def test_copy_keeps_content_but_separates_nested_objects(pipeline, insert_at, copy_index):
    original = pipeline.scripts["p"].frames[0]
    original.video_url, original.selected_video_id = "old.mp4", "v1"
    original.is_video_pinned, original.final_take_id = True, "v1"
    pipeline._save_data()
    result = pipeline.copy_frame("p", "f1", insert_at)
    copied = result.frames[copy_index]
    assert copied.id != "f1"
    assert copied.image_url == original.image_url and copied.locked is False
    assert copied.video_url == "old.mp4"
    assert copied.is_video_pinned is True
    assert copied.selected_video_id not in {None, "v1", "v2"}
    assert copied.final_take_id == copied.selected_video_id
    cloned_task = next(task for task in result.video_tasks if task.id == copied.selected_video_id)
    assert (cloned_task.frame_id, cloned_task.video_url, cloned_task.provider_task_id) == (copied.id, "old.mp4", None)
    assert isinstance(copied.updated_at, float)
    live_copy = next(frame for frame in pipeline.scripts["p"].frames if frame.id == copied.id)
    for field in ("character_ids", "prop_ids", "composition_data", "image_asset", "t2i_image_urls"):
        assert getattr(live_copy, field) is not getattr(original, field), field
    pipeline.update_frame("p", copied.id, expected_revision=pipeline.read_committed_script("p").revision, camera_movement_description="copy-only move")
    pipeline.close()
    restored = module.ComicGenPipeline(data_dir=pipeline.data_dir, script_processor=Mock())
    try:
        frames = {frame.id: frame for frame in restored.get_script("p").frames}
        assert frames["f1"].camera_movement_structured.description == "old move"
        assert frames[copied.id].camera_movement_structured.description == "copy-only move"
    finally:
        restored.close()


@pytest.mark.parametrize("kind", ["frame", "attributes", "image"])
def test_bad_multi_field_edit_is_rejected_before_live_mutation(pipeline, kind):
    before, disk = pipeline.scripts["p"].model_dump(), _disk(pipeline)
    with pytest.raises(ValueError):
        if kind == "frame":
            pipeline.update_frame("p", "f1", expected_revision=pipeline.read_committed_script("p").revision, action_description="new", camera_movement_description=[])
        elif kind == "attributes":
            pipeline.update_asset_attributes("p", "c1", "character", {"name": "new", "description": None})
        else:
            pipeline.update_asset_image("p", "c1", "character", [])
    assert pipeline.scripts["p"].model_dump() == before
    assert _disk(pipeline) == disk
    pipeline.assert_storage_available()


@pytest.mark.parametrize("source", ["script", "series", "global"])
def test_asset_patch_commits_only_its_owning_aggregate(pipeline, source):
    asset = pipeline.scripts["p"].characters[0]
    if source != "script":
        pipeline.scripts["p"].characters = []
        owner = pipeline.series_store["s"] if source == "series" else pipeline.library_store
        owner.characters.append(asset)
        pipeline._commit_storage("projects.json", "series.json", "library_assets.json")
    before = _disk(pipeline)
    pipeline.update_asset_attributes("p", "c1", "character", {"name": "changed"})
    names = {"script": "projects.json", "series": "series.json", "global": "library_assets.json"}
    after = _disk(pipeline)
    assert after[names[source]] != before[names[source]]
    for name in set(before) - {names[source]}:
        assert after[name] == before[name]


@pytest.mark.parametrize("source", ["series", "global"])
def test_shared_asset_patch_failure_rolls_back_the_owner(pipeline, monkeypatch, source):
    asset = pipeline.scripts["p"].characters.pop()
    owner = pipeline.series_store["s"] if source == "series" else pipeline.library_store
    owner.characters.append(asset)
    pipeline._commit_storage("projects.json", "series.json", "library_assets.json")
    before, disk = owner.model_dump(), _disk(pipeline)
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))
    with pytest.raises(StorageError):
        pipeline.update_asset_image("p", "c1", "character", "new.png")
    current = pipeline.series_store["s"] if source == "series" else pipeline.library_store
    assert current.model_dump() == before
    assert _disk(pipeline) == disk


@pytest.mark.parametrize("source", ["series", "global"])
@pytest.mark.parametrize("command", ["toggle_asset_lock", "toggle_asset_starred", "update_asset_attributes"])
def test_shared_mutation_does_not_publish_other_uncommitted_project_fields(pipeline, source, command):
    asset = pipeline.scripts["p"].characters.pop()
    owner = pipeline.series_store["s"] if source == "series" else pipeline.library_store
    owner.characters.append(asset)
    pipeline._commit_storage("projects.json", "series.json", "library_assets.json")
    saved = json.loads((pipeline.data_dir / "projects.json").read_text())["p"]
    pipeline.scripts["p"].title = "unfinished generation mutation"
    result = _run(pipeline, command)
    assert result.model_dump(mode="json") == saved
    assert json.loads((pipeline.data_dir / "projects.json").read_text())["p"] == saved


@pytest.mark.parametrize("case", ["pinned", "no_tasks", "already_selected", "already_unpinned"])
def test_noop_adoption_reads_committed_project_without_saving_other_work(pipeline, case):
    script = pipeline.scripts["p"]
    frame = script.frames[0]
    command = pipeline.auto_select_latest_video
    if case == "pinned":
        frame.is_video_pinned = True
    elif case == "no_tasks":
        script.video_tasks = []
    elif case == "already_selected":
        frame.selected_video_id, frame.video_url = "v2", "new.mp4"
    else:
        command = pipeline.unpin_video
    pipeline._save_data()
    saved = json.loads((pipeline.data_dir / "projects.json").read_text())["p"]
    script.title = "unfinished generation mutation"
    result = command("p", "f1")
    assert result.model_dump(mode="json") == saved
    assert json.loads((pipeline.data_dir / "projects.json").read_text())["p"] == saved


@pytest.mark.parametrize("command", ["text", "audio", "series", "series_models"])
def test_noop_edit_response_does_not_publish_other_uncommitted_fields(pipeline, command):
    is_series = command.startswith("series")
    owner = pipeline.series_store["s"] if is_series else pipeline.scripts["p"]
    filename, key = ("series.json", "s") if is_series else ("projects.json", "p")
    saved = json.loads((pipeline.data_dir / filename).read_text())[key]
    owner.title = "unfinished generation mutation"
    if command == "text":
        result = pipeline.update_script_text("p", owner.original_text, expected_revision=owner.revision)
    elif command == "audio":
        result = pipeline.update_audio_mix("p", {})
    elif command == "series":
        result = pipeline.update_series("s", {}, expected_revision=owner.revision)
    else:
        result = pipeline.update_series_model_settings("s", {})
    assert result.model_dump(mode="json") == saved
    assert json.loads((pipeline.data_dir / filename).read_text())[key] == saved


def test_prop_http_delete_cleans_references_and_preserves_404s(pipeline):
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        missing_project = client.delete("/projects/missing/props/prop1")
        assert missing_project.status_code == 404
        assert missing_project.json()["detail"] == "Project not found"
        missing = client.delete("/projects/p/props/missing")
        assert missing.status_code == 404 and missing.json()["detail"] == "Prop not found"
        created = client.post("/projects/p/props", json={"name": "new prop"})
        assert created.status_code == 200, created.text
        assert created.json()["props"][-1]["name"] == "new prop"
        removed = client.delete("/projects/p/props/prop1")
        assert removed.status_code == 200, removed.text
        assert "prop1" not in removed.json()["frames"][0]["prop_ids"]
    saved = json.loads((pipeline.data_dir / "projects.json").read_text())["p"]
    assert "prop1" not in saved["frames"][0]["prop_ids"]


def test_invalid_asset_patch_is_422_without_mutation_or_runtime_failure(pipeline):
    before, disk = pipeline.scripts["p"].model_dump(), _disk(pipeline)
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.post("/projects/p/assets/update_attributes", json={
            "asset_type": "character", "asset_id": "c1",
            "attributes": {"name": "not saved", "description": None},
        })
        assert response.status_code == 422, response.text
        assert pipeline.scripts["p"].model_dump() == before
        assert _disk(pipeline) == disk
        pipeline.assert_storage_available()
        missing = client.post("/projects/p/assets/update_attributes", json={
            "asset_type": "character", "asset_id": "missing", "attributes": {"name": "new"},
        })
        assert missing.status_code == 404


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/projects/p/props", {"name": "new"}),
    ("delete", "/projects/p/props/prop1", None),
    ("put", "/projects/p/frames/reorder", {"frame_ids": ["f2", "f1"]}),
])
def test_http_storage_failure_is_503(pipeline, monkeypatch, method, path, payload):
    before, disk = pipeline.scripts["p"].model_dump(), _disk(pipeline)
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.request(method, path, json=payload)
        assert response.status_code == 503, response.text
        assert pipeline.scripts["p"].model_dump() == before
        assert _disk(pipeline) == disk
