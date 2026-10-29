"""Actual HTTP receipts must agree with the frontend's committed wire fixture."""

from itertools import count
import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
import requests

from tests import test_studio_core_image_api as old_http
from tests import test_studio_scene_prop_service as scene

runtime = scene.runtime
bound_transport = scene.bound_transport
FIXTURE = Path(__file__).resolve().parents[1] / 'frontend/src/__tests__/fixtures/studio-scene-prop-http.json'


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('route', scene.admission.ROUTES)
@pytest.mark.parametrize('outcome', ['completed', 'unknown'])
def test_actual_http_matches_frontend_wire_fixture(runtime, bound_transport, monkeypatch, tmp_path, kind, route, outcome):
    # Freeze opaque service IDs only; every wire field still comes from the HTTP serializer.
    identifiers = count(1)
    monkeypatch.setattr(scene.module, 'uuid4', lambda: UUID(int=next(identifiers)))
    (root_kind, root_id), _ = scene.setup(runtime, kind, route)
    scene.queue_success(bound_transport, 2)
    if outcome == 'unknown':
        bound_transport['queue'] = [requests.Timeout('synthetic lost response')]
    command = {
        'client_request_id': f'{kind}-{route}-{outcome}', 'asset_id': 'c',
        'asset_type': kind, 'generation_type': 'all', 'apply_style': False,
        'batch_size': 2, 'seed': 0, 'watermark': False, 'prompt': None,
    }
    path = f"/{'projects' if root_kind == 'project' else 'series'}/{root_id}/asset_image_requests"
    app = old_http.real_app(runtime)
    with TestClient(app) as client:
        old_http.enable_fake_key(app)
        response = client.post(path, json=command)
        assert response.status_code == 200, response.text
        initial = response.json()
        assert [m['status'] for m in initial['request']['members']] == ['pending', 'pending']
        saved = {p.name: p.read_bytes() for p in runtime.data_dir.glob('*.json')}
        response = client.get(f"{path}/{command['client_request_id']}")
        assert response.status_code == 200, response.text
        terminal = response.json()
        expected = ['completed', 'completed'] if outcome == 'completed' else ['needs_confirmation', 'cancelled']
        assert [m['status'] for m in terminal['request']['members']] == expected
        response = client.post(path, json=command)
        assert response.status_code == 200, response.text
        replay = response.json()
        assert replay['replayed'] is True and replay['request'] == terminal['request']
        assert initial['request']['id'] == terminal['request']['id']
        assert scene.post_count(bound_transport) == (2 if outcome == 'completed' else 1)
        assert saved == {p.name: p.read_bytes() for p in runtime.data_dir.glob('*.json')}
    value = {'command': command, 'initial': initial, 'terminal': terminal, 'replay': replay}
    name = f'{kind}-{route}-{outcome}'
    # --keep-temp retains actual output for review without updating the shared fixture.
    (tmp_path / 'http-wire.json').write_text(json.dumps({name: value}, indent=2) + '\n')
    assert value == json.loads(FIXTURE.read_text())[name]
