from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.apps.atelier.api import router
from src.apps.atelier import media_upload
from src.apps.atelier.media_upload import ManagedUpload
from src.apps.atelier.models import AtelierAgentPolicy, AtelierNode, AtelierProject
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class Sequencer:
    def __init__(self):
        self.now = 80.0
        self.ids = 0

    def clock(self) -> float:
        self.now += 1.0
        return self.now

    def id_factory(self) -> str:
        self.ids += 1
        return f"id-{self.ids}"


class FakeUploader:
    def __init__(self):
        self.calls = 0

    def __call__(self, chunks, *, filename: str, content_type: str | None) -> ManagedUpload:
        self.calls += 1
        assert list(chunks) == [b"same"]
        return ManagedUpload(url="uploads/upload.png", media_kind="image", size_bytes=4, sha256="a" * 64, width=12, height=8)

    def discard(self, _managed: ManagedUpload) -> None:
        pass


def make_client(tmp_path):
    seq = Sequencer()
    storage = AtelierStorage(tmp_path)
    storage.create_project(
        AtelierProject(
            id="p1",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            agent_policy=AtelierAgentPolicy(updated_at=1.0),
            nodes=[AtelierNode(id="target", project_id="p1", type="video")],
        )
    )
    uploader = FakeUploader()
    app = FastAPI()
    app.state.atelier_service = AtelierService(
        storage,
        clock=seq.clock,
        id_factory=seq.id_factory,
        media_uploader=uploader,
        media_discarder=uploader.discard,
    )

    @app.exception_handler(StorageError)
    async def storage_error_response(_request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse(
            {"code": "storage_conflict" if conflict else "storage_unavailable", "commit_uncertain": exc.commit_uncertain},
            status_code=409 if conflict else 503,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(_request, _exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.include_router(router, prefix="/atelier")
    return TestClient(app), storage, uploader


def test_raw_upload_create_node_endpoint_and_receipt_readback(tmp_path):
    client, storage, uploader = make_client(tmp_path)
    response = client.post(
        "/atelier/projects/p1/uploads/nodes",
        params={
            "expected_revision": "0",
            "client_request_id": "req-1",
            "filename": "hero.png",
            "node_type": "image",
            "title": "Hero",
            "x": "10",
        },
        content=b"same",
        headers={"content-type": "image/png"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["node_ids"] == ["id-2"]
    assert body["receipt_id"] == "id-1"
    assert body["replayed"] is False
    assert body["project"]["node_upload_receipts"][0]["client_request_id"] == "req-1"
    assert uploader.calls == 1

    receipt = client.get("/atelier/projects/p1/uploads/receipts/req-1")
    assert receipt.status_code == 200
    readback = receipt.json()
    assert readback["status"] == "found"
    assert readback["receipt"]["receipt_id"] == "id-1"
    assert readback["project"]["id"] == "p1"
    assert storage.get_project("p1").revision == 1

    missing = client.get("/atelier/projects/p1/uploads/receipts/missing")
    assert missing.status_code == 404


def test_raw_upload_reference_attach_endpoint(tmp_path):
    client, _storage, _uploader = make_client(tmp_path)
    response = client.post(
        "/atelier/projects/p1/uploads/references/target",
        params={"expected_revision": "0", "client_request_id": "ref-1", "filename": "ref.png", "title": "Ref"},
        content=b"same",
        headers={"content-type": "image/png"},
    )

    assert response.status_code == 200
    assert response.json()["node_ids"] == ["target", "id-2"]
    target = response.json()["project"]["nodes"][0]
    assert target["data"]["reference_node_ids"] == ["id-2"]
    assert target["data"]["reference_image_urls"] == ["uploads/upload.png"]


def test_upload_query_contract_rejects_unknown_fields_prompt_and_duplicate_params_without_uploading(tmp_path):
    client, storage, uploader = make_client(tmp_path)
    bad_cases = [
        {"expected_revision": "0", "client_request_id": "req", "filename": "a.png", "node_type": "image", "prompt": "no"},
        {"expected_revision": "0", "client_request_id": "req", "filename": "a.png", "node_type": "image", "unknown": "x"},
        {"expected_revision": "true", "client_request_id": "req", "filename": "a.png", "node_type": "image"},
        {"expected_revision": "0", "client_request_id": "bad space", "filename": "a.png", "node_type": "image"},
    ]
    for params in bad_cases:
        response = client.post("/atelier/projects/p1/uploads/nodes", params=params, content=b"same", headers={"content-type": "image/png"})
        assert response.status_code == 422
    duplicate = client.post(
        "/atelier/projects/p1/uploads/nodes?expected_revision=0&expected_revision=0&client_request_id=req&filename=a.png&node_type=image",
        content=b"same",
        headers={"content-type": "image/png"},
    )
    assert duplicate.status_code == 422
    assert uploader.calls == 0
    assert storage.get_project("p1").revision == 0


def test_chunked_upload_over_limit_rejected_before_service(tmp_path, monkeypatch):
    from src.apps.atelier import api as atelier_api

    monkeypatch.setitem(atelier_api._RAW_UPLOAD_MAX_BYTES_BY_KIND, "image", 3)
    client, storage, uploader = make_client(tmp_path)
    response = client.post(
        "/atelier/projects/p1/uploads/nodes",
        params={"expected_revision": "0", "client_request_id": "req", "filename": "a.png", "node_type": "image"},
        content=b"same",
        headers={"content-type": "image/png"},
    )
    assert response.status_code == 413
    assert uploader.calls == 0
    assert storage.get_project("p1").revision == 0


def test_spool_upload_body_writes_and_fsyncs_off_event_loop_thread(monkeypatch):
    from src.apps.atelier import api as atelier_api

    events: list[str] = []
    started = threading.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()
    write_thread_ids: list[int] = []
    fsync_thread_ids: list[int] = []

    class FakeTarget:
        def __init__(self, fd: int):
            self._fd = fd

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            os.close(self._fd)

        def write(self, data: bytes) -> int:
            assert data == b"same"
            write_thread_ids.append(threading.get_ident())
            events.append("write-start")
            started.set()
            release.wait(timeout=0.2)
            events.append("write-end")
            return len(data)

        def flush(self) -> None:
            events.append("flush")

        def fileno(self) -> int:
            return self._fd

    class FakeRequest:
        async def stream(self):
            yield b"same"

    def fake_fdopen(fd: int, _mode: str):
        return FakeTarget(fd)

    def fake_fsync(_fd: int) -> None:
        fsync_thread_ids.append(threading.get_ident())
        events.append("fsync")

    async def run_spool():
        task = asyncio.create_task(atelier_api._spool_upload_body(FakeRequest(), filename="pixel.png"))
        await asyncio.to_thread(started.wait, 1.0)
        events.append("loop-tick")
        release.set()
        path = await task
        path.unlink()

    monkeypatch.setattr(atelier_api.os, "fdopen", fake_fdopen)
    monkeypatch.setattr(atelier_api.os, "fsync", fake_fsync)

    asyncio.run(run_spool())

    assert write_thread_ids and all(thread_id != loop_thread for thread_id in write_thread_ids)
    assert fsync_thread_ids and all(thread_id != loop_thread for thread_id in fsync_thread_ids)
    assert events.index("loop-tick") < events.index("write-end")


def make_real_upload_client(tmp_path):
    seq = Sequencer()
    storage = AtelierStorage(tmp_path)
    storage.create_project(
        AtelierProject(
            id="p1",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            agent_policy=AtelierAgentPolicy(updated_at=1.0),
        )
    )
    owner = SimpleNamespace(root=tmp_path, lease=storage._writer_lock, lock=RLock(), closed=False)

    @contextmanager
    def guard():
        with owner.lock:
            if owner.closed:
                raise StorageError("Runtime is closed")
            owner.lease.assert_owned()
            yield

    def upload(chunks, *, filename: str, content_type: str | None) -> ManagedUpload:
        return media_upload.save_managed_upload(
            chunks,
            filename=filename,
            content_type=content_type,
            data_dir=tmp_path,
            writer_lock=owner.lease,
            storage_guard=guard,
            upload_id_factory=lambda: "fixture-upload",
        )

    app = FastAPI()
    app.state.atelier_service = AtelierService(
        storage,
        clock=seq.clock,
        id_factory=seq.id_factory,
        media_uploader=upload,
        media_discarder=lambda managed: media_upload.discard_managed_upload(
            managed, data_dir=tmp_path, writer_lock=owner.lease, storage_guard=guard
        ),
    )

    @app.exception_handler(StorageError)
    async def storage_error_response(_request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse(
            {"code": "storage_conflict" if conflict else "storage_unavailable", "commit_uncertain": exc.commit_uncertain},
            status_code=409 if conflict else 503,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(_request, _exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.include_router(router, prefix="/atelier")
    return TestClient(app)


def test_upload_ack_matches_shared_frontend_contract_fixture(tmp_path):
    client = make_real_upload_client(tmp_path)
    response = client.post(
        "/atelier/projects/p1/uploads/nodes",
        params={
            "expected_revision": "0",
            "client_request_id": "fixture-req",
            "filename": "pixel.png",
            "node_type": "image",
            "title": "Pixel",
            "x": "4",
            "y": "5",
        },
        content=PNG_1X1,
        headers={"content-type": "image/png"},
    )
    assert response.status_code == 200
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/atelier-upload-ack.json"
    assert response.json() == json.loads(fixture.read_text(encoding="utf-8"))
