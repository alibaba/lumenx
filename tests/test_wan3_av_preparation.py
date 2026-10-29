"""Wan3 AV Core keeps fixed bytes durable and uploads only for transient submit."""

from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace
import wave
from dataclasses import replace

import pytest

from src.models.provider_recovery import (
    ProviderRecoveryError,
    build_accepted_provider_submission_and_record,
    prepare_submission_transport,
    reconstruct_submission_from_record,
)
from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ProviderSubmission,
    ResolvedCredential,
)
from src.models.provider_upload import ProviderUploadResult
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import DirectoryWriterLock
from src.utils.media_probe import FrozenMediaMetadata
from src.utils.media_versions import MediaVersion, freeze_media
from tests import test_provider_submit as fixtures


contract = fixtures.contract
scope = fixtures.scope
credential = fixtures.credential
model = fixtures.model
transport = fixtures.transport


WAN3_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://original-workspace.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="original-workspace",
)
WAN3_CREDENTIAL = ResolvedCredential(WAN3_SCOPE, "credentials/original", "test-only-secret")


def _intent(mode="r2v", *, duration=5, audio=False):
    return GenerationIntent(
        model_id="wan/wan3.0-video#video",
        catalog_mode="video",
        mode=mode,
        prompt="A scored reference montage",
        params={
            "duration": duration,
            "resolution": "1080P",
            "ratio": "adaptive",
            "prompt_extend": True,
            "watermark": False,
            "audio": audio,
        },
        sources={},
        request_fields=(),
    )


def _version(data: bytes, role="reference_audio", ordinal=0, mime="audio/wav") -> MediaVersion:
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


