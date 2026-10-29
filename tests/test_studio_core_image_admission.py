"""Actual owner transactions and replay precede image runtime authority."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import json
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as pipeline_module
from src.apps.comic_gen.models import Character, Script, Series
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.runtime_environment import RuntimeEnvironment

ROUTES = ('local', 'series', 'global', 'standalone')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ('StoryboardGenerator', 'VideoGenerator', 'AudioGenerator', 'ExportManager'):
        monkeypatch.setattr(pipeline_module, name, Mock())
    item = pipeline_module.ComicGenPipeline(
        data_dir=tmp_path / 'data', script_processor=Mock(),
        runtime_environment=RuntimeEnvironment({'DASHSCOPE_API_KEY': 'synthetic-example-image-admission'}),
    )
    item.scripts['p'] = Script(id='p', title='P', original_text='S', created_at=1, updated_at=1)
    item.series_store['s'] = Series(id='s', title='S', created_at=1, updated_at=1)
    for root in (item.scripts['p'], item.series_store['s']):
        root.model_settings.t2i_model = 'wan2.7-image'
    yield item
    item.close()


def setup(item, route):
    target = Character(id='c', name='Alice', description='red coat')
    if route == 'local':
        owner = item.scripts['p']
    elif route in {'series', 'standalone'}:
        owner = item.series_store['s']
        if route == 'series':
            item.scripts['p'].series_id = 's'
            owner.episode_ids = ['p']
    else:
        owner = item.library_store
    owner.characters.append(target)
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    return ('series', 's') if route == 'standalone' else ('project', 'p')


def api():
    return importlib.import_module('src.apps.comic_gen.asset_image_execution')


def command(**changes):
    return api().AssetImageRequestCommand.model_validate({
        'client_request_id': 'request-1', 'asset_id': 'c', 'apply_style': False, **changes,
    })


def owner(item, route):
    return item.scripts['p'] if route == 'local' else item.series_store['s'] if route in {'series', 'standalone'} else item.library_store


@pytest.mark.parametrize('route', ROUTES)
def test_admission_persists_single_owner_and_root_in_one_transaction(runtime, route, monkeypatch):
    root = setup(runtime, route)
    writes = []
    commit = runtime._storage.commit
    monkeypatch.setattr(runtime._storage, 'commit', lambda payload: (writes.append(deepcopy(payload)), commit(payload))[1])
    result = api().StudioAssetImageService(runtime).admit(*root, command(batch_size=2, seed=0, watermark=False))
    assert not result.replayed and result.reason is None
    record = result.request
    assert len(record.members) == 2
    assert len({m.variant_id for m in record.members}) == 2
    assert all(m.execution.phase == 'accepted' and m.execution.intent['params']['seed'] == 0 for m in record.members)
    assert all(m.execution.intent['params']['watermark'] is False for m in record.members)
    assert owner(runtime, route).asset_image_requests == [record]
    target = owner(runtime, route).characters[0]
    assert target.reference_sheet_request_id == record.id
    assert target.status.value == 'processing'
    actual_root = runtime.series_store[root[1]] if root[0] == 'series' else runtime.scripts[root[1]]
    assert actual_root.asset_image_request_scope_id == record.request_root_scope_id
    assert len(writes) == 1
    expected = {'local': {'projects.json'}, 'series': {'projects.json', 'series.json'}, 'global': {'projects.json', 'library_assets.json'}, 'standalone': {'series.json'}}[route]
    assert set(writes[0]) == expected
    assert list(runtime.asset_generation_tasks) == [record.id]
    assert runtime.asset_generation_tasks[record.id]['status'] == 'pending'
    assert all(m.execution.phase == 'accepted' for m in owner(runtime, route).asset_image_requests[0].members)
    assert len(runtime._load_data()['p'].asset_image_requests) == (route == 'local')
    assert len(runtime._load_series_data()['s'].asset_image_requests) == (route in {'series', 'standalone'})
    assert len(runtime._load_library_data().asset_image_requests) == (route == 'global')
    result.request.members.clear()
    assert len(owner(runtime, route).asset_image_requests[0].members) == 2


@pytest.mark.parametrize('route', ROUTES)
def test_replay_before_current_settings_owner_credentials_or_preparation(runtime, route, monkeypatch):
    root = setup(runtime, route)
    service = api().StudioAssetImageService(runtime)
    first = service.admit(*root, command())
    root_object = runtime.series_store[root[1]] if root[0] == 'series' else runtime.scripts[root[1]]
    root_object.model_settings.t2i_model = 'not-a-current-model'
    runtime._commit_storage('projects.json', 'series.json')
    monkeypatch.setattr(runtime, '_prepare_asset_image', Mock(side_effect=AssertionError('re-normalized')))
    monkeypatch.setattr(api(), 'bind_media_runtime_authority', Mock(side_effect=AssertionError('re-bound')))
    replay = service.admit(*root, command())
    assert replay.replayed and replay.request == first.request and replay.reason is None
    with pytest.raises(StorageConflictError):
        service.admit(*root, command(prompt='different'))


@pytest.mark.parametrize('field,value', [('prompt', None), ('style_prompt', None), ('model_name', None), ('negative_prompt', '')])
def test_raw_presence_is_part_of_identity(runtime, field, value):
    root = setup(runtime, 'local')
    service = api().StudioAssetImageService(runtime)
    service.admit(*root, command())
    with pytest.raises(StorageConflictError):
        service.admit(*root, command(**{field: value}))


@pytest.mark.parametrize('changes', [
    {'batch_size': True}, {'batch_size': 0}, {'unknown': 1}, {'asset_type': 'scene'},
    {'generation_type': 'all'}, {'watermark': None}, {'seed': True},
    {'seed': -1}, {'seed': None}, {'negative_prompt': 'no text'},
    {'reference_image_url': 'uploads/a.png'}, {'reference_image_url': ''},
    {'model_name': 'wan2.6-t2i'}, {'aspect_ratio': 'unsupported'},
])
def test_invalid_request_never_binds_or_changes_aggregate(runtime, changes, monkeypatch):
    root = setup(runtime, 'local')
    before = deepcopy(runtime._committed)
    binder = Mock(side_effect=AssertionError('invalid request reached binding'))
    monkeypatch.setattr(api(), 'bind_media_runtime_authority', binder)
    with pytest.raises(ValueError):
        api().StudioAssetImageService(runtime).admit(*root, command(**changes))
    assert runtime._committed == before and runtime.asset_generation_tasks == {}
    binder.assert_not_called()
    assert runtime._storage_failed is None


@pytest.mark.parametrize('route', ROUTES)
def test_failed_domain_commit_exposes_no_request_or_task(runtime, route, monkeypatch):
    root = setup(runtime, route)
    before = deepcopy(runtime._committed)
    monkeypatch.setattr(runtime._storage, 'commit', Mock(side_effect=StorageError('injected write failure')))
    with pytest.raises(StorageError):
        api().StudioAssetImageService(runtime).admit(*root, command())
    assert runtime._committed == before and runtime.asset_generation_tasks == {}
    assert owner(runtime, route).asset_image_requests == []
    assert owner(runtime, route).characters[0].reference_sheet_request_id is None


def test_concurrent_same_key_only_binds_and_commits_once(runtime, monkeypatch):
    root = setup(runtime, 'global')
    binder = Mock(wraps=api().bind_media_runtime_authority)
    monkeypatch.setattr(api(), 'bind_media_runtime_authority', binder)
    service = api().StudioAssetImageService(runtime)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.admit(*root, command()), range(2)))
    assert sorted(r.replayed for r in results) == [False, True]
    assert results[0].request.id == results[1].request.id
    assert binder.call_count == 1 and len(runtime.library_store.asset_image_requests) == 1


@pytest.mark.parametrize('change', ['root-deleted', 'root-recreated', 'asset-deleted', 'asset-recreated', 'shadow', 'pointer', 'name', 'image-selection', 'series-relation'])
def test_historical_receipt_survives_target_changes_without_fresh_admission(runtime, change, monkeypatch):
    root = setup(runtime, 'global')
    service = api().StudioAssetImageService(runtime)
    first = service.admit(*root, command())
    if change.startswith('root-'):
        del runtime.scripts['p']
        if change == 'root-recreated':
            runtime.scripts['p'] = Script(id='p', title='new', original_text='new', created_at=2, updated_at=2)
    elif change.startswith('asset-'):
        runtime.library_store.characters.clear()
        if change == 'asset-recreated':
            runtime.library_store.characters.append(Character(id='c', name='new', description='new'))
    elif change == 'shadow':
        runtime.scripts['p'].characters.append(Character(id='c', name='shadow', description='shadow'))
    elif change == 'pointer':
        runtime.library_store.characters[0].reference_sheet_request_id = None
    elif change == 'name':
        runtime.library_store.characters[0].name = 'new name'
    elif change == 'image-selection':
        runtime.library_store.characters[0].reference_sheet.selected_image_id = 'manual'
    else:
        runtime.scripts['p'].series_id = 's'
    runtime._commit_storage('projects.json', 'library_assets.json')
    monkeypatch.setattr(api(), 'bind_media_runtime_authority', Mock(side_effect=AssertionError('re-bound')))
    read = service.read_request(*root, 'request-1')
    assert read.request == first.request and read.reason is not None
    replay = service.admit(*root, command())
    assert replay.replayed and replay.request == first.request and replay.reason == read.reason


def test_unrelated_voice_video_and_compat_status_do_not_invalidate_image_guards(runtime):
    root = setup(runtime, 'local')
    service = api().StudioAssetImageService(runtime)
    service.admit(*root, command())
    target = runtime.scripts['p'].characters[0]
    target.voice_id = 'new voice'
    target.reference_sheet.video_prompt = 'new motion'
    target.reference_sheet.video_updated_at = 987
    target.status = 'needs_confirmation'
    runtime._commit_storage('projects.json')
    assert service.read_request(*root, 'request-1').reason is None


def test_owner_deletion_gives_missing_not_a_new_receipt(runtime):
    root = setup(runtime, 'local')
    service = api().StudioAssetImageService(runtime)
    first = service.admit(*root, command())
    task = runtime.asset_generation_tasks[first.request.id]
    del runtime.scripts['p']
    runtime._commit_storage('projects.json')
    with pytest.raises(api().AssetImageRequestNotFound):
        service.read_request(*root, 'request-1')
    assert runtime.asset_generation_tasks == {first.request.id: task}
    assert task['status'] == 'pending'


def test_duplicate_cross_owner_history_is_a_storage_error(runtime):
    root = setup(runtime, 'global')
    service = api().StudioAssetImageService(runtime)
    first = service.admit(*root, command()).request
    payload = first.model_dump(mode='json')
    payload.update(owner_kind='script', owner_id='p')
    runtime._committed['projects.json']['p']['asset_image_requests'] = [payload]
    with pytest.raises(StorageError):
        service.read_request(*root, 'request-1')


def test_seed_is_absent_without_a_synthetic_random_sentinel(runtime):
    root = setup(runtime, 'local')
    result = api().StudioAssetImageService(runtime).admit(*root, command())
    assert 'seed' not in result.request.members[0].execution.intent['params']
    assert result.request.members[0].execution.intent['params']['size'] == '576*1024'
    assert json.loads((runtime.data_dir / 'projects.json').read_text())['p']['asset_image_requests']


@pytest.mark.parametrize('change', ['series', 'scope'])
def test_uncommitted_root_identity_cannot_be_saved_as_part_of_new_admission(runtime, monkeypatch, change):
    root = setup(runtime, 'global')
    service = api().StudioAssetImageService(runtime)
    if change == 'scope':
        service.admit(*root, command())
    before = deepcopy(runtime._committed)
    tasks_before = dict(runtime.asset_generation_tasks)
    if change == 'series':
        runtime.scripts['p'].series_id = 's'
    else:
        runtime.scripts['p'].asset_image_request_scope_id = '11111111-1111-4111-8111-111111111111'
    binder = Mock(side_effect=AssertionError('uncommitted root reached binding'))
    monkeypatch.setattr(api(), 'bind_media_runtime_authority', binder)
    with pytest.raises(StorageConflictError):
        service.admit(*root, command(client_request_id='new-request'))
    binder.assert_not_called()
    assert runtime._committed == before and runtime.asset_generation_tasks == tasks_before
