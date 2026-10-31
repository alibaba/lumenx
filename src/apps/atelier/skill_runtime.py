"""Safe conversion and admission binding for platform Product Skills.

The runtime accepts only structured manifests. It never reads a path, imports a
module, executes a command, or grants a tool that is outside the Atelier
registry.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ...utils.request_identity import fingerprint_request
from .product_skills import (
    CALLABLE_LOOP_TOOL_NAMES,
    AtelierProductSkillSnapshot,
    resolve_product_skill_snapshot,
)

_FORBIDDEN_FIELDS = frozenset({
    "path", "source", "script", "command", "executable", "shell", "imports",
    "network", "url", "credential_ref", "provider_handle", "secret", "env",
})
_ALLOWED_FIELDS = frozenset({
    "name", "version", "title", "description", "category", "prompt_template",
    "expected_tools", "content_hash",
})


class SkillSandboxReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    rejected_fields: tuple[str, ...] = ()
    unsupported_tools: tuple[str, ...] = ()
    content_hash: str | None = None


class SkillExecutionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(1, strict=True)
    skill_name: str
    skill_version: str
    skill_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(sorted(payload.items())), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_skill_manifest_sandbox(manifest: Mapping[str, Any]) -> SkillSandboxReport:
    keys = set(manifest)
    rejected = tuple(sorted(keys - _ALLOWED_FIELDS | (keys & _FORBIDDEN_FIELDS)))
    tools = manifest.get("expected_tools", ())
    if not isinstance(tools, (list, tuple)):
        tools = ()
    unsupported = tuple(sorted(set(tools) - set(CALLABLE_LOOP_TOOL_NAMES)))
    content_hash = manifest.get("content_hash") if isinstance(manifest.get("content_hash"), str) else None
    if content_hash and len(content_hash) == 64:
        source = {key: manifest[key] for key in sorted(keys) if key != "content_hash" and key in _ALLOWED_FIELDS}
        expected = hashlib.sha256(_canonical(source).encode("utf-8")).hexdigest()
        if expected != content_hash:
            rejected = tuple(sorted(set(rejected) | {"content_hash"}))
    return SkillSandboxReport(allowed=not rejected and not unsupported, rejected_fields=rejected, unsupported_tools=unsupported, content_hash=content_hash)


def convert_manifest_to_snapshot(manifest: Mapping[str, Any]) -> AtelierProductSkillSnapshot:
    report = validate_skill_manifest_sandbox(manifest)
    if not report.allowed:
        raise ValueError("skill_manifest_sandbox_rejected")
    required = {"name", "version", "title", "description", "category", "prompt_template", "expected_tools", "content_hash"}
    missing = required - set(manifest)
    if missing:
        raise ValueError("skill_manifest_missing_fields")
    payload = {key: manifest[key] for key in required if key != "content_hash"}
    # The platform manifest hash is verified above; the Atelier snapshot hash
    # is recomputed over its own immutable contract.
    snapshot_payload = {
        "schema_version": 1,
        "name": manifest["name"], "version": manifest["version"],
        "title": manifest["title"], "description": manifest["description"],
        "category": manifest["category"], "prompt_template": manifest["prompt_template"],
        "expected_tools": tuple(manifest["expected_tools"]),
    }
    snapshot_hash = hashlib.sha256(_canonical(snapshot_payload).encode("utf-8")).hexdigest()
    return AtelierProductSkillSnapshot(**snapshot_payload, content_hash=snapshot_hash)


def bind_skill_to_video_request(snapshot: AtelierProductSkillSnapshot, request_payload: Mapping[str, Any]) -> SkillExecutionBinding:
    request_fingerprint = fingerprint_request(
        {"skill_content_hash": snapshot.content_hash, "request": dict(request_payload)},
        entrypoint="atelier.skill-bound-video.v1",
    )
    return SkillExecutionBinding(
        skill_name=snapshot.name,
        skill_version=snapshot.version,
        skill_content_hash=snapshot.content_hash,
        request_fingerprint=request_fingerprint,
    )


def resolve_runtime_skill_snapshot(
    name: str,
    *,
    platform_manifest: Mapping[str, Any] | None = None,
    fallback_resolver=resolve_product_skill_snapshot,
) -> AtelierProductSkillSnapshot:
    """Resolve a platform-bound manifest, falling back to the app registry.

    The caller supplies the already authorized platform manifest. This keeps
    Atelier independent from platform storage while allowing the composition
    root to bridge authenticated platform bindings into Agent runtime.
    """

    if platform_manifest is not None:
        snapshot = convert_manifest_to_snapshot(platform_manifest)
        if snapshot.name != name:
            raise ValueError("atelier_platform_skill_name_mismatch")
        return snapshot
    return fallback_resolver(name)

