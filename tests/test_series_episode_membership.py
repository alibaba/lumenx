"""F06: a late unlink cannot erase an episode's current series ownership."""

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


def saved_bytes(root):
    return {name: (root / name).read_bytes() for name in ("projects.json", "series.json")}


def read_relationship(client, series_ids, project_id):
    result = {}
    for series_id in series_ids:
        response = client.get(f"/series/{series_id}")
        assert response.status_code == 200, response.text
        result[series_id] = response.json()
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 200, response.text
    result[project_id] = response.json()
    return result


@pytest.mark.parametrize("old_membership", ["transferred", "never_owned", "stale_list"])
def test_wrong_series_unlink_preserves_current_owner_and_saved_bytes(tmp_path, old_membership):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        old_series = create_series(client, "A")
        current_series = create_series(client, "B")
        project_id = create_project(client, old_series if old_membership == "transferred" else None)
        moved = client.post(f"/series/{current_series}/episodes", json={
            "script_id": project_id, "episode_number": 7,
        })
        assert moved.status_code == 200, moved.text
        if old_membership == "stale_list":
            # Legacy inconsistent list; the project's explicit owner remains B.
            app.state.pipeline.series_store[old_series].episode_ids.append(project_id)
            app.state.pipeline._save_series_data()
        before_bytes = saved_bytes(tmp_path)
        before_view = read_relationship(client, (old_series, current_series), project_id)
        assert before_view[project_id]["series_id"] == current_series
        assert before_view[project_id]["episode_number"] == 7

        response = client.delete(f"/series/{old_series}/episodes/{project_id}")

        after_view = read_relationship(client, (old_series, current_series), project_id)
        assert response.status_code == 409, {
            "actual_series_id": after_view[project_id]["series_id"],
            "expected_series_id": current_series,
            "current_series_members": after_view[current_series]["episode_ids"],
            "saved_bytes_unchanged": saved_bytes(tmp_path) == before_bytes,
        }
        assert response.json()["code"] == "storage_conflict"
        assert saved_bytes(tmp_path) == before_bytes
        assert after_view == before_view

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        assert read_relationship(client, (old_series, current_series), project_id) == before_view
        assert saved_bytes(tmp_path) == before_bytes


@pytest.mark.parametrize("relationship", ["linked", "project_only", "series_only", "missing_project"])
def test_unlink_preserves_normal_and_legacy_cleanup_semantics(tmp_path, relationship):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        series_id = create_series(client, "A")
        project_id = create_project(client, series_id)
        runtime = app.state.pipeline
        if relationship == "project_only":
            runtime.series_store[series_id].episode_ids.clear()
        elif relationship == "series_only":
            runtime.scripts[project_id].series_id = None
        elif relationship == "missing_project":
            del runtime.scripts[project_id]
        if relationship != "linked":
            runtime._commit_storage("projects.json", "series.json")

        for _ in range(2):
            response = client.delete(f"/series/{series_id}/episodes/{project_id}")
            assert response.status_code == 200, response.text
            assert response.json()["episode_ids"] == []
            project = client.get(f"/projects/{project_id}")
            if relationship == "missing_project":
                assert project.status_code == 404
            else:
                assert project.status_code == 200, project.text
                assert project.json()["series_id"] is None
                assert project.json()["episode_number"] is None
        after_bytes = saved_bytes(tmp_path)

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        assert client.get(f"/series/{series_id}").json()["episode_ids"] == []
        project = client.get(f"/projects/{project_id}")
        if relationship == "missing_project":
            assert project.status_code == 404
        else:
            assert project.json()["series_id"] is None
            assert project.json()["episode_number"] is None
        assert saved_bytes(tmp_path) == after_bytes


def test_missing_series_still_returns_not_found_without_changing_the_project(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        series_id = create_series(client, "Current")
        project_id = create_project(client, series_id)
        before = saved_bytes(tmp_path)
        response = client.delete(f"/series/missing/episodes/{project_id}")
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Series not found"
        assert saved_bytes(tmp_path) == before


def test_failed_unlink_keeps_both_sides_after_reopening(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        series_id = create_series(client, "Current")
        project_id = create_project(client, series_id)
        before_bytes = saved_bytes(tmp_path)
        before_view = read_relationship(client, (series_id,), project_id)

        def fail_commit(payloads):
            raise StorageError("disk unavailable")

        monkeypatch.setattr(app.state.pipeline._storage, "commit", fail_commit)
        response = client.delete(f"/series/{series_id}/episodes/{project_id}")
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "storage_unavailable"
        assert saved_bytes(tmp_path) == before_bytes
        assert client.get(f"/projects/{project_id}").status_code == 503

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        assert read_relationship(client, (series_id,), project_id) == before_view
        assert saved_bytes(tmp_path) == before_bytes


@pytest.mark.parametrize("unavailable", ["closed", "failed"])
def test_unavailable_runtime_cannot_mutate_membership_before_raising(tmp_path, unavailable):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        series_id = create_series(client, "Current")
        project_id = create_project(client, series_id)
        runtime = app.state.pipeline
        before = saved_bytes(tmp_path)
        if unavailable == "closed":
            runtime.close()
        else:
            runtime.mark_storage_failed(StorageError("previous save failed"))

        with pytest.raises(StorageError, match="restart"):
            runtime.remove_episode_from_series(series_id, project_id)

        assert runtime.scripts[project_id].series_id == series_id
        assert runtime.scripts[project_id].episode_number == 1
        assert runtime.series_store[series_id].episode_ids == [project_id]
        assert saved_bytes(tmp_path) == before
