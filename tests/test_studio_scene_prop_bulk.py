"""Bulk preserves its frozen membership and stops before the next paid acceptance."""

from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock
from typing import SupportsIndex

import pytest
import requests

from src.apps.comic_gen import asset_image_execution as service_module
from src.apps.comic_gen.models import Character, Prop, Scene
from src.apps.comic_gen.pipeline import ProjectGenerationError
from src.utils.json_storage import StorageConflictError, StorageError
from tests import test_studio_scene_prop_pipeline as entry

runtime = entry.runtime
bound_transport = entry.bound_transport


def setup_bulk(item):
    project = item.scripts['p']
    project.scenes = [Scene.model_validate({'id': 'first', 'name': 'First', 'description': 'original first'})]
    project.props = [Prop.model_validate({'id': 'second', 'name': 'Second', 'description': 'original second'})]
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    return project


def after_first_download(transport, callback):
    class Queue(list):
        def pop(self, index: SupportsIndex = 0):
            result = super().pop(index)
            if len(transport['calls']) == 3:
                callback()
            return result
    transport['queue'] = Queue(transport['queue'])


def assert_first_only(project, transport):
    assert len(entry.posts(transport)) == 1
    assert len(project.asset_image_requests) == 1
    assert project.asset_image_requests[0].asset_id == 'first'
    assert project.props[0].image_request_id is None


def test_two_strict_assets_share_first_created_scope_without_repreparing(runtime, bound_transport, monkeypatch):
    project = setup_bulk(runtime)
    assert project.asset_image_request_scope_id is None
    entry.queue_images(bound_transport, 2)
    original = runtime._prepare_asset_image
    prepared = []
    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        prepared.append(value)
        return value
    monkeypatch.setattr(runtime, '_prepare_asset_image', capture)
    result = runtime.generate_assets('p')
    assert len(prepared) == 2
    assert len(entry.posts(bound_transport)) == 2
    assert len(project.asset_image_requests) == 2
    assert {r.request_root_scope_id for r in project.asset_image_requests} == {project.asset_image_request_scope_id}
    assert [(r.asset_type, r.asset_id) for r in project.asset_image_requests] == [('scene', 'first'), ('prop', 'second')]
    assert [result.scenes[0].status.value, result.props[0].status.value] == ['completed', 'completed']
    assert all(r.members[0].execution.phase == 'completed' for r in project.asset_image_requests)
    assert 'First. original first.' in entry.posts(bound_transport)[0]['input']['messages'][0]['content'][0]['text']
    assert 'Second. original second.' in entry.posts(bound_transport)[1]['input']['messages'][0]['content'][0]['text']


@pytest.mark.parametrize('changed', ['description', 'aspect', 'style', 'model'])
def test_later_changed_input_is_rejected_not_refrozen(runtime, bound_transport, changed):
    project = setup_bulk(runtime)
    entry.queue_images(bound_transport, 2)
    def edit():
        with runtime._save_lock:
            if changed == 'description':
                project.props[0].description = 'new second'
            elif changed == 'aspect':
                project.model_settings.prop_aspect_ratio = '16:9'
            elif changed == 'style':
                project.style_preset = 'New style'
            else:
                project.model_settings.t2i_model = 'wan2.7-image-pro'
            runtime._commit_storage('projects.json')
    after_first_download(bound_transport, edit)
    with pytest.raises(StorageConflictError):
        runtime.generate_assets('p')
    assert_first_only(project, bound_transport)
    assert project.scenes[0].status.value == 'completed'


def test_new_members_are_not_added_to_an_inflight_batch(runtime, bound_transport):
    project = setup_bulk(runtime)
    entry.queue_images(bound_transport, 2)
    def add():
        project.scenes.append(Scene.model_validate({'id': 'late', 'name': 'Late', 'description': 'later'}))
        runtime._commit_storage('projects.json')
    after_first_download(bound_transport, add)
    result = runtime.generate_assets('p')
    assert len(entry.posts(bound_transport)) == 2
    assert [r.asset_id for r in project.asset_image_requests] == ['first', 'second']
    assert result.scenes[-1].image_request_id is None and result.scenes[-1].status.value == 'pending'


@pytest.mark.parametrize('failure', ['unknown', 'storage'])
def test_failed_first_asset_stops_before_next_receipt_and_restart_does_not_execute_it(runtime, bound_transport, monkeypatch, failure):
    project = setup_bulk(runtime)
    if failure == 'unknown':
        bound_transport['queue'] = [requests.Timeout('synthetic lost response')]
        expected = ProjectGenerationError
    else:
        entry.queue_images(bound_transport, 2)
        original = runtime._storage.commit
        def fail_projection(payload):
            first = payload.get('projects.json', {}).get('p', {}).get('scenes', [{}])[0]
            if first.get('image_asset', {}).get('variants'):
                raise StorageError('synthetic final projection save failure')
            return original(payload)
        monkeypatch.setattr(runtime._storage, 'commit', fail_projection)
        expected = StorageError
    with pytest.raises(expected):
        runtime.generate_assets('p')
    assert_first_only(project, bound_transport)
    before = len(entry.posts(bound_transport))
    runtime.close()
    other = entry.admission.pipeline_module.ComicGenPipeline(
        data_dir=runtime.data_dir, script_processor=Mock(),
        runtime_environment=entry.RuntimeEnvironment({}), recover_orphan_tasks=True,
    )
    try:
        assert other.scripts['p'].props[0].image_request_id is None
        assert len(other.scripts['p'].asset_image_requests) == 1
        assert len(entry.posts(bound_transport)) == before
    finally:
        other.close()


