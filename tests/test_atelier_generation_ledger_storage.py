"""Atelier provider-generation ledger storage invariants."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from src.apps.atelier.models import (
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierGenerationRequestReceiptMember,
    AtelierNode,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.models import provider_recovery
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_tasks import ProviderObservation, ProviderTaskStatus
from src.utils.generation_intent import normalize_generation_intent
from src.utils.media_versions import MediaVersion
from src.utils.json_storage import StorageConflictError, StorageError


HASH_A = "a" * 64
HASH_B = "b" * 64


def _project(*, status: str = "pending", revision: int = 1) -> AtelierProject:
    node = AtelierNode(
        id="node-1",
        project_id="p1",
        type="video",
        data={
            "candidates": [
                {
                    "execution_contract": "atelier-provider-video-v1",
                    "id": "candidate-1",
                    "attempt_id": "attempt-1",
                    "media_kind": "video",
                    "status": status,
                    "video_url": "video/provider-attempt-1.mp4" if status == "completed" else None,
                    "error_code": None,
                    "created_at": 10.0,
                    "updated_at": 10.0,
                }
            ]
        },
        created_at=1.0,
        updated_at=1.0,
    )
    project = AtelierProject(id="p1", title="Canvas", created_at=1.0, updated_at=1.0, revision=revision, nodes=[node])
    project.generation_attempts.append(_attempt(status=status))
    project.generation_request_receipts.append(_receipt())
    return project


def _attempt(**overrides) -> AtelierGenerationAttempt:
    status = overrides.get("status", "pending")
    output_ref = "video/provider-attempt-1.mp4" if status == "completed" else None
    data = {
        "project_id": "p1",
        "node_id": "node-1",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "attempt_revision": 0,
        "status": status,
        "error_code": None,
        "first_frame_source": AtelierGenerationFirstFrameSource(node_id="source-1", media_url="uploads/source.png"),
        "output_ref": output_ref,
        "provider_execution": _provider_record(phase="completed", artifact_path=output_ref)
        if status == "completed"
        else _provider_record(),
        "created_at": 10.0,
        "updated_at": 10.0,
    }
    data.update(overrides)
    return AtelierGenerationAttempt(**data)


def _receipt(**overrides) -> AtelierGenerationRequestReceipt:
    data = {
        "project_id": "p1",
        "entrypoint": "atelier.provider_video.v1",
        "request_contract_version": "request-json-v1",
        "client_request_id": "request-1",
        "request_fingerprint": HASH_B,
        "node_id": "node-1",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "execution_fingerprint": _provider_record().fingerprint,
        "base_revision": 0,
        "committed_revision": 1,
        "created_at": 10.0,
    }
    data.update(overrides)
    return AtelierGenerationRequestReceipt(**data)


def _handle() -> dict:
    return {
        "provider_model_id": "wan2.7-i2v",
        "task_id": "remote-task-1",
        "scope": _scope(),
        "credential_ref": "credential-ref",
        "request_id": "remote-request-1",
        "query_capability": "by_task_id",
        "schema_version": 1,
        "submitted_at": 10.0,
    }


def _scope() -> dict:
    return {
        "provider": "dashscope",
        "backend": "dashscope",
        "gateway": "dashscope_workspace",
        "base_url": "https://dashscope.aliyuncs.com",
        "region": "cn-beijing",
        "workspace_id": None,
        "account_identity": None,
    }


def _observation(status: ProviderTaskStatus) -> dict:
    return ProviderObservation(
        task_id="remote-task-1",
        status=status,
        raw_status=status.value.upper(),
        request_id="remote-request-1",
        video_url=None,
        error_code="REMOTE_FAILED" if status == ProviderTaskStatus.FAILED else None,
        source_handle=_provider_handle(),
    ).to_dict()


def _provider_handle():
    from src.models.provider_tasks import ProviderHandle, ProviderScope, QueryCapability

    scope = _scope()
    return ProviderHandle(
        provider_model_id="wan2.7-i2v",
        task_id="remote-task-1",
        scope=ProviderScope.from_dict(scope),
        credential_ref="credential-ref",
        request_id="remote-request-1",
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=10.0,
    )


def _provider_record(**overrides) -> ProviderExecutionRecord:
    artifact_path = overrides.pop("artifact_path", None)
    intent = normalize_generation_intent(
        {
            "model_id": "wan2.7-i2v",
            "mode": "i2v",
            "prompt": "A slow sunrise",
            "params": {
                "duration": 5,
                "resolution": "720p",
                "seed": 0,
                "prompt_extend": False,
                "negative_prompt": "",
                "watermark": False,
            },
        },
        allowed_modes={"i2v"},
        allowed_parameters={"duration", "resolution", "seed", "prompt_extend", "negative_prompt", "watermark"},
    )
    media = [asdict(MediaVersion("d" * 64, 100, "first_frame", 0, "image/png"))]
    scope = _scope()
    handle = _handle()
    attempt_id = overrides.get("attempt_id", "attempt-1")
    data = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "phase": "accepted",
        "provider_model_id": "wan2.7-i2v",
        "intent": intent.to_dict(),
        "media": media,
        "scope": scope,
        "credential_ref": "credential-ref",
        "fingerprint": provider_recovery._fingerprint({
            "attempt_id": attempt_id,
            "provider_model_id": "wan2.7-i2v",
            "intent": intent.to_dict(),
            "media": media,
            "scope": scope,
            "credential_ref": "credential-ref",
        }),
    }
    if artifact_path is not None:
        data["handle"] = handle
        data["artifact"] = {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "handle": handle,
            "relative_path": artifact_path,
            "sha256": "c" * 64,
            "size_bytes": 12,
        }
    data.update(overrides)
    return ProviderExecutionRecord(**data)


def _db_path(root: Path) -> Path:
    return root / "atelier_projects.json"


def _write_raw_project(root: Path, payload: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _db_path(root).write_text(json.dumps({"p1": payload}), encoding="utf-8")


def _store_with_project(root: Path, project: AtelierProject) -> AtelierStorage:
    _write_raw_project(root, project.model_dump(mode="json"))
    return AtelierStorage(root)


def test_legacy_project_without_generation_ledgers_reads_with_empty_defaults(tmp_path):
    payload = AtelierProject(id="p1", title="Canvas", created_at=1.0, updated_at=1.0).model_dump(mode="json")
    payload.pop("generation_attempts")
    payload.pop("generation_request_receipts")
    _write_raw_project(tmp_path, payload)

    store = AtelierStorage(tmp_path)
    project = store.get_project("p1")

    assert project.generation_attempts == []
    assert project.generation_request_receipts == []
    assert "generation_attempts" not in json.loads(_db_path(tmp_path).read_text(encoding="utf-8"))["p1"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda project: project.generation_attempts.clear(), "receipt points at missing generation attempt"),
        (lambda project: project.generation_request_receipts.clear(), "generation attempt has no request receipt"),
        (lambda project: project.generation_attempts.append(project.generation_attempts[0].model_copy(deep=True)), "Duplicate Atelier generation attempt id"),
        (lambda project: setattr(project.generation_attempts[0], "project_id", "other"), "generation attempt project_id does not match owner"),
        (lambda project: setattr(project.generation_attempts[0], "node_id", "missing"), "active generation attempt cannot be detached"),
        (lambda project: setattr(project.nodes[0], "type", "image"), "generation owner node must be video"),
        (lambda project: setattr(project.generation_attempts[0], "candidate_id", "missing"), "generation receipt execution fingerprint does not match"),
        (lambda project: setattr(project.generation_attempts[0], "attempt_id", "outer"), "provider execution attempt does not match"),
        (lambda project: setattr(project.generation_attempts[0].provider_execution, "fingerprint", HASH_B), "provider execution identity is invalid"),
        (lambda project: setattr(project.generation_attempts[0].provider_execution, "intent", {**project.generation_attempts[0].provider_execution.intent, "prompt": "tampered"}), "provider execution identity is invalid"),
        (lambda project: project.nodes[0].data["candidates"][0].update({"status": "completed", "video_url": None}), "completed candidate requires video_url"),
        (lambda project: project.nodes[0].data["candidates"][0].update({"video_url": "video/not-yet.mp4"}), "unfinished candidate video_url must be null"),
        (lambda project: project.nodes[0].data["candidates"][0].update({"handle": {"task_id": "remote"}}), "generation candidate fields are invalid"),
    ],
)
def test_generation_ledger_relationships_are_validated_on_load(tmp_path, mutate, message):
    project = _project()
    mutate(project)
    _write_raw_project(tmp_path, project.model_dump(mode="json"))

    with pytest.raises(StorageError, match=message):
        AtelierStorage(tmp_path)



def test_generation_failed_status_requires_remote_terminal_observation(tmp_path):
    project = _project(status="failed")
    project.nodes[0].data["candidates"][0]["error_code"] = "REMOTE_FAILED"
    attempt = project.generation_attempts[0]
    attempt.error_code = "REMOTE_FAILED"
    attempt.provider_execution = _provider_record(phase="submitting")
    _write_raw_project(tmp_path / "submitting", project.model_dump(mode="json"))
    with pytest.raises(StorageError, match="failed generation provider execution is unresolved"):
        AtelierStorage(tmp_path / "submitting")

    running = _project(status="failed")
    running.nodes[0].data["candidates"][0]["error_code"] = "REMOTE_FAILED"
    running_attempt = running.generation_attempts[0]
    running_attempt.error_code = "REMOTE_FAILED"
    running_attempt.provider_execution = _provider_record(
        phase="observed",
        handle=_handle(),
        observation=_observation(ProviderTaskStatus.RUNNING),
    )
    running.generation_request_receipts[0].execution_fingerprint = running_attempt.provider_execution.fingerprint
    _write_raw_project(tmp_path / "running", running.model_dump(mode="json"))
    with pytest.raises(StorageError, match="failed generation requires failed provider observation"):
        AtelierStorage(tmp_path / "running")

    remote_failed = _project(status="failed")
    remote_failed.nodes[0].data["candidates"][0]["error_code"] = "REMOTE_FAILED"
    failed_attempt = remote_failed.generation_attempts[0]
    failed_attempt.error_code = "REMOTE_FAILED"
    failed_attempt.provider_execution = _provider_record(
        phase="observed",
        handle=_handle(),
        observation=_observation(ProviderTaskStatus.FAILED),
    )
    remote_failed.generation_request_receipts[0].execution_fingerprint = failed_attempt.provider_execution.fingerprint
    assert _store_with_project(tmp_path / "failed", remote_failed).get_project("p1").generation_attempts[0].status == "failed"


def test_generation_pending_and_accepted_phase_do_not_hide_provider_results(tmp_path):
    accepted_with_handle = _project(status="pending")
    accepted_with_handle.generation_attempts[0].provider_execution = _provider_record(handle=_handle())
    accepted_with_handle.generation_request_receipts[0].execution_fingerprint = accepted_with_handle.generation_attempts[0].provider_execution.fingerprint
    _write_raw_project(tmp_path / "accepted-handle", accepted_with_handle.model_dump(mode="json"))
    with pytest.raises(StorageError, match="accepted provider execution cannot contain provider results"):
        AtelierStorage(tmp_path / "accepted-handle")

    pending_submitted = _project(status="pending")
    pending_submitted.generation_attempts[0].provider_execution = _provider_record(phase="submitted", handle=_handle())
    pending_submitted.generation_request_receipts[0].execution_fingerprint = pending_submitted.generation_attempts[0].provider_execution.fingerprint
    _write_raw_project(tmp_path / "pending-submitted", pending_submitted.model_dump(mode="json"))
    with pytest.raises(StorageError, match="pending generation cannot use submitted provider execution"):
        AtelierStorage(tmp_path / "pending-submitted")


def test_duplicate_generation_receipt_attempt_is_rejected_even_with_distinct_request_id(tmp_path):
    project = _project()
    project.generation_request_receipts.append(_receipt(client_request_id="request-2", request_fingerprint="c" * 64))
    _write_raw_project(tmp_path, project.model_dump(mode="json"))

    with pytest.raises(StorageError, match="Duplicate Atelier generation receipt attempt"):
        AtelierStorage(tmp_path)


def test_product_commands_cannot_mutate_generation_ledger_or_projection(tmp_path):
    store = _store_with_project(tmp_path, _project())

    def append_valid_generation(project: AtelierProject) -> None:
        project.nodes[0].data["candidates"].append(
            {
                "execution_contract": "atelier-provider-video-v1",
                "id": "candidate-2",
                "attempt_id": "attempt-2",
                "media_kind": "video",
                "status": "pending",
                "video_url": None,
                "error_code": None,
                "created_at": 11.0,
                "updated_at": 11.0,
            }
        )
        project.generation_attempts.append(
            _attempt(
                candidate_id="candidate-2",
                attempt_id="attempt-2",
                provider_execution=_provider_record(attempt_id="attempt-2", phase="completed", artifact_path="video/provider-attempt-2.mp4"),
                created_at=11.0,
                updated_at=11.0,
            )
        )
        project.generation_request_receipts.append(
            _receipt(
                client_request_id="request-2",
                request_fingerprint="d" * 64,
                candidate_id="candidate-2",
                attempt_id="attempt-2",
                execution_fingerprint=_provider_record(attempt_id="attempt-2", phase="completed", artifact_path="video/provider-attempt-2.mp4").fingerprint,
                created_at=11.0,
            )
        )

    with pytest.raises(StorageError, match="cannot change generation ledger"):
        store.apply_project_command(
            "p1",
            expected_revision=1,
            command=append_valid_generation,
        )

    with pytest.raises(StorageError, match="cannot change generation candidate projection"):
        store.apply_project_command(
            "p1",
            expected_revision=1,
            command=lambda project: project.nodes[0].data["candidates"][0].update({"updated_at": 11.0}),
        )



def test_upload_command_cannot_mutate_generation_ledger(tmp_path):
    store = _store_with_project(tmp_path, _project())

    with pytest.raises(StorageError, match="cannot change generation ledger"):
        store.apply_upload_command(
            "p1",
            expected_revision=1,
            client_request_id="upload-1",
            request_fingerprint="e" * 64,
            command=lambda project: project.generation_request_receipts.clear(),
        )


def test_terminal_generation_projection_can_be_deleted_and_restored(tmp_path):
    terminal = _project(status="completed")
    original_node = terminal.nodes[0].model_copy(deep=True)
    store = _store_with_project(tmp_path, terminal)

    deleted = store.apply_project_command("p1", expected_revision=1, command=lambda project: project.nodes.clear())
    assert deleted.nodes == []

    restored = store.apply_project_command("p1", expected_revision=2, command=lambda project: project.nodes.append(original_node))
    assert restored.nodes[0].id == "node-1"
    assert restored.nodes[0].data["candidates"][0]["attempt_id"] == "attempt-1"


def test_active_generation_attempt_blocks_node_and_project_delete_but_terminal_keeps_tombstone(tmp_path):
    store = _store_with_project(tmp_path, _project(status="pending"))

    with pytest.raises(StorageError, match="active generation|generation candidate projection"):
        store.apply_project_command("p1", expected_revision=1, command=lambda project: project.nodes.clear())
    with pytest.raises(StorageConflictError, match="pending Atelier generation"):
        store.delete_project("p1", expected_revision=1)

    terminal = _project(status="completed")
    store = _store_with_project(tmp_path / "terminal", terminal)
    saved = store.apply_project_command("p1", expected_revision=1, command=lambda project: project.nodes.clear())

    assert saved.nodes == []
    assert saved.generation_attempts[0].attempt_id == "attempt-1"
    assert saved.generation_request_receipts[0].attempt_id == "attempt-1"


def _project_with_batch_receipt() -> AtelierProject:
    project = _project(status="completed")
    second_record = _provider_record(attempt_id="attempt-2", phase="completed", artifact_path="video/provider-attempt-2.mp4")
    second_attempt = _attempt(
        candidate_id="candidate-2",
        attempt_id="attempt-2",
        status="completed",
        output_ref="video/provider-attempt-2.mp4",
        provider_execution=second_record,
    )
    second_candidate = dict(project.nodes[0].data["candidates"][0])
    second_candidate.update({
        "id": "candidate-2",
        "attempt_id": "attempt-2",
        "video_url": "video/provider-attempt-2.mp4",
    })
    project.nodes[0].data["candidates"].append(second_candidate)
    project.generation_attempts.append(second_attempt)
    project.generation_request_receipts = [AtelierGenerationRequestReceipt(
        schema_version=2,
        project_id="p1",
        entrypoint="atelier.provider_video.v1",
        request_contract_version="request-json-v1",
        client_request_id="request-1",
        request_fingerprint=HASH_B,
        node_id="node-1",
        members=[
            AtelierGenerationRequestReceiptMember(
                candidate_id="candidate-1",
                attempt_id="attempt-1",
                execution_fingerprint=project.generation_attempts[0].provider_execution.fingerprint,
            ),
            AtelierGenerationRequestReceiptMember(
                candidate_id="candidate-2",
                attempt_id="attempt-2",
                execution_fingerprint=second_record.fingerprint,
            ),
        ],
        base_revision=0,
        committed_revision=1,
        created_at=10.0,
    )]
    return project


def test_generation_batch_receipt_members_validate_all_attempts_on_load(tmp_path):
    loaded = _store_with_project(tmp_path, _project_with_batch_receipt()).get_project("p1")

    receipt = loaded.generation_request_receipts[0]
    assert receipt.schema_version == 2
    assert [member.attempt_id for member in receipt.receipt_members] == ["attempt-1", "attempt-2"]
    assert not hasattr(receipt, "task_id")


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda project: project.generation_request_receipts[0].members.pop(), "requires 2..6 members"),
    (lambda project: project.generation_request_receipts[0].members[1].__setattr__("attempt_id", "attempt-1"), "member identity is duplicated"),
    (lambda project: project.generation_request_receipts[0].members[1].__setattr__("candidate_id", "candidate-1"), "member identity is duplicated"),
    (lambda project: project.generation_request_receipts[0].members[1].__setattr__("execution_fingerprint", HASH_A), "receipt execution fingerprint does not match"),
])
def test_generation_batch_receipt_invalid_members_are_rejected(tmp_path, mutate, message):
    project = _project_with_batch_receipt()
    mutate(project)
    _write_raw_project(tmp_path, project.model_dump(mode="json"))

    with pytest.raises(StorageError, match=message):
        AtelierStorage(tmp_path)
