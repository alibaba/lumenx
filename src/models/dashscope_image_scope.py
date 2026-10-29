"""Exact classic DashScope image authority shared by explicitly frozen T2I profiles."""

from collections.abc import Mapping

from .provider_binding import ProviderCredentialStore
from .provider_tasks import ProviderBindingError, ProviderScope
from ..utils.endpoints import DASHSCOPE_CLASSIC_ENDPOINTS

_REGIONS = frozenset({"cn-beijing", "ap-southeast-1"})
_CLASSIC_ENDPOINTS = {
    endpoint: region
    for region, endpoint in DASHSCOPE_CLASSIC_ENDPOINTS.items()
    if region in _REGIONS
}


def validate_classic_image_scope(scope: ProviderScope) -> None:
    """Validate the exact original classic authority, including its workspace header."""
    ProviderCredentialStore._validate_dashscope_scope(scope)
    if scope.gateway != "dashscope" or scope.region not in {
        "cn-beijing",
        "ap-southeast-1",
    }:
        raise ProviderBindingError("Unsupported bound image scope")


def classic_image_scope_from_snapshot(snapshot: Mapping[str, str]) -> ProviderScope:
    """Select only an exact classic scope from one caller-owned admission snapshot."""
    if any(
        snapshot.get(key)
        for key in ("DASHSCOPE_ACCOUNT_ID", "DASHSCOPE_ACCOUNT_IDENTITY")
    ):
        raise ProviderBindingError("Unsupported bound image account")
    base = snapshot.get("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com"
    region = next(
        (
            value
            for endpoint, value in _CLASSIC_ENDPOINTS.items()
            if base in (endpoint, endpoint + "/")
        ),
        None,
    )
    if region is None or snapshot.get("DASHSCOPE_REGION") not in (None, "", region):
        raise ProviderBindingError("Unsupported bound image endpoint")
    primary, legacy = (
        snapshot.get("DASHSCOPE_WORKSPACE_ID"),
        snapshot.get("DASHSCOPE_WORKSPACE"),
    )
    if primary and legacy and primary != legacy:
        raise ProviderBindingError("Conflicting bound image workspace")
    scope = ProviderScope(
        "dashscope", "dashscope", "dashscope", base, region, primary or legacy or None
    )
    validate_classic_image_scope(scope)
    return scope
