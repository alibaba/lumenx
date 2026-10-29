"""HappyHorse 1.0 VideoEdit Playground HTTP execution wire coverage for the smoke harness."""

from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_lumenx_generation_browser_smoke import evidence_payload, load_smoke_module, write_static_root


LEGACY = "happyhorse-1.0-video-edit"
CANONICAL = "happyhorse/happyhorse-1.0-video-edit#v2v"
OLD_CANONICAL = "happyhorse/happyhorse-1.0-video-edit#i2v"


def write_real_happyhorse_videoedit_source(output_root: Path, name: str = "happyhorse-videoedit-source.mp4") -> Path:
    from src.utils import media_probe

    uploads = output_root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    source = uploads / name
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
            "testsrc=duration=3:size=640x360:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=523:duration=3",
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


def assert_completed_outputs(client: TestClient, output_root: Path, generation: dict, *, count: int) -> None:
    assert generation["status"] == "completed", generation
    assert len(generation["outputs"]) == count
    assert [attempt["status"] for attempt in generation["attempts"]] == ["completed"] * count
    for output in generation["outputs"]:
        media = output_root / output["media_path"]
        assert media.is_file() and media.stat().st_size > 0
        assert client.get(f"/files/{output['media_path']}").content == media.read_bytes()


def test_playground_happyhorse_videoedit_http_executes_uploads_and_replay_is_read_only(tmp_path, monkeypatch):
    smoke = load_smoke_module()
    data_root = tmp_path / "data"
    output_root = data_root / "output"
    data_root.mkdir()
    source = write_real_happyhorse_videoedit_source(output_root)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))

    def local_provider_fixture(self):
        from src.utils import media_probe

        self.provider_root.mkdir(parents=True, exist_ok=True)
        fixture = self.provider_root / "happyhorse-videoedit-result.mp4"
        ffmpeg_bin = media_probe.get_ffmpeg_path() or "ffmpeg"
        subprocess.run(
            [
                ffmpeg_bin,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=3:size=640x360:rate=24",
                "-pix_fmt",
                "yuv420p",
                str(fixture),
            ],
            capture_output=True,
            check=True,
            timeout=20,
        )
        return fixture

    monkeypatch.setattr(smoke.FakeWanProvider, "_ensure_fixture", local_provider_fixture)
    app = smoke.create_smoke_app(
        data_root,
        write_static_root(tmp_path),
        evidence,
        fake_happyhorse_videoedit=True,
    )

    wrong_mode_payload = {
        "client_request_id": "hh-videoedit-wrong-mode",
        "mode": "i2v",
        "model_id": LEGACY,
        "prompt": "Wrong mode should not fall through to legacy.",
        "input_media": ["uploads/happyhorse-videoedit-source.mp4"],
        "parameters": {},
        "batch_size": 1,
    }
    default_payload = {
        "client_request_id": "hh-videoedit-defaults",
        "mode": "v2v",
        "model_id": LEGACY,
        "prompt": "HappyHorse VideoEdit default parameter path.",
        "input_media": ["uploads/happyhorse-videoedit-source.mp4"],
        "parameters": {},
        "batch_size": 1,
    }
    explicit_payload = {
        "client_request_id": "hh-videoedit-old-canonical-explicit",
        "mode": "v2v",
        "model_id": OLD_CANONICAL,
        "prompt": "HappyHorse VideoEdit old canonical alias with explicit false and origin audio.",
        "input_media": ["uploads/happyhorse-videoedit-source.mp4"],
        "parameters": {"seed": 0, "watermark": False, "audio_setting": "origin"},
        "batch_size": 2,
    }

    with TestClient(app) as client:
        wrong_mode = client.post("/playground/generate", json=wrong_mode_payload)
        assert wrong_mode.status_code == 422, wrong_mode.text
        assert evidence.payload.get("fake_upload", {"counts": {"upload": 0}})["counts"] == {"upload": 0}
        assert evidence.payload.get("fake_provider", {"counts": {"submit": 0, "query": 0, "fetch": 0}})["counts"] == {"submit": 0, "query": 0, "fetch": 0}

        accepted_default = client.post("/playground/generate", json=default_payload)
        assert accepted_default.status_code == 200, accepted_default.text
        completed_default = wait_for_playground_completion(client, default_payload["client_request_id"])
        assert_completed_outputs(client, output_root, completed_default, count=1)

        accepted_explicit = client.post("/playground/generate", json=explicit_payload)
        assert accepted_explicit.status_code == 200, accepted_explicit.text
        assert accepted_explicit.json()["generation"]["model_id"] == OLD_CANONICAL
        completed_explicit = wait_for_playground_completion(client, explicit_payload["client_request_id"])
        assert_completed_outputs(client, output_root, completed_explicit, count=2)
        assert [attempt["provider_execution"]["media"][0]["sha256"] for attempt in completed_explicit["attempts"]] == [
            source_sha,
            source_sha,
        ]

        source.write_bytes(b"mutated source after admission must not be refrozen")
        assert hashlib.sha256(source.read_bytes()).hexdigest() != source_sha
        upload_counts = dict(evidence.payload["fake_upload"]["counts"])
        provider_counts = dict(evidence.payload["fake_provider"]["counts"])
        replay = client.post("/playground/generate", json=explicit_payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["generation"] == completed_explicit
        assert replay.json()["generation"]["attempts"][0]["provider_execution"]["media"][0]["sha256"] == source_sha
        assert evidence.payload["fake_upload"]["counts"] == upload_counts
        assert evidence.payload["fake_provider"]["counts"] == provider_counts

    assert evidence.payload["fake_happyhorse_videoedit"] is True
    assert evidence.payload["fake_happyhorse11"] is False
    assert evidence.payload["fake_wan3"] is False
    assert evidence.payload["fake_wan27_videoedit"] is False
    assert evidence.payload["fake_upload"]["counts"] == {"upload": 3}
    assert evidence.payload["fake_provider"]["counts"] == {"submit": 3, "query": 3, "fetch": 3}

    upload_events = evidence.payload["fake_upload"]["events"]
    assert [event["provider_model_id"] for event in upload_events] == [LEGACY, LEGACY, LEGACY]
    assert [event["role"] for event in upload_events] == ["reference_video", "reference_video", "reference_video"]
    assert {event["sha256"] for event in upload_events} == {source_sha}
    assert all(event["scope"]["gateway"] == "dashscope_workspace" for event in upload_events)
    assert all(event["scope"]["workspace_id"] == "smoke-workspace" for event in upload_events)

    submitted = [event for event in evidence.payload["fake_provider"]["events"] if event["kind"] == "submit"]
    assert [event["request_summary"]["provider_model_id"] for event in submitted] == [LEGACY, LEGACY, LEGACY]
    default_request = submitted[0]["request_summary"]
    assert default_request["scope"]["gateway"] == "dashscope_workspace"
    assert default_request["intent"]["model_id"] == CANONICAL
    assert default_request["intent"]["mode"] == "v2v"
    assert default_request["intent"]["params"] == {
        "audio_setting": "auto",
        "resolution": "1080P",
        "watermark": True,
    }
    explicit_requests = [event["request_summary"] for event in submitted[1:]]
    for request in explicit_requests:
        assert request["provider_model_id"] == LEGACY
        assert request["intent"]["model_id"] == CANONICAL
        assert request["intent"]["params"] == {
            "audio_setting": "origin",
            "resolution": "1080P",
            "seed": 0,
            "watermark": False,
        }
        assert "duration" not in request["intent"]["params"]
        assert "ratio" not in request["intent"]["params"]
        assert "negative_prompt" not in request["intent"]["params"]
        assert "prompt_extend" not in request["intent"]["params"]
        assert [item["role"] for item in request["media"]] == ["reference_video"]
        assert request["media"][0]["transport_binding"] == "dashscope_oss_sha256_v1"
        assert request["media"][0]["sha256"] == source_sha
