"""Real HTTP/Core fixture self-checks; browser and journal acceptance are separate."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient


SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_studio_full_body_browser_smoke.py'
PUBLIC = '/projects/p/asset_image_requests'
PROMPT = 'Alice standing at ease, full body, keep the red coat and leather boots.'
CAPTURE_MARKER = 'LUMENX_FULL_BODY_BROWSER_EMPTY_SELECTION_WIRE='
ROOT_FIELDS = (
    'id', 'revision', 'title', 'original_text', 'created_at', 'updated_at', 'series_id', 'read_versions',
    'model_settings', 'art_direction', 'style_preset', 'style_prompt', 'workflow_mode', 'status',
    'frames', 'scenes', 'props', 'video_tasks',
)
ASSET_FIELDS = (
    'id', 'name', 'description', 'source', 'status', 'base_character_id', 'voice_id',
    'reference_sheet', 'reference_sheet_request_id', 'image_url', 'full_body', 'full_body_asset',
    'full_body_image_url', 'full_body_prompt', 'full_body_updated_at', 'full_body_request_id',
    'full_body_motion_request_id', 'three_views', 'three_view_asset', 'three_view_image_url',
    'three_view_prompt', 'three_view_updated_at', 'three_view_request_id', 'head_shot', 'headshot_asset',
    'headshot_image_url', 'headshot_prompt', 'headshot_updated_at', 'headshot_request_id', 'avatar_url',
    'is_consistent', 'media_refs',
)
PRIVATE_FIELDS = {
    'credential_ref', 'Authorization', 'execution', 'execution_kind', 'intent', 'prepared_artifact',
    'artifact', 'intent_contract_version', 'input_guard', 'projection_guard', 'asset_image_requests',
    'provider_request_receipts',
}
SOURCE_FILES = (
    'docs/agents/tools/lumenx_studio_full_body_browser_smoke.py',
    'tests/test_lumenx_studio_full_body_browser_smoke.py',
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
    spec = importlib.util.spec_from_file_location('full_body_browser_smoke', SCRIPT)
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


def command(key, batch=2, *, source='first', prompt=PROMPT):
    return {
        'client_request_id': key, 'asset_type': 'character', 'asset_id': 'c', 'generation_type': 'full_body',
        'reference_variant_id': 'view-' + source, 'reference_image_url': 'uploads/view-' + source + '.png',
        'apply_style': False, 'prompt': prompt, 'batch_size': batch,
    }


def character(response):
    assert response.status_code == 200, response.text
    rows = [row for row in response.json()['characters'] if row['id'] == 'c']
    assert len(rows) == 1
    return rows[0]


def receipt(response):
    assert response.status_code == 200, response.text
    return response.json()['request']


def saved_owner(app, owner):
    pipeline = app.state.pipeline
    if owner == 'local':
        return pipeline._load_data()['p']
    if owner == 'series':
        return pipeline._load_series_data()['s']
    return pipeline._load_library_data()


def assert_dual_history(before, after, request, selected):
    legacy, modern = after['full_body_asset'], after['full_body']
    old = {row['id']: row for row in before['full_body_asset']['variants']}
    old.update({row['id']: row for row in before['full_body']['image_variants']})
    expected_ids = set(old) | {row['variant_id'] for row in request['members'] if row['status'] == 'completed'}
    legacy_by_id = {row['id']: row for row in legacy['variants']}
    modern_by_id = {row['id']: row for row in modern['image_variants']}
    assert len(legacy['variants']) == len(modern['image_variants']) == len(expected_ids)
    assert set(legacy_by_id) == expected_ids and legacy_by_id == modern_by_id
    assert {key: legacy_by_id[key] for key in old} == old
    assert legacy['selected_id'] == modern['selected_image_id'] == selected
    assert after['full_body_image_url'] == (legacy_by_id[selected]['url'] if selected else None)
    assert after['full_body_request_id'] == request['id']
    assert after['full_body_prompt'] == modern['image_prompt'] == PROMPT
    for field in ('selected_video_id', 'video_variants', 'video_prompt', 'video_updated_at'):
        assert modern[field] == before['full_body'][field]
    for field in ('three_view_asset', 'three_views', 'three_view_image_url', 'headshot_asset', 'head_shot',
                  'headshot_image_url', 'avatar_url', 'full_body_motion_request_id'):
        assert after[field] == before[field]


def assert_actual_v2(app, owner, smoke, root, public, evidence, *, count, completed):
    from src.utils.media_probe import probe_image_file

    stored = saved_owner(app, owner)
    assert len(stored.asset_image_requests) == 1
    record = stored.asset_image_requests[0]
    assert record.id == public['id']
    assert (public['entrypoint'], public['channel'], public['request_root_kind'], public['request_root_id']) == (
        'studio.asset.full_body.v1', 'full_body', 'project', 'p',
    )
    assert (public['owner_kind'], public['owner_id']) == {
        'local': ('script', 'p'), 'series': ('series', 's'), 'global': ('global', 'library'),
    }[owner]
    reference = (root / 'output' / smoke.SOURCES[0]).read_bytes()
    for member in record.members:
        execution = member.execution
        assert execution.intent['intent_contract_version'] == 'provider-gpt-image2-edit-bound-v2'
        assert execution.intent['mode'] == 'i2i'
        assert execution.intent['params'] == {'size': smoke.SIZE}
        assert execution.intent['prompt'] == smoke.REVERSE + PROMPT
        assert execution.media[0]['sha256'] == hashlib.sha256(reference).hexdigest()
        assert execution.media[0]['size_bytes'] == len(reference)
        if execution.phase == 'completed':
            assert execution.output_ref and execution.artifact
            output = root / 'output' / execution.output_ref
            assert hashlib.sha256(output.read_bytes()).hexdigest() == execution.artifact['sha256']
            dimensions = probe_image_file(output)
            assert (dimensions.width, dimensions.height) == (160, 240)
    posts = [row for row in evidence.payload['fake_provider']['events'] if row['kind'] == 'submit']
    assert len(posts) == count
    expected = {'prompt': smoke.REVERSE + PROMPT, 'size': smoke.SIZE, 'n': 1,
                'images': ['data:image/png;base64,' + base64.b64encode(reference).decode()]}
    assert all(row['payload'] == expected and row['reference'] == smoke.SOURCES[0] for row in posts)
    assert evidence.payload['fake_provider']['counts'] == {'submit': count, 'query': completed, 'fetch': completed}


def assert_public(value):
    if isinstance(value, dict):
        assert not PRIVATE_FIELDS.intersection(value)
        for child in value.values():
            assert_public(child)
    elif isinstance(value, list):
        for child in value:
            assert_public(child)


def capture_event(label, method, path, response, *, request_body=None):
    assert response.status_code == 200, response.text
    raw = response.json()
    event = {'label': label, 'method': method, 'path': path, 'status_code': response.status_code,
             'body': deepcopy(raw), 'raw_response_sha256': hashlib.sha256(response.content).hexdigest()}
    if request_body is not None:
        event['request_body'] = deepcopy(request_body)
    if path == '/projects/p':
        rows = [row for row in raw['characters'] if row['id'] == 'c']
        assert len(rows) == 1
        event['body'] = {field: deepcopy(raw[field]) for field in ROOT_FIELDS if field in raw}
        event['body']['characters'] = [{field: deepcopy(rows[0][field]) for field in ASSET_FIELDS if field in rows[0]}]
        event['projection'] = {
            'rule': 'Copy only present actual HTTP fields; never add defaults or rewrite values.',
            'root_fields': list(ROOT_FIELDS), 'asset_selector': 'characters[id=c]', 'asset_fields': list(ASSET_FIELDS),
        }
        projected = json.dumps(event['body'], ensure_ascii=False, sort_keys=True)
        event['projected_response_text'] = projected
        event['projected_response_sha256'] = hashlib.sha256(projected.encode()).hexdigest()
    else:
        event['raw_response_text'] = response.text
    assert_public(event['body'])
    return event


def emit_capture(name, before, accepted, result, after, replay, body):
    path = PUBLIC + '/' + body['client_request_id']
    events = [
        capture_event('before_project', 'GET', '/projects/p', before),
        capture_event('accepted_post', 'POST', PUBLIC, accepted, request_body=body),
        capture_event('result_get', 'GET', path, result),
        capture_event('after_project', 'GET', '/projects/p', after),
        capture_event('original_replay', 'POST', PUBLIC, replay, request_body=body),
    ]
    print('\n' + CAPTURE_MARKER + json.dumps({
        'schema_version': 1, 'case': name, 'events': events, 'source_sha256': source_bindings(),
    }, ensure_ascii=False))


@pytest.mark.parametrize('owner', ['local', 'series', 'global'])
def test_real_v2_batch_preserves_old_selection_and_both_histories(harness, owner):
    smoke, root, static, evidence, _ = harness
    app = smoke.create_smoke_app(root, static, evidence, owner=owner)
    with TestClient(app) as http:
        initial = http.get('/projects/p')
        before = character(initial)
        settings = initial.json()['model_settings']
        assert (settings['t2i_model'], settings['i2i_model'], settings['character_aspect_ratio']) == (
            'wan2.7-image', 'gpt-image-2', '9:16',
        )
        assert before['three_view_asset']['selected_id'] == 'view-second'
        body = command('full-body-browser-batch')
        assert [row['status'] for row in receipt(http.post(PUBLIC, json=body))['members']] == ['pending'] * 2
        request = receipt(http.get(PUBLIC + '/' + body['client_request_id']))
        assert [row['status'] for row in request['members']] == ['completed'] * 2
        after = character(http.get('/projects/p'))
        assert_dual_history(before, after, request, smoke.OLD_ID)
        assert_actual_v2(app, owner, smoke, root, request, evidence, count=2, completed=2)
        new_id = request['members'][0]['variant_id']
        chosen = character(http.post('/projects/p/assets/variant/select', json={
            'asset_id': 'c', 'asset_type': 'character', 'generation_type': 'full_body', 'variant_id': new_id,
        }))
        assert chosen['full_body_asset']['selected_id'] == chosen['full_body']['selected_image_id'] == new_id
        selected = next(row for row in chosen['full_body_asset']['variants'] if row['id'] == new_id)
        assert chosen['full_body_image_url'] == selected['url']
        assert evidence.payload['fake_provider']['counts']['submit'] == 2
        smoke.validate_seed_media(root)


def test_real_completed_empty_prior_batch_selects_first_actual_member(harness):
    smoke, root, static, evidence, _ = harness
    app = smoke.create_smoke_app(root, static, evidence, owner='series', initial_selection=None)
    with TestClient(app) as http:
        before = http.get('/projects/p')
        target = character(before)
        assert target['full_body_asset']['selected_id'] is target['full_body']['selected_image_id'] is None
        assert target['full_body_image_url'] is None
        body = command('full-body-browser-empty-complete')
        accepted = http.post(PUBLIC, json=body)
        assert [row['status'] for row in receipt(accepted)['members']] == ['pending'] * 2
        result = http.get(PUBLIC + '/' + body['client_request_id'])
        request = receipt(result)
        assert [row['status'] for row in request['members']] == ['completed'] * 2
        after = http.get('/projects/p')
        assert_dual_history(target, character(after), request, request['members'][0]['variant_id'])
        assert_actual_v2(app, 'series', smoke, root, request, evidence, count=2, completed=2)
        replay = http.post(PUBLIC, json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request'] == request
        assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 2, 'fetch': 2}
        emit_capture('series_empty_complete', before, accepted, result, after, replay, body)


def test_real_partial_empty_prior_batch_stays_unselected(harness, monkeypatch):
    smoke, root, static, evidence, _ = harness
    original_submit = smoke.Controls.submit

    def lose_second(controls, payload, reference):
        if controls.evidence.payload['fake_provider']['counts']['submit'] == 1:
            controls.control({'action': 'lose_next_provider_ack'})
        return original_submit(controls, payload, reference)

    # Temporary external transport fault only; no product method or receipt is replaced.
    with monkeypatch.context() as patch:
        patch.setattr(smoke.Controls, 'submit', lose_second)
        app = smoke.create_smoke_app(root, static, evidence, owner='series', initial_selection=None)
        with TestClient(app) as http:
            before = http.get('/projects/p')
            target = character(before)
            assert target['full_body_asset']['selected_id'] is target['full_body']['selected_image_id'] is None
            assert target['full_body_image_url'] is None
            body = command('full-body-browser-empty-partial', 3)
            accepted = http.post(PUBLIC, json=body)
            assert [row['status'] for row in receipt(accepted)['members']] == ['pending'] * 3
            result = http.get(PUBLIC + '/' + body['client_request_id'])
            request = receipt(result)
            assert [row['status'] for row in request['members']] == ['completed', 'needs_confirmation', 'cancelled']
            after = http.get('/projects/p')
            assert_dual_history(target, character(after), request, None)
            assert_actual_v2(app, 'series', smoke, root, request, evidence, count=2, completed=1)
            replay = http.post(PUBLIC, json=body)
            assert replay.status_code == 200 and replay.json()['replayed'] is True
            assert replay.json()['request'] == request
            assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 1, 'fetch': 1}
            emit_capture('series_empty_partial', before, accepted, result, after, replay, body)
    assert smoke.Controls.submit is original_submit


def test_unknown_original_key_reopens_and_replays_without_another_submit(harness):
    smoke, root, static, evidence, evidence_path = harness
    body = command('full-body-browser-unknown', 1)
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner='global')) as http:
        assert http.post('/__smoke/control', json={'action': 'lose_next_provider_ack'}).status_code == 200
        assert http.post(PUBLIC, json=body).status_code == 200
        original = receipt(http.get(PUBLIC + '/' + body['client_request_id']))
        assert [row['status'] for row in original['members']] == ['needs_confirmation']
        assert character(http.get('/projects/p'))['full_body_asset']['selected_id'] == smoke.OLD_ID
        assert evidence.payload['fake_provider']['counts'] == {'submit': 1, 'query': 0, 'fetch': 0}
    restored = smoke.open_evidence(evidence_path, resume=True)
    smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=True)
    with TestClient(smoke.create_smoke_app(root, static, restored, owner='global', resume=True)) as http:
        reopened = receipt(http.get(PUBLIC + '/' + body['client_request_id']))
        assert reopened['id'] == original['id']
        assert [row['status'] for row in reopened['members']] == ['needs_confirmation']
        replay = http.post(PUBLIC, json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request'] == reopened
        assert character(http.get('/projects/p'))['full_body_asset']['selected_id'] == smoke.OLD_ID
        assert restored.payload['fake_provider']['counts'] == {'submit': 1, 'query': 0, 'fetch': 0}


def test_original_get_bytes_survive_real_preferred_source_deletion(harness):
    smoke, root, static, evidence, _ = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner='local', hold_seconds=10)) as http:
        assert http.post('/__smoke/control', json={'action': 'hold_next_project_get'}).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(http.get, '/projects/p')
            try:
                deadline = time.monotonic() + 5
                while not http.get('/__smoke/state').json()['get_held'] and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert http.get('/__smoke/state').json()['get_held'] is True and not pending.done()
                changed = character(http.post('/projects/p/assets/variant/delete', json={
                    'asset_id': 'c', 'asset_type': 'character', 'variant_id': 'view-first',
                }))
                assert [row['id'] for row in changed['three_view_asset']['variants'] if row['is_uploaded_source']] == ['view-second']
            finally:
                assert http.post('/__smoke/control', json={'action': 'release_project_get'}).status_code == 200
            released = pending.result(timeout=5)
        old = character(released)
        assert [row['id'] for row in old['three_view_asset']['variants'] if row['is_uploaded_source']] == ['view-first', 'view-second']
        held = [row for row in evidence.payload['controls'] if row['action'] == 'project_get_held']
        assert len(held) == 1 and held[0]['response_sha256'] == hashlib.sha256(released.content).hexdigest()
        stale = http.post(PUBLIC, json=command('full-body-browser-stale', 1))
        assert stale.status_code == 422, stale.text
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
        fresh = command('full-body-browser-current', 1, source='second', prompt='Current author draft, full body.')
        assert http.post(PUBLIC, json=fresh).status_code == 200
        result = receipt(http.get(PUBLIC + '/' + fresh['client_request_id']))
        assert [row['status'] for row in result['members']] == ['completed']
        submissions = [row for row in evidence.payload['fake_provider']['events'] if row['kind'] == 'submit']
        assert len(submissions) == 1 and submissions[0]['reference'] == smoke.SOURCES[1]
        assert submissions[0]['payload']['prompt'] == smoke.REVERSE + fresh['prompt']
        smoke.validate_seed_media(root)


def test_controls_reject_foreign_paths_or_receipt_changes(harness):
    smoke, root, static, evidence, _ = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        for payload in [{'action': 'hold_next_project_get', 'path': '/projects/foreign'},
                        {'action': 'mark_completed'}, {'action': 'set_receipt', 'id': 'invented'}]:
            assert http.post('/__smoke/control', json=payload).status_code == 422
        assert http.post('/__smoke/control', json={'action': 'lose_next_provider_ack'}).status_code == 200
        assert http.post('/__smoke/control', json={'action': 'lose_next_provider_ack'}).status_code == 422
        assert evidence.payload['fake_provider']['counts']['submit'] == 0


def test_transport_rejects_unbound_requests_and_restores_originals(harness):
    import requests
    from src.models import mulerouter

    smoke, root, static, evidence, _ = harness
    original = requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess
    with TestClient(smoke.create_smoke_app(root, static, evidence)):
        with requests.Session() as session:
            assert session.trust_env is False
            with pytest.raises(RuntimeError, match='Unexpected HTTP'):
                session.post('https://unhandled.invalid/anything')
            with pytest.raises(RuntimeError, match='Unexpected HTTP'):
                session.post(smoke.EDIT, headers={'Authorization': 'Bearer foreign'}, json={})
            with pytest.raises(RuntimeError, match='Unknown external'):
                session.get(smoke.EDIT + '/foreign', headers={'Authorization': 'Bearer ' + smoke.SECRET})
            with pytest.raises(RuntimeError, match='Cookies'):
                session.get(smoke.RESULT + '/foreign.png', headers={'Cookie': 'synthetic=1'})
            with pytest.raises(RuntimeError, match='Unexpected full-body'):
                session.post(smoke.EDIT, headers={'Authorization': 'Bearer ' + smoke.SECRET}, json={
                    'prompt': smoke.REVERSE + PROMPT, 'size': '1024x1024', 'n': 1, 'images': [],
                })
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
    assert (requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess) == original


def test_resume_rejects_owner_initial_selection_or_original_png_change(harness):
    smoke, root, static, evidence, path = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner='local')):
        pass
    restored = smoke.open_evidence(path, resume=True)
    with pytest.raises(ValueError, match='Resume'):
        smoke.create_smoke_app(root, static, restored, owner='global', resume=True)
    with pytest.raises(ValueError, match='Resume'):
        smoke.create_smoke_app(root, static, restored, owner='local', initial_selection=None, resume=True)
    (root / 'output' / smoke.SOURCES[0]).write_bytes(b'changed negative fixture')
    with pytest.raises(ValueError, match='PNG changed'):
        smoke.create_smoke_app(root, static, restored, owner='local', resume=True)


def test_shutdown_releases_owned_pending_hold(harness):
    smoke, _, _, evidence, _ = harness
    controls = smoke.Controls(evidence, 'local', 1)
    controls.control({'action': 'hold_next_project_get'})
    held = controls.take_hold()
    assert held is not None and not held['gate'].is_set()
    controls.shutdown()
    assert held['gate'].is_set()
    with pytest.raises(ValueError, match='closing'):
        controls.control({'action': 'lose_next_provider_ack'})
