"""Studio Wan3 ordered provider admission is pure and keeps legacy v1 stable."""

from __future__ import annotations

import hashlib
import io
import subprocess
import wave

import pytest

from src.apps.comic_gen import provider_recovery
from src.apps.comic_gen.provider_recovery import StudioProviderRecoveryError
from src.models.provider_tasks import ProviderScope
from src.utils.json_storage import DirectoryWriterLock
from src.utils.request_identity import fingerprint_request
from tests.frozen_image_fixtures import rgb_png_bytes


PNG = rgb_png_bytes("studio admission")
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="workspace",
)


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\0\0" * 16_000)
    return buffer.getvalue()


def _mp4_bytes(tmp_path) -> bytes:
    path = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=240x240:d=1:r=16",
        "-an", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path.read_bytes()


def _write_sources(root, *, include_av=False):
    uploads = root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "first.png").write_bytes(PNG)
    (uploads / "last.png").write_bytes(PNG)
    (uploads / "ref.png").write_bytes(PNG)
    if include_av:
        (uploads / "ref.wav").write_bytes(_wav_bytes())
        (uploads / "ref.mp4").write_bytes(_mp4_bytes(root))


def _ordered_request(**overrides):
    request = {
        "contract_version": "ordered-media-v1",
        "model": "wan3.0-video",
        "generation_mode": "t2v",
        "prompt": "A quiet establishing shot",
        "frame_id": "frame-1",
        "duration": -1,
        "resolution": "1080P",
        "ratio": "16:9",
        "seed": 0,
        "prompt_extend": False,
        "watermark": False,
        "generate_audio": False,
    }
    request.update(overrides)
    return request


def _legacy_request():
    return {
        "image_url": "uploads/first.png",
        "prompt": "legacy",
        "model": "wan2.7-i2v",
        "frame_id": "frame-1",
        "duration": 5,
        "resolution": "1080p",
        "seed": 0,
        "prompt_extend": False,
        "watermark": False,
    }


def test_studio_ordered_t2v_accepts_zero_media_and_preserves_params(tmp_path):
    _write_sources(tmp_path)
    submission, record = provider_recovery.build_ordered_accepted_submission_and_record(
        _ordered_request(),
        output_root=tmp_path,
        writer_lock=DirectoryWriterLock(tmp_path).acquire(),
        scope=SCOPE,
        credential_ref="credential-ref",
        attempt_id="attempt-t2v",
    )

    assert provider_recovery.STUDIO_ORDERED_VIDEO_ENTRYPOINT == "studio.provider_video.v2"
    assert submission.media == ()
    assert record.phase == "accepted"
    assert record.provider_model_id == "wan3.0-video"
    assert record.media == []
    assert record.intent["mode"] == "t2v"
    assert record.intent["params"]["duration"] == -1
    assert record.intent["params"]["seed"] == 0
    assert record.intent["params"]["audio"] is False
    assert "negative_prompt" not in record.intent["params"]


def test_studio_ordered_i2v_freezes_first_and_last_without_upload(tmp_path):
    _write_sources(tmp_path)
    submission, record = provider_recovery.build_ordered_accepted_submission_and_record(
        _ordered_request(generation_mode="i2v", image_url="uploads/first.png", last_frame_url="uploads/last.png"),
        output_root=tmp_path,
        writer_lock=DirectoryWriterLock(tmp_path).acquire(),
        scope=SCOPE,
        credential_ref="credential-ref",
        attempt_id="attempt-i2v",
    )

    assert [item.version.role for item in submission.media] == ["first_frame", "last_frame"]
    assert [item.version.ordinal for item in submission.media] == [0, 0]
    assert all(item.transport_binding == "data_uri_sha256_v1" for item in submission.media)
    assert [(item["role"], item["ordinal"]) for item in record.media] == [("first_frame", 0), ("last_frame", 0)]
    assert record.media[0]["sha256"] == hashlib.sha256(PNG).hexdigest()


def test_studio_ordered_r2v_keeps_reference_image_video_audio_order_and_pending_av(tmp_path):
    _write_sources(tmp_path, include_av=True)
    submission, record = provider_recovery.build_ordered_accepted_submission_and_record(
        _ordered_request(
            generation_mode="r2v",
            reference_image_urls=["uploads/ref.png"],
            reference_video_urls=["uploads/ref.mp4"],
            reference_audio_urls=["uploads/ref.wav"],
        ),
        output_root=tmp_path,
        writer_lock=DirectoryWriterLock(tmp_path).acquire(),
        scope=SCOPE,
        credential_ref="credential-ref",
        attempt_id="attempt-r2v",
    )

    assert [item.version.role for item in submission.media] == ["reference_image", "reference_video", "reference_audio"]
    assert [item.transport_binding for item in submission.media] == [
        "data_uri_sha256_v1", "frozen_av_sha256_v1", "frozen_av_sha256_v1",
    ]
    assert submission.media[1].frozen_bytes is not None
    assert submission.media[2].frozen_bytes is not None
    assert [(item["role"], item["ordinal"]) for item in record.media] == [
        ("reference_image", 0), ("reference_video", 0), ("reference_audio", 0),
    ]
    assert record.intent["mode"] == "r2v"


@pytest.mark.parametrize("payload", [
    _ordered_request(generation_mode="t2v", image_url="uploads/first.png"),
    _ordered_request(generation_mode="i2v", last_frame_url="uploads/last.png"),
    _ordered_request(generation_mode="i2v", image_url="uploads/first.png", reference_image_urls=["uploads/ref.png"]),
    _ordered_request(generation_mode="r2v", image_url="uploads/first.png", reference_image_urls=["uploads/ref.png"]),
    _ordered_request(generation_mode="r2v"),
    _ordered_request(generation_mode="r2v", reference_audio_urls=["uploads/ref.wav"], audio_url="uploads/ref.wav"),
    _ordered_request(negative_prompt="legacy unsupported"),
    _ordered_request(contract_version="ordered-media-v2"),
    _ordered_request(model="wan2.7-i2v"),
    _ordered_request(asset_id="asset"),
])
def test_studio_ordered_rejects_conflicting_or_unsupported_shapes(tmp_path, payload):
    _write_sources(tmp_path, include_av=True)
    with pytest.raises(StudioProviderRecoveryError):
        provider_recovery.build_ordered_accepted_submission_and_record(
            payload,
            output_root=tmp_path,
            writer_lock=DirectoryWriterLock(tmp_path).acquire(),
            scope=SCOPE,
            credential_ref="credential-ref",
            attempt_id="attempt-bad",
        )


def test_studio_legacy_v1_request_fingerprint_and_record_remain_unchanged(tmp_path):
    _write_sources(tmp_path)
    request = _legacy_request()
    before = fingerprint_request(dict(request), entrypoint=provider_recovery.STUDIO_VIDEO_ENTRYPOINT)
    _submission, record = provider_recovery.build_accepted_submission_and_record(
        request,
        output_root=tmp_path,
        writer_lock=DirectoryWriterLock(tmp_path).acquire(),
        scope=ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing"),
        credential_ref="legacy-ref",
        attempt_id="attempt-legacy",
    )
    after = fingerprint_request(dict(request), entrypoint=provider_recovery.STUDIO_VIDEO_ENTRYPOINT)

    assert before == after
    assert provider_recovery.STUDIO_VIDEO_ENTRYPOINT == "studio.provider_video.v1"
    assert record.provider_model_id == "wan2.7-i2v"
    assert record.intent["mode"] == "i2v"
    assert [(item["role"], item["ordinal"]) for item in record.media] == [("first_frame", 0)]
