"""New Wan Edit contract, real frozen media/Core/HTTP; external transport only."""

from dataclasses import asdict, replace
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from src.models import provider_media_execution as execution
from src.models import provider_media_intents as intents, provider_recovery as recovery, provider_tasks as tasks
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_runtime import bind_media_runtime_authority, resolve_media_runtime_authority
from src.models.wan27_image_contract import WAN27_IMAGE_CONTRACT_VERSION
from src.utils.json_storage import atomic_write_json
from src.utils.media_versions import freeze_media
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_provider_async_image_operations as operations
from tests import test_wan27_core_execution as old_wan
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan27_bound_image import response

VERSION = 'provider-wan27-i2i-bound-http-v1'
owned = operations.owned
transport = operations.transport


def context(owned, *, model='wan2.7-image', width=240):
    source = owned.root / 'uploads' / 'reference.png'
    source.parent.mkdir(exist_ok=True)
    original = rgb_png_bytes('accepted Wan reference', width=width, height=240)
    source.write_bytes(original)
    version = freeze_media('uploads/reference.png', output_root=owned.root, writer_lock=owned.lease,
                           role='reference_image', ordinal=0, max_bytes=operations.LIMIT)
    frozen = old_wan.intent(model, mode='i2i')
    environment = RuntimeEnvironment({'DASHSCOPE_API_KEY': operations.SYNTHETIC_IMAGE_CREDENTIAL,
                                      'DASHSCOPE_WORKSPACE_ID': 'original-workspace'})
    store = ProviderCredentialStore(owned.root, writer_lock=owned.lease, runtime_lock=owned.lock)
    with bind_media_runtime_authority(environment, store, image_intent=frozen, image_contract_version=VERSION) as authority:
        accepted = intents.build_accepted_async_image_record(
            intent=frozen, provider_model_id=model, media=[asdict(version)], scope=authority.scope,
            credential_ref=authority.credential_ref, attempt_id='wan-edit', output_ref='assets/studio/wan-edit.png',
            image_contract_version=VERSION,
        )
    return SimpleNamespace(operation='wan', accepted=accepted,
                           record=recovery.record_with_changes(accepted, phase='submitting'),
                           authority=authority, source=source, version=version, original=original,
                           environment=environment, intent=frozen)


@pytest.mark.parametrize('model', ['wan2.7-image', 'wan2.7-image-pro'])
def test_frozen_edit_bytes_and_saved_handle_survive_query_failure(model, owned, tmp_path, transport):
    ctx = context(owned, model=model)
    ctx.source.write_bytes(rgb_png_bytes('author changed source'))
    saved, handle = operations.saved_submit(ctx, owned, tmp_path, transport)
    payload = json.loads(transport['calls'][0][0].body)
    assert payload == {
        'model': model,
        'input': {'messages': [{'role': 'user', 'content': [
            {'image': 'data:image/png;base64,' + base64.b64encode(ctx.original).decode('ascii')},
            {'text': ctx.intent.prompt},
        ]}]},
        'parameters': {'size': '1024*1024', 'watermark': False, 'seed': 0, 'n': 1},
    }
    assert saved.media[0]['sha256'] == hashlib.sha256(ctx.original).hexdigest()
    assert saved.intent['intent_contract_version'] == VERSION
    assert handle.query_contract is tasks.ProviderImageQueryContract.WAN27_IMAGE
    transport['queue'].append(requests.ReadTimeout('synthetic query failure'))
    with pytest.raises(tasks.ProviderQueryError, match='transport'):
        execution.query_async_image(handle, ctx.authority)
    ctx.source.unlink()
    reloaded = ProviderExecutionRecord.model_validate_json((owned.root / 'execution.json').read_text())
    original_handle = recovery.image_query_handle_from_record(reloaded)
    assert original_handle == handle
    store = ProviderCredentialStore(owned.root, writer_lock=owned.lease, runtime_lock=owned.lock)
    assert handle.scope is not None and handle.credential_ref is not None
    with resolve_media_runtime_authority(
        ctx.environment, store, media_kind='image', provider_model_id=model,
        scope=handle.scope, credential_ref=handle.credential_ref, image_contract_version=VERSION,
    ) as authority:
        transport['queue'].append(response(operations.query_body('wan')))
        observation = execution.query_async_image(original_handle, authority)
        observed = recovery.record_with_changes(reloaded, phase='observed', observation=observation.to_dict())
        with owned.guard():
            atomic_write_json(owned.root / 'execution.json', observed.model_dump(mode='json'))
        transport['queue'].append(response(rgb_png_bytes('recovered output')))
        artifact = execution.fetch_async_image_to_private_file(
            observed, authority, scratch_dir=tmp_path / 'fetch', max_bytes=operations.LIMIT,
        )
    assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    assert artifact.encoding == 'image/png'
    assert [call.method for call, _ in transport['calls']] == ['POST', 'GET', 'GET', 'GET']
    assert not list((tmp_path / 'submit-scratch').iterdir())
    assert all(not options['proxies'] and options['allow_redirects'] is False for _, options in transport['calls'])
    assert all(name not in transport['calls'][-1][0].headers for name in ('Authorization', 'Cookie', 'X-DashScope-WorkSpace'))


