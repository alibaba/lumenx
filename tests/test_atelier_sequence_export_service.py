from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
from threading import Event, Thread
import os
import shutil
import subprocess
import time

import pytest

from src.apps.atelier.export_jobs import CreateSequenceExportJob, RedoSequenceExportJob
from src.apps.atelier.models import AtelierAudioTrack, AtelierNode, AtelierProject, ExportRecord
from src.apps.atelier.sequence import SequenceValidationError
from src.apps.atelier.sequence_export_service import SequenceExportError, SequenceExportService, SequenceExportWorkerRegistry
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


def _tool(name: str) -> str:
    env_name = "LUMENX_TEST_FFMPEG" if name == "ffmpeg" else "LUMENX_TEST_FFPROBE"
    path = os.environ.get(env_name) or shutil.which(name)
    if not path:
        pytest.skip(f"{name} not available")
    return path


def _make_clip(path: Path, *, color: str = "red", duration: float = 0.7, frequency: int = 440) -> bytes:
    ffmpeg = _tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x48:d={duration}:r=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=44100",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()


def _make_audio(path: Path, *, duration: float = 0.7, frequency: int = 660) -> bytes:
    ffmpeg = _tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=44100",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()


def _project(project_id: str = "atelier-1", ref: str = "uploads/clip.mp4") -> AtelierProject:
    return AtelierProject(
        id=project_id,
        title="Sequence",
        created_at=1,
        updated_at=1,
        nodes=[
            AtelierNode(
                id="video-1",
                project_id=project_id,
                type="video",
                status="completed",
                media_urls=[ref],
            )
        ],
    )


def _store_with_project(tmp_path: Path) -> AtelierStorage:
    store = AtelierStorage(tmp_path)
    _make_clip(tmp_path / "uploads" / "clip.mp4")
    project = _project()
    project.sequence = []
    store.create_project(project)
    return store


def _command(revision: int = 0, request_id: str = "req-1") -> CreateSequenceExportJob:
    return CreateSequenceExportJob(
        expected_revision=revision,
        client_request_id=request_id,
        entries=[{"id": "clip-1", "parentId": "video-1", "trimStart": 0, "trimEnd": 0.4}],
    )


def test_create_replay_by_request_does_not_dispatch_or_require_current_revision(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)

    first = service.create_sequence_export_job("atelier-1", _command())
    store.apply_project_command(
        "atelier-1",
        expected_revision=0,
        command=lambda project: setattr(project, "title", "edited while ack was lost"),
    )
    replay = service.create_sequence_export_job("atelier-1", _command())

    assert first.replayed is False
    assert first.new_job_ids == ["job-1"]
    assert replay.replayed is True
    assert replay.new_job_ids == []
    assert replay.job.job_id == "job-1"
    assert replay.project.revision == 1
    assert len(replay.project.sequence_export_jobs) == 1


