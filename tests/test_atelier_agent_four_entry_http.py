"""P4 actual app/dispatcher/WanxModel integration; only external HTTP is synthetic.

The planner-specific case additionally substitutes the external bounded chat
result. No scheduler, credential store, provider object, or ledger is mocked.
"""
from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import io
import json
import subprocess
import threading
import time
from typing import Any

import anyio
from fastapi.testclient import TestClient
import pytest
import requests
from requests.adapters import HTTPAdapter

from src.apps.atelier.agent_commands import AdmitApprovedAgentGenerationCommand
from src.apps.atelier.agent_stream_http import AgentEventStreamResponse
from src.apps.atelier.models import AtelierProject
from src.apps.comic_gen.api import create_app
from src.models.wanx import WanxModel
from src.utils.media_probe import probe_media_file
from src.utils.system_check import get_ffmpeg_path
from tests.frozen_image_fixtures import rgb_png_bytes

BASE = "https://dashscope.aliyuncs.com"
SUBMIT = BASE + "/api/v1/services/aigc/video-generation/video-synthesis"
SECRET = "synthetic-example-p4-http"
PROJECT = "/atelier/projects/p"
PARAMS = {"duration": 5, "resolution": "720p", "seed": 0, "prompt_extend": False,
          "watermark": False, "negative_prompt": ""}
FLOWS = [("turn", False, False), ("turn", True, True), ("turn", False, True),
         ("turn", True, False), ("loop", False, False), ("loop", True, True)]


@pytest.fixture(scope="module")
def video_bytes(tmp_path_factory):
    target = tmp_path_factory.mktemp("p4-video") / "result.mp4"
    subprocess.run([get_ffmpeg_path() or "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi",
                    "-i", "color=c=blue:s=320x240:r=24:d=1", "-pix_fmt", "yuv420p", str(target)],
                   capture_output=True, check=True, timeout=20)
    assert probe_media_file(target, "video").kind == "video"
    return target.read_bytes()


@dataclass
class HTTPBoundary:
    video: bytes
    mode: str = "success"
    query_status: str = "SUCCEEDED"
    calls: list[tuple[str, requests.PreparedRequest]] = field(default_factory=list)
    app: Any = None
    lock: Any = field(default_factory=threading.Lock)

    def count(self, kind):
        return sum(item[0] == kind for item in self.calls)

    def send(self, request, **_kwargs):
        # Every requests HTTP attempt reaches this exact boundary, including any
        # unexpected route. No external socket is opened by this adapter.
        url = request.url
        if request.method == "POST" and url == SUBMIT:
            assert request.headers["Authorization"] == f"Bearer {SECRET}"
            data = json.loads(request.body)
            assert data["model"] == "wan2.7-i2v"
            media, = data["input"]["media"]
            assert media["type"] == "first_frame"
            assert base64.b64decode(media["url"].split(",", 1)[1]) == rgb_png_bytes("p4-frame")
            attempts = self.app.state.atelier_storage.get_project("p").generation_attempts
            assert any(a.provider_execution.phase == "submitting" and a.provider_execution.handle is None for a in attempts)
            with self.lock:
                self.calls.append(("submit", request))
                task_id = f"p4-task-{self.count('submit')}"
            if self.mode == "lost_ack":
                raise requests.ConnectionError("synthetic lost provider acknowledgement")
            payload = {} if self.mode == "missing_receipt" else {"request_id": task_id + "-request", "output": {"task_id": task_id}}
        elif request.method == "GET" and url.startswith(BASE + "/api/v1/tasks/p4-task-"):
            assert request.headers["Authorization"] == f"Bearer {SECRET}"
            task_id = url.rsplit("/", 1)[1]
            attempts = self.app.state.atelier_storage.get_project("p").generation_attempts
            assert any(a.provider_execution.handle and a.provider_execution.handle["task_id"] == task_id for a in attempts)
            self.calls.append(("query", request))
            payload = {"output": {"task_id": task_id, "task_status": self.query_status,
                                   "video_url": f"https://p4-result.invalid/{task_id}.mp4"}}
        elif request.method == "GET" and url.startswith("https://p4-result.invalid/p4-task-"):
            assert "Authorization" not in request.headers and "Cookie" not in request.headers
            self.calls.append(("fetch", request))
            return self.response(request, self.video, "video/mp4")
        else:
            pytest.fail(f"unexpected external HTTP: {request.method} {url}")
        return self.response(request, json.dumps(payload).encode(), "application/json")

    @staticmethod
    def response(request, data, content_type):
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response.url = request.url
        response.headers.update({"Content-Type": content_type, "Content-Length": str(len(data))})
        response.raw = io.BytesIO(data)
        return response


