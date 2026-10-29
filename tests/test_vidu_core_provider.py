"""Vidu Q3 workspace provider submission contract."""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib

import pytest

from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ProviderSubmission,
)
from src.models.vidu_core_contract import (
    VIDU_PROVIDER_CONTRACT,
    validate_vidu_image_metadata,
    validate_vidu_intent,
    vidu_submission_payload,
)
from src.utils.generation_intent import GenerationIntent
from src.utils.media_probe import FrozenImageMetadata
from src.utils.media_versions import MediaVersion
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import WEBP_240_BASE64, png_bytes


SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://vidu-space.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="vidu-space",
)
CLASSIC_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


def _intent(
    provider_model_id: str,
    *,
    prompt: str = "A precise Vidu provider request.",
    **params,
) -> GenerationIntent:
    canonical, mode = VIDU_PROVIDER_CONTRACT[provider_model_id]
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
    if mime == "image/webp":
        data = base64.b64decode(WEBP_240_BASE64)
    else:
        data = png_bytes(width, height, alpha=alpha) if alpha else rgb_png_bytes(
            f"vidu-{role}-{ordinal}-{width}x{height}",
            width=width,
            height=height,
        )
    return PreparedMedia.from_verified_bytes(
        _version(data, role, ordinal, mime),
        data,
        max_bytes=50 * 1024 * 1024,
    )


def _provider_image(
    role: str,
    ordinal: int,
    url: str,
    *,
    mime: str = "image/png",
    width: int = 512,
    height: int = 512,
    alpha: bool = False,
) -> PreparedMedia:
    data = rgb_png_bytes(f"vidu-provider-{role}-{ordinal}", width=16, height=16)
    prepared = object.__new__(PreparedMedia)
    object.__setattr__(prepared, "version", _version(data, role, ordinal, mime))
    object.__setattr__(prepared, "url", url)
    object.__setattr__(prepared, "transport_binding", "dashscope_oss_sha256_v1")
    object.__setattr__(prepared, "frozen_bytes", None)
    object.__setattr__(prepared, "frozen_metadata", None)
    object.__setattr__(
        prepared,
        "frozen_image_metadata",
        FrozenImageMetadata(kind="image", width=width, height=height, has_alpha=alpha),
    )
    return prepared


def _submission(
    provider_model_id: str,
    *,
    media: tuple[PreparedMedia, ...],
    scope: ProviderScope = SCOPE,
    prompt: str = "A precise Vidu provider request.",
    **params,
) -> ProviderSubmission:
    return ProviderSubmission(
        intent=_intent(provider_model_id, prompt=prompt, **params),
        provider_model_id=provider_model_id,
        media=media,
        scope=scope,
        credential_ref="credentials/vidu",
    )


@pytest.mark.parametrize(
    ("provider_model_id", "canonical", "mode"),
    [
        ("vidu/viduq3-pro_img2video", "vidu/viduq3-pro-video#i2v", "i2v"),
        ("vidu/viduq3-turbo_img2video", "vidu/viduq3-turbo-video#i2v", "i2v"),
        ("vidu/viduq3-mix_reference2video", "vidu/viduq3-pro-video#r2v", "r2v"),
        ("vidu/viduq3-turbo_reference2video", "vidu/viduq3-turbo-video#r2v", "r2v"),
    ],
)
def test_vidu_four_q3_api_ids_have_exact_canonical_contract(provider_model_id, canonical, mode):
    assert VIDU_PROVIDER_CONTRACT[provider_model_id] == (canonical, mode)


