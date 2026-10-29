"""Public batch rendering keeps its frozen author inputs across paid frames.

The real app, Studio service, Core and adapter run; only external HTTP is
substituted. Synthetic image transport is not live-provider quality evidence.
"""

import json

from fastapi.testclient import TestClient

from src.apps.comic_gen.models import StoryboardFrame
from tests import test_storyboard_qwen20_core as frames
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_worker as worker

runtime = frames.runtime
transport = frames.transport


def test_public_qwen_batch_saves_first_result_then_stops_before_edited_later_input(runtime, transport):
    script = runtime.scripts['p']
    script.frames.append(StoryboardFrame.model_validate({
        'id': 'f2', 'scene_id': '', 'character_ids': [], 'action_description': 'original second frame',
    }))
    runtime._commit_storage('projects.json')
    # Batch has always used this component config, not the project picker.
    runtime.config['storyboard'] = {'model': {'params': {
        'model_name': frames.MODELS[0], 'i2i_model_name': frames.MODELS[0], 'n': 1,
    }}}
    frames.queue_image(transport)

    def edit_later_frame(request):
        if request.method != 'POST':
            return
        transport['on_send'] = None
        runtime.update_frame('p', 'f2', expected_revision=runtime.read_committed_script('p').revision,
                             action_description='new author choice')
        with runtime._save_lock:
            runtime.scripts['p'].frames.append(StoryboardFrame.model_validate({'id': 'late', 'scene_id': '', 'character_ids': []}))
            runtime._commit_storage('projects.json')

    transport['on_send'] = edit_later_frame
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        result = client.post('/projects/p/generate_storyboard')
        assert result.status_code == 409, result.text
        saved = runtime.read_committed_script('p')
        assert len(saved.asset_image_requests) == 1
        record = saved.asset_image_requests[0]
        execution = record.members[0].execution
        assert (record.asset_id, record.asset_type, execution.phase) == ('f', 'storyboard_frame', 'completed')
        assert execution.intent['intent_contract_version'] == frames.VERSION
        assert execution.intent['params'] == {'size': '1024*576', 'watermark': False, 'prompt_extend': True}
        assert saved.frames[0].image_url == execution.output_ref
        assert saved.frames[0].rendered_image_asset.selected_id == record.members[0].variant_id
        assert (runtime.data_dir / execution.output_ref).is_file()
        assert saved.frames[1].action_description == 'new author choice'
        assert saved.frames[1].image_request_id is None and not saved.frames[1].image_url
        assert saved.frames[2].id == 'late' and saved.frames[2].image_request_id is None
        assert len(transport['calls']) == 2 and not transport['queue']
        payload = json.loads(transport['calls'][0][0].body)
        assert payload['model'] == frames.MODELS[0]
        assert 'new author choice' not in json.dumps(payload)
        original = client.get('/projects/p/asset_image_requests/' + record.client_request_id)
        assert original.status_code == 200, original.text
        assert original.json()['request']['members'][0]['status'] == 'completed'
    with worker.restarted(runtime) as other:
        before = frames.persisted(other)
        assert other.asset_image_service.run_original_request(record.id) is None
        assert frames.persisted(other) == before and len(transport['calls']) == 2


def test_public_qwen_batch_completes_two_frames_after_its_own_revision_changes(runtime, transport):
    script = runtime.scripts['p']
    script.frames.append(StoryboardFrame.model_validate({
        'id': 'f2', 'scene_id': '', 'character_ids': [], 'action_description': 'original second frame',
    }))
    runtime._commit_storage('projects.json')
    before_revision = runtime.read_committed_script('p').revision
    assert script.asset_image_request_scope_id is None
    runtime.config['storyboard'] = {'model': {'params': {
        'model_name': frames.MODELS[0], 'i2i_model_name': frames.MODELS[0], 'n': 1,
    }}}
    responses = []
    for _ in range(2):
        frames.queue_image(transport)
        responses.extend(transport['queue'])
    transport['queue'] = responses

    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        result = client.post('/projects/p/generate_storyboard')
        assert result.status_code == 200, result.text
        saved = runtime.read_committed_script('p')
        assert saved.revision > before_revision
        assert len(saved.asset_image_requests) == 2
        assert [record.asset_id for record in saved.asset_image_requests] == ['f', 'f2']
        assert saved.asset_image_request_scope_id is not None
        assert {record.request_root_scope_id for record in saved.asset_image_requests} == {
            saved.asset_image_request_scope_id,
        }
        for frame, record in zip(saved.frames, saved.asset_image_requests):
            assert record.asset_type == 'storyboard_frame' and len(record.members) == 1
            member = record.members[0]
            assert member.execution.phase == 'completed'
            assert frame.image_request_id == record.id
            assert frame.image_url == member.execution.output_ref
            assert frame.rendered_image_asset is not None
            assert frame.rendered_image_asset.selected_id == member.variant_id
            original = client.get('/projects/p/asset_image_requests/' + record.client_request_id)
            assert original.status_code == 200, original.text
            assert original.json()['request']['members'][0]['status'] == 'completed'
        posts = [request for request, _ in transport['calls'] if request.method == 'POST']
        assert len(posts) == 2 and len(transport['calls']) == 4 and not transport['queue']
        assert 'original second frame' in posts[1].body.decode()
