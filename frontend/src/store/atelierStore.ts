import { create } from "zustand";
import {
    api,
    API_URL,
    type AtelierAgentPlan,
    type AtelierAgentPolicy,
    type AtelierAgentToolSpec,
    type AtelierAgentPlannerPackage,
    type AtelierAgentToolCallPayload,
    type AtelierAgentTurn,
    type AtelierGenerationConfig,
    type AtelierNode,
    type AtelierProject,
    type RunAtelierAgentTurnPayload,
    type StreamAtelierAgentTurnPayload,
    type AgentStreamDoneEvent,
} from "@/lib/api";
import { computeRegionBoundsForNodes } from "@/components/atelier/v3/regionGeometry";
import {
    userTemplatesKey,
    type PersistedUserTemplate,
    type WorkflowTemplate,
} from "@/components/atelier/v3/workflowTemplates";

// v1.0 track T — per-tool-call timeline entry surfaced under the live
// streaming bubble in AgentPanelV3. The store appends one entry on
// every tool_start frame (status "running"), then patches the matching
// call_id when the harness emits tool_done. Status uses the harness's
// AtelierAgentToolStatus enum values verbatim ("completed" / "failed" /
// "denied" / "approval_required" / "proposed") so the chip rail can
// render a single icon per state without extra mapping.
export interface ToolProgress {
    call_id: string;
    tool_name: string;
    status: "running" | "completed" | "failed" | string;
    error?: string;
}

