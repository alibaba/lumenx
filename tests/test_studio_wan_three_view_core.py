"""Wan three-view uses the existing Studio/Core lifecycle and real frozen input."""
import base64
from copy import deepcopy
import hashlib
import json

import pytest
import requests

from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import ArtDirection, Script, Series
from src.apps.comic_gen.pipeline import ComicGenPipeline
from tests import test_studio_core_image_admission as admission
from tests import test_studio_image_known_handle_recovery as known
from tests import test_studio_three_view_core as three
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = known.runtime
transport = known.transport
waiting_clock = known.waiting_clock
VERSION = 'provider-wan27-i2i-bound-http-v1'


def setup(item, model='wan2.7-image', route='local', width=240):
    target = known.setup(item, 'edit', route)
    item.scripts['p'].model_settings.i2i_model = model
    original = rgb_png_bytes('original Wan selected full body', width=width, height=240)
    (item.data_dir / three.SELECTED_REF).write_bytes(original)
    target.three_view_image_url = 'uploads/b3-old.png'
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    return original


def body():
    return {**known.body('edit'), 'seed': 0, 'watermark': False}


def submit_unknown(client, item, state, raw, *, route='local'):
    state['queue'].extend([known.receipt('wan'), requests.ConnectionError('synthetic query loss')])
    result = client.post(three.PUBLIC, json=raw)
    assert result.status_code == 200, result.text
    saved = known.disk(item, route)
    assert saved['members'][0]['execution']['phase'] == 'submitted'
    return saved


@pytest.mark.parametrize('model,route', [('wan2.7-image', 'local'), ('wan/wan2.7-image-pro#image', 'series')])
def test_selected_reference_frozen_and_original_handle_recovered_without_changing_author_choice(runtime, transport, model, route):
    original = setup(runtime, model, route)
    target = admission.owner(runtime, route).characters[0]
    assert target.three_view_asset is not None
    old_selected = target.three_view_asset.selected_id
    old_variants = [v.model_dump(mode='json') for v in target.three_view_asset.variants]
    protected = target.model_dump(mode='json', include=three.PROTECTED_FIELDS)
    raw = body()
    raw['watermark'] = route == 'series'
    raw['model_name'] = 'wan2.7-image-pro' if route == 'series' else 'wan/wan2.7-image#image'
    def change_source(prepared):
        if prepared.method == 'POST':
            (runtime.data_dir / three.SELECTED_REF).write_bytes(rgb_png_bytes('mutable source B'))
    transport['on_send'] = change_source
    with known.client_for(runtime, 'wan') as client:
        saved = submit_unknown(client, runtime, transport, raw, route=route)
        payload = json.loads(transport['calls'][0][0].body)
        assert payload['model'] == ('wan2.7-image-pro' if route == 'series' else 'wan2.7-image')
        assert payload['input']['messages'][0]['content'] == [
            {'image': 'data:image/png;base64,' + base64.b64encode(original).decode('ascii')},
            {'text': raw['prompt']},
        ]
        assert payload['parameters'] == {'size': '1280*1280', 'watermark': raw['watermark'], 'seed': 0, 'n': 1}
        execution = saved['members'][0]['execution']
        assert execution['intent']['intent_contract_version'] == VERSION
        assert execution['media'][0]['sha256'] == hashlib.sha256(original).hexdigest()
        assert saved['owner_kind'] == ('script' if route == 'local' else 'series')
        replay = client.post(three.PUBLIC, json=raw)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert known.counts(transport) == (1, 1, 0)
    transport['on_send'] = None
    with known.reopened(runtime) as other, known.client_for(other, 'wan'):
        result = known.resume(other, transport, saved, 'wan')
        assert result.request.members[0].execution.phase == 'completed'
        target = admission.owner(other, route).characters[0]
        assert target.three_view_asset is not None and target.three_views is not None
        assert target.three_view_asset.selected_id == target.three_views.selected_image_id == old_selected
        assert target.three_view_image_url == 'uploads/b3-old.png'
        images = {v.id: v.model_dump(mode='json') for v in target.three_view_asset.variants}
        for old in old_variants:
            assert images[old['id']] == old
        assert result.request.members[0].variant_id in images
        assert target.model_dump(mode='json', include=three.PROTECTED_FIELDS) == protected
        assert known.counts(transport) == (1, 2, 1)


def test_legacy_three_view_entry_routes_selected_local_wan_through_same_core(runtime, transport):
    setup(runtime)
    transport['queue'].extend([known.receipt('wan'), known.observation('wan'), known.response(known.PNG)])
    with known.client_for(runtime, 'wan') as client:
        raw = {k: v for k, v in body().items() if k not in {'client_request_id', 'reference_variant_id', 'reference_image_url', 'seed', 'watermark'}}
        result = client.post('/projects/p/assets/generate', json=raw)
        assert result.status_code == 200, result.text
        saved = known.disk(runtime)
        assert saved['channel'] == 'three_view'
        assert saved['members'][0]['execution']['intent']['intent_contract_version'] == VERSION
        assert saved['members'][0]['execution']['phase'] == 'completed'
        assert known.counts(transport) == (1, 1, 1)


