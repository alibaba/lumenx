from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json

import pytest

from src.apps.atelier.export_history import DeleteExportRecord
from src.apps.atelier.models import (
    AtelierNode,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequenceExportJob,
    AtelierSequencePublicationProof,
    ExportDeletionReceipt,
    ExportRecord,
)
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


BYTES = b"rendered export bytes"
SHA = hashlib.sha256(BYTES).hexdigest()


def make_project(project_id: str = "p1") -> AtelierProject:
    return AtelierProject(id=project_id, title="Canvas", created_at=1.0, updated_at=1.0)


def snapshot() -> dict:
    return {"schema_version": "atelier.timeline.v1", "clips": [], "audio_tracks": []}


def digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def completed_project(project_id: str = "p1", *, job_status: str = "completed") -> AtelierProject:
    project = make_project(project_id)
    snap = snapshot()
    snap_digest = digest(snap)
    job = AtelierSequenceExportJob(
        job_id="job1",
        project_id=project.id,
        status=job_status,
        client_request_id="create-1",
        request_fingerprint="a" * 64,
        base_revision=0,
        created_at=10.0,
        updated_at=20.0,
        finished_at=20.0 if job_status == "completed" else None,
        snapshot=snap,
        snapshot_digest=snap_digest,
        source_entries=[AtelierSequenceEntry(parentId="source", id="clip")],
        output_ref="video/atelier_seq_job1.mp4",
        export_id="export1" if job_status == "completed" else None,
    )
    proof = AtelierSequencePublicationProof(
        job_id=job.job_id,
        project_id=project.id,
        relative_path=job.output_ref,
        sha256=SHA,
        size_bytes=len(BYTES),
        duration=1.0,
        width=64,
        height=48,
        fps=24,
        snapshot_digest=snap_digest,
        prepared_at=19.0,
    )
    if job_status in {"completed", "repair_required"}:
        job.prepared_proof = proof
    project.sequence_export_jobs.append(job)
    if job_status == "completed":
        project.exports.append(
            ExportRecord(
                id="export1",
                project_id=project.id,
                created_at=20.0,
                clip_count=1,
                size_mb=len(BYTES) / 1024 / 1024,
                video_url=job.output_ref,
                filename="atelier_seq_job1.mp4",
                source_entries=list(job.source_entries),
                timeline_snapshot=snap,
                publication_proof=proof,
                job_id=job.job_id,
                snapshot_digest=snap_digest,
            )
        )
    return project


def completed_project_with_two_exports() -> AtelierProject:
    project = completed_project()
    source_job = project.sequence_export_jobs[0]
    source_proof = source_job.prepared_proof
    assert source_proof is not None
    second_output = "video/atelier_seq_job2.mp4"
    second_proof = source_proof.model_copy(
        deep=True,
        update={"job_id": "job2", "relative_path": second_output},
    )
    second_job = source_job.model_copy(
        deep=True,
        update={
            "job_id": "job2",
            "client_request_id": "create-2",
            "output_ref": second_output,
            "export_id": "export2",
            "prepared_proof": second_proof,
        },
    )
    second_export = project.exports[0].model_copy(
        deep=True,
        update={
            "id": "export2",
            "video_url": second_output,
            "filename": "atelier_seq_job2.mp4",
            "publication_proof": second_proof,
            "job_id": "job2",
        },
    )
    project.sequence_export_jobs.append(second_job)
    project.exports.append(second_export)
    return project


@contextmanager
def no_external_refs():
    yield True


@contextmanager
def external_refs():
    yield False


def add_reference(project: AtelierProject, ref: str, *, node_id: str = "ref-node") -> None:
    project.nodes.append(
        AtelierNode(
            id=node_id,
            project_id=project.id,
            type="video",
            status="completed",
            media_urls=[ref],
        )
    )


