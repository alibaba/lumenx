"""New closed scene/prop history without broadening any generation producer."""

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from src.apps.comic_gen.asset_image_execution import AssetImageRequestCommand
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord, STUDIO_ASSET_IMAGE_ENTRYPOINT
from src.apps.comic_gen.models import Prop, Scene
from src.utils.request_identity import fingerprint_request
from tests import test_studio_core_image_models as legacy


NEW_ENTRYPOINT = 'studio.asset.scene_prop.v1'
KINDS = ('scene', 'prop')


def new_request(kind, route='project-local', model='wan2.7-image', *, number=2):
    raw = legacy.request_payload(route, model)
    raw.update(
        id=f'{number:08d}-2222-4222-8222-222222222222',
        entrypoint=NEW_ENTRYPOINT, asset_type=kind, channel='image',
        client_request_id=f'{kind}-key-{number}', asset_id='shared-asset-id',
    )
    raw['members'] = [
        {'variant_id': f'{kind}-{number}-{index}', 'execution': legacy.execution_payload(model, f'{kind}-{number}-{index}', phase)}
        for index, phase in enumerate(('accepted', 'submitting', 'completed', 'cancelled'))
    ]
    return raw


@pytest.mark.parametrize('raw_extra,expected', [
    ({}, '42a4c161f29d76c2cf766e0dbe6adcef9993bb57fefc40b454073b5e935088e5'),
    ({'seed': 0, 'watermark': False}, 'c4aba8b690207674731139898036c88e043471e87bc0dd45d80ee0591207dc66'),
    ({'prompt': None}, 'b2858d04a3d813620500996c058d4047700f7f0237a548238c18273e86e98520'),
])
def test_legacy_command_omissions_and_fingerprint_golden_remain_exact(raw_extra, expected):
    raw = {'client_request_id': 'old-key', 'asset_id': 'legacy-character', **raw_extra}
    command = AssetImageRequestCommand.model_validate(raw)
    assert command.asset_type == 'character' and command.generation_type == 'reference_sheet'
    assert command.model_dump(exclude_unset=True) == raw
    payload = {'request_root_kind': 'project', 'request_root_id': 'project-root',
               'body': command.model_dump(exclude={'client_request_id'}, exclude_unset=True)}
    assert STUDIO_ASSET_IMAGE_ENTRYPOINT == 'studio.asset.reference_sheet.v1'
    assert fingerprint_request(payload, entrypoint=STUDIO_ASSET_IMAGE_ENTRYPOINT) == expected
    original = legacy.request_payload()
    assert AssetImageRequestRecord.model_validate_json(json.dumps(original)).model_dump(mode='json') == original


def test_legacy_explicit_null_seed_is_still_rejected():
    with pytest.raises(ValueError, match='seed must be omitted'):
        AssetImageRequestCommand.model_validate({'client_request_id': 'old-key', 'asset_id': 'legacy-character', 'seed': None})


@pytest.mark.parametrize('kind', KINDS)
def test_l2b_new_command_requires_the_explicit_matching_channel(kind):
    raw = {'client_request_id': 'new', 'asset_id': 'asset', 'asset_type': kind, 'generation_type': 'all'}
    assert AssetImageRequestCommand.model_validate(raw).model_dump(exclude_unset=True) == raw
    with pytest.raises(ValueError):
        AssetImageRequestCommand.model_validate({key: value for key, value in raw.items() if key != 'generation_type'})


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('route', legacy.ROUTES)
@pytest.mark.parametrize('model', legacy.PROVIDER_MODELS)
def test_mixed_histories_and_new_pointers_roundtrip_through_actual_owner_files(tmp_path, kind, route, model):
    data = legacy.payloads(route, model)
    owner = legacy.owner_payload(data, route)
    record = new_request(kind, route, model)
    original = deepcopy(owner['asset_image_requests'][0])
    owner['asset_image_requests'].append(record)
    owner[f'{kind}s'] = [{'id': 'shared-asset-id', 'name': kind, 'description': '', 'image_request_id': record['id']}]
    projects, series, library = legacy.save_and_reload(tmp_path, data)
    actual = {'script': projects['project-root'], 'series': series['series-root'], 'global': library}[record['owner_kind']]
    assert [item.model_dump(mode='json') for item in actual.asset_image_requests] == [original, record]
    assert getattr(actual, f'{kind}s')[0].image_request_id == record['id']
    assert [member.execution.phase for member in actual.asset_image_requests[1].members] == ['accepted', 'submitting', 'completed', 'cancelled']
    assert sum(len(item.asset_image_requests) for item in [*projects.values(), *series.values(), library]) == 2


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('route', legacy.ROUTES)
def test_new_history_without_live_root_or_target_remains_loadable(tmp_path, kind, route):
    data = legacy.payloads(route)
    owner = legacy.owner_payload(data, route)
    record = new_request(kind, route)
    owner['asset_image_requests'].append(record)
    owner[f'{kind}s'] = []
    if route in {'project-series', 'project-global'}:
        data['projects.json'] = {}
    else:
        owner['asset_image_request_scope_id'] = legacy.OTHER_SCOPE_ID
    projects, series, library = legacy.save_and_reload(tmp_path, data)
    actual = projects['project-root'] if route == 'project-local' else library if route == 'project-global' else series['series-root']
    assert actual.asset_image_requests[1].model_dump(mode='json') == record