def test_real_ffmpeg_run_completes_against_latest_project_and_publishes_proof(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", export_id_factory=lambda: "export-1", clock=lambda: 10.0)

    created = service.create_sequence_export_job("atelier-1", _command())
    assert created.job.status == "queued"
    store.apply_project_command(
        "atelier-1",
        expected_revision=0,
        command=lambda project: setattr(project, "title", "latest title"),
    )

    ack = service.run_sequence_export_job("atelier-1", "job-1")

    assert ack.project.title == "latest title"
    assert ack.project.revision == 2
    assert ack.job.status == "completed"
    assert ack.export_record is not None
    assert ack.export_record.id == "export-1"
    assert ack.export_record.job_id == "job-1"
    assert ack.export_record.video_url == "video/atelier_seq_job-1.mp4"
    assert ack.export_record.publication_proof == ack.job.prepared_proof
    assert (tmp_path / ack.export_record.video_url).exists()


def test_redo_export_reuses_frozen_history_snapshot_not_current_sequence(tmp_path):
    store = _store_with_project(tmp_path)
    job_ids = iter(["job-1", "job-2"])
    export_ids = iter(["export-1", "export-2"])
    service = SequenceExportService(
        store,
        id_factory=lambda: next(job_ids),
        export_id_factory=lambda: next(export_ids),
        clock=lambda: 10.0,
    )
    first = service.create_sequence_export_job("atelier-1", _command())
    completed = service.run_sequence_export_job("atelier-1", first.job.job_id)
    source = completed.export_record
    assert source is not None

    _make_clip(tmp_path / "uploads" / "replacement.mp4", color="blue")
    edited = store.apply_project_command(
        "atelier-1",
        expected_revision=completed.project.revision,
        command=lambda project: (
            setattr(project, "sequence", []),
            setattr(project.nodes[0], "media_urls", ["uploads/replacement.mp4"]),
        ),
    )
    redo = service.redo_sequence_export_job(
        "atelier-1",
        source.id,
        RedoSequenceExportJob(expected_revision=edited.revision, client_request_id="redo-1"),
    )

    assert redo.replayed is False
    assert redo.new_job_ids == ["job-2"]
    assert redo.job.request_contract_version == "atelier-sequence-export-redo-v1"
    assert redo.job.source_export_id == source.id
    assert redo.job.snapshot == source.timeline_snapshot
    assert redo.job.source_entries == completed.job.source_entries
    assert redo.job.source_audio_tracks == completed.job.source_audio_tracks

    rerun = service.run_sequence_export_job("atelier-1", "job-2")
    assert rerun.export_record is not None
    assert rerun.export_record.id == "export-2"
    assert rerun.export_record.video_url == "video/atelier_seq_job-2.mp4"
    assert [record.id for record in rerun.project.exports] == ["export-1", "export-2"]


def test_redo_request_id_cannot_cross_operation_or_source_export(tmp_path):
    store = _store_with_project(tmp_path)
    job_ids = iter(["job-1", "job-2"])
    service = SequenceExportService(store, id_factory=lambda: next(job_ids), export_id_factory=lambda: "export-1", clock=lambda: 10.0)
    first = service.create_sequence_export_job("atelier-1", _command(request_id="same"))
    completed = service.run_sequence_export_job("atelier-1", first.job.job_id)
    source = completed.export_record
    assert source is not None

    with pytest.raises(StorageConflictError):
        service.redo_sequence_export_job(
            "atelier-1",
            source.id,
            RedoSequenceExportJob(expected_revision=completed.project.revision, client_request_id="same"),
        )

    store.apply_project_command(
        "atelier-1",
        expected_revision=completed.project.revision,
        command=lambda project: project.exports.append(
            ExportRecord(
                id="export-b",
                project_id=project.id,
                clip_count=source.clip_count,
                size_mb=source.size_mb,
                video_url=source.video_url,
                filename=source.filename,
                source_entries=list(source.source_entries),
                timeline_snapshot=source.timeline_snapshot,
            )
        ),
    )
    redo = service.redo_sequence_export_job(
        "atelier-1",
        source.id,
        RedoSequenceExportJob(expected_revision=completed.project.revision + 1, client_request_id="redo-same"),
    )
    assert redo.replayed is False
    with pytest.raises(StorageConflictError):
        service.redo_sequence_export_job(
            "atelier-1",
            "export-b",
            RedoSequenceExportJob(expected_revision=completed.project.revision + 1, client_request_id="redo-same"),
        )


def test_redo_jobless_native_snapshot_maps_audio_metadata_without_current_defaults(tmp_path):
    store = _store_with_project(tmp_path)
    _make_audio(tmp_path / "uploads" / "tone.wav")
    with_audio = store.apply_project_command(
        "atelier-1",
        expected_revision=0,
        command=lambda project: (
            project.nodes.append(
                AtelierNode(
                    id="audio-1",
                    project_id=project.id,
                    type="audio",
                    status="completed",
                    media_urls=["uploads/tone.wav"],
                )
            ),
            setattr(
                project,
                "audio_tracks",
                [
                    AtelierAudioTrack(
                        id="music",
                        node_id="audio-1",
                        start=0.1,
                        trim_start=0.0,
                        trim_end=0.5,
                        gain=0.4,
                        loop=True,
                    )
                ],
            ),
        ),
    )
    service = SequenceExportService(store, id_factory=lambda: "job-1", export_id_factory=lambda: "export-1", clock=lambda: 10.0)
    first = service.create_sequence_export_job("atelier-1", _command(revision=with_audio.revision))
    completed = service.run_sequence_export_job("atelier-1", first.job.job_id)
    source = completed.export_record
    assert source is not None
    jobless = store.apply_project_command(
        "atelier-1",
        expected_revision=completed.project.revision,
        command=lambda project: (
            project.exports.append(
                ExportRecord(
                    id="jobless-native",
                    project_id=project.id,
                    clip_count=source.clip_count,
                    size_mb=source.size_mb,
                    video_url=source.video_url,
                    filename=source.filename,
                    source_entries=[],
                    timeline_snapshot=source.timeline_snapshot,
                )
            ),
            setattr(project, "audio_tracks", []),
        ),
    )
    redo_service = SequenceExportService(store, id_factory=lambda: "job-2", clock=lambda: 20.0)

    redo = redo_service.redo_sequence_export_job(
        "atelier-1",
        "jobless-native",
        RedoSequenceExportJob(expected_revision=jobless.revision, client_request_id="redo-jobless"),
    )

    assert redo.job.source_entries == completed.job.source_entries
    assert redo.job.source_audio_tracks == completed.job.source_audio_tracks
    assert redo.job.snapshot == source.timeline_snapshot


def test_jobless_redo_rejects_duplicate_snapshot_identity_without_appending_job(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    accepted = service.create_sequence_export_job(
        "atelier-1",
        CreateSequenceExportJob(
            expected_revision=0, client_request_id="source",
            entries=[{"id": "a", "parentId": "video-1"}, {"id": "b", "parentId": "video-1"}],
        ),
    )
    snapshot = accepted.job.snapshot
    snapshot["clips"][1]["clip_id"] = snapshot["clips"][0]["clip_id"]
    project = store.apply_project_command(
        "atelier-1", expected_revision=0,
        command=lambda item: item.exports.append(ExportRecord(
            id="jobless", project_id=item.id, clip_count=2,
            video_url="video/old.mp4", filename="old.mp4", timeline_snapshot=snapshot,
        )),
    )
    redo = SequenceExportService(store, id_factory=lambda: "job-2", clock=lambda: 20.0)
    with pytest.raises(SequenceValidationError) as invalid:
        redo.redo_sequence_export_job(
            project.id, "jobless",
            RedoSequenceExportJob(expected_revision=project.revision, client_request_id="redo-invalid"),
        )
    assert any(error["code"] == "duplicate_id" for error in invalid.value.report["errors"])
    assert [job.job_id for job in store.get_project(project.id).sequence_export_jobs] == ["job-1"]


@pytest.mark.parametrize("timeline_snapshot", [None, {"version": 1, "clips": [{"url": "uploads/clip.mp4"}]}])
def test_redo_rejects_missing_or_legacy_snapshot_without_creating_a_job(tmp_path, timeline_snapshot):
    store = _store_with_project(tmp_path)
    store.apply_project_command(
        "atelier-1",
        expected_revision=0,
        command=lambda project: project.exports.append(
            ExportRecord(
                id="legacy-export",
                project_id=project.id,
                clip_count=1,
                size_mb=0.1,
                video_url="video/legacy.mp4",
                filename="legacy.mp4",
                source_entries=[],
                timeline_snapshot=timeline_snapshot,
            )
        ),
    )
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)

    with pytest.raises((SequenceExportError, SequenceValidationError)):
        service.redo_sequence_export_job(
            "atelier-1",
            "legacy-export",
            RedoSequenceExportJob(expected_revision=1, client_request_id="redo-legacy"),
        )
    assert store.get_project("atelier-1").sequence_export_jobs == []


def test_redo_rechecks_source_export_inside_storage_commit(tmp_path, monkeypatch):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", export_id_factory=lambda: "export-1", clock=lambda: 10.0)
    first = service.create_sequence_export_job("atelier-1", _command())
    completed = service.run_sequence_export_job("atelier-1", first.job.job_id)
    source = completed.export_record
    assert source is not None
    original_apply = store.apply_sequence_export_job_creation

    def racing_apply(*args, command, **kwargs):
        def wrapped(project: AtelierProject):
            project.exports[0].filename = "changed-before-append.mp4"
            command(project)

        return original_apply(*args, command=wrapped, **kwargs)

    monkeypatch.setattr(store, "apply_sequence_export_job_creation", racing_apply)
    with pytest.raises(StorageConflictError):
        service.redo_sequence_export_job(
            "atelier-1",
            source.id,
            RedoSequenceExportJob(expected_revision=completed.project.revision, client_request_id="redo-race"),
        )
    assert len(store.get_project("atelier-1").sequence_export_jobs) == 1


def test_publication_uncertain_after_prepared_proof_requires_repair_without_export(tmp_path, monkeypatch):
    from src.apps.atelier import sequence_export_service as service_module

    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())

    real_publish = service_module.publish_sequence_render

    def uncertain_publish(*args, **kwargs):
        real_publish(*args, **kwargs)
        raise StorageError("directory fsync uncertain", commit_uncertain=True)

    monkeypatch.setattr(service_module, "publish_sequence_render", uncertain_publish)
    ack = service.run_sequence_export_job("atelier-1", "job-1")

    assert ack.job.status == "repair_required"
    assert ack.job.prepared_proof is not None
    assert ack.export_record is None
    assert ack.project.exports == []
    assert (tmp_path / ack.job.output_ref).exists()


