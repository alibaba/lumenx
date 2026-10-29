"""Bounded Seedance HTTP admission with actual catalog and frozen inputs."""
from __future__ import annotations

import base64
from dataclasses import replace
from unittest.mock import Mock

import pytest

from src.apps.playground.models import GenerateRequest, PlaygroundMode
from src.apps.playground.seedance_video_admission import (
    SeedanceCapabilityError,
    SeedanceVideoAdmission,
    admitted_seedance_video_generation,
)
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_binding import MULEROUTER_HTTP_SOURCE_KEY, ProviderCredentialStore
from src.models.provider_recovery import ProviderRecoveryError, reconstruct_submission_from_record
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import DirectoryWriterLock
from src.utils.model_catalog import CatalogAccessor, load_generated_model_catalog
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import JPEG_240_BASE64, WEBP_240_BASE64, png_bytes

SCOPE = ProviderScope('mulerouter', 'mulerouter', 'mulerouter_http', 'https://api.mulerouter.ai', None)
SECRET = 'fake'
MODELS = {f'seedance-2.0-{mode}': f'seedance/seedance-2.0-video#{mode}' for mode in ('t2v', 'i2v', 'r2v')}


def image_ref(root, name='frame.png', data=None):
    path = root / 'playground' / 'uploads' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rgb_png_bytes(name) if data is None else data)
    return f'playground/uploads/{name}'


def request(mode='t2v', **changes):
    values = dict(client_request_id='seedance-request', mode=mode, model_id=f'seedance-2.0-{mode}',
                  prompt='Keep this exact prompt.', parameters={}, input_media=[], batch_size=2)
    values.update(changes)
    return GenerateRequest.model_validate(values)


@pytest.fixture
def rig(tmp_path):
    lease = DirectoryWriterLock(tmp_path)
    lease.acquire()
    storage = PlaygroundStorage(tmp_path, writer_lock=lease)
    secret = Mock(return_value=SECRET)
    provider = Mock(return_value=ProviderRecoveryRuntime(SCOPE, secret, provider=None))
    credentials = ProviderCredentialStore(tmp_path, writer_lock=lease, runtime_lock=storage._lock)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider,
                                provider_credential_store=credentials)
    yield tmp_path, storage, service, provider, secret, credentials
    storage.close()
    lease.close()


@pytest.mark.parametrize('model_id', [*MODELS, *MODELS.values()])
def test_known_model_wrong_video_mode_still_routes_to_validation(model_id):
    assert all(admitted_seedance_video_generation(mode, model_id) for mode in
               (PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V))
    assert not admitted_seedance_video_generation(PlaygroundMode.I2V, 'seedance-2.0-fast-i2v')


@pytest.mark.parametrize('model_id', [*MODELS, *MODELS.values()])
def test_actual_catalog_defaults_identity_and_each_member_record(rig, monkeypatch, model_id: str):
    from src.apps.playground import seedance_video_admission as module
    root, _, service, provider, secret, credentials = rig
    canonical = MODELS.get(model_id, model_id)
    mode = canonical.rsplit('#', 1)[1]
    normalizer = Mock(wraps=module.normalize_generation_intent)
    bind = Mock(wraps=credentials.bind)
    monkeypatch.setattr(module, 'normalize_generation_intent', normalizer)
    monkeypatch.setattr(credentials, 'bind', bind)
    refs = [] if mode == 't2v' else [image_ref(root)]
    receipt = service.admit_generation(request(mode, model_id=model_id, input_media=refs))
    assert receipt.generation is not None
    records = [a.provider_execution for a in receipt.generation.attempts]
    assert len(records) == 2 and records[0]['attempt_id'] != records[1]['attempt_id']
    expected = dict(duration=5, resolution='1080p', aspect_ratio='16:9', audio=True)
    if mode == 't2v':
        expected['watermark'] = False
    for record in records:
        assert record['schema_version'] == 1 and record['phase'] == 'accepted'
        assert record['provider_model_id'] == f'seedance-2.0-{mode}'
        assert record['intent']['model_id'] == canonical
        assert record['intent']['params'] == expected
        assert record['intent']['prompt'] == 'Keep this exact prompt.'
        assert record['scope'] == SCOPE.to_dict()
        assert record['handle'] is None
    normalizer.assert_called_once()
    provider.assert_called_once_with(provider_model_id=f'seedance-2.0-{mode}', gateway='mulerouter')
    secret.assert_called_once()
    assert bind.call_args.kwargs == {'source_key': MULEROUTER_HTTP_SOURCE_KEY}


