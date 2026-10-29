"""Vidu's four Playground routes retain Core receipts through HTTP and recovery."""
from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_lumenx_generation_browser_smoke import evidence_payload, load_smoke_module, write_static_root
from tests.test_playground_kling_wire_flow import smoke_module, _wait_generation, _write_image  # noqa: F401
from tests.test_wan3_image_validation import WEBP_240_BASE64

ROUTES = [
    ("viduq3-pro-i2v", "vidu/viduq3-pro_img2video", "i2v"),
    ("viduq3-turbo-i2v", "vidu/viduq3-turbo_img2video", "i2v"),
    ("viduq3-pro-r2v", "vidu/viduq3-mix_reference2video", "r2v"),
    ("viduq3-turbo-r2v", "vidu/viduq3-turbo_reference2video", "r2v"),
]


@pytest.mark.parametrize("model,provider_model,mode", ROUTES)
def test_vidu_http_freeze_submit_fetch_and_changed_source_replay(tmp_path, monkeypatch, smoke_module, model, provider_model, mode):
    from src.apps.playground import service
    def fail_real_runtime(*args, **kwargs):
        raise AssertionError("real provider runtime must not be built")
    monkeypatch.setattr(service, "build_provider_runtime_for_admission", fail_real_runtime)
    smoke = smoke_module
    data_root = tmp_path / "data"
    data_root.mkdir()
    output_root = data_root / "output"
    refs = [_write_image(output_root, "first.webp", data=base64.b64decode(WEBP_240_BASE64))]
    if mode == "r2v":
        refs.append(_write_image(output_root, "second.png", data=rgb_png_bytes("vidu-second", width=512, height=512)))
    hashes = [hashlib.sha256((output_root / ref).read_bytes()).hexdigest() for ref in refs]
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_vidu=True)
    params = {} if model == "viduq3-pro-i2v" else {"duration": 1, "resolution": "1080P", "audio": False, "watermark": False, "seed": 0}
    if mode == "r2v":
        params["aspect_ratio"] = "4:3"
    payload = {"client_request_id": model, "mode": mode, "model_id": model,
               "prompt": "Frozen Vidu wire test", "parameters": params, "input_media": refs, "batch_size": 2}
    with TestClient(app) as http:
        response = http.post("/playground/generate", json=payload)
        assert response.status_code == 200, response.text
        generation = _wait_generation(http, model)
        assert generation["status"] == "completed", generation
        assert len(generation["outputs"]) == 2
        for output in generation["outputs"]:
            assert http.get(f"/files/{output['media_path']}").content == b"fake-transport-result"
        for ref in refs:
            (output_root / ref).write_bytes(b"source changed after acceptance")
        assert http.get(f"/playground/requests/{model}").json()["generation"] == generation
        replay = http.post("/playground/generate", json=payload)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["generation"] == generation
        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)
    assert evidence.payload["fake_vidu"] is True
    assert evidence.payload["fake_provider"]["counts"] == {"submit": 2, "query": 2, "fetch": 2}
    assert evidence.payload["fake_upload"]["counts"] == {"upload": 2 * len(refs)}
    uploads = evidence.payload["fake_upload"]["events"]
    assert [item["sha256"] for item in uploads] == hashes * 2
    assert all(item["provider_model_id"] == provider_model for item in uploads)
    assert all(item["scope"]["gateway"] == "dashscope_workspace" for item in uploads)
    submitted = [item["request_summary"] for item in evidence.payload["fake_provider"]["events"] if item["kind"] == "submit"]
    expected_params = {"duration": 5, "resolution": "720P", "audio": False, "watermark": False} if not params else params
    for summary in submitted:
        assert summary["provider_model_id"] == provider_model
        assert summary["intent"]["params"] == expected_params
        assert [item["role"] for item in summary["media"]] == (["first_frame"] if mode == "i2v" else ["reference_image"] * len(refs))
        assert [item["ordinal"] for item in summary["media"]] == list(range(len(refs)))
        assert all(item["transport_binding"] == "dashscope_oss_sha256_v1" for item in summary["media"])


def test_vidu_flag_is_required_and_does_not_enable_other_workspace_models(tmp_path, smoke_module):
    for enabled in (False, True):
        data_root = tmp_path / str(enabled)
        data_root.mkdir()
        evidence = smoke_module.Evidence(tmp_path / f"evidence-{enabled}.json", evidence_payload("disabled"))
        app = smoke_module.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_vidu=enabled)
        with TestClient(app):
            selector = app.state.provider_runtime_selector
            for _, model, _ in ROUTES:
                result = selector.for_admission(provider_model_id=model, gateway="dashscope_workspace")
                assert (result.runtime is not None) is enabled
            for model in ("wan3.0-video", "kling/kling-v3-video-generation", "happyhorse-1.1-i2v"):
                assert selector.for_admission(provider_model_id=model, gateway="dashscope_workspace").runtime is None


def test_vidu_fake_webp_transport_keeps_kling_mime_limit(tmp_path):
    from src.models.provider_tasks import ProviderScope, ResolvedCredential
    from src.models.provider_upload import ProviderUploadError
    from src.utils.media_versions import MediaVersion
    smoke = load_smoke_module()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    scope = ProviderScope("dashscope", "dashscope", "dashscope_workspace", smoke.SMOKE_WORKSPACE_BASE_URL,
                          smoke.SMOKE_WORKSPACE_REGION, workspace_id=smoke.SMOKE_WORKSPACE_ID)
    credential = ResolvedCredential(scope, "credentials/fake", "fake-secret")
    data = base64.b64decode(WEBP_240_BASE64)
    media = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/webp")
    uploader = smoke.FakeDashScopeUploader(evidence)
    with pytest.raises(ProviderUploadError):
        uploader(scope=scope, provider_model_id="kling/kling-v3-video-generation", credential=credential, version=media, data=data)
    result = uploader(scope=scope, provider_model_id=ROUTES[0][1], credential=credential, version=media, data=data)
    assert result.key.endswith(".webp")


def test_vidu_http_invalid_requests_leave_no_upload_submit_or_receipt(tmp_path, smoke_module):
    smoke = smoke_module
    data_root = tmp_path / "data"
    data_root.mkdir()
    frame = _write_image(data_root / "output", "first.png")
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_vidu=True)
    cases = [
        {"mode": "t2v", "model_id": "viduq3-pro-i2v"},
        {"mode": "r2v", "model_id": "viduq3-pro-r2v", "parameters": {"resolution": "540P"}},
        {"mode": "i2v", "model_id": "viduq3-pro-i2v", "negative_prompt": "unsupported"},
        {"mode": "i2v", "model_id": "viduq3-pro-i2v", "parameters": {"movement_amplitude": "auto"}},
    ]
    with TestClient(app) as http:
        for index, overrides in enumerate(cases):
            request_id = f"invalid-vidu-{index}"
            payload = {"client_request_id": request_id, "prompt": "Invalid request", "input_media": [frame], **overrides}
            response = http.post("/playground/generate", json=payload)
            assert response.status_code == 422, response.text
            assert http.get(f"/playground/requests/{request_id}").status_code == 404
        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)
    assert evidence.payload["fake_provider"]["counts"] == {"submit": 0, "query": 0, "fetch": 0}
    assert evidence.payload.get("fake_upload", {}).get("counts", {"upload": 0}) == {"upload": 0}
