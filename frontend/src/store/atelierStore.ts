import { create } from "zustand";
import { mergeAtelierGenerationState } from "@/lib/atelierGenerationMerge";
import { assertAtelierGenerationAdmissionMatchesRequest, atelierGenerationApi } from "@/lib/atelierGenerationApi";
import { atelierAgentApi, type AtelierAgentTurnAck } from "@/lib/atelierAgentApi";
import { atelierAgentLoopApi, resumeLoopStream, startLoopStream, type AtelierAgentLoopAck, type AtelierAgentLoopEvent, type AtelierAgentLoopPlanner } from "@/lib/atelierAgentLoopApi";
import { continueTurnStream, startTurnStream, type AtelierAgentStreamErrorEvent, type AtelierAgentStreamEvent } from "@/lib/atelierAgentStream";
import {
    beginAtelierGenerationRequest, listProjectAtelierGenerationRequests, resolveAtelierGenerationRequest,
    type StoredAtelierGenerationRequest,
} from "@/lib/atelierGenerationRequest";
import {
    beginAtelierAgentLoopRequest, beginAtelierAgentTurnRequest, createAtelierAgentTurnRequestId, listProjectAtelierAgentTurnRequests,
    resolveAtelierAgentLoopRequest, resolveAtelierAgentTurnRequest,
    type StoredAtelierAgentLoopRequest, type StoredAtelierAgentSingleTurnRequest, type StoredAtelierAgentTurnRequest,
} from "@/lib/atelierAgentTurnRequest";
import { atelierProjectApi } from "@/lib/atelierApi";
import { atelierCandidateBranchApi } from "@/lib/atelierCandidateBranchApi";
import { atelierCandidateJudgmentApi } from "@/lib/atelierCandidateJudgmentApi";
import { atelierMediaApi, type AtelierMediaDraftInput } from "@/lib/atelierMediaApi";
import { atelierNodeApi } from "@/lib/atelierNodeApi";
import { atelierNodeHistoryApi } from "@/lib/atelierNodeHistoryApi";
import { atelierReferenceApi } from "@/lib/atelierReferenceApi";
import { ATELIER_UPLOAD_TARGET_ACTIVE_CODE, atelierUploadApi, validateAtelierUploadInput } from "@/lib/atelierUploadApi";
import { atelierSequenceApi, type AtelierExportDeletionAck, type AtelierSequenceExportAck } from "@/lib/atelierSequenceApi";
import {
    beginAtelierExportRequest,
    createAtelierExportRequestId,
    listAtelierExportRequests,
    resolveAtelierExportRequest,
    type StoredAtelierExportRequest,
} from "@/lib/atelierExportRequest";
import {
    beginAtelierExportDeletionRequest,
    createAtelierExportDeletionRequestId,
    listAtelierExportDeletionRequests,
    resolveAtelierExportDeletionRequest,
    type StoredAtelierExportDeletionRequest,
} from "@/lib/atelierExportDeletionRequest";
import {
    beginAtelierUploadRequest,
    createAtelierUploadRequestId,
    listAtelierUploadRequests,
    readAtelierUploadRequest,
    resolveAtelierUploadRequest,
    type StoredAtelierUploadRequest,
} from "@/lib/atelierUploadRequest";
import type {
    AtelierAudioCandidateRequest, AtelierGenerationAdmissionAck, AtelierGenerationAttemptAck, AtelierImageCandidateRequest, AtelierOrderedVideoCandidateRequest, AtelierVideoCandidateRequest,
    AtelierExportRecord, AtelierMediaKind, AtelierSequenceEntry, AtelierSequenceExportJob, AtelierUploadAck, AtelierUploadReadback,
    AtelierNodeCommandAck, AtelierProject, AttachAtelierPendingReferenceInput, AttachAtelierReferenceInput, CreateAtelierNodeInput, DeleteAtelierNodesResult, DetachAtelierReferenceInput,
    AttachAtelierMediaUploadInput, CreateAtelierMediaUploadInput,
    MoveAtelierNodesInput, RestoreAtelierNodesResult,
    SetAtelierCandidateJudgmentInput,
    SetAtelierLegacyGenerationDraftInput, SetAtelierOrderedGenerationDraftInput, SetAtelierRegionMembersInput, UpdateAtelierNodeBodyInput, UpdateAtelierNodeLayoutInput,
    UpdateAtelierNodesLayoutInput, UpdateAtelierNodeTextInput, UpdateAtelierProjectInput,
    UpdateAtelierRegionAppearanceInput, WrapAtelierRegionInput,
} from "@/lib/atelierTypes";
import {
    isCurrentRuntimeEnvironment,
    requireActiveRuntimeEnvironment,
    subscribeRuntimeEnvironment,
} from "@/lib/runtimeEnvironment";

type MetadataPatch = Omit<UpdateAtelierProjectInput, "expectedRevision">;
type CommandInput<T> = Omit<T, "expectedRevision">;
type UploadCreateInput = Omit<CreateAtelierMediaUploadInput, "expectedRevision" | "clientRequestId">;
type UploadReferenceInput = Omit<AttachAtelierMediaUploadInput, "expectedRevision" | "clientRequestId">;
type GenerationInput = Omit<AtelierVideoCandidateRequest, "expected_revision" | "client_request_id">
    | Omit<AtelierOrderedVideoCandidateRequest, "expected_revision" | "client_request_id">
    | Omit<AtelierImageCandidateRequest, "expected_revision" | "client_request_id">
    | Omit<AtelierAudioCandidateRequest, "expected_revision" | "client_request_id">;
type GenerationDraftInput = Omit<SetAtelierLegacyGenerationDraftInput, "expected_revision">
    | Omit<SetAtelierOrderedGenerationDraftInput, "expected_revision">;
type MediaGenerationDraftInput = AtelierMediaDraftInput extends infer D ? D extends AtelierMediaDraftInput ? Omit<D, "expected_revision"> : never : never;
type CandidateJudgmentInput = Omit<SetAtelierCandidateJudgmentInput, "expected_revision">;
export type AtelierAgentTransportOptions = {
    transport?: "sync" | "stream";
    signal?: AbortSignal;
    onEvent?: (event: AtelierAgentStreamEvent | AtelierAgentLoopEvent | AtelierAgentStreamErrorEvent) => void;
};

interface AtelierStore {
    projects: AtelierProject[];
    currentProject: AtelierProject | null;
    selectedNodeId: string | null;
    isLoading: boolean;
    isSaving: boolean;
    needsRefresh: boolean;
    pendingUploads: StoredAtelierUploadRequest[];
    pendingExports: StoredAtelierExportRequest[];
    pendingExportDeletions: StoredAtelierExportDeletionRequest[];
    pendingGenerations: StoredAtelierGenerationRequest[];
    pendingAgentTurns: StoredAtelierAgentTurnRequest[];
    sequenceExportJobs: AtelierSequenceExportJob[];
    error: string | null;
    loadProjects: () => Promise<AtelierProject[]>;
    ensureProject: () => Promise<AtelierProject>;
    createProject: (title?: string) => Promise<AtelierProject>;
    switchProject: (projectId: string) => Promise<AtelierProject>;
    refreshProject: () => Promise<AtelierProject>;
    updateProject: (patch: MetadataPatch) => Promise<AtelierProject>;
    deleteProject: () => Promise<void>;
    createNode: (input: CommandInput<CreateAtelierNodeInput>) => Promise<AtelierNodeCommandAck>;
    updateNodeText: (nodeId: string, input: CommandInput<UpdateAtelierNodeTextInput>) => Promise<AtelierNodeCommandAck>;
    updateNodeBody: (nodeId: string, input: CommandInput<UpdateAtelierNodeBodyInput>) => Promise<AtelierNodeCommandAck>;
    updateNodeLayout: (nodeId: string, input: CommandInput<UpdateAtelierNodeLayoutInput>) => Promise<AtelierNodeCommandAck>;
    updateNodesLayout: (input: CommandInput<UpdateAtelierNodesLayoutInput>) => Promise<AtelierNodeCommandAck>;
    moveNodes: (input: CommandInput<MoveAtelierNodesInput>) => Promise<AtelierNodeCommandAck>;
    wrapRegion: (input: CommandInput<WrapAtelierRegionInput>) => Promise<AtelierNodeCommandAck>;
    setRegionMembers: (regionId: string, input: CommandInput<SetAtelierRegionMembersInput>) => Promise<AtelierNodeCommandAck>;
    updateRegionAppearance: (regionId: string, input: CommandInput<UpdateAtelierRegionAppearanceInput>) => Promise<AtelierNodeCommandAck>;
    deleteNodes: (nodeIds: string[]) => Promise<DeleteAtelierNodesResult>;
    restoreNodes: (receiptId: string) => Promise<RestoreAtelierNodesResult>;
    createMediaNode: (input: UploadCreateInput) => Promise<AtelierUploadAck>;
    uploadReference: (targetId: string, input: UploadReferenceInput) => Promise<AtelierUploadAck>;
    loadPendingUploads: () => StoredAtelierUploadRequest[];
    readbackPendingUpload: (clientRequestId?: string) => Promise<AtelierUploadReadback | null>;
    saveSequence: (entries: AtelierSequenceEntry[], expectedRevision?: number) => Promise<AtelierProject>;
    createSequenceExport: () => Promise<AtelierSequenceExportAck>;
    redoSequenceExport: (record: AtelierExportRecord) => Promise<AtelierSequenceExportAck>;
    deleteSequenceExport: (record: AtelierExportRecord, deleteFile?: boolean) => Promise<AtelierExportDeletionAck>;
    loadPendingExports: () => StoredAtelierExportRequest[];
    readbackPendingExport: (clientRequestId?: string) => Promise<AtelierSequenceExportAck | null>;
    loadPendingExportDeletions: () => StoredAtelierExportDeletionRequest[];
    readbackPendingExportDeletion: (clientRequestId?: string) => Promise<AtelierExportDeletionAck | null>;
    refreshSequenceExport: (jobId: string) => Promise<AtelierSequenceExportAck>;
    cancelSequenceExport: (jobId: string) => Promise<AtelierSequenceExportAck>;
    repairSequenceExport: (jobId: string) => Promise<AtelierSequenceExportAck>;
    createVideoCandidate: (nodeId: string, input: GenerationInput) => Promise<AtelierGenerationAdmissionAck>;
    createImageCandidate: (nodeId: string, input: Omit<AtelierImageCandidateRequest, "expected_revision" | "client_request_id">) => Promise<AtelierGenerationAdmissionAck>;
    createAudioCandidate: (nodeId: string, input: Omit<AtelierAudioCandidateRequest, "expected_revision" | "client_request_id">) => Promise<AtelierGenerationAdmissionAck>;
    saveGenerationDraft: (nodeId: string, input: GenerationDraftInput) => Promise<AtelierNodeCommandAck>;
    saveMediaGenerationDraft: (nodeId: string, input: MediaGenerationDraftInput) => Promise<AtelierNodeCommandAck>;
    selectMediaGenerationCandidate: (nodeId: string, candidateId: string) => Promise<AtelierNodeCommandAck>;
    setMediaGenerationCandidateVisibility: (nodeId: string, candidateId: string, visible: boolean) => Promise<AtelierNodeCommandAck>;
    loadPendingGenerations: () => StoredAtelierGenerationRequest[];
    readbackPendingGeneration: (clientRequestId?: string) => Promise<AtelierGenerationAdmissionAck | null>;
    refreshGenerationAttempt: (attemptId: string) => Promise<AtelierGenerationAttemptAck>;
    continueGenerationAttempt: (attemptId: string) => Promise<AtelierGenerationAttemptAck>;
    cancelGenerationAttempt: (attemptId: string) => Promise<AtelierGenerationAttemptAck>;
    selectGenerationCandidate: (nodeId: string, candidateId: string) => Promise<AtelierNodeCommandAck>;
    setGenerationCandidateVisibility: (nodeId: string, candidateId: string, visible: boolean) => Promise<AtelierNodeCommandAck>;
    setCandidateJudgment: (nodeId: string, candidateId: string, input: CandidateJudgmentInput) => Promise<AtelierNodeCommandAck>;
    branchFromCandidate: (nodeId: string, candidateId: string) => Promise<AtelierNodeCommandAck>;
    startAgentTurn: (input: { userMessage: string; selectedNodeId: string | null }, options?: AtelierAgentTransportOptions) => Promise<AtelierAgentTurnAck>;
    startAgentLoop: (input: { userMessage: string; selectedNodeId: string | null; planner?: AtelierAgentLoopPlanner; maxIterations?: number; skillName?: string | null }, options?: AtelierAgentTransportOptions) => Promise<AtelierAgentLoopAck>;
    loadPendingAgentTurns: () => StoredAtelierAgentTurnRequest[];
    readbackPendingAgentTurn: (clientTurnId?: string) => Promise<AtelierAgentTurnAck | AtelierAgentLoopAck | null>;
    retryPendingAgentTurn: (clientTurnId?: string) => Promise<AtelierAgentTurnAck | AtelierAgentLoopAck | null>;
    refreshAgentTurn: (turnId: string) => Promise<AtelierAgentTurnAck>;
    refreshAgentLoop: (turnId: string) => Promise<AtelierAgentLoopAck>;
    approveAgentCall: (turnId: string, callId: string) => Promise<AtelierAgentTurnAck>;
    denyAgentCall: (turnId: string, callId: string) => Promise<AtelierAgentTurnAck>;
    revokeAgentCall: (turnId: string, callId: string) => Promise<AtelierAgentTurnAck>;
    continueAgentCall: (turnId: string, callId: string, options?: AtelierAgentTransportOptions) => Promise<AtelierAgentTurnAck>;
    cancelAgentTurn: (turnId: string) => Promise<AtelierAgentTurnAck>;
    recordAgentLoopCanvasApproval: (turnId: string, callId: string, decision: "approved" | "denied" | "revoked") => Promise<AtelierAgentLoopAck>;
    resumeAgentLoop: (turnId: string, options?: AtelierAgentTransportOptions) => Promise<AtelierAgentLoopAck>;
    resumeSequenceExport: (jobId: string) => Promise<AtelierSequenceExportAck>;
    attachPendingReferenceNode: (targetId: string, input: CommandInput<AttachAtelierPendingReferenceInput>) => Promise<AtelierNodeCommandAck>;
    attachReferenceNode: (targetId: string, input: CommandInput<AttachAtelierReferenceInput>) => Promise<AtelierNodeCommandAck>;
    detachReferenceNode: (targetId: string, input: CommandInput<DetachAtelierReferenceInput>) => Promise<AtelierNodeCommandAck>;
    selectNode: (nodeId: string | null) => void;
}

