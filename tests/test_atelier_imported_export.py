from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

import pytest

from src.apps.atelier.export_history import DeleteExportRecord, ExportHistoryService
from src.apps.atelier.export_jobs import RedoSequenceExportJob
from src.apps.atelier.models import (
    AtelierImportedExportProof,
    AtelierNode,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequencePublicationProof,
    ExportRecord,
)
from src.apps.atelier.sequence_export_service import SequenceExportService
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageError


IMPORTED_BYTES = b"imported export bytes"
IMPORTED_SHA = hashlib.sha256(IMPORTED_BYTES).hexdigest()


@contextmanager
def no_external_refs():
    yield True


def _digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timeline(ref: str = "uploads/clip.mp4") -> dict:
    return {
        "schema_version": "atelier.timeline.v1",
        "clips": [
            {
                "clip_id": "clip-1",
                "parentId": "video-1",
                "candidateId": None,
                "source_url": ref,
                "media_version": {"relative_path": ref, "sha256": "a" * 64},
                "trimStart": 0.0,
                "trimEnd": 0.4,
                "gain": 1.0,
                "transition": None,
            }
        ],
        "audio_tracks": [],
    }


def _imported_project(project_id: str = "p1", *, export_id: str = "imported-export") -> AtelierProject:
    video_url = f"video/imported/{project_id}/{export_id}/atelier_seq_source.mp4"
    project = AtelierProject(
        id=project_id,
        title="Imported",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="video-1",
                project_id=project_id,
                type="video",
                status="completed",
                media_urls=["uploads/clip.mp4"],
            )
        ],
        sequence=[AtelierSequenceEntry(id="clip-1", parentId="video-1")],
        exports=[
            ExportRecord(
                id=export_id,
                project_id=project_id,
                created_at=2.0,
                clip_count=1,
                size_mb=len(IMPORTED_BYTES) / 1024 / 1024,
                video_url=video_url,
                filename="atelier_seq_source.mp4",
                source_entries=[AtelierSequenceEntry(id="clip-1", parentId="video-1")],
                timeline_snapshot=_timeline(),
                imported_proof=AtelierImportedExportProof(
                    project_id=project_id,
                    export_id=export_id,
                    relative_path=video_url,
                    sha256=IMPORTED_SHA,
                    size_bytes=len(IMPORTED_BYTES),
                    source_project_id="source-project",
                    source_export_id="source-export",
                    source_storage_id="source-storage",
                    imported_at=3.0,
                ),
            )
        ],
    )
    return project


def _write_imported_media(root: Path, project: AtelierProject) -> Path:
    ref = project.exports[0].video_url
    path = root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(IMPORTED_BYTES)
    clip = root / "uploads" / "clip.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"fake source clip")
    return path


def _real_imported_store(tmp_path: Path):
    from src.apps.atelier.project_package import build_package, prepare_import
    from tests.test_atelier_sequence_export_service import _command, _store_with_project

    source_store = _store_with_project(tmp_path)
    source_service = SequenceExportService(
        source_store,
        id_factory=lambda: "native-job",
        export_id_factory=lambda: "native-export",
        clock=lambda: 10.0,
    )
    created = source_service.create_sequence_export_job("atelier-1", _command())
    completed = source_service.run_sequence_export_job("atelier-1", created.job.job_id)
    source_project = source_store.get_project("atelier-1")
    source_export = completed.export_record
    assert source_export is not None
    source_file = tmp_path / source_export.video_url
    source_store.close()

    artifact = build_package(source_project, data_dir=tmp_path, source_storage_id="source-store")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        now_fn=lambda: 99.0,
        id_factory=lambda kind: f"target-{kind}",
    )
    store = AtelierStorage(tmp_path)
    imported = store.create_project(prepared.project)
    export = imported.exports[0]
    assert export.imported_proof is not None
    assert (tmp_path / export.imported_proof.relative_path).exists()
    return store, imported, export, source_file


def test_storage_rejects_imported_export_mixed_with_native_authority(tmp_path):
    project = _imported_project()
    export = project.exports[0]
    export.video_url = "video/atelier_seq_native.mp4"
    export.filename = "atelier_seq_native.mp4"
    assert export.imported_proof is not None
    export.imported_proof.relative_path = export.video_url
    snap_digest = _digest(export.timeline_snapshot or {})
    export.job_id = "old-job"
    export.snapshot_digest = snap_digest
    export.publication_proof = AtelierSequencePublicationProof(
        job_id="old-job",
        project_id=project.id,
        relative_path=export.video_url,
        sha256=IMPORTED_SHA,
        size_bytes=len(IMPORTED_BYTES),
        duration=1.0,
        width=64,
        height=48,
        fps=24,
        snapshot_digest=snap_digest,
        prepared_at=4.0,
    )

    store = AtelierStorage(tmp_path)
    with pytest.raises(StorageError, match="imported export proof"):
        store.create_project(project)


