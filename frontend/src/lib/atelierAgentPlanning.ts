import { type AtelierAgentTurn } from "@/lib/api";

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
