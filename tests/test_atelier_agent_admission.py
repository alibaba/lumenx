"""Agent approval and paid admission must share a durable transaction."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.apps.atelier.models import AtelierAgentTurn, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageError
from src.utils.json_storage import StorageConflictError


@pytest.mark.parametrize("entrypoint", ["project", "upload"])
def test_ordinary_commands_cannot_create_agent_authority(tmp_path, entrypoint):
    store = AtelierStorage(tmp_path)
    try:
        store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0))
        before = store.path.read_bytes()

        def mutate(project):
            project.agent_turns.append(AtelierAgentTurn(id="turn", project_id="p"))

        with pytest.raises(StorageError, match="Agent"):
            if entrypoint == "project":
                store.apply_project_command("p", expected_revision=0, command=mutate)
            else:
                store.apply_upload_command(
                    "p", expected_revision=0, client_request_id="upload-request",
                    request_fingerprint="a" * 64, command=mutate,
                )
        assert store.path.read_bytes() == before
        assert store.get_project("p").agent_turns == []
    finally:
        store.close()


def test_agent_admission_is_a_typed_storage_entrypoint():
    assert callable(getattr(AtelierStorage, "apply_agent_generation_admission", None))


@pytest.fixture
def approved(tmp_path):
    from src.apps.atelier.agent_authorization import build_agent_generation_binding
    from src.apps.atelier.agent_commands import AdmitApprovedAgentGenerationCommand, RecordAgentApprovalCommand
    from src.apps.atelier.generation_preview import build_generation_preview
    from src.apps.atelier.models import AtelierAgentToolCall, AtelierNode
    from src.models.provider_binding import ProviderCredentialStore
    from src.utils.storage_identity import initialize_storage_identity
    from tests.test_atelier_generation_admission import PNG, SCOPE, request

    store = AtelierStorage(tmp_path)
    try:
        store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, nodes=[
            AtelierNode(id="image", project_id="p", type="image", media_urls=["uploads/frame.png"]),
            AtelierNode(id="video", project_id="p", type="video", data={"reference_node_ids": ["image"]}),
        ]))
        source = tmp_path / "uploads/frame.png"
        source.parent.mkdir()
        source.write_bytes(PNG)
        project = store.get_project("p")
        identity = initialize_storage_identity(tmp_path, writer_lock=store._writer_lock)
        command = request()
        credentials = ProviderCredentialStore(tmp_path, writer_lock=store._writer_lock, runtime_lock=store._lock)
        preview = build_generation_preview(
            project, "video", command, media_scope=store._media_write_scope(),
            credential=credentials.bind(SCOPE, "test-only-secret"),
            server_storage_identity=identity["storage_id"],
        )
        binding = build_agent_generation_binding(
            preview, turn_id="turn", call_id="call", approval_id="approval",
            policy=project.agent_policy, created_at=10.0,
        )
        raw = preview.public_record["raw_request"]
        call = AtelierAgentToolCall(
            call_id="call", tool_name="generation.createVideoCandidates",
            arguments={"node_id": "video", **{k: v for k, v in raw.items() if k != "client_request_id"}},
            preview_binding=binding, approval_required=True, status="approval_required", created_at=10.0,
        )
        turn = AtelierAgentTurn(id="turn", project_id="p", status="waiting_approval", tool_calls=[call], created_at=10.0)
        pending = store.create_agent_turn("p", turn, expected_revision=0)
        identity_args = dict(turn_id="turn", call_id="call", approval_id="approval", authorization_hash=binding.agent_authorization_ref.authorization_hash)
        saved = store.record_agent_approval("p", RecordAgentApprovalCommand(
            expected_revision=pending.revision, decision="approved", **identity_args,
        ), decided_at=11.0)
        admit = AdmitApprovedAgentGenerationCommand(expected_revision=saved.revision, client_request_id="request-1", **identity_args)
        yield store, admit, source
    finally:
        store.close()


def test_approved_call_and_generation_commit_together_without_selection(approved):
    store, command, _ = approved
    project, replayed = store.apply_agent_generation_admission("p", command, now=12.0)
    assert not replayed
    assert project.revision == 3
    assert len(project.generation_attempts) == len(project.generation_request_receipts) == 1
    attempt = project.generation_attempts[0]
    assert attempt.schema_version == 2 and attempt.agent_authorization_ref.call_id == "call"
    assert attempt.provider_execution.attempt_id == attempt.attempt_id
    assert attempt.provider_execution.attempt_id != "preview-attempt"
    call = project.agent_turns[0].tool_calls[0]
    assert call.status == "completed"
    assert call.result_snapshot["attempt_id"] == attempt.attempt_id
    assert len(project.nodes[1].data["candidates"]) == 1
    assert "selected_candidate_id" not in project.nodes[1].data
    assert project.nodes[1].media_urls == []
    assert store.get_project("p") == project
    assert "test-only-secret" not in store.path.read_text()


def test_agent_wire_roundtrip_preserves_authority(approved, tmp_path):
    store, command, _ = approved
    project, _ = store.apply_agent_generation_admission("p", command, now=12.0)
    wire = project.model_dump_json(indent=2)
    assert AtelierProject.model_validate_json(wire) == project
    artifact = tmp_path / "agent-authorization-wire.json"
    artifact.write_text(wire)
    print(f"Agent wire artifact: {artifact}")


def test_shared_wire_fixture_is_a_valid_backend_project():
    from src.apps.atelier.storage import _validate_project

    fixture = Path(__file__).parents[1] / "frontend/src/__tests__/fixtures/atelier-agent-authorization-wire.json"
    project = AtelierProject.model_validate_json(fixture.read_text())
    _validate_project(project)
    assert project.generation_attempts[0].schema_version == 2
    assert project.generation_attempts[0].agent_authorization_ref is not None


def test_replay_precedes_changed_policy_revision_and_missing_source(approved):
    store, command, source = approved
    first, _ = store.apply_agent_generation_admission("p", command, now=12.0)
    store.apply_project_command("p", expected_revision=first.revision, command=lambda p: setattr(p.agent_policy, "allowed_tools", ["canvas.readProject"]))
    store.cancel_agent_turn("p", "turn", cancelled_at=13.0)
    source.unlink()
    before = store.path.read_bytes()
    replay, repeated = store.apply_agent_generation_admission("p", command, now=14.0)
    assert repeated
    assert replay.generation_attempts == first.generation_attempts
    assert store.path.read_bytes() == before
    with pytest.raises(StorageConflictError, match="different content"):
        store.apply_agent_generation_admission("p", command.model_copy(update={"authorization_hash": "f" * 64}), now=15.0)


def test_one_approval_cannot_be_admitted_with_another_request_id(approved):
    store, command, _ = approved
    project, _ = store.apply_agent_generation_admission("p", command, now=12.0)
    before = store.path.read_bytes()
    with pytest.raises(StorageConflictError):
        store.apply_agent_generation_admission("p", command.model_copy(update={"expected_revision": project.revision, "client_request_id": "second-id"}), now=13.0)
    assert store.path.read_bytes() == before


def test_concurrent_agent_admission_commits_once(approved):
    store, command, _ = approved
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.apply_agent_generation_admission("p", command, now=12.0), range(2)))
    assert sorted(replay for _, replay in results) == [False, True]
    assert len(store.get_project("p").generation_attempts) == 1


def test_failed_agent_admission_leaves_call_and_generation_unmodified(approved, monkeypatch):
    store, command, _ = approved
    before = store.path.read_bytes()
    project = store.get_project("p")
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError, match="disk full"):
        store.apply_agent_generation_admission("p", command, now=12.0)
    assert store.path.read_bytes() == before
    assert store.get_project("p") == project


def test_failed_approval_save_never_reports_revocation(approved, monkeypatch):
    from src.apps.atelier.agent_commands import RecordAgentApprovalCommand
    store, command, _ = approved
    before = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))
    revoke = RecordAgentApprovalCommand(**command.model_dump(exclude={"client_request_id"}), decision="revoked")
    with pytest.raises(StorageError, match="disk full"):
        store.record_agent_approval("p", revoke, decided_at=12.0)
    assert store.path.read_bytes() == before
    assert store.get_project("p").agent_turns[0].tool_calls[0].approval_record.decision == "approved"


def test_denied_or_revoked_approval_cannot_be_regranted_without_preview(approved):
    from src.apps.atelier.agent_commands import RecordAgentApprovalCommand
    store, command, _ = approved
    identity = command.model_dump(exclude={"client_request_id"})
    revoke = RecordAgentApprovalCommand(**identity, decision="revoked")
    revoked = store.record_agent_approval("p", revoke, decided_at=12.0)
    regrant = RecordAgentApprovalCommand(**{**identity, "expected_revision": revoked.revision}, decision="approved")
    with pytest.raises(StorageConflictError, match="new_preview"):
        store.record_agent_approval("p", regrant, decided_at=13.0)


@pytest.mark.parametrize("change", ["source", "blob"])
def test_changed_source_or_missing_fixed_blob_cannot_reuse_approval(approved, change):
    store, command, source = approved
    before = store.path.read_bytes()
    binding = store.get_project("p").agent_turns[0].tool_calls[0].preview_binding
    if change == "source":
        source.write_bytes(source.read_bytes() + b"changed")
    else:
        from src.utils.media_versions import MediaVersion
        version = MediaVersion(**binding.provider_execution.media[0])
        (store.data_dir / "media_versions" / "sha256" / version.sha256[:2] / version.sha256).unlink()
    with pytest.raises((ValueError, StorageError)):
        store.apply_agent_generation_admission("p", command, now=12.0)
    assert store.path.read_bytes() == before


def test_catalog_changes_do_not_rebuild_approved_intent(approved, monkeypatch):
    store, command, _ = approved
    monkeypatch.setattr("src.apps.atelier.generation_commands.normalize_generation_intent", Mock(side_effect=AssertionError("Do not normalize approved intent again")))
    project, _ = store.apply_agent_generation_admission("p", command, now=12.0)
    assert project.generation_attempts[0].provider_execution.intent == project.agent_turns[0].tool_calls[0].preview_binding.provider_execution.intent


@pytest.mark.parametrize("action", ["cancel", "revoke"])
def test_unrelated_canvas_edit_does_not_prevent_revocation(approved, action):
    from src.apps.atelier.agent_commands import RecordAgentApprovalCommand
    store, command, _ = approved
    project, _ = store.apply_agent_generation_admission("p", command, now=12.0)
    edited = store.apply_project_command("p", expected_revision=project.revision, command=lambda p: setattr(p, "title", "edited"))
    if action == "cancel":
        cancelled = store.cancel_agent_turn("p", "turn", cancelled_at=13.0)
    else:
        revoke = RecordAgentApprovalCommand(**command.model_dump(exclude={"client_request_id"}), decision="revoked")
        cancelled = store.record_agent_approval("p", revoke, decided_at=13.0)
    assert cancelled.revision == edited.revision + 1
    assert cancelled.agent_turns[0].status == "canceled"
    assert cancelled.generation_attempts == project.generation_attempts
