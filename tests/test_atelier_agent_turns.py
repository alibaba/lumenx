"""Agent turn executor entrypoints compose preview, approval and generation admission."""

import base64
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.apps.atelier.agent_commands import AdmitApprovedAgentGenerationCommand, RecordAgentApprovalCommand
from src.apps.atelier.agent_executor import AtelierAgentExecutor, fingerprint_agent_turn_start
from src.apps.atelier.agent_turn_commands import CancelAgentTurnCommand, StartAgentTurnCommand
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_tasks import ProviderScope
from src.utils.json_storage import StorageConflictError, StorageError

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
    yield executor, store, bind, scheduled, image, tmp_path
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




@pytest.mark.parametrize("client_turn_id", ["", "bad/id", "bad\ncontrol", "x" * 129])
def test_start_turn_command_rejects_path_unsafe_turn_ids(client_turn_id):
    with pytest.raises(ValueError):
        _start(client_turn_id=client_turn_id)


def test_start_turn_saves_pending_preview_without_generation_or_schedule(app):
    executor, store, bind, scheduled, _, _ = app
    ack = executor.start_turn("p", _start())
    assert ack.turn.id == "turn-1"
    assert ack.turn.start_request_fingerprint == fingerprint_agent_turn_start(client_turn_id="turn-1", user_message="生成视频候选", selected_node_id="video")
    assert ack.call.call_id == "call-1"
    assert ack.call.preview_binding is not None
    assert ack.call.approval_record is None
    assert ack.project.generation_attempts == []
    assert scheduled == []
    assert bind.call_count == 1
    assert [event.type for event in ack.events] == ["turn_preview_prepared", "turn_pending_saved"]
    assert "test-only-secret" not in store.path.read_text()


def test_start_turn_lost_ack_replay_skips_credentials_media_and_revision(app):
    executor, _, bind, _, image, _ = app
    first = executor.start_turn("p", _start())
    image.unlink()
    bind.side_effect = AssertionError("replay must not bind credentials")
    replay = executor.start_turn("p", _start(expected_revision=99))
    assert replay.replayed is True
    assert replay.turn == first.turn
    assert [event.type for event in replay.events] == ["turn_start_replayed"]




def test_constructor_and_start_replay_do_not_initialize_default_storage_identity(app, monkeypatch):
    _, store, bind, _, _, tmp_path = app
    creator = AtelierAgentExecutor(store, credential_provider=bind, server_storage_identity="storage-a")
    first = creator.start_turn("p", _start())
    bind.reset_mock()
    monkeypatch.setattr(
        "src.apps.atelier.agent_executor.initialize_storage_identity",
        Mock(side_effect=AssertionError("constructor/replay/read must not initialize storage identity")),
    )
    replay_executor = AtelierAgentExecutor(store, credential_provider=bind)
    replay = replay_executor.start_turn("p", _start(expected_revision=99))
    readback = replay_executor.read_turn("p", "turn-1")
    assert replay.replayed is True
    assert replay.turn == first.turn
    assert readback.turn == first.turn
    assert bind.call_count == 0
    assert not (tmp_path / "storage_identity.json").exists()


def test_stale_exact_approval_replays_but_revocation_is_not_revived(app):
    executor, store, _, _, _, _ = app
    started = executor.start_turn("p", _start())
    command = _approval(started)
    approved = executor.approve_call("p", command)
    replay = executor.approve_call("p", command)
    assert replay.replayed is True
    assert replay.project.revision == approved.project.revision
    revoke = RecordAgentApprovalCommand(**{**command.model_dump(), "expected_revision": approved.project.revision, "decision": "revoked"})
    revoked = executor.approve_call("p", revoke)
    assert revoked.call.approval_record.decision == "revoked"
    with pytest.raises(StorageConflictError):
        executor.approve_call("p", command)
    assert store.get_project("p").generation_attempts == []



def test_stale_exact_approval_after_cancel_is_not_replayed(app):
    executor, store, _, _, _, _ = app
    started = executor.start_turn("p", _start())
    command = _approval(started)
    approved = executor.approve_call("p", command)
    cancelled = executor.cancel_turn("p", CancelAgentTurnCommand(turn_id="turn-1"))
    assert cancelled.turn.status == "canceled"
    with pytest.raises(StorageConflictError):
        executor.approve_call("p", command)
    assert store.get_project("p").revision == cancelled.project.revision
    assert approved.call.approval_record.decision == "approved"

def test_start_turn_same_id_different_payload_conflicts(app):
    executor, _, _, _, _, _ = app
    executor.start_turn("p", _start())
    with pytest.raises(StorageConflictError):
        executor.start_turn("p", _start(expected_revision=1, message="生成另一个视频"))


def test_concurrent_start_turn_commits_once(app):
    executor, store, bind, _, _, _ = app
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: executor.start_turn("p", _start()), range(2)))
    assert sorted(result.replayed for result in results) == [False, True]
    assert len(store.get_project("p").agent_turns) == 1
    assert bind.call_count == 1


