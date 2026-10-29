"""HTTP contract for independent Atelier candidate judgments."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from src.apps.atelier.candidate_judgment import AtelierCandidateJudgmentService
from src.apps.atelier.storage import AtelierStorage
from tests.test_atelier_generation_api import AdmissionFake, DispatcherFake, ExecutionFake, _client
from tests.test_atelier_generation_selection import _store


def _judgment_client(store: AtelierStorage) -> tuple[TestClient, AdmissionFake, ExecutionFake, DispatcherFake]:
    admission, execution, dispatcher = AdmissionFake(), ExecutionFake(), DispatcherFake()
    client = _client(
        admission=admission,
        execution=execution,
        dispatcher=dispatcher,
        judgment=AtelierCandidateJudgmentService(store, clock=lambda: 50.0, id_factory=lambda: "judgment-1"),
    )
    return client, admission, execution, dispatcher


def test_candidate_judgment_http_strict_body_ack_and_no_provider_dispatch(tmp_path):
    store = _store(tmp_path)
    client, admission, execution, dispatcher = _judgment_client(store)

    response = client.put(
        "/projects/p1/nodes/node-1/candidates/candidate-1/judgment",
        json={"expected_revision": 1, "verdict": "keep", "reason": "Best emotion"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["node_ids"] == ["node-1"]
    assert body["project"]["revision"] == 2
    assert body["project"]["candidate_judgments"] == [
        {
            "schema_version": 1,
            "id": "judgment-1",
            "project_id": "p1",
            "node_id": "node-1",
            "candidate_id": "candidate-1",
            "media_kind": "video",
            "verdict": "keep",
            "reason": "Best emotion",
            "created_at": 50.0,
        }
    ]
    assert admission.admitted == [] and execution.continues == [] and dispatcher.scheduled == []

    noop = client.put(
        "/projects/p1/nodes/node-1/candidates/candidate-1/judgment",
        json={"expected_revision": 2, "verdict": "keep", "reason": "Best emotion"},
    )
    assert noop.status_code == 200, noop.text
    assert noop.json()["project"]["revision"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_revision": True, "verdict": "keep", "reason": ""},
        {"expected_revision": 1, "verdict": "maybe", "reason": ""},
        {"expected_revision": 1, "verdict": "keep", "reason": "", "provider": "dashscope"},
    ],
)
def test_candidate_judgment_http_rejects_coercion_unknown_fields_and_authority(tmp_path, payload):
    store = _store(tmp_path)
    before = store.path.read_bytes()
    client, _admission, _execution, _dispatcher = _judgment_client(store)

    response = client.put("/projects/p1/nodes/node-1/candidates/candidate-1/judgment", json=payload)

    assert response.status_code == 422
    assert store.path.read_bytes() == before


def test_candidate_judgment_real_application_conflict_is_409_without_mutation(tmp_path):
    from src.apps.comic_gen.api import create_app

    store = _store(tmp_path)
    store.close()
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        route = "/atelier/projects/p1/nodes/node-1/candidates/candidate-1/judgment"
        first = client.put(route, json={"expected_revision": 1, "verdict": "keep", "reason": ""})
        assert first.status_code == 200, first.text
        conflict = client.put(route, json={"expected_revision": 1, "verdict": "reject", "reason": ""})
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "storage_conflict"
        saved = client.get("/atelier/projects/p1").json()
        assert saved["candidate_judgments"][0]["verdict"] == "keep"
        assert len(saved["candidate_judgments"]) == 1
