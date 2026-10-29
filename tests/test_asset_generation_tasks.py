"""Task admission/claim must retain the synchronous asset image ownership rules."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import ArtDirection, GenerationStatus, Series
from src.utils.json_storage import StorageConflictError, StorageError
from tests import test_asset_image_writeback as image_tests
from tests.test_asset_image_writeback import assert_paid_files, persisted


runtime = image_tests.runtime


def configure_source(item, source):
    """Use real domain roots; standalone Series deliberately has zero projects."""
    if source != 'project':
        scene = item.scripts['p'].scenes.pop()
        if source == 'global':
            item.library_store.scenes.append(scene)
        else:
            item.series_store['series'] = Series(
                id='series', title='series', created_at=1, updated_at=1, scenes=[scene],
                art_direction=ArtDirection(selected_style_id='custom', style_config={'positive_prompt': 'accepted-style'}),
                model_settings=item.scripts['p'].model_settings.model_copy(deep=True),
            )
            if source == 'standalone_series':
                item.scripts.clear()
            else:
                item.scripts['p'].series_id = 'series'
                item.series_store['series'].episode_ids = ['p']
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')


def enqueue(item, source, **kwargs):
    if source == 'standalone_series':
        return item.generate_series_asset('series', 's', 'scene', **kwargs)
    return item.create_asset_generation_task('p', 's', 'scene', **kwargs)


def saved_target(item, source):
    if source == 'project':
        return persisted(item)['p']['scenes'][0]
    if source == 'global':
        return persisted(item, 'library_assets.json')['scenes'][0]
    return persisted(item, 'series.json')['series']['scenes'][0]


def process_outcome(item, task_id):
    try:
        item.process_asset_generation_task(task_id)
    except StorageConflictError as error:
        return error
    return None


@pytest.mark.parametrize('source', ['project', 'episode_series', 'global', 'standalone_series'])
def test_task_admission_persists_actual_owner_before_exposing_pending(runtime, monkeypatch, source):
    item, state = runtime
    configure_source(item, source)
    commit = item._storage.commit
    visible_during_commit = []

    def observe(payloads):
        visible_during_commit.append(dict(item.asset_generation_tasks))
        return commit(payloads)

    monkeypatch.setattr(item._storage, 'commit', observe)
    snapshot, task_id = enqueue(item, source)
    assert visible_during_commit, 'admission must save its initial owner state'
    assert all(not tasks for tasks in visible_during_commit)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'pending'
    assert saved_target(item, source)['status'] == GenerationStatus.PROCESSING.value
    assert not state.calls
    snapshot.title = 'caller-only title'
    if source == 'standalone_series':
        assert item.scripts == {} and persisted(item) == {}
        assert item.series_store['series'].title == 'series'
    else:
        assert item.scripts['p'].title == 'project'
        if source != 'project':
            assert item.scripts['p'].scenes == []


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_failed_task_admission_has_no_visible_task_and_preserves_storage_error(runtime, monkeypatch, source):
    item, state = runtime
    configure_source(item, source)
    before = saved_target(item, source)
    failure = StorageError('synthetic admission failure')

    def fail(payloads):
        raise failure

    monkeypatch.setattr(item._storage, 'commit', fail)
    with pytest.raises(StorageError) as raised:
        enqueue(item, source)
    assert raised.value is failure
    assert item.asset_generation_tasks == {}
    assert saved_target(item, source) == before
    assert not state.calls


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_same_task_concurrent_and_completed_reentry_never_starts_another_paid_call(runtime, source):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)
    entered, release = Event(), Event()

    def pause_first(call):
        if call is state.calls[0]:
            entered.set()
            assert release.wait(3), 'test did not release first provider'

    state.on_generate = pause_first
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(process_outcome, item, task_id)
        try:
            assert entered.wait(2), 'worker did not reach the real AssetGenerator'
            pool.submit(process_outcome, item, task_id).result(timeout=2)
            during_count = len(state.calls)
            during_status = item.get_asset_generation_task_status(task_id)['status']
        finally:
            release.set()
        first.result(timeout=2)
    first_status = item.get_asset_generation_task_status(task_id)
    process_outcome(item, task_id)
    assert (during_count, len(state.calls)) == (1, 1)
    assert during_status == 'processing'
    assert first_status['status'] == 'completed'
    assert item.get_asset_generation_task_status(task_id) == first_status
    assert_paid_files(state)


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_failed_task_reentry_never_implicitly_retries_paid_generation(runtime, source):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)

    def unavailable(call):
        raise RuntimeError('synthetic provider failure after writing an artifact')

    state.on_generate = unavailable
    item.process_asset_generation_task(task_id)
    first_status = item.get_asset_generation_task_status(task_id)
    item.process_asset_generation_task(task_id)
    assert len(state.calls) == 1
    assert first_status['status'] == 'failed'
    assert item.get_asset_generation_task_status(task_id) == first_status
    assert saved_target(item, source)['status'] == GenerationStatus.FAILED.value
    assert_paid_files(state)


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
@pytest.mark.parametrize('changed', ['model', 'style'])
def test_changed_accepted_model_or_style_conflicts_before_any_paid_call(runtime, source, changed):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)
    if source == 'project':
        if changed == 'model':
            item.update_model_settings('p', t2i_model='changed-model')
        else:
            item.update_project_style('p', 'changed-style')
    elif changed == 'model':
        item.update_series_model_settings('series', {'t2i_model': 'changed-model'})
    else:
        item.update_series('series', {'art_direction': {
            'selected_style_id': 'custom', 'style_config': {'positive_prompt': 'changed-style'},
        }})
    error = process_outcome(item, task_id)
    assert not state.calls, [(call['kwargs']['model_name'], call['prompt']) for call in state.calls]
    assert isinstance(error, StorageConflictError)
    assert item.get_asset_generation_task_status(task_id)['status'] != 'completed'
    assert saved_target(item, source)['status'] == GenerationStatus.FAILED.value


@pytest.mark.parametrize('source', ['project', 'episode_series', 'global', 'standalone_series'])
def test_owner_result_is_durable_before_task_becomes_completed(runtime, monkeypatch, source):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)
    commit = item._storage.commit
    publication_statuses = []

    def observe(payloads):
        if source == 'project':
            target = payloads.get('projects.json', {}).get('p', {}).get('scenes', [None])[0]
        elif source == 'global':
            target = payloads.get('library_assets.json', {}).get('scenes', [None])[0]
        else:
            target = payloads.get('series.json', {}).get('series', {}).get('scenes', [None])[0]
        if target and target['status'] == 'completed':
            publication_statuses.append((
                item.get_asset_generation_task_status(task_id)['status'], saved_target(item, source)['status'],
            ))
        return commit(payloads)

    monkeypatch.setattr(item._storage, 'commit', observe)
    item.process_asset_generation_task(task_id)
    assert publication_statuses == [('processing', 'processing')]
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert saved_target(item, source)['status'] == 'completed'
    assert len(saved_target(item, source)['image_asset']['variants']) == 3
    assert len(state.calls) == 1
    if source == 'standalone_series':
        assert item.scripts == {} and persisted(item) == {}
    assert_paid_files(state)


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_worker_result_save_failure_preserves_error_and_never_claims_completion(runtime, monkeypatch, source):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)
    commit = item._storage.commit
    failure = StorageError('synthetic paid result save failure', commit_uncertain=True)

    def fail_completed(payloads):
        roots = payloads.get('projects.json', {}) if source == 'project' else payloads.get('series.json', {})
        if any(scene['status'] == 'completed' for root in roots.values() for scene in root['scenes']):
            raise failure
        return commit(payloads)

    monkeypatch.setattr(item._storage, 'commit', fail_completed)
    with pytest.raises(StorageError) as raised:
        item.process_asset_generation_task(task_id)
    assert raised.value is failure
    assert item.get_asset_generation_task_status(task_id)['status'] != 'completed'
    assert saved_target(item, source)['status'] == 'processing'
    assert saved_target(item, source)['image_url'] == 'uploads/old-a.png'
    assert len(state.calls) == 1
    assert_paid_files(state)


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_task_status_read_is_detached_and_does_not_expose_execution_context(runtime, source):
    item, _ = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)
    pending_snapshot = item.get_asset_generation_task_status(task_id)
    status = item.get_asset_generation_task_status(task_id)
    assert set(status) == {'task_id', 'status', 'progress', 'error', 'asset_id', 'asset_type', 'script_id', 'created_at'}
    status['status'], status['progress'] = 'completed', 100
    assert item.get_asset_generation_task_status(task_id)['status'] == 'pending'
    item.process_asset_generation_task(task_id)
    assert pending_snapshot['status'] == 'pending' and pending_snapshot['progress'] == 0
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_admission_response_is_a_detached_root_snapshot(runtime, source):
    item, state = runtime
    configure_source(item, source)
    root, task_id = enqueue(item, source)
    root.title = 'caller title'
    if source == 'standalone_series':
        assert item.series_store['series'].title == 'series'
    else:
        assert item.scripts['p'].title == 'project'
    assert item.get_asset_generation_task_status(task_id)['status'] == 'pending'
    assert not state.calls


def test_series_worker_private_candidate_cannot_leak_through_unrelated_save(runtime):
    item, state = runtime
    configure_source(item, 'standalone_series')
    _, task_id = enqueue(item, 'standalone_series')
    observed = []

    def during_upload(path):
        item.update_series('series', {'title': 'unrelated new title'})
        observed.append(saved_target(item, 'standalone_series'))

    state.on_upload = during_upload
    item.process_asset_generation_task(task_id)
    assert observed[0]['image_url'] == 'uploads/old-a.png'
    assert [variant['id'] for variant in observed[0]['image_asset']['variants']] == ['old-a', 'old-b']
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert item.series_store['series'].title == 'unrelated new title'
    assert item.scripts == {} and persisted(item) == {}
    assert_paid_files(state)


def test_shared_owner_has_one_admission_across_project_and_standalone_series(runtime):
    item, state = runtime
    configure_source(item, 'episode_series')
    _, task_id = enqueue(item, 'episode_series')
    with pytest.raises(StorageConflictError):
        item.generate_series_asset('series', 's', 'scene')
    with pytest.raises(StorageConflictError):
        item.generate_asset('p', 's', 'scene')
    assert list(item.asset_generation_tasks) == [task_id]
    assert not state.calls
    item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert len(state.calls) == 1
    assert_paid_files(state)


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_task_rejects_invalid_acceptance_before_state_changes(runtime, source):
    item, state = runtime
    configure_source(item, source)
    before = saved_target(item, source)
    with pytest.raises(module.CommandValidationError):
        enqueue(item, source, batch_size=True)
    assert saved_target(item, source) == before
    assert item.asset_generation_tasks == {}
    assert not state.calls


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_worker_cannot_reattach_to_replaced_accepted_owner(runtime, source):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)
    with item._save_lock:
        if source == 'project':
            item.scripts['p'] = item.scripts['p'].model_copy(deep=True)
            item._save_data()
        else:
            item.series_store['series'] = item.series_store['series'].model_copy(deep=True)
            item._save_series_data()
    before = saved_target(item, source)
    with pytest.raises(StorageConflictError):
        item.process_asset_generation_task(task_id)
    assert saved_target(item, source) == before
    assert item.get_asset_generation_task_status(task_id)['status'] != 'completed'
    assert not state.calls


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_returned_failed_asset_is_never_a_completed_task(runtime, monkeypatch, source):
    item, state = runtime
    configure_source(item, source)
    generate = item.asset_generator.generate_scene

    def failed_result(*args, **kwargs):
        candidate = generate(*args, **kwargs)
        candidate.status = GenerationStatus.FAILED
        return candidate

    monkeypatch.setattr(item.asset_generator, 'generate_scene', failed_result)
    _, task_id = enqueue(item, source)
    item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'failed'
    assert saved_target(item, source)['status'] == 'failed'
    assert saved_target(item, source)['image_url'] == 'uploads/old-a.png'
    item.process_asset_generation_task(task_id)
    assert len(state.calls) == 1
    assert_paid_files(state)


@pytest.mark.parametrize('channel', ['three_view', 'headshot'])
def test_series_legacy_unwired_i2i_route_remains_explicit(runtime, channel):
    item, state = runtime
    character = item.scripts['p'].characters[0].model_copy(deep=True)
    configure_source(item, 'standalone_series')
    item.series_store['series'].characters.append(character)
    item.update_series_model_settings('series', {'i2i_model': 'configured-series-i2i'})
    _, task_id = item.generate_series_asset('series', 'c', 'character', generation_type=channel)
    item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert len(state.calls) == 1
    # P3 gap: preserve this legacy route during the task ownership change;
    # this is not evidence that configured i2i is correctly supported.
    assert state.calls[0]['kwargs']['model_name'] is None
    assert state.calls[0]['kwargs']['ref_image_path'] == str(item.data_dir / 'uploads/old-a.png')
    assert item.scripts == {} and persisted(item) == {}
    assert_paid_files(state)


def test_series_explicit_reference_remains_a_documented_unwired_field(runtime):
    from src.apps.comic_gen.models import Character
    item, state = runtime
    configure_source(item, 'standalone_series')
    item.series_store['series'].characters.append(Character(id='new', name='Alice', description='coat'))
    item._save_series_data()
    _, task_id = item.generate_series_asset('series', 'new', 'character', generation_type='full_body',
                                           reference_image_url='uploads/explicit.png')
    item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert len(state.calls) == 1
    # P3 owns supporting/rejecting this parameter; do not silently add a new
    # paid i2i route while fixing task admission and completion.
    assert state.calls[0]['kwargs']['ref_image_path'] is None
    assert state.calls[0]['kwargs']['model_name'] == 'wan2.6-t2i'
    assert_paid_files(state)


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
def test_legacy_task_params_are_not_the_workers_accepted_execution_context(runtime, source):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source)
    item.asset_generation_tasks[task_id]['params'].update(model_name='wrong-model', style_prompt='wrong-style')
    item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert len(state.calls) == 1
    assert state.calls[0]['kwargs']['model_name'] == 'wan2.6-t2i'
    assert 'accepted-style' in state.calls[0]['prompt'] and 'wrong-style' not in state.calls[0]['prompt']


def test_pending_claims_use_real_root_kind_even_when_project_and_series_ids_match(runtime):
    item, state = runtime
    scene = item.scripts['p'].scenes[0].model_copy(deep=True)
    scene.name = 'series-only-room'
    item.series_store['p'] = Series.model_validate({
        'id': 'p', 'title': 'series', 'created_at': 1, 'updated_at': 1, 'scenes': [scene],
        'model_settings': item.scripts['p'].model_settings.model_copy(deep=True),
    })
    item._save_series_data()
    _, project_task = item.create_asset_generation_task('p', 's', 'scene')
    _, series_task = item.generate_series_asset('p', 's', 'scene')
    item.process_asset_generation_task(series_task)
    item.process_asset_generation_task(project_task)
    assert len(state.calls) == 2
    assert 'series-only-room' in state.calls[0]['prompt']
    assert 'series-only-room' not in state.calls[1]['prompt']
    assert item.get_asset_generation_task_status(project_task)['status'] == 'completed'
    assert item.get_asset_generation_task_status(series_task)['status'] == 'completed'
    assert persisted(item)['p']['scenes'][0]['image_url'] != persisted(item, 'series.json')['p']['scenes'][0]['image_url']
    assert_paid_files(state)


def test_standalone_series_task_cannot_overwrite_a_new_shared_user_selection(runtime):
    item, state = runtime
    configure_source(item, 'episode_series')
    _, task_id = item.generate_series_asset('series', 's', 'scene')
    state.on_generate = lambda call: item.select_asset_variant('p', 's', 'scene', 'old-b')
    with pytest.raises(StorageConflictError):
        item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'failed'
    saved = saved_target(item, 'episode_series')
    assert saved['status'] == 'failed'
    assert saved['image_url'] == 'uploads/old-b.png'
    assert saved['image_asset']['selected_id'] == 'old-b'
    assert len(state.calls) == 1
    assert_paid_files(state)


@pytest.mark.parametrize('source', ['project', 'standalone_series'])
@pytest.mark.parametrize('where', ['provider', 'upload'])
def test_runtime_close_stops_task_without_publishing_or_another_paid_slot(runtime, source, where):
    item, state = runtime
    configure_source(item, source)
    _, task_id = enqueue(item, source, batch_size=2)

    def close(*args):
        item.close()
        return 'remote-key'

    if where == 'provider':
        state.on_generate = close
    else:
        state.on_upload = close
    with pytest.raises(StorageError):
        item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'failed'
    assert saved_target(item, source)['image_url'] == 'uploads/old-a.png'
    item.process_asset_generation_task(task_id)
    assert len(state.calls) == 1
    assert_paid_files(state)


def test_concurrent_project_and_series_admission_share_one_pending_owner(runtime):
    item, state = runtime
    configure_source(item, 'episode_series')
    start = Event()

    def accept(source):
        assert start.wait(2)
        try:
            return enqueue(item, source)
        except StorageConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(accept, source) for source in ('episode_series', 'standalone_series')]
        start.set()
        outcomes = [future.result(timeout=2) for future in futures]
    assert sum(isinstance(value, StorageConflictError) for value in outcomes) == 1
    assert len(item.asset_generation_tasks) == 1
    assert not state.calls
    task_id = next(iter(item.asset_generation_tasks))
    item.process_asset_generation_task(task_id)
    assert item.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert len(state.calls) == 1
    assert_paid_files(state)
