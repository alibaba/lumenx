"""PixVerse images use the shared frozen transport with exact model limits."""

import base64
from dataclasses import replace
import hashlib

import pytest

from src.models.provider_image_policy import get_provider_image_policy, recovery_image_max_bytes
from src.models.provider_recovery import ProviderRecoveryError, prepare_submission_transport
from src.models.provider_tasks import PreparedMedia, ProviderBindingError, ProviderSubmission
from src.models.provider_upload import ProviderUploadResult, upload_dashscope_temporary_media
from src.utils.generation_intent import GenerationIntent
from src.utils.media_versions import MediaVersion
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_kling_image_transport import CREDENTIAL, SCOPE, Response, _policy_payload
from tests.test_wan3_image_validation import WEBP_240_BASE64


MB = 1024 * 1024
MODELS = [
    ("pixverse/pixverse-v6-it2v", "pixverse/pixverse/pixverse-v6-video#i2v", "i2v", "first_frame"),
    ("pixverse/pixverse-c1-it2v", "pixverse/pixverse-c1-video#i2v", "i2v", "first_frame"),
    ("pixverse/pixverse-c1-r2v", "pixverse/pixverse-c1-video#r2v", "r2v", "reference_image"),
    ("pixverse/pixverse-v5.6-r2v", "pixverse/pixverse-v5.6-r2v#r2v", "r2v", "reference_image"),
]


def _version(data, role, ordinal=0, mime="image/png"):
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


