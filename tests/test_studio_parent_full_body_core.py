"""Local mother selection uses existing full-body Edit ownership and recovery."""

import hashlib

from fastapi.testclient import TestClient

from src.apps.comic_gen.models import Character
from tests import test_studio_full_body_reference_core as full_body
from tests import test_studio_image_known_handle_recovery as known
from tests import test_studio_three_view_core as three
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = full_body.runtime
transport = full_body.transport


def setup_parent(item):
    before = full_body.setup_reference(item, 'local', 'gpt-image-2')
    child = item.scripts['p'].characters[0]
    child.base_character_id = 'mother'
    parent_ref = 'assets/characters/mother_fullbody_selected-take.png'
    original = rgb_png_bytes('selected mother take', width=32, height=32)
    path = item.data_dir / parent_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original)
    variant = {'id': 'mother-take', 'url': parent_ref, 'created_at': 1, 'is_favorited': True}
    mother = Character.model_validate({
        'id': 'mother', 'name': 'Alice', 'description': 'original mother',
        'full_body_asset': {'selected_id': 'mother-take', 'variants': [variant]},
        'full_body': {'selected_image_id': 'mother-take', 'image_variants': [variant]},
        'full_body_image_url': parent_ref,
    })
    item.scripts['p'].characters.append(mother)
    item._commit_storage('projects.json')
    assert not (item.data_dir / 'assets/characters/mother_fullbody.png').exists()
    assert original != before['reference']
    body = full_body.strict_body(before, reference_variant_id='mother-take', reference_image_url=parent_ref)
    return before, mother, original, path, body


def test_parent_selected_local_full_body_enters_existing_gpt_edit_core(runtime, transport):
    before, mother, original, path, body = setup_parent(runtime)
    original_mother = mother.model_dump(mode='json')
    full_body.gpt_sheet.queue_images(transport, 'http', count=1)

    def change_source(request):
        if request.method == 'POST':
            path.write_bytes(rgb_png_bytes('same parent URL changed bytes'))

    transport['on_send'] = change_source
    with TestClient(three.app_for(runtime)) as client:
        full_body.gpt_sheet.configure(client.app, 'http')
        result = client.post(three.PUBLIC, json=body)
        assert result.status_code == 200, result.text
        record = runtime.scripts['p'].asset_image_requests[0]
        assert record.channel == 'full_body' and record.owner_kind == 'script' and record.owner_id == 'p'
        execution = record.members[0].execution
        assert execution.intent['intent_contract_version'] == full_body.CONTRACT
        assert execution.media[0]['sha256'] == hashlib.sha256(original).hexdigest()
        assert execution.phase == 'completed' and execution.artifact == execution.prepared_artifact
        assert execution.artifact is not None and execution.output_ref is not None
        assert hashlib.sha256((runtime.data_dir / execution.output_ref).read_bytes()).hexdigest() == execution.artifact['sha256']
        full_body.assert_edit_wire(transport, original, full_body.REVERSE + full_body.PROMPT, '1024x1536', 1)
        assert mother.model_dump(mode='json') == original_mother
        runtime.select_asset_variant('p', 'c', 'character', before['selected'], generation_type='full_body')
        replay = client.post(three.PUBLIC, json=body)
        assert replay.status_code == 200 and replay.json()['replayed']
        assert len(transport['calls']) == 3
        persisted = full_body.gpt_sheet.persisted(runtime)
    transport['on_send'] = None
    with known.reopened(runtime) as reopened, TestClient(three.app_for(reopened)) as client:
        assert reopened.asset_image_service.run_original_request(record.id) is None
        restored = reopened.asset_image_service.read_request('project', 'p', body['client_request_id'])
        assert restored.request.model_dump(mode='json') == record.model_dump(mode='json')
        assert client.post(three.PUBLIC, json=body).json()['replayed']
        child = reopened.scripts['p'].characters[0]
        assert child.full_body_asset is not None and child.full_body is not None
        assert child.full_body_asset.selected_id == child.full_body.selected_image_id == before['selected']
        assert any(value.id == record.members[0].variant_id for value in child.full_body_asset.variants)
        assert reopened.scripts['p'].characters[1].model_dump(mode='json') == original_mother
        assert full_body.gpt_sheet.persisted(reopened) == persisted
    assert len(transport['calls']) == 3


def test_legacy_full_body_button_enters_parent_edit_without_fixed_filename(runtime, transport):
    _, mother, original, _, body = setup_parent(runtime)
    original_mother = mother.model_dump(mode='json')
    full_body.gpt_sheet.queue_images(transport, 'http', count=1)
    legacy = {key: value for key, value in body.items()
              if key not in {'client_request_id', 'reference_variant_id', 'reference_image_url'}}
    with TestClient(three.app_for(runtime)) as client:
        full_body.gpt_sheet.configure(client.app, 'http')
        result = client.post(full_body.OLD_ROUTE, json=legacy)
        assert result.status_code == 200, result.text
        record = runtime.scripts['p'].asset_image_requests[0]
        assert record.id == result.json()['_task_id']
        assert record.members[0].execution.phase == 'completed'
        assert record.members[0].execution.intent['intent_contract_version'] == full_body.CONTRACT
        full_body.assert_edit_wire(transport, original, full_body.REVERSE + full_body.PROMPT, '1024x1536', 1)
        assert mother.model_dump(mode='json') == original_mother


def test_parent_request_cannot_substitute_child_upload_for_saved_mother_choice(runtime, transport):
    before, _, _, _, body = setup_parent(runtime)
    body.update(reference_variant_id=before['reference_id'], reference_image_url=before['reference_url'])
    persisted = full_body.gpt_sheet.persisted(runtime)
    with TestClient(three.app_for(runtime)) as client:
        full_body.gpt_sheet.configure(client.app, 'http')
        result = client.post(three.PUBLIC, json=body)
        assert result.status_code == 422, result.text
        assert not runtime.scripts['p'].asset_image_requests and not transport['calls']
        assert full_body.gpt_sheet.persisted(runtime) == persisted
