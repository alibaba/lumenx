"""Product-owned Atelier state models.

These models are transplanted from the fixed Atelier candidate snapshot
(54ff0dcc8b67c1442f0b4f6b4c187fb5fd496d92) so P2A can build storage and API
seams without coupling Atelier state to Studio's comic_gen models. They avoid
runtime, provider SDK, media I/O, and Studio imports by design; neutral durable
provider contract models from ``src.models`` are allowed for persisted identity.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from ...models.provider_execution import ProviderExecutionRecord
from .product_skills import AtelierProductSkillSnapshot
from .skill_runtime import SkillExecutionBinding


class AtelierApprovalMode(str, Enum):
    UNTRUSTED = "untrusted"
    ON_FAILURE = "on_failure"
    ON_REQUEST = "on_request"
    NEVER = "never"


class AtelierAgentPolicy(BaseModel):
    approval_mode: AtelierApprovalMode = Field(
        AtelierApprovalMode.UNTRUSTED,
        description="Approval mode for agent actions on the canvas",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Canvas tool names the agent may call; empty means no project-level restriction",
    )
    max_nodes_per_action: int = Field(
        8,
        ge=1,
        le=50,
        description="Upper bound for nodes an agent may create in one action",
    )
    max_tokens_per_turn: int = Field(
        2048,
        ge=128,
        le=16384,
        description=(
            "Maximum completion tokens the LLM may emit per turn. Passed "
            "through to the ProviderClient so provider-side truncation "
            "applies. Default 2048 — enough for a 6-tool plan with "
            "response text but not unbounded. (v1.5 #10)"
        ),
    )
    enable_reviewer: bool = Field(
        False,
        description=(
            "When True, tools with mutation_scope='generation_write' are "
            "screened by the AtelierReviewer guardian before execution. "
            "Guardian checks: budget_blowup, duplicate_with_existing_take, "
            "prompt_violates_policy (safety). Default False — opt-in. (v1.5 #11)"
        ),
    )
    max_generation_cost_per_turn: float = Field(
        5.0,
        ge=0.0,
        description=(
            "Budget threshold in CNY per turn for generation calls. The "
            "reviewer escalates to user when estimated cost exceeds this. "
            "(v1.5 #11)"
        ),
    )
    updated_at: float = Field(default_factory=time.time)


class AtelierAgentToolStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETED = "completed"
    DENIED = "denied"
    FAILED = "failed"


class AtelierAgentIterationRecord(BaseModel):
    """Per-iteration LLM round metrics + linkage."""

    idx: int = Field(..., description="1-based iteration index within the turn")
    provider: str = Field("", description="Provider id (dashscope/openai/anthropic) used this round")
    model_id: str = Field("", description="Concrete model id resolved post-fallback for this round")
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    tool_call_ids: list[str] = Field(
        default_factory=list,
        description="call_ids of AtelierAgentToolCall entries that fired in this iteration",
    )
    started_at: float = Field(default_factory=time.time)
    completed_at: float | None = None
    error: str | None = Field(
        None,
        description="Optional human-readable error if this iteration's LLM call failed",
    )


class AtelierAgentPlannerInvocationUsage(BaseModel):
    """Provider-reported usage for one durable planner invocation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    prompt_tokens: int | None = Field(None, ge=0, strict=True)
    completion_tokens: int | None = Field(None, ge=0, strict=True)
    total_tokens: int | None = Field(None, ge=0, strict=True)


class AtelierAgentPlannerMessage(BaseModel):
    """Strict message shape accepted by model-backed planner invocations."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class AtelierAgentPlannerConfigIdentity(BaseModel):
    """Secret-free planner runtime/config identity frozen at claim time."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    provider_scope: str = Field(..., min_length=1)
    runtime_config_id: str | None = Field(None, min_length=1)
    model_config_version: str | None = Field(None, min_length=1)
    credential_ref: str | None = Field(
        None,
        min_length=1,
        description="Non-secret credential binding reference; never a key, token or header value.",
    )
    storage_identity: str | None = Field(None, min_length=1)

    @field_validator("provider_scope", "runtime_config_id", "model_config_version", "credential_ref", "storage_identity")
    @classmethod
    def _reject_secret_shaped_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.lower()
        secret_markers = (
            "authorization",
            "bearer ",
            "api_key",
            "apikey",
            "secret",
            "token=",
            "sk-",
            "http://",
            "https://",
        )
        if any(marker in lowered for marker in secret_markers):
            raise ValueError("Planner config identity must be secret-free and non-endpoint identity only")
        return value

    @model_serializer(mode="wrap")
    def _serialize_planner_config_identity(self, handler):
        data = handler(self)
        for field in ("runtime_config_id", "model_config_version", "credential_ref", "storage_identity"):
            if data.get(field) is None:
                data.pop(field, None)
        return data


