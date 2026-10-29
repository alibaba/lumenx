"""Keep frontend Skill fixtures anchored to actual HTTP and stored turn output."""

import json
from pathlib import Path

from src.apps.atelier.agent_loop import DeterministicLoopPlan
from tests.test_atelier_agent_loop_api import BASE, START

pytest_plugins = ("tests.test_atelier_agent_loop_api",)


def test_frontend_product_skills_fixture_matches_http_and_persisted_snapshot(client):
    http, scheduled, root = client
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "frontend/src/__tests__/fixtures/atelier-product-skills-wire.json"
    )
    fixture = json.loads(fixture_path.read_text())
    catalog = http.get("/atelier/agent/product_skills")
    assert catalog.status_code == 200
    assert catalog.json() == fixture["catalog"]

    def planner(_context, **_kwargs):
        return DeterministicLoopPlan("Fixture read", [], "done")

    http.app.state.atelier_agent_loop_service._model_planner = planner
    response = http.post(BASE, json={
        **START,
        "client_turn_id": "product-skill-wire-1",
        "planner": "model_adapter",
        "skill_name": "story-scout",
        "max_iterations": 1,
    })
    assert response.status_code == 200, response.text
    context = response.json()["turn"]["loop_start_context"]
    assert context["schema_version"] == 2
    assert context["skill_snapshot"] == fixture["story_scout_snapshot"]
    persisted = http.app.state.atelier_storage.get_project("p").agent_turns[-1]
    assert persisted.loop_start_context.model_dump(mode="json") == context
    assert fixture["story_scout_snapshot"]["content_hash"] in (root / "atelier_projects.json").read_text()
    scheduled.assert_not_called()
