"""HappyHorse VideoEdit temporary upload stays model/scope/role bound."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.models.provider_upload import ProviderUploadError, upload_dashscope_temporary_media
from src.utils.media_versions import MediaVersion


SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://happyhorse.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="happyhorse",
)
CLASSIC_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
CREDENTIAL = ResolvedCredential(SCOPE, "credentials/happyhorse-videoedit", "test-only-secret")


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


def version_for(
    data: bytes,
    role: str = "reference_video",
    mime: str = "video/mp4",
    ordinal: int = 0,
) -> MediaVersion:
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)


def policy_payload(**overrides):
    data = {
        "policy": "policy-token",
        "signature": "signature-token",
        "upload_dir": "dashscope-temp/happyhorse",
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
    ("mime", "suffix"),
    [
        ("video/mp4", "mp4"),
        ("video/quicktime", "mov"),
    ],
)
def test_happyhorse_videoedit_upload_uses_workspace_scope_model_and_private_form(
    monkeypatch,
    mime,
    suffix,
):
    data = f"happyhorse-videoedit:{mime}".encode("utf-8")
    monkeypatch.setattr("src.models.provider_upload._new_upload_nonce", lambda: "e" * 32)
    captured = install_transport(monkeypatch)
    version = version_for(data, mime=mime)

    result = upload_dashscope_temporary_media(
        scope=SCOPE,
        provider_model_id="happyhorse-1.0-video-edit",
        credential=CREDENTIAL,
        version=version,
        data=data,
    )

    assert result.scope == SCOPE
    assert result.provider_model_id == "happyhorse-1.0-video-edit"
    assert result.credential_ref == CREDENTIAL.credential_ref
    assert result.version == version
    assert result.key == (
        f"dashscope-temp/happyhorse/reference_video-0-{version.sha256[:16]}-{'e' * 32}.{suffix}"
    )
    assert result.url == f"oss://{result.key}"

    assert len(captured["gets"]) == 1
    policy_url, policy_options = captured["gets"][0]
    assert policy_url == "https://happyhorse.cn-beijing.maas.aliyuncs.com/api/v1/uploads"
    assert policy_options["params"] == {
        "action": "getPolicy",
        "model": "happyhorse-1.0-video-edit",
    }
    assert policy_options["headers"] == {
        "Authorization": "Bearer test-only-secret",
        "Content-Type": "application/json",
    }
    assert policy_options["allow_redirects"] is False

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
    ("scope", "credential", "provider_model_id", "version", "data"),
    [
        (CLASSIC_SCOPE, replace(CREDENTIAL, scope=CLASSIC_SCOPE), "happyhorse-1.0-video-edit", version_for(b"video"), b"video"),
        (SCOPE, CREDENTIAL, "happyhorse-1.0-video-edit", version_for(b"audio", "reference_audio", "audio/wav"), b"audio"),
        (SCOPE, CREDENTIAL, "happyhorse-1.0-video-edit", version_for(b"image", "reference_image", "image/png"), b"image"),
        (SCOPE, CREDENTIAL, "happyhorse-1.0-video-edit", version_for(b"video", "reference_video", "video/avi"), b"video"),
        (SCOPE, CREDENTIAL, "happyhorse-1.1-r2v", version_for(b"video"), b"video"),
        (SCOPE, replace(CREDENTIAL, scope=replace(SCOPE, workspace_id="other")), "happyhorse-1.0-video-edit", version_for(b"video"), b"video"),
    ],
)
def test_happyhorse_videoedit_upload_rejects_wrong_scope_model_or_role_before_network(
    monkeypatch,
    scope,
    credential,
    provider_model_id,
    version,
    data,
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
            data=data,
        )

    assert calls == []
