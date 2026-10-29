"""real three-view HTTP results survive independent packages.

Only GPT external transport and a negative final storage checkpoint are injected.
The latter is followed by an actual pipeline/app reconstruction without repair;
it is not a fabricated execution or a claim of a real process crash.
"""

import base64
from contextlib import contextmanager
import hashlib
import io
import json
import zipfile

from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen.models import AssetUnit, Character, ImageAsset, Scene, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_three_view_core as first
from tests import test_studio_three_view_boundaries as boundaries
from tests.frozen_image_fixtures import rgb_png_bytes


runtime = first.runtime
transport = first.transport


def _databases(item):
    return {path.name: path.read_bytes() for path in item.data_dir.glob('*.json')}


def _media(item):
    roots = {'uploads', 'assets', 'media_versions', 'storyboard', 'video', 'audio', 'video_inputs'}
    return {path.relative_to(item.data_dir).as_posix(): path.read_bytes()
            for path in item.data_dir.rglob('*')
            if path.is_file() and path.relative_to(item.data_dir).parts[0] in roots}


def _calls(state):
    return {'http': [(request.method, request.url, request.body, dict(request.headers), dict(options))
                     for request, options in state['calls']],
            'cli': list(state['cli'])}


def _read_only_export(client, item, state, project_id='p'):
    before = _databases(item), _media(item), _calls(state)
    response = client.get(f'/projects/{project_id}/package/export')
    assert (_databases(item), _media(item), _calls(state)) == before
    assert response.status_code == 200, response.text[:500]
    return response.content


def _setup(item, route):
    selected, _ = first.setup(item, route, first.MODELS[1])
    boundaries.seed_three_view_history(item, route, count=2)
    target = boundaries.current_target(item, route)
    # These old helper references were never written as files. Remove only
    # unrelated video fields during fixture initialization, before admission.
    for unit in (target.full_body, target.three_views):
        assert unit is not None
        unit.video_variants = []
        unit.selected_video_id = None
        unit.video_prompt = None
        unit.video_updated_at = 0
    project = item.scripts['p']
    project.scenes = [Scene.model_validate({'id': 'scene', 'name': 'Package scene', 'description': 'Synthetic scene'})]
    project.frames = [StoryboardFrame.model_validate({'id': 'frame', 'scene_id': 'scene', 'character_ids': ['c'],
                                                     'action_description': 'Hero turns slowly'})]
    boundaries.commit_owners(item)
    assert target.three_view_asset.selected_id == target.three_views.selected_image_id == 'three-view-old-1'
    assert target.three_view_asset.variants[0].is_favorited
    return selected, target.model_copy(deep=True)


def _queue(state, label, count):
    assert not state['queue'] and not state['cli_queue'] and not state['cli']
    for index in range(count):
        state['queue'].extend([
            first.gpt_sheet.response({'task_info': {
                'id': first.gpt_sheet.http_task_id(index), 'status': 'pending'}}, status=202),
            first.gpt_sheet.response({'task_info': {
                'id': first.gpt_sheet.http_task_id(index), 'status': 'completed'},
                'images': [first.gpt_sheet.IMAGE_URL]}),
            first.gpt_sheet.response(rgb_png_bytes(f'{label}-paid-{index}', width=16, height=16)),
        ])


def _saved_records(payload, route):
    if route == 'local':
        owner = payload.get('projects.json', {}).get('p', {})
    elif route == 'series':
        owner = payload.get('series.json', {}).get('s', {})
    else:
        owner = payload.get('library_assets.json', {})
    return owner.get('asset_image_requests', [])