def test_vidu_i2v_payload_validates_data_uri_but_submit_requires_oss():
    first_frame = _data_uri_image("first_frame", 0, width=400, height=1600, alpha=True)
    request = _submission(
        "vidu/viduq3-pro_img2video",
        media=(first_frame,),
        duration=16,
        resolution="1080P",
        audio=False,
        watermark=False,
        seed=0,
    )

    assert validate_vidu_intent(request.intent) == {
        "duration": 16,
        "resolution": "1080P",
        "audio": False,
        "watermark": False,
        "seed": 0,
    }
    assert vidu_submission_payload(request) == {
        "model": "vidu/viduq3-pro_img2video",
        "input": {
            "prompt": "A precise Vidu provider request.",
            "media": [{"type": "image", "url": first_frame.url}],
        },
        "parameters": {
            "duration": 16,
            "resolution": "1080P",
            "audio": False,
            "watermark": False,
            "seed": 0,
        },
    }
    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(request, require_transport_ready=True)


def test_vidu_i2v_ready_payload_uses_single_image_and_does_not_invent_defaults():
    first_frame = _provider_image(
        "first_frame",
        0,
        "oss://dashscope-instant/vidu/first.webp",
        mime="image/webp",
        width=512,
        height=512,
    )
    request = _submission("vidu/viduq3-turbo_img2video", media=(first_frame,))

    assert vidu_submission_payload(request, require_transport_ready=True) == {
        "model": "vidu/viduq3-turbo_img2video",
        "input": {
            "prompt": "A precise Vidu provider request.",
            "media": [{"type": "image", "url": "oss://dashscope-instant/vidu/first.webp"}],
        },
        "parameters": {},
    }


def test_vidu_r2v_requires_explicit_resolution_and_aspect_ratio_for_size_derivation():
    media = (_provider_image("reference_image", 0, "oss://dashscope-instant/vidu/ref.png"),)
    request = _submission(
        "vidu/viduq3-turbo_reference2video",
        media=media,
        resolution="720P",
        aspect_ratio="16:9",
    )

    assert validate_vidu_intent(request.intent) == {
        "resolution": "720P",
        "size": "1280*720",
    }
    assert vidu_submission_payload(request, require_transport_ready=True)["parameters"] == {
        "resolution": "720P",
        "size": "1280*720",
    }

    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(
            _submission(
                "vidu/viduq3-turbo_reference2video",
                media=media,
                aspect_ratio="16:9",
            )
        )
    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(
            _submission(
                "vidu/viduq3-turbo_reference2video",
                media=media,
                resolution="720P",
            )
        )


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "expected_size"),
    [
        ("540P", "16:9", "960*528"),
        ("540P", "4:3", "1024*768"),
        ("540P", "1:1", "1024*1024"),
        ("540P", "3:4", "768*1024"),
        ("540P", "9:16", "576*1024"),
        ("720P", "16:9", "1280*720"),
        ("720P", "4:3", "1280*960"),
        ("720P", "1:1", "1280*1280"),
        ("720P", "3:4", "960*1280"),
        ("720P", "9:16", "720*1280"),
        ("1080P", "16:9", "1920*1080"),
        ("1080P", "4:3", "1920*1440"),
        ("1080P", "1:1", "1920*1920"),
        ("1080P", "3:4", "1440*1920"),
        ("1080P", "9:16", "1080*1920"),
    ],
)
def test_vidu_r2v_size_table_is_exact_and_preserves_reference_order(
    resolution,
    aspect_ratio,
    expected_size,
):
    media = (
        _provider_image("reference_image", 0, "oss://dashscope-instant/vidu/ref-0.png"),
        _provider_image("reference_image", 1, "oss://dashscope-instant/vidu/ref-1.png", alpha=True),
    )
    request = _submission(
        "vidu/viduq3-turbo_reference2video",
        media=media,
        duration=1,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        audio=True,
        watermark=False,
        seed=2147483647,
    )

    assert vidu_submission_payload(request, require_transport_ready=True) == {
        "model": "vidu/viduq3-turbo_reference2video",
        "input": {
            "prompt": "A precise Vidu provider request.",
            "media": [
                {"type": "image", "url": "oss://dashscope-instant/vidu/ref-0.png"},
                {"type": "image", "url": "oss://dashscope-instant/vidu/ref-1.png"},
            ],
        },
        "parameters": {
            "duration": 1,
            "resolution": resolution,
            "audio": True,
            "watermark": False,
            "seed": 2147483647,
            "size": expected_size,
        },
    }