// Q (v0.9): per-file upload entry surfaced by AssetLibrary's
// active-uploads strip. Lives on the store so the panel can re-render on
// XHR progress events and so the upload survives a panel collapse /
// expand cycle. Owned by the uploadAsset_Q / cancelUpload_Q actions;
// AssetLibrary treats it as read-only state driven via props.
export interface AtelierUploadEntry {
    id: string;
    name: string;
    kind: "image" | "video" | "audio";
    /** 0-100. For lengthComputable=false uploads this cycles 5→95 via
     *  an indeterminate ticker so the bar still moves. */
    progress: number;
    status: "uploading" | "done" | "error";
    error?: string;
}

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
    /** Q (v0.9): live list of in-flight / recently-finished asset uploads
     *  driven by `uploadAsset_Q`. AssetLibrary renders rows from this
     *  slice; on `done` the entry is auto-purged after 1.5s, on `error`
     *  it sticks until the user dismisses via `cancelUpload_Q`. */
    activeUploads_Q: AtelierUploadEntry[];
    /** Q (v0.9): upload one OS file as an Atelier asset. Branches on
     *  MIME prefix to create an image / video / audio AtelierNode after
     *  the upload completes. Surfaces real progress via XHR (falls back
     *  to indeterminate cycling when the server does not report a
     *  total). Throws on unsupported MIME or upload failure so callers
     *  can toast. */
    uploadAsset_Q: (file: File) => Promise<AtelierNode>;
    /** Q (v0.9): dismiss / cancel an entry in `activeUploads_Q`. For
     *  in-flight uploads, aborts the underlying XHR; for terminal
     *  rows (done / error) this just removes the row from the strip. */
    cancelUpload_Q: (id: string) => void;
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
    /** P (v0.9): in-flight LLM streaming state surfaced to AgentPanelV3
     *  so it can render a partial response bubble with a blinking cursor.
     *  Intentionally NOT reusing `pendingAgentTurn` — that slice carries
     *  waiting_approval turns and a name collision would break the
     *  approval card render. Reset on stream end / abort / error.
     *
     *  v1.0 track T — extended with `tool_progress` so the panel can
     *  render a chip rail under the streamed bubble showing each tool
     *  call as it transitions from running -> completed/failed. The
     *  array is initialised lazily on the first tool_start event so
     *  the LLM-only phase has no extra slot to render. */
    streamingAgentTurn: {
        response: string;
        done: boolean;
        planner?: string | null;
        turnId?: string | null;
        toolCalls?: AtelierAgentToolCallPayload[];
        error?: string | null;
        tool_progress?: ToolProgress[];
    } | null;
    /** P (v0.9): execute a turn via the SSE streaming endpoint. Replaces
     *  the two-step planAgentTurn+runAgentTurn pair for model_adapter
     *  planner calls; deterministic planners stay on the sync path because
     *  they produce no token stream. Resolves with the final agent turn
     *  (or null if the stream was aborted / blocked / failed). */
    runAgentTurnStreaming_P: (
        payload: StreamAtelierAgentTurnPayload & { signal?: AbortSignal },
    ) => Promise<AtelierAgentTurn | null>;
    createVideoNode: () => Promise<AtelierNode>;
    createImageNode: (file: File) => Promise<AtelierNode>;
    /** v0.8 (M): persist a curated Browse seed as a real Atelier node.
     *  Skips api.uploadFile because the seed url already points at a
     *  static file under output/. Returns the created node so callers
     *  (shell drop handler) can chain attachReferenceNode on a draft. */
    createMediaNodeFromSeed: (
        seed: { id: string; kind: "image" | "video" | "audio"; title: string; url: string; category?: string; audioRole?: string },
        opts?: { x?: number; y?: number },
    ) => Promise<AtelierNode>;
    createIdeaNode: (body?: string) => Promise<AtelierNode>;
    createCommentNode: (body?: string) => Promise<AtelierNode>;
    deleteAtelierNode: (nodeId: string) => Promise<void>;
    branchFromCandidate: (parentId: string, candidateId: string) => Promise<AtelierNode>;
    /** Region (B-α): create a region node — a type:"region" container.
     *  When `wrap` is given, the new region's bounds are computed from
     *  the union bounding box of those nodes (with padding) and each
     *  is attached via `attachToRegion`. */
    createRegion: (opts: {
        title: string;
        color?: string;
        wrap?: string[];
        x?: number;
        y?: number;
        width?: number;
        height?: number;
    }) => Promise<AtelierNode>;
    /** Set `data.region_id` on the given node, preserving existing data. */
    attachToRegion: (nodeId: string, regionId: string) => Promise<AtelierNode>;
    /** Clear `data.region_id` on the given node. */
    detachFromRegion: (nodeId: string) => Promise<AtelierNode>;
    /** Move the region by (dx, dy), and translate every attached child
     *  by the same delta so the contents follow the container. Sibling
     *  nodes (no `data.region_id` or attached to a different region)
     *  are unaffected. */
    moveRegion: (regionId: string, dx: number, dy: number) => Promise<void>;
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
    // ── Track O (v0.9): async sequence export ────────────────────────
    // Thin shim over api.exportAtelierSequence — kicks off the POST →
    // poll loop and resolves with the legacy {video_url, ...} payload
    // once the worker hits `completed`. Callers (SequenceStrip) own
    // the AbortController and the per-poll progress sink so the action
    // stays pure. Suffix `_O` per the v0.9 track-collision rule in
    // atelierStore (O / P / Q / R all add named actions here).
    exportSequenceAsync_O: (
        projectId: string,
        entries: Array<{ parentId: string; candidateId: string; trimStart?: number; trimEnd?: number }>,
        options?: { signal?: AbortSignal; onProgress?: (pct: number) => void },
    ) => Promise<{ video_url: string; filename: string; size_mb: number; clip_count: number }>;
    // ── Track R (v0.9): user-saved workflow templates ────────────────
    // Per-project localStorage round-trip. Suffix `_R` keeps the action
    // names unique across the v0.9 parallel tracks (O / P / Q / R all
    // co-edit this store); a future cleanup pass can rename if desired.
    //
    // The actions intentionally take an explicit `projectId` rather than
    // resolving from `state.currentProject` internally. Callers (the
    // panel + the shim helpers in WorkflowsPanel.tsx) read the active
    // project id from the store at call time — this keeps the actions
    // pure-functional (no implicit dependency on state shape) and lets
    // tests pass a synthetic id without spinning up a full project.
    getUserTemplates_R: (projectId?: string | null) => PersistedUserTemplate[];
    addUserTemplate_R: (projectId: string, tpl: PersistedUserTemplate) => void;
    removeUserTemplate_R: (projectId: string, id: string) => void;
}

// ── Track R (v0.9): localStorage helpers ─────────────────────────────
// The store actions below are thin wrappers over these. Defensive
// against quota errors, SSR (no `window`), and legacy entries written
// before the v0.9 schema bump that lack `origin` / `savedAt`.
function readUserTemplatesFromStorage(projectId: string): PersistedUserTemplate[] {
    if (typeof window === "undefined") return [];
    try {
        const raw = window.localStorage.getItem(userTemplatesKey(projectId));
        if (!raw) return [];
        const parsed = JSON.parse(raw) as unknown;
        if (!Array.isArray(parsed)) return [];
        // Cheap shape filter — drop anything that doesn't have the
        // minimum surface a template needs. Backfill `origin` and
        // `savedAt` on entries persisted by an older path (or by a
        // developer hand-editing localStorage) so downstream UI doesn't
        // have to coalesce on every render.
        return parsed
            .filter(
                (t): t is WorkflowTemplate =>
                    !!t &&
                    typeof t === "object" &&
                    typeof (t as WorkflowTemplate).id === "string" &&
                    Array.isArray((t as WorkflowTemplate).nodes),
            )
            .map((t) => {
                const wt = t as WorkflowTemplate;
                return {
                    ...wt,
                    origin: "user" as const,
                    savedAt: typeof wt.savedAt === "number" ? wt.savedAt : 0,
                };
            });
    } catch {
        return [];
    }
}

