"""Real copy/package entrypoints preserve media without transferring image authority."""

from copy import deepcopy
import io
import json
from unittest.mock import Mock
import zipfile

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import AssetUnit, Character, GenerationStatus, GlobalAssetLibrary, ImageVariant, Script, Series
from src.apps.comic_gen.project_package import ProjectPackageError, build_package, prepare_import
from src.utils.json_storage import StorageError
from tests.test_project_package import _resolved_assets, _source_project
from tests.test_studio_core_image_models import REQUEST_ID, SCOPE_ID, payloads, request_payload


STATES = ('processing', 'needs_confirmation', 'completed', 'pending', 'failed')
COPY_ROUTES = ('promote-project', 'promote-series', 'fork-global', 'import-series')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ('AssetGenerator', 'StoryboardGenerator', 'VideoGenerator', 'AudioGenerator', 'ExportManager'):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / 'data', script_processor=Mock())
    blocked = Mock(side_effect=AssertionError('copy attempted generation'))
    monkeypatch.setattr('src.models.provider_media_intents.build_accepted_sync_artifact_record', blocked)
    monkeypatch.setattr('requests.sessions.Session.request', blocked)
    item._enqueue_asset_image = blocked
    yield item, blocked
    item.close()


def seed_runtime(runtime, route, state):
    source_route = {'promote-project': 'project-local', 'promote-series': 'standalone-series',
                    'fork-global': 'project-global', 'import-series': 'standalone-series',
                    'move': 'project-local'}[route]
    data = payloads(source_route)
    runtime.scripts = {key: Script.model_validate(value) for key, value in data['projects.json'].items()}
    runtime.series_store = {key: Series.model_validate(value) for key, value in data['series.json'].items()}
    runtime.library_store = GlobalAssetLibrary.model_validate(data['library_assets.json'])
    owner = runtime.scripts['project-root'] if source_route == 'project-local' else runtime.library_store if source_route == 'project-global' else runtime.series_store['series-root']
    character = owner.characters[0]
    character.status = state if state == "needs_confirmation" else GenerationStatus(state)
    character.starred = True
    character.locked = True
    character.image_url = 'uploads/retained.png'
    character.reference_sheet = AssetUnit(
        selected_image_id='chosen',
        image_variants=[ImageVariant(id='alternate', url='uploads/alternate.png'),
                        ImageVariant(id='chosen', url='uploads/retained.png')],
    )
    if route == 'import-series':
        runtime.series_store['target'] = Series(id='target', title='target', created_at=1, updated_at=1)
    if route == 'move':
        runtime.scripts['project-root'].series_id = 'series-root'
    runtime._commit_storage('projects.json', 'series.json', 'library_assets.json')
    return owner, character


def expected_copy(source):
    expected = deepcopy(source)
    expected.pop('id')
    expected['reference_sheet_request_id'] = None
    if expected['status'] == 'processing':
        expected['status'] = 'needs_confirmation'
    return expected


def assert_copy(source, copied):
    payload = copied.model_dump(mode='json')
    payload.pop('id')
    assert payload == expected_copy(source)


def loaded_history(runtime):
    owners = [*runtime._load_data().values(), *runtime._load_series_data().values(), runtime._load_library_data()]
    return [request.model_dump(mode='json') for owner in owners for request in owner.asset_image_requests]


@pytest.mark.parametrize('route', COPY_ROUTES)
@pytest.mark.parametrize('state', STATES)
def test_copy_entrypoints_clear_only_copied_authority_and_preserve_original_history(runtime, route, state):
    item, blocked = runtime
    owner, character = seed_runtime(item, route, state)
    before = character.model_dump(mode='json')
    history = deepcopy(owner.asset_image_requests[0].model_dump(mode='json'))
    root_scope = item.scripts['project-root'].asset_image_request_scope_id
    if route == 'promote-project':
        copied = item.promote_asset_to_library('project', 'project-root', 'character', character.id)
    elif route == 'promote-series':
        copied = item.promote_asset_to_library('series', 'series-root', 'character', character.id)
    elif route == 'fork-global':
        copied = item.fork_library_asset_to_project('project-root', 'character', character.id)
    else:
        target, imported, skipped = item.import_assets_from_series('target', 'series-root', [character.id])
        assert imported == [character.id] and skipped == []
        copied = target.characters[0]
        assert target.asset_image_request_scope_id is None
    assert copied.id != character.id
    assert_copy(before, copied)
    assert character.model_dump(mode='json') == before
    assert loaded_history(item) == [history]
    assert item._load_data()['project-root'].asset_image_request_scope_id == root_scope
    blocked.assert_not_called()


