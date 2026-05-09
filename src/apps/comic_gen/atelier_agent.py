from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import (
    AtelierAgentPolicy,
    AtelierAgentToolCall,
    AtelierAgentToolStatus,
    AtelierAgentTurn,
    AtelierNode,
    AtelierProject,
)
from ...utils.model_catalog import resolve_r2v_route_model_id, validate_r2v_reference_inputs


READ_PERMISSION = "read"
CANVAS_WRITE_PERMISSION = "canvas_write"
GENERATION_PERMISSION = "generation"


@dataclass(frozen=True)
class AtelierToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    required_permission: str
    mutates_canvas: bool = False
    max_count_cost: int = 0
    requires_approval: bool = False


ToolExecutor = Callable[[str, Dict[str, Any], Any], Dict[str, Any]]


def _tool_call_payload(call: AtelierAgentToolCall) -> Dict[str, Any]:
    return {"tool_name": call.tool_name, "arguments": dict(call.arguments or {})}


def _tool_call_payloads_match(left: List[Dict[str, Any]], right: List[Dict[str, Any]]) -> bool:
    return [
        {"tool_name": item.get("tool_name"), "arguments": dict(item.get("arguments") or {})}
        for item in left
    ] == [
        {"tool_name": item.get("tool_name"), "arguments": dict(item.get("arguments") or {})}
        for item in right
    ]


class AtelierToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tuple[AtelierToolSpec, ToolExecutor]] = {}

    def register(self, spec: AtelierToolSpec, executor: ToolExecutor) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Atelier agent tool already registered: {spec.name}")
        self._tools[spec.name] = (spec, executor)

    def get(self, name: str) -> Optional[Tuple[AtelierToolSpec, ToolExecutor]]:
        return self._tools.get(name)

    def list_specs(self) -> List[AtelierToolSpec]:
        return [entry[0] for entry in self._tools.values()]


class AtelierPermissionEnforcer:
    def evaluate(
        self,
        policy: AtelierAgentPolicy,
        spec: Optional[AtelierToolSpec],
        tool_name: str,
        arguments: Dict[str, Any],
        projected_node_cost: int,
    ) -> Tuple[str, Optional[str]]:
        if not spec:
            return AtelierAgentToolStatus.DENIED.value, f"Unknown Atelier agent tool: {tool_name}"
        if policy.allowed_tools and spec.name not in policy.allowed_tools:
            return AtelierAgentToolStatus.DENIED.value, f"Tool is not allowed by project policy: {spec.name}"
        if projected_node_cost > policy.max_nodes_per_action:
            return (
                AtelierAgentToolStatus.DENIED.value,
                f"Tool call exceeds max_nodes_per_action ({policy.max_nodes_per_action})",
            )

        approval_mode = policy.approval_mode.value if hasattr(policy.approval_mode, "value") else str(policy.approval_mode)
        if spec.mutates_canvas and approval_mode == "untrusted":
            return AtelierAgentToolStatus.APPROVAL_REQUIRED.value, None
        if spec.required_permission == GENERATION_PERMISSION and approval_mode in {"untrusted", "on_failure"}:
            return AtelierAgentToolStatus.APPROVAL_REQUIRED.value, None
        if spec.requires_approval and approval_mode == "on_request":
            return AtelierAgentToolStatus.APPROVAL_REQUIRED.value, None

        return AtelierAgentToolStatus.PROPOSED.value, None


