"""Scene/prop browser fixtures share the real original HTTP and restart machinery."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

from fastapi.testclient import TestClient
import pytest

from tests import test_lumenx_studio_image_browser_smoke as original

harness = original.harness


def submit(http, fixture, kind, key, batch=2, *, standalone=False):
    prefix = f"/series/{fixture['owner_id']}" if standalone else f"/projects/{fixture['project_id']}"
    return http.post(prefix + '/asset_image_requests', json={
        'client_request_id': key, 'asset_id': fixture[f'{kind}_id'], 'asset_type': kind,
        'generation_type': 'all', 'batch_size': batch, 'apply_style': False, 'seed': 0, 'watermark': False,
    })


def asset(pipeline, fixture, kind):
    owner = (pipeline.scripts[fixture['owner_id']] if fixture['owner_kind'] == 'script' else
             pipeline.series_store[fixture['owner_id']] if fixture['owner_kind'] == 'series' else pipeline.library_store)
    return next(v for v in getattr(owner, f'{kind}s') if v.id == fixture[f'{kind}_id'])


@pytest.mark.parametrize('kind', ['scene', 'prop'])
@pytest.mark.parametrize('route', ['local', 'series', 'global', 'standalone'])
def test_scene_prop_fixture_real_http_preserves_original_media_and_owner(harness, kind, route):
    smoke, root, static, evidence, _ = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        f = http.get('/__smoke/state').json()['fixtures']['series' if route == 'standalone' else route]
        before = asset(app.state.pipeline, f, kind).model_dump()
        response = submit(http, f, kind, 'fixture-image', standalone=route == 'standalone')
        assert response.status_code == 200, response.text
        prefix = f"/series/{f['owner_id']}" if route == 'standalone' else f"/projects/{f['project_id']}"
        request = http.get(prefix + '/asset_image_requests/fixture-image').json()['request']
        assert (request['owner_kind'], request['owner_id']) == (f['owner_kind'], f['owner_id'])
        assert [m['status'] for m in request['members']] == ['completed', 'completed']
        target = asset(app.state.pipeline, f, kind)
        assert target.image_asset.selected_id == 'choice'
        assert len(target.image_asset.variants) == 4
        assert [v.model_dump() for v in target.image_asset.variants[-2:]] == before['image_asset']['variants']
        assert [v.id for v in target.image_asset.variants[:2]] == [m['variant_id'] for m in reversed(request['members'])]
        assert target.image_url == before['image_url'] and target.video_prompt == before['video_prompt']
        assert all(http.get('/files/' + m['output_ref']).content.startswith(b'\x89PNG') for m in request['members'])
        assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 2, 'fetch': 2}
        assert evidence.payload['blocked_sockets'] == []


@pytest.mark.parametrize('kind', ['scene', 'prop'])
def test_unknown_second_scene_prop_stops_third_and_remains_inert_after_restart(harness, kind):
    smoke, root, static, evidence, evidence_path = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app) as http:
        f = http.get('/__smoke/state').json()['fixtures']['local']
        http.post('/__smoke/control', json={'action': 'arm', 'mode': 'pause_second_submit'})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(submit, http, f, kind, 'interrupted-image', batch=3)
            try:
                original.wait_paused(http)
            finally:
                http.post('/__smoke/control', json={'action': 'interrupt_submit'})
            assert future.result(timeout=5).status_code == 200
        assert evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 1, 'fetch': 1}
    restored_evidence = smoke.open_evidence(evidence_path, resume=True)
    restored = smoke.create_smoke_app(root, static, restored_evidence, resume=True)
    with TestClient(restored) as http:
        record = http.get(f"/projects/{f['project_id']}/asset_image_requests/interrupted-image").json()['request']
        assert [m['status'] for m in record['members']] == ['completed', 'needs_confirmation', 'cancelled']
        assert restored_evidence.payload['fake_provider']['counts'] == {'submit': 2, 'query': 1, 'fetch': 1}
        assert asset(restored.state.pipeline, f, kind).image_asset.selected_id == 'choice'
        assert restored.state.pipeline.asset_generation_tasks == {}


@pytest.mark.parametrize('kind', ['scene', 'prop'])
def test_scene_prop_completion_checkpoint_repairs_locally(harness, kind):
    smoke, root, static, evidence, evidence_path = harness
    app = smoke.create_smoke_app(root, static, evidence)
    with TestClient(app, raise_server_exceptions=False) as http:
        f = http.get('/__smoke/state').json()['fixtures']['local']
        before = asset(app.state.pipeline, f, kind).model_dump(mode='json')
        retained_bytes = {v['url']: (root / 'output' / v['url']).read_bytes()
                          for v in before['image_asset']['variants']}
        http.post('/__smoke/control', json={'action': 'arm', 'mode': 'fail_completion'})
        assert submit(http, f, kind, 'image-proof', batch=1).status_code == 200
        assert app.state.pipeline._storage_failed is not None
        saved = json.loads((root / 'output/projects.json').read_text())[f['project_id']]['asset_image_requests'][0]
        member = saved['members'][0]
        execution = member['execution']
        assert execution['schema_version'] == 3 and execution['phase'] == 'observed'
        assert execution['handle']['task_id'] == 'studio-image-1'
        assert execution['observation']['status'] == 'succeeded'
        assert execution['observation']['source_handle'] == execution['handle']
        proof = execution['prepared_artifact']
        assert proof is not None
        output = root / 'output' / execution['output_ref']
        generated_bytes = output.read_bytes()
        assert hashlib.sha256(generated_bytes).hexdigest() == proof['sha256']
    counts = dict(evidence.payload['fake_provider']['counts'])
    assert counts == {'submit': 1, 'query': 1, 'fetch': 1}
    restored_evidence = smoke.open_evidence(evidence_path, resume=True)
    restored = smoke.create_smoke_app(root, static, restored_evidence, resume=True)
    with TestClient(restored) as http:
        record = http.get(f"/projects/{f['project_id']}/asset_image_requests/image-proof").json()['request']
        assert record['members'][0]['status'] == 'completed'
        target = asset(restored.state.pipeline, f, kind)
        assert target.image_asset is not None
        assert target.image_asset.selected_id == before['image_asset']['selected_id'] == 'choice'
        assert len(target.image_asset.variants) == 3
        history = [v.model_dump(mode='json') for v in target.image_asset.variants]
        assert history[:2] == before['image_asset']['variants']
        added = [v for v in history if v['id'] == member['variant_id']]
        assert len(added) == 1
        assert added[0] == {
            'id': member['variant_id'], 'url': execution['output_ref'], 'created_at': saved['created_at'],
            'prompt_used': execution['intent']['prompt'], 'is_favorited': False,
            'is_uploaded_source': False, 'upload_type': None,
        }
        assert record['members'][0]['variant_id'] == member['variant_id']
        assert record['members'][0]['output_ref'] == execution['output_ref']
        after = target.model_dump(mode='json')
        assert all(after[field] == before[field] for field in ('name', 'description', 'image_url', 'video_prompt'))
        assert output.read_bytes() == generated_bytes
        assert all((root / 'output' / ref).read_bytes() == content for ref, content in retained_bytes.items())
        repaired = json.loads((root / 'output/projects.json').read_text())[f['project_id']]['asset_image_requests'][0]
        repaired_execution = repaired['members'][0]['execution']
        assert repaired_execution['phase'] == 'completed' and repaired_execution['artifact'] == proof
        assert repaired_execution['prepared_artifact'] == proof
        assert repaired_execution['handle'] == execution['handle']
        assert repaired_execution['observation'] == execution['observation']
        assert restored_evidence.payload['fake_provider']['counts'] == counts
        assert restored_evidence.payload['blocked_sockets'] == []
