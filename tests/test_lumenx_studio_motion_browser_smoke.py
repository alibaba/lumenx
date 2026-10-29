"""Finite harness self-checks using real HTTP admission, storage and recovery.

Media comes only from the two separately encoded/probed checked-in clips.
These API checks are not browser playback or real-provider acceptance.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import time

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'docs/agents/tools/lumenx_studio_motion_browser_smoke.py'
MEDIA = Path(__file__).with_name('fixtures') / 'studio-motion-smoke'


def load():
    spec = importlib.util.spec_from_file_location('studio_motion_smoke_selfcheck', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(tmp_path):
    smoke = load()
    static = tmp_path / 'build'
    static.mkdir()
    (static / 'index.html').write_text('<html>API fixture, not browser acceptance</html>')
    root = tmp_path / 'business'
    manifest = json.loads((MEDIA / 'media-manifest.json').read_text())
    assert set(manifest) == {'old', 'new'}
    sources = {role: MEDIA / f'{role}.mp4' for role in manifest}
    for role, source in sources.items():
        assert source.is_file() and not source.is_symlink()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == manifest[role]['sha256']
        assert source.stat().st_size == manifest[role]['size_bytes']
        assert manifest[role]['ffprobe']['format']['filename'] == source.name
    mounted = smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back',
                                 resume=False, media_sources=sources, media_manifest=manifest)
    evidence_path = tmp_path / 'evidence' / 'audit.json'
    evidence = smoke.open_evidence(evidence_path, resume=False)
    return smoke, root, mounted, evidence, evidence_path


@contextmanager
def session(harness, *, resume=False, evidence=None):
    smoke, root, static, first_evidence, evidence_path = harness
    current = evidence or (smoke.open_evidence(evidence_path, resume=True) if resume else first_evidence)
    with smoke.isolated_environment(root):
        if resume:
            smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=True)
        app = smoke.create_smoke_app(root, static, current, resume=resume, hold_seconds=5)
        with TestClient(app) as http:
            yield app, http, current


def state(http):
    response = http.get('/__smoke/state')
    assert response.status_code == 200, response.text
    return response.json()


def control(http, **value):
    response = http.post('/__smoke/control', json=value)
    assert response.status_code == 200, response.text
    return response.json()


def original_path(fixture, key):
    return f"/projects/{fixture['project_id']}/motion_ref_requests/{key}"


def original(http, fixture, key):
    response = http.get(original_path(fixture, key))
    assert response.status_code == 200, response.text
    return response.json()


def post(http, fixture, key):
    path = f"/projects/{fixture['project_id']}/motion_ref_requests"
    read = http.get(path, params={'asset_id': fixture['asset_id'], 'asset_type': 'full_body', 'batch_size': 1, 'has_audio': False})
    assert read.status_code == 200, read.text
    eligibility = read.json()
    assert eligibility['eligible'] is True and eligibility['reason'] is None
    assert eligibility['owner'] == {'kind': fixture['owner_kind'], 'id': fixture['owner_id']}
    assert eligibility['selected_image_id'] == 'choice' and eligibility['source_kind'] == 'local_path'
    body = {'client_request_id': key, 'asset_id': fixture['asset_id'], 'asset_type': 'full_body',
            'prompt': 'One slow turn, without audio.', 'duration': 5, 'batch_size': 1,
            'expected_target': eligibility['expected_target']}
    return http.post(path, json=body), body


def expected(read):
    return {'expected_attempt_id': read['request']['attempt_id'],
            'expected_fingerprint': read['request']['execution_fingerprint']}


def await_value(read, predicate, *, timeout=8):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = read()
        if predicate(last):
            return last
        time.sleep(0.01)
    pytest.fail(f'Bounded harness observation did not settle: {last!r}')


def selected(http, fixture):
    response = http.get(f"/projects/{fixture['project_id']}")
    assert response.status_code == 200, response.text
    matches = [row for row in response.json()['characters'] if row['id'] == fixture['asset_id']]
    assert len(matches) == 1
    return matches[0]['full_body']


def saved_records(root):
    return {name: (root / 'output' / name).read_bytes()
            for name in ('projects.json', 'series.json', 'library_assets.json')}


def completion(http, fixture, key):
    return await_value(lambda: original(http, fixture, key),
                       lambda value: value['task']['provider_execution']['phase'] == 'completed')


def damage_command(fixture, read, mode='missing'):
    record = read['task']['provider_execution']
    return {'action': 'damage_artifact', 'project_id': fixture['project_id'],
            'client_request_id': read['request']['client_request_id'], 'task_id': read['task_id'],
            'attempt_id': record['attempt_id'], 'fingerprint': record['fingerprint'], 'mode': mode}


@pytest.mark.parametrize('route', ['local', 'series', 'global'])
def test_three_owners_execute_real_core_and_preserve_authored_selection(harness, route):
    smoke, root, _, _, _ = harness
    with session(harness) as (app, http, evidence):
        fixture = state(http)['fixtures'][route]
        project = http.get(f"/projects/{fixture['project_id']}")
        assert project.status_code == 200, project.text
        assert project.json()['workflow_mode'] == 'i2v_legacy'
        prior = selected(http, fixture)
        response, body = post(http, fixture, 'owner-check')
        assert response.status_code == 200, response.text
        read = completion(http, fixture, 'owner-check')
        request, task = read['request'], read['task']
        record = task['provider_execution']
        assert request['entrypoint'] == 'studio.motion_reference.v1' and request['schema_version'] == 3
        assert (request['owner_kind'], request['owner_id']) == (fixture['owner_kind'], fixture['owner_id'])
        assert request['client_request_id'] == body['client_request_id'] and task['duration'] == 5
        assert task['motion_reference']['source_image_id'] == 'choice'
        assert record['prepared_artifact'] == record['artifact'] and read['reason'] is None
        proof = record['artifact']
        data = (root / 'output' / proof['relative_path']).read_bytes()
        assert data == (MEDIA / 'new.mp4').read_bytes()
        assert hashlib.sha256(data).hexdigest() == proof['sha256'] and len(data) == proof['size_bytes']
        assert record['media'][0]['sha256'] == smoke.base.sha256_file(root / 'output/uploads/motion-choice.png')
        after = selected(http, fixture)
        assert after['selected_video_id'] == prior['selected_video_id'] == 'old-selected'
        assert after['video_variants'][:-1] == prior['video_variants']
        assert after['video_variants'][-1]['id'] == read['task_id']
        assert after['image_variants'] == prior['image_variants'] and after['selected_image_id'] == 'choice'
        assert (root / 'output/uploads/motion-selected-old.mp4').read_bytes() == (MEDIA / 'old.mp4').read_bytes()
        events = evidence.payload['fake_provider']['events']
        submissions = [row for row in events if row['kind'] == 'submit']
        assert len(submissions) == state(http)['counts']['submit'] == 1
        wire = submissions[0]['payload']
        assert wire['parameters'] == {'duration': 5, 'resolution': '720P', 'prompt_extend': True, 'watermark': False}
        image = wire['input']['media'][0]['url'].split(',', 1)[1]
        assert base64.b64decode(image, validate=True) == (root / 'output/uploads/motion-choice.png').read_bytes()
        assert evidence.payload['blocked_sockets'] == [] and state(http)['counts']['fetch'] == 1
        persisted = json.loads((root / 'output/projects.json').read_text())[fixture['project_id']]
        assert len(persisted['provider_request_receipts']) == len(persisted['video_tasks']) == 1
        assert persisted['provider_request_receipts'][0]['client_request_id'] == 'owner-check'
        assert app.state.provider_recovery_dispatcher.is_running


def test_asgi_admission_loss_is_genuinely_accepted_until_original_continue(harness):
    with session(harness) as (_, http, evidence):
        fixture = state(http)['fixtures']['local']
        control(http, action='lose_admission_ack', project_id=fixture['project_id'])
        response, _ = post(http, fixture, 'lost-admission')
        assert response.status_code == 503, response.text
        read = original(http, fixture, 'lost-admission')
        assert read['task']['provider_execution']['phase'] == 'accepted'
        assert read['task']['provider_execution']['handle'] is None
        assert state(http)['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        dropped = [row for row in evidence.payload['controls'] if row['action'] == 'admission_ack_lost']
        assert len(dropped) == 1 and dropped[0]['original_response']['request'] == read['request']
        assert dropped[0]['original_response']['task']['provider_execution']['phase'] == 'accepted'
    with session(harness, resume=True) as (_, http, _):
        reopened = original(http, fixture, 'lost-admission')
        assert reopened == read
        assert state(http)['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        continuation = http.post(f"/projects/{fixture['project_id']}/provider_video_tasks/{read['task_id']}/continue", json=expected(read))
        assert continuation.status_code == 200, continuation.text
        recovered = http.post(original_path(fixture, 'lost-admission') + '/recover', json=expected(read))
        assert recovered.status_code == 200, recovered.text
        final = completion(http, fixture, 'lost-admission')
        assert final['request'] == read['request'] and state(http)['counts']['submit'] == 1


def test_provider_lost_ack_stays_unknown_without_handle_after_real_app_reopen(harness):
    with session(harness) as (_, http, _):
        fixture = state(http)['fixtures']['local']
        control(http, action='arm_next', status='SUCCEEDED', hold='none', lose_submit_ack=True)
        response, body = post(http, fixture, 'lost-provider-ack')
        assert response.status_code == 200, response.text
        read = original(http, fixture, 'lost-provider-ack')
        record = read['task']['provider_execution']
        assert record['phase'] == 'submitting' and record['handle'] is None
        assert read['task']['status'] == 'needs_confirmation'
        assert read['reason'] == 'provider_submit_receipt_missing'
        assert state(http)['counts'] == {'submit': 1, 'query': 0, 'fetch': 0}
    with session(harness, resume=True) as (app, http, _):
        assert app.state.provider_recovery_dispatcher.wait_for_idle(timeout=3)
        assert original(http, fixture, 'lost-provider-ack') == read
        before = saved_records(harness[1])
        denied = http.post(original_path(fixture, 'lost-provider-ack') + '/recover', json=expected(read))
        assert denied.status_code == 409 and denied.json()['code'] == 'motion_reference_recover_not_allowed'
        replay = http.post(f"/projects/{fixture['project_id']}/motion_ref_requests", json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request'] == read['request']
        assert state(http)['counts'] == {'submit': 1, 'query': 0, 'fetch': 0}
        assert saved_records(harness[1]) == before


@pytest.mark.parametrize(('hold', 'phase'), [('submit', 'submitting'), ('query', 'submitted'), ('fetch', 'observed')])
def test_bounded_external_holds_release_without_replacing_original_attempt(harness, hold, phase):
    with session(harness) as (_, http, _):
        fixture = state(http)['fixtures']['local']
        control(http, action='arm_next', status='SUCCEEDED', hold=hold, lose_submit_ack=False)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(post, http, fixture, 'held-original')
            try:
                held = await_value(lambda: state(http), lambda value: value['waiting'] == [f'studio-motion-1:{hold}'], timeout=3)
                assert held['counts']['submit'] == 1
                read = original(http, fixture, 'held-original')
                assert read['task']['provider_execution']['phase'] == phase
                assert read['task']['provider_execution']['artifact'] is None
            finally:
                if 'studio-motion-1' in state(http)['external_tasks']:
                    control(http, action='release', task_id='studio-motion-1', status='SUCCEEDED')
            response, _ = pending.result(timeout=5)
        assert response.status_code == 200, response.text
        final = completion(http, fixture, 'held-original')
        assert final['request'] == read['request'] and state(http)['counts']['submit'] == 1


@pytest.mark.parametrize(('status', 'reason'), [('FAILED', 'provider_remote_failed'), ('CANCELED', 'provider_remote_canceled')])
def test_actual_terminal_status_parser_preserves_old_selection_without_an_artifact(harness, status, reason):
    with session(harness) as (_, http, _):
        fixture = state(http)['fixtures']['local']
        prior = selected(http, fixture)
        control(http, action='arm_next', status=status, hold='none', lose_submit_ack=False)
        response, _ = post(http, fixture, 'terminal-original')
        assert response.status_code == 200, response.text
        read = original(http, fixture, 'terminal-original')
        assert read['task']['status'] == 'failed' and read['reason'] == reason
        assert read['task']['provider_execution']['phase'] == 'observed'
        assert read['task']['provider_execution']['artifact'] is None
        assert read['task']['provider_execution']['prepared_artifact'] is None
        assert state(http)['counts']['submit'] == 1 and state(http)['counts']['fetch'] == 0
        assert selected(http, fixture) == prior


def test_real_dispatcher_resumes_original_query_and_fetch_after_app_restart(harness):
    smoke, root, _, _, evidence_path = harness
    with session(harness) as (app, http, _):
        first_pipeline, first_dispatcher = app.state.pipeline, app.state.provider_recovery_dispatcher
        fixture = state(http)['fixtures']['series']
        control(http, action='arm_next', status='RUNNING', hold='none', lose_submit_ack=False)
        response, _ = post(http, fixture, 'restart-original')
        assert response.status_code == 200, response.text
        read = original(http, fixture, 'restart-original')
        record = read['task']['provider_execution']
        assert record['phase'] == 'observed' and record['observation']['status'] == 'running'
        assert record['handle']['task_id'] == 'studio-motion-1' and record['artifact'] is None
        before = state(http)['counts']
    assert not first_dispatcher.is_running
    evidence = smoke.open_evidence(evidence_path, resume=True)
    # Only the external ledger changes while the actual app is closed.
    smoke.Controls(evidence).control({'action': 'release', 'task_id': 'studio-motion-1', 'status': 'SUCCEEDED'})
    with session(harness, resume=True, evidence=evidence) as (app, http, restored_evidence):
        from src.apps.comic_gen.provider_recovery_dispatcher import StudioProviderRecoveryDispatcher
        assert app.state.pipeline is not first_pipeline
        assert type(app.state.provider_recovery_dispatcher) is StudioProviderRecoveryDispatcher
        assert app.state.provider_recovery_dispatcher.is_running
        final = completion(http, fixture, 'restart-original')
        assert final['request'] == read['request']
        current = final['task']['provider_execution']
        for field in ('attempt_id', 'fingerprint', 'handle', 'credential_ref', 'scope', 'media'):
            assert current[field] == record[field]
        after = state(http)['counts']
        assert after['submit'] == before['submit'] == 1 and after['query'] > before['query'] and after['fetch'] == 1
        assert (root / 'output' / current['artifact']['relative_path']).read_bytes() == (MEDIA / 'new.mp4').read_bytes()
        starts = [row for row in restored_evidence.payload['sessions'] if 'started_at' in row]
        assert [row['resume'] for row in starts] == [False, True]
        # Recovery here is exclusively the real lifecycle worker, not a hidden public command.
        assert not any(row['method'] == 'POST' and row['path'].endswith(('/recover', '/continue')) for row in restored_evidence.payload['api_audit'])


@pytest.mark.parametrize('mode', ['missing', 'invalid'])
def test_owned_damage_preserves_proof_and_real_recover_never_redownloads(harness, mode):
    smoke, root, _, _, _ = harness
    with session(harness) as (_, http, evidence):
        fixture = state(http)['fixtures']['local']
        response, _ = post(http, fixture, 'damaged-original')
        assert response.status_code == 200, response.text
        read = completion(http, fixture, 'damaged-original')
        record = deepcopy(read['task']['provider_execution'])
        before, counts = saved_records(root), state(http)['counts']
        value = damage_command(fixture, read, mode)
        damaged = control(http, **value)
        assert damaged == {'fault': mode, 'proof': record['artifact'], 'product_record_modified': False}
        assert saved_records(root) == before
        fault = [row for row in evidence.payload['controls'] if row['action'] == 'damage_artifact'][-1]
        retained = smoke.owned_path(root, fault['retained_path'])
        assert retained.read_bytes() == (MEDIA / 'new.mp4').read_bytes()
        repeated = http.post('/__smoke/control', json=value)
        assert repeated.status_code == 422 and retained.read_bytes() == (MEDIA / 'new.mp4').read_bytes()
        recovered = http.post(original_path(fixture, 'damaged-original') + '/recover', json=expected(read))
        assert recovered.status_code == 200, recovered.text
        result = recovered.json()
        assert result['request'] == read['request'] and result['task']['provider_execution'] == record
        assert result['task']['status'] == 'needs_confirmation'
        assert result['reason'] == f'provider_fetch_artifact_{mode}'
        assert state(http)['counts'] == counts
        assert selected(http, fixture)['selected_video_id'] == 'old-selected'
        path = root / 'output' / record['artifact']['relative_path']
        if mode == 'missing':
            assert not path.exists()
        else:
            assert path.read_bytes() != retained.read_bytes()


@pytest.mark.parametrize('invalid', ['task_id', 'attempt_id', 'fingerprint', 'extra_path', 'file_hash', 'symlink'])
def test_damage_refuses_wrong_authority_or_unsafe_file_without_changing_records(harness, invalid, tmp_path):
    _, root, _, _, _ = harness
    with session(harness) as (_, http, _):
        fixture = state(http)['fixtures']['local']
        response, _ = post(http, fixture, 'protected-original')
        assert response.status_code == 200, response.text
        read = completion(http, fixture, 'protected-original')
        value = damage_command(fixture, read)
        path = root / 'output' / read['task']['provider_execution']['artifact']['relative_path']
        external = tmp_path / 'outside-original.mp4'
        if invalid in {'task_id', 'attempt_id', 'fingerprint'}:
            value[invalid] = 'not-the-original'
        elif invalid == 'extra_path':
            value['relative_path'] = '../outside-original.mp4'
        elif invalid == 'file_hash':
            path.write_bytes(path.read_bytes() + b'changed after genuine completion')
        else:
            path.rename(external)
            path.symlink_to(external)
        before, counts, bytes_before = saved_records(root), state(http)['counts'], path.read_bytes()
        rejected = http.post('/__smoke/control', json=value)
        assert rejected.status_code == 422, rejected.text
        assert saved_records(root) == before and state(http)['counts'] == counts
        assert path.read_bytes() == bytes_before and not (root / 'retained-artifacts').exists()
        if invalid == 'symlink':
            assert path.is_symlink() and external.read_bytes() == bytes_before


@pytest.mark.parametrize('damage', ['commit', 'static_bytes', 'static_extra', 'static_traversal', 'static_symlink', 'seed_media', 'fixture', 'evidence'])
def test_resume_rejects_source_static_media_fixture_or_ledger_drift(harness, damage, tmp_path):
    smoke, root, static, _, evidence_path = harness
    with session(harness):
        pass
    marker_path = root / smoke.MARKER
    if damage == 'static_bytes':
        (static / 'index.html').write_text('changed static bytes')
    elif damage == 'static_extra':
        (static / 'extra.js').write_text('unexpected extra source')
    elif damage == 'static_traversal':
        marker = json.loads(marker_path.read_text())
        outside = root / 'outside.js'
        outside.write_text('outside')
        marker['static']['files'][0] = {'path': '../outside.js', 'sha256': smoke.base.sha256_file(outside)}
        marker_path.write_text(json.dumps(marker))
    elif damage == 'static_symlink':
        outside = tmp_path / 'outside-static'
        outside.mkdir()
        (outside / 'file.js').write_text('outside')
        (static / 'linked').symlink_to(outside, target_is_directory=True)
    elif damage == 'seed_media':
        (root / 'seed-media/new.mp4').write_bytes(b'changed seed')
    elif damage == 'fixture':
        fixture = json.loads((root / 'fixture.json').read_text())
        fixture['unexpected'] = True
        (root / 'fixture.json').write_text(json.dumps(fixture))
    elif damage == 'evidence':
        payload = json.loads(evidence_path.read_text())
        payload['fake_provider']['counts']['submit'] = 1
        evidence_path.write_text(json.dumps(payload))
    with pytest.raises((ValueError, SystemExit)):
        if damage == 'commit':
            smoke.prepare_root(root, static, frontend_commit='changed', backend_commit='fixture-back', resume=True)
        else:
            with session(harness, resume=True):
                pass


def test_unsafe_input_paths_and_evidence_inside_business_root_are_rejected(harness, tmp_path):
    smoke, root, static, _, _ = harness
    manifest = json.loads((MEDIA / 'media-manifest.json').read_text())
    sources = {role: MEDIA / f'{role}.mp4' for role in manifest}
    root_link = tmp_path / 'root-link'
    root_link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        smoke.prepare_root(root_link, static, frontend_commit='x', backend_commit='y', resume=False,
                           media_sources=sources, media_manifest=manifest)
    media_link = tmp_path / 'clip-link.mp4'
    owned_media = tmp_path / 'owned-old.mp4'
    owned_media.write_bytes((MEDIA / 'old.mp4').read_bytes())
    media_link.symlink_to(owned_media)
    with pytest.raises(ValueError, match='symlink'):
        smoke.prepare_root(tmp_path / 'other-root', static, frontend_commit='x', backend_commit='y', resume=False,
                           media_sources={**sources, 'old': media_link}, media_manifest=manifest)
    outside_evidence = tmp_path / 'outside.json'
    outside_evidence.write_text('untouched')
    evidence_link = tmp_path / 'evidence-link.json'
    evidence_link.symlink_to(outside_evidence)
    with pytest.raises(ValueError, match='symlink'):
        smoke.open_evidence(evidence_link, resume=True)
    assert outside_evidence.read_text() == 'untouched'
    inside = smoke.open_evidence(root / 'inside-evidence.json', resume=False)
    with smoke.isolated_environment(root), pytest.raises(ValueError, match='separate evidence'):
        smoke.create_smoke_app(root, static, inside)


def test_both_requests_entries_and_socket_guards_ignore_ambient_authority_and_restore(harness, monkeypatch, tmp_path):
    import requests
    original_aliases = requests.Session, requests.sessions.Session
    original_sockets = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    poison = tmp_path / 'ambient.netrc'
    poison.write_text('machine studio-motion-smoke.invalid login synthetic password synthetic\n')
    monkeypatch.setenv('NETRC', str(poison))
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.invalid:1234')
    with session(harness) as (_, http, evidence):
        assert os.environ['NETRC'] != str(poison) and not Path(os.environ['NETRC']).exists()
        assert 'HTTPS_PROXY' not in os.environ
        for make_session in (requests.Session, requests.sessions.Session):
            with make_session() as client:
                assert client.trust_env is False
                assert client.merge_environment_settings('https://unhandled.invalid', {}, False, None, None)['proxies'] == {}
                with pytest.raises(RuntimeError, match='unexpected external HTTP'):
                    client.post('https://unhandled.invalid/anything')
        with pytest.raises(RuntimeError, match='unexpected external HTTP'):
            requests.get('https://unhandled.invalid/module-level')
        with pytest.raises(RuntimeError, match='synthetic credential'):
            requests.post(harness[0].SUBMIT, json={}, headers={'Authorization': 'Bearer synthetic-wrong'})
        with pytest.raises(RuntimeError, match='blocks outbound'):
            socket.getaddrinfo('external.invalid', 443)
        for method in ('connect', 'connect_ex'):
            with socket.socket() as sock, pytest.raises(RuntimeError, match='blocks outbound'):
                getattr(sock, method)(('192.0.2.3', 443))
        assert state(http)['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        assert len(evidence.payload['blocked_sockets']) == 3
    assert (requests.Session, requests.sessions.Session) == original_aliases
    assert (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo) == original_sockets
    assert os.environ['NETRC'] == str(poison) and os.environ['HTTPS_PROXY'] == 'http://proxy.invalid:1234'


def test_exact_get_faults_do_not_affect_other_projects_or_enable_mutation_controls(harness):
    with session(harness) as (_, http, _):
        fixture = state(http)['fixtures']['local']
        path = f"/projects/{fixture['project_id']}"
        counts = state(http)['counts']
        control(http, action='block_get', path=path, enabled=True)
        assert http.get(path).status_code == 503
        assert http.get('/projects/motion-global').status_code == 200
        rejected = http.post('/__smoke/control', json={'action': 'block_get', 'path': '/projects/../outside', 'enabled': True})
        assert rejected.status_code == 422
        control(http, action='block_get', path=path, enabled=False)
        assert http.get(path).status_code == 200 and state(http)['counts'] == counts


def test_debug_controls_are_absent_from_the_normal_app(harness):
    smoke, root, static, evidence, _ = harness
    with smoke.isolated_environment(root):
        from src.apps.comic_gen.api import create_app, router as product_router
        shared_dispatch = [(route, route.handle, getattr(route, 'app', None), getattr(route, 'endpoint', None))
                           for route in product_router.routes]
        production_before = create_app(data_dir=root / 'production-before', load_config=False, setup_logs=False,
                                       recover_orphan_tasks=False, warmup_demucs=False, access_mode='local')
        fixture_app = smoke.create_smoke_app(root, static, evidence, hold_seconds=5)
        production_after = create_app(data_dir=root / 'production-after', load_config=False, setup_logs=False,
                                      recover_orphan_tasks=False, warmup_demucs=False, access_mode='local')
        boundaries = [route for route in fixture_app.app.router.routes if isinstance(route.handle, smoke.FaultBoundary)]
        assert len(boundaries) == 1 and boundaries[0].handle.admission_transport is True
        assert boundaries[0].handle.app.__self__ is boundaries[0]
        assert all(boundaries[0] is not row[0] for row in shared_dispatch)
        assert [(route, route.handle, getattr(route, 'app', None), getattr(route, 'endpoint', None))
                for route in product_router.routes] == shared_dispatch
        for production in (production_before, production_after):
            assert not any(getattr(route, 'path', '').startswith('/__smoke') for route in production.routes)
            assert all(not isinstance(route.handle, smoke.FaultBoundary) for route in production.routes)
            assert all(boundaries[0] is not route for route in production.routes)
