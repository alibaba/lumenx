from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import pytest
from fastapi.testclient import TestClient

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.comic_gen.api import create_app
from src.utils.json_storage import StorageError


def _tool(name: str) -> str:
    env_name = "LUMENX_TEST_FFMPEG" if name == "ffmpeg" else "LUMENX_TEST_FFPROBE"
    path = os.environ.get(env_name) or shutil.which(name)
    if not path:
        pytest.skip(f"{name} not available")
    return path


def _make_clip(path: Path, *, duration: float = 0.7, color: str = "red") -> None:
    ffmpeg = _tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x48:d={duration}:r=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}:sample_rate=44100",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _app(root: Path, **kwargs):
    return create_app(data_dir=root, load_config=False, recover_orphan_tasks=False, **kwargs)


def _seed_project(app, root: Path, *, project_id: str = "p1") -> AtelierProject:
    _make_clip(root / "uploads" / "clip.mp4")
    project = AtelierProject(
        id=project_id,
        title="Exportable",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="video-1",
                project_id=project_id,
                type="video",
                status="completed",
                media_urls=["uploads/clip.mp4"],
            )
        ],
    )
    return app.state.atelier_storage.create_project(project)


def _create_payload(request_id: str = "req-1", *, revision: int = 0, extra_entry: dict | None = None):
    entry = {"id": "clip-1", "parentId": "video-1", "trimStart": 0, "trimEnd": 0.4}
    if extra_entry:
        entry.update(extra_entry)
    return {"expected_revision": revision, "client_request_id": request_id, "entries": [entry]}


def test_create_export_job_schedules_once_replay_does_not_dispatch_and_worker_completes(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        dispatcher = app.state.atelier_sequence_export_dispatcher
        original_schedule = dispatcher.schedule
        scheduled: list[tuple[str, str]] = []

        def counted_schedule(project_id: str, job_id: str) -> bool:
            scheduled.append((project_id, job_id))
            return original_schedule(project_id, job_id)

        dispatcher.schedule = counted_schedule
        created = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload())
        assert created.status_code == 200, created.text
        assert created.json()["new_job_ids"] == ["job-1"] or len(created.json()["new_job_ids"]) == 1
        assert len(scheduled) == 1
        assert dispatcher.wait_for_idle(10)
        job_id = created.json()["job"]["job_id"]
        completed = client.get(f"/atelier/projects/p1/sequence/export_jobs/{job_id}")
        assert completed.status_code == 200, completed.text
        assert completed.json()["job"]["status"] == "completed"
        assert completed.json()["export_record"]["video_url"] == f"video/atelier_seq_{job_id}.mp4"

        replay = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload())
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["new_job_ids"] == []
        assert len(scheduled) == 1


