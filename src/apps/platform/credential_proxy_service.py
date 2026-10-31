"""Minimal credential broker service for isolated Skill workers.

Secrets are loaded from a local secret-manager adapter boundary for this
deployment slice. The issue endpoint returns only an opaque reference; Core
resolution is separately authenticated and never exposed to the Worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
import time
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


class IssueRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=256)
    provider: str = Field(min_length=1, max_length=80)
    scope: str = Field(min_length=1, max_length=128)


class SecretProvider(Protocol):
    def get(self, provider: str, scope: str) -> str: ...


class EnvironmentSecretProvider:
    """Local adapter; production should replace this with Secret Manager/KMS."""

    def __init__(self, secrets_by_key: dict[str, str]):
        self.secrets_by_key = secrets_by_key

    def get(self, provider: str, scope: str) -> str:
        key = f"{provider}:{scope}"
        value = self.secrets_by_key.get(key)
        if not isinstance(value, str) or not value:
            raise KeyError(key)
        return value


class FileSecretProvider:
    """Read a JSON secret map from a mounted secret file on each lookup."""

    def __init__(self, path: str):
        self.path = Path(path)

    def get(self, provider: str, scope: str) -> str:
        try:
            configured = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyError("secret file unavailable") from exc
        return EnvironmentSecretProvider(configured).get(provider, scope)


class CredentialProxyStore:
    def __init__(self, secrets_by_key: dict[str, str] | None = None, *, provider: SecretProvider | None = None, ttl_seconds: int = 300):
        self.provider = provider or EnvironmentSecretProvider(secrets_by_key or {})
        self.ttl_seconds = ttl_seconds
        self.refs: dict[str, tuple[str, str, float]] = {}

    def issue(self, provider: str, scope: str) -> str:
        key = f"{provider}:{scope}"
        self.provider.get(provider, scope)
        ref = f"credref_{secrets.token_urlsafe(24)}"
        self.refs[hashlib.sha256(ref.encode()).hexdigest()] = (provider, scope, time.time() + self.ttl_seconds)
        return ref

    def resolve(self, ref: str) -> str:
        entry = self.refs.get(hashlib.sha256(ref.encode()).hexdigest())
        if entry is None or entry[2] <= time.time():
            raise KeyError("expired credential reference")
        return self.provider.get(entry[0], entry[1])


def _bearer(value: str | None, expected: str | None) -> None:
    if not expected or value != f"Bearer {expected}":
        raise HTTPException(401, detail={"code": "credential_proxy_unauthorized"})


def create_app(store: CredentialProxyStore | None = None, *, worker_token: str | None = None, core_token: str | None = None) -> FastAPI:
    provider_kind = os.getenv("LUMENX_CREDENTIAL_PROXY_PROVIDER", "env_json")
    if store is None and provider_kind == "file":
        secret_file = os.getenv("LUMENX_CREDENTIAL_PROXY_SECRET_FILE")
        if not secret_file:
            raise RuntimeError("file credential provider requires LUMENX_CREDENTIAL_PROXY_SECRET_FILE")
        store = CredentialProxyStore(provider=FileSecretProvider(secret_file), ttl_seconds=int(os.getenv("LUMENX_CREDENTIAL_PROXY_TTL", "300")))
    elif store is None and provider_kind == "env_json":
        raw = os.getenv("LUMENX_CREDENTIAL_PROXY_SECRETS_JSON", "{}")
        store = CredentialProxyStore(json.loads(raw), ttl_seconds=int(os.getenv("LUMENX_CREDENTIAL_PROXY_TTL", "300")))
    elif store is None:
        raise RuntimeError("unsupported credential provider")
    worker_token = worker_token or os.getenv("LUMENX_CREDENTIAL_PROXY_TOKEN")
    core_token = core_token or os.getenv("LUMENX_CREDENTIAL_CORE_TOKEN") or worker_token
    app = FastAPI(title="LumenX Credential Proxy", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        if not worker_token or not core_token:
            raise HTTPException(503, detail={"code": "credential_proxy_tokens_missing"})
        return {"status": "ready"}

    @app.post("/v1/credentials/issue")
    def issue(body: IssueRequest, authorization: str | None = Header(default=None)) -> dict[str, str]:
        _bearer(authorization, worker_token)
        try:
            ref = store.issue(body.provider, body.scope)
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "credential_not_configured"}) from exc
        return {"credential_ref": ref, "expires_in": str(store.ttl_seconds)}

    @app.post("/v1/credentials/resolve")
    def resolve(body: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, str]:
        _bearer(authorization, core_token)
        ref = body.get("credential_ref")
        if not isinstance(ref, str):
            raise HTTPException(400, detail={"code": "credential_ref_required"})
        try:
            return {"secret": store.resolve(ref)}
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "credential_ref_expired"}) from exc

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("LUMENX_CREDENTIAL_PROXY_HOST", "0.0.0.0"), port=int(os.getenv("LUMENX_CREDENTIAL_PROXY_PORT", "17178")))
