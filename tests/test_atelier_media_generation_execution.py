"""Private sync media execution persists checkpoints and never retries ambiguity."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from src.apps.atelier.media_generation_execution import AtelierMediaGenerationExecutionService
from src.apps.atelier.media_generation_admission import AtelierMediaGenerationAdmissionService
from src.apps.atelier.generation_commands import ManualImageCandidateBatchCommand
from src.apps.atelier.tts_commands import TTSCandidateCommand
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_binding import SOURCE_KEY
from src.models.provider_media_runtime import MediaRuntimeAuthority, MediaRuntimeAuthorityError
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.json_storage import StorageConflictError, StorageError
from tests.frozen_image_fixtures import rgb_png_bytes

SCOPE = ProviderScope('dashscope', 'dashscope', 'dashscope', 'https://dashscope.aliyuncs.com', 'cn-beijing')


@pytest.fixture
def app(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(AtelierProject(id='p', title='Media', created_at=1, updated_at=1, nodes=[
        AtelierNode(id='image', project_id='p', type='image'),
        AtelierNode(id='audio', project_id='p', type='audio'),
    ]))

    @contextmanager
    def bind(*, media_kind, provider_model_id, **kwargs):
        yield authority(media_kind, provider_model_id)

    admission = AtelierMediaGenerationAdmissionService(store, authority_provider=bind, server_storage_identity='test-store')
    yield store, admission
    store.close()


def authority(kind, model):
    return MediaRuntimeAuthority(kind, model, SCOPE, 'credential', SOURCE_KEY, ResolvedCredential(SCOPE, 'credential', 'fake-private-secret'))


class Runtime:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.resolutions = []
        self.fail = None
        self.resolve_fail = False
        self.entered = None
        self.release = None
        self.result_change = None

    @contextmanager
    def resolve(self, record):
        self.resolutions.append(record.fingerprint)
        if self.resolve_fail:
            raise MediaRuntimeAuthorityError('credential_unavailable')
        yield authority(record.media_kind, record.provider_model_id)

    def execute(self, record, bound, *, output_root, scratch_dir, max_bytes):
        durable = self.store.get_project('p').generation_attempts[0]
        assert durable.provider_execution == record
        assert durable.status == 'processing' and record.phase == 'submitting'
        assert bound.credential_ref == record.credential_ref
        self.calls.append(record.fingerprint)
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        if self.fail:
            raise self.fail
        payload = rgb_png_bytes('sync-result') if record.media_kind == 'image' else b'fake-core-normalized-mp3'
        path = Path(scratch_dir) / ('result.png' if record.media_kind == 'image' else 'result.mp3')
        path.write_bytes(payload)
        result = SimpleNamespace(path=path, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload), media_kind=record.media_kind)
        if self.result_change:
            self.result_change(result)
        return result


def admitted(app, kind='image'):
    store, admission = app
    if kind == 'image':
        ack = admission.admit_image('p', 'image', ManualImageCandidateBatchCommand(
            expected_revision=0, client_request_id='media-request', model_id='qwen/qwen-image-2.0#image', mode='t2i', prompt='Moon', batch_size=1,
        ))
    else:
        ack = admission.admit_tts('p', 'audio', TTSCandidateCommand(expected_revision=0, client_request_id='media-request', voice='longanyang', text='Hello'))
    runtime = Runtime(store)
    service = AtelierMediaGenerationExecutionService(store, authority_resolver=runtime.resolve, executor=runtime.execute)
    return ack.members[0].attempt, runtime, service


def run(service, attempt):
    return service.run_attempt('p', attempt.attempt_id)


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_checkpoint_publish_complete_without_selecting(app, kind):
    attempt, runtime, service = admitted(app, kind)
    result = run(service, attempt)
    assert result.attempt.status == 'completed'
    assert result.candidate['media_url'] == attempt.provider_execution.output_ref
    assert result.attempt.provider_execution.artifact == result.attempt.provider_execution.prepared_artifact
    assert (app[0].data_dir / result.attempt.output_ref).is_file()
    assert all(not node.media_urls for node in result.project.nodes)
    assert len(runtime.calls) == 1
    again = run(service, attempt)
    assert again.attempt == result.attempt and len(runtime.calls) == 1


def test_provider_unknown_never_reexecutes(app):
    attempt, runtime, service = admitted(app)
    runtime.fail = RuntimeError('private secret must not escape')
    first = run(service, attempt)
    assert first.attempt.status == 'needs_confirmation'
    assert first.attempt.error_code == 'provider_sync_execution_unknown'
    assert 'private secret' not in first.model_dump_json()
    runtime.fail = None
    repeated = run(service, attempt)
    assert repeated.attempt.provider_execution.phase == 'submitting'
    assert len(runtime.calls) == 1 and len(runtime.resolutions) == 1


def test_credentials_missing_preserves_accepted_and_can_explicitly_continue(app):
    attempt, runtime, service = admitted(app)
    runtime.resolve_fail = True
    result = run(service, attempt)
    assert result.attempt.status == 'needs_confirmation'
    assert result.attempt.provider_execution.phase == 'accepted' and not runtime.calls
    runtime.resolve_fail = False
    done = service.continue_attempt('p', attempt.attempt_id, candidate_id=attempt.candidate_id,
        expected_attempt_revision=result.attempt.attempt_revision, execution_fingerprint=attempt.provider_execution.fingerprint)
    assert done.attempt.status == 'completed' and len(runtime.calls) == 1


def test_prepared_and_published_recovery_needs_no_authority_or_provider(app, monkeypatch):
    attempt, runtime, service = admitted(app)
    with monkeypatch.context() as patch:
        patch.setattr(service, '_complete', lambda *a: (_ for _ in ()).throw(StorageError('completion interrupted')))
        with pytest.raises(StorageError):
            run(service, attempt)
    saved = service.read_attempt('p', attempt.attempt_id).attempt
    assert saved.provider_execution.prepared_artifact is not None
    runtime.resolve_fail = True
    app[0].close()
    reopened = AtelierStorage(app[0].data_dir)
    try:
        recovery = AtelierMediaGenerationExecutionService(reopened, authority_resolver=runtime.resolve, executor=runtime.execute)
        result = run(recovery, saved)
        assert result.attempt.status == 'completed'
        assert len(runtime.calls) == len(runtime.resolutions) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize('state', ['missing', 'changed'])
def test_prepared_missing_or_changed_file_never_reexecutes(app, monkeypatch, state):
    attempt, runtime, service = admitted(app)
    with monkeypatch.context() as patch:
        patch.setattr(service, '_complete', lambda *a: (_ for _ in ()).throw(StorageError('interrupted')))
        with pytest.raises(StorageError):
            run(service, attempt)
    path = app[0].data_dir / attempt.provider_execution.output_ref
    path.unlink() if state == 'missing' else path.write_bytes(b'changed')
    result = run(service, attempt)
    assert result.attempt.status == 'needs_confirmation'
    assert len(runtime.calls) == len(runtime.resolutions) == 1


def test_preexisting_unrecorded_output_blocks_before_provider(app):
    attempt, runtime, service = admitted(app)
    path = app[0].data_dir / attempt.provider_execution.output_ref
    path.parent.mkdir(parents=True)
    path.write_bytes(b'keep existing bytes')
    result = run(service, attempt)
    assert result.attempt.status == 'needs_confirmation'
    assert result.attempt.error_code == 'provider_sync_artifact_unrecorded'
    assert not runtime.calls and path.read_bytes() == b'keep existing bytes'


def test_prepared_commit_failure_does_not_publish_or_retry(app, monkeypatch):
    attempt, runtime, service = admitted(app)
    original = app[0]._commit
    def fail_prepared(projects):
        if projects['p'].generation_attempts[0].provider_execution.prepared_artifact is not None:
            raise StorageError('prepared commit failed')
        return original(projects)
    with monkeypatch.context() as patch:
        patch.setattr(app[0], '_commit', fail_prepared)
        with pytest.raises(StorageError):
            run(service, attempt)
    assert not (app[0].data_dir / attempt.provider_execution.output_ref).exists()
    result = run(service, attempt)
    assert result.attempt.status == 'needs_confirmation' and len(runtime.calls) == 1


@pytest.mark.parametrize('change', ['hash', 'size', 'kind', 'symlink', 'outside'])
def test_invalid_private_executor_result_never_publishes(app, tmp_path, change):
    attempt, runtime, service = admitted(app)
    def mutate(result):
        if change == 'hash':
            result.sha256 = '0' * 64
        elif change == 'size':
            result.size_bytes += 1
        elif change == 'kind':
            result.media_kind = 'audio'
        elif change == 'symlink':
            link = result.path.with_name('link.png')
            link.symlink_to(result.path)
            result.path = link
        else:
            path = tmp_path / 'outside.png'
            path.write_bytes(result.path.read_bytes())
            result.path = path
    runtime.result_change = mutate
    result = run(service, attempt)
    assert result.attempt.status == 'needs_confirmation'
    assert not (app[0].data_dir / attempt.provider_execution.output_ref).exists()
    assert len(runtime.calls) == 1


def test_concurrent_services_cannot_execute_same_attempt_twice(app):
    attempt, runtime, service = admitted(app)
    runtime.entered, runtime.release = threading.Event(), threading.Event()
    second = AtelierMediaGenerationExecutionService(app[0], authority_resolver=runtime.resolve, executor=runtime.execute)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(run, service, attempt)
        assert runtime.entered.wait(5)
        observed = run(second, attempt)
        assert observed.attempt.status == 'needs_confirmation'
        runtime.release.set()
        # The second service may have advanced the revision; a stale writer must
        # fail closed, preserving the original submitting record without retry.
        try:
            future.result(timeout=5)
        except StorageConflictError:
            pass
    assert len(runtime.calls) == 1


def test_cancel_requires_original_identity_and_accepted_phase(app):
    attempt, runtime, service = admitted(app)
    with pytest.raises(StorageConflictError):
        service.cancel_attempt('p', attempt.attempt_id, candidate_id='wrong', expected_attempt_revision=0, execution_fingerprint=attempt.provider_execution.fingerprint)
    result = service.cancel_attempt('p', attempt.attempt_id, candidate_id=attempt.candidate_id, expected_attempt_revision=0, execution_fingerprint=attempt.provider_execution.fingerprint)
    assert result.attempt.status == 'cancelled' and not runtime.calls
    assert run(service, attempt).attempt == result.attempt


def test_stop_prevents_new_execution_and_join_waits_for_current(app):
    attempt, runtime, service = admitted(app)
    runtime.entered, runtime.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run, service, attempt)
        assert runtime.entered.wait(5)
        service.request_stop()
        assert not service.join(timeout=0.001)
        with pytest.raises(StorageConflictError):
            run(service, attempt)
        runtime.release.set()
        assert future.result(timeout=5).attempt.status == 'completed'
    assert service.join(timeout=0.1)


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_default_core_executor_integrates_with_real_admission_and_publication(app, monkeypatch, kind):
    """Only provider transport is fake; Core reconstruction and codecs are real."""
    import wave
    from src.models import provider_media_execution as core
    from src.utils.media_probe import probe_image_file, probe_media_file

    attempt, runtime, _ = admitted(app, kind)
    calls = []

    class ImageModel:
        def __init__(self, config, *, env):
            self.output_root = Path(config['output_root'])
            assert self.output_root.name.startswith('lumenx-sync-media-')
            assert env == {}

        def generate_with_bound_http(self, prompt, output, credential, **kwargs):
            assert Path(output).parent == self.output_root
            assert credential.secret == 'fake-private-secret'
            assert kwargs['image_contract_version'] == 'provider-qwen20-t2i-bound-http-v1'
            assert kwargs['model_name'] == 'qwen-image-2.0'
            assert kwargs['watermark'] is False and kwargs['prompt_extend'] is True
            calls.append(('image', prompt, kwargs))
            assert app[0].get_project('p').generation_attempts[0].provider_execution.phase == 'submitting'
            Path(output).write_bytes(rgb_png_bytes('default-core'))

    class SpeechModel:
        def __init__(self, **kwargs):
            assert kwargs['api_key'] == 'fake-private-secret'

        def execute(self, prepared, output):
            calls.append(('audio', prepared.to_snapshot()))
            assert app[0].get_project('p').generation_attempts[0].provider_execution.phase == 'submitting'
            with wave.open(str(output), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b'\x00\x00' * 1600)

    monkeypatch.setattr(core, 'WanxImageModel', ImageModel)
    monkeypatch.setattr(core, 'TTSProcessor', SpeechModel)
    service = AtelierMediaGenerationExecutionService(app[0], authority_resolver=runtime.resolve)
    result = run(service, attempt)
    assert result.attempt.status == 'completed', result.attempt.error_code
    output = app[0].data_dir / result.attempt.output_ref
    metadata = probe_image_file(output) if kind == 'image' else probe_media_file(output, 'audio')
    assert metadata.kind == kind
    assert len(calls) == 1
    assert run(service, attempt).attempt == result.attempt and len(calls) == 1


@pytest.mark.parametrize('kind', ['image', 'audio'])
@pytest.mark.parametrize('failure', ['none', 'before_write', 'visible_uncertain', 'cancelled', 'publication', 'completion'])
def test_verified_core_result_retained_until_prepared_and_final_are_durable(app, monkeypatch, kind, failure):
    """Real codecs, proof, publication and JSON store; only model output is fake."""
    import asyncio
    import json
    import wave
    from src.apps.atelier import storage as storage_module
    from src.models import provider_media_execution as core
    from src.utils import media_versions
    from src.utils.media_probe import probe_image_file, probe_media_file

    store, _ = app
    attempt, runtime, _ = admitted(app, kind)
    calls, results, checked_proofs = [], [], []
    original_write = storage_module.atomic_write_json
    original_link = media_versions.os.link
    fault = asyncio.CancelledError('prepared interrupted') if failure == 'cancelled' else StorageError(
        'prepared persistence or publication interrupted', commit_uncertain=failure == 'visible_uncertain')

    class ImageModel:
        def __init__(self, config, *, env):
            self.output_root = Path(config['output_root'])
            assert self.output_root.name.startswith('lumenx-sync-media-')
            assert env == {}

        def generate_with_bound_http(self, prompt, output, credential, **kwargs):
            assert Path(output).parent == self.output_root
            assert credential.secret == 'fake-private-secret'
            assert kwargs['image_contract_version'] == 'provider-qwen20-t2i-bound-http-v1'
            assert kwargs['model_name'] == 'qwen-image-2.0'
            assert kwargs['watermark'] is False and kwargs['prompt_extend'] is True
            calls.append('image')
            Path(output).write_bytes(rgb_png_bytes('retained-core-image'))

    class SpeechModel:
        def __init__(self, **kwargs):
            assert kwargs['api_key'] == 'fake-private-secret'

        def execute(self, prepared, output):
            calls.append('audio')
            with wave.open(str(output), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b'\x00\x00' * 1600)

    def execute(*args, **kwargs):
        result = core.execute_sync_artifact_to_private_file(*args, **kwargs)
        metadata = probe_image_file(result.path) if kind == 'image' else probe_media_file(result.path, 'audio')
        assert metadata.kind == kind
        assert hashlib.sha256(result.path.read_bytes()).hexdigest() == result.sha256
        results.append(result)
        return result

    def write(path, payload):
        proof = payload['p']['generation_attempts'][0]['provider_execution']['prepared_artifact']
        if proof is not None and failure in {'before_write', 'visible_uncertain', 'cancelled'}:
            result = results[0]
            assert result.path.is_file() and result.path.stat().st_size == proof['size_bytes']
            assert hashlib.sha256(result.path.read_bytes()).hexdigest() == proof['sha256']
            checked_proofs.append(proof)
            if failure == 'visible_uncertain':
                original_write(path, payload)
            raise fault
        return original_write(path, payload)

    def link(*args, **kwargs):
        if failure == 'publication':
            raise fault
        return original_link(*args, **kwargs)

    monkeypatch.setattr(core, 'WanxImageModel', ImageModel)
    monkeypatch.setattr(core, 'TTSProcessor', SpeechModel)
    service = AtelierMediaGenerationExecutionService(store, authority_resolver=runtime.resolve, executor=execute)
    with monkeypatch.context() as patch:
        patch.setattr(storage_module, 'atomic_write_json', write)
        patch.setattr(media_versions.os, 'link', link)
        if failure == 'completion':
            patch.setattr(service, '_complete', lambda *_args: (_ for _ in ()).throw(fault))
        if failure == 'none':
            assert run(service, attempt).attempt.status == 'completed'
        else:
            with pytest.raises(type(fault)) as raised:
                run(service, attempt)
            assert raised.value is fault

    assert calls == [kind] and len(results) == 1
    result = results[0]
    final = store.data_dir / attempt.provider_execution.output_ref
    durable = json.loads(store.path.read_text())['p']['generation_attempts'][0]['provider_execution']
    should_retain = failure not in {'none', 'completion'}
    if failure in {'before_write', 'visible_uncertain', 'cancelled'}:
        assert len(checked_proofs) == 1
    if should_retain:
        assert not final.exists()
        assert result.path.is_file(), 'Verified private result must survive until proof plus final bytes are durable'
        assert hashlib.sha256(result.path.read_bytes()).hexdigest() == result.sha256
        assert result.path.stat().st_size == result.size_bytes
        assert list(result.path.parent.iterdir()) == [result.path]
    else:
        assert final.is_file() and durable['prepared_artifact'] is not None
        assert not result.path.exists() and not result.path.parent.exists()

    store.close()
    reopened = AtelierStorage(store.data_dir)
    try:
        recovery = AtelierMediaGenerationExecutionService(reopened, authority_resolver=runtime.resolve, executor=execute)
        resumed = run(recovery, attempt)
        assert resumed.attempt.status == ('needs_confirmation' if should_retain else 'completed')
        assert calls == [kind] and len(runtime.resolutions) == 1
        if should_retain:
            assert resumed.attempt.output_ref is None and result.path.is_file()
    finally:
        reopened.close()


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_unverified_private_result_is_not_retained(app, monkeypatch, kind):
    attempt, runtime, service = admitted(app, kind)
    paths = []
    def invalid(result):
        paths.append(result.path)
        result.sha256 = '0' * 64
    monkeypatch.setattr(runtime, 'result_change', invalid)
    result = run(service, attempt)
    assert result.attempt.status == 'needs_confirmation'
    assert len(paths) == 1 and not paths[0].exists() and not paths[0].parent.exists()
