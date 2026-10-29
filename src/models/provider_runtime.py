"""Neutral construction of provider recovery runtime from app config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .provider_binding import MULEROUTER_HTTP_BASE_URL, MULEROUTER_HTTP_SOURCE_KEY, ProviderCredentialStore
from .pixverse_core_contract import PIXVERSE_PROVIDER_MODELS
from .provider_recovery import ProviderRecoveryError
from .provider_tasks import ProviderBindingError, ProviderScope
from .vidu_core_contract import VIDU_PROVIDER_MODELS
from .wanx import WanxModel, _dashscope_scope_headers
from ..utils.endpoints import (
    DASHSCOPE_CLASSIC_ENDPOINTS,
    dashscope_classic_region,
    dashscope_workspace_scope_matches,
    get_provider_base_url,
)
from ..utils.runtime_environment import RuntimeEnvironment


_CLASSIC_DASHSCOPE_REGIONS = (
    "cn-beijing",
    "ap-southeast-1",
    "us-east-1",
    "cn-hongkong",
)
_WORKSPACE_KEYS = ("DASHSCOPE_WORKSPACE", "DASHSCOPE_WORKSPACE_ID")
_ACCOUNT_IDENTITY_KEYS = ("DASHSCOPE_ACCOUNT_IDENTITY", "DASHSCOPE_ACCOUNT_ID")
_WAN2_CLASSIC_MODELS = frozenset({
    "wan2.6-i2v",
    "wan2.6-i2v-flash",
    "wan2.7-i2v",
    "wan2.7-r2v",
    "wan2.7-r2v-2026-06-12",
    "wan2.7-t2v",
    "wan2.7-videoedit",
})
_WAN3_WORKSPACE_MODELS = frozenset({"wan3.0-video", "wan3.0-video-prime"})
_HAPPYHORSE_WORKSPACE_MODELS = frozenset({
    "happyhorse-1.1-t2v",
    "happyhorse-1.1-i2v",
    "happyhorse-1.1-r2v",
    "happyhorse-1.0-video-edit",
})
_KLING_WORKSPACE_MODELS = frozenset({
    "kling/kling-v3-video-generation",
    "kling/kling-v3-omni-video-generation",
})


@dataclass(frozen=True)
class ProviderRecoveryRuntime:
    """Explicit runtime injection; never reads environment or user config."""

    scope: ProviderScope
    secret_resolver: Callable[[], str]
    provider: Any = None
    # None selects the accepted model limit; explicit values only lower it.
    max_bytes: int | None = None
    output_max_bytes: int = 200 * 1024 * 1024

    def resolve_secret(self) -> str:
        secret = self.secret_resolver()
        if not isinstance(secret, str) or not secret:
            raise ProviderRecoveryError("provider_secret_unavailable")
        return secret


@dataclass(frozen=True)
class ProviderRuntimeBuildResult:
    runtime: ProviderRecoveryRuntime | None
    reason: str | None = None


@dataclass(frozen=True)
class ProviderScopeBuildResult:
    scope: ProviderScope | None
    reason: str | None = None


def build_provider_recovery_runtime(
    runtime_environment: RuntimeEnvironment,
    *,
    output_root: str | Path,
) -> ProviderRuntimeBuildResult:
    """Build a current provider recovery runtime without registering it.

    The returned runtime captures one immutable app configuration snapshot for
    both credential resolution and the Wanx provider client. It never performs
    network, CLI, submit, query, fetch, app-state mutation, or paid work.
    """
    snapshot = dict(runtime_environment.snapshot())
    if _has_any(snapshot, _WORKSPACE_KEYS):
        return ProviderRuntimeBuildResult(None, "unsupported_dashscope_workspace")
    if _has_any(snapshot, _ACCOUNT_IDENTITY_KEYS):
        return ProviderRuntimeBuildResult(None, "unsupported_dashscope_account_identity")

    base_url = get_provider_base_url("DASHSCOPE", env=snapshot)
    scope = _classic_dashscope_scope(base_url)
    if scope is None:
        return ProviderRuntimeBuildResult(None, "unsupported_dashscope_endpoint")

    return _runtime_for_scope(snapshot, output_root=output_root, scope=scope)


def build_provider_runtime_for_admission(
    runtime_environment: RuntimeEnvironment,
    *,
    output_root: str | Path,
    provider_model_id: str,
    gateway: str,
) -> ProviderRuntimeBuildResult:
    """Resolve a new provider admission from explicit model and gateway metadata."""
    from .seedance_core_contract import SEEDANCE_PROVIDER_MODELS

    snapshot = dict(runtime_environment.snapshot())
    if provider_model_id in SEEDANCE_PROVIDER_MODELS:
        if gateway != "mulerouter":
            return ProviderRuntimeBuildResult(None, "unsupported_provider_gateway")
        result = build_seedance_http_scope_from_snapshot(snapshot)
        if result.scope is None:
            return ProviderRuntimeBuildResult(None, result.reason)
        return _seedance_http_runtime(snapshot, result.scope)
    if provider_model_id in _WAN2_CLASSIC_MODELS:
        if gateway != "dashscope":
            return ProviderRuntimeBuildResult(None, "unsupported_dashscope_gateway")
        return _classic_admission_runtime(snapshot, output_root=output_root)
    if provider_model_id in _WAN3_WORKSPACE_MODELS:
        if gateway != "dashscope_workspace":
            return ProviderRuntimeBuildResult(None, "unsupported_dashscope_gateway")
        return _workspace_admission_runtime(snapshot, output_root=output_root)
    if provider_model_id in _HAPPYHORSE_WORKSPACE_MODELS:
        if gateway != "dashscope_workspace":
            return ProviderRuntimeBuildResult(None, "unsupported_dashscope_gateway")
        return _workspace_admission_runtime(snapshot, output_root=output_root)
    if (
        provider_model_id in _KLING_WORKSPACE_MODELS
        or provider_model_id in VIDU_PROVIDER_MODELS
        or provider_model_id in PIXVERSE_PROVIDER_MODELS
    ):
        if gateway != "dashscope_workspace":
            return ProviderRuntimeBuildResult(None, "unsupported_dashscope_gateway")
        if (snapshot.get("DASHSCOPE_REGION") or "").strip() != "cn-beijing":
            return ProviderRuntimeBuildResult(None, "unsupported_provider_region")
        return _workspace_admission_runtime(snapshot, output_root=output_root)
    return ProviderRuntimeBuildResult(None, "unsupported_provider_model")


def build_classic_dashscope_scope_for_admission(
    runtime_environment: RuntimeEnvironment,
) -> ProviderScopeBuildResult:
    """Resolve a classic DashScope admission scope without constructing a provider.

    Workspace-configured apps may still admit classic DashScope models only when
    the workspace scope is exact and the region has an official classic host.
    """
    return build_classic_dashscope_scope_from_snapshot(runtime_environment.snapshot())


def build_classic_dashscope_scope_from_snapshot(
    snapshot: Mapping[str, str],
) -> ProviderScopeBuildResult:
    """Resolve a classic DashScope admission scope from an already-frozen snapshot."""
    return _classic_admission_scope(dict(snapshot))


def _classic_admission_scope(snapshot: Mapping[str, str]) -> ProviderScopeBuildResult:
    if not _has_any(snapshot, _WORKSPACE_KEYS):
        scope = _classic_dashscope_scope(get_provider_base_url("DASHSCOPE", env=snapshot))
        if scope is None:
            return ProviderScopeBuildResult(None, "unsupported_dashscope_endpoint")
        return ProviderScopeBuildResult(scope)
    if _has_any(snapshot, _ACCOUNT_IDENTITY_KEYS):
        return ProviderScopeBuildResult(None, "unsupported_dashscope_account_identity")
    workspace_id, reason = _workspace_id_from_snapshot(snapshot)
    if workspace_id is None:
        return ProviderScopeBuildResult(None, reason)
    region = (snapshot.get("DASHSCOPE_REGION") or "").strip()
    base_url = (snapshot.get("DASHSCOPE_BASE_URL") or "").strip().rstrip("/")
    if not region:
        return ProviderScopeBuildResult(None, "missing_dashscope_region")
    if not base_url:
        return ProviderScopeBuildResult(None, "missing_dashscope_workspace_base_url")
    if not dashscope_workspace_scope_matches(base_url, workspace_id, region):
        return ProviderScopeBuildResult(None, "unsupported_dashscope_workspace_scope")
    classic_base_url = DASHSCOPE_CLASSIC_ENDPOINTS.get(region)
    if classic_base_url is None:
        return ProviderScopeBuildResult(None, "unsupported_dashscope_classic_region")
    scope = _classic_dashscope_scope(classic_base_url)
    if scope is None:
        return ProviderScopeBuildResult(None, "unsupported_dashscope_endpoint")
    return ProviderScopeBuildResult(scope)


def _classic_admission_runtime(
    snapshot: Mapping[str, str],
    *,
    output_root: str | Path,
) -> ProviderRuntimeBuildResult:
    scope_result = _classic_admission_scope(snapshot)
    if scope_result.scope is None:
        return ProviderRuntimeBuildResult(None, scope_result.reason)
    return _runtime_for_scope(snapshot, output_root=output_root, scope=scope_result.scope)


def _workspace_admission_runtime(
    snapshot: Mapping[str, str],
    *,
    output_root: str | Path,
) -> ProviderRuntimeBuildResult:
    if _has_any(snapshot, _ACCOUNT_IDENTITY_KEYS):
        return ProviderRuntimeBuildResult(None, "unsupported_dashscope_account_identity")

    workspace_id, reason = _workspace_id_from_snapshot(snapshot)
    if workspace_id is None:
        return ProviderRuntimeBuildResult(None, reason)
    region = (snapshot.get("DASHSCOPE_REGION") or "").strip()
    base_url = (snapshot.get("DASHSCOPE_BASE_URL") or "").strip().rstrip("/")
    if not region:
        return ProviderRuntimeBuildResult(None, "missing_dashscope_region")
    if not base_url:
        return ProviderRuntimeBuildResult(None, "missing_dashscope_workspace_base_url")
    if not dashscope_workspace_scope_matches(base_url, workspace_id, region):
        return ProviderRuntimeBuildResult(None, "unsupported_dashscope_workspace_scope")
    scope = ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope_workspace",
        base_url=base_url,
        region=region,
        workspace_id=workspace_id,
        account_identity=None,
    )
    try:
        _dashscope_scope_headers(scope)
    except ProviderBindingError:
        return ProviderRuntimeBuildResult(None, "unsupported_dashscope_workspace_scope")
    return _runtime_for_scope(snapshot, output_root=output_root, scope=scope)


def build_provider_runtime_for_scope(
    runtime_environment: RuntimeEnvironment,
    *,
    output_root: str | Path,
    scope: ProviderScope,
) -> ProviderRuntimeBuildResult:
    """Restore execution/query capability from a receipt's original scope."""
    snapshot = dict(runtime_environment.snapshot())
    if not isinstance(scope, ProviderScope):
        return ProviderRuntimeBuildResult(None, "unsupported_provider_scope")
    if scope.provider == "mulerouter":
        try:
            ProviderCredentialStore._validate_mulerouter_http_scope(scope)
        except ProviderBindingError:
            return ProviderRuntimeBuildResult(None, "unsupported_provider_scope")
        return _seedance_http_runtime(snapshot, scope)
    try:
        _dashscope_scope_headers(scope)
    except ProviderBindingError:
        return ProviderRuntimeBuildResult(None, "unsupported_provider_scope")
    return _runtime_for_scope(snapshot, output_root=output_root, scope=scope)


