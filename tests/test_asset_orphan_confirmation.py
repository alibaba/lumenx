"""Legacy asset generation has no durable receipt from which to infer its outcome."""

from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, GenerationStatus, Prop, Scene, Series, VideoTask
from src.utils.json_storage import StorageError
from tests import test_asset_image_writeback as image_tests

runtime = image_tests.runtime
FIELDS = {"character": "characters", "scene": "scenes", "prop": "props"}
FILES = ("projects.json", "series.json", "library_assets.json")


def configure_owner(item, source):
    item.scripts["p"].props = [Prop.model_validate({"id": "prop", "name": "Lamp", "description": "old lamp", "image_url": "uploads/old-a.png"})]
    if source == "project":
        owner = item.scripts["p"]
    elif source == "global":
        owner = item.library_store
    else:
        # Keep the standalone root on the same legacy route as the fixture project.
        owner = Series.model_validate({
            "id": "series", "title": "Series", "created_at": 1, "updated_at": 1,
            "model_settings": item.scripts["p"].model_settings.model_copy(deep=True),
        })
        item.series_store["series"] = owner
        if source == "episode_series":
            item.scripts["p"].series_id = "series"
            owner.episode_ids = ["p"]
    if source != "project":
        for field in FIELDS.values():
            setattr(owner, field, getattr(item.scripts["p"], field))
            setattr(item.scripts["p"], field, [])
    if source == "standalone_series":
        item.scripts.clear()
    item._commit_storage(*FILES)
    return owner


def owner_at(item, source):
    if source == "project":
        return item.scripts["p"]
    if source == "global":
        return item.library_store
    return item.series_store["series"]


def restart(item):
    item.close()
    # Reuse the fixture transport so even accidental startup generation is counted.
    with patch.object(module, "AssetGenerator", return_value=item.asset_generator):
        return module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock(), recover_orphan_tasks=True)


def saved_bytes(item):
    return {name: (item.data_dir / name).read_bytes() for name in FILES}


@pytest.mark.parametrize("source", ["project", "episode_series", "global", "standalone_series"])
@pytest.mark.parametrize("kind", FIELDS)
def test_real_image_admission_restarts_as_unknown_in_actual_owner(runtime, source, kind):
    item, state = runtime
    owner = configure_owner(item, source)
    target = getattr(owner, FIELDS[kind])[0]
    if source == "standalone_series":
        _, task_id = item.generate_series_asset("series", target.id, kind)
    else:
        _, task_id = item.create_asset_generation_task("p", target.id, kind)
    before = target.model_dump(mode="json")
    assert before["status"] == "processing"
    paid_file = item.data_dir / "assets" / "unassociated-paid.png"
    paid_file.parent.mkdir(parents=True, exist_ok=True)
    paid_file.write_bytes(b"unassociated paid output")

    reopened = restart(item)
    try:
        actual = getattr(owner_at(reopened, source), FIELDS[kind])[0].model_dump(mode="json")
        assert actual == {**before, "status": "needs_confirmation"}
        assert reopened.get_asset_generation_task_status(task_id) is None
        reopened.process_asset_generation_task(task_id)
        assert reopened.asset_generation_tasks == {}
        assert reopened.video_generation_tasks == {}
        assert not state.calls
        assert paid_file.read_bytes() == b"unassociated paid output"
        if source in {"episode_series", "global"}:
            assert not getattr(reopened.scripts["p"], FIELDS[kind])
        if source == "standalone_series":
            assert not reopened.scripts
        original = saved_bytes(reopened)
        reopened._recover_orphan_tasks()
        assert saved_bytes(reopened) == original
    finally:
        reopened.close()


@pytest.mark.parametrize("kind", FIELDS)
def test_unknown_status_is_asset_only_and_round_trips(kind):
    model = {"character": Character, "scene": Scene, "prop": Prop}[kind]
    asset = model(id="asset", name="Asset", description="Description", status="needs_confirmation")
    assert model.model_validate_json(asset.model_dump_json()).status == "needs_confirmation"
    assert {value.value for value in GenerationStatus} == {"pending", "processing", "completed", "failed"}


def test_recovery_commits_all_actual_containers_once_and_preserves_other_states(runtime, monkeypatch):
    item, _ = runtime
    item.series_store["series"] = Series.model_validate({"id": "series", "title": "Series", "created_at": 1, "updated_at": 1})
    item.scripts["p"].series_id = "series"
    for owner in (item.scripts["p"], item.series_store["series"], item.library_store):
        owner.scenes = [Scene.model_validate({"id": status, "name": status, "description": "keep", "status": status}) for status in GenerationStatus]
        owner.scenes.append(Scene.model_validate({"id": "same-id", "name": "keep", "description": "keep", "status": "processing"}))
    item._commit_storage(*FILES)
    before = deepcopy(item._committed)
    commit = item._storage.commit
    writes = []
    monkeypatch.setattr(item._storage, "commit", lambda payloads: (writes.append(set(payloads)), commit(payloads))[1])

    item._recover_orphan_tasks()

    assert writes == [set(FILES)]
    for name, before_roots in before.items():
        actual_roots = item._committed[name]
        pairs = [(before_roots, actual_roots)] if name == "library_assets.json" else [(root, actual_roots[key]) for key, root in before_roots.items()]
        for previous, actual in pairs:
            expected = deepcopy(previous)
            expected["revision"] += 1
            for scene in expected["scenes"]:
                if scene["status"] == "processing":
                    scene["status"] = "needs_confirmation"
            assert actual == expected
    persisted = saved_bytes(item)
    item._recover_orphan_tasks()
    assert saved_bytes(item) == persisted and len(writes) == 1


