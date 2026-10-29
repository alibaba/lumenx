"""Internal bulk admission preserves its already-frozen context and raw command."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.apps.comic_gen import asset_image_execution as module
from src.apps.comic_gen.models import Prop, Scene
from src.utils.json_storage import StorageConflictError
from tests import test_studio_scene_prop_service as scene

runtime = scene.runtime
bound_transport = scene.bound_transport


def frozen(item, kind, route='local', *, scope=None):
    root, target = scene.setup(item, kind, route)
    with item._save_lock:
        if scope is not None:
            root_object = item.series_store[root[1]] if root[0] == 'series' else item.scripts[root[1]]
            root_object.asset_image_request_scope_id = scope
            item._commit_storage('projects.json', 'series.json')
        view = item._read_asset_image_view(*root)
        context = item._prepare_asset_image(view, 'c', kind, root_kind=root[0])
        context['request_root_scope_id'] = view[0].asset_image_request_scope_id
    command = module.AssetImageRequestCommand.model_validate({
        **context['params'], 'client_request_id': f'{kind}-prepared',
        'asset_id': 'c', 'asset_type': kind,
    })
    return root, target, context, command


def assert_inert(item, transport):
    assert item.asset_generation_tasks == {}
    assert getattr(item, '_asset_image_runs', {}) == {}
    assert not any(root.asset_image_requests for root in [*item.scripts.values(), *item.series_store.values(), item.library_store])
    assert scene.post_count(transport) == 0


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('route', scene.admission.ROUTES)
@pytest.mark.parametrize('existing_scope', [False, True])
def test_frozen_context_executes_real_core_for_each_owner_without_repreparing(runtime, bound_transport, monkeypatch, kind, route, existing_scope):
    scope = str(uuid4()) if existing_scope else None
    root, target, context, command = frozen(runtime, kind, route, scope=scope)
    old_params, old_options, old_expected = [deepcopy(context[key]) for key in ('params', 'options', 'expected')]
    monkeypatch.setattr(runtime.asset_image_service, '_prepare', Mock(side_effect=AssertionError('reprepared')))
    monkeypatch.setattr(runtime, '_prepare_asset_image', Mock(side_effect=AssertionError('refrozen')))
    scene.queue_success(bound_transport, 1)
    ack = runtime.asset_image_service._admit_prepared_context(command, context)
    assert not ack.replayed and ack.request.entrypoint == 'studio.asset.scene_prop.v1'
    assert ack.request.asset_type == kind and target.image_request_id == ack.request.id
    if scope is not None:
        assert ack.request.request_root_scope_id == scope
    assert [context[key] for key in ('params', 'options', 'expected')] == [old_params, old_options, old_expected]
    result = runtime.asset_image_service.run_original_request(ack.request.id)
    assert result is not None and result.request.members[0].execution.phase == 'completed'
    assert scene.post_count(bound_transport) == 1
    assert runtime.asset_image_service.admit(*root, command).replayed


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('change', ['asset-id', 'asset-kind', 'params', 'extra-seed', 'extra-watermark', 'key'])
def test_mismatched_command_or_context_never_binds_or_creates_claim(runtime, bound_transport, monkeypatch, kind, change):
    _, _, context, command = frozen(runtime, kind)
    raw = command.model_dump(exclude_unset=True)
    if change == 'asset-id':
        raw['asset_id'] = 'different'
    elif change == 'asset-kind':
        raw['asset_type'] = 'prop' if kind == 'scene' else 'scene'
    elif change == 'params':
        raw['style_preset'] = 'Cinematic'
    elif change == 'extra-seed':
        raw['seed'] = 0
    elif change == 'extra-watermark':
        raw['watermark'] = True
    else:
        context['key'] = ('global', 'library', kind, 'c')
    command = module.AssetImageRequestCommand.model_validate(raw)
    binder = Mock(side_effect=AssertionError('mismatch bound credentials'))
    monkeypatch.setattr(module, 'bind_media_runtime_authority', binder)
    with pytest.raises((StorageConflictError, ValueError)):
        runtime.asset_image_service._admit_prepared_context(command, context)
    binder.assert_not_called()
    assert_inert(runtime, bound_transport)


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('when', ['before', 'binding'])
@pytest.mark.parametrize('change', ['description', 'aspect', 'style', 'model', 'scope', 'root-identity', 'saved-target'])
def test_changed_frozen_input_or_scope_rejected_before_persistence(runtime, bound_transport, monkeypatch, kind, when, change):
    _, target, context, command = frozen(runtime, kind)
    project = runtime.scripts['p']
    def edit():
        if change == 'description':
            target.description = 'changed after freeze'
        elif change == 'aspect':
            setattr(project.model_settings, f'{kind}_aspect_ratio', '9:16')
        elif change == 'style':
            project.style_preset = 'later style'
        elif change == 'model':
            project.model_settings.t2i_model = 'wan2.7-image-pro'
        elif change == 'scope':
            project.asset_image_request_scope_id = str(uuid4())
        elif change == 'root-identity':
            runtime.scripts['p'] = project.model_copy(deep=True)
        else:
            runtime._committed['projects.json']['p'][f'{kind}s'][0]['description'] = 'saved differs from live'
            return
        runtime._commit_storage('projects.json')
    original = module.bind_media_runtime_authority
    if when == 'before':
        edit()
        binder = Mock(side_effect=AssertionError('changed input bound credentials'))
        monkeypatch.setattr(module, 'bind_media_runtime_authority', binder)
    else:
        @contextmanager
        def reenter(*args, **kwargs):
            with original(*args, **kwargs) as authority:
                edit()
                yield authority
        monkeypatch.setattr(module, 'bind_media_runtime_authority', reenter)
    with pytest.raises(StorageConflictError):
        runtime.asset_image_service._admit_prepared_context(command, context)
    assert_inert(runtime, bound_transport)
    assert target.image_request_id is None


@pytest.mark.parametrize('kind', scene.KINDS)
def test_existing_scope_is_preserved_and_new_scope_requires_explicit_exact_caller_expectation(runtime, bound_transport, kind):
    _, _, first_context, first_command = frozen(runtime, kind)
    project = runtime.scripts['p']
    cls = Scene if kind == 'scene' else Prop
    getattr(project, f'{kind}s').append(cls.model_validate({'id': 'next', 'name': 'Next', 'description': 'original next'}))
    runtime._commit_storage('projects.json')
    view = runtime._read_asset_image_view('project', 'p')
    next_context = runtime._prepare_asset_image(view, 'next', kind)
    next_context['request_root_scope_id'] = view[0].asset_image_request_scope_id
    next_command = module.AssetImageRequestCommand.model_validate({
        **next_context['params'], 'client_request_id': f'{kind}-next', 'asset_id': 'next', 'asset_type': kind,
    })
    svc = runtime.asset_image_service
    first = svc._admit_prepared_context(first_command, first_context)
    scope = first.request.request_root_scope_id
    with pytest.raises(StorageConflictError):
        svc._admit_prepared_context(next_command, next_context)
    assert len(project.asset_image_requests) == 1
    # Only the bulk caller may carry forward the scope created by its first acceptance.
    next_context = {**next_context, 'request_root_scope_id': scope}
    second = svc._admit_prepared_context(next_command, next_context)
    assert second.request.request_root_scope_id == scope == project.asset_image_request_scope_id
    assert len(project.asset_image_requests) == 2 and scene.post_count(bound_transport) == 0


@pytest.mark.parametrize('kind', scene.KINDS)
def test_replay_precedes_invalid_context_settings_scope_secret_or_provider(runtime, bound_transport, monkeypatch, kind):
    root, _, context, command = frozen(runtime, kind)
    svc = runtime.asset_image_service
    first = svc._admit_prepared_context(command, context)
    project = runtime.scripts['p']
    project.asset_image_request_scope_id = str(uuid4())
    project.model_settings.t2i_model = 'later unsupported model'
    runtime._commit_storage('projects.json')
    context['params']['style_preset'] = 'changed stale context'
    for obj, name in [(svc, '_prepare'), (runtime, '_prepare_asset_image'), (runtime, '_check_asset_image'), (module, 'bind_media_runtime_authority')]:
        monkeypatch.setattr(obj, name, Mock(side_effect=AssertionError('replay evaluated mutable inputs')))
    replay = svc._admit_prepared_context(command, context)
    assert replay.replayed and replay.request == first.request
    assert replay.reason == 'studio_image_request_root_changed'
    assert len(project.asset_image_requests) == 1 and scene.post_count(bound_transport) == 0
    with pytest.raises(StorageConflictError, match='payload_conflict'):
        svc._admit_prepared_context(module.AssetImageRequestCommand.model_validate({**command.model_dump(exclude_unset=True), 'prompt': 'changed raw'}), context)
    assert svc.read_request(*root, command.client_request_id).request == first.request


def test_prepared_bridge_rejects_old_character_channel_and_mutated_command(runtime, bound_transport):
    _, _, context, command = frozen(runtime, 'scene')
    with pytest.raises(ValueError):
        runtime.asset_image_service._admit_prepared_context(scene.admission.command(), context)
    command.batch_size = True
    with pytest.raises(ValueError):
        runtime.asset_image_service._admit_prepared_context(command, context)
    assert_inert(runtime, bound_transport)


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('changes', [
    {'negative_prompt': 'no signs'}, {'reference_image_url': 'uploads/reference.png'},
    {'aspect_ratio': '2:1'}, {'model_name': 'wan2.6-t2i'},
])
def test_prepared_inputs_keep_strict_contract_before_binding(runtime, bound_transport, monkeypatch, kind, changes):
    root, _, _, _ = frozen(runtime, kind)
    view = runtime._read_asset_image_view(*root)
    context = runtime._prepare_asset_image(view, 'c', kind, changes)
    context['request_root_scope_id'] = view[0].asset_image_request_scope_id
    command = module.AssetImageRequestCommand.model_validate({
        **context['params'], 'client_request_id': 'invalid-prepared', 'asset_id': 'c', 'asset_type': kind,
    })
    binder = Mock(side_effect=AssertionError('unsupported bound credentials'))
    monkeypatch.setattr(module, 'bind_media_runtime_authority', binder)
    with pytest.raises(ValueError):
        runtime.asset_image_service._admit_prepared_context(command, context)
    binder.assert_not_called()
    assert_inert(runtime, bound_transport)


@pytest.mark.parametrize('kind', scene.KINDS)
def test_concurrent_same_prepared_key_has_one_original_claim_and_one_replay(runtime, bound_transport, monkeypatch, kind):
    _, _, context, command = frozen(runtime, kind)
    binder = Mock(wraps=module.bind_media_runtime_authority)
    monkeypatch.setattr(module, 'bind_media_runtime_authority', binder)
    svc = runtime.asset_image_service
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: svc._admit_prepared_context(command, context), range(2)))
    assert sorted(result.replayed for result in results) == [False, True]
    assert results[0].request == results[1].request
    assert binder.call_count == 1
    assert len(runtime.scripts['p'].asset_image_requests) == 1
    assert len(runtime.asset_generation_tasks) == len(runtime._asset_image_runs) == 1
    assert scene.post_count(bound_transport) == 0
