"""Streaming iterators expose real Agent executor stages without changing sync ACKs."""

import base64
import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.apps.atelier.agent_commands import AdmitApprovedAgentGenerationCommand, RecordAgentApprovalCommand
from src.apps.atelier.agent_executor import AtelierAgentExecutor
from src.apps.atelier.agent_turn_commands import StartAgentTurnCommand
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_tasks import ProviderScope

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
SCOPE = ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing")


@pytest.fixture
def app(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, nodes=[
        AtelierNode(id="image", project_id="p", type="image", status="completed", media_urls=["uploads/frame.png"]),
        AtelierNode(id="video", project_id="p", type="video", prompt="Slow sunrise", data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]}),
    ]))
    image = tmp_path / "uploads/frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=store._writer_lock, runtime_lock=store._lock)
    bind = Mock(side_effect=lambda *, provider_model_id, gateway: credentials.bind(SCOPE, "test-only-secret"))
    scheduled: list[tuple[str, str]] = []
    executor = AtelierAgentExecutor(
        store,
        credential_provider=bind,
        generation_scheduler=lambda project_id, attempt_id: scheduled.append((project_id, attempt_id)),
        clock=itertools.count(10.0).__next__,
        id_factory=(f"{prefix}-{index}" for index in itertools.count(1) for prefix in ("call", "approval", "request")).__next__,
    )
    yield executor, store, bind, scheduled
    store.close()


def _start(expected_revision: int = 0, *, client_turn_id: str = "turn-1", message: str = "生成视频候选"):
    return StartAgentTurnCommand(
        expected_revision=expected_revision,
        client_turn_id=client_turn_id,
        user_message=message,
        selected_node_id="video",
    )


def _approval(ack, *, decision="approved", expected_revision=None):
    ref = ack.call.preview_binding.agent_authorization_ref
    return RecordAgentApprovalCommand(
        expected_revision=ack.project.revision if expected_revision is None else expected_revision,
        turn_id=ack.turn.id,
        call_id=ack.call.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        decision=decision,
    )


def _continue(ack, *, expected_revision=None):
    ref = ack.call.preview_binding.agent_authorization_ref
    return AdmitApprovedAgentGenerationCommand(
        expected_revision=ack.project.revision if expected_revision is None else expected_revision,
        turn_id=ack.turn.id,
        call_id=ack.call.call_id,
        approval_id=ref.approval_id,
        authorization_hash=ref.authorization_hash,
        client_request_id=ack.call.preview_binding.preview_public_record["raw_request"]["client_request_id"],
    )


def _drain(events):
    return list(events)



def _assert_storage_readable_from_another_thread(store):
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(store.get_project, "p").result(timeout=2).id == "p"

def _terminal(events):
    terminal = events[-1]
    assert terminal.type == "turn_terminal"
    assert terminal.ack is not None
    return terminal.ack


def test_iter_start_yields_real_stages_and_terminal_ack(app):
    executor, store, _, _ = app
    stream = executor.iter_start_turn("p", _start())
    first = next(stream)
    assert first.type == "turn_planning_started"
    assert store.get_project("p").agent_turns == []
    rest = [*stream]
    assert [event.type for event in [first, *rest]] == [
        "turn_planning_started",
        "turn_plan_ready",
        "turn_preview_prepared",
        "turn_pending_saved",
        "turn_terminal",
    ]
    ack = _terminal(rest)
    assert ack.turn.id == "turn-1"
    assert [event.type for event in ack.events] == ["turn_preview_prepared", "turn_pending_saved"]


def test_sync_start_drains_stream_without_changing_ack_events(app):
    executor, _, _, _ = app
    ack = executor.start_turn("p", _start())
    assert [event.type for event in ack.events] == ["turn_preview_prepared", "turn_pending_saved"]
    assert all(event.type != "turn_terminal" for event in ack.events)


def test_start_replay_yields_before_credentials_or_media(app):
    executor, _store, bind, _ = app
    first = executor.start_turn("p", _start())
    bind.reset_mock()
    bind.side_effect = AssertionError("replay must not bind credentials")
    events = _drain(executor.iter_start_turn("p", _start(expected_revision=99)))
    assert [event.type for event in events] == ["turn_start_replayed", "turn_terminal"]
    assert events[-1].ack.turn == first.turn
    assert bind.call_count == 0


def test_start_cancel_before_preview_writes_zero(app):
    executor, store, bind, _ = app
    cancel = threading.Event()
    stream = executor.iter_start_turn("p", _start(), cancel_event=cancel)
    assert next(stream).type == "turn_planning_started"
    cancel.set()
    events = list(stream)
    assert [event.type for event in events] == ["turn_closed"]
    assert store.get_project("p").agent_turns == []
    bind.assert_not_called()


