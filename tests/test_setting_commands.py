from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, Script, Series
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(id="p", title="p", original_text="text", created_at=1, updated_at=1,
                               characters=[Character(id="c", name="c", description="c")], series_id="s")
    item.series_store["s"] = Series(id="s", title="s", created_at=1, updated_at=1, episode_ids=["p"])
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    yield item
    item.close()


@pytest.mark.parametrize("command", ["style", "art", "model", "voice"])
def test_closed_setting_command_rejects_before_mutation(runtime, command):
    before = runtime.scripts["p"].model_dump(mode="json")
    runtime.close()
    calls = {
        "style": lambda: runtime.update_project_style("p", "new-style", "new-prompt"),
        "art": lambda: runtime.save_art_direction("p", "new-style", {}, expected_revision=runtime.scripts["p"].revision),
        "model": lambda: runtime.update_model_settings("p", t2i_model="new-model"),
        "voice": lambda: runtime.bind_voice("p", "c", "new-voice", "new-name"),
    }
    with pytest.raises(StorageError):
        calls[command]()
    assert runtime.scripts["p"].model_dump(mode="json") == before


@pytest.mark.parametrize("source", ["series", "global"])
@pytest.mark.parametrize("params", [False, True])
def test_shared_character_voice_edit_uses_the_actual_owner(runtime, source, params):
    characters = runtime.scripts["p"].characters
    runtime.scripts["p"].characters = []
    owner = runtime.series_store["s"] if source == "series" else runtime.library_store
    owner.characters = characters
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        if params:
            response = client.put("/projects/p/characters/c/voice_params", json={"speed": 1.2, "pitch": 0.9, "volume": 0})
        else:
            response = client.post("/projects/p/characters/c/voice", json={"voice_id": "chosen", "voice_name": "Chosen"})
        assert response.status_code == 200, response.text
        assert runtime.scripts["p"].characters == []
        if params:
            assert owner.characters[0].voice_volume == 0 and owner.characters[0].voice_speed == 1.2
        else:
            assert owner.characters[0].voice_id == "chosen"
        returned = response.json()["characters"][0]
        assert returned["id"] == "c" and returned["source"] == source


def disk(runtime):
    return {name: (runtime.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}


def call(runtime, name):
    return {
        "style": lambda: runtime.update_project_style("p", "new", "style prompt"),
        "art": lambda: runtime.save_art_direction("p", "new", {"positive": "paint"}, expected_revision=runtime.scripts["p"].revision),
        "clear_art": lambda: runtime.clear_art_direction("p", expected_revision=runtime.scripts["p"].revision),
        "model": lambda: runtime.update_model_settings("p", t2i_model="new-model"),
        "voice": lambda: runtime.bind_voice("p", "c", "new-voice", "name"),
        "voice_params": lambda: runtime.update_voice_params("p", "c", speed=1.2, pitch=0.9, volume=0),
        "prompt": lambda: runtime.update_prompt_config("p", {"storyboard_polish": "new prompt"}),
        "character": lambda: runtime.create_series_asset("s", "character", name="new", image_url="uploads/ref.png", voice_id="chosen"),
        "scene": lambda: runtime.create_series_asset("s", "scene", name="new", image_url="uploads/ref.png"),
        "prop": lambda: runtime.create_series_asset("s", "prop", name="new", image_url="uploads/ref.png"),
    }[name]()


COMMANDS = ["style", "art", "clear_art", "model", "voice", "voice_params", "prompt", "character", "scene", "prop"]


@pytest.mark.parametrize("command", COMMANDS)
def test_failed_setting_write_rolls_back_every_aggregate(runtime, monkeypatch, command):
    before = disk(runtime)
    committed = runtime.read_committed_project_view("p")
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("synthetic disk full")))
    with pytest.raises(StorageError):
        call(runtime, command)
    assert disk(runtime) == before
    assert runtime.scripts["p"] == committed[0]
    assert runtime.series_store["s"] == committed[1]
    assert runtime.library_store == committed[2]
    with pytest.raises(StorageError):
        runtime.assert_storage_available()


@pytest.mark.parametrize("command", COMMANDS)
def test_setting_rechecks_lifecycle_after_waiting_for_command_lock(runtime, command):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    before = disk(runtime)
    script_before = runtime.scripts["p"].model_dump()
    series_before = runtime.series_store["s"].model_dump()
    entered = Event()
    original_lock = runtime._save_lock

    class ObserveLock:
        def __enter__(self):
            if current_thread().name != "MainThread":
                entered.set()
            original_lock.acquire()
        def __exit__(self, *_):
            original_lock.release()

    runtime._save_lock = ObserveLock()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with original_lock:
            future = pool.submit(call, runtime, command)
            assert entered.wait(2), "command must acquire the lifecycle lock before reading or mutating"
            runtime.close()
        with pytest.raises(StorageError):
            future.result(timeout=2)
    assert disk(runtime) == before
    assert runtime.scripts["p"].model_dump() == script_before
    assert runtime.series_store["s"].model_dump() == series_before