def test_redo_export_route_uses_history_path_and_schedules_once(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        first = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("create-before-redo"))
        assert first.status_code == 200, first.text
        assert app.state.atelier_sequence_export_dispatcher.wait_for_idle(10)
        source = client.get(f"/atelier/projects/p1/sequence/export_jobs/{first.json()['job']['job_id']}").json()["export_record"]
        assert source is not None

        dispatcher = app.state.atelier_sequence_export_dispatcher
        original_schedule = dispatcher.schedule
        scheduled: list[tuple[str, str]] = []

        def counted_schedule(project_id: str, job_id: str) -> bool:
            scheduled.append((project_id, job_id))
            return original_schedule(project_id, job_id)

        dispatcher.schedule = counted_schedule
        redo = client.post(
            f"/atelier/projects/p1/exports/{source['id']}/redo",
            json={"expected_revision": 1, "client_request_id": "redo-1"},
        )
        assert redo.status_code == 200, redo.text
        assert redo.json()["job"]["request_contract_version"] == "atelier-sequence-export-redo-v1"
        assert redo.json()["job"]["source_export_id"] == source["id"]
        assert redo.json()["new_job_ids"] == [redo.json()["job"]["job_id"]]
        assert len(scheduled) == 1

        replay = client.post(
            f"/atelier/projects/p1/exports/{source['id']}/redo",
            json={"expected_revision": 1, "client_request_id": "redo-1"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["new_job_ids"] == []
        assert len(scheduled) == 1

        invalid = client.post(
            f"/atelier/projects/p1/exports/{source['id']}/redo",
            json={**_create_payload("redo-bad", revision=1), "entries": []},
        )
        assert invalid.status_code == 422


def test_redo_export_save_failure_does_not_schedule(tmp_path, monkeypatch):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        first = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("create-before-savefail"))
        assert first.status_code == 200, first.text
        assert app.state.atelier_sequence_export_dispatcher.wait_for_idle(10)
        source = client.get(f"/atelier/projects/p1/sequence/export_jobs/{first.json()['job']['job_id']}").json()["export_record"]
        assert source is not None

        scheduled: list[tuple[str, str]] = []
        app.state.atelier_sequence_export_dispatcher.schedule = lambda project_id, job_id: scheduled.append((project_id, job_id))

        def fail_creation(*_args, **_kwargs):
            raise StorageError("save failed")

        monkeypatch.setattr(app.state.atelier_storage, "apply_sequence_export_job_creation", fail_creation)
        failing = client.post(
            f"/atelier/projects/p1/exports/{source['id']}/redo",
            json={"expected_revision": 1, "client_request_id": "redo-savefail"},
        )
        assert failing.status_code >= 500
        assert scheduled == []
        assert len(app.state.atelier_storage.get_project("p1").sequence_export_jobs) == 1


def test_by_request_route_precedes_job_id_and_missing_request_is_404(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        missing = client.get("/atelier/projects/p1/sequence/export_jobs/by_request/absent")
        assert missing.status_code == 404
        created = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("req-read"))
        assert created.status_code == 200, created.text
        readback = client.get("/atelier/projects/p1/sequence/export_jobs/by_request/req-read")
        assert readback.status_code == 200, readback.text
        assert readback.json()["job"]["client_request_id"] == "req-read"


def test_invalid_nested_request_is_422_and_does_not_create_job(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        bad = client.post(
            "/atelier/projects/p1/sequence/export_jobs",
            json=_create_payload("req-bad", extra_entry={"gain": True, "unknown": "kept raw"}),
        )
        assert bad.status_code == 422, bad.text
        assert bad.json()["detail"]["code"] == "invalid_sequence"
        assert app.state.atelier_storage.get_project("p1").sequence_export_jobs == []
        null_entries = client.post(
            "/atelier/projects/p1/sequence/export_jobs",
            json={"expected_revision": 0, "client_request_id": "req-null", "entries": None},
        )
        assert null_entries.status_code == 422
        assert app.state.atelier_storage.get_project("p1").sequence_export_jobs == []


def test_same_request_id_changed_nested_presence_or_content_conflicts(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        first = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("same"))
        assert first.status_code == 200, first.text
        changed_content = client.post(
            "/atelier/projects/p1/sequence/export_jobs",
            json=_create_payload("same", extra_entry={"trimEnd": 0.5}),
        )
        assert changed_content.status_code == 409
        changed_presence = client.post(
            "/atelier/projects/p1/sequence/export_jobs",
            json={"expected_revision": 0, "client_request_id": "same", "audio_tracks": []},
        )
        assert changed_presence.status_code == 409


def test_stale_revision_is_409_and_missing_job_routes_are_404(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        stale = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload(revision=9))
        assert stale.status_code == 409
        assert client.get("/atelier/projects/p1/sequence/export_jobs/missing").status_code == 404
        assert client.post("/atelier/projects/p1/sequence/export_jobs/missing/cancel").status_code == 404
        assert client.post("/atelier/projects/p1/sequence/export_jobs/missing/repair").status_code == 404
        assert client.post("/atelier/projects/p1/sequence/export_jobs/missing/resume").status_code == 404


def test_lost_ack_schedule_failure_keeps_durable_job_readable_after_restart(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)

        def fail_schedule(_project_id: str, _job_id: str) -> bool:
            raise StorageError("dispatcher unavailable")

        client.app.state.atelier_sequence_export_dispatcher.schedule = fail_schedule
        response = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("lost"))
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "sequence_export_dispatch_unavailable"
        assert response.json()["detail"]["client_request_id"] == "lost"
        saved = client.get("/atelier/projects/p1/sequence/export_jobs/by_request/lost")
        assert saved.status_code == 200, saved.text
        assert saved.json()["job"]["status"] == "queued"

    with TestClient(_app(tmp_path)) as reopened:
        readback = reopened.get("/atelier/projects/p1/sequence/export_jobs/by_request/lost")
        assert readback.status_code == 200, readback.text
        assert readback.json()["job"]["client_request_id"] == "lost"


def test_dispatcher_refusal_after_persist_is_503_with_durable_readback(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        dispatcher = client.app.state.atelier_sequence_export_dispatcher
        dispatcher.request_stop()
        stopped = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("stopped"))
        assert stopped.status_code == 503, stopped.text
        assert stopped.json()["detail"]["code"] == "sequence_export_dispatch_unavailable"
        assert stopped.json()["detail"]["client_request_id"] == "stopped"
        readback = client.get("/atelier/projects/p1/sequence/export_jobs/by_request/stopped")
        assert readback.status_code == 200, readback.text
        assert readback.json()["job"]["status"] == "queued"

    app = _app(tmp_path / "startfail")
    with TestClient(app) as client:
        _seed_project(app, tmp_path / "startfail")

        def fail_schedule(_project_id: str, _job_id: str) -> bool:
            raise RuntimeError("no worker threads")

        client.app.state.atelier_sequence_export_dispatcher.schedule = fail_schedule
        failed = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("startfail"))
        assert failed.status_code == 503, failed.text
        assert failed.json()["detail"]["code"] == "sequence_export_dispatch_unavailable"
        assert failed.json()["detail"]["client_request_id"] == "startfail"
        readback = client.get("/atelier/projects/p1/sequence/export_jobs/by_request/startfail")
        assert readback.status_code == 200, readback.text
        assert readback.json()["job"]["status"] == "queued"


