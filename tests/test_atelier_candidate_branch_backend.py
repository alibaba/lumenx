from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.apps.atelier.candidate_branch import (
    AtelierCandidateBranchService,
    BranchCandidateCommand,
    CandidateBranchNotFound,
    CandidateBranchNotSelectable,
)
from src.apps.atelier.candidate_branch_types import validate_branch_source
from src.apps.atelier.generation_api import router
from src.apps.atelier.media_upload import ManagedUpload
from src.apps.atelier.models import (
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierMediaGenerationAttempt,
    AtelierNode,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.models import provider_recovery
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_intents import build_accepted_sync_artifact_record, prepared_tts_intent_payload, sync_artifact_proof_from_record
from src.models.provider_recovery import record_with_changes
from src.models.provider_tasks import ProviderScope
from src.audio.tts import PreparedTTS
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import DirectoryWriterLock, StorageConflictError, StorageError
from src.utils.media_versions import freeze_media
from tests.frozen_image_fixtures import rgb_png_bytes


SCOPE = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing").to_dict()


@contextmanager
def _guard():
    yield


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        pytest.skip("ffmpeg not available")
    return path


def _make_video(path: Path, *, container: str = "mp4") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:d=0.2:r=10",
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-f",
            container,
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()


def _make_audio(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=0.2:sample_rate=44100",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()


def _write_db(root: Path, project: AtelierProject) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "atelier_projects.json").write_text(json.dumps({project.id: project.model_dump(mode="json")}), encoding="utf-8")


def _video_record(*, attempt_id: str, intent: dict, media: list[dict], artifact_path: str, artifact_bytes: bytes) -> ProviderExecutionRecord:
    identity = {
        "attempt_id": attempt_id,
        "provider_model_id": "wan2.7-i2v" if intent["model_id"] == "wan/wan2.7-video#i2v" else "wan3.0-video",
        "intent": intent,
        "media": media,
        "scope": SCOPE,
        "credential_ref": "credential-video",
    }
    handle = {
        "provider_model_id": identity["provider_model_id"],
        "task_id": f"remote-{attempt_id}",
        "scope": SCOPE,
        "credential_ref": "credential-video",
        "request_id": f"request-{attempt_id}",
        "query_capability": "by_task_id",
        "schema_version": 1,
        "submitted_at": 10.0,
    }
    artifact = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "handle": handle,
        "relative_path": artifact_path,
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "size_bytes": len(artifact_bytes),
    }
    return ProviderExecutionRecord.model_validate({
        "schema_version": 1,
        "attempt_id": attempt_id,
        "phase": "completed",
        "provider_model_id": identity["provider_model_id"],
        "intent": intent,
        "media": media,
        "scope": SCOPE,
        "credential_ref": "credential-video",
        "fingerprint": provider_recovery._fingerprint(identity),
        "handle": handle,
        "prepared_artifact": artifact,
        "artifact": artifact,
    })



def _completed_sync_record(*, media_kind: str, intent, output_ref: str, artifact_bytes: bytes, media: list[dict] | None = None) -> ProviderExecutionRecord:
    accepted = build_accepted_sync_artifact_record(
        intent=intent,
        provider_model_id="wan2.7-image-pro" if media_kind == "image" else intent["prepared_tts"]["parameters"]["model"],
        media_kind=media_kind,  # type: ignore[arg-type]
        media=media or [],
        scope=ProviderScope.from_dict(SCOPE),
        credential_ref=f"credential-{media_kind}",
        attempt_id=f"attempt-{media_kind}",
        output_ref=output_ref,
    )
    submitting = record_with_changes(accepted, phase="submitting")
    proof = sync_artifact_proof_from_record(
        submitting,
        relative_path=output_ref,
        sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        size_bytes=len(artifact_bytes),
    )
    return record_with_changes(submitting, phase="completed", prepared_artifact=proof, artifact=proof)


