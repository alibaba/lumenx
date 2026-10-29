"""Core image operations with external HTTP substitutes and real private media.

No Studio app/claim/ledger is replaced or implied. The publication controls
compose existing writer-lock/storage/proof helpers with real normalized bytes.
They are not provider-paid, crash, desktop or ECS evidence.
"""

from contextlib import contextmanager
from dataclasses import asdict, replace
import base64
import hashlib
import json
from threading import RLock
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from src.models import factory, provider_media_execution as execution
from src.models import provider_media_intents as intents, provider_recovery as recovery, provider_tasks as tasks
from src.models.gpt_image2_edit_contract import GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_runtime import MediaRuntimeAuthority
from src.models.wan27_image_contract import WAN27_IMAGE_CONTRACT_VERSION
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import DirectoryWriterLock, StorageConflictError, StorageError, atomic_write_json
from src.utils.media_versions import (
    MediaVersionError, freeze_media, store_media_stream, verified_private_media_file,
)
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan27_bound_image import response

LIMIT = 1024 * 1024
SYNTHETIC_IMAGE_CREDENTIAL = "synthetic-b2-original-secret"
TASK_ID = "b174971e-7708-4ffa-90f6-1d92131551cb"
IMAGE_URL = "https://images.example.invalid/b2-result.png?signature=synthetic"
OPERATIONS = ("wan", "gpt_generation", "gpt_edit")


@pytest.fixture
def owned(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    identity = object()
    state = SimpleNamespace(root=root, lease=DirectoryWriterLock(root).acquire(), lock=RLock(),
                            original=identity, current=identity, inside=0)

    @contextmanager
    def guard():
        with state.lock:
            state.lease.assert_owned()
            if state.current is not state.original:
                raise StorageConflictError("synthetic original owner replaced")
            state.inside += 1
            try:
                yield
            finally:
                state.inside -= 1

    state.guard = guard
    try:
        yield state
    finally:
        state.lease.close()


@pytest.fixture
def transport(monkeypatch, owned):
    state = {"queue": [], "calls": [], "responses": [], "on_send": None}

    def send(_session, prepared, **kwargs):
        assert owned.inside == 0, "HTTP must stay outside the storage guard"
        state["calls"].append((prepared, kwargs))
        if state["on_send"] is not None:
            state["on_send"](prepared)
        assert state["queue"], f"Unexpected HTTP operation: {prepared.method}"
        item = state["queue"].pop(0)
        if isinstance(item, BaseException):
            raise item
        item.request, item.url = prepared, prepared.url
        state["responses"].append(item)
        return item

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Image operation entered the video factory or ambient auth")

    monkeypatch.setattr(requests.sessions.Session, "send", send)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", forbidden)
    monkeypatch.setattr(factory.ModelFactory, "create_model", forbidden)
    monkeypatch.setenv("HTTPS_PROXY", "https://ambient.invalid:444")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/synthetic/missing-ca")
    return state


def context(owned, operation, *, attempt_id=None):
    wan = operation == "wan"
    scope = (tasks.ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com",
                                 region="cn-beijing", workspace_id="original-workspace") if wan else
             tasks.ProviderScope("mulerouter", "mulerouter", "mulerouter_http", "https://api.mulerouter.ai"))
    model = "wan2.7-image" if wan else "gpt-image-2"
    authority = MediaRuntimeAuthority(
        media_kind="image", provider_model_id=model, scope=scope,
        credential_ref="credential-b2-original", source_key="DASHSCOPE_API_KEY" if wan else "MULEROUTER_API_KEY",
        credential=tasks.ResolvedCredential(scope, "credential-b2-original", SYNTHETIC_IMAGE_CREDENTIAL),
    )
    value = GenerationIntent(
        model_id="wan/wan2.7-image#image" if wan else "gpt-image/gpt-image-2#image",
        catalog_mode="image", mode="i2i" if operation == "gpt_edit" else "t2i",
        prompt="Original frozen B2 author prompt",
        params={"size": "1024*1024", "watermark": False, "seed": 0} if wan else {"size": "1024x1536"},
        sources={"model_id": "request", "mode": "request", "prompt": "request", "params.size": "request"},
        request_fields=("model_id", "mode", "prompt", "params.size"),
    )
    source, version, original = None, None, None
    options = {"image_contract_version": WAN27_IMAGE_CONTRACT_VERSION} if wan else {}
    if operation == "gpt_edit":
        source = owned.root / "uploads" / "reference.png"
        source.parent.mkdir(exist_ok=True)
        original = rgb_png_bytes("original B2 reference", width=32, height=32)
        source.write_bytes(original)
        version = freeze_media("uploads/reference.png", output_root=owned.root, writer_lock=owned.lease,
                               role="reference_image", ordinal=0, max_bytes=LIMIT)
        options["image_contract_version"] = GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION
    accepted = intents.build_accepted_async_image_record(
        intent=value, provider_model_id=model, media=[asdict(version)] if version is not None else [],
        scope=scope, credential_ref=authority.credential_ref, attempt_id=attempt_id or "b2-" + operation,
        output_ref="assets/studio/b2-" + operation + ".png", **options,
    )
    return SimpleNamespace(operation=operation, record=recovery.record_with_changes(accepted, phase="submitting"),
                           accepted=accepted, authority=authority, source=source, version=version, original=original)


