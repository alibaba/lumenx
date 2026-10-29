"""Public Atelier media generation API uses the shared generation dispatcher."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.comic_gen.api import create_app
from src.models.provider_media_execution import PrivateSyncArtifact
from tests.frozen_image_fixtures import rgb_png_bytes


def seed_media_canvas(app, root: Path) -> None:
    uploads = root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "ref.png").write_bytes(rgb_png_bytes("public-media-ref"))
    app.state.atelier_storage.create_project(AtelierProject(
        id="p",
        title="Media canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="image-target", project_id="p", type="image", data={"reference_node_ids": ["ref"]}),
            AtelierNode(id="ref", project_id="p", type="image", media_urls=["uploads/ref.png"]),
            AtelierNode(id="audio-target", project_id="p", type="audio"),
        ],
    ))


def image_payload(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "image-request",
        "model_id": "wan/wan2.7-image-pro#image",
        "mode": "i2i",
        "prompt": "A lunar garden",
        "media": [{"role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png"}],
        "params": {"seed": 0, "prompt_extend": False, "watermark": False},
        "batch_size": 2,
    }
    payload.update(changes)
    return payload


def tts_payload(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "tts-request",
        "voice": "longanyang",
        "text": "旁白从废墟中升起。",
        "params": {"volume": 0, "instructions": "低声"},
    }
    payload.update(changes)
    return payload


def test_public_image_admission_schedules_new_attempts_and_readback_does_not_reschedule(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed_media_canvas(app, tmp_path)
        app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "fixture-secret"}, persist=lambda *_: None)
        schedule = Mock(return_value=True)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", schedule)

        created = client.post("/atelier/projects/p/nodes/image-target/image_candidates", json=image_payload())
        assert created.status_code == 200, created.text
        ack = created.json()

        assert ack["receipt"]["entrypoint"] == "atelier.provider_image.v1"
        assert ack["receipt"]["schema_version"] == 2
        assert len(ack["members"]) == 2
        assert ack["new_candidate_ids"] == [member["candidate"]["id"] for member in ack["members"]]
        assert [call.args for call in schedule.call_args_list] == [
            ("p", ack["members"][0]["attempt"]["attempt_id"]),
            ("p", ack["members"][1]["attempt"]["attempt_id"]),
        ]

        replay = client.post("/atelier/projects/p/nodes/image-target/image_candidates", json=image_payload())
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["new_candidate_ids"] == []
        readback = client.get("/atelier/projects/p/generation/requests/image-request")
        assert readback.status_code == 200, readback.text
        assert readback.json()["receipt"] == ack["receipt"]
        assert schedule.call_count == 2


def test_public_media_admission_requires_credentials_before_storage(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed_media_canvas(app, tmp_path)

        response = client.post("/atelier/projects/p/nodes/image-target/image_candidates", json=image_payload(batch_size=1))

        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "atelier_generation_unavailable"
        assert app.state.atelier_storage.get_project("p").generation_attempts == []


def test_public_audio_admission_uses_media_receipt_without_video_shape(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed_media_canvas(app, tmp_path)
        app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "fixture-secret"}, persist=lambda *_: None)
        schedule = Mock(return_value=True)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", schedule)

        created = client.post("/atelier/projects/p/nodes/audio-target/audio_candidates", json=tts_payload())
        assert created.status_code == 200, created.text
        ack = created.json()

        assert set(ack) == {"project", "receipt", "members", "replayed", "new_candidate_ids", "reason"}
        assert ack["receipt"]["entrypoint"] == "atelier.provider_audio.v1"
        assert ack["receipt"]["schema_version"] == 1
        assert len(ack["members"]) == 1
        attempt = ack["members"][0]["attempt"]
        assert attempt["media_kind"] == "audio"
        schedule.assert_called_once_with("p", attempt["attempt_id"])


def test_public_attempt_endpoint_routes_media_execution_through_same_dispatcher_service(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed_media_canvas(app, tmp_path)
        app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "fixture-secret"}, persist=lambda *_: None)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", Mock(return_value=True))
        calls = []

        def fake_executor(record, authority, *, output_root, scratch_dir, max_bytes):
            calls.append((record.attempt_id, authority.scope.to_dict(), authority.credential_ref))
            path = Path(scratch_dir) / "result.png"
            data = b"fake-png-bytes"
            path.write_bytes(data)
            return PrivateSyncArtifact(
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                encoding="image/png",
                media_kind="image",
            )

        app.state.atelier_media_generation_execution._executor = fake_executor
        created = client.post("/atelier/projects/p/nodes/image-target/image_candidates", json=image_payload(batch_size=1))
        assert created.status_code == 200, created.text
        attempt = created.json()["members"][0]["attempt"]

        result = client.post(
            f"/atelier/projects/p/generation/attempts/{attempt['attempt_id']}/continue",
            json={
                "candidate_id": attempt["candidate_id"],
                "expected_attempt_revision": attempt["attempt_revision"],
                "execution_fingerprint": attempt["provider_execution"]["fingerprint"],
            },
        )
        assert result.status_code == 200, result.text
        progressed = result.json()["attempt"]
        assert progressed["status"] == "completed"
        assert progressed["provider_execution"]["phase"] == "completed"
        assert progressed["output_ref"].startswith("assets/atelier/")
        assert calls and calls[0][0] == attempt["attempt_id"]
        node = next(item for item in result.json()["project"]["nodes"] if item["id"] == "image-target")
        candidate = next(item for item in node["data"]["candidates"] if item["id"] == progressed["candidate_id"])
        assert candidate["media_url"] == progressed["output_ref"]
        assert "video_url" not in candidate


def test_startup_recovery_does_not_schedule_media_accepted_attempts(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        seed_media_canvas(app, tmp_path)
        app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "fixture-secret"}, persist=lambda *_: None)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", Mock(return_value=True))
        created = client.post("/atelier/projects/p/nodes/image-target/image_candidates", json=image_payload(batch_size=1))
        assert created.status_code == 200, created.text

    calls = []
    from src.apps.atelier import generation_dispatcher as dispatcher_module
    monkeypatch.setattr(dispatcher_module.AtelierGenerationDispatcher, "start", lambda self: calls.append("start") or self.run_once())
    monkeypatch.setattr(dispatcher_module.AtelierGenerationDispatcher, "wake", lambda self: calls.append("wake"))

    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened):
        assert calls == ["start", "wake"]
        project = reopened.state.atelier_storage.get_project("p")
        assert project.generation_attempts[0].provider_execution.phase == "accepted"