def test_vidu_mix_r2v_rejects_540p_but_uses_pro_display_identity():
    media = (_provider_image("reference_image", 0, "oss://dashscope-instant/vidu/ref.png"),)
    request = _submission(
        "vidu/viduq3-mix_reference2video",
        media=media,
        resolution="720P",
        aspect_ratio="16:9",
    )
    assert request.intent.model_id == "vidu/viduq3-pro-video#r2v"
    assert vidu_submission_payload(request, require_transport_ready=True)["parameters"]["size"] == "1280*720"

    bad = _submission(
        "vidu/viduq3-mix_reference2video",
        media=media,
        resolution="540P",
        aspect_ratio="16:9",
    )
    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(bad)


def test_vidu_intent_validation_derives_mix_provider_for_pro_r2v_540p_rejection():
    bad = _intent(
        "vidu/viduq3-mix_reference2video",
        resolution="540P",
        aspect_ratio="16:9",
    )

    assert bad.model_id == "vidu/viduq3-pro-video#r2v"
    with pytest.raises(ProviderBindingError):
        validate_vidu_intent(bad)


@pytest.mark.parametrize("count", [0, 8])
def test_vidu_r2v_rejects_reference_count_limits(count):
    request = _submission(
        "vidu/viduq3-turbo_reference2video",
        media=tuple(_data_uri_image("reference_image", index) for index in range(count)),
        resolution="720P",
        aspect_ratio="16:9",
    )

    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(request)


@pytest.mark.parametrize(
    "bad_params",
    [
        {"duration": 0},
        {"duration": 17},
        {"resolution": "720p"},
        {"resolution": None},
        {"resolution": "480P"},
        {"audio": 0},
        {"watermark": 1},
        {"seed": -1},
        {"seed": 2147483648},
        {"negative_prompt": "unsupported"},
        {"movement_amplitude": "auto"},
        {"ratio": "16:9"},
        {"size": "1280*720"},
        {"aspect_ratio": "16:9"},
        {"duration": None},
        {"audio": None},
    ],
)
def test_vidu_i2v_rejects_unsupported_wrong_typed_or_r2v_only_params(bad_params):
    request = _submission(
        "vidu/viduq3-pro_img2video",
        media=(_data_uri_image("first_frame", 0),),
        **bad_params,
    )

    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(request)


@pytest.mark.parametrize(
    "bad_params",
    [
        {},
        {"aspect_ratio": "21:9"},
        {"aspect_ratio": False},
        {"aspect_ratio": None},
        {"aspect_ratio": "16:9", "resolution": "540P"},
        {"aspect_ratio": "16:9", "audio": "false"},
        {"aspect_ratio": "16:9", "seed": 0.0},
    ],
)
def test_vidu_mix_r2v_rejects_missing_or_invalid_r2v_params(bad_params):
    request = _submission(
        "vidu/viduq3-mix_reference2video",
        media=(_data_uri_image("reference_image", 0),),
        **bad_params,
    )

    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(request)


@pytest.mark.parametrize(
    "prompt",
    ["", "   ", "x" * 5001],
)
def test_vidu_rejects_empty_or_oversized_prompt(prompt):
    request = _submission(
        "vidu/viduq3-pro_img2video",
        media=(_data_uri_image("first_frame", 0),),
        prompt=prompt,
    )

    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(request)


