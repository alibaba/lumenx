"""Pure construction of Studio provider recovery runtime from app env."""

from pathlib import Path

import pytest

from src.apps.comic_gen.provider_recovery import StudioProviderRecoveryError
from src.apps.comic_gen.provider_runtime import build_studio_provider_recovery_runtime
from src.models.provider_runtime import (
    build_provider_runtime_for_admission,
    build_provider_runtime_for_scope,
)
from src.models.provider_tasks import ProviderScope
from src.utils.runtime_environment import RuntimeEnvironment


def _runtime(values):
    return RuntimeEnvironment(values)


def test_builds_dashscope_recovery_runtime_from_captured_app_snapshot(tmp_path):
    environment = _runtime({"DASHSCOPE_API_KEY": "first-key"})

    result = build_studio_provider_recovery_runtime(environment, output_root=tmp_path)

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope.provider == "dashscope"
    assert result.runtime.scope.backend == "dashscope"
    assert result.runtime.scope.gateway == "dashscope"
    assert result.runtime.scope.base_url == "https://dashscope.aliyuncs.com"
    assert result.runtime.scope.region == "cn-beijing"
    assert result.runtime.scope.workspace_id is None
    assert result.runtime.scope.account_identity is None
    assert result.runtime.resolve_secret() == "first-key"
    assert result.runtime.provider.api_key == "first-key"
    assert Path(result.runtime.provider.output_root) == tmp_path


def test_constructed_runtime_keeps_original_secret_and_provider_env_after_update(tmp_path):
    environment = _runtime({
        "DASHSCOPE_API_KEY": "first-key",
        "DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com/",
    })
    result = build_studio_provider_recovery_runtime(environment, output_root=tmp_path)

    environment.update(
        {"DASHSCOPE_API_KEY": "second-key", "DASHSCOPE_BASE_URL": "https://dashscope-us.aliyuncs.com"},
        persist=lambda updates, removals: None,
    )

    assert result.runtime is not None
    assert result.runtime.scope.base_url == "https://dashscope-intl.aliyuncs.com"
    assert result.runtime.scope.region == "ap-southeast-1"
    assert result.runtime.resolve_secret() == "first-key"
    assert result.runtime.provider.api_key == "first-key"
    assert result.runtime.provider._env["DASHSCOPE_BASE_URL"] == "https://dashscope-intl.aliyuncs.com"


def test_workspace_config_does_not_fallback_to_classic_compatibility_builder(tmp_path):
    result = build_studio_provider_recovery_runtime(
        _runtime({
            "DASHSCOPE_API_KEY": "key",
            "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
            "DASHSCOPE_REGION": "cn-beijing",
            "DASHSCOPE_BASE_URL": "https://wan-workspace.cn-beijing.maas.aliyuncs.com",
        }),
        output_root=tmp_path,
    )

    assert result.runtime is None
    assert result.reason == "unsupported_dashscope_workspace"


