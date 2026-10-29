"""Optional OSS copies proven local bytes without becoming authoritative media."""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import asset_image_execution as module
from src.utils import media_versions, oss_utils
from src.utils.json_storage import StorageError
from tests import test_oss_environment as oss
from tests import test_studio_scene_prop_service as scene

runtime = scene.runtime
bound_transport = scene.bound_transport
sdk = oss.sdk


def configure(item, **extra):
    assert item.runtime_environment is not None
    item.runtime_environment.update(oss.environment('owned', **extra), persist=lambda *_: None)


def completed(item, kind, transport, *, count=1, selected=False):
    root, target = scene.setup(item, kind, selected=selected)
    scene.queue_success(transport, count)
    svc = item.asset_image_service
    ack = svc.admit(*root, scene.command(kind, batch_size=count))
    return root, target, svc, ack


@pytest.mark.parametrize('kind', scene.KINDS)
def test_real_worker_mirrors_each_proven_member_without_changing_canonical_history(runtime, bound_transport, sdk, monkeypatch, kind):
    configure(runtime)
    root, target, svc, ack = completed(runtime, kind, bound_transport, count=2, selected=True)
    uploader = runtime.oss_uploader()
    bucket = uploader.bucket
    snapshots = []
    def put(key, stream):
        # A concurrent reader acquires the save lock while OSS waits.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            snapshot = pool.submit(svc.read_request, *root, ack.request.client_request_id).result(timeout=2)
        payload = stream.read()
        snapshots.append(snapshot.request.model_dump(mode='json'))
        bucket.uploads.append((key, payload))
        return SimpleNamespace(status=200)
    monkeypatch.setattr(bucket, 'put_object', put)
    result = svc.run_original_request(ack.request.id)
    assert result is not None and svc.request_state(result.request)['status'] == 'completed'
    assert len(bucket.uploads) == len(snapshots) == scene.post_count(bound_transport) == 2
    for index, member in enumerate(result.request.members):
        execution = member.execution
        proof = execution.artifact
        assert proof is not None and proof == execution.prepared_artifact
        key, payload = bucket.uploads[index]
        assert key == f'owned-root/assets/{kind}s/{execution.attempt_id}.png'
        assert len(payload) == proof['size_bytes'] and hashlib.sha256(payload).hexdigest() == proof['sha256']
        assert payload == (runtime.data_dir / execution.output_ref).read_bytes()
        assert execution.output_ref == f'assets/studio/{execution.attempt_id}.png'
        assert snapshots[index]['members'][index]['execution']['phase'] == 'completed'
    assert target.image_asset is not None
    assert target.image_asset.selected_id == 'old' and target.image_url == 'uploads/old.png'
    assert target.image_asset.variants[-1].is_favorited
    assert [v.url for v in target.image_asset.variants[:-1]] == [m.execution.output_ref for m in reversed(result.request.members)]
    assert result.request.request_fingerprint == ack.request.request_fingerprint
    final = deepcopy(result.request.model_dump(mode='json'))
    monkeypatch.setattr(runtime, 'oss_uploader', Mock(side_effect=AssertionError('read/replay acquired OSS')))
    svc.read_request(*root, ack.request.client_request_id)
    svc.read_task_status(ack.request.id)
    assert svc.admit(*root, scene.command(kind, batch_size=2)).replayed
    assert svc.repair_request(*root, ack.request.client_request_id).request.model_dump(mode='json') == final
    with scene.worker.restarted(runtime) as other:
        monkeypatch.setattr(other, 'oss_uploader', Mock(side_effect=AssertionError('restart repair acquired OSS')))
        repaired = other.asset_image_service.repair_request(*root, ack.request.client_request_id)
        assert repaired.request.model_dump(mode='json') == final
    assert len(bucket.uploads) == scene.post_count(bound_transport) == 2


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('mode', ['disabled', 'unconfigured', 'non-200', 'sdk-error', 'wrapper-error'])
def test_optional_mirror_failure_or_disabled_state_preserves_all_original_members(runtime, bound_transport, sdk, monkeypatch, kind, mode):
    if mode != 'unconfigured':
        configure(runtime, OSS_ENABLE='false' if mode == 'disabled' else 'true')
    uploader = runtime.oss_uploader()
    if mode == 'non-200':
        monkeypatch.setattr(uploader.bucket, 'put_object', Mock(return_value=SimpleNamespace(status=503)))
    elif mode == 'sdk-error':
        monkeypatch.setattr(uploader.bucket, 'put_object', Mock(side_effect=RuntimeError('synthetic OSS unavailable')))
    elif mode == 'wrapper-error':
        monkeypatch.setattr(uploader, 'upload_bytes', Mock(side_effect=RuntimeError('synthetic wrapper unavailable')), raising=False)
    _, target, svc, ack = completed(runtime, kind, bound_transport, count=2)
    result = svc.run_original_request(ack.request.id)
    assert result is not None and all(m.execution.phase == 'completed' for m in result.request.members)
    assert svc.request_state(result.request)['status'] == 'completed'
    assert scene.post_count(bound_transport) == 2
    assert target.image_asset is not None and len(target.image_asset.variants) == 3
    if mode in {'disabled', 'unconfigured'}:
        assert all(bucket.uploads == [] for bucket in sdk)
    elif mode == 'wrapper-error':
        assert uploader.upload_bytes.call_count == 2
    else:
        assert uploader.bucket.put_object.call_count == 2


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('failure', ['projection', 'completion-save'])
def test_not_locally_completed_never_instantiates_oss(runtime, bound_transport, monkeypatch, kind, failure):
    root, target, svc, ack = completed(runtime, kind, bound_transport)
    assert target.image_asset is not None
    old_history = deepcopy(target.image_asset.variants)
    image_fields = target.image_asset.model_dump(exclude={'variants'})
    author_fields = (target.name, target.description, target.video_prompt, target.starred, target.image_url)
    incomplete_oss = Mock(side_effect=AssertionError('incomplete acquired OSS'))
    monkeypatch.setattr(runtime, 'oss_uploader', incomplete_oss)
    original = svc._complete_member
    checkpoints = []
    def fail(*args, **kwargs):
        checkpoints.append(args[1].model_dump(mode='json'))
        if failure == 'projection':
            target.description = 'changed before local projection'
            runtime._commit_storage('projects.json')
            return original(*args, **kwargs)
        raise StorageError('synthetic completion storage failure')
    monkeypatch.setattr(svc, '_complete_member', fail)
    if failure == 'completion-save':
        with pytest.raises(StorageError):
            svc.run_original_request(ack.request.id)
    else:
        result = svc.run_original_request(ack.request.id)
        assert result is not None and result.reason == 'studio_image_request_inputs_changed'
        assert result.request.members[0].execution.model_dump(mode='json') == checkpoints[0]
        assert target.description == 'changed before local projection'
    execution = scene.worker.disk_request(runtime, 'local')['members'][0]['execution']
    assert checkpoints == [execution]
    assert execution['schema_version'] == 3 and execution['phase'] == 'observed'
    assert execution['artifact'] is None
    handle = execution['handle']
    assert handle is not None and handle['task_id'] == scene.http.task()['output']['task_id']
    assert handle['scope'] == execution['scope']
    assert handle['query_contract'] == 'dashscope-wan27-image-task-v1'
    observation = execution['observation']
    assert observation is not None and observation['status'] == 'succeeded'
    assert observation['source_handle'] == handle
    proof = execution['prepared_artifact']
    data = (runtime.data_dir / execution['output_ref']).read_bytes()
    assert proof == {
        'schema_version': 3, 'contract_version': 'provider-async-image-artifact-v1',
        'attempt_id': execution['attempt_id'], 'handle': handle, 'media_kind': 'image',
        'relative_path': execution['output_ref'], 'output_ref': execution['output_ref'],
        'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data),
    }
    assert data and target.image_asset.variants == old_history
    assert target.image_asset.model_dump(exclude={'variants'}) == image_fields
    assert [request.method for request, _ in bound_transport['calls']] == ['POST', 'GET', 'GET']
    assert not bound_transport['queue']
    calls = [(request.method, request.url, request.body, dict(request.headers), dict(options))
             for request, options in bound_transport['calls']]
    incomplete_oss.assert_not_called()
    if failure == 'projection':
        target.description = 'kept description'
        runtime._commit_storage('projects.json')
        repair_oss = Mock(side_effect=AssertionError('proof repair acquired OSS'))
        monkeypatch.setattr(scene.admission.pipeline_module.ComicGenPipeline, 'oss_uploader', repair_oss)
        with scene.worker.restarted(runtime) as other:
            repaired = other.asset_image_service.repair_request(*root, ack.request.client_request_id)
            member = repaired.request.members[0]
            assert member.execution.phase == 'completed' and repaired.reason is None
            assert member.execution.artifact == member.execution.prepared_artifact == proof
            assert member.execution.handle == handle and member.execution.observation == observation
            assert member.execution.output_ref is not None
            assert (other.data_dir / member.execution.output_ref).read_bytes() == data
            restored = getattr(other.scripts['p'], kind + 's')[0]
            assert (restored.name, restored.description, restored.video_prompt, restored.starred, restored.image_url) == author_fields
            assert restored.image_asset is not None
            assert restored.image_asset.model_dump(exclude={'variants'}) == image_fields
            assert restored.image_asset.selected_id is None
            assert restored.image_asset.variants[:-1] == old_history
            assert [variant.id for variant in restored.image_asset.variants] == ['old', member.variant_id]
            assert restored.image_asset.variants[-1].url == member.execution.output_ref
            repair_oss.assert_not_called()
    assert [(request.method, request.url, request.body, dict(request.headers), dict(options))
            for request, options in bound_transport['calls']] == calls
    assert scene.post_count(bound_transport) == 1


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('when', ['factory', 'upload-return', 'upload-error', 'storage-error'])
def test_storage_or_close_during_mirror_stops_next_paid_slot_and_preserves_first_proof(runtime, bound_transport, sdk, monkeypatch, kind, when):
    configure(runtime)
    _, _, svc, ack = completed(runtime, kind, bound_transport, count=2)
    uploader = runtime.oss_uploader()
    if when == 'factory':
        def factory():
            runtime.close()
            return uploader
        monkeypatch.setattr(runtime, 'oss_uploader', factory)
    else:
        def upload(*args, **kwargs):
            if when == 'storage-error':
                raise StorageError('synthetic storage guard error')
            runtime.close()
            if when == 'upload-error':
                raise RuntimeError('synthetic network error at shutdown')
            return None
        monkeypatch.setattr(uploader, 'upload_bytes', upload, raising=False)
    with pytest.raises(StorageError):
        svc.run_original_request(ack.request.id)
    assert scene.post_count(bound_transport) == 1
    raw = scene.worker.disk_request(runtime, 'local')
    assert [m['execution']['phase'] for m in raw['members']] == ['completed', 'accepted']
    proof = raw['members'][0]['execution']['artifact']
    assert proof and (runtime.data_dir / proof['relative_path']).is_file()
    assert all(bucket.uploads == [] for bucket in sdk)


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('damage', ['replace-private', 'mutate-verified-stream'])
def test_mirror_rechecks_immutable_payload_after_private_file_verification(runtime, bound_transport, sdk, monkeypatch, kind, damage):
    configure(runtime)
    _, _, svc, ack = completed(runtime, kind, bound_transport)
    original = module.media_versions.verified_private_media_file
    calls = []
    @contextmanager
    def verify(path, **kwargs):
        calls.append(Path(path))
        if len(calls) == 2 and damage == 'replace-private':
            Path(path).write_bytes(b'x' * kwargs['expected_size'])
        with original(path, **kwargs) as stream:
            if len(calls) == 2 and damage == 'mutate-verified-stream':
                yield BytesIO(b'x' * kwargs['expected_size'])
            else:
                yield stream
    monkeypatch.setattr(module.media_versions, 'verified_private_media_file', verify)
    result = svc.run_original_request(ack.request.id)
    assert result is not None and result.request.members[0].execution.phase == 'completed'
    assert len(calls) == 2 and all(bucket.uploads == [] for bucket in sdk)
    assert scene.post_count(bound_transport) == 1
    proof = result.request.members[0].execution.artifact
    assert proof is not None
    assert hashlib.sha256((runtime.data_dir / proof['relative_path']).read_bytes()).hexdigest() == proof['sha256']


