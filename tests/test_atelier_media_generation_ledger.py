"""Dormant image/audio generation ledgers share the Atelier generation ledger safely."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

import pytest

from src.apps.atelier.models import (
    AtelierGenerationAttempt,
    AtelierMediaGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierNode,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.models import provider_recovery
from src.audio.tts import PreparedTTS
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_intents import prepared_tts_intent_payload
from src.models.provider_tasks import ProviderScope
from src.utils.json_storage import StorageError, serialize_json

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _scope() -> dict:
    return ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope",
        base_url="https://dashscope.aliyuncs.com",
        region="cn-beijing",
    ).to_dict()


def _intent(*, media_kind: str) -> dict:
    if media_kind == "audio":
        return prepared_tts_intent_payload(
            PreparedTTS(
                "cosyvoice",
                (
                    ("model", "cosyvoice-v3-flash"),
                    ("voice", "longanyang"),
                    ("text", "hello world"),
                    ("speech_rate", 1.0),
                    ("pitch_rate", 1.0),
                    ("volume", 50),
                ),
            ),
            request_fields=("voice", "text"),
        )
    return {
        "rules_version": "generation-intent-v1",
        "model_id": "qwen/qwen-image#image",
        "catalog_mode": "image",
        "mode": "t2i",
        "prompt": "A moonlit fox",
        "params": {},
        "sources": {},
        "request_fields": [],
        "preset_id": None,
        "preset_version": None,
    }


def _proof(*, media_kind: str, attempt_id: str, output_ref: str, sha: str = HASH_A) -> dict:
    return {
        "schema_version": 2,
        "contract_version": "provider-sync-artifact-v1",
        "attempt_id": attempt_id,
        "media_kind": media_kind,
        "relative_path": output_ref,
        "output_ref": output_ref,
        "sha256": sha,
        "size_bytes": 123,
    }


def _provider_record(*, media_kind: str = "image", attempt_id: str = "attempt-image", output_ref: str = "assets/atelier/out.png") -> dict:
    identity = {
        "attempt_id": attempt_id,
        "provider_model_id": "qwen-image" if media_kind == "image" else "cosyvoice-v3-flash",
        "intent": _intent(media_kind=media_kind),
        "media": [],
        "scope": _scope(),
        "credential_ref": "credential-media",
        "execution_kind": "sync_artifact",
        "media_kind": media_kind,
        "output_ref": output_ref,
    }
    proof = _proof(media_kind=media_kind, attempt_id=attempt_id, output_ref=output_ref)
    return ProviderExecutionRecord(
        schema_version=2,
        phase="completed",
        **identity,
        fingerprint=provider_recovery._fingerprint(identity),
        prepared_artifact=proof,
        artifact=proof,
    ).model_dump(mode="json")


def _media_candidate(*, media_kind: str = "image", candidate_id: str = "candidate-image", attempt_id: str = "attempt-image", media_url: str = "assets/atelier/out.png", fingerprint: str | None = None) -> dict:
    if fingerprint is None:
        fingerprint = _provider_record(media_kind=media_kind, attempt_id=attempt_id, output_ref=media_url)["fingerprint"]
    return {
        "schema_version": 2,
        "execution_contract": "atelier-provider-media-v2",
        "id": candidate_id,
        "media_kind": media_kind,
        "status": "completed",
        "media_url": media_url,
        "error_code": None,
        "authority": "provider",
        "attempt_id": attempt_id,
        "execution_fingerprint": fingerprint,
    }


def _media_attempt(*, media_kind: str = "image", node_id: str = "image-node", candidate_id: str = "candidate-image", attempt_id: str = "attempt-image", output_ref: str = "assets/atelier/out.png") -> dict:
    return {
        "schema_version": 1,
        "execution_contract": "atelier-provider-media-v2",
        "project_id": "p1",
        "node_id": node_id,
        "candidate_id": candidate_id,
        "attempt_id": attempt_id,
        "attempt_revision": 0,
        "media_kind": media_kind,
        "status": "completed",
        "error_code": None,
        "output_ref": output_ref,
        "provider_execution": _provider_record(media_kind=media_kind, attempt_id=attempt_id, output_ref=output_ref),
        "created_at": 10.0,
        "updated_at": 10.0,
    }


def _media_receipt(*, media_kind: str = "image", node_id: str = "image-node", candidate_id: str = "candidate-image", attempt_id: str = "attempt-image", fingerprint: str | None = None, client_request_id: str = "request-image") -> dict:
    output_ref = "assets/atelier/out.png" if media_kind == "image" else "audio/atelier/out.mp3"
    if fingerprint is None:
        fingerprint = _provider_record(media_kind=media_kind, attempt_id=attempt_id, output_ref=output_ref)["fingerprint"]
    return {
        "schema_version": 1,
        "project_id": "p1",
        "entrypoint": "atelier.provider_image.v1" if media_kind == "image" else "atelier.provider_audio.v1",
        "request_contract_version": "request-json-v1",
        "client_request_id": client_request_id,
        "request_fingerprint": HASH_B,
        "node_id": node_id,
        "candidate_id": candidate_id,
        "attempt_id": attempt_id,
        "execution_fingerprint": fingerprint,
        "base_revision": 0,
        "committed_revision": 1,
        "created_at": 10.0,
    }


def _project_payload(*, media_kind: str = "image") -> dict:
    node_id = "image-node" if media_kind == "image" else "audio-node"
    candidate_id = f"candidate-{media_kind}"
    attempt_id = f"attempt-{media_kind}"
    output_ref = "assets/atelier/out.png" if media_kind == "image" else "audio/atelier/out.mp3"
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        revision=1,
        nodes=[
            AtelierNode(
                id=node_id,
                project_id="p1",
                type=media_kind,
                data={"candidates": [_media_candidate(
                    media_kind=media_kind,
                    candidate_id=candidate_id,
                    attempt_id=attempt_id,
                    media_url=output_ref,
                )]},
                created_at=1.0,
                updated_at=1.0,
            )
        ],
    ).model_dump(mode="json")
    project["generation_attempts"] = [_media_attempt(
        media_kind=media_kind,
        node_id=node_id,
        candidate_id=candidate_id,
        attempt_id=attempt_id,
        output_ref=output_ref,
    )]
    project["generation_request_receipts"] = [_media_receipt(
        media_kind=media_kind,
        node_id=node_id,
        candidate_id=candidate_id,
        attempt_id=attempt_id,
    )]
    return project


def _video_project_dump() -> dict:
    intent = {
        "rules_version": "generation-intent-v1",
        "model_id": "wan/wan2.7-video#i2v",
        "catalog_mode": "i2v",
        "mode": "i2v",
        "prompt": "A slow sunrise",
        "params": {"duration": 5},
        "sources": {},
        "request_fields": ["duration"],
        "preset_id": None,
        "preset_version": None,
    }
    identity = {
        "attempt_id": "attempt-video",
        "provider_model_id": "wan2.7-i2v",
        "intent": intent,
        "media": [],
        "scope": _scope(),
        "credential_ref": "credential-video",
    }
    record = ProviderExecutionRecord(
        schema_version=1,
        attempt_id="attempt-video",
        phase="accepted",
        provider_model_id="wan2.7-i2v",
        intent=intent,
        media=[],
        scope=_scope(),
        credential_ref="credential-video",
        fingerprint=provider_recovery._fingerprint(identity),
    )
    attempt = AtelierGenerationAttempt(
        project_id="p1",
        node_id="video-node",
        candidate_id="candidate-video",
        attempt_id="attempt-video",
        attempt_revision=0,
        status="pending",
        error_code=None,
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="source", media_url="uploads/source.png"),
        output_ref=None,
        provider_execution=record,
        created_at=10.0,
        updated_at=10.0,
    )
    return attempt.model_dump(mode="json")


def _sync_provider_record_fingerprint(record: dict) -> str:
    identity = {
        "attempt_id": record["attempt_id"],
        "provider_model_id": record["provider_model_id"],
        "intent": record["intent"],
        "media": record["media"],
        "scope": record["scope"],
        "credential_ref": record["credential_ref"],
        "execution_kind": record["execution_kind"],
        "media_kind": record["media_kind"],
        "output_ref": record["output_ref"],
    }
    record["fingerprint"] = provider_recovery._fingerprint(identity)
    return record["fingerprint"]


def _set_provider_record_fingerprint(payload: dict, fingerprint: str) -> None:
    payload["generation_request_receipts"][0]["execution_fingerprint"] = fingerprint
    payload["nodes"][0]["data"]["candidates"][0]["execution_fingerprint"] = fingerprint


def _write_raw(root: Path, payload: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "atelier_projects.json").write_text(json.dumps({"p1": payload}), encoding="utf-8")


def test_legacy_video_attempt_dump_stays_byte_stable_without_media_fields():
    before = _video_project_dump()
    after = AtelierGenerationAttempt.model_validate(before).model_dump(mode="json")

    assert serialize_json(after) == serialize_json(before)
    assert "execution_contract" not in after
    assert "media_kind" not in after


def test_media_attempt_is_a_separate_discriminator_not_a_legacy_optional_bag():
    media = AtelierMediaGenerationAttempt.model_validate(_media_attempt())

    assert media.execution_contract == "atelier-provider-media-v2"
    assert media.model_dump(mode="json", include={"media_kind"}) == {"media_kind": "image"}
    without_contract = media.model_dump(mode="json", exclude={"execution_contract"})
    assert without_contract["media_kind"] == "image"
    assert without_contract["provider_execution"]["schema_version"] == 2

    legacy = _video_project_dump()
    legacy["schema_version"] = 2
    with pytest.raises(ValueError, match="requires authorization"):
        AtelierGenerationAttempt.model_validate(legacy)

    media_as_legacy = _media_attempt()
    with pytest.raises(ValueError):
        AtelierGenerationAttempt.model_validate(media_as_legacy)


@pytest.mark.parametrize("media_kind", ["image", "audio"])
def test_provider_media_attempt_candidate_and_receipt_roundtrip(tmp_path, media_kind):
    payload = _project_payload(media_kind=media_kind)
    _write_raw(tmp_path, payload)

    loaded = AtelierStorage(tmp_path).get_project("p1")

    assert loaded.generation_attempts[0].provider_execution.schema_version == 2
    assert loaded.generation_request_receipts[0].entrypoint == f"atelier.provider_{media_kind}.v1"
    assert loaded.nodes[0].data["candidates"][0]["execution_contract"] == "atelier-provider-media-v2"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["generation_attempts"][0].__setitem__("media_kind", "audio"),
        lambda p: p["generation_attempts"][0].__setitem__("output_ref", "assets/atelier/other.png"),
        lambda p: p["nodes"][0]["data"]["candidates"][0].__setitem__("media_url", "assets/atelier/other.png"),
        lambda p: p["generation_request_receipts"][0].__setitem__("execution_fingerprint", HASH_C),
        lambda p: p["generation_attempts"][0]["provider_execution"].__setitem__("fingerprint", HASH_C),
        lambda p: p["generation_attempts"][0]["provider_execution"]["artifact"].__setitem__("sha256", HASH_C),
        lambda p: p["generation_attempts"][0]["provider_execution"]["artifact"].__setitem__("schema_version", 1),
        lambda p: p["generation_request_receipts"][0].__setitem__("candidate_id", "other-candidate"),
        lambda p: p["nodes"][0].__setitem__("type", "video"),
    ],
)
def test_media_generation_ledger_tampering_is_rejected(tmp_path, mutate):
    payload = _project_payload()
    mutate(payload)
    _write_raw(tmp_path, payload)

    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)


def test_imported_media_candidates_are_completed_and_inert(tmp_path):
    payload = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        revision=1,
        nodes=[
            AtelierNode(
                id="image-node",
                project_id="p1",
                type="image",
                data={"candidates": [{
                    "schema_version": 2,
                    "execution_contract": "atelier-provider-media-v2",
                    "id": "imported-1",
                    "media_kind": "image",
                    "status": "completed",
                    "media_url": "assets/imported/p1/atelier/imported.png",
                    "error_code": None,
                    "authority": "imported",
                    "attempt_id": None,
                    "execution_fingerprint": None,
                }]},
                created_at=1.0,
                updated_at=1.0,
            )
        ],
    ).model_dump(mode="json")
    _write_raw(tmp_path / "valid", payload)
    assert AtelierStorage(tmp_path / "valid").get_project("p1").generation_attempts == []

    for index, mutate in enumerate([
        lambda c: c.__setitem__("status", "pending"),
        lambda c: c.__setitem__("attempt_id", "attempt-imported"),
        lambda c: c.__setitem__("execution_fingerprint", HASH_A),
        lambda c: c.__setitem__("provider_handle", {"task_id": "remote"}),
        lambda c: c.__setitem__("agent_authorization_ref", {"approval_id": "approval"}),
    ]):
        invalid = deepcopy(payload)
        mutate(invalid["nodes"][0]["data"]["candidates"][0])
        _write_raw(tmp_path / f"invalid-{index}", invalid)
        with pytest.raises(StorageError):
            AtelierStorage(tmp_path / f"invalid-{index}")

    wrong_owner = deepcopy(payload)
    wrong_owner["nodes"][0]["type"] = "audio"
    _write_raw(tmp_path / "invalid-owner-kind", wrong_owner)
    with pytest.raises(StorageError, match="owner node media kind"):
        AtelierStorage(tmp_path / "invalid-owner-kind")


def test_audio_ledger_rejects_fake_tts_catalog_generation_intent(tmp_path):
    payload = _project_payload(media_kind="audio")
    fake_intent = {
        "rules_version": "generation-intent-v1",
        "model_id": "qwen/qwen-tts#tts",
        "catalog_mode": "tts",
        "mode": "tts",
        "prompt": "hello world",
        "params": {},
        "sources": {},
        "request_fields": [],
        "preset_id": None,
        "preset_version": None,
    }
    record = payload["generation_attempts"][0]["provider_execution"]
    record["provider_model_id"] = "qwen-tts"
    record["intent"] = fake_intent
    _set_provider_record_fingerprint(payload, _sync_provider_record_fingerprint(record))
    _write_raw(tmp_path, payload)

    with pytest.raises(StorageError, match="provider_tts_intent_invalid"):
        AtelierStorage(tmp_path)

def test_needs_confirmation_media_attempt_and_candidate_error_are_consistent(tmp_path):
    payload = _project_payload()
    attempt = payload["generation_attempts"][0]
    candidate = payload["nodes"][0]["data"]["candidates"][0]
    attempt["status"] = "needs_confirmation"
    attempt["error_code"] = None
    attempt["output_ref"] = None
    attempt["provider_execution"]["phase"] = "submitting"
    attempt["provider_execution"]["prepared_artifact"] = None
    attempt["provider_execution"]["artifact"] = None
    candidate["status"] = "needs_confirmation"
    candidate["media_url"] = None
    candidate["error_code"] = None
    _write_raw(tmp_path / "valid-needs-confirmation", payload)

    loaded = AtelierStorage(tmp_path / "valid-needs-confirmation").get_project("p1")
    assert loaded.generation_attempts[0].error_code is None
    assert loaded.nodes[0].data["candidates"][0]["error_code"] is None

    with_error = deepcopy(payload)
    with_error["generation_attempts"][0]["error_code"] = "provider_sync_result_unknown"
    with_error["nodes"][0]["data"]["candidates"][0]["error_code"] = "provider_sync_result_unknown"
    _write_raw(tmp_path / "valid-needs-confirmation-error", with_error)
    loaded_error = AtelierStorage(tmp_path / "valid-needs-confirmation-error").get_project("p1")
    assert loaded_error.generation_attempts[0].error_code == "provider_sync_result_unknown"
    assert loaded_error.nodes[0].data["candidates"][0]["error_code"] == "provider_sync_result_unknown"

    attempt_error = deepcopy(payload)
    attempt_error["generation_attempts"][0]["error_code"] = "provider_sync_result_unknown"
    _write_raw(tmp_path / "invalid-attempt-error", attempt_error)
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path / "invalid-attempt-error")

    candidate_error = deepcopy(payload)
    candidate_error["nodes"][0]["data"]["candidates"][0]["error_code"] = "provider_sync_result_unknown"
    _write_raw(tmp_path / "invalid-candidate-error", candidate_error)
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path / "invalid-candidate-error")


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_media_terminal_success_or_cancel_cannot_carry_error_code_even_when_projection_matches(tmp_path, status):
    payload = _project_payload()
    attempt = payload["generation_attempts"][0]
    candidate = payload["nodes"][0]["data"]["candidates"][0]
    if status == "cancelled":
        attempt["status"] = "cancelled"
        attempt["output_ref"] = None
        attempt["provider_execution"]["phase"] = "cancelled"
        attempt["provider_execution"]["prepared_artifact"] = None
        attempt["provider_execution"]["artifact"] = None
        candidate["status"] = "cancelled"
        candidate["media_url"] = None
    attempt["error_code"] = "stale_terminal_error"
    candidate["error_code"] = "stale_terminal_error"
    _write_raw(tmp_path, payload)

    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)


def test_product_command_cannot_mutate_media_candidate_projection(tmp_path):
    _write_raw(tmp_path, _project_payload())
    store = AtelierStorage(tmp_path)

    with pytest.raises(StorageError, match="generation candidate projection|media generation candidate"):
        store.apply_project_command(
            "p1",
            expected_revision=1,
            command=lambda project: project.nodes[0].data["candidates"][0].update({"updated_at": 99.0}),
        )

    with pytest.raises(StorageError, match="generation candidate projection|media generation candidate"):
        store.apply_project_command(
            "p1",
            expected_revision=1,
            command=lambda project: project.nodes[0].data.__setitem__("candidates", []),
        )

    with pytest.raises(StorageError, match="generation candidate projection|media generation candidate"):
        store.apply_project_command(
            "p1",
            expected_revision=1,
            command=lambda project: project.nodes[0].data["candidates"].append({
                "schema_version": 2,
                "execution_contract": "atelier-provider-media-v2",
                "id": "imported-late",
                "media_kind": "image",
                "status": "completed",
                "media_url": "assets/imported/p1/atelier/imported-late.png",
                "error_code": None,
                "authority": "imported",
                "attempt_id": None,
                "execution_fingerprint": None,
            }),
        )


@pytest.mark.parametrize(
    ("mode", "media"),
    [
        ("t2i", [{"schema_version": 1, "sha256": HASH_A, "size_bytes": 123, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png"}]),
        ("i2i", []),
    ],
)
def test_persisted_image_sync_artifact_record_enforces_mode_media_matrix(tmp_path, mode, media):
    payload = _project_payload(media_kind="image")
    record = payload["generation_attempts"][0]["provider_execution"]
    record["intent"]["mode"] = mode
    record["media"] = media
    _set_provider_record_fingerprint(payload, _sync_provider_record_fingerprint(record))
    _write_raw(tmp_path, payload)

    with pytest.raises(StorageError, match="provider_image_media_invalid"):
        AtelierStorage(tmp_path)


@pytest.mark.parametrize(
    "bad_media",
    [
        {"schema_version": 999, "sha256": HASH_A, "size_bytes": 123, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png"},
        {"schema_version": 1, "sha256": "not-a-hash", "size_bytes": 123, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png"},
        {"schema_version": 1, "sha256": HASH_A, "size_bytes": -1, "role": "reference_image", "ordinal": 0, "declared_media_type": "image/png"},
        {"schema_version": 1, "sha256": HASH_A, "size_bytes": 123, "role": "reference_audio", "ordinal": 0, "declared_media_type": "audio/wav"},
        {"schema_version": 1, "sha256": HASH_A, "size_bytes": 123, "role": "reference_image", "ordinal": -5, "declared_media_type": "image/png"},
    ],
)
def test_persisted_image_sync_artifact_record_rejects_malformed_media_even_with_matching_fingerprint(tmp_path, bad_media):
    payload = _project_payload(media_kind="image")
    record = payload["generation_attempts"][0]["provider_execution"]
    record["intent"]["mode"] = "i2i"
    record["media"] = [bad_media]
    _set_provider_record_fingerprint(payload, _sync_provider_record_fingerprint(record))
    _write_raw(tmp_path, payload)

    with pytest.raises(StorageError, match="provider_.*(media|image)"):
        AtelierStorage(tmp_path)


def _pending_media_project_payload(*, media_kind: str = "image") -> dict:
    payload = _project_payload(media_kind=media_kind)
    attempt = payload["generation_attempts"][0]
    candidate = payload["nodes"][0]["data"]["candidates"][0]
    attempt["status"] = "processing"
    attempt["output_ref"] = None
    attempt["provider_execution"]["phase"] = "submitting"
    attempt["provider_execution"]["artifact"] = None
    candidate["status"] = "processing"
    candidate["media_url"] = None
    return payload


def _complete_media_attempt(attempt: AtelierMediaGenerationAttempt) -> None:
    proof = attempt.provider_execution.prepared_artifact
    attempt.status = "completed"
    attempt.output_ref = attempt.provider_execution.output_ref
    attempt.provider_execution = ProviderExecutionRecord(**{
        **attempt.provider_execution.model_dump(mode="json"),
        "phase": "completed",
        "artifact": proof,
    })
    attempt.updated_at += 1.0


def test_sync_artifact_transition_allows_submitting_to_completed_after_prepared_checkpoint():
    before = AtelierMediaGenerationAttempt.model_validate(_pending_media_project_payload()["generation_attempts"][0])
    after = before.model_copy(deep=True)
    _complete_media_attempt(after)

    from src.apps.atelier.generation_ledger import validate_generation_transition

    validate_generation_transition(before, after, StorageError)


def test_sync_artifact_transition_rejects_completion_without_prepared_checkpoint():
    before_payload = _pending_media_project_payload()["generation_attempts"][0]
    before_payload["provider_execution"]["prepared_artifact"] = None
    before = AtelierMediaGenerationAttempt.model_validate(before_payload)
    after = before.model_copy(deep=True)
    proof = _proof(media_kind="image", attempt_id=after.attempt_id, output_ref=after.provider_execution.output_ref)
    after.status = "completed"
    after.output_ref = after.provider_execution.output_ref
    after.provider_execution = ProviderExecutionRecord(**{
        **after.provider_execution.model_dump(mode="json"),
        "phase": "completed",
        "prepared_artifact": proof,
        "artifact": proof,
    })

    from src.apps.atelier.generation_ledger import validate_generation_transition

    with pytest.raises(StorageError, match="saved publication proof"):
        validate_generation_transition(before, after, StorageError)


def test_sync_artifact_transition_rejects_accepted_to_completed_skip():
    before_payload = _project_payload()["generation_attempts"][0]
    before_payload["status"] = "processing"
    before_payload["output_ref"] = None
    before_payload["provider_execution"]["phase"] = "accepted"
    before_payload["provider_execution"]["prepared_artifact"] = None
    before_payload["provider_execution"]["artifact"] = None
    before = AtelierMediaGenerationAttempt.model_validate(before_payload)
    after = before.model_copy(deep=True)
    proof = _proof(media_kind="image", attempt_id=after.attempt_id, output_ref=after.provider_execution.output_ref)
    after.status = "completed"
    after.output_ref = after.provider_execution.output_ref
    after.provider_execution = ProviderExecutionRecord(**{
        **after.provider_execution.model_dump(mode="json"),
        "phase": "completed",
        "prepared_artifact": proof,
        "artifact": proof,
    })

    from src.apps.atelier.generation_ledger import validate_generation_transition

    with pytest.raises(StorageError, match="roll back or skip"):
        validate_generation_transition(before, after, StorageError)


def test_legacy_video_transition_still_cannot_skip_submitting_to_completed():
    before_payload = _video_project_dump()
    before_payload["status"] = "processing"
    before_payload["provider_execution"]["phase"] = "submitting"
    before = AtelierGenerationAttempt.model_validate(before_payload)
    proof = {
        "schema_version": 1,
        "attempt_id": before.attempt_id,
        "handle": {
            "provider_model_id": "wan2.7-i2v",
            "task_id": "task-video",
            "scope": _scope(),
            "credential_ref": "credential-video",
            "request_id": "request-video",
            "query_capability": "by_task_id",
            "submitted_at": 1.0,
        },
        "relative_path": "video/out.mp4",
        "sha256": HASH_A,
        "size_bytes": 123,
    }
    after = before.model_copy(deep=True)
    after.status = "completed"
    after.output_ref = "video/out.mp4"
    after.provider_execution = ProviderExecutionRecord(**{
        **after.provider_execution.model_dump(mode="json"),
        "phase": "completed",
        "handle": proof["handle"],
        "prepared_artifact": proof,
        "artifact": proof,
    })

    from src.apps.atelier.generation_ledger import validate_generation_transition

    with pytest.raises(StorageError, match="roll back or skip"):
        validate_generation_transition(before, after, StorageError)


@pytest.mark.parametrize(
    ("media_kind", "expected_ref"),
    [("image", "assets/atelier/out.png"), ("audio", "audio/atelier/out.mp3")],
)
def test_storage_persists_sync_artifact_completion_and_updates_projection(tmp_path, media_kind, expected_ref):
    payload = _pending_media_project_payload(media_kind=media_kind)
    _write_raw(tmp_path, payload)
    store = AtelierStorage(tmp_path)
    try:
        attempt = store.get_project("p1").generation_attempts[0]

        project, updated = store.update_generation_attempt(
            "p1",
            attempt_id=attempt.attempt_id,
            expected_attempt_revision=attempt.attempt_revision,
            execution_fingerprint=attempt.provider_execution.fingerprint,
            command=_complete_media_attempt,
        )

        candidate = project.nodes[0].data["candidates"][0]
        assert updated.status == "completed"
        assert updated.attempt_revision == 1
        assert updated.provider_execution.artifact == updated.provider_execution.prepared_artifact
        assert candidate["status"] == "completed"
        assert candidate["media_url"] == expected_ref
        assert "video_url" not in candidate
        assert "updated_at" not in candidate
    finally:
        store.close()
    reloaded = AtelierStorage(tmp_path)
    try:
        assert reloaded.get_project("p1").generation_attempts[0].status == "completed"
    finally:
        reloaded.close()


def test_storage_rejects_bad_sync_completion_without_mutating_project(tmp_path):
    payload = _pending_media_project_payload()
    _write_raw(tmp_path, payload)
    store = AtelierStorage(tmp_path)
    try:
        before = store.get_project("p1")
        attempt = before.generation_attempts[0]

        def bad_complete(item: AtelierMediaGenerationAttempt) -> None:
            item.status = "completed"
            item.output_ref = item.provider_execution.output_ref
            item.provider_execution = ProviderExecutionRecord(**{
                **item.provider_execution.model_dump(mode="json"),
                "prepared_artifact": None,
            })

        with pytest.raises(StorageError):
            store.update_generation_attempt(
                "p1",
                attempt_id=attempt.attempt_id,
                expected_attempt_revision=attempt.attempt_revision,
                execution_fingerprint=attempt.provider_execution.fingerprint,
                command=bad_complete,
            )

        assert store.get_project("p1").model_dump(mode="json") == before.model_dump(mode="json")
    finally:
        store.close()
