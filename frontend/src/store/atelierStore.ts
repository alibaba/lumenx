import { create } from "zustand";
import {
    api,
    type AtelierAgentPlan,
    type AtelierAgentPolicy,
    type AtelierAgentToolSpec,
    type AtelierAgentTurn,
    type AtelierGenerationConfig,
    type AtelierNode,
    type AtelierProject,
    type RunAtelierAgentTurnPayload,
} from "@/lib/api";

interface AtelierStore {
    projects: AtelierProject[];
    currentProject: AtelierProject | null;
    selectedNodeId: string | null;
    agentTools: AtelierAgentToolSpec[];
    agentTurns: AtelierAgentTurn[];
    pendingAgentTurn: AtelierAgentTurn | null;
    isLoading: boolean;
    isAgentRunning: boolean;
    error: string | null;
    loadProjects: () => Promise<void>;
    ensureProject: () => Promise<AtelierProject>;
    createProject: (title?: string) => Promise<AtelierProject>;
    loadAgentTools: () => Promise<AtelierAgentToolSpec[]>;
    planAgentTurn: (payload: {
        user_message?: string;
        selected_node_id?: string | null;
        skill_name?: string | null;
    }) => Promise<AtelierAgentPlan>;
    updateAgentPolicy: (
        policy: Partial<Pick<AtelierAgentPolicy, "approval_mode" | "allowed_tools" | "max_nodes_per_action">>
    ) => Promise<AtelierProject>;
    runAgentTurn: (payload: RunAtelierAgentTurnPayload) => Promise<AtelierAgentTurn>;
    createVideoNode: () => Promise<AtelierNode>;
    updateNode: (nodeId: string, patch: Partial<AtelierNode>) => Promise<AtelierNode>;
    uploadReferenceImage: (nodeId: string, file: File) => Promise<AtelierNode>;
    attachReferenceNode: (videoNodeId: string, imageNodeId: string) => Promise<AtelierNode>;
    detachReferenceNode: (videoNodeId: string, referenceUrl: string, imageNodeId?: string) => Promise<AtelierNode>;
    createVideoCandidates: (nodeId: string, config: AtelierGenerationConfig) => Promise<AtelierNode>;
    regenerateVideoCandidates: (nodeId: string, config?: Partial<AtelierGenerationConfig>) => Promise<AtelierNode>;
    retryCandidate: (nodeId: string, candidateId: string) => Promise<AtelierNode>;
    selectCandidate: (nodeId: string, candidateId: string) => Promise<AtelierNode>;
    deleteCandidate: (nodeId: string, candidateId: string) => Promise<AtelierNode>;
    moveNodeLocal: (nodeId: string, x: number, y: number) => void;
    commitNodePosition: (nodeId: string, x: number, y: number) => Promise<AtelierNode>;
    refreshCurrentProject: () => Promise<void>;
    selectNode: (nodeId: string | null) => void;
}

function replaceNode(project: AtelierProject, node: AtelierNode): AtelierProject {
    return {
        ...project,
        nodes: project.nodes.map((candidate) => (candidate.id === node.id ? node : candidate)),
    };
}

function replaceProject(projects: AtelierProject[], project: AtelierProject): AtelierProject[] {
    const exists = projects.some((candidate) => candidate.id === project.id);
    if (!exists) return [project, ...projects];
    return projects.map((candidate) => (candidate.id === project.id ? project : candidate));
}

function getPendingAgentTurn(turns: AtelierAgentTurn[] | undefined): AtelierAgentTurn | null {
    return [...(turns ?? [])].reverse().find((turn) => turn.status === "waiting_approval") ?? null;
}

function getPrimaryAgentResultNodeId(turn: AtelierAgentTurn): string | null {
    for (const toolCall of turn.tool_calls) {
        const snapshot = toolCall.result_snapshot;
        if (!snapshot || typeof snapshot !== "object") continue;
        const maybeNode = "node" in snapshot ? snapshot.node : "video_node" in snapshot ? snapshot.video_node : null;
        if (maybeNode && typeof maybeNode === "object" && "id" in maybeNode && typeof maybeNode.id === "string") {
            return maybeNode.id;
        }
    }
    return null;
}

function getProjectSelection(project: AtelierProject, requestedNodeId: string | null): string | null {
    if (requestedNodeId && project.nodes.some((node) => node.id === requestedNodeId)) return requestedNodeId;
    return project.nodes[0]?.id ?? null;
}

