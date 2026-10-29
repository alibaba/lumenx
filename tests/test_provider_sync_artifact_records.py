"""Strict dormant media-generation records do not alter legacy provider execution."""

from __future__ import annotations

import pytest

from src.models.provider_execution import ProviderExecutionRecord
from src.models.media_generation_records import MediaGenerationCandidateRecord
from src.models.provider_recovery import ProviderRecoveryError, _fingerprint, _verify_record_fingerprint
from src.models.provider_tasks import ProviderHandle, ProviderScope, QueryCapability
from src.utils.json_storage import serialize_json


SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
IDENTITY = {
    "attempt_id": "attempt-1",
    "provider_model_id": "wan2.7-i2v",
    "intent": {
        "rules_version": "generation-intent-v1",
        "model_id": "wan/wan2.7-video#i2v",
        "catalog_mode": "i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "params": {"duration": 5, "resolution": "1080p"},
        "sources": {},
        "request_fields": ["duration", "resolution"],
        "preset_id": None,
        "preset_version": None,
    },
    "media": [],
    "scope": SCOPE.to_dict(),
    "credential_ref": "credential-1",
}
HANDLE = ProviderHandle(
    provider_model_id="wan2.7-i2v",
    task_id="task-1",
    scope=SCOPE,
    credential_ref="credential-1",
    request_id="request-1",
    query_capability=QueryCapability.BY_TASK_ID,
    submitted_at=100.0,
).to_dict()
HASH = "a" * 64


def legacy_record_payload(**changes):
    payload = {
        "schema_version": 1,
        "attempt_id": IDENTITY["attempt_id"],
        "phase": "accepted",
        "provider_model_id": IDENTITY["provider_model_id"],
        "intent": IDENTITY["intent"],
        "media": IDENTITY["media"],
        "scope": IDENTITY["scope"],
        "credential_ref": IDENTITY["credential_ref"],
        "fingerprint": _fingerprint(IDENTITY),
        "handle": None,
        "observation": None,
        "prepared_artifact": None,
        "artifact": None,
    }
    payload.update(changes)
    return payload


def sync_proof(*, media_kind="image", relative_path="assets/atelier/out.png", attempt_id="attempt-sync"):
    return {
        "schema_version": 2,
        "contract_version": "provider-sync-artifact-v1",
        "attempt_id": attempt_id,
        "media_kind": media_kind,
        "relative_path": relative_path,
        "output_ref": relative_path,
        "sha256": HASH,
        "size_bytes": 123,
    }


def sync_record_payload(**changes):
    identity = {
        "attempt_id": "attempt-sync",
        "provider_model_id": "qwen-image",
        "intent": {"model_id": "qwen-image", "mode": "t2i", "prompt": "A cat", "params": {}, "request_fields": []},
        "media": [],
        "scope": SCOPE.to_dict(),
        "credential_ref": "credential-sync",
        "execution_kind": "sync_artifact",
        "media_kind": "image",
        "output_ref": "assets/atelier/out.png",
    }
    payload = {
        "schema_version": 2,
        "phase": "submitting",
        **identity,
        "fingerprint": _fingerprint(identity),
        "handle": None,
        "observation": None,
        "prepared_artifact": sync_proof(),
        "artifact": None,
    }
    payload.update(changes)
    return payload


@pytest.mark.parametrize("path", [
    "playground/out.png", "playground/images/nested/out.png", "playground/images/.hidden.png",
    "playground/images/./out.png", "playground/images//out.png", "playground/images/out.jpg",
    "playground/images/out.png?query=1", "playground/images/out\\name.png", "uploads/out.png",
])
def test_playground_image_record_rejects_unpublishable_paths(path):
    payload = sync_record_payload(output_ref=path, prepared_artifact=sync_proof(relative_path=path))
    with pytest.raises(ValueError):
        ProviderExecutionRecord(**payload)


def test_playground_image_record_preserves_scoped_prepared_proof():
    path = "playground/images/request-1.png"
    payload = sync_record_payload(output_ref=path, prepared_artifact=sync_proof(relative_path=path))
    record = ProviderExecutionRecord(**payload)
    assert record.prepared_artifact is not None
    assert record.output_ref == record.prepared_artifact["relative_path"] == path


def test_legacy_v1_dump_and_fingerprint_stay_byte_identical():
    payload = legacy_record_payload()
    record = ProviderExecutionRecord(**payload)

    dumped = record.model_dump(mode="json")

    assert dumped == payload
    assert serialize_json(dumped) == serialize_json(payload)
    _verify_record_fingerprint(record)