const emptyState = () => ({
    projects: [], currentProject: null, selectedNodeId: null,
    isLoading: false, isSaving: false, needsRefresh: false, pendingUploads: [], pendingExports: [], pendingExportDeletions: [], pendingGenerations: [], pendingAgentTurns: [], sequenceExportJobs: [], error: null,
});
let navigation = 0;
let serial = 0;
let mutation = 0;
let loadingToken = 0;
let listRequest: Promise<AtelierProject[]> | null = null;
let listNavigation = 0;
let ensureRequest: Promise<AtelierProject> | null = null;
let createRequest: Promise<AtelierProject> | null = null;
let navigationRequest: Promise<AtelierProject> | null = null;
const readTokens = new Map<string, number>();
const generationCreates = new Map<string, { input: string; promise: Promise<AtelierGenerationAdmissionAck> }>();
const generationActions = new Set<string>();
const agentStarts = new Map<string, { input: string; promise: Promise<AtelierAgentTurnAck> }>();
const agentLoopStarts = new Map<string, { input: string; promise: Promise<AtelierAgentLoopAck> }>();
const agentActions = new Set<string>();
const writes = new Map<string, number>();
const writeQueues = new Map<string, Promise<unknown>>();
type ProjectWriteFailure = { serial: number; revision: number; message: string };
const failures = new Map<string, ProjectWriteFailure>();
const exportJobs = new Map<string, Map<string, AtelierSequenceExportJob>>();
const exportCreates = new Map<string, number>();
const exportDeletes = new Map<string, number>();
const messageOf = (error: unknown) => error instanceof Error ? error.message : "Atelier request failed";
const changed = () => new Error("Atelier environment or project changed during request");
const capture = () => ({ environment: requireActiveRuntimeEnvironment(), navigation });
const currentEnvironment = (owner: ReturnType<typeof capture>) =>
    isCurrentRuntimeEnvironment(owner.environment.key, owner.environment.generation);
const currentOwner = (owner: ReturnType<typeof capture>) => currentEnvironment(owner) && owner.navigation === navigation;
const uploadPending = () => new Error("An Atelier upload is already pending readback");
const exportPending = () => new Error("An Atelier export is already pending readback");
const isDefiniteUploadRejection = (error: unknown): boolean => {
    if (!(error instanceof Error) || !("status" in error)) return false;
    const status = Number((error as { status?: number }).status);
    if ([401, 403, 413, 422].includes(status)) return true;
    return status === 409 && "code" in error && (error as { code?: string }).code === ATELIER_UPLOAD_TARGET_ACTIVE_CODE;
};

const isDefiniteAgentStartRejection = (error: unknown): boolean => {
    if (!(error instanceof Error) || !("code" in error) || (error as { code?: string }).code !== "atelier_agent_start_rejected") return false;
    return !("status" in error) || (error as { status?: number }).status === undefined || Number((error as { status?: number }).status) === 422;
};

const snapshotUploadInput = <T extends UploadCreateInput | UploadReferenceInput>(input: T): T => ({ ...input });
const projectPendingUploads = (environment: ReturnType<typeof requireActiveRuntimeEnvironment>, projectId: string): StoredAtelierUploadRequest[] =>
    listAtelierUploadRequests({ environment, projectId });
const projectPendingExports = (environment: ReturnType<typeof requireActiveRuntimeEnvironment>, projectId: string): StoredAtelierExportRequest[] =>
    listAtelierExportRequests({ environment, projectId });
const projectPendingExportDeletions = (environment: ReturnType<typeof requireActiveRuntimeEnvironment>, projectId: string): StoredAtelierExportDeletionRequest[] =>
    listAtelierExportDeletionRequests({ environment, projectId });
const projectPendingAgentTurns = (environment: ReturnType<typeof requireActiveRuntimeEnvironment>, projectId: string): StoredAtelierAgentTurnRequest[] =>
    listProjectAtelierAgentTurnRequests({ environment, projectId });
const exportCreateKey = (owner: ReturnType<typeof capture>, projectId: string): string => `${owner.environment.key}:${projectId}`;
const projectExportJobs = (projectId: string): AtelierSequenceExportJob[] => {
    const values = exportJobs.get(projectId);
    return (values ? Array.from(values.values()) : []).sort((left, right) => left.job_id.localeCompare(right.job_id));
};

const canonicalJson = (value: unknown): string => JSON.stringify(sortJson(value));
const sortJson = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(sortJson);
    if (value && typeof value === "object") {
        const output: Record<string, unknown> = {};
        for (const key of Object.keys(value as Record<string, unknown>).sort()) {
            output[key] = sortJson((value as Record<string, unknown>)[key]);
        }
        return output;
    }
    return value;
};
const projectSequenceExportJobs = (project: AtelierProject): AtelierSequenceExportJob[] => {
    const maybe = (project as unknown as { sequence_export_jobs?: unknown }).sequence_export_jobs;
    return Array.isArray(maybe) ? maybe as AtelierSequenceExportJob[] : [];
};

function mergeExportJob(job: AtelierSequenceExportJob): AtelierSequenceExportJob {
    const projectJobs = exportJobs.get(job.project_id) ?? new Map<string, AtelierSequenceExportJob>();
    const existing = projectJobs.get(job.job_id);
    if (existing) {
        if (existing.job_revision > job.job_revision) return existing;
        if (existing.job_revision === job.job_revision) {
            if (canonicalJson(existing) !== canonicalJson(job)) {
                throw new Error("Atelier export job has different content at the same revision");
            }
            return existing;
        }
    }
    projectJobs.set(job.job_id, Object.freeze(structuredClone(job)));
    exportJobs.set(job.project_id, projectJobs);
    return projectJobs.get(job.job_id)!;
}

function mergeProjectExportJobs(project: AtelierProject): void {
    for (const job of projectSequenceExportJobs(project)) mergeExportJob(job);
}

function verifyExportAck(ack: AtelierSequenceExportAck): void {
    if (ack.job.project_id !== ack.project.id) throw new Error("Atelier export job project mismatch");
    if (ack.export_record) {
        const exportId = ack.export_record.id;
        if (ack.job.status !== "completed" || ack.job.export_id !== exportId
            || !ack.project.exports.some(item => item.id === exportId)) {
            throw new Error("Atelier export completion is not atomically bound to its export record");
        }
    }
}

function verifyExportDeletionAck(stored: StoredAtelierExportDeletionRequest, ack: AtelierExportDeletionAck): void {
    if (ack.project.id !== stored.projectId || ack.export_record.id !== stored.exportId || ack.export_record.project_id !== stored.projectId
        || ack.export_record.deletion?.client_request_id !== stored.clientRequestId
        || ack.export_record.deletion.delete_file !== stored.request.delete_file) {
        throw new Error("Atelier export deletion acknowledgement does not match the pending request");
    }
}

