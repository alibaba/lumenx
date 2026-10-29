"""Scene/prop admission and sequential workers use real Core and fake external HTTP."""

from contextlib import contextmanager
import hashlib
import json
import tempfile
from unittest.mock import Mock

import pytest
import requests

from src.apps.comic_gen import asset_image_execution as module
from src.apps.comic_gen.models import ImageAsset, Prop, Scene
from src.models.provider_recovery import record_with_changes
from src.utils.json_storage import StorageConflictError, StorageError
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_worker as worker
from tests import test_wan27_bound_image as http
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = admission.runtime
bound_transport = worker.bound_transport
KINDS = ('scene', 'prop')
PNG = rgb_png_bytes(width=16, height=16)


def setup(item, kind, route='local', *, selected=False):
    root = admission.setup(item, route)
    model = Scene if kind == 'scene' else Prop
    target = model.model_validate({
        'id': 'c', 'name': 'kept name', 'description': 'kept description',
        'image_asset': {'selected_id': 'old' if selected else None, 'variants': [
            {'id': 'old', 'url': 'uploads/old.png', 'is_favorited': True, 'created_at': 1},
        ]},
        'image_url': 'uploads/old.png' if selected else None,
        'video_prompt': 'independent motion', 'starred': True,
    })
    getattr(admission.owner(item, route), f'{kind}s').append(target)
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    return root, target


def command(kind, **changes):
    return module.AssetImageRequestCommand.model_validate({
        'client_request_id': f'{kind}-request', 'asset_id': 'c', 'asset_type': kind,
        'generation_type': 'all', 'apply_style': False, **changes,
    })


def queue_success(transport, count):
    transport['queue'] = [response for _ in range(count) for response in (
        http.response(http.task()), http.response(http.succeeded()), http.response(PNG),
    )]


def post_count(transport):
    return sum(request.method == 'POST' for request, _ in transport['calls'])


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('route', admission.ROUTES)
def test_admission_real_owners_claim_kind_and_replay_before_inputs(runtime, bound_transport, kind, route, monkeypatch):
    root, target = setup(runtime, kind, route)
    svc = runtime.asset_image_service
    first = svc.admit(*root, command(kind, batch_size=2, seed=0))
    record = first.request
    assert record.entrypoint == 'studio.asset.scene_prop.v1' and record.channel == 'image'
    assert record.asset_type == kind and target.image_request_id == record.id
    assert admission.owner(runtime, route).asset_image_requests == [record]
    assert svc._owner_key(record) == (record.owner_kind, record.owner_id, kind, 'c')
    task = runtime.asset_generation_tasks[record.id]
    assert task['asset_type'] == kind and svc._active_task(record) is task
    assert task['_owner_key'] == svc._owner_key(record)
    assert all(m.execution.intent['params'] == {
        'size': '1024*576' if kind == 'scene' else '1024*1024', 'seed': 0, 'watermark': False,
    } for m in record.members)
    monkeypatch.setattr(svc, '_prepare', Mock(side_effect=AssertionError('replay prepared')))
    monkeypatch.setattr(module, 'bind_media_runtime_authority', Mock(side_effect=AssertionError('replay bound')))
    target.description = 'later edit'
    result = svc.admit(*root, command(kind, batch_size=2, seed=0))
    assert result.replayed and result.request == record and post_count(bound_transport) == 0
    with pytest.raises(StorageConflictError):
        svc.admit(*root, command('prop' if kind == 'scene' else 'scene', client_request_id=f'{kind}-request'))


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('field,value', [('negative_prompt', 'negative'), ('reference_image_url', ''), ('aspect_ratio', '2:1'), ('seed', None), ('prompt_extend', False), ('unknown', 'bad')])
def test_invalid_requests_have_no_binding_receipt_or_claim(runtime, kind, field, value, monkeypatch):
    root, _ = setup(runtime, kind)
    bind = Mock(side_effect=AssertionError('invalid bound credentials'))
    monkeypatch.setattr(module, 'bind_media_runtime_authority', bind)
    with pytest.raises(ValueError):
        runtime.asset_image_service.admit(*root, command(kind, **{field: value}))
    bind.assert_not_called()
    assert runtime.scripts['p'].asset_image_requests == [] and runtime.asset_generation_tasks == {}


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('route', admission.ROUTES)
@pytest.mark.parametrize('selected', [False, True])
def test_real_http_batch_defers_selection_and_keeps_order(runtime, bound_transport, kind, route, selected):
    root, target = setup(runtime, kind, route, selected=selected)
    assert target.image_asset is not None
    queue_success(bound_transport, 3)
    svc = runtime.asset_image_service
    original_selected, original_url = target.image_asset.selected_id, target.image_url
    before = target.model_dump(mode='json')
    ack = svc.admit(*root, command(kind, batch_size=3, seed=0))

    class Calls(list):
        def append(self, value):
            request, _ = value
            if request.method == 'POST':
                assert target.image_asset is not None
                assert target.image_asset.selected_id == original_selected
                assert target.image_url == original_url
            super().append(value)
    bound_transport['calls'] = Calls()
    result = svc.run_original_request(ack.request.id)
    assert result is not None and post_count(bound_transport) == 3
    assert [v.id for v in target.image_asset.variants] == [*[m.variant_id for m in reversed(ack.request.members)], 'old']
    assert target.image_asset.selected_id == ('old' if selected else ack.request.members[0].variant_id)
    assert target.image_url == ('uploads/old.png' if selected else ack.request.members[0].execution.output_ref)
    assert target.image_asset.variants[-1].is_favorited and target.status == 'completed'
    after = target.model_dump(mode='json')
    for field in set(before) - {'image_asset', 'image_url', 'image_request_id', 'status'}:
        assert after[field] == before[field]
    assert all(m.execution.phase == 'completed' and (runtime.data_dir / m.execution.output_ref).is_file() for m in result.request.members)
    assert svc.request_state(result.request)['status'] == 'completed'
    svc.run_original_request(ack.request.id)
    assert post_count(bound_transport) == 3