def _image_project(root: Path) -> AtelierProject:
    output = rgb_png_bytes("image-output")
    (root / "assets/atelier").mkdir(parents=True, exist_ok=True)
    (root / "assets/atelier/image.png").write_bytes(output)
    (root / "uploads/reference.png").parent.mkdir(parents=True, exist_ok=True)
    (root / "uploads/reference.png").write_bytes(rgb_png_bytes("image-reference"))
    writer_lock = DirectoryWriterLock(root).acquire()
    try:
        media = [freeze_media(
            "uploads/reference.png",
            output_root=root,
            writer_lock=writer_lock,
            role="reference_image",
            ordinal=0,
            max_bytes=1024 * 1024,
        ).__dict__]
    finally:
        writer_lock.close()
    intent = normalize_generation_intent(
        {
            "model_id": "wan/wan2.7-image-pro#image",
            "mode": "i2i",
            "prompt": "Image prompt",
            "params": {"size": "1280*1280", "seed": 7, "prompt_extend": False, "watermark": False},
        },
        allowed_modes={"t2i", "i2i"},
        allowed_parameters={"size", "seed", "negative_prompt", "prompt_extend", "watermark"},
    )
    record = _completed_sync_record(media_kind="image", intent=intent, output_ref="assets/atelier/image.png", artifact_bytes=output, media=media)
    project = AtelierProject.model_validate({
        "id": "p", "title": "Image", "created_at": 1.0, "updated_at": 2.0, "revision": 1,
        "nodes": [{
            "id": "image", "project_id": "p", "type": "image",
            "data": {"candidates": [{
                "schema_version": 2,
                "execution_contract": "atelier-provider-media-v2",
                "id": "candidate-image",
                "media_kind": "image",
                "status": "completed",
                "media_url": "assets/atelier/image.png",
                "error_code": None,
                "authority": "provider",
                "attempt_id": "attempt-image",
                "execution_fingerprint": record.fingerprint,
            }]},
        }],
    })
    project.generation_attempts.append(AtelierMediaGenerationAttempt.model_validate({
        "schema_version": 1,
        "execution_contract": "atelier-provider-media-v2",
        "project_id": "p",
        "node_id": "image",
        "candidate_id": "candidate-image",
        "attempt_id": "attempt-image",
        "attempt_revision": 0,
        "media_kind": "image",
        "status": "completed",
        "error_code": None,
        "output_ref": "assets/atelier/image.png",
        "provider_execution": record,
        "created_at": 10.0,
        "updated_at": 11.0,
    }))
    project.generation_request_receipts.append(AtelierGenerationRequestReceipt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "entrypoint": "atelier.provider_image.v1",
        "request_contract_version": "request-json-v1",
        "client_request_id": "request-image",
        "request_fingerprint": "c" * 64,
        "node_id": "image",
        "candidate_id": "candidate-image",
        "attempt_id": "attempt-image",
        "execution_fingerprint": record.fingerprint,
        "base_revision": 0,
        "committed_revision": 1,
        "created_at": 10.0,
    }))
    return project


def _audio_project(root: Path) -> AtelierProject:
    output = b"audio-output-bytes"
    (root / "audio/atelier").mkdir(parents=True, exist_ok=True)
    (root / "audio/atelier/audio.mp3").write_bytes(output)
    prepared = PreparedTTS(
        "cosyvoice",
        (("model", "cosyvoice-v3-flash"), ("voice", "longanyang"), ("text", "hello lumen"), ("volume", 0)),
    )
    intent = prepared_tts_intent_payload(prepared, request_fields=("text", "voice", "params.volume"))
    record = _completed_sync_record(media_kind="audio", intent=intent, output_ref="audio/atelier/audio.mp3", artifact_bytes=output)
    project = AtelierProject.model_validate({
        "id": "p", "title": "Audio", "created_at": 1.0, "updated_at": 2.0, "revision": 1,
        "nodes": [{
            "id": "audio", "project_id": "p", "type": "audio",
            "data": {"candidates": [{
                "schema_version": 2,
                "execution_contract": "atelier-provider-media-v2",
                "id": "candidate-audio",
                "media_kind": "audio",
                "status": "completed",
                "media_url": "audio/atelier/audio.mp3",
                "error_code": None,
                "authority": "provider",
                "attempt_id": "attempt-audio",
                "execution_fingerprint": record.fingerprint,
            }]},
        }],
    })
    project.generation_attempts.append(AtelierMediaGenerationAttempt.model_validate({
        "schema_version": 1,
        "execution_contract": "atelier-provider-media-v2",
        "project_id": "p",
        "node_id": "audio",
        "candidate_id": "candidate-audio",
        "attempt_id": "attempt-audio",
        "attempt_revision": 0,
        "media_kind": "audio",
        "status": "completed",
        "error_code": None,
        "output_ref": "audio/atelier/audio.mp3",
        "provider_execution": record,
        "created_at": 10.0,
        "updated_at": 11.0,
    }))
    project.generation_request_receipts.append(AtelierGenerationRequestReceipt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "entrypoint": "atelier.provider_audio.v1",
        "request_contract_version": "request-json-v1",
        "client_request_id": "request-audio",
        "request_fingerprint": "d" * 64,
        "node_id": "audio",
        "candidate_id": "candidate-audio",
        "attempt_id": "attempt-audio",
        "execution_fingerprint": record.fingerprint,
        "base_revision": 0,
        "committed_revision": 1,
        "created_at": 10.0,
    }))
    return project