@contextmanager
def _generate_and_reopen(item, state, monkeypatch, route, *, batch, fail_at):
    selected, historical = _setup(item, route)
    key = f'package-three-view-{route}-{batch}-{fail_at}'
    executed = batch if fail_at is None else fail_at
    _queue(state, key, executed)
    original_commit = item._storage.commit
    failures = []

    def reject_completion_only(payload):
        records = _saved_records(payload, route)
        matching = [row for row in records if row['client_request_id'] == key]
        if fail_at is not None and matching and matching[0]['members'][fail_at - 1]['execution']['phase'] == 'completed':
            failures.append(matching[0]['members'][fail_at - 1]['execution']['attempt_id'])
            raise StorageError('Synthetic B4 three-view completion checkpoint interruption')
        return original_commit(payload)

    app = first.app_for(item)
    with TestClient(app, raise_server_exceptions=False) as client:
        first.gpt_sheet.configure(app, 'http')
        # This preceding export isolates missing legacy fixture files from the
        # preservation regression. Queued external answers must remain unused.
        _read_only_export(client, item, state)
        assert state['calls'] == [] and len(state['queue']) == executed * 3
        monkeypatch.setattr(item._storage, 'commit', reject_completion_only)
        admitted = client.post(first.PUBLIC, json=first.raw() | {'client_request_id': key, 'batch_size': batch})
        assert admitted.status_code == 200, admitted.text
        assert len(failures) == int(fail_at is not None)
        persisted = {name: json.loads(data) for name, data in _databases(item).items()}
        saved = _saved_records(persisted, route)
        assert len(saved) == 1 and saved[0]['id'] == admitted.json()['request']['id']
        phases = ['completed'] * batch if fail_at is None else (
            ['completed'] * (fail_at - 1) + ['observed'] + ['accepted'] * (batch - fail_at))
        assert all(member['execution']['schema_version'] == 3 for member in saved[0]['members'])
        assert [member['execution']['phase'] for member in saved[0]['members']] == phases
        assert [request.method for request, _ in state['calls']] == ['POST', 'GET', 'GET'] * executed
        for index in range(executed):
            requests = [request for request, _ in state['calls'][index * 3:index * 3 + 3]]
            assert [request.url for request in requests] == [first.EDIT, first.EDIT + '/' + first.gpt_sheet.http_task_id(index), first.gpt_sheet.IMAGE_URL]
            assert json.loads(requests[0].body) == {
                'prompt': first.PROMPT, 'size': '1024x1024', 'n': 1,
                'images': ['data:image/png;base64,' + base64.b64encode(selected).decode()],
            }
        assert state['queue'] == [] and state['cli'] == [] and state['cli_queue'] == []
    item.close()
    before_restart = _databases(item), _media(item), _calls(state)
    restored = ComicGenPipeline(data_dir=item.data_dir, runtime_environment=RuntimeEnvironment({}),
                               recover_orphan_tasks=False, repair_series_bindings=False)
    try:
        app = first.app_for(restored)
        with TestClient(app) as client:
            assert (_databases(restored), _media(restored), _calls(state)) == before_restart
            assert not restored.asset_generation_tasks and not getattr(restored, '_asset_image_runs', {})
            owner = first.admission.owner(restored, route)
            record = owner.asset_image_requests[0]
            assert record.model_dump(mode='json') == saved[0]
            assert (record.entrypoint, record.asset_type, record.channel) == (first.ENTRYPOINT, 'character', 'three_view')
            assert (record.owner_kind, record.owner_id) == {
                'local': ('script', 'p'), 'series': ('series', 's'), 'global': ('global', 'library'),
            }[route]
            assert (record.request_root_kind, record.request_root_id) == ('project', 'p')
            target = boundaries.current_target(restored, route)
            assert target.three_view_request_id == record.id
            assert target.model_dump(mode='json', include=first.PROTECTED_FIELDS) == historical.model_dump(mode='json', include=first.PROTECTED_FIELDS)
            histories = (target.three_view_asset.variants, target.three_views.image_variants)
            assert target.three_view_asset.selected_id == target.three_views.selected_image_id == historical.three_view_asset.selected_id
            for history in histories:
                current = {variant.id: variant.model_dump(mode='json') for variant in history}
                assert all(current[variant.id] == variant.model_dump(mode='json') for variant in historical.three_view_asset.variants)
            for member, phase in zip(record.members, phases, strict=True):
                execution = member.execution
                assert execution.schema_version == 3
                assert execution.phase == phase
                assert execution.output_ref is not None
                if phase == 'accepted':
                    assert execution.prepared_artifact is None and execution.artifact is None
                    assert not (restored.data_dir / execution.output_ref).exists()
                else:
                    assert execution.handle is not None and execution.observation['status'] == 'succeeded'
                    boundaries.prove_paid_file(restored, execution)
                    assert execution.artifact == (execution.prepared_artifact if phase == 'completed' else None)
                assert all((member.variant_id in {variant.id for variant in history}) is (phase == 'completed') for history in histories)
            before = _databases(restored), _media(restored), _calls(state)
            original = client.get(first.PUBLIC + '/' + key)
            assert original.status_code == 200 and original.json()['request']['id'] == record.id
            assert (_databases(restored), _media(restored), _calls(state)) == before
            yield client, restored, record
    finally:
        restored.close()


def _assert_no_authority(group):
    assert group['asset_image_requests'] == []
    for character in group['characters']:
        for field in ('reference_sheet_request_id', 'full_body_request_id', 'three_view_request_id', 'full_body_motion_request_id'):
            assert character.get(field) is None


