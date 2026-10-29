from __future__ import annotations

import io
import threading
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.apps.atelier.project_package_api import router as package_router
import src.apps.atelier.project_package_service as package_service_module
from src.apps.atelier.project_package_service import AtelierProjectPackageService
from src.apps.atelier.storage import AtelierStorage
from src.apps.comic_gen.api import create_app
from src.utils.json_storage import StorageConflictError, StorageError
import pytest

from tests.test_atelier_project_package import _mutate_package_json, _source_project, _write_source_media


def _direct_app(root: Path) -> tuple[TestClient, AtelierStorage]:
    storage = AtelierStorage(root)
    app = FastAPI()
    app.state.atelier_project_package_service = AtelierProjectPackageService(
        storage,
        storage_id="target-store",
    )
    app.include_router(package_router, prefix="/atelier")
    return TestClient(app), storage


def _seed_source(storage: AtelierStorage, root: Path):
    _write_source_media(root)
    return storage.create_project(_source_project())


def _export(client: TestClient, project_id: str) -> bytes:
    response = client.get(f"/atelier/projects/{project_id}/package/export")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment" in response.headers["content-disposition"]
    assert zipfile.is_zipfile(io.BytesIO(response.content))
    return response.content


def test_http_roundtrip_duplicate_import_edit_readback_and_reexport(tmp_path):
    client, storage = _direct_app(tmp_path)
    source = _seed_source(storage, tmp_path)
    source_before = storage.get_project(source.id).model_dump(mode="json")

    package = _export(client, source.id)
    first = client.post(
        "/atelier/projects/package/import",
        content=package,
        headers={"content-type": "application/zip"},
    )
    second = client.post(
        "/atelier/projects/package/import",
        content=package,
        headers={"content-type": "application/octet-stream"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_project = first.json()
    second_project = second.json()
    assert first_project["id"] != source.id
    assert second_project["id"] not in {source.id, first_project["id"]}
    assert first_project["import_provenance"]["source_project_id"] == source.id
    assert first_project["exports"][0]["imported_proof"]["project_id"] == first_project["id"]
    assert first_project["agent_turns"] == []
    assert first_project["generation_attempts"] == []

    storage.apply_project_command(
        first_project["id"],
        expected_revision=0,
        command=lambda project: setattr(project, "title", "Edited imported copy"),
    )
    edited = storage.get_project(first_project["id"])
    assert edited.title == "Edited imported copy"
    assert storage.get_project(source.id).model_dump(mode="json") == source_before
    assert zipfile.is_zipfile(io.BytesIO(_export(client, first_project["id"])))


def test_package_routes_are_protected_by_remote_access(tmp_path):
    token = "test-package-access-token-12345678901234567890"
    app = create_app(
        data_dir=tmp_path,
        load_config=False,
        recover_orphan_tasks=False,
        access_mode="remote",
        access_token=token,
    )
    with TestClient(app) as client:
        _write_source_media(tmp_path)
        app.state.atelier_storage.create_project(_source_project())
        denied = client.get("/atelier/projects/source-project/package/export")
        allowed = client.get(
            "/atelier/projects/source-project/package/export",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["cache-control"] == "no-store"


def test_http_errors_are_opaque_for_missing_invalid_type_and_invalid_zip(tmp_path):
    client, storage = _direct_app(tmp_path)
    _seed_source(storage, tmp_path)

    missing = client.get("/atelier/projects/missing/package/export")
    wrong_type = client.post(
        "/atelier/projects/package/import",
        content=b"not a package",
        headers={"content-type": "text/plain"},
    )
    invalid = client.post(
        "/atelier/projects/package/import",
        content=b"not a zip",
        headers={"content-type": "application/zip"},
    )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "atelier_project_not_found"
    assert wrong_type.status_code == 422
    assert wrong_type.json()["detail"]["code"] == "atelier_project_package_invalid_request"
    assert wrong_type.headers["cache-control"] == "no-store"
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "atelier_project_package_invalid"
    assert b"not a zip" not in invalid.content


@pytest.mark.parametrize(
    "mutate",
    [
        lambda project_payload: project_payload.update({"candidate_judgments": None}),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-kind-list",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": ["video"],
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": ["judgment-list-id"],
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-list-node",
                "project_id": project_payload["id"],
                "node_id": ["video-old"],
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-list-candidate",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": ["candidate-old"],
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).extend(
            [
                {
                    "schema_version": 1,
                    "id": "judgment-1",
                    "project_id": project_payload["id"],
                    "node_id": "video-old",
                    "candidate_id": "candidate-old",
                    "media_kind": "video",
                    "verdict": "keep",
                    "reason": "",
                    "created_at": 12.0,
                },
                {
                    "schema_version": 1,
                    "id": "judgment-2",
                    "project_id": project_payload["id"],
                    "node_id": "video-old",
                    "candidate_id": "candidate-old",
                    "media_kind": "video",
                    "verdict": "reject",
                    "reason": "",
                    "created_at": 13.0,
                    "supersedes_judgment_id": ["judgment-1"],
                },
            ]
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-verdict-dict",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": {"value": "keep"},
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-surrogate-reason",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "\ud800",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-huge-created",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 10**400,
            }
        ),
    ],
)
def test_import_rejects_malformed_candidate_judgments_without_500_or_staging(tmp_path, mutate):
    client, storage = _direct_app(tmp_path)
    source = _seed_source(storage, tmp_path)
    damaged = _mutate_package_json(_export(client, source.id), "project.json", mutate)

    response = client.post(
        "/atelier/projects/package/import",
        content=damaged,
        headers={"content-type": "application/zip"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "atelier_project_package_invalid"
    imported_root = tmp_path / "video" / "imported"
    assert not imported_root.exists() or not any(imported_root.rglob("*"))


def test_import_rejects_oversize_declared_and_chunked_before_service(tmp_path, monkeypatch):
    client, _storage = _direct_app(tmp_path)
    client.app.state.atelier_project_package_max_bytes = 16
    called = False

    def unexpected_import(_data: bytes):
        nonlocal called
        called = True
        raise AssertionError("import should not run")

    monkeypatch.setattr(
        client.app.state.atelier_project_package_service,
        "import_project_package",
        unexpected_import,
    )
    declared = client.post(
        "/atelier/projects/package/import",
        content=b"x" * 17,
        headers={"content-type": "application/octet-stream"},
    )

    def chunks():
        yield b"x" * 8
        yield b"y" * 9

    chunked = client.post(
        "/atelier/projects/package/import",
        content=chunks(),
        headers={"content-type": "application/zip"},
    )

    negative_length = client.post(
        "/atelier/projects/package/import",
        content=b"x",
        headers={
            "content-type": "application/zip",
            "content-length": "-1",
        },
    )

    assert declared.status_code == 413
    assert declared.json()["code"] == "atelier_project_package_too_large"
    assert chunked.status_code == 413
    assert negative_length.status_code == 422
    assert negative_length.json()["detail"]["code"] == "atelier_project_package_invalid_request"
    assert called is False


def test_import_holds_storage_guard_across_prepare_and_commit(tmp_path, monkeypatch):
    client, storage = _direct_app(tmp_path)
    source = _seed_source(storage, tmp_path)
    package = _export(client, source.id)
    service = client.app.state.atelier_project_package_service
    real_prepare = package_service_module.prepare_import
    prepare_entered = threading.Event()
    allow_prepare = threading.Event()
    close_done = threading.Event()
    result: dict[str, object] = {}

    def slow_prepare(*args, **kwargs):
        prepare_entered.set()
        assert allow_prepare.wait(2.0)
        return real_prepare(*args, **kwargs)

    def run_import():
        try:
            result["project"] = service.import_project_package(package)
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            result["error"] = exc

    def run_close():
        storage.close()
        close_done.set()

    monkeypatch.setattr(package_service_module, "prepare_import", slow_prepare)
    importer = threading.Thread(target=run_import)
    importer.start()
    assert prepare_entered.wait(2.0)

    closer = threading.Thread(target=run_close)
    closer.start()
    time.sleep(0.05)
    assert not close_done.is_set()

    allow_prepare.set()
    importer.join(2.0)
    closer.join(2.0)

    assert close_done.is_set()
    assert "error" not in result
    assert result["project"].id != source.id


def test_storage_errors_are_opaque_no_store_for_import_and_export(tmp_path, monkeypatch):
    client, storage = _direct_app(tmp_path)
    source = _seed_source(storage, tmp_path)
    package = _export(client, source.id)
    service = client.app.state.atelier_project_package_service
    sentinel = f"sensitive-path::{tmp_path}"

    def conflict_export(_project_id):
        raise StorageConflictError(sentinel)

    monkeypatch.setattr(service, "export_project_package", conflict_export)
    conflict = client.get(f"/atelier/projects/{source.id}/package/export")
    assert conflict.status_code == 409
    assert conflict.json() == {
        "code": "atelier_storage_conflict",
        "detail": "Atelier storage changed; reload and retry",
    }
    assert conflict.headers["cache-control"] == "no-store"
    assert sentinel not in conflict.text

    def unavailable_export(_project_id):
        raise StorageError(sentinel, commit_uncertain=False)

    monkeypatch.setattr(service, "export_project_package", unavailable_export)
    unavailable = client.get(f"/atelier/projects/{source.id}/package/export")
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "atelier_storage_unavailable"
    assert unavailable.json()["commit_uncertain"] is False
    assert unavailable.headers["cache-control"] == "no-store"
    assert sentinel not in unavailable.text

    def conflict_import(_data):
        raise StorageConflictError(sentinel)

    monkeypatch.setattr(service, "import_project_package", conflict_import)
    import_conflict = client.post(
        "/atelier/projects/package/import",
        content=package,
        headers={"content-type": "application/zip"},
    )
    assert import_conflict.status_code == 409
    assert import_conflict.json()["code"] == "atelier_storage_conflict"
    assert import_conflict.headers["cache-control"] == "no-store"
    assert sentinel not in import_conflict.text


def test_known_storage_failure_rolls_back_prepared_import_media(tmp_path, monkeypatch):
    client, storage = _direct_app(tmp_path)
    source = _seed_source(storage, tmp_path)
    package = _export(client, source.id)

    def fail_commit(_next_projects):
        raise StorageError("synthetic known package save failure", commit_uncertain=False)

    monkeypatch.setattr(storage, "_commit", fail_commit)
    response = client.post(
        "/atelier/projects/package/import",
        content=package,
        headers={"content-type": "application/zip"},
    )

    assert response.status_code == 503
    assert response.json()["commit_uncertain"] is False
    imported_root = tmp_path / "video" / "imported"
    assert not imported_root.exists() or not any(
        path.is_file() for path in imported_root.rglob("*")
    )
    assert len(storage.list_projects()) == 1


def test_uncertain_storage_failure_preserves_import_media_for_restart_readback(
    tmp_path,
    monkeypatch,
):
    import src.apps.atelier.storage as storage_module

    client, storage = _direct_app(tmp_path)
    source = _seed_source(storage, tmp_path)
    package = _export(client, source.id)
    real_atomic_write_json = storage_module.atomic_write_json

    def write_then_uncertain(path, value):
        real_atomic_write_json(path, value)
        raise StorageError("synthetic uncertain package save failure", commit_uncertain=True)

    monkeypatch.setattr(storage_module, "atomic_write_json", write_then_uncertain)
    response = client.post(
        "/atelier/projects/package/import",
        content=package,
        headers={"content-type": "application/zip"},
    )

    assert response.status_code == 503
    assert response.json()["commit_uncertain"] is True
    imported_root = tmp_path / "video" / "imported"
    assert imported_root.exists()
    assert any(path.is_file() for path in imported_root.rglob("*"))

    storage.close()
    monkeypatch.setattr(storage_module, "atomic_write_json", real_atomic_write_json)
    restarted = AtelierStorage(tmp_path)
    try:
        projects = restarted.list_projects()
        imported = [project for project in projects if project.id != source.id]
        assert len(imported) == 1
        imported_project = imported[0]
        assert imported_project.import_provenance is not None
        assert (tmp_path / imported_project.exports[0].imported_proof.relative_path).exists()
    finally:
        restarted.close()