def _legacy_video_project(root: Path, *, with_input: bool = True) -> AtelierProject:
    output = b"branch output video bytes"
    (root / "video").mkdir(parents=True, exist_ok=True)
    (root / "video/provider-attempt-1.mp4").write_bytes(output)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    (root / "uploads/frame.png").write_bytes(rgb_png_bytes("first-frame"))
    media: list[dict] = []
    if with_input:
        writer_lock = DirectoryWriterLock(root).acquire()
        try:
            media = [freeze_media(
                "uploads/frame.png",
                output_root=root,
                writer_lock=writer_lock,
                role="first_frame",
                ordinal=0,
                max_bytes=1024 * 1024,
            ).__dict__]
        finally:
            writer_lock.close()
    intent = normalize_generation_intent(
        {
            "model_id": "wan/wan2.7-video#i2v",
            "mode": "i2v",
            "prompt": "Historic prompt",
            "params": {"duration": 5, "seed": 0, "prompt_extend": False},
        },
        allowed_modes={"i2v"},
        allowed_parameters={"duration", "resolution", "seed", "prompt_extend", "negative_prompt", "watermark"},
    ).to_dict()
    record = _video_record(attempt_id="attempt-1", intent=intent, media=media, artifact_path="video/provider-attempt-1.mp4", artifact_bytes=output)
    node = AtelierNode.model_validate({
        "id": "video",
        "project_id": "p",
        "type": "video",
        "prompt": "Mutable parent prompt",
        "x": 100,
        "y": 50,
        "data": {
            "generation_draft": {"schema_version": 1, "model_id": "changed", "mode": "i2v", "params": {}},
            "candidates": [{
                "execution_contract": "atelier-provider-video-v1",
                "id": "candidate-1",
                "attempt_id": "attempt-1",
                "media_kind": "video",
                "status": "completed",
                "video_url": "video/provider-attempt-1.mp4",
                "error_code": None,
                "created_at": 10.0,
                "updated_at": 11.0,
            }],
        },
        "created_at": 1.0,
        "updated_at": 2.0,
    })
    project = AtelierProject.model_validate({"id": "p", "title": "Canvas", "nodes": [node], "created_at": 1.0, "updated_at": 2.0, "revision": 1})
    project.generation_attempts.append(AtelierGenerationAttempt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "node_id": "video",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "attempt_revision": 0,
        "status": "completed",
        "error_code": None,
        "first_frame_source": AtelierGenerationFirstFrameSource(node_id="frame", media_url="uploads/frame.png"),
        "output_ref": "video/provider-attempt-1.mp4",
        "provider_execution": record,
        "created_at": 10.0,
        "updated_at": 11.0,
    }))
    project.generation_request_receipts.append(AtelierGenerationRequestReceipt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "entrypoint": "atelier.provider_video.v1",
        "request_contract_version": "request-json-v1",
        "client_request_id": "request-1",
        "request_fingerprint": "b" * 64,
        "node_id": "video",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "execution_fingerprint": record.fingerprint,
        "base_revision": 0,
        "committed_revision": 1,
        "created_at": 10.0,
    }))
    return project


def _service(root: Path, project: AtelierProject, *, ids: list[str] | None = None):
    _write_db(root, project)
    store = AtelierStorage(root)
    iterator = iter(ids or ["draft", "ref-1", "ref-2", "ref-3"])
    copied: list[bytes] = []

    def save(chunks, *, filename: str, content_type: str | None):
        payload = b"".join(chunks)
        copied.append(payload)
        suffix = Path(filename).suffix or ".bin"
        media_kind = "image" if suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else "video"
        return ManagedUpload(
            url=f"uploads/{len(copied)}{suffix}",
            media_kind=media_kind,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            width=240 if media_kind == "image" else 320,
            height=240 if media_kind == "image" else 180,
            duration=None if media_kind == "image" else 1.0,
            poster_url=None,
            poster_size_bytes=None,
            poster_sha256=None,
        )

    service = AtelierCandidateBranchService(store, clock=lambda: 50.0, id_factory=lambda: next(iterator), upload_saver=save)
    return store, service, copied


def test_branch_completed_video_candidate_copies_output_and_frozen_input_without_parent_fallback(tmp_path):
    store, service, copied = _service(tmp_path, _legacy_video_project(tmp_path))
    before = store.get_project("p")

    ack = service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    assert ack.node_ids == ["draft", "ref-1"]
    assert ack.project.revision == 2
    assert copied[0] == b"branch output video bytes"
    draft = next(node for node in ack.project.nodes if node.id == "draft")
    ref = next(node for node in ack.project.nodes if node.id == "ref-1")
    assert draft.type == "video"
    assert draft.status == "draft"
    assert draft.prompt == "Historic prompt"
    assert draft.media_urls == []
    assert draft.data["generation_draft"] == {
        "schema_version": 1,
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "params": {"duration": 5, "resolution": "1080p", "seed": 0, "prompt_extend": False},
    }
    assert draft.data["branch_source"] == {
        "schema_version": 1,
        "origin_project_id": "p",
        "origin_node_id": "video",
        "origin_candidate_id": "candidate-1",
        "media_kind": "video",
        "media_url": "uploads/1.mp4",
        "media_sha256": hashlib.sha256(b"branch output video bytes").hexdigest(),
        "media_size_bytes": len(b"branch output video bytes"),
        "recipe_status": "available",
    }
    assert draft.data["reference_node_ids"] == ["ref-1"]
    assert draft.data["reference_image_urls"] == [ref.media_urls[0]]
    assert ref.type == "image"
    assert ref.status == "completed"
    assert ref.data["parent_node_id"] == "draft"
    assert ack.project.nodes[0] == before.nodes[0]
    assert ack.project.generation_attempts == before.generation_attempts
    assert ack.project.generation_request_receipts == before.generation_request_receipts