def test_start_cancel_after_preview_before_commit_writes_zero(app):
    executor, store, bind, _ = app
    cancel = threading.Event()
    stream = executor.iter_start_turn("p", _start(), cancel_event=cancel)
    assert [next(stream).type, next(stream).type, next(stream).type] == ["turn_planning_started", "turn_plan_ready", "turn_preview_prepared"]
    cancel.set()
    events = list(stream)
    assert [event.type for event in events] == ["turn_closed"]
    assert store.get_project("p").agent_turns == []
    assert bind.call_count == 1


def test_concurrent_iter_start_allows_duplicate_prepare_but_one_durable_turn(app):
    executor, store, bind, _ = app
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _drain(executor.iter_start_turn("p", _start())), range(2)))
    terminal_acks = [_terminal(events) for events in results]
    assert sorted(ack.replayed for ack in terminal_acks) == [False, True]
    assert len(store.get_project("p").agent_turns) == 1
    assert bind.call_count >= 1




def test_iterator_yields_do_not_hold_storage_lock(app):
    executor, store, _, _ = app
    start_stream = executor.iter_start_turn("p", _start())
    for _ in range(4):
        event = next(start_stream)
        assert event.type != "turn_terminal"
        _assert_storage_readable_from_another_thread(store)
    assert next(start_stream).type == "turn_terminal"

    started = executor.read_turn("p", "turn-1")
    approved = executor.approve_call("p", _approval(started))
    continue_stream = executor.iter_continue_call("p", _continue(approved))
    event = next(continue_stream)
    assert event.type == "generation_admitted"
    _assert_storage_readable_from_another_thread(store)
    assert [event.type for event in continue_stream] == ["generation_schedule_requested", "turn_terminal"]


def test_iter_approve_and_continue_yield_terminal_ack(app):
    executor, _, _, scheduled = app
    started = executor.start_turn("p", _start())
    approved_events = _drain(executor.iter_approve_call("p", _approval(started)))
    assert [event.type for event in approved_events] == ["approval_recorded", "turn_terminal"]
    approved = _terminal(approved_events)
    continue_events = _drain(executor.iter_continue_call("p", _continue(approved)))
    assert [event.type for event in continue_events] == ["generation_admitted", "generation_schedule_requested", "turn_terminal"]
    admitted = _terminal(continue_events)
    assert admitted.scheduled is True
    assert scheduled == [("p", admitted.generation.attempt.attempt_id)]


def test_iter_continue_cancel_before_admission_writes_zero(app):
    executor, store, _, scheduled = app
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    cancel = threading.Event()
    cancel.set()
    events = _drain(executor.iter_continue_call("p", _continue(approved), cancel_event=cancel))
    assert [event.type for event in events] == ["turn_closed"]
    assert store.get_project("p").generation_attempts == []
    assert scheduled == []


def test_iter_continue_cancel_after_admission_keeps_ledger_and_skips_schedule(app):
    executor, store, _, scheduled = app
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    cancel = threading.Event()
    stream = executor.iter_continue_call("p", _continue(approved), cancel_event=cancel)
    admitted_event = next(stream)
    assert admitted_event.type == "generation_admitted"
    cancel.set()
    rest = list(stream)
    assert [event.type for event in rest] == ["generation_schedule_failed", "turn_terminal"]
    ack = _terminal(rest)
    assert ack.scheduled is False
    assert ack.schedule_error == "generation_schedule_cancelled"
    assert len(store.get_project("p").generation_attempts) == 1
    assert scheduled == []


def test_iter_continue_replay_does_not_schedule_or_emit_new_admission(app):
    executor, _, _, scheduled = app
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    first = executor.continue_call("p", _continue(approved))
    replay_events = _drain(executor.iter_continue_call("p", _continue(approved, expected_revision=999)))
    assert [event.type for event in replay_events] == ["turn_terminal"]
    replay = _terminal(replay_events)
    assert replay.replayed is True
    assert replay.generation.new_candidate_ids == []
    assert scheduled == [("p", first.generation.attempt.attempt_id)]


def test_scheduler_callback_already_running_is_not_interrupted_by_cancel(app):
    executor, _, _, scheduled = app
    entered = threading.Event()
    release = threading.Event()

    def scheduler(project_id, attempt_id):
        entered.set()
        release.wait(timeout=5)
        scheduled.append((project_id, attempt_id))
        return True

    executor._generation_scheduler = scheduler
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    cancel = threading.Event()
    events = []

    def run():
        events.extend(executor.iter_continue_call("p", _continue(approved), cancel_event=cancel))

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(timeout=5)
    cancel.set()
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert [event.type for event in events] == ["generation_admitted", "generation_schedule_requested", "turn_terminal"]
    assert _terminal(events).scheduled is True
    assert len(scheduled) == 1
