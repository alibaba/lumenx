from __future__ import annotations

from dataclasses import asdict
import io
import json
import zipfile
from pathlib import Path

import pytest

from src.apps.atelier.generation_selection import AtelierGenerationSelectionService, SetVideoCandidateVisibilityCommand
from src.apps.atelier.models import (
    AtelierAgentTurn,
    AtelierAudioTrack,
    AtelierCandidateJudgment,
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierNode,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequencePublicationProof,
    ExportRecord,
)
from src.apps.atelier.storage import AtelierStorage
from src.models import provider_recovery
from src.models.provider_execution import ProviderExecutionRecord
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import DirectoryWriterLock
from src.utils.media_versions import MediaVersion

HASH = "a" * 64


def _write(root: Path, ref: str, data: bytes) -> None:
    path = root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _source_project() -> AtelierProject:
    project = AtelierProject(
        id="source-project",
        title="Source canvas",
        description="Creative state",
        created_at=10.0,
        updated_at=11.0,
        nodes=[
            AtelierNode(
                id="region-old",
                project_id="source-project",
                type="region",
                title="Region",
                data={"color": "#646cff"},
                created_at=10.0,
                updated_at=11.0,
            ),
            AtelierNode(
                id="image-old",
                project_id="source-project",
                type="image",
                title="Reference",
                media_urls=["uploads/ref.png"],
                data={"parent_node_id": "video-old", "reference_role": "video_reference_image", "region_id": "region-old"},
                created_at=10.0,
                updated_at=11.0,
            ),
            AtelierNode(
                id="audio-old",
                project_id="source-project",
                type="audio",
                title="Music",
                media_urls=["audio/music.mp3"],
                data={"parent_node_id": "video-old", "reference_role": "video_reference_audio"},
                created_at=10.0,
                updated_at=11.0,
            ),
            AtelierNode(
                id="video-old",
                project_id="source-project",
                type="video",
                title="Shot",
                prompt="A neon alley inspired by https://example.test/mood",
                media_urls=["video/take.mp4"],
                data={
                    "reference_node_ids": ["image-old", "audio-old"],
                    "reference_image_urls": ["uploads/ref.png"],
                    "reference_audio_urls": ["audio/music.mp3"],
                    "region_id": "region-old",
                    "selected_candidate_id": "candidate-old",
                    "creative_notes": "Keep literal source URL https://example.test/mood and source id video-old as prose.",
                    "candidates": [
                        {
                            "id": "candidate-old",
                            "status": "completed",
                            "video_url": "video/take.mp4",
                            "prompt": "A neon alley take",
                            "attempt_id": "attempt-authority-to-strip",
                            "updated_at": 12.0,
                        }
                    ],
                },
                created_at=10.0,
                updated_at=11.0,
            ),
        ],
        sequence=[AtelierSequenceEntry(parentId="video-old", candidateId="candidate-old", id="clip-old")],
        audio_tracks=[AtelierAudioTrack(id="track-old", node_id="audio-old", start=0.25)],
        exports=[
            ExportRecord(
                id="export-old",
                project_id="source-project",
                created_at=15.0,
                clip_count=1,
                size_mb=1.0,
                video_url="video/atelier_seq_job-old.mp4",
                filename="atelier_seq_job-old.mp4",
                source_entries=[AtelierSequenceEntry(parentId="video-old", candidateId="candidate-old", id="clip-old")],
                timeline_snapshot={
                    "schema_version": "atelier.timeline.v1",
                    "clips": [
                        {
                            "clip_id": "clip-old",
                            "parentId": "video-old",
                            "candidateId": "candidate-old",
                            "source_url": "video/take.mp4",
                            "media_version": {"relative_path": "video/take.mp4", "sha256": "c" * 64},
                            "label": "literal ids video-old/candidate-old stay prose",
                        }
                    ],
                    "audio_tracks": [
                        {
                            "id": "track-old",
                            "node_id": "audio-old",
                            "source_url": "audio/music.mp3",
                            "media_version": {"relative_path": "audio/music.mp3", "sha256": "d" * 64},
                        }
                    ],
                },
                publication_proof=AtelierSequencePublicationProof(
                    job_id="job-old",
                    project_id="source-project",
                    relative_path="video/atelier_seq_job-old.mp4",
                    sha256=HASH,
                    size_bytes=4,
                    duration=1.0,
                    width=1920,
                    height=1080,
                    fps=24,
                    snapshot_digest="b" * 64,
                    prepared_at=16.0,
                ),
                job_id="job-old",
                snapshot_digest="b" * 64,
            )
        ],
        agent_turns=[
            AtelierAgentTurn(
                id="turn-old",
                project_id="source-project",
                user_message="Make this more cinematic",
                status="completed",
                response="I prepared the selected take.",
                created_at=12.0,
                completed_at=13.0,
                messages_history=[
                    {"role": "user", "content": "Use https://example.test/mood as a visual reference"},
                    {"role": "assistant", "content": "I will use video-old as context, not authority."},
                    {
                        "role": "tool",
                        "name": "canvas.readProject",
                        "content": {
                            "summary": "Read node video-old",
                            "node_id": "video-old",
                            "status": "completed",
                            "public_error_code": "none",
                        },
                    },
                ],
            )
        ],
    )
    project.agent_policy.updated_at = 10.0
    return project


