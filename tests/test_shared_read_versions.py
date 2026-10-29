"""Committed shared-source versions, with real JSON transactions and HTTP reads."""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, GlobalAssetLibrary, Script, Series
from src.utils import json_transactions
from src.utils.json_storage import CorruptStorageError, StorageError


@pytest.fixture
def make_runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    instances = []

    def make(root=None):
        result = module.ComicGenPipeline(data_dir=root or tmp_path / "data", script_processor=Mock())
        instances.append(result)
        return result

    yield make
    for instance in instances:
        instance.close()


@pytest.fixture
def runtime(make_runtime):
    result = make_runtime()
    result.scripts["p"] = Script(
        id="p", title="project", original_text="source", series_id="s", created_at=1, updated_at=1,
    )
    result.scripts["q"] = Script(
        id="q", title="other project", original_text="source", series_id="s", created_at=1, updated_at=1,
    )
    result.series_store["s"] = Series(
        id="s", title="series", episode_ids=["p", "q"], created_at=1, updated_at=1,
    )
    result._commit_storage("projects.json", "series.json", "library_assets.json")
    return result


def client_for(runtime):
    return TestClient(api.create_app(
        data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime,
    ))


def snapshot(client, project="p"):
    response = client.get(f"/projects/{project}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("legacy", [None, {}, {"characters": [], "scenes": [], "props": []}])
def test_legacy_or_absent_library_defaults_in_memory_without_rewriting(make_runtime, tmp_path, legacy):
    root = tmp_path / "legacy"
    root.mkdir()
    path = root / "library_assets.json"
    if legacy is not None:
        path.write_text(json.dumps(legacy))
    before = path.read_bytes() if path.exists() else None
    runtime = make_runtime(root)
    assert runtime.library_store.revision == 0
    assert runtime._committed["library_assets.json"]["revision"] == 0
    assert (path.read_bytes() if path.exists() else None) == before
    runtime.create_library_asset("character", {"name": "new"})
    assert runtime.library_store.revision == 1
    runtime.close()
    reopened = make_runtime(root)
    assert reopened.library_store.revision == 1
    assert json.loads(path.read_text())["revision"] == 1


def test_real_library_commands_and_same_id_recreation_advance_one_sequence(runtime):
    project_revisions = {key: value.revision for key, value in runtime.scripts.items()}
    series_revision = runtime.series_store["s"].revision
    asset = runtime.create_library_asset("character", {"name": "original"}, source_key="same-source")
    assert runtime.library_store.revision == 1
    same = runtime.create_library_asset("character", {"name": "ignored"}, source_key="same-source")
    assert same.id == asset.id and runtime.library_store.revision == 1
    runtime.update_library_asset("character", asset.id, {"name": "changed"})
    assert runtime.library_store.revision == 2
    runtime.delete_library_asset("character", asset.id)
    assert runtime.library_store.revision == 3
    recreated = runtime.create_library_asset("character", {"name": "rebuilt"}, source_key="same-source")
    assert recreated.id == asset.id and runtime.library_store.revision == 4
    assert {key: value.revision for key, value in runtime.scripts.items()} == project_revisions
    assert runtime.series_store["s"].revision == series_revision


@pytest.mark.parametrize("forged", [0, 900])
def test_noop_and_forged_library_revision_do_not_advance_content(runtime, forged):
    asset = runtime.create_library_asset("character", {"name": "unchanged"})
    runtime.library_store.revision = forged
    runtime.update_library_asset("character", asset.id, {"name": "unchanged"})
    assert runtime.library_store.revision == 1
    runtime._save_library_data()
    assert runtime.library_store.revision == 1
    assert json.loads((runtime.data_dir / "library_assets.json").read_text())["revision"] == 1


def test_library_version_is_published_only_after_successful_commit(runtime, monkeypatch):
    original = runtime._storage.commit
    old = runtime.library_store.revision

    def observe(payloads):
        assert runtime.library_store.revision == old
        assert runtime._committed["library_assets.json"]["revision"] == old
        assert payloads["library_assets.json"]["revision"] == old + 1
        original(payloads)

    monkeypatch.setattr(runtime._storage, "commit", observe)
    runtime.create_library_asset("prop", {"name": "prop"})
    assert runtime.library_store.revision == old + 1


@pytest.mark.parametrize("phase,after_replace", [
    ("prepared", True), ("committed", False), ("committed", True),
])
def test_transaction_failure_rolls_back_memory_and_restart_uses_journal(
    runtime, make_runtime, monkeypatch, phase, after_replace,
):
    asset = runtime.create_library_asset("character", {"name": "old"})
    before = runtime.library_store.model_dump()
    write = json_transactions.atomic_write_json
    failed = False

    def fault(path, value):
        nonlocal failed
        if Path(path) == runtime._storage.journal_path and value["phase"] == phase and not failed:
            failed = True
            if after_replace:
                write(path, value)
            raise StorageError("synthetic journal failure", commit_uncertain=after_replace)
        return write(path, value)

    monkeypatch.setattr(json_transactions, "atomic_write_json", fault)
    with pytest.raises(StorageError) as error:
        runtime.update_library_asset("character", asset.id, {"name": "new"})
    uncertain = phase == "committed" and after_replace
    assert error.value.commit_uncertain is uncertain
    assert runtime.library_store.model_dump() == before
    assert runtime._committed["library_assets.json"] == before
    with pytest.raises(StorageError, match="restart"):
        runtime.assert_storage_available()
    runtime.close()
    reopened = make_runtime(runtime.data_dir)
    assert reopened.library_store.revision == before["revision"] + int(uncertain)
    assert reopened.library_store.characters[0].name == ("new" if uncertain else "old")


@pytest.mark.parametrize("invalid", [-1, True, "0", 0.0, None])
def test_library_revision_is_strict_and_corrupt_file_is_preserved(make_runtime, tmp_path, invalid):
    with pytest.raises(ValidationError):
        GlobalAssetLibrary.model_validate({"revision": invalid})
    root = tmp_path / "invalid"
    root.mkdir()
    path = root / "library_assets.json"
    path.write_text(json.dumps({"revision": invalid}))
    before = path.read_bytes()
    with pytest.raises(CorruptStorageError):
        make_runtime(root)
    assert path.read_bytes() == before


@pytest.mark.parametrize("source", ["series", "global"])
def test_shared_voice_ack_and_two_project_reads_advance_only_owner(runtime, source):
    if source == "series":
        asset = runtime.create_series_asset("s", "character", name="shared", voice_id="old")
    else:
        asset = runtime.create_library_asset("character", {"name": "shared", "voice_id": "old"})
    with client_for(runtime) as client:
        before = snapshot(client)
        response = client.post(f"/projects/p/characters/{asset.id}/voice", json={
            "voice_id": "new", "voice_name": "New",
        })
        assert response.status_code == 200, response.text
        after = response.json()
        assert after["revision"] == before["revision"]
        expected = before["read_versions"]
        if source == "series":
            expected["series"]["revision"] += 1
        else:
            expected["library"] += 1
        assert after["read_versions"] == expected
        assert after["characters"][0]["source"] == source
        assert after["characters"][0]["voice_id"] == "new"
        other = snapshot(client, "q")
        assert other["read_versions"]["series"] == expected["series"]
        assert other["read_versions"]["library"] == expected["library"]
        assert other["characters"][0]["voice_id"] == "new"
        assert all(not project.characters for project in runtime.scripts.values())
        data = json.loads((runtime.data_dir / "projects.json").read_text())
        assert data["p"]["characters"] == data["q"]["characters"] == []


@pytest.mark.parametrize("series_id,expected", [(None, None), ("missing", {"id": "missing", "revision": None})])
def test_unbound_and_dangling_series_are_distinct(runtime, series_id, expected):
    runtime.scripts["p"].series_id = series_id
    runtime._save_data()
    with client_for(runtime) as client:
        value = snapshot(client)
        assert value["read_versions"] == {
            "project": value["revision"], "series": expected, "library": 0,
        }


def test_series_delete_returns_unbound_version_without_stale_series_assets(runtime):
    runtime.create_series_asset("s", "character", name="series character")
    with client_for(runtime) as client:
        before = snapshot(client)
        runtime.delete_series("s")
        after = snapshot(client)
        assert after["read_versions"]["series"] is None
        assert after["read_versions"]["project"] == before["revision"] + 1
        assert after["characters"] == []


def test_series_switch_identity_and_return_to_original_advance_project_version(runtime):
    other = runtime.create_series("B")
    runtime.create_series_asset("s", "character", name="from A")
    runtime.create_series_asset(other.id, "character", name="from B")
    with client_for(runtime) as client:
        first = snapshot(client)
        runtime.add_episode_to_series(other.id, "p")
        second = snapshot(client)
        runtime.add_episode_to_series("s", "p")
        third = snapshot(client)
        assert [value["read_versions"]["series"]["id"] for value in (first, second, third)] == ["s", other.id, "s"]
        assert [value["characters"][0]["name"] for value in (first, second, third)] == ["from A", "from B", "from A"]
        assert first["revision"] < second["revision"] < third["revision"]


def test_later_commits_cannot_change_captured_http_content_or_versions(runtime, monkeypatch):
    shared = runtime.create_series_asset("s", "character", name="saved series")
    global_asset = runtime.create_library_asset("character", {"name": "saved global"})
    with client_for(runtime) as client:
        before = snapshot(client)
        read = runtime.read_committed_project_view

        def read_then_change(project_id):
            captured = read(project_id)
            runtime.update_script_text("p", "later", expected_revision=captured[0].revision)
            runtime.update_series_asset_attributes("s", shared.id, "character", {"name": "later series"})
            runtime.update_library_asset("character", global_asset.id, {"name": "later global"})
            return captured

        monkeypatch.setattr(runtime, "read_committed_project_view", read_then_change)
        assert snapshot(client) == before
        monkeypatch.setattr(runtime, "read_committed_project_view", read)
        after = snapshot(client)
        assert after["read_versions"]["project"] == before["read_versions"]["project"] + 1
        assert after["read_versions"]["series"]["revision"] == before["read_versions"]["series"]["revision"] + 1
        assert after["read_versions"]["library"] == before["read_versions"]["library"] + 1


@pytest.mark.parametrize("source", ["series", "global"])
def test_http_read_waits_for_commit_and_never_pairs_mixed_content_and_revision(runtime, monkeypatch, source):
    asset = (runtime.create_series_asset("s", "character", name="old") if source == "series"
             else runtime.create_library_asset("character", {"name": "old"}))
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    entered, release, reading = Event(), Event(), Event()
    commit = runtime._storage.commit

    def pause(payloads):
        entered.set()
        assert release.wait(5)
        commit(payloads)

    def observed_pipeline():
        reading.set()
        return runtime

    app.dependency_overrides[api.get_pipeline] = observed_pipeline
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        before = snapshot(client)
        reading.clear()
        monkeypatch.setattr(runtime._storage, "commit", pause)
        editor = pool.submit(runtime.bind_voice, "p", asset.id, "new", "New")
        try:
            assert entered.wait(5)
            reader = pool.submit(snapshot, client)
            assert reading.wait(5)
            with pytest.raises(TimeoutError):
                reader.result(timeout=0.1)
        finally:
            release.set()
        editor.result(timeout=5)
        after = reader.result(timeout=5)
        assert after["characters"][0]["voice_id"] == "new"
        assert after["revision"] == before["revision"]
        if source == "series":
            assert after["read_versions"]["series"]["revision"] == before["read_versions"]["series"]["revision"] + 1
        else:
            assert after["read_versions"]["library"] == before["read_versions"]["library"] + 1


def test_projection_metadata_never_persists_and_raw_script_ack_stays_raw(runtime):
    runtime.series_store["s"].characters.append(Character(id="same", name="series", description=""))
    runtime.library_store.characters.append(Character(id="same", name="global", description=""))
    runtime.scripts["p"].characters.append(Character(id="same", name="episode", description=""))
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    with client_for(runtime) as client:
        value = snapshot(client)
        assert value["read_versions"]["project"] == value["revision"]
        assert [(item["name"], item["source"]) for item in value["characters"]] == [("episode", "episode")]
        value["read_versions"]["library"] = 999
        assert snapshot(client)["read_versions"]["library"] == 1
        response = client.put("/projects/p/text", json={"text": "edit", "expected_revision": value["revision"]})
        assert response.status_code == 200, response.text
        assert "read_versions" not in response.json()
        for name in ("projects.json", "series.json", "library_assets.json"):
            assert '"read_versions"' not in (runtime.data_dir / name).read_text()
