"""Shared async execution binds Seedance to original HTTP authority and bytes."""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.apps.playground.models import PlaygroundMode
from src.apps.playground.provider_video_execution import PlaygroundProviderVideoExecutionService
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_binding import MULEROUTER_HTTP_SOURCE_KEY, ProviderCredentialStore
from src.models.provider_recovery import build_accepted_provider_submission_and_record, record_with_changes
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import PreparedMedia, ProviderSubmitError, ProviderTaskStatus
from src.utils.media_versions import freeze_media
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_playground_provider_video_execution import FakeProvider, _generation, _intent
from tests.test_seedance_provider_runtime import SCOPE


@pytest.fixture
def setup(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    provider = FakeProvider()
    runtime = ProviderRecoveryRuntime(SCOPE, lambda: 'http-key', provider)
    service = PlaygroundProviderVideoExecutionService(
        storage, runtime_provider=lambda scope: runtime if scope == SCOPE else None, credential_store=credentials,
    )
    yield storage, credentials, provider, service, tmp_path
    storage.close()


def accepted(setup, mode, *, phase='accepted'):
    storage, credentials, _, _, root = setup
    binding = credentials.bind(SCOPE, 'http-key', source_key=MULEROUTER_HTTP_SOURCE_KEY)
    refs = []
    media = []
    count = 0 if mode == 't2v' else 1 if mode == 'i2v' else 2
    for index in range(count):
        ref = f'uploads/input-{index}.png'
        target = root / ref
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(rgb_png_bytes(f'color-{index}', width=256, height=256))
        version = freeze_media(ref, output_root=root, writer_lock=storage._writer_lock,
                               role='first_frame' if mode == 'i2v' else 'reference_image', ordinal=index, max_bytes=20*1024*1024)
        media.append(PreparedMedia.from_media_version(version, output_root=root, max_bytes=20*1024*1024))
        refs.append(ref)
    params = {'duration': 5, 'resolution': '1080p', 'aspect_ratio': '16:9', 'audio': False, 'seed': 0}
    if mode == 't2v':
        params['watermark'] = False
    intent = replace(_intent(), model_id=f'seedance/seedance-2.0-video#{mode}', catalog_mode=mode, mode=mode,
                     params=params, sources={k: 'request' for k in ['model_id','mode','prompt',*(f'params.{p}' for p in params)]},
                     request_fields=('model_id','mode','prompt',*(f'params.{p}' for p in params)))
    _, record = build_accepted_provider_submission_and_record(
        intent, provider_model_id=f'seedance-2.0-{mode}', media=media, scope=SCOPE,
        credential_ref=binding.credential_ref, attempt_id='attempt-1',
    )
    if phase != 'accepted':
        record = record_with_changes(record, phase=phase)
    gen = _generation(record).model_copy(update={'mode': PlaygroundMode(mode), 'model_id': record.provider_model_id,
                                                'parameters': params, 'input_media': refs})
    storage.add_generation(gen)
    return record, media


@pytest.mark.parametrize('mode', ['t2v', 'i2v', 'r2v'])
def test_submit_uses_http_binding_and_frozen_data_without_upload(setup, mode, monkeypatch):
    storage, credentials, provider, service, root = setup
    record, media = accepted(setup, mode)
    for ref in storage.get_generation('generation-1').input_media:
        (root/ref).write_bytes(b'changed source after acceptance')
    def forbidden(*args, **kwargs):
        raise AssertionError('Seedance must not use DashScope upload or CLI')
    monkeypatch.setattr('src.models.provider_upload.upload_dashscope_temporary_media', forbidden)
    def verify_checkpoint():
        gen = storage.get_generation('generation-1')
        assert gen.attempts[0].provider_execution['phase'] == 'submitting'
    provider.before_submit_network = verify_checkpoint
    service.run_attempt('generation-1', 'attempt-1')
    assert len(provider.submits) == 1
    request, credential = provider.submits[0]
    assert credential.scope == SCOPE and credential.secret == 'http-key'
    assert [m.url for m in request.media] == [m.url for m in media]
    assert request.intent.params['seed'] == 0 and request.intent.params['audio'] is False
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = 'completed'
    restarted = PlaygroundProviderVideoExecutionService(storage, runtime_provider=service._runtime_provider,
                                                        credential_store=credentials)
    restarted.run_attempt('generation-1','attempt-1')
    restarted.run_attempt('generation-1','attempt-1')
    assert len(provider.submits) == 1 and len(provider.queries) == 1 and len(provider.fetches) == 1
    assert provider.queries[0][0].provider_model_id == record.provider_model_id
    assert storage.get_generation('generation-1').attempts[0].status == 'completed'


def test_seedance_lost_submission_receipt_never_reposts(setup):
    storage, _, provider, service, _ = setup
    accepted(setup, 't2v')
    provider.submit_error = ProviderSubmitError('transport')
    service.run_attempt('generation-1','attempt-1')
    service.run_attempt('generation-1','attempt-1')
    assert len(provider.submits) == 1
    assert storage.get_generation('generation-1').attempts[0].status == 'needs_confirmation'


def test_seedance_saved_submitting_without_handle_never_submits(setup):
    storage, _, provider, service, _ = setup
    accepted(setup, 't2v', phase='submitting')
    service.run_attempt('generation-1','attempt-1')
    assert provider.submits == []
    assert storage.get_generation('generation-1').attempts[0].status == 'needs_confirmation'


def test_changed_http_key_cannot_query_original_receipt(setup):
    storage, credentials, provider, service, _ = setup
    accepted(setup, 't2v')
    service.run_attempt('generation-1','attempt-1')
    replacement = ProviderRecoveryRuntime(SCOPE, lambda: 'different-key', provider)
    restarted = PlaygroundProviderVideoExecutionService(
        storage, runtime_provider=lambda scope: replacement, credential_store=credentials,
    )
    restarted.run_attempt('generation-1','attempt-1')
    assert len(provider.submits) == 1 and provider.queries == []
    attempt = storage.get_generation('generation-1').attempts[0]
    assert attempt.status == 'needs_confirmation' and attempt.error == 'provider_credentials_required'
    assert attempt.provider_execution['handle']['credential_ref'] == provider.submits[0][1].credential_ref