function writeUserTemplatesToStorage(
    projectId: string,
    list: PersistedUserTemplate[],
): void {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.setItem(
            userTemplatesKey(projectId),
            JSON.stringify(list),
        );
        // Notify any listener (the WorkflowsPanel + future watchers) so
        // the Mine tab re-syncs without polling. Kept on the legacy
        // event name for backward compat with the v0.8 panel listener.
        window.dispatchEvent(new CustomEvent("atelier-user-workflows-changed"));
    } catch {
        /* ignore quota / private mode */
    }
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

// Q (v0.9): non-serialisable handles for in-flight uploads. The
// XMLHttpRequest itself is held outside Zustand state — Zustand stores
// snapshot on every set, so embedding a live XHR would broadcast the
// full object on every progress tick. When cancelUpload_Q removes an
// entry we look up the XHR by id and abort. Indeterminate cycle tickers
// (when lengthComputable is false) live here too so cleanup is symmetric.
const Q_UPLOAD_XHRS = new Map<string, XMLHttpRequest>();
const Q_UPLOAD_TICKERS = new Map<string, number>();

// FIX-1/FIX-4: per-target-node serialisation lock for reference attachments.
// Two rapid-fire attachReferenceNode / uploadReferenceImage calls against the
// SAME video node were racing on the closure-captured `videoNode` snapshot,
// each reading the pre-mutation reference_node_ids and the second call's
// PUT overwrote the first. By chaining promises per nodeId we make the
// later call wait for the earlier one, so it re-reads the latest project
// state inside the chain.
const REF_ATTACH_CHAIN = new Map<string, Promise<unknown>>();

function chainReferenceAttach<T>(nodeId: string, task: () => Promise<T>): Promise<T> {
    const previous = REF_ATTACH_CHAIN.get(nodeId) ?? Promise.resolve();
    const next = previous.then(() => task(), () => task());
    REF_ATTACH_CHAIN.set(
        nodeId,
        next.finally(() => {
            // Only clear if no further task has chained onto us.
            if (REF_ATTACH_CHAIN.get(nodeId) === next) {
                REF_ATTACH_CHAIN.delete(nodeId);
            }
        }),
    );
    return next;
}

function qPruneUploadHandles(id: string): void {
    const xhr = Q_UPLOAD_XHRS.get(id);
    if (xhr) {
        try { xhr.abort(); } catch { /* already done */ }
        Q_UPLOAD_XHRS.delete(id);
    }
    const ticker = Q_UPLOAD_TICKERS.get(id);
    if (ticker !== undefined) {
        window.clearInterval(ticker);
        Q_UPLOAD_TICKERS.delete(id);
    }
}

