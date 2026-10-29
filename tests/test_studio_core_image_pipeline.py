"""Legacy entry points share retained Core receipts without reconstructing authority."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.models.provider_recovery import record_with_changes
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_worker as worker

runtime = admission.runtime
executions = worker.wan_executions


def configure(runtime, executions, route='local'):
    root = admission.setup(runtime, route)
    runtime._asset_image_service = worker.service(runtime, executions[0])
    runtime._generate_asset_image = Mock(side_effect=AssertionError('Core fell back to legacy'))
    return root


def enqueue(runtime, root, **params):
    fn = runtime.generate_series_asset if root[0] == 'series' else runtime.create_asset_generation_task
    return fn(root[1], 'c', 'character', generation_type='reference_sheet', apply_style=False, **params)


@pytest.mark.parametrize('route', admission.ROUTES)
def test_existing_task_entry_persists_receipt_and_executes_once_outside_save_lock(runtime, executions, route):
    root = configure(runtime, executions, route)
    execute = runtime._asset_image_service._executor
    def unlocked(*args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            def read():
                with runtime._save_lock:
                    return runtime.read_committed_project_view('p')[0].id
            assert pool.submit(read).result(timeout=3) == 'p'
        return execute(*args, **kwargs)
    executions.callback = unlocked
    result, task_id = enqueue(runtime, root, batch_size=2)
    receipt = admission.owner(runtime, route).asset_image_requests[0]
    assert result.id == root[1] and receipt.id == task_id
    assert receipt.request_root_kind == root[0] and receipt.request_root_id == root[1]
    assert runtime.get_asset_generation_task_status(task_id)['status'] == 'pending'
    runtime.process_asset_generation_task(task_id)
    runtime.process_asset_generation_task(task_id)
    assert len(executions[1]) == len(executions.posts) == len(executions.queries) == len(executions.downloads) == 2
    assert runtime.get_asset_generation_task_status(task_id)['status'] == 'completed'
    assert all(m.execution.phase == 'completed' for m in admission.owner(runtime, route).asset_image_requests[0].members)
    runtime._generate_asset_image.assert_not_called()


@pytest.mark.parametrize('model', ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'])
def test_sync_entry_uses_same_service_and_explicit_model_precedence(runtime, executions, model):
    configure(runtime, executions)
    runtime.scripts['p'].model_settings.t2i_model = 'wan2.6-t2i'
    runtime._commit_storage('projects.json')
    result = runtime.generate_asset('p', 'c', 'character', generation_type='reference_sheet', apply_style=False, model_name=model)
    assert result.id == 'p' and result.characters[0].reference_sheet.image_variants
    assert len(executions[1]) == len(executions.posts) == len(executions.queries) == len(executions.downloads) == 1
    assert len(runtime.scripts['p'].asset_image_requests) == 1


def test_core_rejection_is_not_a_legacy_fallback(runtime, executions):
    root = configure(runtime, executions)
    with pytest.raises(ValueError):
        enqueue(runtime, root, negative_prompt='unsupported')
    assert runtime.scripts['p'].asset_image_requests == [] and runtime.asset_generation_tasks == {}
    runtime._generate_asset_image.assert_not_called()
    assert executions[1] == [] and executions.http_calls == []


@pytest.mark.parametrize('model,channel', [('wan2.6-t2i', 'reference_sheet'), ('wan2.7-image', 'all'), ('wan2.7-image-pro', 'headshot')])
def test_other_model_or_channel_keeps_legacy_behavior(runtime, model, channel):
    admission.setup(runtime, 'local')
    runtime.scripts['p'].model_settings.t2i_model = model
    runtime._commit_storage('projects.json')
    legacy = Mock(return_value=runtime.read_committed_script('p'))
    runtime._generate_asset_image = legacy
    runtime.generate_asset('p', 'c', 'character', generation_type=channel, apply_style=False)
    legacy.assert_called_once()
    assert runtime.scripts['p'].asset_image_requests == []


@pytest.mark.parametrize('phase', ['accepted', 'submitting'])
def test_restart_task_lookup_retains_unknown_without_execution(runtime, executions, phase):
    root = configure(runtime, executions)
    _, task_id = enqueue(runtime, root)
    member = runtime.scripts['p'].asset_image_requests[0].members[0]
    member.execution = record_with_changes(member.execution, phase=phase)
    runtime._commit_storage('projects.json')
    runtime.close()
    with_patch = Mock(side_effect=AssertionError('startup used credentials or provider'))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr('src.apps.comic_gen.asset_image_execution.resolve_media_runtime_authority', with_patch)
        other = admission.pipeline_module.ComicGenPipeline(data_dir=runtime.data_dir, script_processor=Mock(), runtime_environment=RuntimeEnvironment({}), recover_orphan_tasks=True)
        try:
            state = other.get_asset_generation_task_status(task_id)
            assert state is not None
            assert state['status'] == 'needs_confirmation' and state['asset_id'] == 'c'
            other.process_asset_generation_task(task_id)
            assert other.asset_generation_tasks == {} and executions[1] == [] and executions.http_calls == []
        finally:
            other.close()
        with_patch.assert_not_called()


@pytest.mark.parametrize('route', ['local', 'series', 'global'])
def test_startup_only_repairs_proven_local_publication(runtime, executions, monkeypatch, route):
    root = configure(runtime, executions, route)
    _, task_id = enqueue(runtime, root)
    original = runtime._storage.commit
    owner_file = {'local': 'projects.json', 'series': 'series.json', 'global': 'library_assets.json'}[route]
    interrupted = False
    def fail_projection(payload):
        nonlocal interrupted
        saved = payload.get(owner_file, {})
        saved_owner = saved if route == 'global' else saved.get('p' if route == 'local' else 's', {})
        rows = saved_owner.get('asset_image_requests', [])
        if rows and rows[0]['members'][0]['execution']['phase'] == 'completed':
            interrupted = True
            raise StorageError('completed projection interrupted')
        return original(payload)
    monkeypatch.setattr(runtime._storage, 'commit', fail_projection)
    with pytest.raises(StorageError):
        runtime.process_asset_generation_task(task_id)
    assert interrupted and len(executions[1]) == 1
    assert len(executions.posts) == len(executions.queries) == len(executions.downloads) == 1
    runtime.close()
    blocked = Mock(side_effect=AssertionError('local repair resolved credentials'))
    monkeypatch.setattr('src.apps.comic_gen.asset_image_execution.resolve_media_runtime_authority', blocked)
    other = admission.pipeline_module.ComicGenPipeline(data_dir=runtime.data_dir, script_processor=Mock(), runtime_environment=RuntimeEnvironment({}), recover_orphan_tasks=True)
    try:
        state = other.get_asset_generation_task_status(task_id)
        assert state is not None and state['status'] == 'completed'
        unit = admission.owner(other, route).characters[0].reference_sheet
        assert unit is not None and len(unit.image_variants) == 1
        names = ('projects.json', 'series.json', 'library_assets.json')
        before = {name: (other.data_dir / name).read_bytes() for name in names}
        other._recover_orphan_tasks()
        assert {name: (other.data_dir / name).read_bytes() for name in names} == before
        blocked.assert_not_called()
    finally:
        other.close()