def test_branch_missing_frozen_input_creates_source_only_draft_without_generation_draft(tmp_path):
    project = _legacy_video_project(tmp_path)
    frozen_path = next(path for path in (tmp_path / "media_versions").rglob("*") if path.is_file())
    frozen_path.unlink()
    store, service, _ = _service(tmp_path, project)

    ack = service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    assert ack.node_ids == ["draft"]
    draft = next(node for node in ack.project.nodes if node.id == "draft")
    assert draft.prompt == ""
    assert "generation_draft" not in draft.data
    assert draft.data["branch_source"]["recipe_status"] == "unavailable_inputs"
    assert "reference_node_ids" not in draft.data
    assert len(ack.project.nodes) == 2


def test_branch_imported_candidate_is_source_only_with_verified_copy(tmp_path):
    output = b"imported image bytes"
    (tmp_path / "assets/imported/p/atelier").mkdir(parents=True)
    (tmp_path / "assets/imported/p/atelier/imported.png").write_bytes(output)
    project = AtelierProject.model_validate({
        "id": "p",
        "title": "Imported",
        "nodes": [{
            "id": "image",
            "project_id": "p",
            "type": "image",
            "data": {"candidates": [{
                "schema_version": 2,
                "execution_contract": "atelier-provider-media-v2",
                "id": "imported-candidate",
                "media_kind": "image",
                "status": "completed",
                "media_url": "assets/imported/p/atelier/imported.png",
                "error_code": None,
                "authority": "imported",
                "attempt_id": None,
                "execution_fingerprint": None,
            }]},
        }],
        "import_provenance": {
            "schema_version": 1,
            "source_project_id": "source-p",
            "source_storage_id": "storage-a",
            "imported_at": 5.0,
            "agent_history": [],
        },
        "created_at": 1.0,
        "updated_at": 1.0,
        "revision": 1,
    })
    store, service, copied = _service(tmp_path, project, ids=["draft"])

    ack = service.branch_from_candidate("p", "image", "imported-candidate", BranchCandidateCommand(expected_revision=1))

    draft = ack.project.nodes[-1]
    assert copied == [output]
    assert draft.type == "image"
    assert draft.prompt == ""
    assert "generation_draft" not in draft.data
    assert draft.data["branch_source"]["recipe_status"] == "unavailable_imported"
    assert draft.data["branch_source"]["media_url"] == "uploads/1.png"
    assert store.get_project("p").generation_attempts == []


def test_branch_rejects_hidden_noncompleted_and_stale_without_mutation(tmp_path):
    project = _legacy_video_project(tmp_path)
    hidden = {
        "id": "hidden",
        "media_kind": "video",
        "status": "completed",
        "video_url": "video/provider-attempt-1.mp4",
        "created_at": 10.0,
        "updated_at": 11.0,
    }
    failed = {
        "id": "failed",
        "media_kind": "video",
        "status": "failed",
        "video_url": None,
        "created_at": 10.0,
        "updated_at": 11.0,
    }
    project.nodes[0].data["deleted_candidates"] = [hidden]
    project.nodes[0].data["candidates"].append(failed)
    store, service, _ = _service(tmp_path, project)
    before = store.path.read_bytes()

    with pytest.raises(CandidateBranchNotFound):
        service.branch_from_candidate("p", "video", "hidden", BranchCandidateCommand(expected_revision=1))
    with pytest.raises(CandidateBranchNotSelectable):
        service.branch_from_candidate("p", "video", "failed", BranchCandidateCommand(expected_revision=1))
    with pytest.raises(StorageConflictError):
        service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=0))
    assert store.path.read_bytes() == before


def test_branch_rejects_duplicate_hidden_collision_and_malformed_candidate_buckets(tmp_path):
    def legacy_project(root: Path) -> AtelierProject:
        (root / "video").mkdir(parents=True, exist_ok=True)
        (root / "video/legacy.mp4").write_bytes(b"legacy bytes")
        return AtelierProject.model_validate({
            "id": "p",
            "title": "Legacy opaque",
            "nodes": [{
                "id": "video",
                "project_id": "p",
                "type": "video",
                "data": {"candidates": [{
                    "id": "candidate-1",
                    "media_kind": "video",
                    "status": "completed",
                    "video_url": "video/legacy.mp4",
                    "created_at": 10.0,
                    "updated_at": 11.0,
                }]},
            }],
            "created_at": 1.0,
            "updated_at": 1.0,
            "revision": 1,
        })

    duplicate = legacy_project(tmp_path / "duplicate")
    duplicate.nodes[0].data["candidates"].append(dict(duplicate.nodes[0].data["candidates"][0]))
    _, duplicate_service, _ = _service(tmp_path / "duplicate", duplicate)
    with pytest.raises(CandidateBranchNotSelectable):
        duplicate_service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    hidden = legacy_project(tmp_path / "hidden")
    hidden.nodes[0].data["deleted_candidates"] = [dict(hidden.nodes[0].data["candidates"][0])]
    _, hidden_service, _ = _service(tmp_path / "hidden", hidden)
    with pytest.raises(CandidateBranchNotFound):
        hidden_service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    malformed = legacy_project(tmp_path / "malformed")
    malformed.nodes[0].data["candidates"] = {"id": "candidate-1"}
    _, malformed_service, _ = _service(tmp_path / "malformed", malformed)
    with pytest.raises(CandidateBranchNotSelectable):
        malformed_service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))


