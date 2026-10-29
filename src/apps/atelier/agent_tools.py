"""Read-only Atelier agent tool schemas.

This first Core-owned surface exposes planner-visible capabilities without
executing provider work or canvas mutations. Write tools stay out of the
registry until their product/API boundary exists.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .generation_commands import ATELIER_VIDEO_MODEL, CreateVideoCandidateCommand


TOOL_SCHEMA_VERSION = "atelier.tools.readonly.v1"
GENERATION_PROPOSAL_CONTRACT_VERSION = "atelier-agent-generation-proposal-v1"


@dataclass(frozen=True)
class AtelierAgentToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    required_permission: str
    mutates_canvas: bool = False
    requires_approval: bool = False
    proposal_only: bool = False
    mutation_scope: str = "read"

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": deepcopy(self.input_schema),
            "required_permission": self.required_permission,
            "mutates_canvas": self.mutates_canvas,
            "requires_approval": self.requires_approval,
            "proposal_only": self.proposal_only,
            "mutation_scope": self.mutation_scope,
            "tool_schema_version": TOOL_SCHEMA_VERSION,
        }


_READ_PROJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}

def _generation_create_video_candidates_schema() -> dict[str, Any]:
    schema = CreateVideoCandidateCommand.model_json_schema()
    schema = deepcopy(schema)
    schema["title"] = "AtelierGenerationCreateVideoCandidatesProposal"
    schema["description"] = (
        "Read-only Agent proposal arguments for a future CreateVideoCandidateCommand. "
        "The planner supplies node_id, while expected_revision and client_request_id "
        "are intentionally omitted because this surface does not admit or execute work."
    )
    properties = schema.setdefault("properties", {})
    properties.pop("expected_revision", None)
    properties.pop("client_request_id", None)
    properties["node_id"] = {"title": "Node Id", "type": "string", "minLength": 1}
    if "model_id" in properties:
        properties["model_id"] = {**properties["model_id"], "enum": [ATELIER_VIDEO_MODEL]}
    required = [item for item in schema.get("required", []) if item not in {"expected_revision", "client_request_id"}]
    if "node_id" not in required:
        required.insert(0, "node_id")
    schema["required"] = required
    return schema

_TOOL_SPECS: tuple[AtelierAgentToolSpec, ...] = (
    AtelierAgentToolSpec(
        name="canvas.readProject",
        description="Read a sanitized snapshot of the current Atelier project.",
        input_schema=_READ_PROJECT_SCHEMA,
        required_permission="read",
    ),
    AtelierAgentToolSpec(
        name="generation.createVideoCandidates",
        description=(
            "Propose a durable video-candidate creation command for a video node. "
            "This read-only planner surface does not admit, submit, or execute the generation."
        ),
        input_schema=_generation_create_video_candidates_schema(),
        required_permission="generation_write",
        requires_approval=True,
        proposal_only=True,
        mutation_scope="generation_write",
    ),
)


def list_tool_specs() -> list[AtelierAgentToolSpec]:
    return list(_TOOL_SPECS)


def list_tool_schemas() -> list[dict[str, Any]]:
    return [spec.to_schema() for spec in _TOOL_SPECS]