def _write_source_media(root: Path) -> None:
    _write(root, "uploads/ref.png", b"ref")
    _write(root, "audio/music.mp3", b"music")
    _write(root, "video/take.mp4", b"take")
    _write(root, "video/atelier_seq_job-old.mp4", b"done")


def _generated_source_project() -> AtelierProject:
    project = _source_project()
    candidate = project.nodes[-1].data["candidates"][0]
    candidate.clear()
    candidate.update(
        {
            "execution_contract": "atelier-provider-video-v1",
            "id": "candidate-old",
            "attempt_id": "attempt-old",
            "media_kind": "video",
            "status": "completed",
            "video_url": "video/take.mp4",
            "error_code": None,
            "created_at": 10.0,
            "updated_at": 12.0,
        }
    )
    project.generation_attempts.append(_generation_attempt(project_id=project.id))
    project.generation_request_receipts.append(_generation_receipt(project_id=project.id))
    project.revision = 1
    return project


def _generation_attempt(*, project_id: str) -> AtelierGenerationAttempt:
    output_ref = "video/take.mp4"
    return AtelierGenerationAttempt(
        project_id=project_id,
        node_id="video-old",
        candidate_id="candidate-old",
        attempt_id="attempt-old",
        attempt_revision=0,
        status="completed",
        error_code=None,
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image-old", media_url="uploads/ref.png"),
        output_ref=output_ref,
        provider_execution=_provider_record(attempt_id="attempt-old", artifact_path=output_ref),
        created_at=10.0,
        updated_at=12.0,
    )


def _generation_receipt(*, project_id: str) -> AtelierGenerationRequestReceipt:
    return AtelierGenerationRequestReceipt(
        project_id=project_id,
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="request-old",
        request_fingerprint="b" * 64,
        node_id="video-old",
        candidate_id="candidate-old",
        attempt_id="attempt-old",
        execution_fingerprint=_provider_record(attempt_id="attempt-old").fingerprint,
        base_revision=0,
        committed_revision=1,
        created_at=10.0,
    )


def _provider_record(*, attempt_id: str, artifact_path: str | None = None) -> ProviderExecutionRecord:
    intent = normalize_generation_intent(
        {
            "model_id": "wan2.7-i2v",
            "mode": "i2v",
            "prompt": "A neon alley take",
            "params": {
                "duration": 5,
                "resolution": "720p",
                "seed": 0,
                "prompt_extend": False,
                "negative_prompt": "",
                "watermark": False,
            },
        },
        allowed_modes={"i2v"},
        allowed_parameters={"duration", "resolution", "seed", "prompt_extend", "negative_prompt", "watermark"},
    )
    media = [asdict(MediaVersion("d" * 64, 100, "first_frame", 0, "image/png"))]
    scope = {
        "provider": "dashscope",
        "backend": "dashscope",
        "gateway": "dashscope_workspace",
        "base_url": "https://dashscope.aliyuncs.com",
        "region": "cn-beijing",
        "workspace_id": None,
        "account_identity": None,
    }
    handle = {
        "provider_model_id": "wan2.7-i2v",
        "task_id": "remote-task-old",
        "scope": scope,
        "credential_ref": "credential-ref",
        "request_id": "remote-request-old",
        "query_capability": "by_task_id",
        "schema_version": 1,
        "submitted_at": 10.0,
    }
    payload = {
        "attempt_id": attempt_id,
        "provider_model_id": "wan2.7-i2v",
        "intent": intent.to_dict(),
        "media": media,
        "scope": scope,
        "credential_ref": "credential-ref",
    }
    data = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "phase": "accepted",
        "provider_model_id": "wan2.7-i2v",
        "intent": intent.to_dict(),
        "media": media,
        "scope": scope,
        "credential_ref": "credential-ref",
        "fingerprint": provider_recovery._fingerprint(payload),
    }
    if artifact_path is not None:
        artifact = {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "handle": handle,
            "relative_path": artifact_path,
            "sha256": "c" * 64,
            "size_bytes": 4,
        }
        data.update({"phase": "completed", "handle": handle, "artifact": artifact})
    return ProviderExecutionRecord(**data)


