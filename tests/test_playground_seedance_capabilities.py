"""Read-only capability discovery and narrowly typed admission errors."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.playground.seedance_video_admission import SeedanceCapabilityError
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_recovery import ProviderRecoveryError
from src.utils.runtime_environment import RuntimeEnvironment


@pytest.mark.parametrize('snapshot,available,reason', [
    ({}, False, 'seedance_http_key_required'),
    ({'MULEROUTER_API_KEY': 'fake-http-key'}, True, None),
    ({'MULEROUTER_API_KEY': 'fake-http-key', 'MULEROUTER_BASE_URL': 'https://wrong.invalid'}, False,
     'seedance_http_endpoint_unsupported'),
    ({'MULERUN_TOKEN': 'fake-cli-token'}, False, 'seedance_cli_submission_unsafe'),
    ({'LUMENX_MULERUN_HOME': '/must-not-be-opened'}, False, 'seedance_cli_submission_unsafe'),
])
def test_capability_get_uses_one_snapshot_and_no_runtime_or_binding(tmp_path, monkeypatch, snapshot, available, reason):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        environment = RuntimeEnvironment(snapshot)
        snap = Mock(wraps=environment.snapshot)
        monkeypatch.setattr(environment, 'snapshot', snap)
        app.state.playground_service.runtime_environment = environment
        fail = Mock(side_effect=AssertionError('capability discovery cannot run or bind'))
        monkeypatch.setattr(app.state.playground_service, '_resolve_video_runtime_for_admission', fail)
        monkeypatch.setattr(ProviderCredentialStore, 'bind', fail)
        response = client.get('/playground/capabilities')
        assert response.status_code == 200, response.text
        assert response.json() == {'schema_version': 1, 'seedance': {
            'available': available, 'reason': reason, 'channel': 'mulerouter_http' if available else None,
            'input_kinds': ['image'],
        }}
        snap.assert_called_once()
        fail.assert_not_called()
        assert client.get('/playground/history').json() == []
        assert not (tmp_path / 'provider_credentials.json').exists()


@pytest.mark.parametrize('reason', ['seedance_http_key_required', 'seedance_http_endpoint_unsupported',
                                    'seedance_cli_submission_unsafe'])
def test_only_specialized_gate_error_is_safe_not_admitted_response(tmp_path, monkeypatch, reason):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.playground_service, 'admit_generation', Mock(side_effect=SeedanceCapabilityError(reason)))
        response = client.post('/playground/generate', json={
            'client_request_id': 'gate', 'mode': 't2v', 'model_id': 'seedance-2.0-t2v', 'prompt': 'test',
        })
        assert response.status_code == 503
        assert response.json() == {'detail': {'code': 'playground_generation_not_admitted', 'reason': reason}}
        assert client.get('/playground/requests/gate').status_code == 404


def test_general_503_even_with_known_reason_is_not_classified_as_gate(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.playground_service, 'admit_generation',
                            Mock(side_effect=ProviderRecoveryError('seedance_http_key_required')))
        response = client.post('/playground/generate', json={
            'client_request_id': 'general', 'mode': 't2v', 'model_id': 'seedance-2.0-t2v', 'prompt': 'test',
        })
        assert response.status_code == 503
        assert response.json()['detail']['code'] == 'playground_generation_unavailable'


def test_real_seedance_gate_does_not_save_or_schedule(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        app.state.playground_service.runtime_environment = RuntimeEnvironment({'MULERUN_TOKEN': 'fake-token'})
        fail = Mock(side_effect=AssertionError('gate cannot submit or bind'))
        monkeypatch.setattr(ProviderCredentialStore, 'bind', fail)
        monkeypatch.setattr(app.state.playground_service, 'process_generation', fail)
        response = client.post('/playground/generate', json={
            'client_request_id': 'cli-gate', 'mode': 'i2v', 'model_id': 'seedance-2.0-i2v', 'prompt': 'test',
            'input_media': ['missing.png'],
        })
        assert response.status_code == 503, response.text
        assert response.json()['detail'] == {
            'code': 'playground_generation_not_admitted', 'reason': 'seedance_cli_submission_unsafe',
        }
        assert client.get('/playground/requests/cli-gate').status_code == 404
        assert client.get('/playground/history').json() == []
        fail.assert_not_called()


def test_missing_runtime_environment_ignores_legacy_env_and_process_environment(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        service = app.state.playground_service
        service.runtime_environment = None
        service._env = {'MULEROUTER_API_KEY': 'legacy-key'}
        monkeypatch.setenv('MULEROUTER_API_KEY', 'process-key')
        response = client.get('/playground/capabilities')
        assert response.status_code == 200
        assert response.json()['seedance'] == {
            'available': False, 'reason': 'seedance_http_key_required',
            'channel': None, 'input_kinds': ['image'],
        }
        rejected = client.post('/playground/generate', json={
            'client_request_id': 'no-runtime', 'mode': 't2v', 'model_id': 'seedance-2.0-t2v', 'prompt': 'test',
        })
        assert rejected.status_code == 503, rejected.text
        assert rejected.json()['detail'] == {
            'code': 'playground_generation_not_admitted', 'reason': 'seedance_http_key_required',
        }
        assert client.get('/playground/requests/no-runtime').status_code == 404