def _roundtrip(client, item, state, target, carried, *, excluded=()):
    databases, media, calls = _databases(item), _media(item), _calls(state)
    projects = json.loads(databases['projects.json'])
    expected = {variant.id: {'url': variant.url, 'data': media[variant.url], 'prompt': variant.prompt_used,
                            'favorite': variant.is_favorited}
                for variant in target.three_view_asset.variants}
    originals = [{variant.id: variant.model_dump(mode='json', exclude_none=True) for variant in history}
                 for history in (target.three_view_asset.variants, target.three_views.image_variants)]
    selected_id = target.three_view_asset.selected_id
    assert selected_id is not None and selected_id == target.three_views.selected_image_id
    for member in carried:
        proof, ref = member.execution.prepared_artifact, member.execution.output_ref
        assert proof is not None and ref is not None
        data = media[ref]
        assert (hashlib.sha256(data).hexdigest(), len(data)) == (proof['sha256'], proof['size_bytes'])
        if member.variant_id not in expected:
            expected[member.variant_id] = {'url': ref, 'data': data, 'prompt': member.execution.intent['prompt'], 'favorite': False}
    expected_by_sha = {hashlib.sha256(row['data']).hexdigest(): (identity, row) for identity, row in expected.items()}
    assert len(expected_by_sha) == len(expected)

    def unchanged():
        current = _databases(item)
        assert current.keys() == databases.keys()
        assert all(current[name] == data for name, data in databases.items() if name != 'projects.json')
        current_projects = json.loads(current['projects.json'])
        assert all(current_projects[identity] == value for identity, value in projects.items())
        assert all((item.data_dir / ref).read_bytes() == data for ref, data in media.items())
        assert _calls(state) == calls and not state['queue'] and not state['cli_queue']

    archive = _read_only_export(client, item, state)
    unchanged()
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        payload, manifest = json.loads(package.read('project.json')), json.loads(package.read('manifest.json'))
        for row in expected.values():
            name = 'media/' + row['url']
            # The expected first genuine RED for prepared-only three-view data.
            assert name in package.namelist(), 'Proven three-view PNG omitted from project package'
            assert package.read(name) == row['data']
            proof = next(value for value in manifest['media'] if value['logical_ref'] == row['url'])
            assert (proof['sha256'], proof['size_bytes']) == (hashlib.sha256(row['data']).hexdigest(), len(row['data']))
        assert all('media/' + ref not in package.namelist() for ref in excluded)
    for group in (payload['script'], payload['resolved_assets']):
        _assert_no_authority(group)
        character = next(value for value in group['characters'] if value['id'] == 'c')
        assert (character['name'], character['description'], character['three_view_image_url']) == (target.name, target.description, target.three_view_image_url)
        assert character['three_view_asset']['selected_id'] == character['three_views']['selected_image_id'] == selected_id
        assert character['three_views']['image_prompt'] == target.three_views.image_prompt
        for history, old in zip((character['three_view_asset']['variants'], character['three_views']['image_variants']), originals, strict=True):
            assert len(history) == len(expected) and {variant['id'] for variant in history} == set(expected)
            for variant in history:
                row = expected[variant['id']]
                assert (variant['url'], variant.get('prompt_used'), variant['is_favorited']) == (row['url'], row['prompt'], row['favorite'])
                if variant['id'] in old:
                    assert variant == old[variant['id']]
    assert payload['script'].get('asset_image_request_scope_id') is None
    assert payload['script']['provider_request_receipts'] == []
    assert '"credential_ref"' not in json.dumps(payload) and '"prepared_artifact"' not in json.dumps(payload)

    imported = client.post('/projects/package/import', content=archive, headers={'content-type': 'application/zip'})
    assert imported.status_code == 200, imported.text
    copied_id = imported.json()['id']
    assert copied_id not in projects
    unchanged()
    copied = item.read_committed_script(copied_id)
    assert copied is not None and copied.series_id is None and copied.asset_image_request_scope_id is None
    assert copied.asset_image_requests == [] and copied.provider_request_receipts == [] and copied.video_tasks == []
    character = next(value for value in copied.characters if value.name == target.name)
    assert character.id != 'c' and copied.frames[0].id != 'frame'
    assert copied.frames[0].character_ids == [character.id] and copied.frames[0].scene_id != 'scene'
    _assert_no_authority(copied.model_dump(mode='json'))
    imported_histories = (character.three_view_asset.variants, character.three_views.image_variants)
    assert len(imported_histories[0]) == len(imported_histories[1]) == len(expected)
    assert {variant.id: variant.model_dump() for variant in imported_histories[0]} == {variant.id: variant.model_dump() for variant in imported_histories[1]}
    for variant in imported_histories[0]:
        data = (item.data_dir / variant.url).read_bytes()
        old_id, row = expected_by_sha[hashlib.sha256(data).hexdigest()]
        assert data == row['data'] and variant.id != old_id and variant.url != row['url']
        assert (variant.prompt_used, variant.is_favorited) == (row['prompt'], row['favorite'])
        if old_id == selected_id:
            assert character.three_view_asset.selected_id == character.three_views.selected_image_id == variant.id
            assert character.three_view_image_url == variant.url
        else:
            assert character.three_view_asset.selected_id != variant.id
    assert character.three_views.image_prompt == target.three_views.image_prompt
    reexported = _read_only_export(client, item, state, copied_id)
    unchanged()
    with zipfile.ZipFile(io.BytesIO(reexported)) as package:
        manifest, payload = json.loads(package.read('manifest.json')), json.loads(package.read('project.json'))
        for variant in imported_histories[0]:
            data = (item.data_dir / variant.url).read_bytes()
            assert package.read('media/' + variant.url) == data
            proof = next(row for row in manifest['media'] if row['logical_ref'] == variant.url)
            assert (proof['sha256'], proof['size_bytes']) == (hashlib.sha256(data).hexdigest(), len(data))
        for group in (payload['script'], payload['resolved_assets']):
            _assert_no_authority(group)
            exported = next(row for row in group['characters'] if row['id'] == character.id)
            assert exported['three_view_asset']['selected_id'] == exported['three_views']['selected_image_id'] == character.three_view_asset.selected_id
            assert len(exported['three_view_asset']['variants']) == len(exported['three_views']['image_variants']) == len(expected)
        assert payload['script'].get('asset_image_request_scope_id') is None and payload['script']['provider_request_receipts'] == []
    unchanged()


