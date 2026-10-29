"""Asset video deletion removes only owned metadata and never deletes media bytes."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    GlobalAssetLibrary,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
    VideoVariant,
)
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    item.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        characters=[_character_with_videos("c")],
        frames=[StoryboardFrame(id="f", scene_id="", selected_video_id="frame-task", final_take_id="frame-task")],
        video_tasks=[
            _task("asset-video", asset_id="c", video_url="video/shared.mp4"),
            _task("spare-video", asset_id="c", video_url="video/spare.mp4"),
            _task("frame-task", frame_id="f", asset_id=None, video_url="video/frame.mp4"),
        ],
    )
    for ref, data in {
        "video/shared.mp4": b"shared media",
        "video/spare.mp4": b"spare media",
        "video/frame.mp4": b"frame media",
    }.items():
        path = item.data_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    yield item
    item.close()


def _task(task_id, *, asset_id=None, frame_id=None, video_url="video/clip.mp4"):
    return VideoTask(
        id=task_id,
        project_id="p",
        asset_id=asset_id,
        frame_id=frame_id,
        image_url="uploads/source.png",
        prompt="motion",
        status="completed",
        video_url=video_url,
    )


def _character_with_videos(char_id="c"):
    return Character(
        id=char_id,
        name="Hero",
        description="red coat",
        reference_sheet=AssetUnit(
            selected_video_id="asset-video",
            video_variants=[
                VideoVariant(id="asset-video", url="video/shared.mp4"),
                VideoVariant(id="spare-video", url="video/spare.mp4"),
            ],
        ),
        video_assets=[
            _task("asset-video", asset_id=char_id, video_url="video/shared.mp4"),
            _task("spare-video", asset_id=char_id, video_url="video/spare.mp4"),
        ],
    )


def saved(item, filename="projects.json"):
    return json.loads((item.data_dir / filename).read_text(encoding="utf-8"))


def test_delete_asset_video_save_failure_rolls_back_metadata_and_keeps_media(runtime, monkeypatch):
    item = runtime
    before = saved(item)
    media_path = item.data_dir / "video/shared.mp4"
    failure = StorageError("synthetic save failure")

    def fail(payloads):
        raise failure

    monkeypatch.setattr(item._storage, "commit", fail)

    with pytest.raises(StorageError) as raised:
        item.delete_asset_video("p", "c", "character", "asset-video")

    assert raised.value is failure
    assert saved(item) == before
    assert media_path.read_bytes() == b"shared media"
    assert item.scripts["p"].characters[0].video_assets[0].id == "asset-video"
    assert item.scripts["p"].characters[0].reference_sheet.selected_video_id == "asset-video"


def test_delete_asset_video_success_preserves_shared_media_and_returns_committed_snapshot(runtime):
    item = runtime
    result = item.delete_asset_video("p", "c", "character", "asset-video")

    result.title = "caller mutation"
    persisted = saved(item)["p"]
    assert [video["id"] for video in persisted["characters"][0]["video_assets"]] == ["spare-video"]
    assert [task["id"] for task in persisted["video_tasks"]] == ["spare-video", "frame-task"]
    assert (item.data_dir / "video/shared.mp4").read_bytes() == b"shared media"
    assert item.scripts["p"].title == "project"


def test_delete_asset_video_closed_runtime_rejects_without_mutation(runtime):
    item = runtime
    before_live = item.scripts["p"].model_dump(mode="json")
    item.close()

    with pytest.raises(StorageError):
        item.delete_asset_video("p", "c", "character", "asset-video")

    assert item.scripts["p"].model_dump(mode="json") == before_live


@pytest.mark.parametrize("owner", ["series", "global"])
def test_delete_asset_video_writes_actual_shared_owner(runtime, owner):
    item = runtime
    character = item.scripts["p"].characters.pop()
    item.scripts["p"].video_tasks = []
    if owner == "series":
        item.scripts["p"].series_id = "s"
        item.series_store["s"] = Series(id="s", title="series", created_at=1, updated_at=1, characters=[character])
    else:
        item.library_store = GlobalAssetLibrary(characters=[character])
    item._commit_storage("projects.json", "series.json", "library_assets.json")

    item.delete_asset_video("p", "c", "character", "asset-video")

    assert saved(item)["p"]["characters"] == []
    if owner == "series":
        assert [v["id"] for v in saved(item, "series.json")["s"]["characters"][0]["video_assets"]] == ["spare-video"]
        assert [v["id"] for v in saved(item, "library_assets.json")["characters"]] == []
    else:
        assert saved(item, "series.json") == {}
        assert [v["id"] for v in saved(item, "library_assets.json")["characters"][0]["video_assets"]] == ["spare-video"]


def test_delete_selected_asset_unit_video_falls_back_and_preserves_frame_selection(runtime):
    item = runtime

    item.delete_asset_video("p", "c", "character", "asset-video")

    committed = item.read_committed_script("p")
    character = committed.characters[0]
    assert character.reference_sheet.selected_video_id == "spare-video"
    assert [variant.id for variant in character.reference_sheet.video_variants] == ["spare-video"]
    assert committed.frames[0].selected_video_id == "frame-task"
    assert committed.frames[0].final_take_id == "frame-task"
    assert [task.id for task in committed.video_tasks] == ["spare-video", "frame-task"]


def test_delete_asset_video_rejects_foreign_video_id_without_removing_same_id_frame_task(runtime):
    item = runtime
    item.scripts["p"].characters[0].video_assets = []
    item.scripts["p"].characters[0].reference_sheet.video_variants = []
    item.scripts["p"].characters[0].reference_sheet.selected_video_id = None
    item.scripts["p"].video_tasks = [_task("asset-video", frame_id="f", asset_id=None, video_url="video/frame.mp4")]
    item._commit_storage("projects.json")

    with pytest.raises(ValueError):
        item.delete_asset_video("p", "c", "character", "asset-video")

    committed = item.read_committed_script("p")
    assert [task.id for task in committed.video_tasks] == ["asset-video"]
    assert committed.frames[0].selected_video_id == "frame-task"


@pytest.mark.parametrize("error,status", [
    (ValueError("Asset video not found"), 404),
    (StorageError("Storage unavailable"), 503),
])
def test_delete_asset_video_api_error_contract(runtime, monkeypatch, error, status):
    item = runtime
    calls = []

    def fail(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(item, "delete_asset_video", fail)
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    with TestClient(app) as client:
        response = client.delete("/projects/p/assets/character/c/videos/missing")

    assert response.status_code == status, response.text
    assert calls == [("p", "c", "character", "missing")]