def test_blocked_planner_response_does_not_write_or_bind(app):
    executor, store, bind, _, _, _ = app
    with pytest.raises(ValueError, match="User message is required"):
        executor.start_turn("p", _start(message=""))
    assert store.get_project("p").agent_turns == []
    bind.assert_not_called()


def test_approve_continue_schedule_and_replay_only_once(app):
    executor, store, _, scheduled, _, _ = app
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    assert approved.call.approval_record.decision == "approved"
    assert [event.type for event in approved.events] == ["approval_recorded"]

    admitted = executor.continue_call("p", _continue(approved))
    assert admitted.generation is not None
    assert admitted.scheduled is True
    assert admitted.schedule_error is None
    assert len(admitted.generation.new_candidate_ids) == 1
    assert scheduled == [("p", admitted.generation.attempt.attempt_id)]
    assert [event.type for event in admitted.events] == ["generation_admitted", "generation_schedule_requested"]
    assert "selected_candidate_id" not in store.get_project("p").nodes[1].data

    replay = executor.continue_call("p", _continue(approved, expected_revision=999))
    assert replay.replayed is True
    assert replay.scheduled is False
    assert replay.generation.new_candidate_ids == []
    assert scheduled == [("p", admitted.generation.attempt.attempt_id)]




def test_continue_without_scheduler_reports_durable_unscheduled(app):
    executor, store, _, scheduled, _, _ = app
    executor._generation_scheduler = None
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    ack = executor.continue_call("p", _continue(approved))
    assert ack.generation is not None
    assert ack.scheduled is False
    assert ack.schedule_error == "generation_schedule_unavailable"
    assert [event.type for event in ack.events] == ["generation_admitted", "generation_schedule_failed"]
    assert ack.events[-1].message == "generation_schedule_unavailable"
    assert store.get_project("p").generation_attempts[0].attempt_id == ack.generation.attempt.attempt_id
    assert scheduled == []


def test_continue_scheduler_false_reports_not_accepted_without_throwing(app):
    executor, store, _, scheduled, _, _ = app
    executor._generation_scheduler = Mock(return_value=False)
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    ack = executor.continue_call("p", _continue(approved))
    assert ack.generation is not None
    assert ack.scheduled is False
    assert ack.schedule_error == "generation_schedule_not_accepted"
    assert [event.type for event in ack.events] == ["generation_admitted", "generation_schedule_failed"]
    assert store.get_project("p").generation_attempts[0].attempt_id == ack.generation.attempt.attempt_id
    assert scheduled == []

def test_continue_scheduler_failure_keeps_durable_admission(app):
    executor, store, _, scheduled, _, _ = app
    executor._generation_scheduler = Mock(side_effect=RuntimeError("queue down SECRET"))
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    ack = executor.continue_call("p", _continue(approved))
    assert ack.generation is not None
    assert ack.scheduled is False
    assert ack.schedule_error == "generation_schedule_failed"
    assert store.get_project("p").generation_attempts[0].attempt_id == ack.generation.attempt.attempt_id
    assert scheduled == []


def test_commit_failure_schedules_zero(app, monkeypatch):
    executor, store, _, scheduled, _, _ = app
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError):
        executor.continue_call("p", _continue(approved))
    assert scheduled == []
    assert store.get_project("p").generation_attempts == []


def test_cancel_turn_prevents_later_paid_admission(app):
    executor, store, _, scheduled, _, _ = app
    started = executor.start_turn("p", _start())
    cancelled = executor.cancel_turn("p", CancelAgentTurnCommand(turn_id="turn-1"))
    assert cancelled.turn.status == "canceled"
    with pytest.raises(StorageConflictError):
        executor.approve_call("p", _approval(started, expected_revision=store.get_project("p").revision))
    assert scheduled == []


def test_agent_turn_ack_wire_exports_for_frontend(app, tmp_path):
    executor, _, _, _, _, _ = app
    started = executor.start_turn("p", _start())
    approved = executor.approve_call("p", _approval(started))
    admitted = executor.continue_call("p", _continue(approved))
    executor.start_turn("p", _start(expected_revision=admitted.project.revision, client_turn_id="turn-2", message="生成视频候选"))
    cancelled = executor.cancel_turn("p", CancelAgentTurnCommand(turn_id="turn-2"))
    payload = {
        "started": started.model_dump(mode="json"),
        "approved": approved.model_dump(mode="json"),
        "admitted": admitted.model_dump(mode="json"),
        "cancelled": cancelled.model_dump(mode="json"),
    }
    out = tmp_path / "atelier-agent-turn-acks.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert payload["started"]["turn"]["start_request_fingerprint"]
    assert payload["admitted"]["generation"]["attempt"]["schema_version"] == 2
    print(f"Agent turn ACK wire: {out}")
