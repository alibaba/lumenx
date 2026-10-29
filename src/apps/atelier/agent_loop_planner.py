"""Bounded deterministic planner using the loop's supplied committed snapshot."""

from ...utils.json_storage import StorageConflictError
from .agent_authorization import TOOL_GENERATION_CREATE_VIDEO_CANDIDATES
from .agent_loop import DeterministicLoopPlan, LoopPlannerContext
from .agent_service import plan_for_project
from .models import AtelierAgentToolStatus


def plan_deterministic_loop(context: LoopPlannerContext) -> DeterministicLoopPlan:
    """Inspect, propose one approved generation, then report its actual outcome."""
    project, turn = context.project, context.turn
    intent = turn.loop_start_context
    if intent is None or intent.planner != "deterministic_core" or intent.skill_name is not None:
        raise ValueError("Unsupported deterministic loop context")
    if project.id != turn.project_id:
        raise StorageConflictError("atelier_agent_loop_project_mismatch")

    generation_calls = [call for call in turn.tool_calls if call.tool_name == TOOL_GENERATION_CREATE_VIDEO_CANDIDATES]
    if generation_calls:
        if len(generation_calls) != 1:
            raise StorageConflictError("atelier_agent_loop_generation_evidence_invalid")
        call = generation_calls[0]
        attempt_id = (call.result_snapshot or {}).get("attempt_id")
        attempt = next((item for item in project.generation_attempts if item.attempt_id == attempt_id), None)
        ref = attempt.agent_authorization_ref if attempt is not None else None
        if ref is None or ref.project_id != project.id or ref.turn_id != turn.id or ref.call_id != call.call_id:
            raise StorageConflictError("atelier_agent_loop_generation_evidence_missing")
        if attempt.status not in {"completed", "failed", "cancelled"}:
            raise StorageConflictError("atelier_agent_loop_generation_not_terminal")
        return DeterministicLoopPlan(
            assistant_message=f"Generation {attempt.status}. Review the saved take; selection remains yours.",
            stop_reason=f"generation_{attempt.status}",
        )

    if not any(call.tool_name == "canvas.readProject" and call.status == AtelierAgentToolStatus.COMPLETED for call in turn.tool_calls):
        return DeterministicLoopPlan(
            assistant_message="Inspect the current project before preparing a generation proposal.",
            tool_calls=[{"tool_name": "canvas.readProject", "arguments": {}}],
        )

    proposal = plan_for_project(
        project, user_message=intent.user_message, selected_node_id=intent.selected_node_id,
        planner=intent.planner, skill_name=intent.skill_name,
    )
    if proposal.status != "ready":
        return DeterministicLoopPlan(assistant_message=proposal.reason, stop_reason="blocked")
    return DeterministicLoopPlan(
        assistant_message="Review and approve this generation proposal before it is submitted.",
        tool_calls=[{"tool_name": call["tool_name"], "arguments": call["arguments"]} for call in proposal.tool_calls],
    )
