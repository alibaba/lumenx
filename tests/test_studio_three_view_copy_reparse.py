"""Public copy and reparse retain media without transferring image execution."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from tests import test_studio_three_view_core as first
from tests import test_studio_three_view_boundaries as boundary


runtime = first.runtime
transport = first.transport


def png_bytes(item):
    return {str(path.relative_to(item.data_dir)): path.read_bytes()
            for path in item.data_dir.rglob('*.png')}


def generate(client, item, external, route):
    first.gpt_sheet.queue_images(external, 'http', count=2)
    body = first.raw() | {'batch_size': 2}
    response = client.post(first.PUBLIC, json=body)
    assert response.status_code == 200, response.text
    record = boundary.current_record(item, route)
    assert [member.execution.phase for member in record.members] == ['completed', 'completed']
    target = boundary.current_target(item, route)
    assert target.three_view_asset is not None and target.three_views is not None
    assert target.three_view_asset.selected_id == target.three_views.selected_image_id == 'three-view-old-1'
    assert len(target.three_view_asset.variants) == len(target.three_views.image_variants) == 4
    assert next(variant for variant in target.three_view_asset.variants if variant.id == 'three-view-old-0').is_favorited
    assert len(boundary.paid_posts(external)) == 2 and len(external['calls']) == 6
    assert not external['queue'] and not external['cli']
    return body, deepcopy(record.model_dump(mode='json')), deepcopy(target.model_dump(mode='json'))


@pytest.mark.parametrize('route,operation', [('local', 'promote'), ('global', 'fork')])
def test_public_three_view_copy_retains_choice_and_favorites_without_authority(runtime, transport, route, operation):
    first.setup(runtime, route, first.MODELS[1])
    boundary.seed_three_view_history(runtime, route, count=2)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        first.gpt_sheet.configure(app, 'http')
        body, original, source = generate(client, runtime, transport, route)
        media = png_bytes(runtime)
        calls = (len(transport['calls']), len(transport['cli']))
        scope = runtime.scripts['p'].asset_image_request_scope_id
        if operation == 'promote':
            copied_response = client.post('/library/assets/promote', json={
                'source_kind': 'project', 'source_id': 'p', 'asset_type': 'character', 'asset_id': 'c',
            })
            destination = runtime.library_store
        else:
            copied_response = client.post('/projects/p/assets/fork_from_library', json={
                'asset_type': 'character', 'library_asset_id': 'c',
            })
            destination = runtime.scripts['p']
        assert copied_response.status_code == 200, copied_response.text
        copied_id = copied_response.json()['id']
        assert copied_id != 'c'
        copied = next(target for target in destination.characters if target.id == copied_id)
        assert copied.model_dump(mode='json') == dict(source, id=copied_id, three_view_request_id=None)
        assert destination.asset_image_requests == []
        assert runtime.scripts['p'].asset_image_request_scope_id == scope
        assert boundary.current_target(runtime, route).model_dump(mode='json') == source
        assert boundary.current_record(runtime, route).model_dump(mode='json') == original
        observed = client.get(first.PUBLIC + '/' + body['client_request_id'])
        replay = client.post(first.PUBLIC, json=body)
        assert observed.status_code == replay.status_code == 200
        assert replay.json()['replayed'] is True
        assert observed.json()['request'] == replay.json()['request']
        assert png_bytes(runtime) == media
        assert (len(transport['calls']), len(transport['cli'])) == calls


def test_public_reparse_clears_reused_character_pointer_without_restarting_original_three_view(runtime, transport, monkeypatch):
    first.setup(runtime, 'local', first.MODELS[0])
    boundary.seed_three_view_history(runtime, 'local', count=2)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        first.gpt_sheet.configure(app, 'http')
        body, original, source = generate(client, runtime, transport, 'local')
        before = runtime.read_committed_script('p')
        assert before is not None
        media = png_bytes(runtime)
        calls = (len(transport['calls']), len(transport['cli']))
        parsed = before.model_copy(deep=True)
        parsed.original_text = 'A replacement story reusing the cast'
        parser_input = parsed.model_dump(mode='json')
        parser_calls = []

        def parsed_script(title, text, custom_extraction_prompt):
            parser_calls.append((title, text, custom_extraction_prompt))
            return parsed

        # Stub the structured LLM result, leaving the public reparse transaction real.
        monkeypatch.setattr(runtime.script_processor, 'parse_novel', parsed_script)
        result = client.put('/projects/p/reparse', json={
            'text': parsed.original_text, 'expected_revision': before.revision,
        })
        assert result.status_code == 200, result.text
        assert parser_calls == [(before.title, parsed.original_text, before.prompt_config.entity_extraction)]
        assert parsed.model_dump(mode='json') == parser_input
        saved = runtime.read_committed_script('p')
        assert saved is not None
        assert saved.asset_image_request_scope_id == before.asset_image_request_scope_id
        assert saved.characters[0].model_dump(mode='json') == dict(source, three_view_request_id=None)
        assert saved.asset_image_requests[0].model_dump(mode='json') == original
        assert saved.original_text == parsed.original_text and saved.revision > before.revision
        databases = first.gpt_sheet.persisted(runtime)
        observed = client.get(first.PUBLIC + '/' + body['client_request_id'])
        replay = client.post(first.PUBLIC, json=body)
        assert observed.status_code == replay.status_code == 200
        assert replay.json()['replayed'] is True
        assert observed.json()['request'] == replay.json()['request']
        assert observed.json()['reason'] == 'studio_image_request_target_changed'
        assert first.gpt_sheet.persisted(runtime) == databases and png_bytes(runtime) == media
        assert (len(transport['calls']), len(transport['cli'])) == calls
