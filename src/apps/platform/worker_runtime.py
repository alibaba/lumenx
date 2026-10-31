"""Small runtime boundaries for isolated Skill workers.

The worker must expose operational state without exporting job payloads or
provider secrets. Credential access is deliberately brokered through an
opaque reference returned by a separate service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import time
from typing import Any, Protocol
from urllib import request


class CredentialBroker(Protocol):
    def issue(self, *, worker_id: str, job_id: str, provider: str, scope: str) -> str: ...


class CredentialProxyError(RuntimeError):
    pass


class HttpCredentialProxy:
    """Request a short-lived opaque credential from the platform broker."""

    def __init__(self, url: str, *, timeout: float = 5.0, auth_token: str | None = None):
        if not url.startswith(("http://", "https://")):
            raise CredentialProxyError("credential proxy URL must use http:// or https://")
        self.url, self.timeout, self.auth_token = url, timeout, auth_token

    def issue(self, *, worker_id: str, job_id: str, provider: str, scope: str) -> str:
        body = json.dumps({"worker_id": worker_id, "job_id": job_id, "provider": provider, "scope": scope}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            req = request.Request(self.url, data=body, headers=headers, method="POST")
            with request.urlopen(req, timeout=self.timeout) as response:
                result = json.loads(response.read().decode())
        except Exception as exc:  # pragma: no cover - network dependent
            raise CredentialProxyError("credential proxy request failed") from exc
        token = result.get("credential_ref")
        if not isinstance(token, str) or not token or result.get("secret") is not None:
            raise CredentialProxyError("credential proxy returned an unsafe response")
        return token


class DenyCredentialProxy:
    """Default worker policy: fail closed when no broker is configured."""

    def issue(self, **_: Any) -> str:
        raise CredentialProxyError("credential proxy is not configured")


@dataclass
class WorkerMetrics:
    worker_id: str
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    last_job_id: str | None = None
    last_error: str | None = None
    started_at: float = field(default_factory=time.time)

    def record_claim(self, job_id: str) -> None:
        self.claimed += 1
        self.last_job_id = job_id

    def record_success(self) -> None:
        self.succeeded += 1
        self.last_error = None

    def record_failure(self, error: Exception) -> None:
        self.failed += 1
        self.last_error = type(error).__name__

    def snapshot(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "claimed": self.claimed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "last_job_id": self.last_job_id,
            "last_error": self.last_error,
            "uptime_seconds": max(0.0, time.time() - self.started_at),
        }


def credential_proxy_from_env() -> CredentialBroker:
    url = os.getenv("LUMENX_CREDENTIAL_PROXY_URL")
    if not url:
        return DenyCredentialProxy()
    return HttpCredentialProxy(url, auth_token=os.getenv("LUMENX_CREDENTIAL_PROXY_TOKEN"))
