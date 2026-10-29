"""Atelier media generation preparation builds accepted records without provider I/O."""

from types import SimpleNamespace

import pytest

from src.apps.atelier.generation_commands import ManualImageCandidateBatchCommand
from src.apps.atelier.media_generation_preparation import (
    build_image_media_generation_preparation,
    build_tts_media_generation_preparation,
)
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.tts_commands import TTSCandidateCommand
from src.models.provider_binding import MULEROUTER_HTTP_SOURCE_KEY
from src.models.provider_media_runtime import MediaRuntimeAuthority
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.json_storage import DirectoryWriterLock, serialize_json
from tests.frozen_image_fixtures import rgb_png_bytes


def project():
    return AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="image-target", project_id="p", type="image", data={"reference_node_ids": ["ref", "bad"]}),
            AtelierNode(id="ref", project_id="p", type="image", media_urls=["uploads/ref.png"]),
            AtelierNode(id="bad", project_id="p", type="image", media_urls=["uploads/bad.png"]),
            AtelierNode(id="audio-target", project_id="p", type="audio"),
        ],
    )


def image_command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "image-request",
        "model_id": "wan/wan2.7-image-pro#image",
        "mode": "t2i",
        "prompt": "A lunar garden",
        "media": [],
        "params": {"seed": 0, "prompt_extend": False, "watermark": False},
        "batch_size": 2,
    }
    payload.update(changes)
    return ManualImageCandidateBatchCommand(**payload)


def tts_command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "tts-request",
        "voice": "longanyang",
        "text": "旁白从废墟中升起。",
        "params": {"volume": 0, "instructions": "低声"},
    }
    payload.update(changes)
    return TTSCandidateCommand(**payload)


def scope():
    return ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")


def authority(*, media_kind="image", provider_model_id="wan2.7-image-pro", provider_scope=None):
    provider_scope = provider_scope or scope()
    return MediaRuntimeAuthority(
        media_kind=media_kind,
        provider_model_id=provider_model_id,
        scope=provider_scope,
        credential_ref="credential-ref",
        source_key="DASHSCOPE_API_KEY",
        credential=ResolvedCredential(provider_scope, "credential-ref", "sk-test-" + "d" * 44),
    )


def media_scope(tmp_path):
    root = tmp_path / "media-root"
    (root / "uploads").mkdir(parents=True)
    (root / "uploads" / "ref.png").write_bytes(rgb_png_bytes("atelier-ref"))
    (root / "uploads" / "bad.png").write_bytes(b"not an image")
    lease = DirectoryWriterLock(root).acquire()
    return SimpleNamespace(data_dir=root, writer_lock=lease), lease


