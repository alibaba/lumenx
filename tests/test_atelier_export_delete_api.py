from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import _build_export_history_external_guard, create_app
from tests.test_atelier_export_delete_storage import BYTES, completed_project


@contextmanager
def no_refs():
    yield True


def test_delete_export_route_tombstones_deletes_file_and_replays(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        media = tmp_path / "video" / "atelier_seq_job1.mp4"
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(BYTES)
        app.state.atelier_storage.create_project(completed_project())
        app.state.atelier_export_history_service._external_reference_guard = lambda *_args: no_refs()

        response = client.request(
            "DELETE",
            "/atelier/projects/p1/exports/export1",
            json={"expected_revision": 0, "client_request_id": "delete-1"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["replayed"] is False
        assert body["export_record"]["deletion"]["file_status"] == "deleted"
        assert body["project"]["exports"][0]["deletion"]["client_request_id"] == "delete-1"
        assert body["project"]["sequence_export_jobs"][0]["status"] == "completed"
        assert not media.exists()

        replay = client.request(
            "DELETE",
            "/atelier/projects/p1/exports/export1",
            json={"expected_revision": 0, "client_request_id": "delete-1"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["export_record"]["deletion"]["file_status"] == "deleted"


def test_delete_export_route_validation_conflict_notfound_and_no_physical_delete(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        media = tmp_path / "video" / "atelier_seq_job1.mp4"
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(BYTES)
        app.state.atelier_storage.create_project(completed_project())

        assert client.request(
            "DELETE",
            "/atelier/projects/p1/exports/export1",
            json={"expected_revision": 0, "client_request_id": "bad", "unknown": True},
        ).status_code == 422
        assert client.request(
            "DELETE",
            "/atelier/projects/missing/exports/export1",
            json={"expected_revision": 0, "client_request_id": "delete-missing-project"},
        ).status_code == 404
        assert client.request(
            "DELETE",
            "/atelier/projects/p1/exports/missing",
            json={"expected_revision": 0, "client_request_id": "delete-missing"},
        ).status_code == 404
        stale = client.request(
            "DELETE",
            "/atelier/projects/p1/exports/export1",
            json={"expected_revision": 9, "client_request_id": "delete-stale"},
        )
        assert stale.status_code == 409

        retained = client.request(
            "DELETE",
            "/atelier/projects/p1/exports/export1",
            json={"expected_revision": 0, "client_request_id": "delete-retain", "delete_file": False},
        )
        assert retained.status_code == 200, retained.text
        assert retained.json()["export_record"]["deletion"]["file_status"] == "retained"
        assert retained.json()["export_record"]["deletion"]["checked_at"] is not None
        assert media.read_bytes() == BYTES


def test_delete_export_cross_domain_scan_failure_blocks_without_unlink(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        media = tmp_path / "video" / "atelier_seq_job1.mp4"
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(BYTES)
        app.state.atelier_storage.create_project(completed_project())

        def fail_scan():
            raise RuntimeError("scan failed")

        monkeypatch.setattr(app.state.pipeline, "read_committed_projects", fail_scan)
        response = client.request(
            "DELETE",
            "/atelier/projects/p1/exports/export1",
            json={"expected_revision": 0, "client_request_id": "delete-scan-fail"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["export_record"]["deletion"]["file_status"] == "blocked"
        assert response.json()["export_record"]["deletion"]["error_code"] == "checker_unavailable"
        assert media.read_bytes() == BYTES


def test_delete_export_playground_uncertain_commit_blocks_without_unlink(tmp_path):
    from src.utils.json_storage import StorageError

    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        media = tmp_path / "video" / "atelier_seq_job1.mp4"
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(BYTES)
        app.state.atelier_storage.create_project(completed_project())
        with app.state.playground_storage._lock:
            app.state.playground_storage._write_error = StorageError("playground uncertain", commit_uncertain=True)

        response = client.request(
            "DELETE",
            "/atelier/projects/p1/exports/export1",
            json={"expected_revision": 0, "client_request_id": "delete-playground-uncertain"},
        )

        assert response.status_code == 200, response.text
        deletion = response.json()["export_record"]["deletion"]
        assert deletion["file_status"] == "blocked"
        assert deletion["error_code"] == "checker_unavailable"
        assert media.read_bytes() == BYTES


def test_external_guard_does_not_double_yield_when_body_raises(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        project = completed_project()
        export = project.exports[0]
        guard = _build_export_history_external_guard(app)
        with pytest.raises(ValueError, match="body failed"):
            with guard(project, export):
                raise ValueError("body failed")
