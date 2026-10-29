"""Regression tests for Playground video execution restart boundaries."""

import hashlib
import threading

import pytest

from src.apps.playground.models import PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode, PlaygroundOutput
from src.apps.playground.provider_video_execution import PlaygroundProviderVideoExecutionService
from src.apps.playground.recovery_dispatcher import PlaygroundVideoRecoveryDispatcher
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import build_accepted_provider_submission_and_record, record_with_changes
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderArtifact, ProviderHandle, ProviderObservation, ProviderQueryError, ProviderScope, ProviderFetchError, ProviderTaskStatus, QueryCapability
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import StorageConflictError


SECRET = "fake-secret"
SCOPE = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")


class FakeProvider:
    def __init__(self):
        self.submits = []
        self.queries = []
        self.fetches = []
        self.next_task_id = "remote-1"
        self.query_status = ProviderTaskStatus.SUCCEEDED
        self.fetch_bytes = b"playground batch provider bytes"

    def submit(self, request, *, credential, on_submitted):
        self.submits.append(request)
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id,
            task_id=self.next_task_id,
            scope=request.scope,
            credential_ref=request.credential_ref,
            request_id=f"request-{self.next_task_id}",
            query_capability=QueryCapability.BY_TASK_ID,
            submitted_at=123.0,
        )
        on_submitted(handle)
        return handle

    def query(self, handle, *, credential):
        self.queries.append(handle)
        return ProviderObservation(
            task_id=handle.task_id,
            status=self.query_status,
            raw_status=self.query_status.value,
            request_id=f"query-{handle.task_id}",
            video_url="https://provider.example/video.mp4" if self.query_status is ProviderTaskStatus.SUCCEEDED else None,
            source_handle=handle,
        )

    def fetch(self, handle, observation, *, output_root, output_ref, writer_lock, storage_guard, max_bytes, on_prepared=None, publish_policy="replace"):
        self.fetches.append((handle, output_ref, publish_policy))
        digest = hashlib.sha256(self.fetch_bytes).hexdigest()
        artifact = ProviderArtifact(handle, output_ref, digest, len(self.fetch_bytes))
        if on_prepared is not None:
            on_prepared(artifact)
        target = output_root / output_ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.fetch_bytes)
        return artifact


def _intent():
    return GenerationIntent(
        model_id="wan/wan2.7-video#t2v",
        catalog_mode="t2v",
        mode="t2v",
        prompt="A calm ocean at sunrise",
        params={"duration": 5, "resolution": "720P", "ratio": "16:9", "prompt_extend": False, "watermark": False},
        sources={"model_id": "request", "mode": "request", "prompt": "request"},
        request_fields=("mode", "model_id", "prompt"),
    )


def _record(credentials, attempt_id):
    credential = credentials.bind(SCOPE, SECRET)
    _, record = build_accepted_provider_submission_and_record(
        _intent(), provider_model_id="wan2.7-t2v", media=(), scope=SCOPE,
        credential_ref=credential.credential_ref, attempt_id=attempt_id,
    )
    return record


def _generation(*records, status="pending"):
    return PlaygroundGeneration(
        schema_version=1,
        id="generation-1",
        client_request_id="client-generation-1",
        request_contract_version=None,
        request_fingerprint="fingerprint-generation-1",
        mode=PlaygroundMode.T2V,
        model_id="wan/wan2.7-video#t2v",
        prompt="A calm ocean at sunrise",
        negative_prompt=None,
        parameters={"duration": 5, "resolution": "720P", "ratio": "16:9"},
        batch_size=len(records),
        status=status,
        attempts=[
            PlaygroundAttempt(
                attempt_id=record.attempt_id,
                ordinal=index,
                status="pending" if status == "pending" else "processing",
                error=None,
                provider_execution=record.model_dump(mode="json"),
            )
            for index, record in enumerate(records)
        ],
        error=None,
        created_at="2026-10-13T00:00:00+00:00",
    )