@pytest.fixture
def boundary(monkeypatch, video_bytes):
    # Real app configuration is present before lifespan starts recovery. No
    # credential/runtime replacement is needed, including after store reopen.
    for key, value in {"DASHSCOPE_API_KEY": SECRET, "DASHSCOPE_BASE_URL": BASE,
                       "DASHSCOPE_REGION": "cn-beijing"}.items():
        monkeypatch.setenv(key, value)
    external = HTTPBoundary(video_bytes)
    def send(_adapter, request, **kwargs):
        return external.send(request, **kwargs)
    monkeypatch.setattr(HTTPAdapter, "send", send)
    return external


@contextmanager
def application(root, boundary, *, seed=True, remote=False) -> Iterator[Any]:
    app = create_app(data_dir=root, load_config=False, recover_orphan_tasks=True,
                     access_mode="remote" if remote else "local",
                     access_token="synthetic-example-access-p4-1234567890" if remote else None)
    boundary.app = app
    with TestClient(app) as http:
        runtime = app.state.provider_runtime_selector.for_admission(provider_model_id="wan2.7-i2v", gateway="dashscope").runtime
        assert type(runtime.provider) is WanxModel and runtime.resolve_secret() == SECRET
        if seed:
            (root / "uploads").mkdir(parents=True, exist_ok=True)
            (root / "uploads/frame.png").write_bytes(rgb_png_bytes("p4-frame"))
            app.state.atelier_storage.create_project(AtelierProject.model_validate({
                "id": "p", "title": "P4 isolated", "created_at": 1, "updated_at": 1,
                "nodes": [{"id": "image", "project_id": "p", "type": "image", "media_urls": ["uploads/frame.png"]},
                          {"id": "video", "project_id": "p", "type": "video", "prompt": "Slow sunrise",
                           "data": {"reference_node_ids": ["image"]}}],
            }))
        yield http


def ack(response):
    assert response.status_code == 200, response.text
    if not response.headers["content-type"].startswith("text/event-stream"):
        return response.json()
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert not any(item["type"] == "agent_error" for item in events), events
    assert "ack" in events[-1], events
    return events[-1]["ack"]


def start(http, family, *, stream=False, planner=None):
    body = {"expected_revision": 0, "client_turn_id": "p4-turn", "user_message": "生成视频", "selected_node_id": "video"}
    if family == "loop":
        body["max_iterations"] = 3
        if planner:
            body["planner"] = planner
    path = PROJECT + f"/agent/{family}s" + ("/stream" if stream else "")
    return ack(http.post(path, json=body)), body


def identity(value):
    call = next(item for item in reversed(value["turn"]["tool_calls"]) if item.get("preview_binding"))
    ref = call["preview_binding"]["agent_authorization_ref"]
    return {"expected_revision": value["project"]["revision"], "turn_id": value["turn"]["id"],
            "call_id": call["call_id"], "approval_id": ref["approval_id"], "authorization_hash": ref["authorization_hash"]}


def binding(value):
    return next(item["preview_binding"] for item in reversed(value["turn"]["tool_calls"]) if item.get("preview_binding"))


def decide(http, value, decision="approved"):
    return ack(http.post(PROJECT + "/agent/turns/p4-turn/approval", json={**identity(value), "decision": decision}))


def continued(http, family, approved, *, stream=False):
    if family == "loop":
        path, body = PROJECT + "/agent/loops/p4-turn/resume", {"turn_id": "p4-turn"}
    else:
        path = PROJECT + "/agent/turns/p4-turn/continue"
        body = {**identity(approved), "client_request_id": binding(approved)["preview_public_record"]["raw_request"]["client_request_id"]}
    return http.post(path + ("/stream" if stream else ""), json=body), body


def read_request(http, preview):
    request_id = binding(preview)["preview_public_record"]["raw_request"]["client_request_id"]
    return ack(http.get(PROJECT + "/generation/requests/" + request_id))


