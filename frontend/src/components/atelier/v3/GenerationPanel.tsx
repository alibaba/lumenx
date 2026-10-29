"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import PreviewVideo from "@/components/shared/preview/PreviewVideo";
import { BranchSourceCard } from "./BranchSourceCard";
import { CandidateJudgmentDialog, latestCandidateJudgment } from "./CandidateJudgmentDialog";
import { PinnedComposer, type PinnedComposerTargetRect } from "./PinnedComposer";
import { isSelectableImportedAtelierVideoCandidate, parseAtelierGenerationDraft } from "@/lib/atelierGenerationApi";
import { getCatalogVideoModelConfig, type DurationConfig } from "@/lib/modelCatalog";
import { getRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { AtelierProjectApiError } from "@/lib/atelierApi";
import { buildAtelierGenerationDraft, buildAtelierGenerationRequest, canonicalReferenceMediaFromSources, composerBooleanDefault, hasMissingFirstLastSelection, isOrderedComposerModel, LEGACY_ATELIER_VIDEO_MODEL, orderedMediaHasMissing, orderedMediaFromValues, sourceKey, type AtelierComposerMode, type AtelierComposerModelOption, type AtelierComposerSource, type AtelierComposerValues } from "@/lib/atelierGenParams";
import { useAtelierStore } from "@/store/atelierStore";
import type { AtelierCandidateJudgment, AtelierGenerationAttempt, AtelierGenerationCandidateProjection, AtelierGenerationDraft, AtelierNode, AtelierOrderedVideoModel, AtelierProject, JsonRecord, JsonValue } from "@/lib/atelierTypes";

type StoreCreateOwner = {
    ownerKey: string;
    projectId: string;
    nodeId: string;
    runtimeKey: string;
    runtimeGeneration: number;
};

type RecoverableLocalError =
    | { kind: "attempt-conflict"; ownerKey: string; attemptId: string; attemptRevision: number }
    | { kind: "refresh-required"; ownerKey: string; projectRevision: number };

type CandidateView =
    | { kind: "generation"; candidate: AtelierGenerationCandidateProjection; deleted: boolean }
    | { kind: "legacy"; id: string; title: string; status: string | null; videoUrl: string | null; deleted: boolean; record: JsonRecord };

const MODEL_ID = LEGACY_ATELIER_VIDEO_MODEL;
const CATALOG_MODEL_ID = "wan2.7-i2v";
const POLL_MS = 2000;
const panelButtonClass = "rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-xs text-white/75 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-35";

function recordOf(value: JsonValue | undefined): JsonRecord | null {
    return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : null;
}

function stringArray(value: JsonValue | undefined): string[] {
    return Array.isArray(value) && value.every(item => typeof item === "string") ? value : [];
}

function numberParam(record: JsonRecord | null, key: string): number | null {
    const value = record?.[key];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringParam(record: JsonRecord | null, key: string): string | null {
    const value = record?.[key];
    return typeof value === "string" ? value : null;
}

function booleanParam(record: JsonRecord | null, key: string): boolean | null {
    const value = record?.[key];
    return typeof value === "boolean" ? value : null;
}

function generationDraft(node: AtelierNode | null): { unsupported: boolean; draft: AtelierGenerationDraft | null } {
    if (!node || !Object.prototype.hasOwnProperty.call(node.data, "generation_draft")) return { unsupported: false, draft: null };
    try {
        return { unsupported: false, draft: parseAtelierGenerationDraft(node.data.generation_draft) };
    } catch {
        return { unsupported: true, draft: null };
    }
}


function generationCandidate(value: JsonValue, deleted: boolean): CandidateView | null {
    const record = recordOf(value);
    if (!record) return null;
    if (record.execution_contract === "atelier-provider-video-v1" && typeof record.id === "string" && typeof record.attempt_id === "string"
        && record.media_kind === "video" && typeof record.status === "string") {
        return { kind: "generation", deleted, candidate: record as unknown as AtelierGenerationCandidateProjection };
    }
    if (typeof record.id === "string" || typeof record.video_url === "string") {
        return {
            kind: "legacy",
            deleted,
            id: typeof record.id === "string" ? record.id : record.video_url as string,
            title: typeof record.title === "string" ? record.title : typeof record.prompt === "string" ? record.prompt : "Legacy candidate",
            status: typeof record.status === "string" ? record.status : null,
            videoUrl: typeof record.video_url === "string" ? record.video_url : null,
            record,
        };
    }
    return null;
}

function candidatesFor(node: AtelierNode): CandidateView[] {
    const live = Array.isArray(node.data.candidates) ? node.data.candidates.map(item => generationCandidate(item, false)) : [];
    const deleted = Array.isArray(node.data.deleted_candidates) ? node.data.deleted_candidates.map(item => generationCandidate(item, true)) : [];
    return [...live, ...deleted].filter((item): item is CandidateView => !!item);
}

function durationOption(duration: DurationConfig | undefined): AtelierComposerModelOption["duration"] {
    if (!duration) return { type: "fixed", value: 5, default: 5 };
    if (duration.type === "fixed") return { type: "fixed", value: duration.value, default: duration.value };
    if (duration.type === "buttons") return { type: "buttons", options: [...duration.options], default: duration.default };
    return { type: "slider", min: duration.min, max: duration.max, step: duration.step, default: duration.default, supports_auto: duration.supports_auto };
}

function composerOption(catalogId: string, composerId: AtelierComposerModelOption["id"], fallbackName: string): AtelierComposerModelOption | null {
    const catalog = getCatalogVideoModelConfig(catalogId);
    if (!catalog) return null;
    return {
        id: composerId,
        name: catalog.name || fallbackName,
        status: catalog.status,
        disabled: catalog.status === "planned" || catalog.status === "hidden" || catalog.status === "deprecated",
        params: {
            resolution: catalog.params.resolution,
            ratio: catalog.params.ratio,
            audio: catalog.params.audio,
            promptExtend: catalog.params.promptExtend,
            watermark: catalog.params.watermark,
        },
        duration: durationOption(catalog.duration),
    };
}

function defaultComposerModels(): AtelierComposerModelOption[] {
    return [
        composerOption(CATALOG_MODEL_ID, MODEL_ID, "Wan 2.7 I2V"),
        composerOption("wan3.0-video", "wan/wan3.0-video#video", "Wan 3.0 Video"),
        composerOption("wan3.0-video-prime", "wan/wan3.0-video-prime#video", "Wan 3.0 Video Prime"),
    ].filter((item): item is AtelierComposerModelOption => !!item);
}

function durationDefault(model: AtelierComposerModelOption | null): number {
    return model?.duration?.default ?? model?.duration?.value ?? model?.duration?.options?.[0] ?? 5;
}


function isTerminal(attempt: AtelierGenerationAttempt): boolean {
    return attempt.status === "completed" || attempt.status === "failed" || attempt.status === "cancelled";
}

function isTerminalCandidateStatus(status: string | null): boolean {
    return status === "completed" || status === "failed" || status === "cancelled";
}

function isSelectableImportedCandidate(project: AtelierProject, item: CandidateView): boolean {
    return item.kind === "legacy" && !item.deleted && isSelectableImportedAtelierVideoCandidate(project, item.record, item.id);
}

function isImportedVideoCandidate(project: AtelierProject, item: CandidateView): boolean {
    return item.kind === "legacy" && isSelectableImportedAtelierVideoCandidate(project, item.record, item.id);
}

function supportsVideoCandidateVisibility(item: CandidateView): boolean {
    return item.kind === "generation" || item.record.media_kind === undefined || item.record.media_kind === "video";
}

function candidateId(item: CandidateView): string {
    return item.kind === "generation" ? item.candidate.id : item.id;
}

function candidateStatus(item: CandidateView): string | null {
    return item.kind === "generation" ? item.candidate.status : item.status;
}

function candidateVideoUrl(item: CandidateView): string | null {
    return item.kind === "generation" ? item.candidate.media_kind === "video" ? item.candidate.video_url : null : item.videoUrl;
}

function canContinue(attempt: AtelierGenerationAttempt): boolean {
    return !isTerminal(attempt)
        && ["accepted", "submitting", "submitted", "observed"].includes(attempt.provider_execution.phase);
}

function attemptLabelKey(attempt: AtelierGenerationAttempt): string {
    if (attempt.status === "completed") return "statusCompleted";
    if (attempt.status === "failed") return "statusFailed";
    if (attempt.status === "cancelled") return "statusCancelled";
    if (attempt.status === "needs_confirmation") return "statusNeedsConfirmation";
    if (attempt.provider_execution.phase === "accepted") return "phaseAccepted";
    if (attempt.provider_execution.phase === "submitted" || attempt.provider_execution.phase === "observed") return "phaseSubmitted";
    return "statusPending";
}

function isStorageConflict(cause: unknown): boolean {
    return cause instanceof AtelierProjectApiError && cause.code === "storage_conflict";
}

function isLostAckTransportError(cause: unknown): boolean {
    if (cause instanceof AtelierProjectApiError) return false;
    return cause instanceof TypeError || (cause instanceof Error && cause.message === "Failed to fetch");
}

export function GenerationPanel({ project, selectedNode, ownerKey, disabled, models, targetRect = null, targetAvailableRect = null }: {
    project: AtelierProject;
    selectedNode: AtelierNode | null;
    ownerKey: string;
    disabled: boolean;
    models?: AtelierComposerModelOption[];
    targetRect?: PinnedComposerTargetRect | null;
    targetAvailableRect?: PinnedComposerTargetRect | null;
}) {
    const t = useTranslations("atelier.generationPanel");
    const createVideoCandidate = useAtelierStore(state => state.createVideoCandidate);
    const loadPendingGenerations = useAtelierStore(state => state.loadPendingGenerations);
    const readbackPendingGeneration = useAtelierStore(state => state.readbackPendingGeneration);
    const refreshGenerationAttempt = useAtelierStore(state => state.refreshGenerationAttempt);
    const continueGenerationAttempt = useAtelierStore(state => state.continueGenerationAttempt);
    const cancelGenerationAttempt = useAtelierStore(state => state.cancelGenerationAttempt);
    const selectGenerationCandidate = useAtelierStore(state => state.selectGenerationCandidate);
    const setGenerationCandidateVisibility = useAtelierStore(state => state.setGenerationCandidateVisibility);
    const setCandidateJudgment = useAtelierStore(state => state.setCandidateJudgment);
    const branchFromCandidate = useAtelierStore(state => state.branchFromCandidate);
    const saveGenerationDraft = useAtelierStore(state => state.saveGenerationDraft);
    const pendingGenerations = useAtelierStore(state => state.pendingGenerations);
    const isSaving = useAtelierStore(state => state.isSaving);
    const needsRefresh = useAtelierStore(state => state.needsRefresh);

    const composerModels = useMemo(() => models?.length ? models : defaultComposerModels(), [models]);
    const selectedModel = composerModels.find(item => item.id === MODEL_ID) ?? composerModels[0] ?? null;
    const [draftOwner, setDraftOwner] = useState("");
    const [modelId, setModelId] = useState<AtelierComposerValues["modelId"]>(MODEL_ID);
    const [mode, setMode] = useState<AtelierComposerMode>("i2v");
    const [prompt, setPrompt] = useState("");
    const [duration, setDuration] = useState(() => durationDefault(selectedModel));
    const [resolution, setResolution] = useState(() => selectedModel?.params?.resolution?.default ?? "");
    const [ratio, setRatio] = useState(() => selectedModel?.params?.ratio?.default ?? "adaptive");
    const [seed, setSeed] = useState("");
    const [negativePrompt, setNegativePrompt] = useState("");
    const [promptExtend, setPromptExtend] = useState(() => composerBooleanDefault(selectedModel, "promptExtend"));
    const [watermark, setWatermark] = useState(() => composerBooleanDefault(selectedModel, "watermark"));
    const [audio, setAudio] = useState(() => composerBooleanDefault(selectedModel, "audio"));
    const [batchSize, setBatchSize] = useState(1);
    const [selectedFrameKey, setSelectedFrameKey] = useState("");
    const [selectedLastFrameKey, setSelectedLastFrameKey] = useState("");
    const [selectedReferenceMedia, setSelectedReferenceMedia] = useState<AtelierComposerValues["referenceMedia"] | null>(null);
    const [selectedFrameTouched, setSelectedFrameTouched] = useState(false);
    const [draftUnsupported, setDraftUnsupported] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [recoverableError, setRecoverableError] = useState<RecoverableLocalError | null>(null);
    const [reviewingCandidateId, setReviewingCandidateId] = useState<string | null>(null);
    const draftKey = `${ownerKey}:${selectedNode?.id ?? ""}`;
    const alive = useRef(true);
    const ownerRef = useRef(ownerKey);
    const draftKeyRef = useRef("");
    const editVersionRef = useRef(0);
    const refreshSeenRef = useRef({ ownerKey: draftKey, needsRefresh, revision: project.revision });

    const isVideoNode = selectedNode?.type === "video";
    const sources = useMemo<AtelierComposerSource[]>(() => {
        if (!selectedNode || !isVideoNode) return [];
        const attached = new Set(stringArray(selectedNode.data.reference_node_ids));
        return project.nodes
            .filter(node => node.id !== selectedNode.id && attached.has(node.id) && node.media_urls[0] && ["image", "video", "audio"].includes(node.type))
            .map(node => ({ nodeId: node.id, title: node.title || node.id, mediaUrl: node.media_urls[0], kind: node.type as "image" | "video" | "audio" }));
    }, [isVideoNode, project.nodes, selectedNode]);
    const firstFrames = sources.filter(source => source.kind === "image");
    const selectedFrame = firstFrames.find(frame => sourceKey(frame) === selectedFrameKey) ?? (!selectedFrameTouched ? firstFrames[0] ?? null : null);
    const missingSelectedFrame = selectedFrameTouched && !!selectedFrameKey && !selectedFrame;
    const attempts = useMemo(() => project.generation_attempts
        .filter(attempt => selectedNode && attempt.node_id === selectedNode.id)
        .sort((left, right) => right.updated_at - left.updated_at), [project.generation_attempts, selectedNode]);
    const candidates = selectedNode ? candidatesFor(selectedNode) : [];
    const pendingForNode = pendingGenerations.filter(item => selectedNode && item.nodeId === selectedNode.id);
    const pendingOutsideSelection = pendingGenerations.filter(item => !selectedNode || item.nodeId !== selectedNode.id);
    const blocked = disabled || isSaving || needsRefresh || busy !== null;
    const readBlocked = busy !== null;
    const candidateJudgments = useMemo(() => project.candidate_judgments ?? [], [project.candidate_judgments]);
    const reviewingHistory = useMemo(() => reviewingCandidateId && selectedNode
        ? candidateJudgments.filter(item => item.node_id === selectedNode.id && item.candidate_id === reviewingCandidateId)
        : [], [candidateJudgments, reviewingCandidateId, selectedNode]);

    useEffect(() => {
        draftKeyRef.current = draftKey;
    }, [draftKey]);

    const markEdited = useCallback(() => { editVersionRef.current += 1; }, []);

    useEffect(() => {
        alive.current = true;
        ownerRef.current = draftKey;
        try { loadPendingGenerations(); } catch (cause) { setError(cause instanceof Error ? cause.message : t("storageBlocked")); }
        return () => { alive.current = false; };
    }, [draftKey, loadPendingGenerations, t]);

    useEffect(() => {
        if (draftOwner === draftKey) return;
        const parsed = generationDraft(selectedNode);
        const draft = parsed.draft;
        const params = draft?.params as unknown as JsonRecord | null;
        const nextModelId = draft?.schema_version === 2 ? draft.model_id : MODEL_ID;
        const modelOption = composerModels.find(item => item.id === nextModelId) ?? composerModels[0] ?? null;
        setDraftOwner(draftKey);
        setModelId(nextModelId);
        setMode(draft?.schema_version === 2 ? draft.mode === "i2v" ? "firstlast" : draft.mode : "i2v");
        setPrompt(selectedNode?.prompt ?? "");
        setDuration(numberParam(params, "duration") ?? durationDefault(modelOption));
        setResolution(stringParam(params, "resolution") ?? modelOption?.params?.resolution?.default ?? "");
        setRatio(stringParam(params, "ratio") ?? modelOption?.params?.ratio?.default ?? "adaptive");
        setSeed(numberParam(params, "seed")?.toString() ?? "");
        setNegativePrompt(stringParam(params, "negative_prompt") ?? "");
        setPromptExtend(booleanParam(params, "prompt_extend") ?? composerBooleanDefault(modelOption, "promptExtend"));
        setWatermark(booleanParam(params, "watermark") ?? composerBooleanDefault(modelOption, "watermark"));
        setAudio(booleanParam(params, "audio") ?? composerBooleanDefault(modelOption, "audio"));
        setBatchSize(draft?.schema_version === 2 ? draft.batch_size : 1);
        const first = draft?.schema_version === 2 ? draft.media.find(item => item.role === "first_frame") : null;
        const last = draft?.schema_version === 2 ? draft.media.find(item => item.role === "last_frame") : null;
        setSelectedFrameKey(first ? `${first.node_id}:${first.media_url}` : "");
        setSelectedLastFrameKey(last ? `${last.node_id}:${last.media_url}` : "");
        setSelectedReferenceMedia(draft?.schema_version === 2 ? draft.media : null);
        setSelectedFrameTouched(!!first);
        setDraftUnsupported(parsed.unsupported);
        editVersionRef.current += 1;
        setNotice(null);
        setError(null);
        setRecoverableError(null);
    }, [composerModels, draftKey, draftOwner, selectedNode]);

    useEffect(() => {
        const previous = refreshSeenRef.current;
        if (recoverableError?.kind === "attempt-conflict" && recoverableError.ownerKey === draftKey) {
            const current = attempts.find(item => item.attempt_id === recoverableError.attemptId);
            if (current && current.attempt_revision > recoverableError.attemptRevision) {
                setError(null);
                setRecoverableError(null);
            }
        }
        if (recoverableError?.kind === "refresh-required"
            && recoverableError.ownerKey === draftKey
            && previous.ownerKey === draftKey
            && !needsRefresh
            && project.revision > recoverableError.projectRevision) {
            setError(null);
            setRecoverableError(null);
        }
        refreshSeenRef.current = { ownerKey: draftKey, needsRefresh, revision: project.revision };
    }, [attempts, draftKey, needsRefresh, project.revision, recoverableError]);

    useEffect(() => {
        const parsed = generationDraft(selectedNode).draft;
        const savedFirst = parsed?.schema_version === 2 ? parsed.media.find(item => item.role === "first_frame") : null;
        if (!savedFirst && !selectedFrameTouched && selectedFrame) {
            const key = sourceKey(selectedFrame);
            if (selectedFrameKey !== key) setSelectedFrameKey(key);
        }
    }, [selectedFrame, selectedFrameKey, selectedFrameTouched, selectedNode]);

    const pollIds = attempts.filter(attempt => !isTerminal(attempt)).map(attempt => attempt.attempt_id).join(",");
    useEffect(() => {
        if (!pollIds) return;
        let stopped = false;
        let running = false;
        let timer: ReturnType<typeof setTimeout>;
        const ids = pollIds.split(",");
        const poll = async () => {
            if (running) return;
            running = true;
            try {
                for (const id of ids) {
                    if (stopped) return;
                    await refreshGenerationAttempt(id);
                }
            } catch (cause) {
                if (!stopped && alive.current) {
                    setError(cause instanceof Error ? cause.message : t("actionFailed"));
                    setRecoverableError(null);
                }
            } finally {
                running = false;
                if (!stopped) timer = setTimeout(() => void poll(), POLL_MS);
            }
        };
        timer = setTimeout(() => void poll(), 1000);
        return () => { stopped = true; clearTimeout(timer); };
    }, [pollIds, refreshGenerationAttempt, t]);

    const run = useCallback(async (key: string, action: () => Promise<unknown>, success?: string, recover?: (cause: unknown, message: string) => RecoverableLocalError | null) => {
        const owner = ownerRef.current;
        setBusy(key);
        setError(null);
        setRecoverableError(null);
        setNotice(null);
        try {
            await action();
            if (alive.current && ownerRef.current === owner && success) setNotice(success);
        } catch (cause) {
            if (alive.current && ownerRef.current === owner) {
                const message = cause instanceof Error ? cause.message : t("actionFailed");
                setError(message);
                setRecoverableError(recover?.(cause, message) ?? null);
            }
        } finally {
            if (alive.current && ownerRef.current === owner) setBusy(current => current === key ? null : current);
        }
    }, [t]);

    const readback = (clientRequestId: string) => run(`readback:${clientRequestId}`, async () => {
        const ack = await readbackPendingGeneration(clientRequestId);
        if (!ack) setNotice(t("readbackMissing"));
    });

    const referenceMedia = selectedReferenceMedia ?? canonicalReferenceMediaFromSources(sources);
    const values: AtelierComposerValues = {
        modelId, mode, prompt, duration, resolution, ratio, seed, negativePrompt, promptExtend, watermark, audio, batchSize,
        firstSourceKey: selectedFrameKey || (selectedFrame ? sourceKey(selectedFrame) : ""), lastSourceKey: selectedLastFrameKey, referenceMedia, referenceMediaInitialized: selectedReferenceMedia !== null,
    };
    const ordered = isOrderedComposerModel(modelId);
    const missingLastFrame = ordered && mode === "firstlast" && !!selectedLastFrameKey && hasMissingFirstLastSelection(values, sources);
    const hasPendingForNode = pendingForNode.length > 0;
    const selectedModelAvailable = composerModels.some(item => item.id === modelId && !item.disabled);
    const canCreate = selectedModelAvailable && !!prompt.trim() && !draftUnsupported && !needsRefresh && !hasPendingForNode && (ordered
        ? mode === "t2v" || mode === "firstlast" && !!selectedFrame && !missingSelectedFrame && !missingLastFrame || mode === "r2v" && referenceMedia.length > 0 && !orderedMediaHasMissing(referenceMedia, sources)
        : !!selectedFrame && !missingSelectedFrame);

    const patchValues = (patch: Partial<AtelierComposerValues>) => {
        markEdited();
        if (patch.modelId !== undefined) setModelId(patch.modelId);
        if (patch.mode !== undefined) {
            setMode(patch.mode);
            if (patch.mode !== mode) setSelectedReferenceMedia(null);
        }
        if (patch.prompt !== undefined) setPrompt(patch.prompt);
        if (patch.duration !== undefined) setDuration(patch.duration);
        if (patch.resolution !== undefined) setResolution(patch.resolution);
        if (patch.ratio !== undefined) setRatio(patch.ratio);
        if (patch.seed !== undefined) setSeed(patch.seed.replace(/[^\d-]/g, ""));
        if (patch.negativePrompt !== undefined) setNegativePrompt(patch.negativePrompt);
        if (patch.promptExtend !== undefined) setPromptExtend(patch.promptExtend);
        if (patch.watermark !== undefined) setWatermark(patch.watermark);
        if (patch.audio !== undefined) setAudio(patch.audio);
        if (patch.batchSize !== undefined) setBatchSize(Math.max(1, Math.min(6, Math.trunc(patch.batchSize || 1))));
        if (patch.firstSourceKey !== undefined) { setSelectedFrameTouched(true); setSelectedFrameKey(patch.firstSourceKey); }
        if (patch.lastSourceKey !== undefined) setSelectedLastFrameKey(patch.lastSourceKey);
        if (patch.referenceMedia !== undefined) setSelectedReferenceMedia(patch.referenceMedia);
    };

    const currentStoreAllowsCreate = (identity: StoreCreateOwner): boolean => {
        const state = useAtelierStore.getState();
        const runtime = getRuntimeEnvironment();
        return !!runtime
            && runtime.key === identity.runtimeKey
            && runtime.generation === identity.runtimeGeneration
            && state.currentProject?.id === identity.projectId
            && state.selectedNodeId === identity.nodeId
            && !state.isLoading
            && !state.isSaving
            && !state.needsRefresh
            && !state.pendingGenerations?.some(item => item.nodeId === identity.nodeId);
    };

    const create = () => {
        if (!selectedNode || blocked || !canCreate || draftUnsupported || needsRefresh || hasPendingForNode) return;
        const runtime = getRuntimeEnvironment();
        if (!runtime) return;
        const identity: StoreCreateOwner = {
            ownerKey: draftKey,
            projectId: project.id,
            nodeId: selectedNode.id,
            runtimeKey: runtime.key,
            runtimeGeneration: runtime.generation,
        };
        if (!currentStoreAllowsCreate(identity)) return;
        const draft = buildAtelierGenerationDraft(values, sources);
        const request = buildAtelierGenerationRequest(values, sources);
        if (!request) return;
        void run("create", async () => {
            await saveGenerationDraft(identity.nodeId, draft);
            if (!alive.current
                || draftKeyRef.current !== identity.ownerKey
                || ownerRef.current !== identity.ownerKey
                || !currentStoreAllowsCreate(identity)) return;
            await createVideoCandidate(identity.nodeId, request);
        }, t("submitted"));
    };

    const saveDraft = () => {
        if (!selectedNode || draftUnsupported) return;
        void run("save-draft", () => saveGenerationDraft(selectedNode.id, buildAtelierGenerationDraft(values, sources)), t("draftSaved"));
    };

    const restoreDraft = () => {
        if (!selectedNode) return;
        const requestDraftKey = draftKey;
        const requestEditVersion = editVersionRef.current;
        void run("restore-draft", async () => {
            const modelOption = composerModels.find(item => item.id === modelId) ?? composerModels[0] ?? null;
            const restoredValues: AtelierComposerValues = {
                ...values,
                duration: durationDefault(modelOption),
                resolution: modelOption?.params?.resolution?.default ?? "",
                ratio: modelOption?.params?.ratio?.default ?? "adaptive",
                seed: "",
                negativePrompt: "",
                promptExtend: composerBooleanDefault(modelOption, "promptExtend"),
                watermark: composerBooleanDefault(modelOption, "watermark"),
                audio: composerBooleanDefault(modelOption, "audio"),
            };
            await saveGenerationDraft(selectedNode.id, ordered
                ? { contract_version: "ordered-media-v1", model_id: values.modelId as AtelierOrderedVideoModel, mode: values.mode === "firstlast" ? "i2v" : values.mode, prompt, params: {}, media: orderedMediaFromValues(values, sources), batch_size: values.batchSize }
                : { model_id: MODEL_ID, mode: "i2v", prompt, params: {} });
            if (alive.current && draftKeyRef.current === requestDraftKey && editVersionRef.current === requestEditVersion) {
                setDuration(restoredValues.duration);
                setResolution(restoredValues.resolution);
                setRatio(restoredValues.ratio);
                setSeed("");
                setNegativePrompt("");
                setPromptExtend(restoredValues.promptExtend);
                setWatermark(restoredValues.watermark);
                setAudio(restoredValues.audio);
                setDraftUnsupported(false);
            }
        }, t("draftRestored"));
    };

    return <section className="space-y-3 rounded-2xl border border-white/[0.08] bg-white/[0.04] p-3" aria-label={t("title")}>
        <div>
            <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">{t("eyebrow")}</div>
            <h3 className="mt-1 font-display text-sm font-semibold text-white/85">{t("title")}</h3>
            <p className="mt-1 text-xs leading-5 text-white/45">{t("scope")}</p>
        </div>

        {pendingOutsideSelection.length ? <div className="space-y-2 rounded-xl border border-amber-300/20 bg-amber-500/10 p-3 text-xs text-amber-100">
            <p>{t("projectPendingJournal")}</p>
            {pendingOutsideSelection.map(request => <button key={request.clientRequestId} type="button" className={panelButtonClass}
                disabled={readBlocked} onClick={() => void readback(request.clientRequestId)}>{t("readOriginal")}</button>)}
        </div> : null}

        {!selectedNode ? <p className="rounded-xl border border-white/[0.08] bg-black/25 p-3 text-xs text-white/45">{t("selectVideo")}</p>
            : !isVideoNode ? <p className="rounded-xl border border-white/[0.08] bg-black/25 p-3 text-xs text-white/45">{t("videoOnly")}</p>
                                : !composerModels.length ? <p className="rounded-xl border border-rose-300/20 bg-rose-500/10 p-3 text-xs text-rose-100">{t("modelUnavailable")}</p>
                    : <>
                        {!firstFrames.length && !isOrderedComposerModel(modelId) ? <p className="rounded-xl border border-amber-300/20 bg-amber-500/10 p-3 text-xs text-amber-100">{t("needsFirstFrame")}</p> : null}
                        {missingSelectedFrame || missingLastFrame ? <p className="rounded-xl border border-amber-300/20 bg-amber-500/10 p-3 text-xs text-amber-100">{t("missingSelectedFrame")}</p> : null}
                        {draftUnsupported ? <p className="rounded-xl border border-amber-300/20 bg-amber-500/10 p-3 text-xs text-amber-100">{t("draftUnsupported")}</p> : null}
                        <PinnedComposer values={values} models={composerModels} sources={sources} disabled={blocked} busy={busy}
                            missingSelectedFrame={missingSelectedFrame} missingLastFrame={missingLastFrame} canCreate={canCreate} canSave={!draftUnsupported}
                            targetRect={targetRect}
                            targetAvailableRect={targetAvailableRect}
                            targetLabel={selectedNode.title || selectedNode.id}
                            copy={{ prompt: t("prompt"), firstFrame: t("firstFrame"), duration: t("duration"), resolution: t("resolution"), advanced: t("advanced"),
                                seed: t("seed"), negativePrompt: t("negativePrompt"), promptExtend: t("promptExtend"), watermark: t("watermark"),
                                saveDraft: t("saveDraft"), restoreDraft: t("restoreDraft"), create: t("create"), working: t("working"),
                                missingSelectedFrameOption: t("missingSelectedFrameOption") }}
                            onChange={patchValues} onCreate={create} onSaveDraft={saveDraft} onRestoreDraft={restoreDraft} />
                    </>}

        <BranchSourceCard node={selectedNode} />

        {pendingForNode.length ? <div className="space-y-2 rounded-xl border border-amber-300/20 bg-amber-500/10 p-3 text-xs text-amber-100">
            <p>{t("pendingJournal")}</p>
            {pendingForNode.map(request => <button key={request.clientRequestId} type="button" className={panelButtonClass}
                disabled={readBlocked} onClick={() => void readback(request.clientRequestId)}>{t("readOriginal")}</button>)}
        </div> : null}

        {attempts.length ? <div className="space-y-2">
            <h4 className="text-xs font-medium text-white/65">{t("attempts")}</h4>
            {attempts.map(attempt => <div key={attempt.attempt_id} className="rounded-xl border border-white/[0.08] bg-black/20 p-3 text-xs text-white/55">
                <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-[11px] text-white/45">{attempt.attempt_id}</span>
                    <span>{t(attemptLabelKey(attempt))}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                    {canContinue(attempt) ? <button type="button" className={panelButtonClass} disabled={blocked}
                        onClick={() => void run(`continue:${attempt.attempt_id}`, () => continueGenerationAttempt(attempt.attempt_id), undefined,
                            cause => isStorageConflict(cause) ? { kind: "attempt-conflict", ownerKey: draftKey, attemptId: attempt.attempt_id, attemptRevision: attempt.attempt_revision } : null)}>{t("continue")}</button> : null}
                    {attempt.provider_execution.phase === "accepted" ? <button type="button" className={panelButtonClass} disabled={blocked}
                        onClick={() => void run(`cancel:${attempt.attempt_id}`, () => cancelGenerationAttempt(attempt.attempt_id))}>{t("cancel")}</button> : null}
                    {!isTerminal(attempt) ? <button type="button" className={panelButtonClass} disabled={blocked}
                        onClick={() => void run(`refresh:${attempt.attempt_id}`, () => refreshGenerationAttempt(attempt.attempt_id))}>{t("refresh")}</button> : null}
                </div>
            </div>)}
        </div> : null}

        {candidates.length && selectedNode ? <div className="space-y-2">
            <h4 className="text-xs font-medium text-white/65">{t("candidates")}</h4>
            {candidates.map(item => {
                const id = candidateId(item);
                const status = candidateStatus(item);
                const videoUrl = candidateVideoUrl(item);
                const terminal = isTerminalCandidateStatus(status);
                const importedVideo = isImportedVideoCandidate(project, item);
                const canSelect = (item.kind === "generation" && status === "completed" && !!videoUrl && !item.deleted)
                    || isSelectableImportedCandidate(project, item);
                const canHide = !item.deleted && terminal && supportsVideoCandidateVisibility(item);
                const canRestore = item.deleted && terminal && supportsVideoCandidateVisibility(item);
                const judgmentHistory: AtelierCandidateJudgment[] = candidateJudgments.filter(judgment => judgment.node_id === selectedNode.id && judgment.candidate_id === id);
                const latestJudgment = latestCandidateJudgment(judgmentHistory);
                const canReview = terminal && (item.kind === "generation" || importedVideo) && supportsVideoCandidateVisibility(item);
                const canBranch = !item.deleted && status === "completed" && (item.kind === "generation" || importedVideo) && !!videoUrl;
                return <div key={`${item.deleted ? "d" : "l"}:${id}`} className="rounded-xl border border-white/[0.08] bg-black/20 p-3 text-xs text-white/55">
                    <div className="flex items-center justify-between gap-3">
                        <span>{id}</span>
                        <span>{item.deleted ? t("hidden") : status ?? t("unknownStatus")}</span>
                    </div>
                    {latestJudgment ? <div className="mt-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-white/60">{t("judgment")}: {t(`judgment_${latestJudgment.verdict}`)}</div> : null}
                    {item.kind === "legacy" ? <div className="mt-2">{item.deleted && importedVideo ? t("hiddenImportedPreview") : canSelect ? t("importedPreview") : t("legacyPreview")}</div> : null}
                    {videoUrl ? <PreviewVideo src={videoUrl} alt={item.kind === "generation" ? `${selectedNode.title || selectedNode.id} candidate` : item.title} className="mt-2 aspect-video rounded-xl border border-white/[0.08]" /> : null}
                    <div className="mt-2 flex flex-wrap gap-2">
                        {canReview ? <button type="button" className={panelButtonClass} disabled={blocked} onClick={() => setReviewingCandidateId(id)}>{t("reviewTake")}</button> : null}
                        {canBranch ? <button type="button" className={panelButtonClass} disabled={blocked} onClick={() => void run(`branch:${id}`, () => branchFromCandidate(selectedNode.id, id), t("branchCreated"),
                            cause => isLostAckTransportError(cause) && useAtelierStore.getState().needsRefresh ? { kind: "refresh-required", ownerKey: draftKey, projectRevision: project.revision } : null)}>{t("createBranch")}</button> : null}
                        {canSelect ? <button type="button" className={panelButtonClass} disabled={blocked} onClick={() => void run(`select:${id}`, () => selectGenerationCandidate(selectedNode.id, id), t("selected"))}>{t("useTake")}</button> : null}
                        {canHide ? <button type="button" className={panelButtonClass} disabled={blocked} onClick={() => void run(`hide:${id}`, () => setGenerationCandidateVisibility(selectedNode.id, id, false), t("hiddenNotice"))}>{t("hideTake")}</button> : null}
                        {canRestore ? <button type="button" className={panelButtonClass} disabled={blocked} onClick={() => void run(`restore:${id}`, () => setGenerationCandidateVisibility(selectedNode.id, id, true), t("restoredNotice"))}>{t("restoreTake")}</button> : null}
                    </div>
                </div>;
            })}
        </div> : null}

        {notice ? <p role="status" className="rounded-xl border border-emerald-300/20 bg-emerald-500/10 p-3 text-xs text-emerald-100">{notice}</p> : null}
        {error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-500/10 p-3 text-xs text-rose-100">{error}</p> : null}
        {selectedNode && reviewingCandidateId ? <CandidateJudgmentDialog
            candidateId={reviewingCandidateId}
            mediaKind="video"
            history={reviewingHistory}
            open={true}
            readOnly={!!candidates.find(item => candidateId(item) === reviewingCandidateId)?.deleted}
            saving={busy === `judge:${reviewingCandidateId}`}
            onClose={() => setReviewingCandidateId(null)}
            onSave={async input => {
                const key = `judge:${reviewingCandidateId}`;
                setBusy(key);
                setError(null);
                setRecoverableError(null);
                setNotice(null);
                try {
                    await setCandidateJudgment(selectedNode.id, reviewingCandidateId, input);
                    if (alive.current) setNotice(t("reviewSaved"));
                    if (alive.current) setReviewingCandidateId(null);
                } finally {
                    if (alive.current) setBusy(current => current === key ? null : current);
                }
            }} /> : null}
    </section>;
}

export default GenerationPanel;