@pytest.mark.parametrize('kind', KINDS)
def test_single_result_selects_new_variant(runtime, bound_transport, kind):
    root, target = setup(runtime, kind, selected=True)
    assert target.image_asset is not None
    queue_success(bound_transport, 1)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind))
    svc.run_original_request(ack.request.id)
    assert target.image_asset.selected_id == ack.request.members[0].variant_id
    assert target.image_url == ack.request.members[0].execution.output_ref


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('selected', [False, True])
def test_second_submit_unknown_stops_and_cancels_only_unstarted_slots_after_real_reopen(runtime, bound_transport, kind, selected):
    root, target = setup(runtime, kind, selected=selected)
    assert target.image_asset is not None
    queue_success(bound_transport, 1)
    bound_transport['queue'].append(requests.Timeout('synthetic lost response'))
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    result = svc.run_original_request(ack.request.id)
    assert result is not None and post_count(bound_transport) == 2
    assert [m.execution.phase for m in result.request.members] == ['completed', 'submitting', 'cancelled']
    assert svc.request_state(result.request)['status'] == 'needs_confirmation'
    assert [m['status'] for m in svc.request_state(result.request)['members']] == ['completed', 'needs_confirmation', 'cancelled']
    assert target.image_asset.selected_id == ('old' if selected else None)
    assert target.image_url == ('uploads/old.png' if selected else None)
    assert target.status == 'needs_confirmation' and len(target.image_asset.variants) == 2
    with worker.restarted(runtime) as other:
        recovering = other.asset_image_service
        assert recovering.run_original_request(ack.request.id) is None
        recovered = recovering.repair_request(*root, f'{kind}-request')
        assert [m.execution.phase for m in recovered.request.members] == ['completed', 'submitting', 'cancelled']
        assert recovering.admit(*root, command(kind, batch_size=3)).replayed
    assert post_count(bound_transport) == 2


