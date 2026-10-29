"""Real public HTTP/Core self-checks; browser/journal acceptance stays separate."""
import hashlib
import importlib.util
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_studio_gpt_t2i_browser_smoke.py'
PUBLIC = '/projects/p/asset_image_requests'
PROMPT = 'A distinct authored synthetic scene or prop, soft studio light.'
SOURCE_FILES = (
    'docs/agents/tools/lumenx_studio_gpt_t2i_browser_smoke.py',
    'tests/test_lumenx_studio_gpt_t2i_browser_smoke.py',
    'docs/agents/tools/lumenx_studio_headshot_browser_smoke.py',
    'docs/agents/tools/lumenx_studio_image_browser_smoke.py',
    'docs/agents/tools/lumenx_studio_media_browser_smoke.py',
    'docs/agents/tools/lumenx_generation_browser_smoke.py',
    'tests/frozen_image_fixtures.py',
)


def source_bindings():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    sources = source_bindings()
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    spec = importlib.util.spec_from_file_location('gpt_t2i_browser_smoke', SCRIPT)
    assert spec and spec.loader
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    static = tmp_path / 'build'
    static.mkdir()
    (static / 'index.html').write_text('<html>self-check only</html>')
    root = tmp_path / 'business'
    mounted = smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=False)
    path = tmp_path / 'evidence' / 'audit.json'
    evidence = smoke.open_evidence(path, resume=False)
    yield smoke, root, mounted, evidence, path
    assert source_bindings() == sources


def command(kind, key):
    return {
        'client_request_id': key, 'asset_type': kind, 'asset_id': 'c', 'generation_type': 'all',
        'apply_style': False, 'prompt': PROMPT, 'batch_size': 2,
    }


def asset(response, kind):
    assert response.status_code == 200, response.text
    rows = [row for row in response.json()[kind + 's'] if row['id'] == 'c']
    assert len(rows) == 1
    return rows[0]


def receipt(response):
    assert response.status_code == 200, response.text
    return response.json()['request']


def saved_owner(app, owner):
    pipeline = app.state.pipeline
    return pipeline._load_series_data()['s'] if owner == 'series' else pipeline._load_library_data()


def assert_actual_t2i(app, owner, smoke, root, public, evidence, *, submitted, completed):
    from src.utils.media_probe import probe_image_file

    kind = smoke.OWNER_ASSETS[owner]
    stored = saved_owner(app, owner)
    assert len(stored.asset_image_requests) == 1
    record = stored.asset_image_requests[0]
    assert record.id == public['id']
    assert (public['entrypoint'], public['channel'], public['asset_type']) == (
        'studio.asset.scene_prop.v1', 'image', kind,
    )
    assert (public['request_root_kind'], public['request_root_id']) == ('project', 'p')
    assert (public['owner_kind'], public['owner_id']) == {
        'series': ('series', 's'), 'global': ('global', 'library'),
    }[owner]
    for member in record.members:
        execution = member.execution
        assert execution.provider_model_id == 'gpt-image-2'
        assert execution.intent['mode'] == 't2i' and execution.media == []
        assert 'intent_contract_version' not in execution.intent
        assert execution.intent['params'] == {'size': smoke.SIZES[kind]}
        assert execution.intent['prompt'] == PROMPT
        if execution.phase == 'completed':
            assert execution.output_ref and execution.artifact
            assert execution.artifact == execution.prepared_artifact
            output = root / 'output' / execution.output_ref
            assert hashlib.sha256(output.read_bytes()).hexdigest() == execution.artifact['sha256']
            dimensions = probe_image_file(output)
            assert (dimensions.width, dimensions.height) == (160, 160)
            actual = next(row for row in public['members'] if row['variant_id'] == member.variant_id)
            assert actual['output_ref'] == execution.output_ref
    posts = [row for row in evidence.payload['fake_provider']['events'] if row['kind'] == 'submit']
    assert len(posts) == submitted
    expected = {'prompt': PROMPT, 'size': smoke.SIZES[kind], 'quality': 'high', 'n': 1}
    assert all(row['payload'] == expected and row['reference'] is None for row in posts)
    assert evidence.payload['fake_provider']['counts'] == {
        'submit': submitted, 'query': completed, 'fetch': completed,
    }
    responses = evidence.payload.get('external_http_responses', [])
    accepted = [row for row in responses if row['method'] == 'POST']
    observed = [row for row in responses if row['method'] == 'GET']
    acknowledged = {identity for identity, task in evidence.payload['external_tasks'].items() if not task['lost_ack']}
    assert len({row['task_id'] for row in posts}) == submitted
    assert len(accepted) == len(acknowledged) and len(observed) == completed
    assert {row['body']['task_info']['id'] for row in accepted} == acknowledged
    expected_queries = Counter({identity: 1 for identity in acknowledged})
    assert Counter(row['body']['task_info']['id'] for row in observed) == expected_queries
    for action in ('query', 'fetch'):
        assert Counter(row['task_id'] for row in evidence.payload['fake_provider']['events']
                       if row['kind'] == action) == expected_queries
    for row in accepted:
        task_id = row['body']['task_info']['id']
        assert row['url'] == smoke.GENERATION and row['status'] == 202
        assert row['body'] == {'task_info': {
            'id': task_id, 'status': 'pending',
            'created_at': smoke.SYNTHETIC_TIMESTAMP, 'updated_at': smoke.SYNTHETIC_TIMESTAMP,
        }}
    for row in observed:
        task_id = row['body']['task_info']['id']
        assert task_id in acknowledged and row['url'] == smoke.GENERATION + '/' + task_id
        assert row['status'] == 200
        assert row['body'] == {'task_info': {
            'id': task_id, 'status': 'completed',
            'created_at': smoke.SYNTHETIC_TIMESTAMP, 'updated_at': smoke.SYNTHETIC_TIMESTAMP,
        }, 'images': [smoke.RESULT + '/' + task_id + '.png']}


