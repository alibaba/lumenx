"""Original claims execute once; frozen proofs permit local-only repair."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import asset_image_execution as module
from src.apps.comic_gen.models import ImageVariant, VideoVariant
from src.models.provider_media_execution import PrivateSyncArtifact
from src.models.provider_recovery import record_with_changes
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_core_image_admission as admission
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.studio_wan_http_harness import WanImageHttpHarness, assert_png

runtime = admission.runtime
PNG = rgb_png_bytes()


@pytest.fixture
def executions(tmp_path, monkeypatch):
    calls, paths = [], []
    base = tmp_path / 'private'
    base.mkdir()
    import tempfile
    original = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, 'mkdtemp', lambda **kwargs: original(dir=base, **kwargs))
    monkeypatch.setattr('requests.sessions.Session.request', Mock(side_effect=AssertionError('unexpected network')))

    def execute(record, authority, *, output_root, scratch_dir, max_bytes):
        assert record.phase == 'submitting'
        calls.append((record.model_copy(deep=True), authority))
        path = Path(scratch_dir) / 'normalized.png'
        path.write_bytes(PNG)
        paths.append(path)
        return PrivateSyncArtifact(path, hashlib.sha256(PNG).hexdigest(), len(PNG), 'image/png', 'image')
    return execute, calls, paths


# Preserve the old synchronous fixture for separately scoped Qwen/legacy tests.
# These fresh Wan tests use only real schema3 HTTP + actual Core normalization.
_active_wan_http = None


@pytest.fixture
def wan_executions(tmp_path, monkeypatch):
    harness = WanImageHttpHarness(monkeypatch, private_root=tmp_path / 'wan-private')
    monkeypatch.setattr(sys.modules[__name__], '_active_wan_http', harness)
    return harness


def service(item, execute):
    if _active_wan_http is not None:
        # Custom callbacks inject a boundary failure around the real async fetch.
        _active_wan_http.callback = execute
    return module.StudioAssetImageService(item, executor=execute, max_bytes=1024 * 1024)


def disk_request(item, route):
    name = {'local': 'projects.json', 'series': 'series.json', 'global': 'library_assets.json', 'standalone': 'series.json'}[route]
    data = json.loads((item.data_dir / name).read_text())
    return (data if route == 'global' else data['p' if route == 'local' else 's'])['asset_image_requests'][0]


@contextmanager
def restarted(item):
    item.close()
    other = admission.pipeline_module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock(), runtime_environment=RuntimeEnvironment({}))
    try:
        yield other
    finally:
        other.close()


@pytest.mark.parametrize('route', admission.ROUTES)
def test_first_commit_creates_one_pending_original_claim_without_execution(runtime, wan_executions, route):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, route)
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    task = runtime.asset_generation_tasks[ack.request.id]
    token = task['_token']
    key = (ack.request.owner_kind, ack.request.owner_id, 'character', 'c')
    assert task['status'] == 'pending' and runtime._asset_image_runs[key] is token
    assert calls == [] and wan_executions.http_calls == []
    replay = svc.admit(*root, admission.command())
    assert replay.replayed and runtime.asset_generation_tasks[ack.request.id] is task
    assert len(runtime.asset_generation_tasks) == 1
    with pytest.raises(admission.StorageConflictError, match='busy'):
        svc.admit(*root, admission.command(client_request_id='another'))


@pytest.mark.parametrize('route', admission.ROUTES)
def test_worker_uses_real_checkpoints_and_preserves_existing_selection_and_motion(runtime, wan_executions, route, monkeypatch):
    execute, calls, paths = wan_executions
    root = admission.setup(runtime, route)
    target = admission.owner(runtime, route).characters[0]
    target.reference_sheet.image_variants = [ImageVariant(id='chosen', url='uploads/keep.png', is_favorited=True)]
    target.reference_sheet.selected_image_id = 'chosen'
    target.image_url = 'uploads/keep.png'
    target.reference_sheet.image_prompt = 'keep prompt'
    target.reference_sheet.image_updated_at = 123
    target.reference_sheet.video_variants = [VideoVariant(id='motion', url='video/keep.mp4')]
    target.reference_sheet.selected_video_id = 'motion'
    target.voice_id = 'keep voice'
    runtime._commit_storage('projects.json', 'series.json', 'library_assets.json')
    before = target.model_dump(mode='json')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command(batch_size=2, seed=0))
    phases = []
    commit = runtime._storage.commit
    def save(payload):
        for value in payload.values():
            roots = value.values() if 'characters' not in value else [value]
            for owner in roots:
                for request in owner.get('asset_image_requests', []):
                    phases.append([m['execution']['phase'] for m in request['members']])
                    for member in request['members']:
                        proof = member['execution']['prepared_artifact']
                        if proof and member['execution']['phase'] == 'observed':
                            # First prepared save precedes publication.
                            if not any('completed' in p for p in phases[:-1]):
                                assert not (runtime.data_dir / proof['relative_path']).exists()
        return commit(payload)
    monkeypatch.setattr(runtime._storage, 'commit', save)
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    assert result.request.id == ack.request.id
    assert len(calls) == 2
    assert all(c[0].intent['params']['seed'] == 0 and c[0].intent['params']['watermark'] is False for c in calls)
    saved = admission.owner(runtime, route).characters[0]
    assert saved.reference_sheet.selected_image_id == 'chosen' and saved.image_url == 'uploads/keep.png'
    assert saved.reference_sheet.image_prompt == 'keep prompt' and saved.reference_sheet.image_updated_at == 123
    assert saved.reference_sheet.video_variants[0].id == 'motion' and saved.voice_id == 'keep voice'
    assert saved.reference_sheet.image_variants[0].model_dump(mode='json') == before['reference_sheet']['image_variants'][0]
    assert [v.id for v in saved.reference_sheet.image_variants[1:]] == [m.variant_id for m in ack.request.members]
    assert all(v.created_at == ack.request.created_at for v in saved.reference_sheet.image_variants[1:])
    for member in result.request.members:
        assert_png(runtime.data_dir / member.execution.output_ref)
    assert len(wan_executions.posts) == len(wan_executions.queries) == len(wan_executions.downloads) == 2
    assert all(not p.exists() for p in paths)
    assert svc.request_state(result.request)['status'] == 'completed'
    svc.run_original_request(ack.request.id)
    svc.repair_request(*root, 'request-1')
    assert len(calls) == 2 and len(saved.reference_sheet.image_variants) == 3


@pytest.mark.parametrize('checkpoint,phase,paid,queries,fetched,prepared', [
    ('submitting', 'accepted', 0, 0, 0, False),
    ('submitted', 'submitting', 1, 0, 0, False),
    ('observed', 'submitted', 1, 1, 0, False),
    ('prepared', 'observed', 1, 1, 1, False),
    ('completed', 'observed', 1, 1, 1, True),
])
def test_storage_failures_never_resubmit_and_retain_verified_private_bytes(runtime, wan_executions, monkeypatch, checkpoint, phase, paid, queries, fetched, prepared):
    execute, calls, paths = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    commit = runtime._storage.commit
    triggered = False
    def save(payload):
        nonlocal triggered
        rows = payload.get('projects.json', {}).get('p', {}).get('asset_image_requests', [])
        if rows:
            state = rows[0]['members'][0]['execution']
            label = 'prepared' if state['phase'] == 'observed' and state['prepared_artifact'] is not None else state['phase']
            if not triggered and label == checkpoint:
                triggered = True
                raise StorageError('injected ' + checkpoint + ' checkpoint failure')
        return commit(payload)
    monkeypatch.setattr(runtime._storage, 'commit', save)
    with pytest.raises(StorageError):
        svc.run_original_request(ack.request.id)
    assert triggered and len(calls) == fetched
    assert (len(wan_executions.posts), len(wan_executions.queries), len(wan_executions.downloads)) == (paid, queries, fetched)
    execution = disk_request(runtime, 'local')['members'][0]['execution']
    assert execution['schema_version'] == 3 and execution['phase'] == phase
    assert bool(execution['prepared_artifact']) is prepared
    if checkpoint == 'prepared':
        assert_png(paths[0])
        assert paths[0].parent.stat().st_mode & 0o777 == 0o700
        assert not (runtime.data_dir / execution['output_ref']).exists()
    if prepared:
        assert_png(runtime.data_dir / execution['output_ref'])
        assert not paths[0].exists()  # Durable published proof owns the validated bytes.
    assert not runtime._asset_image_runs
    count = len(wan_executions.http_calls)
    with restarted(runtime) as other:
        recovering = service(other, Mock(side_effect=AssertionError('restart fetched')))
        assert recovering.run_original_request(ack.request.id) is None
        restored = recovering.repair_request(*root, 'request-1')
        assert recovering.request_state(restored.request)['status'] == ('completed' if prepared else 'needs_confirmation')
    assert len(wan_executions.http_calls) == count


@pytest.mark.parametrize('phase', ['accepted', 'submitting'])
def test_restart_without_proof_never_executes_or_infers_success(runtime, wan_executions, phase):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    member = runtime.scripts['p'].asset_image_requests[0].members[0]
    member.execution = record_with_changes(member.execution, phase=phase)
    runtime._commit_storage('projects.json')
    with restarted(runtime) as other:
        recovering = service(other, execute)
        assert recovering.run_original_request(ack.request.id) is None
        result = recovering.repair_request(*root, 'request-1')
        assert recovering.request_state(result.request)['status'] == 'needs_confirmation'
    assert calls == [] and wan_executions.http_calls == []


@pytest.mark.parametrize('change', ['selection', 'name', 'pointer', 'root-relation', 'owner-deleted'])
def test_paid_result_keeps_evidence_without_projecting_changed_target(runtime, wan_executions, change):
    execute, calls, paths = wan_executions
    root = admission.setup(runtime, 'local')
    def changed(*args, **kwargs):
        result = execute(*args, **kwargs)
        target = runtime.scripts['p'].characters[0]
        if change == 'selection':
            target.reference_sheet.selected_image_id = 'manual'
        elif change == 'name':
            target.name = 'edited'
        elif change == 'pointer':
            target.reference_sheet_request_id = None
        elif change == 'root-relation':
            runtime.scripts['p'].series_id = 's'
        else:
            del runtime.scripts['p']
        runtime._commit_storage('projects.json')
        return result
    svc = service(runtime, changed)
    ack = svc.admit(*root, admission.command())
    svc.run_original_request(ack.request.id)
    assert len(calls) == len(wan_executions.posts) == 1
    if change == 'owner-deleted':
        assert_png(paths[0])
        assert not (runtime.data_dir / ack.request.members[0].execution.output_ref).exists()
    else:
        saved = runtime.scripts['p'].asset_image_requests[0].members[0].execution
        assert saved.phase == 'observed' and saved.prepared_artifact is not None
        assert_png(runtime.data_dir / saved.output_ref)
        assert runtime.scripts['p'].characters[0].reference_sheet.image_variants == []
        svc.repair_request(*root, 'request-1')
        assert len(calls) == len(wan_executions.posts) == 1


def test_partial_batch_keeps_unknown_member_visible_and_selects_first_success(runtime, wan_executions):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    seen = 0
    def partial(*args, **kwargs):
        nonlocal seen
        seen += 1
        if seen == 2:
            raise RuntimeError('uncertain upstream result')
        return execute(*args, **kwargs)
    svc = service(runtime, partial)
    ack = svc.admit(*root, admission.command(batch_size=3))
    result = svc.run_original_request(ack.request.id)
    state = svc.request_state(result.request)
    assert [m['status'] for m in state['members']] == ['completed', 'needs_confirmation', 'completed']
    assert state['status'] == 'needs_confirmation' and len(calls) == 2 and seen == 3
    assert len(wan_executions.posts) == len(wan_executions.queries) == 3
    assert len(wan_executions.downloads) == 2
    target = runtime.scripts['p'].characters[0]
    assert target.reference_sheet.selected_image_id == ack.request.members[0].variant_id
    assert len(target.reference_sheet.image_variants) == 2
    assert target.status == 'needs_confirmation'
    svc.run_original_request(ack.request.id)
    assert seen == 3


@pytest.mark.parametrize('route', admission.ROUTES)
def test_task_status_reads_retained_owner_after_restart_without_claim(runtime, wan_executions, route):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, route)
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    assert runtime.asset_generation_tasks[ack.request.id]['_core_image_request_id'] == ack.request.id
    assert svc.read_task_status(ack.request.id)['status'] == 'pending'
    with restarted(runtime) as other:
        recovered = service(other, execute)
        state = recovered.read_task_status(ack.request.id)
        assert state['task_id'] == ack.request.id and state['status'] == 'needs_confirmation'
        assert state['asset_id'] == 'c' and state['asset_type'] == 'character'
        assert state['script_id'] == root[1] and state['created_at'] == ack.request.created_at
        assert state['error'] is None and state['progress'] == 0
        assert len(state['members']) == 1 and recovered.read_task_status('missing') is None
        assert other.asset_generation_tasks == {} and calls == [] and wan_executions.http_calls == []


def test_task_status_duplicate_id_rejected_even_under_a_different_client_key(runtime, wan_executions):
    execute, _, _ = wan_executions
    root = admission.setup(runtime, 'global')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    payload = ack.request.model_dump(mode='json')
    payload.update(owner_kind='script', owner_id='p', client_request_id='different')
    runtime._committed['projects.json']['p']['asset_image_requests'] = [payload]
    with pytest.raises(StorageError, match='Duplicate'):
        svc.read_task_status(ack.request.id)


def test_concurrent_process_and_replay_do_not_duplicate_paid_entry(runtime, wan_executions):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    entered, release = Event(), Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return execute(*args, **kwargs)
    svc = service(runtime, blocked)
    ack = svc.admit(*root, admission.command())
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(svc.run_original_request, ack.request.id)
        try:
            assert entered.wait(5)
            assert svc.run_original_request(ack.request.id) is None
            assert svc.admit(*root, admission.command()).replayed
            assert svc.request_state(ack.request)['status'] == 'processing'
            assert svc.repair_request(*root, 'request-1').request.members[0].execution.phase == 'observed'
        finally:
            release.set()
        assert future.result(timeout=5).request.members[0].execution.phase == 'completed'
    assert len(calls) == len(wan_executions.posts) == 1


@pytest.mark.parametrize('direction', ['legacy-first', 'core-first'])
def test_legacy_and_core_claims_exclude_each_other(runtime, wan_executions, direction):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    def context():
        return runtime._prepare_asset_image(runtime._read_asset_image_view(*root), 'c', 'character',
                                            {'generation_type': 'reference_sheet', 'apply_style': False}, root_kind='project')
    if direction == 'legacy-first':
        runtime._begin_asset_image(context())
        with pytest.raises(admission.StorageConflictError, match='busy'):
            svc.admit(*root, admission.command())
    else:
        svc.admit(*root, admission.command())
        with pytest.raises(admission.StorageConflictError, match='already generating'):
            runtime._begin_asset_image(context())
    assert calls == [] and wan_executions.http_calls == [] and len(runtime._asset_image_runs) == 1


@pytest.mark.parametrize('change', ['root-deleted', 'root-recreated', 'asset-deleted', 'asset-recreated', 'shadow', 'pointer', 'name', 'selection', 'series-relation'])
def test_guard_changes_before_worker_prevent_every_paid_call(runtime, wan_executions, change):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'global')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    target = runtime.library_store.characters[0]
    if change.startswith('root-'):
        del runtime.scripts['p']
        if change == 'root-recreated':
            runtime.scripts['p'] = admission.Script(id='p', title='new', original_text='new', created_at=2, updated_at=2)
    elif change.startswith('asset-'):
        runtime.library_store.characters.clear()
        if change == 'asset-recreated':
            runtime.library_store.characters.append(admission.Character(id='c', name='Alice', description='red coat'))
    elif change == 'shadow':
        runtime.scripts['p'].characters.append(target.model_copy(deep=True))
    elif change == 'pointer':
        target.reference_sheet_request_id = None
    elif change == 'name':
        target.name = 'edited'
    elif change == 'selection':
        target.reference_sheet.selected_image_id = 'new-selection'
    else:
        runtime.scripts['p'].series_id = 's'
    runtime._commit_storage('projects.json', 'library_assets.json')
    result = svc.run_original_request(ack.request.id)
    assert calls == [] and wan_executions.http_calls == [] and not runtime._asset_image_runs
    assert result.request.members[0].execution.phase == 'accepted'
    assert svc.request_state(result.request)['status'] == 'needs_confirmation'


def test_frozen_scope_and_intent_survive_current_settings_and_catalog_changes(runtime, wan_executions, monkeypatch):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command(seed=0))
    runtime.scripts['p'].model_settings.t2i_model = 'qwen-image-2.0'
    runtime.runtime_environment.update({'DASHSCOPE_BASE_URL': 'https://changed.invalid'}, persist=lambda *_: None)
    runtime._commit_storage('projects.json')
    monkeypatch.setattr('src.models.provider_media_runtime.get_catalog_accessor', Mock(side_effect=AssertionError('catalog re-read')))
    result = svc.run_original_request(ack.request.id)
    assert result.request.members[0].execution.phase == 'completed'
    assert calls[0][0].intent == ack.request.members[0].execution.intent
    assert calls[0][1].scope.to_dict() == ack.request.members[0].execution.scope


def test_changed_secret_does_not_rebind_original_paid_authority(runtime, wan_executions):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    runtime.runtime_environment.update({'DASHSCOPE_API_KEY': 'synthetic-replacement'}, persist=lambda *_: None)
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    assert result.request.members[0].execution.phase == 'accepted'
    assert calls == [] and wan_executions.http_calls == [] and svc.request_state(result.request)['status'] == 'needs_confirmation'


@pytest.mark.parametrize('damage', ['missing', 'hash', 'symlink'])
def test_repair_never_rebuilds_a_missing_or_invalid_proven_file(runtime, wan_executions, damage):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    record = runtime.scripts['p'].asset_image_requests[0]
    member = record.members[0]
    member.execution = record_with_changes(member.execution, phase='observed', artifact=None)
    runtime.scripts['p'].characters[0].reference_sheet.image_variants = []
    runtime.scripts['p'].characters[0].reference_sheet.selected_image_id = None
    runtime.scripts['p'].characters[0].image_url = None
    record.projection_guard = module.image_projection_guard(runtime.scripts['p'].characters[0])
    runtime._commit_storage('projects.json')
    output = runtime.data_dir / member.execution.output_ref
    output.unlink()
    if damage == 'hash':
        output.write_bytes(b'wrong')
    elif damage == 'symlink':
        other = runtime.data_dir / 'outside.png'
        other.write_bytes(PNG)
        output.symlink_to(other)
    with restarted(runtime) as other:
        recovering = service(other, Mock(side_effect=AssertionError('repair executed')))
        repaired = recovering.repair_request(*root, 'request-1')
        assert repaired.request.members[0].execution.phase == 'observed'
        state = recovering.request_state(repaired.request)
        assert state['status'] == 'needs_confirmation' and 'output_ref' not in state['members'][0]
        assert other.scripts['p'].characters[0].reference_sheet.image_variants == []
    assert len(calls) == len(wan_executions.posts) == 1 and result.request.members[0].execution.phase == 'completed'


@pytest.mark.parametrize('damage', ['kind', 'encoding', 'hash', 'size', 'symlink', 'outside', 'oversize', 'partial'])
def test_invalid_private_results_never_publish_or_retain_scratch(runtime, wan_executions, damage, tmp_path):
    from dataclasses import replace
    execute, _, paths = wan_executions
    root = admission.setup(runtime, 'local')
    def bad(*args, **kwargs):
        result = execute(*args, **kwargs)
        if damage == 'kind':
            return replace(result, media_kind='video')
        if damage == 'encoding':
            return replace(result, encoding='image/jpeg')
        if damage == 'hash':
            return replace(result, sha256='0' * 64)
        if damage == 'size':
            return replace(result, size_bytes=result.size_bytes + 1)
        if damage in {'symlink', 'outside'}:
            external = tmp_path / 'other.png'
            external.write_bytes(PNG)
            if damage == 'outside':
                return replace(result, path=external)
            result.path.unlink()
            result.path.symlink_to(external)
        elif damage == 'oversize':
            result.path.write_bytes(b'X' * (1024 * 1024 + 1))
            return replace(result, sha256=hashlib.sha256(result.path.read_bytes()).hexdigest(), size_bytes=result.path.stat().st_size)
        elif damage == 'partial':
            raise RuntimeError('partial response')
        return result
    svc = service(runtime, bad)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    member = result.request.members[0].execution
    assert member.phase == 'observed' and member.prepared_artifact is None
    assert not (runtime.data_dir / member.output_ref).exists()
    assert all(not p.parent.exists() for p in paths)


def test_existing_unrecorded_final_prevents_paid_call_and_is_not_overwritten(runtime, wan_executions):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    output = runtime.data_dir / ack.request.members[0].execution.output_ref
    output.parent.mkdir(parents=True)
    output.write_bytes(b'prior unrelated file')
    result = svc.run_original_request(ack.request.id)
    assert calls == [] and wan_executions.http_calls == [] and output.read_bytes() == b'prior unrelated file'
    assert result.request.members[0].execution.phase == 'accepted'


def test_late_worker_cannot_release_replacement_claim_or_project_to_it(runtime, wan_executions):
    execute, calls, _ = wan_executions
    root = admission.setup(runtime, 'local')
    replacement = object()
    def superseded(*args, **kwargs):
        result = execute(*args, **kwargs)
        runtime._asset_image_runs[('script', 'p', 'character', 'c')] = replacement
        return result
    svc = service(runtime, superseded)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    assert len(calls) == len(wan_executions.posts) == 1 and result.request.members[0].execution.phase == 'observed'
    assert runtime._asset_image_runs[('script', 'p', 'character', 'c')] is replacement
    assert runtime.scripts['p'].characters[0].reference_sheet.image_variants == []


def test_voice_and_motion_edits_during_http_survive_image_projection(runtime, wan_executions):
    execute, _, _ = wan_executions
    root = admission.setup(runtime, 'local')
    def edited(*args, **kwargs):
        result = execute(*args, **kwargs)
        target = runtime.scripts['p'].characters[0]
        target.voice_id = 'changed during HTTP'
        target.reference_sheet.video_variants = [VideoVariant(id='new-motion', url='video/new.mp4')]
        target.reference_sheet.selected_video_id = 'new-motion'
        target.reference_sheet.video_updated_at = 77
        runtime._commit_storage('projects.json')
        return result
    svc = service(runtime, edited)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    target = runtime.scripts['p'].characters[0]
    assert result.request.members[0].execution.phase == 'completed'
    assert target.voice_id == 'changed during HTTP' and target.reference_sheet.selected_video_id == 'new-motion'
    assert target.reference_sheet.video_updated_at == 77


def test_worker_uses_real_core_normalization_and_bound_transport_only(runtime, wan_executions, monkeypatch):
    from src.models.image import WanxImageModel
    root = admission.setup(runtime, 'local')
    monkeypatch.setattr(WanxImageModel, 'generate', Mock(side_effect=AssertionError('legacy fallback')))
    monkeypatch.setattr(WanxImageModel, 'generate_with_bound_http', Mock(side_effect=AssertionError('sync wrapper')))
    svc = service(runtime, wan_executions[0])
    ack = svc.admit(*root, admission.command(seed=0))
    result = svc.run_original_request(ack.request.id)
    member = result.request.members[0].execution
    assert member.schema_version == 3 and member.phase == 'completed'
    assert len(wan_executions.posts) == len(wan_executions.queries) == len(wan_executions.downloads) == 1
    assert json.loads(wan_executions.posts[0].body)['parameters'] == {
        'size': '576*1024', 'n': 1, 'seed': 0, 'watermark': False,
    }
    assert_png(runtime.data_dir / member.output_ref)
    assert member.artifact == member.prepared_artifact


def test_old_worker_does_not_overwrite_a_new_legacy_claim_status(runtime, wan_executions):
    execute, _, _ = wan_executions
    root = admission.setup(runtime, 'local')
    replacement = object()
    def superseded(*args, **kwargs):
        result = execute(*args, **kwargs)
        runtime._asset_image_runs[('script', 'p', 'character', 'c')] = replacement
        return result
    svc = service(runtime, superseded)
    ack = svc.admit(*root, admission.command())
    svc.run_original_request(ack.request.id)
    assert runtime.scripts['p'].characters[0].status == 'processing'
    assert runtime._asset_image_runs[('script', 'p', 'character', 'c')] is replacement


@pytest.mark.parametrize('failure', ['publish', 'closed', 'owner-history-removed'])
def test_verified_private_png_survives_unavailable_final_storage_and_drops_only_unverified_scratch(runtime, wan_executions, monkeypatch, failure):
    execute, _, paths = wan_executions
    root = admission.setup(runtime, 'local')
    def private(*args, **kwargs):
        result = execute(*args, **kwargs)
        (result.path.parent / 'raw-provider.part').write_bytes(b'unverified')
        if failure == 'closed':
            runtime.close()
        elif failure == 'owner-history-removed':
            runtime.scripts['p'].asset_image_requests.clear()
            runtime._commit_storage('projects.json')
        return result
    svc = service(runtime, private)
    ack = svc.admit(*root, admission.command())
    if failure == 'publish':
        import os
        link = os.link
        def refuse(source, destination, **kwargs):
            if source.startswith('.fetch-'):
                raise OSError('injected atomic publication failure')
            return link(source, destination, **kwargs)
        monkeypatch.setattr(os, 'link', refuse)
    if failure in {'closed', 'publish'}:
        with pytest.raises(StorageError):
            svc.run_original_request(ack.request.id)
    else:
        svc.run_original_request(ack.request.id)
    assert_png(paths[0])
    assert list(paths[0].parent.iterdir()) == [paths[0]]
    assert paths[0].parent.stat().st_mode & 0o777 == 0o700
    assert not (runtime.data_dir / ack.request.members[0].execution.output_ref).exists()
    assert not runtime._asset_image_runs
    if failure == 'publish':
        execution = disk_request(runtime, 'local')['members'][0]['execution']
        assert execution['phase'] == 'observed' and execution['prepared_artifact'] is not None


def test_mutated_private_stream_never_records_proof_or_retains_invalid_bytes(runtime, wan_executions, monkeypatch):
    execute, _, paths = wan_executions
    root = admission.setup(runtime, 'local')
    store = module.media_versions.store_media_stream
    def changed(chunks, **kwargs):
        paths[-1].write_bytes(b'changed after verification')
        return store(chunks, **kwargs)
    monkeypatch.setattr(module.media_versions, 'store_media_stream', changed)
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    member = result.request.members[0].execution
    assert member.phase == 'observed' and member.prepared_artifact is None
    assert not (runtime.data_dir / member.output_ref).exists()
    assert not paths[0].parent.exists()


def test_local_repair_can_finish_after_original_guard_is_restored_with_no_runtime_key(runtime, wan_executions):
    execute, _, _ = wan_executions
    root = admission.setup(runtime, 'local')
    def changed(*args, **kwargs):
        result = execute(*args, **kwargs)
        runtime.scripts['p'].characters[0].name = 'temporarily edited'
        runtime._commit_storage('projects.json')
        return result
    svc = service(runtime, changed)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    assert result.request.members[0].execution.phase == 'observed'
    runtime.scripts['p'].characters[0].name = 'Alice'
    runtime._commit_storage('projects.json')
    with restarted(runtime) as other:
        recovering = module.StudioAssetImageService(
            other, executor=Mock(side_effect=AssertionError('repair executed')),
            authority_resolver=Mock(side_effect=AssertionError('repair resolved credentials')))
        result = recovering.repair_request(*root, 'request-1')
        assert result.request.members[0].execution.phase == 'completed'
        target = other.scripts['p'].characters[0]
        assert [v.id for v in target.reference_sheet.image_variants] == [ack.request.members[0].variant_id]
        recovering.repair_request(*root, 'request-1')
        assert len(target.reference_sheet.image_variants) == 1


def test_task_read_is_pure_and_checks_completed_file_proof(runtime, wan_executions, monkeypatch):
    execute, _, _ = wan_executions
    root = admission.setup(runtime, 'local')
    svc = service(runtime, execute)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    assert result is not None
    member = result.request.members[0].execution
    (runtime.data_dir / member.output_ref).write_bytes(b'bad final bytes')
    commit = Mock(side_effect=AssertionError('status query wrote state'))
    monkeypatch.setattr(runtime, '_commit_storage', commit)
    state = svc.read_task_status(ack.request.id)
    assert state is not None
    assert state['status'] == 'needs_confirmation' and state['progress'] == 0
    assert svc._file_state(member) == 'invalid'
    assert state['members'][0]['reason'] == 'provider_image_result_pending'
    assert 'output_ref' not in state['members'][0]
    assert runtime.scripts['p'].asset_image_requests[0].members[0].execution.phase == 'completed'
    commit.assert_not_called()


@pytest.mark.parametrize('limit', [True, 0, -1, 1.5])
def test_worker_resource_bound_rejects_invalid_values(runtime, limit):
    with pytest.raises(ValueError):
        module.StudioAssetImageService(runtime, max_bytes=limit)


@pytest.fixture
def bound_transport(monkeypatch):
    from types import SimpleNamespace
    import time
    clock = {'now': 0.0}
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=time.time, monotonic=lambda: clock['now'],
        sleep=lambda seconds: clock.__setitem__('now', clock['now'] + seconds)))
    from tests.test_wan27_bound_image import transport
    return transport.__wrapped__(monkeypatch)


@pytest.mark.parametrize('result_kind', ['success', 'lost-ack', 'bad-final'])
def test_real_worker_core_bound_http_has_one_submit_and_read_only_recovery(runtime, bound_transport, result_kind):
    import requests
    from tests.test_wan27_bound_image import response
    root = admission.setup(runtime, 'local')
    svc = module.StudioAssetImageService(runtime, max_bytes=1024 * 1024)
    ack = svc.admit(*root, admission.command(seed=0, batch_size=1))
    if result_kind == 'lost-ack':
        bound_transport['queue'] = [requests.Timeout('synthetic lost response')]
    else:
        bound_transport['queue'][2] = response(rgb_png_bytes(width=16, height=16) if result_kind == 'success' else b'not an image')
    result = svc.run_original_request(ack.request.id)
    member = result.request.members[0].execution
    expected = 'completed' if result_kind == 'success' else 'needs_confirmation'
    assert svc.request_state(result.request)['status'] == expected
    assert member.phase == ('completed' if result_kind == 'success' else 'submitting' if result_kind == 'lost-ack' else 'observed')
    assert bool(member.prepared_artifact) is (result_kind == 'success')
    requests_before = len(bound_transport['calls'])
    assert [r.method for r, _ in bound_transport['calls']].count('POST') == 1
    payload = json.loads(bound_transport['calls'][0][0].body)
    assert payload['parameters'] == {'size': '576*1024', 'n': 1, 'seed': 0, 'watermark': False}
    with restarted(runtime) as other:
        recovering = module.StudioAssetImageService(other)
        assert recovering.run_original_request(ack.request.id) is None
        repaired = recovering.repair_request(*root, 'request-1')
        assert recovering.request_state(repaired.request)['status'] == expected
        assert recovering.read_task_status(ack.request.id)['status'] == expected
    assert len(bound_transport['calls']) == requests_before


def test_real_core_resolved_result_under_symlink_temp_root_is_preserved(runtime, wan_executions, monkeypatch, tmp_path):
    root = admission.setup(runtime, 'local')
    actual = tmp_path / 'actual-temp'
    actual.mkdir()
    alias = tmp_path / 'symlink-temp'
    alias.symlink_to(actual, target_is_directory=True)
    import tempfile
    monkeypatch.setattr(tempfile, 'mkdtemp', lambda **kwargs: wan_executions.original_mkdtemp(dir=alias, **kwargs))
    execute = wan_executions[0]
    def checked(*args, **kwargs):
        result = execute(*args, **kwargs)
        assert result.path.parent.parent == actual
        return result
    svc = service(runtime, checked)
    ack = svc.admit(*root, admission.command())
    result = svc.run_original_request(ack.request.id)
    member = result.request.members[0].execution
    assert member.schema_version == 3 and member.phase == 'completed'
    assert member.artifact is not None
    assert_png(runtime.data_dir / member.output_ref)
    assert len(wan_executions.paths) == len(wan_executions.posts) == 1
    assert not wan_executions.paths[0].exists()