@pytest.mark.parametrize('mode', ['t2v', 'i2v', 'r2v'])
def test_explicit_false_seed_zero_and_ratio_survive(rig, mode):
    root, _, service, *_ = rig
    params = {'duration': 15, 'resolution': '720p', 'ratio': '3:4', 'audio': False, 'seed': 0}
    if mode == 't2v':
        params['watermark'] = False
    refs = [] if mode == 't2v' else [image_ref(root)]
    accepted = service.admit_generation(request(mode, input_media=refs, parameters=params)).generation
    frozen = accepted.attempts[0].provider_execution['intent']['params']
    assert frozen == {**{k: v for k, v in params.items() if k != 'ratio'}, 'aspect_ratio': '3:4'}


@pytest.mark.parametrize('changes', [
    {'mode': 'i2v'}, {'mode': 'v2v'}, {'negative_prompt': 'unsupported'},
    {'parameters': {'duration': 16}}, {'parameters': {'duration': True}},
    {'parameters': {'resolution': '480p'}}, {'parameters': {'seed': 2**32}},
    {'parameters': {'use_fast': True}}, {'parameters': {'last_frame': 'tail.png'}},
    {'parameters': {'ratio': '4:3', 'aspect_ratio': '3:4'}},
])
def test_invalid_intent_never_freezes_binds_or_persists(rig, monkeypatch, changes):
    root, storage, service, provider, secret, _ = rig
    freeze = Mock(side_effect=AssertionError('invalid intent cannot freeze'))
    monkeypatch.setattr('src.apps.playground.seedance_video_admission.freeze_media', freeze)
    body = request().model_dump()
    body.update(changes)
    with pytest.raises(IntentValidationError):
        service.admit_generation(GenerateRequest(**body))
    freeze.assert_not_called()
    provider.assert_not_called()
    secret.assert_not_called()
    assert storage.read_request('seedance-request') is None
    assert not (root / 'provider_credentials.json').exists()


@pytest.mark.parametrize('mode', ['i2v', 'r2v'])
def test_watermark_is_rejected_outside_t2v_before_binding(rig, mode):
    _, _, service, provider, secret, _ = rig
    with pytest.raises(IntentValidationError):
        service.admit_generation(request(mode, parameters={'watermark': False}))
    provider.assert_not_called()
    secret.assert_not_called()


@pytest.mark.parametrize('mode,count', [('t2v', 1), ('i2v', 0), ('i2v', 2), ('r2v', 0), ('r2v', 10)])
def test_media_counts_reject_before_secret_binding(rig, mode, count):
    root, storage, service, _, secret, _ = rig
    ref = image_ref(root)
    with pytest.raises(IntentValidationError):
        service.admit_generation(request(mode, input_media=[ref] * count))
    secret.assert_not_called()
    assert storage.read_request('seedance-request') is None
    assert not (root / 'provider_credentials.json').exists()


@pytest.mark.parametrize('case', ['wrong-content', 'truncated', 'over-size', 'unsupported-av'])
def test_invalid_actual_bytes_reject_before_secret_binding(rig, case):
    root, storage, service, _, secret, _ = rig
    data = {'wrong-content': b'GIF89a' + b'0' * 40, 'truncated': rgb_png_bytes('cut')[:40],
            'over-size': b'x' * (20 * 1024 * 1024 + 1), 'unsupported-av': b'not-a-video'}[case]
    ref = image_ref(root, 'source.mp4' if case == 'unsupported-av' else 'source.png', data)
    with pytest.raises(IntentValidationError):
        service.admit_generation(request('i2v', input_media=[ref]))
    secret.assert_not_called()
    assert storage.read_request('seedance-request') is None
    assert not (root / 'provider_credentials.json').exists()


@pytest.mark.parametrize('name,data', [('frame.jpg', base64.b64decode(JPEG_240_BASE64)),
                                    ('frame.webp', base64.b64decode(WEBP_240_BASE64)),
                                    ('alpha.png', png_bytes(256, 256, alpha=True)),
                                    ('wide.png', rgb_png_bytes('wide', width=10001, height=2))],
                         ids=['jpeg', 'webp', 'alpha-png', 'wide-png'])
def test_valid_product_images_have_no_invented_alpha_or_dimension_ban(rig, name, data):
    root, _, service, *_ = rig
    result = service.admit_generation(request('i2v', input_media=[image_ref(root, name, data)]))
    assert result.generation.attempts[0].provider_execution is not None


@pytest.mark.parametrize('scope', [
    replace(SCOPE, gateway='mulerun_token'), replace(SCOPE, backend='mulerun_cli'),
    replace(SCOPE, region='cn-beijing'), replace(SCOPE, workspace_id='workspace'),
    replace(SCOPE, account_identity='account'), replace(SCOPE, base_url='http://api.mulerouter.ai'),
    replace(SCOPE, base_url='https://api.mulerouter.ai:443'),
    replace(SCOPE, base_url='https://api.mulerouter.ai/path'),
    replace(SCOPE, base_url='https://api.mulerouter.ai//'),
])
def test_runtime_scope_is_exact_before_secret(rig, scope):
    root, _, service, provider, secret, _ = rig
    provider.return_value = ProviderRecoveryRuntime(scope, secret, provider=None)
    with pytest.raises(IntentValidationError):
        service.admit_generation(request())
    secret.assert_not_called()
    assert not (root / 'provider_credentials.json').exists()


