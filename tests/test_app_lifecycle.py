"""Real lifespan/HTTP regressions; transports stay in-process and provider-free."""

import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_playground_qwen20_t2i_contract as qwen_contract

transport = qwen_contract.transport


class StubPipeline:
    def __init__(self, *, data_dir, **kwargs):
        self.data_dir = Path(data_dir)
        self.scripts = {}
        self.closed = False

    def create_library_asset(self, *args, **kwargs):
        return None

    def read_committed_projects(self):
        # This lifecycle stub has no project-writing operations.
        return []

    def close(self):
        self.closed = True


@pytest.fixture
def api():
    return importlib.import_module('src.apps.comic_gen.api')


def test_fresh_api_import_has_no_runtime_side_effects(tmp_path):
    # A fresh guarded process makes this independent of pytest import order.
    probe = tmp_path / 'import_probe.py'
    probe.write_text('''import os, sys, threading, logging
from pathlib import Path
import dotenv
cwd = Path.cwd()
streams = (sys.stdout, sys.stderr)
handlers = list(logging.getLogger().handlers)
def forbidden(*args, **kwargs):
    raise AssertionError('runtime initialization during import')
os.makedirs = forbidden
Path.mkdir = forbidden
threading.Thread.start = forbidden
dotenv.load_dotenv = forbidden
import src.apps.comic_gen.api as api
assert Path.cwd() == cwd
assert (sys.stdout, sys.stderr) == streams
assert logging.getLogger().handlers == handlers
assert not hasattr(api.app.state, 'pipeline')
''')
    helper = Path(os.environ['LUMENX_REPO_ROOT']) / 'scripts/test_support/isolated_child.py'
    result = subprocess.run([sys.executable, '-S', str(helper), str(probe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_app_factory_defers_config_and_runtime_until_lifespan(api, monkeypatch, tmp_path):
    events = []
    class Pipeline(StubPipeline):
        def __init__(self, **kwargs):
            events.append('pipeline')
            super().__init__(**kwargs)
    monkeypatch.setattr('src.apps.comic_gen.pipeline.ComicGenPipeline', Pipeline)
    monkeypatch.setattr(api, 'load_user_config', lambda **kwargs: (events.append(('config', kwargs['user_data_dir'])) or {}))
    root = tmp_path / 'media'
    app = api.create_app(data_dir=root, user_data_dir=tmp_path / 'settings')
    assert events == []
    assert not root.exists()
    assert not hasattr(app.state, 'pipeline')
    with TestClient(app) as client:
        assert events == [('config', tmp_path / 'settings'), 'pipeline']
        response = client.get('/health')
        assert response.status_code == 200
        assert response.json()['service'] == 'lumenx-core'
        assert response.json()['protocol_version'] == 1
        runtime = app.state.pipeline
        assert (root / 'uploads').is_dir()
        assert not runtime.closed
    assert runtime.closed
    assert not hasattr(app.state, 'pipeline')
    assert not hasattr(app.state, 'runtime_environment')


def test_two_apps_keep_http_state_and_static_media_separate(api, tmp_path):
    a, b = tmp_path / 'media-a', tmp_path / 'media-b'
    app_a = api.create_app(data_dir=a, load_config=False, pipeline_instance=StubPipeline(data_dir=a))
    app_b = api.create_app(data_dir=b, load_config=False, pipeline_instance=StubPipeline(data_dir=b))
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        (a / 'uploads/same.png').write_text('first')
        (b / 'uploads/same.png').write_text('second')
        assert client_a.get('/files/uploads/same.png').text == 'first'
        assert client_b.get('/files/uploads/same.png').text == 'second'
        created = client_a.post('/playground/templates', json={'name': 'Only A', 'prompt': 'a'})
        assert created.status_code == 200, created.text
        assert [item['name'] for item in client_a.get('/playground/templates').json()] == ['Only A']
        assert client_b.get('/playground/templates').json() == []
        assert (a / 'playground_templates.json').is_file()
        assert not (b / 'playground_templates.json').exists()
        assert client_a.get('/projects/').json() == []
        assert client_b.get('/projects/').json() == []
    assert not hasattr(app_a.state, 'pipeline')
    assert not hasattr(app_b.state, 'pipeline')


def test_lifespan_closes_only_its_log_handler(api, tmp_path):
    before = list(logging.getLogger().handlers)
    app = api.create_app(data_dir=tmp_path / 'media', user_data_dir=tmp_path, load_config=False,
                         setup_logs=True, pipeline_instance=StubPipeline(data_dir=tmp_path / 'media'))
    @app.get('/test-log')
    def test_log():
        logging.getLogger('lumenx-test').info('owned lifecycle log')
        return {'ok': True}

    with TestClient(app) as client:
        owned = list(app.state.owned_log_handlers)
        assert client.get('/test-log').status_code == 200
        assert owned and all(handler in logging.getLogger().handlers for handler in owned)
    assert logging.getLogger().handlers == before
    assert all(handler.stream is None for handler in owned)
    assert 'owned lifecycle log' in (tmp_path / 'logs/app.log').read_text()


def test_failed_startup_releases_owned_logging(api, monkeypatch, tmp_path):
    before = list(logging.getLogger().handlers)
    def fail(**kwargs):
        raise ValueError('invalid config')
    monkeypatch.setattr(api, 'load_user_config', fail)
    app = api.create_app(data_dir=tmp_path / 'media', user_data_dir=tmp_path, setup_logs=True)
    with pytest.raises(ValueError, match='invalid config'):
        with TestClient(app):
            pytest.fail('startup should fail')
    assert logging.getLogger().handlers == before
    assert not hasattr(app.state, 'pipeline')


def test_background_video_helper_keeps_explicit_owner_on_failure(api):
    calls = []
    class Pipeline:
        def process_video_task(self, script_id, task_id):
            calls.append(('process', script_id, task_id))
            raise RuntimeError('provider failed')
        def mark_video_task_failed(self, script_id, task_id, reason):
            calls.append(('failed', script_id, task_id, reason))
    api.process_video_task(active_pipeline=Pipeline(), script_id='script-a', task_id='task-a')
    assert calls[0] == ('process', 'script-a', 'task-a')
    assert calls[1][0:3] == ('failed', 'script-a', 'task-a')
    assert 'provider failed' in calls[1][3]


def test_real_pipeline_series_crud_isolated_across_apps_and_reload(api, tmp_path):
    root_a, root_b = tmp_path / 'alpha', tmp_path / 'beta'
    app_a = api.create_app(data_dir=root_a, load_config=False)
    app_b = api.create_app(data_dir=root_b, load_config=False)
    with TestClient(app_a) as a, TestClient(app_b) as b:
        created = a.post('/series', json={'title': 'Series A'})
        assert created.status_code == 200, created.text
        series_id = created.json()['id']
        assert b.get('/series').json() == []
        assert b.get(f'/series/{series_id}').status_code == 404
        assert a.put(f'/series/{series_id}', json={
            'title': 'Updated A', 'expected_revision': created.json()['revision'],
        }).status_code == 200
        assert a.get(f'/series/{series_id}').json()['title'] == 'Updated A'
        assert a.get('/library/assets').status_code == 200
        assert b.get('/library/assets').status_code == 200
        assert a.get('/projects/missing').status_code == 404
        assert (root_a / 'series.json').is_file()
        assert not (root_b / 'series.json').exists()
    with TestClient(api.create_app(data_dir=root_a, load_config=False)) as reloaded:
        assert reloaded.get(f'/series/{series_id}').json()['title'] == 'Updated A'
        assert reloaded.delete(f'/series/{series_id}').status_code == 200
        assert reloaded.get('/series').json() == []


def test_custom_desktop_config_and_logs_use_selected_root(api, monkeypatch, tmp_path):
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    user_root = tmp_path / 'custom-user'
    app = api.create_app(data_dir=user_root / 'media', user_data_dir=user_root,
                         load_config=False, setup_logs=True,
                         pipeline_instance=StubPipeline(data_dir=user_root / 'media'))
    @app.get('/test-log')
    def test_log():
        logging.getLogger('lumenx-test').warning('selected-root-log')
        return {'ok': True}

    with TestClient(app) as client:
        info = client.get('/config/info').json()
        assert info['config_path'] == str(user_root / 'config.json')
        result = client.post('/config/env', json={'DASHSCOPE_API_KEY': 'test-only-key'})
        assert result.status_code == 200, result.text
        assert (user_root / 'config.json').is_file()
        assert client.get('/test-log').status_code == 200
        tail = client.get('/diagnose/log_tail').json()
        assert tail['path'] == str(user_root / 'logs/app.log')
        assert any('selected-root-log' in line for line in tail['lines'])
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    reloaded = api.create_app(data_dir=user_root / 'media', user_data_dir=user_root,
                              pipeline_instance=StubPipeline(data_dir=user_root / 'media'))
    with TestClient(reloaded):
        assert reloaded.state.runtime_environment.snapshot()['DASHSCOPE_API_KEY'] == 'test-only-key'
        assert os.environ['DASHSCOPE_API_KEY'] == ''


def test_playground_upload_generation_and_library_return_served_relative_refs(api, tmp_path, transport):
    qwen_contract.queue_results(transport)
    app = api.create_app(data_dir=tmp_path / 'arbitrary-media-root', load_config=False)
    with TestClient(app) as client:
        app.state.playground_service.runtime_environment = RuntimeEnvironment({
            'DASHSCOPE_API_KEY': qwen_contract.bound_http.SECRET,
        })
        uploaded = client.post('/playground/upload', files={'file': ('ref.png', b'upload', 'image/png')})
        assert uploaded.status_code == 200, uploaded.text
        ref = uploaded.json()['path']
        assert ref.startswith('playground/uploads/')
        assert client.get('/files/' + ref).content == b'upload'
        result = client.post('/playground/generate', json={
            'client_request_id': 'lifecycle-request',
            'mode': 't2i',
            'model_id': 'qwen-image-2.0-pro',
            'prompt': 'test',
            'parameters': {'size': '1280*1280', 'seed': 0, 'prompt_extend': False, 'watermark': False},
        })
        assert result.status_code == 200, result.text
        generation = client.get('/playground/history/' + result.json()['generation']['id']).json()
        assert generation['status'] == 'completed', generation
        output = generation['outputs'][0]
        assert output['media_path'].startswith('playground/images/')
        assert client.get('/files/' + output['media_path']).content.startswith(b'\x89PNG')
        saved = client.post('/playground/history/' + generation['id'] + '/outputs/' + output['id'] + '/save-to-library', json={'category': 'props'})
        assert saved.status_code == 200, saved.text
        asset = client.get('/library/assets').json()['props'][0]
        assert asset['image_url'].startswith('assets/props/')
        assert client.get('/files/' + asset['image_url']).content.startswith(b'\x89PNG')
        assert [request.method for request, _ in transport['calls']] == ['POST', 'GET']


def test_playground_legacy_refs_resolve_within_selected_app(tmp_path, monkeypatch):
    from src.apps.playground.service import PlaygroundService
    from src.apps.playground.storage import PlaygroundStorage
    from src.apps.playground.models import PlaygroundGeneration, PlaygroundMode
    first, second = tmp_path / 'first', tmp_path / 'second'
    for root, content in [(first, b'first'), (second, b'second')]:
        path = root / 'playground/images/same.png'
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    monkeypatch.chdir(first.parent)
    for root, expected in [(first, b'first'), (second, b'second')]:
        service = PlaygroundService(PlaygroundStorage(root), data_dir=root)
        for ref in ['output/playground/images/same.png', 'outputs/playground/images/same.png', 'playground/images/same.png']:
            local, remote = service._resolve_first_input_media(PlaygroundGeneration(
                schema_version=1, id='resolve-only', client_request_id='resolve-only-request',
                request_contract_version='request-json-v1', request_fingerprint='0' * 64,
                mode=PlaygroundMode.I2V, model_id='fake', prompt='resolve', negative_prompt=None,
                input_media=[ref], batch_size=1, status='pending', error=None,
                created_at='2026-10-13T00:00:00Z',
            ))
            assert remote is None
            assert local is not None
            assert Path(local).read_bytes() == expected

def test_lifespan_uses_shared_playground_video_executor_and_joins_before_storage_close(api, monkeypatch, tmp_path):
    app = api.create_app(data_dir=tmp_path / 'media', load_config=False, pipeline_instance=StubPipeline(data_dir=tmp_path / 'media'))
    events = []
    with TestClient(app):
        assert app.state.playground_service.video_execution_service() is app.state.playground_video_execution
        dispatcher = app.state.playground_video_recovery_dispatcher
        storage = app.state.playground_storage

        def request_stop():
            events.append('request_stop')

        def join(timeout=None):
            events.append(('join', timeout, storage._closed))
            return True

        original_close = storage.close

        def close_storage():
            events.append('storage_close')
            original_close()

        monkeypatch.setattr(dispatcher, 'request_stop', request_stop)
        monkeypatch.setattr(dispatcher, 'join', join)
        monkeypatch.setattr(storage, 'close', close_storage)

    assert events[:2] == ['request_stop', ('join', 5.0, False)]
    assert 'storage_close' in events
    assert events.index(('join', 5.0, False)) < events.index('storage_close')

def test_lifespan_waits_for_playground_video_worker_before_storage_close(api, monkeypatch, tmp_path):
    app = api.create_app(data_dir=tmp_path / 'media', load_config=False, pipeline_instance=StubPipeline(data_dir=tmp_path / 'media'))
    events = []
    with TestClient(app):
        dispatcher = app.state.playground_video_recovery_dispatcher
        storage = app.state.playground_storage

        def join(timeout=None):
            events.append(('join', timeout, storage._closed))
            return timeout is None

        original_close = storage.close

        def close_storage():
            events.append('storage_close')
            original_close()

        monkeypatch.setattr(dispatcher, 'join', join)
        monkeypatch.setattr(storage, 'close', close_storage)

    assert events[:2] == [('join', 5.0, False), ('join', None, False)]
    assert events.index(('join', None, False)) < events.index('storage_close')