def test_vidu_rejects_scope_mode_and_canonical_mismatches():
    valid = _submission(
        "vidu/viduq3-pro_img2video",
        media=(_data_uri_image("first_frame", 0),),
    )
    for bad in (
        replace(valid, scope=CLASSIC_SCOPE),
        replace(valid, scope=replace(SCOPE, region="ap-southeast-1")),
        replace(valid, intent=replace(valid.intent, mode="r2v")),
        replace(valid, intent=replace(valid.intent, model_id="vidu/viduq3-turbo-video#i2v")),
        replace(valid, provider_model_id="vidu/viduq3-pro_reference2video"),
    ):
        with pytest.raises(ProviderBindingError):
            vidu_submission_payload(bad)


def test_vidu_rejects_wrong_media_roles_ordinals_mimes_and_untrusted_shapes():
    cases = [
        (_data_uri_image("reference_image", 0),),
        (_data_uri_image("first_frame", 0, mime="image/bmp"),),
        (_provider_image("first_frame", 0, "https://example.com/first.png"),),
    ]
    for media in cases:
        request = _submission("vidu/viduq3-pro_img2video", media=media)
        with pytest.raises(ProviderBindingError):
            vidu_submission_payload(request)

    unsafe = object.__new__(ProviderSubmission)
    object.__setattr__(unsafe, "intent", valid_intent := _intent("vidu/viduq3-pro_img2video"))
    object.__setattr__(unsafe, "provider_model_id", "vidu/viduq3-pro_img2video")
    object.__setattr__(unsafe, "media", [{"url": "https://example.com/image.png"}])
    object.__setattr__(unsafe, "scope", SCOPE)
    object.__setattr__(unsafe, "credential_ref", "credentials/vidu")
    assert valid_intent.model_id == "vidu/viduq3-pro-video#i2v"
    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(unsafe)


def test_vidu_image_metadata_allows_alpha_and_boundary_ratios():
    for metadata in (
        FrozenImageMetadata(kind="image", width=1, height=4, has_alpha=True),
        FrozenImageMetadata(kind="image", width=4, height=1, has_alpha=False),
        FrozenImageMetadata(kind="image", width=4000, height=4000, has_alpha=True),
    ):
        assert validate_vidu_image_metadata(metadata) is None

    for metadata in (
        FrozenImageMetadata(kind="image", width=1, height=5, has_alpha=False),
        FrozenImageMetadata(kind="image", width=5, height=1, has_alpha=False),
        FrozenImageMetadata(kind="image", width=0, height=1, has_alpha=False),
        {"kind": "image", "width": 1, "height": 1},
    ):
        with pytest.raises(ProviderBindingError):
            validate_vidu_image_metadata(metadata)


def test_vidu_metadata_accepts_50mb_limit_without_inheriting_kling_20mb_shape_rules():
    oversized_for_old_limits = object.__new__(PreparedMedia)
    object.__setattr__(
        oversized_for_old_limits,
        "version",
        MediaVersion("0" * 64, 50 * 1024 * 1024, "first_frame", 0, "image/webp"),
    )
    object.__setattr__(oversized_for_old_limits, "url", "oss://dashscope-instant/vidu/large.webp")
    object.__setattr__(oversized_for_old_limits, "transport_binding", "dashscope_oss_sha256_v1")
    object.__setattr__(oversized_for_old_limits, "frozen_bytes", None)
    object.__setattr__(oversized_for_old_limits, "frozen_metadata", None)
    object.__setattr__(
        oversized_for_old_limits,
        "frozen_image_metadata",
        FrozenImageMetadata(kind="image", width=10, height=40, has_alpha=True),
    )
    request = _submission("vidu/viduq3-pro_img2video", media=(oversized_for_old_limits,))

    assert vidu_submission_payload(request, require_transport_ready=True)["input"]["media"][0]["type"] == "image"

    too_large = replace(
        oversized_for_old_limits.version,
        size_bytes=50 * 1024 * 1024 + 1,
    )
    object.__setattr__(oversized_for_old_limits, "version", too_large)
    with pytest.raises(ProviderBindingError):
        vidu_submission_payload(request, require_transport_ready=True)
