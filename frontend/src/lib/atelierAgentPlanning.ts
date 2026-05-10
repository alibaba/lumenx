import {
    type AtelierAgentToolCall,
    type AtelierAgentPlanContext as CoreAtelierAgentPlanContext,
    type AtelierAgentPlannerPackage,
    type AtelierAgentTurn,
} from "@/lib/api";

export interface AtelierAgentPlanResult {
    toolCalls: [];
    error: string | null;
}

export interface AtelierAgentPlanContext {
    projectId: string | null;
    projectUpdatedAt: number | null;
    projectNodeCount: number;
    selectedNodeId: string | null;
    selectedNodeUpdatedAt: number | null;
}

export interface AtelierAgentTraceRow {
    label: string;
    value: string;
}

export interface AtelierAgentToolCallSummary {
    callId: string;
    toolName: string;
    status: string;
    result: string | null;
}

export interface AtelierAgentTurnSummary {
    id: string;
    title: string;
    status: string;
    mode: "preview" | "execute";
    callCount: number;
    completedCount: number;
    failedCount: number;
    waitingApprovalCount: number;
    resultSummary: string | null;
    toolCalls: AtelierAgentToolCallSummary[];
}

export interface AtelierAgentSessionSummary {
    status: "idle" | "planned" | "waiting_approval" | "running";
    focus: {
        label: string;
        detail: string;
        turnId: string | null;
    };
    turnCount: number;
    plannedCallCount: number;
    waitingApprovalCount: number;
    completedCallCount: number;
    failedCallCount: number;
    previewTurnCount: number;
    executeTurnCount: number;
}

export function validateAtelierAgentIntent(intent: string): AtelierAgentPlanResult {
    const prompt = intent.trim();
    if (!prompt) {
        return { toolCalls: [], error: "Enter an intent before previewing or executing." };
    }
    return { toolCalls: [], error: null };
}

export function isAgentTurnBlocked(pendingAgentTurn: AtelierAgentTurn | null): boolean {
    return Boolean(pendingAgentTurn);
}

export function getAtelierAgentPlanContext(
    projectId: string | null,
    projectUpdatedAt: number | null,
    projectNodeCount: number,
    selectedNodeId: string | null,
    selectedNodeUpdatedAt: number | null
): AtelierAgentPlanContext {
    return {
        projectId,
        projectUpdatedAt,
        projectNodeCount,
        selectedNodeId,
        selectedNodeUpdatedAt,
    };
}

export function isAtelierAgentPlanStale(
    previous: AtelierAgentPlanContext | null,
    current: AtelierAgentPlanContext
): boolean {
    if (!previous) return false;
    return (
        previous.projectId !== current.projectId ||
        previous.projectUpdatedAt !== current.projectUpdatedAt ||
        previous.projectNodeCount !== current.projectNodeCount ||
        previous.selectedNodeId !== current.selectedNodeId ||
        previous.selectedNodeUpdatedAt !== current.selectedNodeUpdatedAt
    );
}

export function getAtelierPlannerPackageRows(
    plannerPackage: AtelierAgentPlannerPackage | null
): AtelierAgentTraceRow[] {
    if (!plannerPackage) return [];
    const selectedNode = plannerPackage.selected_node_snapshot;
    const selectedLabel = selectedNode
        ? `${selectedNode.title} · ${selectedNode.type}`
        : plannerPackage.selected_node_id ?? "Canvas";

    return [
        { label: "Planner schema", value: plannerPackage.planner_schema_version },
        { label: "Tool schema", value: plannerPackage.tool_schema_version },
        { label: "Tool scope", value: `${plannerPackage.tool_schemas.length} registered` },
        {
            label: "Policy",
            value: `${plannerPackage.policy_snapshot.approval_mode} · max ${plannerPackage.policy_snapshot.max_nodes_per_action}`,
        },
        { label: "Selected", value: selectedLabel },
    ];
}

export function getAtelierPlanContextRows(
    context: CoreAtelierAgentPlanContext | null
): AtelierAgentTraceRow[] {
    if (!context) return [];
    const plannerInputKeys = Object.keys(context.planner_input ?? {}).sort();
    return [
        { label: "Adapter", value: context.planner_adapter_name ?? "deterministic_core" },
        { label: "Model trace", value: context.model_trace_id ?? "none" },
        { label: "Selected", value: context.selected_node_id ?? "Canvas" },
        { label: "Schema", value: context.planner_schema_version ?? "unknown" },
        { label: "Tool schema", value: context.tool_schema_version ?? "unknown" },
        { label: "Context keys", value: plannerInputKeys.length > 0 ? plannerInputKeys.join(", ") : "none" },
    ];
}

