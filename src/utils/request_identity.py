"""Stable request identity helpers for product-owned provider receipts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

REQUEST_IDENTITY_CONTRACT_VERSION = "request-json-v1"
_SUPPORTED_CONTRACT_VERSIONS = {REQUEST_IDENTITY_CONTRACT_VERSION}
_CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def validate_client_request_id(value: Any) -> str:
    """Validate the project-scoped opaque client request id used for lookup."""
    if type(value) is not str or _CLIENT_REQUEST_ID_RE.fullmatch(value) is None:
        raise ValueError("client_request_id must be 1-128 ASCII chars: letters, digits, . _ : -")
    return value


def canonical_request_payload(
    request: Mapping[str, Any],
    *,
    entrypoint: str,
    contract_version: str = REQUEST_IDENTITY_CONTRACT_VERSION,
) -> dict[str, Any]:
    """Return the strict canonical payload used for request identity hashes.

    The helper intentionally preserves raw JSON semantics. It does not apply
    catalog defaults, trim strings, normalize Unicode, or rewrite field names.
    """
    _validate_non_empty_string("entrypoint", entrypoint)
    _validate_non_empty_string("contract_version", contract_version)
    if contract_version not in _SUPPORTED_CONTRACT_VERSIONS:
        raise ValueError("Unsupported request identity contract version")
    if not isinstance(request, Mapping):
        raise ValueError("Request identity input must be a JSON object")
    return {
        "entrypoint": entrypoint,
        "request": _canonical_json_value(request, path="request"),
        "request_contract_version": contract_version,
    }


def canonical_request_json(
    request: Mapping[str, Any],
    *,
    entrypoint: str,
    contract_version: str = REQUEST_IDENTITY_CONTRACT_VERSION,
) -> str:
    """Serialize a request identity payload with deterministic JSON ordering."""
    payload = canonical_request_payload(
        request,
        entrypoint=entrypoint,
        contract_version=contract_version,
    )
    try:
        rendered = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        rendered.encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("Request identity payload is not strict UTF-8 JSON") from exc
    return rendered


def fingerprint_request(
    request: Mapping[str, Any],
    *,
    entrypoint: str,
    contract_version: str = REQUEST_IDENTITY_CONTRACT_VERSION,
) -> str:
    """Return a sha256 fingerprint for a strict canonical request identity."""
    rendered = canonical_request_json(
        request,
        entrypoint=entrypoint,
        contract_version=contract_version,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _validate_non_empty_string(name: str, value: Any) -> None:
    if type(value) is not str or value == "":
        raise ValueError(f"{name} must be a non-empty string")
    _validate_utf8_string(name, value)


def _validate_utf8_string(path: str, value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{path} must be valid UTF-8") from exc
    return value


def _canonical_json_value(value: Any, *, path: str) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        return _validate_utf8_string(path, value)
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be a finite JSON number")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or key == "":
                raise ValueError(f"{path} keys must be non-empty strings")
            _validate_utf8_string(f"{path}.{key}", key)
            output[key] = _canonical_json_value(item, path=f"{path}.{key}")
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise ValueError(f"{path} contains a value that is not strict JSON")