class AtelierAgentHarness:
    def __init__(self, pipeline: Any):
        self.pipeline = pipeline
        self.registry = build_default_atelier_tool_registry()
        self.enforcer = AtelierPermissionEnforcer()

    def list_tool_specs(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "required_permission": spec.required_permission,
                "mutates_canvas": spec.mutates_canvas,
                "max_count_cost": spec.max_count_cost,
                "requires_approval": spec.requires_approval,
            }
            for spec in self.registry.list_specs()
        ]

    def run_turn(
        self,
        project_id: str,
        tool_calls: List[Dict[str, Any]],
        user_message: str = "",
        preview: bool = False,
        approve: bool = False,
        turn_id: Optional[str] = None,
    ) -> AtelierAgentTurn:
        project = self.pipeline.get_atelier_project(project_id)
        if not project:
            raise ValueError("Atelier project not found")

        pending_turns = [candidate for candidate in project.agent_turns if candidate.status == "waiting_approval"]
        if approve and preview:
            raise ValueError("Approval cannot run in preview mode")
        source_tool_calls: List[Tuple[Dict[str, Any], Optional[AtelierAgentToolCall]]] = [
            (raw_call, None) for raw_call in tool_calls
        ]
        turn = None
        if approve and turn_id:
            turn = next((candidate for candidate in project.agent_turns if candidate.id == turn_id), None)
            if not turn:
                raise ValueError("Atelier agent turn not found")
            if turn.status != "waiting_approval":
                raise ValueError("Atelier agent turn is not waiting for approval")
            approved_calls = [
                call for call in turn.tool_calls
                if call.status == AtelierAgentToolStatus.APPROVAL_REQUIRED
            ]
            if not approved_calls:
                raise ValueError("Atelier agent turn has no approval-required tool calls")
            approved_payloads = [_tool_call_payload(call) for call in approved_calls]
            if not _tool_call_payloads_match(tool_calls, approved_payloads):
                raise ValueError("Approved tool calls do not match the pending Atelier agent turn")
            source_tool_calls = list(zip(approved_payloads, approved_calls))
            turn.user_message = user_message or turn.user_message
            turn.preview = False
            turn.status = "pending"
            turn.completed_at = None
        elif approve:
            raise ValueError("turn_id is required when approving an Atelier agent turn")
        else:
            if pending_turns:
                raise ValueError("Resolve the pending Atelier agent turn before starting a new turn")
            turn = AtelierAgentTurn(
                project_id=project_id,
                user_message=user_message,
                preview=preview,
                status="pending",
            )

        projected_node_cost = 0
        appending_new_turn = not (approve and turn_id)

        for raw_call, existing_call in source_tool_calls:
            tool_name = str(raw_call.get("tool_name") or raw_call.get("name") or "")
            arguments = dict(raw_call.get("arguments") or {})
            call = existing_call or AtelierAgentToolCall(tool_name=tool_name, arguments=arguments)
            call.tool_name = tool_name
            call.arguments = arguments
            call.error = None
            call.result_snapshot = None
            call.completed_at = None
            entry = self.registry.get(tool_name)
            spec = entry[0] if entry else None
            projected_node_cost += spec.max_count_cost if spec else 0
            policy_status, reason = self.enforcer.evaluate(
                project.agent_policy,
                spec,
                tool_name,
                arguments,
                projected_node_cost,
            )

            if reason:
                call.status = AtelierAgentToolStatus.DENIED
                call.error = reason
                call.completed_at = time.time()
                if existing_call is None:
                    turn.tool_calls.append(call)
                continue

            if policy_status == AtelierAgentToolStatus.APPROVAL_REQUIRED.value and not approve:
                call.status = AtelierAgentToolStatus.APPROVAL_REQUIRED
                call.approval_required = True
                if existing_call is None:
                    turn.tool_calls.append(call)
                continue

            if preview:
                call.status = AtelierAgentToolStatus.PROPOSED
                call.approval_required = policy_status == AtelierAgentToolStatus.APPROVAL_REQUIRED.value
                if existing_call is None:
                    turn.tool_calls.append(call)
                continue

            assert entry is not None
            spec, executor = entry
            try:
                result = executor(project_id, arguments, self.pipeline)
                call.status = AtelierAgentToolStatus.COMPLETED
                call.approval_required = policy_status == AtelierAgentToolStatus.APPROVAL_REQUIRED.value
                call.approval_granted = call.approval_required and approve
                call.result_snapshot = result
                call.completed_at = time.time()
            except Exception as exc:
                call.status = AtelierAgentToolStatus.FAILED
                call.error = str(exc)
                call.completed_at = time.time()
            if existing_call is None:
                turn.tool_calls.append(call)

        if any(call.status == AtelierAgentToolStatus.APPROVAL_REQUIRED for call in turn.tool_calls):
            turn.status = "waiting_approval"
        elif any(call.status == AtelierAgentToolStatus.FAILED for call in turn.tool_calls):
            turn.status = "failed"
            turn.completed_at = time.time()
        else:
            turn.status = "completed"
            turn.completed_at = time.time()

        if appending_new_turn:
            project.agent_turns.append(turn)
        project.updated_at = time.time()
        self.pipeline._save_atelier_data()
        return turn


def _compact_node(node: AtelierNode) -> Dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "prompt": node.prompt,
        "status": node.status,
        "x": node.x,
        "y": node.y,
        "width": node.width,
        "height": node.height,
        "media_urls": node.media_urls,
        "data": node.data,
    }


