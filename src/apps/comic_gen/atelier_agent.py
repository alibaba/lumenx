from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, Tuple

from .models import (
    AtelierAgentPlan,
    AtelierAgentPlanContext,
    AtelierAgentPlannerPackage,
    AtelierAgentPolicy,
    AtelierAgentToolCall,
    AtelierAgentToolStatus,
    AtelierAgentTurn,
    AtelierNode,
    AtelierProject,
)
from ...utils.model_catalog import get_default_model_settings, resolve_r2v_route_model_id, validate_r2v_reference_inputs

logger = logging.getLogger(__name__)


READ_PERMISSION = "read"
CANVAS_WRITE_PERMISSION = "canvas_write"
GENERATION_PERMISSION = "generation"
_DEFAULT_MODEL_SETTINGS = get_default_model_settings()
PLANNER_SCHEMA_VERSION = "atelier.agent.planner.v1"
TOOL_SCHEMA_VERSION = "atelier.tools.v1"
PLANNER_CONTEXT_INPUT_KEYS = {
    "schema_version",
    "adapter_name",
    "tool_schema_version",
    "model_trace_id",
    "skill_name",
}
PLANNER_OUTPUT_CONTRACT = {
    "schema_version": PLANNER_SCHEMA_VERSION,
    "tool_schema_version": TOOL_SCHEMA_VERSION,
    "required_top_level_keys": ["tool_calls"],
    "tool_call_shape": {
        "tool_name": "registered Atelier tool name",
        "arguments": "object matching the registered tool input_schema",
    },
    "execution_boundary": "Planner output is advisory. Execute only through /atelier/projects/{project_id}/agent/turns.",
}


# Alias keys used by multi-step planner output. `_alias` on a call's
# arguments dict marks the call as a producer (its result.node.id will be
# bound to that alias name within the turn). `<field>_alias` keys are
# consumer references (e.g. `video_node_id_alias: "draft_2"` resolves to
# the real `video_node_id` once the alias is bound). Aliases are
# turn-scoped — each run_turn invocation builds a fresh map.
_ALIAS_KEY = "_alias"
_ALIAS_SUFFIX = "_alias"


def _resolve_argument_aliases(
    arguments: Dict[str, Any],
    alias_map: Dict[str, str],
) -> Tuple[Dict[str, Any], Optional[str], List[str]]:
    """Translate `_alias` and `*_alias` keys against the turn alias map.

    Returns (resolved_arguments, produced_alias_name, unresolved_aliases).
    `produced_alias_name` is the value of `_alias` if present (the call
    intends to bind its result to this name). `unresolved_aliases` is the
    list of consumer alias keys that couldn't be resolved — callers should
    fail the call rather than passing literal alias strings to the
    executor (which would surface as "node not found" further down the
    stack and confuse the user).
    """
    out = dict(arguments)
    produced: Optional[str] = None
    raw_alias = out.pop(_ALIAS_KEY, None)
    if isinstance(raw_alias, str) and raw_alias:
        produced = raw_alias
    unresolved: List[str] = []
    for key in list(out.keys()):
        if key == _ALIAS_KEY or not key.endswith(_ALIAS_SUFFIX):
            continue
        real_key = key[: -len(_ALIAS_SUFFIX)]
        alias_value = out[key]
        if not isinstance(alias_value, str) or not alias_value:
            del out[key]
            continue
        if alias_value in alias_map:
            out[real_key] = alias_map[alias_value]
            del out[key]
        else:
            # Leave alias key in place so the caller can detect it; the
            # consumer alias remains so the call-level error message can
            # reference the symbolic name the planner emitted.
            unresolved.append(alias_value)
    return out, produced, unresolved