@pytest.mark.parametrize('reason', ['seedance_http_key_required', 'seedance_http_endpoint_unsupported',
                                    'seedance_cli_submission_unsafe'])
def test_known_capability_failure_precedes_media_and_all_persistence(rig, monkeypatch, reason):
    root, storage, service, provider, secret, _ = rig
    provider.side_effect = ProviderRecoveryError(reason)
    freeze = Mock(side_effect=AssertionError('unavailable channel cannot freeze'))
    monkeypatch.setattr('src.apps.playground.seedance_video_admission.freeze_media', freeze)
    with pytest.raises(SeedanceCapabilityError) as error:
        service.admit_generation(request('i2v', input_media=['missing.png']))
    assert error.value.reason == reason
    freeze.assert_not_called()
    secret.assert_not_called()
    assert storage.read_request('seedance-request') is None
    assert not (root / 'provider_credentials.json').exists()


def test_other_runtime_failure_does_not_become_safe_capability_error(rig):
    _, _, service, provider, *_ = rig
    provider.side_effect = ProviderRecoveryError('unrelated-provider-failure')
    with pytest.raises(ProviderRecoveryError, match='unrelated-provider-failure'):
        service.admit_generation(request())


def test_ordered_repeated_r2v_inputs_freeze_once_and_reconstruct_without_source(rig):
    root, _, service, *_ = rig
    first, second = image_ref(root, 'first.png'), image_ref(root, 'second.png')
    generation = service.admit_generation(request('r2v', input_media=[first, second, first])).generation
    record = ProviderExecutionRecord.model_validate(generation.attempts[0].provider_execution)
    media = record.media
    assert len(media) == 3
    assert [item['ordinal'] for item in media] == [0, 1, 2]
    assert all(item['role'] == 'reference_image' for item in media)
    assert media[0]['sha256'] == media[2]['sha256'] != media[1]['sha256']
    (root / first).unlink()
    (root / second).unlink()
    submission = reconstruct_submission_from_record(record, output_root=root)
    assert len(submission.media) == 3
    assert all(item.transport_binding == 'data_uri_sha256_v1' for item in submission.media)


def test_original_request_replay_skips_media_catalog_runtime_and_rebinding(rig, monkeypatch):
    root, _, service, provider, _, credentials = rig
    frame = image_ref(root)
    body = request('i2v', input_media=[frame])
    accepted = service.admit_generation(body)
    (root / frame).unlink()
    fail = Mock(side_effect=AssertionError('replay must only read the original receipt'))
    monkeypatch.setattr('src.apps.playground.seedance_video_admission.freeze_media', fail)
    monkeypatch.setattr('src.apps.playground.seedance_video_admission.get_catalog_accessor', fail)
    monkeypatch.setattr(credentials, 'bind', fail)
    provider.side_effect = AssertionError('replay must not inspect runtime')
    replay = service.admit_generation(body)
    assert replay.replayed and replay.generation == accepted.generation
    fail.assert_not_called()
    assert provider.call_count == 1


@pytest.mark.parametrize('key,value', [('api_model_id', 'seedance-2.0-i2v'), ('gateway', 'mulerouter_http')])
def test_catalog_identity_drift_rejects_before_runtime(rig, monkeypatch, key, value):
    _, _, service, provider, *_ = rig
    data = load_generated_model_catalog()
    data['modes']['seedance/seedance-2.0-video#t2v']['runtime']['mulerouter'][key] = value
    monkeypatch.setattr(SeedanceVideoAdmission, '_catalog_accessor', lambda _: CatalogAccessor(data))
    with pytest.raises(IntentValidationError):
        service.admit_generation(request())
    provider.assert_not_called()


def test_nine_ordered_references_are_accepted_with_boundary_values(rig):
    root, _, service, *_ = rig
    frame = image_ref(root)
    receipt = service.admit_generation(request('r2v', input_media=[frame] * 9, batch_size=1,
                                              parameters={'duration': 4, 'seed': -1}))
    assert receipt.generation is not None
    record = receipt.generation.attempts[0].provider_execution
    assert len(record['media']) == 9
    assert [item['ordinal'] for item in record['media']] == list(range(9))
    assert record['intent']['params']['duration'] == 4
    assert record['intent']['params']['seed'] == -1
