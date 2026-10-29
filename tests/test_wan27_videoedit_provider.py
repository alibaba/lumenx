"""Wan 2.7 VideoEdit durable provider submission contract."""

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
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.media_probe import FrozenMediaMetadata
from src.utils.media_versions import MediaVersion
from src.utils.runtime_environment import RuntimeEnvironment
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import png_bytes


SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
CREDENTIAL = ResolvedCredential(SCOPE, "credentials/wan27-videoedit", "test-only-secret")


def _intent(**params) -> GenerationIntent:
    defaults = {
        "duration": 0,
        "resolution": "720P",
        "ratio": "3:4",
        "seed": 0,
        "prompt_extend": False,
        "watermark": False,
        "negative_prompt": "low quality",
        "audio_setting": "origin",
    }
    defaults.update(params)
    return GenerationIntent(
        model_id="wan/wan2.7-video#v2v",
        catalog_mode="v2v",
        mode="v2v",
        prompt="Replace the jacket with the reference image.",
        params=defaults,
        sources={},
        request_fields=(),
    )


def _version(data: bytes, role: str, ordinal: int, mime: str) -> MediaVersion:
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


def _ready_video(
    data: bytes = b"video bytes",
    *,
    ordinal: int = 0,
    duration: float = 2.0,
    width: int = 1280,
    height: int = 720,
    mime: str = "video/mp4",
) -> PreparedMedia:
    version = _version(data, "reference_video", ordinal, mime)
    upload = ProviderUploadResult(
        url=f"oss://dashscope-temp/video-{version.sha256[:8]}.mp4",
        key=f"dashscope-temp/video-{version.sha256[:8]}.mp4",
        provider_model_id="wan2.7-videoedit",
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
            fps=24.0,
            format_names=("mp4",),
        ),
    )


def _pending_video(data: bytes = b"video bytes") -> PreparedMedia:
    version = _version(data, "reference_video", 0, "video/mp4")
    return PreparedMedia.from_pending_av_bytes(
        version,
        data,
        probe=lambda *_: FrozenMediaMetadata(
            kind="video",
            duration=2.0,
            width=1280,
            height=720,
            fps=24.0,
            format_names=("mp4",),
        ),
    )


def _image(ordinal: int = 0, data: bytes | None = None) -> PreparedMedia:
    payload = data or rgb_png_bytes()
    return PreparedMedia.from_verified_bytes(
        _version(payload, "reference_image", ordinal, "image/png"),
        payload,
        max_bytes=20 * 1024 * 1024,
    )


def _submission(media: tuple[PreparedMedia, ...] | None = None, **params) -> ProviderSubmission:
    return ProviderSubmission(
        intent=_intent(**params),
        provider_model_id="wan2.7-videoedit",
        media=media if media is not None else (_ready_video(), _image(0), _image(1)),
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )


def test_wan27_videoedit_submit_posts_official_media_payload_and_falsey_params(monkeypatch):
    calls = []
    request = _submission()

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
    assert url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    assert options["headers"] == {
        "Authorization": "Bearer test-only-secret",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
        "X-DashScope-OssResourceResolve": "enable",
    }
    assert options["json"] == {
        "model": "wan2.7-videoedit",
        "input": {
            "prompt": "Replace the jacket with the reference image.",
            "negative_prompt": "low quality",
            "media": [
                {"type": "video", "url": request.media[0].url},
                {"type": "reference_image", "url": request.media[1].url},
                {"type": "reference_image", "url": request.media[2].url},
            ],
        },
        "parameters": {
            "audio_setting": "origin",
            "duration": 0,
            "prompt_extend": False,
            "ratio": "3:4",
            "resolution": "720P",
            "seed": 0,
            "watermark": False,
        },
    }


def test_wan27_videoedit_omits_duration_when_intent_omits_it(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-1", "output": {"task_id": "task-1"}}

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("src.models.wanx.requests.post", post)
    request = _submission(duration=2)
    params = dict(request.intent.params)
    params.pop("duration")
    request = replace(request, intent=replace(request.intent, params=params))

    WanxModel({"output_root": "/tmp"}).submit(
        request,
        credential=CREDENTIAL,
        on_submitted=lambda _: None,
    )

    assert "duration" not in calls[0]["parameters"]
    assert calls[0]["parameters"]["resolution"] == "720P"


def test_wan27_videoedit_admission_accepts_pending_video_but_submit_requires_oss_ready(monkeypatch):
    request = _submission(media=(_pending_video(), _image(0)), duration=2, audio_setting="auto")

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
        (_ready_video(), *tuple(_image(index) for index in range(5))),
        (_ready_video(ordinal=1),),
    ],
)
def test_wan27_videoedit_rejects_invalid_media_layout_before_network(media, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid VideoEdit media must not reach network"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(media=tuple(media), duration=2))


@pytest.mark.parametrize(
    "media",
    [
        (_ready_video(duration=1.9),),
        (_ready_video(duration=10.1),),
        (_ready_video(width=239),),
        (_ready_video(height=4097),),
        (_ready_video(width=240, height=2000),),
        (_ready_video(), _image(0, data=png_bytes(240, 240, alpha=True))),
    ],
)
def test_wan27_videoedit_rejects_media_metadata_limits(media):
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(media=media, duration=2))


@pytest.mark.parametrize(
    "params",
    [
        {"duration": -1},
        {"duration": 1},
        {"duration": 11},
        {"duration": True},
        {"resolution": "480P"},
        {"resolution": "720p"},
        {"ratio": "2:1"},
        {"seed": -1},
        {"seed": 2147483648},
        {"prompt_extend": 0},
        {"watermark": 0},
        {"audio_setting": "mute"},
        {"negative_prompt": "x" * 501},
        {"audio": False},
    ],
)
def test_wan27_videoedit_rejects_invalid_params_before_network(params, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid VideoEdit params must not reach network"),
    )
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(_submission(**params))


def test_wan27_videoedit_requires_exact_canonical_intent_and_classic_scope():
    base = _submission(duration=2)
    mutations = [
        replace(base, provider_model_id="wan2.7-r2v"),
        replace(base, intent=replace(base.intent, model_id="wan/wan2.7-video#r2v")),
        replace(base, intent=replace(base.intent, mode="r2v")),
        replace(base, intent=replace(base.intent, catalog_mode="video")),
        replace(base, scope=replace(base.scope, gateway="dashscope_workspace")),
    ]

    for request in mutations:
        with pytest.raises(ProviderBindingError):
            WanxModel.validate_submission(request)


def test_wan27_videoedit_is_classic_dashscope_admission_model(tmp_path):
    result = build_provider_runtime_for_admission(
        RuntimeEnvironment({"DASHSCOPE_API_KEY": "shared-key"}),
        output_root=tmp_path,
        provider_model_id="wan2.7-videoedit",
        gateway="dashscope",
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == SCOPE
    assert result.runtime.resolve_secret() == "shared-key"
