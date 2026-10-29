"""Explicit manual changes keep the chosen image and its history coherent."""
import json

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import AssetUnit, ImageAsset, Series
from tests import test_asset_variant_commands as variants
from tests import test_studio_image_known_handle_recovery as recovery

project = variants.project
runtime = recovery.runtime
transport = recovery.transport
waiting_clock = recovery.waiting_clock


@pytest.mark.parametrize('kind,series', [('character', False), ('scene', False), ('prop', False), ('character', True)])
def test_manual_http_change_reuses_appends_and_clears_the_real_selection(project, kind, series):
    pipeline, script = project
    collection = {'character': 'characters', 'scene': 'scenes', 'prop': 'props'}[kind]
    asset = getattr(script, collection)[0]
    history = [{'id': 'first', 'url': 'uploads/first.png'}, {'id': 'second', 'url': 'uploads/second.png'}]
    if kind == 'character':
        asset.reference_sheet = AssetUnit.model_validate({'image_variants': history, 'selected_image_id': 'first'})
    else:
        asset.image_asset = ImageAsset.model_validate({'variants': history, 'selected_id': 'first'})
    asset.image_url = 'uploads/first.png'
    if series:
        pipeline.series_store['s'] = Series.model_validate({'id': 's', 'title': 'Shared', 'created_at': 1, 'updated_at': 1,
                                                          collection: [asset.model_dump()]})
    pipeline._commit_storage('projects.json', 'series.json')
    original_history = [v.model_dump(mode='json') for v in pipeline._image_variants(asset.reference_sheet if kind == 'character' else asset.image_asset)]
    root_id, filename = ('s', 'series.json') if series else (script.id, 'projects.json')
    endpoint = f"/{'series' if series else 'projects'}/{root_id}/assets/update_image"
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        for url, count in [('uploads/second.png', 2), ('uploads/third.png', 3), ('uploads/third.png', 3), ('', 3)]:
            response = client.post(endpoint, json={'asset_id': asset.id, 'asset_type': kind, 'image_url': url})
            assert response.status_code == 200, response.text
            stored = json.loads((pipeline.data_dir / filename).read_text())[root_id][collection][0]
            unit = stored['reference_sheet' if kind == 'character' else 'image_asset']
            images = unit['image_variants' if kind == 'character' else 'variants']
            selected = unit['selected_image_id' if kind == 'character' else 'selected_id']
            assert len(images) == count
            assert images[:2] == original_history
            assert stored['image_url'] == url
            if url:
                assert next(image for image in images if image['id'] == selected)['url'] == url
                assert not next(image for image in images if image['id'] == selected)['is_uploaded_source']
            else:
                assert selected is None
            if kind == 'character':
                assert stored['avatar_url'] == url
            returned = response.json()[collection][0]
            assert returned['reference_sheet' if kind == 'character' else 'image_asset']['selected_image_id' if kind == 'character' else 'selected_id'] == selected


def test_manual_selection_survives_core_admission_and_original_task_recovery(runtime, transport):
    recovery.setup(runtime)
    manual_url = 'uploads/manual-choice.png'
    (runtime.data_dir / manual_url).write_bytes(recovery.PNG)
    with recovery.client_for(runtime, 'generation') as client:
        changed = client.post('/projects/p/assets/update_image', json={'asset_id': 'c', 'asset_type': 'character', 'image_url': manual_url})
        assert changed.status_code == 200, changed.text
        asset = runtime.scripts['p'].characters[0]
        assert asset.reference_sheet is not None
        selected = asset.reference_sheet.selected_image_id
        assert next(v for v in asset.reference_sheet.image_variants if v.id == selected).url == manual_url
        saved = recovery.unknown(client, runtime, transport)
    with recovery.reopened(runtime) as other, recovery.client_for(other, 'generation'):
        result = recovery.resume(other, transport, saved)
        assert result.request.members[0].execution.phase == 'completed'
        asset = other.scripts['p'].characters[0]
        assert asset.reference_sheet is not None and asset.reference_sheet.selected_image_id == selected
        assert asset.image_url == asset.avatar_url == manual_url
        assert any(v.id == result.request.members[0].variant_id for v in asset.reference_sheet.image_variants)
        assert recovery.counts(transport) == (1, 2, 1)