def build_seedance_http_scope_from_snapshot(snapshot: Mapping[str, str]) -> ProviderScopeBuildResult:
    """Read-only capability check: never inspect a CLI profile or bind a secret."""
    secret = snapshot.get(MULEROUTER_HTTP_SOURCE_KEY, "")
    if not secret:
        reason = (
            "seedance_cli_submission_unsafe"
            if _has_any(snapshot, ("MULERUN_TOKEN", "LUMENX_MULERUN_HOME"))
            else "seedance_http_key_required"
        )
        return ProviderScopeBuildResult(None, reason)
    if not isinstance(secret, str) or not secret.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in secret):
        return ProviderScopeBuildResult(None, "seedance_http_key_required")
    site = (snapshot.get("MULEROUTER_SITE") or "mulerouter").strip().lower()
    base_url = (snapshot.get("MULEROUTER_BASE_URL") or MULEROUTER_HTTP_BASE_URL).strip().rstrip("/")
    if site != "mulerouter" or base_url != MULEROUTER_HTTP_BASE_URL:
        return ProviderScopeBuildResult(None, "seedance_http_endpoint_unsupported")
    scope = ProviderScope("mulerouter", "mulerouter", "mulerouter_http", MULEROUTER_HTTP_BASE_URL)
    ProviderCredentialStore._validate_mulerouter_http_scope(scope)
    return ProviderScopeBuildResult(scope)