def test_branch_rejects_malformed_candidate_bucket_members(tmp_path):
    def legacy_project(root: Path) -> AtelierProject:
        (root / "video").mkdir(parents=True, exist_ok=True)
        (root / "video/legacy.mp4").write_bytes(b"legacy bytes")
        return AtelierProject.model_validate({
            "id": "p",
            "title": "Legacy malformed members",
            "nodes": [{
                "id": "video",
                "project_id": "p",
                "type": "video",
                "data": {"candidates": [{
                    "id": "candidate-1",
                    "media_kind": "video",
                    "status": "completed",
                    "video_url": "video/legacy.mp4",
                    "created_at": 10.0,
                    "updated_at": 11.0,
                }]},
            }],
            "created_at": 1.0,
            "updated_at": 1.0,
            "revision": 1,
        })

    cases = [
        ("live-string", lambda project: project.nodes[0].data["candidates"].append("garbage")),
        ("deleted-string", lambda project: project.nodes[0].data.__setitem__("deleted_candidates", ["garbage"])),
        ("deleted-missing-id", lambda project: project.nodes[0].data.__setitem__("deleted_candidates", [{"foo": "bar"}])),
        ("blank-id", lambda project: project.nodes[0].data["candidates"].append({"id": ""})),
        ("whitespace-id", lambda project: project.nodes[0].data["candidates"].append({"id": "  "})),
        ("wrong-id-type", lambda project: project.nodes[0].data["candidates"].append({"id": 123})),
    ]
    for name, mutate in cases:
        root = tmp_path / name
        project = legacy_project(root)
        mutate(project)
        _, service, _ = _service(root, project)
        with pytest.raises(CandidateBranchNotSelectable):
            service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))


def test_branch_commit_failure_discards_prepared_uploads(tmp_path, monkeypatch):
    project = _legacy_video_project(tmp_path)
    discarded: list[str] = []
    store, service, _ = _service(tmp_path, project)
    monkeypatch.setattr(store, "apply_project_command", lambda *args, **kwargs: (_ for _ in ()).throw(StorageError("disk full")))
    monkeypatch.setattr(store, "discard_managed_upload", lambda managed: discarded.append(managed.url))

    with pytest.raises(StorageError):
        service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    assert discarded == ["uploads/1.mp4", "uploads/2.png"]


def test_branch_reference_upload_failure_discards_output_and_prior_refs(tmp_path, monkeypatch):
    project = _legacy_video_project(tmp_path)
    (tmp_path / "uploads/second.png").write_bytes(rgb_png_bytes("second-reference"))
    writer_lock = DirectoryWriterLock(tmp_path).acquire()
    try:
        second = freeze_media(
            "uploads/second.png",
            output_root=tmp_path,
            writer_lock=writer_lock,
            role="last_frame",
            ordinal=0,
            max_bytes=1024 * 1024,
        ).__dict__
    finally:
        writer_lock.close()
    original = project.generation_attempts[0].provider_execution
    media = [*original.media, second]
    record = _video_record(
        attempt_id="attempt-1",
        intent=original.intent,
        media=media,
        artifact_path="video/provider-attempt-1.mp4",
        artifact_bytes=b"branch output video bytes",
    )
    project.generation_attempts[0].provider_execution = record
    project.generation_request_receipts[0].execution_fingerprint = record.fingerprint
    _write_db(tmp_path, project)
    store = AtelierStorage(tmp_path)
    saved: list[ManagedUpload] = []
    discarded: list[str] = []
    ids = iter(["draft", "ref-1", "ref-2"])

    def save(chunks, *, filename: str, content_type: str | None):
        payload = b"".join(chunks)
        suffix = Path(filename).suffix or ".bin"
        if len(saved) == 2:
            raise StorageError("simulated second reference save failure")
        media_kind = "image" if suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else "video"
        managed = ManagedUpload(
            url=f"uploads/{len(saved) + 1}{suffix}",
            media_kind=media_kind,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            width=240 if media_kind == "image" else 320,
            height=240 if media_kind == "image" else 180,
            duration=None if media_kind == "image" else 1.0,
            poster_url=None,
            poster_size_bytes=None,
            poster_sha256=None,
        )
        saved.append(managed)
        return managed

    service = AtelierCandidateBranchService(store, clock=lambda: 50.0, id_factory=lambda: next(ids), upload_saver=save)
    monkeypatch.setattr(store, "discard_managed_upload", lambda managed: discarded.append(managed.url))

    with pytest.raises(StorageError):
        service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    assert set(discarded) == {"uploads/1.mp4", "uploads/2.png"}
    assert store.get_project("p").revision == 1


