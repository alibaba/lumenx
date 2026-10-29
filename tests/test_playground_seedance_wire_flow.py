"""Seedance HTTP product flow with explicit fake external transport."""
from __future__ import annotations

import hashlib
import inspect

import pytest
from fastapi.testclient import TestClient

from tests import test_playground_kling_wire_flow as wire
from tests.test_playground_kling_wire_flow import _wait_generation, _write_image
from tests.test_lumenx_generation_browser_smoke import evidence_payload, load_smoke_module, write_static_root

smoke_module = wire.smoke_module


def test_seedance_smoke_requires_explicit_flag():
    smoke = load_smoke_module()
    assert 'fake_seedance' in inspect.signature(smoke.create_smoke_app).parameters
    assert inspect.signature(smoke.create_smoke_app).parameters['fake_seedance'].default is False


@pytest.mark.parametrize('mode', ['t2v', 'i2v', 'r2v'])
def test_seedance_http_flow_freezes_order_then_recovers_original_receipt(tmp_path, monkeypatch, smoke_module, mode):
    smoke = smoke_module
    root = tmp_path / 'data'
    root.mkdir()
    refs = [] if mode == 't2v' else [_write_image(root/'output', 'one.png')]
    if mode == 'r2v':
        refs.append(_write_image(root/'output', 'two.png'))
    hashes = [hashlib.sha256((root/'output'/ref).read_bytes()).hexdigest() for ref in refs]
    initial = evidence_payload('disabled')
    initial['fake_upload'] = {'counts': {'upload': 0}, 'events': []}
    evidence = smoke.Evidence(tmp_path/'evidence.json', initial)
    def forbid_upload(*args, **kwargs):
        raise AssertionError('Seedance data URI inputs must not invoke an uploader')
    monkeypatch.setattr('src.models.provider_upload.upload_dashscope_temporary_media', forbid_upload)
    app = smoke.create_smoke_app(root, write_static_root(tmp_path), evidence, fake_seedance=True)
    payload = {'client_request_id': f'seedance-{mode}', 'mode': mode, 'model_id': f'seedance-2.0-{mode}',
               'prompt': 'A frozen local story', 'input_media': refs,
               'parameters': {'seed': 0, 'audio': False}, 'batch_size': 2}
    with TestClient(app) as http:
        cap = http.get('/playground/capabilities')
        assert cap.status_code == 200 and cap.json()['seedance']['available'] is True
        response = http.post('/playground/generate', json=payload)
        assert response.status_code == 200, response.text
        gen = _wait_generation(http, payload['client_request_id'])
        assert gen['status'] == 'completed', gen
        assert len(gen['outputs']) == 2
        for output in gen['outputs']:
            assert http.get('/files/'+output['media_path']).content == b'fake-transport-result'
        for ref in refs:
            (root/'output'/ref).write_bytes(b'changed after acceptance')
        app.state.runtime_environment.update({}, remove_keys=['MULEROUTER_API_KEY'], persist=lambda *args: None)
        assert http.get('/playground/capabilities').json()['seedance']['available'] is False
        fresh = http.post('/playground/generate', json={**payload, 'client_request_id': f'blocked-{mode}'})
        assert fresh.status_code == 503 and fresh.json()['detail']['code'] == 'playground_generation_not_admitted'
        replay = http.post('/playground/generate', json=payload)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['generation'] == gen
        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)
    assert evidence.payload['fake_provider']['counts'] == {'submit':2,'query':2,'fetch':2}
    assert evidence.payload['fake_upload']['counts'] == {'upload':0}
    for event in evidence.payload['fake_provider']['events']:
        if event['kind'] != 'submit':
            continue
        request = event['request_summary']
        assert request['provider_model_id'] == payload['model_id']
        assert request['scope']['gateway'] == 'mulerouter_http'
        assert [x['sha256'] for x in request['media']] == hashes
        assert [x['ordinal'] for x in request['media']] == list(range(len(hashes)))
        assert all(x['transport_binding'] == 'data_uri_sha256_v1' for x in request['media'])
        expected = {'duration':5,'resolution':'1080p','aspect_ratio':'16:9','audio':False,'seed':0}
        if mode == 't2v':
            expected['watermark'] = False
        assert request['intent']['params'] == expected


def test_seedance_fake_does_not_enable_other_models_or_cli(tmp_path, smoke_module):
    smoke = smoke_module
    for enabled in [False, True]:
        root = tmp_path / str(enabled)
        root.mkdir()
        evidence = smoke.Evidence(tmp_path/f'evidence-{enabled}.json', evidence_payload('disabled'))
        app = smoke.create_smoke_app(root, write_static_root(tmp_path), evidence, fake_seedance=enabled)
        with TestClient(app):
            selector = app.state.provider_runtime_selector
            for mode in ['t2v','i2v','r2v']:
                assert (selector.for_admission(provider_model_id=f'seedance-2.0-{mode}',gateway='mulerouter').runtime is not None) is enabled
            for model, gateway in [('seedance-2.0-fast-t2v','mulerouter'),('gpt-image-2','mulerouter'),('seedance-2.0-t2v','mulerun_cli')]:
                assert selector.for_admission(provider_model_id=model,gateway=gateway).runtime is None