def _execute_read_project(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    project: AtelierProject = pipeline.get_atelier_project(project_id)
    return {
        "project": {
            "id": project.id,
            "title": project.title,
            "description": project.description,
            "nodes": [_compact_node(node) for node in project.nodes],
            "agent_policy": project.agent_policy.model_dump(mode="json"),
        }
    }


def _execute_create_video_node(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    node = pipeline.create_atelier_node(
        project_id,
        {
            "type": "video",
            "title": arguments.get("title") or "Agent video node",
            "prompt": arguments.get("prompt") or "",
            "x": arguments.get("x", 160.0),
            "y": arguments.get("y", 160.0),
            "width": arguments.get("width", 420.0),
            "height": arguments.get("height", 560.0),
            "data": {
                "model": arguments.get("model") or "wan2.7-i2v",
                **dict(arguments.get("data") or {}),
            },
            "created_by": "agent",
        },
    )
    return {"node": _compact_node(node)}


def _execute_update_node_prompt(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    node_id = arguments.get("node_id")
    if not node_id:
        raise ValueError("node_id is required")
    data = dict(arguments.get("data") or {})
    if arguments.get("model"):
        data["model"] = arguments["model"]
    payload: Dict[str, Any] = {}
    if "title" in arguments:
        payload["title"] = arguments["title"]
    if "prompt" in arguments:
        payload["prompt"] = arguments["prompt"]
    if data:
        project, current = pipeline._get_atelier_node_pair(project_id, node_id)
        payload["data"] = {**dict(current.data or {}), **data}
    node = pipeline.update_atelier_node(project_id, node_id, payload)
    return {"node": _compact_node(node)}


def _execute_create_reference_image_node(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    media_url = arguments.get("media_url")
    if not media_url:
        raise ValueError("media_url is required")
    node = pipeline.create_atelier_node(
        project_id,
        {
            "type": "image",
            "title": arguments.get("title") or "Agent reference image",
            "prompt": arguments.get("prompt") or "",
            "x": arguments.get("x", 80.0),
            "y": arguments.get("y", 160.0),
            "width": arguments.get("width", 220.0),
            "height": arguments.get("height", 136.0),
            "media_urls": [media_url],
            "data": {"reference_role": "video_reference_image", **dict(arguments.get("data") or {})},
            "created_by": "agent",
        },
    )
    return {"node": _compact_node(node)}


def _execute_attach_reference_node(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    video_node_id = arguments.get("video_node_id")
    image_node_id = arguments.get("image_node_id")
    if not video_node_id or not image_node_id:
        raise ValueError("video_node_id and image_node_id are required")
    project, video_node = pipeline._get_atelier_node_pair(project_id, video_node_id)
    _, image_node = pipeline._get_atelier_node_pair(project_id, image_node_id)
    if video_node.type != "video":
        raise ValueError("video_node_id must reference a video node")
    if image_node.type != "image" or not image_node.media_urls:
        raise ValueError("image_node_id must reference an image node with media")
    image_data = dict(image_node.data or {})
    parent_node_id = image_data.get("parent_node_id")
    if parent_node_id and parent_node_id != video_node.id:
        raise ValueError("Reference node is already attached to another video node")
    image_url = image_node.media_urls[0]
    for node in project.nodes:
        if node.id == video_node.id or node.type != "video":
            continue
        node_data = dict(node.data or {})
        if image_node.id in list(node_data.get("reference_node_ids") or []):
            raise ValueError("Reference node is already attached to another video node")
        if image_url in list(node_data.get("reference_image_urls") or []):
            raise ValueError("Reference media is already attached to another video node")
    video_data = dict(video_node.data or {})
    reference_image_urls = list(video_data.get("reference_image_urls") or [])
    reference_node_ids = list(video_data.get("reference_node_ids") or [])
    if image_url not in reference_image_urls:
        reference_image_urls.append(image_url)
    if image_node.id not in reference_node_ids:
        reference_node_ids.append(image_node.id)
    updated_video = pipeline.update_atelier_node(
        project_id,
        video_node.id,
        {"data": {**video_data, "reference_image_urls": reference_image_urls, "reference_node_ids": reference_node_ids}},
    )
    updated_image = pipeline.update_atelier_node(
        project_id,
        image_node.id,
        {"data": {**image_data, "parent_node_id": video_node.id, "reference_role": "video_reference_image"}},
    )
    return {"video_node": _compact_node(updated_video), "image_node": _compact_node(updated_image)}


def _execute_create_video_candidates(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    node_id = arguments.get("node_id")
    if not node_id:
        raise ValueError("node_id is required")
    prompt = arguments.get("prompt")
    if not prompt:
        raise ValueError("prompt is required")
    reference_image_urls = list(arguments.get("reference_image_urls") or [])
    reference_video_urls = list(arguments.get("reference_video_urls") or [])
    if not reference_image_urls:
        _, node = pipeline._get_atelier_node_pair(project_id, node_id)
        reference_image_urls = list((node.data or {}).get("reference_image_urls") or [])
    params = dict(arguments.get("params") or {})
    generation_mode = params.get("generation_mode")
    model = arguments.get("model") or "wan2.7-i2v"
    if generation_mode == "r2v" or str(model).endswith("-r2v"):
        route_model = resolve_r2v_route_model_id(model)
        reference_config = validate_r2v_reference_inputs(
            model_id=route_model,
            reference_video_urls=reference_video_urls,
            reference_image_urls=reference_image_urls,
        )
        if reference_config.reference_type == "video":
            raise ValueError("Atelier generation tool currently supports image-reference candidate generation only")
        model = route_model
    node = pipeline.create_atelier_video_candidates(
        project_id=project_id,
        node_id=node_id,
        prompt=prompt,
        model=model,
        reference_image_urls=reference_image_urls,
        batch_size=int(arguments.get("batch_size") or 3),
        params=params,
    )
    candidates = list((node.data or {}).get("candidates") or [])
    return {"node": _compact_node(node), "candidate_ids": [candidate.get("id") for candidate in candidates[-int(arguments.get("batch_size") or 3):]]}


def build_default_atelier_tool_registry() -> AtelierToolRegistry:
    registry = AtelierToolRegistry()
    registry.register(
        AtelierToolSpec(
            name="canvas.readProject",
            description="Read a compact snapshot of the current Atelier canvas project.",
            input_schema={"type": "object", "properties": {}},
            required_permission=READ_PERMISSION,
        ),
        _execute_read_project,
    )
    registry.register(
        AtelierToolSpec(
            name="canvas.createVideoNode",
            description="Create a video generation node on the Atelier canvas.",
            input_schema={"type": "object", "required": ["prompt"], "properties": {"title": {"type": "string"}, "prompt": {"type": "string"}, "model": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}}},
            required_permission=CANVAS_WRITE_PERMISSION,
            mutates_canvas=True,
            max_count_cost=1,
        ),
        _execute_create_video_node,
    )
    registry.register(
        AtelierToolSpec(
            name="canvas.updateNodePrompt",
            description="Update a node prompt, title, model, or prompt-adjacent data.",
            input_schema={"type": "object", "required": ["node_id"], "properties": {"node_id": {"type": "string"}, "title": {"type": "string"}, "prompt": {"type": "string"}, "model": {"type": "string"}}},
            required_permission=CANVAS_WRITE_PERMISSION,
            mutates_canvas=True,
        ),
        _execute_update_node_prompt,
    )
    registry.register(
        AtelierToolSpec(
            name="canvas.createReferenceImageNode",
            description="Create an image reference node from an existing media URL.",
            input_schema={"type": "object", "required": ["media_url"], "properties": {"media_url": {"type": "string"}, "title": {"type": "string"}, "prompt": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}}},
            required_permission=CANVAS_WRITE_PERMISSION,
            mutates_canvas=True,
            max_count_cost=1,
        ),
        _execute_create_reference_image_node,
    )
    registry.register(
        AtelierToolSpec(
            name="canvas.attachReferenceNode",
            description="Attach an image node to a video node as a generation reference.",
            input_schema={"type": "object", "required": ["video_node_id", "image_node_id"], "properties": {"video_node_id": {"type": "string"}, "image_node_id": {"type": "string"}}},
            required_permission=CANVAS_WRITE_PERMISSION,
            mutates_canvas=True,
        ),
        _execute_attach_reference_node,
    )
    registry.register(
        AtelierToolSpec(
            name="generation.createVideoCandidates",
            description="Queue candidate video generation for a video node.",
            input_schema={"type": "object", "required": ["node_id", "prompt"], "properties": {"node_id": {"type": "string"}, "prompt": {"type": "string"}, "model": {"type": "string"}, "reference_image_urls": {"type": "array", "items": {"type": "string"}}, "reference_video_urls": {"type": "array", "items": {"type": "string"}}, "batch_size": {"type": "integer"}, "params": {"type": "object"}}},
            required_permission=GENERATION_PERMISSION,
            mutates_canvas=True,
            requires_approval=True,
        ),
        _execute_create_video_candidates,
    )
    return registry