def submit_body(operation):
    return ({"request_id": "b2-request", "output": {"task_id": TASK_ID, "task_status": "PENDING"}}
            if operation == "wan" else {"task_info": {"id": TASK_ID, "status": "pending"}})


def query_body(operation, *, pending=False):
    if operation == "wan":
        output: dict[str, Any] = {"task_id": TASK_ID, "task_status": "RUNNING" if pending else "SUCCEEDED"}
        if not pending:
            output["choices"] = [{"message": {"role": "assistant", "content": [
                {"type": "image", "image": IMAGE_URL}]}}]
        return {"output": output}
    body: dict[str, Any] = {"task_info": {"id": TASK_ID, "status": "processing" if pending else "completed"}}
    if not pending:
        body["images"] = [IMAGE_URL]
    return body


def saved_submit(ctx, owned, tmp_path, transport):
    transport["queue"].append(response(submit_body(ctx.operation), status=200 if ctx.operation == "wan" else 202))
    path = owned.root / "execution.json"
    passed = []

    def save(handle):
        with owned.guard():
            assert not path.exists()
            submitted = recovery.record_with_changes(ctx.record, phase="submitted", handle=handle.to_dict())
            atomic_write_json(path, submitted.model_dump(mode="json"))
            passed.append(handle)

    returned = execution.submit_async_image(
        ctx.record, ctx.authority, output_root=owned.root, scratch_dir=tmp_path / "submit-scratch",
        max_bytes=LIMIT, on_submitted=save,
    )
    saved = ProviderExecutionRecord.model_validate_json(path.read_text())
    assert passed == [returned] and recovery.image_query_handle_from_record(saved) == returned
    return saved, returned


def observed(ctx, owned, tmp_path, transport, *, pending=False):
    submitted, handle = saved_submit(ctx, owned, tmp_path, transport)
    transport["queue"].append(response(query_body(ctx.operation, pending=pending)))
    result = execution.query_async_image(handle, ctx.authority)
    record = recovery.record_with_changes(submitted, phase="observed", observation=result.to_dict())
    with owned.guard():
        atomic_write_json(owned.root / "execution.json", record.model_dump(mode="json"))
    return record, handle


def fetched(ctx, owned, tmp_path, transport):
    record, handle = observed(ctx, owned, tmp_path, transport)
    transport["queue"].append(response(rgb_png_bytes("result", width=32, height=32)))
    result = execution.fetch_async_image_to_private_file(
        record, ctx.authority, scratch_dir=tmp_path / "fetch-scratch", max_bytes=LIMIT,
    )
    return record, handle, result


