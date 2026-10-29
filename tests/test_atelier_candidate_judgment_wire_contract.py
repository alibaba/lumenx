"""Anchor client judgment fixtures to real HTTP and persisted projects."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.apps.atelier.candidate_judgment import AtelierCandidateJudgmentService
from src.apps.comic_gen.api import create_app
from tests.test_atelier_generation_ledger_storage import _project, _store_with_project


def test_candidate_judgment_client_fixture_matches_real_http(tmp_path):
    project = _project(status="completed")
    project.agent_policy.updated_at = 1.0
    store = _store_with_project(tmp_path, project)
    store.close()
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        app.state.atelier_candidate_judgment = AtelierCandidateJudgmentService(
            app.state.atelier_storage, clock=lambda: 50.0, id_factory=lambda: "judgment-wire-1",
        )
        before = client.get("/atelier/projects/p1")
        assert before.status_code == 200
        route = "/atelier/projects/p1/nodes/node-1/candidates/candidate-1/judgment"
        request = {"expected_revision": 1, "verdict": "keep", "reason": "Keep this performance"}
        response = client.put(route, json=request)
        assert response.status_code == 200, response.text
        noop = client.put(route, json={**request, "expected_revision": 2})
        assert noop.status_code == 200, noop.text
        assert noop.json() == response.json()
        after = client.get("/atelier/projects/p1")
        assert after.json() == response.json()["project"]
        wire = {"before": before.json(), "request": request, "ack": response.json(), "noop_ack": noop.json()}
        # Retained only when the isolated runner is invoked with --keep-temp;
        # normal verification never changes the committed fixture.
        (tmp_path / "actual-judgment-wire.json").write_text(json.dumps(wire, indent=2) + "\n")
        fixture = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/atelier-candidate-judgment-wire.json"
        assert wire == json.loads(fixture.read_text())