@pytest.mark.parametrize('kind', KINDS)
def test_pre_submit_exception_cancels_accepted_slots_without_http(runtime, bound_transport, kind):
    root, _ = setup(runtime, kind)
    @contextmanager
    def unavailable(record):
        raise ValueError('synthetic missing original authority')
        yield
    svc = module.StudioAssetImageService(runtime, authority_resolver=unavailable)
    ack = svc.admit(*root, command(kind, batch_size=3))
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    assert [m.execution.phase for m in result.request.members] == ['cancelled'] * 3
    assert svc.request_state(result.request)['status'] == 'cancelled'
    assert post_count(bound_transport) == 0


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('checkpoint,expected_phase,paid,queries,fetched,prepared', [
    ('submitting', 'accepted', 0, 0, 0, False),
    ('submitted', 'submitting', 1, 0, 0, False),
    ('observed', 'submitted', 1, 1, 0, False),
    ('prepared', 'observed', 1, 1, 1, False),
    ('completed', 'observed', 1, 1, 1, True),
])
def test_storage_failure_stops_remaining_slots_and_keeps_verified_private_bytes(runtime, bound_transport, kind, checkpoint, expected_phase, paid, queries, fetched, prepared, monkeypatch, tmp_path):
    root, target = setup(runtime, kind)
    queue_success(bound_transport, 3)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    author_before = target.model_dump(mode='json')
    private_root = tmp_path / 'private'
    private_root.mkdir()
    mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, 'mkdtemp', lambda **kwargs: mkdtemp(dir=private_root, **kwargs))
    fetch, results = module.fetch_async_image_to_private_file, []
    def observe_result(*args, **kwargs):
        result = fetch(*args, **kwargs)
        results.append(result)
        return result
    monkeypatch.setattr(module, 'fetch_async_image_to_private_file', observe_result)
    commit, triggered = runtime._storage.commit, False
    def fail(payload):
        nonlocal triggered
        rows = payload.get('projects.json', {}).get('p', {}).get('asset_image_requests', [])
        if rows:
            execution = rows[0]['members'][0]['execution']
            label = 'prepared' if execution['phase'] == 'observed' and execution['prepared_artifact'] is not None else execution['phase']
            if not triggered and label == checkpoint:
                triggered = True
                raise StorageError('synthetic ' + checkpoint + ' checkpoint failure')
        return commit(payload)
    monkeypatch.setattr(runtime._storage, 'commit', fail)
    with pytest.raises(StorageError):
        svc.run_original_request(ack.request.id)
    assert triggered and post_count(bound_transport) == paid
    assert sum('/api/v1/tasks/' in r.url for r, _ in bound_transport['calls']) == queries
    assert sum(r.url == http.RESULT_URL for r, _ in bound_transport['calls']) == fetched
    assert len(results) == fetched
    raw = json.loads((runtime.data_dir / 'projects.json').read_text())['p']['asset_image_requests'][0]
    assert [m['execution']['phase'] for m in raw['members']] == [expected_phase, 'accepted', 'accepted']
    execution = raw['members'][0]['execution']
    assert execution['schema_version'] == 3 and bool(execution['prepared_artifact']) is prepared
    private_pngs = list(private_root.rglob('*.png'))
    if checkpoint == 'prepared':
        assert private_pngs == [results[0].path]
        assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
        assert results[0].path.stat().st_size == results[0].size_bytes
        assert results[0].path.parent.stat().st_mode & 0o777 == 0o700
    else:
        assert private_pngs == []
    final_path = runtime.data_dir / execution['output_ref']
    if prepared:
        assert final_path.is_file()
        assert hashlib.sha256(final_path.read_bytes()).hexdigest() == execution['prepared_artifact']['sha256']
        assert final_path.stat().st_size == execution['prepared_artifact']['size_bytes']
    else:
        assert not final_path.exists()
    http_count = len(bound_transport['calls'])
    with worker.restarted(runtime) as other:
        repaired = other.asset_image_service.repair_request(*root, f'{kind}-request')
        assert other.asset_image_service.run_original_request(ack.request.id) is None
        assert other.asset_image_service.request_state(repaired.request)['status'] == 'needs_confirmation'
        item = getattr(other.scripts['p'], f'{kind}s')[0]
        assert item.image_asset.selected_id is None and item.image_url is None
        assert [v.id for v in item.image_asset.variants] == ['old', *([ack.request.members[0].variant_id] if prepared else [])]
        after = item.model_dump(mode='json')
        for field in set(author_before) - {'image_asset', 'status'}:
            assert after[field] == author_before[field]
    assert len(bound_transport['calls']) == http_count and post_count(bound_transport) == paid


