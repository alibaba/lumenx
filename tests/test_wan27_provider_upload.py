"""Wan 2.7 classic DashScope temporary upload scope stays explicit."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.models.provider_upload import ProviderUploadError, upload_dashscope_temporary_media
from src.utils.media_versions import MediaVersion


CLASSIC_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
WORKSPACE_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="workspace",
)
CLASSIC_CREDENTIAL = ResolvedCredential(CLASSIC_SCOPE, "credentials/wan27-classic", "test-only-secret")


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.closed = False

    def iter_content(self, chunk_size=8192):
        import json

        yield json.dumps(self.payload).encode("utf-8")

    def close(self):
        self.closed = True


def version_for(data: bytes, role="reference_video", mime="video/mp4", ordinal=0) -> MediaVersion:
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


def policy_payload(**overrides):
    data = {
        "policy": "policy-token",
        "signature": "signature-token",
        "upload_dir": "dashscope-temp/wan27",
        "upload_host": "https://bucket.oss-cn-beijing.aliyuncs.com",
        "expire_in_seconds": 172800,
        "max_file_size_mb": 100,
        "capacity_limit_mb": 1024,
        "oss_access_key_id": "ak-token",
        "x_oss_object_acl": "private",
        "x_oss_forbid_overwrite": "true",
    }
    data.update(overrides)
    return {"data": data}


def install_transport(monkeypatch):
    captured = {"gets": [], "posts": []}

    def fake_get(url, **kwargs):
        captured["gets"].append((url, kwargs))
        return Response(200, policy_payload())

    def fake_post(url, **kwargs):
        captured["posts"].append((url, kwargs))
        return Response(200, {})

    monkeypatch.setattr("src.models.provider_upload.requests.get", fake_get)
    monkeypatch.setattr("src.models.provider_upload.requests.post", fake_post)
    return captured


@pytest.mark.parametrize(
    ("provider_model_id", "role", "mime", "suffix"),
    [
        ("wan2.7-videoedit", "reference_video", "video/mp4", "mp4"),
        ("wan2.7-r2v", "reference_video", "video/quicktime", "mov"),
        ("wan2.7-r2v-2026-06-12", "reference_audio", "audio/wav", "wav"),
        ("wan2.7-r2v", "reference_audio", "audio/mpeg", "mp3"),
    ],
)
def test_wan27_classic_upload_uses_same_model_scope_and_private_oss_form(
    monkeypatch, provider_model_id, role, mime, suffix,
):
    data = f"{provider_model_id}:{role}:{mime}".encode("utf-8")
    monkeypatch.setattr("src.models.provider_upload._new_upload_nonce", lambda: "d" * 32)
    captured = install_transport(monkeypatch)
    version = version_for(data, role=role, mime=mime)

    result = upload_dashscope_temporary_media(
        scope=CLASSIC_SCOPE,
        provider_model_id=provider_model_id,
        credential=CLASSIC_CREDENTIAL,
        version=version,
        data=data,
    )

    assert result.scope == CLASSIC_SCOPE
    assert result.provider_model_id == provider_model_id
    assert result.credential_ref == CLASSIC_CREDENTIAL.credential_ref
    assert result.version == version
    assert result.key == f"dashscope-temp/wan27/{role}-0-{version.sha256[:16]}-{'d' * 32}.{suffix}"
    assert result.url == f"oss://{result.key}"

    assert captured["gets"] == [
        (
            "https://dashscope.aliyuncs.com/api/v1/uploads",
            {
                "params": {"action": "getPolicy", "model": provider_model_id},
                "headers": {
                    "Authorization": "Bearer test-only-secret",
                    "Content-Type": "application/json",
                },
                "timeout": (10, 120),
                "allow_redirects": False,
                "stream": True,
                "auth": captured["gets"][0][1]["auth"],
            },
        )
    ]
    assert len(captured["posts"]) == 1
    upload_url, upload_options = captured["posts"][0]
    assert upload_url == "https://bucket.oss-cn-beijing.aliyuncs.com"
    assert upload_options["headers"] == {}
    fields = upload_options["files"]
    assert [name for name, _ in fields] == [
        "OSSAccessKeyId",
        "policy",
        "Signature",
        "key",
        "x-oss-object-acl",
        "x-oss-forbid-overwrite",
        "success_action_status",
        "file",
    ]
    assert fields[3][1] == (None, result.key)
    assert fields[4][1] == (None, "private")
    assert fields[5][1] == (None, "true")
    assert fields[-1][1] == (result.key.rsplit("/", 1)[-1], data, mime)


@pytest.mark.parametrize(
    ("provider_model_id", "scope", "credential", "version"),
    [
        ("wan3.0-video", CLASSIC_SCOPE, CLASSIC_CREDENTIAL, version_for(b"video")),
        (
            "wan2.7-r2v",
            WORKSPACE_SCOPE,
            ResolvedCredential(WORKSPACE_SCOPE, "credentials/workspace", "test-only-secret"),
            version_for(b"video"),
        ),
        ("wan2.7-videoedit", CLASSIC_SCOPE, CLASSIC_CREDENTIAL, version_for(b"audio", "reference_audio", "audio/wav")),
        ("wan2.7-t2v", CLASSIC_SCOPE, CLASSIC_CREDENTIAL, version_for(b"video")),
        ("wan2.7-r2v", replace(CLASSIC_SCOPE, region="ap-southeast-1"), CLASSIC_CREDENTIAL, version_for(b"video")),
        (
            "wan2.7-r2v",
            CLASSIC_SCOPE,
            ResolvedCredential(replace(CLASSIC_SCOPE, region="ap-southeast-1"), "credentials/other", "test-only-secret"),
            version_for(b"video"),
        ),
    ],
)
def test_wan27_classic_upload_rejects_unsupported_scope_or_media_before_network(
    monkeypatch, provider_model_id, scope, credential, version,
):
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("src.models.provider_upload.requests.post", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ProviderUploadError):
        upload_dashscope_temporary_media(
            scope=scope,
            provider_model_id=provider_model_id,
            credential=credential,
            version=version,
            data=b"video" if version.size_bytes == 5 else b"audio",
        )

    assert calls == []