function getReferenceImageUrls(node: AtelierNode): string[] {
    const data = node.data ?? {};
    const refs = data.reference_image_urls;
    return Array.isArray(refs) ? refs.filter((item): item is string => typeof item === "string") : [];
}

function getReferenceNodeIds(node: AtelierNode): string[] {
    const data = node.data ?? {};
    const refs = data.reference_node_ids;
    return Array.isArray(refs) ? refs.filter((item): item is string => typeof item === "string") : [];
}

function replaceNodes(project: AtelierProject, nodes: AtelierNode[]): AtelierProject {
    const nodeMap = new Map(nodes.map((node) => [node.id, node]));
    return {
        ...project,
        nodes: project.nodes.map((node) => nodeMap.get(node.id) ?? node),
    };
}

export const useAtelierStore = create<AtelierStore>((set, get) => ({
    projects: [],
    currentProject: null,
    selectedNodeId: null,
    agentTools: [],
    agentTurns: [],
    pendingAgentTurn: null,
    isLoading: false,
    isAgentRunning: false,
    error: null,

    loadProjects: async () => {
        set({ isLoading: true, error: null });
        try {
            const projects = await api.listAtelierProjects();
            const currentProject = projects[0] ?? null;
            const agentTurns = currentProject?.agent_turns ?? [];
            set({
                projects,
                currentProject,
                selectedNodeId: currentProject?.nodes[0]?.id ?? null,
                agentTurns,
                pendingAgentTurn: getPendingAgentTurn(agentTurns),
            });
        } catch (error) {
            set({ error: error instanceof Error ? error.message : "Failed to load Atelier projects" });
        } finally {
            set({ isLoading: false });
        }
    },

    ensureProject: async () => {
        const existing = get().currentProject;
        if (existing) return existing;
        await get().loadProjects();
        const loaded = get().currentProject;
        if (loaded) return loaded;
        return get().createProject();
    },

    createProject: async (title = "Atelier Exploration") => {
        const project = await api.createAtelierProject(title, "Freeform AI video exploration");
        set((state) => ({
            projects: [project, ...state.projects],
            currentProject: project,
            selectedNodeId: null,
            agentTurns: project.agent_turns ?? [],
            pendingAgentTurn: getPendingAgentTurn(project.agent_turns),
        }));
        return project;
    },

    loadAgentTools: async () => {
        const project = await get().ensureProject();
        const tools = await api.listAtelierAgentTools(project.id);
        set({ agentTools: tools });
        return tools;
    },

    planAgentTurn: async (payload) => {
        const project = await get().ensureProject();
        return api.planAtelierAgentTurn(project.id, payload);
    },

    updateAgentPolicy: async (policy) => {
        const project = await get().ensureProject();
        const updated = await api.updateAtelierAgentPolicy(project.id, policy);
        set((state) => ({
            currentProject: updated,
            projects: replaceProject(state.projects, updated),
            selectedNodeId: getProjectSelection(updated, state.selectedNodeId),
            agentTurns: updated.agent_turns ?? [],
            pendingAgentTurn: getPendingAgentTurn(updated.agent_turns),
        }));
        return updated;
    },

    runAgentTurn: async (payload) => {
        const project = await get().ensureProject();
        set({ isAgentRunning: true, error: null });
        try {
            const turn = await api.runAtelierAgentTurn(project.id, payload);
            const refreshed = await api.getAtelierProject(project.id);
            const agentTurns = refreshed.agent_turns ?? [turn];
            const resultNodeId = getPrimaryAgentResultNodeId(turn);
            set((state) => ({
                currentProject: refreshed,
                projects: replaceProject(state.projects, refreshed),
                selectedNodeId: getProjectSelection(refreshed, resultNodeId ?? state.selectedNodeId),
                agentTurns,
                pendingAgentTurn: getPendingAgentTurn(agentTurns),
            }));
            return turn;
        } catch (error) {
            set({ error: error instanceof Error ? error.message : "Failed to run Atelier agent turn" });
            throw error;
        } finally {
            set({ isAgentRunning: false });
        }
    },

    createVideoNode: async () => {
        const project = await get().ensureProject();
        const node = await api.createAtelierNode(project.id, {
            type: "video",
            title: `Video Node ${project.nodes.length + 1}`,
            prompt: "Describe the motion, subject, style, and camera behavior.",
            x: 120 + project.nodes.length * 36,
            y: 120 + project.nodes.length * 28,
            width: 420,
            height: 560,
            data: {
                reference_image_urls: [],
                candidates: [],
            },
        });
        set((state) => ({
            currentProject: state.currentProject
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, node] }
                : state.currentProject,
            selectedNodeId: node.id,
        }));
        return node;
    },

    updateNode: async (nodeId, patch) => {
        const project = await get().ensureProject();
        const node = await api.updateAtelierNode(project.id, nodeId, patch);
        set((state) => ({
            currentProject: state.currentProject ? replaceNode(state.currentProject, node) : state.currentProject,
        }));
        return node;
    },

    uploadReferenceImage: async (nodeId, file) => {
        const project = await get().ensureProject();
        const node = project.nodes.find((candidate) => candidate.id === nodeId);
        if (!node) throw new Error("Atelier node not found");
        const upload = await api.uploadFile(file);
        const url = upload.url as string;
        const nextIndex = getReferenceImageUrls(node).length;
        const referenceNode = await api.createAtelierNode(project.id, {
            type: "image",
            title: file.name || `Reference ${nextIndex + 1}`,
            status: "completed",
            x: node.x - 260,
            y: node.y + nextIndex * 150,
            width: 220,
            height: 136,
            media_urls: [url],
            data: {
                parent_node_id: node.id,
                reference_role: "video_reference_image",
            },
        });
        const nextRefs = [...getReferenceImageUrls(node), url];
        const nextReferenceNodeIds = [...getReferenceNodeIds(node), referenceNode.id];
        const updatedNode = await api.updateAtelierNode(project.id, nodeId, {
            data: {
                ...(node.data ?? {}),
                reference_image_urls: nextRefs,
                reference_node_ids: nextReferenceNodeIds,
            },
        });
        set((state) => {
            if (!state.currentProject) return state;
            const withoutOldNode = state.currentProject.nodes.filter((candidate) => candidate.id !== updatedNode.id);
            return {
                currentProject: {
                    ...state.currentProject,
                    nodes: [...withoutOldNode, updatedNode, referenceNode],
                },
                selectedNodeId: updatedNode.id,
            };
        });
        return updatedNode;
    },

    attachReferenceNode: async (videoNodeId, imageNodeId) => {
        const project = await get().ensureProject();
        const videoNode = project.nodes.find((node) => node.id === videoNodeId);
        const imageNode = project.nodes.find((node) => node.id === imageNodeId);
        if (!videoNode) throw new Error("Atelier video node not found");
        if (!imageNode) throw new Error("Atelier reference node not found");
        const referenceUrl = imageNode.media_urls[0];
        if (!referenceUrl) throw new Error("Reference node has no media URL");
        const existingParentNodeId = imageNode.data?.parent_node_id;
        const existingVideoReference = project.nodes.find((node) =>
            node.type === "video" &&
            node.id !== videoNode.id &&
            (
                getReferenceNodeIds(node).includes(imageNode.id) ||
                getReferenceImageUrls(node).includes(referenceUrl)
            )
        );
        if ((existingParentNodeId && existingParentNodeId !== videoNode.id) || existingVideoReference) {
            throw new Error("Reference node is already attached to another video node");
        }

        const nextRefs = Array.from(new Set([...getReferenceImageUrls(videoNode), referenceUrl]));
        const nextReferenceNodeIds = Array.from(new Set([...getReferenceNodeIds(videoNode), imageNode.id]));
        const updatedVideoNode = await api.updateAtelierNode(project.id, videoNode.id, {
            data: {
                ...(videoNode.data ?? {}),
                reference_image_urls: nextRefs,
                reference_node_ids: nextReferenceNodeIds,
            },
        });
        const updatedImageNode = await api.updateAtelierNode(project.id, imageNode.id, {
            data: {
                ...(imageNode.data ?? {}),
                parent_node_id: videoNode.id,
                reference_role: "video_reference_image",
            },
        });

        set((state) => ({
            currentProject: state.currentProject
                ? replaceNodes(state.currentProject, [updatedVideoNode, updatedImageNode])
                : state.currentProject,
            selectedNodeId: updatedVideoNode.id,
        }));
        return updatedVideoNode;
    },

    detachReferenceNode: async (videoNodeId, referenceUrl, imageNodeId) => {
        const project = await get().ensureProject();
        const videoNode = project.nodes.find((node) => node.id === videoNodeId);
        if (!videoNode) throw new Error("Atelier video node not found");
        const inferredImageNode = imageNodeId
            ? project.nodes.find((node) => node.id === imageNodeId)
            : project.nodes.find((node) =>
                node.type === "image" &&
                node.media_urls.includes(referenceUrl) &&
                node.data?.parent_node_id === videoNode.id
            );
        const nextRefs = getReferenceImageUrls(videoNode).filter((url) => url !== referenceUrl);
        const nextReferenceNodeIds = getReferenceNodeIds(videoNode).filter((id) => id !== inferredImageNode?.id);
        const updatedVideoNode = await api.updateAtelierNode(project.id, videoNode.id, {
            data: {
                ...(videoNode.data ?? {}),
                reference_image_urls: nextRefs,
                reference_node_ids: nextReferenceNodeIds,
            },
        });

        let updatedImageNode: AtelierNode | null = null;
        if (inferredImageNode?.data?.parent_node_id === videoNode.id) {
            const restData = { ...inferredImageNode.data };
            delete restData.parent_node_id;
            delete restData.reference_role;
            updatedImageNode = await api.updateAtelierNode(project.id, inferredImageNode.id, {
                data: restData,
            });
        }

        set((state) => ({
            currentProject: state.currentProject
                ? replaceNodes(
                    state.currentProject,
                    updatedImageNode ? [updatedVideoNode, updatedImageNode] : [updatedVideoNode]
                )
                : state.currentProject,
            selectedNodeId: updatedVideoNode.id,
        }));
        return updatedVideoNode;
    },

    createVideoCandidates: async (nodeId, config) => {
        const project = await get().ensureProject();
        const node = await api.createAtelierVideoCandidates(project.id, nodeId, config);
        set((state) => ({
            currentProject: state.currentProject ? replaceNode(state.currentProject, node) : state.currentProject,
        }));
        return node;
    },

    regenerateVideoCandidates: async (nodeId, config) => {
        const project = await get().ensureProject();
        const node = await api.regenerateAtelierVideoCandidates(project.id, nodeId, config);
        set((state) => ({
            currentProject: state.currentProject ? replaceNode(state.currentProject, node) : state.currentProject,
        }));
        return node;
    },

    retryCandidate: async (nodeId, candidateId) => {
        const project = await get().ensureProject();
        const node = await api.retryAtelierVideoCandidate(project.id, nodeId, candidateId);
        set((state) => ({
            currentProject: state.currentProject ? replaceNode(state.currentProject, node) : state.currentProject,
        }));
        return node;
    },

    selectCandidate: async (nodeId, candidateId) => {
        const project = await get().ensureProject();
        const node = await api.selectAtelierVideoCandidate(project.id, nodeId, candidateId);
        set((state) => ({
            currentProject: state.currentProject ? replaceNode(state.currentProject, node) : state.currentProject,
        }));
        return node;
    },

    deleteCandidate: async (nodeId, candidateId) => {
        const project = await get().ensureProject();
        const node = await api.deleteAtelierVideoCandidate(project.id, nodeId, candidateId);
        set((state) => ({
            currentProject: state.currentProject ? replaceNode(state.currentProject, node) : state.currentProject,
        }));
        return node;
    },

    moveNodeLocal: (nodeId, x, y) => {
        set((state) => ({
            currentProject: state.currentProject
                ? {
                    ...state.currentProject,
                    nodes: state.currentProject.nodes.map((node) =>
                        node.id === nodeId ? { ...node, x, y } : node
                    ),
                }
                : state.currentProject,
        }));
    },

    commitNodePosition: async (nodeId, x, y) => {
        return get().updateNode(nodeId, { x, y });
    },

    refreshCurrentProject: async () => {
        const project = get().currentProject;
        if (!project) return;
        const refreshed = await api.getAtelierProject(project.id);
        const agentTurns = refreshed.agent_turns ?? [];
        set((state) => ({
            currentProject: refreshed,
            projects: replaceProject(state.projects, refreshed),
            selectedNodeId: getProjectSelection(refreshed, state.selectedNodeId),
            agentTurns,
            pendingAgentTurn: getPendingAgentTurn(agentTurns),
        }));
    },

    selectNode: (nodeId) => set({ selectedNodeId: nodeId }),
}));