function qKindForFile(file: File): "image" | "video" | "audio" | null {
    if (file.type.startsWith("image/")) return "image";
    if (file.type.startsWith("video/")) return "video";
    if (file.type.startsWith("audio/")) return "audio";
    return null;
}

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
    // P (v0.9): no streaming turn in flight at startup.
    streamingAgentTurn: null,
    // Q (v0.9): live upload strip — see uploadAsset_Q / cancelUpload_Q
    // for the lifecycle. Initialised empty; AssetLibrary mounts with no
    // rows and the section is hidden until the first push.
    activeUploads_Q: [],

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

    // P (v0.9) — SSE streaming agent turn. Updates `streamingAgentTurn`
    // incrementally so AgentPanelV3 can render a live partial response
    // bubble. On the final `done` event, writes the persisted turn into
    // currentProject + agentTurns the same way runAgentTurn does, then
    // clears `streamingAgentTurn`. AbortError (user clicked Stop) is
    // silently swallowed: state is cleared, no `error` is set.
    runAgentTurnStreaming_P: async (payload) => {
        const project = await get().ensureProject();
        const { signal, ...streamPayload } = payload;
        set({
            isAgentRunning: true,
            error: null,
            streamingAgentTurn: { response: "", done: false },
        });
        try {
            const done: AgentStreamDoneEvent = await api.streamAtelierAgentTurn(
                project.id,
                streamPayload,
                {
                    signal,
                    // v1.0 track T — single sink for the new
                    // (turn/llm_delta/llm_done/tool_start/tool_done/
                    // turn_done) wire format. Keeps the response
                    // buffer + tool_progress timeline in sync with the
                    // backend's streaming view.
                    onEvent: (event) => {
                        set((state) => {
                            const current = state.streamingAgentTurn ?? {
                                response: "",
                                done: false,
                            };
                            switch (event.type) {
                                case "turn":
                                    return {
                                        streamingAgentTurn: {
                                            ...current,
                                            planner: event.planner,
                                            turnId: event.turn_id,
                                        },
                                    };
                                case "llm_delta":
                                    return {
                                        streamingAgentTurn: {
                                            ...current,
                                            response: current.response + event.text,
                                        },
                                    };
                                case "llm_done":
                                    return {
                                        streamingAgentTurn: {
                                            ...current,
                                            response:
                                                event.response && event.response.length > current.response.length
                                                    ? event.response
                                                    : current.response,
                                        },
                                    };
                                case "tool_start": {
                                    const progress = current.tool_progress ?? [];
                                    return {
                                        streamingAgentTurn: {
                                            ...current,
                                            tool_progress: [
                                                ...progress,
                                                {
                                                    call_id: event.call_id,
                                                    tool_name: event.tool_name,
                                                    status: "running",
                                                },
                                            ],
                                        },
                                    };
                                }
                                case "tool_done": {
                                    const progress = current.tool_progress ?? [];
                                    const nextStatus: ToolProgress["status"] =
                                        event.status === "completed"
                                            ? "completed"
                                            : event.status === "failed"
                                              ? "failed"
                                              : event.status;
                                    let matched = false;
                                    const updated = progress.map((entry) => {
                                        if (entry.call_id !== event.call_id) return entry;
                                        matched = true;
                                        return {
                                            ...entry,
                                            status: nextStatus,
                                            error: event.error ?? undefined,
                                        };
                                    });
                                    if (!matched) {
                                        updated.push({
                                            call_id: event.call_id,
                                            tool_name: event.tool_name,
                                            status: nextStatus,
                                            error: event.error ?? undefined,
                                        });
                                    }
                                    return {
                                        streamingAgentTurn: {
                                            ...current,
                                            tool_progress: updated,
                                        },
                                    };
                                }
                                case "turn_done":
                                    // Flip `done` so the bubble drops its
                                    // blinking cursor; the slice itself is
                                    // cleared by the post-stream reconciliation
                                    // below once the persisted turn is hydrated.
                                    return {
                                        streamingAgentTurn: {
                                            ...current,
                                            done: true,
                                        },
                                    };
                                default:
                                    return {};
                            }
                        });
                    },
                },
            );

            if (done.status === "completed" && done.turn) {
                // Hydrate the canvas / turns view from the executed turn.
                const refreshed = await api.getAtelierProject(project.id);
                const agentTurns = refreshed.agent_turns ?? [done.turn];
                const resultNodeId = getPrimaryAgentResultNodeId(done.turn);
                set((state) => ({
                    currentProject: refreshed,
                    projects: replaceProject(state.projects, refreshed),
                    selectedNodeId: getProjectSelection(
                        refreshed,
                        resultNodeId ?? state.selectedNodeId,
                    ),
                    agentTurns,
                    pendingAgentTurn: getPendingAgentTurn(agentTurns),
                    streamingAgentTurn: null,
                }));
                return done.turn;
            }

            // blocked / failed paths: surface the error on the store so
            // AgentPanelV3 can render it, then clear the streaming slice
            // so the panel falls back to the static planError banner.
            set({
                streamingAgentTurn: null,
                error: done.error || (done.status === "blocked"
                    ? done.full_response || "Agent declined to act."
                    : "Agent turn failed."),
            });
            return null;
        } catch (error) {
            const isAbort = error instanceof DOMException && error.name === "AbortError";
            set({
                streamingAgentTurn: null,
                error: isAbort
                    ? null
                    : error instanceof Error
                        ? error.message
                        : "Atelier agent stream failed",
            });
            if (isAbort) return null;
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

    // Q (v0.9): generic asset upload. Mirrors createImageNode's persist
    // path but uses XMLHttpRequest so we can report real upload progress
    // to the AssetLibrary strip. Branches on MIME prefix to create the
    // right AtelierNode type (image / video / audio); each gets the
    // per-kind size that AssetLibrary's readMediaKind / cardgrid already
    // know how to render. Errors bubble out so callers can toast — but
    // the activeUploads_Q entry is left in 'error' state for the user to
    // see WHICH file failed (dismissable via cancelUpload_Q).
    uploadAsset_Q: async (file: File): Promise<AtelierNode> => {
        const kind = qKindForFile(file);
        if (!kind) {
            throw new Error("Unsupported file type — only image / video / audio are accepted.");
        }
        const id = (typeof crypto !== "undefined" && "randomUUID" in crypto)
            ? crypto.randomUUID()
            : `q-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        const entry: AtelierUploadEntry = {
            id,
            name: file.name,
            kind,
            progress: 0,
            status: "uploading",
        };
        // Push the row before kicking off network I/O so the strip
        // shows up immediately on click.
        set((state) => ({ activeUploads_Q: [...state.activeUploads_Q, entry] }));

        // We need the project resolved BEFORE we can persist the node,
        // but the upload itself is project-agnostic (the backend writes
        // to output/uploads/<uuid>.<ext>). Resolve in parallel so a
        // slow ensureProject doesn't artificially gate the XHR.
        const projectPromise = get().ensureProject();

        const url = await new Promise<string>((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            Q_UPLOAD_XHRS.set(id, xhr);
            xhr.open("POST", `${API_URL}/upload`);
            xhr.responseType = "json";

            // Fallback ticker for servers that don't expose
            // Content-Length on the request body (rare with multipart,
            // but defensive). Cycles 5→95 so the bar still moves.
            // Real onprogress events cancel the ticker on first fire.
            let realProgressSeen = false;
            const ticker = window.setInterval(() => {
                if (realProgressSeen) return;
                set((state) => ({
                    activeUploads_Q: state.activeUploads_Q.map((e) => {
                        if (e.id !== id || e.status !== "uploading") return e;
                        const next = e.progress + 5;
                        return { ...e, progress: next >= 95 ? 5 : next };
                    }),
                }));
            }, 250);
            Q_UPLOAD_TICKERS.set(id, ticker);

            xhr.upload.onprogress = (ev) => {
                if (!ev.lengthComputable) return;
                realProgressSeen = true;
                const pct = Math.max(1, Math.min(99, Math.round((ev.loaded / ev.total) * 100)));
                set((state) => ({
                    activeUploads_Q: state.activeUploads_Q.map((e) =>
                        e.id === id && e.status === "uploading" ? { ...e, progress: pct } : e,
                    ),
                }));
            };

            xhr.onload = () => {
                qPruneUploadHandles(id);
                if (xhr.status < 200 || xhr.status >= 300) {
                    reject(new Error(`Upload failed (HTTP ${xhr.status})`));
                    return;
                }
                const body = xhr.response as { url?: string } | null;
                const out = body && typeof body.url === "string" ? body.url : null;
                if (!out) {
                    reject(new Error("Upload response missing url field"));
                    return;
                }
                resolve(out);
            };
            xhr.onerror = () => {
                qPruneUploadHandles(id);
                reject(new Error("Network error during upload"));
            };
            xhr.onabort = () => {
                qPruneUploadHandles(id);
                // FIX-3: reject with a DOMException 'AbortError' so the
                // outer .catch + AssetLibrary onUpload loop can distinguish
                // user-initiated cancels from genuine upload failures and
                // skip the error toast.
                reject(new DOMException("Upload cancelled", "AbortError"));
            };

            const form = new FormData();
            form.append("file", file);
            xhr.send(form);
        }).catch((err: unknown) => {
            const message = err instanceof Error ? err.message : String(err);
            // FIX-3: when cancelUpload_Q already removed the entry we must
            // NOT re-insert a synthetic "error" row — leave activeUploads_Q
            // alone if the row is gone.
            set((state) => {
                const exists = state.activeUploads_Q.some((e) => e.id === id);
                if (!exists) return state;
                return {
                    activeUploads_Q: state.activeUploads_Q.map((e) =>
                        e.id === id ? { ...e, status: "error", error: message } : e,
                    ),
                };
            });
            throw err instanceof Error ? err : new Error(message);
        });

        const project = await projectPromise;
        const offset = project.nodes.length * 24;

        // Per-kind footprint mirrors createMediaNodeFromSeed and
        // createImageNode so uploaded assets read as the same shape as
        // their seed / drag-on-canvas equivalents. Audio strips live as
        // 260×80 strips, videos as 320×240 plates, images as 220 squares.
        let node: AtelierNode;
        if (kind === "image") {
            node = await api.createAtelierNode(project.id, {
                type: "image",
                title: file.name || `Upload ${project.nodes.length + 1}`,
                status: "completed",
                x: 80 + offset,
                y: 200 + offset,
                width: 220,
                height: 220,
                media_urls: [url],
                data: { filename: file.name, uploaded: true },
            });
        } else if (kind === "video") {
            // status:"completed" + media_urls present means AssetLibrary's
            // readMediaKind picks the node up; draft videos are
            // intentionally excluded by that guard.
            node = await api.createAtelierNode(project.id, {
                type: "video",
                title: file.name || `Upload ${project.nodes.length + 1}`,
                status: "completed",
                x: 80 + offset,
                y: 200 + offset,
                width: 320,
                height: 240,
                media_urls: [url],
                data: { filename: file.name, uploaded: true },
            });
        } else {
            node = await api.createAtelierNode(project.id, {
                type: "audio",
                title: file.name || `Upload ${project.nodes.length + 1}`,
                status: "completed",
                x: 80 + offset,
                y: 200 + offset,
                width: 260,
                height: 80,
                media_urls: [url],
                data: { filename: file.name, uploaded: true },
            });
        }

        set((state) => ({
            currentProject: state.currentProject?.id === node.project_id
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, node] }
                : state.currentProject,
            selectedNodeId: node.id,
            activeUploads_Q: state.activeUploads_Q.map((e) =>
                e.id === id ? { ...e, status: "done", progress: 100 } : e,
            ),
        }));

        // Auto-prune the row 1.5s after success so the strip clears on
        // its own. Error rows stick until the user dismisses via X.
        window.setTimeout(() => {
            set((state) => ({
                activeUploads_Q: state.activeUploads_Q.filter((e) => e.id !== id),
            }));
        }, 1500);

        return node;
    },

    cancelUpload_Q: (id: string) => {
        qPruneUploadHandles(id);
        set((state) => ({
            activeUploads_Q: state.activeUploads_Q.filter((e) => e.id !== id),
        }));
    },

    createMediaNodeFromSeed: async (seed, opts) => {
        const project = await get().ensureProject();
        const offset = project.nodes.length * 24;
        const x = opts?.x ?? 80 + offset;
        const y = opts?.y ?? 200 + offset;
        // Per-kind default footprint mirrors the per-type sizing the
        // shell already uses when other entry points create media nodes
        // (image references 220 sq, audio strips ~260×80, video plates
        // ~320×240). Keeping each kind's default ratio means a fresh
        // seed drop reads as the same shape as a hand-uploaded asset.
        const size = seed.kind === "video"
            ? { width: 320, height: 240 }
            : seed.kind === "audio"
                ? { width: 260, height: 80 }
                : { width: 240, height: 240 };
        const data: Record<string, unknown> = { seed: true, seed_id: seed.id };
        if (seed.kind === "image" && seed.category) {
            data.category = seed.category;
        }
        if (seed.kind === "audio" && seed.audioRole) {
            data.audio_role = seed.audioRole;
        }
        const node = await api.createAtelierNode(project.id, {
            type: seed.kind,
            title: seed.title,
            status: "completed",
            x,
            y,
            width: size.width,
            height: size.height,
            media_urls: [seed.url],
            data,
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
        // Region cascade (B-α): when deleting a region, the contained
        // child nodes survive — they just lose their region_id binding.
        // We detach BEFORE deletion so the children never spend a tick
        // pointing at a vanished region. Sorted by id for deterministic
        // call order in tests.
        if (target?.type === "region") {
            const children = project.nodes
                .filter((n) => (n.data as { region_id?: string })?.region_id === nodeId)
                .sort((a, b) => a.id.localeCompare(b.id));
            for (const child of children) {
                try {
                    await get().detachFromRegion(child.id);
                } catch {
                    // Best effort — partial detach still allows delete to
                    // proceed; orphaned region_id will be cleaned up on
                    // the next user edit of the child.
                }
            }
        }
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

    createRegion: async (opts) => {
        const project = await get().ensureProject();
        // Bounds: derived from wrap selection, or default size offset by
        // existing nodes (matches the cascade pattern used by createIdeaNode
        // / createCommentNode for "user keeps clicking add").
        const wrapIds = opts.wrap ?? [];
        const wrapNodes = wrapIds
            .map((id) => project.nodes.find((n) => n.id === id))
            .filter((n): n is AtelierNode => !!n);
        let x = opts.x ?? 80 + project.nodes.length * 24;
        let y = opts.y ?? 80 + project.nodes.length * 24;
        let width = opts.width ?? 600;
        let height = opts.height ?? 400;
        if (wrapNodes.length > 0) {
            const bounds = computeRegionBoundsForNodes(
                wrapNodes.map((n) => ({
                    id: n.id,
                    type: n.type,
                    x: n.x,
                    y: n.y,
                    width: n.width,
                    height: n.height,
                })),
            );
            if (bounds) {
                x = bounds.x;
                y = bounds.y;
                width = bounds.width;
                height = bounds.height;
            }
        }
        const region = await api.createAtelierNode(project.id, {
            type: "region",
            title: opts.title,
            status: "completed",
            x,
            y,
            width,
            height,
            data: { color: opts.color ?? "default" },
        });
        set((state) => ({
            currentProject: state.currentProject?.id === region.project_id
                ? { ...state.currentProject, nodes: [...state.currentProject.nodes, region] }
                : state.currentProject,
            selectedNodeId: state.currentProject?.id === region.project_id ? region.id : state.selectedNodeId,
        }));
        // Attach wrapped nodes one by one so each respects the same
        // store update path used by drag-drop spatial attach.
        for (const child of wrapNodes) {
            try {
                await get().attachToRegion(child.id, region.id);
            } catch {
                // Best effort — partial wrap is recoverable, the user can
                // drag missed nodes in. Surfacing toast here would mask
                // the original creation success.
            }
        }
        return region;
    },

    attachToRegion: async (nodeId, regionId) => {
        const project = await get().ensureProject();
        const node = project.nodes.find((n) => n.id === nodeId);
        if (!node) throw new Error("Atelier node not found");
        const updated = await api.updateAtelierNode(project.id, nodeId, {
            data: { ...(node.data ?? {}), region_id: regionId },
        });
        set((state) => ({
            currentProject: state.currentProject
                ? replaceNode(state.currentProject, updated)
                : state.currentProject,
        }));
        return updated;
    },

    detachFromRegion: async (nodeId) => {
        const project = await get().ensureProject();
        const node = project.nodes.find((n) => n.id === nodeId);
        if (!node) throw new Error("Atelier node not found");
        // Build a new data object without region_id. Backend update
        // replaces the data dict whole (see pipeline.update_atelier_node),
        // so we need to omit the key, not set it to null.
        const nextData: Record<string, unknown> = { ...(node.data ?? {}) };
        delete nextData.region_id;
        const updated = await api.updateAtelierNode(project.id, nodeId, {
            data: nextData,
        });
        set((state) => ({
            currentProject: state.currentProject
                ? replaceNode(state.currentProject, updated)
                : state.currentProject,
        }));
        return updated;
    },

    moveRegion: async (regionId, dx, dy) => {
        if (dx === 0 && dy === 0) return;
        const project = await get().ensureProject();
        const region = project.nodes.find((n) => n.id === regionId);
        if (!region) throw new Error("Atelier region not found");
        const children = project.nodes.filter(
            (n) => (n.data as { region_id?: string })?.region_id === regionId,
        );
        // Issue updates in parallel — they're independent rows on the
        // server side. The server save lock serializes them anyway.
        const updates: Array<Promise<AtelierNode>> = [];
        updates.push(
            api.updateAtelierNode(project.id, region.id, {
                x: region.x + dx,
                y: region.y + dy,
            }),
        );
        for (const child of children) {
            updates.push(
                api.updateAtelierNode(project.id, child.id, {
                    x: child.x + dx,
                    y: child.y + dy,
                }),
            );
        }
        const updatedNodes = await Promise.all(updates);
        set((state) => ({
            currentProject: state.currentProject
                ? replaceNodes(state.currentProject, updatedNodes)
                : state.currentProject,
        }));
    },

    uploadReferenceImage: async (nodeId, file) => chainReferenceAttach(nodeId, async () => {
        const project = await get().ensureProject();
        const node = project.nodes.find((candidate) => candidate.id === nodeId);
        if (!node) throw new Error("Atelier node not found");
        const upload = await api.uploadFile(file);
        const url = upload.url as string;
        // FIX-4: re-read the parent node AFTER the upload completes so a
        // concurrent attachReferenceNode that landed during the network
        // round-trip doesn't get its reference_node_ids stomped.
        const freshProjectAfterUpload = get().currentProject;
        const latestNodeAfterUpload =
            freshProjectAfterUpload?.id === project.id
                ? freshProjectAfterUpload.nodes.find((candidate) => candidate.id === nodeId) ?? node
                : node;
        const nextIndex = getReferenceImageUrls(latestNodeAfterUpload).length;
        const referenceNode = await api.createAtelierNode(project.id, {
            type: "image",
            title: file.name || `Reference ${nextIndex + 1}`,
            status: "completed",
            x: latestNodeAfterUpload.x - 260,
            y: latestNodeAfterUpload.y + nextIndex * 150,
            width: 220,
            height: 136,
            media_urls: [url],
            data: {
                parent_node_id: latestNodeAfterUpload.id,
                reference_role: "video_reference_image",
            },
        });
        // FIX-4: re-read once more after the createAtelierNode round-trip
        // before computing the merged ref arrays — otherwise the stale
        // pre-upload node still owns the data passed to updateAtelierNode.
        const freshProjectAfterCreate = get().currentProject;
        const latestNode =
            freshProjectAfterCreate?.id === project.id
                ? freshProjectAfterCreate.nodes.find((candidate) => candidate.id === nodeId) ?? latestNodeAfterUpload
                : latestNodeAfterUpload;
        const nextRefs = [...getReferenceImageUrls(latestNode), url];
        const nextReferenceNodeIds = [...getReferenceNodeIds(latestNode), referenceNode.id];
        const updatedNode = await api.updateAtelierNode(project.id, nodeId, {
            data: {
                ...(latestNode.data ?? {}),
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
    }),

    attachReferenceNode: async (videoNodeId, imageNodeId) => chainReferenceAttach(videoNodeId, async () => {
        const project = await get().ensureProject();
        // FIX-1: re-read videoNode + imageNode from get().currentProject
        // IMMEDIATELY BEFORE computing nextRefs / nextReferenceNodeIds so a
        // concurrent attach against the same target node sees the latest
        // reference_node_ids list instead of the pre-mutation snapshot.
        const freshProject = get().currentProject ?? project;
        const videoNode = freshProject.nodes.find((node) => node.id === videoNodeId)
            ?? project.nodes.find((node) => node.id === videoNodeId);
        const imageNode = freshProject.nodes.find((node) => node.id === imageNodeId)
            ?? project.nodes.find((node) => node.id === imageNodeId);
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
    }),

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

    // ── Track O (v0.9): async sequence export ────────────────────────
    // Pure passthrough — the api.ts orchestrator already owns the
    // POST/poll/abort lifecycle. The store action exists so consumers
    // can subscribe to it via zustand (consistency with other Atelier
    // RPC actions) and so we have a single seam to swap in a mocked
    // export client in tests.
    exportSequenceAsync_O: async (projectId, entries, options) => {
        return api.exportAtelierSequence(projectId, entries, options);
    },

    // ── Track R (v0.9): user-saved workflow templates ────────────────
    // Pure localStorage round-trip; no zustand state lives in the store
    // (the Mine tab subscribes to the `atelier-user-workflows-changed`
    // event for re-renders). A falsy `projectId` on the getter returns
    // [] — the panel renders the friendly "save your selections" empty
    // state. Mutating actions REQUIRE a project id (they early-return
    // silently when missing rather than throw — the shell's save
    // handler runs ensureProject before calling, so this is a safety
    // net).
    getUserTemplates_R: (projectId) => {
        if (!projectId) return [];
        return readUserTemplatesFromStorage(projectId);
    },
    addUserTemplate_R: (projectId, tpl) => {
        if (!projectId) return;
        const list = readUserTemplatesFromStorage(projectId);
        // Newest-first ordering matches the v0.8 behavior (unshift) so
        // a fresh save always lands at the top of the Mine list.
        const next = [tpl, ...list.filter((t) => t.id !== tpl.id)];
        writeUserTemplatesToStorage(projectId, next);
    },
    removeUserTemplate_R: (projectId, id) => {
        if (!projectId) return;
        const list = readUserTemplatesFromStorage(projectId);
        const next = list.filter((t) => t.id !== id);
        if (next.length === list.length) return; // nothing to remove
        writeUserTemplatesToStorage(projectId, next);
    },
}));
