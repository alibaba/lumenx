"""F08: create one series episode and recover the same identity after lost ACKs."""

from concurrent.futures import ThreadPoolExecutor
import json
import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Scene, Script, StoryboardFrame
from src.utils.json_storage import CorruptStorageError, StorageError


@pytest.fixture
def make_runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    processor = Mock()
    processor.create_draft_script.side_effect = lambda title, text: Script(
        id=str(uuid.uuid4()), title=title, original_text=text, created_at=1, updated_at=1,
    )
    runtimes = []

    def make():
        runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=processor)
        runtimes.append(runtime)
        return runtime

    yield make
    for runtime in runtimes:
        runtime.close()


def client_for(runtime):
    return TestClient(api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime))


def new_series(runtime):
    return runtime.create_series("故事", workflow_mode="r2v", default_generation_mode="i2v")


def request_for(series, **updates):
    return {
        "client_request_id": "episode-create-1", "title": "第一集",
        "expected_revision": series.revision, **updates,
    }


def create_episode(client, series_id, request):
    response = client.post(f"/series/{series_id}/episode_requests", json=request)
    assert response.status_code == 200, response.text
    return response.json()


def test_new_episode_and_membership_are_one_committed_command(make_runtime, monkeypatch):
    runtime = make_runtime()
    series = new_series(runtime)
    commits = []
    original_commit = runtime._storage.commit

    def record(payloads):
        commits.append(set(payloads))
        original_commit(payloads)

    monkeypatch.setattr(runtime._storage, "commit", record)
    with client_for(runtime) as client:
        receipt = create_episode(client, series.id, request_for(series))
        project = client.get(f"/projects/{receipt['episode_id']}").json()
        saved_series = client.get(f"/series/{series.id}").json()

    assert commits == [{"projects.json", "series.json"}]
    assert saved_series["episode_ids"] == [receipt["episode_id"]]
    assert project["series_id"] == series.id
    assert project["episode_number"] == receipt["episode_number"] == 1
    assert project["title"] == receipt["title"] == "第一集"
    assert project["original_text"] == ""
    assert project["workflow_mode"] == "r2v"
    assert project["default_generation_mode"] == "i2v"
    assert receipt["series_id"] == series.id
    assert receipt["client_request_id"] == "episode-create-1"
    assert len(receipt["request_fingerprint"]) == 64
    runtime.script_processor.parse_novel.assert_not_called()