@pytest.mark.parametrize('kind', scene.KINDS)
def test_mirrored_candidate_remains_locally_freezable_with_old_oss_history(runtime, bound_transport, sdk, kind):
    configure(runtime)
    _, target = scene.setup(runtime, kind, selected=True)
    svc = runtime.asset_image_service
    assert target.image_asset is not None
    target.image_asset.variants[0].url = 'owned-root/assets/old.png'
    target.image_url = 'owned-root/assets/old.png'
    runtime._commit_storage('projects.json')
    ack = svc.admit('project', 'p', scene.command(kind, client_request_id='new-local', batch_size=2))
    scene.queue_success(bound_transport, 2)
    result = svc.run_original_request(ack.request.id)
    assert result is not None and target.image_asset.variants[-1].url == 'owned-root/assets/old.png'
    assert target.image_asset.selected_id == 'old' and target.image_asset.variants[-1].is_favorited
    member = result.request.members[0].execution
    version = media_versions.freeze_media(member.output_ref, output_root=runtime.data_dir,
        writer_lock=runtime._writer_lock, role='reference_image', ordinal=0, max_bytes=1024 * 1024)
    assert member.artifact is not None and version.sha256 == member.artifact['sha256']
    assert media_versions.read_verified_media(version, output_root=runtime.data_dir, max_bytes=1024 * 1024) == (runtime.data_dir / member.output_ref).read_bytes()
    uploader = runtime.oss_uploader()
    signed = oss_utils.sign_media_refs_in_response(target.model_dump(mode='json'), uploader=uploader)
    assert isinstance(signed, dict)
    assert signed['image_url'].startswith('https://owned.example.invalid/')
    assert target.image_url == 'owned-root/assets/old.png'
    assert all(v.url.startswith(('assets/studio/', 'owned-root/')) for v in target.image_asset.variants)


