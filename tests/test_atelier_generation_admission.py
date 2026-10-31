"""Durable Atelier admission commits one candidate and its original receipt."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.apps.atelier.generation_admission import AtelierGenerationAdmissionService
from src.apps.atelier.generation_commands import CreateVideoCandidateCommand, ManualVideoCandidateBatchCommand
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.product_skills import resolve_product_skill_snapshot
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.json_storage import StorageConflictError, StorageError

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
SCOPE = ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing")


def request(**changes):
    return CreateVideoCandidateCommand(**{
        "expected_revision": 0, "client_request_id": "request-1",
        "model_id": "wan/wan2.7-video#i2v", "mode": "i2v", "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"}, **changes,
    })


@pytest.fixture
def app(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, nodes=[
        AtelierNode(id="image", project_id="p", type="image", status="completed", media_urls=["uploads/frame.png"]),
        AtelierNode(id="video", project_id="p", type="video", data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]}),
        AtelierNode(id="other-video", project_id="p", type="video", data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]}),
    ]))
    image = tmp_path / "uploads/frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG)
    bind = Mock(return_value=ResolvedCredential(SCOPE, "bound-key", "test-only-secret"))
    service = AtelierGenerationAdmissionService(store, credential_provider=bind, clock=lambda: 100.0)
    yield service, store, bind, image
    store.close()


def test_pending_reference_selected_for_video_admission_fails_before_media_or_binding(app, monkeypatch):
    service, store, bind, _ = app

    def add_pending(project: AtelierProject) -> None:
        project.nodes.append(AtelierNode(id="placeholder", project_id="p", type="image"))
        video = next(node for node in project.nodes if node.id == "video")
        video.data = {
            "reference_node_ids": ["placeholder"],
            "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "placeholder", "source_kind": "image", "status": "pending_media"}],
        }

    store.apply_project_command("p", expected_revision=0, command=add_pending)
    freeze = Mock(side_effect=AssertionError("Pending references must fail before freezing media"))
    monkeypatch.setattr("src.apps.atelier.generation_preview.freeze_media", freeze)

    with pytest.raises(ValueError, match="atelier_reference_pending_media"):
        service.admit("p", "video", request(expected_revision=1, first_frame={"node_id": "placeholder", "media_url": "uploads/missing.png"}))

    freeze.assert_not_called()
    bind.assert_not_called()
    assert store.get_project("p").revision == 1


def test_unrelated_pending_reference_does_not_block_explicit_resolved_video_selection(app):
    service, store, _bind, _ = app

    def add_unrelated_pending(project: AtelierProject) -> None:
        project.nodes.append(AtelierNode(id="placeholder", project_id="p", type="image"))
        video = next(node for node in project.nodes if node.id == "video")
        video.data["reference_node_ids"] = ["image", "placeholder"]
        video.data["pending_reference_nodes"] = [{"schema_version": 1, "source_node_id": "placeholder", "source_kind": "image", "status": "pending_media"}]

    store.apply_project_command("p", expected_revision=0, command=add_unrelated_pending)

    ack = service.admit("p", "video", request(expected_revision=1))

    assert ack.project.revision == 2


def test_admission_saves_candidate_attempt_receipt_once_and_does_not_select_it(app):
    service, store, bind, _ = app
    ack = service.admit("p", "video", request())
    assert not ack.replayed and ack.new_candidate_ids == [ack.candidate["id"]]
    assert ack.project.revision == 1
    assert len(ack.project.generation_attempts) == len(ack.project.generation_request_receipts) == 1
    assert ack.attempt.provider_execution.phase == "accepted"
    assert ack.attempt.provider_execution.intent["params"]["duration"] == 5
    assert ack.attempt.provider_execution.intent["params"]["resolution"] == "1080p"
    node = next(n for n in ack.project.nodes if n.id == "video")
    assert "selected_candidate_id" not in node.data and node.media_urls == []
    assert ack.receipt.base_revision == 0 and ack.receipt.committed_revision == 1
    assert ack.receipt.execution_fingerprint == ack.attempt.provider_execution.fingerprint
    assert store.get_project("p") == ack.project
    assert "test-only-secret" not in store.path.read_text()
    bind.assert_called_once_with(provider_model_id="wan2.7-i2v", gateway="dashscope")


def test_admission_binds_skill_snapshot_to_request_identity_and_candidate(app):
    service, store, _bind, _ = app
    snapshot = resolve_product_skill_snapshot("story-scout")
    ack = service.admit("p", "video", request(), skill_snapshot=snapshot)
    binding = ack.candidate["skill_binding"]
    assert binding["skill_name"] == "story-scout"
    assert binding["skill_content_hash"] == snapshot.content_hash
    replay = service.admit("p", "video", request(expected_revision=0), skill_snapshot=snapshot)
    assert replay.replayed
    assert replay.receipt.request_fingerprint == ack.receipt.request_fingerprint


def test_replay_and_readback_do_not_read_changed_media_or_current_credentials(app):
    service, _, bind, image = app
    original = service.admit("p", "video", request())
    image.unlink()
    bind.side_effect = AssertionError("Replay must not resolve current credentials")
    replay = service.admit("p", "video", request(expected_revision=99))
    readback = service.read_request("p", "request-1")
    for result in [replay, readback]:
        assert result.replayed and result.new_candidate_ids == []
        assert result.receipt == original.receipt and result.attempt == original.attempt


def test_same_request_cannot_change_target_or_content_and_new_id_can_add_candidate(app):
    service, store, _, _ = app
    original = service.admit("p", "video", request())
    for node_id, command in [("other-video", request()), ("video", request(prompt="Different"))]:
        with pytest.raises(StorageConflictError):
            service.admit("p", node_id, command)
    second = service.admit("p", "video", request(expected_revision=1, client_request_id="request-2"))
    assert second.candidate["id"] != original.candidate["id"]
    assert len(store.get_project("p").generation_attempts) == 2
    assert store.get_project("p").revision == 2


def test_concurrent_same_id_has_only_one_new_member_and_binding(app):
    service, store, bind, _ = app
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.admit("p", "video", request()), range(2)))
    assert sorted(result.replayed for result in results) == [False, True]
    assert sum(len(result.new_candidate_ids) for result in results) == 1
    assert len(store.get_project("p").generation_attempts) == 1
    bind.assert_called_once_with(provider_model_id="wan2.7-i2v", gateway="dashscope")


def test_failed_commit_does_not_return_dispatch_right_or_leave_partial_candidate(app, monkeypatch):
    service, store, _, _ = app
    before = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError):
        service.admit("p", "video", request())
    assert store.path.read_bytes() == before
    assert store.get_project("p").generation_attempts == []
    assert not next(n for n in store.get_project("p").nodes if n.id == "video").data.get("candidates")


def test_stale_revision_rejects_before_media_or_binding(app, monkeypatch):
    service, store, bind, _ = app
    freeze = Mock(side_effect=AssertionError("A stale request must not freeze media"))
    monkeypatch.setattr("src.apps.atelier.generation_preview.freeze_media", freeze)
    with pytest.raises(StorageConflictError):
        service.admit("p", "video", request(expected_revision=4))
    assert store.get_project("p").revision == 0
    freeze.assert_not_called()
    bind.assert_not_called()


def test_restart_replays_original_identity_without_binding_or_media(app):
    service, store, _, image = app
    original = service.admit("p", "video", request())
    root = store.data_dir
    store.close()
    image.unlink()
    reopened = AtelierStorage(root)
    try:
        restored = AtelierGenerationAdmissionService(reopened, credential_provider=Mock(side_effect=AssertionError("No binding")))
        replay = restored.admit("p", "video", request())
        assert replay.replayed and replay.new_candidate_ids == []
        assert replay.receipt == original.receipt
        assert replay.attempt == original.attempt
    finally:
        reopened.close()


def test_terminal_owner_tombstone_readback_never_recreates_candidate(app):
    service, store, _, _ = app
    original = service.admit("p", "video", request())
    path, root = store.path, store.data_dir
    store.close()
    # Seed a persisted, cancelled terminal record; execution updates are a
    # separate service. Readback must work even after its owner was deleted.
    payload = json.loads(path.read_text())
    project = payload["p"]
    project["generation_attempts"][0]["status"] = "cancelled"
    project["generation_attempts"][0]["provider_execution"]["phase"] = "cancelled"
    project["nodes"] = [n for n in project["nodes"] if n["id"] != "video"]
    path.write_text(json.dumps(payload))
    reopened = AtelierStorage(root)
    try:
        restored = AtelierGenerationAdmissionService(reopened, credential_provider=Mock(side_effect=AssertionError("No binding")))
        for ack in [restored.read_request("p", "request-1"), restored.admit("p", "video", request())]:
            assert ack.receipt == original.receipt
            assert ack.candidate is None and ack.new_candidate_ids == []
            assert ack.reason == "generation_owner_missing" and ack.replayed
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["selection", "layout", "title", "receipt", "owner", "existing_attempt"])
def test_admission_transaction_rejects_unrelated_or_existing_state_changes(app, monkeypatch, change):
    service, store, _, _ = app
    service.admit("p", "video", request())
    original = store.apply_generation_admission
    before = store.path.read_bytes()

    def corrupt(project):
        node = next(n for n in project.nodes if n.id == "video")
        if change == "selection":
            node.data["selected_candidate_id"] = project.generation_attempts[-1].candidate_id
        elif change == "layout":
            node.x += 10
        elif change == "title":
            project.title = "Unexpected edit"
        elif change == "receipt":
            project.generation_request_receipts[-1].client_request_id = "unrelated-request"
        elif change == "owner":
            project.nodes.append(AtelierNode(id="unexpected", project_id="p", type="image"))
        else:
            project.generation_attempts[0].attempt_revision += 1

    def wrapped(project_id, *, command, **kwargs):
        def altered(project):
            command(project)
            corrupt(project)
        return original(project_id, command=altered, **kwargs)

    monkeypatch.setattr(store, "apply_generation_admission", wrapped)
    with pytest.raises(StorageError):
        service.admit("p", "video", request(expected_revision=1, client_request_id="request-2"))
    assert store.path.read_bytes() == before
    assert len(store.get_project("p").generation_attempts) == 1


@pytest.mark.parametrize("action", ["nested", "close"])
def test_binding_callback_cannot_commit_after_reentrant_change_or_shutdown(app, action):
    service, store, bind, _ = app
    before = store.path.read_bytes()
    credential = bind.return_value

    def resolve(*, provider_model_id, gateway):
        if action == "nested":
            store.apply_project_command("p", expected_revision=0, command=lambda p: setattr(p, "title", "nested"))
        else:
            store.close()
        return credential

    bind.side_effect = resolve
    with pytest.raises(StorageConflictError):
        service.admit("p", "video", request())
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("failure", ["missing_target", "wrong_type", "not_attached", "wrong_url", "unsupported_params", "missing_media"])
def test_owner_source_or_parameter_failure_cannot_admit(app, failure):
    service, store, bind, image = app
    target = "video"
    command = request()
    if failure == "missing_target":
        target = "absent"
    elif failure == "wrong_type":
        target = "image"
    elif failure == "not_attached":
        store.apply_project_command("p", expected_revision=0, command=lambda p: next(n for n in p.nodes if n.id == "video").data.clear())
        command = request(expected_revision=1)
    elif failure == "wrong_url":
        command = request(first_frame={"node_id": "image", "media_url": "uploads/other.png"})
    elif failure == "unsupported_params":
        command = request(params={"duration": 16})
    else:
        image.unlink()
    with pytest.raises((ValueError, StorageError)):
        service.admit("p", target, command)
    assert store.get_project("p").generation_attempts == []
    bind.assert_not_called()


def batch_request(**changes):
    return ManualVideoCandidateBatchCommand(**{
        "expected_revision": 0, "client_request_id": "batch-1",
        "model_id": "wan/wan2.7-video#i2v", "mode": "i2v", "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"},
        "batch_size": 3,
        **changes,
    })


def test_manual_batch_admission_commits_one_v2_receipt_with_ordered_members(app):
    service, store, bind, _ = app
    ack = service.admit("p", "video", batch_request(params={"seed": 0}))

    assert not ack.replayed
    assert len(ack.members) == 3
    assert len(ack.new_candidate_ids) == 3
    assert ack.project.revision == 1
    assert ack.receipt.schema_version == 2
    assert not hasattr(ack.receipt, "attempt_id") or ack.receipt.attempt_id is None
    assert [member.candidate_id for member in ack.receipt.receipt_members] == ack.new_candidate_ids
    assert [member.attempt.attempt_id for member in ack.members] == [member.attempt_id for member in ack.receipt.receipt_members]
    assert len({member.attempt.provider_execution.fingerprint for member in ack.members}) == 3
    assert {member.attempt.provider_execution.intent["params"].get("seed") for member in ack.members} == {0}
    node = next(n for n in ack.project.nodes if n.id == "video")
    assert [item["id"] for item in node.data["candidates"]] == ack.new_candidate_ids
    assert store.get_project("p") == ack.project
    bind.assert_called_once_with(provider_model_id="wan2.7-i2v", gateway="dashscope")


def test_manual_batch_replay_does_not_bind_or_schedule_new_candidates(app):
    service, _, bind, image = app
    original = service.admit("p", "video", batch_request(batch_size=2))
    image.unlink()
    bind.side_effect = AssertionError("Replay must not resolve credentials")

    replay = service.admit("p", "video", batch_request(expected_revision=99, batch_size=2))
    readback = service.read_request("p", "batch-1")

    for result in [replay, readback]:
        assert result.replayed
        assert result.new_candidate_ids == []
        assert result.receipt == original.receipt
        assert [member.attempt for member in result.members] == [member.attempt for member in original.members]

def test_manual_batch_commit_failure_publishes_no_partial_members(app, monkeypatch):
    service, store, _, _ = app
    before = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))

    with pytest.raises(StorageError):
        service.admit("p", "video", batch_request(batch_size=3))

    assert store.path.read_bytes() == before
    saved = store.get_project("p")
    assert saved.generation_attempts == []
    assert saved.generation_request_receipts == []
    assert not next(n for n in saved.nodes if n.id == "video").data.get("candidates")


def test_concurrent_same_batch_id_has_one_new_batch_and_changed_payload_conflicts(app):
    service, store, bind, _ = app
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.admit("p", "video", batch_request(batch_size=2)), range(2)))

    assert sorted(result.replayed for result in results) == [False, True]
    assert sorted(len(result.new_candidate_ids) for result in results) == [0, 2]
    assert len(store.get_project("p").generation_attempts) == 2
    assert len(store.get_project("p").generation_request_receipts) == 1
    bind.assert_called_once_with(provider_model_id="wan2.7-i2v", gateway="dashscope")

    with pytest.raises(StorageConflictError):
        service.admit("p", "video", batch_request(expected_revision=1, batch_size=3))
    with pytest.raises(StorageConflictError):
        service.admit("p", "video", batch_request(expected_revision=1, batch_size=2, prompt="Changed"))
