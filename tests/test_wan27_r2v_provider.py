"""Wan 2.7 R2V provider submission contract."""

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
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.media_versions import MediaVersion
from src.utils.runtime_environment import RuntimeEnvironment


SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
CREDENTIAL = ResolvedCredential(SCOPE, "credentials/wan-r2v", "test-only-secret")


def _intent(**params) -> GenerationIntent:
    defaults = {
        "duration": 2,
        "resolution": "720p",
        "ratio": "9:16",
        "seed": 0,
        "prompt_extend": False,
        "watermark": False,
        "negative_prompt": "low quality",
    }
    defaults.update(params)
    return GenerationIntent(
        model_id="wan/wan2.7-video#r2v",
        catalog_mode="r2v",
        mode="r2v",
        prompt="Image 1 walks through Image 2.",
        params=defaults,
        sources={},
        request_fields=(),
    )


def _media(count: int) -> tuple[PreparedMedia, ...]:
    result = []
    for index in range(count):
        url = f"https://example.com/reference-{index}.png"
        version = MediaVersion(
            hashlib.sha256(url.encode("utf-8")).hexdigest(),
            1024,
            "reference_image",
            index,
            "image/png",
        )
        result.append(PreparedMedia(version, url))
    return tuple(result)


def _submission(media_count=2, **params) -> ProviderSubmission:
    return ProviderSubmission(
        intent=_intent(**params),
        provider_model_id="wan2.7-r2v",
        media=_media(media_count),
        scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref,
    )


def test_wan27_r2v_submit_posts_official_media_payload_and_preserves_falsey_params(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-1", "output": {"task_id": "task-1"}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("src.models.wanx.requests.post", post)

    handle = WanxModel({"output_root": "/tmp"}).submit(
        _submission(),
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
    }
    assert options["json"] == {
        "model": "wan2.7-r2v",
        "input": {
            "prompt": "Image 1 walks through Image 2.",
            "negative_prompt": "low quality",
            "media": [
                {"type": "reference_image", "url": "https://example.com/reference-0.png"},
                {"type": "reference_image", "url": "https://example.com/reference-1.png"},
            ],
        },
        "parameters": {
            "duration": 2,
            "resolution": "720P",
            "ratio": "9:16",
            "seed": 0,
            "prompt_extend": False,
            "watermark": False,
        },
    }


@pytest.mark.parametrize("media_count", [0, 6])
def test_wan27_r2v_rejects_reference_image_count_before_network(media_count, monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.requests.post",
        lambda *args, **kwargs: pytest.fail("invalid local request must not reach network"),
    )

    with pytest.raises(ProviderBindingError):
        WanxModel({"output_root": "/tmp"}).submit(
            _submission(media_count=media_count),
            credential=CREDENTIAL,
            on_submitted=lambda _: None,
        )


def test_wan27_r2v_rejects_non_reference_image_media_role():
    request = replace(
        _submission(media_count=1),
        media=tuple(replace(item, version=replace(item.version, role="first_frame")) for item in _media(1)),
    )

    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)


def test_wan27_r2v_accepts_official_dated_model_id():
    request = replace(_submission(media_count=1), provider_model_id="wan2.7-r2v-2026-06-12")

    assert WanxModel.validate_submission(request) is None


def test_wan27_r2v_is_classic_dashscope_admission_model(tmp_path):
    result = build_provider_runtime_for_admission(
        RuntimeEnvironment({
            "DASHSCOPE_API_KEY": "shared-key",
            "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
            "DASHSCOPE_REGION": "cn-beijing",
            "DASHSCOPE_BASE_URL": "https://wan-workspace.cn-beijing.maas.aliyuncs.com",
        }),
        output_root=tmp_path,
        provider_model_id="wan2.7-r2v",
        gateway="dashscope",
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == SCOPE
    assert result.runtime.resolve_secret() == "shared-key"


def test_wan27_r2v_dated_model_id_is_classic_dashscope_admission_model(tmp_path):
    result = build_provider_runtime_for_admission(
        RuntimeEnvironment({"DASHSCOPE_API_KEY": "shared-key"}),
        output_root=tmp_path,
        provider_model_id="wan2.7-r2v-2026-06-12",
        gateway="dashscope",
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == SCOPE


def test_existing_wan27_t2v_payload_remains_media_free(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"request_id": "req-1", "output": {"task_id": "task-1"}}

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("src.models.wanx.requests.post", post)
    request = replace(
        _submission(media_count=0),
        intent=GenerationIntent(
            model_id="wan/wan2.7-video#t2v",
            catalog_mode="t2v",
            mode="t2v",
            prompt="A clean text prompt.",
            params={
                "duration": 2,
                "resolution": "720P",
                "ratio": "16:9",
                "prompt_extend": False,
                "watermark": False,
            },
            sources={},
            request_fields=(),
        ),
        provider_model_id="wan2.7-t2v",
        media=(),
    )

    WanxModel({"output_root": "/tmp"}).submit(
        request,
        credential=CREDENTIAL,
        on_submitted=lambda _: None,
    )

    assert calls[0]["input"] == {"prompt": "A clean text prompt."}
