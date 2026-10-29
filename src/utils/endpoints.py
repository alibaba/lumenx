import os
from typing import Mapping
from urllib.parse import urlsplit

# Provider endpoint registry: {provider_key: default_base_url}
PROVIDER_DEFAULTS = {
    "DASHSCOPE": "https://dashscope.aliyuncs.com",
    "KLING": "https://api-beijing.klingai.com/v1",
    "VIDU": "https://api.vidu.cn/ent/v2",
    "MULEROUTER": "https://api.mulerouter.ai",
}

DASHSCOPE_CLASSIC_ENDPOINTS = {
    "cn-beijing": "https://dashscope.aliyuncs.com",
    "ap-southeast-1": "https://dashscope-intl.aliyuncs.com",
    "us-east-1": "https://dashscope-us.aliyuncs.com",
    "cn-hongkong": "https://cn-hongkong.dashscope.aliyuncs.com",
}

DASHSCOPE_WORKSPACE_REGIONS = frozenset({
    *DASHSCOPE_CLASSIC_ENDPOINTS,
    "eu-central-1",
    "ap-northeast-1",
})


def get_provider_base_url(
    provider: str, default: str | None = None, *, env: Mapping[str, str] | None = None,
) -> str:
    """Resolve a provider endpoint from explicit runtime or standalone config.

    Args:
        provider: Provider key, e.g. "KLING", "DASHSCOPE"
        default: Fallback URL if env var is not set. If None, looks up PROVIDER_DEFAULTS.
        env: App-owned configuration. An empty mapping must not read ambient
            process overrides; None preserves standalone environment behavior.
    """
    env_key = f"{provider.upper()}_BASE_URL"
    fallback = default or PROVIDER_DEFAULTS.get(provider.upper(), "")
    environment = os.environ if env is None else env
    return (environment.get(env_key) or fallback).rstrip("/")


def dashscope_classic_region(base_url: str) -> str | None:
    normalized = _normalized_https_origin(base_url)
    for region, endpoint in DASHSCOPE_CLASSIC_ENDPOINTS.items():
        if normalized == endpoint:
            return region
    return None


def dashscope_workspace_origin(workspace_id: str, region: str) -> str | None:
    if region not in DASHSCOPE_WORKSPACE_REGIONS or not _safe_workspace_label(workspace_id):
        return None
    return f"https://{workspace_id}.{region}.maas.aliyuncs.com"


def dashscope_workspace_scope_matches(base_url: str, workspace_id: str, region: str) -> bool:
    expected = dashscope_workspace_origin(workspace_id, region)
    return expected is not None and _normalized_https_origin(base_url) == expected


def _normalized_https_origin(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.rstrip("/")
    try:
        parsed = urlsplit(stripped)
    except ValueError:
        return None
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.path or parsed.query or parsed.fragment
        or parsed.port is not None
    ):
        return None
    hostname = parsed.hostname
    if hostname != parsed.netloc:
        return None
    return f"https://{hostname}"


def _safe_workspace_label(value: str) -> bool:
    if value == "trial" or not isinstance(value, str):
        return False
    if not 1 <= len(value) <= 63:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
    return (
        value[0] in allowed - {"-"}
        and value[-1] in allowed - {"-"}
        and all(char in allowed for char in value)
    )
