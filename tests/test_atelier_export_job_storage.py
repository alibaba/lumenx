from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pydantic import ValidationError

from src.apps.atelier.export_jobs import (
    CreateSequenceExportJob,
    RedoSequenceExportJob,
    SequenceExportJobAck,
    fingerprint_sequence_export_redo_request,
    fingerprint_sequence_export_request,
)
from src.apps.atelier.models import (
    AtelierNode,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequenceExportJob,
    AtelierSequencePublicationProof,
    ExportRecord,
)
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError

HASH_A = "a" * 64


def make_project(project_id: str = "p1") -> AtelierProject:
    return AtelierProject(
        id=project_id,
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[AtelierNode(id="video-1", project_id=project_id, type="video", media_urls=["video/source.mp4"])],
        sequence=[AtelierSequenceEntry(parentId="video-1", candidateId=None, id="clip-1")],
    )


def make_snapshot() -> dict:
    return {
        "schema_version": "atelier.timeline.v1",
        "clips": [{"url": "video/source.mp4", "duration": 1.0}],
        "audio_tracks": [],
    }


def snapshot_digest(snapshot: dict) -> str:
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_job(project: AtelierProject, *, job_id: str = "job-1", client_request_id: str = "req-1", fingerprint: str = HASH_A) -> AtelierSequenceExportJob:
    return AtelierSequenceExportJob(
        job_id=job_id,
        project_id=project.id,
        status="queued",
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        base_revision=project.revision,
        created_at=10.0,
        updated_at=10.0,
        snapshot=make_snapshot(),
        snapshot_digest=snapshot_digest(make_snapshot()),
        source_entries=list(project.sequence),
        source_audio_tracks=list(project.audio_tracks),
        output_ref=f"video/atelier_seq_{job_id}.mp4",
    )


def make_proof(job: AtelierSequenceExportJob) -> AtelierSequencePublicationProof:
    return AtelierSequencePublicationProof(
        job_id=job.job_id,
        project_id=job.project_id,
        relative_path=job.output_ref,
        sha256="c" * 64,
        size_bytes=4096,
        duration=1.5,
        width=1920,
        height=1080,
        fps=24,
        snapshot_digest=job.snapshot_digest,
        prepared_at=20.0,
    )


def db_path(root: Path) -> Path:
    return root / "atelier_projects.json"


def read_raw(root: Path) -> dict:
    return json.loads(db_path(root).read_text(encoding="utf-8"))


def create_job(store: AtelierStorage, *, expected_revision: int = 0, client_request_id: str = "req-1", fingerprint: str = HASH_A):
    def command(project: AtelierProject) -> None:
        project.sequence_export_jobs.append(make_job(project, client_request_id=client_request_id, fingerprint=fingerprint))
        project.updated_at = 10.0

    return store.apply_sequence_export_job_creation(
        "p1",
        expected_revision=expected_revision,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        command=command,
    )


def test_export_job_request_fingerprint_preserves_omitted_vs_explicit_empty():
    missing = CreateSequenceExportJob(expected_revision=0, client_request_id="req-1")
    explicit = CreateSequenceExportJob(expected_revision=0, client_request_id="req-1", entries=[], audio_tracks=[])

    assert fingerprint_sequence_export_request(missing) == "7bb4dbff380da24177298fb3fd8950b32fa1c511905eceb232c4400c38a97d9b"
    assert fingerprint_sequence_export_request(explicit) == "506d89221b1813a3f3967adb7a165c7c0c7cf848cfacdee4a3f323cc1a302f7a"
    assert fingerprint_sequence_export_request(missing) != fingerprint_sequence_export_request(explicit)

    project = make_project()
    job = make_job(project)
    ack = SequenceExportJobAck(project=project, job=job, new_job_ids=[job.job_id])
    assert ack.replayed is False
    assert ack.new_job_ids == ["job-1"]


def test_redo_export_request_has_separate_contract_and_rejects_create_fields():
    command = RedoSequenceExportJob(expected_revision=4, client_request_id="redo-1")

    assert fingerprint_sequence_export_redo_request(command, source_export_id="export-1") != fingerprint_sequence_export_request(
        CreateSequenceExportJob(expected_revision=4, client_request_id="redo-1")
    )
    assert fingerprint_sequence_export_redo_request(command, source_export_id="export-1") != fingerprint_sequence_export_redo_request(
        command,
        source_export_id="export-2",
    )
    with pytest.raises(ValidationError):
        RedoSequenceExportJob.model_validate({"expected_revision": 4, "client_request_id": "redo-1", "entries": []})