class AtelierAgentPlannerInvocation(BaseModel):
    """Durable claim/reservation ledger for one model-backed planner call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    invocation_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    turn_id: str = Field(..., min_length=1)
    iteration_index: int = Field(..., ge=1, le=8, strict=True)
    planner: Literal["model_adapter"]
    provider_id: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1)
    config_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    config_identity: AtelierAgentPlannerConfigIdentity
    input_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    input_messages: list[AtelierAgentPlannerMessage] = Field(default_factory=list)
    max_completion_tokens: int = Field(..., ge=1, le=16384, strict=True)
    timeout_seconds: float = Field(..., gt=0, allow_inf_nan=False)
    budget_completion_tokens: int = Field(..., ge=0, le=16384, strict=True)
    reserved_completion_tokens: int = Field(..., ge=1, le=16384, strict=True)
    status: Literal["claimed", "settled"] = "claimed"
    claim_request_id: str = Field(..., min_length=1)
    claim_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    claimed_at: float = Field(..., allow_inf_nan=False)
    settled_at: float | None = Field(None, allow_inf_nan=False)
    settlement_fingerprint: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    outcome: Literal["success", "failed", "timeout_unknown"] | None = None
    result_content: str | None = None
    provider_model: str | None = None
    usage: AtelierAgentPlannerInvocationUsage | None = None
    charged_completion_tokens: int | None = Field(None, ge=0, strict=True)
    error_code: str | None = Field(None, min_length=1, max_length=128)
    error_message: str | None = Field(None, min_length=1, max_length=500)

    @model_serializer(mode="wrap")
    def _serialize_planner_invocation(self, handler):
        data = handler(self)
        for field in (
            "settled_at",
            "settlement_fingerprint",
            "outcome",
            "result_content",
            "provider_model",
            "usage",
            "charged_completion_tokens",
            "error_code",
            "error_message",
        ):
            if data.get(field) is None:
                data.pop(field, None)
        return data


class AtelierAgentAuthorizationRef(BaseModel):
    """Frozen reference granting one approved Agent generation submit right."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    project_id: str = Field(..., min_length=1)
    turn_id: str = Field(..., min_length=1)
    call_id: str = Field(..., min_length=1)
    approval_id: str = Field(..., min_length=1)
    tool_name: Literal["generation.createVideoCandidates"]
    tool_schema_version: str = Field(..., min_length=1)
    authorization_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    preview_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    server_storage_identity: str = Field(..., min_length=1)
    policy_snapshot_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class AtelierAgentApprovalRecord(BaseModel):
    """User decision for a prepared Agent generation call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    approval_id: str = Field(..., min_length=1)
    authorization_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "denied", "revoked"]
    decided_at: float = Field(..., allow_inf_nan=False)


class AtelierAgentGenerationBinding(BaseModel):
    """Durable, secret-free preview binding for an Agent generation proposal."""

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    preview_public_record: dict[str, Any]
    provider_execution: ProviderExecutionRecord
    agent_authorization_ref: AtelierAgentAuthorizationRef
    created_at: float = Field(..., allow_inf_nan=False)


class AtelierAgentCanvasAuthorizationRef(BaseModel):
    """Frozen reference granting one approved local Agent canvas mutation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    project_id: str = Field(..., min_length=1)
    turn_id: str = Field(..., min_length=1)
    call_id: str = Field(..., min_length=1)
    approval_id: str = Field(..., min_length=1)
    tool_name: Literal["canvas.createVideoNode", "canvas.updateNodePrompt"]
    tool_schema_version: str = Field(..., min_length=1)
    authorization_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    arguments_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    target_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    server_storage_identity: str = Field(..., min_length=1)
    policy_snapshot_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class AtelierAgentCanvasApprovalRecord(BaseModel):
    """User decision for a prepared local Agent canvas mutation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    approval_id: str = Field(..., min_length=1)
    authorization_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "denied", "revoked"]
    decided_at: float = Field(..., allow_inf_nan=False)


class AtelierAgentCanvasBinding(BaseModel):
    """Durable, secret-free binding for an approved local canvas mutation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    normalized_arguments: dict[str, Any]
    target_state: dict[str, Any]
    canvas_authorization_ref: AtelierAgentCanvasAuthorizationRef
    created_at: float = Field(..., allow_inf_nan=False)


class AtelierAgentToolCall(BaseModel):
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = Field(..., description="Namespaced Atelier agent tool name")
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: AtelierAgentToolStatus = AtelierAgentToolStatus.PROPOSED
    approval_required: bool = False
    approval_granted: bool = False
    error: str | None = None
    result_snapshot: dict[str, Any] | None = None
    preview_binding: AtelierAgentGenerationBinding | None = None
    approval_record: AtelierAgentApprovalRecord | None = None
    canvas_approval_binding: AtelierAgentCanvasBinding | None = None
    canvas_approval_record: AtelierAgentCanvasApprovalRecord | None = None
    created_at: float = Field(default_factory=time.time)
    completed_at: float | None = None

    @model_serializer(mode="wrap")
    def _serialize_tool_call(self, handler):
        data = handler(self)
        for field in ("preview_binding", "approval_record", "canvas_approval_binding", "canvas_approval_record"):
            if data.get(field) is None:
                data.pop(field, None)
        return data


class AtelierAgentPlanContext(BaseModel):
    selected_node_id: str | None = None
    planner_input: dict[str, Any] = Field(default_factory=dict)
    planner_schema_version: str = Field("atelier.agent.planner.v1", description="Planner input/output envelope version")
    planner_adapter_name: str | None = None
    tool_schema_version: str = Field("atelier.tools.v1", description="Tool schema version visible to the planner")
    model_trace_id: str | None = None


