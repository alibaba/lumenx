"""Motion-reference tasks use accepted inputs and publish by short commits."""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    GenerationStatus,
    ImageVariant,
    Scene,
    Script,
    Series,
    VideoVariant,
)
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        characters=[
            Character(
                id="c",
                name="Hero",
                description="red coat",
                full_body_image_url="uploads/hero.png",
            )
        ],
        scenes=[Scene(id="s", name="Room", description="quiet", image_url="uploads/room.png")],
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    state = SimpleNamespace(calls=[], hook=None, results=None)

    def fake_i2v(**kwargs):
        state.calls.append(dict(kwargs))
        if state.hook:
            state.hook(state.calls[-1])
        if state.results:
            result = state.results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return {"video_url": f"video/{len(state.calls)}.mp4"}

    monkeypatch.setattr(item.video_generator, "generate_i2v", fake_i2v)
    yield item, state
    item.close()


def persisted(item, filename="projects.json"):
    return json.loads((item.data_dir / filename).read_text(encoding="utf-8"))


def create_motion_task(item, **kwargs):
    return item.create_motion_ref_task("p", "s", "scene", **kwargs)


def process_motion(item, task_id):
    try:
        item.process_motion_ref_task("p", task_id)
    except StorageConflictError as error:
        return error
    return None


def test_motion_task_admission_persists_before_exposing_pending(runtime, monkeypatch):
    item, state = runtime
    visible_during_commit = []
    commit = item._storage.commit

    def observe(payloads):
        visible_during_commit.append(dict(item.video_generation_tasks))
        return commit(payloads)

    monkeypatch.setattr(item._storage, "commit", observe)
    snapshot, task_id = create_motion_task(item)
    assert visible_during_commit
    assert all(not tasks for tasks in visible_during_commit)
    assert item.get_asset_generation_task_status(task_id)["status"] == "pending"
    assert persisted(item)["p"]["scenes"][0]["status"] == GenerationStatus.PROCESSING.value
    snapshot.title = "caller mutation"
    assert item.scripts["p"].title == "project"
    assert state.calls == []


def test_motion_task_admission_save_failure_leaves_no_visible_task(runtime, monkeypatch):
    item, state = runtime
    before = persisted(item)
    failure = StorageError("synthetic admission failure")

    def fail(payloads):
        raise failure

    monkeypatch.setattr(item._storage, "commit", fail)
    with pytest.raises(StorageError) as raised:
        create_motion_task(item)
    assert raised.value is failure
    assert item.video_generation_tasks == {}
    assert persisted(item) == before
    assert state.calls == []


def test_motion_task_concurrent_claim_and_completed_reentry_do_not_pay_twice(runtime):
    item, state = runtime
    _, task_id = create_motion_task(item)
    entered, release = Event(), Event()

    def pause_first(call):
        if len(state.calls) == 1:
            entered.set()
            assert release.wait(3), "test did not release first provider"

    state.hook = pause_first
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(process_motion, item, task_id)
        assert entered.wait(2), "worker did not reach fake provider"
        second = pool.submit(process_motion, item, task_id)
        try:
            second.result(timeout=1)
        except TimeoutError:
            pass
        during_count = len(state.calls)
        during_status = item.get_asset_generation_task_status(task_id)["status"]
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    first_status = item.get_asset_generation_task_status(task_id)
    process_motion(item, task_id)
    assert (during_count, len(state.calls)) == (1, 1)
    assert during_status == "processing"
    assert first_status["status"] == "completed"
    assert item.get_asset_generation_task_status(task_id) == first_status


def test_motion_task_wrong_project_failure_releases_target_for_next_task(runtime):
    item, state = runtime
    _, task_id = create_motion_task(item)

    item.process_motion_ref_task("wrong-project", task_id)

    status = item.get_asset_generation_task_status(task_id)
    assert status["status"] == "failed"
    assert state.calls == []
    create_motion_task(item)


