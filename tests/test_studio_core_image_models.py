"""Dormant Studio image history validates locally without gaining execution rights."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.apps.comic_gen.models import Character, GlobalAssetLibrary, Script, Series
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models.provider_media_intents import build_accepted_sync_artifact_record, sync_artifact_proof_from_record
from src.models.provider_recovery import _fingerprint, record_with_changes
from src.models.provider_tasks import ProviderScope
from src.models.wan27_image_contract import WAN27_IMAGE_CONTRACT_VERSION
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import CorruptStorageError, DirectoryWriterLock
from src.utils.json_transactions import JsonFileTransaction


REQUEST_ID = '11111111-1111-4111-8111-111111111111'
OTHER_REQUEST_ID = '22222222-2222-4222-8222-222222222222'
SCOPE_ID = '33333333-3333-4333-8333-333333333333'
OTHER_SCOPE_ID = '44444444-4444-4444-8444-444444444444'
ROUTES = ('project-local', 'project-series', 'project-global', 'standalone-series')
PROVIDER_MODELS = ('wan2.7-image', 'wan2.7-image-pro')


def execution_payload(model='wan2.7-image', attempt='attempt-1', phase='accepted'):
    intent = normalize_generation_intent(
        {'model_id': model, 'mode': 't2i', 'prompt': '保留角色设定',
         'params': {'size': '768*1024', 'seed': 0, 'watermark': False}},
        allowed_modes={'t2i'}, allowed_parameters={'size', 'seed', 'watermark'},
    )
    record = build_accepted_sync_artifact_record(
        intent=intent, provider_model_id=model, media_kind='image', media=[],
        scope=ProviderScope('dashscope', 'dashscope', 'dashscope', 'https://dashscope.aliyuncs.com', 'cn-beijing'),
        credential_ref='frozen-credential', attempt_id=attempt, output_ref=f'assets/studio/{attempt}.png',
        image_contract_version=WAN27_IMAGE_CONTRACT_VERSION,
    )
    if phase == 'completed':
        record = record_with_changes(record, phase='submitting')
        proof = sync_artifact_proof_from_record(record, relative_path=record.output_ref, sha256='a' * 64, size_bytes=17)
        record = record_with_changes(record, phase='completed', prepared_artifact=proof, artifact=proof)
    else:
        record = record_with_changes(record, phase=phase)
    return record.model_dump(mode='json')


def request_payload(route='project-local', model='wan2.7-image'):
    owner_kind = {'project-local': 'script', 'project-series': 'series', 'project-global': 'global', 'standalone-series': 'series'}[route]
    return {
        'schema_version': 1, 'id': REQUEST_ID, 'created_at': 0,
        'request_contract_version': 'request-json-v1', 'entrypoint': 'studio.asset.reference_sheet.v1',
        'client_request_id': 'caller.request:1', 'request_fingerprint': 'b' * 64,
        'request_root_kind': 'series' if route == 'standalone-series' else 'project',
        'request_root_id': 'series-root' if route == 'standalone-series' else 'project-root',
        'request_root_scope_id': SCOPE_ID,
        'request_root_series_id': 'series-root' if route == 'project-series' else None,
        'owner_kind': owner_kind, 'owner_id': {'script': 'project-root', 'series': 'series-root', 'global': 'library'}[owner_kind],
        'asset_type': 'character', 'asset_id': 'legacy-character', 'channel': 'reference_sheet',
        'input_guard': 'c' * 64, 'projection_guard': 'd' * 64,
        'members': [{'variant_id': 'attempt-1', 'execution': execution_payload(model)}],
    }


def payloads(route='project-local', model='wan2.7-image'):
    record = request_payload(route, model)
    character = Character(id='legacy-character', name='保留人物', description='保留描述').model_dump(mode='json')
    character['reference_sheet_request_id'] = REQUEST_ID
    project = Script(id='project-root', title='Project', original_text='Script', created_at=1, updated_at=1).model_dump(mode='json')
    series = Series(id='series-root', title='Series', created_at=1, updated_at=1).model_dump(mode='json')
    library = GlobalAssetLibrary().model_dump(mode='json')
    if route == 'project-series':
        project['series_id'] = 'series-root'
        series['episode_ids'] = ['project-root']
    root = series if route == 'standalone-series' else project
    root['asset_image_request_scope_id'] = SCOPE_ID
    owner = {'script': project, 'series': series, 'global': library}[record['owner_kind']]
    owner['characters'] = [character]
    owner['asset_image_requests'] = [record]
    return {'projects.json': {project['id']: project}, 'series.json': {series['id']: series}, 'library_assets.json': library}


def owner_payload(data, route):
    return {'project-local': data['projects.json']['project-root'], 'project-series': data['series.json']['series-root'],
            'project-global': data['library_assets.json'], 'standalone-series': data['series.json']['series-root']}[route]


def owner_model(route):
    return {'project-local': Script, 'project-series': Series, 'project-global': GlobalAssetLibrary, 'standalone-series': Series}[route]


def save_and_reload(root: Path, data):
    lease = DirectoryWriterLock(root).acquire()
    try:
        JsonFileTransaction(root, data).commit(data)
    finally:
        lease.close()
    # Exercise the actual persistence loaders without initializing any workers or providers.
    loader = object.__new__(ComicGenPipeline)
    loader.data_file = str(root / 'projects.json')
    loader.series_data_file = str(root / 'series.json')
    loader.library_data_file = str(root / 'library_assets.json')
    return loader._load_data(), loader._load_series_data(), loader._load_library_data()


def rehash(execution):
    names = ('attempt_id', 'provider_model_id', 'intent', 'media', 'scope', 'credential_ref', 'execution_kind', 'media_kind', 'output_ref')
    execution['fingerprint'] = _fingerprint({name: execution[name] for name in names})


@pytest.mark.parametrize('model_class,kwargs', [
    (Script, dict(id='legacy-script', title='Script', original_text='story', created_at=1, updated_at=1)),
    (Series, dict(id='legacy-series', title='Series', created_at=1, updated_at=1)),
    (GlobalAssetLibrary, {}),
])
def test_legacy_json_gets_no_random_identity_and_roundtrips(model_class, kwargs, monkeypatch, tmp_path):
    original = model_class(**kwargs).model_dump(mode='json')
    original.pop('asset_image_requests', None)
    original.pop('asset_image_request_scope_id', None)
    character = Character(id='legacy', name='保留', description='test').model_dump(mode='json')
    character.pop('reference_sheet_request_id', None)
    original['characters'] = [character]
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(original), encoding='utf-8')
    before = path.read_bytes()
    monkeypatch.setattr('uuid.uuid4', Mock(side_effect=AssertionError('load generated an ID')))
    loaded = model_class.model_validate_json(path.read_bytes())
    assert loaded.asset_image_requests == []
    if model_class is not GlobalAssetLibrary:
        assert loaded.asset_image_request_scope_id is None
    assert loaded.characters[0].reference_sheet_request_id is None
    assert loaded.model_dump(mode='json', exclude_unset=True) == original
    assert model_class.model_validate_json(loaded.model_dump_json()).model_dump() == loaded.model_dump()
    assert path.read_bytes() == before


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('model', PROVIDER_MODELS)
def test_four_actual_owner_routes_roundtrip_ordered_core_records_in_real_files(tmp_path, route, model):
    data = payloads(route, model)
    raw_request = owner_payload(data, route)['asset_image_requests'][0]
    raw_request['members'] = [
        {'variant_id': f'attempt-{index}', 'execution': execution_payload(model, f'attempt-{index}', phase)}
        for index, phase in enumerate(('accepted', 'submitting', 'completed', 'cancelled'))
    ]
    projects, series, library = save_and_reload(tmp_path, data)
    owner = {'script': projects['project-root'], 'series': series['series-root'], 'global': library}[raw_request['owner_kind']]
    saved = owner.asset_image_requests[0]
    assert saved.model_dump(mode='json') == raw_request
    assert [member.execution.phase for member in saved.members] == ['accepted', 'submitting', 'completed', 'cancelled']
    assert owner.characters[0].reference_sheet_request_id == REQUEST_ID
    assert saved.members[0].execution.intent['params'] == {'size': '768*1024', 'seed': 0, 'watermark': False}
    assert sum(len(item.asset_image_requests) for item in [*projects.values(), *series.values(), library]) == 1


@pytest.mark.parametrize('route', ROUTES)
def test_historical_root_target_pointer_or_series_changes_do_not_corrupt_owner_history(tmp_path, route):
    data = payloads(route)
    owner = owner_payload(data, route)
    owner['characters'] = []
    owner['asset_image_request_scope_id'] = OTHER_SCOPE_ID if route != 'project-global' else None
    if route == 'project-global':
        owner.pop('asset_image_request_scope_id')
    data['projects.json']['project-root']['series_id'] = 'new-series'
    if route in ('project-global', 'project-series'):
        data['projects.json'] = {}
    loaded = save_and_reload(tmp_path, data)
    actual = loaded[0]['project-root'] if route == 'project-local' else loaded[2] if route == 'project-global' else loaded[1]['series-root']
    assert actual.asset_image_requests[0].request_root_scope_id == SCOPE_ID
    # Current pointers are allowed to identify a newer request absent from this historical list.
    replacement = Character(id='legacy-character', name='new', description='', reference_sheet_request_id=OTHER_REQUEST_ID)
    edited = actual.model_dump(mode='json')
    edited['characters'] = [replacement.model_dump(mode='json')]
    assert owner_model(route).model_validate(edited).characters[0].reference_sheet_request_id == OTHER_REQUEST_ID


@pytest.mark.parametrize('field,value', [
    ('schema_version', 2), ('schema_version', True), ('schema_version', 1.0),
    ('id', 'not-server-uuid'), ('id', None), ('created_at', True), ('created_at', -1), ('created_at', float('inf')),
    ('request_contract_version', 'future'), ('entrypoint', 'other'), ('client_request_id', 'invalid key'),
    ('client_request_id', ''), ('request_fingerprint', 'A' * 64), ('input_guard', {'description': 'snapshot'}),
    ('projection_guard', 'short'), ('request_root_id', ''), ('request_root_id', ' unsafe '),
    ('request_root_id', 1), ('request_root_scope_id', 'missing-uuid'), ('request_root_series_id', ''),
    ('owner_id', ''), ('asset_type', 'scene'), ('asset_id', ''), ('channel', 'full_body'),
    ('members', []), ('members', None), ('unexpected', 'ignored?'),
])
def test_invalid_request_fields_are_not_silently_ignored(field, value):
    data = payloads()
    data['projects.json']['project-root']['asset_image_requests'][0][field] = value
    with pytest.raises(ValueError):
        Script.model_validate(data['projects.json']['project-root'])


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('foreign', ['owner-id', 'owner-kind', 'request-id', 'request-key', 'member'])
def test_foreign_owners_and_duplicate_request_or_member_identity_are_rejected(route, foreign):
    raw_owner = owner_payload(payloads(route), route)
    record = raw_owner['asset_image_requests'][0]
    if foreign == 'owner-id':
        record['owner_id'] = 'different-owner'
    elif foreign == 'owner-kind':
        record['owner_kind'] = 'global' if record['owner_kind'] != 'global' else 'script'
    else:
        other = deepcopy(record)
        if foreign != 'request-id':
            other['id'] = OTHER_REQUEST_ID
        if foreign != 'request-key':
            other['client_request_id'] = 'new-client-request'
        if foreign != 'member':
            other['members'] = [{'variant_id': 'new-attempt', 'execution': execution_payload(attempt='new-attempt')}]
        if foreign == 'request-key':
            # Scope incarnation never grants a second admission for the same historical client key.
            other['request_root_scope_id'] = OTHER_SCOPE_ID
        raw_owner['asset_image_requests'].append(other)
    with pytest.raises(ValueError):
        owner_model(route).model_validate(raw_owner)


@pytest.mark.parametrize('change', ['foreign-variant', 'extra-member-field', 'duplicate-member', 'wrong-root-owner', 'standalone-project-series'])
def test_member_and_static_route_links_are_strict(change):
    route = 'standalone-series' if change == 'standalone-project-series' else 'project-local'
    raw_owner = owner_payload(payloads(route), route)
    record = raw_owner['asset_image_requests'][0]
    if change == 'foreign-variant':
        record['members'][0]['variant_id'] = 'another-attempt'
    elif change == 'extra-member-field':
        record['members'][0]['phase'] = 'accepted'
    elif change == 'duplicate-member':
        record['members'].append(deepcopy(record['members'][0]))
    elif change == 'wrong-root-owner':
        record['request_root_id'] = 'different-project'
    else:
        record['request_root_series_id'] = 'invented-project-relation'
    with pytest.raises(ValueError):
        owner_model(route).model_validate(raw_owner)


@pytest.mark.parametrize('field,value', [
    ('provider_model_id', 'wan2.6-image'), ('provider_model_id', 'wan2.7-image-pro'),
    ('media_kind', 'audio'), ('schema_version', 1), ('output_ref', 'assets/atelier/attempt-1.png'),
    ('output_ref', 'assets/studio/other-attempt.png'), ('output_ref', 'assets/studio/../attempt-1.png'),
    ('attempt_id', '../attempt'), ('attempt_id', 'attempt.with-dot'), ('credential_ref', ''),
    ('phase', 'submitted'), ('handle', {'task_id': 'foreign'}), ('media', [{'unfrozen': 'reference'}]),
])
def test_invalid_or_foreign_core_member_is_rejected_with_a_recomputed_fingerprint(field, value):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution[field] = value
    rehash(execution)
    with pytest.raises(ValueError):
        Script.model_validate(owner)


@pytest.mark.parametrize('field,value', [
    ('provider', 'mulerouter'), ('backend', 'vendor'), ('gateway', 'dashscope_workspace'),
    ('base_url', 'https://not-official.invalid'), ('region', 'us-east-1'),
    ('workspace_id', 'invalid workspace'), ('account_identity', 'another-account'),
])
def test_classic_scope_cannot_change_authority(field, value):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution['scope'][field] = value
    rehash(execution)
    with pytest.raises(ValueError):
        Script.model_validate(owner)


@pytest.mark.parametrize('params', [
    {}, {'size': 'small'}, {'size': '0*1024'}, {'size': '1*1'}, {'size': '8192*1024'},
    {'size': '768*1024', 'seed': -1}, {'size': '768*1024', 'seed': 2147483648},
    {'size': '768*1024', 'seed': True}, {'size': '768*1024', 'seed': 1.0},
    {'size': '768*1024', 'seed': None}, {'size': '768*1024', 'watermark': 0},
    {'size': '768*1024', 'negative_prompt': ''}, {'size': '768*1024', 'prompt_extend': False},
    {'size': '768*1024', 'n': 1}, {'size': '768*1024', 'size_extra': '768*1024'},
])
def test_unsupported_frozen_parameters_are_rejected_without_defaults(params):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution['intent']['params'] = {'watermark': False, **params}
    rehash(execution)
    with pytest.raises(ValueError):
        Script.model_validate(owner)


@pytest.mark.parametrize('field,value', [
    ('model_id', 'wan/wan2.7-image-pro#image'), ('mode', 'i2i'), ('catalog_mode', 'video'),
    ('rules_version', 'future'), ('prompt', ''), ('prompt', 123), ('scope', 'full-authority'),
])
def test_frozen_intent_fields_must_match_the_supported_contract(field, value):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution['intent'][field] = value
    rehash(execution)
    with pytest.raises(ValueError):
        Script.model_validate(owner)


@pytest.mark.parametrize('change', ['fingerprint', 'missing-proof', 'foreign-attempt', 'foreign-path', 'proof-hash', 'proof-schema', 'proof-size'])
def test_completed_proof_corruption_is_rejected(change):
    owner = owner_payload(payloads(), 'project-local')
    execution = execution_payload(phase='completed')
    owner['asset_image_requests'][0]['members'][0]['execution'] = execution
    if change == 'fingerprint':
        execution['fingerprint'] = '0' * 64
    elif change == 'missing-proof':
        execution['prepared_artifact'] = None
    else:
        field, value = {'foreign-attempt': ('attempt_id', 'another'), 'foreign-path': ('relative_path', 'assets/studio/another.png'),
                        'proof-hash': ('sha256', 'x' * 64), 'proof-schema': ('schema_version', 2.0), 'proof-size': ('size_bytes', True)}[change]
        execution['artifact'][field] = value
    with pytest.raises(ValueError):
        Script.model_validate(owner)


@pytest.mark.parametrize('route', ROUTES)
def test_real_loader_refuses_invalid_nested_records_without_replacing_files(tmp_path, route):
    data = payloads(route)
    owner_payload(data, route)['asset_image_requests'][0]['members'][0]['variant_id'] = 'foreign'
    with pytest.raises(CorruptStorageError):
        save_and_reload(tmp_path, data)
    assert json.loads((tmp_path / {'project-local': 'projects.json', 'project-series': 'series.json',
                                 'project-global': 'library_assets.json', 'standalone-series': 'series.json'}[route]).read_text()) == data[{'project-local': 'projects.json', 'project-series': 'series.json', 'project-global': 'library_assets.json', 'standalone-series': 'series.json'}[route]]


@pytest.mark.parametrize('phase', ['accepted', 'submitting', 'completed', 'cancelled'])
def test_model_load_uses_only_frozen_values_and_does_not_fill_optional_parameters(monkeypatch, phase):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution['intent']['params'] = {'size': '768*1024', 'watermark': False}
    execution['intent']['sources'] = {'params.size': 'request'}
    execution['intent']['request_fields'] = ['params.size']
    rehash(execution)
    if phase == 'completed':
        from src.models.provider_execution import ProviderExecutionRecord
        execution['phase'] = 'submitting'
        submitting = ProviderExecutionRecord.model_validate(execution)
        proof = sync_artifact_proof_from_record(submitting, relative_path=submitting.output_ref, sha256='a' * 64, size_bytes=17)
        execution.update(phase='completed', prepared_artifact=proof, artifact=proof)
    else:
        execution['phase'] = phase
    encoded = json.dumps(owner)
    blocked = Mock(side_effect=AssertionError('model load attempted live I/O or defaults'))
    monkeypatch.setattr('src.utils.model_catalog.get_catalog_accessor', blocked)
    monkeypatch.setattr('src.utils.generation_intent.get_catalog_accessor', blocked)
    monkeypatch.setattr('src.models.provider_binding.ProviderCredentialStore.__init__', blocked)
    monkeypatch.setattr('src.models.provider_binding.ProviderCredentialStore.bind', blocked)
    monkeypatch.setattr('src.models.provider_binding.ProviderCredentialStore.resolve_binding', blocked)
    monkeypatch.setattr('requests.Session', blocked)
    monkeypatch.setattr('subprocess.Popen', blocked)
    monkeypatch.setattr('uuid.uuid4', blocked)
    monkeypatch.setattr('builtins.open', blocked)
    monkeypatch.setattr('io.open', blocked)
    loaded = Script.model_validate_json(encoded)
    assert loaded.asset_image_requests[0].members[0].execution.intent['params'] == {'size': '768*1024', 'watermark': False}
    blocked.assert_not_called()


@pytest.mark.parametrize('tag', ['absent', None, 'future-contract'])
def test_studio_requires_the_explicit_bound_wan27_contract_tag(tag):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    if tag == 'absent':
        execution['intent'].pop('intent_contract_version', None)
    else:
        execution['intent']['intent_contract_version'] = tag
    rehash(execution)
    with pytest.raises(ValueError):
        Script.model_validate(owner)


def test_new_studio_record_requires_watermark_to_be_frozen_explicitly():
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution['intent']['params'].pop('watermark')
    execution['intent']['sources'].pop('params.watermark')
    execution['intent']['request_fields'].remove('params.watermark')
    rehash(execution)
    with pytest.raises(ValueError):
        Script.model_validate(owner)


@pytest.mark.parametrize('field', ['reference_sheet_request_id', 'asset_image_request_scope_id'])
@pytest.mark.parametrize('value', ['', 'not-a-uuid', 0, True, '11111111-1111-4111-8111-11111111111X'])
def test_new_server_identity_fields_reject_invalid_values_without_generating_replacements(field, value):
    if field == 'reference_sheet_request_id':
        with pytest.raises(ValueError):
            Character(id='legacy-id', name='old', description='', **{field: value})
    else:
        for model in (Script, Series):
            data = dict(id='legacy-id', title='old', created_at=0, updated_at=0, **{field: value})
            if model is Script:
                data['original_text'] = 'script'
            with pytest.raises(ValueError):
                model.model_validate(data)


def test_ordered_batch_has_no_artificial_ui_member_limit():
    owner = owner_payload(payloads(), 'project-local')
    owner['asset_image_requests'][0]['members'] = [
        {'variant_id': f'batch-{index}', 'execution': execution_payload(attempt=f'batch-{index}')}
        for index in range(7)
    ]
    restored = Script.model_validate(owner)
    assert [member.variant_id for member in restored.asset_image_requests[0].members] == [f'batch-{index}' for index in range(7)]


def test_client_key_is_namespaced_by_request_root_within_one_shared_owner():
    owner = owner_payload(payloads('project-series'), 'project-series')
    second = deepcopy(owner['asset_image_requests'][0])
    second['id'] = OTHER_REQUEST_ID
    second['request_root_id'] = 'another-project'
    second['members'] = [{'variant_id': 'second-attempt', 'execution': execution_payload(attempt='second-attempt')}]
    owner['asset_image_requests'].append(second)
    assert len(Series.model_validate(owner).asset_image_requests) == 2


def test_mutated_core_instances_are_revalidated_when_reused_as_members():
    owner = Script.model_validate(owner_payload(payloads(), 'project-local'))
    member = owner.asset_image_requests[0].members[0]
    member.execution.phase = 'completed'
    # No proof was ever persisted; passing a Pydantic instance cannot bypass validation.
    with pytest.raises(ValueError):
        Script.model_validate({'id': owner.id, 'title': owner.title, 'original_text': owner.original_text,
                              'created_at': owner.created_at, 'updated_at': owner.updated_at,
                              'asset_image_requests': owner.asset_image_requests})


@pytest.mark.parametrize('field,value', [
    ('sources', {'prompt': []}), ('sources', {'prompt': {'secret': 'not-provenance'}}),
    ('sources', {'prompt': False}), ('request_fields', ['prompt', {}]), ('preset_id', []),
])
def test_malformed_frozen_provenance_is_a_validation_error(field, value):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution['intent'][field] = value
    rehash(execution)
    with pytest.raises(ValueError):
        Script.model_validate(owner)


@pytest.mark.parametrize('region,endpoint', [
    ('cn-beijing', 'https://dashscope.aliyuncs.com'),
    ('ap-southeast-1', 'https://dashscope-intl.aliyuncs.com/'),
])
def test_original_classic_workspace_authority_is_preserved(region, endpoint):
    owner = owner_payload(payloads(), 'project-local')
    execution = owner['asset_image_requests'][0]['members'][0]['execution']
    execution['scope'].update(region=region, base_url=endpoint, workspace_id='original-workspace')
    rehash(execution)
    loaded = Script.model_validate(owner)
    assert loaded.asset_image_requests[0].members[0].execution.scope == execution['scope']
    execution['scope']['workspace_id'] = 'another-workspace'
    with pytest.raises(ValueError):
        Script.model_validate(owner)
