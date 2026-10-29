"""HappyHorse 1.1 Playground HTTP execution wire coverage for the smoke harness."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_lumenx_generation_browser_smoke import evidence_payload, load_smoke_module, write_static_root
from tests.test_wan3_image_validation import png_bytes


@pytest.fixture
def smoke_with_transport_bytes(monkeypatch):
    smoke = load_smoke_module()

    def fixture(self):
        self.provider_root.mkdir(parents=True, exist_ok=True)
        path = self.provider_root / "transport.mp4"
        path.write_bytes(b"fake-transport-result")
        return path

    monkeypatch.setattr(smoke.FakeWanProvider, "_ensure_fixture", fixture)
    return smoke


def _write_png(output_root: Path, name: str, *, width: int = 420, height: int = 420) -> str:
    uploads = output_root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / name
    path.write_bytes(png_bytes(width, height, alpha=True))
    return f"uploads/{name}"


def _wait_for_completion(client: TestClient, request_id: str, *, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/playground/requests/{request_id}")
        assert response.status_code == 200, response.text
        last = response.json()["generation"]
        if last["status"] not in {"pending", "processing"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"playground generation did not settle: {last}")


def _assert_published_outputs(client: TestClient, output_root: Path, generation: dict) -> None:
    assert generation["status"] == "completed", generation
    assert len(generation["outputs"]) == 2
    assert [attempt["status"] for attempt in generation["attempts"]] == ["completed", "completed"]
    for output in generation["outputs"]:
        media = output_root / output["media_path"]
        assert media.is_file() and media.read_bytes() == b"fake-transport-result"
        assert client.get(f"/files/{output['media_path']}").content == b"fake-transport-result"


def test_fake_happyhorse11_selector_is_workspace_only_and_does_not_enable_wan3(tmp_path, smoke_with_transport_bytes):
    smoke = smoke_with_transport_bytes
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_happyhorse11=True)
    with TestClient(app):
        selector = app.state.provider_runtime_selector
        runtimes = [
            selector.for_admission(provider_model_id=model_id, gateway="dashscope_workspace").runtime
            for model_id in ("happyhorse-1.1-t2v", "happyhorse-1.1-i2v", "happyhorse-1.1-r2v")
        ]
        assert all(runtime is not None for runtime in runtimes)
        assert {runtime.scope.gateway for runtime in runtimes if runtime is not None} == {"dashscope_workspace"}
        assert {runtime.scope.workspace_id for runtime in runtimes if runtime is not None} == {"smoke-workspace"}
        assert selector.for_admission(provider_model_id="wan3.0-video", gateway="dashscope_workspace").runtime is None
        assert selector.for_admission(provider_model_id="happyhorse-1.1-t2v", gateway="dashscope").runtime is None
    assert evidence.payload["fake_happyhorse11"] is True
    assert evidence.payload["fake_wan3"] is False
    assert evidence.payload.get("fake_upload", {"counts": {"upload": 0}})["counts"] == {"upload": 0}


def test_playground_happyhorse11_http_executes_tir_batches_and_replay_is_read_only(tmp_path, smoke_with_transport_bytes):
    smoke = smoke_with_transport_bytes
    data_root = tmp_path / "data"
    output_root = data_root / "output"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_happyhorse11=True)
    with TestClient(app) as client:
        first = _write_png(output_root, "first.png", width=420, height=420)
        ref0 = _write_png(output_root, "ref0.png", width=420, height=420)
        ref1 = _write_png(output_root, "ref1.png", width=400, height=450)
        payloads = [
            {
                "client_request_id": "hh11-t2v-old-canonical",
                "mode": "t2v",
                "model_id": "happyhorse/happyhorse-1.1-t2v#i2v",
                "prompt": "HappyHorse old canonical T2V replay path.",
                "parameters": {"seed": 0, "watermark": False},
                "batch_size": 2,
            },
            {
                "client_request_id": "hh11-i2v-defaults",
                "mode": "i2v",
                "model_id": "happyhorse-1.1-i2v",
                "prompt": "HappyHorse I2V defaults with one first frame.",
                "input_media": [first],
                "parameters": {"seed": 0, "watermark": False},
                "batch_size": 2,
            },
            {
                "client_request_id": "hh11-r2v-refs",
                "mode": "r2v",
                "model_id": "happyhorse-1.1-r2v",
                "prompt": "HappyHorse R2V ordered references.",
                "input_media": [ref0, ref1],
                "parameters": {"aspect_ratio": "4:5", "seed": 0, "watermark": False},
                "batch_size": 2,
            },
        ]
        completed = {}
        for payload in payloads:
            accepted = client.post("/playground/generate", json=payload)
            assert accepted.status_code == 200, accepted.text
            completed[payload["client_request_id"]] = _wait_for_completion(client, payload["client_request_id"])
            _assert_published_outputs(client, output_root, completed[payload["client_request_id"]])

        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)
        provider_counts = dict(evidence.payload["fake_provider"]["counts"])
        replay = client.post("/playground/generate", json=payloads[0])
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["generation"] == completed[payloads[0]["client_request_id"]]
        assert evidence.payload["fake_provider"]["counts"] == provider_counts

    assert evidence.payload["fake_happyhorse11"] is True
    assert evidence.payload["fake_wan3"] is False
    assert evidence.payload.get("fake_upload", {"counts": {"upload": 0}})["counts"] == {"upload": 0}
    assert evidence.payload["fake_provider"]["counts"] == {"submit": 6, "query": 6, "fetch": 6}
    submitted = [event for event in evidence.payload["fake_provider"]["events"] if event["kind"] == "submit"]
    assert [event["request_summary"]["provider_model_id"] for event in submitted] == [
        "happyhorse-1.1-t2v", "happyhorse-1.1-t2v",
        "happyhorse-1.1-i2v", "happyhorse-1.1-i2v",
        "happyhorse-1.1-r2v", "happyhorse-1.1-r2v",
    ]
    for event in submitted:
        request = event["request_summary"]
        assert request["scope"]["gateway"] == "dashscope_workspace"
        assert request["scope"]["workspace_id"] == "smoke-workspace"
        assert request["intent"]["params"]["duration"] == 5
        assert request["intent"]["params"]["resolution"] == "1080P"
        assert request["intent"]["params"]["seed"] == 0
        assert request["intent"]["params"]["watermark"] is False
    assert submitted[0]["request_summary"]["intent"]["model_id"] == "happyhorse/happyhorse-1.1-t2v#t2v"
    assert completed["hh11-t2v-old-canonical"]["model_id"] == "happyhorse/happyhorse-1.1-t2v#i2v"
    i2v_media = submitted[2]["request_summary"]["media"]
    assert [item["role"] for item in i2v_media] == ["first_frame"]
    assert [item["transport_binding"] for item in i2v_media] == ["data_uri_sha256_v1"]
    r2v_media = submitted[4]["request_summary"]["media"]
    assert [item["role"] for item in r2v_media] == ["reference_image", "reference_image"]
    assert [item["ordinal"] for item in r2v_media] == [0, 1]
    assert [item["transport_binding"] for item in r2v_media] == ["data_uri_sha256_v1", "data_uri_sha256_v1"]
    assert submitted[4]["request_summary"]["intent"]["params"]["ratio"] == "4:5"