def _wav_bytes(seconds: float = 1.0, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    frames = int(seconds * sample_rate)
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


def _fake_audio_probe(_version, _data):
    return FrozenMediaMetadata(kind="audio", duration=1.0, format_names=("wav",))


def _fake_video_probe(_version, _data):
    return FrozenMediaMetadata(kind="video", duration=2.0, width=1280, height=720, fps=24.0, format_names=("mp4",))


def _pending_audio(data=b"audio bytes", ordinal=0, *, probe=_fake_audio_probe):
    return PreparedMedia.from_pending_av_bytes(_version(data, "reference_audio", ordinal, "audio/wav"), data, probe=probe)


def _pending_video(data=b"video bytes", ordinal=0, *, probe=_fake_video_probe):
    return PreparedMedia.from_pending_av_bytes(_version(data, "reference_video", ordinal, "video/mp4"), data, probe=probe)


def _submission(media, *, duration=5, audio=False):
    return ProviderSubmission(
        intent=_intent(duration=duration, audio=audio),
        provider_model_id="wan3.0-video",
        media=tuple(media),
        scope=WAN3_SCOPE,
        credential_ref="credentials/original",
    )


def test_pending_av_media_is_probe_verified_and_repr_hides_bytes_and_metadata():
    calls = []

    def probe(version, data):
        calls.append((version, data))
        return _fake_audio_probe(version, data)

    media = _pending_audio(b"secret audio bytes", probe=probe)
    assert calls == [(media.version, b"secret audio bytes")]
    assert media.transport_binding == "frozen_av_sha256_v1"
    assert media.frozen_bytes == b"secret audio bytes"
    assert media.frozen_metadata.kind == "audio"
    assert "secret audio bytes" not in repr(media)
    assert "FrozenMediaMetadata" not in repr(media)
    with pytest.raises(TypeError):
        PreparedMedia.from_verified_provider_url(media.version, "oss://arbitrary", metadata=media.frozen_metadata)


def test_verified_provider_url_requires_typed_upload_result_and_matching_metadata_kind():
    media = _pending_audio()
    duck_typed = SimpleNamespace(
        url="oss://dashscope-temp/reference_audio.wav",
        key="dashscope-temp/reference_audio.wav",
        provider_model_id="wan3.0-video",
        version=media.version,
        scope=WAN3_SCOPE,
        credential_ref="credentials/original",
    )
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_url(
            media.version, upload_result=duck_typed, metadata=media.frozen_metadata,
        )
    typed_result = ProviderUploadResult(
        url="oss://dashscope-temp/reference_audio.wav",
        key="dashscope-temp/reference_audio.wav",
        provider_model_id="wan3.0-video",
        version=media.version,
        scope=WAN3_SCOPE,
        credential_ref="credentials/original",
    )
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_url(
            media.version,
            upload_result=typed_result,
            metadata=FrozenMediaMetadata(
                kind="video", duration=2.0, width=1280, height=720, fps=24.0, format_names=("mp4",),
            ),
        )


def test_pending_av_media_supports_actual_frozen_wav_roundtrip():
    data = _wav_bytes()
    version = _version(data, "reference_audio", 0, "audio/wav")
    media = PreparedMedia.from_pending_av_bytes(version, data)
    assert media.frozen_metadata.kind == "audio"
    assert 0.9 <= media.frozen_metadata.duration <= 1.1
    assert media.transport_binding == "frozen_av_sha256_v1"


def test_recovery_reconstructs_av_from_fixed_blob_without_mutable_source(tmp_path, monkeypatch):
    lease = DirectoryWriterLock(tmp_path).acquire()
    try:
        source = tmp_path / "uploads" / "ref.wav"
        source.parent.mkdir()
        source.write_bytes(b"audio bytes")
        version = freeze_media(
            "uploads/ref.wav",
            output_root=tmp_path,
            writer_lock=lease,
            role="reference_audio",
            ordinal=0,
            max_bytes=15 * 1024 * 1024,
        )
        monkeypatch.setattr("src.models.provider_tasks.probe_frozen_media", _fake_audio_probe)
        pending = PreparedMedia.from_pending_av_bytes(version, b"audio bytes", probe=_fake_audio_probe)
        submission, record = build_accepted_provider_submission_and_record(
            _intent(),
            provider_model_id="wan3.0-video",
            media=(pending,),
            scope=WAN3_SCOPE,
            credential_ref="credentials/original",
            attempt_id="attempt-av",
        )
        source.write_bytes(b"changed mutable source")
        recovered = reconstruct_submission_from_record(record, output_root=tmp_path)
        assert recovered.intent == submission.intent
        assert recovered.media[0].version == version
        assert recovered.media[0].frozen_bytes == b"audio bytes"
        assert recovered.media[0].transport_binding == "frozen_av_sha256_v1"
    finally:
        lease.close()


def test_prepare_submission_transport_uploads_pending_av_and_preserves_record_identity():
    pending = _pending_audio()
    submission = _submission((pending,), audio=False)
    _, record = build_accepted_provider_submission_and_record(
        submission.intent,
        provider_model_id=submission.provider_model_id,
        media=submission.media,
        scope=submission.scope,
        credential_ref=submission.credential_ref,
        attempt_id="attempt-av",
    )
    calls = []

    def uploader(**kwargs):
        calls.append(kwargs)
        return ProviderUploadResult(
            url="oss://dashscope-temp/reference_audio.wav",
            key="dashscope-temp/reference_audio.wav",
            provider_model_id=kwargs["provider_model_id"],
            version=kwargs["version"],
            scope=kwargs["scope"],
            credential_ref=kwargs["credential"].credential_ref,
        )

    ready = prepare_submission_transport(submission, WAN3_CREDENTIAL, uploader=uploader)
    assert calls == [{
        "scope": WAN3_SCOPE,
        "provider_model_id": "wan3.0-video",
        "credential": WAN3_CREDENTIAL,
        "version": pending.version,
        "data": b"audio bytes",
    }]
    assert record.media == [pending.version.__dict__]
    assert ready.media[0].transport_binding == "dashscope_oss_sha256_v1"
    assert ready.media[0].url == "oss://dashscope-temp/reference_audio.wav"
    assert ready.media[0].frozen_metadata == pending.frozen_metadata


@pytest.mark.parametrize("field", ["version", "scope", "provider_model_id", "credential_ref", "url"])
def test_prepare_submission_transport_rejects_mismatched_upload_result(field):
    pending = _pending_audio()
    submission = _submission((pending,))

    def uploader(**kwargs):
        result = ProviderUploadResult(
            url="oss://dashscope-temp/reference_audio.wav",
            key="dashscope-temp/reference_audio.wav",
            provider_model_id=kwargs["provider_model_id"],
            version=kwargs["version"],
            scope=kwargs["scope"],
            credential_ref=kwargs["credential"].credential_ref,
        )
        if field == "version":
            return replace(result, version=_version(b"other", "reference_audio", 0, "audio/wav"))
        if field == "scope":
            return replace(result, scope=replace(WAN3_SCOPE, workspace_id="other-workspace"))
        if field == "provider_model_id":
            return replace(result, provider_model_id="wan3.0-video-prime")
        if field == "credential_ref":
            return replace(result, credential_ref="credentials/other")
        return replace(result, url="https://example.invalid/not-oss")

    with pytest.raises(ProviderRecoveryError):
        prepare_submission_transport(submission, WAN3_CREDENTIAL, uploader=uploader)


@pytest.mark.parametrize("submission", [
    ProviderSubmission(
        intent=_intent(mode="i2v"),
        provider_model_id="wan3.0-video",
        media=(_pending_audio(),),
        scope=WAN3_SCOPE,
        credential_ref="credentials/original",
    ),
    _submission((_pending_audio(),), duration=1),
    _submission((_pending_audio(ordinal=1),)),
])
def test_prepare_submission_transport_validates_before_upload(submission):
    calls = []

    def uploader(**kwargs):
        calls.append(kwargs)
        raise AssertionError("uploader must not run for invalid local submissions")

    with pytest.raises(ProviderRecoveryError):
        prepare_submission_transport(submission, WAN3_CREDENTIAL, uploader=uploader)
    assert calls == []


def test_wan3_submit_rejects_pending_av_before_post(model, credential, transport):
    request = _submission((_pending_audio(),))
    with pytest.raises(ProviderBindingError):
        model.submit(request, credential=replace(credential, scope=WAN3_SCOPE), on_submitted=lambda _: None)
    assert transport[1] == []


def test_wan3_submit_oss_av_sets_resolution_header_and_preserves_explicit_audio_false(model, credential, transport):
    ready = prepare_submission_transport(
        _submission((_pending_audio(),), audio=False),
        WAN3_CREDENTIAL,
        uploader=lambda **kwargs: ProviderUploadResult(
            url="oss://dashscope-temp/reference_audio.wav",
            key="dashscope-temp/reference_audio.wav",
            provider_model_id=kwargs["provider_model_id"],
            version=kwargs["version"],
            scope=kwargs["scope"],
            credential_ref=kwargs["credential"].credential_ref,
        ),
    )
    model.submit(ready, credential=replace(credential, scope=WAN3_SCOPE), on_submitted=lambda _: None)
    options = transport[1][0][1]
    assert options["headers"]["X-DashScope-OssResourceResolve"] == "enable"
    assert options["json"]["input"]["media"] == [
        {"type": "reference_audio", "url": "oss://dashscope-temp/reference_audio.wav"},
    ]
    assert options["json"]["parameters"]["audio"] is False


@pytest.mark.parametrize("media", [
    tuple(_pending_audio(ordinal=index) for index in range(6)),
    tuple(_pending_video(ordinal=index) for index in range(6)),
    tuple(_pending_audio(ordinal=index) for index in range(5))
    + tuple(_pending_video(ordinal=index) for index in range(5))
    + tuple(_pending_audio(b"extra", ordinal=5) for _ in range(11)),
])
def test_wan3_rejects_av_count_limits(media):
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(media))