def publish(record, handle, result, owned, *, callback):
    with verified_private_media_file(result.path, scratch_root=result.path.parent,
                                     expected_sha256=result.sha256, expected_size=result.size_bytes,
                                     max_bytes=LIMIT) as stream:
        return store_media_stream(
            iter(lambda: stream.read(8192), b""), output_ref=record.output_ref, output_root=owned.root,
            writer_lock=owned.lease, storage_guard=owned.guard, max_bytes=LIMIT,
            media_kind="image", publish_policy="no_overwrite", on_prepared=callback,
        )


@pytest.mark.parametrize("operation", OPERATIONS)
def test_actual_image_operations_save_before_query_and_publish_real_png(operation, owned, tmp_path, transport):
    ctx = context(owned, operation)
    if ctx.source is not None:
        ctx.source.write_bytes(rgb_png_bytes("mutable source changed", width=32, height=32))
    record, handle, result = fetched(ctx, owned, tmp_path, transport)
    calls = transport["calls"]
    assert [call.method for call, _ in calls] == ["POST", "GET", "GET"]
    assert calls[1][0].url == ctx.authority.scope.base_url + tasks.image_query_path(handle)
    assert calls[2][0].url == IMAGE_URL
    assert calls[0][0].headers["Authorization"] == "Bearer " + SYNTHETIC_IMAGE_CREDENTIAL
    assert calls[1][0].headers["Authorization"] == "Bearer " + SYNTHETIC_IMAGE_CREDENTIAL
    assert all(name not in calls[2][0].headers for name in ("Authorization", "Cookie", "X-DashScope-WorkSpace"))
    assert all(not options["proxies"] and options["allow_redirects"] is False for _, options in calls)
    if operation == "gpt_edit":
        payload = json.loads(calls[0][0].body)
        assert payload["images"] == ["data:image/png;base64," + base64.b64encode(ctx.original).decode()]
        assert payload["size"] == "1024x1536" and "quality" not in payload
    assert not list((tmp_path / "submit-scratch").iterdir())
    data = result.path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert result.sha256 == hashlib.sha256(data).hexdigest() and result.size_bytes == len(data)
    assert result.encoding == "image/png" and result.media_kind == "image"
    assert result.path.parent == tmp_path / "fetch-scratch"
    assert list(result.path.parent.iterdir()) == [result.path]
    assert isinstance(record.output_ref, str)
    destination = owned.root / record.output_ref
    assert not destination.exists()
    proofs = []

    def prepare(relative_path, digest, size):
        assert owned.inside == 1 and not destination.exists()
        artifact = tasks.ProviderArtifact(handle, relative_path, digest, size)
        proof = recovery.artifact_proof_from_record(record, artifact)
        prepared = recovery.record_with_changes(record, prepared_artifact=proof)
        atomic_write_json(owned.root / "execution.json", prepared.model_dump(mode="json"))
        proofs.append(proof)

    relative, digest, size = publish(record, handle, result, owned, callback=prepare)
    assert relative == record.output_ref and digest == result.sha256 and size == result.size_bytes
    assert destination.read_bytes() == data and len(proofs) == 1
    proof = proofs[0]
    assert proof["handle"] == handle.to_dict() and proof["attempt_id"] == record.attempt_id
    assert proof["output_ref"] == record.output_ref and proof["contract_version"] == "provider-async-image-artifact-v1"
    saved = ProviderExecutionRecord.model_validate_json((owned.root / "execution.json").read_text())
    assert saved.prepared_artifact == proof and saved.artifact is None
    assert ctx.record.phase == "submitting" and ctx.record.handle is None
    assert all(item.raw.closed for item in transport["responses"])


