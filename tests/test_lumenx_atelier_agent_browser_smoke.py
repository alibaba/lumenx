"""Browser harness keeps real Agent/runtime/HTTP, with explicit fake planner."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import time

from fastapi.testclient import TestClient
import pytest
import requests
from requests.adapters import HTTPAdapter

SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_atelier_agent_browser_smoke.py'


def load():
    spec = importlib.util.spec_from_file_location('atelier_agent_smoke', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(tmp_path):
    smoke = load()
    source = tmp_path / 'build'
    source.mkdir()
    (source / 'index.html').write_text('<html>Agent smoke</html>')
    root = tmp_path / 'business'
    static = smoke.prepare_root(root, source, frontend_commit='fixture-front', backend_commit='fixture-back')
    evidence = smoke.open_evidence(tmp_path / 'evidence/audit.json')
    return smoke, root, static, evidence


def ack(response):
    assert response.status_code == 200, response.text
    if response.headers['content-type'].startswith('text/event-stream'):
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert not any(e['type'] == 'agent_error' for e in events)
        return events[-1]['ack']
    return response.json()


def identity(value):
    call = next(c for c in reversed(value['turn']['tool_calls']) if c.get('preview_binding'))
    ref = call['preview_binding']['agent_authorization_ref']
    return {'expected_revision': value['project']['revision'], 'turn_id': value['turn']['id'],
            'call_id': call['call_id'], 'approval_id': ref['approval_id'], 'authorization_hash': ref['authorization_hash']}


def test_three_fresh_projects_and_only_readonly_controls(harness):
    smoke, root, static, evidence = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        state = http.get('/__smoke/state').json()
        assert state['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        assert len(state['scenarios']) == 3
        assert len({s['project_id'] for s in state['scenarios']}) == 3
        for scenario in state['scenarios']:
            project = http.get('/atelier/projects/' + scenario['project_id']).json()
            assert len(project['nodes']) == 2
            image = next(n for n in project['nodes'] if n['type'] == 'image')
            video = next(n for n in project['nodes'] if n['type'] == 'video')
            assert video['data']['reference_node_ids'] == [image['id']] and video['media_urls'] == []
            assert http.get('/files/' + image['media_urls'][0]).status_code == 200
            assert not project['generation_attempts'] and not project['generation_request_receipts']
            assert not project['agent_turns']
        assert app.state.pipeline.read_committed_projects() == []
        assert http.post('/__smoke/state', json={}).status_code == 405
        assert http.post('/__smoke/control', json={}).status_code == 404
        assert evidence.payload['fake_model_planner']['counts']['chat_result'] == 0
        assert app.state.atelier_generation_dispatcher._interval_seconds == 0.05


@pytest.mark.parametrize('scenario_index,stream', [(0, False), (0, True), (1, False), (1, True), (2, True)])
def test_actual_agent_approval_dispatch_http_artifact_and_get_recovery(harness, scenario_index, stream):
    from src.models.wanx import WanxModel
    from src.utils.media_probe import probe_media_file
    smoke, root, static, evidence = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        scenario = smoke.SCENARIOS[scenario_index]
        project_id = scenario['project_id']
        base = '/atelier/projects/' + project_id
        family = scenario['family']
        body = {'expected_revision': 0, 'client_turn_id': 'browser-turn', 'user_message': '生成视频', 'selected_node_id': 'video'}
        if family == 'loop':
            body.update(max_iterations=3, planner=scenario['planner'])
        route = base + f'/agent/{family}s' + ('/stream' if stream else '')
        preview = ack(http.post(route, json=body))
        assert preview['turn']['status'] == 'waiting_approval'
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        approved = ack(http.post(base + '/agent/turns/browser-turn/approval', json={**identity(preview), 'decision': 'approved'}))
        call = next(c for c in approved['turn']['tool_calls'] if c.get('preview_binding'))
        request_id = call['preview_binding']['preview_public_record']['raw_request']['client_request_id']
        if family == 'turn':
            continue_path = base + '/agent/turns/browser-turn/continue'
            command = {**identity(approved), 'client_request_id': request_id}
        else:
            continue_path = base + '/agent/loops/browser-turn/resume'
            command = {'turn_id': 'browser-turn'}
        submitted = http.post(continue_path + ('/stream' if stream else ''), json=command)
        assert submitted.status_code == 200
        # Deliberately discard the ACK; only original GETs recover the result.
        deadline = time.monotonic() + 10
        result = None
        while time.monotonic() < deadline:
            result = ack(http.get(base + '/generation/requests/' + request_id))
            if result['attempt']['status'] == 'completed':
                break
            assert result['attempt']['status'] not in {'failed', 'needs_confirmation'}, result['attempt']
            time.sleep(0.02)
        assert result is not None and result['attempt']['status'] == 'completed'
        record = result['attempt']['provider_execution']
        assert record['provider_model_id'] == 'wan2.7-i2v'
        assert record['handle']['task_id'] == 'agent-task-1'
        output = root / 'output' / result['attempt']['output_ref']
        assert probe_media_file(output, 'video').kind == 'video'
        assert record['artifact']['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
        assert http.get('/files/' + result['attempt']['output_ref']).content == output.read_bytes()
        assert all(not n['media_urls'] for n in result['project']['nodes'] if n['type'] == 'video')
        counts = {'submit': 1, 'query': 1, 'fetch': 1}
        assert evidence.payload['fake_provider']['counts'] == counts
        assert type(app.state.provider_runtime_selector.for_admission(provider_model_id='wan2.7-i2v', gateway='dashscope').runtime.provider) is WanxModel
        reread = ack(http.get(base + f'/agent/{family}s/browser-turn'))
        assert reread['turn']['id'] == 'browser-turn'
        assert ack(http.post(route, json=body))['replayed']
        assert app.state.atelier_generation_dispatcher.wait_for_idle(2)
        assert evidence.payload['fake_provider']['counts'] == counts
        if scenario['planner'] == 'model_adapter':
            invocations = reread['turn']['planner_invocations']
            assert len(invocations) == 1 and invocations[0]['status'] == 'settled'
            assert invocations[0]['usage']['completion_tokens'] == 5
            second = ack(http.post(continue_path + '/stream', json=command))
            assert second['turn']['status'] == 'waiting_approval'
            rejected = ack(http.post(base + '/agent/turns/browser-turn/approval', json={**identity(second), 'decision': 'denied'}))
            assert rejected['turn']['status'] == 'failed'
            assert app.state.atelier_generation_dispatcher.wait_for_idle(2)
            assert evidence.payload['fake_provider']['counts'] == counts
        selected = ack(http.post(base + '/nodes/video/video_candidates/' + result['attempt']['candidate_id'] + '/select',
            json={'expected_revision': http.get(base).json()['revision']}))
        assert next(n for n in selected['project']['nodes'] if n['id'] == 'video')['media_urls'] == [result['attempt']['output_ref']]
        assert evidence.payload['blocked_sockets'] == []
    assert evidence.payload['sessions'][-1].get('stopped_at')


def test_fresh_only_fixed_static_and_dependency_hashes(harness):
    smoke, root, static, evidence = harness
    marker = json.loads((root / smoke.MARKER).read_text())
    assert marker['static']['files'][0]['sha256'] == smoke.base.sha256_file(static / 'index.html')
    with pytest.raises(ValueError, match='evidence'):
        smoke.open_evidence(evidence.path)
    with pytest.raises(SystemExit, match='empty'):
        smoke.prepare_root(root, static, frontend_commit='f', backend_commit='b')
    with TestClient(smoke.create_smoke_app(root, static, evidence)):
        pass
    with pytest.raises(ValueError, match='fresh'):
        smoke.create_smoke_app(root, static, evidence)
    with pytest.raises(SystemExit):
        smoke.parse_args(['--resume'])
    hashes = smoke.source_hashes()
    assert hashes['docs/agents/tools/lumenx_generation_browser_smoke.py'] == smoke.base.sha256_file(Path(smoke.base.__file__))
    assert hashes['docs/agents/tools/lumenx_atelier_agent_browser_smoke.py'] == smoke.base.sha256_file(SCRIPT)


@pytest.mark.parametrize('raises', [False, True])
def test_transport_and_planner_patches_restore_and_all_unexpected_egress_is_blocked(harness, raises):
    from src.apps.comic_gen import llm_adapter
    smoke, root, static, evidence = harness
    original = (HTTPAdapter.send, llm_adapter.LLMAdapter, socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo)
    try:
        with TestClient(smoke.create_smoke_app(root, static, evidence)):
            assert HTTPAdapter.send is not original[0] and llm_adapter.LLMAdapter is not original[1]
            with pytest.raises(RuntimeError, match='already active'):
                with smoke.offline_transport(evidence, root / 'provider-fixture/result.mp4', set()):
                    pytest.fail('nested patch must not be installed')
            with pytest.raises(RuntimeError, match='HTTP'):
                requests.get('https://unhandled.invalid/forbidden', auth=lambda request: request)
            with socket.socket() as sock:
                with pytest.raises(RuntimeError, match='network'):
                    sock.connect(('203.0.113.1', 443))
            with pytest.raises(RuntimeError, match='DNS'):
                socket.getaddrinfo('external.invalid', 443)
            if raises:
                raise RuntimeError('synthetic caller exit')
    except RuntimeError as exc:
        assert raises and str(exc) == 'synthetic caller exit'
    assert (HTTPAdapter.send, llm_adapter.LLMAdapter, socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo) == original
    assert not getattr(HTTPAdapter, '_agent_browser_smoke_active', False)


def test_cli_style_shutdown_keeps_egress_guard_until_process_exit(harness, monkeypatch):
    from src.apps.comic_gen import llm_adapter
    smoke, root, static, evidence = harness
    # Restore global process hooks after testing the CLI's intentional retention.
    for obj, name in [(HTTPAdapter, 'send'), (llm_adapter, 'LLMAdapter'), (socket.socket, 'connect'), (socket.socket, 'connect_ex'), (socket, 'getaddrinfo')]:
        monkeypatch.setattr(obj, name, getattr(obj, name))
    monkeypatch.setattr(HTTPAdapter, '_agent_browser_smoke_active', False, raising=False)
    with TestClient(smoke.create_smoke_app(root, static, evidence, restore_transport=False)):
        pass
    with pytest.raises(RuntimeError, match='HTTP'):
        requests.get('https://unhandled.invalid/after-shutdown', auth=lambda request: request)
    with pytest.raises(RuntimeError, match='DNS'):
        socket.getaddrinfo('external.invalid', 443)