def test_repair_matching_prepared_file_completes_without_rerender(tmp_path, monkeypatch):
    from src.apps.atelier import sequence_export_service as service_module

    store = _store_with_project(tmp_path)
    service = SequenceExportService(
        store,
        id_factory=lambda: "job-1",
        export_id_factory=lambda: "export-1",
        clock=lambda: 10.0,
    )
    service.create_sequence_export_job("atelier-1", _command())
    real_publish = service_module.publish_sequence_render

    def uncertain_publish(*args, **kwargs):
        real_publish(*args, **kwargs)
        raise StorageError("directory fsync uncertain", commit_uncertain=True)

    monkeypatch.setattr(service_module, "publish_sequence_render", uncertain_publish)
    repairable = service.run_sequence_export_job("atelier-1", "job-1")
    assert repairable.job.status == "repair_required"

    def forbidden_render(*_args, **_kwargs):
        raise AssertionError("repair must not rerender")

    service._render_context = forbidden_render
    repaired = service.repair_sequence_export_job("atelier-1", "job-1")

    assert repaired.job.status == "completed"
    assert repaired.export_record is not None
    assert repaired.export_record.publication_proof == repairable.job.prepared_proof
    assert len(repaired.project.exports) == 1


def test_cancel_running_uses_owned_token_and_worker_records_cancelled(tmp_path, monkeypatch):
    store = _store_with_project(tmp_path)
    registry = SequenceExportWorkerRegistry()
    service = SequenceExportService(store, id_factory=lambda: "job-1", registry=registry, clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())
    entered = Event()
    release = Event()

    @contextmanager
    def blocking_render(_snapshot, *, media_scope, cancel_token):
        entered.set()
        release.wait(timeout=5)
        cancel_token.assert_not_cancelled()
        raise AssertionError("cancel should stop before yield")
        yield  # pragma: no cover

    service._render_context = blocking_render
    errors: list[BaseException] = []

    def run_worker():
        try:
            service.run_sequence_export_job("atelier-1", "job-1")
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    worker = Thread(target=run_worker)
    worker.start()
    assert entered.wait(timeout=5)
    cancelling = service.cancel_sequence_export_job("atelier-1", "job-1")
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert not errors
    assert cancelling.job.status == "cancelling"
    final = service.get_sequence_export_job("atelier-1", "job-1")
    assert final.job.status == "cancelled"
    assert registry.cancel("atelier-1", "job-1") is False