def test_legacy_export_without_deletion_round_trips_and_unknown_receipt_fields_reject(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(completed_project())
    store.close()

    raw_path = tmp_path / "atelier_projects.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["p1"]["exports"][0].get("deletion") is None
    reopened = AtelierStorage(tmp_path)
    assert reopened.get_project("p1").exports[0].deletion is None
    reopened.close()

    raw["p1"]["exports"][0]["deletion"] = {
        "client_request_id": "delete-1",
        "delete_file": True,
        "deleted_at": 30.0,
        "file_status": "blocked",
        "checked_at": 31.0,
        "error_code": "has_reference",
        "error_message": "referenced elsewhere",
        "future": "reject",
    }
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(StorageError, match="Unsupported Atelier field"):
        AtelierStorage(tmp_path)


def test_delete_tombstone_is_request_id_idempotent_and_preserves_completed_job(tmp_path):
    from src.apps.atelier.export_history import ExportHistoryService

    store = AtelierStorage(tmp_path)
    store.create_project(completed_project())
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 30.0)

    ack = service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-1", delete_file=False))

    assert ack.replayed is False
    assert ack.project.revision == 1
    assert ack.export_record.deletion is not None
    assert ack.export_record.deletion.file_status == "retained"
    assert ack.export_record.deletion.checked_at == 30.0
    assert ack.project.sequence_export_jobs[0].status == "completed"
    assert ack.project.sequence_export_jobs[0].export_id == "export1"
    assert ack.project.exports[0].publication_proof == ack.project.sequence_export_jobs[0].prepared_proof

    replay = service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-1", delete_file=False))
    assert replay.replayed is True
    assert replay.project.revision == 1

    with pytest.raises(StorageConflictError, match="request id"):
        service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=1, client_request_id="delete-1", delete_file=True))
    with pytest.raises(StorageConflictError, match="already deleted"):
        service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=1, client_request_id="delete-2", delete_file=False))


def test_file_delete_uses_tombstone_first_and_records_missing_blocked_and_final_uncertainty(tmp_path, monkeypatch):
    from src.apps.atelier import storage as storage_module
    from src.apps.atelier.export_history import ExportHistoryService

    output_file = tmp_path / "video" / "atelier_seq_job1.mp4"
    output_file.parent.mkdir()
    output_file.write_bytes(BYTES)
    store = AtelierStorage(tmp_path)
    store.create_project(completed_project())
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 40.0)

    original_commit = store._commit
    calls = {"count": 0}

    def fail_first_commit(next_projects):
        calls["count"] += 1
        if calls["count"] == 1:
            raise StorageError("first save failed")
        return original_commit(next_projects)

    monkeypatch.setattr(store, "_commit", fail_first_commit)
    with pytest.raises(StorageError, match="first save failed"):
        service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-savefail"))
    assert output_file.exists()
    monkeypatch.setattr(store, "_commit", original_commit)

    blocked = ExportHistoryService(store, external_reference_guard=lambda *_args: external_refs(), clock=lambda: 41.0).delete_export(
        "p1",
        "export1",
        DeleteExportRecord(expected_revision=0, client_request_id="delete-1"),
    )
    assert blocked.replayed is False
    assert blocked.export_record.deletion.file_status == "blocked"
    assert blocked.export_record.deletion.error_code == "has_external_reference"
    assert output_file.exists()

    deleted = service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=2, client_request_id="delete-1"))
    assert deleted.replayed is True
    assert deleted.export_record.deletion.file_status == "deleted"
    assert not output_file.exists()

    replay_final = service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=3, client_request_id="delete-1"))
    assert replay_final.replayed is True
    assert replay_final.export_record.deletion.file_status == "deleted"

    store_missing = AtelierStorage(tmp_path / "missing")
    store_missing.create_project(completed_project())
    missing = ExportHistoryService(store_missing, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 44.0).delete_export(
        "p1",
        "export1",
        DeleteExportRecord(expected_revision=0, client_request_id="delete-missing"),
    )
    assert missing.export_record.deletion.file_status == "already_missing"

    store2 = AtelierStorage(tmp_path / "uncertain")
    path2 = tmp_path / "uncertain" / "video" / "atelier_seq_job1.mp4"
    path2.parent.mkdir()
    path2.write_bytes(BYTES)
    store2.create_project(completed_project())
    service2 = ExportHistoryService(store2, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 50.0)
    real_atomic = storage_module.atomic_write_json
    writes = {"count": 0}

    def second_write_uncertain(path, value):
        writes["count"] += 1
        real_atomic(path, value)
        if writes["count"] == 2:
            raise StorageError("final fact uncertain", commit_uncertain=True)

    monkeypatch.setattr(storage_module, "atomic_write_json", second_write_uncertain)
    with pytest.raises(StorageError, match="final fact uncertain"):
        service2.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-uncertain"))
    assert not path2.exists()
    with pytest.raises(StorageError, match="restart"):
        store2.get_project("p1")


def test_post_admission_same_project_reference_blocks_unlink(tmp_path, monkeypatch):
    from src.apps.atelier.export_history import ExportHistoryService

    output_file = tmp_path / "video" / "atelier_seq_job1.mp4"
    output_file.parent.mkdir()
    output_file.write_bytes(BYTES)
    store = AtelierStorage(tmp_path)
    store.create_project(completed_project())
    original_admission = store.apply_export_deletion_admission

    def admit_then_add_ref(*args, **kwargs):
        result = original_admission(*args, **kwargs)
        store.apply_project_command(
            "p1",
            expected_revision=1,
            command=lambda project: add_reference(project, "outputs/video/atelier_seq_job1.mp4"),
        )
        return result

    monkeypatch.setattr(store, "apply_export_deletion_admission", admit_then_add_ref)
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 70.0)

    ack = service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-race"))

    assert ack.export_record.deletion.file_status == "blocked"
    assert ack.export_record.deletion.error_code == "has_atelier_reference"
    assert output_file.read_bytes() == BYTES


