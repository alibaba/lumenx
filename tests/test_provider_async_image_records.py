"""Format-only image query authority, not provider completion or published bytes."""

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest

from src.models import provider_media_intents as intents
from src.models import provider_recovery as recovery
from src.models import provider_tasks as tasks
from src.models.provider_execution import ProviderExecutionRecord
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import serialize_json


WAN = "dashscope-wan27-image-task-v1"
GENERATION = "mulerouter-gpt-image2-generation-task-v1"
EDIT = "mulerouter-gpt-image2-edit-task-v1"
TASK_ID = "8ec29164-54ef-4d1b-9a85-594919983133"
OTHER_ID = "2cc5191a-afcb-40c7-9d92-53ade34c65a3"
OUTPUT = "assets/studio/attempt-image.png"
IMAGE = "https://files.example.invalid/result.png?signature=synthetic"
WAN_VERSION = "provider-wan27-t2i-bound-http-v1"
EDIT_VERSION = "provider-gpt-image2-edit-bound-v2"


def _handle_payload(contract=GENERATION) -> dict[str, Any]:
    wan = contract == WAN
    scope = tasks.ProviderScope(
        "dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", region="cn-beijing",
    ) if wan else tasks.ProviderScope("mulerouter", "mulerouter", "mulerouter_http", "https://api.mulerouter.ai")
    return {
        "provider_model_id": "wan2.7-image" if wan else "gpt-image-2",
        "task_id": TASK_ID,
        "scope": scope.to_dict(),
        "credential_ref": "credential-original",
        "request_id": "submit-original",
        "query_capability": "by_task_id",
        "schema_version": 2,
        "submitted_at": 100.0,
        "query_contract": contract,
    }


def _intent(contract=GENERATION):
    return GenerationIntent(
        model_id="wan/wan2.7-image#image" if contract == WAN else "gpt-image/gpt-image-2#image",
        catalog_mode="image", mode="i2i" if contract == EDIT else "t2i", prompt="Original author input",
        params={"size": "1024*1024", "watermark": False, "seed": 0} if contract == WAN else {"size": "1024x1536"},
        sources={"model_id": "request", "mode": "request", "prompt": "request", "params.size": "request"},
        request_fields=("model_id", "mode", "prompt", "params.size"),
    )


def _media(contract):
    # Frozen input metadata only; no claim that these bytes exist or were decoded.
    return [{"schema_version": 1, "sha256": "a" * 64, "size_bytes": 123,
             "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png"}] if contract == EDIT else []


def _payload(contract=GENERATION, phase="submitted") -> dict[str, Any]:
    handle = _handle_payload(contract)
    frozen = _intent(contract).to_dict()
    if contract in {WAN, EDIT}:
        frozen["intent_contract_version"] = WAN_VERSION if contract == WAN else EDIT_VERSION
    # serialize_json preserves insertion order; match the existing identity order.
    identity = {
        "attempt_id": "attempt-image", "provider_model_id": handle["provider_model_id"],
        "intent": frozen, "media": _media(contract), "scope": handle["scope"], "credential_ref": handle["credential_ref"],
        "execution_kind": "async_artifact", "media_kind": "image", "output_ref": OUTPUT, "schema_version": 3,
    }
    return {**identity, "phase": phase, "fingerprint": recovery._fingerprint(identity),
            "handle": handle if phase == "submitted" else None,
            "observation": None, "prepared_artifact": None, "artifact": None}


def _observed(contract=GENERATION):
    record = ProviderExecutionRecord.model_validate(_payload(contract))
    assert isinstance(record.handle, dict)
    handle = tasks.ProviderHandle.from_dict(record.handle)
    observed = tasks.ProviderObservation(
        task_id=TASK_ID, status=tasks.ProviderTaskStatus.SUCCEEDED, raw_status="SUCCEEDED" if contract == WAN else "completed",
        source_handle=handle, image_url=IMAGE,
    )
    return recovery.record_with_changes(record, phase="observed", observation=observed.to_dict()), handle