def test_known_edit_query_and_fetch_need_no_old_generation_material(owned, tmp_path, transport, monkeypatch):
    ctx = context(owned, "gpt_edit")
    submitted, handle = saved_submit(ctx, owned, tmp_path, transport)
    ctx.source.unlink()
    (owned.root / "media_versions" / "sha256" / ctx.version.sha256[:2] / ctx.version.sha256).unlink()
    historical = recovery.record_with_changes(submitted, intent={}, media=[])
    with owned.guard():
        atomic_write_json(owned.root / "execution.json", historical.model_dump(mode="json"))
    loaded = ProviderExecutionRecord.model_validate_json((owned.root / "execution.json").read_text())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Query/fetch must not reconstruct input or a model")

    monkeypatch.setattr(execution, "_materialize_image_references", forbidden)
    monkeypatch.setattr(execution, "image_intent_from_async_record_for_submit", forbidden)
    monkeypatch.setattr(execution.MuleRouterImageModel, "__init__", forbidden)
    transport["queue"].append(response(query_body("gpt_edit")))
    observation = execution.query_async_image(recovery.image_query_handle_from_record(loaded), ctx.authority)
    record = recovery.record_with_changes(loaded, phase="observed", observation=observation.to_dict())
    transport["queue"].append(response(rgb_png_bytes("after missing inputs", width=32, height=32)))
    artifact = execution.fetch_async_image_to_private_file(record, ctx.authority,
                                                         scratch_dir=tmp_path / "recovery-scratch", max_bytes=LIMIT)
    assert artifact.path.is_file() and record.fingerprint == submitted.fingerprint
    assert record.intent == {} and record.media == []
    assert [call.method for call, _ in transport["calls"]] == ["POST", "GET", "GET"]
    assert handle == recovery.image_query_handle_from_record(record)


@pytest.mark.parametrize("schema", [1, 2])
def test_new_operations_do_not_upgrade_legacy_execution(schema, owned, tmp_path, transport):
    ctx = context(owned, "gpt_generation")
    if schema == 2:
        legacy = intents.build_accepted_sync_artifact_record(
            intent=GenerationIntent(model_id="gpt-image/gpt-image-2#image", catalog_mode="image", mode="t2i",
                                    prompt="old original prompt", params={"size": "1024x1024"},
                                    sources={"model_id": "request", "mode": "request", "prompt": "request"},
                                    request_fields=("model_id", "mode", "prompt")),
            provider_model_id="gpt-image-2", media_kind="image", media=[], scope=ctx.authority.scope,
            credential_ref=ctx.authority.credential_ref, attempt_id="old-sync", output_ref="assets/studio/old.png",
        )
        legacy = recovery.record_with_changes(legacy, phase="submitting")
    else:
        legacy = ProviderExecutionRecord.model_validate({
            "schema_version": 1, "phase": "submitting", "attempt_id": "old-video",
            "provider_model_id": "gpt-image-2", "intent": {}, "media": [],
            "scope": ctx.authority.scope.to_dict(), "credential_ref": ctx.authority.credential_ref,
            "fingerprint": "a" * 64,
        })
    original = legacy.model_dump(mode="json")
    with pytest.raises(execution.ProviderMediaExecutionError, match="schema-v3"):
        execution.submit_async_image(legacy, ctx.authority, output_root=owned.root,
                                     scratch_dir=tmp_path / "rejected", max_bytes=LIMIT, on_submitted=lambda _h: None)
    with pytest.raises(execution.ProviderMediaExecutionError, match="schema-v3"):
        execution.fetch_async_image_to_private_file(legacy, ctx.authority,
                                                    scratch_dir=tmp_path / "rejected", max_bytes=LIMIT)
    assert legacy.model_dump(mode="json") == original and not transport["calls"]


