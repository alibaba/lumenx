"""Kling image media temporary transport stays scope, model and bytes bound."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from src.models.provider_recovery import (
    ProviderRecoveryError,
    build_accepted_provider_submission_and_record,
    prepare_submission_transport,
)
from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ProviderSubmission,
    ResolvedCredential,
)
from src.models.provider_upload import (
    ProviderUploadError,
    ProviderUploadResult,
    upload_dashscope_temporary_media,
)
from src.utils.generation_intent import GenerationIntent
from src.utils.media_versions import MediaVersion
from tests.frozen_image_fixtures import rgb_png_bytes


I2V_MODEL = "kling/kling-v3-video-generation"
R2V_MODEL = "kling/kling-v3-omni-video-generation"
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://kling-space.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="kling-space",
)
CLASSIC_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
CREDENTIAL = ResolvedCredential(SCOPE, "credentials/kling", "test-only-secret")


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.closed = False

    def iter_content(self, chunk_size=8192):
        import json

        yield json.dumps(self.payload).encode("utf-8")

    def close(self):
        self.closed = True


def _version(data: bytes, role: str, ordinal: int, mime: str = "image/png") -> MediaVersion:
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


def _image(role: str, ordinal: int, *, size: int = 512, mime: str = "image/png") -> tuple[MediaVersion, bytes, PreparedMedia]:
    data = rgb_png_bytes(f"kling-transport-{role}-{ordinal}", width=size, height=size)
    version = _version(data, role, ordinal, mime)
    return version, data, PreparedMedia.from_verified_bytes(version, data, max_bytes=10 * 1024 * 1024)


def _intent(provider_model_id: str, **params) -> GenerationIntent:
    canonical = "kling/kling-v3-video#i2v" if provider_model_id == I2V_MODEL else "kling/kling-v3-video#r2v"
    mode = "i2v" if provider_model_id == I2V_MODEL else "r2v"
    return GenerationIntent(
        model_id=canonical,
        catalog_mode=mode,
        mode=mode,
        prompt="A verified Kling transport prompt.",
        params=params,
        sources={},
        request_fields=(),
    )


def _submission(provider_model_id: str, media: tuple[PreparedMedia, ...], **params) -> ProviderSubmission:
    return ProviderSubmission(
        intent=_intent(provider_model_id, **params),
        provider_model_id=provider_model_id,
        media=media,
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )


def _policy_payload(**overrides):
    data = {
        "policy": "policy-token",
        "signature": "signature-token",
        "upload_dir": "dashscope-temp/kling",
        "upload_host": "https://bucket.oss-cn-beijing.aliyuncs.com",
        "expire_in_seconds": 172800,
        "max_file_size_mb": 10,
        "capacity_limit_mb": 1024,
        "oss_access_key_id": "ak-token",
        "x_oss_object_acl": "private",
        "x_oss_forbid_overwrite": "true",
    }
    data.update(overrides)
    return {"data": data}


def _install_upload_transport(monkeypatch):
    captured = {"gets": [], "posts": []}

    def fake_get(url, **kwargs):
        captured["gets"].append((url, kwargs))
        return Response(200, _policy_payload())

    def fake_post(url, **kwargs):
        captured["posts"].append((url, kwargs))
        return Response(200, {})

    monkeypatch.setattr("src.models.provider_upload.requests.get", fake_get)
    monkeypatch.setattr("src.models.provider_upload.requests.post", fake_post)
    return captured


@pytest.mark.parametrize(
    ("provider_model_id", "role", "suffix"),
    [
        (I2V_MODEL, "first_frame", "png"),
        (R2V_MODEL, "reference_image", "png"),
    ],
)
def test_kling_upload_accepts_exact_workspace_image_roles(monkeypatch, provider_model_id, role, suffix):
    version, data, _ = _image(role, 0)
    monkeypatch.setattr("src.models.provider_upload._new_upload_nonce", lambda: "a" * 32)
    captured = _install_upload_transport(monkeypatch)

    result = upload_dashscope_temporary_media(
        scope=SCOPE,
        provider_model_id=provider_model_id,
        credential=CREDENTIAL,
        version=version,
        data=data,
    )

    assert result.url == f"oss://dashscope-temp/kling/{role}-0-{version.sha256[:16]}-{'a' * 32}.{suffix}"
    assert result.provider_model_id == provider_model_id
    assert result.scope == SCOPE
    assert result.credential_ref == CREDENTIAL.credential_ref
    assert captured["gets"][0][1]["params"] == {"action": "getPolicy", "model": provider_model_id}
    fields = captured["posts"][0][1]["files"]
    assert fields[-1][1] == (result.key.rsplit("/", 1)[-1], data, "image/png")


@pytest.mark.parametrize(
    ("provider_model_id", "scope", "credential", "role", "mime"),
    [
        (I2V_MODEL, CLASSIC_SCOPE, replace(CREDENTIAL, scope=CLASSIC_SCOPE), "first_frame", "image/png"),
        (I2V_MODEL, SCOPE, CREDENTIAL, "reference_image", "image/png"),
        (R2V_MODEL, SCOPE, CREDENTIAL, "first_frame", "image/png"),
        (R2V_MODEL, SCOPE, CREDENTIAL, "reference_image", "image/webp"),
        ("kling-v3-i2v", SCOPE, CREDENTIAL, "first_frame", "image/png"),
    ],
)
def test_kling_upload_rejects_wrong_scope_id_role_or_mime_before_http(
    monkeypatch,
    provider_model_id,
    scope,
    credential,
    role,
    mime,
):
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("src.models.provider_upload.requests.post", lambda *args, **kwargs: calls.append((args, kwargs)))
    data = rgb_png_bytes("bad-kling-upload", width=512, height=512)
    version = _version(data, role, 0, mime)

    with pytest.raises(ProviderUploadError):
        upload_dashscope_temporary_media(
            scope=scope,
            provider_model_id=provider_model_id,
            credential=credential,
            version=version,
            data=data,
        )

    assert calls == []


def test_provider_image_url_factory_verifies_typed_result_bytes_and_metadata():
    version, data, _ = _image("first_frame", 0)
    result = ProviderUploadResult(
        url="oss://dashscope-temp/kling/first.png",
        key="dashscope-temp/kling/first.png",
        provider_model_id=I2V_MODEL,
        version=version,
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )

    prepared = PreparedMedia.from_verified_provider_image_url(version, upload_result=result, data=data)

    assert prepared.url == result.url
    assert prepared.transport_binding == "dashscope_oss_sha256_v1"
    assert prepared.frozen_bytes is None
    assert prepared.frozen_metadata is None
    assert prepared.frozen_image_metadata is not None
    assert prepared.frozen_image_metadata.width == 512
    assert prepared.frozen_image_metadata.has_alpha is False

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_image_url(version, upload_result=result, data=b"changed")
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_image_url(
            version,
            upload_result=replace(result, version=_version(data, "reference_image", 0)),
            data=data,
        )


def test_prepare_submission_transport_uploads_kling_images_without_changing_accepted_record():
    first_version, first_data, first = _image("reference_image", 0)
    second_version, second_data, second = _image("reference_image", 1)
    submission, record = build_accepted_provider_submission_and_record(
        _intent(R2V_MODEL, duration=5, aspect_ratio="16:9"),
        provider_model_id=R2V_MODEL,
        media=(first, second),
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
        attempt_id="attempt-kling-r2v",
    )
    upload_calls = []

    def uploader(**kwargs):
        upload_calls.append(kwargs)
        version = kwargs["version"]
        return ProviderUploadResult(
            url=f"oss://dashscope-temp/kling/{version.role}-{version.ordinal}.png",
            key=f"dashscope-temp/kling/{version.role}-{version.ordinal}.png",
            provider_model_id=kwargs["provider_model_id"],
            version=version,
            scope=kwargs["scope"],
            credential_ref=kwargs["credential"].credential_ref,
        )

    prepared = prepare_submission_transport(submission, CREDENTIAL, uploader=uploader)

    assert [(item["sha256"], item["role"], item["ordinal"]) for item in record.media] == [
        (first_version.sha256, "reference_image", 0),
        (second_version.sha256, "reference_image", 1),
    ]
    assert [call["data"] for call in upload_calls] == [first_data, second_data]
    assert [item.url for item in prepared.media] == [
        "oss://dashscope-temp/kling/reference_image-0.png",
        "oss://dashscope-temp/kling/reference_image-1.png",
    ]
    assert all(item.transport_binding == "dashscope_oss_sha256_v1" for item in prepared.media)
    assert all(item.frozen_image_metadata is not None for item in prepared.media)


def test_prepare_submission_transport_leaves_other_data_uri_models_unchanged():
    _, _, first = _image("first_frame", 0)
    submission = ProviderSubmission(
        intent=GenerationIntent(
            model_id="happyhorse/happyhorse-1.1-video#i2v",
            catalog_mode="i2v",
            mode="i2v",
            prompt="HappyHorse keeps data URI transport.",
            params={"duration": 5, "resolution": "1080P"},
            sources={},
            request_fields=(),
        ),
        provider_model_id="happyhorse-1.1-i2v",
        media=(first,),
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )

    prepared = prepare_submission_transport(submission, CREDENTIAL, uploader=lambda **_: pytest.fail("no upload expected"))

    assert prepared.media == (first,)


def test_prepare_submission_transport_rejects_upload_mismatch_before_submit():
    version, _, first = _image("first_frame", 0)
    submission = _submission(I2V_MODEL, (first,), mode="pro", duration=5)

    def bad_uploader(**kwargs):
        return ProviderUploadResult(
            url="oss://dashscope-temp/kling/wrong.png",
            key="dashscope-temp/kling/wrong.png",
            provider_model_id=R2V_MODEL,
            version=version,
            scope=kwargs["scope"],
            credential_ref=kwargs["credential"].credential_ref,
        )

    with pytest.raises(ProviderRecoveryError):
        prepare_submission_transport(submission, CREDENTIAL, uploader=bad_uploader)