def test_duplicate_run_while_live_worker_does_not_mark_orphan_failed(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())
    entered = Event()
    release = Event()

    @contextmanager
    def blocking_render(_snapshot, *, media_scope, cancel_token):
        entered.set()
        release.wait(timeout=5)
        cancel_token.assert_not_cancelled()
        raise AssertionError("test stops worker by cancelling")
        yield  # pragma: no cover

    service._render_context = blocking_render
    errors: list[BaseException] = []

    def run_worker():
        try:
            service.run_sequence_export_job("atelier-1", "job-1")
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    worker = Thread(target=run_worker)
    worker.start()
    assert entered.wait(timeout=5)

    duplicate = service.run_sequence_export_job("atelier-1", "job-1")

    assert duplicate.job.status == "running"
    assert duplicate.job.error_code is None
    service.cancel_sequence_export_job("atelier-1", "job-1")
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert not errors


def test_cancel_persists_cancelling_before_token_cancel_race(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())
    entered = Event()

    @contextmanager
    def cancel_race_render(_snapshot, *, media_scope, cancel_token):
        original_cancel = cancel_token.cancel

        def racing_cancel():
            original_cancel()
            deadline = time.time() + 5
            while time.time() < deadline:
                if store.get_project("atelier-1").sequence_export_jobs[0].status == "cancelled":
                    return
                time.sleep(0.01)
            raise AssertionError("worker did not observe cancellation")

        cancel_token.cancel = racing_cancel
        entered.set()
        while True:
            cancel_token.assert_not_cancelled()
            time.sleep(0.01)
        yield  # pragma: no cover

    service._render_context = cancel_race_render
    errors: list[BaseException] = []
    worker = Thread(target=lambda: errors.append(service.run_sequence_export_job("atelier-1", "job-1")))
    worker.start()
    assert entered.wait(timeout=5)

    cancelling = service.cancel_sequence_export_job("atelier-1", "job-1")
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert cancelling.job.status == "cancelling"
    final = service.get_sequence_export_job("atelier-1", "job-1")
    assert final.job.status == "cancelled"