@pytest.mark.parametrize('data,limit', [(b'', 4), (b'four', 3), (bytearray(b'four'), 4), ('four', 4), (b'four', True), (b'four', 0)])
def test_byte_upload_validates_payload_and_size_without_sdk_call(sdk, data, limit):
    uploader = oss_utils.OSSImageUploader(env=oss.environment('owned'))
    with pytest.raises((TypeError, ValueError)):
        uploader.upload_bytes(data, custom_filename='image.png', max_bytes=limit)
    assert sdk[0].uploads == []


def test_byte_upload_uses_runtime_environment_and_preserves_private_signing_when_disabled(monkeypatch, sdk):
    first = oss_utils.OSSImageUploader(env=oss.environment('first'))
    second = oss_utils.OSSImageUploader(env=oss.environment('second', OSS_ENABLE='false'))
    for key, value in oss.environment('ambient').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('OSS_ENABLE', 'false')
    assert first.upload_bytes(b'owned bytes', sub_path='assets/scenes', custom_filename='a.png') == 'first-root/assets/scenes/a.png'
    assert second.upload_bytes(b'owned bytes', sub_path='assets/scenes', custom_filename='a.png') is None
    assert sdk[0].uploads == [('first-root/assets/scenes/a.png', b'owned bytes')]
    assert sdk[1].uploads == []
    assert second.generate_signed_url('second-root/assets/old.png').startswith('https://second.example.invalid/')


