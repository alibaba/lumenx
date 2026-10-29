"""Async asset admission distinguishes invalid input, missing roots and storage."""

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.utils.json_storage import StorageConflictError, StorageError
from tests import test_asset_generation_tasks as task_tests


runtime = task_tests.runtime


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
@pytest.mark.parametrize('error,status', [
    (module.CommandValidationError('Invalid asset inputs'), 422),
    (module.ProjectContextError('Asset not found', not_found=True), 404),
    (module.ProjectContextError('Invalid target context'), 422),
    (StorageConflictError('Asset already queued'), 409),
    (StorageError('Storage unavailable'), 503),
])
def test_asset_task_admission_http_error_contract(runtime, monkeypatch, source, error, status):
    item, state = runtime
    task_tests.configure_source(item, source)
    command = 'generate_series_asset' if source == 'standalone_series' else 'create_asset_generation_task'
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(item, command, fail)
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    path = '/series/series/assets/generate' if source == 'standalone_series' else '/projects/p/assets/generate'
    with TestClient(app) as client:
        response = client.post(path, json={'asset_id': 's', 'asset_type': 'scene'})
    assert response.status_code == status, response.text
    assert len(calls) == 1
    assert not state.calls and item.asset_generation_tasks == {}


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
@pytest.mark.parametrize('invalid', [{'batch_size': 0}, {'asset_type': 'unknown'}])
def test_real_invalid_asset_admission_is_422_without_task_or_provider(runtime, source, invalid):
    item, state = runtime
    task_tests.configure_source(item, source)
    before = task_tests.saved_target(item, source)
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    path = '/series/series/assets/generate' if source == 'standalone_series' else '/projects/p/assets/generate'
    with TestClient(app) as client:
        response = client.post(path, json={'asset_id': 's', 'asset_type': 'scene'} | invalid)
    assert response.status_code == 422, response.text
    assert task_tests.saved_target(item, source) == before
    assert item.asset_generation_tasks == {} and not state.calls


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
@pytest.mark.parametrize('invalid', [
    {'batch_size': True},
    {'batch_size': '2'},
    {'batch_size': 1.5},
    {'apply_style': 'false'},
    {'apply_style': 1},
])
def test_http_asset_admission_rejects_coerced_scalars_before_task_or_provider(runtime, source, invalid):
    item, state = runtime
    task_tests.configure_source(item, source)
    before = task_tests.saved_target(item, source)
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    path = '/series/series/assets/generate' if source == 'standalone_series' else '/projects/p/assets/generate'
    with TestClient(app) as client:
        response = client.post(path, json={'asset_id': 's', 'asset_type': 'scene'} | invalid)
    assert response.status_code == 422, response.text
    assert task_tests.saved_target(item, source) == before
    assert item.asset_generation_tasks == {} and not state.calls
