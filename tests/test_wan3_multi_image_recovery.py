"""Wan3 durable submissions support explicit zero and ordered multi-image inputs."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import (
    MAX_PROVIDER_MEDIA_BYTES,
    ProviderRecoveryError,
    _fingerprint,
    build_accepted_provider_submission_and_record,
    reconstruct_submission_from_record,
    record_with_changes,
)
from src.models.provider_tasks import PreparedMedia, ProviderBindingError, ProviderScope
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import DirectoryWriterLock
from src.utils.media_versions import MediaVersion, freeze_media
from tests import test_provider_submit as fixtures
from tests.frozen_image_fixtures import rgb_png_bytes


contract = fixtures.contract
scope = fixtures.scope
credential = fixtures.credential
model = fixtures.model
submission = fixtures.submission
transport = fixtures.transport


WAN3_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://original-workspace.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="original-workspace",
)


def _intent(mode: str = "i2v", *, prompt: str = "A slow sunrise", **params) -> GenerationIntent:
    return GenerationIntent(
        model_id="wan/wan3.0-video#video",
        catalog_mode="video",
        mode=mode,
        prompt=prompt,
        params={
            "duration": 5,
            "resolution": "1080P",
            "ratio": "adaptive",
            "prompt_extend": True,
            "watermark": False,
            "audio": True,
            **params,
        },
        sources={},
        request_fields=(),
    )


def _prepared_image(role: str, ordinal: int = 0, *, data: bytes | None = None) -> PreparedMedia:
    payload = data if data is not None else rgb_png_bytes(f"fixture-{role}-{ordinal}")
    version = MediaVersion(hashlib.sha256(payload).hexdigest(), len(payload), role, ordinal, "image/png")
    return PreparedMedia.from_verified_bytes(version, payload, max_bytes=1024)


def _wan3_request(base, mode: str = "i2v", *, media=(), prompt="A slow sunrise", **params):
    return replace(
        base,
        intent=_intent(mode, prompt=prompt, **params),
        provider_model_id="wan3.0-video",
        scope=WAN3_SCOPE,
        media=tuple(media),
    )


def _bound_credential(request, credential):
    return replace(credential, scope=request.scope)


def _write_upload(root: Path, name: str, data: bytes) -> None:
    path = root / "uploads" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)


def _freeze(root: Path, lease: DirectoryWriterLock, name: str, role: str, ordinal: int, data: bytes) -> PreparedMedia:
    _write_upload(root, name, data)
    version = freeze_media(
        f"uploads/{name}",
        output_root=root,
        writer_lock=lease,
        role=role,
        ordinal=ordinal,
        max_bytes=MAX_PROVIDER_MEDIA_BYTES,
    )
    return PreparedMedia.from_media_version(version, output_root=root, max_bytes=MAX_PROVIDER_MEDIA_BYTES)


def test_wan3_t2v_accepts_no_media_and_requires_non_empty_prompt(
    submission, model, credential, transport,
):
    request = _wan3_request(submission, "t2v", media=(), prompt="Text only scene", seed=0)
    assert WanxModel.validate_submission(request) is None
    model.submit(request, credential=_bound_credential(request, credential), on_submitted=lambda _: None)
    payload = transport[1][0][1]["json"]
    assert payload["input"] == {"prompt": "Text only scene"}
    assert payload["parameters"]["seed"] == 0

    empty = _wan3_request(submission, "t2v", media=(), prompt="")
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(empty)


def test_wan3_i2v_accepts_first_and_last_frame_in_order(
    submission, model, credential, transport,
):
    media = (_prepared_image("first_frame"), _prepared_image("last_frame"))
    request = _wan3_request(submission, "i2v", media=media, duration=-1)
    assert WanxModel.validate_submission(request) is None
    model.submit(request, credential=_bound_credential(request, credential), on_submitted=lambda _: None)
    payload_media = transport[1][0][1]["json"]["input"]["media"]
    assert payload_media == [
        {"type": "first_frame", "url": media[0].url},
        {"type": "last_frame", "url": media[1].url},
    ]


@pytest.mark.parametrize("prompt", ["", " ", "\t\n", "\u3000"])
def test_wan3_blank_t2v_prompt_never_submits(submission, model, credential, transport, prompt):
    request = _wan3_request(submission, "t2v", prompt=prompt)
    with pytest.raises(ProviderBindingError):
        model.submit(request, credential=_bound_credential(request, credential), on_submitted=lambda _: None)
    assert transport[1] == []


def test_provider_recovery_round_trips_text_only_record_without_media_files(tmp_path):
    intent = _intent("t2v", prompt="  Keep intentional spacing.  ")
    accepted, record = build_accepted_provider_submission_and_record(
        intent,
        provider_model_id="wan3.0-video",
        media=(),
        scope=WAN3_SCOPE,
        credential_ref="credentials/original",
        attempt_id="attempt-text-only",
    )
    persisted = ProviderExecutionRecord.model_validate_json(record.model_dump_json())
    assert persisted.media == []
    assert reconstruct_submission_from_record(persisted, output_root=tmp_path) == accepted
    assert accepted.intent.prompt == "  Keep intentional spacing.  "


def test_wan3_r2v_accepts_one_to_ten_reference_images(submission):
    one = _wan3_request(submission, "r2v", media=(_prepared_image("reference_image"),))
    assert WanxModel.validate_submission(one) is None
    ten_media = tuple(_prepared_image("reference_image", index) for index in range(10))
    assert WanxModel.validate_submission(_wan3_request(submission, "r2v", media=ten_media)) is None
    eleven_media = tuple(_prepared_image("reference_image", index) for index in range(11))
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_wan3_request(submission, "r2v", media=eleven_media))


@pytest.mark.parametrize(
    ("mode", "media"),
    [
        ("t2v", (_prepared_image("first_frame"),)),
        ("i2v", ()),
        ("i2v", (_prepared_image("last_frame"),)),
        ("i2v", (_prepared_image("first_frame"), _prepared_image("reference_image"))),
        ("r2v", ()),
        ("r2v", (_prepared_image("reference_image", 1),)),
        ("r2v", (_prepared_image("reference_image", 0), _prepared_image("reference_image", 0, data=rgb_png_bytes("two")))),
        ("r2v", (_prepared_image("reference_image", 0), _prepared_image("reference_image", 2))),
    ],
)
def test_wan3_rejects_invalid_media_matrix_before_post(
    submission, model, credential, transport, mode, media,
):
    request = _wan3_request(submission, mode, media=media)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)
    with pytest.raises(ProviderBindingError):
        model.submit(request, credential=_bound_credential(request, credential), on_submitted=lambda _: None)
    assert transport[1] == []


def test_wan3_allows_same_hash_under_different_roles(submission):
    data = rgb_png_bytes("shared image")
    media = (
        _prepared_image("first_frame", data=data),
        _prepared_image("last_frame", data=data),
    )
    assert media[0].version.sha256 == media[1].version.sha256
    assert WanxModel.validate_submission(_wan3_request(submission, "i2v", media=media)) is None


def test_provider_recovery_accepts_explicit_ordered_media_and_rebuilds_from_record_bytes(
    tmp_path, submission, model, credential, transport, monkeypatch,
):
    lease = DirectoryWriterLock(tmp_path).acquire()
    try:
        first = _freeze(tmp_path, lease, "first.png", "first_frame", 0, rgb_png_bytes("first"))
        last = _freeze(tmp_path, lease, "last.png", "last_frame", 0, rgb_png_bytes("last"))
        request = _wan3_request(submission, "i2v", media=(first, last), seed=-1)
        accepted, record = build_accepted_provider_submission_and_record(
            request.intent,
            provider_model_id=request.provider_model_id,
            media=(first, last),
            scope=request.scope,
            credential_ref="credentials/original",
            attempt_id="attempt-wan3-n",
        )
        persisted = ProviderExecutionRecord.model_validate_json(record.model_dump_json())
        (tmp_path / "uploads" / "first.png").write_bytes(b"mutated bytes")
        (tmp_path / "uploads" / "last.png").unlink()
        monkeypatch.setattr(
            "src.utils.generation_intent.get_catalog_accessor",
            lambda: pytest.fail("catalog reread"),
        )
        recovered = reconstruct_submission_from_record(persisted, output_root=tmp_path)
        assert recovered == accepted
        assert [item.version.role for item in recovered.media] == ["first_frame", "last_frame"]
        model.submit(
            recovered,
            credential=replace(credential, scope=request.scope, credential_ref="credentials/original"),
            on_submitted=lambda _: None,
        )
        payload_media = transport[1][0][1]["json"]["input"]["media"]
        assert payload_media == [
            {"type": "first_frame", "url": first.url},
            {"type": "last_frame", "url": last.url},
        ]
    finally:
        lease.close()


def test_provider_recovery_rejects_mixed_old_and_new_media_arguments(tmp_path):
    lease = DirectoryWriterLock(tmp_path).acquire()
    try:
        first = _freeze(tmp_path, lease, "first.png", "first_frame", 0, rgb_png_bytes("first"))
        with pytest.raises(ProviderRecoveryError, match="provider_media_arguments_ambiguous"):
            build_accepted_provider_submission_and_record(
                _intent("i2v"),
                provider_model_id="wan3.0-video",
                media_version=first.version,
                prepared_media=first,
                media=(first,),
                scope=WAN3_SCOPE,
                credential_ref="credentials/original",
                attempt_id="attempt-mixed",
            )
    finally:
        lease.close()


def test_provider_recovery_detects_media_order_hash_and_role_tampering(tmp_path, submission):
    lease = DirectoryWriterLock(tmp_path).acquire()
    try:
        first = _freeze(tmp_path, lease, "first.png", "first_frame", 0, rgb_png_bytes("first"))
        last = _freeze(tmp_path, lease, "last.png", "last_frame", 0, rgb_png_bytes("last"))
        request = _wan3_request(submission, "i2v", media=(first, last))
        _, record = build_accepted_provider_submission_and_record(
            request.intent,
            provider_model_id=request.provider_model_id,
            media=(first, last),
            scope=request.scope,
            credential_ref="credentials/original",
            attempt_id="attempt-tamper",
        )
        with pytest.raises(ProviderRecoveryError, match="provider_execution_fingerprint_mismatch"):
            reconstruct_submission_from_record(
                record_with_changes(record, media=list(reversed(record.media))),
                output_root=tmp_path,
            )

        bad_hash_media = [dict(item) for item in record.media]
        bad_hash_media[0]["sha256"] = "f" * 64
        bad_hash_identity = {
            "attempt_id": record.attempt_id,
            "provider_model_id": record.provider_model_id,
            "intent": record.intent,
            "media": bad_hash_media,
            "scope": record.scope,
            "credential_ref": record.credential_ref,
        }
        with pytest.raises((ProviderRecoveryError, ValueError)):
            reconstruct_submission_from_record(
                record_with_changes(record, media=bad_hash_media, fingerprint=_fingerprint(bad_hash_identity)),
                output_root=tmp_path,
            )

        bad_role_media = [dict(item) for item in record.media]
        bad_role_media[1]["role"] = "reference_image"
        bad_role_identity = {
            "attempt_id": record.attempt_id,
            "provider_model_id": record.provider_model_id,
            "intent": record.intent,
            "media": bad_role_media,
            "scope": record.scope,
            "credential_ref": record.credential_ref,
        }
        with pytest.raises(ProviderRecoveryError):
            reconstruct_submission_from_record(
                record_with_changes(record, media=bad_role_media, fingerprint=_fingerprint(bad_role_identity)),
                output_root=tmp_path,
            )
    finally:
        lease.close()