def test_legacy_v1_dump_options_preserve_existing_shape_without_key_errors():
    record = ProviderExecutionRecord(**legacy_record_payload())

    assert record.model_dump(mode="json", exclude_none=True) == {
        "schema_version": 1,
        "attempt_id": IDENTITY["attempt_id"],
        "phase": "accepted",
        "provider_model_id": IDENTITY["provider_model_id"],
        "intent": IDENTITY["intent"],
        "media": IDENTITY["media"],
        "scope": IDENTITY["scope"],
        "credential_ref": IDENTITY["credential_ref"],
        "fingerprint": _fingerprint(IDENTITY),
    }
    assert record.model_dump(mode="json", include={"schema_version", "attempt_id"}) == {
        "schema_version": 1,
        "attempt_id": IDENTITY["attempt_id"],
    }
    assert record.model_dump(mode="json", exclude={"schema_version", "handle"}) == {
        "attempt_id": IDENTITY["attempt_id"],
        "phase": "accepted",
        "provider_model_id": IDENTITY["provider_model_id"],
        "intent": IDENTITY["intent"],
        "media": IDENTITY["media"],
        "scope": IDENTITY["scope"],
        "credential_ref": IDENTITY["credential_ref"],
        "fingerprint": _fingerprint(IDENTITY),
        "observation": None,
        "prepared_artifact": None,
        "artifact": None,
    }
    assert "execution_kind" not in record.model_dump(mode="json", exclude={"schema_version"})
    assert "media_kind" not in record.model_dump(mode="json", exclude_unset=True)
    assert "output_ref" not in record.model_dump(mode="json", exclude_defaults=True)


def test_legacy_v1_still_requires_handle_backed_artifact_proof():
    proof = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": HANDLE,
        "relative_path": "video/out.mp4",
        "sha256": HASH,
        "size_bytes": 123,
    }
    valid = ProviderExecutionRecord(**legacy_record_payload(phase="submitted", handle=HANDLE, prepared_artifact=proof))
    assert valid.prepared_artifact == proof

    with pytest.raises(ValueError, match="receipt|handle"):
        ProviderExecutionRecord(**legacy_record_payload(phase="submitted", handle=None, prepared_artifact={**proof, "handle": None}))



def test_common_fingerprint_verifier_accepts_v2_identity_and_rejects_tamper():
    record = ProviderExecutionRecord(**sync_record_payload())

    _verify_record_fingerprint(record)

    tampered = ProviderExecutionRecord(**sync_record_payload(fingerprint="b" * 64))
    with pytest.raises(ProviderRecoveryError, match="fingerprint"):
        _verify_record_fingerprint(tampered)


def test_sync_artifact_v2_completed_requires_prepared_checkpoint_equal_to_artifact():
    with pytest.raises(ValueError, match="prepared"):
        ProviderExecutionRecord(**sync_record_payload(phase="completed", prepared_artifact=None, artifact=sync_proof()))
    with pytest.raises(ValueError, match="changed"):
        ProviderExecutionRecord(**sync_record_payload(
            phase="completed",
            artifact={**sync_proof(), "sha256": "b" * 64},
        ))


def test_sync_artifact_proof_schema_version_must_be_exact_int_two():
    with pytest.raises(ValueError, match="schema"):
        ProviderExecutionRecord(**sync_record_payload(prepared_artifact={**sync_proof(), "schema_version": 2.0}))


def test_artifact_proof_schema_must_match_record_schema_without_key_error():
    legacy_proof = {
        "schema_version": 1,
        "attempt_id": "attempt-sync",
        "handle": HANDLE,
        "relative_path": "video/out.mp4",
        "sha256": HASH,
        "size_bytes": 123,
    }

    with pytest.raises(ValueError, match="schema"):
        ProviderExecutionRecord(**legacy_record_payload(handle=HANDLE, prepared_artifact=sync_proof(attempt_id="attempt-1")))
    with pytest.raises(ValueError, match="schema"):
        ProviderExecutionRecord(**sync_record_payload(prepared_artifact=legacy_proof))


def test_sync_artifact_v2_allows_handleless_kind_proof_without_polling_fields():
    record = ProviderExecutionRecord(**sync_record_payload())

    assert record.schema_version == 2
    assert record.execution_kind == "sync_artifact"
    assert record.media_kind == "image"
    assert record.prepared_artifact == sync_proof()
    assert record.handle is None and record.observation is None


@pytest.mark.parametrize(
    "media_kind,relative_path",
    [
        ("image", "audio/atelier/out.mp3"),
        ("image", "assets/out.png"),
        ("image", "assets/atelier/out.mp4"),
        ("audio", "assets/atelier/out.png"),
        ("audio", "audio/out.mp3"),
        ("audio", "audio/atelier/out.png"),
    ],
)
def test_sync_artifact_v2_rejects_wrong_root_extension_or_kind(media_kind, relative_path):
    with pytest.raises(ValueError, match="artifact"):
        ProviderExecutionRecord(**sync_record_payload(
            media_kind=media_kind,
            output_ref=relative_path,
            prepared_artifact=sync_proof(media_kind=media_kind, relative_path=relative_path),
        ))