def _mutate_package_json(package_data: bytes, entry_name: str, mutate) -> bytes:
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package_data)) as src, zipfile.ZipFile(rebuilt, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == entry_name:
                payload = json.loads(data.decode("utf-8"))
                mutate(payload)
                data = json.dumps(payload).encode("utf-8")
            dst.writestr(info, data)
    return rebuilt.getvalue()


def test_atelier_package_roundtrip_rewrites_ids_media_exports_and_sanitized_history(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _source_project()
    original = source.model_dump(mode="json")

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-store")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        now_fn=lambda: 99.0,
        id_factory=lambda kind: f"new-{kind}",
    )

    imported = prepared.project
    assert source.model_dump(mode="json") == original
    assert imported.id == "new-project"
    assert imported.source_project_id is None
    assert imported.revision == 0
    assert imported.agent_turns == []
    assert imported.generation_attempts == []
    assert imported.generation_request_receipts == []
    assert imported.node_upload_receipts == []
    assert imported.node_delete_receipts == []
    assert imported.sequence_export_jobs == []
    assert imported.import_provenance is not None
    assert imported.import_provenance.agent_history[0].user_message == "Make this more cinematic"
    history = imported.import_provenance.agent_history[0]
    assert history.assistant_response == "I prepared the selected take."
    assert [message.model_dump(mode="json") for message in history.messages] == [
        {"role": "user", "content": "Use https://example.test/mood as a visual reference"},
        {"role": "assistant", "content": "I will use video-old as context, not authority."},
        {
            "role": "tool",
            "name": "canvas.readProject",
            "content": "Read node video-old (status: completed; error: none)",
            "status": "completed",
            "error_code": "none",
        },
    ]

    ids = {node.id for node in imported.nodes}
    assert ids == {"new-node", "new-node-1", "new-node-2", "new-node-3"}
    by_title = {node.title: node for node in imported.nodes}
    video = by_title["Shot"]
    image = by_title["Reference"]
    audio = by_title["Music"]
    region = by_title["Region"]
    assert all(node.project_id == imported.id for node in imported.nodes)
    assert video.data["reference_node_ids"] == [image.id, audio.id]
    assert video.prompt == "A neon alley inspired by https://example.test/mood"
    assert video.data["creative_notes"] == "Keep literal source URL https://example.test/mood and source id video-old as prose."
    assert video.data["region_id"] == region.id
    assert image.data["parent_node_id"] == video.id
    assert imported.sequence[0].parentId == video.id
    assert imported.audio_tracks[0].node_id == audio.id
    candidate = video.data["candidates"][0]
    assert candidate["id"] == "new-candidate"
    assert "attempt_id" not in candidate
    assert video.data["selected_candidate_id"] == candidate["id"]
    assert candidate["video_url"].startswith(f"video/imported/{imported.id}/")
    assert (tmp_path / candidate["video_url"]).read_bytes() == b"take"

    export = imported.exports[0]
    assert export.id == "new-export"
    assert export.project_id == imported.id
    assert export.job_id is None
    assert export.snapshot_digest is None
    assert export.publication_proof is None
    assert export.deletion is None
    assert export.imported_proof is not None
    assert export.imported_proof.project_id == imported.id
    assert export.imported_proof.export_id == export.id
    assert export.imported_proof.relative_path == export.video_url
    assert not export.imported_proof.relative_path.startswith("video/atelier_seq_job-old")

    clip = export.timeline_snapshot["clips"][0]
    assert clip["clip_id"] == imported.sequence[0].id
    assert clip["parentId"] == video.id
    assert clip["candidateId"] == candidate["id"]
    assert clip["source_url"] == candidate["video_url"]
    assert clip["media_version"]["relative_path"] == candidate["video_url"]
    assert clip["label"] == "literal ids video-old/candidate-old stay prose"
    audio_snapshot = export.timeline_snapshot["audio_tracks"][0]
    assert audio_snapshot["id"] == imported.audio_tracks[0].id
    assert audio_snapshot["node_id"] == audio.id
    assert audio_snapshot["source_url"] == audio.media_urls[0]
    assert audio_snapshot["media_version"]["relative_path"] == audio.media_urls[0]

    store = AtelierStorage(tmp_path / "store")
    try:
        saved = store.create_project(imported)
        assert saved.id == imported.id
        assert saved.exports[0].imported_proof == export.imported_proof
    finally:
        store.close()

def test_atelier_package_strips_product_skill_authority_from_history(tmp_path):
    from src.apps.atelier.agent_loop_commands import loop_start_context
    from src.apps.atelier.agent_loop_commands import StartAgentLoopCommand
    from src.apps.atelier.product_skills import resolve_product_skill_snapshot
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _source_project()
    snapshot = resolve_product_skill_snapshot("story-scout")
    source.agent_turns[0].skill_name = "story-scout"
    source.agent_turns[0].loop_start_context = loop_start_context(
        StartAgentLoopCommand(
            expected_revision=source.revision,
            client_turn_id="turn-old",
            user_message=source.agent_turns[0].user_message,
            planner="model_adapter",
            skill_name="story-scout",
            max_iterations=1,
        ),
        resolved_skill_snapshot=snapshot,
    )

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-store")
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as zf:
        manifest_text = zf.read("manifest.json").decode("utf-8")
    assert "story-scout" not in manifest_text
    assert "prompt_template" not in manifest_text
    assert "expected_tools" not in manifest_text

    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        now_fn=lambda: 99.0,
        id_factory=lambda kind: f"new-{kind}",
    )
    assert prepared.project.agent_turns == []
    history = prepared.project.import_provenance.agent_history[0]
    assert not hasattr(history, "skill_name")


def test_atelier_package_import_turns_generated_candidate_into_inert_media_copy(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _generated_source_project()

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-store")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"imported-{kind}",
    )

    imported = prepared.project
    video = next(node for node in imported.nodes if node.title == "Shot")
    candidate = video.data["candidates"][0]
    assert candidate["id"] == "imported-candidate"
    assert candidate["status"] == "completed"
    assert candidate["video_url"].startswith(f"video/imported/{imported.id}/")
    assert (tmp_path / candidate["video_url"]).read_bytes() == b"take"
    for authority_key in (
        "execution_contract",
        "attempt_id",
        "attempt_revision",
        "provider_execution",
        "request_fingerprint",
        "request_id",
        "execution_fingerprint",
    ):
        assert authority_key not in candidate
    assert imported.generation_attempts == []
    assert imported.generation_request_receipts == []

    store = AtelierStorage(tmp_path / "store")
    try:
        saved = store.create_project(imported)
        saved_candidate = next(node for node in saved.nodes if node.title == "Shot").data["candidates"][0]
        assert saved_candidate == candidate
    finally:
        store.close()