@pytest.mark.parametrize('kind', KINDS)
def test_reverse_local_recovery_preserves_frozen_history_without_choosing(runtime, bound_transport, kind):
    root, target = setup(runtime, kind)
    assert target.image_asset is not None
    queue_success(bound_transport, 3)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    svc.run_original_request(ack.request.id)
    current = runtime.scripts['p'].asset_image_requests[0]
    # Real handles, observations and published proofs survive a completion interruption.
    for member in current.members:
        member.execution = record_with_changes(member.execution, phase='observed', artifact=None)
    target.image_asset = ImageAsset.model_validate({'selected_id': None, 'variants': []})
    target.image_url = None
    current.projection_guard = module.image_projection_guard(target)
    author_before = target.model_dump(mode='json')
    frozen = [m.execution.model_dump(mode='json') for m in current.members]
    runtime._commit_storage('projects.json')
    http_count = len(bound_transport['calls'])
    with worker.restarted(runtime) as other:
        recovering = other.asset_image_service
        record = recovering.read_request(*root, f'{kind}-request').request
        for member in reversed(record.members):
            recovered = recovering.recover_member_once(record.id, member.execution.attempt_id, action='repair')
            item = getattr(other.scripts['p'], f'{kind}s')[0]
            assert item.image_asset.selected_id is None and item.image_url is None
            after = item.model_dump(mode='json')
            for field in set(author_before) - {'image_asset', 'status'}:
                assert after[field] == author_before[field]
            completed = next(m for m in recovered.request.members if m.variant_id == member.variant_id)
            assert completed.execution.phase == 'completed'
        final = recovering.repair_request(*root, f'{kind}-request')
        item = getattr(other.scripts['p'], f'{kind}s')[0]
        assert [v.id for v in item.image_asset.variants] == [m.variant_id for m in reversed(record.members)]
        assert item.image_asset.selected_id is None and item.image_url is None
        for before, member in zip(frozen, final.request.members, strict=True):
            after = member.execution.model_dump(mode='json')
            for field in set(before) - {'phase', 'artifact'}:
                assert after[field] == before[field]
            assert after['artifact'] == before['prepared_artifact']
        assert recovering.request_state(final.request)['status'] == 'completed'
    assert len(bound_transport['calls']) == http_count and post_count(bound_transport) == 3


@pytest.mark.parametrize('kind,generation', [('character', 'all'), ('scene', 'reference_sheet'), ('prop', 'reference_sheet'), ('scene', 'full_body')])
def test_command_rejects_cross_channel_tuples(kind, generation):
    with pytest.raises(ValueError):
        command(kind, generation_type=generation)


def test_same_id_different_kind_claims_do_not_collide_but_same_kind_legacy_does(runtime, bound_transport):
    root, _ = setup(runtime, 'scene')
    runtime.scripts['p'].props.append(Prop.model_validate({'id': 'c', 'name': 'prop', 'description': ''}))
    runtime._commit_storage('projects.json')
    svc = runtime.asset_image_service
    acknowledgements = [svc.admit(*root, admission.command()), svc.admit(*root, command('scene')), svc.admit(*root, command('prop'))]
    assert {task['asset_type'] for task in runtime.asset_generation_tasks.values()} == {'character', 'scene', 'prop'}
    assert len(runtime._asset_image_runs) == 3
    context = runtime._prepare_asset_image(runtime._read_asset_image_view(*root), 'c', 'scene', {'apply_style': False})
    with pytest.raises(StorageConflictError, match='already generating'):
        runtime._begin_asset_image(context)
    queue_success(bound_transport, 3)
    for ack in acknowledgements:
        result = svc.run_original_request(ack.request.id)
        assert result is not None and result.request.members[0].execution.phase == 'completed'
    assert post_count(bound_transport) == 3 and not runtime._asset_image_runs


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('change', ['selection', 'favorite', 'pointer'])
def test_paid_result_does_not_overwrite_changed_projection_or_target(runtime, bound_transport, kind, change):
    root, target = setup(runtime, kind)
    assert target.image_asset is not None
    queue_success(bound_transport, 3)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    class Calls(list):
        def append(self, value):
            request, _ = value
            if request.method == 'POST':
                assert target.image_asset is not None
                if change == 'selection':
                    target.image_asset.selected_id = 'manual'
                    target.image_url = 'uploads/manual.png'
                elif change == 'favorite':
                    target.image_asset.variants[0].is_favorited = False
                else:
                    target.image_request_id = None
                runtime._commit_storage('projects.json')
            super().append(value)
    bound_transport['calls'] = Calls()
    result = svc.run_original_request(ack.request.id)
    assert result is not None and post_count(bound_transport) == 1
    assert [m.execution.phase for m in result.request.members] == ['observed', 'cancelled', 'cancelled']
    execution = result.request.members[0].execution
    assert execution.prepared_artifact is not None and (runtime.data_dir / execution.output_ref).is_file()
    assert [v.id for v in target.image_asset.variants] == ['old']
    before = target.model_dump()
    svc.repair_request(*root, f'{kind}-request')
    assert target.model_dump() == before and post_count(bound_transport) == 1