def test_t2i_image_preparation_builds_secret_free_schema2_record_without_media(tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        prepared = build_image_media_generation_preparation(
            project(),
            "image-target",
            image_command(model_id="qwen/qwen-image-2.0#image"),
            media_scope=ms,
            authority=authority(provider_model_id="qwen-image-2.0"),
            server_storage_identity="server-store",
            attempt_id="attempt-1",
            candidate_id="candidate-1",
        )
    finally:
        lease.close()

    assert prepared.output_ref == "assets/atelier/candidate-1-attempt-1.png"
    assert prepared.record.schema_version == 2
    assert prepared.record.phase == "accepted"
    assert prepared.record.execution_kind == "sync_artifact"
    assert prepared.record.media_kind == "image"
    assert prepared.record.media == []
    assert prepared.record.output_ref == prepared.output_ref
    assert prepared.record.intent["params"]["seed"] == 0
    assert prepared.record.intent["params"]["prompt_extend"] is False
    assert prepared.record.intent["params"]["watermark"] is False
    assert prepared.public_record["runtime_authority"]["credential_ref"] == "credential-ref"
    assert "object at" not in serialize_json(prepared.public_record)
    assert prepared.preview_fingerprint == build_image_media_generation_preparation(
        project(),
        "image-target",
        image_command(model_id="qwen/qwen-image-2.0#image"),
        media_scope=ms,
        authority=authority(provider_model_id="qwen-image-2.0"),
        server_storage_identity="server-store",
        attempt_id="attempt-1",
        candidate_id="candidate-1",
    ).preview_fingerprint


def test_i2i_image_preparation_freezes_owned_real_image_bytes_in_order(tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        prepared = build_image_media_generation_preparation(
            project(),
            "image-target",
            image_command(mode="i2i", media=[{
                "role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png",
            }]),
            media_scope=ms,
            authority=authority(),
            server_storage_identity="server-store",
            attempt_id="attempt-2",
            candidate_id="candidate-2",
        )
    finally:
        lease.close()

    assert [item["role"] for item in prepared.record.media] == ["reference_image"]
    assert prepared.record.media[0]["ordinal"] == 0
    assert prepared.public_record["frozen_media"][0]["declared_media_type"] == "image/png"


def test_i2i_image_preparation_rejects_bad_image_decode_before_record(tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        with pytest.raises(Exception):
            build_image_media_generation_preparation(
                project(),
                "image-target",
                image_command(mode="i2i", media=[{
                    "role": "reference_image", "ordinal": 0, "node_id": "bad", "media_url": "uploads/bad.png",
                }]),
                media_scope=ms,
                authority=authority(),
                server_storage_identity="server-store",
                attempt_id="attempt-3",
                candidate_id="candidate-3",
            )
    finally:
        lease.close()


def test_wrong_image_authority_rejects_before_freezing(monkeypatch, tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        monkeypatch.setattr(
            "src.apps.atelier.media_generation_preparation.freeze_media",
            lambda *_args, **_kwargs: pytest.fail("authority mismatch must reject before freeze_media"),
            raising=False,
        )
        with pytest.raises(ValueError, match="authority"):
            build_image_media_generation_preparation(
                project(),
                "image-target",
                image_command(mode="i2i", media=[{
                    "role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png",
                }]),
                media_scope=ms,
                authority=authority(media_kind="audio", provider_model_id="cosyvoice-v3-flash"),
                server_storage_identity="server-store",
                attempt_id="attempt-4",
                candidate_id="candidate-4",
            )
    finally:
        lease.close()



def test_image_preparation_rejects_scope_mismatch_before_freezing(monkeypatch, tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        monkeypatch.setattr(
            "src.apps.atelier.media_generation_preparation.freeze_media",
            lambda *_args, **_kwargs: pytest.fail("scope mismatch must reject before freeze_media"),
            raising=False,
        )
        bad_scope = ProviderScope("dashscope", "wrong", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")
        with pytest.raises(ValueError, match="scope"):
            build_image_media_generation_preparation(
                project(),
                "image-target",
                image_command(mode="i2i", media=[{
                    "role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png",
                }]),
                media_scope=ms,
                authority=authority(provider_scope=bad_scope),
                server_storage_identity="server-store",
                attempt_id="attempt-scope",
                candidate_id="candidate-scope",
            )
    finally:
        lease.close()

def test_preparation_revalidates_mutated_authority_before_freezing(monkeypatch, tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        bad_authority = authority()
        object.__setattr__(bad_authority, "source_key", MULEROUTER_HTTP_SOURCE_KEY)
        monkeypatch.setattr(
            "src.apps.atelier.media_generation_preparation.freeze_media",
            lambda *_args, **_kwargs: pytest.fail("mutated authority must reject before freeze_media"),
            raising=False,
        )
        with pytest.raises(ValueError, match="authority"):
            build_image_media_generation_preparation(
                project(),
                "image-target",
                image_command(mode="i2i", media=[{
                    "role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png",
                }]),
                media_scope=ms,
                authority=bad_authority,
                server_storage_identity="server-store",
                attempt_id="attempt-mutated",
                candidate_id="candidate-mutated",
            )
    finally:
        lease.close()


def test_tts_preparation_builds_audio_record_with_explicit_request_fields(tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        prepared = build_tts_media_generation_preparation(
            project(),
            "audio-target",
            tts_command(),
            media_scope=ms,
            authority=authority(media_kind="audio", provider_model_id="cosyvoice-v3-flash"),
            server_storage_identity="server-store",
            attempt_id="attempt-audio",
            candidate_id="candidate-audio",
        )
    finally:
        lease.close()

    assert prepared.output_ref == "audio/atelier/candidate-audio-attempt-audio.mp3"
    assert prepared.record.media_kind == "audio"
    assert prepared.record.media == []
    assert prepared.record.provider_model_id == "cosyvoice-v3-flash"
    assert prepared.record.intent["prepared_tts"] == prepared.preview.public_record["prepared_tts"]
    assert prepared.record.intent["request_fields"] == ["params.instructions", "params.volume", "text", "voice"]


def test_tts_preparation_rejects_nonmatching_authority(tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        with pytest.raises(ValueError, match="authority"):
            build_tts_media_generation_preparation(
                project(),
                "audio-target",
                tts_command(),
                media_scope=ms,
                authority=authority(media_kind="image", provider_model_id="wan2.7-image-pro"),
                server_storage_identity="server-store",
                attempt_id="attempt-audio",
                candidate_id="candidate-audio",
            )
    finally:
        lease.close()


def test_preparation_rejects_invalid_output_ids_before_io(monkeypatch, tmp_path):
    ms, lease = media_scope(tmp_path)
    try:
        monkeypatch.setattr(
            "src.apps.atelier.media_generation_preparation.freeze_media",
            lambda *_args, **_kwargs: pytest.fail("invalid id must reject before freeze_media"),
            raising=False,
        )
        with pytest.raises(ValueError, match="identifier"):
            build_image_media_generation_preparation(
                project(),
                "image-target",
                image_command(mode="i2i", media=[{
                    "role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png",
                }]),
                media_scope=ms,
                authority=authority(),
                server_storage_identity="server-store",
                attempt_id="../bad",
                candidate_id="candidate-5",
            )
    finally:
        lease.close()