def test_atelier_package_carries_only_retained_candidate_judgment_chains(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _source_project()
    video = next(node for node in source.nodes if node.id == "video-old")
    video.data["candidates"].append({"id": "draft-candidate", "status": "processing", "video_url": ""})
    source.candidate_judgments = [
        AtelierCandidateJudgment(
            id="judgment-old-1",
            project_id=source.id,
            node_id="video-old",
            candidate_id="candidate-old",
            media_kind="video",
            verdict="keep",
            reason="Keep A",
            created_at=12.0,
        ),
        AtelierCandidateJudgment(
            id="judgment-old-2",
            project_id=source.id,
            node_id="video-old",
            candidate_id="candidate-old",
            media_kind="video",
            verdict="reject",
            reason="B is weaker",
            created_at=13.0,
            supersedes_judgment_id="judgment-old-1",
        ),
        AtelierCandidateJudgment(
            id="judgment-filtered",
            project_id=source.id,
            node_id="video-old",
            candidate_id="draft-candidate",
            media_kind="video",
            verdict="reject",
            reason="Incomplete attempt should not travel",
            created_at=14.0,
        ),
        AtelierCandidateJudgment(
            id="judgment-orphan",
            project_id=source.id,
            node_id="deleted-node",
            candidate_id="deleted-candidate",
            media_kind="video",
            verdict="keep",
            reason="Deleted-node history stays behind",
            created_at=15.0,
        ),
    ]
    original = source.model_dump(mode="json")

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-store")
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as zf:
        project_payload = json.loads(zf.read("project.json").decode("utf-8"))
    assert [item["id"] for item in project_payload["candidate_judgments"]] == ["judgment-old-1", "judgment-old-2"]

    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        now_fn=lambda: 99.0,
        id_factory=lambda kind: f"judged-{kind}",
    )

    assert source.model_dump(mode="json") == original
    assert prepared.id_map["candidate_judgments"] == {
        "judgment-old-1": "judged-judgment",
        "judgment-old-2": "judged-judgment-1",
    }
    imported_video = next(node for node in prepared.project.nodes if node.title == "Shot")
    imported = [item.model_dump(mode="json") for item in prepared.project.candidate_judgments]
    assert imported == [
        {
            "schema_version": 1,
            "id": "judged-judgment",
            "project_id": prepared.project.id,
            "node_id": imported_video.id,
            "candidate_id": imported_video.data["candidates"][0]["id"],
            "media_kind": "video",
            "verdict": "keep",
            "reason": "Keep A",
            "created_at": 12.0,
        },
        {
            "schema_version": 1,
            "id": "judged-judgment-1",
            "project_id": prepared.project.id,
            "node_id": imported_video.id,
            "candidate_id": imported_video.data["candidates"][0]["id"],
            "media_kind": "video",
            "verdict": "reject",
            "reason": "B is weaker",
            "created_at": 13.0,
            "supersedes_judgment_id": "judged-judgment",
        },
    ]
    serialized = json.dumps(prepared.project.model_dump(mode="json"), sort_keys=True)
    assert "attempt-old" not in serialized
    assert "provider_request_id" not in serialized


def test_atelier_package_old_payload_without_candidate_judgments_imports_as_empty_history(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    legacy = _mutate_package_json(artifact.data, "project.json", lambda project_payload: project_payload.pop("candidate_judgments", None))

    prepared = prepare_import(
        legacy,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"legacy-{kind}",
    )

    assert prepared.project.candidate_judgments == []
    assert prepared.id_map["candidate_judgments"] == {}


def test_atelier_package_accepts_valid_unicode_candidate_judgment_reason(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _source_project()
    source.candidate_judgments = [
        AtelierCandidateJudgment(
            id="judgment-unicode",
            project_id=source.id,
            node_id="video-old",
            candidate_id="candidate-old",
            media_kind="video",
            verdict="keep",
            reason="光" * 2000,
            created_at=12.0,
        )
    ]

    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"unicode-{kind}",
    )

    assert prepared.project.candidate_judgments[0].reason == "光" * 2000


@pytest.mark.parametrize(
    "mutate",
    [
        lambda project_payload: project_payload.update({"candidate_judgments": None}),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-forged",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
                "provider_request_id": "forged-provider",
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-kind-list",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": ["video"],
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": ["judgment-list-id"],
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-list-node",
                "project_id": project_payload["id"],
                "node_id": ["video-old"],
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-list-candidate",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": ["candidate-old"],
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).extend(
            [
                {
                    "schema_version": 1,
                    "id": "judgment-1",
                    "project_id": project_payload["id"],
                    "node_id": "video-old",
                    "candidate_id": "candidate-old",
                    "media_kind": "video",
                    "verdict": "keep",
                    "reason": "",
                    "created_at": 12.0,
                },
                {
                    "schema_version": 1,
                    "id": "judgment-2",
                    "project_id": project_payload["id"],
                    "node_id": "video-old",
                    "candidate_id": "candidate-old",
                    "media_kind": "video",
                    "verdict": "reject",
                    "reason": "",
                    "created_at": 13.0,
                    "supersedes_judgment_id": ["judgment-1"],
                },
            ]
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-verdict-dict",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": {"value": "keep"},
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-surrogate-reason",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "\ud800",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-huge-created",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "",
                "created_at": 10**400,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-long-reason",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "a" * 2001,
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).append(
            {
                "schema_version": 1,
                "id": "judgment-wrong-kind",
                "project_id": project_payload["id"],
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "media_kind": "audio",
                "verdict": "keep",
                "reason": "",
                "created_at": 12.0,
            }
        ),
        lambda project_payload: project_payload.setdefault("candidate_judgments", []).extend(
            [
                {
                    "schema_version": 1,
                    "id": "judgment-2",
                    "project_id": project_payload["id"],
                    "node_id": "video-old",
                    "candidate_id": "candidate-old",
                    "media_kind": "video",
                    "verdict": "reject",
                    "reason": "",
                    "created_at": 13.0,
                    "supersedes_judgment_id": "judgment-1",
                },
                {
                    "schema_version": 1,
                    "id": "judgment-1",
                    "project_id": project_payload["id"],
                    "node_id": "video-old",
                    "candidate_id": "candidate-old",
                    "media_kind": "video",
                    "verdict": "keep",
                    "reason": "",
                    "created_at": 12.0,
                },
            ]
        ),
    ],
)
def test_atelier_package_rejects_invalid_candidate_judgments_before_staging(tmp_path, mutate):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    damaged = _mutate_package_json(artifact.data, "project.json", mutate)

    with pytest.raises(AtelierProjectPackageError, match="candidate judgment|authority"):
        prepare_import(
            damaged,
            data_dir=tmp_path,
            target_storage_id="target-store",
            id_factory=lambda kind: f"bad-judgment-{kind}",
        )

    assert not (tmp_path / "video/imported/bad-judgment-project/take.mp4").exists()


