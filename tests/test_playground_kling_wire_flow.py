"""Kling Playground HTTP wire flow stays on Core with local fake transport."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_lumenx_generation_browser_smoke import (
    evidence_payload,
    load_smoke_module,
    wait_for_counts,
    write_static_root,
)
from tests.test_wan3_image_validation import png_bytes


@pytest.fixture
def smoke_module(monkeypatch):
    smoke = load_smoke_module()

    def fixture(self):
        self.provider_root.mkdir(parents=True, exist_ok=True)
        path = self.provider_root / "transport.mp4"
        path.write_bytes(b"fake-transport-result")
        return path

    monkeypatch.setattr(smoke.FakeWanProvider, "_ensure_fixture", fixture)
    return smoke


def _write_image(
    output_root: Path,
    name: str,
    *,
    width: int = 512,
    height: int = 512,
    data: bytes | None = None,
) -> str:
    path = output_root / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else rgb_png_bytes(name, width=width, height=height))
    return f"uploads/{name}"


def _wait_generation(http: TestClient, request_id: str) -> dict:
    deadline = time.monotonic() + 5
    while True:
        receipt = http.get(f"/playground/requests/{request_id}")
        assert receipt.status_code == 200, receipt.text
        generation = receipt.json()["generation"]
        if generation["status"] not in {"pending", "processing"} or time.monotonic() >= deadline:
            return generation
        time.sleep(0.02)


def test_fake_kling_playground_i2v_uploads_one_frame_then_replay_never_resubmits(tmp_path, monkeypatch, smoke_module):
    smoke = smoke_module
    from src.apps.playground import service as playground_service

    monkeypatch.setattr(
        playground_service,
        "build_provider_runtime_for_admission",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("real Playground provider must not be built")),
    )
    data_root = tmp_path / "data"
    output_root = data_root / "output"
    data_root.mkdir()
    frame = _write_image(output_root, "first.png", data=rgb_png_bytes("kling-i2v-first", width=640, height=480))
    expected_hash = hashlib.sha256((output_root / frame).read_bytes()).hexdigest()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_kling=True)

    payload = {
        "client_request_id": "kling-i2v-wire",
        "mode": "i2v",
        "model_id": "kling-v3-i2v",
        "prompt": "A precise Kling I2V wire request.",
        "negative_prompt": "low detail",
        "input_media": [frame],
        "parameters": {"mode": "pro", "duration": 5, "audio": False, "watermark": False},
        "batch_size": 2,
    }
    with TestClient(app) as http:
        response = http.post("/playground/generate", json=payload)
        assert response.status_code == 200, response.text
        generation = _wait_generation(http, "kling-i2v-wire")
        assert generation["status"] == "completed", generation
        assert len(generation["outputs"]) == 2
        for output in generation["outputs"]:
            assert (output_root / output["media_path"]).read_bytes() == b"fake-transport-result"
            assert http.get(f"/files/{output['media_path']}").content == b"fake-transport-result"
        replay = http.post("/playground/generate", json=payload)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)

    assert evidence.payload["fake_provider"]["counts"] == {"submit": 2, "query": 2, "fetch": 2}
    assert evidence.payload["fake_upload"]["counts"] == {"upload": 2}
    uploads = evidence.payload["fake_upload"]["events"]
    assert [item["provider_model_id"] for item in uploads] == [
        "kling/kling-v3-video-generation",
        "kling/kling-v3-video-generation",
    ]
    assert [item["role"] for item in uploads] == ["first_frame", "first_frame"]
    assert [item["sha256"] for item in uploads] == [expected_hash, expected_hash]
    assert all(item["scope"]["gateway"] == "dashscope_workspace" for item in uploads)
    submitted = [item for item in evidence.payload["fake_provider"]["events"] if item["kind"] == "submit"]
    assert len(submitted) == 2
    for event in submitted:
        summary = event["request_summary"]
        assert summary["provider_model_id"] == "kling/kling-v3-video-generation"
        assert summary["intent"]["model_id"] == "kling/kling-v3-video#i2v"
        assert summary["intent"]["params"] == {
            "audio": False,
            "duration": 5,
            "mode": "pro",
            "negative_prompt": "low detail",
            "watermark": False,
        }
        assert summary["media"][0]["role"] == "first_frame"
        assert summary["media"][0]["transport_binding"] == "dashscope_oss_sha256_v1"
        assert summary["media"][0]["url"].startswith("oss://lumenx-smoke-upload/first_frame-0-")


def test_fake_kling_playground_r2v_preserves_reference_order_and_request_fields(tmp_path, smoke_module):
    smoke = smoke_module
    data_root = tmp_path / "data"
    output_root = data_root / "output"
    data_root.mkdir()
    refs = [
        _write_image(output_root, "ref-0.png", data=rgb_png_bytes("kling-r2v-0", width=600, height=600)),
        _write_image(output_root, "ref-1.png", data=rgb_png_bytes("kling-r2v-1", width=600, height=600)),
    ]
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_kling=True)
    payload = {
        "client_request_id": "kling-r2v-wire",
        "mode": "r2v",
        "model_id": "kling/kling-v3-video#r2v",
        "prompt": "A precise Kling R2V wire request.",
        "negative_prompt": "blur",
        "input_media": refs,
        "parameters": {
            "mode": "4k",
            "duration": 3,
            "audio": True,
            "watermark": False,
            "aspect_ratio": "9:16",
        },
        "batch_size": 2,
    }
    with TestClient(app) as http:
        response = http.post("/playground/generate", json=payload)
        assert response.status_code == 200, response.text
        generation = _wait_generation(http, "kling-r2v-wire")
        assert generation["status"] == "completed", generation
        first_generation = generation
        for ref in refs:
            (output_root / ref).write_bytes(b"changed-after-admission")
        replay = http.post("/playground/generate", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["generation"] == first_generation
        readback = http.get("/playground/requests/kling-r2v-wire")
        assert readback.status_code == 200, readback.text
        assert readback.json()["generation"] == first_generation
        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)

    wait_for_counts(evidence, {"submit": 2, "query": 2, "fetch": 2})
    assert evidence.payload["fake_upload"]["counts"] == {"upload": 4}
    uploads = evidence.payload["fake_upload"]["events"]
    assert [item["role"] for item in uploads] == ["reference_image", "reference_image"] * 2
    assert [item["ordinal"] for item in uploads] == [0, 1, 0, 1]
    submitted = [item for item in evidence.payload["fake_provider"]["events"] if item["kind"] == "submit"]
    assert len(submitted) == 2
    for index, event in enumerate(submitted):
        summary = event["request_summary"]
        assert summary["provider_model_id"] == "kling/kling-v3-omni-video-generation"
        assert summary["intent"]["model_id"] == "kling/kling-v3-video#r2v"
        assert summary["intent"]["params"] == {
            "aspect_ratio": "9:16",
            "audio": True,
            "duration": 3,
            "mode": "4k",
            "negative_prompt": "blur",
            "watermark": False,
        }
        assert summary["intent"]["request_fields"] == [
            "mode",
            "model_id",
            "params.aspect_ratio",
            "params.audio",
            "params.duration",
            "params.mode",
            "params.negative_prompt",
            "params.watermark",
            "prompt",
        ]
        assert [(item["role"], item["ordinal"]) for item in summary["media"]] == [
            ("reference_image", 0),
            ("reference_image", 1),
        ]
        upload_pair = uploads[index * 2 : index * 2 + 2]
        assert [item["url"] for item in summary["media"]] == [f"oss://{item['key']}" for item in upload_pair]


def test_fake_kling_playground_rejects_invalid_inputs_before_upload_or_submit(tmp_path, smoke_module):
    smoke = smoke_module
    data_root = tmp_path / "data"
    output_root = data_root / "output"
    data_root.mkdir()
    frame = _write_image(output_root, "first.png")
    alpha = _write_image(output_root, "alpha.png", data=png_bytes(512, 512, alpha=True))
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_kling=True)

    invalid_payloads = [
        {
            "client_request_id": "kling-wrong-mode",
            "mode": "t2v",
            "model_id": "kling-v3-i2v",
            "prompt": "Wrong mode.",
            "input_media": [frame],
            "parameters": {"mode": "pro", "duration": 5, "audio": False, "watermark": False},
        },
        {
            "client_request_id": "kling-alpha-frame",
            "mode": "i2v",
            "model_id": "kling-v3-i2v",
            "prompt": "Alpha frame.",
            "input_media": [alpha],
            "parameters": {"mode": "pro", "duration": 5, "audio": False, "watermark": False},
        },
        {
            "client_request_id": "kling-too-many-refs",
            "mode": "r2v",
            "model_id": "kling-v3-r2v",
            "prompt": "Too many refs.",
            "input_media": [frame] * 8,
            "parameters": {"mode": "pro", "duration": 5, "audio": False, "watermark": False, "aspect_ratio": "16:9"},
        },
    ]
    with TestClient(app) as http:
        for payload in invalid_payloads:
            response = http.post("/playground/generate", json=payload)
            assert response.status_code == 422
        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)

    assert evidence.payload.get("fake_upload", {}).get("counts", {"upload": 0}) == {"upload": 0}
    assert evidence.payload["fake_provider"]["counts"] == {"submit": 0, "query": 0, "fetch": 0}


def test_fake_kling_flag_is_required_for_workspace_kling_runtime(tmp_path, smoke_module):
    smoke = smoke_module
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence)
    with TestClient(app):
        result = app.state.provider_runtime_selector.for_admission(
            provider_model_id="kling/kling-v3-video-generation",
            gateway="dashscope_workspace",
        )
        assert result.runtime is None
        assert result.reason == "unsupported_provider_model"


@pytest.mark.parametrize("model_id", ["wan3.0-video", "wan3.0-video-prime", "happyhorse-1.0-video-edit"])
def test_kling_fake_image_upload_does_not_expand_other_provider_media_roles(tmp_path, model_id):
    from src.models.provider_tasks import ProviderScope, ResolvedCredential
    from src.models.provider_upload import ProviderUploadError
    from src.utils.media_versions import MediaVersion

    smoke = load_smoke_module()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    scope = ProviderScope("dashscope", "dashscope", "dashscope_workspace", smoke.SMOKE_WORKSPACE_BASE_URL,
                          smoke.SMOKE_WORKSPACE_REGION, workspace_id=smoke.SMOKE_WORKSPACE_ID)
    credential = ResolvedCredential(scope, "credentials/fake", "fake-secret")
    data = rgb_png_bytes("image-role-guard", width=512, height=512)
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), "reference_image", 0, "image/png")
    with pytest.raises(ProviderUploadError):
        smoke.FakeDashScopeUploader(evidence)(scope=scope, provider_model_id=model_id,
                                             credential=credential, version=version, data=data)
    assert evidence.payload.get("fake_upload", {}).get("counts", {"upload": 0}) == {"upload": 0}