@pytest.mark.parametrize(
    "base_url,region",
    [
        ("https://dashscope.aliyuncs.com", "cn-beijing"),
        ("https://dashscope-intl.aliyuncs.com", "ap-southeast-1"),
        ("https://dashscope-us.aliyuncs.com", "us-east-1"),
        ("https://cn-hongkong.dashscope.aliyuncs.com", "cn-hongkong"),
    ],
)
def test_official_classic_dashscope_endpoints_map_to_wanx_validated_scope(tmp_path, base_url, region):
    result = build_studio_provider_recovery_runtime(
        _runtime({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_BASE_URL": base_url}),
        output_root=tmp_path,
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope.base_url == base_url
    assert result.runtime.scope.region == region


def test_missing_key_keeps_scope_but_secret_resolver_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-key")

    result = build_studio_provider_recovery_runtime(_runtime({}), output_root=tmp_path)

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope.base_url == "https://dashscope.aliyuncs.com"
    with pytest.raises(StudioProviderRecoveryError) as exc:
        result.runtime.resolve_secret()
    assert exc.value.reason == "provider_secret_unavailable"
    with pytest.raises(RuntimeError, match="configured"):
        result.runtime.provider.api_key


@pytest.mark.parametrize(
    "values,reason",
    [
        ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_BASE_URL": "https://example.invalid"}, "unsupported_dashscope_endpoint"),
        ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE": "workspace-a"}, "unsupported_dashscope_workspace"),
        ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_ACCOUNT_IDENTITY": "account-a"}, "unsupported_dashscope_account_identity"),
    ],
)
def test_unsupported_identity_boundaries_return_no_runtime(tmp_path, values, reason):
    result = build_studio_provider_recovery_runtime(_runtime(values), output_root=tmp_path)

    assert result.runtime is None
    assert result.reason == reason


@pytest.mark.parametrize("base_url", [
    " https://dashscope.aliyuncs.com", "https://dashscope.aliyuncs.com\n",
    "http://dashscope.aliyuncs.com", "https://dashscope.aliyuncs.com/api/v1",
])
def test_malformed_or_nonclassic_endpoint_has_a_typed_unsupported_result(tmp_path, base_url):
    result = build_studio_provider_recovery_runtime(
        _runtime({"DASHSCOPE_BASE_URL": base_url}), output_root=tmp_path,
    )
    assert result.runtime is None
    assert result.reason == "unsupported_dashscope_endpoint"


@pytest.mark.parametrize("values", [
    {
        "DASHSCOPE_API_KEY": "key",
        "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
        "DASHSCOPE_REGION": "cn-beijing",
        "DASHSCOPE_BASE_URL": "https://wan-workspace.cn-beijing.maas.aliyuncs.com",
    },
    {
        "DASHSCOPE_API_KEY": "key",
        "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
        "DASHSCOPE_REGION": "ap-southeast-1",
        "DASHSCOPE_BASE_URL": "https://wan-workspace.ap-southeast-1.maas.aliyuncs.com",
    },
])
def test_workspace_urls_are_not_classic_recovery_fallbacks(tmp_path, values):
    result = build_studio_provider_recovery_runtime(_runtime(values), output_root=tmp_path)

    assert result.runtime is None
    assert result.reason == "unsupported_dashscope_workspace"


def test_workspace_admission_requires_exact_official_scope_without_classic_fallback(tmp_path):
    result = build_provider_runtime_for_admission(
        _runtime({
            "DASHSCOPE_API_KEY": "workspace-key",
            "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
            "DASHSCOPE_REGION": "cn-beijing",
            "DASHSCOPE_BASE_URL": "https://wan-workspace.cn-beijing.maas.aliyuncs.com",
        }),
        output_root=tmp_path,
        provider_model_id="wan3.0-video",
        gateway="dashscope_workspace",
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope_workspace",
        base_url="https://wan-workspace.cn-beijing.maas.aliyuncs.com",
        region="cn-beijing",
        workspace_id="wan-workspace",
    )
    assert result.runtime.resolve_secret() == "workspace-key"
    assert result.runtime.provider._env["DASHSCOPE_BASE_URL"] == "https://wan-workspace.cn-beijing.maas.aliyuncs.com"


@pytest.mark.parametrize("provider_model_id", ["wan2.6-i2v", "wan2.6-i2v-flash", "wan2.7-i2v", "wan2.7-t2v"])
def test_wan2_classic_admission_uses_workspace_region_official_classic_host(
    tmp_path, provider_model_id,
):
    result = build_provider_runtime_for_admission(
        _runtime({
            "DASHSCOPE_API_KEY": "shared-key",
            "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
            "DASHSCOPE_REGION": "ap-southeast-1",
            "DASHSCOPE_BASE_URL": "https://wan-workspace.ap-southeast-1.maas.aliyuncs.com",
        }),
        output_root=tmp_path,
        provider_model_id=provider_model_id,
        gateway="dashscope",
    )

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope",
        base_url="https://dashscope-intl.aliyuncs.com",
        region="ap-southeast-1",
    )
    assert result.runtime.resolve_secret() == "shared-key"
    assert result.runtime.provider._env["DASHSCOPE_BASE_URL"] == "https://dashscope-intl.aliyuncs.com"
    assert result.runtime.provider._env["DASHSCOPE_REGION"] == "ap-southeast-1"
    assert "DASHSCOPE_WORKSPACE_ID" not in result.runtime.provider._env


def test_wan2_classic_and_wan3_workspace_admission_coexist_in_same_snapshot(tmp_path):
    environment = _runtime({
        "DASHSCOPE_API_KEY": "shared-key",
        "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
        "DASHSCOPE_REGION": "cn-beijing",
        "DASHSCOPE_BASE_URL": "https://wan-workspace.cn-beijing.maas.aliyuncs.com",
    })

    wan2 = build_provider_runtime_for_admission(
        environment,
        output_root=tmp_path,
        provider_model_id="wan2.7-i2v",
        gateway="dashscope",
    )
    wan3 = build_provider_runtime_for_admission(
        environment,
        output_root=tmp_path,
        provider_model_id="wan3.0-video",
        gateway="dashscope_workspace",
    )

    assert wan2.reason is None and wan2.runtime is not None
    assert wan3.reason is None and wan3.runtime is not None
    assert wan2.runtime.scope.gateway == "dashscope"
    assert wan2.runtime.scope.base_url == "https://dashscope.aliyuncs.com"
    assert wan2.runtime.scope.region == "cn-beijing"
    assert wan3.runtime.scope.gateway == "dashscope_workspace"
    assert wan3.runtime.scope.base_url == "https://wan-workspace.cn-beijing.maas.aliyuncs.com"
    assert wan3.runtime.scope.region == "cn-beijing"
    assert wan2.runtime.resolve_secret() == wan3.runtime.resolve_secret() == "shared-key"


@pytest.mark.parametrize(
    "values,reason",
    [
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
             "DASHSCOPE_REGION": "eu-central-1",
             "DASHSCOPE_BASE_URL": "https://wan-workspace.eu-central-1.maas.aliyuncs.com"},
            "unsupported_dashscope_classic_region",
        ),
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
             "DASHSCOPE_REGION": "cn-beijing",
             "DASHSCOPE_BASE_URL": "https://other.cn-beijing.maas.aliyuncs.com"},
            "unsupported_dashscope_workspace_scope",
        ),
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE_ID": "wan-workspace",
             "DASHSCOPE_REGION": "cn-beijing"},
            "missing_dashscope_workspace_base_url",
        ),
    ],
)
def test_wan2_classic_admission_rejects_unverified_workspace_without_default_fallback(
    tmp_path, values, reason,
):
    result = build_provider_runtime_for_admission(
        _runtime(values),
        output_root=tmp_path,
        provider_model_id="wan2.7-i2v",
        gateway="dashscope",
    )

    assert result.runtime is None
    assert result.reason == reason