@pytest.mark.parametrize('kind', KINDS)
def test_lost_original_claim_does_not_cancel_another_claim(runtime, bound_transport, kind):
    root, _ = setup(runtime, kind)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    replacement = object()
    @contextmanager
    def changed(record):
        runtime._asset_image_runs[svc._owner_key(ack.request)] = replacement
        raise ValueError('original claim changed')
        yield
    svc._authority_resolver = changed
    result = svc.run_original_request(ack.request.id)
    assert result is not None and all(m.execution.phase == 'accepted' for m in result.request.members)
    assert runtime._asset_image_runs[svc._owner_key(ack.request)] is replacement
    assert post_count(bound_transport) == 0


@pytest.mark.parametrize('kind', KINDS)
def test_partial_completed_then_pre_submit_failure_is_not_whole_batch_success(runtime, bound_transport, kind):
    root, target = setup(runtime, kind)
    assert target.image_asset is not None
    queue_success(bound_transport, 1)
    svc = runtime.asset_image_service
    original_resolver = svc._authority_resolver
    seen = []
    @contextmanager
    def resolve(record):
        seen.append(record.attempt_id)
        if len(seen) == 2:
            raise ValueError('synthetic second authority missing')
        with original_resolver(record) as authority:
            yield authority
    svc._authority_resolver = resolve
    ack = svc.admit(*root, command(kind, batch_size=3))
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    assert [m.execution.phase for m in result.request.members] == ['completed', 'cancelled', 'cancelled']
    assert svc.request_state(result.request)['status'] == 'cancelled'
    assert target.status == 'needs_confirmation' and target.image_asset.selected_id is None
    assert len(target.image_asset.variants) == 2 and post_count(bound_transport) == 1


@pytest.mark.parametrize('kind', KINDS)
def test_owner_deleted_after_submit_does_not_try_to_cancel_missing_receipt(runtime, bound_transport, kind):
    root, _ = setup(runtime, kind)
    queue_success(bound_transport, 1)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    class Calls(list):
        def append(self, value):
            request, _ = value
            if request.method == 'POST':
                del runtime.scripts['p']
                runtime._commit_storage('projects.json')
            super().append(value)
    bound_transport['calls'] = Calls()
    assert svc.run_original_request(ack.request.id) is None
    assert post_count(bound_transport) == 1 and not runtime._asset_image_runs


@pytest.mark.parametrize('kind', KINDS)
def test_base_exception_remains_unknown_and_does_not_cancel_unstarted_or_resubmit(runtime, bound_transport, kind):
    root, _ = setup(runtime, kind)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    failure = KeyboardInterrupt('synthetic interruption')
    class Calls(list):
        def append(self, value):
            super().append(value)
            if value[0].method == 'POST':
                raise failure
    bound_transport['calls'] = Calls()
    with pytest.raises(KeyboardInterrupt) as caught:
        svc.run_original_request(ack.request.id)
    assert caught.value is failure
    assert [m.execution.phase for m in svc.read_request(*root, f'{kind}-request').request.members] == ['submitting', 'accepted', 'accepted']
    with worker.restarted(runtime) as other:
        assert other.asset_image_service.run_original_request(ack.request.id) is None
        result = other.asset_image_service.repair_request(*root, f'{kind}-request')
        assert other.asset_image_service.request_state(result.request)['status'] == 'needs_confirmation'
    assert post_count(bound_transport) == 1


