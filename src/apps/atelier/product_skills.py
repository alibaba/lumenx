"""App-owned product Skill recipes for LumenX Atelier Agent loops.

These are static product-domain recipes. They are not local Agent skills and do
not load from the filesystem, user workspaces, HOME, packages, or imports.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

READ_PROJECT_TOOL = "canvas.readProject"
TOOL_CANVAS_CREATE_VIDEO_NODE = "canvas.createVideoNode"
TOOL_CANVAS_UPDATE_NODE_PROMPT = "canvas.updateNodePrompt"
TOOL_GENERATION_CREATE_VIDEO_CANDIDATES = "generation.createVideoCandidates"
CALLABLE_LOOP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        READ_PROJECT_TOOL,
        TOOL_CANVAS_CREATE_VIDEO_NODE,
        TOOL_CANVAS_UPDATE_NODE_PROMPT,
        TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
    }
)

_SKILL_HASH_FIELDS = (
    "schema_version",
    "name",
    "version",
    "title",
    "description",
    "category",
    "prompt_template",
    "expected_tools",
)


class AtelierProductSkillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=280)
    category: Literal["story", "shot", "review", "sequence"]
    prompt_template: str = Field(min_length=1, max_length=4000)
    expected_tools: tuple[str, ...] = Field(default_factory=tuple)
    default_iteration_cap: int = Field(3, ge=1, le=3, strict=True)


class AtelierProductSkillSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=280)
    category: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1, max_length=4000)
    expected_tools: tuple[str, ...] = Field(default_factory=tuple)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("expected_tools", mode="before")
    @classmethod
    def _expected_tools_tuple(cls, value):
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _hash_matches_content(self) -> "AtelierProductSkillSnapshot":
        if self.content_hash != _content_hash(self.model_dump(mode="json", exclude={"content_hash"})):
            raise ValueError("content_hash does not match product skill snapshot")
        return self


def build_product_skill_registry(specs: tuple[AtelierProductSkillSpec, ...]) -> tuple[AtelierProductSkillSpec, ...]:
    names: set[str] = set()
    versions: set[tuple[str, str]] = set()
    for spec in specs:
        if spec.name in names or (spec.name, spec.version) in versions:
            raise ValueError("duplicate product skill")
        names.add(spec.name)
        versions.add((spec.name, spec.version))
        unsupported = set(spec.expected_tools) - CALLABLE_LOOP_TOOL_NAMES
        if unsupported:
            raise ValueError("unsupported product skill tool")
        # Build and validate the snapshot once so hash drift cannot enter the registry.
        _snapshot_from_spec(spec)
    return tuple(specs)


def list_product_skill_catalog() -> list[dict[str, Any]]:
    return [
        {
            "schema_version": spec.schema_version,
            "name": spec.name,
            "version": spec.version,
            "title": spec.title,
            "description": spec.description,
            "category": spec.category,
            "expected_tools": tuple(spec.expected_tools),
            "default_iteration_cap": spec.default_iteration_cap,
            "content_hash": _snapshot_from_spec(spec).content_hash,
            "enabled": True,
            "disabled_reason": None,
        }
        for spec in PRODUCT_SKILLS
    ]


def resolve_product_skill_snapshot(name: str) -> AtelierProductSkillSnapshot:
    for spec in PRODUCT_SKILLS:
        if spec.name == name:
            return _snapshot_from_spec(spec)
    raise ValueError("atelier_product_skill_unknown")


def _snapshot_from_spec(spec: AtelierProductSkillSpec) -> AtelierProductSkillSnapshot:
    payload = {
        "schema_version": spec.schema_version,
        "name": spec.name,
        "version": spec.version,
        "title": spec.title,
        "description": spec.description,
        "category": spec.category,
        "prompt_template": spec.prompt_template,
        "expected_tools": tuple(spec.expected_tools),
    }
    return AtelierProductSkillSnapshot(**payload, content_hash=_content_hash(payload))


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {field: payload[field] for field in _SKILL_HASH_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


PRODUCT_SKILLS = build_product_skill_registry(
    (
        AtelierProductSkillSpec(
            schema_version=1,
            name="story-scout",
            version="1.0.0",
            title="Story Scout",
            description="Read the current canvas and summarize the next useful story move without changing the project.",
            category="story",
            prompt_template=(
                "Read the Atelier canvas as a story scout. Identify the current narrative material, "
                "call out missing context, and propose the next small creative move. Do not mutate the canvas."
            ),
            expected_tools=(READ_PROJECT_TOOL,),
            default_iteration_cap=1,
        ),
    )
)
