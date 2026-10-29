"""Thin Playground browser fixture exercises production image execution over fake HTTP."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import socket
import time

import pytest
from fastapi.testclient import TestClient

SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_playground_image_browser_smoke.py'
VERSION = 'provider-wan27-t2i-bound-http-v1'


def load():
    spec = importlib.util.spec_from_file_location('playground_image_smoke', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(tmp_path):
    smoke = load()
    static = tmp_path / 'build'
    static.mkdir()
    (static / 'index.html').write_text('<html>offline fixture</html>')
    root = tmp_path / 'business'
    mounted = smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back')
    evidence = smoke.open_evidence(tmp_path / 'evidence' / 'audit.json')
    return smoke, root, mounted, evidence


def payload(key='browser-image', **changes):
    return {'client_request_id': key, 'mode': 't2i', 'model_id': 'wan2.7-image',
            'prompt': 'Synthetic glass moon', 'parameters': {'size': '1280*1280', 'seed': 0, 'watermark': False},
            'batch_size': 2, **changes}


def read(http, key):
    response = http.get('/playground/requests/' + key)
    assert response.status_code == 200, response.text
    return response.json()['generation']


def assert_outputs(http, generation, root):
    from src.utils.media_probe import probe_image_file
    assert generation['status'] == 'completed', generation
    assert len(generation['outputs']) == generation['batch_size']
    for output in generation['outputs']:
        response = http.get('/files/' + output['media_path'])
        assert response.status_code == 200
        path = root / 'output' / output['media_path']
        assert response.content == path.read_bytes()
        image = probe_image_file(path)
        assert image.codec_name == 'png' and (image.width, image.height) == (320, 240)


@pytest.mark.parametrize('model', ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'])
def test_real_http_batch_preserves_zero_false_history_and_get_recovery(harness, model):
    from src.models import provider_media_execution
    smoke, root, static, evidence = harness
    original_factory = provider_media_execution.WanxImageModel
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        initial = http.get('/playground/history').json()
        body = payload(model_id=model)
        response = http.post('/playground/generate', json=body)
        assert response.status_code == 200, response.text
        # Treat the POST acknowledgement as lost; recover only by the original id.
        generation = read(http, body['client_request_id'])
        assert_outputs(http, generation, root)
        for attempt in generation['attempts']:
            execution = attempt['provider_execution']
            assert execution['intent']['intent_contract_version'] == VERSION
            assert execution['intent']['params'] == body['parameters']
            assert execution['intent']['sources']['params.watermark'] == 'request'
            assert execution['artifact'] == execution['prepared_artifact']
        counts = {'submit': 2, 'query': 2, 'fetch': 2}
        assert evidence.payload['fake_provider']['counts'] == counts
        for _ in range(2):
            assert read(http, body['client_request_id']) == generation
        replay = http.post('/playground/generate', json=body)
        assert replay.status_code == 200 and replay.json()['replayed']
        assert evidence.payload['fake_provider']['counts'] == counts
        submitted = [row['payload'] for row in evidence.payload['fake_provider']['events'] if row['kind'] == 'submit']
        assert all(row['model'] == model.removeprefix('wan/').removesuffix('#image') for row in submitted)
        assert all(row['parameters'] == {**body['parameters'], 'n': 1} for row in submitted)
        assert provider_media_execution.WanxImageModel is original_factory
        state = http.get('/__smoke/state').json()
        assert all(row['unchanged'] for row in state['fixtures'].values())
        history = http.get('/playground/history').json()
        assert [row for row in history if row['id'].startswith('legacy-')] == initial
        assert evidence.payload['blocked_sockets'] == []
    assert all(row['unchanged'] for row in evidence.payload['retained_fixtures'].values())


def test_default_source_and_legacy_new_generation_gate(harness):
    smoke, root, static, evidence = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        histories = {row['id']: row for row in http.get('/playground/history').json()}
        assert len(histories) == 2
        # Real DetailPanel exposes Retry only for failed generations. Keep the
        # old PNG and batch=1 so this is one explicit new request, not recovery.
        from src.utils.media_probe import probe_image_file
        for identity, row in histories.items():
            assert row['status'] == 'failed'
            assert row['error'].startswith('Synthetic legacy failure')
            assert row['batch_size'] == 1 and len(row['outputs']) == 1
            assert row['attempts'] == []
            assert row['client_request_id'] is None
            assert row['request_contract_version'] is None and row['request_fingerprint'] is None
            assert http.get('/playground/requests/' + identity).status_code == 404
            output = row['outputs'][0]
            assert output['attempt_id'] is None
            path = root / 'output' / output['media_path']
            assert http.get('/files/' + output['media_path']).content == path.read_bytes()
            image = probe_image_file(path)
            assert image.codec_name == 'png' and (image.width, image.height) == (320, 240)
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        unsupported = histories['legacy-wan27-unsupported']
        retry = {key: unsupported[key] for key in ('mode', 'model_id', 'prompt', 'negative_prompt', 'parameters', 'batch_size')}
        rejected = http.post('/playground/generate', json={**retry, 'client_request_id': 'old-invalid-new-request'})
        assert rejected.status_code == 422, rejected.text
        assert http.get('/playground/requests/old-invalid-new-request').status_code == 404
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        compatible = histories['legacy-wan27-compatible']
        retry = {key: compatible[key] for key in ('mode', 'model_id', 'prompt', 'parameters', 'batch_size')}
        response = http.post('/playground/generate', json={**retry, 'client_request_id': 'old-compatible-new-request'})
        assert response.status_code == 200, response.text
        retried = read(http, 'old-compatible-new-request')
        assert retried['batch_size'] == 1 and len(retried['attempts']) == 1
        assert_outputs(http, retried, root)
        assert evidence.payload['fake_provider']['counts'] == {'submit': 1, 'query': 1, 'fetch': 1}
        response = http.post('/playground/generate', json=payload('default-values', parameters={}, batch_size=1))
        assert response.status_code == 200, response.text
        intent = read(http, 'default-values')['attempts'][0]['provider_execution']['intent']
        assert intent['params'] == {'size': '1280*1280', 'watermark': False}
        assert intent['sources']['params.watermark'] == 'preset' and intent['sources']['params.size'] == 'catalog'
        assert 'params.watermark' not in intent['request_fields']
        assert all(row['unchanged'] for row in http.get('/__smoke/state').json()['fixtures'].values())


@pytest.mark.parametrize('changes', [{'negative_prompt': ''}, {'parameters': {'seed': -1}},
                                     {'parameters': {'prompt_extend': False}}, {'parameters': {'watermark': 0}}])
def test_invalid_strict_draft_has_no_receipt_or_provider_call(harness, changes):
    smoke, root, static, evidence = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        initial = http.get('/playground/history').json()
        response = http.post('/playground/generate', json=payload(**changes))
        assert response.status_code == 422, response.text
        assert http.get('/playground/history').json() == initial
        assert http.get('/playground/requests/browser-image').status_code == 404
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        assert any(row['status'] == 422 for row in evidence.payload['api_audit'])


def test_strict_http_batch_is_not_consumed_by_running_video_dispatcher(harness):
    smoke, root, static, evidence = harness
    app = smoke.create_smoke_app(root, static, evidence, pause_seconds=10)
    with TestClient(app) as http:
        controls = app.state.smoke_controls
        with controls.lock:
            controls.pause_number = 1
            controls.release.clear()
        dispatcher = app.state.playground_video_recovery_dispatcher
        assert dispatcher.is_running
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(http.post, '/playground/generate', json=payload('strict-concurrent'))
            try:
                until = time.monotonic() + 5
                while not controls.paused and time.monotonic() < until:
                    time.sleep(0.01)
                assert controls.paused, 'real provider HTTP submission never paused'
                before = read(http, 'strict-concurrent')
                assert [a['provider_execution']['phase'] for a in before['attempts']] == ['submitting', 'accepted']
                for _ in range(3):
                    assert dispatcher.run_once() is None
                assert read(http, 'strict-concurrent') == before
            finally:
                controls.release.set()
            response = pending.result(timeout=10)
        assert response.status_code == 200, response.text
        assert_outputs(http, read(http, 'strict-concurrent'), root)
        assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 2, 'fetch': 2}
    assert not dispatcher.is_running


def test_owned_fresh_roots_evidence_and_read_only_controls(harness):
    smoke, root, static, evidence = harness
    with pytest.raises(SystemExit, match='empty'):
        smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back')
    with pytest.raises(ValueError, match='evidence'):
        smoke.open_evidence(evidence.path)
    with pytest.raises(ValueError, match='separate'):
        smoke.create_smoke_app(root, static, smoke.open_evidence(root / 'audit.json'))
    # The deliberately rejected internal evidence does not become business data.
    (root / 'audit.json').unlink()
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        assert http.post('/__smoke/control', json={'action': 'arm'}).status_code == 404
        assert http.post('/__smoke/state', json={}).status_code == 405
        assert app.state.pipeline.scripts == {} and app.state.pipeline.series_store == {}
        assert not (root / 'output/uploads/retained-choice.png').exists()
        assert http.get('/__smoke/state').json()['kind'] == smoke.KIND
    before = (root / 'output/playground_history.json').read_bytes()
    with pytest.raises(ValueError, match='fresh'):
        smoke.create_smoke_app(root, static, evidence)
    assert (root / 'output/playground_history.json').read_bytes() == before
    with pytest.raises(SystemExit):
        smoke.parse_args(['--data-root', str(root), '--evidence', str(evidence.path), '--static-root', str(static),
                          '--frontend-freeze-commit', 'fixture-front', '--backend-source-commit', 'fixture-back', '--resume'])
    marker = json.loads((root / smoke.MARKER).read_text())
    assert marker['static']['files'][0]['sha256'] == smoke.base.sha256_file(static / 'index.html')
    assert smoke.source_hashes()[SCRIPT.relative_to(smoke.base.REPO_ROOT).as_posix()] == smoke.base.sha256_file(SCRIPT)


def test_cli_lifespan_keeps_guard_after_shutdown(harness, monkeypatch):
    import requests
    from src.models import wan27_image_http
    smoke, root, static, evidence = harness
    with monkeypatch.context() as restore:
        for target, field in ((requests, 'Session'), (socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                              (socket, 'getaddrinfo'), (wan27_image_http, '_POLL_INTERVAL_SECONDS')):
            restore.setattr(target, field, getattr(target, field))
        restore.setattr(requests, '_studio_image_smoke_active', False, raising=False)
        app = smoke.create_smoke_app(root, static, evidence, restore_transport=False)
        with TestClient(app):
            pass
        assert app.state.smoke_controls.interrupted and app.state.smoke_controls.release.is_set()
        with requests.Session() as session:
            with pytest.raises(RuntimeError, match='Unexpected'):
                session.get('https://unhandled.invalid/after-shutdown')
        with pytest.raises(RuntimeError, match='blocks'):
            socket.getaddrinfo('external.invalid', 443)