def test_cancel_after_prepared_proof_becomes_repair_required(tmp_path, monkeypatch):
    from src.apps.atelier import sequence_export_service as service_module
    from src.apps.atelier.sequence_render import SequencePublicationProof, SequenceRenderCancelled

    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())

    @contextmanager
    def fake_render(_snapshot, *, media_scope, cancel_token):
        yield SimpleNamespace()

    def prepared_then_cancelled(_result, *, output_ref, media_scope, on_prepared=None, **_kwargs):
        job = store.get_project("atelier-1").sequence_export_jobs[0]
        proof = SequencePublicationProof(
            schema_version=1,
            relative_path=output_ref,
            filename=Path(output_ref).name,
            sha256="d" * 64,
            size_bytes=1234,
            duration=0.4,
            width=64,
            height=48,
            fps=30,
            snapshot_sha256=job.snapshot_digest,
        )
        assert on_prepared is not None
        on_prepared(proof)
        raise SequenceRenderCancelled("cancelled after proof")

    service._render_context = fake_render
    monkeypatch.setattr(service_module, "publish_sequence_render", prepared_then_cancelled)

    ack = service.run_sequence_export_job("atelier-1", "job-1")

    assert ack.job.status == "repair_required"
    assert ack.job.prepared_proof is not None
    assert ack.project.exports == []


@pytest.mark.parametrize("orphan_status", ["running", "cancelling"])
def test_repair_orphaned_running_without_live_token_or_proof_marks_failed(tmp_path, orphan_status):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())
    store.apply_sequence_export_job_update(
        "atelier-1",
        job_id="job-1",
        command=lambda _project, job: setattr(job, "status", orphan_status),
    )

    def forbidden_render(*_args, **_kwargs):
        raise AssertionError("repair of orphaned no-proof jobs must not render")

    service._render_context = forbidden_render
    ack = service.repair_sequence_export_job("atelier-1", "job-1")

    assert ack.job.status == "failed"
    assert ack.job.error_code == "orphaned_running_export"
    assert ack.export_record is None
    assert ack.project.exports == []


def test_repair_while_live_worker_does_not_inspect_unpublished_path_or_fail(tmp_path):
    from src.apps.atelier.sequence_render import SequenceRenderCancelToken

    store = _store_with_project(tmp_path)
    registry = SequenceExportWorkerRegistry()
    service = SequenceExportService(store, id_factory=lambda: "job-1", registry=registry, clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())
    store.apply_sequence_export_job_update(
        "atelier-1",
        job_id="job-1",
        command=lambda _project, job: setattr(job, "status", "running"),
    )
    token = SequenceRenderCancelToken()
    registry.register("atelier-1", "job-1", token)
    try:
        ack = service.repair_sequence_export_job("atelier-1", "job-1")
    finally:
        registry.unregister("atelier-1", "job-1", token)

    assert ack.job.status == "running"
    assert ack.job.error_code is None
    assert ack.project.exports == []


