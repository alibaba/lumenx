import { create } from "zustand";
import {
    api,
    type AtelierAgentPlan,
    type AtelierAgentPolicy,
    type AtelierAgentToolSpec,
    type AtelierAgentPlannerPackage,
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
    switchProject: (projectId: string) => Promise<AtelierProject>;
    loadAgentTools: () => Promise<AtelierAgentToolSpec[]>;
    buildPlannerPackage: (payload: {
        user_message?: string;
        selected_node_id?: string | null;
        skill_name?: string | null;
    }) => Promise<AtelierAgentPlannerPackage>;
    planAgentTurn: (payload: {
        user_message?: string;
        selected_node_id?: string | null;
        skill_name?: string | null;
        planner?: string | null;
        planner_input?: Record<string, unknown>;
    }) => Promise<AtelierAgentPlan>;
    updateAgentPolicy: (
        policy: Partial<Pick<AtelierAgentPolicy, "approval_mode" | "allowed_tools" | "max_nodes_per_action">>
    ) => Promise<AtelierProject>;
    runAgentTurn: (payload: RunAtelierAgentTurnPayload) => Promise<AtelierAgentTurn>;
    createVideoNode: () => Promise<AtelierNode>;
    createImageNode: (file: File) => Promise<AtelierNode>;
    createIdeaNode: (body?: string) => Promise<AtelierNode>;
    createCommentNode: (body?: string) => Promise<AtelierNode>;
    deleteAtelierNode: (nodeId: string) => Promise<void>;
    branchFromCandidate: (parentId: string, candidateId: string) => Promise<AtelierNode>;
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
    resizeNodeLocal: (nodeId: string, x: number, y: number, width: number, height: number) => void;
    commitNodePosition: (nodeId: string, x: number, y: number) => Promise<AtelierNode>;
    commitNodeBounds: (nodeId: string, x: number, y: number, width: number, height: number) => Promise<AtelierNode>;
    refreshCurrentProject: () => Promise<void>;
    selectNode: (nodeId: string | null) => void;
}

function replaceNode(project: AtelierProject, node: AtelierNode): AtelierProject {
    if (node.project_id !== project.id) return project;
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
    const nodeMap = new Map(
        nodes
            .filter((node) => node.project_id === project.id)
            .map((node) => [node.id, node])
    );
    return {
        ...project,
        nodes: project.nodes.map((node) => nodeMap.get(node.id) ?? node),
    };
}

let ensureProjectRequest: Promise<AtelierProject> | null = null;
let createProjectRequest: Promise<AtelierProject> | null = null;

