"""Selected-local Qwen three-view preserves the existing Studio sync lifecycle."""

import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import Script, Series
from src.apps.comic_gen.pipeline import ComicGenPipeline
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_image_known_handle_recovery as known
from tests import test_studio_three_view_core as three
from tests import test_qwen20_studio_image as qwen
from tests.frozen_image_fixtures import rgb_png_bytes

transport = known.transport
VERSION = 'provider-qwen20-i2i-bound-http-v1'
SHEET_NEGATIVE = ', background, scenery, landscape, shadows, complex background, text, watermark, messy, distorted, extra limbs'


@pytest.mark.parametrize('model,route,configured_size,expected_size', [
    ('qwen-image-2.0', 'local', None, '1280*1280'),
    ('qwen/qwen-image-2.0-pro#image', 'series', '1536*1024', '1536*1024'),
])
def test_public_qwen_three_view_freezes_legacy_parameters_and_preserves_author_selection_on_reopen(
    tmp_path, transport, model, route, configured_size, expected_size,
):
    config = {'assets': {'model': {'params': {'size': configured_size}}}} if configured_size is not None else {}
    item = ComicGenPipeline(config=config, data_dir=tmp_path / 'output')
    try:
        item.scripts['p'] = Script.model_validate({
            'id': 'p', 'title': 'Qwen three-view', 'original_text': 'Synthetic', 'created_at': 1, 'updated_at': 1,
        })
        item.series_store['s'] = Series.model_validate({
            'id': 's', 'title': 'Shared cast', 'created_at': 1, 'updated_at': 1,
        })
        target = known.setup(item, 'edit', route)
        target.three_view_image_url = 'uploads/b3-old.png'
        root = item.scripts['p']
        root.model_settings.i2i_model = model
        root.art_direction = qwen.style()
        item._commit_storage('projects.json', 'series.json', 'library_assets.json')
        original = (item.data_dir / three.SELECTED_REF).read_bytes()
        protected = target.model_dump(mode='json', include=three.PROTECTED_FIELDS)
        assert item.asset_generator.model.params.get('size', '1280*1280') == expected_size
        raw = {**known.body('edit'), 'apply_style': True, 'negative_prompt': 'draft-negative',
               'seed': 0, 'watermark': route == 'series'}
        expected_params = {'size': expected_size, 'n': 1, 'seed': 0, 'watermark': raw['watermark'],
                           'prompt_extend': True, 'negative_prompt': 'draft-negative, style-negative' + SHEET_NEGATIVE}
        qwen.queue_images(transport)

        def change_mutable_inputs(request):
            if request.method == 'POST':
                (item.data_dir / three.SELECTED_REF).write_bytes(rgb_png_bytes('changed mutable source'))
                item.asset_generator.model.params['size'] = '2048*1024'

        transport['on_send'] = change_mutable_inputs
        with TestClient(three.app_for(item)) as client:
            http.enable_fake_key(client.app)
            accepted = client.post(three.PUBLIC, json=raw)
            assert accepted.status_code == 200, accepted.text
            transport['on_send'] = None
            owner = admission.owner(item, route)
            record = owner.asset_image_requests[0]
            assert (record.request_root_kind, record.request_root_id) == ('project', 'p')
            assert (record.owner_kind, record.owner_id) == (('script', 'p') if route == 'local' else ('series', 's'))
            assert record.base_prompt == raw['prompt'] and record.channel == 'three_view'
            assert record.entrypoint == three.ENTRYPOINT and len(record.members) == 1
            execution = record.members[0].execution
            assert execution.schema_version == 2 and execution.handle is None and execution.phase == 'completed'
            assert execution.intent['intent_contract_version'] == VERSION and execution.intent['mode'] == 'i2i'
            assert execution.intent['params'] == {key: value for key, value in expected_params.items() if key != 'n'}
            assert execution.media[0]['sha256'] == hashlib.sha256(original).hexdigest()
            assert execution.prepared_artifact == execution.artifact and execution.artifact is not None
            assert execution.output_ref is not None
            assert hashlib.sha256((item.data_dir / execution.output_ref).read_bytes()).hexdigest() == execution.artifact['sha256']
            assert json.loads(transport['calls'][0][0].body) == {
                'model': model.removeprefix('qwen/').removesuffix('#image'),
                'input': {'messages': [{'role': 'user', 'content': [
                    {'image': 'data:image/png;base64,' + base64.b64encode(original).decode('ascii')},
                    {'text': raw['prompt'] + ', style-positive'},
                ]}]},
                'parameters': expected_params,
            }
            submit, download = [request for request, _ in transport['calls']]
            assert submit.method == 'POST' and submit.url.endswith('/multimodal-generation/generation')
            assert 'X-DashScope-Async' not in submit.headers
            assert download.method == 'GET' and 'Authorization' not in download.headers
            target = owner.characters[0]
            assert target.three_view_asset is not None and target.three_views is not None
            assert target.three_view_asset.selected_id == target.three_views.selected_image_id == record.members[0].variant_id
            assert target.three_view_image_url == execution.output_ref
            assert target.model_dump(mode='json', include=three.PROTECTED_FIELDS) == protected

            # The author changes the selected candidate after the paid result.
            item.select_asset_variant('p', 'c', 'character', 'old', generation_type='three_view')
            before = known.gpt.persisted(item)
            read = client.get(three.PUBLIC + '/' + raw['client_request_id'])
            replay = client.post(three.PUBLIC, json=raw)
            assert read.status_code == replay.status_code == 200
            assert replay.json()['replayed'] and read.json()['request'] == replay.json()['request']
            assert known.gpt.persisted(item) == before and len(transport['calls']) == 2
        with known.reopened(item) as other, TestClient(three.app_for(other)) as client:
            restored = other.asset_image_service.read_request('project', 'p', raw['client_request_id'])
            assert restored.request.model_dump(mode='json') == record.model_dump(mode='json')
            assert other.asset_image_service.run_original_request(record.id) is None
            assert client.post(three.PUBLIC, json=raw).json()['replayed']
            target = admission.owner(other, route).characters[0]
            assert target.three_view_asset is not None and target.three_views is not None
            assert target.three_view_asset.selected_id == target.three_views.selected_image_id == 'old'
            assert target.three_view_image_url == 'uploads/b3-old.png'
            assert any(value.id == record.members[0].variant_id for value in target.three_view_asset.variants)
            assert target.model_dump(mode='json', include=three.PROTECTED_FIELDS) == protected
            assert known.gpt.persisted(other) == before
        assert len(transport['calls']) == 2 and not transport['queue'] and not transport['cli']
    finally:
        transport['on_send'] = None
        item.close()
