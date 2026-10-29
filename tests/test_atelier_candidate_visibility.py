"""Visibility changes preserve candidate identity and never execute generation."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.apps.atelier.generation_selection import (
    AtelierGenerationSelectionService,
    GenerationCandidateNotFound,
    GenerationCandidateNotSelectable,
    SetVideoCandidateVisibilityCommand,
)
from src.apps.atelier.models import AtelierImportProvenance, AtelierSequenceEntry
from src.apps.atelier.sequence import SequenceError, _candidate_url
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_atelier_generation_api import AdmissionFake, DispatcherFake, ExecutionFake, _client
from tests.test_atelier_generation_ledger_storage import _project
from tests.test_atelier_generation_selection import _store


def _set(service, *, visible, revision=1, node_id="node-1", candidate_id="candidate-1"):
    return service.set_video_candidate_visibility(
        "p1", node_id, candidate_id,
        SetVideoCandidateVisibilityCommand(expected_revision=revision, visible=visible),
    ).project


@pytest.mark.parametrize("preselected", [False, True])
def test_hide_restore_roundtrip_keeps_ledger_media_and_sequence(tmp_path, preselected):
    store = _store(tmp_path, preselected=preselected)
    before = store.apply_project_command(
        "p1", expected_revision=1,
        command=lambda project: project.sequence.append(
            AtelierSequenceEntry(id="clip", parentId="node-1", candidateId="candidate-1")
        ),
    )
    candidate = deepcopy(before.nodes[0].data["candidates"][0])
    service = AtelierGenerationSelectionService(store, clock=lambda: 50.0)

    hidden = _set(service, visible=False, revision=2)
    assert hidden.nodes[0].data["candidates"] == []
    assert hidden.nodes[0].data["deleted_candidates"] == [candidate]
    assert hidden.nodes[0].data.get("selected_candidate_id") is None
    assert hidden.nodes[0].media_urls == []
    assert hidden.sequence == before.sequence
    with pytest.raises(SequenceError) as error:
        _candidate_url(hidden.nodes[0], "candidate-1", "video")
    assert error.value.code == "missing_candidate"
    store.close()
    reopened = AtelierStorage(tmp_path)
    assert reopened.get_project("p1") == hidden
    restored = _set(AtelierGenerationSelectionService(reopened), visible=True, revision=3)
    assert restored.nodes[0].data["candidates"] == [candidate]
    assert restored.nodes[0].data["deleted_candidates"] == []
    assert restored.nodes[0].data.get("selected_candidate_id") is None
    assert restored.nodes[0].media_urls == []
    assert restored.sequence == before.sequence
    assert _candidate_url(restored.nodes[0], "candidate-1", "video") == candidate["video_url"]
    assert restored.generation_attempts == before.generation_attempts
    assert restored.generation_request_receipts == before.generation_request_receipts
    reopened.close()


@pytest.mark.parametrize("status", ["pending", "processing", "needs_confirmation"])
def test_active_or_unknown_generation_cannot_be_hidden(tmp_path, status):
    store = _store(tmp_path, status=status)
    before = store.path.read_bytes()
    with pytest.raises(GenerationCandidateNotSelectable):
        _set(AtelierGenerationSelectionService(store), visible=False)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_attempt_can_be_hidden_without_changing_execution(tmp_path, status):
    store = _store(tmp_path, status=status)
    before = store.get_project("p1")
    hidden = _set(AtelierGenerationSelectionService(store), visible=False)
    assert hidden.generation_attempts == before.generation_attempts
    assert hidden.generation_request_receipts == before.generation_request_receipts
    assert hidden.nodes[0].data["deleted_candidates"] == before.nodes[0].data["candidates"]


def test_desired_visibility_noop_uses_current_revision_but_stale_replay_conflicts(tmp_path):
    store = _store(tmp_path)
    service = AtelierGenerationSelectionService(store)
    before = store.path.read_bytes()
    current = _set(service, visible=True)
    assert current.revision == 1 and store.path.read_bytes() == before
    hidden = _set(service, visible=False)
    assert _set(service, visible=False, revision=2) == hidden
    with pytest.raises(StorageConflictError):
        _set(service, visible=False, revision=1)


@pytest.mark.parametrize("identity", [{"node_id": "other"}, {"candidate_id": "other"}])
def test_visibility_wrong_identity_has_no_write(tmp_path, identity):
    store = _store(tmp_path)
    before = store.path.read_bytes()
    with pytest.raises(GenerationCandidateNotFound):
        _set(AtelierGenerationSelectionService(store), visible=False, **identity)
    assert store.path.read_bytes() == before


def test_hide_failure_keeps_selected_projection_and_disk_unchanged(tmp_path, monkeypatch):
    store = _store(tmp_path, preselected=True)
    before = store.get_project("p1")
    raw = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError):
        _set(AtelierGenerationSelectionService(store), visible=False)
    assert store.get_project("p1") == before and store.path.read_bytes() == raw


def test_imported_media_can_hide_restore_without_gaining_execution_authority(tmp_path):
    project = _project(status="completed", revision=0)
    candidate = project.nodes[0].data["candidates"][0]
    candidate.pop("execution_contract")
    candidate.pop("attempt_id")
    project.generation_attempts.clear()
    project.generation_request_receipts.clear()
    project.import_provenance = AtelierImportProvenance(
        source_project_id="source", source_storage_id="source-storage", imported_at=1.0,
    )
    store = AtelierStorage(tmp_path)
    store.create_project(project)
    service = AtelierGenerationSelectionService(store)
    _set(service, visible=False, revision=0)
    restored = _set(service, visible=True, revision=1)
    assert restored.import_provenance == project.import_provenance
    assert restored.generation_attempts == [] and restored.generation_request_receipts == []
    assert restored.nodes[0].data["candidates"] == [candidate]
    assert restored.nodes[0].media_urls == []
    store.close()


def test_hide_keeps_existing_materialized_reference_media(tmp_path):
    from src.apps.atelier.references import reference_media_urls

    store = _store(tmp_path)
    before = store.get_project("p1")
    source_urls = reference_media_urls(before.nodes[0])
    hidden = _set(AtelierGenerationSelectionService(store), visible=False)
    assert reference_media_urls(hidden.nodes[0]) == source_urls


def test_hidden_sequence_reference_blocks_package_without_changing_project(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package

    store = _store(tmp_path)
    store.apply_project_command("p1", expected_revision=1, command=lambda project: project.sequence.append(
        AtelierSequenceEntry(id="clip", parentId="node-1", candidateId="candidate-1"),
    ))
    hidden = _set(AtelierGenerationSelectionService(store), visible=False, revision=2)
    before = store.path.read_bytes()
    with pytest.raises(AtelierProjectPackageError, match="references deleted candidate"):
        build_package(hidden, data_dir=tmp_path, source_storage_id="storage")
    assert store.path.read_bytes() == before


def test_visibility_http_does_not_dispatch_submit_or_continue(tmp_path):
    store = _store(tmp_path, preselected=True)
    admission, execution, dispatcher = AdmissionFake(), ExecutionFake(), DispatcherFake()
    client = _client(admission=admission, execution=execution, dispatcher=dispatcher,
                     selection=AtelierGenerationSelectionService(store))
    route = "/projects/p1/nodes/node-1/video_candidates/candidate-1/visibility"
    response = client.post(route, json={"expected_revision": 1, "visible": False})
    assert response.status_code == 200, response.text
    assert response.json()["project"]["nodes"][0]["data"]["candidates"] == []
    assert response.json()["node_ids"] == ["node-1"]
    assert admission.admitted == [] and execution.continues == [] and dispatcher.scheduled == []
    with pytest.raises(StorageConflictError):  # Bare-router fixture has no application error handlers.
        client.post(route, json={"expected_revision": 1, "visible": False})
    assert client.post(route, json={"expected_revision": 2, "visible": True}).status_code == 200


@pytest.mark.parametrize("payload", [
    {"expected_revision": 1, "visible": "false"},
    {"expected_revision": True, "visible": False},
    {"expected_revision": 1, "visible": False, "attempt_id": "new"},
])
def test_visibility_http_rejects_coercion_and_authority_fields(tmp_path, payload):
    store = _store(tmp_path)
    before = store.path.read_bytes()
    client = _client(selection=AtelierGenerationSelectionService(store))
    response = client.post("/projects/p1/nodes/node-1/video_candidates/candidate-1/visibility", json=payload)
    assert response.status_code == 422
    assert store.path.read_bytes() == before


def test_visibility_real_application_returns_conflict_without_changing_saved_state(tmp_path):
    from fastapi.testclient import TestClient
    from src.apps.comic_gen.api import create_app

    store = _store(tmp_path, preselected=True)
    store.close()
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        route = "/atelier/projects/p1/nodes/node-1/video_candidates/candidate-1/visibility"
        response = client.post(route, json={"expected_revision": 1, "visible": False})
        assert response.status_code == 200, response.text
        conflict = client.post(route, json={"expected_revision": 1, "visible": True})
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "storage_conflict"
        saved = client.get("/atelier/projects/p1").json()
        assert saved["nodes"][0]["data"]["candidates"] == []
