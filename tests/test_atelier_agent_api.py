from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.atelier.agent_api import router
from src.apps.atelier.agent_service import AtelierAgentReadOnlyService
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


def _project(project_id: str, title: str) -> AtelierProject:
    return AtelierProject(
        id=project_id,
        title=title,
        created_at=100.0,
        updated_at=100.0,
        nodes=[
            AtelierNode(id="img1", project_id=project_id, type="image", media_urls=["uploads/frame.png"]),
            AtelierNode(
                id="video1",
                project_id=project_id,
                type="video",
                prompt=f"prompt for {title}",
                data={"reference_node_ids": ["img1"]},
            ),
        ],
    )


def _client(tmp_path, project_id: str = "p1", title: str = "Canvas") -> tuple[TestClient, AtelierStorage]:
    storage = AtelierStorage(tmp_path)
    storage.create_project(_project(project_id, title))
    app = FastAPI()
    app.state.atelier_agent_service = AtelierAgentReadOnlyService(storage)

    @app.exception_handler(StorageError)
    async def storage_error_response(_request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse(status_code=409 if conflict else 503, content={"code": "storage_conflict" if conflict else "storage_unavailable"})

    app.include_router(router, prefix="/atelier")
    return TestClient(app), storage


def test_http_routes_use_app_owned_agent_service_instances(tmp_path):
    client_a, _storage_a = _client(tmp_path / "a", title="Canvas A")
    client_b, _storage_b = _client(tmp_path / "b", title="Canvas B")

    package_a = client_a.post("/atelier/projects/p1/agent/planner_package", json={"selected_node_id": "video1"})
    package_b = client_b.post("/atelier/projects/p1/agent/planner_package", json={"selected_node_id": "video1"})

    assert package_a.status_code == 200
    assert package_b.status_code == 200
    assert package_a.json()["project_snapshot"]["title"] == "Canvas A"
    assert package_b.json()["project_snapshot"]["title"] == "Canvas B"


def test_http_missing_service_is_503(tmp_path):
    app = FastAPI()
    app.include_router(router, prefix="/atelier")
    client = TestClient(app)

    response = client.get("/atelier/projects/p1/agent/tools")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "atelier_agent_unavailable"


def test_http_validation_rejects_forbidden_fields_without_state_write(tmp_path):
    client, storage = _client(tmp_path)
    before = storage.get_project("p1").model_dump(mode="json")

    bad_package = client.post(
        "/atelier/projects/p1/agent/planner_package",
        json={"selected_node_id": "video1", "unknown": True},
    )
    bad_plan = client.post(
        "/atelier/projects/p1/agent/plan",
        json={"user_message": "生成视频", "selected_node_id": "video1", "extra": "field"},
    )

    assert bad_package.status_code == 422
    assert bad_plan.status_code == 422
    assert storage.get_project("p1").model_dump(mode="json") == before


def test_http_plan_does_not_call_generation_services_or_provider(tmp_path):
    client, storage = _client(tmp_path)
    before = storage.get_project("p1").model_dump(mode="json")
    client.app.state.atelier_generation_admission = object()
    client.app.state.atelier_generation_execution = object()
    client.app.state.provider_recovery_runtime = object()

    response = client.post(
        "/atelier/projects/p1/agent/plan",
        json={"user_message": "生成视频", "selected_node_id": "video1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert storage.get_project("p1").model_dump(mode="json") == before


def test_http_unknown_project_and_cross_project_selection_are_rejected(tmp_path):
    client, storage = _client(tmp_path)
    p2 = _project("p2", "Other")
    p2.nodes[0].id = "image-p2"
    p2.nodes[1].id = "video-p2"
    p2.nodes[1].data["reference_node_ids"] = ["image-p2"]
    storage.create_project(p2)

    missing = client.get("/atelier/projects/missing/agent/tools")
    cross = client.post(
        "/atelier/projects/p1/agent/plan",
        json={"user_message": "生成视频", "selected_node_id": "video-p2"},
    )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "atelier_project_not_found"
    assert cross.status_code == 200
    assert cross.json()["status"] == "blocked"


def test_http_unknown_planner_and_forbidden_planner_input_block_without_secret_echo(tmp_path):
    client, _storage = _client(tmp_path)

    response = client.post(
        "/atelier/projects/p1/agent/plan",
        json={
            "user_message": "生成视频",
            "selected_node_id": "video1",
            "planner_input": {"secret": "SECRET_INPUT"},
        },
    )
    unknown = client.post(
        "/atelier/projects/p1/agent/plan",
        json={"user_message": "生成视频", "selected_node_id": "video1", "planner": "model_adapter"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert "SECRET_INPUT" not in response.text
    assert unknown.status_code == 200
    assert unknown.json()["status"] == "blocked"


def test_real_create_app_mounts_agent_routes_and_keeps_instances_isolated(tmp_path):
    root_a, root_b = tmp_path / "real-a", tmp_path / "real-b"
    app_a = create_app(data_dir=root_a, load_config=False, recover_orphan_tasks=False)
    app_b = create_app(data_dir=root_b, load_config=False, recover_orphan_tasks=False)
    with TestClient(app_a) as a, TestClient(app_b) as b:
        app_a.state.atelier_storage.create_project(_project("p1", "Real A"))
        app_b.state.atelier_storage.create_project(_project("p1", "Real B"))
        before_a = (root_a / "atelier_projects.json").read_bytes()
        before_b = (root_b / "atelier_projects.json").read_bytes()

        tools = a.get("/atelier/projects/p1/agent/tools")
        package_a = a.post("/atelier/projects/p1/agent/planner_package", json={"selected_node_id": "video1"})
        package_b = b.post("/atelier/projects/p1/agent/planner_package", json={"selected_node_id": "video1"})

        assert tools.status_code == 200, tools.text
        assert package_a.status_code == 200, package_a.text
        assert package_b.status_code == 200, package_b.text
        assert package_a.json()["project_snapshot"]["title"] == "Real A"
        assert package_b.json()["project_snapshot"]["title"] == "Real B"
        assert (root_a / "atelier_projects.json").read_bytes() == before_a
        assert (root_b / "atelier_projects.json").read_bytes() == before_b


def test_real_create_app_remote_agent_routes_require_access_authority(tmp_path):
    token = "test-agent-access-token-12345678901234567890"
    app = create_app(
        data_dir=tmp_path,
        load_config=False,
        recover_orphan_tasks=False,
        access_mode="remote",
        access_token=token,
    )
    with TestClient(app) as client:
        app.state.atelier_storage.create_project(_project("p1", "Remote"))
        before = (tmp_path / "atelier_projects.json").read_bytes()

        denied = client.get("/atelier/projects/p1/agent/tools")
        allowed = client.get("/atelier/projects/p1/agent/tools", headers={"Authorization": f"Bearer {token}"})

        assert denied.status_code == 401
        assert allowed.status_code == 200, allowed.text
        assert (tmp_path / "atelier_projects.json").read_bytes() == before