@pytest.mark.parametrize("command", COMMANDS)
def test_setting_response_is_detached_and_matches_saved_state(runtime, command):
    import json
    result = call(runtime, command)
    if command in {"character", "scene", "prop"}:
        field = {"character": "characters", "scene": "scenes", "prop": "props"}[command]
        saved = json.loads(disk(runtime)["series.json"])["s"][field][-1]
        assert result.model_dump(mode="json") == saved
        result.name = "local mutation"
        assert getattr(runtime.series_store["s"], field)[-1].name == "new"
    elif command == "prompt":
        assert result.model_dump(mode="json") == json.loads(disk(runtime)["projects.json"])["p"]
        result.prompt_config.storyboard_polish = "local mutation"
        assert runtime.scripts["p"].prompt_config.storyboard_polish == "new prompt"
    else:
        assert result.model_dump(mode="json") == json.loads(disk(runtime)["projects.json"])["p"]
        result.title = "local mutation"
        assert runtime.scripts["p"].title == "p"


@pytest.mark.parametrize("command", ["style", "art", "model", "voice", "voice_params", "prompt", "character"])
def test_invalid_setting_cannot_partly_mutate_or_poison_next_save(runtime, command):
    from pydantic import ValidationError
    before = disk(runtime)
    invalid = {
        "style": lambda: runtime.update_project_style("p", "valid", {"bad": "type"}),
        "art": lambda: runtime.save_art_direction("p", "valid", ["bad"], expected_revision=runtime.scripts["p"].revision),
        "model": lambda: runtime.update_model_settings("p", t2i_model="valid", i2v_model={"bad": "type"}),
        "voice": lambda: runtime.bind_voice("p", "c", "valid", {"bad": "type"}),
        "voice_params": lambda: runtime.update_voice_params("p", "c", speed=1.2, pitch="not-number", volume=0),
        "prompt": lambda: runtime.update_prompt_config("p", {"storyboard_polish": "valid", "video_polish": []}),
        "character": lambda: runtime.create_series_asset("s", "character", name="valid", voice_id=[]),
    }
    with pytest.raises((ValueError, ValidationError)):
        invalid[command]()
    assert disk(runtime) == before
    # A rejected partial patch must not leak into an unrelated later save.
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    assert disk(runtime) == before


@pytest.mark.parametrize("source", ["script", "series", "global"])
@pytest.mark.parametrize("params", [False, True])
def test_voice_edit_changes_only_selected_owner_and_keeps_priority(runtime, source, params):
    import json
    from copy import deepcopy
    original = runtime.scripts["p"].characters[0]
    runtime.series_store["s"].characters = [deepcopy(original)]
    runtime.library_store.characters = [deepcopy(original)]
    if source != "script":
        runtime.scripts["p"].characters = []
    if source == "global":
        runtime.series_store["s"].characters = []
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    before = disk(runtime)
    call(runtime, "voice_params" if params else "voice")
    after = disk(runtime)
    name = {"script": "projects.json", "series": "series.json", "global": "library_assets.json"}[source]
    assert before[name] != after[name]
    assert all(before[key] == after[key] for key in before if key != name)
    raw = json.loads(after[name])
    owner = raw["p"] if source == "script" else raw["s"] if source == "series" else raw
    character = owner["characters"][0]
    assert character["voice_volume"] == 0 if params else character["voice_id"] == "new-voice"


def test_model_omitted_null_empty_keep_existing_values(runtime):
    old = runtime.scripts["p"].model_settings.model_dump()
    result = runtime.update_model_settings("p", t2i_model=None, i2i_model="", r2v_model="updated")
    assert result.model_settings.t2i_model == old["t2i_model"]
    assert result.model_settings.i2i_model == old["i2i_model"]
    assert result.model_settings.r2v_model == "updated"