@pytest.mark.parametrize("fault", ["accepted", "fingerprint", "missing_input"])
def test_unready_or_corrupt_submit_material_cannot_reach_post(fault, owned, tmp_path, transport):
    ctx = context(owned, "gpt_edit")
    record = ctx.record
    if fault == "accepted":
        record = ctx.accepted
    elif fault == "fingerprint":
        record = recovery.record_with_changes(record, intent={**record.intent, "prompt": "replaced author prompt"})
    else:
        (owned.root / "media_versions" / "sha256" / ctx.version.sha256[:2] / ctx.version.sha256).unlink()
    expected = (execution.ProviderMediaExecutionError if fault == "accepted"
                else recovery.ProviderRecoveryError if fault == "fingerprint" else MediaVersionError)
    with pytest.raises(expected):
        execution.submit_async_image(record, ctx.authority, output_root=owned.root,
                                     scratch_dir=tmp_path / "rejected", max_bytes=LIMIT, on_submitted=lambda _h: None)
    assert not transport["calls"]


def test_submit_cannot_pay_for_an_attempt_that_private_fetch_cannot_name(owned, tmp_path, transport):
    # A's opaque record identity is structurally valid; this execution path has
    # the existing stricter private filename rule, which must run before POST.
    ctx = context(owned, "gpt_generation", attempt_id="unsafe/attempt")
    with pytest.raises(execution.ProviderMediaExecutionError, match="attempt id"):
        execution.submit_async_image(ctx.record, ctx.authority, output_root=owned.root,
                                     scratch_dir=tmp_path / "unsafe-attempt", max_bytes=LIMIT,
                                     on_submitted=lambda _handle: None)
    assert not transport["calls"] and not (tmp_path / "unsafe-attempt").exists()


def test_submit_authority_mismatch_is_rejected_before_provider_or_scratch(owned, tmp_path, transport):
    ctx = context(owned, "wan")
    scope = replace(ctx.authority.scope, workspace_id="different-workspace")
    foreign = replace(ctx.authority, scope=scope,
                      credential=tasks.ResolvedCredential(scope, ctx.authority.credential_ref, SYNTHETIC_IMAGE_CREDENTIAL))
    with pytest.raises(ValueError, match="does not match"):
        execution.submit_async_image(ctx.record, foreign, output_root=owned.root,
                                     scratch_dir=tmp_path / "wrong-authority", max_bytes=LIMIT,
                                     on_submitted=lambda _handle: None)
    assert not transport["calls"] and not (tmp_path / "wrong-authority").exists()


@pytest.mark.parametrize("persist_first", [False, True])
def test_callback_storage_failure_keeps_original_exception_and_never_queries(persist_first, owned, tmp_path, transport):
    ctx = context(owned, "gpt_edit")
    transport["queue"].append(response(submit_body("gpt_edit"), status=202))
    failure = StorageError("synthetic original durable failure", commit_uncertain=persist_first)
    seen = []

    def save(handle):
        seen.append(handle)
        if persist_first:
            with owned.guard():
                submitted = recovery.record_with_changes(ctx.record, phase="submitted", handle=handle.to_dict())
                atomic_write_json(owned.root / "execution.json", submitted.model_dump(mode="json"))
        raise failure

    with pytest.raises(StorageError) as caught:
        execution.submit_async_image(ctx.record, ctx.authority, output_root=owned.root,
                                     scratch_dir=tmp_path / "failed-submit", max_bytes=LIMIT, on_submitted=save)
    assert caught.value is failure and len(seen) == 1
    assert [call.method for call, _ in transport["calls"]] == ["POST"]
    assert not list((tmp_path / "failed-submit").iterdir())
    assert (owned.root / "execution.json").exists() is persist_first


def test_async_caller_callback_is_rejected_before_materialization_or_post(owned, tmp_path, transport):
    ctx = context(owned, "gpt_edit")

    async def deferred(_handle):
        raise AssertionError("This callback must never execute")

    with pytest.raises(ValueError, match="synchronous"):
        execution.submit_async_image(ctx.record, ctx.authority, output_root=owned.root,
                                     scratch_dir=tmp_path / "deferred", max_bytes=LIMIT, on_submitted=deferred)
    assert not transport["calls"] and not (tmp_path / "deferred").exists()


