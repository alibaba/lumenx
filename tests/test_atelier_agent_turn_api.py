"""Actual app integration for durable Agent preview/approval/continue."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.atelier.agent_api import router
from src.apps.comic_gen.api import create_app
from tests.test_atelier_generation_app import install_provider_runtime, seed

BASE = "/atelier/projects/p/agent/turns"
START = {"expected_revision": 0, "client_turn_id": "turn-http-1", "user_message": "生成视频", "selected_node_id": "video"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        seed(app, tmp_path)
        install_provider_runtime(app, replace(app.state.provider_recovery_runtime, secret_resolver=lambda: "fixture-agent-secret"))
        scheduled = Mock(return_value=True)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", scheduled)
        yield http, scheduled, tmp_path


def _clear_provider_runtime(app):
    app.state.provider_recovery_runtime = None
    app.state.pipeline.provider_recovery_runtime = None
    app.state.provider_runtime_selector = None
    app.state.pipeline.provider_runtime_selector = None


def _identity(ack):
    call = ack["turn"]["tool_calls"][0]
    ref = call["preview_binding"]["agent_authorization_ref"]
    return {"expected_revision": ack["project"]["revision"], "turn_id": ack["turn"]["id"],
            "call_id": call["call_id"], "approval_id": ref["approval_id"], "authorization_hash": ref["authorization_hash"]}


def _start(client):
    response = client.post(BASE, json=START)
    assert response.status_code == 200, response.text
    return response.json()


def _approve_and_admit(client):
    start = _start(client)
    identity = _identity(start)
    approved = client.post(BASE + "/turn-http-1/approval", json={**identity, "decision": "approved"})
    assert approved.status_code == 200, approved.text
    payload = {**identity, "expected_revision": approved.json()["project"]["revision"],
               "client_request_id": start["turn"]["tool_calls"][0]["preview_binding"]["preview_public_record"]["raw_request"]["client_request_id"]}
    admitted = client.post(BASE + "/turn-http-1/continue", json=payload)
    assert admitted.status_code == 200, admitted.text
    return admitted, payload


def test_real_app_agent_preview_approval_and_continue_schedule_exactly_once(client):
    http, scheduled, root = client
    admitted, payload = _approve_and_admit(http)
    ack = admitted.json()
    assert ack["scheduled"] is True and ack["replayed"] is False
    generation = ack["generation"]
    scheduled.assert_called_once_with("p", generation["attempt"]["attempt_id"])
    assert generation["attempt"]["schema_version"] == 2
    node = next(n for n in ack["project"]["nodes"] if n["id"] == "video")
    assert node["media_urls"] == [] and "selected_candidate_id" not in node["data"]
    replay = http.post(BASE + "/turn-http-1/continue", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["generation"]["new_candidate_ids"] == []
    assert replay.json()["replayed"] and not replay.json()["scheduled"]
    assert scheduled.call_count == 1
    assert http.app.state.pipeline.read_committed_projects() == []
    assert "fixture-agent-secret" not in admitted.text
    assert "fixture-agent-secret" not in (root / "atelier_projects.json").read_text()


def test_start_replay_reads_saved_preview_after_source_and_secret_are_gone(client):
    http, scheduled, root = client
    first = _start(http)
    (root / "uploads/frame.png").unlink()
    _clear_provider_runtime(http.app)
    replay = http.post(BASE, json=START)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"]
    assert replay.json()["turn"] == first["turn"]
    readback = http.get(BASE + "/turn-http-1")
    assert readback.status_code == 200 and readback.json()["turn"] == first["turn"]
    scheduled.assert_not_called()


def test_saved_agent_attempt_survives_scheduler_failure_without_secret_leak(client):
    http, scheduled, _root = client
    scheduled.side_effect = RuntimeError("PRIVATE-SCHEDULER-DETAIL")
    admitted, payload = _approve_and_admit(http)
    assert admitted.json()["scheduled"] is False
    assert admitted.json()["schedule_error"]
    assert admitted.json()["generation"]["attempt"]["provider_execution"]["phase"] == "accepted"
    assert "PRIVATE-SCHEDULER-DETAIL" not in admitted.text
    replay = http.post(BASE + "/turn-http-1/continue", json=payload)
    assert replay.status_code == 200 and replay.json()["generation"]["new_candidate_ids"] == []
    assert scheduled.call_count == 1


def test_cancelled_preview_cannot_be_approved_or_admitted(client):
    http, scheduled, _root = client
    start = _start(http)
    canceled = http.post(BASE + "/turn-http-1/cancel", json={"turn_id": "turn-http-1"})
    assert canceled.status_code == 200 and canceled.json()["turn"]["status"] == "canceled"
    approval = http.post(BASE + "/turn-http-1/approval", json={**_identity(start), "decision": "approved"})
    assert approval.status_code == 409
    assert http.app.state.atelier_storage.get_project("p").generation_attempts == []
    scheduled.assert_not_called()


def test_route_identity_and_strict_fields_reject_before_any_write(client):
    http, scheduled, root = client
    start = _start(http)
    before = (root / "atelier_projects.json").read_bytes()
    wrong = http.post(BASE + "/another-turn/approval", json={**_identity(start), "decision": "approved"})
    extra = http.post(BASE, json={**START, "approved": True})
    assert wrong.status_code == extra.status_code == 422
    assert (root / "atelier_projects.json").read_bytes() == before
    scheduled.assert_not_called()



def test_start_pre_save_planner_rejection_has_specific_code_and_no_write(client, monkeypatch):
    http, scheduled, root = client

    def blocked(*_args, **_kwargs):
        raise ValueError("PRIVATE-PLANNER-DETAIL")

    monkeypatch.setattr(http.app.state.atelier_agent_executor._readonly, "plan", blocked)
    before = (root / "atelier_projects.json").read_bytes()
    response = http.post(BASE, json={**START, "selected_node_id": None})

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "atelier_agent_start_rejected",
        "detail": "Agent start request could not be prepared",
    }
    assert "PRIVATE-PLANNER-DETAIL" not in response.text
    assert (root / "atelier_projects.json").read_bytes() == before
    scheduled.assert_not_called()

def test_missing_turn_and_executor_are_explicit_not_found_or_unavailable(client):
    http, _scheduled, _root = client
    missing = http.get(BASE + "/absent")
    assert missing.status_code == 404
    app = FastAPI()
    app.include_router(router, prefix="/atelier")
    with TestClient(app) as empty:
        unavailable = empty.post(BASE, json=START)
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "atelier_agent_executor_unavailable"


def test_two_real_apps_keep_identical_agent_turn_ids_separate(tmp_path):
    apps = [create_app(data_dir=tmp_path / name, load_config=False, recover_orphan_tasks=False) for name in ("a", "b")]
    with TestClient(apps[0]) as a, TestClient(apps[1]) as b:
        for name, app in zip(("a", "b"), apps):
            seed(app, tmp_path / name)
            install_provider_runtime(app, replace(app.state.provider_recovery_runtime, secret_resolver=lambda: "fixture-secret"))
        first, second = _start(a), _start(b)
        assert first["turn"]["id"] == second["turn"]["id"]
        binding_a = first["turn"]["tool_calls"][0]["preview_binding"]["agent_authorization_ref"]
        binding_b = second["turn"]["tool_calls"][0]["preview_binding"]["agent_authorization_ref"]
        assert binding_a["server_storage_identity"] != binding_b["server_storage_identity"]
        crossed = b.post(BASE + "/turn-http-1/approval", json={**_identity(first), "decision": "approved"})
        assert crossed.status_code == 409
        assert apps[1].state.atelier_storage.get_project("p").generation_attempts == []
    assert all(not hasattr(app.state, "atelier_agent_executor") for app in apps)


def test_remote_agent_write_routes_require_access_authority(tmp_path):
    token = "test-agent-access-12345678901234567890"
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False, access_mode="remote", access_token=token)
    with TestClient(app) as http:
        seed(app, tmp_path)
        install_provider_runtime(app, replace(app.state.provider_recovery_runtime, secret_resolver=lambda: "fixture-secret"))
        before = (tmp_path / "atelier_projects.json").read_bytes()
        denied = http.post(BASE, json=START)
        assert denied.status_code == 401
        assert (tmp_path / "atelier_projects.json").read_bytes() == before
        allowed = http.post(BASE, json=START, headers={"Authorization": f"Bearer {token}"})
        assert allowed.status_code == 200, allowed.text


def test_approval_lost_ack_replays_exact_decision_but_cannot_revive_revocation(client):
    http, scheduled, _root = client
    start = _start(http)
    payload = {**_identity(start), "decision": "approved"}
    first = http.post(BASE + "/turn-http-1/approval", json=payload)
    assert first.status_code == 200, first.text
    repeated = http.post(BASE + "/turn-http-1/approval", json=payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["project"]["revision"] == first.json()["project"]["revision"]
    revoked = http.post(BASE + "/turn-http-1/approval", json={**payload, "decision": "revoked"})
    assert revoked.status_code == 200, revoked.text
    stale = http.post(BASE + "/turn-http-1/approval", json=payload)
    assert stale.status_code == 409
    assert http.app.state.atelier_storage.get_project("p").agent_turns[0].tool_calls[0].approval_record.decision == "revoked"
    scheduled.assert_not_called()


def test_missing_generation_runtime_fails_without_turn_or_secret_detail(client):
    http, scheduled, root = client
    _clear_provider_runtime(http.app)
    before = (root / "atelier_projects.json").read_bytes()
    failed = http.post(BASE, json=START)
    assert failed.status_code == 503, failed.text
    assert failed.json()["detail"]["code"] == "atelier_agent_runtime_unavailable"
    assert (root / "atelier_projects.json").read_bytes() == before
    scheduled.assert_not_called()


def test_two_tabs_approve_then_continue_with_one_durable_admission(client):
    from concurrent.futures import ThreadPoolExecutor

    http, scheduled, _root = client
    start = _start(http)
    identity = _identity(start)
    approval = {**identity, "decision": "approved"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        approved = list(pool.map(lambda _: http.post(BASE + "/turn-http-1/approval", json=approval), range(2)))
    assert all(response.status_code == 200 for response in approved), [r.text for r in approved]
    payload = {**identity, "expected_revision": approved[0].json()["project"]["revision"],
               "client_request_id": start["call"]["preview_binding"]["preview_public_record"]["raw_request"]["client_request_id"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted = list(pool.map(lambda _: http.post(BASE + "/turn-http-1/continue", json=payload), range(2)))
    assert all(response.status_code == 200 for response in admitted), [r.text for r in admitted]
    assert sorted(r.json()["replayed"] for r in admitted) == [False, True]
    assert len(http.app.state.atelier_storage.get_project("p").generation_attempts) == 1
    assert scheduled.call_count == 1


def test_http_agent_ack_fixture_export(client, tmp_path):
    import json

    http, _scheduled, _root = client
    started = _start(http)
    approved = http.post(BASE + "/turn-http-1/approval", json={**_identity(started), "decision": "approved"})
    assert approved.status_code == 200
    admitted = http.post(BASE + "/turn-http-1/continue", json={
        **_identity(approved.json()), "client_request_id": started["call"]["preview_binding"]["preview_public_record"]["raw_request"]["client_request_id"],
    })
    assert admitted.status_code == 200
    payload = {"started": started, "approved": approved.json(), "admitted": admitted.json()}
    for name, decision in [("canceled", None), ("denied", "denied"), ("revoked", "revoked")]:
        turn_id = f"turn-http-{name}"
        revision = http.app.state.atelier_storage.get_project("p").revision
        response = http.post(BASE, json={**START, "client_turn_id": turn_id, "expected_revision": revision})
        assert response.status_code == 200, response.text
        current = response.json()
        if decision is None:
            response = http.post(BASE + f"/{turn_id}/cancel", json={"turn_id": turn_id})
        else:
            if decision == "revoked":
                response = http.post(BASE + f"/{turn_id}/approval", json={**_identity(current), "decision": "approved"})
                assert response.status_code == 200, response.text
                current = response.json()
            response = http.post(BASE + f"/{turn_id}/approval", json={**_identity(current), "decision": decision})
        assert response.status_code == 200, response.text
        payload[name] = response.json()
    out = tmp_path / "atelier-agent-turn-acks.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HTTP Agent ACK fixture: {out}")