def test_create_sequence_export_job_is_request_id_idempotent_without_project_revision_bump(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())

    project, job, replayed = create_job(store)

    assert replayed is False
    assert project.revision == 0
    assert job.job_id == "job-1"
    assert job.job_revision == 0
    assert store.get_project("p1").sequence_export_jobs[0].client_request_id == "req-1"

    store.apply_project_command("p1", expected_revision=0, command=lambda project: setattr(project, "title", "Edited"))
    replay_project, replay_job, replayed = create_job(store, expected_revision=0)

    assert replayed is True
    assert replay_project.revision == 1
    assert replay_job.job_id == "job-1"
    assert len(replay_project.sequence_export_jobs) == 1

    with pytest.raises(StorageConflictError, match="request id"):
        create_job(store, expected_revision=1, fingerprint="d" * 64)



def test_create_job_cannot_mutate_existing_export_jobs(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)

    def create_second_and_mutate_existing(project: AtelierProject) -> None:
        project.sequence_export_jobs[0].status = "running"
        project.sequence_export_jobs.append(
            make_job(project, job_id="job-2", client_request_id="req-2", fingerprint="d" * 64)
        )

    with pytest.raises(StorageError, match="creation can only append the export job"):
        store.apply_sequence_export_job_creation(
            "p1",
            expected_revision=0,
            client_request_id="req-2",
            request_fingerprint="d" * 64,
            command=create_second_and_mutate_existing,
        )


