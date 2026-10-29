"""Wan3 uses the same frozen submit contract without polling or current defaults."""

from dataclasses import replace
import hashlib

import pytest

from src.models.provider_tasks import PreparedMedia, ProviderBindingError, ProviderScope
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import (
    build_accepted_provider_submission_and_record, reconstruct_submission_from_record,
)
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.media_versions import MediaVersion, freeze_media
from tests.frozen_image_fixtures import rgb_png_bytes
from tests import test_provider_submit as fixtures
from tests import test_provider_admission_builder as builder_fixtures


contract = fixtures.contract
credential = fixtures.credential
model = fixtures.model
scope = fixtures.scope
submission = fixtures.submission
transport = fixtures.transport
inputs = builder_fixtures.inputs


def prepared_image(role="first_frame"):
    data = rgb_png_bytes()
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, 0, "image/png")
    return PreparedMedia.from_verified_bytes(version, data, max_bytes=1024)


def wan3_request(submission, model_id="wan3.0-video", **params):
    intent = GenerationIntent(
        model_id=f"wan/{model_id}#video", catalog_mode="video", mode="i2v",
        prompt="A slow sunrise", params={
            "duration": 5, "resolution": "1080P", "ratio": "adaptive",
            "prompt_extend": True, "watermark": False, "audio": True, **params,
        }, sources={}, request_fields=(),
    )
    return replace(
        submission, intent=intent, provider_model_id=model_id,
        scope=ProviderScope(
            provider="dashscope", backend="dashscope", gateway="dashscope_workspace",
            base_url="https://original-workspace.cn-beijing.maas.aliyuncs.com",
            region="cn-beijing", workspace_id="original-workspace",
        ),
        media=(prepared_image(),),
    )


@pytest.mark.parametrize("model_id", ["wan3.0-video", "wan3.0-video-prime"])
@pytest.mark.parametrize("seed", [-1, 0, 2147483647])
def test_real_submit_preserves_wan3_frozen_values_and_original_scope(
    submission, model, credential, transport, monkeypatch, model_id, seed,
):
    request = wan3_request(
        submission, model_id, seed=seed, duration=-1, prompt_extend=False,
        watermark=False, audio=False, ratio="21:9",
    )
    bound = replace(credential, scope=request.scope)
    before = request.intent.to_dict()
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "another-workspace")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://wrong.invalid")
    model.params = {"duration": 9, "seed": 888, "audio": True}
    monkeypatch.setattr(
        "src.utils.model_catalog.get_catalog_accessor", lambda: pytest.fail("catalog reread"),
    )
    saved = []
    assert WanxModel.validate_submission(request) is None
    handle = model.submit(request, credential=bound, on_submitted=saved.append)
    assert saved == [handle] and len(transport[1]) == 1
    assert request.intent.to_dict() == before
    assert handle.provider_model_id == model_id and handle.scope == request.scope
    assert handle.credential_ref == bound.credential_ref
    url, options = transport[1][0]
    assert url == request.scope.base_url + "/api/v1/services/aigc/video-generation/video-synthesis"
    assert options["headers"] == {
        "Authorization": "Bearer test-only-secret", "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    assert options["allow_redirects"] is False
    assert options["json"] == {
        "model": model_id,
        "input": {"prompt": "A slow sunrise", "media": [
            {"type": "first_frame", "url": request.media[0].url},
        ]},
        "parameters": {"duration": -1, "resolution": "1080P", "ratio": "21:9",
                       "seed": seed, "prompt_extend": False, "watermark": False, "audio": False},
    }


def test_wan3_does_not_refill_omitted_frozen_parameters(submission, model, credential, transport):
    request = wan3_request(submission)
    request = replace(request, intent=replace(request.intent, params={"duration": 30, "resolution": "720P"}))
    model.submit(request, credential=replace(credential, scope=request.scope), on_submitted=lambda _: None)
    assert transport[1][0][1]["json"]["parameters"] == {"duration": 30, "resolution": "720P"}


@pytest.mark.parametrize("params", [
    {"duration": 0}, {"duration": -2}, {"duration": 31}, {"duration": True}, {"duration": 5.0},
    {"duration": None}, {"resolution": None}, {"resolution": "360p"},
    {"resolution": "480p"}, {"resolution": "720p"}, {"resolution": "1080p"},
    {"seed": True}, {"seed": -2}, {"seed": 2147483648}, {"seed": "0"},
    {"ratio": "2:1"}, {"ratio": False}, {"audio": 0}, {"watermark": 1},
    {"prompt_extend": "false"}, {"negative_prompt": ""}, {"shot_type": "single"},
])
def test_wan3_invalid_frozen_params_fail_before_post(submission, model, credential, transport, params):
    request = wan3_request(submission, **params)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)
    with pytest.raises(ProviderBindingError):
        model.submit(request, credential=replace(credential, scope=request.scope), on_submitted=lambda _: None)
    assert transport[1] == []


