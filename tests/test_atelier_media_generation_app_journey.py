"""Real public admission, dispatcher, Core codecs and restart; fake transport only."""
from pathlib import Path
import wave

import pytest
from fastapi.testclient import TestClient

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.comic_gen.api import create_app
from src.models import provider_media_execution as core
from src.utils.media_probe import probe_image_file, probe_media_file
from tests.frozen_image_fixtures import rgb_png_bytes


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_public_media_dispatch_publishes_decodable_bytes_and_reopens_without_provider(tmp_path, monkeypatch, kind):
    calls = []

    class ImageProvider:
        def __init__(self, config, *, env):
            assert env == {}

        def generate_with_bound_http(self, prompt, output, credential, **kwargs):
            assert credential.secret == 'journey-private-secret'
            assert kwargs['image_contract_version'] == 'provider-qwen20-t2i-bound-http-v1'
            assert kwargs['model_name'] == 'qwen-image-2.0' and kwargs['seed'] == 0
            assert kwargs['watermark'] is False and kwargs['prompt_extend'] is True
            calls.append(('image', prompt))
            Path(output).write_bytes(rgb_png_bytes('journey-output'))

    class SpeechProvider:
        def __init__(self, **kwargs):
            assert kwargs['api_key'] == 'journey-private-secret'

        def execute(self, prepared, output):
            calls.append(('audio', prepared.text))
            with wave.open(str(output), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b'\x00\x00' * 1600)

    monkeypatch.setattr(core, 'WanxImageModel', ImageProvider)
    monkeypatch.setattr(core, 'TTSProcessor', SpeechProvider)
    payload = {'expected_revision': 0, 'client_request_id': f'{kind}-journey'}
    if kind == 'image':
        payload.update(model_id='qwen/qwen-image-2.0#image', mode='t2i', prompt='A glass moon', params={'seed': 0}, batch_size=1)
    else:
        payload.update(voice='longanyang', text='A glass moon.', params={})
    path = f'/atelier/projects/p/nodes/{kind}/{kind}_candidates'
    request_path = f'/atelier/projects/p/generation/requests/{kind}-journey'
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        app.state.runtime_environment.update({'DASHSCOPE_API_KEY': 'journey-private-secret'}, persist=lambda *_: None)
        app.state.atelier_storage.create_project(AtelierProject(id='p', title='Media journey', created_at=1, updated_at=1,
            nodes=[AtelierNode(id=kind, project_id='p', type=kind)]))
        admitted = client.post(path, json=payload)
        assert admitted.status_code == 200, admitted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        result = client.get(request_path)
        assert result.status_code == 200, result.text
        attempt = result.json()['members'][0]['attempt']
        assert attempt['status'] == 'completed', attempt
        media = tmp_path / attempt['output_ref']
        metadata = probe_image_file(media) if kind == 'image' else probe_media_file(media, 'audio')
        assert metadata.kind == kind
        assert result.json()['project']['nodes'][0]['media_urls'] == []
        candidate_id = result.json()['members'][0]['candidate']['id']
        candidate_path = f'/atelier/projects/p/nodes/{kind}/media_candidates/{candidate_id}'
        selected = client.post(f'{candidate_path}/select', json={'expected_revision': result.json()['project']['revision']})
        assert selected.status_code == 200, selected.text
        assert selected.json()['project']['nodes'][0]['media_urls'] == [attempt['output_ref']]
        hidden = client.post(f'{candidate_path}/visibility', json={'expected_revision': selected.json()['project']['revision'], 'visible': False})
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()['project']['nodes'][0]['media_urls'] == []
        restored = client.post(f'{candidate_path}/visibility', json={'expected_revision': hidden.json()['project']['revision'], 'visible': True})
        assert restored.status_code == 200, restored.text
        assert restored.json()['project']['nodes'][0]['media_urls'] == []
        assert len(calls) == 1
        assert 'journey-private-secret' not in result.text
        assert app.state.pipeline.read_committed_projects() == []

    # No runtime key is supplied to the fresh app. Receipt replay must return
    # the existing outcome before binding any credentials or scheduling again.
    reopened = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(reopened) as client:
        readback = client.get(request_path)
        assert readback.status_code == 200, readback.text
        assert readback.json()['members'][0]['attempt'] == attempt
        replay = client.post(path, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()['replayed'] and replay.json()['new_candidate_ids'] == []
        assert reopened.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert len(calls) == 1
