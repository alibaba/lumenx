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
        self.now = 30.0
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
        return JSONResponse({"code": "storage_conflict" if conflict else "storage_unavailable"}, status_code=409 if conflict else 503)

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(_request, _exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.include_router(router, prefix="/atelier")
    return TestClient(app)


def test_node_api_create_update_batch_and_region(tmp_path):
    client = make_client(tmp_path)
    project = client.post("/atelier/projects", json={"title": "Canvas"}).json()

    created = client.post(
        f"/atelier/projects/{project['id']}/nodes",
        json={"expected_revision": 0, "title": "A", "x": 1, "y": 2, "width": 100, "height": 80},
    )
    assert created.status_code == 200
    node_id = created.json()["node_ids"][0]
    assert created.json()["project"]["revision"] == 1

    text = client.put(
        f"/atelier/projects/{project['id']}/nodes/{node_id}/text",
        json={"expected_revision": 1, "title": "B", "prompt": "P"},
    )
    assert text.status_code == 200
    assert text.json()["project"]["nodes"][0]["title"] == "B"

    body = client.put(
        f"/atelier/projects/{project['id']}/nodes/{node_id}/body",
        json={"expected_revision": 2, "body": "copy"},
    )
    assert body.status_code == 200
    assert body.json()["project"]["nodes"][0]["data"]["body"] == "copy"

    layout = client.put(
        f"/atelier/projects/{project['id']}/nodes/layout",
        json={"expected_revision": 3, "updates": [{"node_id": node_id, "x": 3, "y": 4, "width": 120, "height": 90}]},
    )
    assert layout.status_code == 200
    assert layout.json()["node_ids"] == [node_id]

    region = client.post(
        f"/atelier/projects/{project['id']}/regions/wrap",
        json={"expected_revision": 4, "title": "Group", "member_node_ids": [node_id], "color": "#111111", "collapsed": False},
    )
    assert region.status_code == 200
    assert region.json()["project"]["revision"] == 5
    region_id = region.json()["node_ids"][0]

    appearance = client.put(
        f"/atelier/projects/{project['id']}/regions/{region_id}/appearance",
        json={"expected_revision": 5, "color": "#222222", "collapsed": True},
    )
    assert appearance.status_code == 200
    assert appearance.json()["project"]["revision"] == 6


def test_node_api_strict_validation_and_conflict(tmp_path):
    client = make_client(tmp_path)
    project = client.post("/atelier/projects", json={"title": "Canvas"}).json()
    assert client.post(f"/atelier/projects/{project['id']}/nodes", json={"expected_revision": True}).status_code == 422
    assert client.post(
        f"/atelier/projects/{project['id']}/nodes",
        content=b'{"expected_revision":0,"x":NaN}',
        headers={"content-type": "application/json"},
    ).status_code == 422
    assert client.post(
        f"/atelier/projects/{project['id']}/nodes",
        content=b'{"expected_revision":0,"x":100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000}',
        headers={"content-type": "application/json"},
    ).status_code == 422
    created = client.post(f"/atelier/projects/{project['id']}/nodes", json={"expected_revision": 0, "title": "A"}).json()
    node_id = created["node_ids"][0]
    assert client.put(f"/atelier/projects/{project['id']}/nodes/{node_id}/text", json={"expected_revision": 0, "title": "late"}).status_code == 409