@pytest.mark.parametrize("change", ["credential", "scope", "model", "legacy"])
def test_query_binding_mismatch_is_zero_transport(change, owned, tmp_path, transport):
    ctx = context(owned, "wan")
    _, handle = saved_submit(ctx, owned, tmp_path, transport)
    authority = ctx.authority
    if change == "credential":
        authority = replace(authority, credential_ref="credential-other",
                            credential=tasks.ResolvedCredential(authority.scope, "credential-other", SYNTHETIC_IMAGE_CREDENTIAL))
    elif change == "scope":
        scope = replace(authority.scope, workspace_id="different-workspace")
        authority = replace(authority, scope=scope,
                            credential=tasks.ResolvedCredential(scope, authority.credential_ref, SYNTHETIC_IMAGE_CREDENTIAL))
    elif change == "model":
        authority = replace(authority, provider_model_id="wan2.7-image-pro")
    else:
        handle = tasks.ProviderHandle(provider_model_id=handle.provider_model_id, task_id=handle.task_id,
                                      scope=handle.scope, credential_ref=handle.credential_ref,
                                      query_capability=tasks.QueryCapability.BY_TASK_ID)
    count = len(transport["calls"])
    with pytest.raises((ValueError, execution.ProviderMediaExecutionError)):
        execution.query_async_image(handle, authority)
    assert len(transport["calls"]) == count


def test_core_query_transport_failure_does_not_resume_submission(owned, tmp_path, transport):
    ctx = context(owned, "gpt_generation")
    saved, handle = saved_submit(ctx, owned, tmp_path, transport)
    original = saved.model_dump(mode="json")
    transport["queue"].append(requests.ReadTimeout("synthetic-private-detail"))
    with pytest.raises(tasks.ProviderQueryError, match="transport"):
        execution.query_async_image(handle, ctx.authority)
    assert saved.model_dump(mode="json") == original
    assert [call.method for call, _ in transport["calls"]] == ["POST", "GET"]


def test_non_successful_original_observation_cannot_fetch(owned, tmp_path, transport):
    ctx = context(owned, "gpt_generation")
    record, _ = observed(ctx, owned, tmp_path, transport, pending=True)
    with pytest.raises(ValueError):
        execution.fetch_async_image_to_private_file(record, ctx.authority,
                                                    scratch_dir=tmp_path / "not-ready", max_bytes=LIMIT)
    assert [call.method for call, _ in transport["calls"]] == ["POST", "GET"]
    assert not (tmp_path / "not-ready").exists()


@pytest.mark.parametrize("fault", ["invalid_image", "oversized"])
def test_failed_fetch_leaves_no_private_or_managed_result(fault, owned, tmp_path, transport):
    ctx = context(owned, "gpt_generation")
    record, _ = observed(ctx, owned, tmp_path, transport)
    body = b"not an image" if fault == "invalid_image" else rgb_png_bytes("bounded", width=32, height=32)
    headers = {"Content-Length": str(LIMIT + 1)} if fault == "oversized" else {}
    transport["queue"].append(response(body, headers=headers))
    with pytest.raises((execution.ProviderMediaExecutionError, tasks.ProviderFetchError)):
        execution.fetch_async_image_to_private_file(record, ctx.authority,
                                                    scratch_dir=tmp_path / "bad-fetch", max_bytes=LIMIT)
    assert not list((tmp_path / "bad-fetch").iterdir())
    assert not (owned.root / record.output_ref).exists()
    assert [call.method for call, _ in transport["calls"]] == ["POST", "GET", "GET"]


