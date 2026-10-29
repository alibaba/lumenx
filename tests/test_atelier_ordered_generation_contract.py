"""Ordered-media Atelier generation schema foundation stays separate from legacy I2V."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.apps.atelier.generation_commands import (
    ATELIER_ORDERED_VIDEO_ENTRYPOINT,
    ORDERED_MEDIA_CONTRACT_VERSION,
    CreateVideoCandidateCommand,
    OrderedVideoCandidateCommand,
    fingerprint_ordered_video_candidate_request,
    fingerprint_video_candidate_request,
    generation_entrypoint_for_command,
    normalize_ordered_video_candidate_request,
)
from src.apps.atelier.generation_ledger import validate_generation_ledger
from src.apps.atelier.models import (
    AtelierGenerationAttempt,
    AtelierGenerationRequestReceipt,
    AtelierGenerationMediaSource,
    AtelierNode,
    AtelierProject,
)
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import _fingerprint


HASH = "a" * 64
SCOPE = {
    "provider": "dashscope",
    "backend": "dashscope",
    "gateway": "dashscope_workspace",
    "base_url": "https://original-workspace.cn-beijing.maas.aliyuncs.com",
    "region": "cn-beijing",
    "workspace_id": "original-workspace",
    "account_identity": None,
}


def legacy_command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "legacy-request",
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"},
        "params": {"duration": 5, "watermark": False},
    }
    payload.update(changes)
    return CreateVideoCandidateCommand(**payload)


def ordered_command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "ordered-request",
        "contract_version": ORDERED_MEDIA_CONTRACT_VERSION,
        "model_id": "wan/wan3.0-video#video",
        "mode": "t2v",
        "prompt": "Text scene",
        "media": [],
        "params": {"duration": 5, "resolution": "1080P", "audio": True},
    }
    payload.update(changes)
    return OrderedVideoCandidateCommand(**payload)


def intent_dict(mode="t2v", media=()):
    return {
        "rules_version": "generation-parameters-v1",
        "scope": "parameters-only",
        "model_id": "wan/wan3.0-video#video",
        "catalog_mode": "video",
        "mode": mode,
        "prompt": "Text scene",
        "params": {"duration": 5, "resolution": "1080P", "audio": True},
        "sources": {"model_id": "request", "mode": "request", "prompt": "request"},
        "request_fields": ["mode", "model_id", "prompt"],
        "preset_id": None,
        "preset_version": None,
    }


def provider_record(attempt_id="attempt-1", *, mode="t2v", media=()):
    identity = {
        "attempt_id": attempt_id,
        "provider_model_id": "wan3.0-video",
        "intent": intent_dict(mode=mode, media=media),
        "media": list(media),
        "scope": SCOPE,
        "credential_ref": "credentials/original",
    }
    return ProviderExecutionRecord(
        schema_version=1,
        phase="accepted",
        **identity,
        fingerprint=_fingerprint(identity),
    )


def project_with_attempt(attempt: AtelierGenerationAttempt, *, entrypoint=ATELIER_ORDERED_VIDEO_ENTRYPOINT) -> AtelierProject:
    candidate = {
        "execution_contract": "atelier-provider-video-v1",
        "id": attempt.candidate_id,
        "attempt_id": attempt.attempt_id,
        "media_kind": "video",
        "status": attempt.status,
        "video_url": None,
        "error_code": None,
        "created_at": attempt.created_at,
        "updated_at": attempt.updated_at,
    }
    return AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        revision=1,
        nodes=[AtelierNode(id="video", project_id="p", type="video", data={"candidates": [candidate]})],
        generation_attempts=[attempt],
        generation_request_receipts=[AtelierGenerationRequestReceipt(
            project_id="p",
            entrypoint=entrypoint,
            request_contract_version="request-json-v1",
            client_request_id="ordered-request",
            request_fingerprint="b" * 64,
            node_id="video",
            candidate_id=attempt.candidate_id,
            attempt_id=attempt.attempt_id,
            execution_fingerprint=attempt.provider_execution.fingerprint,
            base_revision=0,
            committed_revision=1,
            created_at=10.0,
        )],
    )


def ordered_attempt(*, input_media, record_media, mode="t2v") -> AtelierGenerationAttempt:
    return AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id="candidate-1",
        attempt_id="attempt-1",
        attempt_revision=0,
        status="pending",
        input_contract_version=ORDERED_MEDIA_CONTRACT_VERSION,
        input_media=input_media,
        provider_execution=provider_record(mode=mode, media=record_media),
        created_at=10.0,
        updated_at=10.0,
    )


def test_legacy_fingerprint_and_serialized_wire_remain_byte_equivalent():
    command = legacy_command()
    assert fingerprint_video_candidate_request(command, node_id="video") == "fa10869c4b567ce1922f9c37c2a767d0f3f6a008789ac1aa16b723cb50f430b3"
    assert generation_entrypoint_for_command(command) == "atelier.provider_video.v1"
    dumped = command.model_dump(mode="json", exclude_unset=True)
    assert dumped == {
        "expected_revision": 0,
        "client_request_id": "legacy-request",
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"},
        "params": {"duration": 5, "watermark": False},
    }


def test_ordered_t2v_command_is_separate_and_normalizes_without_media():
    command = ordered_command()
    assert command.contract_version == ORDERED_MEDIA_CONTRACT_VERSION
    assert generation_entrypoint_for_command(command) == ATELIER_ORDERED_VIDEO_ENTRYPOINT
    assert command.media == ()
    fingerprint = fingerprint_ordered_video_candidate_request(command, node_id="video")
    assert len(fingerprint) == 64 and fingerprint != fingerprint_video_candidate_request(legacy_command(), node_id="video")
    intent = normalize_ordered_video_candidate_request(command)
    assert intent.model_id == "wan/wan3.0-video#video"
    assert intent.mode == "t2v"
    assert intent.params["audio"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"first_frame": {"node_id": "image", "media_url": "uploads/frame.png"}},
        {"contract_version": None},
        {"__drop_media__": True},
        {"media": None},
        {"model_id": "wan/wan2.7-video#i2v"},
        {"mode": "t2v", "prompt": "   "},
        {"mode": "t2v", "media": [{"role": "first_frame", "ordinal": 0, "node_id": "image", "media_url": "uploads/frame.png"}]},
        {"mode": "i2v", "media": []},
        {"mode": "i2v", "media": [{"role": "last_frame", "ordinal": 0, "node_id": "image", "media_url": "uploads/last.png"}]},
        {"mode": "r2v", "media": []},
        {"mode": "r2v", "media": [
            {"role": "reference_image", "ordinal": 1, "node_id": "image", "media_url": "uploads/a.png"},
        ]},
        {"mode": "r2v", "media": [
            {"role": "reference_image", "ordinal": 0, "node_id": "image", "media_url": "uploads/a.png"},
            {"role": "reference_image", "ordinal": 0, "node_id": "image2", "media_url": "uploads/b.png"},
        ]},
        {"mode": "r2v", "media": [{"role": "reference_audio", "ordinal": 0, "node_id": " ", "media_url": "uploads/a.wav"}]},
        {"mode": "r2v", "media": [
            {"role": "reference_image", "ordinal": index, "node_id": f"image-{index}", "media_url": f"uploads/{index}.png"}
            for index in range(11)
        ]},
        {"mode": "r2v", "media": [
            {"role": "reference_video", "ordinal": index, "node_id": f"video-{index}", "media_url": f"uploads/{index}.mp4"}
            for index in range(6)
        ]},
        {"mode": "r2v", "media": [
            {"role": "reference_audio", "ordinal": index, "node_id": f"audio-{index}", "media_url": f"uploads/{index}.wav"}
            for index in range(6)
        ]},
        {"mode": "r2v", "media": [
            {"role": "reference_audio", "ordinal": 0, "node_id": "audio", "media_url": "uploads/a.wav"},
            {"role": "reference_image", "ordinal": 0, "node_id": "image", "media_url": "uploads/a.png"},
        ]},
    ],
)
def test_ordered_command_rejects_ambiguous_or_invalid_media_contract(payload):
    data = ordered_command().model_dump(mode="json")
    if payload.pop("__drop_media__", False):
        data.pop("media")
    data.update(payload)
    with pytest.raises(ValidationError):
        OrderedVideoCandidateCommand(**data)


def test_ordered_normalization_uses_catalog_defaults_and_request_provenance():
    intent = normalize_ordered_video_candidate_request(ordered_command(params={}))
    assert intent.params == {
        "audio": True,
        "duration": 5,
        "prompt_extend": True,
        "ratio": "adaptive",
        "resolution": "1080P",
        "watermark": False,
    }
    assert intent.sources["params.duration"] == "catalog"
    assert intent.sources["params.audio"] == "catalog"
    assert "params.seed" not in intent.sources

    explicit = normalize_ordered_video_candidate_request(ordered_command(params={
        "audio": False,
        "duration": -1,
        "prompt_extend": False,
        "seed": 0,
        "watermark": False,
    }))
    assert explicit.params["audio"] is False
    assert explicit.params["duration"] == -1
    assert explicit.params["prompt_extend"] is False
    assert explicit.params["seed"] == 0
    assert explicit.params["watermark"] is False
    for name in ("audio", "duration", "prompt_extend", "seed", "watermark"):
        assert explicit.sources[f"params.{name}"] == "request"
        assert f"params.{name}" in explicit.request_fields


@pytest.mark.parametrize("params", [{"duration": True}, {"duration": float("nan")}, {"ratio": "2:1"}])
def test_ordered_normalization_rejects_invalid_catalog_parameters(params):
    with pytest.raises(ValueError):
        normalize_ordered_video_candidate_request(ordered_command(params=params))


def test_ordered_attempt_serializes_without_legacy_first_frame_and_legacy_stays_unchanged():
    source = AtelierGenerationMediaSource(role="reference_image", ordinal=0, node_id="image", media_url="uploads/a.png")
    media = [{"sha256": "a" * 64, "size_bytes": 10, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png", "schema_version": 1}]
    attempt = ordered_attempt(input_media=[source], record_media=media, mode="r2v")
    payload = attempt.model_dump(mode="json")
    assert payload["input_contract_version"] == ORDERED_MEDIA_CONTRACT_VERSION
    assert payload["input_media"] == [{"role": "reference_image", "ordinal": 0, "node_id": "image", "media_url": "uploads/a.png"}]
    assert "first_frame_source" not in payload

    invalid = attempt.model_dump(mode="json")
    invalid["first_frame_source"] = {"node_id": "image", "media_url": "uploads/a.png"}
    with pytest.raises(ValidationError):
        AtelierGenerationAttempt(**invalid)

    legacy = AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id="candidate-old",
        attempt_id="attempt-old",
        attempt_revision=0,
        status="pending",
        first_frame_source={"node_id": "image", "media_url": "uploads/frame.png"},
        provider_execution=provider_record("attempt-old", media=media, mode="i2v"),
        created_at=10.0,
        updated_at=10.0,
    )
    legacy_payload = legacy.model_dump(mode="json")
    assert "input_contract_version" not in legacy_payload
    assert "input_media" not in legacy_payload
    assert legacy_payload["first_frame_source"] == {"node_id": "image", "media_url": "uploads/frame.png"}


def test_ordered_attempt_rejects_noncanonical_role_order():
    image = AtelierGenerationMediaSource(role="reference_image", ordinal=0, node_id="image", media_url="uploads/a.png")
    audio = AtelierGenerationMediaSource(role="reference_audio", ordinal=0, node_id="audio", media_url="uploads/a.wav")
    media = [
        {"sha256": "a" * 64, "size_bytes": 10, "role": "reference_audio", "ordinal": 0, "declared_media_type": "audio/wav", "schema_version": 1},
        {"sha256": "b" * 64, "size_bytes": 10, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png", "schema_version": 1},
    ]

    with pytest.raises(ValidationError):
        ordered_attempt(input_media=[audio, image], record_media=media, mode="r2v")


def test_ledger_accepts_zero_media_t2v_and_rejects_ordered_source_tampering():
    attempt = ordered_attempt(input_media=[], record_media=[], mode="t2v")
    validate_generation_ledger(project_with_attempt(attempt), ValueError)

    source = AtelierGenerationMediaSource(role="reference_image", ordinal=0, node_id="image", media_url="uploads/a.png")
    media = [{"sha256": "a" * 64, "size_bytes": 10, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png", "schema_version": 1}]
    validate_generation_ledger(project_with_attempt(ordered_attempt(input_media=[source], record_media=media, mode="r2v")), ValueError)

    tampered = deepcopy(media)
    tampered[0]["role"] = "reference_audio"
    with pytest.raises(ValueError):
        validate_generation_ledger(project_with_attempt(ordered_attempt(input_media=[source], record_media=tampered, mode="r2v")), ValueError)


    image = AtelierGenerationMediaSource(role="reference_image", ordinal=0, node_id="image", media_url="uploads/a.png")
    audio = AtelierGenerationMediaSource(role="reference_audio", ordinal=0, node_id="audio", media_url="uploads/a.wav")
    canonical_media = [
        {"sha256": "a" * 64, "size_bytes": 10, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png", "schema_version": 1},
        {"sha256": "b" * 64, "size_bytes": 10, "role": "reference_audio", "ordinal": 0, "declared_media_type": "audio/wav", "schema_version": 1},
    ]
    out_of_order_attempt = ordered_attempt(input_media=[image, audio], record_media=canonical_media, mode="r2v")
    out_of_order_attempt.input_media = [audio, image]
    out_of_order_attempt.provider_execution = provider_record(mode="r2v", media=[canonical_media[1], canonical_media[0]])
    with pytest.raises(ValueError):
        validate_generation_ledger(project_with_attempt(out_of_order_attempt), ValueError)

    with pytest.raises(ValidationError):
        ordered_attempt(input_media=None, record_media=[], mode="t2v")

    with pytest.raises(ValueError):
        validate_generation_ledger(project_with_attempt(attempt, entrypoint="atelier.provider_video.v1"), ValueError)

    legacy = AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id="candidate-old",
        attempt_id="attempt-old",
        attempt_revision=0,
        status="pending",
        first_frame_source={"node_id": "image", "media_url": "uploads/frame.png"},
        provider_execution=provider_record("attempt-old", media=media, mode="i2v"),
        created_at=10.0,
        updated_at=10.0,
    )
    with pytest.raises(ValueError):
        validate_generation_ledger(project_with_attempt(legacy, entrypoint=ATELIER_ORDERED_VIDEO_ENTRYPOINT), ValueError)


def test_ledger_rejects_duplicate_request_id_across_legacy_and_ordered_entrypoints():
    ordered = ordered_attempt(input_media=[], record_media=[], mode="t2v")
    legacy = AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id="candidate-old",
        attempt_id="attempt-old",
        attempt_revision=0,
        status="pending",
        first_frame_source={"node_id": "image", "media_url": "uploads/frame.png"},
        provider_execution=provider_record("attempt-old", media=[], mode="i2v"),
        created_at=11.0,
        updated_at=11.0,
    )
    project = project_with_attempt(ordered)
    project.generation_attempts.append(legacy)
    project.nodes[0].data["candidates"].append({
        "execution_contract": "atelier-provider-video-v1",
        "id": legacy.candidate_id,
        "attempt_id": legacy.attempt_id,
        "media_kind": "video",
        "status": legacy.status,
        "video_url": None,
        "error_code": None,
        "created_at": legacy.created_at,
        "updated_at": legacy.updated_at,
    })
    project.generation_request_receipts.append(AtelierGenerationRequestReceipt(
        project_id="p",
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="ordered-request",
        request_fingerprint="c" * 64,
        node_id="video",
        candidate_id=legacy.candidate_id,
        attempt_id=legacy.attempt_id,
        execution_fingerprint=legacy.provider_execution.fingerprint,
        base_revision=0,
        committed_revision=1,
        created_at=11.0,
    ))
    with pytest.raises(ValueError):
        validate_generation_ledger(project, ValueError)
