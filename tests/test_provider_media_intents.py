"""Core sync artifact intent contracts are product-neutral and frozen."""

from dataclasses import asdict
import hashlib
from types import MappingProxyType

import pytest

from src.audio.tts import PreparedTTS, TTSParameterNotice
from src.models.provider_media_intents import (
    PROVIDER_PREPARED_TTS_CONTRACT_VERSION,
    build_accepted_sync_artifact_record,
    prepared_tts_intent_payload,
    validate_sync_artifact_record_intent,
)
from src.models.provider_recovery import ProviderRecoveryError, _fingerprint, _record_fingerprint_payload, _verify_record_fingerprint
from src.models.provider_tasks import PreparedMedia, ProviderScope
from src.utils.generation_intent import GenerationIntent, normalize_generation_intent
from src.utils.media_versions import MediaVersion

SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


def _image_intent(*, mode: str = "t2i"):
    return normalize_generation_intent(
        {
            "model_id": "wan/wan2.7-image-pro#image",
            "mode": mode,
            "prompt": "A glass fox on the moon",
            "params": {"seed": 0, "prompt_extend": False},
        },
        allowed_modes={"t2i", "i2i"},
        allowed_parameters={"size", "seed", "negative_prompt", "prompt_extend", "watermark"},
    )


def _image_media(*, ordinal: int = 0, role: str = "reference_image", mime: str = "image/png") -> dict:
    return {
        "schema_version": 1,
        "sha256": chr(ord("a") + ordinal) * 64,
        "size_bytes": 123 + ordinal,
        "role": role,
        "ordinal": ordinal,
        "declared_media_type": mime,
    }


def _prepared_image_media() -> tuple[PreparedMedia, MediaVersion]:
    data = b"fixed-image-bytes"
    version = MediaVersion(
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        role="reference_image",
        ordinal=0,
        declared_media_type="image/png",
    )
    return PreparedMedia.from_verified_bytes(version, data, max_bytes=1024), version


def _prepared_tts():
    return PreparedTTS(
        "cosyvoice",
        (
            ("model", "cosyvoice-v3-flash"),
            ("voice", "longanyang"),
            ("text", "旁白从废墟中升起。"),
            ("speech_rate", 0.5),
            ("pitch_rate", 1.0),
            ("volume", 50),
        ),
        adjustments=(TTSParameterNotice("speech_rate", "clamped", 0.1, 0.5),),
        unsupported=(),
    )


def test_build_image_sync_artifact_record_uses_generation_intent_without_io():
    record = build_accepted_sync_artifact_record(
        intent=_image_intent(),
        provider_model_id="wan2.7-image-pro",
        media_kind="image",
        media=[],
        scope=SCOPE,
        credential_ref="credential-image",
        attempt_id="attempt-image",
        output_ref="assets/atelier/out.png",
    )

    assert record.schema_version == 2
    assert record.execution_kind == "sync_artifact"
    assert record.media_kind == "image"
    assert record.phase == "accepted"
    assert record.handle is None and record.observation is None
    assert validate_sync_artifact_record_intent(record) == "image"
    _verify_record_fingerprint(record)
    assert record.fingerprint == _fingerprint(_record_fingerprint_payload(record))


def test_build_audio_sync_artifact_record_uses_prepared_tts_snapshot_not_fake_catalog_intent():
    payload = prepared_tts_intent_payload(_prepared_tts(), request_fields=("voice", "text", "params.speech_rate"))
    record = build_accepted_sync_artifact_record(
        intent=payload,
        provider_model_id="cosyvoice-v3-flash",
        media_kind="audio",
        media=[],
        scope=SCOPE,
        credential_ref="credential-audio",
        attempt_id="attempt-audio",
        output_ref="audio/atelier/out.mp3",
    )

    assert payload["intent_contract_version"] == PROVIDER_PREPARED_TTS_CONTRACT_VERSION
    assert payload["prepared_tts"] == _prepared_tts().to_snapshot()
    assert payload["request_fields"] == ["params.speech_rate", "text", "voice"]
    assert record.provider_model_id == "cosyvoice-v3-flash"
    assert validate_sync_artifact_record_intent(record) == "audio"
    _verify_record_fingerprint(record)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p.__setitem__("intent_contract_version", "generation-intent-v1"), "tts"),
        (lambda p: p.__setitem__("prepared_tts", {**p["prepared_tts"], "family": "alien"}), "tts"),
        (lambda p: p.__setitem__("request_fields", ["voice", "voice"]), "request"),
    ],
)
def test_prepared_tts_intent_validation_rejects_malformed_frozen_snapshots(mutate, match):
    payload = prepared_tts_intent_payload(_prepared_tts(), request_fields=("voice", "text"))
    mutate(payload)

    with pytest.raises(ProviderRecoveryError, match=match):
        build_accepted_sync_artifact_record(
            intent=payload,
            provider_model_id="cosyvoice-v3-flash",
            media_kind="audio",
            media=[],
            scope=SCOPE,
            credential_ref="credential-audio",
            attempt_id="attempt-audio",
            output_ref="audio/atelier/out.mp3",
        )