def test_wan3_admission_rejects_classic_gateway(tmp_path):
    result = build_provider_runtime_for_admission(
        _runtime({"DASHSCOPE_API_KEY": "key"}),
        output_root=tmp_path,
        provider_model_id="wan3.0-video",
        gateway="dashscope",
    )

    assert result.runtime is None
    assert result.reason == "unsupported_dashscope_gateway"


@pytest.mark.parametrize(
    "values,reason",
    [
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_REGION": "cn-beijing",
             "DASHSCOPE_BASE_URL": "https://wan-workspace.cn-beijing.maas.aliyuncs.com"},
            "missing_dashscope_workspace",
        ),
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE_ID": "trial", "DASHSCOPE_REGION": "cn-beijing",
             "DASHSCOPE_BASE_URL": "https://trial.cn-beijing.maas.aliyuncs.com"},
            "unsupported_dashscope_workspace_scope",
        ),
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE_ID": "wan-workspace", "DASHSCOPE_REGION": "cn-beijing",
             "DASHSCOPE_BASE_URL": "https://other.cn-beijing.maas.aliyuncs.com"},
            "unsupported_dashscope_workspace_scope",
        ),
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE": "one", "DASHSCOPE_WORKSPACE_ID": "two",
             "DASHSCOPE_REGION": "cn-beijing", "DASHSCOPE_BASE_URL": "https://one.cn-beijing.maas.aliyuncs.com"},
            "conflicting_dashscope_workspace",
        ),
        (
            {"DASHSCOPE_API_KEY": "key", "DASHSCOPE_WORKSPACE_ID": "wan-workspace", "DASHSCOPE_REGION": "moon-1",
             "DASHSCOPE_BASE_URL": "https://wan-workspace.moon-1.maas.aliyuncs.com"},
            "unsupported_dashscope_workspace_scope",
        ),
    ],
)
def test_workspace_admission_rejects_incomplete_or_invalid_scope(tmp_path, values, reason):
    result = build_provider_runtime_for_admission(
        _runtime(values),
        output_root=tmp_path,
        provider_model_id="wan3.0-video",
        gateway="dashscope_workspace",
    )

    assert result.runtime is None
    assert result.reason == reason


