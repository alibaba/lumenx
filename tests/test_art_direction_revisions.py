"""Project style commands must reject edits based on an old committed version."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import ArtDirection, Script, Series


@pytest.fixture
def make_runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    instances = []

    def make():
        runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
        instances.append(runtime)
        if not runtime.scripts:
            runtime.scripts["p"] = Script(
                id="p", title="project", original_text="script", created_at=1, updated_at=1,
                series_id="s", art_direction=ArtDirection(
                    selected_style_id="initial", style_config={"name": "Initial"},
                ),
            )
            runtime.series_store["s"] = Series(id="s", title="series", created_at=1, updated_at=1, episode_ids=["p"])
            runtime._commit_storage("projects.json", "series.json", "library_assets.json")
        return runtime

    yield make
    for runtime in instances:
        runtime.close()


def client_for(runtime):
    return TestClient(api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime))


def payload(operation, revision):
    result = {"expected_revision": revision}
    if operation == "save":
        result.update(selected_style_id="chosen", style_config={"name": "Chosen", "negative_prompt": ""})
    return result


def disk(runtime):
    return {name: (runtime.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}


@pytest.mark.parametrize("operation", ["save", "clear"])
@pytest.mark.parametrize("revision", ["missing", None, -1, True, "0", 0.0])
def test_style_http_requires_a_strict_nonnegative_revision(make_runtime, operation, revision):
    runtime = make_runtime()
    before = disk(runtime)
    request = payload(operation, revision)
    if revision == "missing":
        request.pop("expected_revision")
    with client_for(runtime) as client:
        response = client.post(f"/projects/p/art_direction/{operation}", json=request)
        assert response.status_code == 422, response.text
        runtime.assert_storage_available()
    assert disk(runtime) == before


@pytest.mark.parametrize("operation", ["save", "clear"])
def test_stale_style_command_preserves_newer_state_and_fresh_command_survives_reopen(make_runtime, operation):
    runtime = make_runtime()
    revision = runtime.read_committed_script("p").revision
    with client_for(runtime) as client:
        first = client.post("/projects/p/art_direction/save", json={
            **payload("save", revision), "selected_style_id": "newer", "style_config": {"name": "Newer"},
        })
        assert first.status_code == 200, first.text
        assert first.json()["revision"] == revision + 1
        before = disk(runtime)
        old = client.post(f"/projects/p/art_direction/{operation}", json=payload(operation, revision))
        assert old.status_code == 409, old.text
        assert old.json()["code"] == "storage_conflict"
        assert disk(runtime) == before
        assert runtime.read_committed_script("p").art_direction.selected_style_id == "newer"
        runtime.assert_storage_available()
        read = client.get("/projects/p")
        assert read.status_code == 200, read.text
        fresh = client.post(f"/projects/p/art_direction/{operation}", json=payload(operation, read.json()["revision"]))
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["revision"] == revision + 2
        saved = json.loads(disk(runtime)["projects.json"])["p"]
        assert fresh.json()["art_direction"] == saved["art_direction"]
    runtime.close()
    reopened = make_runtime().read_committed_script("p")
    assert reopened.model_dump(mode="json") == saved
    if operation == "clear":
        assert reopened.art_direction is None
    else:
        assert reopened.art_direction.selected_style_id == "chosen"


@pytest.mark.parametrize("operation", ["save", "clear"])
def test_style_revision_is_rechecked_after_waiting_for_the_writer_lock(make_runtime, operation):
    runtime = make_runtime()
    revision = runtime.read_committed_script("p").revision
    entered = Event()
    original_lock = runtime._save_lock

    class ObservedLock:
        def __enter__(self):
            if current_thread().name != "MainThread":
                entered.set()
            original_lock.acquire()

        def __exit__(self, *_):
            original_lock.release()

    with client_for(runtime) as client:
        runtime._save_lock = ObservedLock()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with original_lock:
                future = pool.submit(client.post, f"/projects/p/art_direction/{operation}", json=payload(operation, revision))
                assert entered.wait(2), "the command must wait for the writer lock"
                runtime.update_project_style("p", "new-look", "new prompt")
                before = disk(runtime)
            response = future.result(timeout=2)
        assert response.status_code == 409, response.text
        assert disk(runtime) == before
        runtime.assert_storage_available()
