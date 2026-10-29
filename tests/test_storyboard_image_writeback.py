"""Behavioral image writeback probes; run only with run_tests_isolated.py."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import (
    Character, GenerationStatus, ImageAsset, ImageVariant, Script, StoryboardFrame,
)
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # Keep the actual StoryboardGenerator and its actual model instance.
    for name in ("AssetGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(
        id="p", title="project", original_text="script", created_at=1, updated_at=1,
        characters=[Character(id="c", name="Alice", description="red coat")],
        frames=[StoryboardFrame(
            id="f", scene_id="", character_ids=["c"], action_description="walks",
            image_prompt="original prompt", image_url="uploads/old-a.png",
            rendered_image_url="uploads/old-a.png",
            rendered_image_asset=ImageAsset(selected_id="old-a", variants=[
                ImageVariant(id="old-a", url="uploads/old-a.png", created_at=1),
                ImageVariant(id="old-b", url="uploads/old-b.png", created_at=2),
            ]),
        )],
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    state = SimpleNamespace(calls=[], on_generate=None, on_upload=None)

    def generate(prompt, output_path, **kwargs):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"paid-image-retained")
        state.calls.append({"prompt": prompt, "path": path, "kwargs": kwargs})
        if state.on_generate:
            state.on_generate()
        return str(path), 0

    def upload(path, **kwargs):
        if state.on_upload:
            return state.on_upload(path)
        return None

    monkeypatch.setattr(item.storyboard_generator.model, "generate", generate)
    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", lambda: SimpleNamespace(
        is_configured=True, upload_file=upload,
    ))
    yield item, state
    item.close()


def render(item):
    return item.generate_storyboard_render("p", "f", None, "requested prompt")


def persisted(item):
    return json.loads((item.data_dir / "projects.json").read_text(encoding="utf-8"))


def assert_paid_files_remain(state):
    assert state.calls
    assert all(call["path"].read_bytes() == b"paid-image-retained" for call in state.calls)


def render_outcome(item):
    try:
        return render(item), None
    except (StorageConflictError, module.ProjectContextError) as error:
        return None, error


def test_unrelated_save_during_upload_cannot_publish_private_generated_candidate(runtime):
    item, state = runtime
    entered, release = Event(), Event()

    def upload(path):
        entered.set()
        assert release.wait(3), "test did not release fake upload"
        return None

    state.on_upload = upload
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = pool.submit(render_outcome, item)
        try:
            assert entered.wait(2), "real generator did not reach fake upload"
            # This distinct command must finish while the network is blocked.
            edit = pool.submit(item.update_project_style, "p", "unrelated edit")
            edit.result(timeout=2)
            during = persisted(item)["p"]["frames"][0]
        finally:
            release.set()
        result.result(timeout=2)

    assert during["rendered_image_asset"]["selected_id"] == "old-a"
    assert [variant["id"] for variant in during["rendered_image_asset"]["variants"]] == ["old-a", "old-b"]
    assert during["image_url"] == "uploads/old-a.png"
    assert during["status"] != GenerationStatus.COMPLETED.value
    assert_paid_files_remain(state)


@pytest.mark.parametrize("edit", ["selection", "prompt"])
def test_late_render_cannot_attach_to_an_edited_target(runtime, edit):
    item, state = runtime

    def change_target():
        if edit == "selection":
            item.select_asset_variant("p", "f", "storyboard_frame", "old-b")
        else:
            item.update_frame("p", "f", expected_revision=item.read_committed_script("p").revision, image_prompt="new user prompt")

    state.on_generate = change_target
    _, error = render_outcome(item)
    stored = persisted(item)["p"]["frames"][0]
    if edit == "selection":
        assert stored["rendered_image_asset"]["selected_id"] == "old-b"
        assert stored["image_url"] == "uploads/old-b.png"
    else:
        assert stored["image_prompt"] == "new user prompt"
    assert [variant["id"] for variant in stored["rendered_image_asset"]["variants"]] == ["old-a", "old-b"]
    assert isinstance(error, StorageConflictError), "a late image must not be acknowledged as committed"
    assert_paid_files_remain(state)


@pytest.mark.parametrize("target", ["frame", "project"])
def test_deleted_generation_target_is_not_acknowledged_as_saved(runtime, target):
    item, state = runtime
    state.on_generate = (
        (lambda: item.delete_frame("p", "f")) if target == "frame"
        else (lambda: item.delete_project("p"))
    )
    result, error = render_outcome(item)
    data = persisted(item)
    if target == "frame":
        assert data["p"]["frames"] == []
    else:
        assert "p" not in data
    assert result is None and error is not None, "deleted targets must not return a successful render ACK"
    assert len(state.calls) == 1
    assert_paid_files_remain(state)


def test_first_batch_result_must_be_saved_before_a_second_paid_image(runtime, monkeypatch):
    item, state = runtime
    item.scripts["p"].frames.append(StoryboardFrame(id="f2", scene_id="", action_description="waits"))
    item._save_data()
    commit = item._storage.commit

    def fail_completed_result(payloads):
        if any(
            frame["status"] == GenerationStatus.COMPLETED.value
            for frame in payloads.get("projects.json", {}).get("p", {}).get("frames", [])
        ):
            raise StorageError("synthetic disk full while committing paid image")
        return commit(payloads)

    monkeypatch.setattr(item._storage, "commit", fail_completed_result)
    with pytest.raises(StorageError, match="paid image"):
        item.generate_storyboard("p")
    assert len(state.calls) == 1, "frame two was charged before frame one's result could be saved"
    assert persisted(item)["p"]["frames"][0]["image_url"] == "uploads/old-a.png"
    assert_paid_files_remain(state)


def test_generated_variant_records_the_prompt_actually_sent_to_the_model(runtime):
    item, state = runtime
    render(item)
    stored = persisted(item)["p"]["frames"][0]
    generated = next(variant for variant in stored["rendered_image_asset"]["variants"] if variant["id"] not in {"old-a", "old-b"})
    assert generated["prompt_used"] == state.calls[0]["prompt"]
    assert state.calls[0]["prompt"] == "requested prompt Characters: Alice (red coat)."
    assert_paid_files_remain(state)


def test_same_frame_duplicate_is_rejected_before_another_paid_call(runtime):
    item, state = runtime
    entered, release = Event(), Event()

    def pause():
        entered.set()
        assert release.wait(3)

    state.on_generate = pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(render, item)
        try:
            assert entered.wait(2)
            with pytest.raises(StorageConflictError):
                render(item)
            assert len(state.calls) == 1
        finally:
            release.set()
        first.result(timeout=2)
    state.on_generate = None
    render(item)  # The first request must release its token.
    assert len(state.calls) == 2
    assert len({call['path'] for call in state.calls}) == 2
    assert_paid_files_remain(state)


@pytest.mark.parametrize('where', ['model', 'upload'])
@pytest.mark.parametrize('ordinary_error', [False, True])
def test_close_during_network_never_publishes_candidate(runtime, where, ordinary_error):
    item, state = runtime

    def close(*args):
        item.close()
        if ordinary_error:
            raise RuntimeError('network ended after close')
        return 'storyboard/remote.png'

    if where == 'model':
        state.on_generate = close
    else:
        state.on_upload = close
    with pytest.raises(StorageError):
        item.generate_storyboard_render('p', 'f', None, 'request', batch_size=2 if where == 'model' else 1)
    assert len(state.calls) == 1
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    assert_paid_files_remain(state)


@pytest.mark.parametrize('layer', ['series', 'library'])
def test_changed_shared_character_stops_late_image(runtime, layer):
    from src.apps.comic_gen.models import Series
    item, state = runtime
    character = item.scripts['p'].characters.pop()
    if layer == 'series':
        item.scripts['p'].series_id = 's'
        item.series_store['s'] = Series(id='s', title='series', created_at=1, updated_at=1, characters=[character])
    else:
        item.library_store.characters.append(character)
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')

    def change():
        with item._save_lock:
            character.description = 'blue coat'
            item._commit_storage('series.json' if layer == 'series' else 'library_assets.json')

    state.on_generate = change
    with pytest.raises(StorageConflictError):
        render(item)
    assert 'red coat' in state.calls[0]['prompt']
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    assert persisted(item)['p']['frames'][0]['status'] == GenerationStatus.FAILED.value
    assert_paid_files_remain(state)


def test_unrelated_title_and_voice_edits_survive_successful_writeback(runtime):
    item, state = runtime

    def edit():
        with item._save_lock:
            item.scripts['p'].title = 'new title'
            item.scripts['p'].characters[0].voice_id = 'different voice'
            item._save_data()

    state.on_generate = edit
    result = render(item)
    assert result.title == 'new title'
    assert result.characters[0].voice_id == 'different voice'
    assert result.frames[0].status == GenerationStatus.COMPLETED
    result.frames[0].rendered_image_asset.variants.clear()
    assert len(item.scripts['p'].frames[0].rendered_image_asset.variants) == 3


def test_batch_freezes_membership_but_own_first_commit_does_not_conflict(runtime):
    item, state = runtime
    item.scripts['p'].frames.append(StoryboardFrame(id='f2', scene_id='', action_description='second'))
    item._save_data()

    def add():
        if len(state.calls) == 1:
            with item._save_lock:
                item.scripts['p'].frames.append(StoryboardFrame(id='late', scene_id='', action_description='late'))
                item._save_data()

    state.on_generate = add
    result = item.generate_storyboard('p')
    assert len(state.calls) == 2
    assert [frame.status for frame in result.frames] == [GenerationStatus.COMPLETED, GenerationStatus.COMPLETED, GenerationStatus.PENDING]
    assert all(call['kwargs']['model_name'] is None for call in state.calls)
    assert_paid_files_remain(state)


def test_batch_does_not_refreeze_edited_later_frame(runtime):
    item, state = runtime
    item.scripts['p'].frames.append(StoryboardFrame(id='f2', scene_id='', action_description='second'))
    item._save_data()
    state.on_generate = lambda: item.update_frame('p', 'f2', expected_revision=item.read_committed_script("p").revision, action_description='changed')
    with pytest.raises(StorageConflictError):
        item.generate_storyboard('p')
    assert len(state.calls) == 1
    assert persisted(item)['p']['frames'][0]['status'] == GenerationStatus.COMPLETED.value
    assert persisted(item)['p']['frames'][1]['action_description'] == 'changed'


@pytest.mark.parametrize('kwargs', [
    {'prompt': '\ud800'}, {'composition_data': {'x': float('nan')}},
    {'composition_data': {'reference_image_urls': 'not a list'}},
    {'batch_size': 0}, {'batch_size': True},
])
def test_invalid_request_never_mutates_or_latches_runtime(runtime, kwargs):
    item, state = runtime
    before = persisted(item)
    arguments = {'composition_data': None, 'prompt': 'request', 'batch_size': 1, **kwargs}
    with pytest.raises(module.CommandValidationError):
        item.generate_storyboard_render('p', 'f', **arguments)
    assert persisted(item) == before
    assert item.scripts['p'].model_dump() == before['p']
    assert not state.calls
    item.update_project_style('p', 'still usable')


@pytest.mark.parametrize('output', ['\ud800', {'not': 'a url'}])
def test_invalid_provider_metadata_never_publishes_or_latches_runtime(runtime, output):
    item, state = runtime
    state.on_upload = lambda path: output
    with pytest.raises(module.ProjectGenerationError):
        render(item)
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    assert persisted(item)['p']['frames'][0]['status'] == GenerationStatus.FAILED.value
    assert len(item.scripts['p'].frames[0].rendered_image_asset.variants) == 2
    item.update_project_style('p', 'still usable')
    assert_paid_files_remain(state)


@pytest.mark.parametrize('has_selection', [False, True])
def test_partial_batch_variants_stay_private_until_failed_result_commit(runtime, has_selection):
    item, state = runtime
    if not has_selection:
        frame = item.scripts['p'].frames[0]
        frame.rendered_image_asset = None
        frame.image_url = None
        frame.rendered_image_url = None
        item._save_data()
    observed = []

    def fail_second():
        if len(state.calls) == 2:
            item.update_project_style('p', item.scripts['p'].style_preset)
            observed.append(persisted(item)['p']['frames'][0])
            raise RuntimeError('ordinary second provider failure')

    state.on_generate = fail_second
    result = item.generate_storyboard_render('p', 'f', None, 'request', batch_size=2)
    assert len((observed[0]['rendered_image_asset'] or {}).get('variants', [])) == (2 if has_selection else 0)
    assert result.frames[0].status == GenerationStatus.FAILED
    assert len(result.frames[0].rendered_image_asset.variants) == (3 if has_selection else 1)
    expected_selection = 'old-a' if has_selection else None
    expected_url = 'uploads/old-a.png' if has_selection else None
    assert result.frames[0].rendered_image_asset.selected_id == expected_selection
    assert result.frames[0].image_url == result.frames[0].rendered_image_url == expected_url
    assert len(state.calls) == 2
    assert_paid_files_remain(state)

    # A failed attempt's retained candidate is not evidence that a later
    # all-failed attempt succeeded or selected anything.
    def fail_again():
        raise RuntimeError('all new calls failed')

    state.on_generate = fail_again
    retry = render(item)
    assert retry.frames[0].status == GenerationStatus.FAILED
    assert len(retry.frames[0].rendered_image_asset.variants) == (3 if has_selection else 1)
    assert retry.frames[0].rendered_image_asset.selected_id == expected_selection
    assert retry.frames[0].image_url == retry.frames[0].rendered_image_url == expected_url
    assert len(state.calls) == 3
    assert_paid_files_remain(state)


def test_ordinary_provider_failure_preserves_batch_continue_contract(runtime):
    item, state = runtime
    item.scripts['p'].frames.append(StoryboardFrame(id='f2', scene_id='', action_description='second'))
    item._save_data()

    def fail_first():
        if len(state.calls) == 1:
            raise RuntimeError('ordinary provider failure')

    state.on_generate = fail_first
    result = item.generate_storyboard('p')
    assert [frame.status for frame in result.frames] == [GenerationStatus.FAILED, GenerationStatus.COMPLETED]
    assert len(state.calls) == 2
    assert_paid_files_remain(state)


def test_rejected_duplicate_cannot_release_the_first_failed_runs_token(runtime):
    item, state = runtime
    entered, release = Event(), Event()

    def fail_late():
        entered.set()
        assert release.wait(3)
        raise RuntimeError('first call failed')

    state.on_generate = fail_late
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(render, item)
        try:
            assert entered.wait(2)
            for _ in range(2):
                with pytest.raises(StorageConflictError):
                    render(item)
            assert len(state.calls) == 1
        finally:
            release.set()
        assert first.result(timeout=2).frames[0].status == GenerationStatus.FAILED
    state.on_generate = None
    assert render(item).frames[0].status == GenerationStatus.COMPLETED
    assert len(state.calls) == 2
    assert_paid_files_remain(state)


def test_runtime_failed_by_another_save_stops_after_paid_result(runtime, monkeypatch):
    item, state = runtime
    failure = StorageError('other command failed to save')

    def fail_save(payloads):
        raise failure

    def latch():
        monkeypatch.setattr(item._storage, 'commit', fail_save)
        with pytest.raises(StorageError) as raised:
            item.update_project_style('p', 'different style')
        assert raised.value is failure

    state.on_generate = latch
    with pytest.raises(StorageError):
        item.generate_storyboard_render('p', 'f', None, 'request', batch_size=2)
    assert len(state.calls) == 1
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    assert item.scripts['p'].frames[0].image_url == 'uploads/old-a.png'
    assert_paid_files_remain(state)


@pytest.mark.parametrize('kind', ['character', 'scene'])
@pytest.mark.parametrize('explicit', [False, True])
def test_reference_dependency_tracks_only_consumed_channel(runtime, kind, explicit):
    from src.apps.comic_gen.models import Scene
    item, state = runtime
    source = Path(item.storyboard_generator.output_root) / 'uploads/reference.png'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'reference')
    asset = ImageAsset(selected_id='ref-a', variants=[
        ImageVariant(id='ref-a', url='uploads/reference.png'),
        ImageVariant(id='ref-b', url='uploads/changed.png'),
    ])
    if kind == 'character':
        item.scripts['p'].characters[0].three_view_asset = asset
    else:
        item.scripts['p'].scenes.append(Scene(id='scene', name='room', description='quiet', image_asset=asset))
        item.scripts['p'].frames[0].scene_id = 'scene'
    item._save_data()

    def change():
        with item._save_lock:
            asset.selected_id = 'ref-b'
            item._save_data()

    state.on_generate = change
    composition = {'reference_image_urls': ['https://example.test/frozen.png']} if explicit else None
    if explicit:
        result = item.generate_storyboard_render('p', 'f', composition, 'request')
        assert result.frames[0].status == GenerationStatus.COMPLETED
        assert state.calls[0]['kwargs']['ref_image_paths'] == ['https://example.test/frozen.png']
    else:
        with pytest.raises(StorageConflictError):
            item.generate_storyboard_render('p', 'f', composition, 'request')
        assert state.calls[0]['kwargs']['ref_image_paths'] == [str(source)]
    assert_paid_files_remain(state)


@pytest.mark.parametrize('change', ['model', 'style', 'owner', 'replace_frame'])
def test_generation_rechecks_model_style_owner_and_target_identity(runtime, change):
    from src.apps.comic_gen.models import Series
    item, state = runtime

    def edit():
        if change == 'model':
            item.update_model_settings('p', i2i_model='changed-model')
        elif change == 'style':
            item.update_project_style('p', 'changed-style')
        else:
            with item._save_lock:
                if change == 'owner':
                    item.series_store['new-series'] = Series(id='new-series', title='new', created_at=1, updated_at=1)
                    item.scripts['p'].series_id = 'new-series'
                    item._commit_storage('projects.json', 'series.json')
                else:
                    item.scripts['p'].frames[0] = item.scripts['p'].frames[0].model_copy(deep=True)
                    item._save_data()

    state.on_generate = edit
    with pytest.raises(StorageConflictError):
        render(item)
    assert len(state.calls) == 1
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    if change in {'model', 'style', 'owner'}:
        assert persisted(item)['p']['frames'][0]['status'] == GenerationStatus.FAILED.value
    assert_paid_files_remain(state)


def test_caller_cannot_mutate_accepted_composition_or_reference_list(runtime):
    item, state = runtime
    composition = {'reference_image_urls': ['https://example.test/accepted.png'], 'layout': {'x': 1}}

    def edit_request():
        composition['reference_image_urls'].append('https://example.test/late.png')
        composition['layout']['x'] = 2

    state.on_generate = edit_request
    result = item.generate_storyboard_render('p', 'f', composition, 'request')
    assert state.calls[0]['kwargs']['ref_image_paths'] == ['https://example.test/accepted.png']
    assert result.frames[0].composition_data == {'reference_image_urls': ['https://example.test/accepted.png'], 'layout': {'x': 1}}
    result.frames[0].composition_data['layout']['x'] = 3
    assert item.scripts['p'].frames[0].composition_data['layout']['x'] == 1


def test_invalid_output_failed_status_save_error_propagates(runtime, monkeypatch):
    item, state = runtime
    commit = item._storage.commit
    failure = StorageError('failed status save failed')

    def fail_failed_status(payloads):
        if payloads.get('projects.json', {}).get('p', {}).get('frames', [{}])[0].get('status') == GenerationStatus.FAILED.value:
            raise failure
        return commit(payloads)

    monkeypatch.setattr(item._storage, 'commit', fail_failed_status)
    state.on_upload = lambda path: '\ud800'
    with pytest.raises(StorageError) as raised:
        render(item)
    assert raised.value is failure
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    assert_paid_files_remain(state)


@pytest.mark.parametrize('empty', [False, True])
def test_batch_final_missing_target_is_not_a_success_or_generic_error(runtime, monkeypatch, empty):
    item, _ = runtime
    if empty:
        item.scripts['p'].frames.clear()
        item._save_data()
    original = item.read_committed_project_view
    returned = False

    def view(script_id):
        nonlocal returned
        result = original(script_id)
        # Simulate deletion after the frozen batch has been captured or after
        # its last result has committed, at the final public read boundary.
        if not returned and (empty or (result[0] and result[0].frames[0].status == GenerationStatus.COMPLETED)):
            returned = True
            item.delete_project('p')
        return result

    monkeypatch.setattr(item, 'read_committed_project_view', view)
    # For empty batches deletion occurs inside the capture; for nonempty
    # batches force the final read entry using the committed read wrapper.
    if not empty:
        saved = item.read_committed_script

        def committed(script_id):
            result = saved(script_id)
            if result.frames[0].status == GenerationStatus.COMPLETED:
                item.delete_project('p')
            return result

        monkeypatch.setattr(item, 'read_committed_script', committed)
    with pytest.raises(module.ProjectContextError) as raised:
        item.generate_storyboard('p')
    assert raised.value.not_found


def test_conflict_terminal_save_failure_propagates_and_retains_old_media(runtime, monkeypatch):
    item, state = runtime
    commit = item._storage.commit
    failure = StorageError('could not persist conflict terminal state')

    def fail_terminal(payloads):
        if payloads.get('projects.json', {}).get('p', {}).get('frames', [{}])[0].get('status') == GenerationStatus.FAILED.value:
            raise failure
        return commit(payloads)

    monkeypatch.setattr(item._storage, 'commit', fail_terminal)
    state.on_generate = lambda: item.update_project_style('p', 'changed-style')
    with pytest.raises(StorageError) as raised:
        render(item)
    assert raised.value is failure
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    assert_paid_files_remain(state)


@pytest.mark.parametrize('change', ['edit', 'replace', 'new_token'])
def test_conflict_cleanup_never_writes_to_an_edited_or_new_execution_target(runtime, change):
    item, state = runtime
    observed = []
    other_token = object()

    def change_target():
        with item._save_lock:
            if change == 'edit':
                item.update_frame('p', 'f', expected_revision=item.read_committed_script("p").revision, image_prompt='new user prompt')
            elif change == 'replace':
                item.scripts['p'].frames[0] = item.scripts['p'].frames[0].model_copy(deep=True)
                item._save_data()
            else:
                item._storyboard_image_runs[('p', 'f')] = other_token
            observed.append(persisted(item)['p']['frames'][0])

    state.on_generate = change_target
    with pytest.raises(StorageConflictError):
        render(item)
    assert persisted(item)['p']['frames'][0] == observed[0]
    if change == 'new_token':
        assert item._storyboard_image_runs[('p', 'f')] is other_token
    assert_paid_files_remain(state)


@pytest.mark.parametrize('closed', [False, True])
def test_existing_storage_error_is_not_replaced_by_conflict_cleanup(runtime, closed):
    item, state = runtime
    failure = StorageConflictError('original provider receipt conflict')

    def fail_runtime():
        if closed:
            item.close()
        else:
            item._storage_failed = failure
        raise failure

    state.on_generate = fail_runtime
    with pytest.raises(StorageConflictError) as raised:
        render(item)
    assert raised.value is failure
    assert persisted(item)['p']['frames'][0]['image_url'] == 'uploads/old-a.png'
    assert_paid_files_remain(state)