@pytest.fixture
def app(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    provider = FakeProvider()
    runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
    service = PlaygroundProviderVideoExecutionService(
        storage,
        runtime_provider=lambda scope: runtime if scope == SCOPE else None,
        credential_store=credentials,
    )
    yield service, storage, credentials, provider, tmp_path
    storage.close()


def test_batch_two_accepted_attempts_can_all_submit_query_and_fetch_in_one_process(app):
    service, storage, credentials, provider, _ = app
    first = _record(credentials, "attempt-1")
    second = _record(credentials, "attempt-2")
    storage.add_generation(_generation(first, second))

    provider.next_task_id = "remote-1"
    service.run_attempt("generation-1", "attempt-1")
    provider.next_task_id = "remote-2"
    service.run_attempt("generation-1", "attempt-2")
    assert len(provider.submits) == 2

    service.run_attempt("generation-1", "attempt-1")
    service.run_attempt("generation-1", "attempt-2")
    service.run_attempt("generation-1", "attempt-1")
    completed = service.run_attempt("generation-1", "attempt-2")

    assert completed.generation.status == "completed"
    assert [attempt.status for attempt in completed.generation.attempts] == ["completed", "completed"]
    assert {output.attempt_id for output in completed.generation.outputs} == {"attempt-1", "attempt-2"}
    assert len(provider.fetches) == 2



def test_claimed_batch_member_submit_permit_does_not_require_boolean(app):
    service, storage, credentials, provider, _ = app
    first = _record(credentials, "attempt-1")
    second = _record(credentials, "attempt-2")
    storage.add_generation(_generation(first, second))

    provider.next_task_id = "remote-1"
    service.run_attempt("generation-1", "attempt-1")
    provider.next_task_id = "remote-2"
    second_ack = service.run_attempt("generation-1", "attempt-2")

    assert len(provider.submits) == 2
    assert second_ack.attempt.status == "processing"
    assert ProviderExecutionRecord.model_validate(second_ack.attempt.provider_execution).phase == "submitted"


def test_restart_processing_accepted_marks_confirmation_without_submit_when_batch_has_output(app):
    service, storage, credentials, provider, _ = app
    first = _record(credentials, "attempt-1")
    second = _record(credentials, "attempt-2")
    gen = _generation(first, second, status="processing")
    gen.outputs.append(
        PlaygroundOutput(
            id="output-attempt-1",
            media_path="playground/videos/already-done.mp4",
            media_type="video",
            thumbnail_path=None,
            saved_to_library=False,
            attempt_id="attempt-1",
        )
    )
    gen.attempts[0].status = "completed"
    gen.attempts[1].status = "processing"
    storage.add_generation(gen)

    ack = service.run_attempt("generation-1", "attempt-2")

    assert provider.submits == []
    assert ack.attempt.status == "needs_confirmation"
    assert ack.attempt.error == "provider_accepted_after_restart"
    assert ack.generation.status == "needs_confirmation"
    assert ack.generation.outputs[0].attempt_id == "attempt-1"


def test_service_can_share_app_owned_video_executor(app):
    executor, storage, credentials, _provider, tmp_path = app
    service = PlaygroundService(
        storage,
        data_dir=tmp_path,
        video_scope_runtime_provider=lambda _scope: None,
        provider_credential_store=credentials,
        video_execution_service=executor,
    )

    assert service.video_execution_service() is executor



def test_storage_attempt_cas_rejects_stale_provider_phase_without_touching_sibling(app):
    _service, storage, credentials, _provider, _ = app
    first = _record(credentials, "attempt-1")
    second = _record(credentials, "attempt-2")
    storage.add_generation(_generation(first, second, status="processing"))

    def advance_first(gen, attempt):
        attempt.provider_execution = record_with_changes(first, phase="submitting").model_dump(mode="json")

    storage.update_generation_attempt(
        "generation-1",
        "attempt-1",
        advance_first,
        require_processing=True,
        expected_status="processing",
        expected_provider_execution=first.model_dump(mode="json"),
    )

    def stale_first(gen, attempt):
        attempt.provider_execution = record_with_changes(first, phase="submitted").model_dump(mode="json")

    with pytest.raises(StorageConflictError):
        storage.update_generation_attempt(
            "generation-1",
            "attempt-1",
            stale_first,
            require_processing=True,
            expected_status="processing",
            expected_provider_execution=first.model_dump(mode="json"),
        )

    saved = storage.get_generation("generation-1")
    records = [ProviderExecutionRecord.model_validate(attempt.provider_execution) for attempt in saved.attempts]
    assert [record.phase for record in records] == ["submitting", "accepted"]
    assert records[1].fingerprint == second.fingerprint


def test_completed_proof_missing_file_marks_confirmation_without_runtime(app):
    _service, storage, credentials, _provider, _ = app
    record = _record(credentials, "attempt-1")
    handle = ProviderHandle(
        provider_model_id=record.provider_model_id,
        task_id="remote-missing-proof",
        scope=SCOPE,
        credential_ref=record.credential_ref,
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=123.0,
    )
    output_ref = "playground/videos/playground-generation-1-0.mp4"
    proof = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": handle.to_dict(),
        "relative_path": output_ref,
        "sha256": "0" * 64,
        "size_bytes": 12,
    }
    durable = record_with_changes(record, phase="completed", handle=handle.to_dict(), artifact=proof)
    gen = _generation(durable, status="processing")
    storage.add_generation(gen)
    service = PlaygroundProviderVideoExecutionService(
        storage,
        runtime_provider=lambda _scope: (_ for _ in ()).throw(AssertionError("missing proof repair must not resolve runtime")),
        credential_store=credentials,
    )

    ack = service.run_attempt("generation-1", "attempt-1")

    assert ack.attempt.status == "needs_confirmation"
    assert ack.attempt.error == "provider_fetch_artifact_missing"
    assert ack.generation.outputs == []


