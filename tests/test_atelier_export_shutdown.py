"""A closing application terminates only its render and seals late writes."""

from contextlib import contextmanager
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.utils.json_storage import DirectoryWriterLock, StorageError
from tests.test_atelier_sequence_export_api import _app, _create_payload, _seed_project, _tool


def test_shutdown_terminates_owned_ffmpeg_and_cannot_write_after_owner_reopens(tmp_path):
    app = _app(tmp_path)
    entered = threading.Event()
    returned = threading.Event()
    tokens = []

    @contextmanager
    def held_render(_snapshot, *, media_scope, cancel_token):
        tokens.append(cancel_token)
        entered.set()
        try:
            # A real long-running encoder makes process ownership observable.
            cancel_token.run([
                _tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-re",
                "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=25", "-t", "60",
                "-f", "null", "-",
            ], timeout=65)
            pytest.fail("the owned encoder should have been cancelled on shutdown")
            yield  # pragma: no cover - contextmanager protocol, cancellation never yields.
        finally:
            returned.set()

    with TestClient(app) as client:
        _seed_project(app, tmp_path)
        service = app.state.atelier_sequence_export_service
        dispatcher = app.state.atelier_sequence_export_dispatcher
        storage = app.state.atelier_storage
        service._render_context = held_render
        response = client.post("/atelier/projects/p1/sequence/export_jobs", json=_create_payload("shutdown"))
        assert response.status_code == 200, response.text
        assert entered.wait(3)
        deadline = time.monotonic() + 3
        while tokens[0].current_pid is None and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = tokens[0].current_pid
        assert pid is not None
        os.kill(pid, 0)

    assert returned.wait(1)
    dispatcher.join(1)
    assert dispatcher.wait_for_idle(1)
    assert tokens[0].current_pid is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(StorageError):
        storage.get_project("p1")
    with DirectoryWriterLock(tmp_path):
        pass
    saved_bytes = (tmp_path / "atelier_projects.json").read_bytes()
    with TestClient(_app(tmp_path)) as reopened:
        readback = reopened.get("/atelier/projects/p1/sequence/export_jobs/by_request/shutdown")
        assert readback.status_code == 200, readback.text
        assert readback.json()["job"]["status"] in {"running", "cancelled"}
        assert readback.json()["project"]["exports"] == []
        assert (tmp_path / "atelier_projects.json").read_bytes() == saved_bytes
        assert dispatcher.wait_for_idle(1)
        assert (tmp_path / "atelier_projects.json").read_bytes() == saved_bytes
    assert not list((tmp_path / "video").glob("atelier_seq_*.mp4"))