def _redact_planner_context_input(
    planner_input: Dict[str, Any],
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    context_input = {
        key: planner_input[key]
        for key in PLANNER_CONTEXT_INPUT_KEYS
        if key in planner_input
    }
    if tool_calls is not None:
        context_input["tool_calls"] = tool_calls
    return context_input


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


def _pluralize(noun: str, count: int) -> str:
    return noun if count == 1 else f"{noun}s"


def _build_turn_response(
    tool_calls: List[AtelierAgentToolCall],
    status: str,
    preview: bool,
) -> str:
    """Render a deterministic, sentence-case English summary of a turn.

    Tallies tool calls by (tool_name, status) and emits one short clause per
    category. The result is stable: same `tool_calls` list → same string.
    LLM-backed conversational responses are out of scope here (see Phase 1
    spec); this function is the v1 baseline so the UI can drop the static
    "Turn complete · N actions ran" derivation.
    """
    if not tool_calls:
        return "Reviewed canvas. No actions taken."

    completed: Dict[str, int] = {}
    approval_required: Dict[str, int] = {}
    failed_count = 0
    first_failed_error: Optional[str] = None
    denied_count = 0
    candidate_total = 0
    candidate_pending_total = 0

    for call in tool_calls:
        name = call.tool_name
        if call.status == AtelierAgentToolStatus.COMPLETED:
            completed[name] = completed.get(name, 0) + 1
            if name == "generation.createVideoCandidates":
                snapshot = call.result_snapshot or {}
                ids = snapshot.get("candidate_ids")
                if isinstance(ids, list):
                    candidate_total += len(ids)
        elif call.status == AtelierAgentToolStatus.APPROVAL_REQUIRED:
            approval_required[name] = approval_required.get(name, 0) + 1
            if name == "generation.createVideoCandidates":
                args = call.arguments or {}
                count = args.get("count")
                candidate_pending_total += int(count) if isinstance(count, int) and count > 0 else 1
        elif call.status == AtelierAgentToolStatus.FAILED:
            failed_count += 1
            if first_failed_error is None and call.error:
                first_failed_error = call.error
        elif call.status == AtelierAgentToolStatus.DENIED:
            denied_count += 1

    clauses: List[str] = []

    def add_completed(name: str, template: str) -> None:
        count = completed.get(name, 0)
        if count:
            clauses.append(template.format(count=count, noun_draft=_pluralize("draft", count)))

    add_completed("canvas.createVideoNode", "Created {count} video {noun_draft}.")
    n_update = completed.get("canvas.updateNodePrompt", 0)
    if n_update:
        clauses.append(f"Updated {n_update} node {_pluralize('prompt', n_update)}.")
    n_attach = completed.get("canvas.attachReferenceNode", 0)
    if n_attach:
        clauses.append(f"Attached {n_attach} reference {_pluralize('image', n_attach)}.")
    n_ref = completed.get("canvas.createReferenceImageNode", 0)
    if n_ref:
        clauses.append(f"Added {n_ref} reference image {_pluralize('node', n_ref)}.")
    n_region = completed.get("canvas.createRegion", 0)
    if n_region:
        clauses.append(f"Created {n_region} {_pluralize('region', n_region)}.")
    n_region_attach = completed.get("canvas.attachToRegion", 0)
    if n_region_attach:
        clauses.append(f"Attached {n_region_attach} {_pluralize('node', n_region_attach)} to a region.")
    n_region_detach = completed.get("canvas.detachFromRegion", 0)
    if n_region_detach:
        clauses.append(f"Detached {n_region_detach} {_pluralize('node', n_region_detach)} from a region.")
    if candidate_total:
        clauses.append(
            f"Generated {candidate_total} video {_pluralize('candidate', candidate_total)}."
        )

    if approval_required:
        pending_total = candidate_pending_total or sum(approval_required.values())
        clauses.append(
            f"Awaiting your approval to generate {pending_total} "
            f"{_pluralize('batch', pending_total)}."
        )

    if failed_count:
        action_word = _pluralize("action", failed_count)
        if first_failed_error:
            clauses.append(f"{failed_count} {action_word} failed: {first_failed_error.rstrip('.')}.")
        else:
            clauses.append(f"{failed_count} {action_word} failed.")

    if denied_count:
        clauses.append(
            f"Denied {denied_count} pending {_pluralize('action', denied_count)}."
        )

    if not clauses:
        if preview:
            count = len(tool_calls)
            return f"Previewed {count} {_pluralize('action', count)}."
        return "No actions ran."

    return " ".join(clauses)


def _compact_intent_title(intent: str) -> str:
    trimmed = " ".join(intent.strip().split())
    if not trimmed:
        return "Agent Video Node"
    return f"{trimmed[:32]}..." if len(trimmed) > 32 else trimmed


def _get_node_model_id(node: Optional[AtelierNode]) -> str:
    if node and isinstance((node.data or {}).get("model"), str):
        return str((node.data or {}).get("model"))
    return _DEFAULT_MODEL_SETTINGS.i2v_model


def _get_reference_image_urls(node: Optional[AtelierNode]) -> List[str]:
    if not node:
        return []
    refs = (node.data or {}).get("reference_image_urls")
    if not isinstance(refs, list):
        return []
    return [item for item in refs if isinstance(item, str) and item]


def _should_generate_candidates(intent: str) -> bool:
    normalized = intent.lower()
    return any(token in normalized for token in ("生成", "候选", "视频", "generate", "candidate", "render"))


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


class AtelierAgentPlanner(Protocol):
    name: str

    def plan(
        self,
        project: AtelierProject,
        user_message: str,
        selected_node_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        planner_input: Optional[Dict[str, Any]] = None,
    ) -> AtelierAgentPlan:
        ...


class AtelierPlannerRegistry:
    def __init__(self):
        self._planners: Dict[str, AtelierAgentPlanner] = {}

    def register(self, planner: AtelierAgentPlanner) -> None:
        if planner.name in self._planners:
            raise ValueError(f"Atelier agent planner already registered: {planner.name}")
        self._planners[planner.name] = planner

    def get(self, name: str) -> Optional[AtelierAgentPlanner]:
        return self._planners.get(name)


class DeterministicCorePlanner:
    name = "deterministic_core"

    def plan(
        self,
        project: AtelierProject,
        user_message: str,
        selected_node_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        planner_input: Optional[Dict[str, Any]] = None,
    ) -> AtelierAgentPlan:
        prompt = user_message.strip()
        context = AtelierAgentPlanContext(selected_node_id=selected_node_id)
        if not prompt:
            return AtelierAgentPlan(
                project_id=project.id,
                user_message=user_message,
                planner=self.name,
                skill_name=skill_name,
                status="blocked",
                reason="Enter an intent before previewing or executing.",
                context=context,
            )

        selected_node = None
        if selected_node_id:
            selected_node = next((node for node in project.nodes if node.id == selected_node_id), None)
            if not selected_node:
                return AtelierAgentPlan(
                    project_id=project.id,
                    user_message=user_message,
                    planner=self.name,
                    skill_name=skill_name,
                    status="blocked",
                    reason="Selected Atelier node was not found.",
                    context=context,
                )

        if _should_generate_candidates(prompt):
            if not selected_node or selected_node.type != "video":
                return AtelierAgentPlan(
                    project_id=project.id,
                    user_message=user_message,
                    planner=self.name,
                    skill_name=skill_name,
                    status="blocked",
                    reason="Video candidate generation requires a selected video node with reference images.",
                    context=context,
                )
            reference_image_urls = _get_reference_image_urls(selected_node)
            if not reference_image_urls:
                return AtelierAgentPlan(
                    project_id=project.id,
                    user_message=user_message,
                    planner=self.name,
                    skill_name=skill_name,
                    status="blocked",
                    reason="Video candidate generation requires at least one reference image on the selected node.",
                    context=context,
                )
            return AtelierAgentPlan(
                project_id=project.id,
                user_message=user_message,
                planner=self.name,
                skill_name=skill_name or "candidate-brief",
                status="ready",
                reason="Generate candidate videos for the selected node.",
                context=context,
                tool_calls=[
                    {
                        "tool_name": "generation.createVideoCandidates",
                        "arguments": {
                            "node_id": selected_node.id,
                            "prompt": selected_node.prompt or prompt,
                            "model": _get_node_model_id(selected_node),
                            "reference_image_urls": reference_image_urls,
                            "batch_size": 3,
                            "params": {
                                "duration": 5,
                                "resolution": "720p",
                                "prompt_extend": True,
                                "generation_mode": "i2v",
                            },
                        },
                    }
                ],
            )

        if selected_node and selected_node.type == "video":
            return AtelierAgentPlan(
                project_id=project.id,
                user_message=user_message,
                planner=self.name,
                skill_name=skill_name or "shot-variant-maker",
                status="ready",
                reason="Update the selected video node prompt.",
                context=context,
                tool_calls=[
                    {
                        "tool_name": "canvas.updateNodePrompt",
                        "arguments": {
                            "node_id": selected_node.id,
                            "prompt": prompt,
                            "model": _get_node_model_id(selected_node),
                        },
                    }
                ],
            )

        project_node_count = len(project.nodes)
        return AtelierAgentPlan(
            project_id=project.id,
            user_message=user_message,
            planner=self.name,
            skill_name=skill_name or "idea-to-canvas",
            status="ready",
            reason="Create a draft video node from the user intent.",
            context=context,
            tool_calls=[
                {
                    "tool_name": "canvas.createVideoNode",
                    "arguments": {
                        "title": _compact_intent_title(prompt),
                        "prompt": prompt,
                        "model": _DEFAULT_MODEL_SETTINGS.i2v_model,
                        "x": 160 + project_node_count * 36,
                        "y": 160 + project_node_count * 28,
                    },
                }
            ],
        )


# --- LLM-backed planning helper -------------------------------------------
#
# v0.8 item L: route the model_adapter planner through llm_adapter.LLMAdapter
# (DashScope qwen / OpenAI-compat) so the agent actually reasons over the
# canvas instead of replaying pre-validated tool_calls. The helper is module-
# level so tests can monkeypatch a single seam.
#
# Contract (matches PLANNER_OUTPUT_CONTRACT + the explicit JSON shape we ask
# the LLM to emit):
#   { "response": "<short assistant text>", "tool_calls": [ ... ] }
#
# Returns a dict — never raises — so the planner can map errors into a
# `blocked` plan with a helpful reason.

_AGENT_LLM_MODEL_ENV = "DASHSCOPE_AGENT_MODEL"


def _build_atelier_llm_system_prompt(package: AtelierAgentPlannerPackage) -> str:
    """Compose the system prompt fed to the LLM.

    Embeds the registered tools (names + schemas), the project snapshot, the
    selected node (if any), the policy, and the planner output contract. Kept
    deterministic so identical packages produce identical prompts (useful
    for caching / replay in the future).
    """
    schema_overview = [
        {
            "name": t.get("name"),
            "description": t.get("description"),
            "input_schema": t.get("input_schema"),
            "mutates_canvas": t.get("mutates_canvas"),
            "requires_approval": t.get("requires_approval"),
        }
        for t in (package.tool_schemas or [])
    ]
    project_snapshot = package.project_snapshot or {}
    sections: List[str] = [
        "You are the Atelier canvas agent. You help users compose, plan, and generate "
        "video shots on an infinite canvas. Be concise and concrete.",
        "",
        "OUTPUT FORMAT — respond ONLY with a single JSON object:",
        '  { "response": "<short assistant text, 1-2 sentences>", '
        '"tool_calls": [{ "tool_name": "...", "arguments": { ... } }] }',
        "",
        "Hard rules:",
        "- tool_name MUST be one of the registered Atelier tools.",
        "- arguments MUST match each tool's input_schema; omit unknown keys.",
        "- Never invent node IDs — only use IDs present in the canvas snapshot.",
        "- Respect policy.max_nodes_per_action — do not exceed it.",
        "- If the request is unclear, unsafe, or needs more context, return "
        "tool_calls: [] and explain why in `response`.",
        "- The user-visible `response` is what they read in chat — write it for them, "
        "not for a logger. Skip restating tool names.",
        "",
        "Planner output contract:",
        json.dumps(package.output_contract or {}, ensure_ascii=False),
        "",
        "Registered tools:",
        json.dumps(schema_overview, ensure_ascii=False),
        "",
        f"Canvas snapshot (scope={project_snapshot.get('scope', 'full')}, "
        f"node_count={project_snapshot.get('node_count', 0)}):",
        json.dumps(project_snapshot, ensure_ascii=False),
    ]
    if package.selected_node_snapshot:
        sections.extend([
            "",
            "Currently selected node:",
            json.dumps(package.selected_node_snapshot, ensure_ascii=False),
        ])
    sections.extend([
        "",
        "Project policy:",
        json.dumps(package.policy_snapshot or {}, ensure_ascii=False),
    ])
    return "\n".join(sections)


def call_atelier_llm_planner(
    package: AtelierAgentPlannerPackage,
    user_message: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the LLM call for the model-backed planner.

    Returns a dict shaped:
      {
        "ok": bool,
        "response": Optional[str],     # LLM-generated assistant text
        "tool_calls": List[Dict],      # parsed (unvalidated) tool calls
        "error": Optional[str],        # human-readable failure reason if !ok
      }
    Never raises — failures are reported via the `error` field so the planner
    can translate them into a `blocked` plan.
    """
    try:
        from .llm_adapter import LLMAdapter
    except Exception as exc:  # pragma: no cover — defensive import guard
        return {"ok": False, "response": None, "tool_calls": [], "error": f"LLM adapter unavailable: {exc}"}

    adapter = LLMAdapter()
    if not adapter.is_configured:
        key_name = "OPENAI_API_KEY" if adapter.provider == "openai" else "DASHSCOPE_API_KEY"
        return {
            "ok": False,
            "response": None,
            "tool_calls": [],
            "error": f"LLM not configured — set {key_name} to use the model-backed agent.",
        }

    system_prompt = _build_atelier_llm_system_prompt(package)
    payload_message = user_message.strip() or "(no message — propose a sensible next step)"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": payload_message},
    ]
    model_id = model or os.getenv(_AGENT_LLM_MODEL_ENV) or None
    try:
        raw = adapter.chat(messages, model=model_id, response_format={"type": "json_object"})
    except Exception as exc:
        return {"ok": False, "response": None, "tool_calls": [], "error": f"LLM call failed: {exc}"}

    if not raw or not isinstance(raw, str):
        return {"ok": False, "response": None, "tool_calls": [], "error": "LLM returned empty content."}

    parsed_payload: Any
    try:
        parsed_payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Some models wrap JSON in ```json fences even with response_format
        # set; try to recover before giving up.
        recovered = _extract_first_json_object(raw)
        if recovered is None:
            return {
                "ok": False,
                "response": None,
                "tool_calls": [],
                "error": f"LLM response was not valid JSON: {exc}",
            }
        parsed_payload = recovered

    if not isinstance(parsed_payload, dict):
        return {
            "ok": False,
            "response": None,
            "tool_calls": [],
            "error": "LLM response must be a JSON object with response + tool_calls.",
        }

    response_value = parsed_payload.get("response")
    if response_value is not None and not isinstance(response_value, str):
        response_value = str(response_value)

    raw_calls = parsed_payload.get("tool_calls")
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        return {
            "ok": False,
            "response": response_value,
            "tool_calls": [],
            "error": "LLM response 'tool_calls' must be an array.",
        }

    return {
        "ok": True,
        "response": response_value,
        "tool_calls": raw_calls,
        "error": None,
    }


def _extract_first_json_object(text: str) -> Optional[Any]:
    """Best-effort recovery of a JSON object embedded in fenced output.

    Some chat models will return ```json\n{...}\n``` despite a JSON-only
    response_format hint. We scan for a balanced `{ ... }` block and try to
    parse it.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start : i + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    return None
    return None


# --- LLM-backed STREAMING planning helper (v0.9 track P) ------------------
#
# Mirrors call_atelier_llm_planner but yields chunks as the LLM emits them so
# the agent panel can render the response token-by-token instead of waiting
# for the full payload. Contract (each yielded dict):
#
#   { "type": "delta", "text": "<chunk>" }
#   { "type": "done", "response": "<text>", "tool_calls": [...], "error": str? }
#
# A single trailing `done` event is always yielded; on parse failure it carries
# `error: "parse_failed"` plus the raw buffered text so the caller can decide
# whether to surface the buffer as a blocked plan or as the assistant's own
# explanation. Never raises — failures become a `done` event so the SSE route
# can finish cleanly.
def stream_atelier_llm_planner(
    package: AtelierAgentPlannerPackage,
    user_message: str,
    model: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    try:
        from .llm_adapter import LLMAdapter
    except Exception as exc:  # pragma: no cover — defensive import guard
        yield {
            "type": "done",
            "response": "",
            "tool_calls": [],
            "error": f"LLM adapter unavailable: {exc}",
        }
        return

    adapter = LLMAdapter()
    if not adapter.is_configured:
        key_name = "OPENAI_API_KEY" if adapter.provider == "openai" else "DASHSCOPE_API_KEY"
        yield {
            "type": "done",
            "response": "",
            "tool_calls": [],
            "error": f"LLM not configured — set {key_name} to use the model-backed agent.",
        }
        return

    system_prompt = _build_atelier_llm_system_prompt(package)
    payload_message = user_message.strip() or "(no message — propose a sensible next step)"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": payload_message},
    ]
    model_id = model or os.getenv(_AGENT_LLM_MODEL_ENV) or None

    buffer_parts: List[str] = []
    try:
        chunk_iter = adapter.stream_chat(messages, model=model_id, response_format={"type": "json_object"})
    except Exception as exc:
        yield {
            "type": "done",
            "response": "",
            "tool_calls": [],
            "error": f"LLM call failed: {exc}",
        }
        return

    try:
        for chunk in chunk_iter:
            if not chunk:
                continue
            buffer_parts.append(chunk)
            yield {"type": "delta", "text": chunk}
    except Exception as exc:
        # Mid-stream provider exception — fall through to the done event so
        # the route still emits a clean terminator.
        yield {
            "type": "done",
            "response": "".join(buffer_parts),
            "tool_calls": [],
            "error": f"LLM stream interrupted: {exc}",
        }
        return

    buffer = "".join(buffer_parts)

    if not buffer.strip():
        yield {
            "type": "done",
            "response": "",
            "tool_calls": [],
            "error": "LLM returned empty content.",
        }
        return

    parsed_payload: Any
    try:
        parsed_payload = json.loads(buffer)
    except json.JSONDecodeError:
        recovered = _extract_first_json_object(buffer)
        if recovered is None:
            # Surface the buffered text as the response so the UI can show
            # the model's own (non-JSON) explanation, but mark it as a
            # parse failure so the route returns it as a blocked plan.
            yield {
                "type": "done",
                "response": buffer,
                "tool_calls": [],
                "error": "parse_failed",
            }
            return
        parsed_payload = recovered

    if not isinstance(parsed_payload, dict):
        yield {
            "type": "done",
            "response": buffer,
            "tool_calls": [],
            "error": "LLM response must be a JSON object with response + tool_calls.",
        }
        return

    response_value = parsed_payload.get("response")
    if response_value is not None and not isinstance(response_value, str):
        response_value = str(response_value)

    raw_calls = parsed_payload.get("tool_calls")
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        yield {
            "type": "done",
            "response": response_value or "",
            "tool_calls": [],
            "error": "LLM response 'tool_calls' must be an array.",
        }
        return

    yield {
        "type": "done",
        "response": response_value or "",
        "tool_calls": raw_calls,
        "error": None,
    }


class ModelAdapterPlanner:
    name = "model_adapter"

    # Keys injected by AtelierAgentHarness.plan_turn that the planner needs
    # internally but should not surface in the persisted planner_input
    # context. Stripped from the redacted context payload below.
    _INTERNAL_INPUT_KEYS = {"_planner_package"}

    def __init__(self, tool_registry: AtelierToolRegistry):
        self.tool_registry = tool_registry

    def plan(
        self,
        project: AtelierProject,
        user_message: str,
        selected_node_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        planner_input: Optional[Dict[str, Any]] = None,
    ) -> AtelierAgentPlan:
        planner_input_payload = dict(planner_input or {})
        # Strip internal harness-injected fields before context redaction so
        # the persisted plan never carries the full planner_package payload.
        package: Optional[AtelierAgentPlannerPackage] = planner_input_payload.pop(
            "_planner_package", None
        ) if "_planner_package" in planner_input_payload else None
        context = AtelierAgentPlanContext(
            selected_node_id=selected_node_id,
            planner_input=_redact_planner_context_input(planner_input_payload),
            planner_schema_version=str(planner_input_payload.get("schema_version") or PLANNER_SCHEMA_VERSION),
            planner_adapter_name=self._optional_string(planner_input_payload.get("adapter_name")),
            tool_schema_version=str(planner_input_payload.get("tool_schema_version") or TOOL_SCHEMA_VERSION),
            model_trace_id=self._optional_string(planner_input_payload.get("model_trace_id")),
        )
        draft_calls = list(planner_input_payload.get("tool_calls") or [])
        llm_response_text: Optional[str] = None
        if not draft_calls:
            # No pre-validated tool_calls → fall through to the LLM call.
            # The harness injects `_planner_package`; without it we can't
            # build the prompt, so return blocked rather than calling the
            # LLM with empty context.
            if package is None:
                return AtelierAgentPlan(
                    project_id=project.id,
                    user_message=user_message,
                    planner=self.name,
                    skill_name=skill_name,
                    status="blocked",
                    reason=(
                        "Model planner requires either pre-validated tool_calls "
                        "or a planner_package — neither was provided."
                    ),
                    context=context,
                )
            llm_result = call_atelier_llm_planner(
                package,
                user_message,
                model=self._optional_string(planner_input_payload.get("model")),
            )
            if not llm_result.get("ok"):
                error_reason = llm_result.get("error") or "LLM planner produced no usable output."
                # Surface the LLM text even on failure so the UI can show
                # the model's own explanation when it refused to act.
                fallback_response = llm_result.get("response")
                logger.warning("Atelier LLM planner failed: %s", error_reason)
                return AtelierAgentPlan(
                    project_id=project.id,
                    user_message=user_message,
                    planner=self.name,
                    skill_name=skill_name or "model-planner",
                    status="blocked",
                    reason=error_reason,
                    context=context,
                    response=(fallback_response or None),
                )
            draft_calls = list(llm_result.get("tool_calls") or [])
            llm_response_text = llm_result.get("response") or None
            if not draft_calls:
                # LLM intentionally declined to act (tool_calls: []). Treat
                # as a blocked turn but carry the LLM's response so the UI
                # can show the explanation in chat.
                return AtelierAgentPlan(
                    project_id=project.id,
                    user_message=user_message,
                    planner=self.name,
                    skill_name=skill_name or "model-planner",
                    status="blocked",
                    reason=(llm_response_text or "Model planner returned no actions."),
                    context=context,
                    response=llm_response_text,
                )

        if context.planner_schema_version != PLANNER_SCHEMA_VERSION:
            return self._blocked(
                project,
                user_message,
                skill_name,
                context,
                f"Unsupported model planner schema_version: {context.planner_schema_version}",
            )
        if context.tool_schema_version != TOOL_SCHEMA_VERSION:
            return self._blocked(
                project,
                user_message,
                skill_name,
                context,
                f"Unsupported Atelier tool_schema_version: {context.tool_schema_version}",
            )

        sanitized_calls: List[Dict[str, Any]] = []
        for index, raw_call in enumerate(draft_calls):
            if not isinstance(raw_call, dict):
                return self._blocked(
                    project,
                    user_message,
                    skill_name,
                    context,
                    f"Model planner tool call #{index + 1} must be an object.",
                )
            tool_name = str(raw_call.get("tool_name") or raw_call.get("name") or "")
            if not tool_name or not self.tool_registry.get(tool_name):
                return self._blocked(
                    project,
                    user_message,
                    skill_name,
                    context,
                    f"Model planner proposed an unknown Atelier tool: {tool_name or '<empty>'}",
                )
            arguments = raw_call.get("arguments") or {}
            if not isinstance(arguments, dict):
                return self._blocked(
                    project,
                    user_message,
                    skill_name,
                    context,
                    f"Model planner arguments for {tool_name} must be an object.",
                )
            sanitized_calls.append({"tool_name": tool_name, "arguments": dict(arguments)})

        context.planner_input = _redact_planner_context_input(planner_input_payload, sanitized_calls)

        return AtelierAgentPlan(
            project_id=project.id,
            user_message=user_message,
            planner=self.name,
            skill_name=skill_name or str(planner_input_payload.get("skill_name") or "model-planner"),
            status="ready",
            reason=(
                llm_response_text
                or "Model planner produced a validated Atelier tool-call plan."
            ),
            tool_calls=sanitized_calls,
            context=context,
            response=llm_response_text,
        )

    @staticmethod
    def _optional_string(value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)

    def _blocked(
        self,
        project: AtelierProject,
        user_message: str,
        skill_name: Optional[str],
        context: AtelierAgentPlanContext,
        reason: str,
    ) -> AtelierAgentPlan:
        return AtelierAgentPlan(
            project_id=project.id,
            user_message=user_message,
            planner=self.name,
            skill_name=skill_name,
            status="blocked",
            reason=reason,
            context=context,
        )


# ---------------------------------------------------------------------------
# StructurePlanner — "Director's Console" mode.
#
# Maps a free-form intent like "3-shot story" or "4 motion variants" into a
# multi-tool-call plan that materializes a small cluster of nodes on the
# canvas. Complements DeterministicCorePlanner (single action) by handling
# the multi-node creation patterns RHTV / LibTV expose as their primary
# Director-mode output.
#
# Boundaries (by design):
#   - No model adapter call. Pattern matching is deterministic regex; this
#     keeps the planner reproducible, replayable, and offline-friendly.
#   - Only emits tool calls the registry already knows about. We do NOT
#     introduce new tools here — every plan is something the user could
#     have built by hand using the existing canvas.* / generation.* set.
#   - Bounded by policy.max_nodes_per_action; if the requested structure
#     would create more nodes than the policy allows, return blocked
#     instead of silently truncating (truncated plans confuse users about
#     why their "5-shot story" became 3).
#
# Recognized intents (kind → trigger phrases):
#   story_beats       —  "3-shot story" / "三镜头" / "setup turn payoff" / "N shots"
#   variants          —  "4 variants" / "N candidates" / "平行候选"
#   motion_study      —  "motion study" / "运镜变体"  (requires selected image ref)
#   character_ref     —  "character ref → video"      (requires selected image ref)
#   scene_ref         —  "scene ref → video"          (requires selected image ref)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StructureIntent:
    kind: str
    count: int
    label: str


_STRUCTURE_KINDS = {"story_beats", "variants", "motion_study", "character_ref", "scene_ref"}
_STORY_BEAT_NAMES = ["Setup", "Turn", "Payoff"]


def _classify_structure_intent(message: str) -> Optional[_StructureIntent]:
    text = " ".join(message.lower().split())
    if not text:
        return None

    # Numeric N-shot first — pin the count when the user is explicit.
    m = re.search(r"(\d+)\s*-?\s*(?:shots?|镜头|分镜|个镜头|个分镜|场)", text)
    if m:
        n = max(2, min(8, int(m.group(1))))
        return _StructureIntent(kind="story_beats", count=n, label=f"{n}-beat story")

    # 3-beat story shorthands ("setup turn payoff", "三幕", "三段式").
    if re.search(
        r"(三镜头|三幕|三段式|三场|setup[^\w]*turn[^\w]*payoff|开端[^\w]*转折[^\w]*结局|three[\s-]*shot|3[\s-]*shot|3[\s-]*beat|3[\s-]*act)",
        text,
    ):
        return _StructureIntent(kind="story_beats", count=3, label="3-beat story")

    # Motion study — "运镜变体" / "motion study". Default to 4 variants.
    if re.search(
        r"(运镜变体|运镜研究|motion[\s-]*study|motion[\s-]*variants?|camera[\s-]*variants?|镜头变体)",
        text,
    ):
        return _StructureIntent(kind="motion_study", count=4, label="motion study")

    # N variants / N candidates — explicit count.
    m = re.search(r"(\d+)\s*(?:个)?\s*(?:variants?|候选|平行候选|variation)", text)
    if m:
        n = max(2, min(6, int(m.group(1))))
        return _StructureIntent(kind="variants", count=n, label=f"{n} variants")

    if re.search(r"(平行候选|平行对比|variants?\b|candidates?\b|对比候选)", text):
        return _StructureIntent(kind="variants", count=4, label="4 variants")

    # Character / scene ref to video. The "→" / "-" / "to" / "→视频" chain
    # is what the user types when they think "use this image as a ref".
    if re.search(r"(角色参考|character[\s-]*ref|character[\s-]*reference|主角[\s-]*参考|character[\s-]*to[\s-]*video)", text):
        return _StructureIntent(kind="character_ref", count=1, label="character → video")

    if re.search(r"(场景参考|scene[\s-]*ref|scene[\s-]*reference|establishing[\s-]*shot|场景到视频)", text):
        return _StructureIntent(kind="scene_ref", count=1, label="scene → video")

    return None


class StructurePlanner:
    name = "structure"

    # World-coord step between successive draft cards. Matches the spacing
    # the front-end's workflow templates use so a Director-built cluster
    # and a template-built cluster look identical on the canvas.
    DRAFT_W = 240
    IMG_W = 244
    COL_GAP = 80
    ROW_GAP = 60
    DEFAULT_DROP_X = 160.0
    DEFAULT_DROP_Y = 160.0

    def plan(
        self,
        project: AtelierProject,
        user_message: str,
        selected_node_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        planner_input: Optional[Dict[str, Any]] = None,
    ) -> AtelierAgentPlan:
        planner_input_payload = dict(planner_input or {})
        prompt = (user_message or "").strip()
        context = AtelierAgentPlanContext(
            selected_node_id=selected_node_id,
            planner_input=_redact_planner_context_input(planner_input_payload),
        )

        intent = self._intent_from_inputs(prompt, planner_input_payload)

        if not prompt and not intent:
            return self._blocked(
                project, user_message, skill_name, context,
                "Enter an intent before previewing or executing.",
            )

        if not intent:
            return self._blocked(
                project, user_message, skill_name, context,
                "Couldn't recognize a structure pattern. Try '3-shot story', '4 variants', "
                "'motion study', 'character ref → video', or 'scene ref → video'.",
            )

        max_nodes = max(1, int(getattr(project.agent_policy, "max_nodes_per_action", 8) or 8))
        drop_x = self._coerce_float(planner_input_payload.get("drop_world_x"), self.DEFAULT_DROP_X)
        drop_y = self._coerce_float(planner_input_payload.get("drop_world_y"), self.DEFAULT_DROP_Y)

        selected_node = None
        if selected_node_id:
            selected_node = next((n for n in project.nodes if n.id == selected_node_id), None)
            if not selected_node:
                return self._blocked(
                    project, user_message, skill_name, context,
                    "Selected Atelier node was not found.",
                )

        model_id = _DEFAULT_MODEL_SETTINGS.i2v_model

        if intent.kind == "story_beats":
            tool_calls = self._build_story_beats(prompt, intent.count, drop_x, drop_y, model_id)
        elif intent.kind == "variants":
            tool_calls = self._build_variants(prompt, intent.count, drop_x, drop_y, model_id, selected_node)
        elif intent.kind == "motion_study":
            require = self._require_image_with_media(selected_node)
            if require:
                return self._blocked(project, user_message, skill_name, context, require)
            tool_calls = self._build_motion_study(prompt, intent.count, drop_x, drop_y, model_id, selected_node)
        elif intent.kind in ("character_ref", "scene_ref"):
            require = self._require_image_with_media(selected_node)
            if require:
                return self._blocked(project, user_message, skill_name, context, require)
            tool_calls = self._build_ref_to_video(prompt, drop_x, drop_y, model_id, selected_node, intent.kind)
        else:  # pragma: no cover — guarded by classifier
            tool_calls = []

        if not tool_calls:
            return self._blocked(
                project, user_message, skill_name, context,
                f"Structure planner produced no tool calls for kind '{intent.kind}'.",
            )

        # Count nodes the plan would create. attachReferenceNode + readProject
        # don't add nodes; only create* calls do. Bound by policy.
        node_creating = sum(
            1 for c in tool_calls
            if c.get("tool_name") in ("canvas.createVideoNode", "canvas.createReferenceImageNode")
        )
        if node_creating > max_nodes:
            return self._blocked(
                project, user_message, skill_name, context,
                f"Structure plan would create {node_creating} nodes, exceeding "
                f"policy.max_nodes_per_action ({max_nodes}). Lower the count and try again.",
            )

        return AtelierAgentPlan(
            project_id=project.id,
            user_message=user_message,
            planner=self.name,
            skill_name=skill_name or f"director-{intent.kind.replace('_', '-')}",
            status="ready",
            reason=f"Structured plan: {intent.label}",
            context=context,
            tool_calls=tool_calls,
        )

    # ---------- intent resolution ----------

    @classmethod
    def _intent_from_inputs(
        cls, prompt: str, planner_input_payload: Dict[str, Any]
    ) -> Optional[_StructureIntent]:
        explicit_kind = str(planner_input_payload.get("intent_kind") or "").strip() or None
        if explicit_kind:
            if explicit_kind not in _STRUCTURE_KINDS:
                return None
            raw_count = planner_input_payload.get("count")
            count = cls._coerce_count_for_kind(explicit_kind, raw_count)
            return _StructureIntent(
                kind=explicit_kind,
                count=count,
                label=f"{explicit_kind.replace('_', ' ')} (explicit)",
            )
        return _classify_structure_intent(prompt)

    @staticmethod
    def _coerce_count_for_kind(kind: str, raw: Any) -> int:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = {"story_beats": 3, "variants": 4, "motion_study": 4}.get(kind, 1)
        if kind == "story_beats":
            return max(2, min(8, n))
        if kind == "variants":
            return max(2, min(6, n))
        if kind == "motion_study":
            return max(2, min(6, n))
        return 1

    @staticmethod
    def _coerce_float(value: Any, fallback: float) -> float:
        try:
            return float(value) if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _require_image_with_media(node: Optional[AtelierNode]) -> Optional[str]:
        if not node or node.type != "image":
            return "This structure requires a selected image reference node — pick one and try again."
        if not _get_reference_image_urls_from_media(node):
            return "Selected image has no media yet — upload or generate one before building this structure."
        return None

    # ---------- tool call builders ----------

    def _build_story_beats(
        self, prompt: str, count: int, drop_x: float, drop_y: float, model_id: str,
    ) -> List[Dict[str, Any]]:
        beat_names = list(_STORY_BEAT_NAMES) + [f"Beat {i + 1}" for i in range(3, count)]
        beats = beat_names[:count]
        title = _compact_intent_title(prompt) or "Story"
        calls: List[Dict[str, Any]] = []
        for i, beat in enumerate(beats):
            calls.append({
                "tool_name": "canvas.createVideoNode",
                "arguments": {
                    "title": f"{title} · {beat}",
                    "prompt": f"{beat} — {prompt}".strip(" —"),
                    "model": model_id,
                    "x": drop_x,
                    "y": drop_y + i * (self.DRAFT_W * 0.5 + self.ROW_GAP),
                },
            })
        return calls

    def _build_variants(
        self,
        prompt: str,
        count: int,
        drop_x: float,
        drop_y: float,
        model_id: str,
        selected_node: Optional[AtelierNode],
    ) -> List[Dict[str, Any]]:
        # If a real image ref is selected, anchor variants to its right and
        # auto-attach the ref to every variant via the harness alias map.
        # Otherwise lay out variants horizontally with no attaches.
        title = _compact_intent_title(prompt) or "Variant"
        calls: List[Dict[str, Any]] = []
        anchor_x = drop_x
        anchor_y = drop_y
        attach_to: Optional[AtelierNode] = selected_node if (selected_node and selected_node.type == "image") else None
        if attach_to:
            anchor_x = float(attach_to.x or 0) + self.IMG_W + self.COL_GAP
            anchor_y = float(attach_to.y or 0)
        for i in range(count):
            alias = f"draft_{i + 1}"
            calls.append({
                "tool_name": "canvas.createVideoNode",
                "arguments": {
                    "title": f"{title} · v{i + 1}",
                    "prompt": prompt or f"Variant {i + 1}",
                    "model": model_id,
                    "x": anchor_x + (i % 2) * (self.DRAFT_W + self.COL_GAP),
                    "y": anchor_y + (i // 2) * (self.DRAFT_W * 0.5 + self.ROW_GAP),
                    _ALIAS_KEY: alias,
                },
            })
            if attach_to:
                calls.append({
                    "tool_name": "canvas.attachReferenceNode",
                    "arguments": {
                        "video_node_id_alias": alias,
                        "image_node_id": attach_to.id,
                    },
                })
        return calls

    def _build_motion_study(
        self,
        prompt: str,
        count: int,
        drop_x: float,
        drop_y: float,
        model_id: str,
        selected_node: Optional[AtelierNode],
    ) -> List[Dict[str, Any]]:
        # Selected image is mandatory (validated upstream). Stack drafts
        # vertically to the right of the ref so the cluster reads as
        # "this ref → these takes". Each createVideoNode binds an alias;
        # the paired attachReferenceNode resolves it to attach the ref.
        assert selected_node is not None
        anchor_x = float(selected_node.x or 0) + self.IMG_W + self.COL_GAP
        anchor_y = float(selected_node.y or 0)
        intents = ["Slow push", "Whip pan", "Pull back", "Hold still", "Orbit", "Push and tilt"][:count]
        calls: List[Dict[str, Any]] = []
        for i, intent in enumerate(intents):
            alias = f"motion_{i + 1}"
            calls.append({
                "tool_name": "canvas.createVideoNode",
                "arguments": {
                    "title": f"{intent}",
                    "prompt": (prompt or intent).strip(),
                    "model": model_id,
                    "x": anchor_x,
                    "y": anchor_y + i * (self.DRAFT_W * 0.5 + self.ROW_GAP),
                    _ALIAS_KEY: alias,
                },
            })
            calls.append({
                "tool_name": "canvas.attachReferenceNode",
                "arguments": {
                    "video_node_id_alias": alias,
                    "image_node_id": selected_node.id,
                },
            })
        return calls

    def _build_ref_to_video(
        self,
        prompt: str,
        drop_x: float,
        drop_y: float,
        model_id: str,
        selected_node: Optional[AtelierNode],
        kind: str,
    ) -> List[Dict[str, Any]]:
        # Selected image is mandatory (validated upstream). Place the new
        # draft to the right of the ref and auto-attach via alias.
        assert selected_node is not None
        intent_label = "Character shot" if kind == "character_ref" else "Establishing shot"
        anchor_x = float(selected_node.x or 0) + self.IMG_W + self.COL_GAP
        anchor_y = float(selected_node.y or 0)
        alias = "draft" if kind == "character_ref" else "establishing"
        return [
            {
                "tool_name": "canvas.createVideoNode",
                "arguments": {
                    "title": intent_label,
                    "prompt": prompt or intent_label,
                    "model": model_id,
                    "x": anchor_x,
                    "y": anchor_y,
                    _ALIAS_KEY: alias,
                },
            },
            {
                "tool_name": "canvas.attachReferenceNode",
                "arguments": {
                    "video_node_id_alias": alias,
                    "image_node_id": selected_node.id,
                },
            },
        ]

    # ---------- helpers ----------

    @staticmethod
    def _blocked(
        project: AtelierProject,
        user_message: str,
        skill_name: Optional[str],
        context: AtelierAgentPlanContext,
        reason: str,
    ) -> AtelierAgentPlan:
        return AtelierAgentPlan(
            project_id=project.id,
            user_message=user_message,
            planner=StructurePlanner.name,
            skill_name=skill_name,
            status="blocked",
            reason=reason,
            context=context,
        )


def _select_planner_snapshot_nodes(
    nodes: List[AtelierNode],
    selected_node: Optional[AtelierNode],
) -> Tuple[List[AtelierNode], str]:
    """Build the planner snapshot's node list.

    If any node carries data.agent_pinned == True, return ONLY:
      - the pinned nodes
      - the selected node (if any)
      - any image nodes referenced by the above (so refs don't dangle)
    Returned scope tag is "pinned" in that case, "full" otherwise.

    The pinned-set is the user's curated context — everything else is
    canvas noise from the planner's perspective. Per Codex doc §7.5
    selective-context guidance.
    """
    pinned_ids = {
        n.id for n in nodes
        if isinstance((n.data or {}).get("agent_pinned"), bool) and (n.data or {}).get("agent_pinned") is True
    }
    if not pinned_ids:
        return list(nodes), "full"

    keep_ids = set(pinned_ids)
    if selected_node and selected_node.id in {n.id for n in nodes}:
        keep_ids.add(selected_node.id)

    # Pull in image refs the kept nodes depend on so the planner sees
    # the supporting refs (e.g. a pinned video draft's image ref must
    # also be visible).
    by_id = {n.id: n for n in nodes}
    for nid in list(keep_ids):
        node = by_id.get(nid)
        if not node:
            continue
        for ref in (node.data or {}).get("reference_node_ids") or []:
            if isinstance(ref, str) and ref in by_id:
                keep_ids.add(ref)

    ordered = [n for n in nodes if n.id in keep_ids]
    return ordered, "pinned"


def _get_reference_image_urls_from_media(node: AtelierNode) -> List[str]:
    """Image nodes carry their bytes either at top-level media_urls or in
    data.media_urls (legacy). Either is treated as 'this node has media'."""
    urls = list(node.media_urls or [])
    if not urls:
        nested = (node.data or {}).get("media_urls")
        if isinstance(nested, list):
            urls = [u for u in nested if isinstance(u, str) and u]
    return urls


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
        self.planner_registry = build_default_atelier_planner_registry(self.registry)
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

    def build_planner_package(
        self,
        project_id: str,
        user_message: str = "",
        selected_node_id: Optional[str] = None,
        skill_name: Optional[str] = None,
    ) -> AtelierAgentPlannerPackage:
        project = self.pipeline.get_atelier_project(project_id)
        if not project:
            raise ValueError("Atelier project not found")
        selected_node = None
        if selected_node_id:
            selected_node = next((node for node in project.nodes if node.id == selected_node_id), None)
            if not selected_node:
                raise ValueError("Selected Atelier node was not found")
        # Selective context (Codex doc §7.5): if any node in the project
        # carries data.agent_pinned == True, narrow the snapshot to those
        # pinned nodes plus any references they directly depend on plus
        # the selected node. The full canvas can grow large fast — sending
        # everything every turn pollutes the planner with noise and
        # bloats token cost. Pinned nodes are the user's curated context.
        nodes_for_snapshot, scope = _select_planner_snapshot_nodes(
            project.nodes, selected_node
        )
        return AtelierAgentPlannerPackage(
            project_id=project.id,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner_schema_version=PLANNER_SCHEMA_VERSION,
            tool_schema_version=TOOL_SCHEMA_VERSION,
            output_contract=dict(PLANNER_OUTPUT_CONTRACT),
            tool_schemas=self.list_tool_specs(),
            project_snapshot={
                "id": project.id,
                "title": project.title,
                "description": project.description,
                "node_count": len(project.nodes),
                "scope": scope,
                "nodes": [_compact_node(node) for node in nodes_for_snapshot],
            },
            selected_node_snapshot=_compact_node(selected_node) if selected_node else None,
            policy_snapshot=project.agent_policy.model_dump(mode="json"),
        )

    def plan_turn(
        self,
        project_id: str,
        user_message: str,
        selected_node_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        planner_name: Optional[str] = None,
        planner_input: Optional[Dict[str, Any]] = None,
    ) -> AtelierAgentPlan:
        project = self.pipeline.get_atelier_project(project_id)
        if not project:
            raise ValueError("Atelier project not found")

        planner = self.planner_registry.get(planner_name or DeterministicCorePlanner.name)
        if not planner:
            return AtelierAgentPlan(
                project_id=project.id,
                user_message=user_message,
                planner=planner_name or "",
                skill_name=skill_name,
                status="blocked",
                reason=f"Unknown Atelier agent planner: {planner_name}",
                context=AtelierAgentPlanContext(
                    selected_node_id=selected_node_id,
                    planner_input=_redact_planner_context_input(dict(planner_input or {})),
                ),
            )
        planner_input_payload = dict(planner_input or {})
        # v0.8 item L: when the LLM-backed planner is selected and the
        # caller didn't pre-supply tool_calls, build the planner_package
        # here and thread it through `_planner_package` so the planner can
        # construct the LLM prompt without re-fetching state. The key is
        # consumed inside ModelAdapterPlanner and never persisted.
        is_llm_planner = (planner_name == ModelAdapterPlanner.name)
        has_predigested_calls = bool(planner_input_payload.get("tool_calls"))
        if is_llm_planner and not has_predigested_calls and "_planner_package" not in planner_input_payload:
            try:
                planner_input_payload["_planner_package"] = self.build_planner_package(
                    project_id=project_id,
                    user_message=user_message,
                    selected_node_id=selected_node_id,
                    skill_name=skill_name,
                )
            except ValueError:
                # Re-raise so the API surface returns 4xx; build failure
                # means the project / selected node doesn't exist.
                raise
        return planner.plan(
            project=project,
            user_message=user_message,
            selected_node_id=selected_node_id,
            skill_name=skill_name,
            planner_input=planner_input_payload,
        )

    def run_turn(
        self,
        project_id: str,
        tool_calls: List[Dict[str, Any]],
        user_message: str = "",
        preview: bool = False,
        approve: bool = False,
        deny: bool = False,
        turn_id: Optional[str] = None,
        assistant_response: Optional[str] = None,
    ) -> AtelierAgentTurn:
        """Synchronous facade — drains the streaming generator and returns the
        persisted turn carried by the terminal `turn_done` event. Kept so the
        sync /agent/turns route and existing callers (approval/deny flows,
        preview replays, tests) keep their `-> AtelierAgentTurn` shape.
        """
        for event in self.run_turn_streaming(
            project_id=project_id,
            tool_calls=tool_calls,
            user_message=user_message,
            preview=preview,
            approve=approve,
            deny=deny,
            turn_id=turn_id,
            assistant_response=assistant_response,
        ):
            if event.get("type") == "turn_done":
                turn = event.get("turn")
                if isinstance(turn, AtelierAgentTurn):
                    return turn
                raise RuntimeError(
                    "run_turn_streaming yielded a turn_done event without an AtelierAgentTurn payload"
                )
        raise RuntimeError("run_turn_streaming closed without a turn_done event")

    # v1.0 track T — per-tool-call streaming generator. Yields one dict per
    # event so the SSE route can flush a `tool_start` chip to the panel the
    # moment an executor is invoked and a `tool_done` chip when it returns
    # (success or fail). The terminal `turn_done` carries the persisted
    # AtelierAgentTurn. Failing tool calls do NOT abort the loop — downstream
    # consumer calls that depended on the failed producer's alias surface as
    # their own `tool_done(status="failed")` via the existing alias-resolution
    # path. Event shapes:
    #
    #   {"type": "tool_start", "call_id", "tool_name", "arguments"}
    #   {"type": "tool_done",  "call_id", "tool_name", "status",
    #                            "result_snapshot": dict|None, "error": str|None}
    #   {"type": "turn_done",  "turn": AtelierAgentTurn, "status": str,
    #                            "error": str|None}
    #
    # Non-executor branches (denied / approval_required / preview / unresolved
    # alias) emit a single `tool_done` with the matching status and no
    # `tool_start` — nothing actually ran for them, so the wire timeline stays
    # truthful.
    def run_turn_streaming(
        self,
        project_id: str,
        tool_calls: List[Dict[str, Any]],
        user_message: str = "",
        preview: bool = False,
        approve: bool = False,
        deny: bool = False,
        turn_id: Optional[str] = None,
        assistant_response: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        project = self.pipeline.get_atelier_project(project_id)
        if not project:
            raise ValueError("Atelier project not found")

        pending_turns = [candidate for candidate in project.agent_turns if candidate.status == "waiting_approval"]
        if approve and deny:
            raise ValueError("Cannot approve and deny the same Atelier agent turn")
        if approve and preview:
            raise ValueError("Approval cannot run in preview mode")
        if deny and preview:
            raise ValueError("Denial cannot run in preview mode")

        if deny:
            if not turn_id:
                raise ValueError("turn_id is required when denying an Atelier agent turn")
            turn = next((candidate for candidate in project.agent_turns if candidate.id == turn_id), None)
            if not turn:
                raise ValueError("Atelier agent turn not found")
            if turn.status != "waiting_approval":
                raise ValueError("Atelier agent turn is not waiting for approval")
            denied_calls = [
                call for call in turn.tool_calls
                if call.status == AtelierAgentToolStatus.APPROVAL_REQUIRED
            ]
            if not denied_calls:
                raise ValueError("Atelier agent turn has no approval-required tool calls")
            for call in denied_calls:
                call.status = AtelierAgentToolStatus.DENIED
                call.approval_required = True
                call.approval_granted = False
                call.error = "User denied approval"
                call.completed_at = time.time()
            turn.user_message = user_message or turn.user_message
            turn.preview = False
            turn.status = "failed"
            turn.completed_at = time.time()
            # Denial branch: deterministic summary always — LLM didn't
            # author this terminal state, the user did.
            turn.response = _build_turn_response(turn.tool_calls, turn.status, preview=False)
            project.updated_at = time.time()
            self.pipeline._save_atelier_data()
            # Emit one tool_done per denied call so the streaming UI can
            # flip the corresponding chips to "denied" without needing a
            # separate event channel for the user-initiated denial.
            for call in denied_calls:
                yield {
                    "type": "tool_done",
                    "call_id": call.call_id,
                    "tool_name": call.tool_name,
                    "status": call.status.value if hasattr(call.status, "value") else str(call.status),
                    "result_snapshot": None,
                    "error": call.error,
                }
            yield {
                "type": "turn_done",
                "turn": turn,
                "status": turn.status,
                "error": None,
            }
            return

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
        # Turn-scoped alias bindings. Producer calls (`_alias` set) bind
        # their result.node.id here; consumer calls (`*_alias` keys)
        # resolve against this map at executor-call time. Reset on every
        # run_turn so aliases never leak across turns.
        alias_map: Dict[str, str] = {}

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
                # No tool_start was emitted — the policy gate rejected
                # before any executor ran. The chip rail still needs a
                # terminal status, so emit tool_done with status="denied".
                yield {
                    "type": "tool_done",
                    "call_id": call.call_id,
                    "tool_name": tool_name,
                    "status": AtelierAgentToolStatus.DENIED.value,
                    "result_snapshot": None,
                    "error": reason,
                }
                continue

            if policy_status == AtelierAgentToolStatus.APPROVAL_REQUIRED.value and not approve:
                call.status = AtelierAgentToolStatus.APPROVAL_REQUIRED
                call.approval_required = True
                if existing_call is None:
                    turn.tool_calls.append(call)
                yield {
                    "type": "tool_done",
                    "call_id": call.call_id,
                    "tool_name": tool_name,
                    "status": AtelierAgentToolStatus.APPROVAL_REQUIRED.value,
                    "result_snapshot": None,
                    "error": None,
                }
                continue

            if preview:
                call.status = AtelierAgentToolStatus.PROPOSED
                call.approval_required = policy_status == AtelierAgentToolStatus.APPROVAL_REQUIRED.value
                # Register a placeholder alias so chained preview calls
                # render with meaningful symbolic ids (the user sees
                # "draft_1 → ref" rather than two unrelated calls).
                preview_alias = arguments.get(_ALIAS_KEY)
                if isinstance(preview_alias, str) and preview_alias:
                    alias_map[preview_alias] = f"<preview:{preview_alias}>"
                if existing_call is None:
                    turn.tool_calls.append(call)
                yield {
                    "type": "tool_done",
                    "call_id": call.call_id,
                    "tool_name": tool_name,
                    "status": AtelierAgentToolStatus.PROPOSED.value,
                    "result_snapshot": None,
                    "error": None,
                }
                continue

            assert entry is not None
            spec, executor = entry
            # Resolve aliases just before executor call. Stored
            # call.arguments keeps the symbolic form so approval-flow
            # payload-match still works (the user-visible plan still
            # carries `_alias` / `*_alias` keys).
            resolved_arguments, produced_alias, unresolved = _resolve_argument_aliases(
                arguments, alias_map
            )
            if unresolved:
                call.status = AtelierAgentToolStatus.FAILED
                call.error = f"Unresolved planner aliases: {', '.join(sorted(set(unresolved)))}"
                call.completed_at = time.time()
                if existing_call is None:
                    turn.tool_calls.append(call)
                # Alias resolution failure happens BEFORE executor invocation,
                # so emit a synthetic tool_start so the chip lands on the rail
                # and immediately resolve it with the failure reason. Keeps the
                # start/done pairing invariant the frontend relies on.
                yield {
                    "type": "tool_start",
                    "call_id": call.call_id,
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                }
                yield {
                    "type": "tool_done",
                    "call_id": call.call_id,
                    "tool_name": tool_name,
                    "status": AtelierAgentToolStatus.FAILED.value,
                    "result_snapshot": None,
                    "error": call.error,
                }
                continue
            # Executor branch — emit tool_start, run, then tool_done. Failure
            # is caught and emitted as tool_done(status="failed") so the loop
            # keeps walking downstream calls (their unresolved-alias errors
            # surface on their own chips).
            yield {
                "type": "tool_start",
                "call_id": call.call_id,
                "tool_name": tool_name,
                "arguments": dict(resolved_arguments),
            }
            try:
                result = executor(project_id, resolved_arguments, self.pipeline)
                call.status = AtelierAgentToolStatus.COMPLETED
                call.approval_required = policy_status == AtelierAgentToolStatus.APPROVAL_REQUIRED.value
                call.approval_granted = call.approval_required and approve
                call.result_snapshot = result
                call.completed_at = time.time()
                # Bind the alias once the producer call succeeds.
                if produced_alias and isinstance(result, dict):
                    node_payload = result.get("node")
                    if isinstance(node_payload, dict):
                        node_id = node_payload.get("id")
                        if isinstance(node_id, str) and node_id:
                            alias_map[produced_alias] = node_id
                yield {
                    "type": "tool_done",
                    "call_id": call.call_id,
                    "tool_name": tool_name,
                    "status": AtelierAgentToolStatus.COMPLETED.value,
                    "result_snapshot": result if isinstance(result, dict) else None,
                    "error": None,
                }
            except Exception as exc:
                call.status = AtelierAgentToolStatus.FAILED
                call.error = str(exc)
                call.completed_at = time.time()
                yield {
                    "type": "tool_done",
                    "call_id": call.call_id,
                    "tool_name": tool_name,
                    "status": AtelierAgentToolStatus.FAILED.value,
                    "result_snapshot": None,
                    "error": str(exc),
                }
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

        # v0.8 item L: when the caller forwarded the LLM's own assistant_response
        # (the `response` field from a ModelAdapterPlanner plan), prefer it
        # over the deterministic English summary so the chat surface reads
        # like an actual reply. Fall back to the deterministic summary when
        # absent, empty, or the turn failed before any LLM-authored text
        # was produced.
        if assistant_response and assistant_response.strip():
            turn.response = assistant_response.strip()
        else:
            turn.response = _build_turn_response(turn.tool_calls, turn.status, turn.preview)
        if appending_new_turn:
            project.agent_turns.append(turn)
        project.updated_at = time.time()
        self.pipeline._save_atelier_data()
        yield {
            "type": "turn_done",
            "turn": turn,
            "status": turn.status,
            "error": None,
        }


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
    # N:M attachments: an image can be referenced by any number of video
    # nodes. (The earlier 1:1 lock was a vestige of the pre-graph-first
    # design and made shared character / scene refs impossible — see the
    # motion_study / character_ref structure planner patterns.) Edge
    # uniqueness is still enforced *per video* via the reference_node_ids
    # check below, so re-attaching the same ref to the same video is a
    # no-op rather than a duplicate.
    image_data = dict(image_node.data or {})
    image_url = image_node.media_urls[0]
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
    # parent_node_id keeps its first-attacher semantics for the
    # back-pointer used by buildReferenceLinks (frontend) — we no longer
    # enforce uniqueness, but we also don't overwrite the existing value
    # if the image is being attached to a second video. Subsequent edges
    # are derivable from the videos' reference_node_ids list.
    next_image_data = {
        **image_data,
        "reference_role": "video_reference_image",
    }
    if not image_data.get("parent_node_id"):
        next_image_data["parent_node_id"] = video_node.id
    updated_image = pipeline.update_atelier_node(
        project_id,
        image_node.id,
        {"data": next_image_data},
    )
    return {"video_node": _compact_node(updated_video), "image_node": _compact_node(updated_image)}


def _execute_create_region(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    # Region (B-α): a type:"region" container node. Children are bound
    # via `data.region_id` set on each child by separate attachToRegion
    # calls. v1 does not nest, so we don't wire region_id on the region
    # node itself.
    title = arguments.get("title") or "Region"
    color = arguments.get("color") or "default"
    region = pipeline.create_atelier_node(
        project_id,
        {
            "type": "region",
            "title": title,
            "status": "completed",
            "x": float(arguments.get("x", 80.0)),
            "y": float(arguments.get("y", 80.0)),
            "width": float(arguments.get("width", 600.0)),
            "height": float(arguments.get("height", 400.0)),
            "data": {"color": color},
            "created_by": "agent",
        },
    )
    return {"region": _compact_node(region), "node": _compact_node(region)}


def _execute_attach_to_region(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    node_id = arguments.get("node_id")
    region_id = arguments.get("region_id")
    if not node_id:
        raise ValueError("node_id is required")
    if not region_id:
        raise ValueError("region_id is required")
    project, node = pipeline._get_atelier_node_pair(project_id, node_id)
    region = next((n for n in project.nodes if n.id == region_id), None)
    if region is None:
        raise ValueError("region_id does not reference an existing node")
    if region.type != "region":
        raise ValueError("region_id must reference a node of type 'region'")
    if node.type == "region":
        raise ValueError("region nodes cannot be attached to other regions (no nesting in v1)")
    next_data = {**dict(node.data or {}), "region_id": region_id}
    updated = pipeline.update_atelier_node(project_id, node_id, {"data": next_data})
    return {"node": _compact_node(updated)}


def _execute_detach_from_region(project_id: str, arguments: Dict[str, Any], pipeline: Any) -> Dict[str, Any]:
    node_id = arguments.get("node_id")
    if not node_id:
        raise ValueError("node_id is required")
    _, node = pipeline._get_atelier_node_pair(project_id, node_id)
    next_data = {k: v for k, v in (node.data or {}).items() if k != "region_id"}
    updated = pipeline.update_atelier_node(project_id, node_id, {"data": next_data})
    return {"node": _compact_node(updated)}


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
            name="canvas.createRegion",
            description="Create a region (board) container on the canvas. Child nodes attach via canvas.attachToRegion. Regions cannot nest in v1.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "color": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                },
            },
            required_permission=CANVAS_WRITE_PERMISSION,
            mutates_canvas=True,
            max_count_cost=1,
        ),
        _execute_create_region,
    )
    registry.register(
        AtelierToolSpec(
            name="canvas.attachToRegion",
            description="Attach an existing canvas node to a region (sets data.region_id). Errors if region_id does not reference a region, or if the target is itself a region.",
            input_schema={
                "type": "object",
                "required": ["node_id", "region_id"],
                "properties": {
                    "node_id": {"type": "string"},
                    "region_id": {"type": "string"},
                },
            },
            required_permission=CANVAS_WRITE_PERMISSION,
            mutates_canvas=True,
        ),
        _execute_attach_to_region,
    )
    registry.register(
        AtelierToolSpec(
            name="canvas.detachFromRegion",
            description="Detach a node from any region it is currently attached to. No-op if not attached.",
            input_schema={
                "type": "object",
                "required": ["node_id"],
                "properties": {"node_id": {"type": "string"}},
            },
            required_permission=CANVAS_WRITE_PERMISSION,
            mutates_canvas=True,
        ),
        _execute_detach_from_region,
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


def build_default_atelier_planner_registry(
    tool_registry: Optional[AtelierToolRegistry] = None,
) -> AtelierPlannerRegistry:
    registry = AtelierPlannerRegistry()
    registry.register(DeterministicCorePlanner())
    registry.register(ModelAdapterPlanner(tool_registry or build_default_atelier_tool_registry()))
    registry.register(StructurePlanner())
    return registry
