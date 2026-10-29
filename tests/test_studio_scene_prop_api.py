"""Existing project/series strict endpoints expose scene/prop receipts safely."""

import pytest
import requests
from fastapi.testclient import TestClient

from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as old_http
from tests import test_studio_core_image_worker as worker
from tests import test_studio_scene_prop_service as focused

runtime = admission.runtime
bound_transport = worker.bound_transport


@pytest.mark.parametrize('kind', focused.KINDS)
@pytest.mark.parametrize('route', admission.ROUTES)
@pytest.mark.parametrize('outcome', ['completed', 'unknown'])
def test_existing_real_http_endpoints_admit_replay_and_read_actual_owner(runtime, bound_transport, kind, route, outcome):
    (root_kind, root_id), _ = focused.setup(runtime, kind, route)
    focused.queue_success(bound_transport, 2)
    if outcome == 'unknown':
        bound_transport['queue'] = [requests.Timeout('synthetic lost response')]
    raw = {'client_request_id': f'{kind}-request', 'asset_id': 'c', 'asset_type': kind,
           'generation_type': 'all', 'batch_size': 2, 'apply_style': False, 'seed': 0, 'watermark': False}
    path = f"/{'projects' if root_kind == 'project' else 'series'}/{root_id}/asset_image_requests"
    app = old_http.real_app(runtime)
    with TestClient(app) as client:
        old_http.enable_fake_key(app)
        initial = client.post(path, json=raw)
        assert initial.status_code == 200, initial.text
        first = initial.json()
        assert first['request']['entrypoint'] == 'studio.asset.scene_prop.v1'
        assert first['request']['asset_type'] == kind and first['request']['channel'] == 'image'
        assert [m['status'] for m in first['request']['members']] == ['pending', 'pending']
        reads = {name: (runtime.data_dir / name).read_bytes() for name in ('projects.json', 'series.json', 'library_assets.json')}
        recovered = client.get(path + f'/{kind}-request')
        assert recovered.status_code == 200, recovered.text
        record = recovered.json()['request']
        assert record['id'] == first['request']['id']
        expected = ['completed', 'completed'] if outcome == 'completed' else ['needs_confirmation', 'cancelled']
        assert [m['status'] for m in record['members']] == expected
        assert ('output_ref' in record['members'][0]) == (outcome == 'completed')
        assert 'credential_ref' not in str(record) and 'https://' not in str(record)
        replay = client.post(path, json=raw)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request'] == record
        assert client.get(path + '/missing').status_code == 404
        assert focused.post_count(bound_transport) == (2 if outcome == 'completed' else 1)
        assert reads == {name: (runtime.data_dir / name).read_bytes() for name in reads}
        conflict = client.post(path, json={**raw, 'asset_type': 'scene' if kind == 'prop' else 'prop'})
        assert conflict.status_code == 409