def _seedance_http_runtime(snapshot: Mapping[str, str], scope: ProviderScope) -> ProviderRuntimeBuildResult:
    from .seedance_http import SeedanceHttpProvider

    # Recovery keeps the recorded scope even when the current route has changed.
    # Credential fingerprints still require the original HTTP key at execution.
    secret = snapshot.get(MULEROUTER_HTTP_SOURCE_KEY, "")
    return ProviderRuntimeBuildResult(ProviderRecoveryRuntime(
        scope=scope, secret_resolver=lambda: secret, provider=SeedanceHttpProvider(),
    ))


def _runtime_for_scope(
    snapshot: Mapping[str, str],
    *,
    output_root: str | Path,
    scope: ProviderScope,
) -> ProviderRuntimeBuildResult:
    captured_env = _scope_environment(snapshot, scope)
    provider = WanxModel({"output_root": str(Path(output_root))}, env=dict(captured_env))
    return ProviderRuntimeBuildResult(
        ProviderRecoveryRuntime(
            scope=scope,
            secret_resolver=lambda: captured_env.get("DASHSCOPE_API_KEY", ""),
            provider=provider,
        )
    )


def _scope_environment(snapshot: Mapping[str, str], scope: ProviderScope) -> dict[str, str]:
    captured_env = dict(snapshot)
    for key in (*_WORKSPACE_KEYS, *_ACCOUNT_IDENTITY_KEYS, "DASHSCOPE_REGION"):
        captured_env.pop(key, None)
    captured_env["DASHSCOPE_BASE_URL"] = scope.base_url
    if scope.region is not None:
        captured_env["DASHSCOPE_REGION"] = scope.region
    if scope.workspace_id is not None:
        captured_env["DASHSCOPE_WORKSPACE_ID"] = scope.workspace_id
    return captured_env


def _has_any(values: Mapping[str, str], keys: tuple[str, ...]) -> bool:
    return any(bool((values.get(key) or "").strip()) for key in keys)


def _classic_dashscope_scope(base_url: str) -> ProviderScope | None:
    region = dashscope_classic_region(base_url)
    if region is None or region not in _CLASSIC_DASHSCOPE_REGIONS:
        return None
    try:
        candidate = ProviderScope(
            provider="dashscope",
            backend="dashscope",
            gateway="dashscope",
            base_url=base_url.rstrip("/"),
            region=region,
            workspace_id=None,
            account_identity=None,
        )
        _dashscope_scope_headers(candidate)
    except ProviderBindingError:
        return None
    return candidate


def _workspace_id_from_snapshot(snapshot: Mapping[str, str]) -> tuple[str | None, str]:
    primary = (snapshot.get("DASHSCOPE_WORKSPACE_ID") or "").strip()
    legacy = (snapshot.get("DASHSCOPE_WORKSPACE") or "").strip()
    if primary and legacy and primary != legacy:
        return None, "conflicting_dashscope_workspace"
    workspace_id = primary or legacy
    if not workspace_id:
        return None, "missing_dashscope_workspace"
    return workspace_id, ""