def test_motion_ref_saves_each_result_before_next_provider_without_unrelated_leak(runtime):
    item, state = runtime
    _, task_id = create_motion_task(item, batch_size=2)
    second_call_entered, release_second = Event(), Event()

    def pause_second_call(call):
        if len(state.calls) == 2:
            second_call_entered.set()
            assert release_second.wait(3), "test did not release second provider"

    state.hook = pause_second_call
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(process_motion, item, task_id)
        assert second_call_entered.wait(2), "worker did not enter second provider"
        before_save = persisted(item)["p"]["scenes"][0]["video_assets"]
        item.update_project_style("p", "user-save")
        after_save = persisted(item)["p"]["scenes"][0]["video_assets"]
        release_second.set()
        worker.result(timeout=2)
    assert len(before_save) == 1
    assert after_save == before_save
    assert len(persisted(item)["p"]["scenes"][0]["video_assets"]) == 2


@pytest.mark.parametrize("owner", ["script", "series", "global"])
def test_motion_ref_writes_actual_owner_and_returns_snapshot(runtime, owner):
    item, state = runtime
    scene = item.scripts["p"].scenes[0]
    if owner != "script":
        item.scripts["p"].scenes = []
        if owner == "series":
            item.scripts["p"].series_id = "series"
            item.series_store["series"] = Series(
                id="series", title="series", created_at=1, updated_at=1, scenes=[scene]
            )
        else:
            item.library_store.scenes.append(scene)
        item._commit_storage("projects.json", "series.json", "library_assets.json")
    result = item.generate_motion_ref("p", "s", "scene")
    result.title = "caller mutation"
    assert item.scripts["p"].title == "project"
    assert len(state.calls) == 1
    if owner == "script":
        assert len(persisted(item)["p"]["scenes"][0]["video_assets"]) == 1
    elif owner == "series":
        assert persisted(item)["p"]["scenes"] == []
        assert len(persisted(item, "series.json")["series"]["scenes"][0]["video_assets"]) == 1
    else:
        assert persisted(item)["p"]["scenes"] == []
        assert len(persisted(item, "library_assets.json")["scenes"][0]["video_assets"]) == 1


@pytest.mark.parametrize("mutation", ["deleted", "replaced"])
def test_motion_task_late_missing_or_replaced_target_fails_without_provider(runtime, mutation):
    item, state = runtime
    _, task_id = create_motion_task(item)
    with item._save_lock:
        if mutation == "deleted":
            item.scripts["p"].scenes = []
        else:
            item.scripts["p"].scenes[0] = Scene(id="s", name="New", description="replacement", image_url="uploads/new.png")
        item._save_data()
    process_motion(item, task_id)
    assert item.get_asset_generation_task_status(task_id)["status"] == "failed"
    assert state.calls == []


def test_motion_storage_failure_preserves_error_and_never_retries_paid_call(runtime, monkeypatch):
    item, state = runtime
    _, task_id = create_motion_task(item)
    failure = StorageError("synthetic result save failure", commit_uncertain=True)
    commit = item._storage.commit

    def fail_on_result(payloads):
        if payloads.get("projects.json", {}).get("p", {}).get("scenes", [{}])[0].get("video_assets"):
            raise failure
        return commit(payloads)

    monkeypatch.setattr(item._storage, "commit", fail_on_result)
    with pytest.raises(StorageError) as raised:
        item.process_motion_ref_task("p", task_id)
    assert raised.value is failure
    assert item.get_asset_generation_task_status(task_id)["status"] == "failed"
    item.process_motion_ref_task("p", task_id)
    assert len(state.calls) == 1


def test_motion_ref_partial_provider_failures_save_successes_and_complete(runtime):
    item, state = runtime
    state.results = [{}, RuntimeError("provider rejected"), {"video_url": "video/ok.mp4"}]
    _, task_id = create_motion_task(item, batch_size=3)
    item.process_motion_ref_task("p", task_id)
    assert item.get_asset_generation_task_status(task_id)["status"] == "completed"
    assert len(state.calls) == 3
    videos = persisted(item)["p"]["scenes"][0]["video_assets"]
    assert len(videos) == 1 and videos[0]["video_url"] == "video/ok.mp4"
    assert persisted(item)["p"]["scenes"][0]["status"] == GenerationStatus.COMPLETED.value