def test_branch_api_strict_body_and_error_codes(tmp_path):
    project = _legacy_video_project(tmp_path)
    store, service, _ = _service(tmp_path, project)
    app = FastAPI()
    app.state.atelier_candidate_branch = service
    app.include_router(router, prefix="/atelier")
    client = TestClient(app)

    response = client.post("/atelier/projects/p/nodes/video/candidates/candidate-1/branch", json={"expected_revision": 1})
    assert response.status_code == 200
    assert response.json()["node_ids"][0] == "draft"
    assert response.json()["project"]["revision"] == 2

    extra = client.post("/atelier/projects/p/nodes/video/candidates/candidate-1/branch", json={"expected_revision": 2, "x": 1})
    assert extra.status_code == 422
    missing = client.post("/atelier/projects/p/nodes/video/candidates/missing/branch", json={"expected_revision": 2})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "atelier_generation_candidate_not_found"
    store.close()

def test_branch_legacy_completed_candidate_without_attempt_is_source_only_unavailable_legacy(tmp_path):
    output = b"legacy local video bytes"
    (tmp_path / "video").mkdir(parents=True)
    (tmp_path / "video/legacy.mp4").write_bytes(output)
    project = AtelierProject.model_validate({
        "id": "p",
        "title": "Legacy",
        "nodes": [{
            "id": "video",
            "project_id": "p",
            "type": "video",
            "data": {"candidates": [{
                "id": "legacy-candidate",
                "media_kind": "video",
                "status": "completed",
                "video_url": "video/legacy.mp4",
                "created_at": 10.0,
                "updated_at": 11.0,
            }]},
        }],
        "created_at": 1.0,
        "updated_at": 1.0,
        "revision": 1,
    })
    store, service, copied = _service(tmp_path, project, ids=["draft"])

    ack = service.branch_from_candidate("p", "video", "legacy-candidate", BranchCandidateCommand(expected_revision=1))

    draft = ack.project.nodes[-1]
    assert copied == [output]
    assert ack.node_ids == ["draft"]
    assert draft.data["branch_source"]["recipe_status"] == "unavailable_legacy"
    assert "generation_draft" not in draft.data
    assert store.get_project("p").generation_attempts == []


def test_branch_imported_video_from_package_is_source_only_unavailable_imported(tmp_path):
    output = b"imported portable video bytes"
    (tmp_path / "video/imported/p/clip").mkdir(parents=True)
    (tmp_path / "video/imported/p/clip/source.mp4").write_bytes(output)
    project = AtelierProject.model_validate({
        "id": "p",
        "title": "Imported video",
        "nodes": [{
            "id": "video",
            "project_id": "p",
            "type": "video",
            "data": {"candidates": [{
                "id": "imported-video",
                "media_kind": "video",
                "status": "completed",
                "video_url": "video/imported/p/clip/source.mp4",
                "created_at": 10.0,
                "updated_at": 11.0,
            }]},
        }],
        "import_provenance": {
            "schema_version": 1,
            "source_project_id": "source-p",
            "source_storage_id": "storage-a",
            "imported_at": 5.0,
            "agent_history": [],
        },
        "created_at": 1.0,
        "updated_at": 1.0,
        "revision": 1,
    })
    _, service, copied = _service(tmp_path, project, ids=["draft"])

    ack = service.branch_from_candidate("p", "video", "imported-video", BranchCandidateCommand(expected_revision=1))

    draft = ack.project.nodes[-1]
    assert copied == [output]
    assert draft.data["branch_source"]["recipe_status"] == "unavailable_imported"
    assert "generation_draft" not in draft.data


def test_branch_ordered_v2_video_materializes_three_reference_roles(tmp_path):
    project = _legacy_video_project(tmp_path)
    media: list[dict] = []
    sources = [
        ("first_frame", "uploads/first.png", rgb_png_bytes("first")),
        ("last_frame", "uploads/last.png", rgb_png_bytes("last")),
        ("reference_image", "uploads/ref.png", rgb_png_bytes("ref")),
    ]
    for _, rel, payload in sources:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(payload)
    writer_lock = DirectoryWriterLock(tmp_path).acquire()
    try:
        for ordinal, (role, rel, _) in enumerate(sources):
            media.append(freeze_media(
                rel,
                output_root=tmp_path,
                writer_lock=writer_lock,
                role=role,
                ordinal=0 if role != "reference_image" else 0,
                max_bytes=1024 * 1024,
            ).__dict__)
    finally:
        writer_lock.close()
    intent = normalize_generation_intent(
        {
            "model_id": "wan/wan3.0-video#video",
            "mode": "r2v",
            "prompt": "Ordered prompt",
            "params": {"duration": 5, "resolution": "1080P", "seed": 4, "prompt_extend": False, "watermark": False},
        },
        allowed_modes={"t2v", "i2v", "r2v"},
        allowed_parameters={"duration", "resolution", "seed", "prompt_extend", "negative_prompt", "watermark"},
    ).to_dict()
    record = _video_record(attempt_id="attempt-1", intent=intent, media=media, artifact_path="video/provider-attempt-1.mp4", artifact_bytes=b"branch output video bytes")
    project.generation_attempts[0] = AtelierGenerationAttempt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "node_id": "video",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "attempt_revision": 0,
        "status": "completed",
        "error_code": None,
        "input_contract_version": "ordered-media-v1",
        "input_media": [
            {"role": "first_frame", "ordinal": 0, "node_id": "old-first", "media_url": "uploads/first.png"},
            {"role": "last_frame", "ordinal": 0, "node_id": "old-last", "media_url": "uploads/last.png"},
            {"role": "reference_image", "ordinal": 0, "node_id": "old-ref", "media_url": "uploads/ref.png"},
        ],
        "output_ref": "video/provider-attempt-1.mp4",
        "provider_execution": record,
        "created_at": 10.0,
        "updated_at": 11.0,
    })
    project.generation_request_receipts[0] = AtelierGenerationRequestReceipt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "entrypoint": "atelier.provider_video.v2",
        "request_contract_version": "request-json-v1",
        "client_request_id": "request-1",
        "request_fingerprint": "b" * 64,
        "node_id": "video",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "execution_fingerprint": record.fingerprint,
        "base_revision": 0,
        "committed_revision": 1,
        "created_at": 10.0,
    })
    store, service, _ = _service(tmp_path, project, ids=["draft", "ref-1", "ref-2", "ref-3"])

    ack = service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    draft = next(node for node in ack.project.nodes if node.id == "draft")
    assert ack.node_ids == ["draft", "ref-1", "ref-2", "ref-3"]
    assert draft.prompt == "Ordered prompt"
    assert draft.data["generation_draft"]["schema_version"] == 2
    assert draft.data["generation_draft"]["input_contract_version"] == "ordered-media-v1"
    assert [item["role"] for item in draft.data["generation_draft"]["media"]] == ["first_frame", "last_frame", "reference_image"]
    assert draft.data["branch_source"]["recipe_status"] == "available"
    assert len(store.get_project("p").nodes) == 5