def test_audio_record_provider_model_must_match_prepared_tts_model_and_have_no_media():
    payload = prepared_tts_intent_payload(_prepared_tts(), request_fields=("voice", "text"))
    with pytest.raises(ProviderRecoveryError, match="provider_model"):
        build_accepted_sync_artifact_record(
            intent=payload,
            provider_model_id="qwen-tts-fake",
            media_kind="audio",
            media=[],
            scope=SCOPE,
            credential_ref="credential-audio",
            attempt_id="attempt-audio",
            output_ref="audio/atelier/out.mp3",
        )

    with pytest.raises(ProviderRecoveryError, match="media"):
        build_accepted_sync_artifact_record(
            intent=payload,
            provider_model_id="cosyvoice-v3-flash",
            media=[{"sha256": "a" * 64, "size_bytes": 1, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png"}],
            media_kind="audio",
            scope=SCOPE,
            credential_ref="credential-audio",
            attempt_id="attempt-audio",
            output_ref="audio/atelier/out.mp3",
        )


@pytest.mark.parametrize(
    "intent",
    [
        normalize_generation_intent(
            {"model_id": "wan/wan3.0-video#video", "mode": "t2v", "prompt": "x", "params": {}},
            allowed_modes={"t2v"},
            allowed_parameters=set(),
        ),
        GenerationIntent(
            model_id="wan/wan2.7-image-pro#image",
            catalog_mode="image",
            mode="r2v",
            prompt="x",
            params=MappingProxyType({}),
            sources=MappingProxyType({}),
            request_fields=(),
            preset_id=None,
            preset_version=None,
        ),
    ],
)
def test_image_record_requires_frozen_generation_intent_for_image_t2i_or_i2i(intent):
    with pytest.raises(ProviderRecoveryError, match="image"):
        build_accepted_sync_artifact_record(
            intent=intent,
            provider_model_id="wan2.7-image-pro",
            media_kind="image",
            media=[],
            scope=SCOPE,
            credential_ref="credential-image",
            attempt_id="attempt-image",
            output_ref="assets/atelier/out.png",
        )


def test_builder_rejects_media_kind_intent_mismatch_and_never_reads_environment():
    with pytest.raises(ProviderRecoveryError, match="intent"):
        build_accepted_sync_artifact_record(
            intent=prepared_tts_intent_payload(_prepared_tts(), request_fields=("voice", "text")),
            provider_model_id="cosyvoice-v3-flash",
            media_kind="image",
            media=[],
            scope=SCOPE,
            credential_ref="credential-image",
            attempt_id="attempt-image",
            output_ref="assets/atelier/out.png",
        )


def test_build_image_i2i_sync_artifact_record_accepts_strict_canonical_media_versions_without_live_files():
    prepared, version = _prepared_image_media()
    record = build_accepted_sync_artifact_record(
        intent=_image_intent(mode="i2i"),
        provider_model_id="wan2.7-image-pro",
        media_kind="image",
        media=[prepared],
        scope=SCOPE,
        credential_ref="credential-image",
        attempt_id="attempt-image",
        output_ref="assets/atelier/out.png",
    )

    assert record.media == [asdict(version)]
    assert validate_sync_artifact_record_intent(record) == "image"


@pytest.mark.parametrize(
    ("intent_mode", "media", "match"),
    [
        ("t2i", [_image_media()], "media"),
        ("i2i", [], "media"),
        ("i2i", [{**_image_media(), "schema_version": 999}], "media"),
        ("i2i", [{**_image_media(), "sha256": "not-a-hash"}], "media"),
        ("i2i", [{**_image_media(), "size_bytes": -1}], "media"),
        ("i2i", [_image_media(role="reference_audio", mime="audio/wav")], "image"),
        ("i2i", [{**_image_media(), "ordinal": -5}], "media"),
        ("i2i", [_image_media(mime="audio/wav")], "image"),
        ("i2i", [_image_media(ordinal=1)], "ordinal"),
    ],
)
def test_build_image_sync_artifact_record_rejects_malformed_or_noncanonical_media(intent_mode, media, match):
    with pytest.raises(ProviderRecoveryError, match=match):
        build_accepted_sync_artifact_record(
            intent=_image_intent(mode=intent_mode),
            provider_model_id="wan2.7-image-pro",
            media_kind="image",
            media=media,
            scope=SCOPE,
            credential_ref="credential-image",
            attempt_id="attempt-image",
            output_ref="assets/atelier/out.png",
        )
