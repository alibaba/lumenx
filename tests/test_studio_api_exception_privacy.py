"""Studio HTTP errors expose approved feedback, never arbitrary exception text."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.pipeline import ProjectContextError
from src.apps.comic_gen.project_package import ProjectPackageError
from src.utils.json_storage import StorageConflictError


PRIVATE_DETAIL = "synthetic-secret-token /private/provider/config.json upstream traceback"
CONTINUE_BODY = {"expected_attempt_id": "attempt-1", "expected_fingerprint": "a" * 64}


@pytest.fixture
def entry():
    runtime = SimpleNamespace(
        export_project_package=Mock(),
        import_project_package=Mock(),
        admit_provider_video_tasks_with_receipt=Mock(),
        continue_accepted_provider_video_task=Mock(),
        motion_reference_service=SimpleNamespace(recover=Mock()),
        asset_image_service=SimpleNamespace(read_request=Mock()),
    )
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_pipeline] = lambda: runtime
    with TestClient(app) as client:
        yield runtime, client


@pytest.mark.parametrize("operation,error_type", [
    ("export", ValueError), ("export", ProjectPackageError),
    ("import", ValueError), ("import", ProjectPackageError),
])
@pytest.mark.parametrize("private_detail", [PRIVATE_DETAIL, "Invalid project package zip: " + PRIVATE_DETAIL])
def test_package_errors_keep_private_details_in_logs(entry, caplog, operation, error_type, private_detail):
    runtime, client = entry
    getattr(runtime, f"{operation}_project_package").side_effect = error_type(private_detail)
    response = (
        client.get("/projects/p/package/export") if operation == "export" else
        client.post("/projects/package/import", content=b"package", headers={"content-type": "application/zip"})
    )
    assert response.status_code == 422
    assert response.json() == {"code": "project_package_invalid", "detail": "Project package is invalid"}
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE_DETAIL not in response.text
    assert PRIVATE_DETAIL in caplog.text


def test_provider_admission_keeps_private_details_in_logs(entry, caplog):
    runtime, client = entry
    runtime.admit_provider_video_tasks_with_receipt.side_effect = ValueError(PRIVATE_DETAIL)
    response = client.post("/projects/p/provider_video_tasks", json={"client_request_id": "request-1"})
    assert response.status_code == 422
    assert response.json() == {
        "code": "provider_request_invalid",
        "detail": "Invalid provider video request; check the model, mode, parameters and references.",
    }
    assert PRIVATE_DETAIL not in response.text
    assert PRIVATE_DETAIL in caplog.text


@pytest.mark.parametrize("root", ["projects", "series"])
@pytest.mark.parametrize("error,status,detail,code", [
    (ValueError(PRIVATE_DETAIL), 422, "Invalid image request; check the model, parameters and references.", "studio_image_request_invalid"),
    (ValueError("studio_image_aspect_ratio_invalid: " + PRIVATE_DETAIL), 422, "Invalid image request; check the model, parameters and references.", "studio_image_request_invalid"),
    (ProjectContextError(PRIVATE_DETAIL, not_found=True), 404, "Image request target not found", None),
    (ProjectContextError(PRIVATE_DETAIL), 422, "Invalid image request target context", None),
])
def test_image_request_errors_keep_private_details_in_logs(entry, caplog, root, error, status, detail, code):
    runtime, client = entry
    runtime.asset_image_service.read_request.side_effect = error
    response = client.get(f"/{root}/p/asset_image_requests/request-1")
    expected = {"detail": detail}
    if code:
        expected["code"] = code
    assert response.status_code == status
    assert response.json() == expected
    assert PRIVATE_DETAIL not in response.text
    assert PRIVATE_DETAIL in caplog.text


@pytest.mark.parametrize("detail", [
    "Invalid project package zip",
    "Project package is missing required metadata",
    "Prepared scene image has no unique packaged target",
    "Prepared reference sheet has no unique packaged character",
    "Variant identity refers to different media",
])
def test_package_known_validation_feedback_is_preserved(entry, detail):
    runtime, client = entry
    runtime.export_project_package.side_effect = ProjectPackageError(detail)
    response = client.get("/projects/p/package/export")
    assert response.status_code == 422
    assert response.json() == {"code": "project_package_invalid", "detail": detail}


@pytest.mark.parametrize("detail", [
    "studio_image_aspect_ratio_invalid",
    "studio_image_base_prompt_required",
    "studio_three_view_selected_local_reference_required",
])
def test_image_known_validation_feedback_is_preserved(entry, detail):
    runtime, client = entry
    runtime.asset_image_service.read_request.side_effect = ValueError(detail)
    response = client.get("/projects/p/asset_image_requests/request-1")
    assert response.status_code == 422
    assert response.json() == {"code": "studio_image_request_invalid", "detail": detail}


@pytest.mark.parametrize("operation,code", [
    ("motion", "motion_reference_recover_not_allowed"), ("motion", "provider_operation_running"),
    ("continue", "provider_continue_not_allowed"), ("continue", "provider_operation_running"),
    ("admit", "provider_request_conflict"), ("admit", "provider_request_receipt_invalid"),
])
def test_provider_conflict_codes_and_status_are_preserved(entry, operation, code):
    runtime, client = entry
    if operation == "motion":
        runtime.motion_reference_service.recover.side_effect = StorageConflictError(code)
        response = client.post("/projects/p/motion_ref_requests/r/recover", json=CONTINUE_BODY)
    elif operation == "continue":
        runtime.continue_accepted_provider_video_task.side_effect = StorageConflictError(code)
        response = client.post("/projects/p/provider_video_tasks/t/continue", json=CONTINUE_BODY)
    else:
        runtime.admit_provider_video_tasks_with_receipt.side_effect = StorageConflictError(code)
        response = client.post("/projects/p/provider_video_tasks", json={"client_request_id": "request-1"})
    assert response.status_code == 409
    assert response.json() == {"code": code, "detail": code}


def test_package_too_large_remains_413_without_running_import(entry, monkeypatch):
    runtime, client = entry
    monkeypatch.setattr(api, "DEFAULT_MAX_PACKAGE_BYTES", 4)
    response = client.post("/projects/package/import", content=b"large", headers={"content-type": "application/zip"})
    assert response.status_code == 413
    assert response.json() == {"code": "project_package_too_large", "detail": "Project package is too large"}
    runtime.import_project_package.assert_not_called()
