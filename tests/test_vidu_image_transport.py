"""Vidu's larger images retain model-specific limits across frozen transport."""
from dataclasses import replace
import base64
import hashlib
import struct
import zlib
from typing import Any, cast

import pytest

from src.models.provider_tasks import PreparedMedia, ProviderBindingError
from src.models.provider_upload import ProviderUploadError, ProviderUploadResult, upload_dashscope_temporary_media
from src.utils.media_probe import MediaProbeError, probe_frozen_image
from src.utils.media_versions import MediaVersion
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_kling_image_transport import CREDENTIAL, SCOPE, Response, _policy_payload
from tests.test_wan3_image_validation import WEBP_240_BASE64

MB = 1024 * 1024
VIDU_MODELS = [
    ("vidu/viduq3-pro_img2video", "first_frame"),
    ("vidu/viduq3-turbo_img2video", "first_frame"),
    ("vidu/viduq3-mix_reference2video", "reference_image"),
    ("vidu/viduq3-turbo_reference2video", "reference_image"),
]


def version(data, role="first_frame", mime="image/png"):
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, 0, mime)


def uploaded(model, media):
    key = "test/vidu/image"
    return ProviderUploadResult(f"oss://{key}", key, model, media, SCOPE, CREDENTIAL.credential_ref)


def large_png(size):
    image = rgb_png_bytes("vidu-large", width=512, height=512)
    # A valid ancillary PNG chunk exercises byte limits without a giant raster.
    content = b"Comment\0" + b"x" * (size - len(image) - 20)
    chunk = b"tEXt" + content
    return image[:-12] + struct.pack(">I", len(content)) + chunk + struct.pack(">I", zlib.crc32(chunk)) + image[-12:]


@pytest.mark.parametrize("model,role", VIDU_MODELS)
def test_vidu_webp_upload_and_typed_factory_use_exact_model(monkeypatch, model, role):
    data = base64.b64decode(WEBP_240_BASE64)
    media = version(data, role, "image/webp")
    calls = []
    def get(url, **kwargs):
        calls.append(("get", kwargs))
        return Response(200, _policy_payload(max_file_size_mb=50))
    def post(url, **kwargs):
        calls.append(("post", kwargs))
        return Response(200)
    monkeypatch.setattr("src.models.provider_upload.requests.get", get)
    monkeypatch.setattr("src.models.provider_upload.requests.post", post)
    result = upload_dashscope_temporary_media(scope=SCOPE, provider_model_id=model, credential=CREDENTIAL, version=media, data=data)
    prepared = PreparedMedia.from_verified_provider_image_url(media, upload_result=result, data=data)
    assert result.key.endswith(".webp")
    assert calls[0][1]["params"]["model"] == model
    assert calls[1][1]["files"][-1][1][1:] == (data, "image/webp")
    assert prepared.frozen_image_metadata is not None
    assert prepared.frozen_image_metadata.width == 240
    assert prepared.transport_binding == "dashscope_oss_sha256_v1"


def test_vidu_exact_fifty_mb_is_probeable_and_bound_without_loosening_default():
    data = large_png(50 * MB)
    assert len(data) == 50 * MB
    media = version(data)
    with pytest.raises(MediaProbeError):
        probe_frozen_image(media, data)
    assert probe_frozen_image(media, data, max_bytes=50 * MB).width == 512
    prepared = PreparedMedia.from_verified_provider_image_url(media, upload_result=uploaded(VIDU_MODELS[0][0], media), data=data)
    assert prepared.version.sha256 == media.sha256
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_image_url(media, upload_result=uploaded("kling/kling-v3-video-generation", media), data=data)


