"""HappyHorse 1.0 VideoEdit durable provider submission contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ProviderSubmission,
    ResolvedCredential,
)
from src.models.provider_runtime import build_provider_runtime_for_admission
from src.models.provider_upload import ProviderUploadResult
from src.models.wanx import (
    WanxModel,
    validate_happyhorse_videoedit_intent,
    validate_happyhorse_videoedit_video_metadata,
)
from src.utils.generation_intent import GenerationIntent
from src.utils.media_probe import FrozenMediaMetadata
from src.utils.media_versions import MediaVersion
from src.utils.runtime_environment import RuntimeEnvironment
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import png_bytes


SCOPE = ProviderScope(
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
CREDENTIAL = ResolvedCredential(SCOPE, "credentials/happyhorse-videoedit", "test-only-secret")


def _intent(prompt: str = "Edit the source clip with cinematic precision.", **params) -> GenerationIntent:
    return GenerationIntent(
        model_id="happyhorse/happyhorse-1.0-video-edit#v2v",
        catalog_mode="v2v",
        mode="v2v",
        prompt=prompt,
        params=params,
        sources={},
        request_fields=(),
    )


def _version(data: bytes, role: str, ordinal: int, mime: str) -> MediaVersion:
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


def _ready_video(
    data: bytes = b"happyhorse video bytes",
    *,
    ordinal: int = 0,
    duration: float = 3.0,
    width: int = 1280,
    height: int = 720,
    fps: float | None = 24.0,
    mime: str = "video/mp4",
) -> PreparedMedia:
    version = _version(data, "reference_video", ordinal, mime)
    upload = ProviderUploadResult(
        url=f"oss://dashscope-temp/hh-video-{version.sha256[:8]}.mp4",
        key=f"dashscope-temp/hh-video-{version.sha256[:8]}.mp4",
        provider_model_id="happyhorse-1.0-video-edit",
        version=version,
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )
    return PreparedMedia.from_verified_provider_url(
        version,
        upload_result=upload,
        metadata=FrozenMediaMetadata(
            kind="video",
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            format_names=("mp4",),
        ),
    )


def _pending_video(data: bytes = b"happyhorse video bytes") -> PreparedMedia:
    version = _version(data, "reference_video", 0, "video/mp4")
    return PreparedMedia.from_pending_av_bytes(
        version,
        data,
        probe=lambda *_: FrozenMediaMetadata(
            kind="video",
            duration=3.0,
            width=1280,
            height=720,
            fps=24.0,
            format_names=("mp4",),
        ),
    )


def _image(
    ordinal: int = 0,
    *,
    width: int = 300,
    height: int = 300,
    alpha: bool = False,
) -> PreparedMedia:
    payload = png_bytes(width, height, alpha=alpha) if alpha else rgb_png_bytes(
        f"happyhorse-videoedit-{ordinal}-{width}x{height}",
        width=width,
        height=height,
    )
    return PreparedMedia.from_verified_bytes(
        _version(payload, "reference_image", ordinal, "image/png"),
        payload,
        max_bytes=20 * 1024 * 1024,
    )


def _submission(
    media: tuple[PreparedMedia, ...] | None = None,
    *,
    prompt: str = "Edit the source clip with cinematic precision.",
    scope: ProviderScope = SCOPE,
    **params,
) -> ProviderSubmission:
    return ProviderSubmission(
        intent=_intent(prompt=prompt, **params),
        provider_model_id="happyhorse-1.0-video-edit",
        media=media if media is not None else (_ready_video(), _image(0), _image(1, alpha=True)),
        scope=scope,
        credential_ref=CREDENTIAL.credential_ref,
    )


def test_happyhorse_videoedit_submit_posts_payload_and_preserves_falsey_params(monkeypatch):
    calls = []
    request = _submission(resolution="720P", seed=0, watermark=False, audio_setting="origin")

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-1", "output": {"task_id": "task-1"}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("src.models.wanx.requests.post", post)

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
        "X-DashScope-OssResourceResolve": "enable",
    }
    assert options["json"] == {
        "model": "happyhorse-1.0-video-edit",
        "input": {
            "prompt": "Edit the source clip with cinematic precision.",
            "media": [
                {"type": "video", "url": request.media[0].url},
                {"type": "reference_image", "url": request.media[1].url},
                {"type": "reference_image", "url": request.media[2].url},
            ],
        },
        "parameters": {
            "audio_setting": "origin",
            "resolution": "720P",
            "seed": 0,
            "watermark": False,
        },
    }


def test_happyhorse_videoedit_public_intent_helper_accepts_exact_contract():
    assert validate_happyhorse_videoedit_intent(
        _intent(resolution="1080P", seed=0, watermark=False, audio_setting="auto")
    ) is None


def test_happyhorse_videoedit_public_metadata_helper_accepts_reported_video_metadata():
    assert validate_happyhorse_videoedit_video_metadata(
        FrozenMediaMetadata(
            kind="video",
            duration=60.0,
            width=900,
            height=360,
            fps=8.0,
            format_names=("mp4",),
        )
    ) is None


def test_happyhorse_videoedit_public_helpers_reject_invalid_intent_and_metadata():
    with pytest.raises(ProviderBindingError):
        validate_happyhorse_videoedit_intent(_intent(duration=0))
    with pytest.raises(ProviderBindingError):
        validate_happyhorse_videoedit_video_metadata(
            FrozenMediaMetadata(
                kind="video",
                duration=3.0,
                width=1280,
                height=720,
                fps=None,
                format_names=("mp4",),
            )
        )


@pytest.mark.parametrize("bad_audio_setting", [[], {}])
def test_happyhorse_videoedit_public_intent_helper_rejects_non_string_audio_setting(
    bad_audio_setting,
):
    with pytest.raises(ProviderBindingError):
        validate_happyhorse_videoedit_intent(_intent(audio_setting=bad_audio_setting))


def test_happyhorse_videoedit_validate_accepts_pending_video_but_submit_requires_upload(monkeypatch):
    request = _submission(media=(_pending_video(), _image(0)), resolution="1080P", audio_setting="auto")

    assert WanxModel.validate_submission(request) is None
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("pending AV must be uploaded before submit"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel({"output_root": "/tmp"}).submit(
            request,
            credential=CREDENTIAL,
            on_submitted=lambda _: None,
        )


@pytest.mark.parametrize(
    "media",
    [
        (),
        (_image(0),),
        (_ready_video(), _ready_video()),
        (_ready_video(), *tuple(_image(index) for index in range(6))),
        (_ready_video(ordinal=1),),
        (_ready_video(), _image(1)),
    ],
)
def test_happyhorse_videoedit_rejects_invalid_media_layout_before_network(media, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid HappyHorse VideoEdit media must not reach network"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(media=tuple(media)))


@pytest.mark.parametrize(
    "media",
    [
        (_ready_video(duration=2.9),),
        (_ready_video(duration=60.1),),
        (_ready_video(width=359, height=720),),
        (_ready_video(width=4097, height=720),),
        (_ready_video(width=360, height=901),),
        (_ready_video(fps=None),),
        (_ready_video(fps=7.9),),
        (_ready_video(fps=60.1),),
        (_ready_video(), _image(0, width=299, height=300)),
        (_ready_video(), _image(0, width=300, height=751)),
    ],
)
def test_happyhorse_videoedit_rejects_media_metadata_limits(media):
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(media=media))


@pytest.mark.parametrize(
    "params",
    [
        {"duration": 0},
        {"duration": 5},
        {"ratio": "16:9"},
        {"negative_prompt": "bad"},
        {"prompt_extend": False},
        {"audio": False},
        {"resolution": "480P"},
        {"resolution": "720p"},
        {"seed": -1},
        {"seed": 2147483648},
        {"seed": True},
        {"watermark": 0},
        {"audio_setting": "mute"},
    ],
)
def test_happyhorse_videoedit_rejects_invalid_or_unsupported_params_before_network(params, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid HappyHorse VideoEdit params must not reach network"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(**params))


@pytest.mark.parametrize(
    "prompt",
    ["", "   ", "字" * 2501, "a" * 5001, "\U00020000" * 2501],
)
def test_happyhorse_videoedit_rejects_blank_or_oversize_prompt_before_network(prompt, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid HappyHorse VideoEdit prompt must not reach network"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(prompt=prompt))


def test_happyhorse_videoedit_requires_exact_canonical_intent_and_workspace_scope():
    base = _submission()
    mutations = [
        replace(base, provider_model_id="happyhorse-1.1-r2v"),
        replace(base, intent=replace(base.intent, model_id="happyhorse/happyhorse-1.1-video#r2v")),
        replace(base, intent=replace(base.intent, mode="r2v")),
        replace(base, intent=replace(base.intent, catalog_mode="video")),
        replace(base, scope=CLASSIC_SCOPE),
    ]

    for request in mutations:
        with pytest.raises(ProviderBindingError):
            WanxModel.validate_submission(request)


def test_happyhorse_videoedit_is_workspace_admission_model(tmp_path):
    result = build_provider_runtime_for_admission(
        RuntimeEnvironment({
            "DASHSCOPE_API_KEY": "shared-key",
            "DASHSCOPE_WORKSPACE_ID": "happyhorse",
            "DASHSCOPE_REGION": "cn-beijing",
            "DASHSCOPE_BASE_URL": "https://happyhorse.cn-beijing.maas.aliyuncs.com",
        }),
        output_root=tmp_path,
        provider_model_id="happyhorse-1.0-video-edit",
        gateway="dashscope_workspace",
    )
    classic = build_provider_runtime_for_admission(
        RuntimeEnvironment({"DASHSCOPE_API_KEY": "shared-key"}),
        output_root=tmp_path,
        provider_model_id="happyhorse-1.0-video-edit",
        gateway="dashscope",
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == SCOPE
    assert result.runtime.resolve_secret() == "shared-key"
    assert classic.runtime is None
    assert classic.reason == "unsupported_dashscope_gateway"
