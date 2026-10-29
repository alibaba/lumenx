"""Existing Studio scene/prop entrypoints retain Core receipts and stop on uncertainty."""

import json
from unittest.mock import Mock

import pytest
import requests

from src.apps.comic_gen.models import Prop, Scene
from src.apps.comic_gen.pipeline import ProjectGenerationError
from src.utils.json_storage import StorageConflictError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_worker as worker
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan27_bound_image import response, succeeded, task

runtime = admission.runtime
bound_transport = worker.bound_transport
KINDS = ('scene', 'prop')
MODELS = ('wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image')


def setup(item, kind, route='local'):
    root = admission.setup(item, route)
    owner = admission.owner(item, route)
    target = (Scene if kind == 'scene' else Prop).model_validate({
        'id': 'image', 'name': 'Room' if kind == 'scene' else 'Lamp', 'description': 'original',
        'image_url': 'uploads/chosen.png',
        'image_asset': {'selected_id': 'chosen', 'variants': [
            {'id': 'chosen', 'url': 'uploads/chosen.png', 'is_favorited': True},
        ]},
    })
    getattr(owner, kind + 's').append(target)
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    return root, owner, target


def queue_images(transport, count):
    transport['queue'] = [value for _ in range(count) for value in (
        response(task()), response(succeeded()), response(rgb_png_bytes(width=16, height=16)),
    )]


def posts(transport):
    return [json.loads(request.body) for request, _ in transport['calls'] if request.method == 'POST']


def enqueue(item, root, kind, **params):
    fn = item.generate_series_asset if root[0] == 'series' else item.create_asset_generation_task
    return fn(root[1], 'image', kind, generation_type='all', apply_style=False, **params)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('route', admission.ROUTES)
def test_task_entry_runs_actual_bound_http_once_and_reopens_read_only(runtime, bound_transport, kind, route):
    root, owner, target = setup(runtime, kind, route)
    queue_images(bound_transport, 2)
    assert target.image_asset is not None
    before_selection = target.image_asset.selected_id
    result, task_id = enqueue(runtime, root, kind, batch_size=2)
    assert result.id == root[1]
    assert len(owner.asset_image_requests) == 1
    record = owner.asset_image_requests[0]
    assert record.id == task_id and record.asset_type == kind and record.channel == 'image'
    assert target.image_request_id == task_id
    runtime.process_asset_generation_task(task_id)
    runtime.process_asset_generation_task(task_id)
    assert len(posts(bound_transport)) == 2
    assert runtime.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert target.image_asset.selected_id == before_selection
    assert [v.id for v in target.image_asset.variants] == [m.variant_id for m in reversed(record.members)] + ['chosen']
    runtime.close()
    other = admission.pipeline_module.ComicGenPipeline(
        data_dir=runtime.data_dir, script_processor=Mock(), runtime_environment=RuntimeEnvironment({}),
        recover_orphan_tasks=True,
    )
    try:
        state = other.get_asset_generation_task_status(task_id)
        assert state is not None
        assert state['status'] == 'completed' and state['asset_type'] == kind
        other.process_asset_generation_task(task_id)
        assert len(posts(bound_transport)) == 2
    finally:
        other.close()


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('model', MODELS)
def test_sync_entry_respects_explicit_model_and_returns_only_confirmed_projection(runtime, bound_transport, kind, model):
    _, owner, _ = setup(runtime, kind)
    runtime.scripts['p'].model_settings.t2i_model = 'wan2.6-t2i'
    runtime._commit_storage('projects.json')
    queue_images(bound_transport, 1)
    result = runtime.generate_asset('p', 'image', kind, apply_style=False, model_name=model)
    record = owner.asset_image_requests[0]
    target = getattr(result, kind + 's')[0]
    assert target.status.value == 'completed' and target.image_request_id == record.id
    assert target.image_asset.selected_id == record.members[0].variant_id
    assert posts(bound_transport)[0]['model'] == ('wan2.7-image-pro' if 'image-pro' in model else 'wan2.7-image')
    assert len(posts(bound_transport)) == 1


@pytest.mark.parametrize('kind', KINDS)
def test_sync_unknown_raises_preserving_original_receipt_and_does_not_reexecute(runtime, bound_transport, kind):
    _, owner, target = setup(runtime, kind)
    bound_transport['queue'] = [requests.Timeout('synthetic lost ACK')]
    with pytest.raises(ProjectGenerationError):
        runtime.generate_asset('p', 'image', kind, apply_style=False, batch_size=3)
    record = owner.asset_image_requests[0]
    assert [m.execution.phase for m in record.members] == ['submitting', 'cancelled', 'cancelled']
    assert target.image_asset is not None
    assert target.image_asset.selected_id == 'chosen' and target.image_url == 'uploads/chosen.png'
    assert target.status == 'needs_confirmation'
    service = runtime.asset_image_service
    assert service.read_task_status(record.id)['status'] == 'needs_confirmation'
    assert service.run_original_request(record.id) is None
    assert len(posts(bound_transport)) == 1


@pytest.mark.parametrize('kind', KINDS)
def test_sync_invalidated_projection_raises_instead_of_reporting_success(runtime, bound_transport, kind, monkeypatch):
    _, owner, _ = setup(runtime, kind)
    queue_images(bound_transport, 1)
    original = runtime.asset_image_service.run_original_request
    def changed(request_id):
        result = original(request_id)
        runtime.update_asset_description('p', 'image', kind, 'new description')
        return result
    monkeypatch.setattr(runtime.asset_image_service, 'run_original_request', changed)
    with pytest.raises(StorageConflictError):
        runtime.generate_asset('p', 'image', kind, apply_style=False)
    assert len(posts(bound_transport)) == 1 and len(owner.asset_image_requests) == 1
    assert getattr(owner, kind + 's')[0].description == 'new description'


