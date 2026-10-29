"""The shared frontend export fixture must round-trip through backend storage."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.apps.atelier.export_history import DeleteExportRecordAck
from src.apps.atelier.export_jobs import SequenceExportJobAck
from src.apps.atelier.models import AtelierProject
from src.apps.atelier.storage import AtelierStorage, _canonical_project
from src.apps.comic_gen.api import create_app


def test_shared_export_job_fixture_round_trips_backend_storage_and_ack():
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/atelier-export-job-contract.json"
    raw = json.loads(fixture.read_text())
    project = _canonical_project(AtelierProject.model_validate(raw))
    normalized = json.loads(json.dumps(raw))
    for export in normalized["exports"]:
        export.setdefault("deletion", None)
    assert project.model_dump(mode="json") == normalized
    ack = SequenceExportJobAck(project=project, job=project.sequence_export_jobs[0], export_record=project.exports[0])
    wire = ack.model_dump(mode="json")
    assert wire["job"] == raw["sequence_export_jobs"][0]
    assert wire["export_record"] == normalized["exports"][0]
    assert wire["new_job_ids"] == []


def test_shared_export_deletion_retained_fixture_matches_backend_dto_storage_and_api(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/atelier-export-deletion-retained.json"
    raw = json.loads(fixture.read_text())
    project = _canonical_project(AtelierProject.model_validate(raw))
    assert project.model_dump(mode="json") == json.loads(json.dumps(raw))

    ack = DeleteExportRecordAck(project=project, export_record=project.exports[0], replayed=True)
    wire = ack.model_dump(mode="json")
    assert wire["export_record"] == raw["exports"][0]

    (tmp_path / "atelier_projects.json").write_text(json.dumps({raw["id"]: raw}), encoding="utf-8")
    store = AtelierStorage(tmp_path)
    stored_project = store.get_project(raw["id"])
    assert stored_project.model_dump(mode="json") == json.loads(json.dumps(raw))
    store.close()

    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        response = client.request(
            "DELETE",
            f"/atelier/projects/{raw['id']}/exports/{raw['exports'][0]['id']}",
            json={
                "expected_revision": 0,
                "client_request_id": raw["exports"][0]["deletion"]["client_request_id"],
                "delete_file": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["replayed"] is True
    assert body["export_record"] == raw["exports"][0]
    assert body["project"] == json.loads(json.dumps(raw))
