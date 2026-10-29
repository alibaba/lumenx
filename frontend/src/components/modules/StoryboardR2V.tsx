"use client";

import { useState, useCallback, useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from "react";
import { motion } from "framer-motion";
import { Plus, Loader2, Sparkles, PanelBottomOpen, PanelBottomClose } from "lucide-react";
import StepPageHeader, { StepPill } from "@/components/shared/StepPageHeader";
import { useTranslations } from "next-intl";
import { useProjectStore, type Project } from "@/store/projectStore";
import { API_URL, api, crudApi, type VideoTask, type RefineSSEEvent } from "@/lib/api";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment, subscribeRuntimeEnvironment, type RuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { getAssetUrl } from "@/lib/utils";
import { selectedVariantUrl } from "@/lib/characterImage";
import { debugLog } from "@/lib/debugLog";
import type { BatchSummary } from "./storyboard-r2v/shot-panel/CandidatesSection";
import { getR2vRouteModelId, isR2vImageBased, VIDEO_I2V_MODELS, VIDEO_R2V_MODELS, DEFAULT_I2V_MODEL_ID, DEFAULT_R2V_MODEL_ID } from "@/lib/modelCatalog";
import ShotCard, { type ShotNode } from "./storyboard-r2v/ShotCard";
import { buildAssembledPrompt } from "./storyboard-r2v/buildAssembledPrompt";
import DialogueAudioRow from "./storyboard-r2v/DialogueAudioRow";
import StoryboardGenerateDialog from "./storyboard-r2v/StoryboardGenerateDialog";
import { toast } from "@/store/toastStore";
import { Wand2 } from "lucide-react";
import AssetDrawer from "./storyboard-r2v/AssetDrawer";
import { type VideoConfig, DEFAULT_VIDEO_CONFIG } from "./storyboard-r2v/VideoConfigModal";
import {
    setActiveT2IIndex,
    removeT2IImage,
    getActiveT2IImageUrl,
    frameToShotNode,
} from "./storyboard-r2v/shotNodeHelpers";
import { overridePanelSectionState } from "./storyboard-r2v/shot-panel/usePanelSectionState";
import ParamsSection, { type ParamsState } from "./storyboard-r2v/shot-panel/ParamsSection";
import T2ISubsection from "./storyboard-r2v/shot-panel/T2ISubsection";
import CandidatesSection from "./storyboard-r2v/shot-panel/CandidatesSection";
import CompareModal from "./storyboard-r2v/shot-panel/CompareModal";
import TaskQueueButton from "./storyboard-r2v/shot-panel/TaskQueueButton";
import TaskQueuePanel, { canContinueProviderTask } from "./storyboard-r2v/shot-panel/TaskQueuePanel";
import { GenerationBanner, type BannerState } from "./storyboard-r2v/GenerationBanner";
import { FrameReadbackError, clearFrameEditFailures, frameEditFailure, frameEditsPending, frameRefiningKey, trackFrameEdit, useFrameCommand } from '@/lib/useFrameCommand';
import { captureFrameEditRevision, enqueueFrameEdit, releaseFrameEditRevision, sendFrameRevisionEdit, type FrameEditRevision } from '@/lib/frameEditQueue';
import { dialogueAudioOperationKey } from '@/lib/dialogueAudioState';
import { selectCurrentVideoTask, getVideoTaskUrl, selectVerifiedAppliedDubUrlForTask } from '@/lib/dubSource';
import { confirmRawProjectAck, ProjectReadbackError, readProjectOnce } from '@/lib/projectReadback';
import { sameReadVersion } from '@/lib/frameCommandSnapshot';
import { useStudioAssetImageRequests, usesStudioQwenImageCore } from '@/lib/useStudioAssetImageRequests';
import StudioImageRequestResults from '@/components/shared/StudioImageRequestResults';
import { RefineBatchAbortedError, type RefineBatchComplete } from '@/lib/refineSse';
import { beginProviderVideoRequest, readProviderVideoRequest, resolveProviderVideoRequest, withProviderVideoRequestLock, ProviderVideoRequestError, type ProviderVideoRequestOwner, type ProviderVideoRequestResult } from "@/lib/providerVideoRequest";
import { buildProviderVideoTaskInput, isProviderVideoModel, parseProviderVideoTaskReceipt, ProviderVideoTaskEntryError, ORDERED_MEDIA_CONTRACT_VERSION, ORDERED_PROVIDER_VIDEO_MODELS, providerVideoTaskExpectationFromPayload, providerVideoTaskMatchesExpectation, assertProviderVideoTasksMatchPayload, type ProviderVideoTaskReceiptExpectation } from "@/lib/providerVideoTaskEntry";


type ApiErrorLike = { response?: { status?: number; data?: { code?: unknown; detail?: unknown } }; message?: string };
type ProviderPendingEntry = ProviderVideoRequestResult & { ownerKey: string };
type FrameImageRequests = ReturnType<typeof useStudioAssetImageRequests>;

/** Follow renderer priority, admitting zero or one deduplicated raw local reference. */
function isCoreStoryboardFrame(project: Project, frameId: string): boolean {
    const frame = project.frames.find(candidate => candidate.id === frameId);
    if (!frame) return false;
    const raw = (value: object, field: string): unknown => {
        const record = value as Record<string, unknown>, refs = record.media_refs;
        return refs && typeof refs === 'object' && Object.hasOwn(refs, field)
            ? (refs as Record<string, unknown>)[field] : record[field];
    };
    const selected = (asset?: { selected_id?: string | null; variants?: { id: string; url?: string }[] } | null) => {
        const variant = asset?.selected_id ? asset.variants?.find(value => value.id === asset.selected_id) : undefined;
        return variant?.url ? variant : undefined;
    };
    const references = new Set<unknown>();
    for (const id of frame.character_ids ?? []) {
        const character = project.characters.find(candidate => candidate.id === id);
        if (!character) return false;
        const variant = [character.three_view_asset, character.full_body_asset, character.headshot_asset]
            .map(selected).find(value => value !== undefined);
        if (variant) references.add(raw(variant, 'url'));
        else {
            const field = (['three_view_image_url', 'full_body_image_url', 'headshot_image_url', 'avatar_url', 'image_url'] as const)
                .find(name => character[name]);
            if (field) references.add(raw(character, field));
        }
    }
    if (frame.scene_id) {
        const scene = project.scenes.find(candidate => candidate.id === frame.scene_id);
        if (!scene) return false;
        const variant = selected(scene.image_asset);
        if (variant) references.add(raw(variant, 'url'));
        else if (scene.image_url) references.add(raw(scene, 'image_url'));
    }
    return references.size <= 1 && Array.from(references).every(url => {
        if (typeof url !== 'string' || !url || url.trim() !== url || /[\x00-\x1f\x7f]/.test(url) || url.startsWith('//')) return false;
        const drive = /^[a-z]:[\\/]/i.test(url);
        if (/^[a-z][a-z0-9+.-]*:/i.test(url) && !drive || url.split(/[\\/]/).includes('..')
            || url.includes('\\') && !drive) return false;
        return url.startsWith('/') || drive || /^(?:assets|storyboard|video|audio|export|uploads|playground|outputs?)\//.test(url);
    });
}

/** Each frame keeps the existing durable journal/hook; this component adds no request authority. */
function StoryboardFrameImageRequests({ projectId, frameId, readback, onBusy, children }: {
    projectId?: string; frameId: string;
    readback: (frameId: string, isCurrent: (snapshot: Project) => boolean) => Promise<void>;
    onBusy: (frameId: string, busy: boolean) => void;
    children: (requests: FrameImageRequests) => ReactNode;
}) {
    const boundReadback = useCallback((isCurrent: (snapshot: Project) => boolean) => readback(frameId, isCurrent), [readback, frameId]);
    const requests = useStudioAssetImageRequests({ kind: 'storyboard_frame', channel: 'image', open: !!projectId, projectId, assetId: frameId, readback: boundReadback });
    useEffect(() => { onBusy(frameId, requests.busy); return () => onBusy(frameId, false); }, [frameId, onBusy, requests.busy]);
    return children(requests);
}

const ORDERED_PROVIDER_VIDEO_MODEL_IDS = new Set<string>(ORDERED_PROVIDER_VIDEO_MODELS);

function isOrderedStudioProviderVideoModel(model: string | undefined | null): model is typeof ORDERED_PROVIDER_VIDEO_MODELS[number] {
    return typeof model === "string" && ORDERED_PROVIDER_VIDEO_MODEL_IDS.has(model);
}

function normalizeWan3StudioResolution(value: string | undefined): string | undefined {
    if (value === "480p") return "480P";
    if (value === "720p") return "720P";
    if (value === "1080p") return "1080P";
    return value;
}

function providerExpectationFromPending(
    pending: ProviderVideoRequestResult,
    projectId: string,
    frameId: string,
): ProviderVideoTaskReceiptExpectation {
    return providerVideoTaskExpectationFromPayload(pending.payload, {
        clientRequestId: pending.clientRequestId,
        projectId,
        frameId,
    });
}

function apiErrorLike(error: unknown): ApiErrorLike {
    return (error && typeof error === "object") ? error as ApiErrorLike : {};
}

const PROVIDER_CONTINUE_PHASE_RANK: Record<string, number> = {
    accepted: 0,
    submitting: 1,
    submitted: 2,
    observed: 3,
    completed: 4,
};

function providerContinuePhaseRank(phase: unknown): number | null {
    return typeof phase === "string" && Object.prototype.hasOwnProperty.call(PROVIDER_CONTINUE_PHASE_RANK, phase)
        ? PROVIDER_CONTINUE_PHASE_RANK[phase]
        : null;
}

export default function StoryboardR2V() {
    const projectId = useProjectStore(state => state.currentProject?.id);
    const environment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
    if (!environment) return null;
    return <StoryboardR2VSession key={JSON.stringify([projectId, environment.key, environment.generation])} />;
}

function StoryboardR2VSession() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const t = useTranslations("storyboardR2V");
    const tStep = useTranslations("stepHeader");
    const tCommand = useTranslations('frameCommands');
    const projectId = currentProject?.id;
    const audioOperation = dialogueAudioOperationKey(projectId ?? "");
    const audioBusy = useProjectStore(state => !!state.runningOps[audioOperation]);
    const command = useFrameCommand(projectId);
    const requiresReadback = command.error === 'savedReadFailed' || command.error === 'submitUnconfirmed';
    const { isBusy: isStructureBusy, setError: setCommandError } = command;
    const editSavesRef = useRef(new Set<Promise<unknown>>());
    const editFailuresRef = useRef(new Set<string>());
    const lastStructureRevisionRef = useRef(currentProject?.revision);
    const sessionMounted = useRef(true);
    const readSessionVersion = useRef(0);
    const videoPollInFlightRef = useRef(false);
    const autoSelectInFlightRef = useRef(new Set<string>());
    const autoSelectAttemptedRef = useRef(new Set<string>());
    useEffect(() => {
        sessionMounted.current = true;
        return () => { sessionMounted.current = false; };
    }, []);
    const [editEpoch, setEditEpoch] = useState(0);
    const [videoPollBlocked, setVideoPollBlocked] = useState<{ projectId: string; signature: string } | null>(null);

    useEffect(() => {
        readSessionVersion.current += 1;
        return () => { readSessionVersion.current += 1; };
    }, [currentProject?.id, currentProject?.readEnvironment]);

    const currentProjectReadGuard = useCallback((expectedProjectId: string, expectedEnvironment = getRuntimeEnvironment()) => {
        const version = readSessionVersion.current;
        const environment = expectedEnvironment;
        return () => {
            const current = useProjectStore.getState().currentProject;
            return !!environment && sessionMounted.current && readSessionVersion.current === version
                && current?.id === expectedProjectId && current.readEnvironment === environment.key
                && isCurrentRuntimeEnvironment(environment.key, environment.generation);
        };
    }, []);

    type FramePatch = Parameters<typeof api.updateFrame>[2];
    const frameDraftsRef = useRef(new Map<string, { patch: FramePatch; version: FrameEditRevision }>());
    const imageDraftEpochRef = useRef(new Map<string, number>());
    const imageRequestDraftEpochRef = useRef(new Map<string, number>());
    const imagePreparingRef = useRef(new Map<string, object>());
    const imageBusyRef = useRef(new Set<string>());
    const conflictRecoveryBusyRef = useRef(false);
    const [conflictRecovery, setConflictRecovery] = useState<'idle' | 'reading' | 'ready' | 'saving' | 'readFailed'>('idle');
    const rememberFrameDraft = useCallback((frameId: string, patch: FramePatch) => {
        if (!projectId || !Number.isSafeInteger(lastStructureRevisionRef.current)) return;
        imageDraftEpochRef.current.set(frameId, (imageDraftEpochRef.current.get(frameId) ?? 0) + 1);
        const previous = frameDraftsRef.current.get(frameId);
        if (previous) previous.patch = { ...previous.patch, ...patch };
        else frameDraftsRef.current.set(frameId, {
            patch: { ...patch }, version: captureFrameEditRevision(projectId, lastStructureRevisionRef.current!),
        });
    }, [projectId]);
    const acknowledgeFrameDraft = useCallback((frameId: string, patch: Record<string, unknown>) => {
        const draft = frameDraftsRef.current.get(frameId);
        if (!draft) return;
        for (const field of Object.keys(patch) as (keyof FramePatch)[]) {
            if (draft.patch[field] === patch[field]) delete draft.patch[field];
        }
        if (!Object.keys(draft.patch).length) {
            releaseFrameEditRevision(draft.version);
            frameDraftsRef.current.delete(frameId);
        }
    }, []);

    // Keep ready writes ordered even when their original editor has unmounted.
    const trackEdit = useCallback(<T extends Record<string, unknown>>(
        key: string, payload: T, send: (patch: T, expectedRevision: number) => Promise<unknown>,
        failed?: () => void, environment = requireActiveRuntimeEnvironment(),
    ) => {
        if (!projectId) return;
        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        const isCurrent = currentProjectReadGuard(projectId, environment);
        const frameId = key.slice(key.indexOf(':') + 1);
        const draft = frameDraftsRef.current.get(frameId);
        const write = trackFrameEdit(projectId, enqueueFrameEdit(projectId, key, payload, async patch => {
            if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                throw new Error('Runtime environment changed before frame edit dispatch');
            }
            const response = key.startsWith('workbench:') ? await send(patch, 0)
                : draft ? await sendFrameRevisionEdit(draft.version, revision => send(patch, revision))
                    : await Promise.reject(new Error('Frame edit has no captured revision'));
            if (key.startsWith('workbench:')) {
                // PATCH returns one Frame, not a Script. Once acknowledged,
                // only retry the read: repeating PATCH could duplicate a write.
                const receipt = response as Record<string, unknown> | null;
                if (!receipt || receipt.id !== key.slice('workbench:'.length)
                    || typeof receipt.updated_at !== 'number' || !Number.isFinite(receipt.updated_at)
                    || Object.keys(patch).some(field => field === 't2i_image_urls'
                        ? !Array.isArray(receipt[field]) || !(receipt[field] as unknown[]).every(url => typeof url === 'string')
                        : receipt[field] !== patch[field])) throw new Error('Invalid workbench acknowledgement');
                try {
                    if (!isCurrent()) throw new FrameReadbackError('Saved workbench editor is no longer current');
                    await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
                        if (!isCurrent()) return false;
                        const frame = snapshot.frames.find(candidate => candidate.id === receipt.id);
                        return frame?.updated_at === receipt.updated_at && Object.keys(patch).every(field =>
                            field === 't2i_image_urls'
                                ? Array.isArray(frame?.t2i_image_urls) && frame.t2i_image_urls.length === (receipt[field] as unknown[]).length
                                : frame?.[field] === receipt[field]);
                    } });
                } catch {
                    throw new FrameReadbackError('Saved workbench state could not be read');
                }
            } else {
                useProjectStore.getState().applyFrameCommandSnapshot(projectId, response);
                acknowledgeFrameDraft(frameId, patch);
            }
        })).catch(cause => {
            if (isCurrent()) {
                editFailuresRef.current.add(key);
                failed?.();
            }
            if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                setCommandError(cause instanceof FrameReadbackError ? 'savedReadFailed' : frameEditFailure(projectId) ?? 'failedEdits');
            }
        }).finally(() => {
            editSavesRef.current.delete(write);
            if (isCurrent()) setEditEpoch(epoch => epoch + 1);
        });
        editSavesRef.current.add(write);
        return write;
    }, [projectId, setCommandError, currentProjectReadGuard, acknowledgeFrameDraft]);

    // Derive shots from project frames. Workbench state (T2I 抽卡
    // history, last-active tab, batch count) now comes from backend-
    // persisted frame fields (added in commit 9149b06) instead of
    // React-only state, so cross-refresh / cross-device users see the
    // same panel state.
    const [shots, setShots] = useState<ShotNode[]>(() => {
        if (currentProject?.frames && currentProject.frames.length > 0) {
            const videoTasks: any[] = (currentProject as any).video_tasks ?? [];
            const defaultMode = currentProject.default_generation_mode === "i2v" ? "t2i_i2v" : "direct_r2v";
            return currentProject.frames.map((frame: any) => frameToShotNode(frame, videoTasks, defaultMode));
        }
        // Empty committed projects stay empty. The manual add CTA creates a
        // real backend frame before any frame-scoped operation can target it.
        return [];
    });

    // Global video config (with localStorage persistence for model selection)
    const [videoConfig, setVideoConfig] = useState<VideoConfig>(() => {
        const ls = typeof window !== 'undefined' ? window.localStorage : null;
        const savedI2v = ls?.getItem('storyboard-r2v-model') ?? null;
        const savedR2v = ls?.getItem('storyboard-r2v-r2v-model') ?? null;
        const projectI2v = currentProject?.model_settings?.i2v_model || DEFAULT_I2V_MODEL_ID;

        // I2V — defensive: a cached localStorage model id may have been
        // hidden or removed from the I2V list since it was last
        // selected (e.g. the user once picked `wan2.7-r2v` while it was
        // visible, the catalog later marked it hidden, and now the ID
        // lingers in their browser). Falling back to the default avoids
        // silently shipping the wrong model into the I2V flow.
        const i2vCandidate = savedI2v || projectI2v;
        const i2vOk = VIDEO_I2V_MODELS.find(m => m.id === i2vCandidate);
        const i2vModelId = i2vOk ? i2vCandidate : DEFAULT_I2V_MODEL_ID;
        if (!i2vOk && ls && savedI2v) {
            ls.removeItem('storyboard-r2v-model');
            debugLog.warn(
                "Studio",
                `Cached I2V model "${i2vCandidate}" is no longer in the visible I2V list; ` +
                `falling back to "${DEFAULT_I2V_MODEL_ID}".`,
            );
        }

        // R2V preference order:
        //   1. localStorage (user's last explicit pick — survives reloads)
        //   2. project.model_settings.r2v_model (project-level default,
        //      set in 生成设置 — Plan B "specialize" hierarchy)
        //   3. derived from i2v family (initial coherence on first mount)
        //   4. catalog DEFAULT_R2V_MODEL_ID
        // Each candidate is validated against VIDEO_R2V_MODELS so a
        // hidden id from any layer falls through cleanly.
        const projectR2v = currentProject?.model_settings?.r2v_model;
        const r2vDerived = getR2vRouteModelId(i2vModelId);
        const r2vCandidate = savedR2v || projectR2v || r2vDerived || DEFAULT_R2V_MODEL_ID;
        const r2vOk = VIDEO_R2V_MODELS.find(m => m.id === r2vCandidate);
        const r2vModelId = r2vOk ? r2vCandidate : (VIDEO_R2V_MODELS[0]?.id ?? DEFAULT_R2V_MODEL_ID);
        if (!r2vOk && ls && savedR2v) {
            ls.removeItem('storyboard-r2v-r2v-model');
        }

        const finalConfig = VIDEO_I2V_MODELS.find(m => m.id === i2vModelId);
        const dc = finalConfig?.duration;
        const defaultDuration = dc ? (dc.type === 'fixed' ? dc.value : dc.default) : 5;
        return {
            ...DEFAULT_VIDEO_CONFIG,
            model: i2vModelId,
            r2vModel: r2vModelId,
            duration: defaultDuration,
        };
    });

    // Modal & drawer state (configModalOpen retired with the gear; the
    // old VideoConfigModal mount is gone, replaced by per-shot
    // ParamsSection panels under each ShotCard. handleConfigChange is
    // also gone — model writes now flow through handleShotParamsChange
    // below, which mirrors them to localStorage.)
    const [drawerState, setDrawerState] = useState<{ isOpen: boolean; targetShotIndex: number | null }>({
        isOpen: false,
        targetShotIndex: null,
    });

    // Task-queue side panel state. Persisted across renders only; we
    // intentionally don't localStorage this — it's transient "I want
    // to peek at queue" UI affordance, not a saved layout preference.
    const [queueOpen, setQueueOpen] = useState(false);

    // Compare-mode selection: a Set of task ids the user shift-clicked
    // in any shot's candidate panel. Multi-shot compare is a future
    // feature; for now the same Set is shared across shots so user
    // can only effectively compare within one shot at a time. Cleared
    // on Compare modal close.
    const [compareSelectedIds, setCompareSelectedIds] = useState<Set<string>>(() => new Set());
    const [compareModalOpen, setCompareModalOpen] = useState(false);

    // Refs map for textareas (for asset insertion from drawer)
    const textareaRefs = useRef<Map<number, HTMLTextAreaElement>>(new Map());
    // Refs to each shot's outer wrapper so the task-queue panel can
    // jump-scroll the canvas to a specific frame.
    const shotWrapperRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());
    // Inline per-shot validation error messages (shown by ParamsSection
    // below the Generate CTA). Used for pre-flight failures like
    // "R2V needs reference images" that we catch before hitting the
    // backend, so the user gets immediate feedback instead of a
    // task that queues, fails, and shows up only in the diagnose log.
    const [shotErrors, setShotErrors] = useState<Record<string, string>>({});
    const [providerPendingByFrame, setProviderPendingByFrame] = useState<Record<string, ProviderPendingEntry>>({});
    const missingRefsMessage = useCallback(
        (modelLabel: string) =>
            t("missingRefs", { model: modelLabel }),
        [t],
    );

    // Per-shot seed override. The Seed advanced param doesn't live in
    // videoConfig (seeds are inherently per-generation; sharing one
    // across shots would defeat the "different shots = different
    // creative takes" expectation). Without this state the seed
    // input + dice button would appear to do nothing because
    // ParamsSection.set("seed", N) flowed up to handleShotParamsChange,
    // which silently dropped it, so the next paramsStateForShot()
    // call would always rebuild params.seed = undefined.
    //
    // `undefined` means "no explicit seed" (provider picks). Any
    // number means "use this exact seed" — same for all takes in a
    // batch (intentional: ×N with a fixed seed = N runs at that seed
    // for ablation testing). Users who want N varied takes leave it
    // empty.
    const [shotSeeds, setShotSeeds] = useState<Record<string, number | undefined>>({});

    // Per-shot batch count (the "抽卡 ×N" knob). Decoupled from
    // videoConfig because users typically pick the model + duration
    // once and vary count per shot. Keyed by shot.id so insert/move
    // don't shuffle counts onto the wrong shot. Seeded from backend
    // workbench_generate_count so user choices survive refresh.
    const [shotCounts, setShotCounts] = useState<Record<string, number>>(() => {
        const out: Record<string, number> = {};
        const frames: any[] = currentProject?.frames ?? [];
        for (const f of frames) {
            if (typeof f.workbench_generate_count === "number") {
                out[f.id] = f.workbench_generate_count;
            }
        }
        return out;
    });

    // Issue 16 — per-shot expand state (P plan). Default: all collapsed
    // (browse mode). Set persists per project to localStorage so coming back
    // to the project restores the user's last working layout.
    const expandStorageKey = currentProject ? `storyboard-r2v-expanded-${currentProject.id}` : null;
    const [expandedShots, setExpandedShots] = useState<Set<string>>(() => {
        if (typeof window === "undefined" || !expandStorageKey) return new Set();
        try {
            const raw = window.localStorage.getItem(expandStorageKey);
            if (raw) {
                const arr = JSON.parse(raw);
                if (Array.isArray(arr)) return new Set(arr.filter(x => typeof x === "string"));
            }
        } catch { /* corrupt localStorage value — ignore */ }
        return new Set();
    });
    // Persist on change.
    useEffect(() => {
        if (typeof window === "undefined" || !expandStorageKey) return;
        try {
            window.localStorage.setItem(expandStorageKey, JSON.stringify(Array.from(expandedShots)));
        } catch { /* quota exceeded — ignore */ }
    }, [expandedShots, expandStorageKey]);

    const toggleShotExpanded = useCallback((shotId: string) => {
        setExpandedShots(prev => {
            const next = new Set(prev);
            if (next.has(shotId)) next.delete(shotId);
            else next.add(shotId);
            return next;
        });
    }, []);
    const expandAllShots = useCallback(() => {
        const ids = shots.map(s => s.id);
        // Force every inner section open — overrides each shot's sticky
        // preference. Section keys must match what ParamsSection /
        // CandidatesSection register inside their SectionShells.
        overridePanelSectionState(ids, ["params", "candidates"], true);
        setExpandedShots(new Set(ids));
    }, [shots]);
    const collapseAllShots = useCallback(() => {
        // Don't reset section preferences here — sticky memory should
        // survive a global collapse so re-expanding a shot returns to
        // the user's chosen drawer state.
        setExpandedShots(new Set());
    }, []);

    // Debounced backend writer for workbench state. Coalesces rapid
    // changes (e.g. user clicking through T2I thumbs) into one PATCH
    // per shot per second. Per-shot map ensures one shot's pending
    // write doesn't get overwritten by another's.
    const workbenchPendingRef = useRef<Map<string, {
        timer: number;
        patch: Parameters<typeof api.updateFrameWorkbench>[2];
        environment: RuntimeEnvironment;
    }>>(new Map());
    const persistWorkbench = useCallback((
        shotId: string,
        patch: Parameters<typeof api.updateFrameWorkbench>[2],
    ) => {
        if (!projectId || isStructureBusy() || frameEditFailure(projectId)) return;
        const environment = requireActiveRuntimeEnvironment();
        // Synthetic shot id (not yet materialized on backend) — skip; the
        // workbench state will be re-applied after createFrame swaps the id.
        if (shotId.startsWith("shot_")) return;
        imageDraftEpochRef.current.set(shotId, (imageDraftEpochRef.current.get(shotId) ?? 0) + 1);
        const map = workbenchPendingRef.current;
        const existing = map.get(shotId);
        const merged = { ...(existing?.patch ?? {}), ...patch };
        if (existing) {
            window.clearTimeout(existing.timer);
        }
        const timer = window.setTimeout(() => {
            map.delete(shotId);
            trackEdit(`workbench:${shotId}`, merged, patch => api.updateFrameWorkbench(projectId, shotId, patch), () => {
                if (!map.has(shotId)) map.set(shotId, { timer: 0, patch: merged, environment });
            }, environment);
        }, 1000);
        map.set(shotId, { timer, patch: merged, environment });
    }, [projectId, isStructureBusy, trackEdit]);

    // Prompt edits hit a different endpoint (POST /frames/update with
    // visual_description) — debounced separately from workbench so a
    // user typing fast doesn't push 6 PATCH /workbench every keystroke.
    const promptPendingRef = useRef<Map<string, { timer: number; prompt: string; environment: RuntimeEnvironment }>>(new Map());
    const persistPrompt = useCallback((shotId: string, prompt: string) => {
        if (!projectId || isStructureBusy()) return;
        const environment = requireActiveRuntimeEnvironment();
        if (shotId.startsWith("shot_")) return;
        rememberFrameDraft(shotId, { visual_description: prompt });
        if (frameEditFailure(projectId)) return;
        const map = promptPendingRef.current;
        const existing = map.get(shotId);
        if (existing) window.clearTimeout(existing.timer);
        const timer = window.setTimeout(() => {
            map.delete(shotId);
            trackEdit(`prompt:${shotId}`, { visual_description: prompt }, (patch, revision) => api.updateFrame(projectId, shotId, patch, revision), undefined, environment);
        }, 800);
        map.set(shotId, { timer, prompt, environment });
    }, [projectId, isStructureBusy, trackEdit, rememberFrameDraft]);

    // Flush all pending writes on unmount (e.g. user switches step tab)
    // so the last keystroke / param change isn't stranded in the debounce
    // window. Ready workbench, prompt and field edits share the project queue.
    useEffect(() => {
        const wbMap = workbenchPendingRef.current;
        const pMap = promptPendingRef.current;
        const fMap = fieldPendingRef.current;
        const drafts = frameDraftsRef.current;
        const saves = editSavesRef.current;
        return () => {
            if (!projectId) return;
            for (const [shotId, entry] of Array.from(wbMap.entries())) {
                window.clearTimeout(entry.timer);
                if (!entry.timer) continue; // Failed drafts are not an automatic retry on navigation.
                trackEdit(`workbench:${shotId}`, entry.patch, patch => api.updateFrameWorkbench(projectId, shotId, patch), undefined, entry.environment);
            }
            wbMap.clear();
            for (const [shotId, entry] of Array.from(pMap.entries())) {
                window.clearTimeout(entry.timer);
                trackEdit(`prompt:${shotId}`, { visual_description: entry.prompt }, (patch, revision) => api.updateFrame(projectId, shotId, patch, revision), undefined, entry.environment);
            }
            pMap.clear();
            for (const [shotId, entry] of Array.from(fMap.entries())) {
                window.clearTimeout(entry.timer);
                if (!entry.timer) continue;
                trackEdit(`field:${shotId}`, entry.fields, (patch, revision) => api.updateFrame(projectId, shotId, patch, revision), undefined, entry.environment);
            }
            fMap.clear();
            const versions = Array.from(drafts.values(), draft => draft.version);
            void Promise.allSettled(Array.from(saves)).then(() => versions.forEach(releaseFrameEditRevision));
        };
    }, [projectId, trackEdit]);

    // beforeunload guard: warn when structured field edits are pending
    useEffect(() => {
        const handler = (e: BeforeUnloadEvent) => {
            if (fieldPendingRef.current.size || promptPendingRef.current.size || workbenchPendingRef.current.size
                || editSavesRef.current.size || editFailuresRef.current.size || frameDraftsRef.current.size) {
                e.preventDefault();
            }
        };
        window.addEventListener("beforeunload", handler);
        return () => window.removeEventListener("beforeunload", handler);
    }, []);

    const characters = currentProject?.characters || [];
    const scenes = currentProject?.scenes || [];
    const props = currentProject?.props || [];

    // PR-3 followup · LLM storyboard generation. State + handler live at
    // the StoryboardR2V level (not in a sub-component) because the toast
    // lifecycle survives the dialog closing and we need the parent to
    // setShots() when the new frames come back.
    const [genDialogOpen, setGenDialogOpen] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [bannerState, setBannerState] = useState<BannerState>(
        () => (currentProject?.frames?.length ?? 0) > 0 ? "summary" : "idle"
    );
    const [refineProgress, setRefineProgress] = useState<{ current: number; total: number } | null>(null);
    const [dialogueProgress, setDialogueProgress] = useState<{ current: number; total: number } | null>(null);

    const PHASE1_CAPTIONS = useMemo(() => [
        "正在分析剧本结构…",
        "识别场景切换点…",
        "拆分镜头与动作…",
        "琢磨每帧的构图和节奏…",
        "快了，安排景别和运镜…",
        "最后润色一下…",
    ], []);

    const bannerSummary = useMemo(() => {
        if (!currentProject?.frames?.length) return null;
        const frames = currentProject.frames as any[];
        const frameCount = frames.length;
        const withDialogue = frames.filter((f: any) =>
            f.dialogue_structured?.line || f.dialogue
        );
        const dialogueReady = withDialogue.length;
        // Voice resolution belongs to the backend; local first-character guesses
        // cannot classify frames which use the speaker-name fallback.
        const dialogueMissing = 0;
        return { frameCount, dialogueReady, dialogueMissing };
    }, [currentProject?.frames, (currentProject as any)?.characters]);

    const settleAudioEdits = useCallback(async () => {
        if (!projectId || !sessionMounted.current || useProjectStore.getState().currentProject?.id !== projectId) throw new Error(tCommand("pendingEdits"));
        if (isStructureBusy()) throw new Error(tCommand("pendingEdits"));
        for (const [id, entry] of Array.from(workbenchPendingRef.current.entries())) {
            if (!entry.timer) continue;
            window.clearTimeout(entry.timer); workbenchPendingRef.current.delete(id);
            trackEdit(`workbench:${id}`, entry.patch, patch => api.updateFrameWorkbench(projectId, id, patch), undefined, entry.environment);
        }
        for (const [id, entry] of Array.from(promptPendingRef.current.entries())) {
            window.clearTimeout(entry.timer); promptPendingRef.current.delete(id);
            trackEdit(`prompt:${id}`, { visual_description: entry.prompt }, (patch, revision) => api.updateFrame(projectId, id, patch, revision), undefined, entry.environment);
        }
        for (const [id, entry] of Array.from(fieldPendingRef.current.entries())) {
            if (!entry.timer) continue;
            window.clearTimeout(entry.timer); fieldPendingRef.current.delete(id);
            trackEdit(`field:${id}`, entry.fields, (patch, revision) => api.updateFrame(projectId, id, patch, revision), undefined, entry.environment);
        }
        await Promise.all(Array.from(editSavesRef.current));
        const failure = frameEditFailure(projectId);
        if (failure || editFailuresRef.current.size) throw new Error(tCommand(failure ?? "failedEdits"));
        if (frameEditsPending(projectId) || workbenchPendingRef.current.size || promptPendingRef.current.size || fieldPendingRef.current.size || frameDraftsRef.current.size) {
            throw new Error(tCommand("pendingEdits"));
        }
    }, [projectId, isStructureBusy, trackEdit, tCommand]);

    const handleBatchDialogue = useCallback(async () => {
        if (!projectId || useProjectStore.getState().runningOps[audioOperation]) return;
        const store = useProjectStore.getState();
        const environment = requireActiveRuntimeEnvironment();
        const active = () => sessionMounted.current && store.currentProject?.id === projectId
            && useProjectStore.getState().currentProject?.id === projectId;
        store.setRunningOp(audioOperation, true);
        setBannerState("dialogue");
        setDialogueProgress({ current: 0, total: bannerSummary?.dialogueReady ?? 0 });
        try {
            await settleAudioEdits();
            if (!active()) return;
            const result = await api.generateDialogueAudioBatch(projectId);
            if (!active()) return;
            await confirmRawProjectAck(projectId, result);
            if (!active()) return;
            const stats = result._batch_stats;
            if (!stats || ![stats.failed, stats.generated, stats.no_voice, stats.skipped].every(value => Number.isSafeInteger(value) && value >= 0)) {
                throw new Error(t("batchDialogueFailed"));
            }
            if (stats.failed > 0) toast.warning(`对白生成完成：${stats.generated} 条成功，${stats.failed} 条失败`);
            else if (stats.generated > 0) toast.success(`已生成 ${stats.generated} 条对白音频`);
            else if (stats.no_voice > 0 && stats.skipped === 0) toast.warning(`${stats.no_voice} 条对白的角色尚未绑定语音`);
            else if (stats.skipped > 0) toast.success(t("dialogueAllUpToDate"));
            else toast.warning("未找到可生成的对白");
        } catch (error: unknown) {
            if (active()) {
                debugLog.error("Studio", "batch dialogue audio failed", error);
                toast.error(error instanceof ProjectReadbackError ? t("batchDialogueReadFailed")
                    : error instanceof Error ? error.message : t("batchDialogueFailed"));
            }
        } finally {
            if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                store.setRunningOp(audioOperation, false);
            }
            if (active()) { setBannerState("summary"); setDialogueProgress(null); }
        }
    }, [projectId, audioOperation, bannerSummary?.dialogueReady, settleAudioEdits, t]);

    const applyLocalSnapshot = useCallback((snapshot: NonNullable<typeof currentProject>) => {
        const defaultMode = snapshot.default_generation_mode === "i2v" ? "t2i_i2v" : "direct_r2v";
        setShots(snapshot.frames.map(frame => frameToShotNode(frame, snapshot.video_tasks ?? [], defaultMode)));
        setShotCounts(Object.fromEntries(snapshot.frames.map(frame => [frame.id, frame.workbench_generate_count ?? 1])));
        const ids = new Set(snapshot.frames.map(frame => frame.id));
        setExpandedShots(previous => new Set(Array.from(previous).filter(id => ids.has(id))));
        lastStructureRevisionRef.current = snapshot.revision;
    }, []);

    const handleSmartGenerate = useCallback(async () => {
        if (!currentProject?.id || isStructureBusy()) return;
        const projectId = currentProject.id;
        const failure = frameEditFailure(projectId);
        if (failure || editFailuresRef.current.size) { setCommandError(failure ?? 'failedEdits'); return; }
        if (workbenchPendingRef.current.size || promptPendingRef.current.size || fieldPendingRef.current.size
            || editSavesRef.current.size || frameEditsPending(projectId)) {
            setCommandError('pendingEdits'); return;
        }
        const scriptText = currentProject.originalText;
        if (!scriptText.trim()) { toast.warning(t("genToastNoScript")); return; }
        const environment = requireActiveRuntimeEnvironment();
        const isCurrent = () => isCurrentRuntimeEnvironment(environment.key, environment.generation);
        const store = useProjectStore.getState();
        const operation = frameRefiningKey(projectId, environment.key);
        // Reserve before awaiting anything; this also protects another mount.
        store.setRunningOp(operation, true);
        setCommandError(null);
        setGenerating(true);
        setBannerState("phase1");
        const context = { projectId, projectTitle: currentProject.title };
        const showCommitted = () => {
            const selected = useProjectStore.getState().currentProject;
            if (isCurrent() && sessionMounted.current && selected?.id === projectId) applyLocalSnapshot(selected);
        };
        let readFailed = false;
        try {
            const updated = await api.analyzeToStoryboard(projectId, scriptText);
            if (!isCurrent()) return;
            store.applyFrameCommandSnapshot(projectId, updated);
            showCommitted();
            const newFrameCount = updated.frames.length;
            let result: RefineBatchComplete | undefined;
            if (newFrameCount > 0) {
                if (sessionMounted.current) {
                    setBannerState("phase2");
                    setRefineProgress({ current: 0, total: newFrameCount });
                }
                let streamError: unknown;
                let streamFailed = false;
                try {
                    result = await api.refineBatchFrames(projectId, (event: RefineSSEEvent) => {
                        if (isCurrent() && sessionMounted.current && event.type === "frame_refine_start") {
                            setRefineProgress({ current: event.frame_index + 1, total: event.total });
                        }
                    });
                } catch (error) { streamError = error; streamFailed = true; }
                if (!isCurrent()) return;
                // Complete, abort and interrupted streams may all have committed
                // some frames. Refresh once without resubmitting any generation.
                try {
                    await readProjectOnce(projectId, undefined, { isCurrent });
                    showCommitted();
                } catch {
                    readFailed = true;
                    if (!streamFailed) {
                        streamError = new FrameReadbackError(t('refineReadFailed'));
                        streamFailed = true;
                    }
                }
                if (streamFailed) throw streamError;
            }
            if (!isCurrent()) return;
            if (result && result.failed > 0) {
                toast.warning(t('refinePartial', { success: result.success, failed: result.failed }), context);
            } else {
                toast.success(t('genToastDone', { count: result?.success ?? newFrameCount }), context);
            }
        } catch (error: unknown) {
            if (!isCurrent()) return;
            if (error instanceof RefineBatchAbortedError) {
                toast.error(t('refineAborted', { success: error.success, failed: error.failed }), {
                    ...context,
                    body: t(`refineAbortReasons.${error.code}`) + '：' + error.message
                        + (readFailed ? '；' + t('refineReadFailed') : ''),
                });
            } else {
                const detail = error instanceof Error ? error.message : t('genToastErrUnknown');
                toast.error(t('genToastErr') + ': ' + detail.slice(0, 200), {
                    ...context, ...(readFailed ? { body: t('refineReadFailed') } : {}),
                });
            }
        } finally {
            if (isCurrent()) {
                store.setRunningOp(operation, false);
                if (sessionMounted.current) {
                    setGenerating(false);
                    setRefineProgress(null);
                    const selected = useProjectStore.getState().currentProject;
                    setBannerState(selected?.id === projectId && selected.frames.length ? 'summary' : 'idle');
                }
            }
        }
    }, [currentProject, isStructureBusy, setCommandError, applyLocalSnapshot, t]);

    const handleRefineFrame = useCallback(async (frameId: string) => {
        if (!currentProject?.id || requiresReadback) return;
        const projectId = currentProject.id;
        const environment = requireActiveRuntimeEnvironment();
        const operation = frameRefiningKey(projectId, environment.key);
        const store = useProjectStore.getState();
        const isCurrent = currentProjectReadGuard(projectId, environment);
        if (!isCurrent() || store.runningOps[operation] || isStructureBusy()) return;
        if (workbenchPendingRef.current.size || promptPendingRef.current.size || fieldPendingRef.current.size
            || editSavesRef.current.size || editFailuresRef.current.size || frameEditsPending(projectId)) {
            setCommandError('pendingEdits'); return;
        }
        store.setRunningOp(operation, true);
        setCommandError(null);
        let saved = false;
        try {
            const response: { id?: unknown; updated_at?: unknown; assembled_prompt?: unknown } = await api.refineSingleFrame(projectId, frameId);
            if (!isCurrent()) return;
            if (!response || response.id !== frameId || typeof response.updated_at !== 'number'
                || !Number.isFinite(response.updated_at) || typeof response.assembled_prompt !== 'string') {
                throw new Error('Invalid refined frame acknowledgement');
            }
            saved = true;
            // This endpoint returns a Frame. Match its persisted receipt before
            // accepting an enriched project; it carries no project revision.
            await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
                const frame = snapshot.frames.find(candidate => candidate.id === frameId);
                return isCurrent() && frame?.updated_at === response.updated_at
                    && frame?.assembled_prompt === response.assembled_prompt;
            } });
            if (!isCurrent()) return;
            const confirmed = useProjectStore.getState().currentProject;
            if (!confirmed?.frames.some(frame => frame.id === frameId)) throw new ProjectReadbackError('Refined frame is no longer present');
            applyLocalSnapshot(confirmed);
            toast.success(t("refineDoneToast"));
        } catch (err) {
            if (isCurrent()) {
                setCommandError(saved || err instanceof ProjectReadbackError ? 'savedReadFailed' : 'unconfirmed');
                toast.error(t("refineFailedToast"));
            }
            debugLog.warn("Studio", "single frame refine failed", err);
        } finally {
            if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                store.setRunningOp(operation, false);
            }
        }
    }, [currentProject?.id, requiresReadback, currentProjectReadGuard, isStructureBusy, setCommandError, applyLocalSnapshot, t]);

    const updateInlineDialogue = useCallback(async (frameId: string, text: string) => {
        if (!projectId || requiresReadback) return;
        const isCurrent = currentProjectReadGuard(projectId);
        if (!isCurrent() || isStructureBusy()) return;
        const key = `field:${frameId}`;
        rememberFrameDraft(frameId, { dialogue: text });
        const draft = frameDraftsRef.current.get(frameId);
        if (!draft) throw new Error(tCommand('failedEdits'));
        const write = trackFrameEdit(projectId, enqueueFrameEdit(projectId, key, { dialogue: text }, async patch => {
            const response = await sendFrameRevisionEdit(draft.version, revision => api.updateFrame(projectId, frameId, patch, revision));
            acknowledgeFrameDraft(frameId, patch);
            if (!isCurrent()) { useProjectStore.getState().applyFrameCommandSnapshot(projectId, response); return; }
            try { await confirmRawProjectAck(projectId, response, { isCurrent }); }
            catch (error) {
                if (error instanceof ProjectReadbackError) throw new FrameReadbackError(error.message);
                throw error;
            }
        })).catch(cause => {
            editFailuresRef.current.add(key);
            if (isCurrent()) setCommandError(cause instanceof FrameReadbackError ? 'savedReadFailed' : frameEditFailure(projectId) ?? 'failedEdits');
            throw cause;
        }).finally(() => {
            editSavesRef.current.delete(write);
            setEditEpoch(epoch => epoch + 1);
        });
        editSavesRef.current.add(write);
        await write;
    }, [projectId, requiresReadback, currentProjectReadGuard, isStructureBusy, setCommandError, rememberFrameDraft, acknowledgeFrameDraft, tCommand]);

    const hasUnsavedEdits = useCallback(() => workbenchPendingRef.current.size > 0
        || promptPendingRef.current.size > 0 || fieldPendingRef.current.size > 0
        || editSavesRef.current.size > 0 || editFailuresRef.current.size > 0 || frameDraftsRef.current.size > 0
        || (projectId ? frameEditsPending(projectId) : false), [projectId]);
    const generationBlockedState = useCallback(() => {
        const failure = projectId ? frameEditFailure(projectId) : null;
        if (failure || editFailuresRef.current.size > 0 || command.editsFailed) {
            return { message: t('generationSettingsBlocked'), busy: false };
        }
        if (hasUnsavedEdits()) return { message: t('savingGenerationSettings'), busy: true };
        return undefined;
    }, [projectId, command.editsFailed, hasUnsavedEdits, t]);

    const canChangeStructure = useCallback(() => {
        if (!projectId || !currentProjectReadGuard(projectId)() || isStructureBusy()) return false;
        if (editFailuresRef.current.size || command.editsFailed) { setCommandError(currentProject ? frameEditFailure(currentProject.id) ?? 'failedEdits' : 'failedEdits'); return false; }
        if (hasUnsavedEdits()) { setCommandError('pendingEdits'); return false; }
        const hasActiveVideoSubmit = Object.entries(useProjectStore.getState().runningOps)
            .some(([key, active]) => active && key.startsWith(`video-submit:${encodeURIComponent(requireActiveRuntimeEnvironment().key)}:${encodeURIComponent(projectId)}:`));
        if (generating || refineProgress || dialogueProgress || hasActiveVideoSubmit || imagePreparingRef.current.size || imageBusyRef.current.size
            || shots.some(shot => shot.id.startsWith('shot_')
                || ['pending', 'processing'].includes(shot.videoStatus ?? '')
                || ['pending', 'processing'].includes(shot.t2iStatus ?? ''))
            || currentProject?.video_tasks?.some(task => ['pending', 'processing'].includes(task.status))) {
            setCommandError('unsettled'); return false;
        }
        return true;
    }, [projectId, currentProjectReadGuard, isStructureBusy, command.editsFailed, setCommandError, currentProject, hasUnsavedEdits, generating, refineProgress, dialogueProgress, shots]);



    // ────────────────────────────────────────────────────────────────────
    // New-shot creation is a structural command. No synthetic local frame is
    // rendered before the backend returns a committed frame id.
    // ────────────────────────────────────────────────────────────────────

    // Add a new shot after the given index
    const addShot = useCallback(async (afterIndex: number) => {
        if (!currentProject || !canChangeStructure()) return;
        const insertAt = Math.max(0, afterIndex + 1);
        const snapshot = await command.run(() => crudApi.createFrame(currentProject.id, {
            scene_id: "",
            action_description: "",
            insert_at: insertAt,
        }));
        if (!snapshot) return;
        applyLocalSnapshot(snapshot);
        const created = snapshot.frames[Math.min(insertAt, snapshot.frames.length - 1)];
        if (created?.id) {
            setExpandedShots(prev => {
                const next = new Set(prev);
                next.add(created.id);
                return next;
            });
        }
    }, [currentProject, command, applyLocalSnapshot, canChangeStructure]);

    const deleteShot = async (index: number) => {
        const target = shots[index];
        if (!target || !currentProject || !canChangeStructure()) return;
        const snapshot = await command.run(() => crudApi.deleteFrame(currentProject.id, target.id));
        if (snapshot) applyLocalSnapshot(snapshot);
    };

    const moveShot = async (index: number, direction: 'up' | 'down') => {
        const targetIndex = direction === 'up' ? index - 1 : index + 1;
        if (!currentProject || targetIndex < 0 || targetIndex >= shots.length || !canChangeStructure()) return;
        const ids = shots.map(shot => shot.id);
        [ids[index], ids[targetIndex]] = [ids[targetIndex], ids[index]];
        const snapshot = await command.run(() => crudApi.reorderFrames(currentProject.id, ids));
        if (snapshot) applyLocalSnapshot(snapshot);
    };

    const duplicateShot = async (index: number) => {
        const source = shots[index];
        if (!source || !currentProject || !canChangeStructure()) return;
        const snapshot = await command.run(() => crudApi.copyFrame(currentProject.id, source.id, index + 1));
        // The backend copies media references and pin state. Render its full
        // result instead of inventing a temporary intent-only copy.
        if (snapshot) applyLocalSnapshot(snapshot);
    };

    // Update shot prompt — local immediate + debounced backend write
    const updatePrompt = useCallback((index: number, prompt: string) => {
        if (isStructureBusy() || (projectId && frameEditFailure(projectId) === 'savedReadFailed')) return;
        setShots(prev => prev.map((s, i) => {
            if (i !== index) return s;
            persistPrompt(s.id, prompt);
            return { ...s, prompt };
        }));
    }, [persistPrompt, projectId, isStructureBusy]);

    // Set shot tab mode + persist so the user's last-active tab
    // survives refresh.
    const setTabMode = useCallback((index: number, mode: "t2i_i2v" | "direct_r2v") => {
        if (isStructureBusy() || (projectId && frameEditFailure(projectId) === 'savedReadFailed')) return;
        setShots(prev => prev.map((s, i) => {
            if (i !== index) return s;
            persistWorkbench(s.id, { workbench_tab_mode: mode });
            return { ...s, tabMode: mode };
        }));
    }, [persistWorkbench, projectId, isStructureBusy]);

    // Structured field updates — local immediate + debounce 3s auto-save
    const fieldPendingRef = useRef<Map<string, { timer: number; fields: Parameters<typeof api.updateFrame>[2]; environment: RuntimeEnvironment }>>(new Map());
    const handleUpdateField = useCallback((index: number, field: string, value: string | number | null) => {
        if (isStructureBusy() || (projectId && frameEditFailure(projectId) === 'savedReadFailed')) return;
        const environment = requireActiveRuntimeEnvironment();
        setShots(prev => prev.map((s, i) => {
            if (i !== index) return s;
            if (field === "duration") return { ...s, duration: typeof value === "number" ? value : null };
            if (field === "shotSize") return { ...s, shotSize: typeof value === "string" ? value : null };
            if (field === "cameraAngle") return { ...s, cameraAngle: typeof value === "string" ? value : null };
            if (field === "cameraMovement") {
                const desc = typeof value === "string" ? value : "固定镜头";
                return {
                    ...s,
                    cameraMovementStructured: {
                        primary: desc,
                        speed: s.cameraMovementStructured?.speed ?? "normal",
                        description: desc,
                        secondary: s.cameraMovementStructured?.secondary ?? null,
                    },
                };
            }
            if (field === "transitionHint") return { ...s, transitionHint: typeof value === "string" ? value : null };
            return s;
        }));
        // Debounce 3s persist to backend
        const shotId = shots[index]?.id;
        if (!shotId || shotId.startsWith("shot_") || !projectId) return;
        const map = fieldPendingRef.current;
        const existing = map.get(shotId);
        if (existing) window.clearTimeout(existing.timer);
        const backendField: Parameters<typeof api.updateFrame>[2] = {};
        if (field === "duration") backendField.duration = typeof value === "number" ? value : undefined;
        if (field === "shotSize") backendField.shot_size = typeof value === "string" ? value : undefined;
        if (field === "cameraAngle") backendField.camera_angle = typeof value === "string" ? value : undefined;
        if (field === "cameraMovement") backendField.camera_movement_description = typeof value === "string" ? value : undefined;
        if (field === "transitionHint") backendField.transition_hint = typeof value === "string" ? value : undefined;
        // This endpoint currently ignores omitted/null structured fields. Keep
        // the local clear visible as unsaved instead of accepting a no-op ACK
        // and then allowing a structural snapshot to erase the draft.
        if (Object.values(backendField).some(next => next === undefined)) {
            map.delete(shotId);
            editFailuresRef.current.add(`invalid-field:${shotId}`);
            setCommandError('failedEdits');
            return;
        }
        rememberFrameDraft(shotId, backendField);
        if (frameEditFailure(projectId)) return;
        const merged = { ...(existing?.fields ?? {}), ...backendField };
        const timer = window.setTimeout(() => {
            map.delete(shotId);
            trackEdit(`field:${shotId}`, merged, (patch, revision) => api.updateFrame(projectId, shotId, patch, revision), () => {
                if (!map.has(shotId)) map.set(shotId, { timer: 0, fields: merged, environment });
            }, environment);
        }, 3000);
        map.set(shotId, { timer, fields: merged, environment });
    }, [shots, projectId, isStructureBusy, trackEdit, setCommandError, rememberFrameDraft]);

    const readConflictState = async () => {
        if (!projectId || conflictRecoveryBusyRef.current || frameEditsPending(projectId) || editSavesRef.current.size) return;
        const isCurrent = currentProjectReadGuard(projectId);
        if (!isCurrent()) return;
        conflictRecoveryBusyRef.current = true;
        setConflictRecovery('reading');
        // Keep draft content, but no debounce or navigation flush may retry it.
        for (const entry of Array.from(promptPendingRef.current.values())) window.clearTimeout(entry.timer);
        for (const entry of Array.from(fieldPendingRef.current.values())) window.clearTimeout(entry.timer);
        promptPendingRef.current.clear();
        fieldPendingRef.current.clear();
        try {
            const snapshot = await readProjectOnce(projectId, undefined, { isCurrent });
            if (!isCurrent()) return;
            if (Array.from(frameDraftsRef.current.keys()).some(id => !snapshot.frames.some(frame => frame.id === id))) {
                throw new ProjectReadbackError('An edited frame is no longer present');
            }
            for (const draft of Array.from(frameDraftsRef.current.values())) draft.version.revision = snapshot.revision!;
            setConflictRecovery('ready');
        } catch { if (isCurrent()) setConflictRecovery('readFailed'); }
        finally { conflictRecoveryBusyRef.current = false; }
    };
    const saveConflictDrafts = async () => {
        if (!projectId || conflictRecovery !== 'ready' || conflictRecoveryBusyRef.current) return;
        const isCurrent = currentProjectReadGuard(projectId);
        if (!isCurrent()) return;
        conflictRecoveryBusyRef.current = true;
        setConflictRecovery('saving');
        clearFrameEditFailures(projectId);
        editFailuresRef.current.clear();
        setCommandError(null);
        try {
            const writes = Array.from(frameDraftsRef.current, ([frameId, draft]) => trackEdit(
                `field:${frameId}`, { ...draft.patch },
                (patch, revision) => api.updateFrame(projectId, frameId, patch, revision), undefined, draft.version.environment,
            ));
            await Promise.all(writes);
            if (isCurrent()) setConflictRecovery('idle');
        } finally { conflictRecoveryBusyRef.current = false; }
    };

    // A command from a previous mount can update the cache after returning to
    // this project. Reconcile only confirmed versions, never over live drafts.
    useEffect(() => {
        if (!command.busy && !hasUnsavedEdits() && currentProject
            && lastStructureRevisionRef.current !== currentProject.revision
            && !shots.some(shot => shot.id.startsWith('shot_'))) {
            applyLocalSnapshot(currentProject);
        }
        if (!hasUnsavedEdits() && command.error === 'pendingEdits') setCommandError(null);
    }, [currentProject, command.busy, command.editsPending, command.error, editEpoch, applyLocalSnapshot, hasUnsavedEdits, shots, setCommandError]);

    // Duration editor config — derived from active R2V model's catalog entry
    const durationEditorCfg = useMemo(() => {
        const r2vModel = VIDEO_R2V_MODELS.find(m => m.id === videoConfig.r2vModel);
        const dc = r2vModel?.duration;
        if (!dc) return { min: 3, max: 15, step: 1 };
        if (dc.type === "slider") return { min: dc.min, max: dc.max, step: dc.step };
        if (dc.type === "buttons") return { min: Math.min(...dc.options), max: Math.max(...dc.options), step: 1 };
        return { min: dc.value, max: dc.value, step: 1 };
    }, [videoConfig.r2vModel]);

    // Parse asset tags from prompt and resolve to URLs
    const parseAssetTags = useCallback((prompt: string): string[] => {
        // HappyHorse uses [characterN:name] as generic reference-image slots.
        // N determines the position in the URL array: character1 → image[0], etc.
        // The "name" can be a character, scene, or prop — we look up all three.
        // Dedup by slot number (first-seen wins): the same slot referenced
        // multiple times in the prompt still corresponds to one reference
        // image, so [character1:小兔子] used twice resolves to one URL slot
        // not two. Without this, model expectations (characterN → URL[N-1])
        // would shift right and downstream slots would point to the wrong
        // images.
        const seenSlot = new Set<number>();
        const slots: { idx: number; url: string }[] = [];
        const tagPattern = /\[character(\d+):([^\]]+)\]/g;
        let match;
        while ((match = tagPattern.exec(prompt)) !== null) {
            const slotNum = parseInt(match[1], 10);
            if (seenSlot.has(slotNum)) continue;
            const name = match[2];
            let url: string | undefined;

            // Try character first
            const char = characters.find((c: any) => c.name === name);
            if (char) {
                url = selectedVariantUrl(char.reference_sheet) || selectedVariantUrl(char.full_body_asset);
            }
            // Try scene
            if (!url) {
                const scene = scenes.find((s: any) => s.name === name);
                const sceneAsset = scene?.image_asset;
                if (sceneAsset?.selected_id && sceneAsset.variants?.length) {
                    const selected = sceneAsset.variants.find((v: any) => v.id === sceneAsset.selected_id);
                    if (selected) url = selected.url;
                } else if (sceneAsset?.variants?.[0]) {
                    url = sceneAsset.variants[0].url;
                }
            }
            // Try prop
            if (!url) {
                const prop = props.find((p: any) => p.name === name);
                const propAsset = prop?.image_asset;
                if (propAsset?.selected_id && propAsset.variants?.length) {
                    const selected = propAsset.variants.find((v: any) => v.id === propAsset.selected_id);
                    if (selected) url = selected.url;
                } else if (propAsset?.variants?.[0]) {
                    url = propAsset.variants[0].url;
                }
            }

            if (url) {
                slots.push({ idx: slotNum, url });
                seenSlot.add(slotNum);
            }
        }
        // Sort by slot number so URL array matches HappyHorse's positional mapping
        slots.sort((a, b) => a.idx - b.idx);
        return slots.map(s => s.url);
    }, [characters, scenes, props]);

    const hasAssetTags = useCallback((prompt: string): boolean => {
        return /\[character\d+:[^\]]+\]/.test(prompt);
    }, []);

    const getUnresolvedAssetNames = useCallback((prompt: string): string[] => {
        const unresolved: string[] = [];
        const tagPattern = /\[character\d+:([^\]]+)\]/g;
        let match;
        while ((match = tagPattern.exec(prompt)) !== null) {
            const name = match[1];
            let hasImage = false;
            // Check character
            const char = characters.find((c: any) => c.name === name);
            if (char) {
                hasImage = !!(char.reference_sheet?.image_variants?.length || char.full_body_asset?.variants?.length);
            }
            // Check scene
            if (!hasImage) {
                const scene = scenes.find((s: any) => s.name === name);
                hasImage = !!(scene?.image_asset?.variants?.length);
            }
            // Check prop
            if (!hasImage) {
                const prop = props.find((p: any) => p.name === name);
                hasImage = !!(prop?.image_asset?.variants?.length);
            }
            if (!hasImage) unresolved.push(name);
        }
        return unresolved;
    }, [characters, scenes, props]);

    // Strip tags from prompt for clean text
    const cleanPrompt = (prompt: string): string => {
        return prompt.replace(/\[character\d+:[^\]]+\]/g, "").replace(/\s+/g, " ").trim();
    };

    // Render returns a saved Script, never an asynchronous task handle.
    const recordFrameImageBusy = useCallback((frameId: string, busy: boolean) => {
        if (busy) imageBusyRef.current.add(frameId); else imageBusyRef.current.delete(frameId);
    }, []);
    const readFrameImage = useCallback(async (frameId: string, receiptCurrent: (snapshot: Project) => boolean) => {
        if (!projectId) return;
        const isCurrent = currentProjectReadGuard(projectId);
        const epoch = imageDraftEpochRef.current.get(frameId) ?? 0;
        const requestEpoch = imageRequestDraftEpochRef.current.get(frameId);
        const active = () => isCurrent() && (imageDraftEpochRef.current.get(frameId) ?? 0) === epoch
            && !workbenchPendingRef.current.has(frameId) && !editSavesRef.current.size
            && !editFailuresRef.current.has(`workbench:${frameId}`)
            && (requestEpoch === undefined ? !frameDraftsRef.current.has(frameId) : requestEpoch === epoch);
        const snapshot = await readProjectOnce(projectId, undefined, { isCurrent: value => active() && receiptCurrent(value) });
        if (!active() || !receiptCurrent(snapshot)) throw new ProjectReadbackError('Frame author or receipt changed during image readback');
        const frame = snapshot.frames.find(candidate => candidate.id === frameId);
        setShots(previous => previous.map(shot => {
            if (shot.id !== frameId) return shot;
            const fresh = frameToShotNode(frame, snapshot.video_tasks ?? [], shot.tabMode);
            return { ...shot, imageUrl: fresh.imageUrl, t2iImageUrl: fresh.t2iImageUrl, t2iImageUrls: fresh.t2iImageUrls,
                t2iSelectedIndex: fresh.t2iSelectedIndex, t2iTaskId: undefined, t2iStatus: 'completed' };
        }));
    }, [projectId, currentProjectReadGuard]);

    const generateT2I = useCallback(async (index: number, requests: FrameImageRequests) => {
        const shot = shots[index];
        if (!currentProject || !shot?.prompt.trim() || requiresReadback) return;
        const projectId = currentProject.id;
        const frameId = shot.id;
        const operation = `frame-image:${encodeURIComponent(requireActiveRuntimeEnvironment().key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(frameId)}`;
        const store = useProjectStore.getState();
        if (store.runningOps[operation] || isStructureBusy()) return;
        const isCurrent = currentProjectReadGuard(projectId);
        if (!isCurrent()) return;
        // Receipt recovery remains available even after model/reference settings change.
        if (requests.reading || requests.busy || requests.unknown || requests.readFailed || requests.readbackFailed || imagePreparingRef.current.has(frameId)) return;
        const imageModel = currentProject.model_settings?.i2i_model ?? '';
        if (usesStudioQwenImageCore('storyboard_frame', imageModel, 'image') && isCoreStoryboardFrame(currentProject, frameId)) {
            if (!Number.isSafeInteger(lastStructureRevisionRef.current)) return;
            const authorDraft = frameDraftsRef.current.get(frameId)?.version;
            const version = authorDraft ?? captureFrameEditRevision(projectId, lastStructureRevisionRef.current!);
            const epoch = imageDraftEpochRef.current.get(frameId) ?? 0;
            const lease = {};
            imagePreparingRef.current.set(frameId, lease);
            const active = () => isCurrent() && imagePreparingRef.current.get(frameId) === lease
                && (imageDraftEpochRef.current.get(frameId) ?? 0) === epoch;
            setShots(previous => previous.map(candidate => candidate.id === frameId ? { ...candidate, t2iStatus: 'pending' } : candidate));
            try {
                const ownerMatches = (snapshot: Project) => snapshot.model_settings?.i2i_model === imageModel && isCoreStoryboardFrame(snapshot, frameId);
                // This GET proves current eligibility only. It must never replace the author's version.
                const fresh = await readProjectOnce(projectId, undefined, { isCurrent: snapshot => active() && ownerMatches(snapshot) });
                if (!active()) return;
                const expectedRevision = version.revision;
                imageRequestDraftEpochRef.current.set(frameId, epoch);
                await requests.submit({ asset_type: 'storyboard_frame', generation_type: 'all', asset_id: frameId,
                    expected_revision: expectedRevision, prompt: cleanPrompt(shot.prompt), composition_data: {}, batch_size: 1 }, () => {
                    const current = useProjectStore.getState().currentProject;
                    return active() && version.revision === expectedRevision && !!current && sameReadVersion(current, fresh)
                        && !current.sharedAssetsNeedRefresh && ownerMatches(current)
                        && current.model_settings?.storyboard_aspect_ratio === fresh.model_settings?.storyboard_aspect_ratio;
                });
            } catch { if (active()) setCommandError('unconfirmed'); }
            finally {
                if (!authorDraft) releaseFrameEditRevision(version);
                if (imagePreparingRef.current.get(frameId) === lease) imagePreparingRef.current.delete(frameId);
                if (isCurrent()) setShots(previous => previous.map(candidate => candidate.id === frameId && candidate.t2iStatus === 'pending'
                    ? { ...candidate, t2iStatus: undefined } : candidate));
            }
            return;
        }
        store.setRunningOp(operation, true);
        setShots(prev => prev.map(s => s.id === frameId ? { ...s, t2iStatus: "pending" } : s));
        let rendered = false;
        try {
            const response = await api.renderFrame(projectId, frameId, {}, cleanPrompt(shot.prompt), 1);
            rendered = true;
            if (!isCurrent()) { setCommandError('savedReadFailed'); return; }
            await confirmRawProjectAck(projectId, response, { isCurrent });
            if (!isCurrent()) return;
            const confirmed = useProjectStore.getState().currentProject!;
            const frame = confirmed.frames.find(candidate => candidate.id === frameId);
            if (!frame) throw new ProjectReadbackError('Rendered frame is no longer present');
            const fresh = frameToShotNode(frame, confirmed.video_tasks ?? [], shot.tabMode);
            setShots(prev => prev.map(s => s.id === frameId ? {
                ...s, imageUrl: fresh.imageUrl, t2iImageUrl: fresh.t2iImageUrl,
                t2iImageUrls: fresh.t2iImageUrls, t2iSelectedIndex: fresh.t2iSelectedIndex,
                t2iTaskId: undefined, t2iStatus: 'completed',
            } : s));
        } catch (error) {
            const status = (error as { response?: { status?: number } })?.response?.status;
            const uncertain = ![400, 401, 403, 404, 409, 422].includes(status ?? 0);
            setCommandError(rendered ? 'savedReadFailed' : uncertain ? 'submitUnconfirmed' : 'unconfirmed');
            if (!isCurrent()) return;
            debugLog.error("Studio", "Failed to confirm rendered image:", error);
            setShots(prev => prev.map(s => s.id === frameId ? { ...s, t2iStatus: "failed" } : s));
        } finally {
            store.setRunningOp(operation, false);
        }
    }, [shots, currentProject, requiresReadback, currentProjectReadGuard, isStructureBusy, setCommandError]);

    const providerOwnerFor = useCallback((projectId: string, frameId: string, environment: RuntimeEnvironment): ProviderVideoRequestOwner => ({
        apiBase: API_URL,
        storageId: environment.storageId,
        projectId,
        frameId,
    }), []);

    const refreshProviderPending = useCallback((project: Project | null | undefined) => {
        const environment = getRuntimeEnvironment();
        if (!project || !environment) {
            setProviderPendingByFrame({});
            return;
        }
        const next: Record<string, ProviderPendingEntry> = {};
        for (const frame of project.frames ?? []) {
            try {
                const owner = providerOwnerFor(project.id, frame.id, environment);
                const pending = readProviderVideoRequest({ owner });
                if (pending) next[frame.id] = { ...pending, ownerKey: owner.storageId };
            } catch (error) {
                debugLog.warn("Studio", "Provider video pending cache unavailable:", error);
                setCommandError('submitUnconfirmed');
            }
        }
        setProviderPendingByFrame(next);
        if (Object.keys(next).length > 0) {
            setCommandError('submitUnconfirmed');
        }
    }, [providerOwnerFor, setCommandError]);

    useEffect(() => {
        refreshProviderPending(currentProject);
    }, [currentProject, shots, refreshProviderPending]);

    const confirmProviderTasks = useCallback(async (
        projectId: string,
        taskIds: string[],
        expected: ProviderVideoTaskReceiptExpectation,
        payload: Record<string, unknown>,
        isCurrent: () => boolean,
    ): Promise<VideoTask[]> => {
        await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
            if (!isCurrent()) return false;
            const tasks = snapshot.video_tasks ?? [];
            return taskIds.every(id => tasks.some(task => task.id === id && providerVideoTaskMatchesExpectation(task, expected, payload)));
        } });
        if (!isCurrent()) return [];
        const confirmed = useProjectStore.getState().currentProject;
        const confirmedTasks = taskIds
            .map(id => confirmed?.video_tasks?.find(task => task.id === id && providerVideoTaskMatchesExpectation(task, expected, payload)))
            .filter((task): task is VideoTask => !!task);
        if (confirmedTasks.length !== taskIds.length) throw new ProjectReadbackError('Accepted provider video tasks were not committed');
        return confirmedTasks;
    }, []);

    const applyProviderConfirmedTasks = useCallback((frameId: string, confirmedTasks: VideoTask[]) => {
        const latest = confirmedTasks[confirmedTasks.length - 1];
        if (!latest) return;
        setShots(prev => prev.map(s =>
            s.id === frameId ? { ...s, videoTaskId: latest.id, videoStatus: latest.status ?? "processing" } : s,
        ));
    }, []);

    const confirmProviderResolved = useCallback((owner: ProviderVideoRequestOwner, frameId: string, clientRequestId: string): boolean => {
        const resolved = resolveProviderVideoRequest({ owner, clientRequestId });
        const currentPending = readProviderVideoRequest({ owner });
        if (!resolved || currentPending) {
            setProviderPendingByFrame(prev => currentPending
                ? { ...prev, [frameId]: { ...currentPending, ownerKey: owner.storageId } }
                : prev);
            return false;
        }
        const remainingPending: Record<string, ProviderPendingEntry> = {};
        let pendingReadFailed = false;
        const latestProject = useProjectStore.getState().currentProject;
        const environment = getRuntimeEnvironment();
        if (latestProject && environment) {
            for (const frame of latestProject.frames ?? []) {
                if (frame.id === frameId) continue;
                try {
                    const frameOwner = providerOwnerFor(latestProject.id, frame.id, environment);
                    const pending = readProviderVideoRequest({ owner: frameOwner });
                    if (pending) remainingPending[frame.id] = { ...pending, ownerKey: frameOwner.storageId };
                } catch (error) {
                    debugLog.warn("Studio", "Provider video pending cache unavailable:", error);
                    pendingReadFailed = true;
                }
            }
        }
        setProviderPendingByFrame(prev => {
            const next = { ...prev, ...remainingPending };
            delete next[frameId];
            return next;
        });
        if (pendingReadFailed || Object.keys(remainingPending).length > 0) {
            setCommandError('submitUnconfirmed');
        } else {
            setCommandError(null);
        }
        return true;
    }, [providerOwnerFor, setCommandError]);

    const submitProviderPendingLocked = useCallback(async (
        projectId: string,
        frameId: string,
        owner: ProviderVideoRequestOwner,
        environment: RuntimeEnvironment,
        pending: ProviderVideoRequestResult,
        isCurrent: () => boolean,
    ) => {
        const operation = `video-submit:${encodeURIComponent(environment.key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(frameId)}`;
        const store = useProjectStore.getState();
        if (!isCurrent() || isStructureBusy()) return;
        store.setRunningOp(operation, true);
        setCommandError(null);
        try {
            const expected = providerExpectationFromPending(pending, projectId, frameId);
            const payload = pending.payload as Record<string, unknown>;
            const rawReceipt = await api.createProviderVideoTasks(projectId, pending.payload);
            if (!isCurrent()) return;
            const receipt = parseProviderVideoTaskReceipt(rawReceipt, expected);
            assertProviderVideoTasksMatchPayload(receipt.tasks, expected, payload);
            if (receipt.reason) {
                await readProjectOnce(projectId, undefined, { isCurrent: () => isCurrent() });
                if (!isCurrent()) return;
                if (!confirmProviderResolved(owner, frameId, pending.clientRequestId)) {
                    setCommandError('submitUnconfirmed');
                    return;
                }
                setCommandError('providerRequestDeleted');
                return;
            }
            if (receipt.taskIds.length === 0) {
                setCommandError('submitUnconfirmed');
                setProviderPendingByFrame(prev => ({ ...prev, [frameId]: { ...pending, ownerKey: owner.storageId } }));
                return;
            }
            const confirmedTasks = await confirmProviderTasks(projectId, receipt.taskIds, expected, payload, isCurrent);
            if (!isCurrent()) return;
            if (!confirmProviderResolved(owner, frameId, pending.clientRequestId)) {
                setCommandError('submitUnconfirmed');
                return;
            }
            applyProviderConfirmedTasks(frameId, confirmedTasks);
        } catch (error: unknown) {
            if (!isCurrent()) return;
            debugLog.warn("Studio", "Provider video submission remains unconfirmed:", error);
            const response = apiErrorLike(error).response;
            const code = response?.data?.code;
            if (response?.status === 422 && code === 'provider_request_invalid') {
                setShotErrors(prev => ({ ...prev, [frameId]: t('providerUnsupportedInput') }));
                try {
                    if (!confirmProviderResolved(owner, frameId, pending.clientRequestId)) {
                        setCommandError('submitUnconfirmed');
                    }
                } catch {
                    setCommandError('submitUnconfirmed');
                    setProviderPendingByFrame(prev => ({ ...prev, [frameId]: { ...pending, ownerKey: owner.storageId } }));
                }
                return;
            }
            setCommandError('submitUnconfirmed');
            setProviderPendingByFrame(prev => ({ ...prev, [frameId]: { ...pending, ownerKey: owner.storageId } }));
        } finally {
            store.setRunningOp(operation, false);
        }
    }, [applyProviderConfirmedTasks, confirmProviderResolved, confirmProviderTasks, isStructureBusy, setCommandError, t]);

    const submitProviderRetry = useCallback(async (frameId: string) => {
        const project = useProjectStore.getState().currentProject;
        const environment = requireActiveRuntimeEnvironment();
        if (!project) return;
        const projectId = project.id;
        const owner = providerOwnerFor(projectId, frameId, environment);
        const isCurrent = currentProjectReadGuard(projectId, environment);
        try {
            await withProviderVideoRequestLock(owner, async () => {
                if (!isCurrent()) return;
                const pending = readProviderVideoRequest({ owner });
                if (!pending) {
                    setProviderPendingByFrame(prev => {
                        const next = { ...prev };
                        delete next[frameId];
                        return next;
                    });
                    return;
                }
                setProviderPendingByFrame(prev => ({ ...prev, [frameId]: { ...pending, ownerKey: owner.storageId } }));
                await submitProviderPendingLocked(projectId, frameId, owner, environment, pending, isCurrent);
            });
        } catch (error) {
            if (!isCurrent()) return;
            debugLog.warn("Studio", "Provider video lock unavailable:", error);
            setCommandError(error instanceof ProviderVideoRequestError && error.code === 'provider_video_request_lock_unavailable'
                ? 'providerLockUnavailable'
                : 'submitUnconfirmed');
        }
    }, [currentProjectReadGuard, providerOwnerFor, setCommandError, submitProviderPendingLocked]);

    const parseCreatedVideoTasks = useCallback((
        tasks: unknown,
        projectId: string,
        frameId: string,
        generationMode: 'r2v' | 'i2v',
        workbenchTab: 'direct_r2v' | 't2i_i2v',
    ): { accepted: VideoTask[]; invalidAck: boolean } => {
        const values = Array.isArray(tasks) ? tasks : [tasks];
        const accepted = values.filter((task): task is VideoTask => {
            if (!task || typeof task !== 'object') return false;
            const candidate = task as Partial<VideoTask> & { project_id?: unknown; frame_id?: unknown; id?: unknown; generation_mode?: unknown; workbench_tab?: unknown };
            return typeof candidate.id === 'string'
                && candidate.id.trim().length > 0
                && candidate.project_id === projectId
                && candidate.frame_id === frameId
                && candidate.generation_mode === generationMode
                && candidate.workbench_tab === workbenchTab;
        });
        return { accepted, invalidAck: values.length === 0 || accepted.length !== values.length };
    }, []);

    // Batch-aware generation. The user's "抽卡" mental model: one
    // click of Generate ×N fires N independent createVideoTask calls
    // in parallel (each becomes its own VideoTask record on the
    // backend). All accepted task ids are confirmed through the
    // committed project read before local UI state mirrors them.
    const generateVideoBatch = useCallback(async (
        index: number,
        count: number,
        params?: Partial<ParamsState>,
    ) => {
        const shot = shots[index];
        if (!currentProject || !shot?.prompt.trim() || requiresReadback) return;
        const projectId = currentProject.id;
        const frameId = shot.id;
        const tabMode = shot.tabMode;
        const actionEnvironment = requireActiveRuntimeEnvironment();
        const operation = `video-submit:${encodeURIComponent(actionEnvironment.key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(frameId)}`;
        const store = useProjectStore.getState();
        const isCurrent = currentProjectReadGuard(projectId, actionEnvironment);
        if (!isCurrent() || store.runningOps[operation] || isStructureBusy()) return;
        if (hasUnsavedEdits()) {
            setCommandError('pendingEdits');
            return;
        }
        const promptText = buildAssembledPrompt(shot);
        const effectiveCount = Math.max(1, Math.min(6, count || 1));

        // Pre-flight: R2V tab needs reference inputs. Without them
        // the backend rejects with 400 anyway, but historically the
        // task would queue, fail mid-generation, and the user'd see
        // "排队中..." until the failure surfaced. Cheaper to validate
        // here and show inline error in the ParamsSection.
        if (tabMode === "direct_r2v") {
            const refs = parseAssetTags(shot.prompt);
            if (refs.length === 0) {
                const hasTags = hasAssetTags(shot.prompt);
                let errMsg: string;
                if (hasTags) {
                    const unresolved = getUnresolvedAssetNames(shot.prompt);
                    errMsg = t("unresolvedRefImages", { refs: unresolved.join("、") });
                } else {
                    const r2vModelId = params?.model ?? videoConfig.r2vModel;
                    const r2vModel = VIDEO_R2V_MODELS.find(m => m.id === r2vModelId);
                    const modelLabel = r2vModel?.name ?? r2vModelId;
                    errMsg = missingRefsMessage(modelLabel);
                }
                setShotErrors(prev => ({ ...prev, [frameId]: errMsg }));
                toast.warning(errMsg);
                return;
            }
        } else {
            const probeImage = getActiveT2IImageUrl(shot) || shot.imageUrl || "";
            if (!probeImage) {
                const errMsg = t("i2vNeedsFirstFrame") || "请先上传或生成首帧再生成视频。";
                setShotErrors(prev => ({ ...prev, [frameId]: errMsg }));
                toast.warning(errMsg);
                return;
            }
        }

        setCommandError(null);
        setShotErrors(prev => {
            if (!prev[frameId]) return prev;
            const next = { ...prev };
            delete next[frameId];
            return next;
        });

        const providerOwner = providerOwnerFor(projectId, frameId, actionEnvironment);

        const submitProviderInput = async (buildInput: () => Record<string, unknown>) => {
            store.setRunningOp(operation, true);
            try {
                const input = buildInput();
                await withProviderVideoRequestLock(providerOwner, async () => {
                    if (!isCurrent()) return;
                    const lockedPending = readProviderVideoRequest({ owner: providerOwner });
                    if (lockedPending) {
                        setProviderPendingByFrame(prev => ({ ...prev, [frameId]: { ...lockedPending, ownerKey: providerOwner.storageId } }));
                        setCommandError('submitUnconfirmed');
                        return;
                    }
                    const pending = beginProviderVideoRequest({ owner: providerOwner, input, batchSize: effectiveCount });
                    setProviderPendingByFrame(prev => ({ ...prev, [frameId]: { ...pending, ownerKey: providerOwner.storageId } }));
                    setShots(prev => prev.map(s =>
                        s.id === frameId ? { ...s, videoStatus: "pending" } : s,
                    ));
                    await submitProviderPendingLocked(projectId, frameId, providerOwner, actionEnvironment, pending, isCurrent);
                });
            } catch (error: unknown) {
                if (!isCurrent()) return;
                if (error instanceof ProviderVideoTaskEntryError) {
                    const detail = t('providerUnsupportedInput');
                    setShotErrors(prev => ({ ...prev, [frameId]: detail }));
                    toast.warning(detail);
                    return;
                }
                debugLog.warn("Studio", "Provider video request could not be prepared:", error);
                setCommandError(error instanceof ProviderVideoRequestError && error.code === 'provider_video_request_lock_unavailable'
                    ? 'providerLockUnavailable'
                    : 'submitUnconfirmed');
            } finally {
                store.setRunningOp(operation, false);
            }
        };

        if (tabMode === "t2i_i2v") {
            const i2vModelId = params?.model ?? videoConfig.model;
            if (isProviderVideoModel(i2vModelId) || isOrderedStudioProviderVideoModel(i2vModelId)) {
                const promptExtend = params?.promptExtend ?? videoConfig.promptExtend;
                const shotType = params?.shotType;
                const common = {
                    imageUrl: getActiveT2IImageUrl(shot) || shot.imageUrl || "",
                    prompt: promptText,
                    model: i2vModelId,
                    frameId,
                    duration: params?.duration ?? videoConfig.duration,
                    resolution: isOrderedStudioProviderVideoModel(i2vModelId)
                        ? normalizeWan3StudioResolution(params?.resolution ?? videoConfig.resolution)
                        : params?.resolution ?? videoConfig.resolution,
                    promptExtend,
                    seed: params?.seed,
                    watermark: params?.watermark,
                    workbenchTab: "t2i_i2v" as const,
                };
                await submitProviderInput(() => buildProviderVideoTaskInput(isOrderedStudioProviderVideoModel(i2vModelId) ? {
                    ...common,
                    contractVersion: ORDERED_MEDIA_CONTRACT_VERSION,
                    generationMode: "i2v",
                    lastFrameUrl: shot.lastFrameUrl,
                    ratio: params?.ratio ?? videoConfig.ratio,
                } : {
                    ...common,
                    negativePrompt: params?.negativePrompt ?? videoConfig.negativePrompt,
                    shotType: shotType && (promptExtend !== false || shotType !== 'single') ? shotType : undefined,
                }));
                return;
            }
        }

        if (tabMode === "direct_r2v") {
            const r2vModelId = params?.model ?? videoConfig.r2vModel;
            if (isOrderedStudioProviderVideoModel(r2vModelId)) {
                await submitProviderInput(() => buildProviderVideoTaskInput({
                    contractVersion: ORDERED_MEDIA_CONTRACT_VERSION,
                    generationMode: "r2v",
                    referenceImageUrls: parseAssetTags(shot.prompt),
                    prompt: promptText,
                    model: r2vModelId,
                    frameId,
                    duration: params?.duration ?? videoConfig.duration,
                    resolution: normalizeWan3StudioResolution(params?.resolution ?? videoConfig.resolution),
                    promptExtend: params?.promptExtend ?? videoConfig.promptExtend,
                    seed: params?.seed,
                    watermark: params?.watermark,
                    ratio: params?.ratio ?? videoConfig.ratio,
                    workbenchTab: "direct_r2v",
                }));
                return;
            }
        }

        let existingProviderPending: ProviderVideoRequestResult | null = null;
        try { existingProviderPending = readProviderVideoRequest({ owner: providerOwner }); }
        catch (error) {
            debugLog.warn("Studio", "Provider video pending cache unavailable:", error);
            setCommandError('submitUnconfirmed');
            return;
        }
        if (existingProviderPending) {
            setProviderPendingByFrame(prev => ({ ...prev, [frameId]: { ...existingProviderPending!, ownerKey: providerOwner.storageId } }));
            setCommandError('submitUnconfirmed');
            return;
        }

        store.setRunningOp(operation, true);
        setShots(prev => prev.map(s =>
            s.id === frameId ? { ...s, videoStatus: "pending" } : s,
        ));

        const createOne = async (): Promise<{ accepted: VideoTask[]; invalidAck: boolean }> => {
            if (!isCurrent()) return { accepted: [], invalidAck: false };
            if (tabMode === "direct_r2v") {
                const referenceUrls = parseAssetTags(shot.prompt);
                const explicitR2v = params?.model ?? videoConfig.r2vModel;
                const explicitOk = VIDEO_R2V_MODELS.some(m => m.id === explicitR2v);
                const routeModelId = explicitOk
                    ? explicitR2v
                    : getR2vRouteModelId(videoConfig.model);
                const imageBased = isR2vImageBased(routeModelId);
                const tasks = await api.createVideoTask(
                    projectId,
                    "",
                    promptText,
                    params?.duration ?? videoConfig.duration,
                    params?.seed,
                    params?.resolution ?? videoConfig.resolution,
                    false,
                    "",
                    params?.promptExtend ?? videoConfig.promptExtend,
                    params?.negativePrompt ?? videoConfig.negativePrompt,
                    1,
                    routeModelId,
                    frameId,
                    params?.shotType ?? "multi",
                    "r2v",
                    !imageBased ? referenceUrls : undefined,
                    undefined, undefined, undefined,
                    undefined, undefined,
                    imageBased ? referenceUrls : undefined,
                    params?.ratio,
                    tabMode,
                    params?.watermark,
                );
                if (!isCurrent()) return { accepted: [], invalidAck: false };
                return parseCreatedVideoTasks(tasks, projectId, frameId, 'r2v', tabMode);
            }
            const i2vModelId = params?.model ?? videoConfig.model;
            const i2vModelOk = VIDEO_I2V_MODELS.some(m => m.id === i2vModelId);
            if (!i2vModelOk) {
                debugLog.warn("Studio", `Refusing I2V submission with non-I2V model "${i2vModelId}".`);
                return { accepted: [], invalidAck: false };
            }
            const imageUrl = getActiveT2IImageUrl(shot) || shot.imageUrl || "";
            const tasks = await api.createVideoTask(
                projectId,
                imageUrl,
                promptText,
                params?.duration ?? videoConfig.duration,
                params?.seed,
                params?.resolution ?? videoConfig.resolution,
                false,
                "",
                params?.promptExtend ?? videoConfig.promptExtend,
                params?.negativePrompt ?? videoConfig.negativePrompt,
                1,
                i2vModelId,
                frameId,
                params?.shotType ?? "multi",
                "i2v",
                undefined,
                params?.mode ?? videoConfig.mode,
                params?.sound ?? videoConfig.sound,
                params?.cfgScale ?? videoConfig.cfgScale,
                params?.viduAudio ?? videoConfig.viduAudio,
                params?.movementAmplitude ?? videoConfig.movementAmplitude,
                undefined,
                undefined,
                tabMode,
                params?.watermark,
            );
            if (!isCurrent()) return { accepted: [], invalidAck: false };
            return parseCreatedVideoTasks(tasks, projectId, frameId, 'i2v', tabMode);
        };

        try {
            const settled = await Promise.allSettled(Array.from({ length: effectiveCount }, createOne));
            if (!isCurrent()) {
                // The command may have been accepted after its editor retired.
                // A later mount must read the result before another paid submit.
                setCommandError('submitUnconfirmed');
                return;
            }
            const uncertainFailure = settled.some(result => {
                if (result.status !== 'rejected') return false;
                const status = result.reason?.response?.status;
                return ![400, 401, 403, 404, 409, 422].includes(status);
            });
            const fulfilled = settled.filter((result): result is PromiseFulfilledResult<{ accepted: VideoTask[]; invalidAck: boolean }> => result.status === 'fulfilled');
            const taskIds = Array.from(new Set(fulfilled.flatMap(result => result.value.accepted.map(task => task.id))));
            const invalidAckCount = fulfilled.filter(result => result.value.invalidAck).length;
            if (taskIds.length === 0) {
                if (invalidAckCount > 0 || uncertainFailure) {
                    setCommandError('submitUnconfirmed');
                    return;
                }
                const firstRejected = settled.find(result => result.status === 'rejected') as PromiseRejectedResult | undefined;
                const error: any = firstRejected?.reason;
                const status = error?.response?.status;
                const detail = error?.response?.data?.detail || error?.message || t("unknownErrorFallback");
                if (status === 400 && typeof detail === "string") {
                    setShotErrors(prev => ({ ...prev, [frameId]: detail }));
                }
                toast.error(firstRejected ? t("videoGenFailedToast", { detail: String(detail).slice(0, 150) }) : t("videoGenSubmitFailedToast"));
                setShots(prev => prev.map(s =>
                    s.id === frameId ? { ...s, videoStatus: "failed" as const } : s,
                ));
                return;
            }
            await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
                if (!isCurrent()) return false;
                const tasks = snapshot.video_tasks ?? [];
                return taskIds.every(id => tasks.some(task =>
                    task.id === id
                    && task.project_id === projectId
                    && task.frame_id === frameId
                    && task.generation_mode === (tabMode === 'direct_r2v' ? 'r2v' : 'i2v')
                    && task.workbench_tab === tabMode,
                ));
            } });
            if (!isCurrent()) return;
            const confirmed = useProjectStore.getState().currentProject;
            const confirmedTasks = taskIds
                .map(id => confirmed?.video_tasks?.find(task => task.id === id
                    && task.project_id === projectId
                    && task.frame_id === frameId
                    && task.generation_mode === (tabMode === 'direct_r2v' ? 'r2v' : 'i2v')
                    && task.workbench_tab === tabMode))
                .filter((task): task is VideoTask => !!task);
            if (confirmedTasks.length !== taskIds.length) throw new ProjectReadbackError('Accepted video tasks were not committed');
            const latest = confirmedTasks[confirmedTasks.length - 1];
            setShots(prev => prev.map(s =>
                s.id === frameId ? { ...s, videoTaskId: latest.id, videoStatus: latest.status ?? "processing" } : s,
            ));
            if (invalidAckCount > 0 || uncertainFailure) {
                setCommandError('submitUnconfirmed');
            }
            if (invalidAckCount > 0 || settled.some(result => result.status === 'rejected')) {
                toast.warning(t("videoGenPartialSubmitToast", { count: confirmedTasks.length, total: effectiveCount }));
            }
        } catch (error: any) {
            if (!isCurrent()) return;
            debugLog.error("Studio", "Batch generate failed for shot:", error);
            if (error instanceof ProjectReadbackError) {
                setCommandError('savedReadFailed');
                return;
            }
            const status = error?.response?.status;
            const detail = error?.response?.data?.detail || error?.message || t("unknownErrorFallback");
            if (status === 400 && typeof detail === "string") {
                setShotErrors(prev => ({ ...prev, [frameId]: detail }));
            }
            toast.error(t("videoGenFailedToast", { detail: String(detail).slice(0, 150) }));
            setShots(prev => prev.map(s =>
                s.id === frameId ? { ...s, videoStatus: "failed" as const } : s,
            ));
        } finally {
            store.setRunningOp(operation, false);
        }
    }, [shots, currentProject, requiresReadback, currentProjectReadGuard, isStructureBusy, hasUnsavedEdits, setCommandError, videoConfig, parseAssetTags, missingRefsMessage, hasAssetTags, getUnresolvedAssetNames, parseCreatedVideoTasks, providerOwnerFor, submitProviderPendingLocked, t]);

    // Project-level task refresh: when any task on any shot is in
    // flight, refetch the whole project every 5s. The candidates
    // panel + queue read from currentProject.video_tasks for canonical
    // state. The GET must go through the shared-source read gate; a
    // stale enriched response cannot shallow-merge over a newer source
    // floor or keep retrying forever in the same poll session.
    useEffect(() => {
        if (!currentProject?.id) return;
        const projectId = currentProject.id;
        const allTasks = (currentProject.video_tasks ?? []) as VideoTask[];
        const anyInFlight = allTasks.some(
            (t) => t.status === "pending" || t.status === "processing",
        );
        // Also poll if any shot's locally-tracked videoTaskId is not
        // yet reflected in the project record (closes the just-created
        // window). With the Phase-2 derive-from-tasks model, we only
        // care about the legacy single-id mirror on the shot.
        const localInFlight = shots.some((s) => {
            const id = s.videoTaskId;
            if (!id) return false;
            const t = allTasks.find((tt) => tt.id === id);
            return !t || t.status === "pending" || t.status === "processing";
        });
        if (!anyInFlight && !localInFlight) return;
        const pollSignature = Array.from(new Set([
            ...allTasks.filter(t => t.status === "pending" || t.status === "processing").map(t => t.id),
            ...shots.flatMap(s => s.videoTaskId ? [s.videoTaskId] : []),
        ])).sort().join('|');
        if (videoPollBlocked?.projectId === projectId && videoPollBlocked.signature === pollSignature) return;
        let cancelled = false;
        const id = window.setInterval(async () => {
            if (videoPollInFlightRef.current) return;
            videoPollInFlightRef.current = true;
            try {
                await readProjectOnce(projectId, undefined, { isCurrent: () => !cancelled && currentProjectReadGuard(projectId)() });
                if (!cancelled) setVideoPollBlocked(prev => prev?.projectId === projectId ? null : prev);
            } catch (error) {
                if (cancelled) return;
                debugLog.warn("Studio", "Video project poll readback failed:", error);
                if (error instanceof ProjectReadbackError) setCommandError('savedReadFailed');
                setVideoPollBlocked({ projectId, signature: pollSignature });
            } finally {
                videoPollInFlightRef.current = false;
            }
        }, 5000);
        return () => { cancelled = true; window.clearInterval(id); };
    }, [currentProject?.id, currentProject?.video_tasks, shots, videoPollBlocked, setCommandError, currentProjectReadGuard]);

    // Insert asset tag from drawer into target shot
    const insertAssetFromDrawer = useCallback((type: string, name: string) => {
        const shotIndex = drawerState.targetShotIndex;
        if (shotIndex === null || shotIndex === undefined) return;

        const tag = `[${type}:${name}]`;
        const textarea = textareaRefs.current.get(shotIndex) ?? null;
        if (textarea) {
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const currentPrompt = shots[shotIndex].prompt;
            const newPrompt = currentPrompt.slice(0, start) + tag + currentPrompt.slice(end);
            updatePrompt(shotIndex, newPrompt);
            setTimeout(() => {
                textarea.selectionStart = textarea.selectionEnd = start + tag.length;
                textarea.focus();
            }, 0);
        } else {
            updatePrompt(shotIndex, shots[shotIndex].prompt + " " + tag);
        }
    }, [drawerState.targetShotIndex, shots, updatePrompt]);

    // Toolbar model display: surface the model the project's workflow
    // mode actually uses, not the I2V parent. R2V projects were
    // showing "wan2.7-i2v" while their generation actually went
    // through wan2.6-r2v / wan2.7-r2v — confusing and the source of
    // the "but I selected R2V" support thread.
    const isR2VWorkflow = (currentProject?.workflow_mode ?? "r2v") === "r2v";
    const currentModelName = isR2VWorkflow
        ? (VIDEO_R2V_MODELS.find(m => m.id === videoConfig.r2vModel)?.name ?? videoConfig.r2vModel)
        : (VIDEO_I2V_MODELS.find(m => m.id === videoConfig.model)?.name ?? (isProviderVideoModel(videoConfig.model) ? videoConfig.model : videoConfig.model));

    // ---- Project-level task derivations (drive Queue + Candidates) ----
    // We derive these via useMemo so per-render allocation is cheap and
    // children can rely on referentially-stable arrays (set-membership
    // tests in CompareModal etc. are correctness-sensitive).
    const allVideoTasks: VideoTask[] = useMemo(
        () => (currentProject?.video_tasks ?? []) as VideoTask[],
        [currentProject?.video_tasks],
    );

    const tasksById = useMemo(() => {
        const map = new Map<string, VideoTask>();
        for (const t of allVideoTasks) map.set(t.id, t);
        return map;
    }, [allVideoTasks]);

    // Map shot.id → human label for the queue panel's frame column.
    const shotLabelByFrameId = useMemo(() => {
        const out: Record<string, string> = {};
        shots.forEach((s, i) => { out[s.id] = `Shot ${i + 1}`; });
        return out;
    }, [shots]);

    // In-flight aggregate count drives the TaskQueueButton badge.
    const inFlightTaskCount = useMemo(
        () => allVideoTasks.filter(t => t.status === "pending" || t.status === "processing").length,
        [allVideoTasks],
    );

    // Sync shot.videoStatus from project.video_tasks when the
    // project-level poll refreshes. Video task status is only visible
    // via project refresh (GET /tasks/ only covers asset tasks).
    useEffect(() => {
        if (!allVideoTasks.length) return;
        const tasksById = new Map(allVideoTasks.map(task => [task.id, task]));
        const autoSelectTargets = shots.flatMap(s => {
            const task = s.videoTaskId ? tasksById.get(s.videoTaskId) : undefined;
            if (!currentProject?.id || !s.videoTaskId || task?.frame_id !== s.id || task.status !== "completed" || s.videoStatus === "completed") return [];
            const key = `${currentProject.id}:${s.id}:${s.videoTaskId}`;
            return autoSelectInFlightRef.current.has(key) || autoSelectAttemptedRef.current.has(key) ? [] : [{ frameId: s.id, key }];
        });
        setShots(prev => {
            let changed = false;
            const next = prev.map(s => {
                if (!s.videoTaskId) return s;
                const task = tasksById.get(s.videoTaskId);
                if (!task || task.frame_id !== s.id) return s;
                if (task.status === "completed" && s.videoStatus !== "completed") {
                    changed = true;
                    return { ...s, videoStatus: "completed" as const, videoUrl: task.video_url };
                }
                if (task.status === "needs_confirmation" && s.videoStatus !== "needs_confirmation") {
                    changed = true;
                    return { ...s, videoStatus: "needs_confirmation" as const, videoUrl: task.video_url };
                }
                if (task.status === "failed" && s.videoStatus !== "failed") {
                    changed = true;
                    return { ...s, videoStatus: "failed" as const };
                }
                return s;
            });
            return changed ? next : prev;
        });
        // Persist newly-completed videos as the frame's active take so the
        // hero survives reload / refine / cross-device opens. Backend skips
        // pinned frames; each raw command ACK must be confirmed by a guarded
        // readback before it can affect the store or hero projection.
        const projectId = currentProject?.id;
        if (projectId && autoSelectTargets.length > 0) {
            const targets = [...autoSelectTargets];
            targets.forEach(target => {
                autoSelectInFlightRef.current.add(target.key);
                autoSelectAttemptedRef.current.add(target.key);
            });
            void (async () => {
                for (const { frameId, key } of targets) {
                    try {
                        if (!sessionMounted.current || useProjectStore.getState().currentProject?.id !== projectId) return;
                        const response = await api.autoSelectLatestVideo(projectId, frameId);
                        if (!sessionMounted.current || useProjectStore.getState().currentProject?.id !== projectId) return;
                        await confirmRawProjectAck(projectId, response, { isCurrent: currentProjectReadGuard(projectId) });
                        if (!sessionMounted.current || useProjectStore.getState().currentProject?.id !== projectId) return;
                        const confirmed = useProjectStore.getState().currentProject;
                        const frame = confirmed?.frames?.find(candidate => candidate.id === frameId);
                        if (!confirmed || !frame) continue;
                        const videoTasks = (confirmed.video_tasks ?? []) as VideoTask[];
                        const defaultMode = confirmed.default_generation_mode === "i2v" ? "t2i_i2v" : "direct_r2v";
                        const refreshed = frameToShotNode(frame, videoTasks, defaultMode);
                        setShots(prev => prev.map(s => s.id === frameId ? {
                            ...s,
                            videoUrl: refreshed.videoUrl,
                            videoStatus: refreshed.videoStatus ?? s.videoStatus,
                            videoTaskId: refreshed.videoTaskId ?? s.videoTaskId,
                            isVideoPinned: refreshed.isVideoPinned,
                            finalTakeId: refreshed.finalTakeId,
                        } : s));
                    } catch (err) {
                        debugLog.warn("Studio", "autoSelectLatestVideo failed for frame:", frameId, err);
                        if (err instanceof ProjectReadbackError) setCommandError('savedReadFailed');
                    } finally {
                        autoSelectInFlightRef.current.delete(key);
                    }
                }
            })();
        }
    }, [allVideoTasks, currentProject?.id, currentProjectReadGuard, setCommandError, shots]);

    // Compare modal needs the actual VideoTask objects for the
    // currently-selected ids (in whatever order they were selected).
    const compareTasks = useMemo(() => {
        const out: VideoTask[] = [];
        Array.from(compareSelectedIds).forEach((id) => {
            const t = tasksById.get(id);
            if (t) out.push(t);
        });
        return out;
    }, [compareSelectedIds, tasksById]);

    // Per-shot candidate tasks — derived directly from the project-
    // level video_tasks. After Phase 2 persistence, each VideoTask
    // carries `frame_id` + `workbench_tab` so we can bucket without a
    // shot-side index. Pre-Phase-2 tasks lack `workbench_tab`; they
    // fall back to `generation_mode` so legacy records still group
    // correctly into the right tab.
    const tasksForShot = useCallback((shot: ShotNode): VideoTask[] => {
        return allVideoTasks.filter((t) => {
            if (t.frame_id !== shot.id) return false;
            if (t.workbench_tab != null) {
                return t.workbench_tab === shot.tabMode;
            }
            // Legacy fallback: i2v tasks belong in t2i_i2v, r2v in direct_r2v.
            if (shot.tabMode === "direct_r2v") return t.generation_mode === "r2v";
            return t.generation_mode !== "r2v"; // i2v + undefined → i2v tab
        });
    }, [allVideoTasks]);

    // Build a ParamsState from videoConfig + per-shot overrides.
    // Single source of truth strategy:
    //  - Per-shot overrides (shotCounts, shotSeeds) for params whose
    //    "right value" naturally differs by shot.
    //  - videoConfig for shared knobs the user typically picks once
    //    and uses across all shots in a project.
    const paramsStateForShot = useCallback((shot: ShotNode): ParamsState => {
        const isR2v = shot.tabMode === "direct_r2v";
        const modelId = isR2v ? videoConfig.r2vModel : videoConfig.model;
        return {
            model: modelId,
            duration: shot.duration ?? videoConfig.duration,
            count: shotCounts[shot.id] ?? 1,
            // Per-shot seed override (Sweep G fix); undefined means
            // "random per generation".
            seed: shotSeeds[shot.id],
            resolution: videoConfig.resolution,
            ratio: videoConfig.ratio,
            negativePrompt: videoConfig.negativePrompt,
            promptExtend: videoConfig.promptExtend,
            cfgScale: videoConfig.cfgScale,
            mode: videoConfig.mode,
            movementAmplitude: videoConfig.movementAmplitude,
            sound: videoConfig.sound,
            viduAudio: videoConfig.viduAudio,
            watermark: videoConfig.watermark,
        };
    }, [videoConfig, shotCounts, shotSeeds]);

    // ParamsSection.onChange handler: per-shot overrides (count, seed)
    // go into their dedicated maps; everything else writes back to
    // the shared videoConfig (so the user's most-recent picks become
    // the new default for siblings). videoConfig is mirrored to
    // localStorage as a recovery cache only — the authoritative model
    // selection lives in project.model_settings, written via the
    // 生成设置 modal.
    const handleShotParamsChange = useCallback((shot: ShotNode, next: ParamsState) => {
        if ((shotCounts[shot.id] ?? 1) !== next.count) {
            persistWorkbench(shot.id, { workbench_generate_count: next.count });
        }
        setShotCounts(prev => ({ ...prev, [shot.id]: next.count }));
        // Sync duration back to structured field (single source of truth)
        if (next.duration !== (shot.duration ?? videoConfig.duration)) {
            const idx = shots.findIndex(s => s.id === shot.id);
            if (idx >= 0) handleUpdateField(idx, "duration", next.duration);
        }
        // Seed: track per-shot. Undefined ↔ "random" — stored as
        // delete-from-map so the entry doesn't accrete forever.
        setShotSeeds(prev => {
            const wasSet = prev[shot.id] !== undefined;
            const isSet = next.seed !== undefined && !Number.isNaN(next.seed);
            if (!wasSet && !isSet) return prev;
            if (wasSet && !isSet) {
                const out = { ...prev };
                delete out[shot.id];
                return out;
            }
            if (prev[shot.id] === next.seed) return prev;
            return { ...prev, [shot.id]: next.seed };
        });
        const isR2v = shot.tabMode === "direct_r2v";
        const ls = typeof window !== "undefined" ? window.localStorage : null;
        setVideoConfig(prev => {
            const updated: VideoConfig = {
                ...prev,
                duration: next.duration,
                resolution: next.resolution ?? prev.resolution,
                ratio: next.ratio ?? prev.ratio,
                negativePrompt: next.negativePrompt ?? prev.negativePrompt,
                promptExtend: next.promptExtend ?? prev.promptExtend,
                cfgScale: next.cfgScale ?? prev.cfgScale,
                mode: next.mode ?? prev.mode,
                movementAmplitude: next.movementAmplitude ?? prev.movementAmplitude,
                sound: next.sound ?? prev.sound,
                viduAudio: next.viduAudio ?? prev.viduAudio,
                // Watermark — preserve undefined (means "model doesn't expose
                // it") so swapping to a non-watermark-supporting model clears it.
                watermark: next.watermark,
            };
            if (isR2v) {
                updated.r2vModel = next.model;
                ls?.setItem("storyboard-r2v-r2v-model", next.model);
            } else {
                updated.model = next.model;
                ls?.setItem("storyboard-r2v-model", next.model);
            }
            return updated;
        });
    }, [persistWorkbench, shotCounts]);


    // These endpoints return a task, not a project revision. Check the
    // acknowledged fields before accepting the enriched project read.
    const confirmTaskChange = useCallback(async (
        taskId: string,
        send: () => Promise<unknown>,
        fields: readonly string[],
    ) => {
        if (!currentProject?.id || requiresReadback) return;
        const projectId = currentProject.id;
        const isCurrent = currentProjectReadGuard(projectId);
        const operation = `task-edit:${encodeURIComponent(requireActiveRuntimeEnvironment().key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(taskId)}`;
        const store = useProjectStore.getState();
        if (!isCurrent() || store.runningOps[operation]) return;
        store.setRunningOp(operation, true);
        try {
            const response = await send();
            if (!isCurrent()) return;
            const task = response as Record<string, unknown> | null;
            if (!task || task.id !== taskId || !fields.every(field => Object.prototype.hasOwnProperty.call(task, field))) {
                throw new Error('Invalid committed task acknowledgement');
            }
            await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
                if (!isCurrent()) return false;
                const confirmed = snapshot.video_tasks?.find(candidate => candidate.id === taskId);
                return !!confirmed && fields.every(field =>
                    (confirmed as unknown as Record<string, unknown>)[field] === task[field]);
            } });
        } catch (err) {
            if (isCurrent()) {
                const response = apiErrorLike(err).response;
                const status = response?.status;
                const code = response?.data?.code;
                setCommandError(err instanceof ProjectReadbackError
                    ? 'savedReadFailed'
                    : status === 409 && code === 'provider_cancel_unconfirmed'
                        ? 'providerCancelUnconfirmed'
                        : 'unconfirmed');
            }
            debugLog.error("Studio", "Failed to confirm task change:", err);
        } finally {
            store.setRunningOp(operation, false);
        }
    }, [currentProject?.id, requiresReadback, currentProjectReadGuard, setCommandError]);

    const handleToggleStar = useCallback(async (task: VideoTask, next: boolean) => {
        if (!currentProject?.id) return;
        await confirmTaskChange(task.id,
            () => api.annotateVideoTask(currentProject.id, task.id, { is_starred: next }), ['is_starred']);
    }, [currentProject?.id, confirmTaskChange]);

    // Pin/unpin only changes the hero after the committed project confirms it.
    const changeActiveVideo = useCallback(async (frameId: string, videoId: string | null) => {
        if (!currentProject?.id || requiresReadback) return;
        const projectId = currentProject.id;
        const isCurrent = currentProjectReadGuard(projectId);
        const operation = `video-selection:${encodeURIComponent(requireActiveRuntimeEnvironment().key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(frameId)}`;
        const store = useProjectStore.getState();
        if (!isCurrent() || store.runningOps[operation]) return;
        store.setRunningOp(operation, true);
        let saved = false;
        try {
            const response = videoId === null ? await api.unpinVideo(projectId, frameId) : await api.selectVideo(projectId, frameId, videoId);
            saved = true;
            if (!isCurrent()) return;
            await confirmRawProjectAck(projectId, response, { isCurrent });
            if (!isCurrent()) return;
            const confirmed = useProjectStore.getState().currentProject!;
            const frame = confirmed.frames.find(candidate => candidate.id === frameId);
            if (!frame) throw new ProjectReadbackError('Selected frame is no longer present');
            const fresh = frameToShotNode(frame, confirmed.video_tasks ?? [], 'direct_r2v');
            setShots(prev => prev.map(shot => shot.id === frameId ? {
                ...shot, videoUrl: fresh.videoUrl, videoStatus: fresh.videoStatus,
                videoTaskId: fresh.videoTaskId, isVideoPinned: fresh.isVideoPinned, finalTakeId: fresh.finalTakeId,
            } : shot));
        } catch (err) {
            if (isCurrent()) setCommandError(saved ? 'savedReadFailed' : 'unconfirmed');
            debugLog.error("Studio", "Failed to confirm active take:", err);
        } finally {
            store.setRunningOp(operation, false);
        }
    }, [currentProject?.id, currentProjectReadGuard, requiresReadback, setCommandError]);

    const handleSetLabel = useCallback(async (task: VideoTask, next: string | null) => {
        if (!currentProject?.id) return;
        await confirmTaskChange(task.id,
            () => api.annotateVideoTask(currentProject.id, task.id, next ? { label: next } : { clear_label: true }), ['label']);
    }, [currentProject?.id, confirmTaskChange]);

    const handleCancelTask = useCallback(async (taskId: string) => {
        if (!currentProject?.id) return;
        await confirmTaskChange(taskId, () => api.cancelVideoTask(currentProject.id, taskId), ['status', 'error']);
    }, [currentProject?.id, confirmTaskChange]);

    const handleContinueProviderTask = useCallback(async (task: VideoTask) => {
        if (!currentProject?.id || requiresReadback || !canContinueProviderTask(task) || !task.provider_execution) return;
        const projectId = currentProject.id;
        const expectedAttempt = task.provider_execution.attempt_id;
        const expectedFingerprint = task.provider_execution.fingerprint;
        const environment = requireActiveRuntimeEnvironment();
        const isCurrent = currentProjectReadGuard(projectId, environment);
        const operation = `provider-continue:${encodeURIComponent(environment.key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(task.id)}:${encodeURIComponent(expectedAttempt)}`;
        const store = useProjectStore.getState();
        if (!isCurrent() || store.runningOps[operation]) return;
        store.setRunningOp(operation, true);
        try {
            setCommandError(null);
            const response = await api.continueProviderVideoTask(projectId, task.id, {
                expected_attempt_id: expectedAttempt,
                expected_fingerprint: expectedFingerprint,
            });
            if (!isCurrent()) return;
            const record = response.provider_execution;
            const acknowledgedPhaseRank = providerContinuePhaseRank(record?.phase);
            if (response.id !== task.id || record?.attempt_id !== expectedAttempt
                || record.fingerprint !== expectedFingerprint || acknowledgedPhaseRank == null) {
                throw new Error("Invalid provider continuation acknowledgement");
            }
            await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
                if (!isCurrent()) return false;
                const confirmed = snapshot.video_tasks?.find(candidate => candidate.id === task.id);
                const confirmedRecord = confirmed?.provider_execution;
                const confirmedPhaseRank = providerContinuePhaseRank(confirmedRecord?.phase);
                return !!confirmed
                    && confirmedRecord?.attempt_id === expectedAttempt
                    && confirmedRecord.fingerprint === expectedFingerprint
                    && confirmedPhaseRank != null
                    && confirmedPhaseRank >= acknowledgedPhaseRank;
            } });
        } catch (err) {
            if (isCurrent()) {
                const response = apiErrorLike(err).response;
                const code = response?.data?.code;
                setCommandError(err instanceof ProjectReadbackError
                    ? 'savedReadFailed'
                    : response?.status === 409 && (code === 'provider_continue_not_allowed'
                        || code === 'provider_operation_running'
                        || code === 'provider_recovery_unavailable')
                        ? 'providerContinueUnavailable'
                        : 'unconfirmed');
            }
            debugLog.error("Studio", "Failed to continue provider video task:", err);
        } finally {
            store.setRunningOp(operation, false);
        }
    }, [currentProject?.id, currentProjectReadGuard, requiresReadback, setCommandError]);

    // Retry = fire a fresh batch of 1 for the shot owning this task,
    // reusing the task's params as best-effort. After Phase 2 the
    // task→shot mapping is direct via task.frame_id; falls back to
    // current ParamsSection state if we can't find the owner.
    const handleRetryTask = useCallback(async (task: VideoTask) => {
        const ownerIdx = task.frame_id
            ? shots.findIndex((s) => s.id === task.frame_id)
            : -1;
        if (ownerIdx < 0) return;
        await generateVideoBatch(ownerIdx, 1);
    }, [shots, generateVideoBatch]);

    // Click on a candidate thumb: plain click = preview (open new
    // window for v1), shift-click = toggle compare-selection.
    const handleCandidateClick = useCallback((task: VideoTask, mods: { shift: boolean; meta: boolean }) => {
        if (task.status !== "completed") return;
        if (mods.shift) {
            setCompareSelectedIds(prev => {
                const next = new Set(prev);
                if (next.has(task.id)) next.delete(task.id);
                else next.add(task.id);
                return next;
            });
            return;
        }
        const frame = currentProject?.frames?.find((f) => f.dubbed_video_task_id === task.id);
        const url = frame ? selectVerifiedAppliedDubUrlForTask(frame, task) || task.video_url : task.video_url;
        if (url) {
            window.open(getAssetUrl(url), "_blank", "noopener");
        }
    }, [currentProject]);

    // 复用此批参数: copy a batch's model + neg_prompt into videoConfig,
    // so the next Generate uses the same recipe. We don't change count
    // here — count remains the per-shot knob the user chose.
    const handleReuseBatchParams = useCallback((batch: BatchSummary) => {
        const first = batch.tasks[0];
        if (!first) return;
        setVideoConfig(prev => {
            const updated = { ...prev };
            // Decide which slot the batch's model lives in (I2V or R2V).
            if (VIDEO_R2V_MODELS.some(m => m.id === first.model)) {
                updated.r2vModel = first.model!;
            } else if (VIDEO_I2V_MODELS.some(m => m.id === first.model)) {
                updated.model = first.model!;
            }
            if (first.duration) updated.duration = first.duration;
            if (first.resolution) updated.resolution = first.resolution;
            if (first.negative_prompt !== undefined) updated.negativePrompt = first.negative_prompt;
            return updated;
        });
    }, []);

    // Queue's jump-to-shot: scroll the shot's wrapper into view AND
    // expand the shot panel + its sections (otherwise jumping to a
    // collapsed shot lands the user on a 1-line strip and they have
    // to expand manually).
    const handleJumpToShot = useCallback((frameId: string) => {
        setExpandedShots(prev => {
            if (prev.has(frameId)) return prev;
            const next = new Set(prev);
            next.add(frameId);
            return next;
        });
        // Force inner sections open too — feels right when arriving from
        // a queue task: you want to see Params + Candidates for that shot.
        overridePanelSectionState([frameId], ["params", "candidates"], true);
        // Scroll after the next paint so the newly-expanded body is
        // measured correctly. RAF is sufficient — we don't need the
        // full layout effect cycle.
        requestAnimationFrame(() => {
            const el = shotWrapperRefs.current.get(frameId);
            if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    }, []);

    // Active candidate URL resolver — many backend video URLs are
    // relative paths needing the asset prefix to render in <video>.
    const resolveAssetUrl = useCallback((u: string) => getAssetUrl(u), []);

    // In-flight shot count for trailing slot stat
    const totalInFlight = useMemo(
        () => Object.values(shotCounts).reduce((acc: number, c: any) => acc + (c?.processing ?? 0) + (c?.pending ?? 0), 0),
        [shotCounts],
    );
    const providerPendingEntries = Object.entries(providerPendingByFrame);

    return (
        <>
        {providerPendingEntries.length > 0 ? (
            <div className="relative z-20 border-b border-warning/30 bg-warning/10 px-6 py-3 text-sm text-warning-fg">
                <div className="flex flex-wrap items-center gap-3">
                    <span>{t("providerRetrySubmissionHint")}</span>
                    {providerPendingEntries.map(([frameId, pending]) => (
                        <button
                            key={pending.clientRequestId}
                            type="button"
                            onClick={() => submitProviderRetry(frameId)}
                            disabled={command.busy}
                            className="rounded-full border border-warning/40 bg-warning/15 px-3 py-1 font-mono text-xs font-semibold text-warning-fg transition-colors hover:bg-warning/25 disabled:opacity-50"
                        >
                            {t("providerRetrySubmission")}
                        </button>
                    ))}
                </div>
            </div>
        ) : null}
        {/* Layout v4: outer horizontal split. Custom page header belongs
            to main column (not page-wide), so the right TaskQueuePanel can
            be a true floor-to-ceiling sidebar with its own SidePanelHeader. */}
        <fieldset disabled={command.busy || requiresReadback} className="h-full flex overflow-hidden relative min-w-0">
        {/* Main column — pushed (compressed) when the queue panel opens
            so the queue doesn't overlay content. Bloom/grain now global in
            ProjectClient so the whole pipeline shares one atmosphere. */}
        <div className="relative z-10 flex-1 flex flex-col overflow-hidden min-w-0">
            {command.busy && <p role="status" className="px-6 py-2 text-sm">{command.refining ? t('genInFlight') : tCommand('saving')}</p>}
            {currentProject?.sharedAssetsNeedRefresh && <p role="status" className="px-6 py-2 text-sm">{tCommand('seriesAssetsPending')}</p>}
            {command.error && <p role="alert" className="px-6 py-2 text-sm text-status-failed-fg">{currentProject?.title}: {tCommand(command.error)}</p>}
            {command.error === 'editConflict' && frameDraftsRef.current.size > 0
                && !Array.from(editFailuresRef.current).some(key => key.startsWith('workbench:') || key.startsWith('invalid-field:')) && (
                <div className="mx-6 mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-glass-border bg-white/5 px-3 py-2 text-sm">
                    <span role="status">{tCommand(conflictRecovery === 'ready' ? 'conflictReady' : conflictRecovery === 'readFailed' ? 'conflictReadFailed' : 'conflictDraftKept')}</span>
                    <button type="button" className="glass-button px-3 py-1.5" disabled={command.editsPending || conflictRecovery === 'reading'} onClick={() => void readConflictState()}>
                        {tCommand('readLatest')}
                    </button>
                    {conflictRecovery === 'ready' && <button type="button" className="glass-button px-3 py-1.5" onClick={() => void saveConflictDrafts()}>{tCommand('saveRetainedDraft')}</button>}
                </div>
            )}
            {/* Unified page header (shared StepPageHeader) */}
            <StepPageHeader
                stepNumber={4}
                englishName="STORYBOARD R2V"
                title={tStep("storyboardTitle")}
                subtitle={tStep("storyboardSubtitle")}
                pills={(
                    <>
                        {currentProject?.art_direction?.style_config?.name ? (
                            <StepPill label={t("artStyleLabel")} value={currentProject.art_direction.style_config.name} />
                        ) : null}
                        <StepPill label={t("currentModel")} value={currentModelName} />
                    </>
                )}
                trailing={(
                    <>
                        <TaskQueueButton
                            inFlightCount={inFlightTaskCount}
                            open={queueOpen}
                            onToggle={() => setQueueOpen(v => !v)}
                        />
                        <button
                            type="button"
                            onClick={() => setGenDialogOpen(true)}
                            disabled={generating}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 font-sans text-[0.8125rem] font-semibold text-on-accent shadow-[var(--btn-pri-glow),inset_0_1.5px_0_rgba(255,255,255,0.14)] transition-all duration-fast ease-out-quart hover:bg-primary-hover disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            {generating ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                            <span>{generating ? t("genInFlight") : t("genShots")}</span>
                        </button>
                    </>
                )}
            />
            {/* Top Toolbar — mock-aligned: count on the left, expand/collapse pills on the right */}
            <div className="flex flex-wrap items-center gap-3 px-4 py-3 shrink-0 sm:px-6">
                <div className="flex items-center gap-3">
                    <span className="font-mono text-[11px] tracking-[0.04em] text-text-secondary">
                        <span className="text-foreground font-medium">{shots.length}</span>
                        <span className="ml-1.5 uppercase">{shots.length === 1 ? t("shot") : t("shots")}</span>
                        {totalInFlight > 0 ? <span className="ml-2 text-status-processing-fg">· {totalInFlight} {t("inFlightShort")}</span> : null}
                    </span>
                    <motion.button
                        whileHover={{ scale: 1.04 }}
                        whileTap={{ scale: 0.96 }}
                        onClick={() => addShot(shots.length - 1)}
                        className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-primary hover:text-primary/80 transition-colors"
                    >
                        <Plus size={13} strokeWidth={2} />
                        {t("addShot")}
                    </motion.button>
                </div>
                {shots.length > 1 ? (
                    <div className="ml-auto flex items-center gap-2">
                        <button
                            type="button"
                            onClick={expandAllShots}
                            title={t("expandAll")}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-glass-border bg-transparent px-3.5 font-mono text-[13px] uppercase tracking-[0.06em] text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            <PanelBottomOpen size={12} strokeWidth={1.8} />
                            {t("expandAll")}
                        </button>
                        <button
                            type="button"
                            onClick={collapseAllShots}
                            title={t("collapseAll")}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-glass-border bg-transparent px-3.5 font-mono text-[13px] uppercase tracking-[0.06em] text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            <PanelBottomClose size={12} strokeWidth={1.8} />
                            {t("collapseAll")}
                        </button>
                    </div>
                ) : null}
            </div>

            <GenerationBanner
                state={bannerState}
                phase1Captions={PHASE1_CAPTIONS}
                refineProgress={refineProgress}
                dialogueProgress={dialogueProgress}
                summary={bannerSummary}
                onGenerateDialogue={handleBatchDialogue}
                dialogueBusy={audioBusy}
            />

            <div className="flex-1 overflow-y-auto px-5 pt-1.5 pb-10 space-y-5 sm:px-7">
                {shots.length === 0 && (
                    <div className="h-full min-h-[300px] flex flex-col items-center justify-center text-center px-6">
                        <div className="rounded-2xl border border-glass-border bg-glass p-8 max-w-lg">
                            <div className="mx-auto w-12 h-12 grid place-items-center rounded-full bg-primary/10 border border-primary/30 mb-4">
                                <Wand2 size={20} className="text-primary" />
                            </div>
                            <h3 className="text-display font-medium text-foreground">{t("emptyTitle")}</h3>
                            <p className="text-body-sm text-text-secondary mt-1.5 max-w-md mx-auto leading-relaxed">
                                {t("emptyBody")}
                            </p>
                            <div className="mt-5 flex items-center justify-center gap-2">
                                <button
                                    type="button"
                                    aria-label={generating ? t("genInFlight") : t("emptyTitle")}
                                    onClick={() => setGenDialogOpen(true)}
                                    disabled={generating}
                                    className="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-md bg-primary text-white border border-primary/65 shadow-[inset_0_1.5px_0_rgba(255,255,255,0.14)] hover:bg-primary-hover disabled:opacity-40 transition-colors text-[0.8125rem] font-semibold"
                                >
                                    {generating ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                                    {generating ? t("genInFlight") : t("emptyCTA")}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => addShot(-1)}
                                    className="inline-flex items-center gap-1.5 px-4 py-2.5 rounded-md bg-glass border border-glass-border text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors text-[0.75rem]"
                                >
                                    <Plus size={12} />
                                    {t("emptyManualAdd")}
                                </button>
                            </div>
                        </div>
                    </div>
                )}
                {shots.map((shot, index) => {
                    const shotTasks = tasksForShot(shot);
                    const shotInFlight = shotTasks.filter(
                        (t) => t.status === "pending" || t.status === "processing",
                    ).length;
                    const paramsState = paramsStateForShot(shot);
                    const isI2vTab = shot.tabMode === "t2i_i2v";
                    const modelList = shot.tabMode === "direct_r2v" ? VIDEO_R2V_MODELS : VIDEO_I2V_MODELS;
                    return (
                    /* Plain div (was motion.div) — staggered enter
                       animation re-fired every time the user switched
                       step tabs and came back, causing a noticeable
                       全 list opacity flicker. ShotCard hover micro-
                       motion is kept inside the card itself. */
                    <StoryboardFrameImageRequests key={shot.id} projectId={projectId} frameId={shot.id} readback={readFrameImage} onBusy={recordFrameImageBusy}>
                    {requests => <div ref={(el) => { shotWrapperRefs.current.set(shot.id, el); }}>
                        <ShotCard
                            structureDisabled={command.busy}
                            shot={requests.busy ? { ...shot, t2iStatus: 'pending' } : shot}
                            index={index}
                            totalShots={shots.length}
                            characters={characters}
                            scenes={scenes}
                            props={props}
                            onUpdatePrompt={(prompt) => updatePrompt(index, prompt)}
                            onUpdateField={(field, value) => handleUpdateField(index, field, value)}
                            durationEditorConfig={durationEditorCfg}
                            onGenerateT2I={() => generateT2I(index, requests)}
                            onGenerateVideo={() => generateVideoBatch(index, 1, paramsState)}
                            onDelete={() => deleteShot(index)}
                            onMoveUp={() => moveShot(index, "up")}
                            onMoveDown={() => moveShot(index, "down")}
                            onDuplicate={() => duplicateShot(index)}
                            onSetTabMode={(mode) => setTabMode(index, mode)}
                            onOpenDrawer={() => setDrawerState({ isOpen: true, targetShotIndex: index })}
                            onInsertAsset={(type, name) => {
                                // Direct chip insert (same as chip bar logic, delegated to chip bar)
                                const tag = `[${type}:${name}]`;
                                updatePrompt(index, shots[index].prompt + " " + tag);
                            }}
                            onCancelVideo={shot.videoTaskId ? () => handleCancelTask(shot.videoTaskId!) : undefined}
                            expanded={expandedShots.has(shot.id)}
                            onToggleExpanded={() => toggleShotExpanded(shot.id)}
                            /* PR-3c · 闭环生成: ShotCard 内全宽生成行 + count selector.
                               canGenerate: direct_r2v 需 prompt; t2i_i2v 还需 first frame. */
                            generateCount={paramsState.count}
                            genSummary={`${
                                shot.tabMode === "direct_r2v"
                                    ? (VIDEO_R2V_MODELS.find(m => m.id === videoConfig.r2vModel)?.name ?? videoConfig.r2vModel ?? "")
                                    : (VIDEO_I2V_MODELS.find(m => m.id === videoConfig.model)?.name ?? videoConfig.model ?? "")
                            } · ${paramsState.duration}s`}
                            canGenerate={
                                shot.prompt.trim().length > 0
                                && (
                                    shot.tabMode === "direct_r2v"
                                    || !!shot.t2iImageUrl
                                    || !!shot.imageUrl
                                    || (shot.t2iImageUrls?.length ?? 0) > 0
                                )
                            }
                            onSetGenerateCount={(n) => handleShotParamsChange(shot, { ...paramsState, count: n })}
                            onGenerateBatch={(n) => generateVideoBatch(index, n, paramsState)}
                            generationBlockedMessage={generationBlockedState()?.message}
                            generationBlockedBusy={generationBlockedState()?.busy}
                            inFlightCount={shotInFlight}
                            onRefineFrame={() => handleRefineFrame(shot.id)}
                            onUnpinVideo={() => changeActiveVideo(shot.id, null)}
                            onUpdateDialogue={async (text: string) => {
                                try { await updateInlineDialogue(shot.id, text); }
                                catch (e) { debugLog.error("Studio", "update dialogue failed", e); }
                            }}
                        />
                        {/* PR-3j · Frame-level dialogue audio row. Only renders
                            when the frame has dialogue text; resolves the
                            bound character's voice_id and tracks stale state. */}
                        {(() => {
                            const frame = currentProject?.frames?.find((f: any) => f.id === shot.id);
                            if (!frame) return null;
                            const dialogueText = frame?.dialogue_structured?.line || frame?.dialogue;
                            const selectedTask = selectCurrentVideoTask(frame, currentProject?.video_tasks ?? []).task;
                            const hasVideoTask = !!selectedTask;
                            // Show row when dialogue exists, or when video exists (dub available)
                            if (!dialogueText?.trim() && !hasVideoTask) return null;
                            const charId = Array.isArray(frame.character_ids) ? frame.character_ids[0] : null;
                            const speaker = charId ? characters.find((c: any) => c.id === charId) : null;
                            return (
                                <div className="mx-5 mb-4">
                                    <DialogueAudioRow
                                        scriptId={currentProject!.id}
                                        frameId={frame.id}
                                        dialogue={dialogueText}
                                        voiceId={speaker?.voice_id}
                                        audioUrl={frame.audio_url}
                                        frameMediaRefs={frame.media_refs}
                                        audioError={frame.audio_error}
                                        onBeforeGenerate={settleAudioEdits}
                                        retainedDialogueDraft={frameDraftsRef.current.get(frame.id)?.patch.dialogue}
                                        onDialogueDraftChange={(text, dirty) => {
                                            rememberFrameDraft(frame.id, { dialogue: text.trim() });
                                            if (!dirty) acknowledgeFrameDraft(frame.id, { dialogue: text.trim() });
                                            setEditEpoch(epoch => epoch + 1);
                                        }}
                                        onUpdateDialogue={text => updateInlineDialogue(frame.id, text)}
                                        videoUrl={selectedTask ? getVideoTaskUrl(selectedTask) ?? undefined : undefined}
                                        videoTaskId={selectedTask?.id ?? undefined}
                                        videoTaskMediaRefs={selectedTask?.media_refs ?? undefined}
                                        previewVideoUrl={frame.preview_video_url}
                                        dubbedVideoUrl={frame.dubbed_video_url}
                                        dubOffsetMs={frame.dub_offset_ms ?? 0}
                                        dubPreviewSource={frame.dub_preview_source}
                                        dubAppliedSource={frame.dub_applied_source}
                                        onPreviewDub={async (videoTaskId: string, offsetMs: number) => {
                                            if (!projectId) return;
                                            const isCurrent = currentProjectReadGuard(projectId);
                                            if (!isCurrent()) return;
                                            await settleAudioEdits();
                                            if (!isCurrent()) return;
                                            const result = await api.previewDub(projectId, frame.id, videoTaskId, offsetMs);
                                            if (!isCurrent()) return;
                                            await confirmRawProjectAck(projectId, result, { isCurrent });
                                        }}
                                        onApplyDub={async () => {
                                            if (!projectId) return;
                                            const isCurrent = currentProjectReadGuard(projectId);
                                            if (!isCurrent()) return;
                                            const result = await api.applyDub(projectId, frame.id);
                                            if (!isCurrent()) return;
                                            await confirmRawProjectAck(projectId, result, { isCurrent });
                                        }}
                                        onRevertDub={async () => {
                                            if (!projectId) return;
                                            const isCurrent = currentProjectReadGuard(projectId);
                                            if (!isCurrent()) return;
                                            const result = await api.revertDub(projectId, frame.id);
                                            if (!isCurrent()) return;
                                            await confirmRawProjectAck(projectId, result, { isCurrent });
                                        }}
                                    />
                                </div>
                            );
                        })()}
                        {/* Attached workbench: t2i_i2v 模式下渲染顺序为
                            Step 1 (T2ISubsection) → Step 2 (ParamsSection)
                            → CandidatesSection；direct_r2v 模式无 T2I 区，
                            ParamsSection → CandidatesSection。
                            Spec: docs/design/r2v-workflow-v3-unified.md §4.3.2
                            (PR-3a · Option A 最小修复)
                            v1 不加 explicit section header / first-frame
                            thumbnail in Step 2 — 看用户反馈再升级 v2. */}
                        {expandedShots.has(shot.id) ? (
                        <div className="mx-5 mb-[18px] motion-safe:animate-[shotPanelIn_220ms_cubic-bezier(0.22,1,0.36,1)_both]">
                            {isI2vTab ? (
                                <div>
                                    <T2ISubsection
                                        imageUrls={shot.t2iImageUrls ?? []}
                                        selectedIndex={shot.t2iSelectedIndex ?? 0}
                                        storyboardFrameUrl={shot.imageUrl || undefined}
                                        promptIsEmpty={!shot.prompt.trim()}
                                        generating={requests.busy || shot.t2iStatus === "pending" || shot.t2iStatus === "processing"}
                                        generationDisabled={requests.reading || requests.unknown || requests.readFailed || requests.readbackFailed}
                                        inFlightTaskId={shot.t2iTaskId}
                                        inFlightStatus={shot.t2iStatus}
                                        onSelect={(i) => setShots(prev => prev.map((s, j) => {
                                            if (j !== index) return s;
                                            const next = setActiveT2IIndex(s, i);
                                            persistWorkbench(s.id, {
                                                t2i_selected_index: next.t2iSelectedIndex ?? 0,
                                            });
                                            return next;
                                        }))}
                                        onRemove={(i) => setShots(prev => prev.map((s, j) => {
                                            if (j !== index) return s;
                                            const next = removeT2IImage(s, i);
                                            persistWorkbench(s.id, {
                                                t2i_image_urls: next.t2iImageUrls ?? [],
                                                t2i_selected_index: next.t2iSelectedIndex ?? 0,
                                            });
                                            return next;
                                        }))}
                                        onGenerate={() => generateT2I(index, requests)}
                                        onUpload={async (file) => {
                                            // Upload only targets committed frames. The add-shot CTA
                                            // creates the frame first; upload no longer hides a lazy
                                            // createFrame write behind media upload.
                                            if (!currentProject) return { code: "network", detail: "no current project" };
                                            const projectId = currentProject.id;
                                            const frameId = shot.id;
                                            if (frameId.startsWith("shot_") || !currentProject.frames?.some(frame => frame.id === frameId)) {
                                                return { code: "not_found", detail: "请先保存/创建镜头后再上传首帧" };
                                            }
                                            const operation = `frame-image-upload:${encodeURIComponent(requireActiveRuntimeEnvironment().key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(frameId)}`;
                                            const store = useProjectStore.getState();
                                            const isCurrent = currentProjectReadGuard(projectId);
                                            if (!isCurrent() || store.runningOps[operation] || requiresReadback) {
                                                return { code: "network", detail: tCommand("pendingEdits") };
                                            }
                                            store.setRunningOp(operation, true);
                                            let uploadedFrame: { id?: unknown; updated_at?: unknown; t2i_image_urls?: unknown; t2i_selected_index?: unknown } | null = null;
                                            try {
                                                uploadedFrame = await api.uploadT2IFrame(projectId, frameId, file);
                                                if (!uploadedFrame || uploadedFrame.id !== frameId || !Array.isArray(uploadedFrame.t2i_image_urls)
                                                    || typeof uploadedFrame.updated_at !== 'number' || !Number.isFinite(uploadedFrame.updated_at)
                                                    || !Number.isSafeInteger(uploadedFrame.t2i_selected_index)) {
                                                    throw new Error("Invalid upload acknowledgement");
                                                }
                                                if (!isCurrent()) return undefined;
                                                await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
                                                    if (!isCurrent()) return false;
                                                    const confirmed = snapshot.frames.find(candidate => candidate.id === frameId);
                                                    const confirmedUrls = Array.isArray(confirmed?.t2i_image_urls) ? confirmed.t2i_image_urls : [];
                                                    // Signatures can rotate between ACK and GET. The
                                                    // exact persisted frame receipt confirms this upload.
                                                    return confirmed?.updated_at === uploadedFrame!.updated_at
                                                        && confirmed?.t2i_selected_index === uploadedFrame!.t2i_selected_index
                                                        && confirmedUrls.length > 0
                                                        && confirmedUrls.length === (uploadedFrame!.t2i_image_urls as unknown[]).length;
                                                } });
                                                if (!isCurrent()) return undefined;
                                                const confirmed = useProjectStore.getState().currentProject;
                                                const frame = confirmed?.frames.find(candidate => candidate.id === frameId);
                                                if (!frame) throw new ProjectReadbackError('Uploaded frame is no longer present');
                                                const fresh = frameToShotNode(frame, confirmed?.video_tasks ?? [], shot.tabMode);
                                                setShots(prev => prev.map(s => s.id === frameId ? {
                                                    ...s,
                                                    imageUrl: fresh.imageUrl,
                                                    t2iImageUrls: fresh.t2iImageUrls,
                                                    t2iSelectedIndex: fresh.t2iSelectedIndex,
                                                    t2iImageUrl: fresh.t2iImageUrl,
                                                    t2iStatus: "completed",
                                                } : s));
                                                return undefined;
                                            } catch (err: unknown) {
                                                debugLog.error("Studio", "T2I upload failed", err);
                                                const readbackFailed = err instanceof ProjectReadbackError;
                                                if (isCurrent()) setCommandError(readbackFailed ? 'savedReadFailed' : 'unconfirmed');
                                                const errorResponse = typeof err === "object" && err !== null && "response" in err
                                                    ? (err as { response?: { status?: number; data?: { detail?: unknown } } }).response
                                                    : undefined;
                                                const status = errorResponse?.status;
                                                const detail = readbackFailed ? tCommand("savedReadFailed") : errorResponse?.data?.detail
                                                    || (err instanceof Error ? err.message : undefined)
                                                    || `HTTP ${status ?? "?"}`;
                                                if (status === 413) return { code: "size", detail: String(detail) };
                                                if (status === 415) return { code: "type", detail: String(detail) };
                                                if (status === 404) return { code: "not_found", detail: String(detail) };
                                                if (status && status >= 500) return { code: "server", detail: String(detail) };
                                                return { code: readbackFailed ? "server" : "network", detail: String(detail) };
                                            } finally {
                                                store.setRunningOp(operation, false);
                                            }
                                        }}
                                        lastFrameEnabled={isOrderedStudioProviderVideoModel(videoConfig.model)}
                                        lastFrameUrl={shot.lastFrameUrl}
                                        onLastFrameUpload={async (file) => {
                                            if (!currentProject) return { code: "network", detail: "no current project" };
                                            const projectId = currentProject.id;
                                            const frameId = shot.id;
                                            if (frameId.startsWith("shot_") || !currentProject.frames?.some(frame => frame.id === frameId)) {
                                                return { code: "not_found", detail: "请先保存/创建镜头后再上传尾帧" };
                                            }
                                            const operation = `last-frame-upload:${encodeURIComponent(requireActiveRuntimeEnvironment().key)}:${encodeURIComponent(projectId)}:${encodeURIComponent(frameId)}`;
                                            const store = useProjectStore.getState();
                                            const isCurrent = currentProjectReadGuard(projectId);
                                            if (!isCurrent() || store.runningOps[operation] || requiresReadback) {
                                                return { code: "network", detail: tCommand("pendingEdits") };
                                            }
                                            store.setRunningOp(operation, true);
                                            try {
                                                const uploaded = await api.uploadFile(file);
                                                if (!isCurrent()) return undefined;
                                                const url = typeof uploaded.local_url === "string" ? uploaded.local_url : "";
                                                if (!url) throw new Error("Invalid last-frame upload acknowledgement");
                                                await trackFrameEdit(projectId, enqueueFrameEdit(projectId, `workbench:${frameId}`, { last_frame_url: url }, async patch => {
                                                    if (!isCurrent()) throw new FrameReadbackError('Saved workbench editor is no longer current');
                                                    const receipt = await api.updateFrameWorkbench(projectId, frameId, patch);
                                                    if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)
                                                        || (receipt as { id?: unknown }).id !== frameId
                                                        || typeof (receipt as { updated_at?: unknown }).updated_at !== "number"
                                                        || (receipt as { last_frame_url?: unknown }).last_frame_url !== url) {
                                                        throw new Error("Invalid last-frame workbench acknowledgement");
                                                    }
                                                    await readProjectOnce(projectId, undefined, { isCurrent: snapshot => {
                                                        if (!isCurrent()) return false;
                                                        const confirmed = snapshot.frames.find(candidate => candidate.id === frameId) as { last_frame_url?: unknown; updated_at?: unknown } | undefined;
                                                        return confirmed?.last_frame_url === url
                                                            && confirmed?.updated_at === (receipt as { updated_at: number }).updated_at;
                                                    } });
                                                    return receipt;
                                                }));
                                                if (!isCurrent()) return undefined;
                                                setShots(prev => prev.map(s => s.id === frameId ? { ...s, lastFrameUrl: url } : s));
                                                return undefined;
                                            } catch (err: unknown) {
                                                debugLog.error("Studio", "Last frame upload failed", err);
                                                if (isCurrent()) setCommandError(err instanceof ProjectReadbackError ? 'savedReadFailed' : 'unconfirmed');
                                                const detail = err instanceof Error ? err.message : `HTTP ?`;
                                                return { code: err instanceof ProjectReadbackError ? "server" : "network", detail: String(detail) };
                                            } finally {
                                                store.setRunningOp(operation, false);
                                            }
                                        }}
                                        onLastFrameClear={() => {
                                            if (!currentProject) return;
                                            const frameId = shot.id;
                                            setShots(prev => prev.map(s => s.id === frameId ? { ...s, lastFrameUrl: undefined } : s));
                                            persistWorkbench(frameId, { last_frame_url: null });
                                        }}
                                        resolveUrl={resolveAssetUrl}
                                    />
                                </div>
                            ) : null}
                            {/* Step 2 · 生成视频 (ParamsSection) — always shown
                                when shot expanded; renders below Step 1 in
                                t2i_i2v mode, and is the only section above
                                candidates in direct_r2v mode. */}
                            <div className={isI2vTab ? "border-t border-glass-border" : ""}>
                                <ParamsSection
                                    shotId={shot.id}
                                    modelList={modelList}
                                    title={isI2vTab ? "I2V Params" : "R2V Params"}
                                    params={paramsState}
                                    onChange={(next) => handleShotParamsChange(shot, next)}
                                    inFlightCount={shotInFlight}
                                    errorMessage={shotErrors[shot.id] ?? null}
                                />
                            </div>
                            <div className="border-t border-glass-border">
                                <CandidatesSection
                                    shotId={shot.id}
                                    tasks={shotTasks}
                                    activeModel={paramsState.model}
                                    compareSelectedIds={compareSelectedIds}
                                    activeTaskId={currentProject?.frames?.find((f: any) => f.id === shot.id)?.selected_video_id ?? null}
                                    dubbedVideoUrl={currentProject?.frames?.find((f: any) => f.id === shot.id)?.dubbed_video_url}
                                    dubbedVideoTaskId={currentProject?.frames?.find((f: any) => f.id === shot.id)?.dubbed_video_task_id}
                                    frameAudioUrl={currentProject?.frames?.find((f: any) => f.id === shot.id)?.audio_url}
                                    frameMediaRefs={currentProject?.frames?.find((f) => f.id === shot.id)?.media_refs}
                                    dubAppliedSource={currentProject?.frames?.find((f: any) => f.id === shot.id)?.dub_applied_source}
                                    onClickThumb={handleCandidateClick}
                                    onToggleStar={handleToggleStar}
                                    onSetLabel={handleSetLabel}
                                    onSetActive={(task) => changeActiveVideo(shot.id, task.id)}
                                    onCancel={(task) => handleCancelTask(task.id)}
                                    onRetry={handleRetryTask}
                                    onReuseBatchParams={handleReuseBatchParams}
                                    onOpenCompare={() => setCompareModalOpen(true)}
                                    resolveUrl={resolveAssetUrl}
                                />
                            </div>
                        </div>
                        ) : null}
                        <StudioImageRequestResults requests={requests} />
                    </div>}
                    </StoryboardFrameImageRequests>
                    );
                })}

                {/* Add shot at end */}
                <motion.button
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: Math.min(shots.length * 0.03, 0.3) }}
                    whileHover={{ scale: 1.005 }}
                    whileTap={{ scale: 0.995 }}
                    onClick={() => addShot(shots.length - 1)}
                    className="w-full py-3.5 border border-dashed border-glass-border hover:border-primary/40 rounded-xl text-text-secondary hover:text-primary text-sm font-medium transition-all duration-300 flex items-center justify-center gap-2 bg-glass hover:bg-hover-bg"
                >
                    <Plus size={16} strokeWidth={1.5} />
                    {t("addShot")}
                </motion.button>
            </div>

            {/* Asset Drawer (fixed overlay) */}
            <AssetDrawer
                isOpen={drawerState.isOpen}
                onClose={() => setDrawerState({ isOpen: false, targetShotIndex: null })}
                characters={characters}
                scenes={scenes}
                props={props}
                onSelectAsset={insertAssetFromDrawer}
            />
        </div>
        {/* Right-side Task Queue — pushes (does not overlay) the main
            column. Mounted in the layout flex, not as a fixed overlay,
            so width compression is automatic when it opens. */}
        <TaskQueuePanel
            open={queueOpen}
            onClose={() => setQueueOpen(false)}
            tasks={allVideoTasks}
            shotLabelByFrameId={shotLabelByFrameId}
            onJumpToShot={handleJumpToShot}
            onCancel={(task) => handleCancelTask(task.id)}
            onRetry={handleRetryTask}
            onContinue={handleContinueProviderTask}
        />
        {/* Compare modal — portaled to body to escape clipped/transformed
            ancestors. Shows once user has shift-selected ≥2 and clicked
            the floating Compare button in any CandidatesSection. */}
        {compareModalOpen && compareTasks.length >= 2 ? (
            <CompareModal
                tasks={compareTasks}
                onClose={() => setCompareModalOpen(false)}
                resolveUrl={resolveAssetUrl}
            />
        ) : null}
        {/* LLM-generate frames dialog */}
        <StoryboardGenerateDialog
            isOpen={genDialogOpen}
            onClose={() => setGenDialogOpen(false)}
            project={currentProject as any}
            existingShotCount={shots.length}
            onConfirm={handleSmartGenerate}
            onJumpToScript={() => {
                setGenDialogOpen(false);
                window.dispatchEvent(new CustomEvent("navigateStep", { detail: "script" }));
            }}
        />
        </fieldset>
        </>
    );
}
