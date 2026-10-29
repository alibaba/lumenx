"""Explicit selection and visibility for provider-owned media candidates."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from src.apps.atelier.generation_selection import (
    AtelierGenerationSelectionService,
    GenerationCandidateNotSelectable,
    SelectVideoCandidateCommand,
    SetVideoCandidateVisibilityCommand,
)
from src.apps.atelier.storage import AtelierStorage
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_atelier_media_generation_ledger import _project_payload, _write_raw


def _bytes_for(media_kind: str) -> bytes:
    return rgb_png_bytes("media-selection") if media_kind == "image" else b"fake-selection-mp3"


def _completed_media_store(tmp_path: Path, *, media_kind: str = "image", write_file: bool = True, corrupt_file: bool = False) -> AtelierStorage:
    payload = _project_payload(media_kind=media_kind)
    output_ref = "assets/atelier/out.png" if media_kind == "image" else "audio/atelier/out.mp3"
    data = _bytes_for(media_kind)
    if write_file:
        path = tmp_path / output_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"corrupt" if corrupt_file else data)
    proof = {
        "schema_version": 2,
        "contract_version": "provider-sync-artifact-v1",
        "attempt_id": f"attempt-{media_kind}",
        "media_kind": media_kind,
        "relative_path": output_ref,
        "output_ref": output_ref,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    attempt = payload["generation_attempts"][0]
    attempt["provider_execution"]["prepared_artifact"] = proof
    attempt["provider_execution"]["artifact"] = proof
    _write_raw(tmp_path, payload)
    return AtelierStorage(tmp_path)


def _terminal_media_store(tmp_path: Path, *, status: str) -> AtelierStorage:
    payload = _project_payload(media_kind="image")
    attempt = payload["generation_attempts"][0]
    candidate = payload["nodes"][0]["data"]["candidates"][0]
    attempt["status"] = status
    attempt["output_ref"] = None
    candidate["status"] = status
    candidate["media_url"] = None
    if status == "failed":
        attempt["error_code"] = "provider_sync_failed"
        candidate["error_code"] = "provider_sync_failed"
        attempt["provider_execution"]["phase"] = "accepted"
    elif status == "cancelled":
        attempt["provider_execution"]["phase"] = "cancelled"
    elif status == "needs_confirmation":
        attempt["error_code"] = "provider_sync_execution_unknown"
        candidate["error_code"] = "provider_sync_execution_unknown"
        attempt["provider_execution"]["phase"] = "submitting"
    else:
        raise AssertionError(status)
    attempt["provider_execution"]["prepared_artifact"] = None
    attempt["provider_execution"]["artifact"] = None
    _write_raw(tmp_path, payload)
    return AtelierStorage(tmp_path)


def _media_command(revision: int = 1) -> SelectVideoCandidateCommand:
    return SelectVideoCandidateCommand(expected_revision=revision)


def _visibility_command(*, visible: bool, revision: int = 1) -> SetVideoCandidateVisibilityCommand:
    return SetVideoCandidateVisibilityCommand(expected_revision=revision, visible=visible)


@pytest.mark.parametrize("media_kind", ["image", "audio"])
def test_select_completed_provider_media_candidate_verifies_bytes_and_updates_user_media_state(tmp_path, media_kind):
    store = _completed_media_store(tmp_path, media_kind=media_kind)
    service = AtelierGenerationSelectionService(store, clock=lambda: 50.0)
    before = store.get_project("p1")
    before_attempts = deepcopy(before.generation_attempts)
    before_receipts = deepcopy(before.generation_request_receipts)
    node = before.nodes[0]
    candidate = node.data["candidates"][0]

    ack = service.select_media_candidate("p1", node.id, candidate["id"], _media_command())

    selected = ack.project.nodes[0]
    assert ack.node_ids == [node.id]
    assert ack.project.revision == 2
    assert selected.data["selected_candidate_id"] == candidate["id"]
    assert selected.media_urls == [candidate["media_url"]]
    assert selected.updated_at == 50.0 and ack.project.updated_at == 50.0
    assert ack.project.generation_attempts == before_attempts
    assert ack.project.generation_request_receipts == before_receipts
    store.close()


@pytest.mark.parametrize("corrupt_file", [False, True])
def test_select_completed_provider_media_candidate_rejects_missing_or_changed_final_bytes(tmp_path, corrupt_file):
    store = _completed_media_store(tmp_path, write_file=corrupt_file, corrupt_file=corrupt_file)
    service = AtelierGenerationSelectionService(store)
    before = store.path.read_bytes()
    node = store.get_project("p1").nodes[0]
    candidate = node.data["candidates"][0]

    with pytest.raises(GenerationCandidateNotSelectable):
        service.select_media_candidate("p1", node.id, candidate["id"], _media_command())

    assert store.path.read_bytes() == before
    assert store.get_project("p1").nodes[0].media_urls == []
    store.close()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_provider_media_candidate_visibility_moves_intact_without_changing_ledger(tmp_path, status):
    store = _terminal_media_store(tmp_path, status=status)
    service = AtelierGenerationSelectionService(store, clock=lambda: 70.0)
    before = store.get_project("p1")
    node = before.nodes[0]
    candidate = deepcopy(node.data["candidates"][0])

    hidden = service.set_media_candidate_visibility("p1", node.id, candidate["id"], _visibility_command(visible=False))

    assert hidden.project.generation_attempts == before.generation_attempts
    assert hidden.project.generation_request_receipts == before.generation_request_receipts
    assert hidden.project.nodes[0].data["candidates"] == []
    assert hidden.project.nodes[0].data["deleted_candidates"] == [candidate]
    assert hidden.project.nodes[0].media_urls == []

    restored = service.set_media_candidate_visibility("p1", node.id, candidate["id"], _visibility_command(visible=True, revision=2))
    assert restored.project.nodes[0].data["candidates"] == [candidate]
    assert restored.project.nodes[0].data["deleted_candidates"] == []
    store.close()


def test_hidden_selected_provider_media_candidate_clears_selection_and_materialized_media(tmp_path):
    store = _completed_media_store(tmp_path)
    service = AtelierGenerationSelectionService(store)
    node = store.get_project("p1").nodes[0]
    candidate = node.data["candidates"][0]
    selected = service.select_media_candidate("p1", node.id, candidate["id"], _media_command())
    assert selected.project.nodes[0].media_urls == [candidate["media_url"]]

    hidden = service.set_media_candidate_visibility("p1", node.id, candidate["id"], _visibility_command(visible=False, revision=2))

    assert "selected_candidate_id" not in hidden.project.nodes[0].data
    assert hidden.project.nodes[0].media_urls == []
    store.close()


def test_needs_confirmation_provider_media_candidate_cannot_be_hidden(tmp_path):
    store = _terminal_media_store(tmp_path, status="needs_confirmation")
    service = AtelierGenerationSelectionService(store)
    before = store.path.read_bytes()
    node = store.get_project("p1").nodes[0]
    candidate = node.data["candidates"][0]

    with pytest.raises(GenerationCandidateNotSelectable):
        service.set_media_candidate_visibility("p1", node.id, candidate["id"], _visibility_command(visible=False))

    assert store.path.read_bytes() == before
    store.close()