def test_project_command_rejects_valid_shape_imported_proof_and_provenance_mutation(tmp_path):
    store, project, export, _source_file = _real_imported_store(tmp_path)

    with pytest.raises(StorageError, match="imported export proof"):
        store.apply_project_command(
            project.id,
            expected_revision=0,
            command=lambda item: setattr(item.exports[0].imported_proof, "sha256", "f" * 64),
        )
    with pytest.raises(StorageError, match="imported export proof"):
        store.apply_project_command(
            project.id,
            expected_revision=0,
            command=lambda item: setattr(item.exports[0].imported_proof, "source_project_id", "other-source-project"),
        )
    replacement_path = f"video/imported/{project.id}/replacement/atelier_seq_replacement.mp4"

    def replace_path_and_proof(item: AtelierProject) -> None:
        target = item.exports[0]
        assert target.imported_proof is not None
        target.video_url = replacement_path
        target.filename = "atelier_seq_replacement.mp4"
        target.imported_proof = target.imported_proof.model_copy(
            deep=True,
            update={
                "relative_path": replacement_path,
                "sha256": "e" * 64,
                "size_bytes": len(IMPORTED_BYTES) + 1,
            },
        )

    with pytest.raises(StorageError, match="imported export proof"):
        store.apply_project_command(project.id, expected_revision=0, command=replace_path_and_proof)
    with pytest.raises(StorageError, match="import provenance"):
        store.apply_project_command(
            project.id,
            expected_revision=0,
            command=lambda item: setattr(item.import_provenance, "source_project_id", "other-source-project"),
        )
    saved = store.get_project(project.id)
    assert saved.exports[0].imported_proof == export.imported_proof
    assert saved.import_provenance == project.import_provenance


def test_project_command_cannot_add_imported_proof_to_existing_or_new_export(tmp_path):
    store, project, export, _source_file = _real_imported_store(tmp_path)
    source = store.get_project("atelier-1")
    native_export = source.exports[0]
    assert native_export.imported_proof is None

    def add_proof_to_existing_native(item: AtelierProject) -> None:
        target = item.exports[0]
        target.imported_proof = AtelierImportedExportProof(
            project_id=item.id,
            export_id=target.id,
            relative_path=f"video/imported/{item.id}/forged/atelier_seq_forged.mp4",
            sha256="d" * 64,
            size_bytes=123,
            source_project_id="source",
            source_export_id="source-export",
            source_storage_id="source-storage",
            imported_at=44.0,
        )

    with pytest.raises(StorageError, match="imported export proof"):
        store.apply_project_command(source.id, expected_revision=source.revision, command=add_proof_to_existing_native)

    forged_export = ExportRecord(
        id="forged-import",
        project_id=source.id,
        created_at=44.0,
        clip_count=1,
        size_mb=0.1,
        video_url=f"video/imported/{source.id}/forged/atelier_seq_forged.mp4",
        filename="atelier_seq_forged.mp4",
        source_entries=list(native_export.source_entries),
        timeline_snapshot=native_export.timeline_snapshot,
        imported_proof=AtelierImportedExportProof(
            project_id=source.id,
            export_id="forged-import",
            relative_path=f"video/imported/{source.id}/forged/atelier_seq_forged.mp4",
            sha256="c" * 64,
            size_bytes=99,
            source_project_id="source",
            source_export_id="source-export",
            source_storage_id="source-storage",
            imported_at=44.0,
        ),
    )
    with pytest.raises(StorageError, match="imported export proof"):
        store.apply_project_command(
            source.id,
            expected_revision=source.revision,
            command=lambda item: item.exports.append(forged_export),
        )
    assert store.get_project(source.id).exports[0].imported_proof is None


def test_storage_rejects_raw_forged_imported_proof_owner_and_traversal_path(tmp_path):
    store, project, _export, _source_file = _real_imported_store(tmp_path)
    raw_path = tmp_path / "atelier_projects.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw[project.id]["exports"][0]["imported_proof"]["project_id"] = "other-project"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    store.close()
    with pytest.raises(StorageError, match="imported export proof"):
        AtelierStorage(tmp_path)

    raw[project.id]["exports"][0]["imported_proof"]["project_id"] = project.id
    raw[project.id]["exports"][0]["video_url"] = f"video/imported/{project.id}/../atelier_seq_bad.mp4"
    raw[project.id]["exports"][0]["filename"] = "atelier_seq_bad.mp4"
    raw[project.id]["exports"][0]["imported_proof"]["relative_path"] = raw[project.id]["exports"][0]["video_url"]
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(StorageError, match="imported export proof"):
        AtelierStorage(tmp_path)


