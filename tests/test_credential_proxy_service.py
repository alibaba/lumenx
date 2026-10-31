from fastapi.testclient import TestClient

from src.apps.platform.credential_proxy_service import CredentialProxyStore, create_app


def test_credential_proxy_issues_opaque_ref_and_core_resolves_secret():
    store = CredentialProxyStore({"dashscope:tenant-1": "super-secret"}, ttl_seconds=60)
    app = create_app(store, worker_token="local-worker-token", core_token="local-core-token")
    client = TestClient(app)
    issue = client.post(
        "/v1/credentials/issue",
        headers={"Authorization": "Bearer local-worker-token"},
        json={"worker_id": "w1", "job_id": "j1", "provider": "dashscope", "scope": "tenant-1"},
    )
    assert issue.status_code == 200
    body = issue.json()
    assert body["credential_ref"].startswith("credref_")
    assert "secret" not in body
    resolved = client.post(
        "/v1/credentials/resolve",
        headers={"Authorization": "Bearer local-core-token"},
        json={"credential_ref": body["credential_ref"]},
    )
    assert resolved.status_code == 200
    assert resolved.json() == {"secret": "super-secret"}


def test_credential_proxy_rejects_missing_secret_and_wrong_worker_token():
    app = create_app(CredentialProxyStore({"dashscope:tenant-1": "super-secret"}), worker_token="local-worker-token")
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/v1/credentials/issue", json={"worker_id": "w", "job_id": "j", "provider": "dashscope", "scope": "tenant-1"}).status_code == 401
