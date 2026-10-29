from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.apps.atelier.api import router
from src.apps.atelier.models import AtelierNode, AtelierProject
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
    service = AtelierService(AtelierStorage(tmp_path), clock=seq.clock, id_factory=seq.id_factory)
    service.storage.create_project(
        AtelierProject(
            id="p1",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            nodes=[
                AtelierNode(id="target", project_id="p1", type="video"),
                AtelierNode(id="image", project_id="p1", type="image", media_urls=["image.png"]),
            ],
        )
    )
    app.state.atelier_service = service

    @app.exception_handler(StorageError)
    async def storage_error_response(_request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse({"code": "storage_conflict" if conflict else "storage_unavailable"}, status_code=409 if conflict else 503)

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(_request, _exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.include_router(router, prefix="/atelier")
    return TestClient(app)


def test_detach_source_identity_does_not_require_a_surviving_media_url(tmp_path):
    client = make_client(tmp_path)
    service = client.app.state.atelier_service

    def lose_source_media(project):
        project.nodes[0].data = {"reference_node_ids": ["image"], "reference_image_urls": ["uploads/old.png"]}
        project.nodes[1].media_urls = []
        project.nodes[1].data = {"parent_node_id": "target"}

    service.storage.apply_project_command("p1", expected_revision=0, command=lose_source_media)
    path = "/atelier/projects/p1/nodes/target/references/detach"
    result = client.post(path, json={"expected_revision": 1, "source_node_id": "image"})
    assert result.status_code == 200
    project = result.json()["project"]
    assert project["revision"] == 2
    assert project["nodes"][0]["data"]["reference_node_ids"] == []
    assert project["nodes"][1]["data"]["parent_node_id"] is None
    # Unproven legacy URLs are a separate explicit removal, not guessed ownership.
    assert project["nodes"][0]["data"]["reference_image_urls"] == ["uploads/old.png"]
    assert client.post(path, json={"expected_revision": 2}).status_code == 422
    assert client.post(path, json={"expected_revision": 2, "reference_url": " "}).status_code == 422
    assert client.post(path, json={"expected_revision": 2, "reference_url": "uploads/old.png"}).json()["project"]["nodes"][0]["data"]["reference_image_urls"] == []
    service.storage.close()


def test_reference_api_attach_detach_and_strict_validation(tmp_path):
    client = make_client(tmp_path)

    attached = client.post(
        "/atelier/projects/p1/nodes/target/references/attach",
        json={"expected_revision": 0, "source_node_id": "image"},
    )
    assert attached.status_code == 200
    assert attached.json()["node_ids"] == ["target", "image"]
    assert attached.json()["project"]["revision"] == 1
    assert attached.json()["project"]["nodes"][0]["data"]["reference_image_urls"] == ["image.png"]

    assert client.post(
        "/atelier/projects/p1/nodes/target/references/attach",
        json={"expected_revision": 1, "source_node_id": "image", "data": {}},
    ).status_code == 422
    assert client.post(
        "/atelier/projects/p1/nodes/target/references/attach",
        json={"expected_revision": True, "source_node_id": "image"},
    ).status_code == 422

    detached = client.post(
        "/atelier/projects/p1/nodes/target/references/detach",
        json={"expected_revision": 1, "reference_url": "image.png", "source_node_id": "image"},
    )
    assert detached.status_code == 200
    assert detached.json()["node_ids"] == ["target", "image"]
    assert detached.json()["project"]["nodes"][0]["data"]["reference_image_urls"] == []

    late = client.post(
        "/atelier/projects/p1/nodes/target/references/detach",
        json={"expected_revision": 1, "reference_url": "image.png"},
    )
    assert late.status_code == 409


def test_attach_pending_reference_api_persists_pending_graph_only(tmp_path):
    client = make_client(tmp_path)

    def replace_seed(project):
        project.nodes = [
            AtelierNode(id="target", project_id="p1", type="video"),
            AtelierNode(id="placeholder", project_id="p1", type="image"),
        ]

    client.app.state.atelier_service.storage.apply_project_command("p1", expected_revision=0, command=replace_seed)

    response = client.post(
        "/atelier/projects/p1/nodes/target/references/attach_pending",
        json={"expected_revision": 1, "source_node_id": "placeholder"},
    )

    assert response.status_code == 200
    target = next(node for node in response.json()["project"]["nodes"] if node["id"] == "target")
    assert target["data"] == {
        "reference_node_ids": ["placeholder"],
        "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "placeholder", "source_kind": "image", "status": "pending_media"}],
    }