@pytest.mark.parametrize('route,batch,fail_at', [
    pytest.param('local', 2, None, id='completed-local'),
    pytest.param('series', 3, 2, id='partial-series'),
    pytest.param('global', 1, 1, id='prepared-only-global'),
])
def test_three_view_actual_owner_media_survives_export_import_reexport(runtime, transport, monkeypatch, route, batch, fail_at):
    with _generate_and_reopen(runtime, transport, monkeypatch, route, batch=batch, fail_at=fail_at) as (client, item, record):
        carried = [member for member in record.members if member.execution.prepared_artifact is not None]
        excluded = [member.execution.output_ref for member in record.members if member.execution.prepared_artifact is None]
        _roundtrip(client, item, transport, boundaries.current_target(item, route), carried, excluded=excluded)


def _higher_character(item):
    variants = []
    for index in range(2):
        ref = f'uploads/higher-three-view-{index}.png'
        (item.data_dir / ref).write_bytes(rgb_png_bytes('higher owner ' + str(index), width=16, height=16))
        variants.append({'id': f'higher-{index}', 'url': ref, 'prompt_used': f'Higher prompt {index}',
                         'created_at': index + 1, 'is_favorited': index == 0})
    return Character.model_validate({'id': 'c', 'name': 'Visible higher owner', 'description': 'Independent author asset',
        'three_view_asset': ImageAsset.model_validate({'selected_id': 'higher-1', 'variants': variants}),
        'three_views': AssetUnit.model_validate({'selected_image_id': 'higher-1', 'image_variants': variants,
                                               'image_prompt': 'Higher current prompt', 'image_updated_at': 2}),
        'three_view_image_url': variants[-1]['url'], 'three_view_prompt': 'Higher current prompt'})


@pytest.mark.parametrize('lower,higher', [('series', 'local'), ('global', 'series')])
def test_three_view_lower_owner_prepared_media_never_attaches_to_same_id_higher_owner(runtime, transport, monkeypatch, lower, higher):
    with _generate_and_reopen(runtime, transport, monkeypatch, lower, batch=1, fail_at=1) as (client, item, record):
        member = record.members[0]
        assert member.execution.schema_version == 3
        assert member.execution.phase == 'observed' and member.execution.prepared_artifact is not None
        visible = _higher_character(item)
        # Controlled committed collaborator ownership change; the real lower
        # owner's execution/proof is neither copied nor edited.
        if higher == 'local':
            item.scripts['p'].characters.append(visible)
        else:
            item.series_store['s'].characters.append(visible)
            item.series_store['s'].episode_ids = ['p']
            item.scripts['p'].series_id = 's'
        boundaries.commit_owners(item)
        source, series, library = item.read_committed_project_view('p')
        assert source is not None and source.frames[0].character_ids == ['c']
        actual = next(target for target in item._resolve_asset_layers(source, series, library)['characters'] if target.id == 'c')
        assert actual.name == visible.name
        _roundtrip(client, item, transport, actual, [], excluded=[
            member.execution.output_ref, 'uploads/three-view-old-0.png', 'uploads/three-view-old-1.png',
        ])
        retained = first.admission.owner(item, lower).asset_image_requests[0]
        assert retained.model_dump(mode='json') == record.model_dump(mode='json')
        boundaries.prove_paid_file(item, retained.members[0].execution)