def test_branch_image_v3_and_audio_v3_candidates_restore_drafts(tmp_path):
    image_project = _image_project(tmp_path / "image")
    _, image_service, image_copied = _service(tmp_path / "image", image_project, ids=["image-draft", "image-ref"])
    image_ack = image_service.branch_from_candidate("p", "image", "candidate-image", BranchCandidateCommand(expected_revision=1))
    image_draft = next(node for node in image_ack.project.nodes if node.id == "image-draft")
    assert image_draft.data["generation_draft"]["schema_version"] == 3
    assert image_draft.data["generation_draft"]["media_kind"] == "image"
    assert image_draft.data["reference_node_ids"] == ["image-ref"]
    assert image_draft.data["branch_source"]["recipe_status"] == "available"
    assert image_copied[0] == rgb_png_bytes("image-output")

    audio_project = _audio_project(tmp_path / "audio")
    _, audio_service, audio_copied = _service(tmp_path / "audio", audio_project, ids=["audio-draft"])
    audio_ack = audio_service.branch_from_candidate("p", "audio", "candidate-audio", BranchCandidateCommand(expected_revision=1))
    audio_draft = next(node for node in audio_ack.project.nodes if node.id == "audio-draft")
    assert audio_draft.data["generation_draft"] == {
        "schema_version": 3,
        "media_kind": "audio",
        "voice": "longanyang",
        "params": {"volume": 0},
    }
    assert audio_draft.prompt == "hello lumen"
    assert audio_draft.data["branch_source"]["recipe_status"] == "available"
    assert audio_copied == [b"audio-output-bytes"]

