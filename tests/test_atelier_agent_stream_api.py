"""SSE entrances use the same durable Agent authority as synchronous HTTP."""

import json
import threading
from dataclasses import replace

import anyio
import pytest
from fastapi.testclient import TestClient

from src.apps.atelier.agent_commands import AdmitApprovedAgentGenerationCommand
from src.apps.atelier.agent_stream_http import AgentEventStreamResponse
from src.apps.atelier.agent_turn_commands import StartAgentTurnCommand
from src.apps.comic_gen.api import create_app
from tests.test_atelier_generation_app import install_provider_runtime, seed

from tests import test_atelier_agent_turn_api
from tests.test_atelier_agent_turn_api import BASE, START, _clear_provider_runtime, _identity, _start

client = test_atelier_agent_turn_api.client


def events(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    parsed = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        event_type = next(line[7:] for line in lines if line.startswith("event: "))
        payload = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        assert payload["type"] == event_type
        parsed.append(payload)
    return parsed


def test_real_http_start_stream_saves_only_pending_approval_and_replays(client):
    http, scheduled, root = client
    first = events(http.post(BASE + "/stream", json=START))
    assert [event["type"] for event in first] == [
        "turn_planning_started", "turn_plan_ready", "turn_preview_prepared",
        "turn_pending_saved", "turn_terminal",
    ]
    ack = first[-1]["ack"]
    assert ack["turn"]["user_message"] == "生成视频"
    assert ack["turn"]["status"] == "waiting_approval"
    assert ack["generation"] is None
    assert http.get(BASE + "/turn-http-1").json()["turn"] == ack["turn"]
    (root / "uploads/frame.png").unlink()
    _clear_provider_runtime(http.app)
    replay = events(http.post(BASE + "/stream", json=START))
    assert replay[-1]["ack"]["replayed"] is True
    assert replay[-1]["ack"]["turn"] == ack["turn"]
    scheduled.assert_not_called()


def test_real_http_continue_stream_dispatches_only_one_durable_admission(client):
    http, scheduled, _root = client
    start = _start(http)
    identity = _identity(start)
    approved = http.post(BASE + "/turn-http-1/approval", json={**identity, "decision": "approved"})
    assert approved.status_code == 200
    binding = start["call"]["preview_binding"]
    payload = {**identity, "expected_revision": approved.json()["project"]["revision"],
               "client_request_id": binding["preview_public_record"]["raw_request"]["client_request_id"]}
    result = events(http.post(BASE + "/turn-http-1/continue/stream", json=payload))
    ack = result[-1]["ack"]
    assert ack["scheduled"] and not ack["replayed"]
    assert ack["generation"]["attempt"]["schema_version"] == 2
    scheduled.assert_called_once_with("p", ack["generation"]["attempt"]["attempt_id"])
    replay = events(http.post(BASE + "/turn-http-1/continue/stream", json=payload))
    assert replay[-1]["ack"]["replayed"] is True
    assert replay[-1]["ack"]["generation"]["new_candidate_ids"] == []
    assert "generation_admitted" not in [event["type"] for event in replay]
    # The historical synchronous ACK keeps its existing event summary; only
    # live stream stages distinguish a replay from a fresh admission.
    sync_replay = http.post(BASE + "/turn-http-1/continue", json=payload).json()
    assert [event["type"] for event in sync_replay["events"]] == ["generation_admitted"]
    assert scheduled.call_count == 1


def test_stream_errors_are_explicit_sanitized_and_never_terminal_success(client, monkeypatch, tmp_path):
    http, scheduled, _root = client
    def broken(*_args, **_kwargs):
        raise ValueError("PRIVATE-PROVIDER-SECRET")
    monkeypatch.setattr(http.app.state.atelier_agent_executor._readonly, "plan", broken)
    response = http.post(BASE + "/stream", json=START)
    result = events(response)
    assert result[-1]["type"] == "agent_error"
    assert result[-1]["code"] == "atelier_agent_start_rejected"
    assert result[-1]["detail"] == "Agent start request could not be prepared; no turn was saved"
    assert "PRIVATE-PROVIDER-SECRET" not in response.text
    assert not any(event["type"] == "turn_terminal" for event in result)
    assert http.app.state.atelier_storage.get_project("p").agent_turns == []
    scheduled.assert_not_called()
    target = tmp_path / "atelier-agent-stream-error-wire.json"
    target.write_text(json.dumps({"request": START, "stream": response.text}, ensure_ascii=False, indent=2) + "\n")
    print(f"HTTP Agent stream error fixture: {target}")


def test_stream_path_identity_and_unavailable_executor_fail_before_headers(client):
    http, scheduled, _root = client
    start = _start(http)
    payload = {**_identity(start), "client_request_id": "original-request"}
    mismatch = http.post(BASE + "/wrong/continue/stream", json=payload)
    assert mismatch.status_code == 422
    del http.app.state.atelier_agent_executor
    unavailable = http.post(BASE + "/stream", json=START)
    assert unavailable.status_code == 503
    scheduled.assert_not_called()


def _disconnect_after(iterator, cancel, marker):
    response = AgentEventStreamResponse(iterator, cancel, project_id="p", turn_id="turn-http-1")
    bodies = []
    async def scenario():
        reached = anyio.Event()
        async def receive():
            await reached.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                bodies.append(body)
                if f"event: {marker}\n".encode() in body:
                    reached.set()
                    while not cancel.is_set():
                        await anyio.sleep(0)
        with anyio.fail_after(3):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    anyio.run(scenario)
    assert cancel.is_set()
    assert b"event: turn_terminal" not in b"".join(bodies)


@pytest.mark.parametrize("marker,saved", [("turn_preview_prepared", False), ("turn_pending_saved", True)])
def test_asgi_disconnect_at_actual_start_commit_boundary_preserves_readback(client, marker, saved):
    http, scheduled, _root = client
    cancel = threading.Event()
    executor = http.app.state.atelier_agent_executor
    iterator = executor.iter_start_turn("p", StartAgentTurnCommand(**START), cancel_event=cancel)
    _disconnect_after(iterator, cancel, marker)
    readback = http.get(BASE + "/turn-http-1")
    assert readback.status_code == (200 if saved else 404)
    if saved:
        assert readback.json()["turn"]["status"] == "waiting_approval"
    assert http.app.state.atelier_storage.get_project("p").generation_attempts == []
    scheduled.assert_not_called()


def test_asgi_disconnect_after_actual_admission_keeps_receipt_without_resubmit(client):
    http, scheduled, _root = client
    start = _start(http)
    identity = _identity(start)
    approved = http.post(BASE + "/turn-http-1/approval", json={**identity, "decision": "approved"}).json()
    binding = start["call"]["preview_binding"]
    payload = {**identity, "expected_revision": approved["project"]["revision"],
               "client_request_id": binding["preview_public_record"]["raw_request"]["client_request_id"]}
    cancel = threading.Event()
    iterator = http.app.state.atelier_agent_executor.iter_continue_call(
        "p", AdmitApprovedAgentGenerationCommand(**payload), cancel_event=cancel,
    )
    _disconnect_after(iterator, cancel, "generation_admitted")
    project = http.app.state.atelier_storage.get_project("p")
    assert len(project.generation_attempts) == 1
    original_id = project.generation_attempts[0].attempt_id
    replay = http.post(BASE + "/turn-http-1/continue", json=payload).json()
    assert replay["replayed"] and not replay["scheduled"]
    assert replay["generation"]["attempt"]["attempt_id"] == original_id
    scheduled.assert_not_called()


def test_remote_stream_requires_authorization_before_preparing_a_turn(tmp_path):
    token = "fixture-stream-access-12345678901234567890"
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False, access_mode="remote", access_token=token)
    with TestClient(app) as http:
        seed(app, tmp_path)
        install_provider_runtime(app, replace(app.state.provider_recovery_runtime, secret_resolver=lambda: "fixture-secret"))
        denied = http.post(BASE + "/stream", json=START)
        assert denied.status_code == 401
        assert app.state.atelier_storage.get_project("p").agent_turns == []
        allowed = events(http.post(BASE + "/stream", json=START, headers={"Authorization": f"Bearer {token}"}))
        assert allowed[-1]["ack"]["turn"]["status"] == "waiting_approval"


def test_real_http_stream_wire_export(client, tmp_path):
    http, scheduled, _root = client
    start_response = http.post(BASE + "/stream", json=START)
    start = events(start_response)[-1]["ack"]
    identity = _identity(start)
    approved = http.post(BASE + "/turn-http-1/approval", json={**identity, "decision": "approved"}).json()
    payload = {**identity, "expected_revision": approved["project"]["revision"],
               "client_request_id": start["call"]["preview_binding"]["preview_public_record"]["raw_request"]["client_request_id"]}
    continue_response = http.post(BASE + "/turn-http-1/continue/stream", json=payload)
    assert events(continue_response)[-1]["ack"]["scheduled"]
    replay_response = http.post(BASE + "/turn-http-1/continue/stream", json=payload)
    assert events(replay_response)[-1]["ack"]["replayed"]
    scheduled.assert_called_once()
    target = tmp_path / "atelier-agent-stream-wire.json"
    target.write_text(json.dumps({
        "start_request": START, "continue_request": payload,
        "start": start_response.text, "continue": continue_response.text, "replay": replay_response.text,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"HTTP Agent stream fixture: {target}")
