from types import MappingProxyType

import pytest

from src.utils.endpoints import PROVIDER_DEFAULTS, get_provider_base_url
from src.utils.provider_registry import resolve_provider_backend


@pytest.mark.parametrize("provider", ["DASHSCOPE", "KLING", "VIDU", "MULEROUTER"])
def test_empty_runtime_mapping_does_not_use_ambient_endpoint(monkeypatch, provider):
    monkeypatch.setenv(f"{provider}_BASE_URL", "https://ambient.example.invalid/")
    assert get_provider_base_url(provider, env={}) == PROVIDER_DEFAULTS[provider]


def test_runtime_endpoint_mapping_is_independent_and_unmodified(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://ambient.example.invalid")
    first = MappingProxyType({"DASHSCOPE_BASE_URL": "https://a.example.invalid///"})
    second = MappingProxyType({"DASHSCOPE_BASE_URL": "https://b.example.invalid/"})
    assert get_provider_base_url("dashscope", env=first) == "https://a.example.invalid"
    assert get_provider_base_url("dashscope", env=second) == "https://b.example.invalid"
    assert get_provider_base_url("dashscope", env=first) == "https://a.example.invalid"
    assert first["DASHSCOPE_BASE_URL"] == "https://a.example.invalid///"


def test_empty_explicit_override_keeps_callers_default_without_ambient_fallback(monkeypatch):
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://ambient.example.invalid")
    assert get_provider_base_url("custom", "https://fallback.example.invalid/", env={
        "CUSTOM_BASE_URL": "",
    }) == "https://fallback.example.invalid"
    assert get_provider_base_url("custom", env={}) == ""


def test_standalone_endpoint_call_preserves_process_environment_compatibility(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://standalone.example.invalid/")
    assert get_provider_base_url("dashscope") == "https://standalone.example.invalid"
    assert get_provider_base_url("dashscope", env=None) == "https://standalone.example.invalid"


def test_explicit_empty_routing_mapping_also_ignores_ambient_mode(monkeypatch):
    monkeypatch.setenv("KLING_PROVIDER_MODE", "vendor")
    assert resolve_provider_backend("kling-v1", env={}) == "dashscope"
    assert resolve_provider_backend("kling-v1", env={"KLING_PROVIDER_MODE": "vendor"}) == "vendor"
    assert resolve_provider_backend("kling-v1") == "vendor"