def test_base_character_order_and_mixed_legacy_to_strict_order_stay_unchanged(runtime, bound_transport, monkeypatch):
    project = setup_bulk(runtime)
    project.characters = [
        Character.model_validate({'id': 'derived', 'name': 'Derived', 'description': 'variant', 'base_character_id': 'base'}),
        Character.model_validate({'id': 'base', 'name': 'Base', 'description': 'base'}),
    ]
    runtime._commit_storage('projects.json')
    entry.queue_images(bound_transport, 2)
    order = []
    def legacy(context, **kwargs):
        assert context['kind'] == 'character'
        assert entry.posts(bound_transport) == [] and project.asset_image_requests == []
        order.append(context['asset_id'])
        return runtime.read_committed_script('p')
    monkeypatch.setattr(runtime, '_generate_asset_image', legacy)
    runtime.generate_assets('p')
    assert order == ['base', 'derived']
    assert [r.asset_id for r in project.asset_image_requests] == ['first', 'second']
    assert len(entry.posts(bound_transport)) == 2


def test_legacy_character_failure_does_not_accept_any_strict_asset(runtime, bound_transport, monkeypatch):
    project = setup_bulk(runtime)
    project.characters = [Character.model_validate({'id': 'c', 'name': 'C', 'description': 'character'})]
    runtime._commit_storage('projects.json')
    legacy = Mock(side_effect=ProjectGenerationError('synthetic legacy failure'))
    monkeypatch.setattr(runtime, '_generate_asset_image', legacy)
    with pytest.raises(ProjectGenerationError):
        runtime.generate_assets('p')
    assert legacy.call_count == 1 and project.asset_image_requests == []
    assert entry.posts(bound_transport) == []


@pytest.mark.parametrize('changed', ['aspect', 'style', 'model'])
def test_binding_reentry_cannot_change_frozen_options_before_persist(runtime, bound_transport, monkeypatch, changed):
    project = setup_bulk(runtime)
    original = service_module.bind_media_runtime_authority
    @contextmanager
    def reenter(*args, **kwargs):
        with original(*args, **kwargs) as authority:
            if changed == 'aspect':
                project.model_settings.scene_aspect_ratio = '1:1'
            elif changed == 'style':
                project.style_preset = 'Changed during binding'
            else:
                project.model_settings.t2i_model = 'wan2.7-image-pro'
            runtime._commit_storage('projects.json')
            yield authority
    monkeypatch.setattr(service_module, 'bind_media_runtime_authority', reenter)
    with pytest.raises(StorageConflictError):
        runtime.generate_assets('p')
    assert project.asset_image_requests == [] and runtime.asset_generation_tasks == {}
    assert getattr(runtime, '_asset_image_runs', {}) == {}
    assert project.asset_image_request_scope_id is None and entry.posts(bound_transport) == []


def test_legacy_bulk_contexts_still_use_original_route(runtime, bound_transport, monkeypatch):
    project = setup_bulk(runtime)
    project.model_settings.t2i_model = 'wan2.6-t2i'
    runtime._commit_storage('projects.json')
    original = deepcopy(project.model_dump())
    contexts = []
    monkeypatch.setattr(runtime, '_generate_asset_image', lambda context: contexts.append(context))
    runtime.generate_assets('p')
    assert [c['kind'] for c in contexts] == ['scene', 'prop']
    assert project.model_dump() == original and entry.posts(bound_transport) == []


@pytest.mark.parametrize('outcome', ['completed', 'unknown'])
def test_bulk_real_http_reports_confirmed_success_or_safe_interruption(runtime, bound_transport, outcome):
    from fastapi.testclient import TestClient
    from tests import test_studio_core_image_api as old_http
    project = setup_bulk(runtime)
    entry.queue_images(bound_transport, 2)
    if outcome == 'unknown':
        bound_transport['queue'] = [requests.Timeout('synthetic lost response')]
    app = old_http.real_app(runtime)
    with TestClient(app) as client:
        old_http.enable_fake_key(app)
        result = client.post('/projects/p/generate_assets')
        assert result.status_code == (200 if outcome == 'completed' else 502), result.text
        assert len(entry.posts(bound_transport)) == (2 if outcome == 'completed' else 1)
        if outcome == 'completed':
            assert result.json()['scenes'][0]['status'] == 'completed'
            assert result.json()['props'][0]['status'] == 'completed'
        else:
            assert_first_only(project, bound_transport)