def test_legacy_handle_and_observation_dumps_do_not_gain_image_fields():
    raw = _handle_payload()
    raw.update(schema_version=1, provider_model_id="wan2.7-i2v")
    raw.pop("query_contract")
    handle = tasks.ProviderHandle.from_dict(raw)
    assert serialize_json(handle.to_dict()) == serialize_json(raw)
    old = tasks.ProviderObservation(TASK_ID, tasks.ProviderTaskStatus.RUNNING, "RUNNING", source_handle=handle)
    expected = {"task_id": TASK_ID, "status": "running", "raw_status": "RUNNING", "request_id": None,
                "video_url": None, "error_code": None, "source_handle": raw}
    assert serialize_json(old.to_dict()) == serialize_json(expected)
    old_identity = {"schema_version": 1, "attempt_id": "attempt-old", "phase": "submitted",
                    "provider_model_id": handle.provider_model_id, "intent": {}, "media": [], "scope": raw["scope"],
                    "credential_ref": raw["credential_ref"], "fingerprint": "f" * 64, "handle": raw}
    old_video_proof = {"schema_version": 1, "attempt_id": "attempt-old", "handle": raw,
                       "relative_path": OUTPUT, "sha256": "a" * 64, "size_bytes": 11}
    with pytest.raises(ValueError, match="path"):
        ProviderExecutionRecord.model_validate({**old_identity, "prepared_artifact": old_video_proof})
    for introduced in (None, GENERATION):
        with pytest.raises(ValueError, match="receipt|contract"):
            tasks.ProviderHandle.from_dict({**raw, "query_contract": introduced})


@pytest.mark.parametrize("contract,path", [
    (WAN, "/api/v1/tasks/"),
    (GENERATION, "/vendors/openai/v1/gpt-image-2/generation/"),
    (EDIT, "/vendors/openai/v1/gpt-image-2/edit/"),
])
def test_image_handle_roundtrip_has_fixed_original_query_path(contract, path):
    raw = _handle_payload(contract)
    handle = tasks.ProviderHandle.from_dict(json.loads(json.dumps(raw)))
    assert handle.to_dict() == raw
    assert tasks.image_query_path(handle) == path + TASK_ID
    assert handle.scope is not None and handle.credential_ref is not None
    credential = tasks.ResolvedCredential(handle.scope, handle.credential_ref, "synthetic-original-key")
    assert tasks.validate_query_binding(handle, credential, media_kind="image") == handle.scope
    with pytest.raises(ValueError, match="media kind"):
        tasks.validate_query_binding(handle, credential)


@pytest.mark.parametrize("field,value", [
    ("query_contract", "/arbitrary/url"), ("provider_model_id", "qwen-image-2.0"),
    ("query_capability", "unknown"), ("credential_ref", None), ("task_id", "../escape"),
    ("task_id", "non-uuid"), ("scope.backend", "mulerun_cli"),
    ("scope.base_url", "https://other.example.invalid"),
])
def test_image_handle_rejects_untrusted_query_identity(field, value):
    raw = _handle_payload()
    if field.startswith("scope."):
        raw["scope"][field.split(".")[1]] = value
    else:
        raw[field] = value
    with pytest.raises(ValueError):
        tasks.ProviderHandle.from_dict(raw)


@pytest.mark.parametrize("bad_echo", [None, OTHER_ID])
def test_image_observation_requires_nonempty_matching_response_id(bad_echo):
    handle = tasks.ProviderHandle.from_dict(_handle_payload())
    with pytest.raises(ValueError, match="observation|task ID"):
        tasks.ProviderObservation(
            task_id=bad_echo, status=tasks.ProviderTaskStatus.SUCCEEDED, raw_status="completed",
            source_handle=handle, image_url=IMAGE,
        )


def test_image_observation_and_fetch_are_explicitly_separate_from_video():
    record, handle = _observed()
    observed = recovery.observation_from_record(record)
    assert observed.image_url == IMAGE and observed.video_url is None
    tasks.validate_fetch_binding(handle, observed, media_kind="image")
    for raw_status, status in (("queued", tasks.ProviderTaskStatus.PENDING), ("processing", tasks.ProviderTaskStatus.RUNNING),
                               ("failed", tasks.ProviderTaskStatus.FAILED), ("canceled", tasks.ProviderTaskStatus.UNKNOWN)):
        assert tasks.image_status_from_raw(handle, raw_status) is status
        pending = tasks.ProviderObservation(TASK_ID, status, raw_status, source_handle=handle)
        assert pending.image_url is None
    with pytest.raises(ValueError):
        tasks.validate_fetch_binding(handle, observed)
    for changes in ({"raw_status": "failed"}, {"video_url": "https://files.example.invalid/out.mp4"}, {"image_url": "data:image/png;base64,AA=="},
                    {"image_url": None}, {"status": tasks.ProviderTaskStatus.RUNNING}, {"status": tasks.ProviderTaskStatus.CANCELED}):
        kwargs: dict[str, Any] = {"task_id": TASK_ID, "status": tasks.ProviderTaskStatus.SUCCEEDED, "raw_status": "completed",
                  "source_handle": handle, "image_url": IMAGE, **changes}
        with pytest.raises(ValueError):
            tasks.ProviderObservation(**kwargs)
    old_raw = _handle_payload()
    old_raw.update(schema_version=1, provider_model_id="wan2.7-i2v")
    old_raw.pop("query_contract")
    old_handle = tasks.ProviderHandle.from_dict(old_raw)
    old_observed = tasks.ProviderObservation(TASK_ID, tasks.ProviderTaskStatus.SUCCEEDED, "SUCCEEDED",
                                          source_handle=old_handle, video_url="https://files.example.invalid/out.mp4")
    with pytest.raises(ValueError):
        tasks.validate_fetch_binding(old_handle, old_observed, media_kind="image")
    with pytest.raises(ValueError):
        tasks.image_query_path(old_handle)
    legacy_image = tasks.ProviderHandle.from_dict({**old_raw, "provider_model_id": "gpt-image-2"})
    with pytest.raises(ValueError):
        tasks.image_query_path(legacy_image)