def test_invalid_reference_dimensions_are_rejected_before_post(owned, tmp_path, transport):
    ctx = context(owned, width=239)
    with pytest.raises(ValueError, match='dimensions'):
        execution.submit_async_image(ctx.record, ctx.authority, output_root=owned.root,
                                     scratch_dir=tmp_path / 'invalid', max_bytes=operations.LIMIT,
                                     on_submitted=lambda _handle: pytest.fail('Invalid input obtained a paid handle'))
    assert not transport['calls']


def test_edit_does_not_inherit_pro_t2i_4k_or_legacy_parameters(owned, transport):
    ctx = context(owned, model='wan2.7-image-pro')
    for params in ({'size': '4096*4096', 'watermark': False},
                   {'size': '1024*1024', 'watermark': False, 'negative_prompt': 'do not silently remove'}):
        with pytest.raises(ValueError):
            intents.build_accepted_async_image_record(
                intent=replace(ctx.intent, params=params), provider_model_id=ctx.authority.provider_model_id,
                media=[asdict(ctx.version)], scope=ctx.authority.scope, credential_ref=ctx.authority.credential_ref,
                attempt_id='bad-edit', output_ref='assets/studio/bad-edit.png', image_contract_version=VERSION,
            )
    assert not transport['calls']
    old = old_wan.record(frozen=old_wan.intent(params={'size': '4096*4096', 'watermark': False}))
    assert old.intent['intent_contract_version'] == WAN27_IMAGE_CONTRACT_VERSION
    with pytest.raises(ValueError):
        old_wan.record(frozen=ctx.intent)


def test_new_edit_contract_cannot_fall_back_to_legacy_sync_execution(owned, tmp_path, transport):
    ctx = context(owned)
    sync = intents.build_accepted_sync_artifact_record(
        intent=ctx.intent, provider_model_id=ctx.authority.provider_model_id, media_kind='image',
        media=[asdict(ctx.version)], scope=ctx.authority.scope, credential_ref=ctx.authority.credential_ref,
        attempt_id='sync-edit', output_ref='assets/studio/sync-edit.png', image_contract_version=VERSION,
    )
    with pytest.raises(execution.ProviderMediaExecutionError, match='async'):
        execution.execute_sync_artifact_to_private_file(
            recovery.record_with_changes(sync, phase='submitting'), ctx.authority,
            output_root=owned.root, scratch_dir=tmp_path / 'sync', max_bytes=operations.LIMIT,
        )
    assert not transport['calls']


def test_retained_edit_handle_can_finish_without_reconstructing_missing_material(owned, tmp_path, transport):
    ctx = context(owned)
    saved, handle = operations.saved_submit(ctx, owned, tmp_path, transport)
    value = saved.model_dump(mode='json')
    del value['intent']['intent_contract_version']
    with owned.guard():
        atomic_write_json(owned.root / 'execution.json', value)
    retained = ProviderExecutionRecord.model_validate_json((owned.root / 'execution.json').read_text())
    assert retained.intent['mode'] == 'i2i' and 'intent_contract_version' not in retained.intent
    assert recovery.image_query_handle_from_record(retained) == handle
    with pytest.raises(recovery.ProviderRecoveryError):
        intents.validate_async_image_record_intent(retained)
    transport['queue'].append(response(operations.query_body('wan')))
    observation = execution.query_async_image(handle, ctx.authority)
    observed = recovery.record_with_changes(retained, phase='observed', observation=observation.to_dict())
    transport['queue'].append(response(rgb_png_bytes('retained original result')))
    artifact = execution.fetch_async_image_to_private_file(observed, ctx.authority,
        scratch_dir=tmp_path / 'retained-fetch', max_bytes=operations.LIMIT)
    assert artifact.encoding == 'image/png'
    assert 'intent_contract_version' not in observed.intent
    assert [request.method for request, _ in transport['calls']] == ['POST', 'GET', 'GET']
