"""Actual FastAPI admission/readback/deletion shapes consumed by the frontend."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.playground import service as service_module, storage as storage_module
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope


class FixedTime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 10, 13, tzinfo=timezone.utc)


def test_playground_actual_admission_wire_matches_frontend_fixture(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    calls = []
    body = {
        "client_request_id": "wire-playground-request",
        "mode": "t2v", "model_id": "wan2.7-t2v", "prompt": "月光与海 · wire",
        "negative_prompt": "", "parameters": {"seed": 0, "watermark": False, "duration": 5},
        "batch_size": 2,
    }
    with TestClient(app) as client:
        monkeypatch.setattr(service_module.uuid, "uuid4", lambda: UUID("11111111-1111-4111-8111-111111111111"))
        monkeypatch.setattr(service_module, "datetime", FixedTime)
        monkeypatch.setattr(storage_module, "datetime", FixedTime)
        # Preserve the real catalog/Core admission chain with a synthetic authority.
        # The worker is the only paid execution boundary replaced in this wire test.
        runtime = ProviderRecoveryRuntime(
            ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing"),
            lambda: "wire-fixture-credential",
        )
        monkeypatch.setattr(app.state.playground_service, "_video_runtime_provider", lambda **_: runtime)
        monkeypatch.setattr(app.state.playground_video_recovery_dispatcher, "schedule", lambda generation_id, attempt_id: calls.append((generation_id, attempt_id)))
        accepted = client.post("/playground/generate", json=body)
        assert accepted.status_code == 200, accepted.text
        readback = client.get("/playground/requests/" + body["client_request_id"])
        assert readback.status_code == 200, readback.text
        assert len(calls) == 2
        generation_id = accepted.json()["generation"]["id"]
        assert client.delete("/playground/history/" + generation_id).status_code == 200
        deleted = client.get("/playground/requests/" + body["client_request_id"])
        assert deleted.status_code == 200, deleted.text
        replay = client.post("/playground/generate", json=body)
        assert replay.status_code == 200 and replay.json() == deleted.json()
        assert len(calls) == 2
    wire = {"request": body, "accepted": accepted.json(), "readback": readback.json(), "deleted": deleted.json()}
    actual = tmp_path / "playground-admission-wire.json"
    actual.write_text(json.dumps(wire, indent=2, ensure_ascii=False) + "\n")
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/playground-admission-wire.json"
    if not fixture.exists() or wire != json.loads(fixture.read_text()):
        print("PLAYGROUND_WIRE=" + json.dumps(wire, ensure_ascii=False))
    assert fixture.exists(), f"Export actual HTTP fixture from {actual}"
    assert wire == json.loads(fixture.read_text())