def test_atelier_package_import_preserves_hidden_completed_video_candidate_as_inert_copy(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _generated_source_project()
    video = next(node for node in source.nodes if node.id == "video-old")
    candidate = video.data["candidates"].pop()
    video.media_urls = []
    video.data.pop("selected_candidate_id", None)
    source.sequence = []
    source.exports = []
    video.data["deleted_candidates"] = [candidate]

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-store")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"imported-{kind}",
    )

    imported = prepared.project
    imported_video = next(node for node in imported.nodes if node.title == "Shot")
    assert imported_video.data.get("candidates", []) == []
    hidden = imported_video.data["deleted_candidates"][0]
    assert hidden["id"] == "imported-candidate"
    assert hidden["status"] == "completed"
    assert hidden["video_url"].startswith(f"video/imported/{imported.id}/")
    assert (tmp_path / hidden["video_url"]).read_bytes() == b"take"
    for authority_key in (
        "execution_contract",
        "attempt_id",
        "attempt_revision",
        "provider_execution",
        "request_fingerprint",
        "request_id",
        "execution_fingerprint",
    ):
        assert authority_key not in hidden
    assert imported.generation_attempts == []
    assert imported.generation_request_receipts == []

    store = AtelierStorage(tmp_path / "store")
    try:
        saved = store.create_project(imported)
        restored = AtelierGenerationSelectionService(store).set_video_candidate_visibility(
            saved.id,
            imported_video.id,
            hidden["id"],
            SetVideoCandidateVisibilityCommand(expected_revision=0, visible=True),
        )
        assert restored.project.nodes[-1].data["candidates"] == [hidden]
        assert restored.project.nodes[-1].data["deleted_candidates"] == []
        assert restored.project.nodes[-1].media_urls == []
    finally:
        store.close()