function summarizeToolCallResult(call: AtelierAgentToolCall): string | null {
    const snapshot = call.result_snapshot;
    if (!snapshot || typeof snapshot !== "object") return call.error ?? null;
    const maybeNode = "node" in snapshot ? snapshot.node : "video_node" in snapshot ? snapshot.video_node : null;
    if (maybeNode && typeof maybeNode === "object" && "title" in maybeNode && typeof maybeNode.title === "string") {
        return `Node: ${maybeNode.title}`;
    }
    if ("candidate_ids" in snapshot && Array.isArray(snapshot.candidate_ids)) {
        return `Candidates: ${snapshot.candidate_ids.length}`;
    }
    if ("node_ids" in snapshot && Array.isArray(snapshot.node_ids)) {
        return `Nodes: ${snapshot.node_ids.length}`;
    }
    return call.error ?? "Result captured";
}

export function getAtelierAgentTurnSummary(turn: AtelierAgentTurn): AtelierAgentTurnSummary {
    const toolCalls = turn.tool_calls.map((call) => ({
        callId: call.call_id,
        toolName: call.tool_name,
        status: call.status,
        result: summarizeToolCallResult(call),
    }));
    const completedCount = toolCalls.filter((call) => call.status === "completed").length;
    const failedCount = toolCalls.filter((call) => call.status === "failed" || call.status === "denied").length;
    const waitingApprovalCount = toolCalls.filter((call) => call.status === "approval_required").length;
    const firstResult = toolCalls.find((call) => call.result)?.result ?? null;
    return {
        id: turn.id,
        title: turn.user_message || "Agent turn",
        status: turn.status,
        mode: turn.preview ? "preview" : "execute",
        callCount: toolCalls.length,
        completedCount,
        failedCount,
        waitingApprovalCount,
        resultSummary: firstResult,
        toolCalls,
    };
}

export function getAtelierAgentTurnSummaries(
    turns: AtelierAgentTurn[],
    limit = 5
): AtelierAgentTurnSummary[] {
    return [...turns].slice(-limit).reverse().map(getAtelierAgentTurnSummary);
}

export function getAtelierAgentSessionSummary({
    turns,
    plannedCallCount,
    pendingTurn,
    isRunning,
}: {
    turns: AtelierAgentTurn[];
    plannedCallCount: number;
    pendingTurn: AtelierAgentTurn | null;
    isRunning: boolean;
}): AtelierAgentSessionSummary {
    const turnSummaries = turns.map(getAtelierAgentTurnSummary);
    const completedCallCount = turnSummaries.reduce((count, turn) => count + turn.completedCount, 0);
    const failedCallCount = turnSummaries.reduce((count, turn) => count + turn.failedCount, 0);
    const previewTurnCount = turns.filter((turn) => turn.preview).length;
    const executeTurnCount = turns.length - previewTurnCount;
    const waitingApprovalCount = pendingTurn
        ? getAtelierAgentTurnSummary(pendingTurn).waitingApprovalCount
        : 0;
    const status = waitingApprovalCount > 0
        ? "waiting_approval"
        : isRunning
            ? "running"
            : plannedCallCount > 0
                ? "planned"
                : "idle";
    const latestTurn = turns.at(-1) ?? null;
    const focus = pendingTurn
        ? {
            label: "Approval",
            detail: pendingTurn.user_message || "Pending agent turn",
            turnId: pendingTurn.id,
        }
        : plannedCallCount > 0
            ? {
                label: "Plan ready",
                detail: `${plannedCallCount} tool call${plannedCallCount === 1 ? "" : "s"} staged`,
                turnId: null,
            }
            : isRunning
                ? {
                    label: "Running",
                    detail: "Agent turn in progress",
                    turnId: null,
                }
                : latestTurn
                    ? {
                        label: latestTurn.preview ? "Latest preview" : "Latest execution",
                        detail: latestTurn.user_message || "Agent turn",
                        turnId: latestTurn.id,
                    }
                    : {
                        label: "Idle",
                        detail: "No active turn",
                        turnId: null,
                    };

    return {
        status,
        focus,
        turnCount: turns.length,
        plannedCallCount,
        waitingApprovalCount,
        completedCallCount,
        failedCallCount,
        previewTurnCount,
        executeTurnCount,
    };
}