def test_ready_prepared_proof_repairs_without_runtime_or_credential_resolution(app, tmp_path):
    _service, storage, credentials, _provider, _ = app
    record = _record(credentials, "attempt-1")
    handle = ProviderHandle(
        provider_model_id=record.provider_model_id,
        task_id="remote-proof",
        scope=SCOPE,
        credential_ref=record.credential_ref,
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=123.0,
    )
    observation = ProviderObservation(
        task_id=handle.task_id,
        status=ProviderTaskStatus.SUCCEEDED,
        raw_status="succeeded",
        request_id="query-proof",
        video_url="https://provider.example/video.mp4",
        source_handle=handle,
    )
    output_ref = "playground/videos/playground-generation-1-0.mp4"
    payload = b"already published provider bytes"
    target = tmp_path / output_ref
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    proof = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": handle.to_dict(),
        "relative_path": output_ref,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    durable = record_with_changes(record, phase="observed", handle=handle.to_dict(), observation=observation.to_dict(), prepared_artifact=proof)
    storage.add_generation(_generation(durable))
    service = PlaygroundProviderVideoExecutionService(
        storage,
        runtime_provider=lambda _scope: (_ for _ in ()).throw(AssertionError("local proof repair must not resolve runtime")),
        credential_store=credentials,
    )

    repaired = service.run_attempt("generation-1", "attempt-1")

    assert repaired.attempt.status == "completed"
    assert repaired.generation.outputs[0].media_path == output_ref


def test_two_executors_interleaved_member_submit_callbacks_preserve_sibling_handle(app):
    _service, storage, credentials, _provider, _ = app
    first = _record(credentials, "attempt-1")
    second = _record(credentials, "attempt-2")
    storage.add_generation(_generation(first, second))
    first_started = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()

    class InterleavedProvider(FakeProvider):
        def __init__(self, task_id):
            super().__init__()
            self.next_task_id = task_id

        def submit(self, request, *, credential, on_submitted):
            if self.next_task_id == "remote-1":
                first_started.set()
                assert release_first.wait(3), "test did not release first submit"
            return super().submit(request, credential=credential, on_submitted=on_submitted)

    first_provider = InterleavedProvider("remote-1")
    second_provider = InterleavedProvider("remote-2")
    first_runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=first_provider, max_bytes=1024 * 1024)
    second_runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=second_provider, max_bytes=1024 * 1024)
    first_executor = PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: first_runtime, credential_store=credentials)
    second_executor = PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: second_runtime, credential_store=credentials)

    def run_first():
        first_executor.run_attempt("generation-1", "attempt-1")

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert first_started.wait(3), "first submit was not reached"
    second_executor.run_attempt("generation-1", "attempt-2")
    second_done.set()
    release_first.set()
    first_thread.join(3)
    assert not first_thread.is_alive()
    assert second_done.is_set()

    saved = storage.get_generation("generation-1")
    records = {attempt.attempt_id: ProviderExecutionRecord.model_validate(attempt.provider_execution) for attempt in saved.attempts}
    assert records["attempt-1"].phase == "submitted"
    assert records["attempt-1"].handle is not None
    assert records["attempt-1"].handle["task_id"] == "remote-1"
    assert records["attempt-2"].phase == "submitted"
    assert records["attempt-2"].handle is not None
    assert records["attempt-2"].handle["task_id"] == "remote-2"
    assert len(first_provider.submits) == 1
    assert len(second_provider.submits) == 1