@pytest.mark.parametrize('fault', ['small_reference', 'model_mismatch', 'aspect_ratio', 'negative_prompt', 'style_negative', 'base_character'])
def test_unrepresentable_wan_three_view_input_rejects_before_admission_or_payment(runtime, transport, fault):
    setup(runtime, width=239 if fault == 'small_reference' else 240)
    raw = body()
    if fault == 'model_mismatch':
        raw['model_name'] = 'wan2.7-image-pro'
    elif fault == 'aspect_ratio':
        raw['aspect_ratio'] = '9:16'
    elif fault == 'negative_prompt':
        raw['negative_prompt'] = 'must not disappear'
    elif fault == 'style_negative':
        raw['apply_style'] = True
        runtime.scripts['p'].art_direction = ArtDirection.model_validate({
            'selected_style_id': 'custom', 'style_config': {'positive_prompt': 'kept style', 'negative_prompt': 'do not drop'},
        })
        runtime._commit_storage('projects.json')
    elif fault == 'base_character':
        runtime.scripts['p'].characters[0].base_character_id = 'original-base'
        runtime._commit_storage('projects.json')
    before = known.gpt.persisted(runtime)
    with known.client_for(runtime, 'wan') as client:
        result = client.post(three.PUBLIC, json=raw)
        assert result.status_code == 422, result.text
        assert not runtime.scripts['p'].asset_image_requests and not transport['calls']
        assert known.gpt.persisted(runtime) == before


def test_missing_wan_edit_marker_keeps_original_query_but_never_reconstructs_or_projects(runtime, transport):
    setup(runtime)
    with known.client_for(runtime, 'wan') as client:
        saved = submit_unknown(client, runtime, transport, body())
        retained = runtime.scripts['p'].asset_image_requests[0].members[0].execution
        fingerprint, handle = retained.fingerprint, deepcopy(retained.handle)
        del retained.intent['intent_contract_version']
        runtime._commit_storage('projects.json')
    with known.reopened(runtime) as other, known.client_for(other, 'wan'):
        before = other.scripts['p'].characters[0].model_dump(mode='json')
        result = known.resume(other, transport, saved, 'wan')
        execution = result.request.members[0].execution
        assert execution.fingerprint == fingerprint and execution.handle == handle
        assert 'intent_contract_version' not in execution.intent
        assert execution.phase == 'observed' and execution.artifact is None and execution.prepared_artifact is not None
        assert result.reason == 'studio_image_material_incomplete'
        assert other.scripts['p'].characters[0].model_dump(mode='json') == before
        assert AssetImageRequestRecord.model_validate_json(result.request.model_dump_json()) == result.request
        assert known.counts(transport) == (1, 2, 1)


@pytest.mark.parametrize('configured_size,expected_size', [(None, '1280*1280'), ('1536*1024', '1536*1024')])
def test_legacy_effective_size_is_frozen_before_submit_and_survives_configuration_change(
    tmp_path, transport, configured_size, expected_size,
):
    config = {'assets': {'model': {'params': {'size': configured_size}}}} if configured_size is not None else {}
    item = ComicGenPipeline(config=config, data_dir=tmp_path / 'configured-output')
    try:
        item.scripts['p'] = Script.model_validate({'id': 'p', 'title': 'Configured dimensions', 'original_text': 'Synthetic', 'created_at': 1, 'updated_at': 1})
        item.series_store['s'] = Series.model_validate({'id': 's', 'title': 'Shared cast', 'created_at': 1, 'updated_at': 1})
        setup(item)
        assert item.asset_generator.model.params.get('size', '1280*1280') == expected_size
        with known.client_for(item, 'wan') as client:
            saved = submit_unknown(client, item, transport, body())
            payload = json.loads(transport['calls'][0][0].body)
            assert payload['parameters']['size'] == expected_size
            assert saved['members'][0]['execution']['intent']['params']['size'] == expected_size
            item.asset_generator.model.params['size'] = '2048*1024'
            replay = client.post(three.PUBLIC, json=body())
            assert replay.status_code == 200 and replay.json()['replayed'] is True
            assert known.counts(transport) == (1, 1, 0)
        with known.reopened(item) as other, known.client_for(other, 'wan'):
            assert other.asset_generator.model.params.get('size', '1280*1280') == '1280*1280'
            result = known.resume(other, transport, saved, 'wan')
            execution = result.request.members[0].execution
            assert execution.phase == 'completed'
            assert execution.intent['params']['size'] == expected_size
            assert execution.handle == saved['members'][0]['execution']['handle']
            assert known.counts(transport) == (1, 2, 1)
    finally:
        item.close()


def test_illegal_configured_wan_size_rejects_before_admission_or_payment(runtime, transport):
    setup(runtime)
    with known.client_for(runtime, 'wan') as client:
        runtime.asset_generator.model.params['size'] = '1*1'
        before = known.gpt.persisted(runtime)
        result = client.post(three.PUBLIC, json=body())
        assert result.status_code == 422, result.text
        assert not runtime.scripts['p'].asset_image_requests and not transport['calls']
        assert known.gpt.persisted(runtime) == before
