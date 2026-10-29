from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.apps.atelier.api import router
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


class Sequencer:
    def __init__(self):
        self.now = 10.0
        self.ids = 0

    def clock(self) -> float:
        self.now += 1.0
        return self.now

    def id_factory(self) -> str:
        self.ids += 1
        return f"id-{self.ids}"


def make_client(tmp_path) -> TestClient:
    seq = Sequencer()
    app = FastAPI()
    app.state.atelier_service = AtelierService(AtelierStorage(tmp_path), clock=seq.clock, id_factory=seq.id_factory)

    @app.exception_handler(StorageError)
    async def storage_error_response(_request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse({"code": "storage_conflict" if conflict else "storage_unavailable", "detail": str(exc)}, status_code=409 if conflict else 503)

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(_request, _exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.include_router(router, prefix="/atelier")
    return TestClient(app)


def test_node_delete_restore_http_contract_and_strict_payload(tmp_path):
    client = make_client(tmp_path)
    project = client.post("/atelier/projects", json={"title": "Canvas"}).json()
    created = client.post(f"/atelier/projects/{project['id']}/nodes", json={"expected_revision": 0, "title": "A"}).json()
    node_id = created["node_ids"][0]

    assert client.post(f"/atelier/projects/{project['id']}/nodes/delete", json={"expected_revision": True, "node_ids": [node_id]}).status_code == 422
    assert client.post(f"/atelier/projects/{project['id']}/nodes/delete", json={"expected_revision": 1, "node_ids": [node_id], "extra": 1}).status_code == 422

    deleted = client.post(f"/atelier/projects/{project['id']}/nodes/delete", json={"expected_revision": 1, "node_ids": [node_id]})
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["node_ids"] == [node_id]
    assert body["receipt_id"]
    assert body["project"]["node_delete_receipts"][0]["base_revision"] == 1

    restored = client.post(f"/atelier/projects/{project['id']}/nodes/restore", json={"expected_revision": 2, "receipt_id": body["receipt_id"]})
    assert restored.status_code == 200
    assert restored.json()["restored_node_ids"] == [node_id]
    assert restored.json()["skips"] == []

    duplicate = client.post(f"/atelier/projects/{project['id']}/nodes/restore", json={"expected_revision": 3, "receipt_id": body["receipt_id"]})
    assert duplicate.status_code == 409