@pytest.mark.parametrize("mutation", ["classic", "wrong-canonical", "wrong-mode", "wrong-catalog-mode", "reference", "two-frames"])
def test_wan3_rejects_incomplete_or_mismatched_binding(submission, transport, mutation):
    request = wan3_request(submission)
    if mutation == "classic":
        request = replace(request, scope=submission.scope)
    elif mutation == "wrong-canonical":
        request = replace(request, intent=replace(request.intent, model_id="wan/wan3.0-video-prime#video"))
    elif mutation == "wrong-mode":
        request = replace(request, intent=replace(request.intent, mode="t2v"))
    elif mutation == "wrong-catalog-mode":
        request = replace(request, intent=replace(request.intent, catalog_mode="i2v"))
    elif mutation == "reference":
        media = prepared_image("reference_image")
        request = replace(request, media=(media,))
    else:
        request = replace(request, media=request.media * 2)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(request)
    assert transport[1] == []


@pytest.mark.parametrize("prompt", ["", "x" * 20000], ids=["empty-with-frame", "max-length"])
def test_wan3_first_frame_accepts_documented_prompt_boundary(submission, prompt):
    request = wan3_request(submission)
    assert WanxModel.validate_submission(replace(request, intent=replace(request.intent, prompt=prompt))) is None


def test_wan3_rejects_prompt_that_provider_would_truncate(submission):
    request = wan3_request(submission)
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(replace(request, intent=replace(request.intent, prompt="x" * 20001)))


@pytest.mark.parametrize("resolution", ["480P", "720P", "1080P"])
@pytest.mark.parametrize("ratio", ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"])
def test_wan3_accepts_documented_resolution_and_ratio(submission, resolution, ratio):
    assert WanxModel.validate_submission(wan3_request(submission, resolution=resolution, ratio=ratio)) is None


@pytest.mark.parametrize("model_id", ["wan3.0-video", "wan3.0-video-prime"])
def test_wan3_record_recovers_original_bytes_without_defaults(
    inputs, submission, model, credential, transport, monkeypatch, model_id,
):
    root, lease, kwargs = inputs
    (root / "uploads/frame.png").write_bytes(rgb_png_bytes())
    version = freeze_media("uploads/frame.png", output_root=root, writer_lock=lease, role="first_frame", ordinal=0, max_bytes=1024)
    kwargs.update(media_version=version, prepared_media=PreparedMedia.from_media_version(version, output_root=root, max_bytes=1024))
    request = wan3_request(submission, model_id, seed=-1, duration=-1, audio=False)
    kwargs.update(provider_model_id=model_id, scope=request.scope)
    accepted, record = build_accepted_provider_submission_and_record(request.intent, **kwargs)
    persisted = ProviderExecutionRecord.model_validate_json(record.model_dump_json())
    (root / "uploads/frame.png").unlink()
    monkeypatch.setattr(
        "src.utils.generation_intent.get_catalog_accessor", lambda: pytest.fail("catalog reread"),
    )
    recovered = reconstruct_submission_from_record(persisted, output_root=root)
    assert recovered == accepted
    assert recovered.intent.params["seed"] == -1 and recovered.intent.params["audio"] is False
    saved = []
    bound = replace(credential, scope=request.scope, credential_ref=kwargs["credential_ref"])
    handle = model.submit(recovered, credential=bound, on_submitted=saved.append)
    assert saved == [handle] and len(transport[1]) == 1
    assert transport[1][0][1]["json"]["input"]["media"][0]["url"] == accepted.media[0].url