def test_delete_imported_export_verifies_target_proof_and_never_source_path(tmp_path):
    store, project, export, source_file = _real_imported_store(tmp_path)
    imported_path = tmp_path / export.video_url
    assert source_file.exists()

    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 10.0)
    ack = service.delete_export(project.id, export.id, DeleteExportRecord(expected_revision=0, client_request_id="delete-imported"))

    assert ack.export_record.deletion is not None
    assert ack.export_record.deletion.file_status == "deleted"
    assert not imported_path.exists()
    assert source_file.exists()


def test_delete_imported_export_blocks_hash_mismatch_and_cross_reference(tmp_path):
    store, project, export, _source_file = _real_imported_store(tmp_path)
    imported_path = tmp_path / export.video_url
    imported_path.write_bytes(b"tampered")
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 11.0)

    blocked = service.delete_export(project.id, export.id, DeleteExportRecord(expected_revision=0, client_request_id="delete-bad-hash"))

    assert blocked.export_record.deletion is not None
    assert blocked.export_record.deletion.file_status == "blocked"
    assert blocked.export_record.deletion.error_code == "media_proof_mismatch"
    assert imported_path.read_bytes() == b"tampered"


def test_delete_imported_export_blocks_cross_reference_without_unlink(tmp_path):
    store, project, export, _source_file = _real_imported_store(tmp_path)
    imported_path = tmp_path / export.video_url
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 12.0)
    store.apply_project_command(
        project.id,
        expected_revision=0,
        command=lambda item: item.nodes.append(
            AtelierNode(
                id="ref-node",
                project_id=item.id,
                type="video",
                status="completed",
                media_urls=[export.video_url],
            )
        ),
    )
    blocked_ref = service.delete_export(
        project.id,
        export.id,
        DeleteExportRecord(expected_revision=1, client_request_id="delete-referenced"),
    )
    assert blocked_ref.export_record.deletion is not None
    assert blocked_ref.export_record.deletion.file_status == "blocked"
    assert blocked_ref.export_record.deletion.error_code == "has_atelier_reference"
    assert imported_path.exists()


def test_redo_imported_export_runs_fresh_job_without_native_source_authority(tmp_path):
    store, project, export, _source_file = _real_imported_store(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "redo-job", export_id_factory=lambda: "redo-export", clock=lambda: 20.0)

    ack = service.redo_sequence_export_job(
        project.id,
        export.id,
        RedoSequenceExportJob(expected_revision=0, client_request_id="redo-imported"),
    )

    assert ack.replayed is False
    assert ack.job.job_id == "redo-job"
    assert ack.job.request_contract_version == "atelier-sequence-export-redo-v1"
    assert ack.job.source_export_id == export.id
    assert ack.job.prepared_proof is None
    assert ack.job.snapshot == export.timeline_snapshot
    assert ack.job.source_entries == export.source_entries
    completed = service.run_sequence_export_job(project.id, ack.job.job_id)
    assert completed.export_record is not None
    assert completed.export_record.id == "redo-export"
    assert completed.export_record.publication_proof is not None
    assert completed.export_record.imported_proof is None
    saved_import = next(item for item in store.get_project(project.id).exports if item.id == export.id)
    assert saved_import.job_id is None
    assert saved_import.publication_proof is None


def test_imported_delete_blocks_symlinked_target_directory(tmp_path):
    project = _imported_project("p-sym", export_id="export")
    proof = project.exports[0].imported_proof
    assert proof is not None
    imported_root = tmp_path / "video" / "imported"
    imported_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "export").mkdir(parents=True)
    target = outside / "export" / "atelier_seq_source.mp4"
    target.write_bytes(IMPORTED_BYTES)
    (imported_root / project.id).symlink_to(outside, target_is_directory=True)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "clip.mp4").write_bytes(b"clip")
    store = AtelierStorage(tmp_path)
    store.create_project(project)
    service = ExportHistoryService(store, external_reference_guard=lambda *_args: no_external_refs(), clock=lambda: 12.0)

    blocked = service.delete_export(project.id, project.exports[0].id, DeleteExportRecord(expected_revision=0, client_request_id="delete-symlink"))

    assert blocked.export_record.deletion is not None
    assert blocked.export_record.deletion.file_status == "blocked"
    assert blocked.export_record.deletion.error_code == "media_proof_mismatch"
    assert target.read_bytes() == IMPORTED_BYTES
