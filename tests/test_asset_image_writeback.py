"""Synchronous asset images use detached inputs and publish only to their owner."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.assets import AssetGenerator
from src.apps.comic_gen.models import Character, GenerationStatus, ImageAsset, ImageVariant, Scene, Script, Series
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ('StoryboardGenerator', 'VideoGenerator', 'AudioGenerator', 'ExportManager'):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / 'data', script_processor=Mock())
    assert isinstance(item.asset_generator, AssetGenerator)
    variants = ImageAsset(selected_id='old-a', variants=[
        ImageVariant(id='old-a', url='uploads/old-a.png', created_at=1),
        ImageVariant(id='old-b', url='uploads/old-b.png', created_at=2),
    ])
    item.scripts['p'] = Script(
        id='p', title='project', original_text='script', created_at=1, updated_at=1,
        characters=[Character(id='c', name='Alice', description='red coat',
                              full_body_asset=variants.model_copy(deep=True),
                              full_body_image_url='uploads/old-a.png')],
        scenes=[Scene(id='s', name='room', description='empty room', image_url='uploads/old-a.png',
                      image_asset=variants.model_copy(deep=True))],
        style_preset='accepted-style',
    )
    item.scripts['p'].model_settings.t2i_model = 'wan2.6-t2i'
    item.scripts['p'].model_settings.i2i_model = 'wan2.6-image'
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    for name in ('old-a.png', 'old-b.png', 'explicit.png'):
        path = item.data_dir / 'uploads' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic-input')
    state = SimpleNamespace(calls=[], on_generate=None, on_upload=None)

    def generate(prompt, output_path, **kwargs):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'paid-asset-retained')
        call = {'prompt': prompt, 'path': path, 'kwargs': kwargs}
        state.calls.append(call)
        if state.on_generate:
            state.on_generate(call)
        return str(path), 0

    def upload(path, **kwargs):
        if state.on_upload:
            return state.on_upload(path)
        return None

    monkeypatch.setattr(item.asset_generator.model, 'generate', generate)
    monkeypatch.setattr('src.utils.oss_utils.OSSImageUploader', lambda: SimpleNamespace(
        is_configured=True, upload_file=upload,
    ))
    yield item, state
    item.close()


def persisted(item, name='projects.json'):
    return json.loads((item.data_dir / name).read_text(encoding='utf-8'))


def assert_paid_files(state):
    assert state.calls
    assert len({call['path'] for call in state.calls}) == len(state.calls)
    assert all(call['path'].read_bytes() == b'paid-asset-retained' for call in state.calls)


def outcome(call):
    try:
        return call(), None
    except StorageConflictError as error:
        return None, error


def test_unrelated_save_cannot_publish_the_generators_private_variant(runtime):
    item, state = runtime
    entered, release = Event(), Event()

    def pause(path):
        entered.set()
        assert release.wait(3), 'test did not release fake OSS'
        return None

    state.on_upload = pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        generating = pool.submit(outcome, lambda: item.generate_asset('p', 's', 'scene'))
        try:
            assert entered.wait(2), 'actual generator did not reach fake OSS'
            pool.submit(item.update_project_style, 'p', 'other-style').result(timeout=2)
            during = persisted(item)['p']['scenes'][0]
        finally:
            release.set()
        generating.result(timeout=2)
    assert_paid_files(state)
    assert [v['id'] for v in during['image_asset']['variants']] == ['old-a', 'old-b']
    assert during['image_url'] == 'uploads/old-a.png'


def test_late_asset_image_cannot_replace_user_selection(runtime):
    item, state = runtime
    state.on_generate = lambda call: item.select_asset_variant('p', 's', 'scene', 'old-b')
    _, error = outcome(lambda: item.generate_asset('p', 's', 'scene'))
    stored = persisted(item)['p']['scenes'][0]
    assert_paid_files(state)
    assert stored['image_asset']['selected_id'] == 'old-b'
    assert stored['image_url'] == 'uploads/old-b.png'
    assert isinstance(error, StorageConflictError)
    assert stored['status'] == GenerationStatus.FAILED.value


def test_late_episode_request_cannot_write_to_a_detached_series_owner(runtime):
    item, state = runtime
    source = item.scripts['p'].scenes.pop()
    item.scripts['p'].series_id = 'series'
    item.series_store['series'] = Series(
        id='series', title='series', created_at=1, updated_at=1,
        episode_ids=['p'], scenes=[source],
    )
    item._commit_storage('projects.json', 'series.json')
    state.on_generate = lambda call: item.remove_episode_from_series('series', 'p')
    result, error = outcome(lambda: item.generate_asset('p', 's', 'scene'))
    stored = persisted(item, 'series.json')['series']['scenes'][0]
    assert persisted(item)['p']['series_id'] is None
    assert_paid_files(state)
    assert [v['id'] for v in stored['image_asset']['variants']] == ['old-a', 'old-b']
    assert result is None and isinstance(error, StorageConflictError)


def test_visible_global_asset_can_generate_without_copying_into_episode(runtime):
    item, state = runtime
    item.library_store.scenes.append(item.scripts['p'].scenes.pop())
    item._commit_storage('projects.json', 'library_assets.json')
    view = item.read_committed_project_view('p')
    assert [value.id for value in item._resolve_asset_layers(*view)['scenes']] == ['s']
    result = item.generate_asset('p', 's', 'scene')
    assert len(state.calls) == 1
    assert result.scenes == []
    assert len(persisted(item, 'library_assets.json')['scenes'][0]['image_asset']['variants']) == 3
    assert_paid_files(state)




@pytest.mark.parametrize('owner', ['script', 'series', 'global'])
@pytest.mark.parametrize('kind', ['character', 'scene', 'prop'])
def test_success_writes_actual_owner_and_returns_detached_project(runtime, owner, kind):
    from src.apps.comic_gen.models import Prop, GenerationStatus
    item, state = runtime
    if kind == 'prop':
        item.scripts['p'].props.append(Prop(id='prop', name='lamp', description='brass'))
    field = {'character': 'characters', 'scene': 'scenes', 'prop': 'props'}[kind]
    target = getattr(item.scripts['p'], field)[0]
    if owner != 'script':
        getattr(item.scripts['p'], field).remove(target)
        if owner == 'series':
            item.scripts['p'].series_id = 'series'
            item.series_store['series'] = Series(id='series', title='series', created_at=1, updated_at=1)
            getattr(item.series_store['series'], field).append(target)
        else:
            getattr(item.library_store, field).append(target)
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    result = item.generate_asset('p', target.id, kind, generation_type='full_body' if kind == 'character' else 'all')
    resolved = item._resolve_asset_layers(*item.read_committed_project_view('p'))
    generated = next(asset for asset in resolved[field] if asset.id == target.id)
    assert generated.status == GenerationStatus.COMPLETED
    assert len(state.calls) == 1
    assert_paid_files(state)
    assert result is not item.scripts['p']
    result.title = 'caller edit'
    assert item.scripts['p'].title == 'project'
    if owner != 'script':
        assert getattr(result, field) == []
        assert getattr(item.scripts['p'], field) == []


@pytest.mark.parametrize('generation_type', ['reference_sheet', 'full_body', 'three_view', 'headshot', 'all'])
def test_character_actual_selected_channels_and_urls_stay_consistent(runtime, generation_type):
    from src.apps.comic_gen.models import AssetUnit
    item, state = runtime
    character = item.scripts['p'].characters[0]
    # A prior uploaded legacy image has both representations. The generated
    # selected variant must be the same one the short selection API resolves.
    character.full_body = AssetUnit(selected_image_id='old-a', image_variants=[
        value.model_copy(deep=True) for value in character.full_body_asset.variants
    ])
    item._save_data()
    result = item.generate_asset('p', 'c', 'character', generation_type=generation_type)
    character = result.characters[0]
    if generation_type == 'reference_sheet':
        variant = next(v for v in character.reference_sheet.image_variants if v.id == character.reference_sheet.selected_image_id)
        assert character.image_url == variant.url
    else:
        channels = ['full_body', 'three_view', 'headshot'] if generation_type == 'all' else [generation_type]
        for channel in channels:
            containers = dict(item._variant_channels(character, 'character'))[channel]
            selected_ids = [item._selected_image(value) for value in containers if value is not None]
            assert len(set(selected_ids)) == 1
            selected = next(v for v in character.model_dump()[channel + '_asset']['variants'] if v['id'] == selected_ids[0])
            assert getattr(character, {'full_body': 'full_body_image_url', 'three_view': 'three_view_image_url', 'headshot': 'headshot_image_url'}[channel]) == selected['url']
    assert_paid_files(state)


@pytest.mark.parametrize('where', ['provider', 'upload'])
def test_closed_runtime_never_publishes_or_continues_asset_batch(runtime, where):
    item, state = runtime
    def close(*args):
        item.close()
        return 'remote-key'
    if where == 'provider':
        state.on_generate = close
    else:
        state.on_upload = close
    with pytest.raises(StorageError):
        item.generate_asset('p', 's', 'scene', batch_size=2)
    assert len(state.calls) == 1
    assert persisted(item)['p']['scenes'][0]['image_url'] == 'uploads/old-a.png'
    assert item.scripts['p'].scenes[0].image_url == 'uploads/old-a.png'
    assert_paid_files(state)


def test_first_asset_result_save_failure_stops_next_asset(runtime, monkeypatch):
    item, state = runtime
    item.scripts['p'].characters.clear()
    item.scripts['p'].scenes.append(Scene(id='second', name='next', description='room'))
    item._save_data()
    commit = item._storage.commit
    failure = StorageError('paid asset result save failed')
    def fail(payloads):
        if any(asset['status'] == 'completed' for asset in payloads.get('projects.json', {}).get('p', {}).get('scenes', [])):
            raise failure
        return commit(payloads)
    monkeypatch.setattr(item._storage, 'commit', fail)
    with pytest.raises(StorageError) as raised:
        item.generate_assets('p')
    assert raised.value is failure
    assert len(state.calls) == 1
    assert persisted(item)['p']['scenes'][0]['image_url'] == 'uploads/old-a.png'
    assert_paid_files(state)


def test_asset_batch_freezes_later_input_instead_of_refreezing(runtime):
    item, state = runtime
    item.scripts['p'].characters.clear()
    item.scripts['p'].scenes.append(Scene(id='second', name='next', description='original'))
    item._save_data()
    state.on_generate = lambda call: item.update_asset_description('p', 'second', 'scene', 'changed')
    with pytest.raises(StorageConflictError):
        item.generate_assets('p')
    assert len(state.calls) == 1
    assert persisted(item)['p']['scenes'][0]['status'] == 'completed'


@pytest.mark.parametrize('kwargs', [
    {'prompt': '\ud800'}, {'batch_size': 0}, {'batch_size': True},
    {'apply_style': 'yes'}, {'generation_type': 'unknown'},
])
def test_invalid_inputs_make_no_live_changes_or_provider_calls(runtime, kwargs):
    item, state = runtime
    before = persisted(item)
    with pytest.raises(module.CommandValidationError):
        item.generate_asset('p', 'c', 'character', **kwargs)
    assert persisted(item) == before
    assert item.scripts['p'].model_dump() == before['p']
    assert not state.calls
    item.update_project_style('p', 'still usable')


@pytest.mark.parametrize('channel', ['scene', 'prop', 'reference_sheet', 'full_body', 'three_view', 'headshot'])
@pytest.mark.parametrize('output', ['\ud800', {'bad': 'url'}])
def test_invalid_output_never_publishes_and_finishes_valid_target(runtime, monkeypatch, output, channel):
    item, state = runtime
    monkeypatch.setattr('src.apps.comic_gen.assets.time.sleep', lambda seconds: None)
    if channel == 'prop':
        from src.apps.comic_gen.models import Prop
        item.scripts['p'].props.append(Prop(id='prop', name='lamp', description='brass'))
        item._save_data()
    kind = channel if channel in ('scene', 'prop') else 'character'
    asset_id = {'scene': 's', 'prop': 'prop', 'character': 'c'}[kind]
    field = {'scene': 'scenes', 'prop': 'props', 'character': 'characters'}[kind]
    original = persisted(item)['p'][field][0]
    state.on_upload = lambda path: output
    with pytest.raises(module.ProjectGenerationError):
        item.generate_asset('p', asset_id, kind, generation_type=channel if kind == 'character' else 'all', batch_size=3)
    assert len(state.calls) == 1
    stored = persisted(item)['p'][field][0]
    assert stored['image_url'] == original['image_url']
    if kind == 'character':
        container = stored['reference_sheet'] if channel == 'reference_sheet' else stored[channel + '_asset']
        before = original['reference_sheet'] if channel == 'reference_sheet' else original[channel + '_asset']
    else:
        container, before = stored['image_asset'], original['image_asset']
    variants_key, selection_key = ('image_variants', 'selected_image_id') if channel == 'reference_sheet' else ('variants', 'selected_id')
    variants = container[variants_key]
    assert len(variants) == (len(before[variants_key]) if before else 0) + 1
    assert container[selection_key] == (before[selection_key] if before else None)
    local_url = str(state.calls[0]['path'].relative_to(item.data_dir))
    assert any(variant['url'] == local_url for variant in variants)
    assert stored['status'] == 'failed'
    item.update_project_style('p', 'still usable')
    assert_paid_files(state)


def test_same_shared_asset_from_two_episodes_cannot_generate_concurrently(runtime):
    item, state = runtime
    source = item.scripts['p'].scenes.pop()
    item.library_store.scenes.append(source)
    item.scripts['q'] = Script.model_validate({
        'id': 'q', 'title': 'other', 'original_text': '', 'created_at': 1, 'updated_at': 1,
        'model_settings': item.scripts['p'].model_settings.model_copy(deep=True),
    })
    item._commit_storage('projects.json', 'library_assets.json')
    entered, release = Event(), Event()
    def pause(call):
        entered.set()
        assert release.wait(3)
    state.on_generate = pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(item.generate_asset, 'p', 's', 'scene')
        try:
            assert entered.wait(2)
            with pytest.raises(StorageConflictError):
                item.generate_asset('q', 's', 'scene')
            assert len(state.calls) == 1
        finally:
            release.set()
        first.result(timeout=2)
    state.on_generate = None
    item.generate_asset('q', 's', 'scene')
    assert len(state.calls) == 2
    assert_paid_files(state)


@pytest.mark.parametrize('previous_status', ['completed', 'failed'])
def test_regeneration_always_persists_its_new_terminal_status(runtime, previous_status):
    item, state = runtime
    item.scripts['p'].scenes[0].status = GenerationStatus(previous_status)
    item._save_data()
    result = item.generate_asset('p', 's', 'scene')
    assert result.scenes[0].status == 'completed'
    assert persisted(item)['p']['scenes'][0]['status'] == 'completed'
    assert_paid_files(state)


@pytest.mark.parametrize('has_selection', [False, True])
def test_terminal_partial_scene_failure_keeps_original_selection_and_paid_candidate(runtime, has_selection):
    item, state = runtime
    scene = item.scripts['p'].scenes[0]
    if not has_selection:
        scene.image_asset = ImageAsset()
        scene.image_url = None
        item._save_data()
    def fail_second(call):
        if len(state.calls) == 2:
            raise RuntimeError('second slot failed')
    state.on_generate = fail_second
    with pytest.raises(RuntimeError, match='second slot'):
        item.generate_asset('p', 's', 'scene', batch_size=2)
    saved = persisted(item)['p']['scenes'][0]
    assert saved['status'] == 'failed'
    assert saved['image_asset']['selected_id'] == ('old-a' if has_selection else None)
    assert saved['image_url'] == ('uploads/old-a.png' if has_selection else None)
    assert len(saved['image_asset']['variants']) == (3 if has_selection else 1)
    assert len(state.calls) == 2
    assert_paid_files(state)


def test_character_channel_partial_success_preserves_existing_completion_rule(runtime, monkeypatch):
    item, state = runtime
    monkeypatch.setattr('src.apps.comic_gen.assets.time.sleep', lambda seconds: None)
    def fail_second(call):
        if len(state.calls) == 2:
            raise RuntimeError('second requested slot failed')
    state.on_generate = fail_second
    result = item.generate_asset('p', 'c', 'character', generation_type='full_body', batch_size=2)
    assert result.characters[0].status == 'completed'
    assert len(result.characters[0].full_body_asset.variants) == 3
    assert result.characters[0].full_body_asset.selected_id == 'old-a'
    assert len(state.calls) == 2
    assert_paid_files(state)


def test_later_channel_failure_restores_original_selection_even_after_candidate_cleanup(runtime, monkeypatch):
    item, state = runtime
    monkeypatch.setattr('src.apps.comic_gen.assets.time.sleep', lambda seconds: None)
    def fail_derived(call):
        if len(state.calls) > 11:
            raise RuntimeError('derived channel failed')
    state.on_generate = fail_derived
    with pytest.raises(RuntimeError, match='derived channel'):
        item.generate_asset('p', 'c', 'character', generation_type='all', batch_size=11)
    character = item.read_committed_script('p').characters[0]
    assert character.status == 'failed'
    assert character.full_body_asset.selected_id == 'old-a'
    assert any(v.id == 'old-a' for v in character.full_body_asset.variants)
    assert character.full_body_image_url == 'uploads/old-a.png'
    assert len(state.calls) == 22  # Requested slots; no automatic resubmission.
    assert_paid_files(state)


@pytest.mark.parametrize('style', [{'positive_prompt': {'invalid': 'text'}}, {'negative_prompt': ['invalid', 'text']}])
def test_invalid_effective_style_is_rejected_before_processing_or_model(runtime, style):
    from src.apps.comic_gen.models import ArtDirection
    item, state = runtime
    item.scripts['p'].art_direction = ArtDirection(selected_style_id='custom', style_config=style)
    item._save_data()
    before = persisted(item)
    with pytest.raises(module.CommandValidationError):
        item.generate_asset('p', 's', 'scene')
    assert persisted(item) == before
    assert state.calls == []


def test_style_conflict_finishes_owned_processing_target_without_publishing(runtime):
    item, state = runtime
    state.on_generate = lambda call: item.update_project_style('p', 'changed-style')
    with pytest.raises(StorageConflictError):
        item.generate_asset('p', 's', 'scene', batch_size=2)
    saved = persisted(item)['p']['scenes'][0]
    assert saved['status'] == 'failed'
    assert saved['image_url'] == 'uploads/old-a.png'
    assert len(state.calls) == 1
    assert_paid_files(state)


def test_previously_failed_asset_persists_failure_again(runtime):
    item, state = runtime
    item.scripts['p'].scenes[0].status = GenerationStatus.FAILED
    item._save_data()
    failure = RuntimeError('provider still unavailable')
    def fail(call):
        raise failure
    state.on_generate = fail
    with pytest.raises(RuntimeError) as raised:
        item.generate_asset('p', 's', 'scene')
    assert raised.value is failure
    assert persisted(item)['p']['scenes'][0]['status'] == 'failed'
    assert_paid_files(state)


def test_unrelated_title_edit_does_not_discard_paid_asset(runtime):
    item, state = runtime
    def edit(call):
        with item._save_lock:
            item.scripts['p'].title = 'new title'
            item._save_data()
    state.on_generate = edit
    result = item.generate_asset('p', 's', 'scene')
    assert result.title == 'new title'
    assert result.scenes[0].status == 'completed'
    assert_paid_files(state)


@pytest.mark.parametrize('returned_status', [GenerationStatus.PROCESSING, GenerationStatus.FAILED])
def test_generator_without_success_terminal_cannot_reuse_old_success(runtime, monkeypatch, returned_status):
    item, state = runtime
    item.scripts['p'].scenes[0].status = GenerationStatus.COMPLETED
    item._save_data()
    generate = item.asset_generator.generate_scene
    def changed_terminal(*args, **kwargs):
        candidate = generate(*args, **kwargs)  # Real generator and paid boundary.
        candidate.status = returned_status
        return candidate
    monkeypatch.setattr(item.asset_generator, 'generate_scene', changed_terminal)
    with pytest.raises(module.ProjectGenerationError):
        item.generate_asset('p', 's', 'scene')
    assert persisted(item)['p']['scenes'][0]['status'] == 'failed'
    assert persisted(item)['p']['scenes'][0]['image_url'] == 'uploads/old-a.png'
    assert len(state.calls) == 1
    assert_paid_files(state)


@pytest.mark.parametrize('edit', ['description', 'replacement', 'deletion', 'local_shadow'])
def test_late_shared_result_cannot_attach_after_target_change(runtime, edit):
    item, state = runtime
    source = item.scripts['p'].scenes.pop()
    item.library_store.scenes.append(source)
    item._commit_storage('projects.json', 'library_assets.json')
    observed = []
    def change(call):
        with item._save_lock:
            if edit == 'description':
                item.update_asset_description('p', 's', 'scene', 'new description')
            elif edit == 'replacement':
                item.library_store.scenes[0] = source.model_copy(deep=True)
                item._save_library_data()
            elif edit == 'deletion':
                item.library_store.scenes.clear()
                item._save_library_data()
            else:
                item.scripts['p'].scenes.append(Scene(id='s', name='local', description='new local owner'))
                item._save_data()
            observed.append((persisted(item), persisted(item, 'library_assets.json')))
    state.on_generate = change
    with pytest.raises(StorageConflictError):
        item.generate_asset('p', 's', 'scene')
    if edit == 'description':
        observed[0][1]['scenes'][0]['status'] = 'failed'
        observed[0][1]['revision'] += 1
    assert (persisted(item), persisted(item, 'library_assets.json')) == observed[0]
    assert_paid_files(state)


def test_new_invalid_style_during_call_conflicts_and_never_starts_next_slot(runtime):
    from src.apps.comic_gen.models import ArtDirection
    item, state = runtime
    def edit(call):
        with item._save_lock:
            item.scripts['p'].art_direction = ArtDirection(selected_style_id='custom', style_config={'positive_prompt': ['bad', 'type']})
            item._save_data()
    state.on_generate = edit
    with pytest.raises(StorageConflictError):
        item.generate_asset('p', 'c', 'character', generation_type='all', batch_size=2)
    assert len(state.calls) == 1
    assert persisted(item)['p']['characters'][0]['status'] == 'failed'
    assert_paid_files(state)


def test_new_batch_member_is_not_generated_by_existing_batch(runtime):
    item, state = runtime
    item.scripts['p'].characters.clear()
    item._save_data()
    def add(call):
        with item._save_lock:
            item.scripts['p'].scenes.append(Scene(id='late', name='late', description='new member'))
            item._save_data()
    state.on_generate = add
    result = item.generate_assets('p')
    assert len(state.calls) == 1
    assert [scene.status for scene in result.scenes] == [GenerationStatus.COMPLETED, GenerationStatus.PENDING]


def test_independent_asset_calls_do_not_replace_each_others_callback(runtime):
    item, state = runtime
    item.scripts['p'].scenes.append(Scene(id='other', name='other', description='room'))
    item._save_data()
    entered, release = Event(), Event()
    def pause_first(call):
        if call is state.calls[0]:
            entered.set()
            assert release.wait(3)
    state.on_generate = pause_first
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(item.generate_asset, 'p', 's', 'scene')
        try:
            assert entered.wait(2)
            second = pool.submit(item.generate_asset, 'p', 'other', 'scene')
            assert second.result(timeout=2).scenes[1].status == GenerationStatus.COMPLETED
        finally:
            release.set()
        first.result(timeout=2)
    assert [scene.status for scene in item.read_committed_script('p').scenes] == [GenerationStatus.COMPLETED] * 2
    assert len(state.calls) == 2
    assert_paid_files(state)


def test_conflict_terminal_save_failure_preserves_storage_error(runtime, monkeypatch):
    item, state = runtime
    commit = item._storage.commit
    failure = StorageError('conflict cleanup save failed')
    def fail_terminal(payloads):
        if payloads.get('projects.json', {}).get('p', {}).get('scenes', [{}])[0].get('status') == 'failed':
            raise failure
        return commit(payloads)
    monkeypatch.setattr(item._storage, 'commit', fail_terminal)
    state.on_generate = lambda call: item.update_project_style('p', 'new-style')
    with pytest.raises(StorageError) as raised:
        item.generate_asset('p', 's', 'scene')
    assert raised.value is failure
    assert persisted(item)['p']['scenes'][0]['image_url'] == 'uploads/old-a.png'
    assert_paid_files(state)


def test_generator_result_cannot_edit_non_image_asset_metadata(runtime, monkeypatch):
    item, state = runtime
    generate = item.asset_generator.generate_character
    def unexpected_metadata(*args, **kwargs):
        candidate = generate(*args, **kwargs)
        candidate.description = 'unrelated model metadata'
        candidate.voice_id = 'unexpected voice'
        candidate.full_body.video_prompt = 'unexpected video prompt'
        return candidate
    monkeypatch.setattr(item.asset_generator, 'generate_character', unexpected_metadata)
    result = item.generate_asset('p', 'c', 'character', generation_type='full_body')
    assert result.characters[0].description == 'red coat'
    assert result.characters[0].voice_id is None
    assert result.characters[0].full_body.video_prompt is None
    assert result.characters[0].status == GenerationStatus.COMPLETED
    assert_paid_files(state)


@pytest.mark.parametrize('channel', ['scene', 'prop', 'reference_sheet', 'full_body', 'three_view', 'headshot'])
def test_ordinary_upload_failure_keeps_local_candidate_and_requested_slots(runtime, monkeypatch, channel):
    item, state = runtime
    monkeypatch.setattr('src.apps.comic_gen.assets.time.sleep', lambda seconds: None)
    if channel == 'prop':
        from src.apps.comic_gen.models import Prop
        item.scripts['p'].props.append(Prop(id='prop', name='lamp', description='brass'))
        item._save_data()
    kind = channel if channel in ('scene', 'prop') else 'character'
    asset_id = {'scene': 's', 'prop': 'prop', 'character': 'c'}[kind]
    def upload_failed(path):
        raise OSError('synthetic optional mirror unavailable')
    state.on_upload = upload_failed
    result = item.generate_asset('p', asset_id, kind, generation_type=channel if kind == 'character' else 'all', batch_size=2)
    target = getattr(result, {'scene': 'scenes', 'prop': 'props', 'character': 'characters'}[kind])[0]
    assert target.status == GenerationStatus.COMPLETED
    container = target.reference_sheet if channel == 'reference_sheet' else (
        getattr(target, channel + '_asset') if kind == 'character' else target.image_asset
    )
    variants = container.image_variants if channel == 'reference_sheet' else container.variants
    assert len(state.calls) == 2
    for call in state.calls:
        assert any(variant.url == str(call['path'].relative_to(item.data_dir)) for variant in variants)
    assert_paid_files(state)


@pytest.mark.parametrize('change', ['new_token', 'terminal', 'live_terminal', 'saved_terminal'])
def test_conflict_cleanup_never_overwrites_another_execution_or_terminal(runtime, change):
    item, state = runtime
    observed = []
    new_token = object()
    def supersede(call):
        with item._save_lock:
            target = item.scripts['p'].scenes[0]
            if change == 'new_token':
                key = next(iter(item._asset_image_runs))
                item._asset_image_runs[key] = new_token
            else:
                target.status = GenerationStatus.COMPLETED
                if change != 'live_terminal':
                    item._save_data()
                if change == 'saved_terminal':
                    target.status = GenerationStatus.PROCESSING
            observed.append((persisted(item), target.model_dump()))
    state.on_generate = supersede
    with pytest.raises(StorageConflictError):
        item.generate_asset('p', 's', 'scene')
    assert (persisted(item), item.scripts['p'].scenes[0].model_dump()) == observed[0]
    if change == 'new_token':
        assert list(item._asset_image_runs.values()) == [new_token]
    assert_paid_files(state)