def test_motion_ref_all_empty_or_failed_results_is_not_completed(runtime):
    item, state = runtime
    state.results = [{}, RuntimeError("provider rejected")]
    _, task_id = create_motion_task(item, batch_size=2)
    item.process_motion_ref_task("p", task_id)
    assert item.get_asset_generation_task_status(task_id)["status"] == "failed"
    assert len(state.calls) == 2
    scene = persisted(item)["p"]["scenes"][0]
    assert scene["video_assets"] == []
    assert scene["status"] == GenerationStatus.FAILED.value
    item.process_motion_ref_task("p", task_id)
    assert len(state.calls) == 2


@pytest.mark.parametrize("url", ["   ", "video/\ud800.mp4"])
def test_motion_ref_invalid_provider_url_never_reaches_live_storage(runtime, url):
    item, state = runtime
    state.results = [{"video_url": url}]
    _, task_id = create_motion_task(item)
    item.process_motion_ref_task("p", task_id)
    assert item.get_asset_generation_task_status(task_id)["status"] == "failed"
    scene = item.read_committed_script("p").scenes[0]
    assert scene.video_assets == []
    assert scene.status == GenerationStatus.FAILED
    item.update_project_style("p", "storage remains healthy")


def test_motion_ref_preserves_existing_selected_character_video(runtime):
    item, state = runtime
    character = item.scripts["p"].characters[0]
    character.full_body = AssetUnit(
        selected_image_id="source",
        image_variants=[ImageVariant(id="source", url="uploads/hero.png")],
        selected_video_id="old-video",
        video_variants=[VideoVariant(id="old-video", url="video/old.mp4")],
    )
    item._save_data()
    item.generate_motion_ref("p", "c", "full_body")
    saved = persisted(item)["p"]["characters"][0]["full_body"]
    assert len(state.calls) == 1
    assert saved["selected_video_id"] == "old-video"
    assert [video["url"] for video in saved["video_variants"]] == ["video/old.mp4", "video/1.mp4"]


def test_closed_runtime_rejects_motion_admission_without_task_or_provider(runtime):
    item, state = runtime
    item.close()
    with pytest.raises(StorageError):
        create_motion_task(item)
    assert item.video_generation_tasks == {}
    assert state.calls == []


@pytest.mark.parametrize("payload", [
    {"batch_size": True},
    {"batch_size": "2"},
    {"batch_size": 0},
    {"duration": "5"},
    {"duration": 0},
])
def test_motion_ref_http_rejects_invalid_scalars_without_task_or_provider(runtime, payload):
    item, state = runtime
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    with TestClient(app) as client:
        response = client.post(
            "/projects/p/assets/generate_motion_ref",
            json={"asset_id": "s", "asset_type": "scene"} | payload,
        )
    assert response.status_code == 422, response.text
    assert item.video_generation_tasks == {}
    assert state.calls == []


@pytest.mark.parametrize("error,status", [
    (module.CommandValidationError("invalid motion input"), 422),
    (module.ProjectContextError("project missing", not_found=True), 404),
    (module.ProjectContextError("asset changed"), 422),
    (StorageConflictError("already running"), 409),
    (StorageError("storage unavailable"), 503),
])
def test_motion_ref_http_error_contract(runtime, monkeypatch, error, status):
    item, state = runtime
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    monkeypatch.setattr(item, "create_motion_ref_task", fail)
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    with TestClient(app) as client:
        response = client.post(
            "/projects/p/assets/generate_motion_ref",
            json={"asset_id": "s", "asset_type": "scene"},
        )
    assert response.status_code == status, response.text
    assert len(calls) == 1
    assert item.video_generation_tasks == {}
    assert state.calls == []