def test_atelier_package_hidden_video_candidate_survives_reexport_as_new_inert_copy(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _generated_source_project()
    video = next(node for node in source.nodes if node.id == "video-old")
    candidate = video.data["candidates"].pop()
    video.media_urls = []
    video.data.pop("selected_candidate_id", None)
    source.sequence = []
    source.exports = []
    video.data["deleted_candidates"] = [candidate]

    first = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"first-{kind}",
    )
    second = prepare_import(
        build_package(first.project, data_dir=tmp_path, source_storage_id="target-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store-2",
        id_factory=lambda kind: f"second-{kind}",
    )

    hidden = next(node for node in second.project.nodes if node.title == "Shot").data["deleted_candidates"][0]
    assert hidden["id"] == "second-candidate"
    assert hidden["video_url"].startswith(f"video/imported/{second.project.id}/")
    assert hidden["video_url"] != next(node for node in first.project.nodes if node.title == "Shot").data["deleted_candidates"][0]["video_url"]
    assert (tmp_path / hidden["video_url"]).read_bytes() == b"take"
    assert "execution_contract" not in hidden
    assert "attempt_id" not in hidden


def test_repeated_atelier_import_allocates_independent_identities_and_media(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")

    first = prepare_import(artifact.data, data_dir=tmp_path, target_storage_id="target-store", id_factory=lambda kind: f"a-{kind}")
    second = prepare_import(artifact.data, data_dir=tmp_path, target_storage_id="target-store", id_factory=lambda kind: f"b-{kind}")

    assert first.project.id == "a-project"
    assert second.project.id == "b-project"
    assert {node.id for node in first.project.nodes}.isdisjoint({node.id for node in second.project.nodes})
    assert first.project.exports[0].video_url != second.project.exports[0].video_url
    assert (tmp_path / first.project.exports[0].video_url).read_bytes() == b"done"
    assert (tmp_path / second.project.exports[0].video_url).read_bytes() == b"done"


def test_atelier_package_copy_of_import_preserves_readonly_history_without_all_node_refs(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    first = prepare_import(
        build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"first-{kind}",
    )

    second = prepare_import(
        build_package(first.project, data_dir=tmp_path, source_storage_id="target-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store-2",
        id_factory=lambda kind: f"second-{kind}",
    )

    assert first.project.import_provenance is not None
    assert second.project.import_provenance is not None
    first_history = first.project.import_provenance.agent_history[0]
    second_history = second.project.import_provenance.agent_history[0]
    assert second_history.user_message == first_history.user_message
    assert [message.model_dump(mode="json") for message in second_history.messages] == [message.model_dump(mode="json") for message in first_history.messages]
    assert set(second_history.source_refs) == {"video-old"}
    assert set(second_history.target_refs) == {"video-old"}
    assert second_history.target_refs["video-old"] != first_history.target_refs["video-old"]


def test_atelier_package_sanitizes_raw_loop_tool_messages_without_authority_or_prose_ref_guessing(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _source_project()
    source.agent_turns[0].messages_history = [
        {"role": "user", "content": "The prose mentions video-old and https://example.test/mood but should not create refs."},
        {
            "role": "tool",
            "name": "generation.createVideoCandidates",
            "content": {
                "status": "completed",
                "summary": "Generated one candidate",
                "node_id": "video-old",
                "candidate_id": "candidate-old",
                "client_request_id": "raw-request-id",
                "attempt_id": "raw-attempt-id",
                "credential_ref": "raw-credential",
                "provider_execution": {"handle": "raw-provider-handle"},
                "public_error_code": "provider_rate_limited",
            },
        },
    ]

    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"safe-{kind}",
    )

    assert prepared.project.import_provenance is not None
    history = prepared.project.import_provenance.agent_history[0]
    serialized = json.dumps(history.model_dump(mode="json"), sort_keys=True)
    assert "raw-request-id" not in serialized
    assert "raw-attempt-id" not in serialized
    assert "raw-credential" not in serialized
    assert "raw-provider-handle" not in serialized
    assert "client_request_id" not in serialized
    assert "attempt_id" not in serialized
    assert "credential_ref" not in serialized
    assert "provider_execution" not in serialized
    assert [message.model_dump(mode="json") for message in history.messages] == [
        {"role": "user", "content": "The prose mentions video-old and https://example.test/mood but should not create refs."},
        {
            "role": "tool",
            "name": "generation.createVideoCandidates",
            "content": "Generated one candidate (status: completed; error: provider_rate_limited)",
            "status": "completed",
            "error_code": "provider_rate_limited",
        },
    ]
    assert set(history.source_refs) == {"video-old", "video-old:candidate-old"}
    assert history.target_refs["video-old"] == prepared.id_map["nodes"]["video-old"]
    assert history.target_refs["video-old:candidate-old"] == prepared.id_map["candidates"]["video-old:candidate-old"]


def test_atelier_package_strips_model_authority_but_rejects_hidden_authority(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    source = _source_project()
    source.generation_attempts = []
    source.generation_request_receipts = []
    source.node_upload_receipts = []
    source.node_delete_receipts = []
    source.sequence_export_jobs = []
    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"clean-{kind}",
    )
    assert prepared.project.agent_turns == []
    assert prepared.project.sequence_export_jobs == []

    damaged = _source_project()
    damaged.nodes[-1].data["provider_request_id"] = "hidden-authority"
    with pytest.raises(AtelierProjectPackageError, match="authority"):
        build_package(damaged, data_dir=tmp_path, source_storage_id="source-store")


def test_atelier_package_rejects_remote_missing_and_bad_graph_refs(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package

    _write_source_media(tmp_path)
    remote = _source_project()
    remote.nodes[-1].data["reference_image_urls"] = ["https://example.test/ref.png"]
    with pytest.raises(AtelierProjectPackageError, match="Remote media"):
        build_package(remote, data_dir=tmp_path, source_storage_id="source-store")

    missing = _source_project()
    missing.nodes[-1].media_urls.append("video/missing.mp4")
    with pytest.raises(AtelierProjectPackageError, match="unavailable|unsafe"):
        build_package(missing, data_dir=tmp_path, source_storage_id="source-store")

    broken = _source_project()
    broken.sequence[0].candidateId = "missing-candidate"
    with pytest.raises(AtelierProjectPackageError, match="candidate"):
        build_package(broken, data_dir=tmp_path, source_storage_id="source-store")


def test_atelier_package_remaps_historical_timeline_ids_not_in_live_sequence(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    source = _source_project()
    source.sequence = []
    source.audio_tracks = []
    source.exports[0].source_entries = []
    source.exports[0].timeline_snapshot = {
        "schema_version": "atelier.timeline.v1",
        "fps": 30,
        "width": 320,
        "height": 180,
        "duration": 3.0,
        "clips": [
            {
                "clip_id": "historical-clip",
                "parentId": "video-old",
                "candidateId": "candidate-old",
                "source_url": "video/take.mp4",
                "thumbnail_url": "uploads/ref.png",
                "poster_url": "uploads/ref.png",
                "video_url": "video/take.mp4",
                "media_version": {
                    "sha256": "e" * 64,
                    "size_bytes": 4,
                    "role": "reference_video",
                    "schema_version": 1,
                },
            }
        ],
        "audio_tracks": [
            {
                "id": "historical-audio",
                "node_id": "audio-old",
                "source_url": "audio/music.mp3",
                "media_version": {"sha256": "d" * 64, "size_bytes": 5, "schema_version": 1},
            }
        ],
    }

    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"timeline-{kind}",
    )

    timeline = prepared.project.exports[0].timeline_snapshot
    clip = timeline["clips"][0]
    assert clip["clip_id"] == "timeline-clip"
    assert clip["parentId"] == prepared.id_map["nodes"]["video-old"]
    assert clip["candidateId"] == prepared.id_map["candidates"]["video-old:candidate-old"]
    assert clip["source_url"] == prepared.media["video/take.mp4"]
    assert clip["thumbnail_url"] == prepared.media["uploads/ref.png"]
    assert clip["poster_url"] == prepared.media["uploads/ref.png"]
    assert clip["video_url"] == prepared.media["video/take.mp4"]
    assert clip["media_version"]["sha256"] == "e" * 64
    track = timeline["audio_tracks"][0]
    assert track["id"] == "timeline-audio-track"
    assert track["node_id"] == prepared.id_map["nodes"]["audio-old"]
    assert track["source_url"] == prepared.media["audio/music.mp3"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda project_payload: project_payload["exports"][0]["timeline_snapshot"]["clips"][0].update({"parentId": "missing-node"}),
        lambda project_payload: project_payload["exports"][0]["timeline_snapshot"]["clips"][0].update({"candidateId": "missing-candidate"}),
        lambda project_payload: project_payload["exports"][0]["timeline_snapshot"]["audio_tracks"][0].update({"node_id": "missing-node"}),
    ],
)
def test_atelier_package_rejects_malformed_timeline_before_staging(tmp_path, mutate):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    damaged = _mutate_package_json(artifact.data, "project.json", mutate)

    with pytest.raises(AtelierProjectPackageError, match="timeline"):
        prepare_import(
            damaged,
            data_dir=tmp_path,
            target_storage_id="target-store",
            id_factory=lambda kind: f"bad-timeline-{kind}",
        )

    assert not (tmp_path / "video/imported/bad-timeline-project/take.mp4").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["agent_history"][0]["messages"][0].update({"request_id": "forged-request"}),
        lambda manifest: manifest["agent_history"][0]["messages"][0].update({"authorization_hash": "a" * 64}),
        lambda manifest: manifest["agent_history"][0].update({"source_refs": {"request_id": "request_id"}}),
        lambda manifest: manifest["agent_history"][0].update({"target_refs": {"video-old": "credential_ref"}}),
    ],
)
def test_atelier_package_rejects_manifest_agent_history_authority_before_staging(tmp_path, mutate):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    damaged = _mutate_package_json(artifact.data, "manifest.json", mutate)

    with pytest.raises(AtelierProjectPackageError, match="agent history|authority"):
        prepare_import(
            damaged,
            data_dir=tmp_path,
            target_storage_id="target-store",
            id_factory=lambda kind: f"bad-history-{kind}",
        )

    assert not (tmp_path / "video/imported/bad-history-project/take.mp4").exists()


def test_atelier_import_stage_failure_cleans_only_owned_files(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    conflict = tmp_path / "video/imported/fail-project/take.mp4"
    conflict.parent.mkdir(parents=True, exist_ok=True)
    conflict.write_bytes(b"existing")

    with pytest.raises(AtelierProjectPackageError):
        prepare_import(
            artifact.data,
            data_dir=tmp_path,
            target_storage_id="target-store",
            id_factory=lambda kind: f"fail-{kind}",
        )

    assert conflict.read_bytes() == b"existing"
    imported_root = tmp_path / "uploads/imported/fail-project"
    assert not any(imported_root.rglob("*") if imported_root.exists() else [])


def test_discard_prepared_import_removes_only_target_owned_media(tmp_path):
    from src.apps.atelier.project_package import build_package, discard_prepared_import, prepare_import

    _write_source_media(tmp_path)
    prepared = prepare_import(
        build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"discard-{kind}",
    )
    unrelated = tmp_path / "video/imported/other-project/keep.mp4"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"keep")

    with DirectoryWriterLock(tmp_path).acquire() as lease:
        discard_prepared_import(prepared, data_dir=tmp_path, writer_lock=lease)

    assert unrelated.read_bytes() == b"keep"
    assert not (tmp_path / prepared.project.exports[0].video_url).exists()


def test_new_atelier_package_model_fields_omit_absent_legacy_wire():
    project_payload = AtelierProject(id="legacy", title="Legacy", created_at=1.0, updated_at=1.0).model_dump(mode="json")
    export_payload = ExportRecord(
        id="export",
        project_id="legacy",
        video_url="video/atelier_seq_job.mp4",
        filename="atelier_seq_job.mp4",
    ).model_dump(mode="json")

    assert "import_provenance" not in project_payload
    assert "imported_proof" not in export_payload


def test_atelier_package_rewrites_pending_reference_source_ids_without_media_authority(tmp_path):
    from src.apps.atelier.project_package import build_package, prepare_import

    source = AtelierProject(
        id="source-project",
        title="Pending graph",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target-old",
                project_id="source-project",
                type="video",
                data={
                    "reference_node_ids": ["placeholder-old"],
                    "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "placeholder-old", "source_kind": "image", "status": "pending_media"}],
                },
            ),
            AtelierNode(id="placeholder-old", project_id="source-project", type="image"),
        ],
    )

    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-store").data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        id_factory=lambda kind: f"new-{kind}",
    )

    target = next(node for node in prepared.project.nodes if node.type == "video")
    placeholder = next(node for node in prepared.project.nodes if node.type == "image")
    assert target.data["reference_node_ids"] == [placeholder.id]
    assert target.data["pending_reference_nodes"] == [{"schema_version": 1, "source_node_id": placeholder.id, "source_kind": "image", "status": "pending_media"}]
    assert prepared.media == {}
    assert prepared.id_map["nodes"]["placeholder-old"] == placeholder.id