def test_same_attempt_two_executors_do_not_double_post_or_overwrite_saved_handle(app):
    _service, storage, credentials, _provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    first_started = threading.Event()
    release_first = threading.Event()
    outcomes = []

    class SlowProvider(FakeProvider):
        def submit(self, request, *, credential, on_submitted):
            first_started.set()
            assert release_first.wait(3), "test did not release first submit"
            return super().submit(request, credential=credential, on_submitted=on_submitted)

    first_provider = SlowProvider()
    first_provider.next_task_id = "remote-primary"
    second_provider = FakeProvider()
    second_provider.next_task_id = "remote-duplicate"
    first_runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=first_provider, max_bytes=1024 * 1024)
    second_runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=second_provider, max_bytes=1024 * 1024)
    first_executor = PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: first_runtime, credential_store=credentials)
    second_executor = PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: second_runtime, credential_store=credentials)

    def run_first():
        outcomes.append(first_executor.run_attempt("generation-1", "attempt-1"))

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert first_started.wait(3), "first submit was not reached"
    with pytest.raises(StorageConflictError):
        second_executor.run_attempt("generation-1", "attempt-1")
    release_first.set()
    first_thread.join(3)
    assert not first_thread.is_alive()

    saved = storage.get_generation("generation-1")
    saved_record = ProviderExecutionRecord.model_validate(saved.attempts[0].provider_execution)
    assert saved_record.phase == "submitted"
    assert saved_record.handle is not None
    assert saved_record.handle["task_id"] == "remote-primary"
    assert len(first_provider.submits) == 1
    assert second_provider.submits == []


def test_provider_callback_cas_rejects_deleted_generation_without_output(app):
    _service, storage, credentials, _provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))

    class DeletingProvider(FakeProvider):
        def submit(self, request, *, credential, on_submitted):
            self.submits.append(request)
            handle = ProviderHandle(
                provider_model_id=request.provider_model_id,
                task_id="remote-deleted",
                scope=request.scope,
                credential_ref=request.credential_ref,
                request_id="request-remote-deleted",
                query_capability=QueryCapability.BY_TASK_ID,
                submitted_at=123.0,
            )
            assert storage.delete_generation("generation-1") is not None
            on_submitted(handle)
            return handle

    provider = DeletingProvider()
    runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
    service = PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: runtime, credential_store=credentials)

    with pytest.raises(ValueError, match="playground_generation_not_found"):
        service.run_attempt("generation-1", "attempt-1")

    assert len(provider.submits) == 1
    assert storage.get_generation("generation-1") is None

def test_runtime_missing_marks_confirmation_without_dispatcher_spin(app):
    _service, storage, credentials, _provider, _ = app
    record = _record(credentials, "attempt-1")
    handle = ProviderHandle(
        provider_model_id=record.provider_model_id,
        task_id="remote-runtime-missing",
        scope=SCOPE,
        credential_ref=record.credential_ref,
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=123.0,
    )
    durable = record_with_changes(record, phase="submitted", handle=handle.to_dict())
    storage.add_generation(_generation(durable, status="processing"))
    service = PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: None, credential_store=credentials)

    ack = service.run_attempt("generation-1", "attempt-1")

    assert ack.attempt.status == "needs_confirmation"
    assert ack.attempt.error == "provider_recovery_runtime_missing"
    assert ProviderExecutionRecord.model_validate(ack.attempt.provider_execution).phase == "submitted"