def test_existing_prepared_proof_rejects_changed_refetched_bytes(owned, tmp_path, transport):
    ctx = context(owned, "gpt_generation")
    record, handle, result = fetched(ctx, owned, tmp_path, transport)
    assert isinstance(record.output_ref, str)
    proof = recovery.artifact_proof_from_record(record, tasks.ProviderArtifact(
        handle, record.output_ref, result.sha256, result.size_bytes))
    prepared = recovery.record_with_changes(record, prepared_artifact=proof)
    transport["queue"].append(response(rgb_png_bytes("changed upstream bytes", width=32, height=32)))
    with pytest.raises(recovery.ProviderRecoveryError, match="changed_after_prepare"):
        execution.fetch_async_image_to_private_file(prepared, ctx.authority,
                                                    scratch_dir=tmp_path / "refetch", max_bytes=LIMIT)
    assert not list((tmp_path / "refetch").iterdir())
    assert prepared.prepared_artifact == proof and result.path.is_file()
    assert not (owned.root / record.output_ref).exists()
    assert [call.method for call, _ in transport["calls"]].count("POST") == 1


@pytest.mark.parametrize("fault", ["authority", "completed", "http_result"])
def test_fetch_rejects_invalid_authority_phase_or_result_before_download(fault, owned, tmp_path, transport):
    ctx = context(owned, "gpt_generation")
    record, handle, result = fetched(ctx, owned, tmp_path, transport)
    authority = ctx.authority
    if fault == "authority":
        authority = replace(authority, credential_ref="other-credential",
                            credential=tasks.ResolvedCredential(authority.scope, "other-credential", SYNTHETIC_IMAGE_CREDENTIAL))
    elif fault == "completed":
        assert isinstance(record.output_ref, str)
        proof = recovery.artifact_proof_from_record(record, tasks.ProviderArtifact(
            handle, record.output_ref, result.sha256, result.size_bytes))
        record = recovery.record_with_changes(record, phase="completed", prepared_artifact=proof, artifact=proof)
    else:
        # Corrupt only the saved transport URL after a real prior observation.
        assert isinstance(record.observation, dict)
        record = recovery.record_with_changes(record, observation={
            **record.observation, "image_url": IMAGE_URL.replace("https:", "http:", 1)})
    before = len(transport["calls"])
    with pytest.raises((ValueError, execution.ProviderMediaExecutionError)):
        execution.fetch_async_image_to_private_file(record, authority,
                                                    scratch_dir=tmp_path / "rejected-fetch", max_bytes=LIMIT)
    assert len(transport["calls"]) == before and not (tmp_path / "rejected-fetch").exists()
    assert result.path.is_file()


@pytest.mark.parametrize("fault", ["owner", "closed", "prepared_failure", "existing_destination"])
def test_existing_publication_guards_keep_unrelated_output_unchanged(fault, owned, tmp_path, transport):
    ctx = context(owned, "gpt_generation")
    record, handle, result = fetched(ctx, owned, tmp_path, transport)
    assert isinstance(record.output_ref, str)
    destination = owned.root / record.output_ref
    old = b"old unrelated destination"
    prepared_calls = []
    failure = StorageError("synthetic prepared proof storage failure")

    def prepare(relative, digest, size):
        proof = recovery.artifact_proof_from_record(record, tasks.ProviderArtifact(handle, relative, digest, size))
        prepared_calls.append(proof)
        if fault == "prepared_failure":
            raise failure
        raise AssertionError("Publication must reject before this callback")

    if fault == "owner":
        owned.current = object()
    elif fault == "closed":
        owned.lease.close()
    elif fault == "existing_destination":
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(old)
    with pytest.raises((StorageError, MediaVersionError)) as caught:
        publish(record, handle, result, owned, callback=prepare)
    if fault == "prepared_failure":
        assert caught.value is failure and len(prepared_calls) == 1
    else:
        assert not prepared_calls
    if fault == "existing_destination":
        assert destination.read_bytes() == old
    else:
        assert not destination.exists()
    assert result.path.is_file() and [call.method for call, _ in transport["calls"]] == ["POST", "GET", "GET"]