@pytest.mark.parametrize("model,role,mime,size", [
    (VIDU_MODELS[0][0], "first_frame", "image/png", 50 * MB + 1),
    (VIDU_MODELS[0][0], "reference_image", "image/png", 1),
    (VIDU_MODELS[2][0], "first_frame", "image/png", 1),
    (VIDU_MODELS[0][0], "first_frame", "image/bmp", 1),
    ("kling/kling-v3-video-generation", "first_frame", "image/png", 10 * MB + 1),
    ("kling/kling-v3-video-generation", "first_frame", "image/webp", 1),
    ("unrecognized-provider", "first_frame", "image/png", 1),
])
def test_uploaded_image_factory_does_not_cross_model_limits(model, role, mime, size):
    data = b"x"
    media = MediaVersion(hashlib.sha256(data).hexdigest(), size, role, 0, mime)
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_image_url(media, upload_result=uploaded(model, media), data=data)


def test_unknown_model_cannot_wrap_a_valid_image_as_verified_provider_transport():
    data = rgb_png_bytes(width=512, height=512)
    media = version(data)
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_provider_image_url(media, upload_result=uploaded("unknown", media), data=data)


def test_vidu_scope_role_and_limit_rejection_precedes_upload_policy(monkeypatch):
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *a, **kw: calls.append(kw))
    data = rgb_png_bytes(width=512, height=512)
    media = version(data)
    classic = replace(SCOPE, gateway="dashscope", base_url="https://dashscope.aliyuncs.com", workspace_id=None)
    for scope, candidate in [(classic, media), (SCOPE, replace(media, role="reference_image")), (SCOPE, replace(media, size_bytes=50 * MB + 1))]:
        with pytest.raises(ProviderUploadError):
            upload_dashscope_temporary_media(scope=scope, provider_model_id=VIDU_MODELS[0][0], credential=replace(CREDENTIAL, scope=scope), version=candidate, data=data)
    assert calls == []


@pytest.mark.parametrize("model,role", VIDU_MODELS)
def test_vidu_recovery_upload_and_submit_keep_frozen_identity(tmp_path, monkeypatch, model, role):
    from src.models.provider_execution import ProviderExecutionRecord
    from src.models.provider_recovery import (
        build_accepted_provider_submission_and_record,
        reconstruct_submission_from_record,
        prepare_submission_transport,
    )
    from src.models.vidu_core_contract import VIDU_PROVIDER_CONTRACT
    from src.models.wanx import WanxModel
    from src.utils.generation_intent import GenerationIntent
    from src.utils.json_storage import DirectoryWriterLock
    from src.utils.media_versions import freeze_media

    # Only one case needs the large file; all four exact model routes must submit.
    data = large_png(50 * MB) if model == VIDU_MODELS[0][0] else rgb_png_bytes(model, width=512, height=512)
    source = tmp_path / "uploads" / "original.png"
    source.parent.mkdir()
    source.write_bytes(data)
    canonical, mode = VIDU_PROVIDER_CONTRACT[model]
    params = {"duration": 5, "resolution": "720P", "audio": False, "watermark": False, "seed": 0}
    if mode == "r2v":
        params["aspect_ratio"] = "16:9"
    intent = GenerationIntent(model_id=canonical, mode=mode, catalog_mode=mode,
                              prompt="Keep the accepted Vidu identity.", params=params,
                              sources={}, request_fields=())
    with DirectoryWriterLock(tmp_path) as lease:
        frozen = freeze_media("uploads/original.png", output_root=tmp_path, writer_lock=lease,
                              role=role, ordinal=0, max_bytes=50 * MB)
    image = PreparedMedia.from_media_version(frozen, output_root=tmp_path, max_bytes=50 * MB)
    accepted, record = build_accepted_provider_submission_and_record(
        intent, provider_model_id=model, media=(image,), scope=SCOPE,
        credential_ref=CREDENTIAL.credential_ref, attempt_id="vidu-frozen-recovery",
    )
    persisted = ProviderExecutionRecord.model_validate_json(record.model_dump_json())
    snapshot = persisted.model_dump_json()
    source.write_bytes(b"changed source must never be read during recovery")
    recovered = reconstruct_submission_from_record(persisted, output_root=tmp_path)
    assert recovered == accepted
    if len(data) > 20 * MB:
        with pytest.raises(ProviderBindingError):
            reconstruct_submission_from_record(persisted, output_root=tmp_path, max_bytes=20 * MB)
    uploads = []
    def uploader(**kwargs):
        uploads.append(kwargs)
        return uploaded(kwargs["provider_model_id"], kwargs["version"])
    prepared = prepare_submission_transport(recovered, CREDENTIAL, uploader=uploader)
    calls = []
    class SubmitResponse:
        status_code = 200
        def json(self):
            return {"output": {"task_id": "synthetic-vidu-handle"}}
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SubmitResponse()
    monkeypatch.setattr("src.models.wanx.requests.post", post)
    handle = WanxModel({"output_root": str(tmp_path)}, env={}).submit(
        prepared, credential=CREDENTIAL, on_submitted=lambda _: None,
    )
    assert handle.task_id == "synthetic-vidu-handle"
    assert uploads[0]["data"] == data
    assert uploads[0]["provider_model_id"] == model
    assert uploads[0]["version"] == frozen
    url, request = calls[0]
    assert url == SCOPE.base_url + "/api/v1/services/aigc/video-generation/video-synthesis"
    assert request["headers"]["X-DashScope-OssResourceResolve"] == "enable"
    assert request["json"]["model"] == model
    assert request["json"]["input"]["media"] == [{"type": "image", "url": "oss://test/vidu/image"}]
    expected = dict(params)
    if mode == "r2v":
        expected.pop("aspect_ratio")
        expected["size"] = "1280*720"
    assert request["json"]["parameters"] == expected
    assert len(calls) == len(uploads) == 1
    assert persisted.model_dump_json() == snapshot


