"""Offline browser harness retains the real Studio/Core transaction path."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import socket
import time

import pytest
from fastapi.testclient import TestClient

SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_studio_image_browser_smoke.py'


def load():
    spec = importlib.util.spec_from_file_location('studio_image_smoke', SCRIPT)
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
    mounted = smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=False)
    evidence_path = tmp_path / 'evidence' / 'audit.json'
    evidence = smoke.open_evidence(evidence_path, resume=False)
    return smoke, root, mounted, evidence, evidence_path


def post(http, project, asset, key, batch=2):
    return http.post(f'/projects/{project}/asset_image_requests', json={
        'client_request_id': key, 'asset_id': asset, 'batch_size': batch,
        'apply_style': False, 'seed': 0, 'watermark': False,
    })


def wait_paused(http):
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        state = http.get('/__smoke/state').json()
        if state['paused']:
            return state
        time.sleep(0.01)
    pytest.fail('second provider submission did not pause')


@pytest.mark.parametrize('route', ['local', 'series', 'global'])
def test_three_owners_use_real_core_http_and_preserve_retained_selection(harness, route):
    smoke, root, static, evidence, _ = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        fixture = http.get('/__smoke/state').json()['fixtures'][route]
        source = root / 'output' / 'uploads' / 'retained-choice.png'
        retained = source.read_bytes()
        response = post(http, fixture['project_id'], fixture['asset_id'], 'browser-double')
        assert response.status_code == 200, response.text
        record = http.get(f"/projects/{fixture['project_id']}/asset_image_requests/browser-double").json()['request']
        assert record['owner_kind'] == fixture['owner_kind'] and record['owner_id'] == fixture['owner_id']
        assert [m['status'] for m in record['members']] == ['completed', 'completed']
        for member in record['members']:
            assert (root / 'output' / member['output_ref']).read_bytes().startswith(b'\x89PNG')
        owner = (app.state.pipeline.scripts[fixture['owner_id']] if route == 'local' else
                 app.state.pipeline.series_store[fixture['owner_id']] if route == 'series' else app.state.pipeline.library_store)
        target = next(c for c in owner.characters if c.id == fixture['asset_id'])
        assert target.reference_sheet.selected_image_id == 'choice'
        assert [v.id for v in target.reference_sheet.image_variants[:2]] == ['choice', 'alternate']
        assert target.reference_sheet.image_variants[0].is_favorited
        assert source.read_bytes() == retained and len(target.reference_sheet.image_variants) == 4
        assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 2, 'fetch': 2}
        submissions = [e for e in evidence.payload['fake_provider']['events'] if e['kind'] == 'submit']
        assert all(e['payload']['parameters'] == {'n': 1, 'size': '1024*1024', 'seed': 0, 'watermark': False} for e in submissions)
        assert evidence.payload['blocked_sockets'] == []
        bad = post(http, fixture['project_id'], fixture['asset_id'], 'invalid', batch=0)
        assert bad.status_code == 422
        assert any(row['status'] == 422 for row in evidence.payload['api_audit'])


def test_paused_second_submit_is_unknown_after_interrupt_and_restart_without_extra_post(harness):
    smoke, root, static, evidence, evidence_path = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        f = http.get('/__smoke/state').json()['fixtures']['local']
        assert http.post('/__smoke/control', json={'action': 'arm', 'mode': 'pause_second_submit'}).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(post, http, f['project_id'], f['asset_id'], 'interrupted')
            try:
                state = wait_paused(http)
                assert state['counts']['submit'] == 2
                rows = app.state.pipeline.scripts[f['project_id']].asset_image_requests[0].members
                assert [r.execution.phase for r in rows] == ['completed', 'submitting']
            finally:
                http.post('/__smoke/control', json={'action': 'interrupt_submit'})
            assert future.result(timeout=5).status_code == 200
    before = json.loads(evidence_path.read_text())
    restored_evidence = smoke.open_evidence(evidence_path, resume=True)
    smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=True)
    restored = smoke.create_smoke_app(root, static, restored_evidence, resume=True)
    with TestClient(restored) as http:
        response = http.get(f"/projects/{f['project_id']}/asset_image_requests/interrupted")
        assert response.status_code == 200
        assert [m['status'] for m in response.json()['request']['members']] == ['completed', 'needs_confirmation']
        assert restored_evidence.payload['fake_provider']['counts'] == before['fake_provider']['counts']
        assert len(restored_evidence.payload['api_audit']) > len(before['api_audit'])
        assert restored.state.pipeline.asset_generation_tasks == {}


def test_real_completion_failure_leaves_proof_and_file_for_production_startup_repair(harness):
    smoke, root, static, evidence, evidence_path = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app, raise_server_exceptions=False) as http:
        f = http.get('/__smoke/state').json()['fixtures']['local']
        http.post('/__smoke/control', json={'action': 'arm', 'mode': 'fail_completion'})
        response = post(http, f['project_id'], f['asset_id'], 'proof-window', batch=1)
        assert response.status_code == 200
        saved = json.loads((root / 'output' / 'projects.json').read_text())[f['project_id']]['asset_image_requests'][0]
        member = saved['members'][0]['execution']
        assert member['schema_version'] == 3 and member['phase'] == 'observed'
        assert member['prepared_artifact'] is not None
        assert member['handle']['task_id'] == 'studio-image-1'
        assert member['observation']['task_id'] == member['handle']['task_id']
        assert member['observation']['status'] == 'succeeded'
        assert member['observation']['source_handle'] == member['handle']
        output = root / 'output' / member['output_ref']
        assert output.exists()
        produced = output.read_bytes()
        retained = (root / 'output' / 'uploads' / 'retained-choice.png').read_bytes()
        assert app.state.pipeline._storage_failed is not None
    counts = dict(evidence.payload['fake_provider']['counts'])
    next_evidence = smoke.open_evidence(evidence_path, resume=True)
    restored = smoke.create_smoke_app(root, static, next_evidence, resume=True)
    with TestClient(restored) as http:
        response = http.get(f"/projects/{f['project_id']}/asset_image_requests/proof-window")
        assert response.status_code == 200 and response.json()['request']['members'][0]['status'] == 'completed'
        assert next_evidence.payload['fake_provider']['counts'] == counts
        assert not any(e.get('action') == 'repair' for e in next_evidence.payload.get('controls', []))
        target = restored.state.pipeline.scripts[f['project_id']].characters[0]
        assert target.reference_sheet.selected_image_id == 'choice' and len(target.reference_sheet.image_variants) == 3
        assert target.reference_sheet.image_prompt == 'Preserve this original prompt'
        assert output.read_bytes() == produced
        assert (root / 'output' / 'uploads' / 'retained-choice.png').read_bytes() == retained


def test_transport_and_socket_guard_are_strict_and_restored(harness):
    import requests
    from src.models import wan27_image_http
    from src.apps.comic_gen import asset_image_execution
    smoke, root, static, evidence, _ = harness
    original_interval = wan27_image_http._POLL_INTERVAL_SECONDS
    original_clock, original_sleep = asset_image_execution.time, time.sleep
    original = requests.Session, socket.socket.connect, socket.getaddrinfo
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app):
        clock = asset_image_execution.time
        assert isinstance(clock, smoke.PollingClock) and clock is not original_clock
        elapsed, wall = clock.monotonic(), clock.time()
        clock.sleep(0.25)
        clock.sleep(9.75)
        assert clock.monotonic() - elapsed == pytest.approx(10)
        assert clock.time() - wall == pytest.approx(10)
        assert time.sleep is original_sleep and smoke.time is time
        with pytest.raises(RuntimeError, match='blocks'):
            socket.getaddrinfo('external.invalid', 443)
        with socket.socket() as sock:
            with pytest.raises(RuntimeError, match='blocks'):
                sock.connect(('192.0.2.3', 443))
        with requests.Session() as session:
            assert session.trust_env is False
            assert wan27_image_http._POLL_INTERVAL_SECONDS == 0
            with pytest.raises(RuntimeError, match='Unexpected'):
                session.post('https://unhandled.invalid/anything')
    assert (requests.Session, socket.socket.connect, socket.getaddrinfo) == original
    assert len(evidence.payload['blocked_sockets']) == 2
    assert wan27_image_http._POLL_INTERVAL_SECONDS == original_interval
    assert asset_image_execution.time is original_clock and time.sleep is original_sleep


@pytest.mark.parametrize('damage', ['unowned', 'static', 'commit', 'evidence'])
def test_resume_never_overwrites_unowned_or_changed_fixture(harness, damage):
    smoke, root, static, evidence, evidence_path = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)):
        pass
    if damage == 'unowned':
        (root / 'studio-image-smoke.json').unlink()
    elif damage == 'static':
        (static / 'index.html').write_text('changed')
    elif damage == 'evidence':
        evidence_path.write_text('{}')
    if damage == 'evidence':
        with pytest.raises((ValueError, SystemExit)):
            smoke.open_evidence(evidence_path, resume=True)
    else:
        with pytest.raises((ValueError, SystemExit)):
            smoke.prepare_root(root, static, frontend_commit='different' if damage == 'commit' else 'fixture-front', backend_commit='fixture-back', resume=True)


def test_debug_controls_are_absent_from_production_app(harness):
    from src.apps.comic_gen.api import create_app
    _, root, _, _, _ = harness
    production = create_app(data_dir=root / 'production', load_config=False, access_mode='local')
    assert not any(getattr(r, 'path', '').startswith('/__smoke') for r in production.routes)


@pytest.mark.parametrize('damage', ['extra', 'traversal', 'symlink'])
def test_resume_rejects_changed_static_file_set_or_unsafe_manifest(harness, damage, tmp_path):
    smoke, root, static, evidence, _ = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)):
        pass
    marker_path = root / smoke.MARKER
    marker = json.loads(marker_path.read_text())
    if damage == 'extra':
        (static / 'extra.js').write_text('changed')
    elif damage == 'traversal':
        outside = root / 'outside.js'
        outside.write_text('outside')
        marker['static']['files'][0] = {'path': '../outside.js', 'sha256': smoke.base.sha256_file(outside)}
        marker_path.write_text(json.dumps(marker))
    else:
        outside = tmp_path / 'outside'
        outside.mkdir()
        (outside / 'file.js').write_text('outside')
        (static / 'linked').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=True)


def test_resume_requires_matching_cumulative_evidence(harness):
    smoke, root, static, evidence, evidence_path = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)):
        pass
    fresh = smoke.open_evidence(evidence_path.with_name('new.json'), resume=False)
    with pytest.raises(ValueError, match='evidence'):
        smoke.create_smoke_app(root, static, fresh, resume=True)
    payload = json.loads(evidence_path.read_text())
    payload['fake_provider']['counts']['submit'] = 4
    evidence_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='evidence'):
        smoke.open_evidence(evidence_path, resume=True)


def test_cli_shutdown_retains_fake_transport_until_process_exit(harness, monkeypatch):
    import requests
    from src.models import wan27_image_http
    from src.apps.comic_gen import asset_image_execution
    smoke, _, _, evidence, _ = harness
    # Test-only outer patch restores this process after checking the CLI mode.
    with monkeypatch.context() as restore:
        for target, field in ((requests, 'Session'), (socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                              (socket, 'getaddrinfo'), (wan27_image_http, '_POLL_INTERVAL_SECONDS'),
                              (asset_image_execution, 'time')):
            restore.setattr(target, field, getattr(target, field))
        restore.setattr(requests, '_studio_image_smoke_active', False, raising=False)
        original_clock = asset_image_execution.time
        with smoke.offline_transport(evidence, smoke.Controls(evidence, 1), restore=False):
            retained_clock = asset_image_execution.time
        assert retained_clock is not original_clock
        assert asset_image_execution.time is retained_clock
        with requests.Session() as session:
            with pytest.raises(RuntimeError, match='Unexpected'):
                session.get('https://unhandled.invalid/after-shutdown')
        with pytest.raises(RuntimeError, match='blocks'):
            socket.getaddrinfo('external.invalid', 443)


@pytest.mark.parametrize('kind', ['headshot', 'full_body', 'gpt_t2i'])
def test_gpt_polling_clock_is_local_and_restored_after_transport_error(harness, monkeypatch, kind):
    import requests
    from src.apps.comic_gen import asset_image_execution
    from src.models import mulerouter

    _, root, _, evidence, _ = harness
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    spec = importlib.util.spec_from_file_location(
        f'{kind}_clock_smoke', SCRIPT.with_name(f'lumenx_studio_{kind}_browser_smoke.py'))
    assert spec and spec.loader
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    controls = smoke.Controls(evidence, 'local', 1)
    original = requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess
    original_clock, original_sleep = asset_image_execution.time, time.sleep
    with pytest.raises(RuntimeError, match='synthetic transport unwind'):
        with smoke.offline_transport(evidence, controls, root):
            clock = asset_image_execution.time
            assert isinstance(clock, smoke.images.PollingClock)
            assert clock is mulerouter.time and clock is not original_clock
            elapsed, wall = clock.monotonic(), clock.time()
            clock.sleep(0.25)
            clock.sleep(19.75)
            assert clock.monotonic() - elapsed == pytest.approx(20)
            assert clock.time() - wall == pytest.approx(20)
            assert time.sleep is original_sleep and smoke.images.time is time
            raise RuntimeError('synthetic transport unwind')
    assert (requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess) == original
    assert asset_image_execution.time is original_clock and time.sleep is original_sleep
    assert controls.closing is True
    assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