def test_series_only_recovery_does_not_write_unrelated_aggregates(runtime, monkeypatch):
    item, _ = runtime
    configure_owner(item, "standalone_series").scenes[0].status = GenerationStatus.PROCESSING
    item._commit_storage(*FILES)
    commit = item._storage.commit
    writes = []
    monkeypatch.setattr(item._storage, "commit", lambda payloads: (writes.append(set(payloads)), commit(payloads))[1])
    item._recover_orphan_tasks()
    assert writes == [{"series.json"}]


def test_motion_admission_restarts_as_neutral_unknown_without_changing_video_records(runtime):
    item, state = runtime
    _, task_id = item.create_motion_ref_task("p", "s", "scene")
    video = VideoTask.model_validate({"id": "finished", "project_id": "p", "image_url": "uploads/old-a.png",
                                     "prompt": "keep", "status": "completed", "video_url": "video/keep.mp4"})
    item.scripts["p"].scenes[0].video_assets = [video]
    item._commit_storage("projects.json")
    before = item.scripts["p"].scenes[0].model_dump(mode="json")
    reopened = restart(item)
    try:
        assert reopened.scripts["p"].scenes[0].model_dump(mode="json") == {**before, "status": "needs_confirmation"}
        assert reopened.get_asset_generation_task_status(task_id) is None
        assert not state.calls
    finally:
        reopened.close()


@pytest.mark.parametrize("operation", ["image", "motion"])
def test_recovery_does_not_reclassify_a_live_owned_generation(runtime, operation):
    item, _ = runtime
    if operation == "image":
        item.create_asset_generation_task("p", "s", "scene")
    else:
        item.create_motion_ref_task("p", "s", "scene")
    before = saved_bytes(item)
    item._recover_orphan_tasks()
    assert saved_bytes(item) == before
    assert item.scripts["p"].scenes[0].status == GenerationStatus.PROCESSING


def test_recovery_save_failure_rolls_back_and_closes_runtime(runtime, monkeypatch):
    item, _ = runtime
    item.scripts["p"].scenes[0].status = GenerationStatus.PROCESSING
    item._commit_storage("projects.json")
    before = saved_bytes(item)
    failure = StorageError("synthetic recovery save failure")
    monkeypatch.setattr(item._storage, "commit", Mock(side_effect=failure))
    with pytest.raises(StorageError) as raised:
        item._recover_orphan_tasks()
    assert raised.value is failure
    assert saved_bytes(item) == before
    assert item.scripts["p"].scenes[0].status == GenerationStatus.PROCESSING
    with pytest.raises(StorageError):
        item.assert_storage_available()


def test_legacy_task_404_does_not_forge_completion_and_project_read_exposes_unknown(runtime, monkeypatch):
    item, _ = runtime
    _, task_id = item.create_asset_generation_task("p", "s", "scene")
    reopened = restart(item)
    try:
        monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", lambda **kwargs: Mock(is_configured=False))
        app = api.create_app(data_dir=item.data_dir, user_data_dir=item.data_dir.parent / "settings",
                             load_config=False, pipeline_instance=reopened)
        with TestClient(app) as client:
            assert client.get(f"/tasks/{task_id}").status_code == 404
            response = client.get("/projects/p")
            assert response.status_code == 200
            assert response.json()["scenes"][0]["status"] == "needs_confirmation"
    finally:
        reopened.close()


def test_existing_confirmation_is_unchanged_on_another_real_restart(runtime):
    item, _ = runtime
    item.scripts["p"].scenes[0] = Scene.model_validate({"id": "s", "name": "Room", "description": "keep", "status": "needs_confirmation"})
    item._commit_storage("projects.json")
    before = saved_bytes(item)
    reopened = restart(item)
    try:
        assert saved_bytes(reopened) == before
    finally:
        reopened.close()


def test_late_old_runtime_keeps_paid_file_without_publishing_after_restart(runtime):
    item, state = runtime
    restarted = []
    before = item.scripts["p"].scenes[0].model_dump(mode="json")

    def interrupt_after_provider(call):
        restarted.append(restart(item))

    state.on_generate = interrupt_after_provider
    try:
        with pytest.raises(StorageError):
            item.generate_asset("p", "s", "scene")
        assert len(state.calls) == 1
        reopened = restarted[0]
        assert reopened.scripts["p"].scenes[0].model_dump(mode="json") == {**before, "status": "needs_confirmation"}
        image_tests.assert_paid_files(state)
    finally:
        for reopened in restarted:
            reopened.close()
