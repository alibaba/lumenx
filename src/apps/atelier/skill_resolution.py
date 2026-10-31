"""Scope-aware Product Skill resolution for the first cloud-ready slice.

This module is deliberately storage-free. It resolves already validated Skill
snapshots and never loads filesystem paths, executes scripts, or widens tool
permissions. Persistence and HTTP bindings are later slices.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .product_skills import AtelierProductSkillSnapshot, CALLABLE_LOOP_TOOL_NAMES


class SkillScope(IntEnum):
    SYSTEM = 0
    WORKSPACE = 1
    USER = 2
    PROJECT = 3
    SESSION = 4


class SkillBinding(BaseModel):
    """A validated scope binding; it contains no executable content."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    scope: SkillScope
    owner_id: str | None = Field(default=None, min_length=1)
    snapshot: AtelierProductSkillSnapshot


class EffectiveSkill(BaseModel):
    """The immutable Skill view used by a planner for one run."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str
    snapshot: AtelierProductSkillSnapshot
    scope: SkillScope
    owner_id: str | None = None
    allowed_tools: tuple[str, ...] = ()


def resolve_effective_skills(
    bindings: Iterable[SkillBinding],
    *,
    available_tools: Iterable[str] = CALLABLE_LOOP_TOOL_NAMES,
) -> dict[str, EffectiveSkill]:
    """Resolve the most specific binding for each Skill name.

    A binding may only narrow the callable tool set. The returned object is a
    run-ready snapshot and is safe to persist later by its content hash.
    """

    available = set(available_tools) & set(CALLABLE_LOOP_TOOL_NAMES)
    selected: dict[str, SkillBinding] = {}
    for binding in bindings:
        if binding.snapshot.name != binding.name:
            raise ValueError("skill_binding_snapshot_mismatch")
        unsupported = set(binding.snapshot.expected_tools) - set(CALLABLE_LOOP_TOOL_NAMES)
        if unsupported:
            raise ValueError("skill_binding_unsupported_tool")
        current = selected.get(binding.name)
        if current is not None and binding.scope < current.scope:
            continue
        if current is not None and binding.scope == current.scope and binding.owner_id != current.owner_id:
            raise ValueError("skill_binding_scope_conflict")
        selected[binding.name] = binding

    result: dict[str, EffectiveSkill] = {}
    for name, binding in selected.items():
        requested = set(binding.snapshot.expected_tools)
        allowed = tuple(sorted(requested & available)) if requested else tuple(sorted(available))
        result[name] = EffectiveSkill(
            name=name,
            snapshot=binding.snapshot,
            scope=binding.scope,
            owner_id=binding.owner_id,
            allowed_tools=allowed,
        )
    return result


def bind_system_skill(snapshot: AtelierProductSkillSnapshot) -> SkillBinding:
    """Create the immutable system baseline binding for one Skill snapshot."""

    return SkillBinding(name=snapshot.name, scope=SkillScope.SYSTEM, snapshot=snapshot)

