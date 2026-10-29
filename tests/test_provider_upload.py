"""DashScope temporary uploads bind frozen audio/video bytes to explicit scope."""

from __future__ import annotations

import hashlib
import traceback
from dataclasses import replace

import pytest

from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.models.provider_upload import (
    ProviderUploadError,
    upload_dashscope_temporary_media,
)
from src.utils.media_versions import MediaVersion


WAN3_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://original-workspace.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="original-workspace",
)
CREDENTIAL = ResolvedCredential(WAN3_SCOPE, "credentials/original", "test-only-secret")


class Response:
    def __init__(self, status_code=200, payload=None, text="", chunks=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.text = text
        self.chunks = chunks
        self.closed = False

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def iter_content(self, chunk_size=8192):
        if self.chunks is not None:
            yield from self.chunks
            return
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
        "upload_dir": "dashscope-temp/session",
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


def install_success_transport(monkeypatch, *, policy=None, upload_status=200):
    captured = {"gets": [], "posts": []}

    def fake_get(url, **kwargs):
        captured["gets"].append((url, kwargs))
        return Response(200, policy or policy_payload())

    def fake_post(url, **kwargs):
        captured["posts"].append((url, kwargs))
        return Response(upload_status, {})

    monkeypatch.setattr("src.models.provider_upload.requests.get", fake_get)
    monkeypatch.setattr("src.models.provider_upload.requests.post", fake_post)
    return captured


def test_upload_uses_original_scope_policy_and_ordered_private_oss_form(monkeypatch):
    data = b"video bytes"
    monkeypatch.setattr("src.models.provider_upload._new_upload_nonce", lambda: "c" * 32)
    captured = install_success_transport(monkeypatch)

    version = version_for(data)
    result = upload_dashscope_temporary_media(
        scope=WAN3_SCOPE,
        provider_model_id="wan3.0-video",
        credential=CREDENTIAL,
        version=version,
        data=data,
    )

    expected_key = f"dashscope-temp/session/reference_video-0-{version.sha256[:16]}-{ 'c' * 32 }.mp4"
    assert result.url == f"oss://{expected_key}"
    assert result.provider_model_id == "wan3.0-video"
    assert result.scope == WAN3_SCOPE
    assert result.credential_ref == "credentials/original"
    assert result.version == version

    assert len(captured["gets"]) == 1
    policy_url, policy_options = captured["gets"][0]
    assert policy_url == WAN3_SCOPE.base_url + "/api/v1/uploads"
    assert policy_options["params"] == {"action": "getPolicy", "model": "wan3.0-video"}
    assert policy_options["headers"] == {
        "Authorization": "Bearer test-only-secret",
        "Content-Type": "application/json",
    }
    assert policy_options["allow_redirects"] is False

    assert len(captured["posts"]) == 1
    upload_url, upload_options = captured["posts"][0]
    assert upload_url == "https://bucket.oss-cn-beijing.aliyuncs.com"
    assert upload_options["headers"] == {}
    assert "Authorization" not in upload_options["headers"]
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
    assert fields[0][1] == (None, "ak-token")
    assert fields[2][1] == (None, "signature-token")
    assert fields[3][1] == (None, expected_key)
    assert fields[4][1] == (None, "private")
    assert fields[5][1] == (None, "true")
    assert fields[-1][1] == (expected_key.rsplit("/", 1)[-1], data, "video/mp4")
    assert "video-synthesis" not in policy_url and "video-synthesis" not in upload_url


@pytest.mark.parametrize(
    "mutation",
    [
        "bad-bytes",
        "bad-role",
        "bad-mime",
        "bad-model",
        "bad-scope",
        "bad-credential-scope",
        "oversize-audio",
    ],
)
def test_upload_validates_identity_before_any_http(monkeypatch, mutation):
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr("src.models.provider_upload.requests.post", lambda *a, **k: calls.append((a, k)))
    data = b"media bytes"
    version = version_for(data)
    scope = WAN3_SCOPE
    credential = CREDENTIAL
    model_id = "wan3.0-video"
    payload = data
    if mutation == "bad-bytes":
        payload = b"changed bytes"
    elif mutation == "bad-role":
        version = version_for(data, role="first_frame", mime="image/png")
    elif mutation == "bad-mime":
        version = version_for(data, mime="video/avi")
    elif mutation == "bad-model":
        model_id = "wan2.7-video"
    elif mutation == "bad-scope":
        scope = replace(WAN3_SCOPE, gateway="dashscope", base_url="https://dashscope.aliyuncs.com")
    elif mutation == "bad-credential-scope":
        credential = replace(CREDENTIAL, scope=replace(WAN3_SCOPE, workspace_id="other-workspace"))
    elif mutation == "oversize-audio":
        payload = b"x" * (15 * 1024 * 1024 + 1)
        version = version_for(payload, role="reference_audio", mime="audio/mpeg")

    with pytest.raises(ProviderUploadError) as raised:
        upload_dashscope_temporary_media(
            scope=scope,
            provider_model_id=model_id,
            credential=credential,
            version=version,
            data=payload,
        )
    assert calls == []
    message = str(raised.value)
    assert "test-only-secret" not in message
    assert "media bytes" not in message


@pytest.mark.parametrize(
    "policy",
    [
        policy_payload(upload_host="http://bucket.oss-cn-beijing.aliyuncs.com"),
        policy_payload(upload_host="https://evil.example.com"),
        policy_payload(signature=""),
        policy_payload(upload_dir="../escape"),
        policy_payload(x_oss_object_acl="public-read"),
        policy_payload(x_oss_forbid_overwrite="false"),
        policy_payload(max_file_size_mb=0),
        {"data": {"policy": "policy-token"}},
    ],
)
def test_upload_rejects_malformed_policy_before_oss_post(monkeypatch, policy):
    data = b"video bytes"
    captured = install_success_transport(monkeypatch, policy=policy)
    with pytest.raises(ProviderUploadError) as raised:
        upload_dashscope_temporary_media(
            scope=WAN3_SCOPE,
            provider_model_id="wan3.0-video",
            credential=CREDENTIAL,
            version=version_for(data),
            data=data,
        )
    assert len(captured["gets"]) == 1
    assert captured["posts"] == []
    message = str(raised.value)
    assert "policy-token" not in message
    assert "signature-token" not in message
    assert "test-only-secret" not in message
    assert "video bytes" not in message



def test_upload_accepts_official_decimal_string_policy_limits(monkeypatch):
    data = b"video bytes"
    captured = install_success_transport(
        monkeypatch,
        policy=policy_payload(
            expire_in_seconds="300",
            max_file_size_mb="100",
            capacity_limit_mb="999999999",
        ),
    )
    result = upload_dashscope_temporary_media(
        scope=WAN3_SCOPE,
        provider_model_id="wan3.0-video",
        credential=CREDENTIAL,
        version=version_for(data),
        data=data,
    )
    assert result.url.startswith("oss://dashscope-temp/session/reference_video-0-")
    assert len(captured["posts"]) == 1


@pytest.mark.parametrize("value", [True, "", " 100", "100 ", "+100", "001", "0", "1.0", "1e2", "9" * 40])
def test_upload_rejects_noncanonical_policy_limit_strings_before_oss_post(monkeypatch, value):
    data = b"video bytes"
    captured = install_success_transport(monkeypatch, policy=policy_payload(max_file_size_mb=value))
    with pytest.raises(ProviderUploadError):
        upload_dashscope_temporary_media(
            scope=WAN3_SCOPE,
            provider_model_id="wan3.0-video",
            credential=CREDENTIAL,
            version=version_for(data),
            data=data,
        )
    assert len(captured["gets"]) == 1
    assert captured["posts"] == []


def test_upload_repeated_same_policy_and_bytes_use_distinct_object_keys(monkeypatch):
    data = b"video bytes"
    uuids = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr("src.models.provider_upload._new_upload_nonce", lambda: next(uuids))
    captured = install_success_transport(monkeypatch)
    kwargs = dict(
        scope=WAN3_SCOPE,
        provider_model_id="wan3.0-video",
        credential=CREDENTIAL,
        version=version_for(data),
        data=data,
    )
    first = upload_dashscope_temporary_media(**kwargs)
    second = upload_dashscope_temporary_media(**kwargs)
    assert first.key != second.key
    assert first.key.endswith(f"-{ 'a' * 32 }.mp4")
    assert second.key.endswith(f"-{ 'b' * 32 }.mp4")
    assert len(captured["posts"]) == 2


def test_upload_reads_bounded_streamed_policy_response_and_closes(monkeypatch):
    data = b"video bytes"
    response = Response(200, chunks=[b'{"data": ', __import__("json").dumps(policy_payload()["data"]).encode("utf-8"), b'}'])
    captured = {"get_kwargs": None, "post_kwargs": None}

    def fake_get(url, **kwargs):
        captured["get_kwargs"] = kwargs
        return response

    def fake_post(url, **kwargs):
        captured["post_kwargs"] = kwargs
        return Response(200, {})

    monkeypatch.setattr("src.models.provider_upload.requests.get", fake_get)
    monkeypatch.setattr("src.models.provider_upload.requests.post", fake_post)
    upload_dashscope_temporary_media(
        scope=WAN3_SCOPE,
        provider_model_id="wan3.0-video",
        credential=CREDENTIAL,
        version=version_for(data),
        data=data,
    )
    assert captured["get_kwargs"]["stream"] is True
    assert response.closed is True
    assert captured["post_kwargs"]["stream"] is True


def test_upload_rejects_oversized_policy_response_before_oss_post_and_closes(monkeypatch):
    data = b"video bytes"
    response = Response(200, chunks=[b"{" + b"x" * (80 * 1024)])
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *a, **k: response)
    monkeypatch.setattr("src.models.provider_upload.requests.post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ProviderUploadError) as raised:
        upload_dashscope_temporary_media(
            scope=WAN3_SCOPE,
            provider_model_id="wan3.0-video",
            credential=CREDENTIAL,
            version=version_for(data),
            data=data,
        )
    assert raised.value.reason == "policy_protocol"
    assert response.closed is True
    assert calls == []


def test_upload_closes_oss_response_without_buffering_body(monkeypatch):
    data = b"video bytes"
    upload_response = Response(200, chunks=[b"unexpected body"] )

    def fake_post(url, **kwargs):
        return upload_response

    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *a, **k: Response(200, policy_payload()))
    monkeypatch.setattr("src.models.provider_upload.requests.post", fake_post)
    upload_dashscope_temporary_media(
        scope=WAN3_SCOPE,
        provider_model_id="wan3.0-video",
        credential=CREDENTIAL,
        version=version_for(data),
        data=data,
    )
    assert upload_response.closed is True


