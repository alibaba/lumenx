"""Direct ASGI tests avoid TestClient's buffered response behavior."""

import threading
from types import SimpleNamespace

import anyio
import pytest

from src.apps.atelier.agent_stream_http import AgentEventStreamResponse


def event(name):
    return SimpleNamespace(model_dump=lambda **_kwargs: {"type": name, "project_id": "p", "turn_id": "t"})


@pytest.mark.parametrize("spec", ["2.0", "2.4"])
def test_disconnect_stops_next_stage_and_closes_iterator_on_all_asgi_versions(spec):
    cancel, closed = threading.Event(), threading.Event()
    stages = []
    def work():
        try:
            stages.append("first")
            yield event("turn_planning_started")
            stages.append("forbidden-next-stage")
            yield event("turn_plan_ready")
        finally:
            closed.set()
    response = AgentEventStreamResponse(work(), cancel, project_id="p", turn_id="t")
    async def scenario():
        first_body = anyio.Event()
        async def receive():
            await first_body.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                first_body.set()
                # Hold this send until the listener has observed disconnect.
                with anyio.fail_after(2):
                    while not cancel.is_set():
                        await anyio.sleep(0)
        await response({"type": "http", "asgi": {"spec_version": spec}}, receive, send)
    anyio.run(scenario)
    assert cancel.is_set() and closed.is_set()
    assert stages == ["first"]


def test_disconnect_signals_a_running_callback_then_joins_before_generator_close():
    cancel, entered, finished, closed = (threading.Event() for _ in range(4))
    stages = []
    def work():
        try:
            yield event("turn_planning_started")
            entered.set()
            assert cancel.wait(2), "listener did not signal the running callback"
            finished.set()
            yield event("turn_plan_ready")
            stages.append("forbidden-next-stage")
        finally:
            closed.set()
    response = AgentEventStreamResponse(work(), cancel, project_id="p", turn_id="t")
    async def scenario():
        async def receive():
            assert await anyio.to_thread.run_sync(entered.wait, 2)
            return {"type": "http.disconnect"}
        async def send(_message):
            pass
        with anyio.fail_after(4):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    anyio.run(scenario)
    assert finished.is_set() and closed.is_set()
    assert stages == []


def test_send_failure_also_signals_and_closes_without_advancing_iterator():
    cancel, closed = threading.Event(), threading.Event()
    advanced = []
    def work():
        try:
            yield event("turn_planning_started")
            advanced.append(True)
        finally:
            closed.set()
    response = AgentEventStreamResponse(work(), cancel, project_id="p", turn_id="t")
    async def scenario():
        async def receive():
            await anyio.sleep_forever()
        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("PRIVATE-TRANSPORT-ERROR")
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    anyio.run(scenario)
    assert cancel.is_set() and closed.is_set() and not advanced


def test_normal_completion_closes_iterator_and_stops_disconnect_listener():
    cancel, closed = threading.Event(), threading.Event()
    bodies = []
    def work():
        try:
            yield event("turn_terminal")
        finally:
            closed.set()
    response = AgentEventStreamResponse(work(), cancel, project_id="p", turn_id="t")
    async def scenario():
        async def receive():
            await anyio.sleep_forever()
        async def send(message):
            bodies.append(message)
        with anyio.fail_after(2):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    anyio.run(scenario)
    assert closed.is_set() and cancel.is_set()
    assert bodies[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
