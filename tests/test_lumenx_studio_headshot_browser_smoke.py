"""Real HTTP/Core self-check, not browser acceptance."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient


SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_studio_headshot_browser_smoke.py'


@pytest.fixture
def harness(tmp_path, monkeypatch):
    # The pytest guard is already installed; disable dotenv explicitly just as
    # the browser bootstrap does, without changing any guard permission.
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    spec = importlib.util.spec_from_file_location('headshot_browser_smoke', SCRIPT)
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
    return smoke, root, mounted, evidence, path


def command(key, batch=2):
    return {
        'client_request_id': key, 'asset_type': 'character', 'asset_id': 'c',
        'generation_type': 'headshot', 'reference_variant_id': 'selected-c',
        'reference_image_url': 'uploads/selected-c.png', 'apply_style': False,
        'prompt': 'Keep the face and red collar; crop to face and shoulders.', 'batch_size': batch,
    }


def character(response):
    assert response.status_code == 200, response.text
    return next(row for row in response.json()['characters'] if row['id'] == 'c')


@pytest.mark.parametrize('owner', ['local', 'series', 'global'])
def test_actual_bound_edit_keeps_dual_history_and_owner_until_explicit_selection(harness, owner):
    from src.utils.media_probe import probe_image_file

    smoke, root, static, evidence, _ = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner=owner)) as http:
        before = character(http.get('/projects/p'))
        post = http.post('/projects/p/asset_image_requests', json=command('headshot-browser-batch'))
        assert post.status_code == 200, post.text
        result = http.get('/projects/p/asset_image_requests/headshot-browser-batch')
        assert result.status_code == 200, result.text
        receipt = result.json()['request']
        assert (receipt['owner_kind'], receipt['owner_id']) == {
            'local': ('script', 'p'), 'series': ('series', 's'), 'global': ('global', 'library'),
        }[owner]
        assert (receipt['entrypoint'], receipt['request_root_kind'], receipt['request_root_id']) == (
            'studio.asset.headshot.v1', 'project', 'p',
        )
        assert [row['status'] for row in receipt['members']] == ['completed', 'completed']
        after = character(http.get('/projects/p'))
        assert after['headshot_request_id'] == receipt['id']
        legacy, modern = after['headshot_asset'], after['head_shot']
        assert legacy['selected_id'] == modern['selected_image_id'] == 'portrait-old'
        # Legacy history prepends while the modern unit appends; identity and
        # complete member contents must agree without requiring the same order.
        legacy_by_id = {row['id']: row for row in legacy['variants']}
        modern_by_id = {row['id']: row for row in modern['image_variants']}
        assert len(legacy['variants']) == len(modern['image_variants']) == len(legacy_by_id) == len(modern_by_id) == 3
        assert legacy_by_id == modern_by_id
        for field in ('full_body_asset', 'full_body', 'full_body_image_url', 'three_view_asset', 'three_views'):
            assert after[field] == before[field]
        assert legacy_by_id['portrait-old'] == before['headshot_asset']['variants'][0]
        assert legacy_by_id['portrait-old']['is_favorited'] is True
        for member in receipt['members']:
            media = probe_image_file(root / 'output' / member['output_ref'])
            assert (media.width, media.height) == (240, 240)
        submissions = [row for row in evidence.payload['fake_provider']['events'] if row['kind'] == 'submit']
        assert len(submissions) == 2
        selected = (root / 'output/uploads/selected-c.png').read_bytes()
        assert all(row['reference'] == 'uploads/selected-c.png' for row in submissions)
        assert all(base64.b64decode(row['payload']['images'][0].split(',', 1)[1]) == selected for row in submissions)
        assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 2, 'fetch': 2}
        new_id = receipt['members'][0]['variant_id']
        chosen = character(http.post('/projects/p/assets/variant/select', json={
            'asset_id': 'c', 'asset_type': 'character', 'generation_type': 'headshot', 'variant_id': new_id,
        }))
        assert chosen['headshot_asset']['selected_id'] == chosen['head_shot']['selected_image_id'] == new_id
        new_url = next(row['url'] for row in chosen['headshot_asset']['variants'] if row['id'] == new_id)
        assert chosen['headshot_image_url'] == chosen['avatar_url'] == new_url
        assert evidence.payload['fake_provider']['counts']['submit'] == 2
        smoke.validate_seed_media(root)


def test_provider_ack_unknown_reopens_without_another_paid_post(harness):
    smoke, root, static, evidence, evidence_path = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner='global')) as http:
        assert http.post('/__smoke/control', json={'action': 'lose_next_provider_ack'}).status_code == 200
        response = http.post('/projects/p/asset_image_requests', json=command('headshot-browser-unknown', 1))
        assert response.status_code == 200, response.text
        original = http.get('/projects/p/asset_image_requests/headshot-browser-unknown').json()
        assert [row['status'] for row in original['request']['members']] == ['needs_confirmation']
        assert character(http.get('/projects/p'))['headshot_asset']['selected_id'] == 'portrait-old'
        assert evidence.payload['fake_provider']['counts'] == {'submit': 1, 'query': 0, 'fetch': 0}
    restored_evidence = smoke.open_evidence(evidence_path, resume=True)
    smoke.prepare_root(root, static, frontend_commit='fixture-front', backend_commit='fixture-back', resume=True)
    with TestClient(smoke.create_smoke_app(root, static, restored_evidence, owner='global', resume=True)) as http:
        reopened = http.get('/projects/p/asset_image_requests/headshot-browser-unknown')
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()['request']['id'] == original['request']['id']
        assert [row['status'] for row in reopened.json()['request']['members']] == ['needs_confirmation']
        assert restored_evidence.payload['fake_provider']['counts'] == {'submit': 1, 'query': 0, 'fetch': 0}
        assert character(http.get('/projects/p'))['headshot_asset']['selected_id'] == 'portrait-old'
        replay = http.post('/projects/p/asset_image_requests', json=command('headshot-browser-unknown', 1))
        assert replay.status_code == 200, replay.text
        assert replay.json()['replayed'] is True
        assert replay.json()['request'] == reopened.json()['request']
        assert restored_evidence.payload['fake_provider']['counts'] == {'submit': 1, 'query': 0, 'fetch': 0}


def test_exact_get_hold_returns_original_bytes_after_a_real_source_selection(harness):
    smoke, root, static, evidence, _ = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner='local', hold_seconds=10)) as http:
        assert http.post('/__smoke/control', json={'action': 'hold_next_project_get'}).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(http.get, '/projects/p')
            try:
                deadline = time.monotonic() + 5
                while not http.get('/__smoke/state').json()['get_held'] and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert http.get('/__smoke/state').json()['get_held'] is True
                assert not pending.done()
                selected = character(http.post('/projects/p/assets/variant/select', json={
                    'asset_id': 'c', 'asset_type': 'character', 'generation_type': 'full_body', 'variant_id': 'first-b',
                }))
                assert selected['full_body_asset']['selected_id'] == 'first-b'
            finally:
                assert http.post('/__smoke/control', json={'action': 'release_project_get'}).status_code == 200
            released = pending.result(timeout=5)
        assert character(released)['full_body_asset']['selected_id'] == 'selected-c'
        assert character(http.get('/projects/p'))['full_body_asset']['selected_id'] == 'first-b'
        held = [row for row in evidence.payload['controls'] if row['action'] == 'project_get_held']
        assert len(held) == 1 and held[0]['response_sha256'] == hashlib.sha256(released.content).hexdigest()
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}


def test_control_surface_rejects_foreign_paths_or_receipt_changes(harness):
    smoke, root, static, evidence, _ = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence)) as http:
        for payload in [
            {'action': 'hold_next_project_get', 'path': '/projects/foreign'},
            {'action': 'mark_completed'}, {'action': 'set_receipt', 'id': 'invented'},
        ]:
            assert http.post('/__smoke/control', json=payload).status_code == 422
        assert http.post('/__smoke/control', json={'action': 'lose_next_provider_ack'}).status_code == 200
        assert http.post('/__smoke/control', json={'action': 'lose_next_provider_ack'}).status_code == 422
        assert evidence.payload['fake_provider']['counts']['submit'] == 0


def test_transport_rejects_unbound_http_and_restores_existing_boundaries(harness):
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
        assert evidence.payload['fake_provider']['counts'] == {'submit': 0, 'query': 0, 'fetch': 0}
    assert (requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess) == original


def test_resume_rejects_owner_or_original_png_change(harness):
    smoke, root, static, evidence, path = harness
    with TestClient(smoke.create_smoke_app(root, static, evidence, owner='local')):
        pass
    restored = smoke.open_evidence(path, resume=True)
    with pytest.raises(ValueError, match='Resume'):
        smoke.create_smoke_app(root, static, restored, owner='global', resume=True)
    (root / 'output/uploads/selected-c.png').write_bytes(b'changed negative fixture')
    with pytest.raises(ValueError, match='PNG changed'):
        smoke.create_smoke_app(root, static, restored, owner='local', resume=True)


def test_shutdown_releases_an_owned_pending_transport_hold(harness):
    smoke, _, _, evidence, _ = harness
    controls = smoke.Controls(evidence, 'local', 1)
    controls.control({'action': 'hold_next_project_get'})
    held = controls.take_hold()
    assert held is not None and not held['gate'].is_set()
    controls.shutdown()
    assert held['gate'].is_set()
    with pytest.raises(ValueError, match='closing'):
        controls.control({'action': 'lose_next_provider_ack'})
