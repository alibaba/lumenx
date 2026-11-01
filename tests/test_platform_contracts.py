from pydantic import ValidationError

from src.apps.platform.contracts import (
    ArtifactSnapshot,
    IdempotencyContext,
    LedgerEntry,
    PlatformRole,
    TaskStatus,
    can_transition,
)


def test_platform_roles_and_task_transitions_are_explicit():
    assert PlatformRole.ADMIN.value == "admin"
    assert can_transition(TaskStatus.PUBLISHED, TaskStatus.CLAIMED)
    assert not can_transition(TaskStatus.CLOSED, TaskStatus.PUBLISHED)
    assert can_transition(TaskStatus.DELIVERED, TaskStatus.REVISION_REQUESTED)


def test_artifact_snapshot_is_immutable_and_rejects_client_url_only_evidence():
    snapshot = ArtifactSnapshot(
        artifact_id="artifact-1",
        artifact_version="v3",
        sha256="a" * 64,
        media_type="video/mp4",
        project_id="project-1",
        project_title_snapshot="Demo",
    )
    try:
        snapshot.sha256 = "b" * 64
    except ValidationError:
        pass
    else:
        raise AssertionError("artifact snapshots must be immutable")

    try:
        ArtifactSnapshot(url="/tmp/demo.mp4")
    except ValidationError:
        pass
    else:
        raise AssertionError("a URL cannot replace an ownership-checked snapshot")


def test_idempotency_and_ledger_contracts_reject_invalid_values():
    IdempotencyContext(key="claim-1234", request_id="req-1", actor_id="user-1")
    LedgerEntry(
        entry_id="entry-1",
        transaction_id="txn-1",
        account_id="acct-1",
        amount_cents=100,
        direction="credit",
        idempotency_key="ledger-1234",
    )
    try:
        LedgerEntry(
            entry_id="entry-1",
            transaction_id="txn-1",
            account_id="acct-1",
            amount_cents=0,
            direction="credit",
            idempotency_key="ledger-1234",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("ledger entries must use positive integer cents")