@pytest.mark.parametrize("model,canonical,mode,role", MODELS)
def test_pixverse_policy_is_exact_and_does_not_widen_other_provider_limits(model, canonical, mode, role):
    policy = get_provider_image_policy(model)
    assert policy is not None
    assert policy.role == role
    assert dict(policy.mime_suffixes) == {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
    assert policy.max_bytes == recovery_image_max_bytes(model, 50 * MB) == 20 * MB
    assert recovery_image_max_bytes(model, 1) == 1
    assert recovery_image_max_bytes("kling/kling-v3-video-generation") == 10 * MB
    assert recovery_image_max_bytes("vidu/viduq3-pro_img2video") == 50 * MB
    assert get_provider_image_policy("pixverse/pixverse-v6-r2v") is None
    assert get_provider_image_policy(canonical) is None


@pytest.mark.parametrize("model,canonical,mode,role", MODELS)
def test_pixverse_webp_upload_and_typed_factory_bind_exact_model(monkeypatch, model, canonical, mode, role):
    data = base64.b64decode(WEBP_240_BASE64)
    version = _version(data, role, mime="image/webp")
    calls = []
    def get(url, **kwargs):
        calls.append(("get", kwargs))
        return Response(200, _policy_payload(max_file_size_mb=20))
    def post(url, **kwargs):
        calls.append(("post", kwargs))
        return Response(200)
    monkeypatch.setattr("src.models.provider_upload.requests.get", get)
    monkeypatch.setattr("src.models.provider_upload.requests.post", post)
    result = upload_dashscope_temporary_media(scope=SCOPE, provider_model_id=model,
                credential=CREDENTIAL, version=version, data=data)
    assert result.provider_model_id == model
    assert result.url.endswith(".webp")
    assert calls[0][1]["params"] == {"action": "getPolicy", "model": model}
    assert calls[1][1]["files"][-1][1][1:] == (data, "image/webp")
    image = PreparedMedia.from_verified_provider_image_url(version, upload_result=result, data=data)
    assert image.version == version
    assert image.frozen_image_metadata.width == 240


@pytest.mark.parametrize("model,canonical,mode,role", MODELS)
def test_pixverse_recovery_upload_submit_keep_original_frozen_bytes_and_order(tmp_path, monkeypatch, model, canonical, mode, role):
    from src.models.provider_execution import ProviderExecutionRecord
    from src.models.provider_recovery import build_accepted_provider_submission_and_record, reconstruct_submission_from_record
    from src.models.wanx import WanxModel
    from src.utils.json_storage import DirectoryWriterLock
    from src.utils.media_versions import freeze_media

    (tmp_path / "uploads").mkdir()
    data = [rgb_png_bytes(f"pixverse-{n}", width=100, height=800) for n in range(2 if mode == "r2v" else 1)]
    images = []
    for n, raw in enumerate(data):
        source = tmp_path / "uploads" / f"{n}.png"
        source.write_bytes(raw)
        with DirectoryWriterLock(tmp_path) as lease:
            version = freeze_media(f"uploads/{n}.png", output_root=tmp_path, writer_lock=lease,
                                   role=role, ordinal=n, max_bytes=20 * MB)
        images.append(PreparedMedia.from_media_version(version, output_root=tmp_path, max_bytes=20 * MB))
    params = {"duration": 5, "resolution": "720P", "audio": False, "watermark": False, "seed": 0}
    if mode == "r2v":
        params["aspect_ratio"] = "4:3"
    else:
        if model == MODELS[0][0]:
            params["shot_type"] = "multi"
    intent = GenerationIntent(model_id=canonical, mode=mode, catalog_mode=mode,
                              prompt="Preserve @image1 and @image2 without rewriting.", params=params,
                              sources={}, request_fields=())
    accepted, record = build_accepted_provider_submission_and_record(intent, provider_model_id=model,
        media=tuple(images), scope=SCOPE, credential_ref=CREDENTIAL.credential_ref, attempt_id="pixverse-recovery")
    persisted = ProviderExecutionRecord.model_validate_json(record.model_dump_json())
    snapshot = persisted.model_dump_json()
    for n in range(len(data)):
        (tmp_path / "uploads" / f"{n}.png").write_bytes(b"source changed after acceptance")
    recovered = reconstruct_submission_from_record(persisted, output_root=tmp_path)
    assert recovered == accepted
    uploads = []
    def uploader(**kwargs):
        uploads.append(kwargs)
        key = f"test/pixverse/{kwargs['version'].ordinal}.png"
        return ProviderUploadResult(f"oss://{key}", key, model, kwargs["version"], SCOPE, CREDENTIAL.credential_ref)
    prepared = prepare_submission_transport(recovered, CREDENTIAL, uploader=uploader)
    calls = []
    class SubmitResponse:
        status_code = 200
        def json(self):
            return {"output": {"task_id": "synthetic-pixverse-handle"}}
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SubmitResponse()
    monkeypatch.setattr("src.models.wanx.requests.post", post)
    handle = WanxModel({"output_root": str(tmp_path)}, env={}).submit(prepared,
                    credential=CREDENTIAL, on_submitted=lambda _: None)
    assert handle.task_id == "synthetic-pixverse-handle"
    assert [item["data"] for item in uploads] == data
    assert [item["provider_model_id"] for item in uploads] == [model] * len(data)
    assert [item["version"] for item in uploads] == [image.version for image in images]
    url, request = calls[0]
    assert url == SCOPE.base_url + "/api/v1/services/aigc/video-generation/video-synthesis"
    assert request["headers"]["X-DashScope-OssResourceResolve"] == "enable"
    assert request["json"]["input"] == {"prompt": intent.prompt, "media": [
        {"type": "image_url", "url": f"oss://test/pixverse/{n}.png"} for n in range(len(data))]}
    expected = dict(params)
    if mode == "r2v":
        expected.pop("aspect_ratio")
        expected.pop("resolution")
        expected["size"] = "1108*832" if model == MODELS[2][0] else "1108*830"
    assert request["json"]["parameters"] == expected
    assert len(calls) == 1
    assert persisted.model_dump_json() == snapshot


@pytest.mark.parametrize("model,canonical,mode,role", MODELS)
def test_pixverse_runtime_constructs_only_exact_beijing_workspace_model(tmp_path, model, canonical, mode, role):
    from src.models.provider_runtime import build_provider_runtime_for_admission
    from src.utils.runtime_environment import RuntimeEnvironment
    values = {"DASHSCOPE_API_KEY": "synthetic-key", "DASHSCOPE_WORKSPACE_ID": SCOPE.workspace_id,
              "DASHSCOPE_REGION": SCOPE.region, "DASHSCOPE_BASE_URL": SCOPE.base_url}
    result = build_provider_runtime_for_admission(RuntimeEnvironment(values), output_root=tmp_path,
                provider_model_id=model, gateway="dashscope_workspace")
    assert result.reason is None
    assert result.runtime.scope == SCOPE
    assert result.runtime.resolve_secret() == "synthetic-key"


@pytest.mark.parametrize("bad,reason", [
    ({"gateway": "dashscope"}, "unsupported_dashscope_gateway"),
    ({"DASHSCOPE_REGION": "ap-southeast-1", "DASHSCOPE_BASE_URL": "https://kling-space.ap-southeast-1.maas.aliyuncs.com"}, "unsupported_provider_region"),
    ({"DASHSCOPE_BASE_URL": "https://example.com"}, "unsupported_dashscope_workspace_scope"),
    ({"DASHSCOPE_WORKSPACE_ID": ""}, "missing_dashscope_workspace"),
])
@pytest.mark.parametrize("model,canonical,mode,role", MODELS)
def test_pixverse_runtime_rejects_wrong_scope_before_client_creation(tmp_path, monkeypatch, model, canonical, mode, role, bad, reason):
    from src.models.provider_runtime import build_provider_runtime_for_admission
    from src.utils.runtime_environment import RuntimeEnvironment
    def no_client(*args, **kwargs):
        pytest.fail("invalid scope constructed a provider client")
    monkeypatch.setattr("src.models.provider_runtime.WanxModel", no_client)
    values = {"DASHSCOPE_API_KEY": "synthetic-key", "DASHSCOPE_WORKSPACE_ID": SCOPE.workspace_id,
              "DASHSCOPE_REGION": SCOPE.region, "DASHSCOPE_BASE_URL": SCOPE.base_url, **bad}
    result = build_provider_runtime_for_admission(RuntimeEnvironment(values), output_root=tmp_path,
                provider_model_id=model, gateway=values.pop("gateway", "dashscope_workspace"))
    assert result.runtime is None
    assert result.reason == reason


@pytest.mark.parametrize("bad", ["params", "mode", "scope", "size", "duration"])
def test_pixverse_preparation_rejects_invalid_submission_before_any_upload(monkeypatch, bad):
    data = rgb_png_bytes("pixverse-preflight", width=100, height=100)
    image = PreparedMedia.from_verified_bytes(_version(data, "first_frame"), data, max_bytes=20 * MB)
    params = {"duration": 5, "resolution": "720P"}
    if bad == "params":
        params["negative_prompt"] = "unsupported"
    if bad == "duration":
        params.pop("duration")
    if bad == "size":
        image = PreparedMedia.from_verified_bytes(_version(rgb_png_bytes("oversized-edge", width=10001, height=1), "first_frame"),
                    rgb_png_bytes("oversized-edge", width=10001, height=1), max_bytes=20 * MB)
    intent = GenerationIntent(model_id=MODELS[0][1], mode="r2v" if bad == "mode" else "i2v",
                              catalog_mode="i2v", prompt="Unchanged", params=params, sources={}, request_fields=())
    scope = replace(SCOPE, region="ap-southeast-1",
                    base_url="https://kling-space.ap-southeast-1.maas.aliyuncs.com") if bad == "scope" else SCOPE
    request = ProviderSubmission(intent=intent, provider_model_id=MODELS[0][0], media=(image,),
                                 scope=scope, credential_ref=CREDENTIAL.credential_ref)
    calls = []
    def uploader(**kwargs):
        calls.append(kwargs)
        pytest.fail("invalid request uploaded an image")
    with pytest.raises((ProviderRecoveryError, ProviderBindingError)):
        prepare_submission_transport(request, replace(CREDENTIAL, scope=scope), uploader=uploader)
    assert calls == []


def test_pixverse_actual_20_mib_boundary_is_enforced_before_upload_http(monkeypatch):
    from src.models.provider_upload import ProviderUploadError
    from tests.test_vidu_image_transport import large_png

    model = MODELS[0][0]
    data = large_png(20 * MB)
    version = _version(data, "first_frame")
    result = ProviderUploadResult("oss://test/pixverse/boundary.png", "test/pixverse/boundary.png",
                                  model, version, SCOPE, CREDENTIAL.credential_ref)
    prepared = PreparedMedia.from_verified_provider_image_url(version, upload_result=result, data=data)
    assert prepared.version.size_bytes == 20 * MB
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *args, **kwargs: calls.append("get"))
    monkeypatch.setattr("src.models.provider_upload.requests.post", lambda *args, **kwargs: calls.append("post"))
    oversized = large_png(20 * MB + 1)
    with pytest.raises(ProviderUploadError):
        upload_dashscope_temporary_media(scope=SCOPE, provider_model_id=model,
            credential=CREDENTIAL, version=_version(oversized, "first_frame"), data=oversized)
    assert calls == []