@pytest.mark.parametrize("metadata", [
    FrozenMediaMetadata(kind="audio", duration=0.5, format_names=("wav",)),
    FrozenMediaMetadata(kind="audio", duration=16.0, format_names=("wav",)),
    FrozenMediaMetadata(kind="video", duration=2.0, width=1280, height=720, fps=15.0, format_names=("mp4",)),
    FrozenMediaMetadata(kind="video", duration=2.0, width=200, height=720, fps=24.0, format_names=("mp4",)),
    FrozenMediaMetadata(kind="video", duration=2.0, width=4097, height=720, fps=24.0, format_names=("mp4",)),
    FrozenMediaMetadata(kind="video", duration=2.0, width=4096, height=240, fps=24.0, format_names=("mp4",)),
])
def test_wan3_rejects_av_metadata_limits(metadata):
    role = "reference_audio" if metadata.kind == "audio" else "reference_video"
    mime = "audio/wav" if metadata.kind == "audio" else "video/mp4"
    data = f"{role} bytes".encode()
    media = PreparedMedia.from_pending_av_bytes(_version(data, role, 0, mime), data, probe=lambda *_: metadata)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission((media,)))


def test_wan3_rejects_video_input_plus_positive_output_over_30_seconds():
    media = _pending_video(probe=lambda *_: FrozenMediaMetadata(
        kind="video", duration=15.0, width=1280, height=720, fps=24.0, format_names=("mp4",),
    ))
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission((media,), duration=16))
    assert WanxModel.validate_submission(_submission((media,), duration=-1)) is None