def drive(http, preview, *, terminal=True):
    # Wake only the real recovery dispatcher. Accepted work cannot acquire a
    # submission right from wake/GET; the original admission must schedule it.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        dispatcher = http.app.state.atelier_generation_dispatcher
        dispatcher.wake()
        assert dispatcher.wait_for_idle(2)
        result = read_request(http, preview)
        attempt = result["attempt"]
        if terminal and attempt["status"] in {"completed", "failed", "needs_confirmation"}:
            return result
        if not terminal and attempt["provider_execution"]["handle"]:
            return result
        time.sleep(0.01)
    pytest.fail("real dispatcher did not reach the expected durable state")


@pytest.mark.parametrize("family,start_stream,continue_stream", FLOWS)
def test_four_entry_variants_reach_real_http_once_and_cross_transport_replay(
    tmp_path, boundary, family, start_stream, continue_stream,
):
    with application(tmp_path, boundary) as http:
        preview, original = start(http, family, stream=start_stream)
        assert preview["turn"]["status"] == "waiting_approval"
        assert preview["project"]["generation_attempts"] == [] and boundary.calls == []
        approved = decide(http, preview)
        assert boundary.calls == []
        response, continue_body = continued(http, family, approved, stream=continue_stream)
        admitted = ack(response)
        assert admitted["generation"]["attempt"]["schema_version"] == 2
        # A loop's final waiting ACK re-reads the saved generation and thus
        # reports no new candidates; schedule authority comes from its event.
        assert admitted["scheduled"]
        if family == "turn":
            assert admitted["generation"]["new_candidate_ids"]
        else:
            assert any(e["type"] == "generation_admitted" and not e["replayed"] for e in admitted["events"])
        result = drive(http, preview)
        assert result["attempt"]["status"] == "completed"
        record = result["attempt"]["provider_execution"]
        assert record["provider_model_id"] == "wan2.7-i2v"
        assert record["intent"] == binding(preview)["provider_execution"]["intent"]
        assert result["attempt"]["agent_authorization_ref"] == binding(preview)["agent_authorization_ref"]
        output = tmp_path / result["attempt"]["output_ref"]
        assert probe_media_file(output, "video").kind == "video"
        assert output.read_bytes() == boundary.video
        assert record["artifact"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        assert record["artifact"]["size_bytes"] == len(boundary.video)
        node = next(n for n in result["project"]["nodes"] if n["id"] == "video")
        assert node["media_urls"] == [] and not node["data"].get("selected_candidate_id")
        assert [boundary.count(k) for k in ("submit", "query", "fetch")] == [1, 1, 1]
        sent = json.loads(boundary.calls[0][1].body)
        assert sent["parameters"] == {"duration": 5, "resolution": "1080P"}
        # Repeat the original start and continue through the other transport.
        replay_start = ack(http.post(PROJECT + f"/agent/{family}s" + ("" if start_stream else "/stream"), json=original))
        assert replay_start["replayed"]
        replay_path = PROJECT + ("/agent/loops/p4-turn/resume" if family == "loop" else "/agent/turns/p4-turn/continue")
        replay = ack(http.post(replay_path + ("" if continue_stream else "/stream"), json=continue_body))
        if family == "turn":
            assert replay["generation"]["attempt"]["attempt_id"] == result["attempt"]["attempt_id"]
            assert replay["generation"]["new_candidate_ids"] == []
        assert not replay["scheduled"]
        assert http.app.state.atelier_generation_dispatcher.wait_for_idle(2)
        assert [boundary.count(k) for k in ("submit", "query", "fetch")] == [1, 1, 1]
        after_replay = read_request(http, preview)
        assert after_replay["attempt"]["attempt_id"] == result["attempt"]["attempt_id"]
        assert after_replay["receipt"] == result["receipt"]
        assert len(after_replay["project"]["generation_attempts"]) == 1
        assert len(after_replay["project"]["generation_request_receipts"]) == 1
        assert ack(http.get(PROJECT + f"/agent/{family}s/p4-turn"))["turn"]["id"] == "p4-turn"
        chosen = ack(http.post(PROJECT + f"/nodes/video/video_candidates/{result['attempt']['candidate_id']}/select",
                               json={"expected_revision": replay["project"]["revision"]}))
        chosen_node = next(n for n in chosen["project"]["nodes"] if n["id"] == "video")
        assert chosen_node["media_urls"] == [result["attempt"]["output_ref"]]
        assert http.app.state.pipeline.read_committed_projects() == []
        assert SECRET not in (tmp_path / "atelier_projects.json").read_text()


@pytest.mark.parametrize("family", ["turn", "loop"])
@pytest.mark.parametrize("action", ["unapproved", "denied", "revoked", "cancelled"])
def test_without_current_approval_real_dispatcher_has_no_submit(tmp_path, boundary, family, action):
    with application(tmp_path, boundary) as http:
        preview, _ = start(http, family, stream=True)
        current = preview
        if action in {"revoked", "cancelled"}:
            current = decide(http, current)
        if action in {"denied", "revoked"}:
            current = decide(http, current, action)
        elif action == "cancelled":
            current = ack(http.post(PROJECT + "/agent/turns/p4-turn/cancel", json={"turn_id": "p4-turn"}))
        response, _ = continued(http, family, current)
        assert response.status_code in {200, 409}, response.text
        http.app.state.atelier_generation_dispatcher.wake()
        assert http.app.state.atelier_generation_dispatcher.wait_for_idle(2)
        assert http.app.state.atelier_storage.get_project("p").generation_attempts == []
        assert boundary.calls == []


@pytest.mark.parametrize("family", ["turn", "loop"])
def test_changed_first_frame_cannot_reuse_original_approval(tmp_path, boundary, family):
    with application(tmp_path, boundary) as http:
        preview, _ = start(http, family)
        approved = decide(http, preview)
        (tmp_path / "uploads/frame.png").write_bytes(rgb_png_bytes("changed-after-approval"))
        response, _ = continued(http, family, approved)
        assert response.status_code == 409, response.text
        assert http.app.state.atelier_storage.get_project("p").generation_attempts == []
        assert boundary.calls == []


@pytest.mark.parametrize("mode", ["lost_ack", "missing_receipt"])
def test_missing_provider_receipt_is_unknown_and_original_read_replay_never_resubmits(tmp_path, boundary, mode):
    boundary.mode = mode
    with application(tmp_path, boundary) as http:
        preview, _ = start(http, "turn", stream=True)
        approved = decide(http, preview)
        response, body = continued(http, "turn", approved, stream=True)
        ack(response)
        result = drive(http, preview)
        assert result["attempt"]["status"] == "needs_confirmation"
        assert result["attempt"]["provider_execution"]["phase"] == "submitting"
        assert result["attempt"]["provider_execution"]["handle"] is None
        assert [boundary.count(k) for k in ("submit", "query", "fetch")] == [1, 0, 0]
    with application(tmp_path, boundary, seed=False) as reopened:
        restored = read_request(reopened, preview)
        assert restored["attempt"] == result["attempt"]
        assert ack(reopened.get(PROJECT + "/agent/turns/p4-turn"))["turn"]["id"] == "p4-turn"
        replay = ack(reopened.post(PROJECT + "/agent/turns/p4-turn/continue", json=body))
        assert replay["replayed"] and not replay["scheduled"]
        reopened.app.state.atelier_generation_dispatcher.wake()
        assert reopened.app.state.atelier_generation_dispatcher.wait_for_idle(2)
        assert [boundary.count(k) for k in ("submit", "query", "fetch")] == [1, 0, 0]


@pytest.mark.parametrize("family", ["turn", "loop"])
def test_real_store_reopen_queries_original_handle_without_resubmit(tmp_path, boundary, family):
    boundary.query_status = "RUNNING"
    with application(tmp_path, boundary) as http:
        preview, _ = start(http, family)
        approved = decide(http, preview)
        response, _ = continued(http, family, approved)
        ack(response)
        result = drive(http, preview, terminal=False)
        handle = result["attempt"]["provider_execution"]["handle"]
        assert handle["task_id"] == "p4-task-1" and boundary.count("submit") == 1
    boundary.query_status = "SUCCEEDED"
    with application(tmp_path, boundary, seed=False) as reopened:
        final = drive(reopened, preview)
        assert final["attempt"]["status"] == "completed"
        assert final["attempt"]["provider_execution"]["handle"] == handle
        assert final["attempt"]["attempt_id"] == result["attempt"]["attempt_id"]
        assert boundary.count("submit") == boundary.count("fetch") == 1
        assert boundary.count("query") >= 1


def disconnect_at_admission(iterator, cancel, spec):
    response = AgentEventStreamResponse(iterator, cancel, project_id="p", turn_id="p4-turn")
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
                if b"event: generation_admitted\n" in body:
                    reached.set()
                    while not cancel.is_set():
                        await anyio.sleep(0)
        with anyio.fail_after(3):
            await response({"type": "http", "asgi": {"spec_version": spec}}, receive, send)
    anyio.run(scenario)
    assert cancel.is_set() and any(b"event: generation_admitted\n" in b for b in bodies)
    assert not any(b"event: generation_schedule_requested\n" in b for b in bodies)


@pytest.mark.parametrize("family", ["turn", "loop"])
@pytest.mark.parametrize("spec", ["2.0", "2.4"])
def test_actual_asgi_disconnect_after_admission_does_not_grant_dispatch_rights(tmp_path, boundary, family, spec):
    with application(tmp_path, boundary) as http:
        preview, _ = start(http, family)
        approved = decide(http, preview)
        cancel = threading.Event()
        if family == "loop":
            iterator = http.app.state.atelier_agent_loop_service.iter_resume_loop("p", "p4-turn", cancel_event=cancel)
        else:
            command = AdmitApprovedAgentGenerationCommand.model_validate({**identity(approved),
                "client_request_id": binding(approved)["preview_public_record"]["raw_request"]["client_request_id"]})
            iterator = http.app.state.atelier_agent_executor.iter_continue_call("p", command, cancel_event=cancel)
        disconnect_at_admission(iterator, cancel, spec)
        result = read_request(http, preview)
        assert result["attempt"]["provider_execution"]["phase"] == "accepted"
        http.app.state.atelier_generation_dispatcher.wake()
        assert http.app.state.atelier_generation_dispatcher.wait_for_idle(2)
        assert boundary.calls == []
        assert ack(http.get(PROJECT + f"/agent/{family}s/p4-turn"))["turn"]["id"] == "p4-turn"
    with application(tmp_path, boundary, seed=False) as reopened:
        assert read_request(reopened, preview)["attempt"] == result["attempt"]
        reopened.app.state.atelier_generation_dispatcher.wake()
        assert reopened.app.state.atelier_generation_dispatcher.wait_for_idle(2)
        assert boundary.calls == []


def test_external_model_planner_preserves_explicit_params_cost_claims_and_second_denial(tmp_path, boundary, monkeypatch):
    calls = []
    proposal = {"node_id": "video", "model_id": "wan/wan2.7-video#i2v", "mode": "i2v", "prompt": "Slow sunrise",
                "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"}, "params": PARAMS}
    plans = [{"assistant_message": "Read first", "tool_calls": [{"tool_name": "canvas.readProject", "arguments": {}}]},
             {"assistant_message": "Review generation", "tool_calls": [{"tool_name": "generation.createVideoCandidates", "arguments": proposal}]},
             {"assistant_message": "Review another take", "tool_calls": [{"tool_name": "generation.createVideoCandidates", "arguments": {**proposal, "prompt": "Another sunrise"}}]}]
    class ExternalChat:
        provider = "fixture-provider"
        def __init__(self, *, env=None):
            self.env = env
        def _get_default_model(self):
            return "fixture-model"
        def chat_result(self, messages, **kwargs):
            from types import SimpleNamespace
            calls.append((messages, kwargs))
            assert kwargs["max_completion_tokens"] == 1024 and kwargs["timeout"] == 60.0
            return SimpleNamespace(content=json.dumps(plans[len(calls) - 1]), requested_model="fixture-model",
                provider_model="fixture-provider-model", prompt_tokens=10, completion_tokens=3, total_tokens=13)
    monkeypatch.setattr("src.apps.comic_gen.llm_adapter.LLMAdapter", ExternalChat)
    with application(tmp_path, boundary) as http:
        preview, _ = start(http, "loop", stream=True, planner="model_adapter")
        assert len(calls) == 2 and boundary.calls == []
        assert binding(preview)["provider_execution"]["intent"]["params"] == PARAMS
        approved = decide(http, preview)
        response, _ = continued(http, "loop", approved, stream=True)
        ack(response)
        result = drive(http, preview)
        assert result["attempt"]["status"] == "completed"
        sent = json.loads(next(r.body for k, r in boundary.calls if k == "submit"))
        assert sent["input"]["negative_prompt"] == ""
        assert sent["parameters"] == {k: ("720P" if k == "resolution" else v) for k, v in PARAMS.items() if k != "negative_prompt"}
        second = ack(http.post(PROJECT + "/agent/loops/p4-turn/resume/stream", json={"turn_id": "p4-turn"}))
        assert len(calls) == 3 and second["turn"]["status"] == "waiting_approval"
        denied = decide(http, second, "denied")
        assert denied["turn"]["status"] == "failed"
        assert denied["turn"]["tool_calls"][-1]["approval_record"]["decision"] == "denied"
        restored = ack(http.get(PROJECT + "/agent/loops/p4-turn"))
        invocations = restored["turn"]["planner_invocations"]
        assert len(invocations) == 3 and all(i["charged_completion_tokens"] == 3 for i in invocations)
        assert boundary.count("submit") == 1
        assert all(not n["media_urls"] for n in restored["project"]["nodes"] if n["id"] == "video")