@pytest.mark.parametrize('owner', ['series', 'global'])
def test_real_t2i_batch_keeps_prior_choice_and_explicit_selection_has_no_submit(harness, owner):
    smoke, root, static, evidence, _ = harness
    kind = smoke.OWNER_ASSETS[owner]
    app = smoke.create_smoke_app(root, static, evidence, owner=owner)
    with TestClient(app) as http:
        initial = http.get('/projects/p')
        before = asset(initial, kind)
        settings = initial.json()['model_settings']
        assert settings['t2i_model'] == 'gpt-image-2'
        assert settings[kind + '_aspect_ratio'] == ('16:9' if kind == 'scene' else '1:1')
        body = command(kind, 'gpt-t2i-browser-batch')
        assert [row['status'] for row in receipt(http.post(PUBLIC, json=body))['members']] == ['pending'] * 2
        request = receipt(http.get(PUBLIC + '/' + body['client_request_id']))
        assert [row['status'] for row in request['members']] == ['completed'] * 2
        assert len({row['variant_id'] for row in request['members']}) == 2
        assert len({row['output_ref'] for row in request['members']}) == 2
        after = asset(http.get('/projects/p'), kind)
        old = before['image_asset']['variants'][0]
        by_id = {row['id']: row for row in after['image_asset']['variants']}
        assert len(after['image_asset']['variants']) == 3
        assert set(by_id) == {smoke.OLD_ID, *(row['variant_id'] for row in request['members'])}
        assert by_id[smoke.OLD_ID] == old and old['is_favorited'] is True
        assert after['image_asset']['selected_id'] == smoke.OLD_ID
        assert after['image_url'] == before['image_url'] == smoke.OLD_REF
        assert after['image_request_id'] == request['id']
        for field in set(before) - {'image_asset', 'image_url', 'image_request_id', 'status'}:
            assert after[field] == before[field]
        for member in request['members']:
            assert by_id[member['variant_id']]['url'] == member['output_ref']
        assert_actual_t2i(app, owner, smoke, root, request, evidence, submitted=2, completed=2)
        new_id = request['members'][0]['variant_id']
        chosen = asset(http.post('/projects/p/assets/variant/select', json={
            'asset_id': 'c', 'asset_type': kind, 'generation_type': 'all', 'variant_id': new_id,
        }), kind)
        assert chosen['image_asset']['selected_id'] == new_id
        assert chosen['image_url'] == by_id[new_id]['url']
        assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 2, 'fetch': 2}
        smoke.validate_seed_media(root)