def test_completion_preserves_existing_exports_and_rejects_unrelated_export_append(tmp_path):
    project = make_project()
    project.exports.append(
        ExportRecord(
            id="legacy-export",
            project_id=project.id,
            created_at=5.0,
            video_url="videos/legacy.mp4",
            filename="legacy.mp4",
        )
    )
    store = AtelierStorage(tmp_path)
    store.create_project(project)
    create_job(store)

    def mutate_existing_export(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        proof = make_proof(job)
        job.status = "completed"
        job.prepared_proof = proof
        job.export_id = "export-1"
        job.finished_at = 21.0
        project.exports[0].filename = "mutated.mp4"
        project.exports.append(
            ExportRecord(
                id="export-1",
                project_id=project.id,
                created_at=21.0,
                clip_count=len(job.source_entries),
                size_mb=proof.size_bytes / 1024 / 1024,
                video_url=proof.relative_path,
                filename=Path(proof.relative_path).name,
                source_entries=list(job.source_entries),
                timeline_snapshot=job.snapshot,
                publication_proof=proof,
                job_id=job.job_id,
                snapshot_digest=job.snapshot_digest,
            )
        )

    with pytest.raises(StorageError, match="must append exactly one matching export record"):
        store.apply_sequence_export_completion("p1", job_id="job-1", command=mutate_existing_export)

    def append_unrelated_export(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        proof = make_proof(job)
        job.status = "completed"
        job.prepared_proof = proof
        job.export_id = "export-1"
        job.finished_at = 21.0
        project.exports.append(
            ExportRecord(
                id="export-1",
                project_id=project.id,
                created_at=21.0,
                clip_count=len(job.source_entries),
                size_mb=proof.size_bytes / 1024 / 1024,
                video_url=proof.relative_path,
                filename=Path(proof.relative_path).name,
                source_entries=list(job.source_entries),
                timeline_snapshot=job.snapshot,
                publication_proof=proof,
                job_id=job.job_id,
                snapshot_digest=job.snapshot_digest,
            )
        )
        project.exports.append(
            ExportRecord(
                id="unrelated",
                project_id=project.id,
                created_at=22.0,
                video_url="videos/unrelated.mp4",
                filename="unrelated.mp4",
            )
        )

    with pytest.raises(StorageError, match="must append exactly one matching export record"):
        store.apply_sequence_export_completion("p1", job_id="job-1", command=append_unrelated_export)

def test_job_update_changes_only_job_state_without_project_revision_bump(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)

    def mark_running(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        assert project.id == "p1"
        job.status = "running"
        job.started_at = 11.0
        job.updated_at = 11.0

    project, job = store.apply_sequence_export_job_update("p1", job_id="job-1", command=mark_running)

    assert project.revision == 0
    assert job.status == "running"
    assert job.job_revision == 1
    assert store.get_project("p1").sequence_export_jobs[0].job_revision == 1

    def bad_broad_mutation(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        job.updated_at = 12.0
        project.sequence.append(AtelierSequenceEntry(parentId="video-1"))

    with pytest.raises(StorageError, match="only change the matching export job"):
        store.apply_sequence_export_job_update("p1", job_id="job-1", command=bad_broad_mutation)



def test_job_update_rejects_job_list_mutation_with_storage_error(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)

    def add_extra_job(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        job.updated_at = 12.0
        project.sequence_export_jobs.append(
            make_job(project, job_id="job-2", client_request_id="req-2", fingerprint="d" * 64)
        )

    with pytest.raises(StorageError, match="only change the matching export job"):
        store.apply_sequence_export_job_update("p1", job_id="job-1", command=add_extra_job)


def test_export_completion_rejects_job_list_mutation_with_storage_error(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)

    def complete_and_delete_job(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        proof = make_proof(job)
        job.status = "completed"
        job.prepared_proof = proof
        job.export_id = "export-1"
        job.finished_at = 21.0
        project.exports.append(
            ExportRecord(
                id="export-1",
                project_id=project.id,
                created_at=21.0,
                clip_count=len(job.source_entries),
                size_mb=proof.size_bytes / 1024 / 1024,
                video_url=proof.relative_path,
                filename=Path(proof.relative_path).name,
                source_entries=list(job.source_entries),
                timeline_snapshot=job.snapshot,
                publication_proof=proof,
                job_id=job.job_id,
                snapshot_digest=job.snapshot_digest,
            )
        )
        project.sequence_export_jobs = []

    with pytest.raises(StorageError, match="only change the matching export job"):
        store.apply_sequence_export_completion("p1", job_id="job-1", command=complete_and_delete_job)

def test_job_update_cannot_mutate_immutable_request_snapshot_or_output_fields(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)

    immutable_mutations = [
        lambda job: setattr(job, "request_fingerprint", "d" * 64),
        lambda job: setattr(job, "snapshot_digest", "e" * 64),
        lambda job: setattr(job, "output_ref", "video/atelier_seq_other.mp4"),
        lambda job: job.source_entries.clear(),
        lambda job: job.snapshot.__setitem__("clips", []),
        lambda job: setattr(job, "base_revision", 99),
        lambda job: setattr(job, "created_at", 99.0),
    ]

    for mutate in immutable_mutations:
        with pytest.raises(StorageError, match="export job"):
            store.apply_sequence_export_job_update(
                "p1",
                job_id="job-1",
                command=lambda _project, job, mutate=mutate: mutate(job),
            )



def test_repair_required_requires_prepared_proof_and_recorded_proof_is_immutable(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)

    with pytest.raises(StorageError, match="repair proof"):
        store.apply_sequence_export_job_update(
            "p1",
            job_id="job-1",
            command=lambda _project, job: setattr(job, "status", "repair_required"),
        )

    def record_prepared_proof(_project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        job.status = "running"
        job.prepared_proof = make_proof(job)
        job.updated_at = 12.0

    store.apply_sequence_export_job_update("p1", job_id="job-1", command=record_prepared_proof)

    with pytest.raises(StorageError, match="prepared proof"):
        store.apply_sequence_export_job_update(
            "p1",
            job_id="job-1",
            command=lambda _project, job: setattr(job, "prepared_proof", None),
        )

    def replace_proof(_project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        replacement = make_proof(job).model_copy(update={"sha256": "d" * 64})
        job.prepared_proof = replacement

    with pytest.raises(StorageError, match="prepared proof"):
        store.apply_sequence_export_job_update("p1", job_id="job-1", command=replace_proof)


def test_completion_export_record_must_match_job_and_publication_proof(tmp_path):
    for mutate_export, message in [
        (lambda export: setattr(export, "timeline_snapshot", {"clips": []}), "snapshot"),
        (lambda export: setattr(export, "source_entries", []), "source entries"),
        (lambda export: setattr(export, "filename", "wrong.mp4"), "filename"),
        (lambda export: setattr(export, "clip_count", 99), "clip count"),
        (lambda export: setattr(export, "size_mb", 99.0), "size"),
    ]:
        store = AtelierStorage(tmp_path / message.replace(" ", "-"))
        store.create_project(make_project())
        create_job(store)

        def complete_with_bad_export(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
            proof = make_proof(job)
            job.status = "completed"
            job.prepared_proof = proof
            job.export_id = "export-1"
            job.finished_at = 21.0
            export = ExportRecord(
                id="export-1",
                project_id=project.id,
                created_at=21.0,
                clip_count=len(job.source_entries),
                size_mb=proof.size_bytes / 1024 / 1024,
                video_url=proof.relative_path,
                filename=Path(proof.relative_path).name,
                source_entries=list(job.source_entries),
                timeline_snapshot=job.snapshot,
                publication_proof=proof,
                job_id=job.job_id,
                snapshot_digest=job.snapshot_digest,
            )
            mutate_export(export)
            project.exports.append(export)

        with pytest.raises(StorageError, match=message):
            store.apply_sequence_export_completion("p1", job_id="job-1", command=complete_with_bad_export)


def test_export_job_snapshot_digest_must_match_canonical_snapshot(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())

    def create_bad_digest(project: AtelierProject) -> None:
        project.sequence_export_jobs.append(
            make_job(project).model_copy(update={"snapshot_digest": "b" * 64})
        )

    with pytest.raises(StorageError, match="snapshot digest"):
        store.apply_sequence_export_job_creation(
            "p1",
            expected_revision=0,
            client_request_id="req-1",
            request_fingerprint=HASH_A,
            command=create_bad_digest,
        )


def test_export_job_status_transitions_are_monotonic(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)
    store.apply_sequence_export_job_update(
        "p1",
        job_id="job-1",
        command=lambda _project, job: setattr(job, "status", "failed"),
    )

    with pytest.raises(StorageError, match="status transition"):
        store.apply_sequence_export_job_update(
            "p1",
            job_id="job-1",
            command=lambda _project, job: setattr(job, "status", "running"),
        )


def test_completion_uses_latest_project_and_appends_one_export_with_revision_bump(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)
    store.apply_project_command("p1", expected_revision=0, command=lambda project: setattr(project, "title", "Edited while exporting"))

    def complete(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
        proof = make_proof(job)
        job.status = "completed"
        job.prepared_proof = proof
        job.export_id = "export-1"
        job.finished_at = 21.0
        job.updated_at = 21.0
        project.exports.append(
            ExportRecord(
                id="export-1",
                project_id=project.id,
                created_at=21.0,
                clip_count=len(job.source_entries),
                size_mb=proof.size_bytes / 1024 / 1024,
                video_url=proof.relative_path,
                filename=Path(proof.relative_path).name,
                source_entries=list(job.source_entries),
                timeline_snapshot=job.snapshot,
                publication_proof=proof,
                job_id=job.job_id,
                snapshot_digest=job.snapshot_digest,
            )
        )
        project.updated_at = 21.0

    project = store.apply_sequence_export_completion("p1", job_id="job-1", command=complete)

    assert project.title == "Edited while exporting"
    assert project.revision == 2
    assert len(project.exports) == 1
    assert project.exports[0].video_url == "video/atelier_seq_job-1.mp4"
    assert project.sequence_export_jobs[0].status == "completed"
    assert project.sequence_export_jobs[0].job_revision == 1


def test_persisted_export_jobs_are_validated_fail_closed(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)
    store.close()

    base = read_raw(tmp_path)
    job = base["p1"]["sequence_export_jobs"][0]
    proof = make_proof(AtelierSequenceExportJob.model_validate(job))
    cases = [
        (lambda data: data["p1"]["sequence_export_jobs"].append(dict(job)), "Duplicate Atelier export job id"),
        (lambda data: data["p1"]["sequence_export_jobs"].append({**job, "job_id": "job-2", "output_ref": "video/atelier_seq_job-2.mp4"}), "Duplicate Atelier export client request id"),
        (lambda data: data["p1"]["sequence_export_jobs"][0].__setitem__("project_id", "other"), "project_id"),
        (lambda data: data["p1"]["sequence_export_jobs"][0].__setitem__("client_request_id", "bad space"), "Invalid Atelier project"),
        (lambda data: data["p1"]["sequence_export_jobs"][0].__setitem__("status", "completed"), "completed"),
        (lambda data: (data["p1"]["sequence_export_jobs"][0].update({"status": "completed", "finished_at": 20.0, "export_id": "missing-export", "prepared_proof": make_proof(AtelierSequenceExportJob.model_validate(job)).model_dump(mode="json")})), "completed export"),
        (lambda data: data["p1"]["sequence_export_jobs"][0].__setitem__("snapshot_digest", "bad"), "Invalid Atelier project"),
        (lambda data: data["p1"]["sequence_export_jobs"][0].__setitem__("output_ref", "videos/wrong.mp4"), "Invalid Atelier project"),
        (lambda data: data["p1"]["sequence_export_jobs"][0].__setitem__("prepared_proof", {**proof.model_dump(mode="json"), "snapshot_digest": "d" * 64}), "proof snapshot"),
    ]
    for mutate, message in cases:
        payload = json.loads(json.dumps(base))
        mutate(payload)
        db_path(tmp_path).write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(StorageError, match=message):
            AtelierStorage(tmp_path)


def test_uncertain_job_update_latches_store_and_restart_reads_actual_job_state(tmp_path, monkeypatch):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    create_job(store)

    from src.apps.atelier import storage as storage_module

    original = storage_module.atomic_write_json

    def write_then_uncertain(path, value):
        original(path, value)
        raise StorageError("directory fsync failed", commit_uncertain=True)

    monkeypatch.setattr(storage_module, "atomic_write_json", write_then_uncertain)
    with pytest.raises(StorageError) as exc_info:
        store.apply_sequence_export_job_update(
            "p1",
            job_id="job-1",
            command=lambda _project, job: setattr(job, "status", "running"),
        )
    assert exc_info.value.commit_uncertain is True
    with pytest.raises(StorageError, match="restart"):
        store.get_project("p1")

    store.close()
    restarted = AtelierStorage(tmp_path)
    job = restarted.get_project("p1").sequence_export_jobs[0]
    assert job.status == "running"
    assert job.job_revision == 1