def test_orphaned_running_without_live_token_is_not_rerendered(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())
    store.apply_sequence_export_job_update(
        "atelier-1",
        job_id="job-1",
        command=lambda _project, job: setattr(job, "status", "running"),
    )

    def forbidden_render(*_args, **_kwargs):
        raise AssertionError("orphaned running jobs must not rerender automatically")

    service._render_context = forbidden_render
    ack = service.run_sequence_export_job("atelier-1", "job-1")

    assert ack.job.status == "failed"
    assert ack.job.error_code == "orphaned_running_export"
    assert ack.project.exports == []


def test_registry_request_stop_cancels_live_workers_and_blocks_new_reservations(tmp_path):
    from src.apps.atelier.sequence_render import SequenceRenderCancelToken

    registry = SequenceExportWorkerRegistry()
    token = SequenceRenderCancelToken()
    registry.register("atelier-1", "job-1", token)

    assert registry.request_stop() == 1
    with pytest.raises(Exception, match="cancelled"):
        token.assert_not_cancelled()
    registry.unregister("atelier-1", "job-1", token)
    assert registry.join(timeout=0.1) is True
    with pytest.raises(StorageConflictError, match="stopping"):
        registry.register("atelier-1", "job-2", SequenceRenderCancelToken())


def test_service_request_stop_blocks_future_run_claims(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())

    assert service.request_stop() == 0
    assert service.join(timeout=0.1) is True
    with pytest.raises(StorageConflictError, match="stopping"):
        service.run_sequence_export_job("atelier-1", "job-1")


def test_closed_storage_prevents_late_worker_completion(tmp_path):
    store = _store_with_project(tmp_path)
    service = SequenceExportService(store, id_factory=lambda: "job-1", clock=lambda: 10.0)
    service.create_sequence_export_job("atelier-1", _command())
    store.close()

    with pytest.raises(StorageConflictError, match="closed"):
        service.run_sequence_export_job("atelier-1", "job-1")


def test_redo_rejects_tombstoned_source_before_creating_another_job(tmp_path):
    from src.apps.atelier.export_history import DeleteExportRecord, ExportHistoryService

    store = _store_with_project(tmp_path)
    ids = iter(["job-original", "job-forbidden"])
    service = SequenceExportService(store, id_factory=lambda: next(ids), export_id_factory=lambda: "export-original")
    created = service.create_sequence_export_job("atelier-1", _command())
    done = service.run_sequence_export_job("atelier-1", created.job.job_id)
    deleted = ExportHistoryService(store).delete_export(
        "atelier-1", done.export_record.id,
        DeleteExportRecord(expected_revision=done.project.revision, client_request_id="hide-original", delete_file=False),
    )

    with pytest.raises(SequenceExportError, match="deleted"):
        service.redo_sequence_export_job(
            "atelier-1", done.export_record.id,
            RedoSequenceExportJob(expected_revision=deleted.project.revision, client_request_id="redo-forbidden"),
        )
    assert len(store.get_project("atelier-1").sequence_export_jobs) == 1


def test_admitted_redo_replay_and_execution_survive_source_tombstone(tmp_path):
    from src.apps.atelier.export_history import DeleteExportRecord, ExportHistoryService

    store = _store_with_project(tmp_path)
    ids = iter(["job-original", "job-admitted"])
    exports = iter(["export-original", "export-admitted"])
    service = SequenceExportService(store, id_factory=lambda: next(ids), export_id_factory=lambda: next(exports))
    created = service.create_sequence_export_job("atelier-1", _command())
    done = service.run_sequence_export_job("atelier-1", created.job.job_id)
    request = RedoSequenceExportJob(expected_revision=done.project.revision, client_request_id="redo-admitted")
    admitted = service.redo_sequence_export_job("atelier-1", done.export_record.id, request)
    ExportHistoryService(store).delete_export(
        "atelier-1", done.export_record.id,
        DeleteExportRecord(expected_revision=admitted.project.revision, client_request_id="hide-original", delete_file=False),
    )

    replay = service.redo_sequence_export_job("atelier-1", done.export_record.id, request)
    assert replay.replayed is True
    assert replay.job.job_id == admitted.job.job_id
    assert replay.new_job_ids == []
    completed = service.run_sequence_export_job("atelier-1", admitted.job.job_id)
    assert completed.job.status == "completed"
    assert completed.export_record.id == "export-admitted"
    assert len(completed.project.sequence_export_jobs) == 2
    assert completed.project.exports[0].deletion is not None
