"""Shared series/library commands return committed detached responses."""

import json
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import Character, Scene, Script, Series
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(id="p", title="project", original_text="source", created_at=1, updated_at=1)
    item.scripts["q"] = Script(id="q", title="other", original_text="source", created_at=1, updated_at=1)
    item.series_store["s"] = Series(
        id="s",
        title="series",
        created_at=1,
        updated_at=1,
        episode_ids=["p"],
        characters=[Character(id="sc", name="series char", description="old", image_url="old.png")],
        scenes=[Scene(id="ss", name="series scene", description="old", image_url="old.png")],
    )
    item.series_store["source"] = Series(
        id="source",
        title="source",
        created_at=1,
        updated_at=1,
        characters=[Character(id="import-me", name="source char", description="old")],
    )
    item.library_store.characters = [Character(id="gc", name="global char", description="old")]
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    yield item
    item.close()


def disk_json(runtime, name):
    return json.loads((runtime.data_dir / name).read_text())


def mutate_response(value):
    if hasattr(value, "title"):
        value.title = "response-only"
    if hasattr(value, "name"):
        value.name = "response-only"
    if hasattr(value, "characters") and value.characters:
        value.characters[0].name = "response-only"


SERIES_COMMANDS = [
    "create_series",
    "add_episode_to_series",
    "remove_episode_from_series",
    "toggle_series_asset_lock",
    "toggle_series_asset_starred",
    "update_series_asset_image",
    "update_series_asset_attributes",
    "import_assets_from_series",
]

LIBRARY_COMMANDS = [
    "create_library_asset",
    "update_library_asset",
    "promote_asset_to_library",
    "fork_library_asset_to_project",
]


def run_series(runtime, command):
    if command == "create_series":
        return runtime.create_series("new series")
    if command == "add_episode_to_series":
        return runtime.add_episode_to_series("s", "q")
    if command == "remove_episode_from_series":
        return runtime.remove_episode_from_series("s", "p")
    if command == "toggle_series_asset_lock":
        return runtime.toggle_series_asset_lock("s", "sc", "character")
    if command == "toggle_series_asset_starred":
        return runtime.toggle_series_asset_starred("s", "sc", "character")
    if command == "update_series_asset_image":
        return runtime.update_series_asset_image("s", "sc", "character", "new.png")
    if command == "update_series_asset_attributes":
        return runtime.update_series_asset_attributes("s", "sc", "character", {"name": "renamed"})
    if command == "import_assets_from_series":
        return runtime.import_assets_from_series("s", "source", ["import-me"])[0]
    raise AssertionError(command)


def run_library(runtime, command):
    if command == "create_library_asset":
        return runtime.create_library_asset("character", {"name": "new global"})
    if command == "update_library_asset":
        return runtime.update_library_asset("character", "gc", {"name": "renamed global"})
    if command == "promote_asset_to_library":
        return runtime.promote_asset_to_library("series", "s", "character", "sc")
    if command == "fork_library_asset_to_project":
        return runtime.fork_library_asset_to_project("p", "character", "gc")
    raise AssertionError(command)


@pytest.mark.parametrize("command", SERIES_COMMANDS)
def test_series_command_response_is_committed_and_detached(runtime, command):
    result = run_series(runtime, command)
    series_id = result.id
    saved = disk_json(runtime, "series.json")[series_id]
    assert result.model_dump(mode="json") == saved
    mutate_response(result)
    assert runtime.series_store[series_id].model_dump(mode="json") == saved


@pytest.mark.parametrize("command", LIBRARY_COMMANDS)
def test_library_command_response_is_committed_and_detached(runtime, command):
    result = run_library(runtime, command)
    if command == "fork_library_asset_to_project":
        saved = disk_json(runtime, "projects.json")["p"]["characters"][-1]
        live = runtime.scripts["p"].characters[-1]
    else:
        saved = next(item for item in disk_json(runtime, "library_assets.json")["characters"] if item["id"] == result.id)
        live = next(item for item in runtime.library_store.characters if item.id == result.id)
    assert result.model_dump(mode="json") == saved
    mutate_response(result)
    assert live.model_dump(mode="json") == saved


def test_library_source_key_noop_returns_committed_detached_existing_asset(runtime):
    first = runtime.create_library_asset("character", {"name": "saved"}, source_key="same")
    saved = disk_json(runtime, "library_assets.json")["characters"][-1]
    runtime.library_store.characters[-1].name = "dirty live not committed"

    again = runtime.create_library_asset("scene", {"name": "ignored"}, source_key="same")

    assert again.id == first.id
    assert again.model_dump(mode="json") == saved
    mutate_response(again)
    assert runtime.library_store.characters[-1].name == "dirty live not committed"


@pytest.mark.parametrize("command,runner", [
    ("create_series", run_series),
    ("add_episode_to_series", run_series),
    ("toggle_series_asset_lock", run_series),
    ("import_assets_from_series", run_series),
    ("create_library_asset", run_library),
    ("update_library_asset", run_library),
    ("promote_asset_to_library", run_library),
    ("fork_library_asset_to_project", run_library),
])
def test_shared_command_save_failure_restores_disk_and_runtime(runtime, monkeypatch, command, runner):
    before_disk = {name: (runtime.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}
    before_runtime = {
        "projects.json": {key: value.model_dump(mode="json") for key, value in runtime.scripts.items()},
        "series.json": {key: value.model_dump(mode="json") for key, value in runtime.series_store.items()},
        "library_assets.json": runtime.library_store.model_dump(mode="json"),
    }
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("synthetic disk full")))

    with pytest.raises(StorageError):
        runner(runtime, command)

    assert {name: (runtime.data_dir / name).read_bytes() for name in before_disk} == before_disk
    assert {key: value.model_dump(mode="json") for key, value in runtime.scripts.items()} == before_runtime["projects.json"]
    assert {key: value.model_dump(mode="json") for key, value in runtime.series_store.items()} == before_runtime["series.json"]
    assert runtime.library_store.model_dump(mode="json") == before_runtime["library_assets.json"]
    with pytest.raises(StorageError):
        runtime.assert_storage_available()