def test_async_image_proof_roundtrip_binds_format_without_claiming_published_file():
    record, handle = _observed()
    artifact = tasks.ProviderArtifact(handle, OUTPUT, hashlib.sha256(b"format-only").hexdigest(), 11)
    proof = recovery.artifact_proof_from_record(record, artifact)
    assert proof == {"schema_version": 3, "contract_version": "provider-async-image-artifact-v1",
                     "attempt_id": "attempt-image", "handle": handle.to_dict(), "media_kind": "image",
                     "relative_path": OUTPUT, "output_ref": OUTPUT, "sha256": artifact.sha256, "size_bytes": 11}
    prepared = recovery.record_with_changes(record, prepared_artifact=proof)
    with pytest.raises(recovery.ProviderRecoveryError, match="changed_after_prepare"):
        recovery.artifact_proof_from_record(prepared, tasks.ProviderArtifact(handle, OUTPUT, "b" * 64, 11))
    completed = recovery.record_with_changes(prepared, phase="completed", artifact=proof)
    roundtrip = ProviderExecutionRecord.model_validate_json(completed.model_dump_json())
    assert roundtrip.model_dump(mode="json") == completed.model_dump(mode="json")
    assert recovery.artifact_from_proof(proof) == artifact
    assert recovery.require_matching_artifact_proof(completed, artifact, proof) == proof
    recovery._verify_record_fingerprint(roundtrip)
    assert recovery._record_fingerprint_payload(roundtrip)["schema_version"] == 3


@pytest.mark.parametrize("mutation", ["attempt", "scope", "task", "output", "video", "schema"])
def test_async_image_proof_cannot_cross_identity_output_or_old_format(mutation):
    record, handle = _observed(WAN if mutation == "scope" else GENERATION)
    artifact = tasks.ProviderArtifact(handle, OUTPUT, "a" * 64, 11)
    proof = deepcopy(recovery.artifact_proof_from_record(record, artifact))
    if mutation == "attempt":
        proof["attempt_id"] = "other-attempt"
    elif mutation == "scope":
        proof["handle"]["scope"].update(base_url="https://dashscope-intl.aliyuncs.com", region="ap-southeast-1")
    elif mutation == "task":
        proof["handle"]["task_id"] = OTHER_ID
    elif mutation == "output":
        proof["output_ref"] = proof["relative_path"] = "assets/studio/other.png"
    elif mutation == "video":
        proof["media_kind"] = "video"
        proof["output_ref"] = proof["relative_path"] = "video/other.mp4"
    else:
        proof["schema_version"] = 2
        proof["contract_version"] = "provider-sync-artifact-v1"
    with pytest.raises(ValueError):
        recovery.record_with_changes(record, prepared_artifact=proof)


def test_async_image_phases_require_original_handle_observation_and_equal_proofs():
    raw = _payload()
    valid = ProviderExecutionRecord.model_validate(raw)
    for changes in ({"handle": None}, {"handle": {**raw["handle"], "query_contract": EDIT}},
                    {"phase": "accepted"}, {"phase": "submitting"}, {"phase": "cancelled"},
                    {"phase": "observed"}, {"phase": "completed"}, {"scope": {**raw["scope"], "base_url": "https://different.invalid"}}):
        with pytest.raises(ValueError):
            ProviderExecutionRecord.model_validate({**raw, **changes})
    with pytest.raises(ValueError):
        recovery.require_matching_handle(valid, tasks.ProviderHandle.from_dict({**raw["handle"], "task_id": OTHER_ID}))
    observed, handle = _observed()
    proof = recovery.artifact_proof_from_record(observed, tasks.ProviderArtifact(handle, OUTPUT, "a" * 64, 11))
    with pytest.raises(ValueError):
        recovery.record_with_changes(observed, phase="completed", artifact=proof)
    with pytest.raises(ValueError):
        recovery.record_with_changes(observed, phase="completed", prepared_artifact=proof, artifact={**proof, "sha256": "b" * 64})
    # V1/v2 are not alternate labels for this new authority.
    for schema in (1, 2):
        old = {**raw, "schema_version": schema}
        if schema == 1:
            for key in ("execution_kind", "media_kind", "output_ref"):
                old.pop(key)
        else:
            old["execution_kind"] = "sync_artifact"
        with pytest.raises(ValueError):
            ProviderExecutionRecord.model_validate(old)


