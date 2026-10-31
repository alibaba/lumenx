import json
from urllib import request

import pytest

from src.apps.platform.worker_runtime import (
    CredentialProxyError,
    DenyCredentialProxy,
    HttpCredentialProxy,
    WorkerMetrics,
)


def test_worker_metrics_snapshot_is_payload_free():
    metrics = WorkerMetrics("worker-1")
    metrics.record_claim("job-1")
    metrics.record_success()
    snapshot = metrics.snapshot()
    assert snapshot["claimed"] == 1
    assert snapshot["succeeded"] == 1
    assert "payload" not in snapshot


def test_worker_metrics_exposes_prometheus_counters_without_payload():
    metrics = WorkerMetrics("worker-1")
    metrics.record_claim("job-1")
    metrics.record_failure(RuntimeError("secret details"))
    output = metrics.prometheus()
    assert "lumenx_skill_worker_claimed_total" in output
    assert "lumenx_skill_worker_failed_total" in output
    assert "secret details" not in output


def test_default_credential_proxy_fails_closed():
    with pytest.raises(CredentialProxyError):
        DenyCredentialProxy().issue(worker_id="w", job_id="j", provider="p", scope="s")


def test_http_credential_proxy_returns_opaque_ref(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return json.dumps({"credential_ref": "broker/ref/1"}).encode()

    observed = {}
    def fake_urlopen(req, timeout):
        observed["body"] = json.loads(req.data)
        observed["auth"] = req.headers["Authorization"]
        return Response()

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    proxy = HttpCredentialProxy("http://proxy.local/issue", auth_token="worker-token")
    assert proxy.issue(worker_id="w", job_id="j", provider="p", scope="s") == "broker/ref/1"
    assert observed["body"] == {"worker_id": "w", "job_id": "j", "provider": "p", "scope": "s"}
    assert observed["auth"] == "Bearer worker-token"
