"""Frozen media execution reads and proof construction do not consult live defaults."""

import pytest

from src.audio.tts import PreparedTTS
from src.models import provider_media_intents as intents
from src.models.provider_recovery import ProviderRecoveryError, record_with_changes
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import GenerationIntent


def record(kind="image"):
    image = GenerationIntent(
        model_id="wan/wan2.7-image-pro#image", catalog_mode="image", mode="t2i",
        prompt="Frozen light", params={"seed": 0, "watermark": False},
        sources={"params.seed": "request", "params.watermark": "request"},
        request_fields=("params.seed", "params.watermark"),
    )
    speech = PreparedTTS("cosyvoice", (
        ("model", "cosyvoice-v3-flash"), ("voice", "longanyang"), ("text", "Frozen speech"),
        ("speech_rate", 0.5), ("pitch_rate", 1.0), ("volume", 0),
    ))
    return intents.build_accepted_sync_artifact_record(
        intent=image if kind == "image" else intents.prepared_tts_intent_payload(speech, request_fields=("text",)),
        provider_model_id="wan2.7-image-pro" if kind == "image" else speech.model,
        media_kind=kind, media=[],
        scope=ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing"),
        credential_ref="credential", attempt_id="attempt",
        output_ref="assets/atelier/result.png" if kind == "image" else "audio/atelier/result.mp3",
    )


def test_reconstructs_only_frozen_settings_without_live_catalog(monkeypatch):
    from src.utils import model_catalog
    monkeypatch.setattr(model_catalog, "get_catalog_accessor", lambda: pytest.fail("live catalog consulted"))
    original = record()
    image = intents.image_intent_from_sync_record(original)
    assert image.to_dict() == original.intent
    assert image.params["seed"] == 0 and image.params["watermark"] is False
    with pytest.raises(TypeError):
        image.params["seed"] = 99
    audio = record("audio")
    speech = intents.prepared_tts_from_sync_record(audio)
    assert speech.to_snapshot() == audio.intent["prepared_tts"]
    assert speech.sdk_kwargs()["volume"] == 0


@pytest.mark.parametrize("kind,accessor", [("audio", "image_intent_from_sync_record"), ("image", "prepared_tts_from_sync_record")])
def test_wrong_media_kind_is_rejected(kind, accessor):
    with pytest.raises(ProviderRecoveryError):
        getattr(intents, accessor)(record(kind))


@pytest.mark.parametrize("accessor", ["image_intent_from_sync_record", "prepared_tts_from_sync_record"])
def test_changed_identity_is_rejected_before_reconstruction(accessor):
    source = record("audio" if accessor.startswith("prepared") else "image")
    source.intent["unexpected_tamper"] = True
    with pytest.raises(ProviderRecoveryError, match="fingerprint"):
        getattr(intents, accessor)(source)


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_proof_is_bound_to_frozen_output_and_cannot_change_after_prepare(kind):
    source = record_with_changes(record(kind), phase="submitting")
    proof = intents.sync_artifact_proof_from_record(
        source, relative_path=source.output_ref, sha256="a" * 64, size_bytes=123,
    )
    assert proof == {
        "schema_version": 2, "contract_version": "provider-sync-artifact-v1",
        "attempt_id": source.attempt_id, "media_kind": kind,
        "relative_path": source.output_ref, "output_ref": source.output_ref,
        "sha256": "a" * 64, "size_bytes": 123,
    }
    prepared = record_with_changes(source, prepared_artifact=proof)
    assert intents.sync_artifact_proof_from_record(
        prepared, relative_path=source.output_ref, sha256="a" * 64, size_bytes=123,
    ) == proof
    with pytest.raises(ProviderRecoveryError, match="changed"):
        intents.sync_artifact_proof_from_record(prepared, relative_path=source.output_ref, sha256="b" * 64, size_bytes=123)


@pytest.mark.parametrize("changes", [
    {"relative_path": "assets/atelier/other.png"}, {"relative_path": "../outside.png"},
    {"sha256": "not-a-hash"}, {"size_bytes": True}, {"size_bytes": 0},
])
def test_proof_rejects_invalid_or_different_artifact(changes):
    source = record_with_changes(record(), phase="submitting")
    args = dict(relative_path=source.output_ref, sha256="a" * 64, size_bytes=123)
    args.update(changes)
    with pytest.raises(ValueError):
        intents.sync_artifact_proof_from_record(source, **args)


def test_proof_requires_durable_submitting_phase_in_helper():
    accepted = record()
    with pytest.raises(ProviderRecoveryError, match="phase"):
        intents.sync_artifact_proof_from_record(accepted, relative_path=accepted.output_ref, sha256="a" * 64, size_bytes=123)


def test_persisted_accepted_record_cannot_already_contain_artifact_proof():
    accepted = record()
    proof = {
        "schema_version": 2, "contract_version": "provider-sync-artifact-v1",
        "attempt_id": accepted.attempt_id, "media_kind": "image",
        "relative_path": accepted.output_ref, "output_ref": accepted.output_ref,
        "sha256": "a" * 64, "size_bytes": 123,
    }
    with pytest.raises(ValueError, match="accepted"):
        record_with_changes(accepted, prepared_artifact=proof)