@pytest.mark.parametrize('kind', scene.KINDS)
def test_cancellation_during_mirror_propagates_and_leaves_later_members_unsubmitted(runtime, bound_transport, sdk, monkeypatch, kind):
    configure(runtime)
    _, _, svc, ack = completed(runtime, kind, bound_transport, count=2)
    monkeypatch.setattr(runtime.oss_uploader(), 'upload_bytes', Mock(side_effect=KeyboardInterrupt('synthetic cancellation')), raising=False)
    with pytest.raises(KeyboardInterrupt):
        svc.run_original_request(ack.request.id)
    assert scene.post_count(bound_transport) == 1
    raw = scene.worker.disk_request(runtime, 'local')
    assert [m['execution']['phase'] for m in raw['members']] == ['completed', 'accepted']


def test_two_pipeline_runtimes_mirror_only_to_their_owned_bucket(runtime, bound_transport, sdk, monkeypatch, tmp_path):
    from src.utils.runtime_environment import RuntimeEnvironment
    configure(runtime)
    other = scene.admission.pipeline_module.ComicGenPipeline(
        data_dir=tmp_path / 'other', script_processor=Mock(),
        runtime_environment=RuntimeEnvironment({
            **oss.environment('other'), 'DASHSCOPE_API_KEY': 'synthetic-example-image-admission',
        }),
    )
    other.scripts['p'] = runtime.scripts['p'].model_copy(deep=True)
    other.series_store['s'] = runtime.series_store['s'].model_copy(deep=True)
    try:
        for key, value in oss.environment('ambient').items():
            monkeypatch.setenv(key, value)
        records = []
        for item in (runtime, other):
            _, _, svc, ack = completed(item, 'scene', bound_transport)
            result = svc.run_original_request(ack.request.id)
            assert result is not None
            records.append(result.request.members[0].execution)
        assert len(sdk) == 2 and [b.name for b in sdk] == ['owned', 'other']
        for bucket, record, label in zip(sdk, records, ['owned', 'other']):
            assert bucket.uploads[0][0] == f'{label}-root/assets/scenes/{record.attempt_id}.png'
            assert record.artifact is not None
            assert hashlib.sha256(bucket.uploads[0][1]).hexdigest() == record.artifact['sha256']
        assert scene.post_count(bound_transport) == 2
    finally:
        other.close()


def test_byte_uploader_propagates_storage_failure_from_guarded_sdk(sdk, monkeypatch):
    uploader = oss_utils.OSSImageUploader(env=oss.environment('owned'))
    monkeypatch.setattr(uploader.bucket, 'put_object', Mock(side_effect=StorageError('synthetic storage guard')))
    with pytest.raises(StorageError):
        uploader.upload_bytes(b'owned bytes', custom_filename='a.png')
