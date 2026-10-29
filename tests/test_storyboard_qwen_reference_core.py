"""A current local character reference must survive the real frame API."""

import base64
import hashlib
import json
import struct

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import ImageAsset, ImageVariant
from tests import test_storyboard_qwen20_core as frames
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_worker as worker
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = frames.runtime
transport = frames.transport


@pytest.mark.parametrize('entry', ['legacy', 'command'])
def test_frame_keeps_its_selected_local_character_reference_in_qwen_core(runtime, transport, entry):
    reference = 'uploads/character-sheet.png'
    original = rgb_png_bytes('character source')
    source = runtime.data_dir / reference
    source.write_bytes(original)
    character = runtime.scripts['p'].characters[0]
    character.three_view_asset = ImageAsset(selected_id='sheet', variants=[ImageVariant.model_validate({'id': 'sheet', 'url': reference})])
    runtime._commit_storage('projects.json')
    raw = frames.command(runtime)
    frames.queue_image(transport)

    def mutate_source(request):
        if request.method == 'POST':
            source.write_bytes(rgb_png_bytes('later source bytes'))

    transport['on_send'] = mutate_source
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        path = '/projects/p/asset_image_requests' if entry == 'command' else '/projects/p/storyboard/render'
        body = raw if entry == 'command' else {'frame_id': 'f', 'composition_data': {}, 'prompt': raw['prompt']}
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        saved = runtime.read_committed_script('p')
        assert len(saved.asset_image_requests) == 1
        record = saved.asset_image_requests[0]
        execution = record.members[0].execution
        assert (record.asset_type, record.channel, execution.phase) == ('storyboard_frame', 'image', 'completed')
        assert execution.intent['intent_contract_version'] == 'provider-qwen20-i2i-bound-http-v1'
        assert execution.intent['mode'] == 'i2i'
        assert execution.media[0]['sha256'] == hashlib.sha256(original).hexdigest()
        payload = json.loads(transport['calls'][0][0].body)
        assert payload['input']['messages'][0]['content'] == [
            {'image': 'data:image/png;base64,' + base64.b64encode(original).decode()},
            {'text': 'Author prompt Characters: Alice (red coat).'},
        ]
        assert payload['parameters'] == {'size': '1024*576', 'watermark': False, 'prompt_extend': True, 'n': 1}
        assert saved.frames[0].image_url == execution.output_ref
        output = (runtime.data_dir / execution.output_ref).read_bytes()
        assert hashlib.sha256(output).hexdigest() == execution.artifact['sha256']
        assert output[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', output[16:24]) == (16, 16)
        assert len(transport['calls']) == 2 and not transport['queue']
        if entry == 'command':
            before = frames.persisted(runtime)
            assert client.post(path, json=raw).json()['replayed'] is True
            assert frames.persisted(runtime) == before and len(transport['calls']) == 2
    transport['on_send'] = None
    with worker.restarted(runtime) as other:
        before = frames.persisted(other)
        result = other.asset_image_service.read_request('project', 'p', record.client_request_id)
        assert result.request.model_dump(mode='json') == record.model_dump(mode='json')
        assert other.asset_image_service.run_original_request(record.id) is None
        assert frames.persisted(other) == before and len(transport['calls']) == 2