def test_dispatcher_retries_transient_query_to_completion_without_repost(app):
    service, storage, credentials, provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    service.run_attempt("generation-1", "attempt-1")
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_error = Exception("placeholder")

    def raise_query_once(handle, *, credential):
        provider.queries.append(handle)
        raise ProviderQueryError("timeout")

    original_query = provider.query
    provider.query = raise_query_once
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service, transient_backoff_seconds=0)

    assert dispatcher.run_once() == ("query", "generation-1", "attempt-1")
    failed = storage.get_generation("generation-1")
    assert failed.status == "processing"
    assert failed.attempts[0].status == "needs_confirmation"
    assert failed.attempts[0].error == "provider_query_unavailable"

    provider.query = original_query
    assert dispatcher.run_once() == ("query", "generation-1", "attempt-1")
    assert dispatcher.run_once() == ("fetch", "generation-1", "attempt-1")
    completed = storage.get_generation("generation-1")
    assert completed.status == "completed"
    assert completed.outputs[0].attempt_id == "attempt-1"
    assert len(provider.submits) == 1


def test_dispatcher_retries_transient_fetch_to_completion_without_repost(app):
    service, storage, credentials, provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    service.run_attempt("generation-1", "attempt-1")
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    service.run_attempt("generation-1", "attempt-1")

    original_fetch = provider.fetch
    failed = {"done": False}

    def fail_fetch_once(*args, **kwargs):
        if not failed["done"]:
            failed["done"] = True
            raise ProviderFetchError("temporary download failure")
        return original_fetch(*args, **kwargs)

    provider.fetch = fail_fetch_once
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service, transient_backoff_seconds=0)

    assert dispatcher.run_once() == ("fetch", "generation-1", "attempt-1")
    transient = storage.get_generation("generation-1")
    assert transient.status == "processing"
    assert transient.attempts[0].status == "needs_confirmation"
    assert transient.attempts[0].error == "provider_fetch_unavailable"

    assert dispatcher.run_once() == ("fetch", "generation-1", "attempt-1")
    completed = storage.get_generation("generation-1")
    assert completed.status == "completed"
    assert completed.outputs[0].attempt_id == "attempt-1"
    assert len(provider.submits) == 1

def test_dispatcher_marks_old_core_video_generation_without_attempts_confirmation_preserving_outputs(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    gen = PlaygroundGeneration(
        schema_version=1,
        id="generation-no-attempts",
        client_request_id="client-no-attempts",
        request_contract_version=None,
        request_fingerprint="fingerprint-no-attempts",
        mode=PlaygroundMode.T2V,
        model_id="wan/wan2.7-video#t2v",
        prompt="A calm ocean at sunrise",
        negative_prompt=None,
        parameters={},
        batch_size=1,
        status="processing",
        attempts=[],
        outputs=[PlaygroundOutput(
            id="out-old",
            media_path="playground/videos/old.mp4",
            media_type="video",
            thumbnail_path=None,
            saved_to_library=False,
            attempt_id=None,
        )],
        error=None,
        created_at="2026-10-13T00:00:00+00:00",
    )
    storage.add_generation(gen)
    service = PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: None, credential_store=ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock))
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.run_once() == ("confirm_generation", "generation-no-attempts", "")
    saved = storage.get_generation("generation-no-attempts")
    assert saved is not None
    assert saved.status == "needs_confirmation"
    assert saved.error == "playground_core_video_record_missing"
    assert saved.outputs[0].media_path == "playground/videos/old.mp4"
    storage.close()



def test_first_submit_without_runtime_is_visible_confirmation_and_zero_post(app):
    _service, storage, credentials, provider, _ = app
    storage.add_generation(_generation(_record(credentials, "attempt-1")))
    executor = PlaygroundProviderVideoExecutionService(
        storage, runtime_provider=lambda _scope: None, credential_store=credentials,
    )
    ack = executor.run_attempt("generation-1", "attempt-1")
    assert ack.attempt.status == "needs_confirmation"
    assert ack.attempt.error == "provider_recovery_runtime_missing"
    assert provider.submits == []
