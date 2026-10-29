"""HappyHorse 1.1 workspace-only provider submission contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ProviderSubmission,
    ProviderSubmitError,
    ResolvedCredential,
)
from src.models.provider_runtime import build_provider_runtime_for_admission
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.media_versions import MediaVersion
from src.utils.runtime_environment import RuntimeEnvironment
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import bmp_bytes, png_bytes


WORKSPACE_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://happyhorse.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="happyhorse",
)
CLASSIC_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
CREDENTIAL = ResolvedCredential(
    WORKSPACE_SCOPE,
    "credentials/happyhorse",
    "test-only-secret",
)
CANONICAL_BY_PROVIDER = {
    "happyhorse-1.1-t2v": ("happyhorse/happyhorse-1.1-t2v#t2v", "t2v"),
    "happyhorse-1.1-i2v": ("happyhorse/happyhorse-1.1-video#i2v", "i2v"),
    "happyhorse-1.1-r2v": ("happyhorse/happyhorse-1.1-video#r2v", "r2v"),
}


def _intent(
    provider_model_id: str,
    *,
    prompt: str = "A precise HappyHorse provider request.",
    **params,
) -> GenerationIntent:
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


def _image(
    role: str,
    ordinal: int,
    *,
    width: int = 400,
    height: int = 400,
    alpha: bool = False,
    mime: str = "image/png",
) -> PreparedMedia:
    data = png_bytes(width, height, alpha=alpha) if alpha else rgb_png_bytes(
        f"{role}-{ordinal}-{width}x{height}",
        width=width,
        height=height,
    )
    return PreparedMedia.from_verified_bytes(
        _version(data, role, ordinal, mime),
        data,
        max_bytes=20 * 1024 * 1024,
    )


def _submission(
    provider_model_id: str,
    *,
    media: tuple[PreparedMedia, ...] = (),
    scope: ProviderScope = WORKSPACE_SCOPE,
    prompt: str = "A precise HappyHorse provider request.",
    **params,
) -> ProviderSubmission:
    return ProviderSubmission(
        intent=_intent(provider_model_id, prompt=prompt, **params),
        provider_model_id=provider_model_id,
        media=media,
        scope=scope,
        credential_ref=CREDENTIAL.credential_ref,
    )


def _unsafe_submission(
    provider_model_id: str,
    *,
    media: tuple[PreparedMedia, ...] = (),
    scope: ProviderScope = WORKSPACE_SCOPE,
    prompt: str = "A precise HappyHorse provider request.",
    **params,
) -> ProviderSubmission:
    submission = object.__new__(ProviderSubmission)
    object.__setattr__(submission, "intent", _intent(provider_model_id, prompt=prompt, **params))
    object.__setattr__(submission, "provider_model_id", provider_model_id)
    object.__setattr__(submission, "media", media)
    object.__setattr__(submission, "scope", scope)
    object.__setattr__(submission, "credential_ref", CREDENTIAL.credential_ref)
    return submission


def test_happyhorse_t2v_payload_is_workspace_only_and_media_free(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-1", "output": {"task_id": "task-1"}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("src.models.wanx.requests.post", post)
    request = _submission(
        "happyhorse-1.1-t2v",
        duration=3,
        resolution="480P",
        ratio="21:9",
        seed=0,
        watermark=False,
    )

    assert WanxModel.validate_submission(request) is None
    handle = WanxModel({"output_root": "/tmp"}).submit(
        request,
        credential=CREDENTIAL,
        on_submitted=lambda _: None,
    )

    assert handle.task_id == "task-1"
    assert len(calls) == 1
    url, options = calls[0]
    assert url == (
        "https://happyhorse.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/aigc/video-generation/video-synthesis"
    )
    assert options["headers"] == {
        "Authorization": "Bearer test-only-secret",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    assert options["json"] == {
        "model": "happyhorse-1.1-t2v",
        "input": {"prompt": "A precise HappyHorse provider request."},
        "parameters": {
            "duration": 3,
            "resolution": "480P",
            "ratio": "21:9",
            "seed": 0,
            "watermark": False,
        },
    }


def test_happyhorse_i2v_payload_uses_exact_first_frame_without_ratio(monkeypatch):
    calls = []
    first_frame = _image("first_frame", 0, width=300, height=750)

    class Response:
        status_code = 200

        def json(self):
            return {"output": {"task_id": "task-i2v"}}

    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda _url, **kwargs: calls.append(kwargs["json"]) or Response(),
    )
    request = _submission(
        "happyhorse-1.1-i2v",
        media=(first_frame,),
        duration=15,
        resolution="1080P",
        seed=2147483647,
        watermark=False,
    )

    WanxModel({"output_root": "/tmp"}).submit(
        request,
        credential=CREDENTIAL,
        on_submitted=lambda _: None,
    )

    assert calls == [{
        "model": "happyhorse-1.1-i2v",
        "input": {
            "prompt": "A precise HappyHorse provider request.",
            "media": [{"type": "first_frame", "url": first_frame.url}],
        },
        "parameters": {
            "duration": 15,
            "resolution": "1080P",
            "seed": 2147483647,
            "watermark": False,
        },
    }]


def test_happyhorse_r2v_payload_preserves_reference_order_and_accepts_png_alpha(monkeypatch):
    media = (
        _image("reference_image", 0, width=400, height=401),
        _image("reference_image", 1, width=720, height=720, alpha=True),
    )
    request = _submission(
        "happyhorse-1.1-r2v",
        media=media,
        duration=5,
        resolution="720P",
        ratio="9:21",
        watermark=False,
    )

    assert WanxModel.validate_submission(request) is None
    payload = []

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-r2v", "output": {"task_id": "task-r2v"}}

    def post(_url, **kwargs):
        payload.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("src.models.wanx.requests.post", post)
    WanxModel({"output_root": "/tmp"}).submit(
        request,
        credential=CREDENTIAL,
        on_submitted=lambda _: None,
    )

    assert payload[0]["input"]["media"] == [
        {"type": "reference_image", "url": media[0].url},
        {"type": "reference_image", "url": media[1].url},
    ]


def test_happyhorse_missing_receipt_does_not_retry_or_acknowledge(monkeypatch):
    calls = []
    acknowledgements = []

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-1", "output": {"task_status": "PENDING"}}

    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda url, **kwargs: calls.append((url, kwargs)) or Response(),
    )
    request = _submission("happyhorse-1.1-t2v", duration=5, resolution="720P", ratio="16:9")

    with pytest.raises(ProviderSubmitError) as raised:
        WanxModel({"output_root": "/tmp"}).submit(
            request,
            credential=CREDENTIAL,
            on_submitted=lambda handle: acknowledgements.append(handle),
        )

    assert raised.value.kind == "missing_receipt"
    assert len(calls) == 1
    assert acknowledgements == []


@pytest.mark.parametrize(
    "submission",
    [
        _submission("happyhorse-1.1-t2v", media=(_image("first_frame", 0),)),
        _submission("happyhorse-1.1-i2v"),
        _submission(
            "happyhorse-1.1-i2v",
            media=(_image("first_frame", 0), _image("first_frame", 0)),
        ),
        _submission("happyhorse-1.1-i2v", media=(_image("reference_image", 0),)),
        _submission("happyhorse-1.1-i2v", media=(_image("first_frame", 0, width=299),)),
        _submission("happyhorse-1.1-i2v", media=(_image("first_frame", 0, width=300, height=751),)),
        _submission("happyhorse-1.1-r2v"),
        _submission(
            "happyhorse-1.1-r2v",
            media=tuple(_image("reference_image", index) for index in range(10)),
        ),
        _submission("happyhorse-1.1-r2v", media=(_image("reference_image", 0, width=399),)),
        _submission("happyhorse-1.1-r2v", media=(_image("first_frame", 0),)),
    ],
)
def test_happyhorse_rejects_invalid_media_layout_or_dimensions_before_network(submission, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid HappyHorse media must not reach network"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(submission)


def test_happyhorse_rejects_unverified_url_and_unsupported_image_mime():
    data = rgb_png_bytes("unverified", width=400, height=400)
    unverified = PreparedMedia(
        _version(data, "first_frame", 0),
        url="https://media.invalid/frame.png",
    )
    bmp_payload = bmp_bytes(400, 400)
    declared_bmp = PreparedMedia.from_verified_bytes(
        _version(bmp_payload, "first_frame", 0, "image/bmp"),
        bmp_payload,
        max_bytes=20 * 1024 * 1024,
    )

    for media in (unverified, declared_bmp):
        with pytest.raises(ProviderBindingError):
            WanxModel.validate_submission(_submission("happyhorse-1.1-i2v", media=(media,)))


@pytest.mark.parametrize(
    "provider_model_id,params",
    [
        ("happyhorse-1.1-t2v", {"duration": 2}),
        ("happyhorse-1.1-t2v", {"duration": 16}),
        ("happyhorse-1.1-t2v", {"duration": True}),
        ("happyhorse-1.1-t2v", {"resolution": "720p"}),
        ("happyhorse-1.1-t2v", {"ratio": "2:1"}),
        ("happyhorse-1.1-t2v", {"seed": -1}),
        ("happyhorse-1.1-t2v", {"seed": 2147483648}),
        ("happyhorse-1.1-t2v", {"seed": True}),
        ("happyhorse-1.1-t2v", {"watermark": 0}),
        ("happyhorse-1.1-t2v", {"negative_prompt": "bad"}),
        ("happyhorse-1.1-t2v", {"prompt_extend": False}),
        ("happyhorse-1.1-t2v", {"audio": False}),
        ("happyhorse-1.1-i2v", {"ratio": "16:9"}),
    ],
)
def test_happyhorse_rejects_invalid_or_unsupported_params(provider_model_id, params):
    media = () if provider_model_id.endswith("t2v") else (_image("first_frame", 0),)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(provider_model_id, media=media, **params))


@pytest.mark.parametrize("bad_ratio", [[], {}])
def test_happyhorse_rejects_non_string_ratio_before_validate_or_submit_network(
    bad_ratio,
    monkeypatch,
):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid HappyHorse ratio must not reach network"),
    )
    request = _unsafe_submission("happyhorse-1.1-t2v", duration=5, resolution="720P", ratio=bad_ratio)

    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)
    with pytest.raises(ProviderBindingError):
        WanxModel({"output_root": "/tmp"}).submit(
            request,
            credential=CREDENTIAL,
            on_submitted=lambda _: None,
        )


@pytest.mark.parametrize(
    "prompt",
    [
        "字" * 2500,
        "a" * 5000,
        "字" * 1000 + "a" * 3000,
        "\U00020000" * 2500,
        "\uf900" * 2500,
    ],
)
def test_happyhorse_accepts_supported_prompt_lengths(prompt):
    assert WanxModel.validate_submission(
        _submission("happyhorse-1.1-t2v", prompt=prompt, duration=5, resolution="720P", ratio="16:9")
    ) is None


@pytest.mark.parametrize(
    "prompt",
    [
        "字" * 2501,
        "a" * 5001,
        "字" * 1000 + "a" * 3001,
        "\U00020000" * 2501,
        "\uf900" * 2501,
    ],
)
def test_happyhorse_rejects_oversize_prompt_lengths_before_network(prompt, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("oversize HappyHorse prompt must not reach network"),
    )
    request = _submission("happyhorse-1.1-t2v", prompt=prompt, duration=5, resolution="720P", ratio="16:9")

    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)
    with pytest.raises(ProviderBindingError):
        WanxModel({"output_root": "/tmp"}).submit(
            request,
            credential=CREDENTIAL,
            on_submitted=lambda _: None,
        )


def test_happyhorse_requires_exact_canonical_intent_and_workspace_scope():
    base = _submission("happyhorse-1.1-r2v", media=(_image("reference_image", 0),))
    mutations = [
        replace(base, provider_model_id="happyhorse-1.1-i2v"),
        replace(base, intent=replace(base.intent, model_id="happyhorse/happyhorse-1.1-t2v#t2v")),
        replace(base, intent=replace(base.intent, mode="i2v")),
        replace(base, intent=replace(base.intent, catalog_mode="video")),
        replace(base, scope=CLASSIC_SCOPE),
    ]

    for request in mutations:
        with pytest.raises(ProviderBindingError):
            WanxModel.validate_submission(request)


def test_happyhorse_workspace_admission_runtime_and_classic_rejection(tmp_path):
    environment = RuntimeEnvironment({
        "DASHSCOPE_API_KEY": "shared-key",
        "DASHSCOPE_WORKSPACE_ID": "happyhorse",
        "DASHSCOPE_REGION": "cn-beijing",
        "DASHSCOPE_BASE_URL": "https://happyhorse.cn-beijing.maas.aliyuncs.com",
    })

    result = build_provider_runtime_for_admission(
        environment,
        output_root=tmp_path,
        provider_model_id="happyhorse-1.1-r2v",
        gateway="dashscope_workspace",
    )
    classic = build_provider_runtime_for_admission(
        environment,
        output_root=tmp_path,
        provider_model_id="happyhorse-1.1-r2v",
        gateway="dashscope",
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == WORKSPACE_SCOPE
    assert result.runtime.resolve_secret() == "shared-key"
    assert classic.runtime is None
    assert classic.reason == "unsupported_dashscope_gateway"
    assert "test-only-secret" not in json.dumps(result.runtime.scope.to_dict())
