from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from src.apps.atelier.api import router
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


class Sequencer:
    def __init__(self):
        self.now = 200.0
        self.ids = 0

    def clock(self) -> float:
        self.now += 1.0
        return self.now

    def id_factory(self) -> str:
        self.ids += 1
        return f"atelier-{self.ids}"


def make_client(
    tmp_path, *, sources: set[str] | None = None
) -> tuple[TestClient, AtelierStorage]:
    seq = Sequencer()
    storage = AtelierStorage(tmp_path)
    app = FastAPI()
    app.state.atelier_service = AtelierService(
        storage,
        source_project_exists=lambda source_id: source_id in (sources or set()),
        clock=seq.clock,
        id_factory=seq.id_factory,
    )

    @app.exception_handler(StorageError)
    async def storage_error_response(_request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse(
            status_code=409 if conflict else 503,
            content={
                "code": "storage_conflict" if conflict else "storage_unavailable",
                "detail": "The saved version changed; reload and retry"
                if conflict
                else "Saving is unavailable; restart the application before continuing",
                "commit_uncertain": exc.commit_uncertain,
            },
        )

    app.include_router(router, prefix="/atelier")
    return TestClient(app), storage


def test_http_crud_with_real_storage_and_restart(tmp_path):
    client, storage = make_client(tmp_path, sources={"studio-1"})

    created = client.post(
        "/atelier/projects",
        json={"title": "First", "description": "Desc", "source_project_id": "studio-1"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["id"] == "atelier-1"
    assert body["revision"] == 0
    assert body["agent_policy"]["approval_mode"] == "untrusted"

    listed = client.get("/atelier/projects")
    assert listed.status_code == 200
    assert [project["id"] for project in listed.json()] == ["atelier-1"]

    updated = client.put(
        "/atelier/projects/atelier-1",
        json={"expected_revision": 0, "title": "Second", "source_project_id": None},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 1
    assert updated.json()["source_project_id"] is None

    storage.close()
    restarted = AtelierStorage(tmp_path)
    assert restarted.get_project("atelier-1").title == "Second"
    restarted.close()


def test_http_validation_rejects_unknown_null_bool_and_missing_source_without_commit(
    tmp_path,
):
    client, storage = make_client(tmp_path, sources={"studio-1"})

    cases = [
        ("/atelier/projects", {"title": "Bad", "unknown": True}),
        ("/atelier/projects", {"title": None}),
        ("/atelier/projects", {"title": "Bad", "source_project_id": "missing"}),
        ("/atelier/projects", {"title": "Bad", "source_project_id": "   "}),
    ]
    for path, payload in cases:
        response = client.post(path, json=payload)
        assert response.status_code in {404, 422}
    assert storage.list_projects() == []

    created = client.post("/atelier/projects", json={"title": "First"}).json()
    update_cases = [
        {"expected_revision": 0, "title": None},
        {"expected_revision": 0, "description": None},
        {"expected_revision": True, "title": "Bool"},
        {"expected_revision": 0, "extra": "field"},
    ]
    for payload in update_cases:
        response = client.put(f"/atelier/projects/{created['id']}", json=payload)
        assert response.status_code == 422
    assert storage.get_project(created["id"]).revision == 0


def test_http_conflict_notfound_and_storage_failure_mapping(tmp_path, monkeypatch):
    client, storage = make_client(tmp_path)
    created = client.post("/atelier/projects", json={"title": "First"}).json()

    assert (
        client.put(
            f"/atelier/projects/{created['id']}",
            json={"expected_revision": 9, "title": "Late"},
        ).status_code
        == 409
    )
    assert client.get("/atelier/projects/missing").status_code == 404
    assert (
        client.request(
            "DELETE", "/atelier/projects/missing", json={"expected_revision": 0}
        ).status_code
        == 404
    )
    assert (
        client.request(
            "DELETE",
            f"/atelier/projects/{created['id']}",
            json={"expected_revision": True},
        ).status_code
        == 422
    )

    def fail_commit(_next_projects):
        raise StorageError("disk unavailable")

    monkeypatch.setattr(storage, "_commit", fail_commit)
    response = client.put(
        f"/atelier/projects/{created['id']}",
        json={"expected_revision": 0, "title": "Lost"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"
    assert storage.get_project(created["id"]).title == "First"


def test_router_requires_injected_service(tmp_path):
    app = FastAPI()
    app.include_router(router, prefix="/atelier")
    client = TestClient(app)

    response = client.get("/atelier/projects")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "atelier_service_unavailable"
