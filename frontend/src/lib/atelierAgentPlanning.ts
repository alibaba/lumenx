import {
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