@pytest.mark.parametrize("host", [
    "https://bucket.oss-cn-beijing.aliyuncs.com:444",
    "https://bucket.oss-cn-beijing.aliyuncs.com:bad",
    "https://bucket.notoss-cn-beijing.aliyuncs.com",
    "https://bucket.oss-cn-beijing.aliyuncs.com.evil.example",
])
def test_upload_rejects_non_official_or_non_default_oss_hosts(monkeypatch, host):
    data = b"video bytes"
    captured = install_success_transport(monkeypatch, policy=policy_payload(upload_host=host))
    with pytest.raises(ProviderUploadError):
        upload_dashscope_temporary_media(
            scope=WAN3_SCOPE,
            provider_model_id="wan3.0-video",
            credential=CREDENTIAL,
            version=version_for(data),
            data=data,
        )
    assert len(captured["gets"]) == 1
    assert captured["posts"] == []


def test_upload_wraps_midstream_policy_transport_error_and_closes_without_post(monkeypatch):
    data = b"video bytes"

    class BrokenStream(Response):
        def iter_content(self, chunk_size=8192):
            yield b'{"data": {'
            import requests
            raise requests.ConnectionError("secret=test-only-secret signature-token video bytes")

    response = BrokenStream(200)
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *a, **k: response)
    monkeypatch.setattr("src.models.provider_upload.requests.post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ProviderUploadError) as raised:
        upload_dashscope_temporary_media(
            scope=WAN3_SCOPE,
            provider_model_id="wan3.0-video",
            credential=CREDENTIAL,
            version=version_for(data),
            data=data,
        )
    assert raised.value.reason == "policy_transport"
    assert response.closed is True
    assert calls == []
    message = str(raised.value)
    assert "test-only-secret" not in message
    assert "signature-token" not in message
    assert "video bytes" not in message
    diagnostic = "".join(traceback.format_exception(raised.value))
    assert "secret=test-only-secret" not in diagnostic


def test_upload_wraps_deep_policy_json_recursion_as_protocol_and_closes(monkeypatch):
    data = b"video bytes"
    response = Response(200, chunks=[b"[" * 10000, b"]" * 10000])
    calls = []
    monkeypatch.setattr("src.models.provider_upload.requests.get", lambda *a, **k: response)
    monkeypatch.setattr("src.models.provider_upload.requests.post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ProviderUploadError) as raised:
        upload_dashscope_temporary_media(
            scope=WAN3_SCOPE,
            provider_model_id="wan3.0-video",
            credential=CREDENTIAL,
            version=version_for(data),
            data=data,
        )
    assert raised.value.reason == "policy_protocol"
    assert response.closed is True
    assert calls == []

def test_upload_failure_is_typed_and_does_not_return_url(monkeypatch):
    data = b"video bytes"
    captured = install_success_transport(monkeypatch, upload_status=403)
    with pytest.raises(ProviderUploadError) as raised:
        upload_dashscope_temporary_media(
            scope=WAN3_SCOPE,
            provider_model_id="wan3.0-video-prime",
            credential=CREDENTIAL,
            version=version_for(data),
            data=data,
        )
    assert raised.value.reason == "upload_http"
    assert raised.value.status_code == 403
    assert len(captured["posts"]) == 1
    assert "oss://" not in str(raised.value)


@pytest.mark.parametrize(
    ("mime", "suffix"),
    [("video/quicktime", "mov"), ("audio/wav", "wav"), ("audio/mpeg", "mp3")],
)
def test_upload_accepts_wan3_video_and_audio_mimes(monkeypatch, mime, suffix):
    data = f"{mime} bytes".encode("utf-8")
    role = "reference_audio" if mime.startswith("audio/") else "reference_video"
    captured = install_success_transport(monkeypatch)
    result = upload_dashscope_temporary_media(
        scope=WAN3_SCOPE,
        provider_model_id="wan3.0-video",
        credential=CREDENTIAL,
        version=version_for(data, role=role, mime=mime),
        data=data,
    )
    assert result.url.endswith(f".{suffix}")
    assert captured["posts"][0][1]["files"][-1][1][2] == mime


def test_upload_accepts_kling_workspace_images(monkeypatch):
    data = b"png bytes"
    monkeypatch.setattr("src.models.provider_upload._new_upload_nonce", lambda: "d" * 32)
    captured = install_success_transport(monkeypatch)
    version = version_for(data, role="first_frame", mime="image/png")

    result = upload_dashscope_temporary_media(
        scope=WAN3_SCOPE,
        provider_model_id="kling/kling-v3-video-generation",
        credential=CREDENTIAL,
        version=version,
        data=data,
    )

    assert result.url.endswith(f"first_frame-0-{version.sha256[:16]}-{'d' * 32}.png")
    assert captured["gets"][0][1]["params"] == {
        "action": "getPolicy",
        "model": "kling/kling-v3-video-generation",
    }
    assert captured["posts"][0][1]["files"][-1][1][2] == "image/png"
