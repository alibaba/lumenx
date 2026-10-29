"""Atelier browser fixture keeps real product/runtime code behind fake HTTP."""
import importlib.util
import json
from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient

SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_atelier_image_browser_smoke.py'
VERSION = 'provider-wan27-t2i-bound-http-v1'


def load():
    spec = importlib.util.spec_from_file_location('atelier_image_smoke', SCRIPT)
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


def test_owned_fresh_fixture_preserves_legacy_identity_without_paid_calls(harness):
    smoke, root, static, evidence = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        state = http.get('/__smoke/state').json()
        assert state['kind'] == smoke.KIND
        assert state['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        assert state['legacy']['unchanged']
        project = http.get('/atelier/projects/' + state['project_id']).json()
        assert len(project['nodes']) == 2
        assert {n['id'] for n in project['nodes']} == {smoke.IMAGE_NODE, smoke.PRO_NODE}
        assert all(n['data']['generation_draft']['batch_size'] == 2 and n['media_urls'] == [] for n in project['nodes'])
        old_project = http.get('/atelier/projects/' + state['legacy']['project_id']).json()
        assert app_empty(http.app)
        assert http.post('/__smoke/state', json={}).status_code == 405
        assert http.post('/__smoke/control', json={}).status_code == 404
        old = next(n for n in old_project['nodes'] if n['id'] == state['legacy']['node_id'])
        assert old['data']['candidates'][0]['status'] == 'completed'
        old_attempt = next(a for a in old_project['generation_attempts'] if a['node_id'] == old['id'])
        assert 'intent_contract_version' not in old_attempt['provider_execution']['intent']
        assert old_attempt['provider_execution']['phase'] == 'completed'
        draft = old
        assert draft['data']['generation_draft']['schema_version'] == 3
        assert draft['data']['generation_draft']['params']['seed'] == -1
    assert evidence.payload['retained_legacy']['unchanged']


def app_empty(app):
    return app.state.pipeline.scripts == {} and app.state.pipeline.series_store == {} and not app.state.playground_storage.list_history(limit=1)


@pytest.mark.parametrize('node_id,model', [('wan-image', 'wan2.7-image'), ('wan-image-pro', 'wan/wan2.7-image-pro#image')])
def test_strict_batch_real_http_recovery_selection_and_judgment(harness, node_id, model):
    from src.models import provider_media_execution
    from src.utils.media_probe import probe_image_file
    smoke, root, static, evidence = harness
    original_factory = provider_media_execution.WanxImageModel
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        project_path = '/atelier/projects/' + smoke.PROJECT_ID
        path = project_path + '/nodes/' + node_id
        body = {'expected_revision': 0, 'client_request_id': 'batch-request', 'model_id': model,
                'mode': 't2i', 'prompt': 'Synthetic crystal moon', 'params': {'seed': 0, 'watermark': False}, 'batch_size': 2}
        response = http.post(path + '/image_candidates', json=body)
        assert response.status_code == 200, response.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        # Discard POST ACK and read the original request, without another submit.
        result = http.get(project_path + '/generation/requests/batch-request')
        assert result.status_code == 200, result.text
        ack = result.json()
        assert len(ack['members']) == 2
        for member in ack['members']:
            attempt = member['attempt']
            execution = attempt['provider_execution']
            assert attempt['status'] == 'completed', attempt
            assert execution['intent']['intent_contract_version'] == VERSION
            assert execution['intent']['params'] == {'size': '1280*1280', 'seed': 0, 'watermark': False}
            assert execution['intent']['sources']['params.watermark'] == 'request'
            assert execution['scope']['gateway'] == 'dashscope'
            assert execution['artifact'] == execution['prepared_artifact']
            media = root / 'output' / attempt['output_ref']
            assert http.get('/files/' + attempt['output_ref']).content == media.read_bytes()
            metadata = probe_image_file(media)
            assert metadata.codec_name == 'png' and (metadata.width, metadata.height) == (320, 240)
        node = next(n for n in ack['project']['nodes'] if n['id'] == node_id)
        assert node['media_urls'] == [] and 'selected_candidate_id' not in node['data']
        counts = {'submit': 2, 'query': 2, 'fetch': 2}
        assert evidence.payload['fake_provider']['counts'] == counts
        assert http.get(project_path + '/generation/requests/batch-request').json() == ack
        replay = http.post(path + '/image_candidates', json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] and replay.json()['new_candidate_ids'] == []
        assert evidence.payload['fake_provider']['counts'] == counts
        candidate_id = ack['members'][1]['candidate']['id']
        selected = http.post(path + '/media_candidates/' + candidate_id + '/select',
                             json={'expected_revision': ack['project']['revision']})
        assert selected.status_code == 200, selected.text
        judged = http.put(path + '/candidates/' + candidate_id + '/judgment',
                          json={'expected_revision': selected.json()['project']['revision'], 'verdict': 'keep', 'reason': 'Synthetic reviewer choice'})
        assert judged.status_code == 200, judged.text
        refreshed = http.get(project_path).json()
        node = next(n for n in refreshed['nodes'] if n['id'] == node_id)
        assert node['data']['selected_candidate_id'] == candidate_id
        assert len(refreshed['candidate_judgments']) == 1
        assert evidence.payload['fake_provider']['counts'] == counts
        assert provider_media_execution.WanxImageModel is original_factory
        assert http.get('/__smoke/state').json()['legacy']['unchanged']
        assert evidence.payload['blocked_sockets'] == []


def test_legacy_branch_restores_original_unsupported_draft_without_new_execution(harness):
    smoke, root, static, evidence = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        fixture = http.get('/__smoke/state').json()['legacy']
        path = '/atelier/projects/' + fixture['project_id']
        original = http.get(path).json()
        branch = http.post(path + '/nodes/' + fixture['node_id'] + '/candidates/' + fixture['candidate_id'] + '/branch',
                           json={'expected_revision': original['revision']})
        assert branch.status_code == 200, branch.text
        ack = branch.json()
        draft = next(n for n in ack['project']['nodes'] if n['id'] == ack['node_ids'][0])
        assert draft['data']['branch_source']['recipe_status'] == 'available'
        assert draft['data']['generation_draft']['schema_version'] == 3
        params = draft['data']['generation_draft']['params']
        assert params['seed'] == -1 and params['prompt_extend'] is False and params['negative_prompt'] == ''
        assert draft['media_urls'] == [] and not draft['data'].get('candidates')
        assert ack['project']['generation_attempts'] == original['generation_attempts']
        assert ack['project']['generation_request_receipts'] == original['generation_request_receipts']
        rejected = http.post(path + '/nodes/' + draft['id'] + '/image_candidates', json={
            'expected_revision': ack['project']['revision'], 'client_request_id': 'invalid-legacy-new',
            'model_id': draft['data']['generation_draft']['model_id'], 'mode': 't2i',
            'prompt': draft['prompt'], 'params': params, 'batch_size': 1})
        assert rejected.status_code == 422, rejected.text
        assert http.get(path + '/generation/requests/invalid-legacy-new').status_code == 404
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        assert http.get('/__smoke/state').json()['legacy']['unchanged']


def test_default_source_is_preset_and_catalog_with_seed_omitted(harness):
    smoke, root, static, evidence = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        path = '/atelier/projects/' + smoke.PROJECT_ID
        response = http.post(path + '/nodes/' + smoke.IMAGE_NODE + '/image_candidates', json={
            'expected_revision': 0, 'client_request_id': 'default-request', 'model_id': 'wan2.7-image',
            'mode': 't2i', 'prompt': 'Defaults', 'params': {}, 'batch_size': 1})
        assert response.status_code == 200, response.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        ack = http.get(path + '/generation/requests/default-request').json()
        intent = ack['members'][0]['attempt']['provider_execution']['intent']
        assert intent['params'] == {'size': '1280*1280', 'watermark': False}
        assert intent['sources']['params.watermark'] == 'preset'
        assert intent['sources']['params.size'] == 'catalog'
        assert intent['preset_id'] == 'atelier.wan27.t2i' and intent['preset_version'] == '1'
        assert 'params.seed' not in intent['request_fields'] and 'params.watermark' not in intent['request_fields']
        assert evidence.payload['fake_provider']['counts'] == {'submit': 1, 'query': 1, 'fetch': 1}


def test_fresh_root_separate_evidence_and_no_resume(harness):
    smoke, root, static, evidence = harness
    with pytest.raises(SystemExit, match='empty'):
        smoke.prepare_root(root, static, frontend_commit='front', backend_commit='back')
    with pytest.raises(ValueError, match='evidence'):
        smoke.open_evidence(evidence.path)
    with pytest.raises(ValueError, match='separate'):
        smoke.create_smoke_app(root, static, smoke.open_evidence(root / 'audit.json'))
    (root / 'audit.json').unlink()
    with TestClient(smoke.create_smoke_app(root, static, evidence)):
        pass
    before = (root / 'output/atelier_projects.json').read_bytes()
    with pytest.raises(ValueError, match='fresh'):
        smoke.create_smoke_app(root, static, evidence)
    assert (root / 'output/atelier_projects.json').read_bytes() == before
    with pytest.raises(SystemExit):
        smoke.parse_args(['--data-root', str(root), '--evidence', str(evidence.path), '--static-root', str(static),
                          '--frontend-freeze-commit', 'front', '--backend-source-commit', 'back', '--resume'])
    for host in ('0.0.0.0', 'localhost', 'example.invalid'):
        with pytest.raises(SystemExit):
            smoke.base.validate_loopback_literal(host)
    marker = json.loads((root / smoke.MARKER).read_text())
    assert marker['static']['files'][0]['sha256'] == smoke.base.sha256_file(static / 'index.html')
    hashes = smoke.source_hashes()
    assert hashes[smoke.LEGACY_SOURCE.relative_to(smoke.base.REPO_ROOT).as_posix()] == smoke.base.sha256_file(smoke.LEGACY_SOURCE)


def test_cli_guard_survives_shutdown_and_restores_only_in_tests(harness, monkeypatch):
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