def test_post_admission_other_atelier_project_reference_blocks_unlink(tmp_path, monkeypatch):
    from src.apps.atelier.export_history import ExportHistoryService

    output_file = tmp_path / "video" / "atelier_seq_job1.mp4"
    output_file.parent.mkdir()
    output_file.write_bytes(BYTES)
    store = AtelierStorage(tmp_path)
    store.create_project(completed_project())
    store.create_project(make_project("p2"))
    original_admission = store.apply_export_deletion_admission

    def admit_then_add_other_project_ref(*args, **kwargs):
        result = original_admission(*args, **kwargs)
        store.apply_project_command(
            "p2",
            expected_revision=0,
            command=lambda project: add_reference(project, str(tmp_path / "video" / "atelier_seq_job1.mp4"), node_id="other-ref"),
        )
        return result

    monkeypatch.setattr(store, "apply_export_deletion_admission", admit_then_add_other_project_ref)
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 80.0)

    ack = service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-race-other"))

    assert ack.export_record.deletion.file_status == "blocked"
    assert ack.export_record.deletion.error_code == "has_atelier_reference"
    assert output_file.read_bytes() == BYTES


@pytest.mark.parametrize("status", ["queued", "running", "cancelling", "repair_required"])
def test_delete_project_refuses_unresolved_export_jobs(tmp_path, status):
    store = AtelierStorage(tmp_path)
    store.create_project(completed_project(job_status=status))

    with pytest.raises(StorageConflictError, match="pending Atelier export operation"):
        store.delete_project("p1", expected_revision=0)


def test_delete_project_refuses_pending_export_deletion_receipt_but_allows_blocked(tmp_path):
    from src.apps.atelier.export_history import ExportHistoryService

    store = AtelierStorage(tmp_path)
    store.create_project(completed_project())
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: external_refs(), clock=lambda: 60.0)
    service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-1"))
    store.apply_export_deletion_file_fact(
        "p1",
        export_id="export1",
        client_request_id="delete-1",
        file_status="pending",
        checked_at=None,
        error_code=None,
        error_message=None,
    )

    with pytest.raises(StorageConflictError, match="pending Atelier export operation"):
        store.delete_project("p1", expected_revision=3)

    store.apply_export_deletion_file_fact(
        "p1",
        export_id="export1",
        client_request_id="delete-1",
        file_status="blocked",
        checked_at=61.0,
        error_code="has_external_reference",
        error_message="referenced elsewhere",
    )
    store.delete_project("p1", expected_revision=4)


@pytest.mark.parametrize(
    "ref",
    [
        "http://localhost:17177/files/videos/atelier_seq_job1.mp4",
        "/files/videos/atelier_seq_job1.mp4",
        "/files/outputs/videos/atelier_seq_job1.mp4",
        "files/video/atelier_seq_job1.mp4",
        "files/videos/atelier_seq_job1.mp4",
        "/video/atelier_seq_job1.mp4",
    ],
)
def test_managed_reference_aliases_include_static_file_and_leading_slash_paths(tmp_path, ref):
    from src.apps.atelier.export_history import ExportHistoryService

    output_file = tmp_path / "video" / "atelier_seq_job1.mp4"
    output_file.parent.mkdir()
    output_file.write_bytes(BYTES)
    store = AtelierStorage(tmp_path)
    project = completed_project()
    add_reference(project, ref)
    store.create_project(project)
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 90.0)

    ack = service.delete_export("p1", "export1", DeleteExportRecord(expected_revision=0, client_request_id="delete-url-ref"))

    assert ack.export_record.deletion.file_status == "blocked"
    assert ack.export_record.deletion.error_code == "has_atelier_reference"
    assert output_file.read_bytes() == BYTES


def test_duplicate_export_deletion_request_ids_reject_on_load(tmp_path):
    store = AtelierStorage(tmp_path)
    project = completed_project_with_two_exports()
    store.create_project(project)
    store.close()

    raw_path = tmp_path / "atelier_projects.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    duplicate = ExportDeletionReceipt(
        client_request_id="delete-duplicate",
        delete_file=False,
        deleted_at=100.0,
        file_status="retained",
        checked_at=100.0,
    ).model_dump(mode="json")
    raw["p1"]["exports"][0]["deletion"] = duplicate
    raw["p1"]["exports"][1]["deletion"] = duplicate
    raw_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(StorageError, match="Duplicate Atelier export deletion request id"):
        AtelierStorage(tmp_path)