class AtelierAgentPlannerPackage(BaseModel):
    project_id: str
    user_message: str = ""
    selected_node_id: str | None = None
    skill_name: str | None = None
    planner_schema_version: str = "atelier.agent.planner.v1"
    tool_schema_version: str = "atelier.tools.v1"
    output_contract: dict[str, Any] = Field(default_factory=dict)
    tool_schemas: list[dict[str, Any]] = Field(default_factory=list)
    project_snapshot: dict[str, Any] = Field(default_factory=dict)
    selected_node_snapshot: dict[str, Any] | None = None
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    prior_messages: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "OpenAI Chat Completions-shaped history of prior agent turns. "
            "Threaded between the stable system prefix and the volatile "
            "current user message so the LLM can chain results across "
            "iterations and turns. Bounded by ATELIER_AGENT_PRIOR_TURNS "
            "with overflow folded into a compaction summary by "
            "atelier_context_compactor.maybe_compact_messages. (v1.3 2a)"
        ),
    )
    system_prefix: str | None = Field(
        None,
        description=(
            "Byte-stable system prompt prefix (directive + sorted tool "
            "schemas + policy). Constant across iterations within a turn "
            "so prompt caches hit. (v1.3 2c)"
        ),
    )
    volatile_suffix_meta: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Debug metadata describing what's in the volatile suffix this "
            "iteration (canvas snapshot scope, selected node id, presence "
            "of last_iter_result). Wire-only — not consumed by the LLM. (v1.3 2c)"
        ),
    )
    skill_spec: dict[str, Any] | None = Field(
        None,
        description=(
            "Optional resolved SkillSpec payload for the active skill. Set "
            "by build_planner_package when skill_name matches a registered "
            "skill; consumed by _build_atelier_llm_prefix. (v1.4 Batch 3)"
        ),
    )
    provider_id: str | None = Field(
        None,
        description=(
            "Resolved provider id (dashscope/openai/anthropic) for this "
            "iteration's LLM call. Set by build_planner_package via "
            "atelier_runtime_provider.resolve. (v1.4 Batch 4)"
        ),
    )
    model_id: str | None = Field(
        None,
        description=(
            "Resolved concrete model id (e.g. qwen-plus, gpt-4o, "
            "claude-3-5-sonnet-20241022) for this iteration's LLM call. "
            "(v1.4 Batch 4)"
        ),
    )
    created_at: float = Field(default_factory=time.time)


class AtelierAgentPlan(BaseModel):
    project_id: str
    user_message: str = ""
    planner: str = Field("deterministic_core", description="Planner implementation that produced this plan")
    skill_name: str | None = None
    status: str = Field("ready", description="ready/blocked")
    reason: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    context: AtelierAgentPlanContext = Field(default_factory=AtelierAgentPlanContext)
    created_at: float = Field(default_factory=time.time)
    response: str | None = Field(
        default=None,
        description=(
            "Optional LLM-emitted human-readable assistant text returned alongside "
            "tool_calls by an LLM-backed planner (e.g. ModelAdapterPlanner). When the "
            "frontend executes the plan it forwards this string back as "
            "RunAtelierAgentTurnRequest.assistant_response, and the harness uses it "
            "verbatim for AtelierAgentTurn.response (preferred over the deterministic "
            "summary). Null for deterministic planners."
        ),
    )