def test_next_episode_uses_max_number_instead_of_list_length(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    previous = runtime.create_project("第八集", "", skip_analysis=True)
    series = runtime.add_episode_to_series(series.id, previous.id, episode_number=8)
    with client_for(runtime) as client:
        receipt = create_episode(client, series.id, request_for(series))
    assert receipt["episode_number"] == 9


def test_same_identity_replays_before_revision_check_without_recreating(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    request = request_for(series)
    with client_for(runtime) as client:
        first = create_episode(client, series.id, request)
        replay = create_episode(client, series.id, request)
    assert replay == first
    assert len(runtime.scripts) == 1
    runtime.script_processor.create_draft_script.assert_called_once_with("第一集", "")


def test_same_identity_with_changed_intent_is_a_conflict(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    request = request_for(series)
    with client_for(runtime) as client:
        create_episode(client, series.id, request)
        response = client.post(f"/series/{series.id}/episode_requests", json={**request, "title": "另一集"})
    assert response.status_code == 409, response.text
    assert len(runtime.scripts) == 1


def test_new_identity_with_stale_series_revision_creates_nothing(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    runtime.update_series(series.id, {"title": "已更新"})
    with client_for(runtime) as client:
        response = client.post(f"/series/{series.id}/episode_requests", json=request_for(series))
    assert response.status_code == 409, response.text
    assert runtime.scripts == {}
    runtime.script_processor.create_draft_script.assert_not_called()


def test_lost_ack_can_be_read_after_restart_without_writes(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    request = request_for(series)
    with client_for(runtime) as client:
        first = create_episode(client, series.id, request)
    runtime.close()
    restored = make_runtime()
    before = {path.name: path.read_bytes() for path in restored.data_dir.glob("*.json")}
    with client_for(restored) as client:
        recovered = client.get(f"/series/{series.id}/episode_requests/{request['client_request_id']}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == first
    assert {path.name: path.read_bytes() for path in restored.data_dir.glob("*.json")} == before
    restored.script_processor.create_draft_script.assert_called_once()


@pytest.mark.parametrize("commit_visible", [False, True])
def test_failed_commit_has_no_orphan_and_same_identity_is_recoverable(make_runtime, monkeypatch, commit_visible):
    runtime = make_runtime()
    series = new_series(runtime)
    request = request_for(series)
    original_commit = runtime._storage.commit

    def fail(payloads):
        if commit_visible:
            original_commit(payloads)
        raise StorageError("injected episode creation failure", commit_uncertain=commit_visible)

    monkeypatch.setattr(runtime._storage, "commit", fail)
    with client_for(runtime) as client:
        response = client.post(f"/series/{series.id}/episode_requests", json=request)
        assert response.status_code == 503, response.text
        assert runtime.scripts == {}
        assert runtime.series_store[series.id].episode_ids == []
    runtime.close()
    restored = make_runtime()
    with client_for(restored) as client:
        recovered = client.get(f"/series/{series.id}/episode_requests/{request['client_request_id']}")
        assert recovered.status_code == (200 if commit_visible else 404), recovered.text
        first = create_episode(client, series.id, request)
        replay = create_episode(client, series.id, request)
    assert replay == first
    assert len(restored.scripts) == 1
    assert restored.series_store[series.id].episode_ids == [first["episode_id"]]


def test_parallel_replay_commits_one_episode(make_runtime, monkeypatch):
    runtime = make_runtime()
    series = new_series(runtime)
    request = request_for(series)
    commits = []
    original_commit = runtime._storage.commit

    def record(payloads):
        commits.append(set(payloads))
        original_commit(payloads)

    monkeypatch.setattr(runtime._storage, "commit", record)
    with client_for(runtime) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: create_episode(client, series.id, request), range(2)))
    assert receipts[0] == receipts[1]
    assert commits == [{"projects.json", "series.json"}]


@pytest.mark.parametrize("remove_project", [False, True])
def test_replay_does_not_reattach_or_recreate_a_removed_episode(make_runtime, remove_project):
    runtime = make_runtime()
    series = new_series(runtime)
    request = request_for(series)
    with client_for(runtime) as client:
        first = create_episode(client, series.id, request)
        if remove_project:
            runtime.delete_project(first["episode_id"])
        else:
            runtime.remove_episode_from_series(series.id, first["episode_id"])
        replay = create_episode(client, series.id, request)
    assert replay == first
    assert runtime.series_store[series.id].episode_ids == []
    assert len(runtime.scripts) == (0 if remove_project else 1)
    runtime.script_processor.create_draft_script.assert_called_once()


@pytest.mark.parametrize("updates", [
    {"title": "  "}, {"client_request_id": ""}, {"expected_revision": True}, {"episode_number": 99},
])
def test_invalid_creation_request_cannot_mutate_series(make_runtime, updates):
    runtime = make_runtime()
    series = new_series(runtime)
    with client_for(runtime) as client:
        response = client.post(f"/series/{series.id}/episode_requests", json=request_for(series, **updates))
    assert response.status_code == 422, response.text
    assert runtime.scripts == {}


def test_corrupt_creation_receipt_is_not_silently_discarded(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    with client_for(runtime) as client:
        create_episode(client, series.id, request_for(series))
    runtime.close()
    path = runtime.data_dir / "series.json"
    saved = json.loads(path.read_text())
    saved[series.id]["episode_creation_receipts"][0]["request_fingerprint"] = "invalid"
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(CorruptStorageError):
        make_runtime()
    assert path.read_bytes() == before


def test_independent_project_package_keeps_media_without_copying_series_creation_identity(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    image = runtime.data_dir / "assets/scenes/room.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"original scene image")
    runtime.series_store[series.id].scenes.append(Scene(
        id="original-room", name="房间", description="原始场景", image_url="assets/scenes/room.png",
    ))
    runtime._commit_storage("series.json")
    series = runtime.read_committed_series_view(series.id)[0]
    with client_for(runtime) as client:
        receipt = create_episode(client, series.id, request_for(series))
        runtime.scripts[receipt["episode_id"]].frames.append(StoryboardFrame(
            id="frame-room", scene_id="original-room", action_description="使用原场景",
        ))
        runtime._commit_storage("projects.json")
        original_project = runtime.read_committed_script(receipt["episode_id"]).model_dump(mode="json")
        original_series = runtime.read_committed_series_view(series.id)[0].model_dump(mode="json")
        exported = client.get(f"/projects/{receipt['episode_id']}/package/export")
        assert exported.status_code == 200, exported.text
        imported = client.post("/projects/package/import", content=exported.content, headers={"content-type": "application/zip"})
        assert imported.status_code == 200, imported.text
        copy = runtime.read_committed_script(imported.json()["id"])
        assert copy.id != receipt["episode_id"]
        assert copy.series_id is None and copy.episode_number is None
        assert "episode_creation_receipts" not in copy.model_dump()
        assert copy.scenes[0].id != "original-room"
        copied_image = runtime.data_dir / copy.scenes[0].image_url
        assert copied_image.read_bytes() == image.read_bytes() == b"original scene image"
        assert runtime.read_committed_script(receipt["episode_id"]).model_dump(mode="json") == original_project
        assert runtime.read_committed_series_view(series.id)[0].model_dump(mode="json") == original_series


def test_series_asset_copy_does_not_inherit_episode_creation_receipts(make_runtime):
    runtime = make_runtime()
    source = new_series(runtime)
    with client_for(runtime) as client:
        receipt = create_episode(client, source.id, request_for(source))
        runtime.series_store[source.id].scenes.append(Scene(
            id="original-room", name="房间", description="原始场景", image_url="assets/scenes/room.png",
        ))
        runtime._commit_storage("series.json")
        target = runtime.create_series("独立系列")
        copied, imported, skipped = runtime.import_assets_from_series(target.id, source.id, ["original-room"])
        assert imported == ["original-room"] and skipped == []
        assert copied.scenes[0].image_url == "assets/scenes/room.png"
        assert copied.scenes[0].id != "original-room"
        assert copied.episode_creation_receipts == []
        assert client.get(f"/series/{target.id}/episode_requests/{receipt['client_request_id']}").status_code == 404
        assert client.get(f"/series/{source.id}/episode_requests/{receipt['client_request_id']}").json() == receipt


def test_series_metadata_edits_cannot_clear_replay_receipts(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    with client_for(runtime) as client:
        receipt = create_episode(client, series.id, request_for(series))
        runtime.update_series(series.id, {"episode_creation_receipts": [], "title": "已改标题"})
        assert client.get(f"/series/{series.id}/episode_requests/{receipt['client_request_id']}").json() == receipt


def test_deleted_series_cannot_replay_into_a_new_series(make_runtime):
    runtime = make_runtime()
    series = new_series(runtime)
    request = request_for(series)
    with client_for(runtime) as client:
        receipt = create_episode(client, series.id, request)
        runtime.delete_series(series.id)
        assert client.post(f"/series/{series.id}/episode_requests", json=request).status_code == 404
        assert client.get(f"/series/{series.id}/episode_requests/{receipt['client_request_id']}").status_code == 404
    assert len(runtime.scripts) == 1
    assert runtime.series_store == {}