def test_sync_artifact_v2_rejects_mismatched_proof_identity():
    with pytest.raises(ValueError, match="attempt"):
        ProviderExecutionRecord(**sync_record_payload(prepared_artifact=sync_proof(attempt_id="other")))
    with pytest.raises(ValueError, match="output"):
        ProviderExecutionRecord(**sync_record_payload(output_ref="assets/atelier/other.png"))
    with pytest.raises(ValueError, match="hash"):
        ProviderExecutionRecord(**sync_record_payload(prepared_artifact={**sync_proof(), "sha256": "z" * 64}))


def test_sync_artifact_v2_completed_requires_durable_artifact_proof_and_forbids_provider_handle():
    with pytest.raises(ValueError, match="[Cc]ompleted"):
        ProviderExecutionRecord(**sync_record_payload(phase="completed", prepared_artifact=None, artifact=None))
    with pytest.raises(ValueError, match="handle"):
        ProviderExecutionRecord(**sync_record_payload(handle=HANDLE))
    with pytest.raises(ValueError, match="observation"):
        ProviderExecutionRecord(**sync_record_payload(observation={"status": "succeeded"}))

    completed = ProviderExecutionRecord(**sync_record_payload(phase="completed", artifact=sync_proof()))
    assert completed.artifact == sync_proof()


def test_media_candidate_v2_is_strict_and_imports_cannot_carry_authority():
    completed = MediaGenerationCandidateRecord(
        schema_version=2,
        execution_contract="atelier-provider-media-v2",
        id="candidate-1",
        media_kind="image",
        status="completed",
        media_url="assets/atelier/out.png",
        authority="provider",
        attempt_id="attempt-sync",
        execution_fingerprint=HASH,
        error_code=None,
    )
    assert completed.media_url == "assets/atelier/out.png"

    with pytest.raises(ValueError, match="media_url"):
        MediaGenerationCandidateRecord(
            schema_version=2,
            execution_contract="atelier-provider-media-v2",
            id="candidate-2",
            media_kind="audio",
            status="processing",
            media_url="audio/atelier/out.mp3",
            authority="provider",
            attempt_id="attempt-sync",
            execution_fingerprint=HASH,
            error_code=None,
        )
    with pytest.raises(ValueError, match="authority"):
        MediaGenerationCandidateRecord(
            schema_version=2,
            execution_contract="atelier-provider-media-v2",
            id="candidate-3",
            media_kind="image",
            status="completed",
            media_url="assets/imported/imported-project/atelier/out.png",
            authority="imported",
            attempt_id="attempt-sync",
            execution_fingerprint=HASH,
            error_code=None,
        )


@pytest.mark.parametrize(
    "schema_version",
    [2.0, True],
)
def test_media_candidate_schema_version_is_exact_int_two(schema_version):
    with pytest.raises(ValueError, match="schema"):
        MediaGenerationCandidateRecord(
            schema_version=schema_version,
            execution_contract="atelier-provider-media-v2",
            id="candidate-schema",
            media_kind="image",
            status="completed",
            media_url="assets/atelier/out.png",
            authority="imported",
            attempt_id=None,
            execution_fingerprint=None,
            error_code=None,
        )


@pytest.mark.parametrize(
    "media_kind,media_url",
    [
        ("image", "https://example.invalid/out.png"),
        ("image", "audio/atelier/out.mp3"),
        ("image", "assets/atelier/out.exe"),
        ("image", "audio/imported/project/out.mp3"),
        ("audio", "assets/atelier/out.png"),
        ("audio", "assets/imported/project/out.png"),
        ("audio", "audio/atelier/out.exe"),
        ("audio", "uploads/imported.wav"),
    ],
)
def test_media_candidate_url_is_managed_and_kind_aware(media_kind, media_url):
    with pytest.raises(ValueError, match="media_url"):
        MediaGenerationCandidateRecord(
            schema_version=2,
            execution_contract="atelier-provider-media-v2",
            id="candidate-url",
            media_kind=media_kind,
            status="completed",
            media_url=media_url,
            authority="imported",
            attempt_id=None,
            execution_fingerprint=None,
            error_code=None,
        )


def test_imported_media_candidate_is_completed_inert_only():
    with pytest.raises(ValueError, match="Imported"):
        MediaGenerationCandidateRecord(
            schema_version=2,
            execution_contract="atelier-provider-media-v2",
            id="candidate-imported-pending",
            media_kind="image",
            status="pending",
            authority="imported",
            media_url=None,
            attempt_id=None,
            execution_fingerprint=None,
            error_code=None,
        )

    imported = MediaGenerationCandidateRecord(
        schema_version=2,
        execution_contract="atelier-provider-media-v2",
        id="candidate-imported-upload",
        media_kind="audio",
        status="completed",
        media_url="audio/imported/imported-project/atelier/imported.wav",
        authority="imported",
        attempt_id=None,
        execution_fingerprint=None,
        error_code=None,
    )
    assert imported.media_url == "audio/imported/imported-project/atelier/imported.wav"