class AtelierAgentLoopStartContext(BaseModel):
    """Immutable start intent for a durable multi-iteration Agent loop."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(1, ge=1, le=2, strict=True)
    user_message: str = ""
    selected_node_id: str | None = Field(default=None, min_length=1)
    planner: Literal["deterministic_core", "model_adapter"] = "deterministic_core"
    skill_name: str | None = Field(default=None, min_length=1)
    max_iterations: int = Field(default=3, ge=1, le=8, strict=True)
    skill_snapshot: AtelierProductSkillSnapshot | None = None

    @model_validator(mode="after")
    def _skill_context_shape(self) -> "AtelierAgentLoopStartContext":
        if self.schema_version == 1:
            if self.skill_name is not None or self.skill_snapshot is not None:
                raise ValueError("v1 Agent loop context cannot carry product skill data")
            return self
        if self.skill_name is None or self.skill_snapshot is None:
            raise ValueError("v2 Agent loop context requires product skill data")
        if self.skill_snapshot.name != self.skill_name:
            raise ValueError("Agent loop skill snapshot does not match skill_name")
        return self

    @model_serializer(mode="wrap")
    def _serialize_loop_start_context(self, handler):
        data = handler(self)
        if data.get("skill_snapshot") is None:
            data.pop("skill_snapshot", None)
        return data


class AtelierAgentTurn(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    user_message: str = ""
    preview: bool = False
    session_id: str | None = None
    status: str = Field(
        "pending",
        description="pending/waiting_approval/completed/failed/canceled",
    )
    start_request_fingerprint: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    loop_start_context: AtelierAgentLoopStartContext | None = None
    tool_calls: list[AtelierAgentToolCall] = Field(default_factory=list)
    node_reservations: dict[str, int] = Field(default_factory=dict)
    completion_tokens_used: int = Field(0, ge=0)
    completion_tokens_reserved: int = Field(0, ge=0)
    generation_cost_quotes: dict[str, float] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    completed_at: float | None = None
    response: str | None = Field(
        default=None,
        description=(
            "Derived human-readable English summary of the turn's outcome. "
            "Populated by AtelierAgentHarness when the turn reaches a terminal "
            "status (completed/failed/waiting_approval). Optional so historical "
            "persisted turns load cleanly."
        ),
    )
    iteration_count: int = Field(
        0,
        description="Number of completed LLM rounds in the multi-step loop (v1.1 X)",
    )
    messages_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Accumulated OpenAI-style messages history fed back into the LLM on each iteration (v1.1 X)",
    )
    max_iterations: int = Field(
        3,
        description="Snapshot of ATELIER_AGENT_MAX_ITERATIONS at turn start; the loop terminates after this many iterations (v1.1 X)",
    )
    pending_tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Tool calls planned by the LLM but pending user approval. "
            "Populated when the multi-step loop emits a `tool_plan` event "
            "and pauses; consumed by POST /agent/turns/{id}/continue to "
            "resume execution. Cleared once execution proceeds (v1.1 X)."
        ),
    )
    skill_name: str | None = Field(
        None,
        description=(
            "Optional name of the app-owned Atelier product Skill frozen in "
            "loop_start_context.skill_snapshot when this durable loop started."
        ),
    )
    iterations: list[AtelierAgentIterationRecord] = Field(
        default_factory=list,
        description=(
            "Per-iteration LLM round metrics (model, provider, prompt/"
            "completion tokens, latency, tool linkage). Empty for pre-1.4 "
            "persisted turns. (v1.4 Batch 4 / item 4g)"
        ),
    )
    planner_invocations: list[AtelierAgentPlannerInvocation] = Field(
        default_factory=list,
        description=(
            "Durable model-backed planner claim/reservation ledger. Each "
            "turn/iteration may have at most one invocation; legacy turns omit "
            "the field when empty."
        ),
    )

    @model_serializer(mode="wrap")
    def _serialize_agent_turn(self, handler):
        data = handler(self)
        if data.get("start_request_fingerprint") is None:
            data.pop("start_request_fingerprint", None)
        if data.get("loop_start_context") is None:
            data.pop("loop_start_context", None)
        if not data.get("planner_invocations"):
            data.pop("planner_invocations", None)
        return data


class AtelierNode(BaseModel):
    id: str = Field(..., description="Unique node identifier")
    project_id: str = Field(..., description="Parent Atelier project ID")
    type: str = Field("idea", description="Node type, for example idea/image/video/audio/workflow")
    title: str = Field("", description="Short visible node title")
    prompt: str = Field("", description="Generation or planning prompt attached to this node")
    status: str = Field("draft", description="draft/pending/processing/completed/failed")
    x: float = Field(0.0, description="Canvas X position")
    y: float = Field(0.0, description="Canvas Y position")
    width: float = Field(320.0, description="Canvas node width")
    height: float = Field(180.0, description="Canvas node height")
    source_project_id: str | None = Field(None, description="Studio project ID this node references")
    frame_id: str | None = Field(None, description="Storyboard frame ID this node references")
    asset_id: str | None = Field(None, description="Studio asset ID this node references")
    video_task_id: str | None = Field(None, description="Shared VideoTask ID this node references")
    media_urls: list[str] = Field(default_factory=list, description="Generated or attached media URLs")
    data: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Node-type specific payload. For draft / video nodes this includes "
            "`candidates`, `generation`, `selected_candidate_id`, and the single "
            "`reference_image_urls` reference bucket — bucket name is image-historical "
            "but as of v0.7 it also holds video URLs sourced from sibling drafts' "
            "completed takes (attach_atelier_reference). The shell infers per-URL kind "
            "at render time from the owning node's `type`."
        ),
    )
    created_by: str = Field("user", description="user or agent")
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class AtelierCandidateJudgment(BaseModel):
    """Append-only human judgment attached to one generation candidate."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    node_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    media_kind: Literal["image", "video", "audio"]
    verdict: Literal["unreviewed", "keep", "reject"]
    reason: str = Field("", max_length=2000, strict=True)
    created_at: float = Field(..., ge=0, allow_inf_nan=False)
    supersedes_judgment_id: str | None = Field(None, min_length=1)

    @model_serializer(mode="wrap")
    def _serialize_candidate_judgment(self, handler):
        data = handler(self)
        if data.get("supersedes_judgment_id") is None:
            data.pop("supersedes_judgment_id", None)
        return data


ORDERED_GENERATION_MEDIA_ROLE_ORDER = (
    "first_frame", "last_frame", "reference_image", "reference_video", "reference_audio",
)
_ORDERED_GENERATION_MEDIA_ROLE_INDEX = {role: index for index, role in enumerate(ORDERED_GENERATION_MEDIA_ROLE_ORDER)}


def ordered_generation_media_sort_key(role: str, ordinal: int) -> tuple[int, int]:
    if role not in _ORDERED_GENERATION_MEDIA_ROLE_INDEX:
        raise ValueError("Ordered Atelier generation source role is invalid")
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("Ordered Atelier generation source ordinal is invalid")
    return (_ORDERED_GENERATION_MEDIA_ROLE_INDEX[role], ordinal)


def ordered_generation_media_is_canonical(items) -> bool:
    previous: tuple[int, int] | None = None
    for item in items:
        if isinstance(item, dict):
            role = item.get("role")
            ordinal = item.get("ordinal")
        else:
            role = getattr(item, "role", None)
            ordinal = getattr(item, "ordinal", None)
        key = ordered_generation_media_sort_key(role, ordinal)
        if previous is not None and key < previous:
            return False
        previous = key
    return True


