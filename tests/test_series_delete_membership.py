"""F06: deleting a series cannot erase another series' explicit ownership."""

from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.utils.json_storage import StorageError


def create_series(client, title):
    response = client.post("/series", json={"title": title})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def create_project(client, series_id=None):
    response = client.post("/projects?skip_analysis=true", json={
        "title": "Episode", "text": "Authored story", "series_id": series_id,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def read(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def saved_bytes(root):
    return {name: (root / name).read_bytes() for name in ("projects.json", "series.json")}


def memory(runtime):
    return {
        "live": runtime._storage_payloads(("projects.json", "series.json")),
        "committed": deepcopy(runtime._committed),
    }


def move(client, series_id, project_id):
    response = client.post(f"/series/{series_id}/episodes", json={
        "script_id": project_id, "episode_number": 7,
    })
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("owner_exists", [True, False])
def test_delete_old_series_preserves_foreign_owner_record_and_reopen(tmp_path, owner_exists):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        old_series = create_series(client, "A")
        current_series = create_series(client, "B")
        project_id = create_project(client, old_series)
        move(client, current_series, project_id)
        runtime = app.state.pipeline
        # Normal moves remove A's list entry. Only the historical stale list is injected.
        assert runtime.series_store[old_series].episode_ids == []
        runtime.series_store[old_series].episode_ids.append(project_id)
        if owner_exists:
            runtime.create_series_asset(current_series, "character", name="Only B")
        else:
            del runtime.series_store[current_series]
        runtime._save_series_data()
        before_bytes = saved_bytes(tmp_path)
        before_project = read(client, f"/projects/{project_id}")
        before_series = read(client, f"/series/{current_series}") if owner_exists else None
        assert before_project["episode_number"] == 7
        assert before_project["read_versions"]["series"]["id"] == current_series
        if owner_exists:
            assert [asset["name"] for asset in before_project["characters"]] == ["Only B"]

        response = client.delete(f"/series/{old_series}")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "deleted"}
        assert client.get(f"/series/{old_series}").status_code == 404
        assert read(client, f"/projects/{project_id}") == before_project
        assert (tmp_path / "projects.json").read_bytes() == before_bytes["projects.json"]
        if owner_exists:
            assert read(client, f"/series/{current_series}") == before_series
        else:
            assert client.get(f"/series/{current_series}").status_code == 404
        after_bytes = saved_bytes(tmp_path)

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        assert client.get(f"/series/{old_series}").status_code == 404
        assert read(client, f"/projects/{project_id}") == before_project
        if owner_exists:
            assert read(client, f"/series/{current_series}") == before_series
        assert saved_bytes(tmp_path) == after_bytes


def test_delete_mixed_legacy_list_changes_only_owned_or_unbound_projects(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        old_series = create_series(client, "A")
        current_series = create_series(client, "B")
        owned = create_project(client, old_series)
        foreign = create_project(client, current_series)
        unbound = create_project(client)
        move(client, current_series, foreign)
        runtime = app.state.pipeline
        runtime.scripts[unbound].episode_number = 4
        runtime.series_store[old_series].episode_ids = [foreign, owned, unbound, "missing", foreign]
        runtime._commit_storage("projects.json", "series.json")
        before_projects = json.loads((tmp_path / "projects.json").read_bytes())
        before_current = read(client, f"/series/{current_series}")
        before_foreign = read(client, f"/projects/{foreign}")

        response = client.delete(f"/series/{old_series}")

        assert response.status_code == 200, response.text
        assert read(client, f"/projects/{foreign}") == before_foreign
        assert read(client, f"/series/{current_series}") == before_current
        expected_projects = deepcopy(before_projects)
        for project_id in (owned, unbound):
            expected_projects[project_id].update({
                "series_id": None, "episode_number": None,
                "revision": before_projects[project_id]["revision"] + 1,
            })
            view = read(client, f"/projects/{project_id}")
            assert view["series_id"] is None
            assert view["episode_number"] is None
            assert view["original_text"] == "Authored story"
        assert json.loads((tmp_path / "projects.json").read_bytes()) == expected_projects
        assert set(json.loads((tmp_path / "series.json").read_bytes())) == {current_series}
        after_bytes = saved_bytes(tmp_path)

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        assert read(client, f"/projects/{foreign}") == before_foreign
        assert read(client, f"/series/{current_series}") == before_current
        for project_id in (owned, unbound):
            assert read(client, f"/projects/{project_id}")["series_id"] is None
        assert saved_bytes(tmp_path) == after_bytes


@pytest.mark.parametrize("relationship", ["owned", "none", "empty_owner", "missing_project"])
def test_delete_keeps_normal_and_legacy_cleanup_semantics(tmp_path, relationship):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        series_id = create_series(client, "A")
        project_id = create_project(client, series_id)
        runtime = app.state.pipeline
        if relationship in ("none", "empty_owner"):
            runtime.scripts[project_id].series_id = None if relationship == "none" else ""
        elif relationship == "missing_project":
            del runtime.scripts[project_id]
        runtime._commit_storage("projects.json", "series.json")
        before_projects = json.loads((tmp_path / "projects.json").read_bytes())

        response = client.delete(f"/series/{series_id}")

        assert response.status_code == 200, response.text
        assert client.get(f"/series/{series_id}").status_code == 404
        assert read(client, "/series") == []
        if relationship == "missing_project":
            assert client.get(f"/projects/{project_id}").status_code == 404
        else:
            expected = deepcopy(before_projects[project_id])
            expected.update({"series_id": None, "episode_number": None, "revision": expected["revision"] + 1})
            assert json.loads((tmp_path / "projects.json").read_bytes())[project_id] == expected
            view = read(client, f"/projects/{project_id}")
            assert view["read_versions"]["series"] is None
            assert view["original_text"] == "Authored story"
        after_bytes = saved_bytes(tmp_path)
        assert client.delete(f"/series/{series_id}").status_code == 404
        assert saved_bytes(tmp_path) == after_bytes

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        assert read(client, "/series") == []
        if relationship != "missing_project":
            view = read(client, f"/projects/{project_id}")
            assert view["series_id"] is None
            assert view["episode_number"] is None
        assert saved_bytes(tmp_path) == after_bytes


def test_delete_empty_or_missing_series_preserves_unrelated_project(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        series_id = create_series(client, "Empty")
        project_id = create_project(client)
        before_project = read(client, f"/projects/{project_id}")
        response = client.delete(f"/series/{series_id}")
        assert response.status_code == 200, response.text
        assert read(client, f"/projects/{project_id}") == before_project
        before_bytes = saved_bytes(tmp_path)
        response = client.delete("/series/missing")
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Series not found"
        assert saved_bytes(tmp_path) == before_bytes


@pytest.mark.parametrize("unavailable", ["closed", "failed"])
def test_unavailable_runtime_cannot_mutate_before_delete_raises(tmp_path, unavailable):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        series_id = create_series(client, "A")
        create_project(client, series_id)
        runtime = app.state.pipeline
        before_memory = memory(runtime)
        before_bytes = saved_bytes(tmp_path)
        if unavailable == "closed":
            runtime.close()
        else:
            runtime.mark_storage_failed(StorageError("previous save failed"))
        response = client.delete(f"/series/{series_id}")
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "storage_unavailable"
        assert memory(runtime) == before_memory

        # Cover closing after the HTTP dependency check but before the domain lock.
        with pytest.raises(StorageError, match="restart"):
            runtime.delete_series(series_id)

        assert memory(runtime) == before_memory
        assert saved_bytes(tmp_path) == before_bytes


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_delete_rolls_back_both_aggregates_and_reopens(tmp_path, monkeypatch, uncertain):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        old_series = create_series(client, "A")
        current_series = create_series(client, "B")
        owned = create_project(client, old_series)
        foreign = create_project(client, current_series)
        move(client, current_series, foreign)
        runtime = app.state.pipeline
        runtime.series_store[old_series].episode_ids.append(foreign)
        runtime._save_series_data()
        paths = [f"/series/{old_series}", f"/series/{current_series}",
                 f"/projects/{owned}", f"/projects/{foreign}"]
        before_views = {path: read(client, path) for path in paths}
        before_memory = memory(runtime)
        before_bytes = saved_bytes(tmp_path)
        commits = []

        def fail_commit(payloads):
            commits.append(set(payloads))
            raise StorageError("disk unavailable", commit_uncertain=uncertain)

        monkeypatch.setattr(runtime._storage, "commit", fail_commit)
        response = client.delete(f"/series/{old_series}")
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "storage_unavailable"
        assert response.json()["commit_uncertain"] is uncertain
        assert commits == [{"projects.json", "series.json"}]
        assert memory(runtime) == before_memory
        assert saved_bytes(tmp_path) == before_bytes
        assert client.delete(f"/series/{old_series}").status_code == 503
        assert len(commits) == 1

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        assert {path: read(client, path) for path in paths} == before_views
        assert saved_bytes(tmp_path) == before_bytes
