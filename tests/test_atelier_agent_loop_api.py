"""Actual app routes for durable Atelier Agent loops and live SSE stages."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from threading import Event
from dataclasses import replace
from unittest.mock import Mock

import anyio
import pytest
from fastapi.testclient import TestClient

from src.apps.atelier.agent_loop import DeterministicLoopPlan
from src.apps.atelier.agent_loop_commands import StartAgentLoopCommand
from src.apps.atelier.agent_planner_invocations import ClaimAgentPlannerInvocationCommand, claim_config_fingerprint
from src.apps.atelier.models import AtelierAgentPlannerMessage
from src.apps.atelier.agent_stream_http import AgentEventStreamResponse
from src.apps.comic_gen.api import create_app, _planner_config_identity_for_runtime
from src.models.provider_recovery import record_with_changes
from tests.test_atelier_generation_app import install_provider_runtime, seed

BASE = "/atelier/projects/p/agent/loops"
START = {
    "expected_revision": 0,
    "client_turn_id": "loop-http-1",
    "user_message": "生成视频",
    "selected_node_id": "video",
    "max_iterations": 3,
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        seed(app, tmp_path)
        install_provider_runtime(app, replace(app.state.provider_recovery_runtime, secret_resolver=lambda: "fixture-loop-secret"))
        scheduled = Mock(return_value=True)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", scheduled)
        yield http, scheduled, tmp_path


def _clear_provider_runtime(app):
    app.state.provider_recovery_runtime = None
    app.state.pipeline.provider_recovery_runtime = None
    app.state.provider_runtime_selector = None
    app.state.pipeline.provider_runtime_selector = None


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


def _generation_identity(ack):
    call = next(item for item in ack["turn"]["tool_calls"] if item.get("preview_binding"))
    ref = call["preview_binding"]["agent_authorization_ref"]
    return {
        "expected_revision": ack["project"]["revision"],
        "turn_id": ack["turn"]["id"],
        "call_id": call["call_id"],
        "approval_id": ref["approval_id"],
        "authorization_hash": ref["authorization_hash"],
        "decision": "approved",
    }


def _start_loop(http):
    response = http.post(BASE, json=START)
    assert response.status_code == 200, response.text
    return response.json()


def test_iter_start_cancelled_before_entry_does_not_write(client):
    http, scheduled, root = client
    cancel = Event()
    cancel.set()
    iterator = http.app.state.atelier_agent_loop_service.iter_start_loop("p", StartAgentLoopCommand(**START), cancel_event=cancel)
    assert list(iterator) == []
    assert http.app.state.atelier_storage.get_project("p").agent_turns == []
    assert (root / "atelier_projects.json").read_text().count("loop-http-1") == 0
    scheduled.assert_not_called()


def test_loop_cancel_after_planner_prepare_blocks_iteration_save(client):
    http, scheduled, _root = client
    cancel = Event()

    def planner(_ctx):
        cancel.set()
        return DeterministicLoopPlan("inspect", [{"tool_name": "canvas.readProject", "arguments": {}}])

    http.app.state.atelier_agent_loop_service._planner = planner
    result = list(http.app.state.atelier_agent_loop_service.iter_start_loop("p", StartAgentLoopCommand(**START), cancel_event=cancel))
    assert result[-1].type == "loop_closed"
    turn = http.app.state.atelier_storage.get_project("p").agent_turns[0]
    assert turn.iterations == [] and turn.tool_calls == []
    scheduled.assert_not_called()


def test_loop_disconnect_after_tool_done_does_not_finish_iteration_limit(client):
    http, scheduled, _root = client
    request = {**START, "max_iterations": 1}
    cancel = Event()
    iterator = http.app.state.atelier_agent_loop_service.iter_start_loop("p", StartAgentLoopCommand(**request), cancel_event=cancel)
    seen = []
    for event in iterator:
        seen.append(event.type)
        if event.type == "tool_done":
            cancel.set()
    assert seen[-1] == "loop_closed"
    turn = http.app.state.atelier_storage.get_project("p").agent_turns[0]
    assert turn.status == "pending"
    assert turn.response is None
    scheduled.assert_not_called()



def test_real_app_loop_start_read_replay_resume_and_schedule_once(client):
    http, scheduled, root = client
    start = _start_loop(http)
    event_types = [event["type"] for event in start["events"]]
    assert "tool_done" in event_types
    assert start["turn"]["status"] == "waiting_approval"
    assert start["project"]["generation_attempts"] == []
    readback = http.get(BASE + "/loop-http-1")
    assert readback.status_code == 200 and readback.json()["turn"] == start["turn"]

    replay = http.post(BASE, json=START)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["turn"] == start["turn"]
    scheduled.assert_not_called()

    approval = http.post("/atelier/projects/p/agent/turns/loop-http-1/approval", json=_generation_identity(start))
    assert approval.status_code == 200, approval.text
    resumed = http.post(BASE + "/loop-http-1/resume", json={"turn_id": "loop-http-1"})
    assert resumed.status_code == 200, resumed.text
    ack = resumed.json()
    assert ack["generation"]["attempt"]["provider_execution"]["phase"] == "accepted"
    assert ack["scheduled"] is True
    scheduled.assert_called_once_with("p", ack["generation"]["attempt"]["attempt_id"])
    replay_resume = http.post(BASE + "/loop-http-1/resume", json={"turn_id": "loop-http-1"})
    assert replay_resume.status_code == 200
    assert replay_resume.json()["generation"]["attempt"]["attempt_id"] == ack["generation"]["attempt"]["attempt_id"]
    assert scheduled.call_count == 1
    assert "fixture-loop-secret" not in resumed.text


def _canvas_identity(ack):
    call = next(item for item in ack["turn"]["tool_calls"] if item.get("canvas_approval_binding"))
    ref = call["canvas_approval_binding"]["canvas_authorization_ref"]
    return {
        "expected_revision": ack["project"]["revision"],
        "turn_id": ack["turn"]["id"],
        "call_id": call["call_id"],
        "approval_id": ref["approval_id"],
        "authorization_hash": ref["authorization_hash"],
        "decision": "approved",
    }


def test_loop_canvas_approval_route_guards_loop_context_and_resume_applies_once(client):
    http, scheduled, _root = client
    plans = iter([
        DeterministicLoopPlan("inspect", [{"tool_name": "canvas.readProject", "arguments": {}}]),
        DeterministicLoopPlan("create", [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Agent take", "prompt": "draft", "x": 10.0, "y": 20.0}}]),
        DeterministicLoopPlan("done", [], "done"),
    ])
    http.app.state.atelier_agent_loop_service._planner = lambda _ctx: next(plans)
    start = _start_loop(http)
    assert start["turn"]["status"] == "waiting_approval"
    approval = http.post(BASE + "/loop-http-1/canvas_approval", json=_canvas_identity(start))
    assert approval.status_code == 200, approval.text
    assert approval.json()["events"][0]["type"] == "canvas_approval_recorded"
    resumed = http.post(BASE + "/loop-http-1/resume", json={"turn_id": "loop-http-1"})
    assert resumed.status_code == 200, resumed.text
    ack = resumed.json()
    assert ack["turn"]["status"] == "completed"
    assert any(node["title"] == "Agent take" and node["created_by"] == "agent" for node in ack["project"]["nodes"])
    replay = http.post(BASE + "/loop-http-1/resume", json={"turn_id": "loop-http-1"})
    assert replay.status_code == 200
    assert replay.json()["project"]["nodes"] == ack["project"]["nodes"]
    scheduled.assert_not_called()


def test_loop_stream_yields_live_stage_events_before_final_ack(client):
    http, _scheduled, _root = client
    result = events(http.post(BASE + "/stream", json=START))
    event_types = [event["type"] for event in result]
    assert event_types[:3] == ["loop_started", "iteration_started", "iteration_saved"]
    assert "tool_started" in event_types
    assert "tool_done" in event_types
    assert event_types[-1] == "approval_paused"
    assert result[-1]["ack"]["turn"]["status"] == "waiting_approval"



def test_product_skill_catalog_route_is_sanitized_readonly(client):
    http, scheduled, root = client
    before = (root / "atelier_projects.json").read_bytes()
    response = http.get("/atelier/agent/product_skills")
    assert response.status_code == 200, response.text
    catalog = response.json()
    enabled = next(item for item in catalog if item["enabled"] and item["name"] == "story-scout")
    assert enabled["version"] == "1.0.0"
    assert enabled["expected_tools"] == ["canvas.readProject"]
    assert "prompt_template" not in enabled
    assert "path" not in enabled and "credential_ref" not in enabled
    assert (root / "atelier_projects.json").read_bytes() == before
    scheduled.assert_not_called()


def test_model_adapter_product_skill_start_persists_frozen_snapshot(client):
    http, scheduled, _root = client

    def model_planner(context, **_kwargs):
        assert context.turn.loop_start_context.skill_snapshot.name == "story-scout"
        return DeterministicLoopPlan("read", [{"tool_name": "canvas.readProject", "arguments": {}}], "done")

    http.app.state.atelier_agent_loop_service._model_planner = model_planner
    response = http.post(BASE, json={**START, "client_turn_id": "skill-http-1", "planner": "model_adapter", "skill_name": "story-scout", "max_iterations": 1})
    assert response.status_code == 200, response.text
    turn = response.json()["turn"]
    assert turn["skill_name"] == "story-scout"
    context = turn["loop_start_context"]
    assert context["schema_version"] == 2
    assert context["skill_name"] == "story-scout"
    assert context["skill_snapshot"]["name"] == "story-scout"
    assert context["skill_snapshot"]["content_hash"]
    http.app.state.atelier_agent_loop_service._product_skill_resolver = lambda _name: (_ for _ in ()).throw(AssertionError("replay must not resolve live registry"))
    replay = http.post(BASE, json={**START, "client_turn_id": "skill-http-1", "planner": "model_adapter", "skill_name": "story-scout", "max_iterations": 1})
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["turn"] == turn
    scheduled.assert_not_called()


def test_product_skill_start_errors_are_typed_and_do_not_call_model(client):
    http, scheduled, root = client
    calls = 0

    def model_planner(_context, **_kwargs):
        nonlocal calls
        calls += 1
        return DeterministicLoopPlan("bad", [], "done")

    http.app.state.atelier_agent_loop_service._model_planner = model_planner
    before = (root / "atelier_projects.json").read_bytes()
    unknown = http.post(BASE, json={**START, "client_turn_id": "skill-unknown", "planner": "model_adapter", "skill_name": "missing-skill"})
    deterministic = http.post(BASE, json={**START, "client_turn_id": "skill-deterministic", "skill_name": "story-scout"})
    assert unknown.status_code == deterministic.status_code == 422
    assert unknown.json()["detail"]["code"] == "atelier_product_skill_unknown"
    assert deterministic.json()["detail"]["code"] == "atelier_agent_loop_skill_requires_model_adapter"
    assert calls == 0
    assert (root / "atelier_projects.json").read_bytes() == before
    scheduled.assert_not_called()


def test_standalone_agent_turns_are_not_loop_read_or_resume_targets(client):
    http, scheduled, _root = client
    standalone = http.post("/atelier/projects/p/agent/turns", json={
        "expected_revision": 0, "client_turn_id": "standalone-turn", "user_message": "生成视频", "selected_node_id": "video",
    })
    assert standalone.status_code == 200, standalone.text
    read = http.get(BASE + "/standalone-turn")
    resume = http.post(BASE + "/standalone-turn/resume", json={"turn_id": "standalone-turn"})
    cancel = http.post("/atelier/projects/p/agent/turns/standalone-turn/cancel", json={"turn_id": "standalone-turn"})
    terminal_read = http.get(BASE + "/standalone-turn")
    assert read.status_code == resume.status_code == terminal_read.status_code == 404
    assert cancel.status_code == 200
    scheduled.assert_not_called()


def test_loop_missing_generation_runtime_maps_to_503_not_500(client):
    http, scheduled, _root = client
    _clear_provider_runtime(http.app)
    response = http.post(BASE, json=START)
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "atelier_agent_runtime_unavailable"
    assert http.app.state.atelier_storage.get_project("p").agent_turns[0].loop_start_context is not None
    scheduled.assert_not_called()


def test_start_replay_reports_persisted_generation_waiting_and_schedule_error(client):
    http, scheduled, _root = client
    scheduled.return_value = False
    start = _start_loop(http)
    approved = http.post("/atelier/projects/p/agent/turns/loop-http-1/approval", json=_generation_identity(start))
    assert approved.status_code == 200, approved.text
    failed_schedule = http.post(BASE + "/loop-http-1/resume", json={"turn_id": "loop-http-1"})
    assert failed_schedule.status_code == 200, failed_schedule.text
    assert failed_schedule.json()["schedule_error"] == "generation_schedule_not_accepted"
    replay = http.post(BASE, json=START)
    assert replay.status_code == 200, replay.text
    assert replay.json()["events"][-1]["type"] == "generation_waiting"
    assert replay.json()["schedule_error"] == "generation_schedule_not_accepted"
    assert replay.json()["generation"]["attempt"]["attempt_id"] == failed_schedule.json()["generation"]["attempt"]["attempt_id"]
    assert scheduled.call_count == 1


def test_scheduler_that_advances_then_raises_persists_diagnostic_on_latest_snapshot(client):
    http, scheduled, _root = client

    def advance_then_raise(project_id, attempt_id):
        store = http.app.state.atelier_storage
        project = store.get_project(project_id)
        attempt = next(item for item in project.generation_attempts if item.attempt_id == attempt_id)
        store.update_generation_attempt(
            project_id,
            attempt_id=attempt_id,
            expected_attempt_revision=attempt.attempt_revision,
            execution_fingerprint=attempt.provider_execution.fingerprint,
            command=lambda item: (
                setattr(item, "status", "processing"),
                setattr(item, "provider_execution", record_with_changes(item.provider_execution, phase="submitting")),
            ),
        )
        raise RuntimeError("private scheduler detail")

    scheduled.side_effect = advance_then_raise
    start = _start_loop(http)
    approved = http.post("/atelier/projects/p/agent/turns/loop-http-1/approval", json=_generation_identity(start))
    assert approved.status_code == 200, approved.text
    result = http.post(BASE + "/loop-http-1/resume", json={"turn_id": "loop-http-1"})
    assert result.status_code == 200, result.text
    ack = result.json()
    assert ack["schedule_error"] == "generation_schedule_failed"
    assert ack["generation"]["project"]["revision"] == ack["project"]["revision"]
    assert ack["generation"]["attempt"]["provider_execution"]["phase"] == "submitting"
    readback = http.get(BASE + "/loop-http-1").json()
    assert readback["schedule_error"] == "generation_schedule_failed"
    assert "private scheduler detail" not in result.text



def test_loop_route_identity_and_unknown_planner_reject_before_write(client):
    http, scheduled, root = client
    before = (root / "atelier_projects.json").read_bytes()
    unsupported = http.post(BASE, json={**START, "client_turn_id": "loop-bad", "planner": "unknown"})
    assert unsupported.status_code == 422
    assert (root / "atelier_projects.json").read_bytes() == before
    start = _start_loop(http)
    mismatch = http.post(BASE + "/wrong/resume", json={"turn_id": start["turn"]["id"]})
    assert mismatch.status_code == 422
    scheduled.assert_not_called()


def test_invalid_loop_body_uses_sanitized_validation_response_without_write(client):
    http, scheduled, root = client
    before = (root / "atelier_projects.json").read_bytes()
    response = http.post(BASE, json={
        **START, "expected_revision": "bad", "raw_input": "PRIVATE-LOOP-INPUT",
    })
    assert response.status_code == 422
    assert "PRIVATE-LOOP-INPUT" not in response.text
    assert all("input" not in item for item in response.json()["detail"])
    assert (root / "atelier_projects.json").read_bytes() == before
    scheduled.assert_not_called()



def test_loop_service_is_removed_on_app_shutdown(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        seed(app, tmp_path)
        assert hasattr(app.state, "atelier_agent_loop_service")
    assert not hasattr(app.state, "atelier_agent_loop_service")


def test_remote_loop_routes_require_access_authority(tmp_path):
    token = "test-loop-access-token-12345678901234567890"
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


def _disconnect_after(iterator, cancel, marker, *, asgi_version):
    response = AgentEventStreamResponse(iterator, cancel, project_id="p", turn_id="loop-http-1")
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
            await response({"type": "http", "asgi": {"spec_version": asgi_version}}, receive, send)

    anyio.run(scenario)
    assert cancel.is_set()
    assert b"event: loop_terminal" not in b"".join(bodies)


@pytest.mark.parametrize("asgi_version", ["2.0", "2.4"])
def test_loop_stream_disconnect_after_admission_retains_attempt_without_schedule(client, asgi_version):
    http, scheduled, _root = client
    start = _start_loop(http)
    approved = http.post("/atelier/projects/p/agent/turns/loop-http-1/approval", json=_generation_identity(start))
    assert approved.status_code == 200, approved.text
    cancel = threading.Event()
    iterator = http.app.state.atelier_agent_loop_service.iter_resume_loop("p", "loop-http-1", cancel_event=cancel)
    _disconnect_after(iterator, cancel, "generation_admitted", asgi_version=asgi_version)
    project = http.app.state.atelier_storage.get_project("p")
    assert len(project.generation_attempts) == 1
    attempt_id = project.generation_attempts[0].attempt_id
    scheduled.assert_not_called()
    replay = http.post(BASE + "/loop-http-1/resume", json={"turn_id": "loop-http-1"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["generation"]["attempt"]["attempt_id"] == attempt_id
    assert scheduled.call_count == 0

def test_real_app_model_adapter_loop_fake_llm_serializes_http_readback(tmp_path, monkeypatch):
    calls = []

    class FakeChatResult:
        content = json.dumps({
            "assistant_message": "Inspect via model planner.",
            "tool_calls": [{"tool_name": "canvas.readProject", "arguments": {}}],
        })
        requested_model = "fixture-model"
        provider_model = "fixture-provider-model"
        prompt_tokens = 11
        completion_tokens = 3
        total_tokens = 14

    class FakeLLMAdapter:
        provider = "fixture-provider"

        def __init__(self, *, env=None):
            self.env = env

        def _get_default_model(self):
            return "fixture-model"

        def chat_result(self, messages, *, model, max_completion_tokens, timeout, response_format):
            calls.append({
                "env_id": id(self.env),
                "messages": messages,
                "model": model,
                "max_completion_tokens": max_completion_tokens,
                "timeout": timeout,
                "response_format": response_format,
            })
            return FakeChatResult()

    monkeypatch.setattr("src.apps.comic_gen.llm_adapter.LLMAdapter", FakeLLMAdapter)
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        seed(app, tmp_path)
        response = http.post(BASE, json={**START, "client_turn_id": "loop-http-model", "planner": "model_adapter", "max_iterations": 1})
        assert response.status_code == 200, response.text
        ack = response.json()
        assert len(calls) == 1
        assert calls[0]["model"] == "fixture-model"
        assert calls[0]["max_completion_tokens"] == 1024
        assert calls[0]["timeout"] == 60.0
        assert calls[0]["response_format"] == {"type": "json_object"}
        assert ack["turn"]["loop_start_context"]["planner"] == "model_adapter"
        assert ack["turn"]["status"] == "completed"
        invocation = ack["turn"]["planner_invocations"][0]
        assert invocation["provider_id"] == "fixture-provider"
        assert invocation["model_id"] == "fixture-model"
        assert invocation["provider_model"] == "fixture-provider-model"
        assert invocation["usage"] == {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
        assert invocation["charged_completion_tokens"] == 3
        assert "result_content" in invocation
        assert "fixture-loop-secret" not in response.text

        read_loop = http.get(BASE + "/loop-http-model")
        assert read_loop.status_code == 200, read_loop.text
        assert read_loop.json()["turn"] == ack["turn"]
        read_project = http.get("/atelier/projects/p")
        assert read_project.status_code == 200, read_project.text
        project_turn = next(item for item in read_project.json()["agent_turns"] if item["id"] == "loop-http-model")
        assert project_turn["loop_start_context"]["planner"] == "model_adapter"
        assert project_turn["planner_invocations"][0]["invocation_id"] == "loop-http-model:iteration:1"
        output = Path(os.environ.get("LUMENX_TEST_ISOLATION_ROOT", str(tmp_path))) / "agent-model-loop-wire.json"
        output.write_text(json.dumps({
            "schema_version": 1,
            "start": ack,
            "read": read_loop.json(),
            "project": read_project.json(),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"AGENT_MODEL_LOOP_FIXTURE={output}")


def test_model_planner_config_identity_fingerprint_tracks_endpoint_and_credential_alias_without_leaks():
    base_env = {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-secret-do-not-store",
        "OPENAI_BASE_URL": "https://endpoint-a.example/v1",
        "OPENAI_MODEL": "fixture-model",
    }
    endpoint_changed = {**base_env, "OPENAI_BASE_URL": "https://endpoint-b.example/v1"}
    alias_changed = {**base_env, "OPENAI_CREDENTIAL_ALIAS": "account-alpha-v2"}
    base_identity = _planner_config_identity_for_runtime(
        provider_id="openai", model_id="fixture-model", env=base_env, storage_identity="storage-fixture",
    )
    endpoint_identity = _planner_config_identity_for_runtime(
        provider_id="openai", model_id="fixture-model", env=endpoint_changed, storage_identity="storage-fixture",
    )
    alias_identity = _planner_config_identity_for_runtime(
        provider_id="openai", model_id="fixture-model", env=alias_changed, storage_identity="storage-fixture",
    )

    assert base_identity.credential_ref is None
    assert alias_identity.credential_ref == "account-alpha-v2"
    serialized = json.dumps(base_identity.model_dump(mode="json"), sort_keys=True)
    assert "endpoint-a.example" not in serialized
    assert "sk-secret" not in serialized
    assert base_identity.runtime_config_id != endpoint_identity.runtime_config_id

    def fingerprint(identity):
        return claim_config_fingerprint(ClaimAgentPlannerInvocationCommand(
            expected_revision=0,
            turn_id="turn-1",
            iteration_index=1,
            claim_request_id="claim-1",
            provider_id="openai",
            model_id="fixture-model",
            config_identity=identity,
            input_messages=(AtelierAgentPlannerMessage(role="user", content="{}"),),
            max_completion_tokens=64,
            timeout_seconds=7.5,
        ))

    assert fingerprint(base_identity) != fingerprint(endpoint_identity)
    assert fingerprint(base_identity) != fingerprint(alias_identity)