@pytest.mark.parametrize('state', STATES)
def test_local_to_series_move_clears_pointer_without_moving_history(runtime, state):
    item, blocked = runtime
    owner, character = seed_runtime(item, 'move', state)
    before = character.model_dump(mode='json')
    history = owner.asset_image_requests[0].model_dump(mode='json')
    item.reconcile_project_assets('project-root', {'characters': [{'local_id': character.id, 'action': 'create_new_in_series'}]})
    project = item._load_data()['project-root']
    series = item._load_series_data()['series-root']
    assert project.characters == []
    assert project.asset_image_request_scope_id == SCOPE_ID
    assert project.asset_image_requests[0].model_dump(mode='json') == history
    assert series.asset_image_requests == [] and series.asset_image_request_scope_id is None
    assert series.characters[0].id == before['id']
    assert_copy(before, series.characters[0])
    blocked.assert_not_called()


@pytest.mark.parametrize('action', ['skip', 'merge_into_series'])
def test_reconcile_does_not_transfer_execution_to_existing_target(runtime, action):
    item, blocked = runtime
    owner, character = seed_runtime(item, 'move', 'needs_confirmation')
    target = Character(id='target', name='target', description='', reference_sheet_request_id=SCOPE_ID)
    item.series_store['series-root'].characters.append(target)
    item._commit_storage('series.json')
    target_before = target.model_dump(mode='json')
    history = owner.asset_image_requests[0].model_dump(mode='json')
    item.reconcile_project_assets('project-root', {'characters': [{'local_id': character.id, 'action': action, 'target_series_id': target.id}]})
    assert item._load_series_data()['series-root'].characters[0].model_dump(mode='json') == target_before
    project = item._load_data()['project-root']
    assert project.asset_image_requests[0].model_dump(mode='json') == history
    assert len(project.characters) == (1 if action == 'skip' else 0)
    if action == 'skip':
        assert project.characters[0].reference_sheet_request_id == REQUEST_ID
    blocked.assert_not_called()


def test_failed_move_restores_original_pointer_and_history(runtime, monkeypatch):
    item, _ = runtime
    seed_runtime(item, 'move', 'processing')
    before = {name: (item.data_dir / name).read_bytes() for name in ('projects.json', 'series.json')}
    monkeypatch.setattr(item._storage, 'commit', Mock(side_effect=StorageError('write failed')))
    with pytest.raises(StorageError):
        item.reconcile_project_assets('project-root', {'characters': [{'local_id': 'legacy-character', 'action': 'create_new_in_series'}]})
    assert item.scripts['project-root'].characters[0].reference_sheet_request_id == REQUEST_ID
    assert item.series_store['series-root'].characters == []
    assert {name: (item.data_dir / name).read_bytes() for name in before} == before


def package_source(tmp_path, state):
    source = _source_project(tmp_path / 'source')
    assets = _resolved_assets()
    source.asset_image_request_scope_id = SCOPE_ID
    raw = request_payload()
    raw.update(owner_id=source.id, request_root_id=source.id, asset_id=assets.characters[0].id)
    source.asset_image_requests = [AssetImageRequestRecord.model_validate(raw)]
    char = assets.characters[0]
    char.reference_sheet_request_id = REQUEST_ID
    char.status = state if state == "needs_confirmation" else GenerationStatus(state)
    char.starred = True
    char.reference_sheet = AssetUnit(selected_image_id='v1', image_variants=[ImageVariant(id='v1', url=char.image_url)])
    return source, assets