def test_branch_ordered_video_audio_references_use_real_managed_upload_classification(tmp_path):
    output = _make_video(tmp_path / "video/out.mp4")
    ref_video = _make_video(tmp_path / "video_inputs/ref.mov", container="mov")
    ref_audio = _make_audio(tmp_path / "audio/ref.m4a")
    writer_lock = DirectoryWriterLock(tmp_path).acquire()
    try:
        media = [
            freeze_media(
                "video_inputs/ref.mov",
                output_root=tmp_path,
                writer_lock=writer_lock,
                role="reference_video",
                ordinal=0,
                max_bytes=1024 * 1024,
            ).__dict__,
            freeze_media(
                "audio/ref.m4a",
                output_root=tmp_path,
                writer_lock=writer_lock,
                role="reference_audio",
                ordinal=0,
                max_bytes=1024 * 1024,
            ).__dict__,
        ]
    finally:
        writer_lock.close()
    intent = normalize_generation_intent(
        {
            "model_id": "wan/wan3.0-video#video",
            "mode": "r2v",
            "prompt": "Video and audio refs",
            "params": {"duration": 5, "resolution": "1080P", "seed": 8, "prompt_extend": False, "watermark": False},
        },
        allowed_modes={"t2v", "i2v", "r2v"},
        allowed_parameters={"duration", "resolution", "seed", "prompt_extend", "negative_prompt", "watermark"},
    ).to_dict()
    record = _video_record(attempt_id="attempt-1", intent=intent, media=media, artifact_path="video/out.mp4", artifact_bytes=output)
    project = AtelierProject.model_validate({
        "id": "p",
        "title": "Ordered AV",
        "nodes": [{
            "id": "video",
            "project_id": "p",
            "type": "video",
            "data": {"candidates": [{
                "execution_contract": "atelier-provider-video-v1",
                "id": "candidate-1",
                "attempt_id": "attempt-1",
                "media_kind": "video",
                "status": "completed",
                "video_url": "video/out.mp4",
                "error_code": None,
                "created_at": 10.0,
                "updated_at": 11.0,
            }]},
        }],
        "created_at": 1.0,
        "updated_at": 1.0,
        "revision": 1,
    })
    project.generation_attempts.append(AtelierGenerationAttempt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "node_id": "video",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "attempt_revision": 0,
        "status": "completed",
        "error_code": None,
        "input_contract_version": "ordered-media-v1",
        "input_media": [
            {"role": "reference_video", "ordinal": 0, "node_id": "old-video", "media_url": "video_inputs/ref.mov"},
            {"role": "reference_audio", "ordinal": 0, "node_id": "old-audio", "media_url": "audio/ref.m4a"},
        ],
        "output_ref": "video/out.mp4",
        "provider_execution": record,
        "created_at": 10.0,
        "updated_at": 11.0,
    }))
    project.generation_request_receipts.append(AtelierGenerationRequestReceipt.model_validate({
        "schema_version": 1,
        "project_id": "p",
        "entrypoint": "atelier.provider_video.v2",
        "request_contract_version": "request-json-v1",
        "client_request_id": "request-1",
        "request_fingerprint": "e" * 64,
        "node_id": "video",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "execution_fingerprint": record.fingerprint,
        "base_revision": 0,
        "committed_revision": 1,
        "created_at": 10.0,
    }))
    _write_db(tmp_path, project)
    store = AtelierStorage(tmp_path)
    service = AtelierCandidateBranchService(store, clock=lambda: 50.0, id_factory=iter(["draft", "ref-video", "ref-audio"]).__next__)

    ack = service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    draft = next(node for node in ack.project.nodes if node.id == "draft")
    ref_by_id = {node.id: node for node in ack.project.nodes}
    assert ack.node_ids == ["draft", "ref-video", "ref-audio"]
    assert ref_by_id["ref-video"].media_urls[0].endswith(".mov")
    assert ref_by_id["ref-audio"].media_urls[0].endswith(".m4a")
    assert draft.data["reference_video_urls"] == ref_by_id["ref-video"].media_urls
    assert draft.data["reference_audio_urls"] == ref_by_id["ref-audio"].media_urls
    assert draft.data["generation_draft"]["media"] == [
        {"role": "reference_video", "ordinal": 0, "node_id": "ref-video", "media_url": ref_by_id["ref-video"].media_urls[0]},
        {"role": "reference_audio", "ordinal": 0, "node_id": "ref-audio", "media_url": ref_by_id["ref-audio"].media_urls[0]},
    ]
    assert output and ref_video and ref_audio


def test_branch_corrupt_frozen_input_cleans_materialized_refs_and_keeps_source_only(tmp_path, monkeypatch):
    project = _legacy_video_project(tmp_path)
    frozen_path = next(path for path in (tmp_path / "media_versions").rglob("*") if path.is_file())
    frozen_path.write_bytes(b"corrupt")
    discarded: list[str] = []
    store, service, _ = _service(tmp_path, project)
    monkeypatch.setattr(store, "discard_managed_upload", lambda managed: discarded.append(managed.url))

    ack = service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    draft = next(node for node in ack.project.nodes if node.id == "draft")
    assert ack.node_ids == ["draft"]
    assert draft.data["branch_source"]["recipe_status"] == "unavailable_inputs"
    assert "generation_draft" not in draft.data
    assert discarded == []


def test_branch_commit_uncertain_preserves_prepared_uploads(tmp_path, monkeypatch):
    project = _legacy_video_project(tmp_path)
    discarded: list[str] = []
    store, service, _ = _service(tmp_path, project)
    monkeypatch.setattr(store, "apply_project_command", lambda *args, **kwargs: (_ for _ in ()).throw(StorageError("maybe committed", commit_uncertain=True)))
    monkeypatch.setattr(store, "discard_managed_upload", lambda managed: discarded.append(managed.url))

    with pytest.raises(StorageError):
        service.branch_from_candidate("p", "video", "candidate-1", BranchCandidateCommand(expected_revision=1))

    assert discarded == []


def test_branch_source_validator_is_strict_and_package_rewrite_ready():
    valid = {
        "schema_version": 1,
        "origin_project_id": "p",
        "origin_node_id": "n",
        "origin_candidate_id": "c",
        "media_kind": "video",
        "media_url": "uploads/imported/new/imported/old/source.mp4",
        "media_sha256": "a" * 64,
        "media_size_bytes": 12,
        "recipe_status": "available",
    }

    assert validate_branch_source(valid).media_url == valid["media_url"]
    for invalid in (
        {key: value for key, value in valid.items() if key != "schema_version"},
        {**valid, "schema_version": True},
        {**valid, "extra": "nope"},
        {**valid, "origin_node_id": " n"},
        {**valid, "media_url": "uploads/../source.mp4"},
    ):
        with pytest.raises(ValueError):
            validate_branch_source(invalid)
