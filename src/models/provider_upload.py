"""DashScope temporary uploads from frozen provider media bytes.

This module is intentionally smaller and stricter than the legacy Wanx path
helper. It never reads mutable files, global environment, OSS configuration, or
current provider defaults. Callers must provide the exact scope, credential,
model id, MediaVersion and bytes they already accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping
import uuid
from urllib.parse import urlsplit

import requests

from .provider_image_policy import get_provider_image_policy
from .provider_tasks import ProviderScope, ResolvedCredential
from .wanx import _dashscope_scope_headers
from ..utils.media_versions import MediaVersion


_WAN3_WORKSPACE_MODELS = {"wan3.0-video", "wan3.0-video-prime"}
_HAPPYHORSE_WORKSPACE_MEDIA_ROLES = {
    "happyhorse-1.0-video-edit": {"reference_video"},
}
_WAN27_CLASSIC_MEDIA_ROLES = {
    "wan2.7-videoedit": {"reference_video"},
    "wan2.7-r2v": {"reference_video", "reference_audio"},
    "wan2.7-r2v-2026-06-12": {"reference_video", "reference_audio"},
}
_VIDEO_MIMES = {"video/mp4": "mp4", "video/quicktime": "mov"}
_AUDIO_MIMES = {"audio/wav": "wav", "audio/mpeg": "mp3"}
_VIDEO_MAX_BYTES = 100 * 1024 * 1024
_AUDIO_MAX_BYTES = 15 * 1024 * 1024


class ProviderUploadError(RuntimeError):
    """A temporary upload failed before any generation task was submitted."""

    def __init__(self, reason: str, *, status_code: int | None = None):
        self.reason = reason
        self.status_code = status_code
        suffix = f", status={status_code}" if status_code is not None else ""
        super().__init__(f"Provider upload failed ({reason}{suffix})")


@dataclass(frozen=True)
class ProviderUploadResult:
    """Secret-free binding returned after a successful temporary upload."""

    url: str
    key: str
    provider_model_id: str
    version: MediaVersion
    scope: ProviderScope
    credential_ref: str


def upload_dashscope_temporary_media(
    *,
    scope: ProviderScope,
    provider_model_id: str,
    credential: ResolvedCredential,
    version: MediaVersion,
    data: bytes,
    timeout: tuple[int, int] = (10, 120),
) -> ProviderUploadResult:
    """Upload frozen provider media bytes to a DashScope temporary OSS URL."""
    _validate_static_inputs(scope, provider_model_id, credential, version, data)
    policy = _get_upload_policy(scope, provider_model_id, credential, timeout=timeout)
    key = _object_key(str(policy["upload_dir"]), version, nonce=_new_upload_nonce(), provider_model_id=provider_model_id)
    _upload_to_oss(policy, key=key, version=version, data=data, timeout=timeout)
    return ProviderUploadResult(
        url=f"oss://{key}",
        key=key,
        provider_model_id=provider_model_id,
        version=version,
        scope=scope,
        credential_ref=credential.credential_ref,
    )


def _validate_static_inputs(
    scope: ProviderScope,
    provider_model_id: str,
    credential: ResolvedCredential,
    version: MediaVersion,
    data: bytes,
) -> None:
    if not isinstance(scope, ProviderScope):
        raise ProviderUploadError("scope_invalid")
    _validate_model_scope_media(scope, provider_model_id, version)
    try:
        _dashscope_scope_headers(scope)
    except Exception as exc:
        raise ProviderUploadError("scope_invalid") from exc
    if not isinstance(credential, ResolvedCredential) or credential.scope != scope:
        raise ProviderUploadError("credential_scope_mismatch")
    if not isinstance(version, MediaVersion):
        raise ProviderUploadError("media_version_invalid")
    if type(data) is not bytes or not data:
        raise ProviderUploadError("media_bytes_invalid")
    if len(data) != version.size_bytes or hashlib.sha256(data).hexdigest() != version.sha256:
        raise ProviderUploadError("media_bytes_mismatch")
    _media_suffix_and_limit(version, provider_model_id)


def _validate_model_scope_media(
    scope: ProviderScope,
    provider_model_id: str,
    version: MediaVersion,
) -> None:
    if provider_model_id in _WAN3_WORKSPACE_MODELS:
        expected_gateway = "dashscope_workspace"
        allowed_roles = {"reference_video", "reference_audio"}
    elif provider_model_id in _HAPPYHORSE_WORKSPACE_MEDIA_ROLES:
        expected_gateway = "dashscope_workspace"
        allowed_roles = _HAPPYHORSE_WORKSPACE_MEDIA_ROLES[provider_model_id]
    elif (image_policy := get_provider_image_policy(provider_model_id)) is not None:
        expected_gateway = "dashscope_workspace"
        allowed_roles = {image_policy.role}
    elif provider_model_id in _WAN27_CLASSIC_MEDIA_ROLES:
        expected_gateway = "dashscope"
        allowed_roles = _WAN27_CLASSIC_MEDIA_ROLES[provider_model_id]
    else:
        raise ProviderUploadError("model_invalid")
    if scope.gateway != expected_gateway:
        raise ProviderUploadError("scope_invalid")
    if isinstance(version, MediaVersion) and version.role not in allowed_roles:
        raise ProviderUploadError("media_role_invalid")


def _media_suffix_and_limit(version: MediaVersion, provider_model_id: str | None = None) -> tuple[str, int]:
    if version.role == "reference_video" and version.declared_media_type in _VIDEO_MIMES:
        suffix, max_bytes = _VIDEO_MIMES[version.declared_media_type], _VIDEO_MAX_BYTES
    elif version.role == "reference_audio" and version.declared_media_type in _AUDIO_MIMES:
        suffix, max_bytes = _AUDIO_MIMES[version.declared_media_type], _AUDIO_MAX_BYTES
    elif (policy := get_provider_image_policy(provider_model_id or "")) is not None:
        if version.role != policy.role or not isinstance(version.declared_media_type, str) or version.declared_media_type not in policy.mime_suffixes:
            raise ProviderUploadError("media_type_invalid")
        suffix, max_bytes = policy.mime_suffixes[version.declared_media_type], policy.max_bytes
    else:
        raise ProviderUploadError("media_type_invalid")
    if version.size_bytes > max_bytes:
        raise ProviderUploadError("media_size_invalid")
    return suffix, max_bytes


def _get_upload_policy(
    scope: ProviderScope,
    provider_model_id: str,
    credential: ResolvedCredential,
    *,
    timeout: tuple[int, int],
) -> dict[str, str | int]:
    url = f"{scope.base_url.rstrip('/')}/api/v1/uploads"
    try:
        response = requests.get(
            url,
            params={"action": "getPolicy", "model": provider_model_id},
            headers={
                "Authorization": f"Bearer {credential.secret}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            allow_redirects=False,
            stream=True,
            auth=lambda prepared: prepared,
        )
    except requests.RequestException:
        raise ProviderUploadError("policy_transport") from None
    try:
        if response.status_code != 200:
            raise ProviderUploadError("policy_http", status_code=response.status_code)
        payload = _read_bounded_policy_json(response)
    finally:
        response.close()
    data = payload.get("data") if isinstance(payload, Mapping) else None
    return _validate_policy_data(data)



def _read_bounded_policy_json(response: Any, *, max_bytes: int = 64 * 1024) -> Any:
    chunks: list[bytes] = []
    size = 0
    try:
        iterator = response.iter_content(chunk_size=8192)
    except AttributeError as exc:
        raise ProviderUploadError("policy_protocol") from exc
    try:
        for chunk in iterator:
            if not chunk:
                continue
            if type(chunk) is not bytes:
                raise ProviderUploadError("policy_protocol")
            size += len(chunk)
            if size > max_bytes:
                raise ProviderUploadError("policy_protocol")
            chunks.append(chunk)
    except requests.RequestException:
        raise ProviderUploadError("policy_transport") from None
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProviderUploadError("policy_protocol") from exc

def _validate_policy_data(raw: Any) -> dict[str, str | int]:
    if not isinstance(raw, Mapping):
        raise ProviderUploadError("policy_invalid")
    required = {
        "policy", "signature", "upload_dir", "upload_host", "expire_in_seconds",
        "max_file_size_mb", "capacity_limit_mb", "oss_access_key_id",
        "x_oss_object_acl", "x_oss_forbid_overwrite",
    }
    if required - set(raw):
        raise ProviderUploadError("policy_invalid")
    policy = {
        "policy": _field(raw["policy"], "policy"),
        "signature": _field(raw["signature"], "signature"),
        "upload_dir": _upload_dir(raw["upload_dir"]),
        "upload_host": _upload_host(raw["upload_host"]),
        "expire_in_seconds": _bounded_int(raw["expire_in_seconds"], "expire", upper=172800),
        "max_file_size_mb": _bounded_int(raw["max_file_size_mb"], "max_file", upper=1024),
        "capacity_limit_mb": _bounded_int(raw["capacity_limit_mb"], "capacity", upper=999999999),
        "oss_access_key_id": _field(raw["oss_access_key_id"], "access_key"),
        "x_oss_object_acl": _field(raw["x_oss_object_acl"], "acl"),
        "x_oss_forbid_overwrite": _field(raw["x_oss_forbid_overwrite"], "overwrite"),
    }
    if policy["x_oss_object_acl"] != "private" or policy["x_oss_forbid_overwrite"] != "true":
        raise ProviderUploadError("policy_security_invalid")
    return policy


def _field(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 16384
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ProviderUploadError(f"{label}_invalid")
    return value


def _bounded_int(value: Any, label: str, *, upper: int) -> int:
    if type(value) is int:
        candidate = value
    elif isinstance(value, str):
        if len(value) > 9 or re.fullmatch(r"[1-9][0-9]*", value) is None:
            raise ProviderUploadError(f"{label}_invalid")
        candidate = int(value)
    else:
        raise ProviderUploadError(f"{label}_invalid")
    if not 0 < candidate <= upper:
        raise ProviderUploadError(f"{label}_invalid")
    return candidate


def _upload_dir(value: Any) -> str:
    text = _field(value, "upload_dir")
    if (
        text.startswith("/")
        or text.endswith("/")
        or "//" in text
        or any(part in {"", ".", ".."} for part in text.split("/"))
        or re.fullmatch(r"[A-Za-z0-9._=/-]{1,512}", text) is None
    ):
        raise ProviderUploadError("upload_dir_invalid")
    return text


def _upload_host(value: Any) -> str:
    text = _field(value, "upload_host")
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise ProviderUploadError("upload_host_invalid") from exc
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProviderUploadError("upload_host_invalid") from exc
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    official_oss_host = re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+oss-[a-z0-9-]+\.aliyuncs\.com",
        hostname,
    ) is not None
    if (
        parsed.scheme != "https"
        or not official_oss_host
        or port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ProviderUploadError("upload_host_invalid")
    return text.rstrip("/")


def _new_upload_nonce() -> str:
    return uuid.uuid4().hex


def _object_key(
    upload_dir: str, version: MediaVersion, *, nonce: str, provider_model_id: str | None = None,
) -> str:
    if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ProviderUploadError("upload_nonce_invalid")
    suffix, _ = _media_suffix_and_limit(version, provider_model_id)
    filename = f"{version.role}-{version.ordinal}-{version.sha256[:16]}-{nonce}.{suffix}"
    return f"{upload_dir}/{filename}"


def _upload_to_oss(
    policy: Mapping[str, str | int],
    *,
    key: str,
    version: MediaVersion,
    data: bytes,
    timeout: tuple[int, int],
) -> None:
    if version.size_bytes > int(policy["max_file_size_mb"]) * 1024 * 1024:
        raise ProviderUploadError("policy_size_invalid")
    filename = key.rsplit("/", 1)[-1]
    fields = [
        ("OSSAccessKeyId", (None, str(policy["oss_access_key_id"]))),
        ("policy", (None, str(policy["policy"]))),
        ("Signature", (None, str(policy["signature"]))),
        ("key", (None, key)),
        ("x-oss-object-acl", (None, str(policy["x_oss_object_acl"]))),
        ("x-oss-forbid-overwrite", (None, str(policy["x_oss_forbid_overwrite"]))),
        ("success_action_status", (None, "200")),
        ("file", (filename, data, version.declared_media_type or "application/octet-stream")),
    ]
    try:
        response = requests.post(
            str(policy["upload_host"]),
            files=fields,
            headers={},
            timeout=timeout,
            allow_redirects=False,
            stream=True,
            auth=lambda prepared: prepared,
        )
    except requests.RequestException:
        raise ProviderUploadError("upload_transport") from None
    try:
        if response.status_code != 200:
            raise ProviderUploadError("upload_http", status_code=response.status_code)
    finally:
        response.close()