def test_prompt_http_replacement_preserves_polish_model(runtime):
    runtime.scripts["p"].prompt_config.video_polish = "old"
    runtime.scripts["p"].prompt_config.polish_model = "custom-polish"
    runtime._save_data()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.put("/projects/p/prompt_config", json={"storyboard_polish": "new"})
        assert response.status_code == 200, response.text
        value = response.json()["prompt_config"]
        assert value["storyboard_polish"] == "new" and value["video_polish"] == ""
        assert value["polish_model"] == "custom-polish"


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_series_create_http_retains_requested_shape_and_sheet(runtime, kind):
    import json
    field = {"character": "characters", "scene": "scenes", "prop": "props"}[kind]
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(f"/series/s/{field}", json={"name": "hero", "image_url": "uploads/ref.png", "persona": "group", "voice_id": "v"})
        assert response.status_code == 200, response.text
        value = response.json()
        stored = json.loads(disk(runtime)["series.json"])["s"][field][-1]
        assert value["id"] == stored["id"] and value["name"] == "hero"
        if kind == "character":
            sheet = stored["reference_sheet"]
            assert sheet["selected_image_id"] == sheet["image_variants"][0]["id"]
            assert sheet["image_variants"][0]["url"] == "uploads/ref.png"
            assert stored["persona"] == "group" and stored["voice_id"] == "v"
        else:
            assert stored["image_url"] == "uploads/ref.png"


def test_concurrent_setting_fields_survive_both_commits(runtime):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(runtime.update_model_settings, "p", t2i_model="first")
        two = pool.submit(runtime.update_model_settings, "p", i2v_model="second")
        one.result(timeout=2)
        two.result(timeout=2)
    result = runtime.read_committed_script("p")
    assert result.model_settings.t2i_model == "first" and result.model_settings.i2v_model == "second"


@pytest.mark.parametrize("method,url,payload", [
    ("put", "/projects/p/characters/c/voice_params", {"speed": "NaN", "pitch": 1, "volume": 50}),
    ("put", "/projects/p/characters/c/voice_params", {"speed": 1, "pitch": "Infinity", "volume": 50}),
    ("post", "/projects/p/art_direction/save", {"expected_revision": 0, "selected_style_id": "x", "style_config": {"nested": [float("nan")]}}),
    ("post", "/projects/p/art_direction/save", {"expected_revision": 0, "selected_style_id": "x", "style_config": {"nested": "\ud800"}}),
    ("put", "/projects/p/prompt_config", {"storyboard_polish": "\ud800"}),
    ("post", "/projects/p/model_settings", {"t2i_model": "\ud800"}),
    ("patch", "/projects/p/style", {"style_preset": "\ud800"}),
    ("post", "/projects/p/characters/c/voice", {"voice_id": "\ud800", "voice_name": "invalid"}),
    ("post", "/series/s/characters", {"name": "invalid", "description": "\ud800"}),
    ("post", "/series/s/scenes", {"name": "invalid", "image_url": "\ud800"}),
])
def test_unpersistable_http_setting_is_422_without_poisoning_storage(runtime, method, url, payload):
    import json
    before = disk(runtime)
    saved = runtime.scripts["p"].model_dump()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = getattr(client, method)(url, content=json.dumps(payload), headers={"content-type": "application/json"})
        assert response.status_code == 422, response.text
        assert disk(runtime) == before and runtime.scripts["p"].model_dump() == saved
        runtime.assert_storage_available()
        runtime.update_project_style("p", "valid later edit")
        assert runtime.read_committed_script("p").style_preset == "valid later edit"


def test_prompt_http_ack_keeps_config_and_revision_from_same_commit(runtime, monkeypatch):
    initial_revision = runtime.read_committed_script("p").revision
    update = runtime.update_prompt_config

    def update_then_later_write(script_id, prompts):
        result = update(script_id, prompts)
        runtime.update_project_style(script_id, "later", "later style")
        return result

    monkeypatch.setattr(runtime, "update_prompt_config", update_then_later_write)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.put("/projects/p/prompt_config", json={"storyboard_polish": "saved"})
        assert response.status_code == 200, response.text
        ack = response.json()
        assert ack["prompt_config"]["storyboard_polish"] == "saved"
        assert ack["revision"] == initial_revision + 1
        current = client.get("/projects/p/prompt_config").json()
        assert current["revision"] == initial_revision + 2
        assert current["prompt_config"] == ack["prompt_config"]


def test_prompt_read_revision_ignores_uncommitted_runtime_mutation(runtime):
    committed = runtime.read_committed_script("p")
    runtime.scripts["p"].revision += 20
    runtime.scripts["p"].prompt_config.storyboard_polish = "uncommitted"
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.get("/projects/p/prompt_config")
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == committed.revision
        assert response.json()["prompt_config"] == committed.prompt_config.model_dump()
