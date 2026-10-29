"""Explicit user selection for completed Atelier generation candidates."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.apps.atelier.models import AtelierGenerationRequestReceipt, AtelierGenerationRequestReceiptMember, AtelierNode, AtelierProject
from src.apps.atelier.generation_selection import (
    AtelierGenerationSelectionService,
    GenerationCandidateNotFound,
    GenerationCandidateNotSelectable,
    SelectVideoCandidateCommand,
    SetVideoCandidateVisibilityCommand,
)
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_recovery import record_with_changes
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_atelier_generation_ledger_storage import _attempt, _project, _provider_record, _store_with_project


def _store(tmp_path, *, status: str = "completed", preselected: bool = False) -> AtelierStorage:
    project = _project(status=status)
    node = project.nodes[0]
    if preselected:
        node.data["selected_candidate_id"] = "candidate-1"
        node.media_urls = ["video/provider-attempt-1.mp4"]
    if status == "cancelled":
        attempt = project.generation_attempts[0]
        attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="cancelled")
    return _store_with_project(tmp_path, project)



def _source_project_with_imported_candidates():
    node = AtelierNode(
        id="video-source",
        project_id="source-project",
        type="video",
        title="Shot",
        data={
            "candidates": [
                {
                    "id": "source-candidate-a",
                    "media_kind": "video",
                    "status": "completed",
                    "video_url": "video/take-a.mp4",
                    "created_at": 10.0,
                    "updated_at": 11.0,
                },
                {
                    "id": "source-candidate-b",
                    "media_kind": "video",
                    "status": "completed",
                    "video_url": "video/take-b.mp4",
                    "created_at": 12.0,
                    "updated_at": 13.0,
                },
            ],
        },
        created_at=1.0,
        updated_at=1.0,
    )
    return AtelierProject(id="source-project", title="Imported candidates", nodes=[node], created_at=1.0, updated_at=1.0)


def _imported_candidate_store(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    (tmp_path / "video").mkdir(parents=True, exist_ok=True)
    (tmp_path / "video/take-a.mp4").write_bytes(b"take-a")
    (tmp_path / "video/take-b.mp4").write_bytes(b"take-b")
    source = _source_project_with_imported_candidates()
    counter = {"project": 0, "node": 0, "candidate": 0}

    def next_id(kind: str) -> str:
        counter[kind] = counter.get(kind, 0) + 1
        return f"imported-{kind}-{counter[kind]}"

    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=next_id,
    )
    store = AtelierStorage(tmp_path)
    store.create_project(prepared.project)
    return store, prepared.project

def _command(revision: int = 1) -> SelectVideoCandidateCommand:
    return SelectVideoCandidateCommand(expected_revision=revision)


def _selection_payload(project):
    node = project.nodes[0]
    return {
        "selected_candidate_id": node.data.get("selected_candidate_id"),
        "media_urls": list(node.media_urls),
        "updated_at": node.updated_at,
        "project_updated_at": project.updated_at,
    }


def test_select_completed_candidate_updates_only_user_media_state(tmp_path):
    store = _store(tmp_path)
    service = AtelierGenerationSelectionService(store, clock=lambda: 50.0)
    before = store.get_project("p1")
    before_attempts = deepcopy(before.generation_attempts)
    before_receipts = deepcopy(before.generation_request_receipts)
    before_candidate = deepcopy(before.nodes[0].data["candidates"][0])

    ack = service.select_video_candidate("p1", "node-1", "candidate-1", _command())

    node = ack.project.nodes[0]
    assert ack.node_ids == ["node-1"]
    assert ack.project.revision == 2
    assert node.data["selected_candidate_id"] == "candidate-1"
    assert node.media_urls == ["video/provider-attempt-1.mp4"]
    assert node.updated_at == 50.0 and ack.project.updated_at == 50.0
    assert ack.project.generation_attempts == before_attempts
    assert ack.project.generation_request_receipts == before_receipts
    assert node.data["candidates"][0] == before_candidate


def test_select_noop_when_already_selected_does_not_bump_revision(tmp_path):
    store = _store(tmp_path, preselected=True)
    service = AtelierGenerationSelectionService(store, clock=lambda: 99.0)
    before = store.get_project("p1")

    ack = service.select_video_candidate("p1", "node-1", "candidate-1", _command())

    assert ack.project == before
    assert store.get_project("p1") == before


def test_select_rejects_stale_revision_without_mutation(tmp_path):
    store = _store(tmp_path)
    service = AtelierGenerationSelectionService(store)
    before_bytes = store.path.read_bytes()

    with pytest.raises(StorageConflictError):
        service.select_video_candidate("p1", "node-1", "candidate-1", _command(revision=0))

    assert store.path.read_bytes() == before_bytes
    assert _selection_payload(store.get_project("p1"))["selected_candidate_id"] is None


@pytest.mark.parametrize("status", ["pending", "processing", "needs_confirmation", "failed", "cancelled"])
def test_select_rejects_non_completed_candidate_status_without_mutation(tmp_path, status):
    store = _store(tmp_path, status=status)
    service = AtelierGenerationSelectionService(store)
    before_bytes = store.path.read_bytes()

    with pytest.raises(GenerationCandidateNotSelectable):
        service.select_video_candidate("p1", "node-1", "candidate-1", _command())

    assert store.path.read_bytes() == before_bytes
    assert _selection_payload(store.get_project("p1"))["selected_candidate_id"] is None


def test_select_rejects_missing_wrong_owner_or_legacy_candidate_without_mutation(tmp_path):
    store = _store(tmp_path)
    service = AtelierGenerationSelectionService(store)
    before_bytes = store.path.read_bytes()

    with pytest.raises(GenerationCandidateNotFound):
        service.select_video_candidate("p1", "node-1", "missing", _command())
    with pytest.raises(GenerationCandidateNotFound):
        service.select_video_candidate("p1", "other-node", "candidate-1", _command())

    legacy = _project(status="completed")
    legacy.nodes[0].data["candidates"][0].pop("execution_contract")
    legacy.generation_attempts.clear()
    legacy.generation_request_receipts.clear()
    store.close()
    legacy_store = AtelierStorage(tmp_path / "legacy")
    legacy_store.create_project(legacy)
    legacy_service = AtelierGenerationSelectionService(legacy_store)
    with pytest.raises(GenerationCandidateNotFound):
        legacy_service.select_video_candidate("p1", "node-1", "candidate-1", _command(revision=0))
    legacy_store.close()

    assert store.path.read_bytes() == before_bytes


def test_select_save_failure_rolls_back_without_partial_selection(tmp_path, monkeypatch):
    store = _store(tmp_path)
    service = AtelierGenerationSelectionService(store, clock=lambda: 50.0)
    before_bytes = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))

    with pytest.raises(StorageError):
        service.select_video_candidate("p1", "node-1", "candidate-1", _command())

    assert store.path.read_bytes() == before_bytes
    saved = store.get_project("p1")
    assert saved.nodes[0].media_urls == []
    assert "selected_candidate_id" not in saved.nodes[0].data


def test_select_imported_completed_candidate_uses_inert_media_copy_without_ledger_authority(tmp_path):
    store, imported = _imported_candidate_store(tmp_path)
    service = AtelierGenerationSelectionService(store, clock=lambda: 80.0)
    node = imported.nodes[0]
    first, second = node.data["candidates"]
    before_candidates = deepcopy(node.data["candidates"])

    ack = service.select_video_candidate(imported.id, node.id, second["id"], SelectVideoCandidateCommand(expected_revision=0))

    selected = ack.project.nodes[0]
    assert ack.project.revision == 1
    assert selected.data["selected_candidate_id"] == second["id"]
    assert selected.media_urls == [second["video_url"]]
    assert selected.data["candidates"] == before_candidates
    assert ack.project.generation_attempts == []
    assert ack.project.generation_request_receipts == []
    assert ack.project.import_provenance == imported.import_provenance
    assert first["video_url"].startswith(f"video/imported/{imported.id}/")
    assert second["video_url"].startswith(f"video/imported/{imported.id}/")
    assert (tmp_path / second["video_url"]).read_bytes() == b"take-b"

    store.close()


def test_select_imported_candidate_rejects_deleted_active_or_cross_node_without_mutation(tmp_path):
    store, imported = _imported_candidate_store(tmp_path)
    project = store.get_project(imported.id)
    node = project.nodes[0]
    deleted = dict(node.data["candidates"][0])
    active = dict(node.data["candidates"][1], id="active-imported-candidate", status="processing")
    outside_media = dict(node.data["candidates"][1], id="outside-media-candidate", video_url="video/take-b.mp4")
    node.data["deleted_candidates"] = [deleted]
    node.data["candidates"] = [active, outside_media]
    other = AtelierNode(
        id="other-video",
        project_id=project.id,
        type="video",
        data={"candidates": [dict(deleted, id="other-candidate")]},
        created_at=1.0,
        updated_at=1.0,
    )
    project.nodes.append(other)
    store.apply_project_command(
        project.id,
        expected_revision=0,
        command=lambda target: target.nodes.__setitem__(slice(None), project.nodes),
    )
    before_bytes = store.path.read_bytes()
    service = AtelierGenerationSelectionService(store)

    with pytest.raises(GenerationCandidateNotFound):
        service.select_video_candidate(imported.id, node.id, deleted["id"], SelectVideoCandidateCommand(expected_revision=1))
    with pytest.raises(GenerationCandidateNotSelectable):
        service.select_video_candidate(imported.id, node.id, active["id"], SelectVideoCandidateCommand(expected_revision=1))
    with pytest.raises(GenerationCandidateNotFound):
        service.select_video_candidate(imported.id, node.id, outside_media["id"], SelectVideoCandidateCommand(expected_revision=1))
    with pytest.raises(GenerationCandidateNotFound):
        service.select_video_candidate(imported.id, node.id, "other-candidate", SelectVideoCandidateCommand(expected_revision=1))

    assert store.path.read_bytes() == before_bytes
    store.close()


@pytest.mark.parametrize(
    "authority_key",
    ["handle", "provider_handle", "agent_authorization_ref", "attempt_token", "binding", "execution_contract", "attempt_id"],
)
def test_select_imported_candidate_rejects_any_authority_field_presence_even_empty(tmp_path, authority_key):
    store, imported = _imported_candidate_store(tmp_path)
    project = store.get_project(imported.id)
    node = project.nodes[0]
    node.data["candidates"][0][authority_key] = ""
    store.apply_project_command(
        project.id,
        expected_revision=0,
        command=lambda target: target.nodes.__setitem__(slice(None), project.nodes),
    )
    before_bytes = store.path.read_bytes()
    service = AtelierGenerationSelectionService(store)

    with pytest.raises(GenerationCandidateNotFound):
        service.select_video_candidate(imported.id, node.id, node.data["candidates"][0]["id"], SelectVideoCandidateCommand(expected_revision=1))

    assert store.path.read_bytes() == before_bytes
    store.close()


@pytest.mark.parametrize("symlink_kind", ["file", "directory"])
def test_select_imported_candidate_rejects_symlinked_imported_media(tmp_path, symlink_kind):
    store, imported = _imported_candidate_store(tmp_path)
    project = store.get_project(imported.id)
    node = project.nodes[0]
    candidate = node.data["candidates"][0]
    media_path = tmp_path / candidate["video_url"]
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "take.mp4"
    outside_file.write_bytes(b"outside")
    if symlink_kind == "file":
        media_path.unlink()
        try:
            media_path.symlink_to(outside_file)
        except OSError:
            pytest.skip("symlink creation is not supported")
    else:
        imported_dir = tmp_path / "video" / "imported" / imported.id
        original = tmp_path / "original-imported-dir"
        imported_dir.rename(original)
        try:
            imported_dir.symlink_to(outside, target_is_directory=True)
        except OSError:
            original.rename(imported_dir)
            pytest.skip("symlink creation is not supported")
        candidate["video_url"] = f"video/imported/{imported.id}/take.mp4"
        store.apply_project_command(
        project.id,
        expected_revision=0,
        command=lambda target: target.nodes.__setitem__(slice(None), project.nodes),
    )
    before_bytes = store.path.read_bytes()
    service = AtelierGenerationSelectionService(store)

    with pytest.raises(GenerationCandidateNotFound):
        service.select_video_candidate(
            imported.id,
            node.id,
            candidate["id"],
            SelectVideoCandidateCommand(expected_revision=0 if symlink_kind == "file" else 1),
        )

    assert store.path.read_bytes() == before_bytes
    if symlink_kind == "file":
        assert media_path.is_symlink()
    else:
        assert (tmp_path / "video" / "imported" / imported.id).is_symlink()
    store.close()


def test_select_imported_candidate_rejects_backslash_and_parent_segments(tmp_path):
    store, imported = _imported_candidate_store(tmp_path)
    project = store.get_project(imported.id)
    node = project.nodes[0]
    node.data["candidates"][0]["video_url"] = f"video/imported/{imported.id}/../escape.mp4"
    node.data["candidates"][1]["video_url"] = f"video\\imported\\{imported.id}\\take.mp4"
    store.apply_project_command(
        project.id,
        expected_revision=0,
        command=lambda target: target.nodes.__setitem__(slice(None), project.nodes),
    )
    before_bytes = store.path.read_bytes()
    service = AtelierGenerationSelectionService(store)

    for candidate in node.data["candidates"]:
        with pytest.raises(GenerationCandidateNotFound):
            service.select_video_candidate(imported.id, node.id, candidate["id"], SelectVideoCandidateCommand(expected_revision=1))

    assert store.path.read_bytes() == before_bytes
    store.close()


def _batch_completed_store(tmp_path) -> AtelierStorage:
    project = _project(status="completed")
    second_record = _provider_record(attempt_id="attempt-2", phase="completed", artifact_path="video/provider-attempt-2.mp4")
    second_attempt = _attempt(
        candidate_id="candidate-2",
        attempt_id="attempt-2",
        status="completed",
        output_ref="video/provider-attempt-2.mp4",
        provider_execution=second_record,
    )
    second_candidate = dict(project.nodes[0].data["candidates"][0])
    second_candidate.update({
        "id": "candidate-2",
        "attempt_id": "attempt-2",
        "video_url": "video/provider-attempt-2.mp4",
    })
    project.nodes[0].data["candidates"].append(second_candidate)
    project.generation_attempts.append(second_attempt)
    project.generation_request_receipts = [AtelierGenerationRequestReceipt(
        schema_version=2,
        project_id="p1",
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="request-1",
        request_fingerprint="b" * 64,
        node_id="node-1",
        members=[
            AtelierGenerationRequestReceiptMember(
                candidate_id="candidate-1", attempt_id="attempt-1",
                execution_fingerprint=project.generation_attempts[0].provider_execution.fingerprint,
            ),
            AtelierGenerationRequestReceiptMember(
                candidate_id="candidate-2", attempt_id="attempt-2",
                execution_fingerprint=second_record.fingerprint,
            ),
        ],
        base_revision=0, committed_revision=1, created_at=10.0,
    )]
    return _store_with_project(tmp_path, project)


def test_select_and_hide_non_first_batch_candidate_uses_member_receipt(tmp_path):
    store = _batch_completed_store(tmp_path)
    service = AtelierGenerationSelectionService(store, clock=lambda: 70.0)

    selected = service.select_video_candidate("p1", "node-1", "candidate-2", _command())
    assert selected.project.nodes[0].data["selected_candidate_id"] == "candidate-2"
    assert selected.project.nodes[0].media_urls == ["video/provider-attempt-2.mp4"]

    hidden = service.set_video_candidate_visibility(
        "p1", "node-1", "candidate-2", SetVideoCandidateVisibilityCommand(expected_revision=2, visible=False),
    )
    node = hidden.project.nodes[0]
    assert "selected_candidate_id" not in node.data
    assert node.media_urls == []
    assert [item["id"] for item in node.data["deleted_candidates"]] == ["candidate-2"]