@pytest.mark.parametrize('entrypoint', [STUDIO_ASSET_IMAGE_ENTRYPOINT, NEW_ENTRYPOINT])
@pytest.mark.parametrize('kind', ['character', *KINDS])
@pytest.mark.parametrize('channel', ['reference_sheet', 'image'])
def test_only_exact_entrypoint_kind_channel_tuples_are_valid(entrypoint, kind, channel):
    raw = legacy.request_payload()
    raw.update(entrypoint=entrypoint, asset_type=kind, channel=channel)
    valid = (entrypoint, kind, channel) in {
        (STUDIO_ASSET_IMAGE_ENTRYPOINT, 'character', 'reference_sheet'),
        (NEW_ENTRYPOINT, 'scene', 'image'), (NEW_ENTRYPOINT, 'prop', 'image'),
    }
    if valid:
        assert AssetImageRequestRecord.model_validate(raw).model_dump(mode='json') == raw
    else:
        with pytest.raises(ValueError):
            AssetImageRequestRecord.model_validate(raw)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('new_scope', [False, True])
def test_same_root_key_is_not_reusable_across_entrypoints_or_scope(kind, new_scope):
    owner = legacy.owner_payload(legacy.payloads('project-series'), 'project-series')
    raw = new_request(kind, 'project-series')
    raw['client_request_id'] = owner['asset_image_requests'][0]['client_request_id']
    if new_scope:
        raw['request_root_scope_id'] = legacy.OTHER_SCOPE_ID
    owner['asset_image_requests'].append(raw)
    with pytest.raises(ValueError, match='Duplicate Studio image request identity'):
        legacy.owner_model('project-series').model_validate(owner)


@pytest.mark.parametrize('root_change', ['id', 'kind'])
def test_same_key_remains_scoped_by_original_request_root(root_change):
    owner = legacy.owner_payload(legacy.payloads('project-series'), 'project-series')
    raw = new_request('scene', 'project-series')
    raw['client_request_id'] = owner['asset_image_requests'][0]['client_request_id']
    if root_change == 'id':
        raw['request_root_id'] = 'other-project'
    else:
        owner['asset_image_requests'][0]['request_root_id'] = 'series-root'
        raw.update(request_root_kind='series', request_root_id='series-root', request_root_series_id=None)
    owner['asset_image_requests'].append(raw)
    assert len(legacy.owner_model('project-series').model_validate(owner).asset_image_requests) == 2


def test_scene_and_prop_same_key_conflict_even_with_different_asset_ids():
    owner = legacy.owner_payload(legacy.payloads(), 'project-local')
    scene = new_request('scene')
    prop = new_request('prop', number=3)
    prop.update(client_request_id=scene['client_request_id'], asset_id='different-prop')
    owner['asset_image_requests'] = [scene, prop]
    with pytest.raises(ValueError, match='Duplicate Studio image request identity'):
        legacy.owner_model('project-local').model_validate(owner)


@pytest.mark.parametrize('model', [Scene, Prop])
@pytest.mark.parametrize('provided', ['missing', None, legacy.REQUEST_ID])
def test_pointer_load_has_no_random_identity_and_preserves_field_presence(model, provided, monkeypatch):
    raw = {'id': 'old', 'name': 'old', 'description': 'same'}
    if provided != 'missing':
        raw['image_request_id'] = provided
    blocked = Mock(side_effect=AssertionError('load created identity'))
    monkeypatch.setattr('uuid.uuid4', blocked)
    loaded = model.model_validate(raw)
    assert loaded.image_request_id == (None if provided == 'missing' else provided)
    assert loaded.model_dump(mode='json', exclude_unset=True) == raw
    blocked.assert_not_called()


@pytest.mark.parametrize('model', [Scene, Prop])
@pytest.mark.parametrize('value', ['', 0, True, 'not-a-uuid', 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'])
def test_pointer_rejects_invalid_or_noncanonical_server_identity(model, value):
    with pytest.raises(ValueError):
        model.model_validate({'id': 'asset', 'name': 'name', 'description': '', 'image_request_id': value})


@pytest.mark.parametrize('kind', KINDS)
def test_new_model_load_does_not_read_live_catalog_credentials_media_or_transport(monkeypatch, kind):
    raw = new_request(kind)
    encoded = json.dumps(raw)
    blocked = Mock(side_effect=AssertionError('history load performed live I/O'))
    for path in (
        'src.utils.model_catalog.get_catalog_accessor', 'src.utils.generation_intent.get_catalog_accessor',
        'src.models.provider_binding.ProviderCredentialStore.__init__',
        'src.models.provider_binding.ProviderCredentialStore.bind',
        'src.models.provider_binding.ProviderCredentialStore.resolve_binding',
        'requests.Session', 'subprocess.Popen', 'uuid.uuid4', 'builtins.open', 'io.open',
    ):
        monkeypatch.setattr(path, blocked)
    assert AssetImageRequestRecord.model_validate_json(encoded).model_dump(mode='json') == raw
    blocked.assert_not_called()
