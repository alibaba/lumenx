"""Wan 2.7 VideoEdit Playground HTTP admission/execution wire coverage."""

from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_lumenx_generation_browser_smoke import evidence_payload, load_smoke_module, write_static_root


def write_real_videoedit_source(output_root: Path) -> Path:
    from src.utils import media_probe

    uploads = output_root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    source = uploads / "videoedit-source.mp4"
    ffmpeg = media_probe.get_ffmpeg_path() or "ffmpeg"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert source.is_file() and source.stat().st_size > 0
    return source


def wait_for_playground_completion(client: TestClient, request_id: str, *, timeout: float = 8.0) -> dict:
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


def test_playground_wan27_videoedit_http_freezes_av_executes_and_replay_is_read_only(tmp_path, monkeypatch):
    smoke = load_smoke_module()
    data_root = tmp_path / "data"
    output_root = data_root / "output"
    data_root.mkdir()
    source = write_real_videoedit_source(output_root)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))

    def local_provider_fixture(self):
        from src.utils import media_probe

        self.provider_root.mkdir(parents=True, exist_ok=True)
        fixture = self.provider_root / "videoedit-result.mp4"
        ffmpeg_bin = media_probe.get_ffmpeg_path() or "ffmpeg"
        ffmpeg = subprocess.run(
            [
                ffmpeg_bin,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=2:size=320x180:rate=24",
                "-pix_fmt",
                "yuv420p",
                str(fixture),
            ],
            capture_output=True,
            check=True,
            timeout=20,
        )
        assert ffmpeg.returncode == 0
        return fixture

    monkeypatch.setattr(smoke.FakeWanProvider, "_ensure_fixture", local_provider_fixture)
    app = smoke.create_smoke_app(
        data_root,
        write_static_root(tmp_path),
        evidence,
        fake_wan27_videoedit=True,
    )
    payload = {
        "client_request_id": "playground-videoedit-smoke",
        "mode": "v2v",
        "model_id": "wan/wan2.7-video#videoedit",
        "prompt": "Edit the source clip while preserving the original audio.",
        "input_media": ["uploads/videoedit-source.mp4"],
        "parameters": {
            "duration": 0,
            "resolution": "1080P",
            "aspect_ratio": "16:9",
            "seed": 0,
            "audio_setting": "origin",
            "prompt_extend": False,
            "watermark": False,
        },
        "batch_size": 2,
    }
    with TestClient(app) as client:
        accepted = client.post("/playground/generate", json=payload)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["generation"]["model_id"] == "wan/wan2.7-video#videoedit"
        generation = wait_for_playground_completion(client, payload["client_request_id"])
        assert generation["status"] == "completed", generation
        assert len(generation["outputs"]) == 2
        assert [attempt["status"] for attempt in generation["attempts"]] == ["completed", "completed"]
        media_records = [attempt["provider_execution"]["media"] for attempt in generation["attempts"]]
        assert media_records[0] == media_records[1]
        assert media_records[0][0]["sha256"] == source_sha
        for output in generation["outputs"]:
            media = output_root / output["media_path"]
            assert media.is_file() and media.stat().st_size > 0
            assert client.get(f"/files/{output['media_path']}").content == media.read_bytes()

        source.write_bytes(b"mutated source after admission must not be refrozen")
        assert hashlib.sha256(source.read_bytes()).hexdigest() != source_sha
        upload_counts = dict(evidence.payload["fake_upload"]["counts"])
        provider_counts = dict(evidence.payload["fake_provider"]["counts"])
        replay = client.post("/playground/generate", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["generation"] == generation
        assert replay.json()["generation"]["attempts"][0]["provider_execution"]["media"][0]["sha256"] == source_sha
        assert evidence.payload["fake_upload"]["counts"] == upload_counts
        assert evidence.payload["fake_provider"]["counts"] == provider_counts

    assert evidence.payload["fake_wan27_videoedit"] is True
    assert evidence.payload["fake_wan3"] is False
    assert evidence.payload["fake_upload"]["counts"] == {"upload": 2}
    upload_events = evidence.payload["fake_upload"]["events"]
    assert [event["role"] for event in upload_events] == ["reference_video", "reference_video"]
    assert {event["sha256"] for event in upload_events} == {source_sha}
    assert all(event["provider_model_id"] == "wan2.7-videoedit" for event in upload_events)
    assert all(event["scope"]["gateway"] == "dashscope" for event in upload_events)
    assert evidence.payload["fake_provider"]["counts"] == {"submit": 2, "query": 2, "fetch": 2}
    submitted = [event for event in evidence.payload["fake_provider"]["events"] if event["kind"] == "submit"]
    assert len(submitted) == 2
    for event in submitted:
        request = event["request_summary"]
        assert request["provider_model_id"] == "wan2.7-videoedit"
        assert request["intent"]["model_id"] == "wan/wan2.7-video#v2v"
        assert request["intent"]["mode"] == "v2v"
        assert request["intent"]["params"]["duration"] == 0
        assert request["intent"]["params"]["seed"] == 0
        assert request["intent"]["params"]["audio_setting"] == "origin"
        assert request["intent"]["params"]["ratio"] == "16:9"
        assert request["intent"]["params"]["prompt_extend"] is False
        assert request["intent"]["params"]["watermark"] is False
        assert [item["role"] for item in request["media"]] == ["reference_video"]
        assert request["media"][0]["transport_binding"] == "dashscope_oss_sha256_v1"
        assert request["media"][0]["sha256"] == source_sha
