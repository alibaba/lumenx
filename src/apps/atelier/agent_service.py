"""Read-only Atelier agent planning service.

The service prepares sanitized planner context and deterministic proposal plans.
It deliberately does not mutate storage, call providers, read credentials, or
admit generation requests.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .agent_tools import (
    GENERATION_PROPOSAL_CONTRACT_VERSION,
    TOOL_SCHEMA_VERSION,
    list_tool_schemas,
)
from .generation_commands import ATELIER_VIDEO_MODEL
from .references import pending_reference_nodes
from .models import (
    AtelierAgentPlan,
    AtelierAgentPlanContext,
    AtelierAgentPlannerPackage,
    AtelierAgentToolStatus,
    AtelierNode,
    AtelierProject,
)
from .storage import AtelierStorage
from ...utils.media_refs import LOCAL_MEDIA_PREFIXES


_DETERMINISTIC_PLANNER = "deterministic_core"
_GENERATION_TOOL = "generation.createVideoCandidates"
_EFFECTIVE_LOCAL_MEDIA_PREFIXES = tuple(dict.fromkeys((*LOCAL_MEDIA_PREFIXES, "video_inputs/")))
_NESTED_OUTPUT_PREFIXES = tuple(
    prefix for prefix in _EFFECTIVE_LOCAL_MEDIA_PREFIXES if prefix not in {"output/", "outputs/"}
)

_GENERATION_INTENT_TOKENS = (
    "生成",
    "候选",
    "视频",
    "generate",
    "candidate",
    "render",
    "video",
)


class AtelierAgentReadOnlyService:
    """Build sanitized Agent context and read-only deterministic plans."""

    def __init__(self, storage: AtelierStorage):
        self._storage = storage

    def list_tools(self, project_id: str) -> list[dict[str, Any]]:
        self._storage.get_project(project_id)
        return list_tool_schemas()

    def build_planner_package(
        self,
        project_id: str,
        *,
        user_message: str = "",
        selected_node_id: str | None = None,
        skill_name: str | None = None,
    ) -> AtelierAgentPlannerPackage:
        project = self._storage.get_project(project_id)
        selected_node = _find_node(project, selected_node_id) if selected_node_id else None
        if selected_node_id and selected_node is None:
            raise ValueError("Selected Atelier node was not found in this project")
        project_snapshot = _project_snapshot(project)
        selected_snapshot = _node_snapshot(selected_node) if selected_node is not None else None
        return AtelierAgentPlannerPackage(
            project_id=project.id,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            tool_schema_version=TOOL_SCHEMA_VERSION,
            output_contract={
                "schema_version": "atelier.agent.readonly-plan.v1",
                "planner": _DETERMINISTIC_PLANNER,
                "status": ["ready", "blocked"],
                "tool_calls": [
                    {
                        "tool_name": _GENERATION_TOOL,
                        "contract_version": GENERATION_PROPOSAL_CONTRACT_VERSION,
                        "effect": "proposal_only",
                    }
                ],
                "side_effects": "none",
            },
            tool_schemas=list_tool_schemas(),
            project_snapshot=project_snapshot,
            selected_node_snapshot=selected_snapshot,
            policy_snapshot=project.agent_policy.model_dump(mode="json"),
            prior_messages=[],
            system_prefix=None,
            volatile_suffix_meta={
                "scope": "readonly",
                "node_count": len(project.nodes),
                "selected_node_id": selected_node_id,
                "tool_schema_version": TOOL_SCHEMA_VERSION,
            },
            provider_id=None,
            model_id=None,
        )

    def plan(
        self,
        project_id: str,
        *,
        user_message: str = "",
        selected_node_id: str | None = None,
        skill_name: str | None = None,
        planner: str | None = None,
        planner_input: dict[str, Any] | None = None,
    ) -> AtelierAgentPlan:
        project = self._storage.get_project(project_id)
        return plan_for_project(
            project, user_message=user_message, selected_node_id=selected_node_id,
            skill_name=skill_name, planner=planner, planner_input=planner_input,
        )


def plan_for_project(
    project: AtelierProject,
    *,
    user_message: str = "",
    selected_node_id: str | None = None,
    skill_name: str | None = None,
    planner: str | None = None,
    planner_input: dict[str, Any] | None = None,
) -> AtelierAgentPlan:
    """Prepare the existing proposal from one supplied snapshot, without storage reads."""
    planner_name = planner or _DETERMINISTIC_PLANNER
    if planner_name != _DETERMINISTIC_PLANNER:
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason=f"Unknown Atelier agent planner: {planner_name}",
        )
    if planner_input:
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason="planner_input is not supported by the read-only deterministic planner",
            planner_input={"rejected": True},
        )
    if not user_message.strip():
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason="User message is required for deterministic planning",
        )
    selected_node = _find_node(project, selected_node_id) if selected_node_id else None
    if selected_node is None:
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason="Selected Atelier node was not found in this project",
        )
    if not _is_generation_intent(user_message):
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason="Canvas write planning is not available in this read-only slice",
        )
    if selected_node.type != "video":
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason="Video generation proposals require a selected video node",
        )
    if not _policy_allows(project, _GENERATION_TOOL):
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason=f"Tool is not allowed by project policy: {_GENERATION_TOOL}",
        )
    first_frame = _explicit_attached_image_source(project, selected_node)
    if first_frame is None:
        return _blocked_plan(
            project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner=planner_name,
            reason="Video generation proposals require an explicit same-project attached image first frame from managed local media",
        )
    arguments = {
        "node_id": selected_node.id,
        "prompt": selected_node.prompt.strip() or user_message.strip(),
        "model_id": ATELIER_VIDEO_MODEL,
        "mode": "i2v",
        "first_frame": first_frame,
        "params": {},
    }
    return AtelierAgentPlan(
        project_id=project.id,
        user_message=user_message,
        planner=planner_name,
        skill_name=skill_name,
        status="ready",
        reason="",
        tool_calls=[
            {
                "call_id": str(uuid.uuid4()),
                "tool_name": _GENERATION_TOOL,
                "contract_version": GENERATION_PROPOSAL_CONTRACT_VERSION,
                "arguments": arguments,
                "status": AtelierAgentToolStatus.PROPOSED.value,
                "approval_required": True,
                "proposal_only": True,
            }
        ],
        context=_context(selected_node_id=selected_node_id, planner=planner_name),
    )


def _blocked_plan(
    project: AtelierProject,
    *,
    user_message: str,
    selected_node_id: str | None,
    skill_name: str | None,
    planner: str,
    reason: str,
    planner_input: dict[str, Any] | None = None,
) -> AtelierAgentPlan:
    return AtelierAgentPlan(
        project_id=project.id,
        user_message=user_message,
        planner=planner,
        skill_name=skill_name,
        status="blocked",
        reason=reason,
        tool_calls=[],
        context=_context(selected_node_id=selected_node_id, planner=planner, planner_input=planner_input),
    )


def _context(
    *,
    selected_node_id: str | None,
    planner: str,
    planner_input: dict[str, Any] | None = None,
) -> AtelierAgentPlanContext:
    return AtelierAgentPlanContext(
        selected_node_id=selected_node_id,
        planner_input=planner_input or {},
        planner_adapter_name=planner,
        tool_schema_version=TOOL_SCHEMA_VERSION,
    )


def _project_snapshot(project: AtelierProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "source_project_id": project.source_project_id,
        "revision": project.revision,
        "node_count": len(project.nodes),
        "nodes": [_node_snapshot(node) for node in project.nodes],
    }


def _node_snapshot(node: AtelierNode) -> dict[str, Any]:
    media_urls = [_safe_media_url(url) for url in node.media_urls]
    safe_urls = [url for url in media_urls if url]
    snapshot: dict[str, Any] = {
        "id": node.id,
        "project_id": node.project_id,
        "type": node.type,
        "title": node.title,
        "prompt": node.prompt,
        "status": node.status,
        "x": node.x,
        "y": node.y,
        "width": node.width,
        "height": node.height,
        "source_project_id": node.source_project_id,
        "frame_id": node.frame_id,
        "asset_id": node.asset_id,
        "video_task_id": node.video_task_id,
        "media_urls": safe_urls,
    }
    reference_node_ids = node.data.get("reference_node_ids")
    if isinstance(reference_node_ids, list):
        snapshot["reference_node_ids"] = [item for item in reference_node_ids if isinstance(item, str)]
    pending_refs = pending_reference_nodes(node.data.get("pending_reference_nodes"))
    if pending_refs:
        snapshot["pending_reference_nodes"] = pending_refs
    return snapshot


def _safe_media_url(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or candidate.startswith("/"):
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username or parsed.password:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if ".." in candidate.split("/"):
        return None
    return candidate.split("?", 1)[0].split("#", 1)[0]


def _managed_local_media_url(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or candidate.startswith("/") or "?" in candidate or "#" in candidate:
        return None
    if "\\" in candidate or any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if candidate.startswith(("output/", "outputs/")):
        _, inner = candidate.split("/", 1)
        if not inner.startswith(_NESTED_OUTPUT_PREFIXES):
            return None
        return candidate
    if not candidate.startswith(_EFFECTIVE_LOCAL_MEDIA_PREFIXES):
        return None
    return candidate


def _find_node(project: AtelierProject, node_id: str | None) -> AtelierNode | None:
    if not node_id:
        return None
    for node in project.nodes:
        if node.id == node_id and node.project_id == project.id:
            return node
    return None


def _policy_allows(project: AtelierProject, tool_name: str) -> bool:
    allowed = project.agent_policy.allowed_tools
    return not allowed or tool_name in allowed


def _is_generation_intent(user_message: str) -> bool:
    lowered = user_message.lower()
    return any(token in lowered for token in _GENERATION_INTENT_TOKENS)


def _explicit_attached_image_source(project: AtelierProject, video_node: AtelierNode) -> dict[str, str] | None:
    reference_node_ids = video_node.data.get("reference_node_ids")
    if not isinstance(reference_node_ids, list):
        return None
    for reference_node_id in reference_node_ids:
        if not isinstance(reference_node_id, str):
            continue
        node = _find_node(project, reference_node_id)
        if node is None or node.type != "image":
            continue
        for media_url in node.media_urls:
            local_url = _managed_local_media_url(media_url)
            if local_url:
                return {"node_id": node.id, "media_url": local_url}
    return None