def test_repair_after_restart_marks_orphaned_running_without_proof_failed(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        client.app.state.atelier_sequence_export_dispatcher.schedule = lambda _project_id, _job_id: True
        created = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("orphan"))
        assert created.status_code == 200, created.text
        job_id = created.json()["job"]["job_id"]
        app.state.atelier_storage.apply_sequence_export_job_update(
            "p1",
            job_id=job_id,
            command=lambda _project, job: setattr(job, "status", "running"),
        )

    with TestClient(_app(tmp_path)) as reopened:
        repaired = reopened.post(f"/atelier/projects/p1/sequence/export_jobs/{job_id}/repair")
        assert repaired.status_code == 200, repaired.text
        assert repaired.json()["job"]["status"] == "failed"
        assert repaired.json()["job"]["error_code"] == "orphaned_running_export"
        assert repaired.json()["export_record"] is None


def test_resume_schedules_existing_job_only_and_repair_queued_is_no_mutation_ack(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        dispatcher = app.state.atelier_sequence_export_dispatcher
        calls: list[tuple[str, str]] = []
        dispatcher.schedule = lambda project_id, job_id: calls.append((project_id, job_id)) or True
        created = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("resume"))
        job_id = created.json()["job"]["job_id"]
        assert calls == [("p1", job_id)]
        repair = client.post(f"/atelier/projects/p1/sequence/export_jobs/{job_id}/repair")
        assert repair.status_code == 200, repair.text
        assert repair.json()["job"]["status"] == "queued"
        assert app.state.atelier_storage.get_project("p1").sequence_export_jobs[0].job_revision == 0
        resumed = client.post(f"/atelier/projects/p1/sequence/export_jobs/{job_id}/resume")
        assert resumed.status_code == 200, resumed.text
        assert calls == [("p1", job_id), ("p1", job_id)]


def test_access_identity_and_two_apps_are_isolated(tmp_path):
    token = "synthetic-export-api-token-" * 3
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    with TestClient(_app(root_a, access_mode="remote", access_token=token)) as a, TestClient(_app(root_b)) as b:
        _seed_project(a.app, root_a, project_id="pa")
        _seed_project(b.app, root_b, project_id="pb")
        auth = {"Authorization": "Bearer " + token}
        identity = a.get("/runtime/environment", headers=auth).json()["storage_id"]
        path = "/atelier/projects/pa/sequence/export_jobs"
        assert a.post(path, json=_create_payload(), headers={"X-LumenX-Environment": identity}).status_code == 401
        wrong = {**auth, "X-LumenX-Environment": "11111111-1111-4111-8111-111111111111"}
        assert a.post(path, json=_create_payload(), headers=wrong).status_code == 409
        headers = {**auth, "X-LumenX-Environment": identity}
        ok = a.post(path, json=_create_payload("iso"), headers=headers)
        assert ok.status_code == 200, ok.text
        assert b.get("/atelier/projects/pa/sequence/export_jobs/by_request/iso").status_code == 404