def test_global_unknown_original_key_new_app_reads_without_another_post(harness):
    smoke, root, static, evidence, path = harness
    body = command('prop', 'gpt-t2i-browser-unknown')
    app = smoke.create_smoke_app(root, static, evidence, owner='global')
    with TestClient(app) as http:
        before = asset(http.get('/projects/p'), 'prop')
        assert http.post('/__smoke/control', json={'action': 'lose_next_provider_ack'}).status_code == 200
        assert http.post(PUBLIC, json=body).status_code == 200
        original = receipt(http.get(PUBLIC + '/' + body['client_request_id']))
        assert [row['status'] for row in original['members']] == ['needs_confirmation', 'cancelled']
        after = asset(http.get('/projects/p'), 'prop')
        assert after['image_asset'] == before['image_asset']
        assert after['image_url'] == before['image_url'] == smoke.OLD_REF
        assert_actual_t2i(app, 'global', smoke, root, original, evidence, submitted=1, completed=0)
    restored = smoke.open_evidence(path, resume=True)
    smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=True)
    reopened_app = smoke.create_smoke_app(root, static, restored, owner='global', resume=True)
    with TestClient(reopened_app) as http:
        for _ in range(2):
            reopened = receipt(http.get(PUBLIC + '/' + body['client_request_id']))
            assert reopened == original
            current = asset(http.get('/projects/p'), 'prop')
            assert current['image_asset'] == before['image_asset']
            assert current['image_url'] == before['image_url']
        assert_actual_t2i(reopened_app, 'global', smoke, root, original, restored, submitted=1, completed=0)
        smoke.validate_seed_media(root)


def test_transport_and_controls_reject_foreign_inputs_and_restore_originals(harness):
    import requests
    from src.models import mulerouter

    smoke, root, static, evidence, _ = harness
    originals = requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        with requests.Session() as session:
            assert session.trust_env is False
            with pytest.raises(RuntimeError, match='Unexpected HTTP'):
                session.post('https://unhandled.invalid/anything')
            with pytest.raises(RuntimeError, match='Unexpected HTTP'):
                session.post(smoke.GENERATION, headers={'Authorization': 'Bearer foreign'}, json={})
            with pytest.raises(RuntimeError, match='Unknown external'):
                session.get(smoke.GENERATION + '/foreign', headers={'Authorization': 'Bearer ' + smoke.SECRET})
            with pytest.raises(RuntimeError, match='Cookies'):
                session.get(smoke.RESULT + '/foreign.png', headers={'Cookie': 'synthetic=1'})
            with pytest.raises(RuntimeError, match='Unexpected GPT T2I'):
                session.post(smoke.GENERATION, headers={'Authorization': 'Bearer ' + smoke.SECRET}, json={
                    'prompt': PROMPT, 'size': '1536x1024', 'quality': 'high', 'n': 1, 'images': [],
                })
            with pytest.raises(RuntimeError, match='Unexpected HTTP'):
                session.post(smoke.GENERATION.replace('/generation', '/edit'),
                             headers={'Authorization': 'Bearer ' + smoke.SECRET}, json={})
        for body in [{'action': 'hold_next_project_get', 'path': '/projects/foreign'},
                     {'action': 'mark_completed'}, {'action': 'set_receipt', 'id': 'invented'}]:
            assert http.post('/__smoke/control', json=body).status_code == 422
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
    assert (requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess) == originals


@pytest.mark.parametrize('change', ['owner', 'seed'])
def test_resume_rejects_owner_or_existing_output_png_change(harness, change):
    smoke, root, static, evidence, path = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner='series')):
        pass
    restored = smoke.open_evidence(path, resume=True)
    if change == 'owner':
        with pytest.raises(ValueError, match='Resume'):
            smoke.create_smoke_app(root, static, restored, owner='global', resume=True)
    else:
        (root / 'output' / smoke.OLD_REF).write_bytes(b'changed negative fixture')
        with pytest.raises(ValueError, match='PNG changed'):
            smoke.create_smoke_app(root, static, restored, owner='series', resume=True)