@pytest.mark.parametrize('state', STATES)
def test_export_zip_has_no_image_execution_authority_but_keeps_source_and_media(tmp_path, state):
    source, assets = package_source(tmp_path, state)
    source_before, assets_before = source.model_dump(), assets.model_dump()
    artifact = build_package(source, resolved_assets=assets, data_dir=tmp_path / 'source', source_storage_id='storage')
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as zf:
        payload = json.loads(zf.read('project.json'))
    assert payload['script'].get('asset_image_request_scope_id') is None
    assert payload['script']['asset_image_requests'] == []
    assert payload['resolved_assets']['asset_image_requests'] == []
    for char in [*payload['script']['characters'], *payload['resolved_assets']['characters']]:
        assert char.get('reference_sheet_request_id') is None
        assert char['status'] == ('needs_confirmation' if state == 'processing' else state)
        assert char['starred'] is True
        assert char['reference_sheet']['selected_image_id'] == 'v1'
        assert char['reference_sheet']['image_variants'][0]['url'] == 'assets/characters/hero.png'
    assert 'frozen-credential' not in json.dumps(payload)
    assert source.model_dump() == source_before and assets.model_dump() == assets_before


@pytest.mark.parametrize('state', STATES)
def test_import_strips_authority_from_older_package_independently_of_export(tmp_path, state):
    source, assets = package_source(tmp_path, state)
    artifact = build_package(source, resolved_assets=assets, data_dir=tmp_path / 'source', source_storage_id='storage')
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    payload = json.loads(entries['project.json'])
    payload['script']['asset_image_request_scope_id'] = SCOPE_ID
    payload['script']['asset_image_requests'] = [source.asset_image_requests[0].model_dump(mode='json')]
    global_record = request_payload('project-global')
    global_record.update(request_root_id=source.id, asset_id=assets.characters[0].id)
    payload['resolved_assets']['asset_image_requests'] = [global_record]
    for char in [*payload['script']['characters'], *payload['resolved_assets']['characters']]:
        char['reference_sheet_request_id'] = REQUEST_ID
        char['status'] = state
    entries['project.json'] = json.dumps(payload).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as target:
        for name, value in entries.items():
            target.writestr(name, value)
    imported = prepare_import(buffer.getvalue(), data_dir=tmp_path / 'target').script
    assert imported.id != source.id
    assert imported.asset_image_requests == [] and imported.asset_image_request_scope_id is None
    char = imported.characters[0]
    assert char.reference_sheet_request_id is None
    assert char.status == ('needs_confirmation' if state == 'processing' else state)
    assert char.starred is True
    variant = char.reference_sheet.image_variants[0]
    assert char.reference_sheet.selected_image_id == variant.id
    assert (tmp_path / 'target' / variant.url).read_bytes() == b'hero'


@pytest.mark.parametrize('damage', ['invalid-record', 'unsafe-dynamic'])
def test_import_does_not_relax_original_record_or_remaining_metadata_validation(tmp_path, damage):
    source, assets = package_source(tmp_path, 'needs_confirmation')
    artifact = build_package(source, resolved_assets=assets, data_dir=tmp_path / 'source', source_storage_id='storage')
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    payload = json.loads(entries['project.json'])
    payload['script']['asset_image_requests'] = [source.asset_image_requests[0].model_dump(mode='json')]
    if damage == 'invalid-record':
        payload['script']['asset_image_requests'][0]['members'][0]['execution']['fingerprint'] = '0' * 64
    else:
        payload['script']['frames'][0]['composition_data'] = {'extra': 'https://untrusted.invalid/metadata'}
    entries['project.json'] = json.dumps(payload).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as target:
        for name, value in entries.items():
            target.writestr(name, value)
    with pytest.raises(ProjectPackageError):
        prepare_import(buffer.getvalue(), data_dir=tmp_path / 'target')