function findDeletionAck(project: AtelierProject, stored: StoredAtelierExportDeletionRequest): AtelierExportDeletionAck | null {
    const record = project.exports.find(item => item.id === stored.exportId) ?? null;
    if (!record?.deletion) return null;
    const ack = { project, export_record: record, replayed: true };
    verifyExportDeletionAck(stored, ack);
    return ack;
}

function hasBlockingExportJob(projectId: string): boolean {
    return projectExportJobs(projectId).some(job => ["queued", "running", "cancelling", "repair_required"].includes(job.status));
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export const useAtelierStore = create<AtelierStore>((set, get) => {
    const merge = (incoming: AtelierProject, token: number): AtelierProject => {
        const existing = get().projects.find(p => p.id === incoming.id);
        if (existing && (existing.revision > incoming.revision
            || (existing.revision === incoming.revision && (readTokens.get(incoming.id) ?? 0) > token))) {
            return mergeAtelierGenerationState(existing, incoming);
        }
        readTokens.set(incoming.id, token);
        return existing ? mergeAtelierGenerationState(incoming, existing) : incoming;
    };
    const cacheProject = (incoming: AtelierProject, token: number): AtelierProject => {
        mergeProjectExportJobs(incoming);
        const project = merge(incoming, token);
        set(state => ({
            projects: state.projects.some(p => p.id === project.id)
                ? state.projects.map(p => p.id === project.id ? project : p) : [...state.projects, project],
        }));
        return project;
    };
    const adopt = (incoming: AtelierProject, token: number, select = false) => {
        mergeProjectExportJobs(incoming);
        const project = merge(incoming, token);
        set(state => {
            const projects = state.projects.some(p => p.id === project.id)
                ? state.projects.map(p => p.id === project.id ? project : p) : [...state.projects, project];
            if (!select && state.currentProject?.id !== project.id) return { projects };
            const selectedNodeId = project.nodes.some(n => n.id === state.selectedNodeId) ? state.selectedNodeId : null;
            return { projects, currentProject: project, selectedNodeId,
                pendingUploads: projectPendingUploads(requireActiveRuntimeEnvironment(), project.id),
                pendingExports: projectPendingExports(requireActiveRuntimeEnvironment(), project.id),
                pendingExportDeletions: projectPendingExportDeletions(requireActiveRuntimeEnvironment(), project.id),
                sequenceExportJobs: projectExportJobs(project.id),
                needsRefresh: failures.has(project.id), isSaving: writes.has(project.id) };
        });
        return project;
    };
    const startLoading = () => { const token = ++serial; loadingToken = token; set({ isLoading: true, error: failures.get(get().currentProject?.id ?? "")?.message ?? null }); return token; };
    const finishLoading = (token: number) => { if (loadingToken === token) set({ isLoading: false }); };

    const clearReadbackFailure = (project: AtelierProject, failure: ProjectWriteFailure | undefined) => {
        if (failure && failures.get(project.id) === failure && !writes.has(project.id)
            && project.revision >= failure.revision) {
            failures.delete(project.id);
            set({ error: null });
        }
    };

    const readProject = async (projectId: string, owner: ReturnType<typeof capture>, token: number, select: boolean) => {
        const failure = failures.get(projectId);
        try {
            const project = await atelierProjectApi.getProject(projectId);
            if (!currentOwner(owner)) throw changed();
            clearReadbackFailure(project, failure);
            const accepted = adopt(project, token, select);
            if (!failures.has(projectId)) set({ error: null });
            return accepted;
        } catch (error) {
            if (currentOwner(owner)) set({ error: messageOf(error) });
            throw error;
        } finally {
            if (currentOwner(owner)) finishLoading(token);
        }
    };

    const writeProject = async <T>(send: (project: AtelierProject) => Promise<T>, apply: (result: T, token: number) => void, options: { noRefreshOnError?: (error: unknown) => boolean } = {}): Promise<T> => {
        const owner = capture();
        const projectId = get().currentProject?.id;
        if (!projectId) throw new Error("No Atelier project is selected");
        const run = async () => {
            const project = get().currentProject;
            if (!currentOwner(owner) || project?.id !== projectId) throw changed();
            if (failures.has(projectId)) throw new Error("Refresh the Atelier project before saving again");
            const token = ++serial;
            writes.set(projectId, token);
            mutation += 1;
            set({ isSaving: true, error: null });
            try {
                const result = await send(project);
                if (!currentEnvironment(owner)) throw changed();
                mutation += 1;
                apply(result, token);
                return result;
            } catch (error) {
                if (currentEnvironment(owner)) {
                    mutation += 1;
                    if (options.noRefreshOnError?.(error)) {
                        if (get().currentProject?.id === projectId) set({ error: messageOf(error), needsRefresh: false });
                    } else {
                        failures.set(projectId, { serial: ++serial, revision: project.revision, message: messageOf(error) });
                        if (get().currentProject?.id === projectId) set({ error: messageOf(error), needsRefresh: true });
                    }
                }
                throw error;
            } finally {
                if (currentEnvironment(owner) && writes.get(projectId) === token) {
                    writes.delete(projectId);
                    if (get().currentProject?.id === projectId) set({ isSaving: false });
                }
            }
        };
        const previous = writeQueues.get(projectId);
        // A rejected predecessor rejects every already-queued intent; a refresh must not replay it.
        const request = previous ? previous.then(run) : run();
        writeQueues.set(projectId, request);
        const release = () => { if (writeQueues.get(projectId) === request) writeQueues.delete(projectId); };
        void request.then(release, release);
        return request;
    };

    const writeNode = <T>(input: T, send: (project: AtelierProject, snapshot: T) => Promise<AtelierNodeCommandAck>) => {
        const snapshot = structuredClone(input);
        return writeProject(project => send(project, snapshot), (ack, token) => { adopt(ack.project, token); });
    };


    const publishAgentPending = (owner: ReturnType<typeof capture>, projectId: string) => {
        if (currentOwner(owner) && get().currentProject?.id === projectId) {
            set({ pendingAgentTurns: projectPendingAgentTurns(owner.environment, projectId) });
        }
    };

    const findAgentTurn = (project: AtelierProject, turnId: string): Record<string, unknown> => {
        const turn = project.agent_turns.find(item => isRecord(item) && item.id === turnId);
        if (!isRecord(turn)) throw new Error("Atelier Agent turn not found");
        return turn;
    };

    const findAgentCall = (project: AtelierProject, turnId: string, callId: string): Record<string, unknown> => {
        const turn = findAgentTurn(project, turnId);
        const calls = Array.isArray(turn.tool_calls) ? turn.tool_calls : [];
        const call = calls.find(item => isRecord(item) && item.call_id === callId);
        if (!isRecord(call)) throw new Error("Atelier Agent call not found");
        return call;
    };

    const authorizationFromCall = (call: Record<string, unknown>): { approvalId: string; authorizationHash: string } => {
        const binding = call.preview_binding;
        if (!isRecord(binding) || !isRecord(binding.agent_authorization_ref)) throw new Error("Atelier Agent approval is unavailable");
        const ref = binding.agent_authorization_ref;
        if (typeof ref.approval_id !== "string" || typeof ref.authorization_hash !== "string") throw new Error("Atelier Agent approval is unavailable");
        return { approvalId: ref.approval_id, authorizationHash: ref.authorization_hash };
    };

    const canvasAuthorizationFromCall = (call: Record<string, unknown>): { approvalId: string; authorizationHash: string } => {
        const binding = call.canvas_approval_binding;
        if (!isRecord(binding) || !isRecord(binding.canvas_authorization_ref)) throw new Error("Atelier Agent canvas approval is unavailable");
        const ref = binding.canvas_authorization_ref;
        if (typeof ref.approval_id !== "string" || typeof ref.authorization_hash !== "string") throw new Error("Atelier Agent canvas approval is unavailable");
        return { approvalId: ref.approval_id, authorizationHash: ref.authorization_hash };
    };

    const clientRequestIdFromCall = (call: Record<string, unknown>): string => {
        const binding = call.preview_binding;
        const publicRecord = isRecord(binding) ? binding.preview_public_record : null;
        const rawRequest = isRecord(publicRecord) ? publicRecord.raw_request : null;
        if (isRecord(rawRequest) && typeof rawRequest.client_request_id === "string") return rawRequest.client_request_id;
        throw new Error("Atelier Agent generation request identity is unavailable");
    };

    const verifyAgentStartAck = (stored: StoredAtelierAgentTurnRequest, ack: AtelierAgentTurnAck): void => {
        const binding = ack.call?.preview_binding;
        const preview = isRecord(binding) ? binding.preview_public_record : null;
        const arguments_ = ack.call?.arguments;
        // The current start planner proposes exactly one generation for the selected node.
        // Resolve only its frozen target; a displayed result snapshot is not request identity.
        const targetMatches = stored.request.selected_node_id === null || (
            isRecord(preview) && preview.node_id === stored.request.selected_node_id
            && isRecord(arguments_) && arguments_.node_id === stored.request.selected_node_id
        );
        if (ack.project.id !== stored.projectId || ack.turn.id !== stored.clientTurnId || ack.turn.project_id !== stored.projectId
            || ack.turn.user_message !== stored.request.user_message || !targetMatches) {
            throw new Error("Atelier Agent start acknowledgement does not match the pending request");
        }
    };

    const resolveAgentStart = (owner: ReturnType<typeof capture>, stored: StoredAtelierAgentTurnRequest, ack: AtelierAgentTurnAck): void => {
        verifyAgentStartAck(stored, ack);
        resolveAtelierAgentTurnRequest({ environment: owner.environment, projectId: stored.projectId, clientTurnId: stored.clientTurnId, request: stored.request });
        publishAgentPending(owner, stored.projectId);
    };

    const verifyAgentLoopStartAck = (stored: StoredAtelierAgentLoopRequest, ack: AtelierAgentLoopAck): void => {
        const context = ack.turn.loop_start_context;
        if (!isRecord(context)) throw new Error("Atelier Agent loop acknowledgement does not match the pending request");
        const requestSkill = stored.request.skill_name ?? null;
        const contextMatches = typeof requestSkill === "string"
            ? isRecord(context.skill_snapshot)
                && context.schema_version === 2
                && context.user_message === stored.request.user_message
                && context.selected_node_id === stored.request.selected_node_id
                && context.planner === "model_adapter"
                && context.skill_name === requestSkill
                && context.max_iterations === (stored.request.max_iterations ?? 3)
                && context.skill_snapshot.name === requestSkill
                && typeof context.skill_snapshot.version === "string"
                && /^\d+\.\d+\.\d+$/.test(context.skill_snapshot.version)
                && typeof context.skill_snapshot.content_hash === "string"
                && /^[0-9a-f]{64}$/.test(context.skill_snapshot.content_hash)
            : canonicalJson(context) === canonicalJson({
                schema_version: 1, user_message: stored.request.user_message, selected_node_id: stored.request.selected_node_id,
                planner: stored.request.planner ?? "deterministic_core", skill_name: null, max_iterations: stored.request.max_iterations ?? 3,
            });
        if (ack.project.id !== stored.projectId || ack.turn.id !== stored.clientTurnId || ack.turn.project_id !== stored.projectId
            || ack.turn.skill_name !== requestSkill || !contextMatches) {
            throw new Error("Atelier Agent loop acknowledgement does not match the pending request");
        }
    };

    const resolveAgentLoopStart = (owner: ReturnType<typeof capture>, stored: StoredAtelierAgentLoopRequest, ack: AtelierAgentLoopAck): void => {
        verifyAgentLoopStartAck(stored, ack);
        resolveAtelierAgentLoopRequest({ environment: owner.environment, projectId: stored.projectId, clientTurnId: stored.clientTurnId, request: stored.request });
        publishAgentPending(owner, stored.projectId);
    };

    const runAgentApprovalAction = (turnId: string, callId: string, decision: "approved" | "denied" | "revoked"): Promise<AtelierAgentTurnAck> => {
        const owner = capture();
        const key = JSON.stringify([owner.environment.key, owner.environment.generation, get().currentProject?.id ?? "", turnId, callId, decision]);
        if (agentActions.has(key)) return Promise.reject(new Error("Atelier Agent operation is already pending"));
        agentActions.add(key);
        return writeProject(project => {
            const call = findAgentCall(project, turnId, callId);
            const auth = authorizationFromCall(call);
            return atelierAgentApi.recordApproval(project.id, turnId, {
                expected_revision: project.revision,
                turn_id: turnId,
                call_id: callId,
                approval_id: auth.approvalId,
                authorization_hash: auth.authorizationHash,
                decision,
            });
        }, (ack, token) => {
            adopt(ack.project, token);
        }).finally(() => { agentActions.delete(key); });
    };

    const generationPending = (owner: ReturnType<typeof capture>, projectId: string) =>
        listProjectAtelierGenerationRequests({ environment: owner.environment, projectId });
    const publishGenerationPending = (owner: ReturnType<typeof capture>, projectId: string) => {
        if (currentOwner(owner) && get().currentProject?.id === projectId) {
            set({ pendingGenerations: generationPending(owner, projectId) });
        }
    };

    const orderedProviderMediaMatches = (providerMedia: unknown, inputMedia: Array<{ role: string; ordinal: number }>): boolean => Array.isArray(providerMedia)
        && providerMedia.length === inputMedia.length
        && inputMedia.every((item, index) => {
            const provider = providerMedia[index];
            return isRecord(provider)
                && provider.schema_version === 1
                && provider.role === item.role
                && provider.ordinal === item.ordinal
                && typeof provider.declared_media_type === "string"
                && provider.declared_media_type.length > 0
                && typeof provider.sha256 === "string"
                && /^[0-9a-f]{64}$/.test(provider.sha256)
                && typeof provider.size_bytes === "number"
                && Number.isSafeInteger(provider.size_bytes)
                && provider.size_bytes > 0;
        });
    const generationRequestBatchSize = (request: StoredAtelierGenerationRequest["request"]): number | undefined => (
        "batch_size" in request ? request.batch_size : undefined
    );

    const verifyGenerationReceipt = (stored: StoredAtelierGenerationRequest, ack: AtelierGenerationAdmissionAck) => {
        const ordered = "contract_version" in stored.request;
        const mediaEntry = "entrypoint" in stored.request ? stored.request.entrypoint : null;
        const expectedEntrypoint = mediaEntry ?? (ordered ? "atelier.provider_video.v2" : "atelier.provider_video.v1");
        const projectReceiptMatches = ack.project.generation_request_receipts
            .filter(item => item.client_request_id === stored.clientRequestId);
        if (ack.project.id !== stored.projectId || ack.receipt.project_id !== stored.projectId
            || ack.receipt.entrypoint !== expectedEntrypoint
            || projectReceiptMatches.length !== 1
            || ack.receipt.client_request_id !== stored.clientRequestId || ack.receipt.node_id !== stored.nodeId
            || ack.receipt.base_revision !== stored.request.expected_revision
            || (generationRequestBatchSize(stored.request) !== undefined && generationRequestBatchSize(stored.request) !== ack.members.length)
            || (generationRequestBatchSize(stored.request) === undefined && ack.members.length !== 1)) {
            throw new Error("Atelier generation receipt does not match the saved request");
        }
        assertAtelierGenerationAdmissionMatchesRequest(ack, stored.request, { projectId: stored.projectId, nodeId: stored.nodeId });
        for (const member of ack.members) {
            if (member.attempt.node_id !== stored.nodeId) {
                throw new Error("Atelier generation receipt does not match the saved request");
            }
            if (mediaEntry) {
                if (!("execution_contract" in member.attempt) || member.attempt.execution_contract !== "atelier-provider-media-v2"
                    || (mediaEntry === "atelier.provider_image.v1" ? member.attempt.media_kind !== "image" : member.attempt.media_kind !== "audio")) {
                    throw new Error("Atelier generation receipt does not match the saved request");
                }
            } else if (ordered) {
                const request = stored.request;
                if (!("contract_version" in request) || !("input_contract_version" in member.attempt)
                    || member.attempt.input_contract_version !== request.contract_version
                    || canonicalJson(member.attempt.input_media) !== canonicalJson(request.media)
                    || !orderedProviderMediaMatches(member.attempt.provider_execution.media, request.media)) {
                    throw new Error("Atelier generation receipt does not match the saved request");
                }
            } else if (!("first_frame" in stored.request) || !("first_frame_source" in member.attempt)
                || canonicalJson(member.attempt.first_frame_source) !== canonicalJson(stored.request.first_frame)) {
                throw new Error("Atelier generation receipt does not match the saved request");
            }
        }
    };
    const resolveGenerationReceipt = (owner: ReturnType<typeof capture>, stored: StoredAtelierGenerationRequest) => {
        resolveAtelierGenerationRequest({ environment: owner.environment, projectId: stored.projectId,
            nodeId: stored.nodeId, clientRequestId: stored.clientRequestId, request: stored.request });
        publishGenerationPending(owner, stored.projectId);
    };
    const runGenerationAction = async (attemptId: string, action: "read" | "continue" | "cancel"): Promise<AtelierGenerationAttemptAck> => {
        const owner = capture();
        const project = get().currentProject;
        const attempt = project?.generation_attempts.find(item => item.attempt_id === attemptId);
        if (!project || !attempt) throw new Error("Atelier generation attempt not found");
        const key = JSON.stringify([owner.environment.key, owner.environment.generation, project.id, attemptId]);
        if (action !== "read" && generationActions.has(key)) throw new Error("Atelier generation operation is already pending");
        if (action !== "read") generationActions.add(key);
        const token = ++serial;
        try {
            const identity = { candidate_id: attempt.candidate_id, expected_attempt_revision: attempt.attempt_revision,
                execution_fingerprint: attempt.provider_execution.fingerprint };
            const ack = action === "read" ? await atelierGenerationApi.getAttempt(project.id, attemptId)
                : action === "continue" ? await atelierGenerationApi.continueAttempt(project.id, attemptId, identity)
                : await atelierGenerationApi.cancelAttempt(project.id, attemptId, identity);
            if (!currentOwner(owner)) throw changed();
            adopt(ack.project, token);
            return ack;
        } finally {
            if (action !== "read") generationActions.delete(key);
        }
    };

    const verifyReadback = (projectId: string, stored: ReturnType<typeof readAtelierUploadRequest>, readback: AtelierUploadReadback) => {
        if (!stored || readback.receipt.project_id !== projectId || readback.receipt.client_request_id !== stored.clientRequestId
            || readback.receipt.operation !== stored.operation || readback.receipt.target_node_id !== stored.targetNodeId
            || readback.receipt.media.media_kind !== stored.mediaKind || readback.receipt.media.size_bytes !== stored.fileSize
            || readback.receipt.base_revision !== stored.baseRevision) {
            throw new Error("Atelier upload readback does not match the pending request");
        }
    };

    const writeUpload = <T extends UploadCreateInput | UploadReferenceInput>(
        input: T,
        operation: "create_media_node" | "upload_reference_attach",
        targetNodeId: string | null,
        send: (projectId: string, request: T & { expectedRevision: number; clientRequestId: string }) => Promise<AtelierUploadAck>,
        explicitKind?: AtelierMediaKind,
    ) => {
        const snapshot = snapshotUploadInput(input);
        if (operation === "upload_reference_attach" && (typeof targetNodeId !== "string" || !targetNodeId.trim())) {
            return Promise.reject(new Error("Invalid Atelier upload target"));
        }
        const initialProject = get().currentProject;
        if (!initialProject) return Promise.reject(new Error("No Atelier project is selected"));
        const initialOwner = capture();
        let clientRequestId: string;
        let kind: AtelierMediaKind;
        try {
            if (listAtelierUploadRequests({ environment: initialOwner.environment, projectId: initialProject.id }).length > 0) {
                set({ pendingUploads: projectPendingUploads(initialOwner.environment, initialProject.id) });
                return Promise.reject(uploadPending());
            }
            clientRequestId = createAtelierUploadRequestId();
            kind = validateAtelierUploadInput({ ...snapshot, expectedRevision: initialProject.revision, clientRequestId }, explicitKind);
        } catch (error) {
            return Promise.reject(error);
        }
        return writeProject(project => {
            if (listAtelierUploadRequests({ environment: initialOwner.environment, projectId: project.id }).length > 0) throw uploadPending();
            const expectedRevision = project.revision;
            const request = { ...snapshot, expectedRevision, clientRequestId };
            validateAtelierUploadInput(request, explicitKind);
            const stored = beginAtelierUploadRequest({
                environment: initialOwner.environment,
                projectId: project.id,
                operation,
                targetNodeId,
                mediaKind: kind,
                fileName: snapshot.file.name,
                fileSize: snapshot.file.size,
                baseRevision: expectedRevision,
                title: snapshot.title,
                x: snapshot.x,
                y: snapshot.y,
                width: snapshot.width,
                height: snapshot.height,
                idFactory: () => clientRequestId,
            });
            if (stored.status !== "created") throw uploadPending();
            if (currentOwner(initialOwner) && get().currentProject?.id === project.id) {
                set({ pendingUploads: projectPendingUploads(initialOwner.environment, project.id) });
            }
            return send(project.id, request).catch(error => {
                if (currentEnvironment(initialOwner) && isDefiniteUploadRejection(error)) {
                    resolveAtelierUploadRequest({ environment: initialOwner.environment, projectId: project.id, clientRequestId });
                    if (currentOwner(initialOwner) && get().currentProject?.id === project.id) {
                        set({ pendingUploads: projectPendingUploads(initialOwner.environment, project.id) });
                    }
                } else if (currentOwner(initialOwner) && get().currentProject?.id === project.id) {
                    set({ pendingUploads: projectPendingUploads(initialOwner.environment, project.id) });
                }
                throw error;
            });
        }, (ack, token) => {
            const receipt = ack.project.node_upload_receipts.find(item => item.receipt_id === ack.receipt_id);
            const stored = listAtelierUploadRequests({ environment: initialOwner.environment, projectId: ack.project.id })
                .find(item => item.clientRequestId === clientRequestId);
            if (stored && receipt?.client_request_id === clientRequestId) {
                resolveAtelierUploadRequest({ environment: initialOwner.environment, projectId: ack.project.id, clientRequestId });
            }
            adopt(ack.project, token);
            if (currentOwner(initialOwner) && get().currentProject?.id === ack.project.id) {
                set({ pendingUploads: projectPendingUploads(initialOwner.environment, ack.project.id) });
            }
        });
    };

    const clearMatchingExportRequest = (projectId: string, environment: ReturnType<typeof requireActiveRuntimeEnvironment>, stored: StoredAtelierExportRequest | null, ack: AtelierSequenceExportAck): void => {
        if (!stored || ack.job.client_request_id !== stored.clientRequestId || ack.job.base_revision !== stored.request.expected_revision) return;
        resolveAtelierExportRequest({
            environment,
            projectId,
            clientRequestId: stored.clientRequestId,
            request: stored.request,
            operation: stored.operation,
            sourceExportId: stored.sourceExportId,
        });
    };

    const applyExportAck = (ack: AtelierSequenceExportAck, token: number, owner: ReturnType<typeof capture>): AtelierSequenceExportAck => {
        verifyExportAck(ack);
        mergeExportJob(ack.job);
        if (currentOwner(owner)) {
            adopt(ack.project, token);
            set({ sequenceExportJobs: projectExportJobs(ack.project.id), pendingExports: projectPendingExports(owner.environment, ack.project.id), error: null });
        } else if (currentEnvironment(owner)) {
            cacheProject(ack.project, token);
        }
        return ack;
    };

    const runExportJobAction = async (
        jobId: string,
        send: (api: typeof atelierSequenceApi, projectId: string, jobId: string) => Promise<AtelierSequenceExportAck>,
    ): Promise<AtelierSequenceExportAck> => {
        const project = get().currentProject;
        if (!project) throw new Error("No Atelier project is selected");
        const owner = capture();
        const token = ++serial;
        try {
            const ack = await send(atelierSequenceApi, project.id, jobId);
            if (!currentEnvironment(owner)) throw changed();
            return applyExportAck(ack, token, owner);
        } catch (error) {
            if (currentOwner(owner)) set({ error: messageOf(error) });
            throw error;
        }
    };


    const runGenerationCreate = (nodeId: string, input: GenerationInput): Promise<AtelierGenerationAdmissionAck> => {
        const owner = capture();
        const selected = get().currentProject;
        if (!selected) return Promise.reject(new Error("No Atelier project is selected"));
        const key = JSON.stringify([owner.environment.key, owner.environment.generation, selected.id, nodeId]);
        const inputKey = canonicalJson(input);
        const inFlight = generationCreates.get(key);
        if (inFlight) return inFlight.input === inputKey ? inFlight.promise
            : Promise.reject(new Error("A different Atelier generation request is already pending"));
        const snapshot = structuredClone(input);
        let stored: StoredAtelierGenerationRequest | null = null;
        const request = writeProject(project => {
            if (generationPending(owner, project.id).length) throw new Error("Read back the pending Atelier generation request before creating another");
            const saved = "entrypoint" in snapshot && snapshot.entrypoint === "atelier.provider_image.v1"
                ? beginAtelierGenerationRequest({ environment: owner.environment, projectId: project.id, nodeId, entrypoint: snapshot.entrypoint,
                    expectedRevision: project.revision, modelId: snapshot.model_id, mode: snapshot.mode, prompt: snapshot.prompt, media: snapshot.media,
                    ...(Object.prototype.hasOwnProperty.call(snapshot, "params") ? { params: snapshot.params } : {}),
                    ...(Object.prototype.hasOwnProperty.call(snapshot, "batch_size") ? { batchSize: snapshot.batch_size } : {}) })
                : "entrypoint" in snapshot && snapshot.entrypoint === "atelier.provider_audio.v1"
                    ? beginAtelierGenerationRequest({ environment: owner.environment, projectId: project.id, nodeId, entrypoint: snapshot.entrypoint,
                        expectedRevision: project.revision, voice: snapshot.voice, text: snapshot.text,
                        ...(Object.prototype.hasOwnProperty.call(snapshot, "params") ? { params: snapshot.params } : {}) })
                    : "contract_version" in snapshot
                        ? beginAtelierGenerationRequest({ environment: owner.environment, projectId: project.id, nodeId,
                            expectedRevision: project.revision, contractVersion: snapshot.contract_version, modelId: snapshot.model_id,
                            mode: snapshot.mode, prompt: snapshot.prompt, media: snapshot.media,
                            ...(Object.prototype.hasOwnProperty.call(snapshot, "params") ? { params: snapshot.params } : {}),
                            ...(Object.prototype.hasOwnProperty.call(snapshot, "batch_size") ? { batchSize: snapshot.batch_size } : {}) })
                        : beginAtelierGenerationRequest({ environment: owner.environment, projectId: project.id,
                            nodeId, expectedRevision: project.revision, modelId: snapshot.model_id, mode: snapshot.mode,
                            prompt: snapshot.prompt, firstFrame: snapshot.first_frame,
                            ...(Object.prototype.hasOwnProperty.call(snapshot, "params") ? { params: snapshot.params } : {}),
                            ...(Object.prototype.hasOwnProperty.call(snapshot, "batch_size") ? { batchSize: snapshot.batch_size } : {}) });
            stored = saved.request;
            publishGenerationPending(owner, project.id);
            const send = "entrypoint" in stored.request && stored.request.entrypoint === "atelier.provider_image.v1"
                ? atelierGenerationApi.createImageCandidate(project.id, nodeId, stored.request)
                : "entrypoint" in stored.request && stored.request.entrypoint === "atelier.provider_audio.v1"
                    ? atelierGenerationApi.createAudioCandidate(project.id, nodeId, stored.request)
                    : atelierGenerationApi.createVideoCandidate(project.id, nodeId, stored.request);
            return send.catch(error => {
                if (currentEnvironment(owner) && stored && isDefiniteUploadRejection(error)) resolveGenerationReceipt(owner, stored);
                throw error;
            });
        }, (ack, token) => {
            if (!stored) throw new Error("Atelier original generation request missing");
            verifyGenerationReceipt(stored, ack);
            adopt(ack.project, token);
            resolveGenerationReceipt(owner, stored);
        });
        generationCreates.set(key, { input: inputKey, promise: request });
        const release = () => { if (generationCreates.get(key)?.promise === request) generationCreates.delete(key); };
        void request.then(release, release);
        return request;
    };

    return {
        ...emptyState(),
        loadProjects: () => {
            if (listRequest && listNavigation === navigation) return listRequest;
            listNavigation = navigation;
            const owner = capture();
            const issuedMutation = mutation;
            const token = startLoading();
            const request = (async () => {
                try {
                    const incoming = await atelierProjectApi.listProjects();
                    if (!currentEnvironment(owner)) throw changed();
                    if (!currentOwner(owner) || issuedMutation !== mutation) return incoming;
                    incoming.forEach(mergeProjectExportJobs);
                    const projects = incoming.map(p => merge(p, token));
                    const current = get().currentProject;
                    const currentProject = current ? projects.find(p => p.id === current.id) ?? current : projects[0] ?? null;
                    // A list is not a recovery read for a failed command.
                    set({ projects: currentProject && !projects.some(p => p.id === currentProject.id) ? [...projects, currentProject] : projects,
                        currentProject, selectedNodeId: currentProject?.nodes.some(n => n.id === get().selectedNodeId) ? get().selectedNodeId : null,
                        pendingUploads: currentProject ? projectPendingUploads(owner.environment, currentProject.id) : [],
                        pendingExports: currentProject ? projectPendingExports(owner.environment, currentProject.id) : [],
                        pendingExportDeletions: currentProject ? projectPendingExportDeletions(owner.environment, currentProject.id) : [],
                        sequenceExportJobs: currentProject ? projectExportJobs(currentProject.id) : [],
                        needsRefresh: !!currentProject && failures.has(currentProject.id) });
                    return projects;
                } catch (error) {
                    if (currentOwner(owner)) set({ error: messageOf(error) });
                    throw error;
                }
            })().finally(() => {
                if (listRequest === request) listRequest = null;
                if (currentOwner(owner)) finishLoading(token);
            });
            listRequest = request;
            return request;
        },
        ensureProject: () => {
            if (get().currentProject) return Promise.resolve(get().currentProject!);
            if (navigationRequest) return navigationRequest;
            if (ensureRequest) return ensureRequest;
            const owner = capture();
            const issuedMutation = mutation;
            const request = (async () => {
                const listed = await get().loadProjects();
                if (!currentEnvironment(owner)) throw changed();
                if (navigationRequest) return await navigationRequest;
                if (get().currentProject) return get().currentProject!;
                if (!currentOwner(owner) || mutation !== issuedMutation || listed.length) throw changed();
                return await get().createProject();
            })().finally(() => { if (ensureRequest === request) ensureRequest = null; });
            ensureRequest = request;
            return request;
        },
        createProject: (title = "Atelier Exploration") => {
            if (createRequest) return createRequest;
            navigation += 1;
            const owner = capture();
            const token = startLoading();
            mutation += 1;
            const request = (async () => {
                try {
                    const project = await atelierProjectApi.createProject({ title });
                    if (!currentEnvironment(owner)) throw changed();
                    mutation += 1;
                    adopt(project, token, currentOwner(owner));
                    return project;
                } catch (error) {
                    if (currentOwner(owner)) set({ error: messageOf(error) });
                    throw error;
                }
            })().finally(() => {
                if (createRequest === request) createRequest = null;
                if (currentOwner(owner)) finishLoading(token);
            });
            createRequest = request;
            return request;
        },
        switchProject: projectId => {
            navigation += 1;
            const owner = capture();
            const cached = get().projects.find(p => p.id === projectId) ?? null;
            set({ currentProject: cached, selectedNodeId: null, pendingUploads: projectPendingUploads(owner.environment, projectId),
                pendingExports: projectPendingExports(owner.environment, projectId), pendingExportDeletions: projectPendingExportDeletions(owner.environment, projectId), sequenceExportJobs: projectExportJobs(projectId),
                needsRefresh: failures.has(projectId), isSaving: writes.has(projectId) });
            const token = startLoading();
            const request = readProject(projectId, owner, token, true).finally(() => {
                if (navigationRequest === request) navigationRequest = null;
            });
            navigationRequest = request;
            return request;
        },
        refreshProject: () => {
            const project = get().currentProject;
            if (!project) return Promise.reject(new Error("No Atelier project is selected"));
            return readProject(project.id, capture(), startLoading(), false);
        },
        updateProject: patch => {
            const snapshot = structuredClone(patch);
            return writeProject(project => atelierProjectApi.updateProject(project.id, { ...snapshot, expectedRevision: project.revision }),
                (project, token) => { adopt(project, token); });
        },
        deleteProject: async () => {
            const id = get().currentProject?.id;
            await writeProject(project => atelierProjectApi.deleteProject(project.id, { expectedRevision: project.revision }), () => {
                if (id) { failures.delete(id); readTokens.delete(id); }
                set(state => ({ projects: state.projects.filter(p => p.id !== id),
                    ...(state.currentProject?.id === id ? { currentProject: null, selectedNodeId: null, needsRefresh: false, isSaving: false, pendingExportDeletions: [] } : {}) }));
            });
        },
        createNode: input => writeNode(input, (p, patch) => atelierNodeApi.createNode(p.id, { ...patch, expectedRevision: p.revision })),
        updateNodeText: (id, input) => writeNode(input, (p, patch) => atelierNodeApi.updateNodeText(p.id, id, { ...patch, expectedRevision: p.revision })),
        updateNodeBody: (id, input) => writeNode(input, (p, patch) => atelierNodeApi.updateNodeBody(p.id, id, { ...patch, expectedRevision: p.revision })),
        updateNodeLayout: (id, input) => writeNode(input, (p, patch) => atelierNodeApi.updateNodeLayout(p.id, id, { ...patch, expectedRevision: p.revision })),
        updateNodesLayout: input => writeNode(input, (p, patch) => atelierNodeApi.updateNodesLayout(p.id, { ...patch, expectedRevision: p.revision })),
        moveNodes: input => writeNode(input, (p, patch) => atelierNodeApi.moveNodes(p.id, { ...patch, expectedRevision: p.revision })),
        wrapRegion: input => writeNode(input, (p, patch) => atelierNodeApi.wrapRegion(p.id, { ...patch, expectedRevision: p.revision })),
        setRegionMembers: (id, input) => writeNode(input, (p, patch) => atelierNodeApi.setRegionMembers(p.id, id, { ...patch, expectedRevision: p.revision })),
        updateRegionAppearance: (id, input) => writeNode(input, (p, patch) => atelierNodeApi.updateRegionAppearance(p.id, id, { ...patch, expectedRevision: p.revision })),
        deleteNodes: nodeIds => {
            const ids = [...nodeIds];
            return writeProject(p => atelierNodeHistoryApi.deleteNodes(p.id, { nodeIds: ids, expectedRevision: p.revision }),
                (ack, token) => { adopt(ack.project, token); });
        },
        restoreNodes: receiptId => writeProject(p => atelierNodeHistoryApi.restoreNodes(p.id, { receiptId, expectedRevision: p.revision }),
            (ack, token) => { adopt(ack.project, token); }),
        createMediaNode: input => writeUpload(input, "create_media_node", null,
            (projectId, request) => atelierUploadApi.createMediaNode(projectId, request), input.nodeType),
        uploadReference: (targetId, input) => writeUpload(input, "upload_reference_attach", targetId,
            (projectId, request) => atelierUploadApi.uploadReference(projectId, targetId, request), input.sourceNodeType),
        loadPendingUploads: () => {
            const project = get().currentProject;
            if (!project) return [];
            const pendingUploads = projectPendingUploads(requireActiveRuntimeEnvironment(), project.id);
            set({ pendingUploads });
            return pendingUploads;
        },
        readbackPendingUpload: async (clientRequestId) => {
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const owner = capture();
            const pendingUploads = listAtelierUploadRequests({ environment: owner.environment, projectId: project.id });
            set({ pendingUploads });
            const stored = clientRequestId === undefined ? pendingUploads[0] ?? null
                : pendingUploads.find(item => item.clientRequestId === clientRequestId) ?? null;
            if (!stored) return null;
            try {
                const token = ++serial;
                const capturedFailure = failures.get(project.id);
                const readback = await atelierUploadApi.getReceipt(project.id, stored.clientRequestId);
                if (!currentEnvironment(owner)) throw changed();
                verifyReadback(project.id, stored, readback);
                resolveAtelierUploadRequest({ environment: owner.environment, projectId: project.id, clientRequestId: stored.clientRequestId });
                if (capturedFailure === failures.get(project.id)) failures.delete(project.id);
                if (currentOwner(owner) && !failures.has(project.id)) set({ error: null });
                adopt(readback.project, token);
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ pendingUploads: projectPendingUploads(owner.environment, project.id) });
                }
                return readback;
            } catch (error) {
                if (error instanceof Error && "status" in error && (error as { status?: number }).status === 404) return null;
                if (currentOwner(owner)) set({ error: messageOf(error) });
                throw error;
            }
        },

        saveSequence: (entries, expectedRevision) => {
            const snapshot = structuredClone(entries);
            return writeProject(async project => atelierSequenceApi.saveSequence(project.id, { expectedRevision: expectedRevision ?? project.revision, entries: snapshot }),
                (project, token) => { adopt(project, token); });
        },
        createSequenceExport: async () => {
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            if (failures.has(project.id)) throw new Error("Refresh the Atelier project before exporting again");
            const owner = capture();
            if (hasBlockingExportJob(project.id)) throw exportPending();
            const pending = projectPendingExports(owner.environment, project.id);
            const pendingDeletes = projectPendingExportDeletions(owner.environment, project.id);
            if (pending.length > 0 || pendingDeletes.length > 0) {
                if (currentOwner(owner) && get().currentProject?.id === project.id) set({ pendingExports: pending, pendingExportDeletions: pendingDeletes });
                throw exportPending();
            }
            const createKey = exportCreateKey(owner, project.id);
            if (exportCreates.has(createKey)) throw exportPending();
            const createToken = ++serial;
            exportCreates.set(createKey, createToken);
            const clientRequestId = createAtelierExportRequestId();
            let stored: StoredAtelierExportRequest;
            try {
                const result = beginAtelierExportRequest({
                    environment: owner.environment,
                    projectId: project.id,
                    expectedRevision: project.revision,
                    idFactory: () => clientRequestId,
                });
                stored = result.request;
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ pendingExports: projectPendingExports(owner.environment, project.id) });
                }
            } catch (error) {
                if (exportCreates.get(createKey) === createToken) exportCreates.delete(createKey);
                return Promise.reject(error);
            }
            const token = ++serial;
            try {
                const ack = await atelierSequenceApi.createExport(project.id, stored.request);
                verifyExportAck(ack);
                if (!currentEnvironment(owner)) throw changed();
                clearMatchingExportRequest(project.id, owner.environment, stored, ack);
                return applyExportAck(ack, token, owner);
            } catch (error) {
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ error: messageOf(error), pendingExports: projectPendingExports(owner.environment, project.id), pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id) });
                }
                throw error;
            } finally {
                if (exportCreates.get(createKey) === createToken) exportCreates.delete(createKey);
            }
        },
        redoSequenceExport: async record => {
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            if (record.project_id !== project.id || !project.exports.some(item => item.id === record.id)) {
                throw new Error("Atelier export record does not belong to the selected project");
            }
            const snapshot = record.timeline_snapshot;
            if (!snapshot || (snapshot as { schema_version?: unknown }).schema_version !== "atelier.timeline.v1") {
                throw new Error("This export cannot be redone because it does not have a native timeline snapshot");
            }
            if (failures.has(project.id)) throw new Error("Refresh the Atelier project before exporting again");
            const owner = capture();
            if (hasBlockingExportJob(project.id)) throw exportPending();
            const pending = projectPendingExports(owner.environment, project.id);
            const pendingDeletes = projectPendingExportDeletions(owner.environment, project.id);
            if (pending.length > 0 || pendingDeletes.length > 0) {
                if (currentOwner(owner) && get().currentProject?.id === project.id) set({ pendingExports: pending, pendingExportDeletions: pendingDeletes });
                throw exportPending();
            }
            const createKey = exportCreateKey(owner, project.id);
            if (exportCreates.has(createKey)) throw exportPending();
            const createToken = ++serial;
            exportCreates.set(createKey, createToken);
            const clientRequestId = createAtelierExportRequestId();
            let stored: StoredAtelierExportRequest;
            try {
                const result = beginAtelierExportRequest({
                    environment: owner.environment,
                    projectId: project.id,
                    expectedRevision: project.revision,
                    sourceExportId: record.id,
                    idFactory: () => clientRequestId,
                });
                stored = result.request;
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ pendingExports: projectPendingExports(owner.environment, project.id) });
                }
            } catch (error) {
                if (exportCreates.get(createKey) === createToken) exportCreates.delete(createKey);
                return Promise.reject(error);
            }
            const token = ++serial;
            try {
                const ack = await atelierSequenceApi.redoExport(project.id, record.id, stored.request);
                verifyExportAck(ack);
                if (!currentEnvironment(owner)) throw changed();
                clearMatchingExportRequest(project.id, owner.environment, stored, ack);
                return applyExportAck(ack, token, owner);
            } catch (error) {
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ error: messageOf(error), pendingExports: projectPendingExports(owner.environment, project.id), pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id) });
                }
                throw error;
            } finally {
                if (exportCreates.get(createKey) === createToken) exportCreates.delete(createKey);
            }
        },
        loadPendingExports: () => {
            const project = get().currentProject;
            if (!project) return [];
            const pendingExports = projectPendingExports(requireActiveRuntimeEnvironment(), project.id);
            set({ pendingExports });
            return pendingExports;
        },
        readbackPendingExport: async (clientRequestId) => {
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const owner = capture();
            const pendingExports = listAtelierExportRequests({ environment: owner.environment, projectId: project.id });
            set({ pendingExports });
            const stored = clientRequestId === undefined ? pendingExports[0] ?? null
                : pendingExports.find(item => item.clientRequestId === clientRequestId) ?? null;
            if (!stored) return null;
            const token = ++serial;
            try {
                const ack = await atelierSequenceApi.getExportByRequest(project.id, stored.clientRequestId, stored);
                verifyExportAck(ack);
                if (!currentEnvironment(owner)) throw changed();
                clearMatchingExportRequest(project.id, owner.environment, stored, ack);
                return applyExportAck(ack, token, owner);
            } catch (error) {
                if (error instanceof Error && "status" in error && (error as { status?: number }).status === 404) return null;
                if (currentOwner(owner)) set({ error: messageOf(error), pendingExports: projectPendingExports(owner.environment, project.id), pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id) });
                throw error;
            }
        },

        deleteSequenceExport: async (record, deleteFile = true) => {
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            if (record.project_id !== project.id || !project.exports.some(item => item.id === record.id)) {
                throw new Error("Atelier export record does not belong to the selected project");
            }
            if (record.deletion && !["pending", "blocked"].includes(record.deletion.file_status)) {
                throw new Error("Atelier export has already been deleted from history");
            }
            if (failures.has(project.id)) throw new Error("Refresh the Atelier project before exporting again");
            const owner = capture();
            if (hasBlockingExportJob(project.id)) throw exportPending();
            const pendingExports = projectPendingExports(owner.environment, project.id);
            const pendingDeletes = projectPendingExportDeletions(owner.environment, project.id);
            if (pendingExports.length > 0 || pendingDeletes.length > 0) {
                if (currentOwner(owner) && get().currentProject?.id === project.id) set({ pendingExports, pendingExportDeletions: pendingDeletes });
                throw exportPending();
            }
            const createKey = exportCreateKey(owner, project.id);
            if (exportCreates.has(createKey) || exportDeletes.has(createKey)) throw exportPending();
            const createToken = ++serial;
            exportDeletes.set(createKey, createToken);
            const clientRequestId = record.deletion?.client_request_id ?? createAtelierExportDeletionRequestId();
            let stored: StoredAtelierExportDeletionRequest;
            try {
                const result = beginAtelierExportDeletionRequest({
                    environment: owner.environment,
                    projectId: project.id,
                    exportId: record.id,
                    expectedRevision: project.revision,
                    deleteFile,
                    idFactory: () => clientRequestId,
                });
                stored = result.request;
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id) });
                }
            } catch (error) {
                if (exportDeletes.get(createKey) === createToken) exportDeletes.delete(createKey);
                return Promise.reject(error);
            }
            const token = ++serial;
            try {
                const ack = await atelierSequenceApi.deleteExport(project.id, record.id, stored.request);
                verifyExportDeletionAck(stored, ack);
                if (!currentEnvironment(owner)) throw changed();
                resolveAtelierExportDeletionRequest({ environment: owner.environment, projectId: project.id, exportId: record.id, clientRequestId: stored.clientRequestId, request: stored.request });
                adopt(ack.project, token);
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id), error: null });
                }
                return ack;
            } catch (error) {
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ error: messageOf(error), pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id) });
                }
                throw error;
            } finally {
                if (exportDeletes.get(createKey) === createToken) exportDeletes.delete(createKey);
            }
        },
        loadPendingExportDeletions: () => {
            const project = get().currentProject;
            if (!project) return [];
            const pendingExportDeletions = projectPendingExportDeletions(requireActiveRuntimeEnvironment(), project.id);
            set({ pendingExportDeletions });
            return pendingExportDeletions;
        },
        readbackPendingExportDeletion: async (clientRequestId) => {
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const owner = capture();
            const pendingExportDeletions = listAtelierExportDeletionRequests({ environment: owner.environment, projectId: project.id });
            set({ pendingExportDeletions });
            const stored = clientRequestId === undefined ? pendingExportDeletions[0] ?? null
                : pendingExportDeletions.find(item => item.clientRequestId === clientRequestId) ?? null;
            if (!stored) return null;
            const token = ++serial;
            try {
                const readProject = await atelierProjectApi.getProject(project.id);
                if (!currentEnvironment(owner)) throw changed();
                const ack = findDeletionAck(readProject, stored);
                if (!ack) return null;
                resolveAtelierExportDeletionRequest({ environment: owner.environment, projectId: project.id, exportId: stored.exportId, clientRequestId: stored.clientRequestId, request: stored.request });
                adopt(readProject, token);
                if (currentOwner(owner) && get().currentProject?.id === project.id) {
                    set({ pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id), error: null });
                }
                return ack;
            } catch (error) {
                if (currentOwner(owner)) set({ error: messageOf(error), pendingExportDeletions: projectPendingExportDeletions(owner.environment, project.id) });
                throw error;
            }
        },
        createVideoCandidate: (nodeId, input) => runGenerationCreate(nodeId, input),
        createImageCandidate: (nodeId, input) => runGenerationCreate(nodeId, input),
        createAudioCandidate: (nodeId, input) => runGenerationCreate(nodeId, input),
        saveGenerationDraft: (nodeId, input) => writeNode(input, (p, patch) => {
            const request = "contract_version" in patch
                ? {
                    expected_revision: p.revision,
                    contract_version: patch.contract_version,
                    prompt: patch.prompt,
                    model_id: patch.model_id,
                    mode: patch.mode,
                    params: patch.params,
                    media: patch.media,
                    ...(Object.prototype.hasOwnProperty.call(patch, "batch_size") ? { batch_size: patch.batch_size } : {}),
                }
                : { expected_revision: p.revision, prompt: patch.prompt, model_id: patch.model_id, mode: patch.mode, params: patch.params };
            return atelierGenerationApi.setGenerationDraft(p.id, nodeId, request);
        }),
        saveMediaGenerationDraft: (nodeId, input) => writeNode(input, (p, patch) =>
            atelierMediaApi.saveDraft(p.id, nodeId, { ...patch, expected_revision: p.revision })),
        selectMediaGenerationCandidate: (nodeId, candidateId) => writeNode({ candidateId }, (p, value) =>
            atelierMediaApi.selectCandidate(p.id, nodeId, value.candidateId, { expected_revision: p.revision })),
        setMediaGenerationCandidateVisibility: (nodeId, candidateId, visible) => writeNode({ candidateId, visible }, (p, value) =>
            atelierMediaApi.setCandidateVisibility(p.id, nodeId, value.candidateId, { expected_revision: p.revision, visible: value.visible })),
        loadPendingGenerations: () => {
            const project = get().currentProject;
            const pendingGenerations = project ? generationPending(capture(), project.id) : [];
            set({ pendingGenerations });
            return pendingGenerations;
        },
        readbackPendingGeneration: async clientRequestId => {
            const owner = capture();
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const pending = generationPending(owner, project.id);
            set({ pendingGenerations: pending });
            const stored = clientRequestId === undefined ? pending[0] : pending.find(item => item.clientRequestId === clientRequestId);
            if (!stored) return null;
            const failure = failures.get(project.id);
            const token = ++serial;
            const ack = await atelierGenerationApi.getRequest(project.id, stored.clientRequestId, stored.request);
            if (!currentOwner(owner)) throw changed();
            verifyGenerationReceipt(stored, ack);
            const accepted = adopt(ack.project, token);
            resolveGenerationReceipt(owner, stored);
            if (failure && failures.get(project.id) === failure && !writes.has(project.id) && accepted.revision >= failure.revision) {
                failures.delete(project.id);
                set({ needsRefresh: false, error: null });
            }
            return ack;
        },
        refreshGenerationAttempt: id => runGenerationAction(id, "read"),
        continueGenerationAttempt: id => runGenerationAction(id, "continue"),
        cancelGenerationAttempt: id => runGenerationAction(id, "cancel"),
        selectGenerationCandidate: (nodeId, candidateId) => writeNode({ candidateId }, (p, value) =>
            atelierGenerationApi.selectCandidate(p.id, nodeId, value.candidateId, { expected_revision: p.revision })),
        setGenerationCandidateVisibility: (nodeId, candidateId, visible) => writeNode({ candidateId, visible }, (p, value) =>
            atelierGenerationApi.setCandidateVisibility(p.id, nodeId, value.candidateId, { expected_revision: p.revision, visible: value.visible })),
        setCandidateJudgment: (nodeId, candidateId, input) => writeNode({ candidateId, ...input }, (p, value) =>
            atelierCandidateJudgmentApi.setCandidateJudgment(p.id, nodeId, value.candidateId, {
                expected_revision: p.revision,
                verdict: value.verdict,
                reason: value.reason,
            })),
        branchFromCandidate: (nodeId, candidateId) => {
            const owner = capture();
            return writeProject(project => atelierCandidateBranchApi.branchCandidate(project.id, nodeId, candidateId, { expected_revision: project.revision }, project),
                (ack, token) => {
                    adopt(ack.project, token);
                    const branchedId = ack.node_ids[0];
                    if (currentOwner(owner) && get().currentProject?.id === ack.project.id
                        && get().currentProject?.nodes.some(node => node.id === branchedId)) {
                        set({ selectedNodeId: branchedId });
                    }
                });
        },
        startAgentTurn: (input, options = {}) => {
            const owner = capture();
            const selected = get().currentProject;
            if (!selected) return Promise.reject(new Error("No Atelier project is selected"));
            const inputKey = canonicalJson({ ...input, transport: options.transport ?? "sync" });
            const key = JSON.stringify([owner.environment.key, owner.environment.generation, selected.id, "single_turn"]);
            const inFlight = agentStarts.get(key);
            if (inFlight) return inFlight.input === inputKey ? inFlight.promise : Promise.reject(new Error("A different Atelier Agent turn is already pending"));
            const clientTurnId = createAtelierAgentTurnRequestId();
            let stored: StoredAtelierAgentSingleTurnRequest | null = null;
            const request = writeProject(project => {
                if (projectPendingAgentTurns(owner.environment, project.id).length) throw new Error("Read back or retry the pending Atelier Agent turn before starting another");
                const saved = beginAtelierAgentTurnRequest({
                    environment: owner.environment, projectId: project.id, expectedRevision: project.revision, clientTurnId,
                    userMessage: input.userMessage, selectedNodeId: input.selectedNodeId,
                });
                stored = saved.request;
                publishAgentPending(owner, project.id);
                const send = options.transport === "stream"
                    ? startTurnStream(project.id, stored.request, { signal: options.signal, onEvent: options.onEvent })
                    : atelierAgentApi.startTurn(project.id, stored.request);
                return send.catch(error => {
                    if (currentOwner(owner) && stored && isDefiniteAgentStartRejection(error)) {
                        resolveAtelierAgentTurnRequest({ environment: owner.environment, projectId: stored.projectId, clientTurnId: stored.clientTurnId, request: stored.request });
                        publishAgentPending(owner, project.id);
                    }
                    throw error;
                });
            }, (ack, token) => {
                if (!stored) throw new Error("Atelier Agent start request missing");
                resolveAgentStart(owner, stored, ack);
                adopt(ack.project, token);
            }, { noRefreshOnError: isDefiniteAgentStartRejection });
            agentStarts.set(key, { input: inputKey, promise: request });
            const release = () => { if (agentStarts.get(key)?.promise === request) agentStarts.delete(key); };
            void request.then(release, release);
            return request;
        },
        startAgentLoop: (input, options = {}) => {
            const owner = capture();
            const selected = get().currentProject;
            if (!selected) return Promise.reject(new Error("No Atelier project is selected"));
            const maxIterations = input.maxIterations ?? 3;
            const planner = input.planner ?? "deterministic_core";
            const skillName = input.skillName ?? null;
            const inputKey = canonicalJson({ userMessage: input.userMessage, selectedNodeId: input.selectedNodeId, planner, skillName, maxIterations, transport: options.transport ?? "sync" });
            const key = JSON.stringify([owner.environment.key, owner.environment.generation, selected.id, "agent_loop"]);
            const inFlight = agentLoopStarts.get(key);
            if (inFlight) return inFlight.input === inputKey ? inFlight.promise : Promise.reject(new Error("A different Atelier Agent loop is already pending"));
            const clientTurnId = createAtelierAgentTurnRequestId();
            let stored: StoredAtelierAgentLoopRequest | null = null;
            const request = writeProject(project => {
                if (projectPendingAgentTurns(owner.environment, project.id).length) throw new Error("Read back or retry the pending Atelier Agent turn before starting another");
                const saved = beginAtelierAgentLoopRequest({
                    environment: owner.environment, projectId: project.id, expectedRevision: project.revision, clientTurnId,
                    userMessage: input.userMessage, selectedNodeId: input.selectedNodeId, ...(input.planner === undefined ? {} : { planner: input.planner }),
                    ...(input.skillName === undefined ? {} : { skillName: input.skillName }), maxIterations,
                });
                stored = saved.request;
                publishAgentPending(owner, project.id);
                const send = options.transport === "stream"
                    ? startLoopStream(project.id, stored.request, { signal: options.signal, onEvent: options.onEvent })
                    : atelierAgentLoopApi.startLoop(project.id, stored.request);
                return send.catch(error => {
                    if (currentOwner(owner) && stored && isDefiniteAgentStartRejection(error)) {
                        resolveAtelierAgentLoopRequest({ environment: owner.environment, projectId: stored.projectId, clientTurnId: stored.clientTurnId, request: stored.request });
                        publishAgentPending(owner, project.id);
                    }
                    throw error;
                });
            }, (ack, token) => {
                if (!stored) throw new Error("Atelier Agent loop start request missing");
                resolveAgentLoopStart(owner, stored, ack);
                adopt(ack.project, token);
                if (ack.schedule_error && get().currentProject?.id === ack.project.id) set({ error: ack.schedule_error });
            }, { noRefreshOnError: isDefiniteAgentStartRejection });
            agentLoopStarts.set(key, { input: inputKey, promise: request });
            const release = () => { if (agentLoopStarts.get(key)?.promise === request) agentLoopStarts.delete(key); };
            void request.then(release, release);
            return request;
        },
        loadPendingAgentTurns: () => {
            const project = get().currentProject;
            const pendingAgentTurns = project ? projectPendingAgentTurns(capture().environment, project.id) : [];
            set({ pendingAgentTurns });
            return pendingAgentTurns;
        },
        readbackPendingAgentTurn: async clientTurnId => {
            const owner = capture();
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const pendingAgentTurns = projectPendingAgentTurns(owner.environment, project.id);
            set({ pendingAgentTurns });
            const stored = clientTurnId === undefined ? pendingAgentTurns[0] : pendingAgentTurns.find(item => item.clientTurnId === clientTurnId);
            if (!stored) return null;
            const failure = failures.get(project.id);
            const token = ++serial;
            if (stored.entrypoint === "agent_loop") {
                const ack = await atelierAgentLoopApi.getLoop(project.id, stored.clientTurnId);
                if (!currentOwner(owner)) throw changed();
                resolveAgentLoopStart(owner, stored, ack);
                clearReadbackFailure(ack.project, failure);
                adopt(ack.project, token);
                return ack;
            }
            const ack = await atelierAgentApi.getTurn(project.id, stored.clientTurnId);
            if (!currentOwner(owner)) throw changed();
            resolveAgentStart(owner, stored, ack);
            clearReadbackFailure(ack.project, failure);
            adopt(ack.project, token);
            return ack;
        },
        retryPendingAgentTurn: async clientTurnId => {
            const owner = capture();
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const pendingAgentTurns = projectPendingAgentTurns(owner.environment, project.id);
            set({ pendingAgentTurns });
            const stored = clientTurnId === undefined ? pendingAgentTurns[0] : pendingAgentTurns.find(item => item.clientTurnId === clientTurnId);
            if (!stored) return null;
            const failure = failures.get(project.id);
            const token = ++serial;
            if (stored.entrypoint === "agent_loop") {
                const ack = await atelierAgentLoopApi.startLoop(project.id, stored.request);
                if (!currentOwner(owner)) throw changed();
                resolveAgentLoopStart(owner, stored, ack);
                clearReadbackFailure(ack.project, failure);
                adopt(ack.project, token);
                return ack;
            }
            const ack = await atelierAgentApi.startTurn(project.id, stored.request);
            if (!currentOwner(owner)) throw changed();
            resolveAgentStart(owner, stored, ack);
            clearReadbackFailure(ack.project, failure);
            adopt(ack.project, token);
            return ack;
        },
        refreshAgentTurn: async turnId => {
            const owner = capture();
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const failure = failures.get(project.id);
            const token = ++serial;
            const ack = await atelierAgentApi.getTurn(project.id, turnId);
            if (!currentOwner(owner)) throw changed();
            clearReadbackFailure(ack.project, failure);
            adopt(ack.project, token);
            return ack;
        },
        refreshAgentLoop: async turnId => {
            const owner = capture();
            const project = get().currentProject;
            if (!project) throw new Error("No Atelier project is selected");
            const failure = failures.get(project.id);
            const token = ++serial;
            const ack = await atelierAgentLoopApi.getLoop(project.id, turnId);
            if (!currentOwner(owner)) throw changed();
            clearReadbackFailure(ack.project, failure);
            adopt(ack.project, token);
            if (ack.schedule_error && get().currentProject?.id === ack.project.id) set({ error: ack.schedule_error });
            return ack;
        },
        approveAgentCall: (turnId, callId) => runAgentApprovalAction(turnId, callId, "approved"),
        denyAgentCall: (turnId, callId) => runAgentApprovalAction(turnId, callId, "denied"),
        revokeAgentCall: (turnId, callId) => runAgentApprovalAction(turnId, callId, "revoked"),
        continueAgentCall: (turnId, callId, options = {}) => writeProject(project => {
            const call = findAgentCall(project, turnId, callId);
            const auth = authorizationFromCall(call);
            const request = {
                expected_revision: project.revision,
                turn_id: turnId,
                call_id: callId,
                approval_id: auth.approvalId,
                authorization_hash: auth.authorizationHash,
                client_request_id: clientRequestIdFromCall(call),
            };
            return options.transport === "stream"
                ? continueTurnStream(project.id, turnId, request, { signal: options.signal, onEvent: options.onEvent })
                : atelierAgentApi.continueTurn(project.id, turnId, request);
        }, (ack, token) => {
            adopt(ack.project, token);
            if (ack.schedule_error && get().currentProject?.id === ack.project.id) set({ error: ack.schedule_error });
        }),
        cancelAgentTurn: turnId => writeProject(project => atelierAgentApi.cancelTurn(project.id, turnId, { turn_id: turnId }), (ack, token) => {
            adopt(ack.project, token);
        }),
        recordAgentLoopCanvasApproval: (turnId, callId, decision) => writeProject(project => {
            const call = findAgentCall(project, turnId, callId);
            const auth = canvasAuthorizationFromCall(call);
            return atelierAgentLoopApi.recordCanvasApproval(project.id, turnId, {
                expected_revision: project.revision,
                turn_id: turnId,
                call_id: callId,
                approval_id: auth.approvalId,
                authorization_hash: auth.authorizationHash,
                decision,
            });
        }, (ack, token) => {
            adopt(ack.project, token);
            if (ack.schedule_error && get().currentProject?.id === ack.project.id) set({ error: ack.schedule_error });
        }),
        resumeAgentLoop: (turnId, options = {}) => writeProject(project => (options.transport === "stream"
            ? resumeLoopStream(project.id, turnId, { turn_id: turnId }, { signal: options.signal, onEvent: options.onEvent })
            : atelierAgentLoopApi.resumeLoop(project.id, turnId, { turn_id: turnId })), (ack, token) => {
            adopt(ack.project, token);
            if (ack.schedule_error && get().currentProject?.id === ack.project.id) set({ error: ack.schedule_error });
        }),
        refreshSequenceExport: jobId => runExportJobAction(jobId, (api, projectId, id) => api.getExport(projectId, id)),
        cancelSequenceExport: jobId => runExportJobAction(jobId, (api, projectId, id) => api.cancelExport(projectId, id)),
        repairSequenceExport: jobId => runExportJobAction(jobId, (api, projectId, id) => api.repairExport(projectId, id)),
        resumeSequenceExport: jobId => runExportJobAction(jobId, (api, projectId, id) => api.resumeExport(projectId, id)),
        attachPendingReferenceNode: (id, input) => writeNode(input, (p, patch) => atelierReferenceApi.attachPendingReference(p.id, id, { ...patch, expectedRevision: p.revision })),
        attachReferenceNode: (id, input) => writeNode(input, (p, patch) => atelierReferenceApi.attachReference(p.id, id, { ...patch, expectedRevision: p.revision })),
        detachReferenceNode: (id, input) => writeNode(input, (p, patch) => atelierReferenceApi.detachReference(p.id, id, { ...patch, expectedRevision: p.revision })),
        selectNode: nodeId => set({ selectedNodeId: nodeId && get().currentProject?.nodes.some(n => n.id === nodeId) ? nodeId : null }),
    };
});

subscribeRuntimeEnvironment(() => {
    navigation += 1;
    mutation += 1;
    loadingToken = ++serial;
    listRequest = null;
    ensureRequest = null;
    createRequest = null;
    navigationRequest = null;
    readTokens.clear();
    generationCreates.clear();
    generationActions.clear();
    agentStarts.clear();
    agentLoopStarts.clear();
    agentActions.clear();
    exportJobs.clear();
    exportCreates.clear();
    exportDeletes.clear();
    writes.clear();
    writeQueues.clear();
    failures.clear();
    useAtelierStore.setState(emptyState());
});