function isNodeInProject(project: AtelierProject, nodeId: string): boolean {
    return project.nodes.some((node) => node.id === nodeId);
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
        if (ensureProjectRequest) return ensureProjectRequest;
        ensureProjectRequest = (async () => {
            await get().loadProjects();
            const loaded = get().currentProject;
            if (loaded) return loaded;
            return get().createProject();
        })();
        try {
            return await ensureProjectRequest;
        } finally {
            ensureProjectRequest = null;
        }
    },

    createProject: async (title = "Atelier Exploration") => {
        if (createProjectRequest) {
            const project = await createProjectRequest;
            set((state) => ({
                projects: replaceProject(state.projects, project),
                currentProject: state.currentProject ?? project,
                selectedNodeId: state.currentProject ? state.selectedNodeId : null,
                agentTurns: state.currentProject ? state.agentTurns : project.agent_turns ?? [],
                pendingAgentTurn: state.currentProject ? state.pendingAgentTurn : getPendingAgentTurn(project.agent_turns),
            }));
            return project;
        }
        createProjectRequest = api.createAtelierProject(title, "Freeform AI video exploration");
        let project: AtelierProject;
        try {
            project = await createProjectRequest;
        } finally {
            createProjectRequest = null;
        }
        set((state) => ({
            projects: [project, ...state.projects],
            currentProject: project,
            selectedNodeId: null,
            agentTurns: project.agent_turns ?? [],
            pendingAgentTurn: getPendingAgentTurn(project.agent_turns),
        }));
        return project;
    },

    switchProject: async (projectId: string) => {
        const cached = get().projects.find((p) => p.id === projectId);
        // Optimistic: flip currentProject to the cached snapshot if we have
        // one, so the canvas re-renders immediately.
        if (cached) {
            set({
                currentProject: cached,
                selectedNodeId: null,
                agentTurns: cached.agent_turns ?? [],
                pendingAgentTurn: getPendingAgentTurn(cached.agent_turns),
            });
        }
        // Then refetch authoritative state for fresh nodes/turns/policy.
        const fresh = await api.getAtelierProject(projectId);
        set((state) => ({
            projects: replaceProject(state.projects, fresh),
            currentProject: fresh,
            selectedNodeId: null,
            agentTurns: fresh.agent_turns ?? [],
            pendingAgentTurn: getPendingAgentTurn(fresh.agent_turns),
        }));
        return fresh;
    },

    loadAgentTools: async () => {
        const project = await get().ensureProject();
        const tools = await api.listAtelierAgentTools(project.id);
        set({ agentTools: tools });
        return tools;
    },

    buildPlannerPackage: async (payload) => {
        const project = await get().ensureProject();
        return api.buildAtelierAgentPlannerPackage(project.id, payload);
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
            currentProject: state.currentProject?.id === node.project_id
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, node] }
                : state.currentProject,
            selectedNodeId: state.currentProject?.id === node.project_id ? node.id : state.selectedNodeId,
        }));
        return node;
    },

    createImageNode: async (file) => {
        const project = await get().ensureProject();
        const upload = await api.uploadFile(file);
        const url = upload.url as string;
        // Place near the right of the existing nodes so it doesn't overlap.
        const offset = project.nodes.length * 24;
        const node = await api.createAtelierNode(project.id, {
            type: "image",
            title: file.name || `Reference ${project.nodes.length + 1}`,
            status: "completed",
            x: 80 + offset,
            y: 200 + offset,
            width: 220,
            height: 220,
            media_urls: [url],
            data: { filename: file.name },
        });
        set((state) => ({
            currentProject: state.currentProject?.id === node.project_id
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, node] }
                : state.currentProject,
            selectedNodeId: node.id,
        }));
        return node;
    },

    createIdeaNode: async (body) => {
        const project = await get().ensureProject();
        const offset = project.nodes.length * 24;
        const node = await api.createAtelierNode(project.id, {
            type: "idea",
            title: "Idea",
            prompt: body ?? "",
            status: "draft",
            x: 80 + offset,
            y: 480 + offset,
            width: 240,
            height: 120,
            // Leave body empty so the IdeaNode placeholder ('Empty idea —
            // double-click to edit.') reads as a placeholder, not as
            // literal user content.
            data: { body: body ?? "" },
        });
        set((state) => ({
            currentProject: state.currentProject?.id === node.project_id
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, node] }
                : state.currentProject,
            selectedNodeId: node.id,
        }));
        return node;
    },

    createCommentNode: async (body) => {
        const project = await get().ensureProject();
        const offset = project.nodes.length * 24;
        const node = await api.createAtelierNode(project.id, {
            type: "comment",
            title: "Comment",
            prompt: body ?? "",
            status: "completed",
            x: 80 + offset,
            y: 720 + offset,
            width: 220,
            height: 100,
            data: { body: body ?? "" },
        });
        set((state) => ({
            currentProject: state.currentProject?.id === node.project_id
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, node] }
                : state.currentProject,
            selectedNodeId: node.id,
        }));
        return node;
    },

    deleteAtelierNode: async (nodeId) => {
        const project = await get().ensureProject();
        if (!isNodeInProject(project, nodeId)) return;
        const target = project.nodes.find((n) => n.id === nodeId);
        // Cascade: if this is an image node, scan every video draft for a
        // reference back to it (via reference_node_ids OR reference_image_urls
        // matching the image's media_urls) and detach those references BEFORE
        // calling delete on the server. Otherwise the draft is left holding
        // dead URL pointers + stale node_id references that won't render but
        // still ride along through generation requests.
        if (target?.type === "image") {
            const targetUrls = new Set(target.media_urls ?? []);
            for (const node of project.nodes) {
                if (node.type !== "video") continue;
                const data = (node.data ?? {}) as Record<string, unknown>;
                const refUrls = Array.isArray(data.reference_image_urls)
                    ? (data.reference_image_urls as unknown[]).filter((u): u is string => typeof u === "string")
                    : [];
                const refIds = Array.isArray(data.reference_node_ids)
                    ? (data.reference_node_ids as unknown[]).filter((id): id is string => typeof id === "string")
                    : [];
                const cleanedUrls = refUrls.filter((u) => !targetUrls.has(u));
                const cleanedIds = refIds.filter((id) => id !== nodeId);
                const urlsChanged = cleanedUrls.length !== refUrls.length;
                const idsChanged = cleanedIds.length !== refIds.length;
                if (!urlsChanged && !idsChanged) continue;
                try {
                    await api.updateAtelierNode(project.id, node.id, {
                        data: {
                            ...data,
                            reference_image_urls: cleanedUrls,
                            reference_node_ids: cleanedIds,
                        },
                    });
                } catch {
                    // Best-effort cleanup. Continue even if one detach fails;
                    // the user-visible delete still proceeds and a refresh
                    // will eventually reconcile.
                }
            }
        }
        await api.deleteAtelierNode(project.id, nodeId);
        set((state) => {
            if (!state.currentProject || state.currentProject.id !== project.id) return state;
            return {
                ...state,
                currentProject: {
                    ...state.currentProject,
                    nodes: state.currentProject.nodes
                        .filter((n) => n.id !== nodeId)
                        .map((n) => {
                            if (n.type !== "video" || target?.type !== "image") return n;
                            const data = (n.data ?? {}) as Record<string, unknown>;
                            const refUrls = Array.isArray(data.reference_image_urls)
                                ? (data.reference_image_urls as unknown[]).filter((u): u is string => typeof u === "string")
                                : [];
                            const refIds = Array.isArray(data.reference_node_ids)
                                ? (data.reference_node_ids as unknown[]).filter((id): id is string => typeof id === "string")
                                : [];
                            const targetUrls = new Set(target?.media_urls ?? []);
                            const cleanedUrls = refUrls.filter((u) => !targetUrls.has(u));
                            const cleanedIds = refIds.filter((id) => id !== nodeId);
                            if (cleanedUrls.length === refUrls.length && cleanedIds.length === refIds.length) return n;
                            return {
                                ...n,
                                data: { ...data, reference_image_urls: cleanedUrls, reference_node_ids: cleanedIds },
                            };
                        }),
                },
                selectedNodeId: state.selectedNodeId === nodeId ? null : state.selectedNodeId,
            };
        });
    },

    branchFromCandidate: async (parentId, candidateId) => {
        const project = await get().ensureProject();
        const parent = project.nodes.find((n) => n.id === parentId);
        if (!parent) throw new Error("Parent node not found");
        const cands = (parent.data as { candidates?: unknown })?.candidates;
        if (!Array.isArray(cands)) throw new Error("Parent has no candidates");
        const cand = cands.find((c): c is { id: string; prompt?: string; model?: string; video_url?: string } =>
            !!c && typeof c === "object" && "id" in c && (c as { id: unknown }).id === candidateId
        );
        if (!cand) throw new Error("Candidate not found");
        // Branch = a fresh draft video node anchored to the right of the take.
        const node = await api.createAtelierNode(project.id, {
            type: "video",
            title: `${parent.title} · branch`,
            prompt: cand.prompt ?? parent.prompt ?? "",
            status: "draft",
            x: parent.x + (parent.width || 240) + 320,
            y: parent.y + 24,
            width: 240,
            height: 110,
            data: {
                intent: `Branched from ${parent.title}`,
                model: cand.model ?? "Wan 2.7",
                config_summary: "1280×720 · 5s · 4×",
                reference_image_urls: cand.video_url ? [cand.video_url] : [],
                branched_from: { parent_id: parentId, candidate_id: candidateId },
                candidates: [],
            },
        });
        set((state) => ({
            currentProject: state.currentProject?.id === node.project_id
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, node] }
                : state.currentProject,
            selectedNodeId: node.id,
        }));
        return node;
    },

    updateNode: async (nodeId, patch) => {
        const project = await get().ensureProject();
        if (!isNodeInProject(project, nodeId)) throw new Error("Atelier node not found in current project");
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
        // N:M attachments. The earlier 1:1 lock blocked shared-ref
        // workflows (motion_study / character_ref → multiple shots),
        // which is the central RHTV / LibTV pattern. Edge uniqueness is
        // still enforced per video via Set dedupe below.

        const nextRefs = Array.from(new Set([...getReferenceImageUrls(videoNode), referenceUrl]));
        const nextReferenceNodeIds = Array.from(new Set([...getReferenceNodeIds(videoNode), imageNode.id]));
        const updatedVideoNode = await api.updateAtelierNode(project.id, videoNode.id, {
            data: {
                ...(videoNode.data ?? {}),
                reference_image_urls: nextRefs,
                reference_node_ids: nextReferenceNodeIds,
            },
        });
        // Keep parent_node_id at the FIRST attacher for back-pointer
        // semantics in buildReferenceLinks; subsequent edges are
        // derivable from each video's reference_node_ids.
        const existingParent = imageNode.data?.parent_node_id;
        const updatedImageNode = await api.updateAtelierNode(project.id, imageNode.id, {
            data: {
                ...(imageNode.data ?? {}),
                parent_node_id: existingParent || videoNode.id,
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

    resizeNodeLocal: (nodeId, x, y, width, height) => {
        set((state) => ({
            currentProject: state.currentProject
                ? {
                    ...state.currentProject,
                    nodes: state.currentProject.nodes.map((node) =>
                        node.id === nodeId ? { ...node, x, y, width, height } : node
                    ),
                }
                : state.currentProject,
        }));
    },

    commitNodePosition: async (nodeId, x, y) => {
        return get().updateNode(nodeId, { x, y });
    },

    commitNodeBounds: async (nodeId, x, y, width, height) => {
        return get().updateNode(nodeId, { x, y, width, height });
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
