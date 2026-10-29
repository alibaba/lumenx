"""Seedance's HTTP runtime never falls back to CLI or changes saved authority."""
from dataclasses import replace

import pytest

from src.models import provider_runtime
from src.models.provider_tasks import ProviderScope
from src.utils.runtime_environment import RuntimeEnvironment

SCOPE = ProviderScope('mulerouter', 'mulerouter', 'mulerouter_http', 'https://api.mulerouter.ai')


def test_seedance_http_runtime_freezes_snapshot_without_cli(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('CLI is outside admitted video authority')
    monkeypatch.setattr('subprocess.run', forbidden)
    for mode in ('t2v', 'i2v', 'r2v'):
        env = RuntimeEnvironment({'MULEROUTER_API_KEY': 'http-key', 'MULERUN_TOKEN': 'cli-token'})
        result = provider_runtime.build_provider_runtime_for_admission(
            env, output_root=tmp_path, provider_model_id=f'seedance-2.0-{mode}', gateway='mulerouter',
        )
        assert result.runtime is not None, result.reason
        assert result.runtime.scope == SCOPE
        assert result.runtime.resolve_secret() == 'http-key'
        assert callable(result.runtime.provider.submit)
        env.update({'MULEROUTER_API_KEY': 'replacement'}, persist=lambda *args: None)
        assert result.runtime.resolve_secret() == 'http-key'


@pytest.mark.parametrize('values,reason', [
    ({}, 'seedance_http_key_required'),
    ({'MULERUN_TOKEN': 'token'}, 'seedance_cli_submission_unsafe'),
    ({'LUMENX_MULERUN_HOME': '/not/read'}, 'seedance_cli_submission_unsafe'),
    ({'MULEROUTER_API_KEY': 'key', 'MULEROUTER_SITE': 'mulerun'}, 'seedance_http_endpoint_unsupported'),
    ({'MULEROUTER_API_KEY': 'key', 'MULEROUTER_BASE_URL': 'https://other.invalid'}, 'seedance_http_endpoint_unsupported'),
    ({'MULEROUTER_API_KEY': 'bad key'}, 'seedance_http_key_required'),
])
def test_seedance_scope_preflight_rejects_before_provider_or_io(values, reason, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Preflight must not perform file, CLI or HTTP I/O')
    monkeypatch.setattr('subprocess.run', forbidden)
    monkeypatch.setattr('requests.Session', forbidden)
    result = provider_runtime.build_seedance_http_scope_from_snapshot(values)
    assert result.scope is None and result.reason == reason


def test_seedance_recovery_uses_receipt_scope_not_current_route(tmp_path):
    env = RuntimeEnvironment({'MULEROUTER_API_KEY': 'original-key', 'MULEROUTER_SITE': 'mulerun',
                              'MULEROUTER_BASE_URL': 'https://current.invalid', 'MULERUN_TOKEN': 'token'})
    result = provider_runtime.build_provider_runtime_for_scope(env, output_root=tmp_path, scope=SCOPE)
    assert result.runtime is not None, result.reason
    assert result.runtime.scope == SCOPE and result.runtime.resolve_secret() == 'original-key'


@pytest.mark.parametrize('scope', [replace(SCOPE, backend='mulerun_cli', gateway='mulerun_token'),
    replace(SCOPE, gateway='mulerouter'), replace(SCOPE, base_url='https://api.mulerouter.ai//'),
    replace(SCOPE, region='cn-beijing'), replace(SCOPE, account_identity='other')])
def test_seedance_unsupported_saved_scope_does_not_fallback(tmp_path, scope):
    result = provider_runtime.build_provider_runtime_for_scope(
        RuntimeEnvironment({'MULEROUTER_API_KEY': 'key', 'DASHSCOPE_API_KEY': 'other'}),
        output_root=tmp_path, scope=scope,
    )
    assert result.runtime is None