@pytest.mark.parametrize("model,role", VIDU_MODELS)
def test_vidu_runtime_selects_exact_workspace_model(tmp_path, model, role):
    from src.models.provider_runtime import build_provider_runtime_for_admission
    from src.utils.runtime_environment import RuntimeEnvironment
    values = {"DASHSCOPE_API_KEY": "synthetic-key", "DASHSCOPE_WORKSPACE_ID": SCOPE.workspace_id,
              "DASHSCOPE_REGION": SCOPE.region, "DASHSCOPE_BASE_URL": SCOPE.base_url}
    result = build_provider_runtime_for_admission(RuntimeEnvironment(values), output_root=tmp_path,
                                                provider_model_id=model, gateway="dashscope_workspace")
    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == SCOPE
    assert result.runtime.resolve_secret() == "synthetic-key"


@pytest.mark.parametrize("model,expected", [(VIDU_MODELS[0][0], 50 * MB), ("kling/kling-v3-video-generation", 10 * MB), ("wan3.0-video", 20 * MB)])
def test_recovery_cap_is_model_bound_and_explicit_override_only_restricts(model, expected):
    from src.models.provider_image_policy import recovery_image_max_bytes
    assert recovery_image_max_bytes(model) == expected
    assert recovery_image_max_bytes(model, 100 * MB) == expected
    assert recovery_image_max_bytes(model, 1) == 1
    for invalid in (0, -1, True, "50"):
        with pytest.raises(ValueError):
            recovery_image_max_bytes(model, cast(Any, invalid))


@pytest.mark.parametrize("model", [model for model, _ in VIDU_MODELS] + ["kling/kling-v3-video-generation", "kling/kling-v3-omni-video-generation"])
def test_beijing_only_model_runtime_rejects_other_valid_workspace_region(tmp_path, model):
    from src.models.provider_runtime import build_provider_runtime_for_admission
    from src.utils.runtime_environment import RuntimeEnvironment
    result = build_provider_runtime_for_admission(RuntimeEnvironment({
        "DASHSCOPE_WORKSPACE_ID": "synthetic-space", "DASHSCOPE_REGION": "ap-southeast-1",
        "DASHSCOPE_BASE_URL": "https://synthetic-space.ap-southeast-1.maas.aliyuncs.com",
        "DASHSCOPE_API_KEY": "synthetic-key",
    }), output_root=tmp_path, provider_model_id=model, gateway="dashscope_workspace")
    assert result.runtime is None
    assert result.reason == "unsupported_provider_region"