def test_atelier_package_rejects_pending_reference_authority_or_missing_source(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package

    authority = AtelierProject(
        id="source-project",
        title="Pending graph",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target-old",
                project_id="source-project",
                type="video",
                data={
                    "reference_node_ids": ["placeholder-old"],
                    "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "placeholder-old", "source_kind": "image", "status": "pending_media", "credential_ref": "secret"}],
                },
            ),
            AtelierNode(id="placeholder-old", project_id="source-project", type="image"),
        ],
    )
    with pytest.raises(AtelierProjectPackageError, match="pending reference"):
        build_package(authority, data_dir=tmp_path, source_storage_id="source-store")

    missing = AtelierProject(
        id="source-project",
        title="Pending graph",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target-old",
                project_id="source-project",
                type="video",
                data={
                    "reference_node_ids": ["missing"],
                    "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "missing", "source_kind": "image", "status": "pending_media"}],
                },
            ),
        ],
    )
    with pytest.raises(AtelierProjectPackageError, match="reference|pending"):
        build_package(missing, data_dir=tmp_path, source_storage_id="source-store")


def test_atelier_package_rejects_raw_authority_inside_serialized_package(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    damaged = _mutate_package_json(artifact.data, "project.json", lambda project_payload: project_payload.update({"agent_turns": [{"id": "hidden"}]}))

    with pytest.raises(AtelierProjectPackageError, match="authority|entry"):
        prepare_import(damaged, data_dir=tmp_path, target_storage_id="target-store")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda project_payload: project_payload["nodes"][0]["data"].update({"provider_request_id": "forged-provider-request"}),
        lambda project_payload: project_payload["nodes"][-1]["data"]["candidates"][0].update({"credential_ref": "forged-credential"}),
        lambda project_payload: project_payload["nodes"][-1]["data"]["candidates"][0].update({"execution_contract": "atelier-provider-video-v1"}),
        lambda project_payload: project_payload["exports"][0]["timeline_snapshot"]["clips"][0].update({"request_id": "forged-request"}),
        lambda project_payload: project_payload.update({"import_provenance": {"agent_history": [{"approval_record": {"decision": "approved"}}]}}),
    ],
)
def test_atelier_package_rejects_nested_forged_authority_before_staging(tmp_path, mutate):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    damaged = _mutate_package_json(artifact.data, "project.json", mutate)

    with pytest.raises(AtelierProjectPackageError, match="authority"):
        prepare_import(
            damaged,
            data_dir=tmp_path,
            target_storage_id="target-store",
            id_factory=lambda kind: f"forged-{kind}",
        )

    assert not (tmp_path / "video/imported/forged-project/take.mp4").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["source"].update({"project_id": "forged-source"}),
        lambda manifest: manifest["source"].update({"project_revision": 999}),
        lambda manifest: manifest["source"].update({"title": "Forged title"}),
    ],
)
def test_atelier_package_rejects_manifest_source_mismatch_before_staging(tmp_path, mutate):
    from src.apps.atelier.project_package import AtelierProjectPackageError, build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    damaged = _mutate_package_json(artifact.data, "manifest.json", mutate)

    with pytest.raises(AtelierProjectPackageError, match="source"):
        prepare_import(
            damaged,
            data_dir=tmp_path,
            target_storage_id="target-store",
            id_factory=lambda kind: f"mismatch-{kind}",
        )

    assert not (tmp_path / "video/imported/mismatch-project/take.mp4").exists()


def test_atelier_project_package_wire_fixture_export(tmp_path):
    import hashlib

    from src.apps.atelier.project_package import build_package, prepare_import

    _write_source_media(tmp_path)
    artifact = build_package(_source_project(), data_dir=tmp_path, source_storage_id="source-store")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-store",
        now_fn=lambda: 99.0,
        id_factory=lambda kind: f"fixture-{kind}",
    )
    payload = {
        "manifest": artifact.manifest,
        "prepared_project": prepared.project.model_dump(mode="json"),
        "media_map": prepared.media,
        "id_map": prepared.id_map,
    }
    out = tmp_path / "lumenx-atelier-project-package-wire.json"
    wire_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    out.write_text(wire_json, encoding="utf-8")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"atelier_project_package_wire={out} sha256={digest}")
    print("ATELIER_PROJECT_PACKAGE_WIRE_JSON_BEGIN")
    print(wire_json)
    print("ATELIER_PROJECT_PACKAGE_WIRE_JSON_END")
    assert payload["prepared_project"]["import_provenance"]["agent_history"][0]["user_message"]
    assert payload["prepared_project"]["exports"][0]["imported_proof"]["relative_path"] == payload["prepared_project"]["exports"][0]["video_url"]