@pytest.mark.parametrize('kind', KINDS)
def test_strict_rejection_does_not_fall_back_to_legacy(runtime, bound_transport, kind, monkeypatch):
    _, owner, _ = setup(runtime, kind)
    legacy = Mock(side_effect=AssertionError('strict rejected into legacy'))
    monkeypatch.setattr(runtime, '_generate_asset_image', legacy)
    with pytest.raises(ValueError):
        runtime.generate_asset('p', 'image', kind, negative_prompt='unsupported')
    assert owner.asset_image_requests == [] and posts(bound_transport) == []
    legacy.assert_not_called()


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('model,channel', [('wan2.6-t2i', 'all'), ('wan2.7-image', 'full_body')])
def test_unmigrated_model_or_channel_retains_existing_legacy_route(runtime, kind, model, channel, monkeypatch):
    _, owner, _ = setup(runtime, kind)
    legacy = Mock(return_value=runtime.read_committed_script('p'))
    monkeypatch.setattr(runtime, '_generate_asset_image', legacy)
    runtime.generate_asset('p', 'image', kind, model_name=model, generation_type=channel)
    legacy.assert_called_once()
    assert owner.asset_image_requests == []


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('route', ['local', 'standalone'])
@pytest.mark.parametrize('outcome', ['completed', 'unknown'])
def test_legacy_http_returns_original_core_task_and_reading_never_submits(runtime, bound_transport, kind, route, outcome):
    from fastapi.testclient import TestClient
    from tests import test_studio_core_image_api as old_http
    root, owner, _ = setup(runtime, kind, route)
    queue_images(bound_transport, 1)
    if outcome == 'unknown':
        bound_transport['queue'] = [requests.Timeout('synthetic lost response')]
    app = old_http.real_app(runtime)
    with TestClient(app) as client:
        old_http.enable_fake_key(app)
        path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/assets/generate"
        result = client.post(path, json={'asset_id': 'image', 'asset_type': kind, 'generation_type': 'all', 'apply_style': False})
        assert result.status_code == 200, result.text
        task_id = result.json()['_task_id']
        assert owner.asset_image_requests[0].id == task_id
        for _ in range(2):
            recovered = client.get(f'/tasks/{task_id}')
            assert recovered.status_code == 200, recovered.text
            assert recovered.json()['status'] == ('completed' if outcome == 'completed' else 'needs_confirmation')
            assert recovered.json()['asset_type'] == kind
        assert len(posts(bound_transport)) == 1


@pytest.mark.parametrize('kind', KINDS)
def test_sync_completion_rejects_changed_member_order_after_worker_returns(runtime, bound_transport, kind, monkeypatch):
    _, owner, _ = setup(runtime, kind)
    queue_images(bound_transport, 2)
    original = runtime.asset_image_service.run_original_request
    def reorder(request_id):
        result = original(request_id)
        owner.asset_image_requests[0].members.reverse()
        runtime._commit_storage('projects.json')
        return result
    monkeypatch.setattr(runtime.asset_image_service, 'run_original_request', reorder)
    with pytest.raises(StorageConflictError):
        runtime.generate_asset('p', 'image', kind, apply_style=False, batch_size=2)
    assert len(posts(bound_transport)) == 2


@pytest.mark.parametrize('kind', KINDS)
def test_equal_project_series_ids_keep_distinct_original_core_claims(runtime, bound_transport, kind):
    from src.apps.comic_gen.models import Series
    _, project, target = setup(runtime, kind)
    series = Series.model_validate({'id': 'p', 'title': 'Separate series', 'created_at': 1, 'updated_at': 1,
                                   kind + 's': [target.model_dump()]})
    runtime.series_store['p'] = series
    runtime._commit_storage('series.json')
    queue_images(bound_transport, 2)
    _, project_task = enqueue(runtime, ('project', 'p'), kind)
    _, series_task = enqueue(runtime, ('series', 'p'), kind)
    assert project_task != series_task
    runtime.process_asset_generation_task(series_task)
    runtime.process_asset_generation_task(project_task)
    assert [project.asset_image_requests[0].request_root_kind, series.asset_image_requests[0].request_root_kind] == ['project', 'series']
    assert target.image_request_id == project_task
    assert getattr(series, kind + 's')[0].image_request_id == series_task
    assert len(posts(bound_transport)) == 2


@pytest.mark.parametrize('kind', KINDS)
def test_shared_asset_pending_claim_blocks_other_project_until_original_finishes(runtime, bound_transport, kind):
    from src.apps.comic_gen.models import Script
    _, owner, target = setup(runtime, kind, 'global')
    runtime.scripts['q'] = Script.model_validate({'id': 'q', 'title': 'Q', 'original_text': '', 'created_at': 1, 'updated_at': 1})
    runtime._commit_storage('projects.json')
    queue_images(bound_transport, 2)
    _, first = enqueue(runtime, ('project', 'p'), kind)
    with pytest.raises(StorageConflictError):
        runtime.generate_asset('q', 'image', kind, apply_style=False)
    assert posts(bound_transport) == [] and len(owner.asset_image_requests) == 1
    runtime.process_asset_generation_task(first)
    runtime.generate_asset('q', 'image', kind, apply_style=False)
    assert len(posts(bound_transport)) == 2
    assert [r.request_root_id for r in owner.asset_image_requests] == ['p', 'q']
    assert target.image_request_id == owner.asset_image_requests[1].id
