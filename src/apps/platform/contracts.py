"""Stable, product-independent contracts for the creator platform.

This module has no database or Studio/Atelier imports. It is the first
implementation slice for the Gate 0 decisions in PLATFORM-DATA-CONTRACT.md.
"""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field


class PlatformRole(StrEnum):
    USER = "user"
    CREATOR = "creator"
    REVIEWER = "reviewer"
    CLIENT = "client"
    ADMIN = "admin"


class TaskStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    REVISION_REQUESTED = "revision_requested"
    RESUBMITTED = "resubmitted"
    ACCEPTED = "accepted"
    SETTLEMENT_PENDING = "settlement_pending"
    SETTLED = "settled"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    DISPUTED = "disputed"
    REFUNDED = "refunded"


TASK_TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.PUBLISHED, TaskStatus.CANCELLED}),
    TaskStatus.PUBLISHED: frozenset({TaskStatus.CLAIMED, TaskStatus.EXPIRED, TaskStatus.CANCELLED}),
    TaskStatus.CLAIMED: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.EXPIRED, TaskStatus.CANCELLED}),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.DELIVERED, TaskStatus.EXPIRED, TaskStatus.DISPUTED}),
    TaskStatus.DELIVERED: frozenset({TaskStatus.REVISION_REQUESTED, TaskStatus.ACCEPTED}),
    TaskStatus.REVISION_REQUESTED: frozenset({TaskStatus.RESUBMITTED, TaskStatus.CANCELLED}),
    TaskStatus.RESUBMITTED: frozenset({TaskStatus.REVISION_REQUESTED, TaskStatus.ACCEPTED}),
    TaskStatus.ACCEPTED: frozenset({TaskStatus.SETTLEMENT_PENDING, TaskStatus.DISPUTED}),
    TaskStatus.SETTLEMENT_PENDING: frozenset({TaskStatus.SETTLED, TaskStatus.REFUNDED, TaskStatus.DISPUTED}),
    TaskStatus.SETTLED: frozenset({TaskStatus.CLOSED, TaskStatus.DISPUTED}),
    TaskStatus.CLOSED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.EXPIRED: frozenset({TaskStatus.PUBLISHED, TaskStatus.CANCELLED}),
    TaskStatus.DISPUTED: frozenset({TaskStatus.REFUNDED, TaskStatus.SETTLEMENT_PENDING, TaskStatus.CLOSED}),
    TaskStatus.REFUNDED: frozenset({TaskStatus.CLOSED}),
}


class ArtifactSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=128)
    artifact_version: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    project_title_snapshot: str = Field(min_length=1, max_length=200)


class IdempotencyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=8, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)


class TaskTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    from_status: TaskStatus
    to_status: TaskStatus
    actor_id: str = Field(min_length=1, max_length=128)
    idempotency: IdempotencyContext
    reason: str | None = Field(default=None, max_length=2000)


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Return whether a state change is allowed by the platform contract."""

    return target in TASK_TRANSITIONS[current]


class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    transaction_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    amount_cents: int = Field(gt=0)
    direction: str = Field(pattern=r"^(debit|credit)$")
    idempotency_key: str = Field(min_length=8, max_length=128)

