import { type AtelierAgentToolCallPayload, type AtelierAgentTurn, type AtelierNode } from "@/lib/api";
import { VIDEO_I2V_MODELS } from "@/lib/modelCatalog";

export interface AtelierAgentPlanResult {
    toolCalls: AtelierAgentToolCallPayload[];
    error: string | null;
}

export interface AtelierAgentPlanContext {
    projectId: string | null;
    projectUpdatedAt: number | null;
    projectNodeCount: number;
    selectedNodeId: string | null;
    selectedNodeUpdatedAt: number | null;
}

function getDefaultVideoModelId() {
    return VIDEO_I2V_MODELS[0]?.id || "wan2.7-i2v";
}

function getNodeModelId(node: AtelierNode | null) {
    return typeof node?.data?.model === "string" ? node.data.model : getDefaultVideoModelId();
}

function getReferenceImageUrls(node: AtelierNode | null): string[] {
    const refs = node?.data?.reference_image_urls;
    return Array.isArray(refs) ? refs.filter((item): item is string => typeof item === "string") : [];
}

function compactIntentTitle(intent: string) {
    const trimmed = intent.trim().replace(/\s+/g, " ");
    if (!trimmed) return "Agent Video Node";
    return trimmed.length > 32 ? `${trimmed.slice(0, 32)}...` : trimmed;
}

function shouldGenerateCandidates(intent: string) {
    return /生成|候选|视频|generate|candidate|render/i.test(intent);
}

export function planAtelierAgentTurn(
    intent: string,
    projectNodeCount: number,
    selectedNode: AtelierNode | null
): AtelierAgentPlanResult {
    const prompt = intent.trim();
    if (!prompt) {
        return { toolCalls: [], error: "Enter an intent before previewing or executing." };
    }

    if (shouldGenerateCandidates(prompt)) {
        if (selectedNode?.type !== "video") {
            return {
                toolCalls: [],
                error: "Video candidate generation requires a selected video node with reference images.",
            };
        }

        const model = getNodeModelId(selectedNode);
        const referenceImageUrls = getReferenceImageUrls(selectedNode);
        if (referenceImageUrls.length === 0) {
            return {
                toolCalls: [],
                error: "Video candidate generation requires at least one reference image on the selected node.",
            };
        }

        return {
            toolCalls: [
                {
                    tool_name: "generation.createVideoCandidates",
                    arguments: {
                        node_id: selectedNode.id,
                        prompt: selectedNode.prompt || prompt,
                        model,
                        reference_image_urls: referenceImageUrls,
                        batch_size: 3,
                        params: {
                            duration: 5,
                            resolution: "720p",
                            prompt_extend: true,
                            generation_mode: "i2v",
                        },
                    },
                },
            ],
            error: null,
        };
    }

    if (selectedNode?.type === "video") {
        const model = getNodeModelId(selectedNode);
        return {
            toolCalls: [
                {
                    tool_name: "canvas.updateNodePrompt",
                    arguments: {
                        node_id: selectedNode.id,
                        prompt,
                        model,
                    },
                },
            ],
            error: null,
        };
    }

    return {
        toolCalls: [
            {
                tool_name: "canvas.createVideoNode",
                arguments: {
                    title: compactIntentTitle(prompt),
                    prompt,
                    model: getDefaultVideoModelId(),
                    x: 160 + projectNodeCount * 36,
                    y: 160 + projectNodeCount * 28,
                },
            },
        ],
        error: null,
    };
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