@pytest.mark.parametrize("contract", [GENERATION, EDIT])
def test_json_reloaded_known_handle_needs_no_generation_material_and_cannot_submit(tmp_path, contract):
    record = ProviderExecutionRecord.model_validate(_payload(contract))
    original_fingerprint = record.fingerprint
    persisted = record.model_dump(mode="json")
    persisted.pop("intent")
    persisted.pop("media")
    path = tmp_path / "known-image.json"
    path.write_text(json.dumps(persisted), encoding="utf-8")
    reloaded = ProviderExecutionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    assert reloaded.intent == {} and reloaded.media == []
    assert reloaded.fingerprint == original_fingerprint
    handle = recovery.image_query_handle_from_record(reloaded)
    expected = "edit" if contract == EDIT else "generation"
    assert tasks.image_query_path(handle) == f"/vendors/openai/v1/gpt-image-2/{expected}/{TASK_ID}"
    with pytest.raises(recovery.ProviderRecoveryError):
        intents.image_intent_from_async_record_for_submit(reloaded)
    with pytest.raises(recovery.ProviderRecoveryError):
        recovery.reconstruct_submission_from_record(reloaded, output_root=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == persisted


@pytest.mark.parametrize("contract", [WAN, GENERATION, EDIT])
def test_new_async_identity_preserves_intent_and_unknown_record_gains_no_query_authority(contract):
    original = _intent(contract)
    raw_handle = _handle_payload(contract)
    kwargs: dict[str, Any] = {} if contract == GENERATION else {"image_contract_version": WAN_VERSION if contract == WAN else EDIT_VERSION}
    record = intents.build_accepted_async_image_record(
        intent=original, provider_model_id=raw_handle["provider_model_id"], media=_media(contract),
        scope=tasks.ProviderScope.from_dict(raw_handle["scope"]), credential_ref=raw_handle["credential_ref"],
        attempt_id="attempt-image", output_ref=OUTPUT, **kwargs,
    )
    assert record.phase == "accepted" and record.schema_version == 3 and record.handle is None
    assert intents.image_intent_from_async_record_for_submit(record).to_dict() == original.to_dict()
    recovery._verify_record_fingerprint(record)
    unknown = recovery.record_with_changes(record, phase="submitting")
    with pytest.raises(recovery.ProviderRecoveryError, match="handle_missing"):
        recovery.image_query_handle_from_record(unknown)
    # This is validation of complete material, never a claim permitting recovery POST.
    missing = record.model_dump(mode="json")
    missing["intent"] = {}
    invalid = ProviderExecutionRecord.model_validate(missing)
    with pytest.raises(recovery.ProviderRecoveryError):
        intents.image_intent_from_async_record_for_submit(invalid)
    recomputed = recovery.record_with_changes(invalid, fingerprint=recovery._fingerprint(recovery._record_fingerprint_payload(invalid)))
    with pytest.raises(recovery.ProviderRecoveryError):
        intents.image_intent_from_async_record_for_submit(recomputed)
    handle = tasks.ProviderHandle.from_dict(raw_handle)
    assert recovery.require_matching_handle(unknown, handle) == handle
    submitted = recovery.record_with_changes(unknown, phase="submitted", handle=handle.to_dict())
    with pytest.raises(recovery.ProviderRecoveryError):
        intents.image_intent_from_async_record_for_submit(submitted)


def test_async_submit_material_rejects_incomplete_or_extra_gpt_parameters_even_with_matching_fingerprint():
    for changes in ({"prompt": None}, {"prompt": " "}, {"params": {}}, {"params": {"size": "1024x1024", "negative_prompt": "ignored"}}):
        raw = _payload(phase="accepted")
        raw["intent"].update(changes)
        record = ProviderExecutionRecord.model_validate(raw)
        record = recovery.record_with_changes(record, fingerprint=recovery._fingerprint(recovery._record_fingerprint_payload(record)))
        with pytest.raises(ValueError):
            intents.image_intent_from_async_record_for_submit(record)