@pytest.mark.parametrize("family", ["turn", "loop"])
def test_request_content_conflict_and_wrong_approval_identity_never_submit(tmp_path, boundary, family):
    with application(tmp_path, boundary) as http:
        preview, original = start(http, family)
        changed = {**original, "user_message": "Different request"}
        conflict = http.post(PROJECT + f"/agent/{family}s", json=changed)
        assert conflict.status_code == 409, conflict.text
        # SSE headers are already sent: the same conflict is a safe event.
        stream_conflict = http.post(PROJECT + f"/agent/{family}s/stream", json=changed)
        assert stream_conflict.status_code == 200
        events = [json.loads(line[6:]) for line in stream_conflict.text.splitlines() if line.startswith("data: ")]
        assert len(events) == 1 and events[0]["type"] == "agent_error"
        assert events[0]["code"] == "atelier_agent_conflict"
        wrong = http.post(PROJECT + "/agent/turns/p4-turn/approval", json={
            **identity(preview), "authorization_hash": "0" * 64, "decision": "approved",
        })
        assert wrong.status_code == 409, wrong.text
        current = ack(http.get(PROJECT + f"/agent/{family}s/p4-turn"))
        assert current["turn"]["status"] == "waiting_approval"
        assert current["project"]["generation_attempts"] == [] and boundary.calls == []


