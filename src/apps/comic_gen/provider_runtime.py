"""Studio compatibility aliases for neutral provider recovery runtime construction."""

from __future__ import annotations

from ...models.provider_runtime import (
    ProviderRecoveryRuntime as StudioProviderRecoveryRuntime,
    ProviderRuntimeBuildResult as StudioProviderRuntimeBuildResult,
    build_provider_recovery_runtime as build_studio_provider_recovery_runtime,
)


__all__ = [
    "StudioProviderRecoveryRuntime",
    "StudioProviderRuntimeBuildResult",
    "build_studio_provider_recovery_runtime",
]