def test_restore_runtime_uses_original_scope_independent_of_current_defaults(tmp_path):
    original = ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope_workspace",
        base_url="https://wan-workspace.cn-beijing.maas.aliyuncs.com",
        region="cn-beijing",
        workspace_id="wan-workspace",
    )
    environment = _runtime({
        "DASHSCOPE_API_KEY": "rotated-key",
        "DASHSCOPE_BASE_URL": "https://dashscope-us.aliyuncs.com",
        "DASHSCOPE_REGION": "us-east-1",
        "DASHSCOPE_WORKSPACE_ID": "other-workspace",
        "DASHSCOPE_WORKSPACE": "legacy-workspace",
        "DASHSCOPE_ACCOUNT_IDENTITY": "wrong-account",
        "DASHSCOPE_ACCOUNT_ID": "wrong-account-id",
    })

    result = build_provider_runtime_for_scope(environment, output_root=tmp_path, scope=original)

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == original
    assert result.runtime.resolve_secret() == "rotated-key"
    assert result.runtime.provider._env["DASHSCOPE_BASE_URL"] == original.base_url
    assert result.runtime.provider._env["DASHSCOPE_REGION"] == "cn-beijing"
    assert result.runtime.provider._env["DASHSCOPE_WORKSPACE_ID"] == "wan-workspace"
    assert "DASHSCOPE_WORKSPACE" not in result.runtime.provider._env
    assert "DASHSCOPE_ACCOUNT_IDENTITY" not in result.runtime.provider._env
    assert "DASHSCOPE_ACCOUNT_ID" not in result.runtime.provider._env


def test_restore_classic_scope_ignores_current_workspace_settings(tmp_path):
    original = ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope",
        base_url="https://dashscope-intl.aliyuncs.com",
        region="ap-southeast-1",
    )
    environment = _runtime({
        "DASHSCOPE_API_KEY": "classic-key",
        "DASHSCOPE_BASE_URL": "https://wrong.cn-beijing.maas.aliyuncs.com",
        "DASHSCOPE_REGION": "cn-beijing",
        "DASHSCOPE_WORKSPACE_ID": "wrong",
        "DASHSCOPE_WORKSPACE": "wrong-legacy",
        "DASHSCOPE_ACCOUNT_ID": "wrong-account",
    })

    result = build_provider_runtime_for_scope(environment, output_root=tmp_path, scope=original)

    assert result.reason is None
    assert result.runtime is not None
    assert result.runtime.scope == original
    assert result.runtime.resolve_secret() == "classic-key"
    assert result.runtime.provider._env["DASHSCOPE_BASE_URL"] == original.base_url
    assert result.runtime.provider._env["DASHSCOPE_REGION"] == "ap-southeast-1"
    assert "DASHSCOPE_WORKSPACE_ID" not in result.runtime.provider._env
    assert "DASHSCOPE_WORKSPACE" not in result.runtime.provider._env
    assert "DASHSCOPE_ACCOUNT_ID" not in result.runtime.provider._env


def test_restore_runtime_rejects_invalid_original_scope_before_provider_use(tmp_path):
    bad_scope = ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope_workspace",
        base_url="https://attacker.invalid",
        region="cn-beijing",
        workspace_id="wan-workspace",
    )

    result = build_provider_runtime_for_scope(
        _runtime({"DASHSCOPE_API_KEY": "key"}),
        output_root=tmp_path,
        scope=bad_scope,
    )

    assert result.runtime is None
    assert result.reason == "unsupported_provider_scope"