class AtelierGenerationFirstFrameSource(BaseModel):
    """Source media identity for an Atelier provider video attempt."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(..., min_length=1)
    media_url: str = Field(..., min_length=1)


class AtelierGenerationMediaSource(BaseModel):
    """Product-owned ordered media source identity for a provider video attempt."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["first_frame", "last_frame", "reference_image", "reference_video", "reference_audio"]
    ordinal: int = Field(..., ge=0, strict=True)
    node_id: str = Field(..., min_length=1)
    media_url: str = Field(..., min_length=1)

    @field_validator("node_id", "media_url")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Atelier ordered generation source identity must be nonempty")
        return value

    @model_validator(mode="after")
    def _frame_ordinals_are_singleton(self) -> "AtelierGenerationMediaSource":
        if self.role in {"first_frame", "last_frame"} and self.ordinal != 0:
            raise ValueError("Atelier ordered generation frame source requires ordinal zero")
        return self


class AtelierGenerationAttempt(BaseModel):
    """Project-owned durable generation attempt ledger entry."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=2, strict=True)
    project_id: str = Field(..., min_length=1)
    node_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    attempt_id: str = Field(..., min_length=1)
    attempt_revision: int = Field(0, ge=0, strict=True)
    status: Literal["pending", "processing", "needs_confirmation", "failed", "cancelled", "completed"]
    error_code: str | None = Field(None, min_length=1, max_length=128)
    first_frame_source: AtelierGenerationFirstFrameSource | None = None
    input_contract_version: Literal["ordered-media-v1"] | None = None
    input_media: list[AtelierGenerationMediaSource] | None = None
    output_ref: str | None = Field(None, min_length=1)
    provider_execution: ProviderExecutionRecord
    skill_binding: SkillExecutionBinding | None = None
    agent_authorization_ref: AtelierAgentAuthorizationRef | None = None
    created_at: float = Field(..., allow_inf_nan=False)
    updated_at: float = Field(..., allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_agent_authorization_ref(self) -> "AtelierGenerationAttempt":
        if self.schema_version == 1 and self.agent_authorization_ref is not None:
            raise ValueError("Atelier manual generation attempt cannot carry agent authorization")
        if self.schema_version == 2 and self.agent_authorization_ref is None:
            raise ValueError("Atelier agent generation attempt requires authorization")
        return self

    @model_validator(mode="after")
    def _validate_generation_input_contract(self) -> "AtelierGenerationAttempt":
        if self.input_contract_version is None:
            if self.input_media is not None:
                raise ValueError("Legacy Atelier generation attempt cannot carry ordered media")
            if self.first_frame_source is None:
                raise ValueError("Legacy Atelier generation attempt requires first frame source")
            return self
        if self.first_frame_source is not None:
            raise ValueError("Ordered Atelier generation attempt cannot carry legacy first frame source")
        if self.input_media is None:
            raise ValueError("Ordered Atelier generation attempt requires explicit input media")
        if not ordered_generation_media_is_canonical(self.input_media):
            raise ValueError("Ordered Atelier generation source roles must be canonical")
        seen: set[tuple[str, int]] = set()
        ordinals_by_role: dict[str, list[int]] = {}
        for item in self.input_media:
            key = (item.role, item.ordinal)
            if key in seen:
                raise ValueError("Ordered Atelier generation source role ordinal is duplicated")
            seen.add(key)
            ordinals_by_role.setdefault(item.role, []).append(item.ordinal)
        for role, ordinals in ordinals_by_role.items():
            if ordinals != list(range(len(ordinals))):
                raise ValueError("Ordered Atelier generation source ordinals must be continuous")
        return self

    @model_serializer(mode="wrap")
    def _serialize_generation_attempt(self, handler):
        data = handler(self)
        if data.get("schema_version") == 1 and data.get("agent_authorization_ref") is None:
            data.pop("agent_authorization_ref", None)
        if data.get("input_contract_version") is None:
            data.pop("input_contract_version", None)
            data.pop("input_media", None)
        if data.get("first_frame_source") is None:
            data.pop("first_frame_source", None)
        return data


class AtelierMediaGenerationAttempt(BaseModel):
    """Dormant strict image/audio generation attempt; not wired to public routes yet."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    execution_contract: Literal["atelier-provider-media-v2"]
    project_id: str = Field(..., min_length=1)
    node_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    attempt_id: str = Field(..., min_length=1)
    attempt_revision: int = Field(0, ge=0, strict=True)
    media_kind: Literal["image", "audio"]
    status: Literal["pending", "processing", "needs_confirmation", "failed", "cancelled", "completed"]
    error_code: str | None = Field(None, min_length=1, max_length=128)
    output_ref: str | None = Field(None, min_length=1)
    provider_execution: ProviderExecutionRecord
    created_at: float = Field(..., allow_inf_nan=False)
    updated_at: float = Field(..., allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_media_generation_attempt(self) -> "AtelierMediaGenerationAttempt":
        record = self.provider_execution
        if (
            record.schema_version != 2
            or record.execution_kind != "sync_artifact"
            or record.media_kind != self.media_kind
            or record.attempt_id != self.attempt_id
        ):
            raise ValueError("Atelier media generation attempt requires sync provider execution")
        if self.status not in {"needs_confirmation", "failed"} and self.error_code is not None:
            raise ValueError("Atelier media generation error_code is only allowed for failed or needs_confirmation")
        if self.status == "completed":
            if self.output_ref is None or record.output_ref != self.output_ref:
                raise ValueError("Atelier completed media generation output does not match provider execution")
        elif self.output_ref is not None:
            raise ValueError("Atelier unfinished media generation output_ref must be null")
        return self


class AtelierGenerationRequestReceiptMember(BaseModel):
    """One candidate/attempt member owned by an Atelier generation receipt."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=1)
    attempt_id: str = Field(..., min_length=1)
    execution_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class AtelierGenerationRequestReceipt(BaseModel):
    """Idempotency receipt for admitting Atelier provider video candidates."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=2, strict=True)
    project_id: str = Field(..., min_length=1)
    entrypoint: Literal["atelier.provider_video.v1", "atelier.provider_video.v2", "atelier.provider_image.v1", "atelier.provider_audio.v1"]
    request_contract_version: Literal["request-json-v1"]
    client_request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    request_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    node_id: str = Field(..., min_length=1)
    candidate_id: str | None = Field(None, min_length=1)
    attempt_id: str | None = Field(None, min_length=1)
    execution_fingerprint: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    members: list[AtelierGenerationRequestReceiptMember] | None = None
    base_revision: int = Field(..., ge=0, strict=True)
    committed_revision: int = Field(..., ge=0, strict=True)
    created_at: float = Field(..., allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_receipt_shape(self) -> "AtelierGenerationRequestReceipt":
        if self.schema_version == 1:
            if self.candidate_id is None or self.attempt_id is None or self.execution_fingerprint is None:
                raise ValueError("Atelier generation receipt v1 requires a single member")
            if self.members is not None:
                raise ValueError("Atelier generation receipt v1 cannot carry batch members")
            return self
        if self.candidate_id is not None or self.attempt_id is not None or self.execution_fingerprint is not None:
            raise ValueError("Atelier generation receipt v2 cannot carry single-member fields")
        if self.members is None or not 2 <= len(self.members) <= 6:
            raise ValueError("Atelier generation receipt v2 requires 2..6 members")
        seen_candidates: set[str] = set()
        seen_attempts: set[str] = set()
        for member in self.members:
            if member.candidate_id in seen_candidates or member.attempt_id in seen_attempts:
                raise ValueError("Atelier generation receipt v2 member identity is duplicated")
            seen_candidates.add(member.candidate_id)
            seen_attempts.add(member.attempt_id)
        return self

    @property
    def receipt_members(self) -> tuple[AtelierGenerationRequestReceiptMember, ...]:
        if self.schema_version == 1:
            return (
                AtelierGenerationRequestReceiptMember(
                    candidate_id=self.candidate_id or "",
                    attempt_id=self.attempt_id or "",
                    execution_fingerprint=self.execution_fingerprint or "",
                ),
            )
        return tuple(self.members or ())

    @model_serializer(mode="wrap")
    def _serialize_generation_receipt(self, handler):
        data = handler(self)
        if data.get("schema_version") == 1:
            data.pop("members", None)
        else:
            data.pop("candidate_id", None)
            data.pop("attempt_id", None)
            data.pop("execution_fingerprint", None)
        return data


class AtelierSequenceTransition(BaseModel):
    """Transition into this clip; the first clip must use cut."""

    kind: str = Field("cut", pattern="^(cut|fade)$")
    duration: float = Field(0.0, ge=0, allow_inf_nan=False)


class AtelierSequenceEntry(BaseModel):
    """One clip instance; null candidateId references an uploaded video node."""

    parentId: str = Field(..., min_length=1)
    candidateId: str | None = Field(None, min_length=1)
    id: str | None = Field(None, min_length=1, description="Optional clip instance id; materials may repeat")
    trimStart: float | None = Field(None, ge=0, allow_inf_nan=False)
    trimEnd: float | None = Field(None, gt=0, allow_inf_nan=False)
    gain: float = Field(1.0, ge=0, le=2, allow_inf_nan=False)
    transition: AtelierSequenceTransition = Field(default_factory=AtelierSequenceTransition)


class AtelierAudioTrack(BaseModel):
    """Independent selected/uploaded audio node placed on the video timeline."""

    id: str = Field(..., min_length=1)
    node_id: str = Field(..., min_length=1)
    start: float = Field(0.0, ge=0, allow_inf_nan=False)
    trim_start: float | None = Field(None, ge=0, allow_inf_nan=False)
    trim_end: float | None = Field(None, gt=0, allow_inf_nan=False)
    gain: float = Field(1.0, ge=0, le=2, allow_inf_nan=False)
    loop: bool = False


class AtelierSequencePublicationProof(BaseModel):
    """Durable proof captured before publishing a rendered Atelier sequence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    job_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    relative_path: str = Field(..., pattern=r"^video/atelier_seq_[A-Za-z0-9_-]+\.mp4$")
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(..., gt=0, strict=True)
    duration: float = Field(..., gt=0, allow_inf_nan=False)
    width: int = Field(..., gt=0, strict=True)
    height: int = Field(..., gt=0, strict=True)
    fps: int = Field(..., gt=0, strict=True)
    snapshot_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    prepared_at: float = Field(..., allow_inf_nan=False)


class ExportDeletionReceipt(BaseModel):
    """Durable tombstone and physical-file deletion fact for an export."""

    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    delete_file: bool = Field(..., strict=True)
    deleted_at: float = Field(..., allow_inf_nan=False)
    file_status: Literal["retained", "pending", "deleted", "already_missing", "blocked"]
    checked_at: float | None = Field(None, allow_inf_nan=False)
    error_code: str | None = Field(None, min_length=1, max_length=128)
    error_message: str | None = Field(None, min_length=1, max_length=500)


class AtelierImportedExportProof(BaseModel):
    """Target-owned proof for an export media file copied by project import."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    project_id: str = Field(..., min_length=1)
    export_id: str = Field(..., min_length=1)
    relative_path: str = Field(..., min_length=1)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(..., gt=0, strict=True)
    source_project_id: str = Field(..., min_length=1)
    source_export_id: str = Field(..., min_length=1)
    source_storage_id: str = Field(..., min_length=1)
    imported_at: float = Field(..., allow_inf_nan=False)


class ExportRecord(BaseModel):
    """Persisted record of a successful Atelier sequence export."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = Field(..., description="Owning Atelier project id")
    created_at: float = Field(default_factory=time.time)
    clip_count: int = Field(0, description="Number of clips concatenated into the export")
    size_mb: float = Field(0.0, description="Final mp4 size in MiB (matches export job response)")
    video_url: str = Field(..., description="Relative URL of the exported mp4 (e.g. video/atelier_seq_*.mp4)")
    filename: str = Field(..., description="Basename of the exported file under output/video/")
    source_entries: list[AtelierSequenceEntry] = Field(
        default_factory=list,
        description="Original sequence edit; not re-resolved by Redo.",
    )
    timeline_snapshot: dict[str, Any] | None = Field(
        None,
        description="Versioned, fully resolved timeline with managed relative URLs only",
    )
    publication_proof: AtelierSequencePublicationProof | None = None
    job_id: str | None = None
    snapshot_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    deletion: ExportDeletionReceipt | None = None
    imported_proof: AtelierImportedExportProof | None = None

    @model_serializer(mode="wrap")
    def _serialize_export_record(self, handler):
        data = handler(self)
        if data.get("imported_proof") is None:
            data.pop("imported_proof", None)
        return data


class AtelierSequenceExportJob(BaseModel):
    """Durable journal record for an in-progress or completed sequence export."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    job_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    project_id: str = Field(..., min_length=1)
    status: Literal["queued", "running", "cancelling", "cancelled", "failed", "repair_required", "completed"] = "queued"
    request_contract_version: Literal["atelier-sequence-export-v1", "atelier-sequence-export-redo-v1"] = "atelier-sequence-export-v1"
    client_request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    source_export_id: str | None = None
    request_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    base_revision: int = Field(..., ge=0, strict=True)
    job_revision: int = Field(0, ge=0, strict=True)
    created_at: float = Field(..., allow_inf_nan=False)
    updated_at: float = Field(..., allow_inf_nan=False)
    started_at: float | None = Field(None, allow_inf_nan=False)
    finished_at: float | None = Field(None, allow_inf_nan=False)
    snapshot_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    snapshot: dict[str, Any]
    source_entries: list[AtelierSequenceEntry] = Field(default_factory=list)
    source_audio_tracks: list[AtelierAudioTrack] = Field(default_factory=list)
    output_ref: str = Field(..., pattern=r"^video/atelier_seq_[A-Za-z0-9_-]+\.mp4$")
    prepared_proof: AtelierSequencePublicationProof | None = None
    export_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancelled_at: float | None = Field(None, allow_inf_nan=False)


class AtelierNodeDeleteFieldValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exists: bool = Field(..., strict=True)
    value: Any = None


class AtelierNodeDeleteSourceProof(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_node_id: str = Field(..., min_length=1)
    source_node_id: str = Field(..., min_length=1)
    source_type: str = Field(..., min_length=1)
    urls: list[str] = Field(default_factory=list)


class AtelierNodeDeleteDataChangeGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(..., min_length=1)
    group: str = Field(..., pattern="^(references|parent|region)$")
    before: dict[str, AtelierNodeDeleteFieldValue]
    after: dict[str, AtelierNodeDeleteFieldValue]
    source_proofs: list[AtelierNodeDeleteSourceProof] = Field(default_factory=list)


class AtelierNodeDeleteSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: AtelierNode
    original_index: int = Field(..., ge=0, strict=True)
    reference_media_urls: list[str] = Field(default_factory=list)
    referenced_source_ids: list[str] = Field(default_factory=list)
    source_proofs: list[AtelierNodeDeleteSourceProof] = Field(default_factory=list)


class AtelierNodeDeleteSequenceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: list[AtelierSequenceEntry]
    after: list[AtelierSequenceEntry]
    removed_node_ids: list[str]


class AtelierNodeDeleteAudioTracksChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: list[AtelierAudioTrack]
    after: list[AtelierAudioTrack]
    removed_node_ids: list[str]


class AtelierNodeRestoreSkip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(..., pattern="^(node|data_group|sequence|audio_tracks)$")
    reason: str = Field(..., min_length=1)
    node_id: str | None = None
    group: str | None = None


class AtelierNodeDeleteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    receipt_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    base_revision: int = Field(..., ge=0, strict=True)
    restore_base_revision: int | None = Field(None, ge=0, strict=True)
    deleted_at: float = Field(..., allow_inf_nan=False)
    restored_at: float | None = Field(None, allow_inf_nan=False)
    node_ids: list[str] = Field(..., min_length=1)
    node_snapshots: list[AtelierNodeDeleteSnapshot] = Field(..., min_length=1)
    data_change_groups: list[AtelierNodeDeleteDataChangeGroup] = Field(default_factory=list)
    sequence_change: AtelierNodeDeleteSequenceChange | None = None
    audio_tracks_change: AtelierNodeDeleteAudioTracksChange | None = None
    last_restore_skips: list[AtelierNodeRestoreSkip] = Field(default_factory=list)


class AtelierUploadedMediaProof(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., pattern=r"^uploads/[^/]+$")
    media_kind: Literal["image", "audio", "video"]
    size_bytes: int = Field(..., gt=0, strict=True)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    width: int | None = Field(None, gt=0, strict=True)
    height: int | None = Field(None, gt=0, strict=True)
    duration: float | None = Field(None, gt=0, allow_inf_nan=False)
    poster_url: str | None = Field(None, pattern=r"^uploads/[^/]+$")
    poster_size_bytes: int | None = Field(None, gt=0, strict=True)
    poster_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


class AtelierNodeUploadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    receipt_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    client_request_id: str = Field(..., min_length=1, max_length=128)
    operation: Literal["create_media_node", "upload_reference_attach"]
    request_contract_version: str = Field("atelier-upload-request-v1", pattern=r"^atelier-upload-request-v1$")
    request_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    base_revision: int = Field(..., ge=0, strict=True)
    committed_revision: int = Field(..., ge=0, strict=True)
    created_at: float = Field(..., allow_inf_nan=False)
    node_ids: list[str] = Field(..., min_length=1)
    media: AtelierUploadedMediaProof
    target_node_id: str | None = None
    replaced_node_id: str | None = None
    source_node_id: str | None = None
    previous_media: AtelierUploadedMediaProof | None = None


class AtelierImportedAgentHistoryMessage(BaseModel):
    """Strict public message preserved inside import provenance."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["user", "assistant", "tool"]
    name: str | None = Field(None, min_length=1)
    content: str = ""
    status: str | None = Field(None, min_length=1)
    error_code: str | None = Field(None, min_length=1)

    @model_serializer(mode="wrap")
    def _serialize_imported_agent_history_message(self, handler):
        data = handler(self)
        for field in ("name", "status", "error_code"):
            if data.get(field) is None:
                data.pop(field, None)
        return data


class AtelierImportedAgentHistoryEntry(BaseModel):
    """Sanitized readonly Agent text preserved by independent project import."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    source_turn_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    user_message: str = ""
    assistant_response: str | None = None
    messages: list[AtelierImportedAgentHistoryMessage] = Field(default_factory=list)
    created_at: float | None = Field(None, allow_inf_nan=False)
    completed_at: float | None = Field(None, allow_inf_nan=False)
    source_refs: dict[str, str] = Field(default_factory=dict)
    target_refs: dict[str, str] = Field(default_factory=dict)


class AtelierImportProvenance(BaseModel):
    """Readonly provenance for an independent project-package import."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(1, ge=1, le=1, strict=True)
    source_project_id: str = Field(..., min_length=1)
    source_storage_id: str = Field(..., min_length=1)
    imported_at: float = Field(..., allow_inf_nan=False)
    agent_history: list[AtelierImportedAgentHistoryEntry] = Field(default_factory=list)


class AtelierProject(BaseModel):
    id: str = Field(..., description="Unique Atelier project ID")
    title: str = Field(..., description="Canvas project title")
    description: str = Field("", description="Canvas project description")
    source_project_id: str | None = Field(None, description="Optional Studio project ID used as a seed")
    nodes: list[AtelierNode] = Field(default_factory=list)
    agent_policy: AtelierAgentPolicy = Field(default_factory=AtelierAgentPolicy)
    agent_turns: list[AtelierAgentTurn] = Field(default_factory=list)
    sequence: list[AtelierSequenceEntry] = Field(
        default_factory=list,
        description="Ordered list of clips assembled in the Sequence Strip; persisted server-side so the cut survives across devices",
    )
    audio_tracks: list[AtelierAudioTrack] = Field(default_factory=list)
    exports: list[ExportRecord] = Field(
        default_factory=list,
        description=(
            "Persisted history of successful sequence exports. Appended by "
            "the product storage/API layer after an export job reaches "
            "status='completed'."
        ),
    )
    node_delete_receipts: list[AtelierNodeDeleteReceipt] = Field(default_factory=list)
    node_upload_receipts: list[AtelierNodeUploadReceipt] = Field(default_factory=list)
    sequence_export_jobs: list[AtelierSequenceExportJob] = Field(default_factory=list)
    generation_attempts: list[AtelierGenerationAttempt | AtelierMediaGenerationAttempt] = Field(default_factory=list)
    generation_request_receipts: list[AtelierGenerationRequestReceipt] = Field(default_factory=list)
    candidate_judgments: list[AtelierCandidateJudgment] = Field(default_factory=list)
    import_provenance: AtelierImportProvenance | None = None
    agent_compaction_summary: str | None = Field(
        None,
        description=(
            "Rolling summary of historical Atelier agent turns produced "
            "by atelier_context_compactor. Replaces the oldest M turns "
            "when prior_messages exceeds ATELIER_AGENT_COMPACT_LIMIT. (v1.3 2b)"
        ),
    )
    compacted_through_turn_id: str | None = Field(
        None,
        description=(
            "ID of the most recent agent turn included in "
            "agent_compaction_summary. Resume paths use this to avoid "
            "re-summarizing already-folded turns. (v1.3 2b)"
        ),
    )
    revision: int = Field(
        0,
        ge=0,
        strict=True,
        description=(
            "Strict nonnegative storage revision for future P2A optimistic "
            "write checks. Defaults to 0 so candidate-era persisted projects "
            "without revision load unchanged."
        ),
    )
    created_at: float
    updated_at: float

    @model_serializer(mode="wrap")
    def _serialize_project(self, handler):
        data = handler(self)
        if not data.get("candidate_judgments"):
            data.pop("candidate_judgments", None)
        if data.get("import_provenance") is None:
            data.pop("import_provenance", None)
        return data
