import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.platform.api import router
from src.apps.platform.config import PlatformConfig
from src.apps.platform.service import PlatformService


def _manifest(version="1.0.0", title="Story Scout"):
    payload = {"name": "story-scout", "version": version, "title": title, "expected_tools": ["canvas.readProject"]}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "content_hash": hashlib.sha256(canonical.encode()).hexdigest()}


def _client(tmp_path):
    app = FastAPI()
    app.include_router(router, prefix="/api/platform")
    app.state.platform_service = PlatformService(PlatformConfig(enabled=True, database_url=f"sqlite:///{tmp_path / 'platform.db'}"))
    client = TestClient(app)
    register = client.post("/api/platform/auth/register", json={"username": "alice", "password": "a" * 12})
    token = client.post("/api/platform/auth/login", json={"username": "alice", "password": "a" * 12}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_skill_api_creates_lists_binds_and_resolves_user_skill(tmp_path):
    client, headers = _client(tmp_path)
    created = client.post("/api/platform/skills/versions", headers=headers, json={"manifest": _manifest(), "scope": "user"})
    assert created.status_code == 200
    skill_id = created.json()["id"]
    assert client.post(f"/api/platform/skills/versions/{skill_id}/bind", headers=headers).status_code == 200
    listed = client.get("/api/platform/skills", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "story-scout"
    resolved = client.get("/api/platform/skills/story-scout/resolve", headers=headers)
    assert resolved.status_code == 200
    assert resolved.json()["version"] == "1.0.0"


def test_skill_api_rejects_script_field_before_service_write(tmp_path):
    client, headers = _client(tmp_path)
    payload = _manifest(); payload["script"] = "echo unsafe"
    response = client.post("/api/platform/skills/versions", headers=headers, json={"manifest": payload, "scope": "user"})
    assert response.status_code == 422
