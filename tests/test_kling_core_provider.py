"""Kling workspace provider submission contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from src.models.kling_core_contract import (
    validate_kling_image_metadata,
    validate_kling_intent,
)
from src.models.provider_recovery import ProviderRecoveryError, prepare_submission_transport
from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ProviderSubmission,
    ResolvedCredential,
)
from src.models.provider_upload import ProviderUploadResult
from src.models.provider_runtime import build_provider_runtime_for_admission
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.media_probe import FrozenImageMetadata
from src.utils.media_versions import MediaVersion
from src.utils.runtime_environment import RuntimeEnvironment
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import png_bytes


SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://kling-space.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="kling-space",
)
CREDENTIAL = ResolvedCredential(SCOPE, "credentials/kling", "test-only-secret")
CANONICAL_BY_PROVIDER = {
    "kling/kling-v3-video-generation": ("kling/kling-v3-video#i2v", "i2v"),
    "kling/kling-v3-omni-video-generation": ("kling/kling-v3-video#r2v", "r2v"),
}


def _intent(provider_model_id: str, *, prompt: str = "A cinematic test prompt.", **params):
    canonical, mode = CANONICAL_BY_PROVIDER[provider_model_id]
    return GenerationIntent(
        model_id=canonical,
        catalog_mode=mode,
        mode=mode,
        prompt=prompt,
        params=params,
        sources={},
        request_fields=(),
    )


def _version(data: bytes, role: str, ordinal: int, mime: str = "image/png") -> MediaVersion:
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


def _data_uri_image(
    role: str,
    ordinal: int,
    *,
    width: int = 400,
    height: int = 400,
    alpha: bool = False,
    mime: str = "image/png",
) -> PreparedMedia:
    data = png_bytes(width, height, alpha=alpha) if alpha else rgb_png_bytes(
        f"kling-{role}-{ordinal}-{width}x{height}",
        width=width,
        height=height,
    )
    return PreparedMedia.from_verified_bytes(
        _version(data, role, ordinal, mime),
        data,
        max_bytes=10 * 1024 * 1024,
    )


def _provider_image(role: str, ordinal: int, url: str) -> PreparedMedia:
    data = rgb_png_bytes(f"kling-provider-{role}-{ordinal}", width=512, height=512)
    prepared = object.__new__(PreparedMedia)
    object.__setattr__(prepared, "version", _version(data, role, ordinal))
    object.__setattr__(prepared, "url", url)
    object.__setattr__(prepared, "transport_binding", "dashscope_oss_sha256_v1")
    object.__setattr__(prepared, "frozen_bytes", None)
    object.__setattr__(prepared, "frozen_metadata", None)
    object.__setattr__(
        prepared,
        "frozen_image_metadata",
        FrozenImageMetadata(kind="image", width=512, height=512, has_alpha=False),
    )
    return prepared


def _provider_upload_result(
    version: MediaVersion,
    *,
    key: str = "dashscope-instant/kling/verified.png",
) -> ProviderUploadResult:
    return ProviderUploadResult(
        url=f"oss://{key}",
        key=key,
        provider_model_id="kling/kling-v3-video-generation",
        version=version,
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )


def _submission(
    provider_model_id: str,
    *,
    media: tuple[PreparedMedia, ...],
    **params,
) -> ProviderSubmission:
    return ProviderSubmission(
        intent=_intent(provider_model_id, **params),
        provider_model_id=provider_model_id,
        media=media,
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )


def test_kling_i2v_validates_data_uri_but_submit_requires_provider_oss(monkeypatch):
    first_frame = _data_uri_image("first_frame", 0, width=300, height=750)
    request = _submission(
        "kling/kling-v3-video-generation",
        media=(first_frame,),
        mode="4k",
        duration=15,
        audio=False,
        watermark=True,
        negative_prompt="low detail",
    )

    assert validate_kling_intent(request.intent) == {
        "mode": "4k",
        "duration": 15,
        "audio": False,
        "watermark": True,
        "negative_prompt": "low detail",
    }
    assert WanxModel.validate_submission(request) is None
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *_args, **_kwargs: pytest.fail("data URI Kling submit must not reach network"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel({"output_root": "/tmp"}).submit(
            request,
            credential=CREDENTIAL,
            on_submitted=lambda _: None,
        )


def test_kling_i2v_submit_posts_exact_workspace_payload_and_resolve_header(monkeypatch):
    first_frame = _provider_image("first_frame", 0, "oss://dashscope-instant/kling/first.png")
    request = _submission(
        "kling/kling-v3-video-generation",
        media=(first_frame,),
        mode="std",
        duration=3,
        audio=False,
        watermark=False,
    )
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-kling", "output": {"task_id": "task-kling"}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("src.models.wanx.requests.post", post)

    handle = WanxModel({"output_root": "/tmp"}).submit(
        request,
        credential=CREDENTIAL,
        on_submitted=lambda _: None,
    )

    assert handle.task_id == "task-kling"
    assert len(calls) == 1
    url, options = calls[0]
    assert url == "https://kling-space.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    assert options["headers"] == {
        "Authorization": "Bearer test-only-secret",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
        "X-DashScope-OssResourceResolve": "enable",
    }
    assert options["json"] == {
        "model": "kling/kling-v3-video-generation",
        "input": {
            "prompt": "A cinematic test prompt.",
            "media": [{"type": "first_frame", "url": "oss://dashscope-instant/kling/first.png"}],
        },
        "parameters": {
            "mode": "std",
            "duration": 3,
            "audio": False,
            "watermark": False,
        },
    }


def test_kling_provider_image_url_requires_typed_upload_and_verified_bytes():
    data = rgb_png_bytes("typed-kling-upload", width=512, height=512)
    version = _version(data, "first_frame", 0)
    prepared = PreparedMedia.from_verified_provider_image_url(
        version,
        upload_result=_provider_upload_result(version),
        data=data,
    )

    assert prepared.url == "oss://dashscope-instant/kling/verified.png"
    assert prepared.transport_binding == "dashscope_oss_sha256_v1"
    assert prepared.frozen_image_metadata is not None
    assert prepared.frozen_image_metadata.kind == "image"
    assert prepared.frozen_image_metadata.width == 512
    assert prepared.frozen_image_metadata.height == 512
    assert prepared.frozen_image_metadata.has_alpha is False

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_image_url(
            version,
            upload_result=_provider_upload_result(
                _version(data, "reference_image", 0),
            ),
            data=data,
        )

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_image_url(
            version,
            upload_result=_provider_upload_result(version, key="bad key.png"),
            data=data,
        )


def test_kling_prepare_transport_uploads_verified_image_before_submit():
    data = rgb_png_bytes("kling-transport-upload", width=512, height=512)
    pending = PreparedMedia.from_verified_bytes(
        _version(data, "first_frame", 0),
        data,
        max_bytes=10 * 1024 * 1024,
    )
    submission = _submission(
        "kling/kling-v3-video-generation",
        media=(pending,),
        mode="pro",
        duration=5,
        audio=False,
        watermark=False,
    )
    calls = []

    def uploader(**kwargs):
        calls.append(kwargs)
        return _provider_upload_result(kwargs["version"])

    ready = prepare_submission_transport(submission, CREDENTIAL, uploader=uploader)

    assert calls == [{
        "scope": SCOPE,
        "provider_model_id": "kling/kling-v3-video-generation",
        "credential": CREDENTIAL,
        "version": pending.version,
        "data": data,
    }]
    assert ready.media[0].url == "oss://dashscope-instant/kling/verified.png"
    assert ready.media[0].transport_binding == "dashscope_oss_sha256_v1"
    assert ready.media[0].frozen_image_metadata is not None
    WanxModel.validate_submission(ready)


def test_kling_prepare_transport_rejects_corrupt_frozen_image_before_upload():
    data = rgb_png_bytes("kling-transport-corrupt", width=512, height=512)
    pending = PreparedMedia.from_verified_bytes(
        _version(data, "first_frame", 0),
        data,
        max_bytes=10 * 1024 * 1024,
    )
    object.__setattr__(pending, "url", pending.url[:-8] + "AAAA")
    submission = _submission("kling/kling-v3-video-generation", media=(pending,), duration=5)
    calls = []

    with pytest.raises(ProviderRecoveryError):
        prepare_submission_transport(submission, CREDENTIAL, uploader=lambda **kwargs: calls.append(kwargs))

    assert calls == []


def test_kling_r2v_posts_refer_media_in_order_and_requires_aspect_ratio(monkeypatch):
    media = (
        _provider_image("reference_image", 0, "oss://dashscope-instant/kling/ref-0.png"),
        _provider_image("reference_image", 1, "oss://dashscope-instant/kling/ref-1.png"),
    )
    request = _submission(
        "kling/kling-v3-omni-video-generation",
        media=media,
        mode="pro",
        duration=5,
        audio=True,
        watermark=False,
        aspect_ratio="9:16",
        negative_prompt="bad anatomy",
    )
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"output": {"task_id": "task-r2v"}}

    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda _url, **kwargs: calls.append(kwargs["json"]) or Response(),
    )

    WanxModel({"output_root": "/tmp"}).submit(
        request,
        credential=CREDENTIAL,
        on_submitted=lambda _: None,
    )

    assert calls == [{
        "model": "kling/kling-v3-omni-video-generation",
        "input": {
            "prompt": "A cinematic test prompt.",
            "media": [
                {"type": "refer", "url": "oss://dashscope-instant/kling/ref-0.png"},
                {"type": "refer", "url": "oss://dashscope-instant/kling/ref-1.png"},
            ],
            "negative_prompt": "bad anatomy",
        },
        "parameters": {
            "mode": "pro",
            "duration": 5,
            "audio": True,
            "watermark": False,
            "aspect_ratio": "9:16",
        },
    }]


@pytest.mark.parametrize(
    "bad_params",
    [
        {"seed": 0, "duration": 5},
        {"sound": True, "duration": 5},
        {"cfg": 0.5, "duration": 5},
        {"prompt_extend": False, "duration": 5},
        {"duration": 2},
        {"duration": 16},
        {"duration": 5, "mode": "turbo"},
        {"duration": 5, "audio": 0},
        {"duration": 5, "watermark": 1},
        {"duration": 5, "negative_prompt": "x" * 2501},
        {"duration": 5, "aspect_ratio": "16:9"},
    ],
)
def test_kling_i2v_rejects_unsupported_or_wrong_typed_params(bad_params):
    request = _submission(
        "kling/kling-v3-video-generation",
        media=(_data_uri_image("first_frame", 0),),
        **bad_params,
    )

    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)


@pytest.mark.parametrize("count", [0, 8])
def test_kling_r2v_rejects_reference_count_before_network(count, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *_args, **_kwargs: pytest.fail("invalid Kling request must not reach network"),
    )
    request = _submission(
        "kling/kling-v3-omni-video-generation",
        media=tuple(_data_uri_image("reference_image", index) for index in range(count)),
        duration=5,
        aspect_ratio="16:9",
    )

    with pytest.raises(ProviderBindingError):
        WanxModel({"output_root": "/tmp"}).submit(
            request,
            credential=CREDENTIAL,
            on_submitted=lambda _: None,
        )


def test_kling_rejects_alpha_webp_bmp_and_bad_dimensions():
    for item in (
        _data_uri_image("first_frame", 0, alpha=True),
        _data_uri_image("first_frame", 0, mime="image/webp"),
        _data_uri_image("first_frame", 0, mime="image/bmp"),
        _data_uri_image("first_frame", 0, width=299, height=400),
        _data_uri_image("first_frame", 0, width=300, height=751),
    ):
        request = _submission(
            "kling/kling-v3-video-generation",
            media=(item,),
            duration=5,
        )
        with pytest.raises(ProviderBindingError):
            WanxModel.validate_submission(request)


def test_kling_wrong_modes_and_untrusted_shapes_raise_binding_errors():
    valid = _submission(
        "kling/kling-v3-video-generation",
        media=(_data_uri_image("first_frame", 0),),
        duration=5,
    )
    wrong_mode = replace(valid, intent=replace(valid.intent, mode="r2v"))
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(wrong_mode)

    unsafe_media = object.__new__(ProviderSubmission)
    object.__setattr__(unsafe_media, "intent", valid.intent)
    object.__setattr__(unsafe_media, "provider_model_id", valid.provider_model_id)
    object.__setattr__(unsafe_media, "media", [{"url": "https://example.com/image.png"}])
    object.__setattr__(unsafe_media, "scope", valid.scope)
    object.__setattr__(unsafe_media, "credential_ref", valid.credential_ref)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(unsafe_media)

    unsafe_params = replace(valid.intent, params={"duration": {"bad": "shape"}})
    with pytest.raises(ProviderBindingError):
        validate_kling_intent(unsafe_params)


def test_kling_pure_image_metadata_validator_is_public():
    assert validate_kling_image_metadata(
        FrozenImageMetadata(kind="image", width=8000, height=3200, has_alpha=False)
    ) is None
    for metadata in (
        FrozenImageMetadata(kind="image", width=299, height=400, has_alpha=False),
        FrozenImageMetadata(kind="image", width=400, height=8001, has_alpha=False),
        FrozenImageMetadata(kind="image", width=300, height=751, has_alpha=False),
        FrozenImageMetadata(kind="image", width=400, height=400, has_alpha=True),
        {"kind": "image", "width": 400, "height": 400},
    ):
        with pytest.raises(ProviderBindingError):
            validate_kling_image_metadata(metadata)


def test_kling_workspace_admission_uses_exact_api_ids(tmp_path):
    for provider_model_id in CANONICAL_BY_PROVIDER:
        result = build_provider_runtime_for_admission(
            RuntimeEnvironment({
                "DASHSCOPE_API_KEY": "shared-key",
                "DASHSCOPE_WORKSPACE_ID": "kling-space",
                "DASHSCOPE_REGION": "cn-beijing",
                "DASHSCOPE_BASE_URL": "https://kling-space.cn-beijing.maas.aliyuncs.com",
            }),
            output_root=tmp_path,
            provider_model_id=provider_model_id,
            gateway="dashscope_workspace",
        )

        assert result.reason is None
        assert result.runtime is not None
        assert result.runtime.scope == SCOPE
        assert result.runtime.resolve_secret() == "shared-key"


@pytest.mark.parametrize("provider_model_id", CANONICAL_BY_PROVIDER)
def test_kling_rejects_non_beijing_workspace_before_upload(provider_model_id):
    scope = replace(SCOPE, region="ap-southeast-1", base_url="https://kling-space.ap-southeast-1.maas.aliyuncs.com")
    mode = CANONICAL_BY_PROVIDER[provider_model_id][1]
    role = "first_frame" if mode == "i2v" else "reference_image"
    params = {"aspect_ratio": "16:9"} if mode == "r2v" else {}
    request = replace(_submission(provider_model_id, media=(_data_uri_image(role, 0),), **params), scope=scope)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)
    with pytest.raises(ProviderRecoveryError):
        prepare_submission_transport(request, replace(CREDENTIAL, scope=scope), uploader=lambda **_: pytest.fail("upload must not run"))