@pytest.mark.parametrize('kind', KINDS)
def test_changed_member_order_cannot_gain_cancellation_authority(runtime, bound_transport, kind):
    root, _ = setup(runtime, kind)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    @contextmanager
    def changed(record):
        runtime.scripts['p'].asset_image_requests[0].members.reverse()
        runtime._commit_storage('projects.json')
        raise ValueError('original member order changed')
        yield
    svc._authority_resolver = changed
    with pytest.raises(StorageConflictError, match='members_changed'):
        svc.run_original_request(ack.request.id)
    result = svc.read_request(*root, f'{kind}-request')
    assert all(m.execution.phase == 'accepted' for m in result.request.members)
    assert [m.variant_id for m in result.request.members] == [m.variant_id for m in reversed(ack.request.members)]
    assert post_count(bound_transport) == 0


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('model_name', ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'])
def test_exact_supported_model_aliases_reach_real_bound_http(runtime, bound_transport, kind, model_name):
    root, _ = setup(runtime, kind)
    queue_success(bound_transport, 1)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, model_name=model_name))
    result = svc.run_original_request(ack.request.id)
    assert result is not None and result.request.members[0].execution.phase == 'completed'
    assert post_count(bound_transport) == 1
    request = bound_transport['calls'][0][0]
    assert json.loads(request.body)['model'] == model_name.removeprefix('wan/').removesuffix('#image')


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('change_at', ['submit', 'fetch'])
def test_successful_http_cannot_reorder_original_member_projection_authority(runtime, bound_transport, kind, change_at, monkeypatch, tmp_path):
    root, target = setup(runtime, kind)
    assert target.image_asset is not None
    queue_success(bound_transport, 3)
    svc = runtime.asset_image_service
    ack = svc.admit(*root, command(kind, batch_size=3))
    private_root = tmp_path / 'private'
    private_root.mkdir()
    mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, 'mkdtemp', lambda **kwargs: mkdtemp(dir=private_root, **kwargs))
    def change_order():
        runtime.scripts['p'].asset_image_requests[0].members.reverse()
        runtime._commit_storage('projects.json')
    fetch, results = module.fetch_async_image_to_private_file, []
    def observe_result(*args, **kwargs):
        result = fetch(*args, **kwargs)
        results.append(result)
        if change_at == 'fetch':
            change_order()
        return result
    monkeypatch.setattr(module, 'fetch_async_image_to_private_file', observe_result)
    class Calls(list):
        def append(self, value):
            request, _ = value
            if change_at == 'submit' and request.method == 'POST' and not self:
                change_order()
            super().append(value)
    bound_transport['calls'] = Calls()
    with pytest.raises(StorageConflictError, match='member'):
        svc.run_original_request(ack.request.id)
    assert post_count(bound_transport) == 1
    fetched = int(change_at == 'fetch')
    assert sum('/api/v1/tasks/' in r.url for r, _ in bound_transport['calls']) == fetched
    assert sum(r.url == http.RESULT_URL for r, _ in bound_transport['calls']) == fetched
    assert len(results) == fetched
    assert target.image_asset.selected_id is None and target.image_url is None
    assert [v.id for v in target.image_asset.variants] == ['old']
    if fetched:
        assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
        assert results[0].path.stat().st_size == results[0].size_bytes
        assert results[0].path.parent.stat().st_mode & 0o777 == 0o700
    else:
        assert list(private_root.rglob('*.png')) == []
    assert not runtime._asset_image_runs
    current = runtime.scripts['p'].asset_image_requests[0]
    phases = [m.execution.phase for m in current.members]
    assert phases.count('observed' if fetched else 'submitting') == 1 and phases.count('accepted') == 2
    started = next(m.execution for m in current.members if m.execution.phase != 'accepted')
    assert started.schema_version == 3 and started.prepared_artifact is None
    assert (started.handle is not None) is bool(fetched)
    assert not (runtime.data_dir / started.output_ref).exists()
    http_count = len(bound_transport['calls'])
    assert svc.run_original_request(ack.request.id) is None
    assert len(bound_transport['calls']) == http_count
