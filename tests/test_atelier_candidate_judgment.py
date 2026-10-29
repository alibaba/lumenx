"""Independent judgments for Atelier generation candidates."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.apps.atelier.candidate_judgment import (
    AtelierCandidateJudgmentService,
    CandidateJudgmentCommand,
    CandidateJudgmentNotFound,
    CandidateJudgmentNotReviewable,
)
from src.apps.atelier.generation_selection import (
    AtelierGenerationSelectionService,
    SelectVideoCandidateCommand,
    SetVideoCandidateVisibilityCommand,
)
from src.apps.atelier.models import AtelierSequenceEntry
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_atelier_generation_ledger_storage import _project
from tests.test_atelier_generation_selection import _batch_completed_store, _store
from tests.test_atelier_media_generation_ledger import _project_payload, _write_raw


def _judge(
    service: AtelierCandidateJudgmentService,
    *,
    project_id: str = "p1",
    node_id: str = "node-1",
    candidate_id: str = "candidate-1",
    revision: int = 1,
    verdict: str = "keep",
    reason: str = "Strong alternate take",
):
    return service.set_candidate_judgment(
        project_id,
        node_id,
        candidate_id,
        CandidateJudgmentCommand(expected_revision=revision, verdict=verdict, reason=reason),
    )


def _media_store(tmp_path, media_kind: str) -> AtelierStorage:
    payload = _project_payload(media_kind=media_kind)
    _write_raw(tmp_path, payload)
    return AtelierStorage(tmp_path)


@pytest.mark.parametrize(
    ("media_kind", "node_id", "candidate_id", "store_factory"),
    [
        ("video", "node-1", "candidate-1", lambda tmp_path: _store(tmp_path)),
        ("image", "image-node", "candidate-image", lambda tmp_path: _media_store(tmp_path, "image")),
        ("audio", "audio-node", "candidate-audio", lambda tmp_path: _media_store(tmp_path, "audio")),
    ],
)
def test_candidate_judgment_appends_for_video_image_and_audio(tmp_path, media_kind, node_id, candidate_id, store_factory):
    store = store_factory(tmp_path)
    service = AtelierCandidateJudgmentService(store, clock=lambda: 50.0, id_factory=lambda: f"judgment-{media_kind}")

    ack = _judge(service, node_id=node_id, candidate_id=candidate_id)

    assert ack.node_ids == [node_id]
    assert ack.project.revision == 2
    assert [item.id for item in ack.project.candidate_judgments] == [f"judgment-{media_kind}"]
    judgment = ack.project.candidate_judgments[0]
    assert judgment.project_id == "p1"
    assert judgment.node_id == node_id
    assert judgment.candidate_id == candidate_id
    assert judgment.media_kind == media_kind
    assert judgment.verdict == "keep"
    assert judgment.reason == "Strong alternate take"
    assert judgment.created_at == 50.0
    assert judgment.supersedes_judgment_id is None
    store.close()


def test_same_judgment_value_is_noop_at_current_revision(tmp_path):
    store = _store(tmp_path)
    service = AtelierCandidateJudgmentService(store, clock=lambda: 10.0, id_factory=lambda: "judgment-1")
    first = _judge(service)
    raw = store.path.read_bytes()

    second = _judge(service, revision=2)

    assert second.project == first.project
    assert store.path.read_bytes() == raw
    with pytest.raises(StorageConflictError):
        _judge(service, revision=1)
    store.close()


def test_changed_judgment_appends_chain_without_reordering_by_clock(tmp_path):
    store = _store(tmp_path)
    ids = iter(["judgment-1", "judgment-2"])
    service = AtelierCandidateJudgmentService(store, clock=lambda: 1.0, id_factory=lambda: next(ids))
    first = _judge(service, verdict="keep", reason="A")

    changed = _judge(service, revision=2, verdict="reject", reason="B")

    assert [item.id for item in changed.project.candidate_judgments] == ["judgment-1", "judgment-2"]
    assert changed.project.candidate_judgments[1].supersedes_judgment_id == first.project.candidate_judgments[0].id
    assert changed.project.candidate_judgments[1].created_at == 1.0
    store.close()


def test_judging_unselected_candidate_keeps_selected_candidate_sequence_and_projection(tmp_path):
    store = _batch_completed_store(tmp_path)
    selector = AtelierGenerationSelectionService(store, clock=lambda: 20.0)
    selected = selector.select_video_candidate("p1", "node-1", "candidate-1", SelectVideoCandidateCommand(expected_revision=1))
    store.apply_project_command(
        "p1",
        expected_revision=2,
        command=lambda project: project.sequence.append(
            AtelierSequenceEntry(id="clip", parentId="node-1", candidateId="candidate-1")
        ),
    )
    before = store.get_project("p1")
    before_node = deepcopy(before.nodes[0])
    before_sequence = deepcopy(before.sequence)
    before_attempts = deepcopy(before.generation_attempts)
    service = AtelierCandidateJudgmentService(store, clock=lambda: 30.0, id_factory=lambda: "judgment-b")

    judged = _judge(service, candidate_id="candidate-2", revision=3, verdict="reject", reason="Too static")

    node = judged.project.nodes[0]
    assert selected.project.nodes[0].data["selected_candidate_id"] == "candidate-1"
    assert node == before_node
    assert judged.project.sequence == before_sequence
    assert judged.project.generation_attempts == before_attempts
    assert judged.project.candidate_judgments[0].candidate_id == "candidate-2"
    store.close()


def test_hidden_candidate_history_is_readable_but_cannot_be_changed_until_restored(tmp_path):
    store = _store(tmp_path)
    ids = iter(["judgment-1", "judgment-2"])
    service = AtelierCandidateJudgmentService(store, clock=lambda: 10.0, id_factory=lambda: next(ids))
    judged = _judge(service)
    selector = AtelierGenerationSelectionService(store)
    selector.set_video_candidate_visibility(
        "p1", "node-1", "candidate-1",
        SetVideoCandidateVisibilityCommand(expected_revision=2, visible=False),
    )

    hidden = store.get_project("p1")
    assert hidden.candidate_judgments == judged.project.candidate_judgments
    with pytest.raises(CandidateJudgmentNotReviewable):
        _judge(service, revision=3, verdict="reject", reason="Changed while hidden")

    selector.set_video_candidate_visibility(
        "p1", "node-1", "candidate-1",
        SetVideoCandidateVisibilityCommand(expected_revision=3, visible=True),
    )
    changed = _judge(service, revision=4, verdict="reject", reason="Changed after restore")
    assert changed.project.candidate_judgments[-1].supersedes_judgment_id == "judgment-1"
    store.close()


def test_deleted_node_retains_orphan_history_and_reconnects_after_restore(tmp_path):
    store = _store(tmp_path)
    ids = iter(["judgment-1", "judgment-2"])
    service = AtelierCandidateJudgmentService(store, clock=lambda: 10.0, id_factory=lambda: next(ids))
    judged = _judge(service)
    history = AtelierService(store, clock=lambda: 20.0, id_factory=lambda: "delete-receipt-1")
    deleted = history.delete_nodes("p1", expected_revision=2, node_ids=["node-1"])

    assert deleted.project.nodes == []
    assert deleted.project.candidate_judgments == judged.project.candidate_judgments
    with pytest.raises(CandidateJudgmentNotFound):
        _judge(service, revision=3, verdict="reject", reason="Cannot write while deleted")

    restored = history.restore_nodes("p1", expected_revision=3, receipt_id=deleted.receipt_id)
    assert restored.project.nodes[0].id == "node-1"
    changed = _judge(service, revision=4, verdict="reject", reason="Visible again")
    assert [item.id for item in changed.project.candidate_judgments] == ["judgment-1", "judgment-2"]
    store.close()


def test_judgment_rejects_extra_client_fields_and_oversized_reason():
    with pytest.raises(ValueError):
        CandidateJudgmentCommand(expected_revision=1, verdict="keep", reason="ok", provider="dashscope")
    with pytest.raises(ValueError):
        CandidateJudgmentCommand(expected_revision=1, verdict="reject", reason="x" * 2001)
    with pytest.raises(ValueError):
        CandidateJudgmentCommand(expected_revision=True, verdict="keep", reason="")


def test_write_failure_keeps_memory_and_file_unchanged(tmp_path, monkeypatch):
    store = _store(tmp_path)
    service = AtelierCandidateJudgmentService(store, clock=lambda: 10.0, id_factory=lambda: "judgment-1")
    before = store.get_project("p1")
    raw = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))

    with pytest.raises(StorageError):
        _judge(service)

    assert store.get_project("p1") == before
    assert store.path.read_bytes() == raw
    store.close()


def test_storage_rejects_invalid_judgment_chain_and_duplicate_ids(tmp_path):
    project = _project(status="completed")
    payload = project.model_dump(mode="json")
    payload["candidate_judgments"] = [
        {
            "schema_version": 1,
            "id": "judgment-1",
            "project_id": "p1",
            "node_id": "node-1",
            "candidate_id": "candidate-1",
            "media_kind": "audio",
            "verdict": "keep",
            "reason": "",
            "created_at": 1.0,
        }
    ]
    _write_raw(tmp_path, payload)
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)

    payload["candidate_judgments"][0]["media_kind"] = "video"
    payload["candidate_judgments"].append(dict(payload["candidate_judgments"][0]))
    _write_raw(tmp_path, payload)
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)


def test_old_project_without_judgments_loads_with_empty_list(tmp_path):
    payload = _project(status="completed").model_dump(mode="json")
    payload.pop("candidate_judgments", None)
    _write_raw(tmp_path, payload)

    store = AtelierStorage(tmp_path)

    assert store.get_project("p1").candidate_judgments == []
    store.close()


def test_empty_judgment_list_does_not_validate_legacy_candidate_collections(tmp_path):
    payload = _project(status="completed").model_dump(mode="json")
    payload["candidate_judgments"] = []
    payload["nodes"][0]["data"]["candidates"] = {"legacy": "opaque"}
    payload["generation_attempts"] = []
    payload["generation_request_receipts"] = []
    _write_raw(tmp_path, payload)

    store = AtelierStorage(tmp_path)

    assert store.get_project("p1").candidate_judgments == []
    store.close()


def test_orphan_judgment_chain_requires_consistent_media_kind(tmp_path):
    payload = _project(status="completed").model_dump(mode="json")
    payload["nodes"] = []
    payload["candidate_judgments"] = [
        {
            "schema_version": 1,
            "id": "judgment-1",
            "project_id": "p1",
            "node_id": "deleted-node",
            "candidate_id": "candidate-1",
            "media_kind": "video",
            "verdict": "keep",
            "reason": "",
            "created_at": 1.0,
        },
        {
            "schema_version": 1,
            "id": "judgment-2",
            "project_id": "p1",
            "node_id": "deleted-node",
            "candidate_id": "candidate-1",
            "media_kind": "image",
            "verdict": "reject",
            "reason": "",
            "created_at": 2.0,
            "supersedes_judgment_id": "judgment-1",
        },
    ]
    _write_raw(tmp_path, payload)

    with pytest.raises(StorageError, match="media_kind"):
        AtelierStorage(tmp_path)


def test_judgment_validation_ignores_unreferenced_legacy_candidate_buckets(tmp_path):
    payload = _project(status="completed").model_dump(mode="json")
    payload["candidate_judgments"] = [
        {
            "schema_version": 1,
            "id": "judgment-1",
            "project_id": "p1",
            "node_id": "node-1",
            "candidate_id": "candidate-1",
            "media_kind": "video",
            "verdict": "keep",
            "reason": "",
            "created_at": 1.0,
        }
    ]
    payload["nodes"].append(
        {
            "id": "legacy-image",
            "project_id": "p1",
            "type": "image",
            "title": "Legacy",
            "prompt": "",
            "status": "draft",
            "x": 0.0,
            "y": 0.0,
            "width": 320.0,
            "height": 180.0,
            "source_project_id": None,
            "frame_id": None,
            "asset_id": None,
            "video_task_id": None,
            "media_urls": [],
            "data": {"candidates": {"legacy": "opaque"}},
            "created_by": "user",
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    )
    _write_raw(tmp_path, payload)

    store = AtelierStorage(tmp_path)

    assert store.get_project("p1").candidate_judgments[0].id == "judgment-1"
    store.close()