@pytest.mark.parametrize("family", ["turn", "loop"])
def test_identical_turn_ids_cannot_transfer_approval_across_actual_apps(tmp_path, boundary, family):
    with application(tmp_path / "a", boundary) as first:
        first_preview, _ = start(first, family)
        first_approved = decide(first, first_preview)
    with application(tmp_path / "b", boundary) as second:
        second_preview, _ = start(second, family)
        assert binding(first_preview)["agent_authorization_ref"] != binding(second_preview)["agent_authorization_ref"]
        supplied = {**identity(first_approved), "expected_revision": second_preview["project"]["revision"], "decision": "approved"}
        rejected = second.post(PROJECT + "/agent/turns/p4-turn/approval", json=supplied)
        assert rejected.status_code == 409, rejected.text
        assert second.app.state.atelier_storage.get_project("p").generation_attempts == []
        assert boundary.calls == []


def test_remote_middleware_prevents_every_entry_from_bypassing_access(tmp_path, boundary):
    with application(tmp_path, boundary, remote=True) as http:
        body = {"expected_revision": 0, "client_turn_id": "p4-turn", "user_message": "生成视频", "selected_node_id": "video"}
        for route in ("turns", "turns/stream", "loops", "loops/stream", "turns/p4-turn/continue", "turns/p4-turn/continue/stream", "loops/p4-turn/resume", "loops/p4-turn/resume/stream"):
            denied = http.post(PROJECT + "/agent/" + route, json=body)
            assert denied.status_code == 401, denied.text
        assert http.get(PROJECT + "/agent/turns/p4-turn").status_code == 401
        assert http.app.state.atelier_storage.get_project("p").agent_turns == []
        assert boundary.calls == []
