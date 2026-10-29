"use client";

import { useState, useEffect, useRef, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";
import { Wand2, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { extractionPreviewOperationKey, useProjectStore } from "@/store/projectStore";
import { toast } from "@/store/toastStore";
import StepPageHeader, { StepPill } from "@/components/shared/StepPageHeader";
import PreviousEpisodeSummary from "@/components/modules/PreviousEpisodeSummary";
import ReconcileModal from "@/components/modules/ReconcileModal";
import { scriptDraftKey, useScriptDraftStore } from "@/store/scriptDraftStore";
import { readProjectOnce } from "@/lib/projectReadback";

let extractionSessionCounter = 0;

function hasOtherExtractionOperation(runningOps: Record<string, boolean>, operationKey: string): boolean {
    return Object.keys(runningOps).some(key => key !== operationKey
        && (key.startsWith('script-extraction-preview:') || key.startsWith('script-extraction-confirm:')));
}

export default function ScriptProcessor() {
    const ts = useTranslations("script");
    const tp = useTranslations("persistence");
    const currentProject = useProjectStore((state) => state.currentProject);
    const environment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
    const activeProject = environment && currentProject?.readEnvironment === environment.key ? currentProject : null;
    const isAnalyzing = useProjectStore((state) => state.isAnalyzing);
    const mountedRef = useRef(false);
    const activeExtractionSessionRef = useRef<string | null>(null);
    const reviewRequestRef = useRef<object | null>(null);
    const draftInteractionRef = useRef(0);
    const pendingAnalyzeRef = useRef<object | null>(null);
    const blurSaveRef = useRef<{
        key: string; generation: number; text: string; interaction: number; promise: Promise<void>;
    } | null>(null);
    const [isWaitingForSave, setIsWaitingForSave] = useState(false);
    const [review, setReview] = useState<{
        key: string; generation: number; text: string; baseline: number | undefined;
        status: 'reading' | 'ready' | 'failed'; savedText?: string; revision?: number;
    } | null>(null);

    // Initialize from project data. Fallback to snake_case original_text
    // in case the API wrapper didn't map it (e.g. raw axios response, or a
    // store update that spread the backend payload without re-mapping).
    const projectText = (activeProject?.originalText ?? (activeProject as any)?.original_text) || "";
    const projectId = activeProject?.id ?? "";
    const draftKey = environment && projectId ? scriptDraftKey(projectId) : null;
    const { initialize, changeText, save } = useScriptDraftStore();
    const draft = useScriptDraftStore(state => draftKey ? state.drafts[draftKey] : undefined) ?? {
        text: projectText, savedText: projectText, revision: activeProject?.revision,
        saving: false, error: null,
    };
    const script = draft.text;
    const currentReview = review?.key === draftKey && review?.generation === environment?.generation
        && review?.text === script && review?.baseline === draft.revision ? review : null;
    // A dirty draft keeps the version on which editing began. A newer store
    // snapshot must not silently grant permission to overwrite server changes.
    useEffect(() => {
        mountedRef.current = true;
        // Observe each owner change, including A→B→A changes batched by React.
        const unsubscribe = useProjectStore.subscribe((state, previous) => {
            if (state.currentProject?.id !== previous.currentProject?.id
                || state.currentProject?.readEnvironment !== previous.currentProject?.readEnvironment) {
                draftInteractionRef.current += 1;
                pendingAnalyzeRef.current = null;
                blurSaveRef.current = null;
                setIsWaitingForSave(false);
            }
        });
        return () => {
            unsubscribe();
            mountedRef.current = false;
            activeExtractionSessionRef.current = null;
            reviewRequestRef.current = null;
            pendingAnalyzeRef.current = null;
            blurSaveRef.current = null;
        };
    }, []);

    useEffect(() => {
        draftInteractionRef.current += 1;
        pendingAnalyzeRef.current = null;
        blurSaveRef.current = null;
        setIsWaitingForSave(false);
        reviewRequestRef.current = null;
        setReview(null);
    }, [draftKey, environment?.generation]);

    useEffect(() => {
        if (!draftKey || !environment || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        initialize(draftKey, projectText, activeProject?.revision);
    }, [draftKey, environment, activeProject?.revision, projectText, initialize]);

    useEffect(() => {
        const owner = useProjectStore.getState().pendingExtractionOwner;
        if (owner?.projectId === projectId
            && (owner.environment !== currentProject?.readEnvironment
                || owner.revision !== currentProject?.revision
                || owner.text !== script)) {
            useProjectStore.setState({
                pendingExtraction: null,
                pendingExtractionScript: null,
                pendingExtractionOwner: null,
            });
        }
    }, [projectId, script, currentProject?.revision, currentProject?.readEnvironment]);

    const saveDraft = (retry = false) => {
        if (!draftKey || !environment || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        const initial = useScriptDraftStore.getState().drafts[draftKey];
        const previous = blurSaveRef.current;
        const request = save(draftKey, projectId, retry);
        // A queued blur returns immediately; its original save Promise owns the
        // queue and resolves only after the latest blurred text is acknowledged.
        const promise = initial?.saving
            ? previous?.key === draftKey && previous.generation === environment.generation ? previous.promise : null
            : request;
        blurSaveRef.current = initial && promise ? {
            key: draftKey, generation: environment.generation, text: initial.text,
            interaction: draftInteractionRef.current, promise,
        } : null;
        return request;
    };

    const readLatest = async () => {
        if (!draftKey || !environment || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        const initial = useScriptDraftStore.getState().drafts[draftKey];
        if (!initial || initial.saving || currentReview?.status === 'reading') return;
        const token = {};
        reviewRequestRef.current = token;
        const captured = { key: draftKey, generation: environment.generation, text: initial.text, baseline: initial.revision };
        const isCurrent = () => {
            const latest = useScriptDraftStore.getState().drafts[draftKey];
            return mountedRef.current && reviewRequestRef.current === token
                && isCurrentRuntimeEnvironment(environment.key, environment.generation)
                && useProjectStore.getState().currentProject?.id === projectId
                && latest?.text === captured.text && latest.revision === captured.baseline;
        };
        setReview({ ...captured, status: 'reading' });
        try {
            const saved = await readProjectOnce(projectId, undefined, { isCurrent });
            if (isCurrent()) setReview({ ...captured, status: 'ready', savedText: saved.originalText, revision: saved.revision });
        } catch {
            if (isCurrent()) setReview({ ...captured, status: 'failed' });
        }
    };

    const adoptReview = (retainDraft: boolean) => {
        if (!draftKey || !currentReview || currentReview.status !== 'ready'
            || currentReview.revision === undefined || currentReview.savedText === undefined) return;
        const adopted = useScriptDraftStore.getState().adoptReviewed(
            draftKey, projectId, currentReview.savedText, currentReview.revision, retainDraft,
        );
        reviewRequestRef.current = null;
        setReview(null);
        if (adopted && retainDraft) void handleAnalyze();
    };

    // R2V v2 Phase 4 — ReconcileModal opens after a successful analyze
    // when the episode belongs to a series (series_id !== null).
    const [reconcileOpen, setReconcileOpen] = useState(false);

    useEffect(() => {
        const handler = () => setReconcileOpen(true);
        document.addEventListener("lumenx:openReconcile", handler);
        return () => document.removeEventListener("lumenx:openReconcile", handler);
    }, []);

    const handleAnalyze = async () => {
        if (!draftKey || !environment || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        if (!script.trim()) {
            toast.warning(ts("scriptEmpty"), {
                projectId: currentProject?.id,
                projectTitle: currentProject?.title,
            });
            return;
        }
        if (!currentProject?.id || currentProject.readEnvironment !== environment.key) return;
        const projectId = currentProject.id;
        const projectTitle = currentProject.title;
        if (pendingAnalyzeRef.current) return;
        const interaction = draftInteractionRef.current;
        let capturedDraft = useScriptDraftStore.getState().drafts[draftKey];
        let expectedRevision: number | undefined = currentProject.revision;
        if (capturedDraft?.saving) {
            const blurSave = blurSaveRef.current;
            if (!blurSave || blurSave.key !== draftKey || blurSave.generation !== environment.generation
                || blurSave.text !== script || blurSave.interaction !== interaction) return;
            const token = {};
            pendingAnalyzeRef.current = token;
            setIsWaitingForSave(true);
            try {
                await blurSave.promise;
                if (!mountedRef.current || pendingAnalyzeRef.current !== token
                    || draftInteractionRef.current !== interaction
                    || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
                capturedDraft = useScriptDraftStore.getState().drafts[draftKey];
                if (!capturedDraft || capturedDraft.saving || capturedDraft.error
                    || capturedDraft.text !== script || capturedDraft.savedText !== script) return;
                // Only this save's acknowledgement may advance the author's
                // baseline. A later project read cannot lend it a new revision.
                expectedRevision = capturedDraft.revision;
            } finally {
                if (pendingAnalyzeRef.current === token) {
                    pendingAnalyzeRef.current = null;
                    setIsWaitingForSave(false);
                }
            }
        }
        if (!capturedDraft || typeof capturedDraft.revision !== 'number' || capturedDraft.revision !== expectedRevision
            || !Number.isSafeInteger(capturedDraft.revision) || capturedDraft.revision < 0) {
            useScriptDraftStore.getState().markConflict(draftKey);
            return;
        }
        if (capturedDraft.error) return;
        const projectRevision = capturedDraft.revision;
        const sessionId = `${projectId}:${projectRevision}:${++extractionSessionCounter}`;
        const operationKey = extractionPreviewOperationKey(projectId);
        const store = useProjectStore.getState();
        if (store.runningOps[operationKey]) return;

        const capturedText = capturedDraft?.text ?? projectText;
        if (capturedText !== script || store.currentProject?.id !== projectId
            || store.currentProject.revision !== projectRevision || store.currentProject.readEnvironment !== environment.key) return;

        activeExtractionSessionRef.current = sessionId;
        store.setRunningOp(operationKey, true);
        useProjectStore.setState({ isAnalyzing: true });
        const toastId = toast.progress(ts("analyzingScript"), {
            projectId,
            projectTitle,
            body: ts("analyzingScriptBody"),
        });
        const isCurrentSession = () => {
            const latestProject = useProjectStore.getState().currentProject;
            const latestDraft = useScriptDraftStore.getState().drafts[draftKey];
            const latestText = latestDraft?.text ?? projectText;
            return mountedRef.current && activeExtractionSessionRef.current === sessionId
                && draftInteractionRef.current === interaction
                && latestProject?.id === projectId && latestProject.revision === projectRevision
                && latestProject.readEnvironment === environment.key && latestText === script
                && isCurrentRuntimeEnvironment(environment.key, environment.generation);
        };
        try {
            if (!isCurrentSession()) return;
            const preview = await api.extractPreview(projectId, script);
            if (!isCurrentSession()) return;
            toast.update(toastId, {
                kind: "success",
                title: ts("analysisDone"),
                body: ts("analysisDoneBody", {
                    c: preview.characters.length,
                    s: preview.scenes.length,
                    p: preview.props.length,
                }),
                autoCloseMs: 5000,
            });
            useProjectStore.setState({
                pendingExtraction: preview,
                pendingExtractionScript: script,
                pendingExtractionOwner: { projectId, environment: environment.key, generation: environment.generation, revision: projectRevision, text: script, sessionId },
            });
        } catch (error: any) {
            if (isCurrentSession()) {
                console.error("Failed to analyze script:", error);
                const errorMessage = error?.response?.data?.detail || error?.message || "未知错误";
                toast.update(toastId, {
                    kind: "error",
                    title: ts("analysisFailedShort"),
                    body: String(errorMessage).slice(0, 240),
                    action: {
                        label: ts("retry"),
                        onClick: () => {
                            if (isCurrentSession()) void handleAnalyze();
                        },
                    },
                });
            }
        } finally {
            if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                useProjectStore.getState().setRunningOp(operationKey, false);
                if (!hasOtherExtractionOperation(useProjectStore.getState().runningOps, operationKey)) {
                    useProjectStore.setState({ isAnalyzing: false });
                }
            }
        }
    };

    const tStep = useTranslations("stepHeader");

    return (
        // R2V v2 Phase 3: Script step = main editor (left) + Previously on... (right).
        // Entity extraction still runs via the trailing "提取实体" button —
        // parsed entities flow to series pools and surface in Cast step.
        <div className="flex h-full w-full overflow-hidden">
            {/* Left: main script editor */}
            <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                <StepPageHeader
                    stepNumber={1}
                    englishName="SCRIPT"
                    title={tStep("scriptTitle")}
                    subtitle={tStep("scriptSubtitle")}
                    pills={script ? (
                        <>
                            <StepPill label={ts("wordsLabel")} value={script.length} />
                            <StepPill label={ts("scenesLabel")} value={currentProject?.frames?.length ?? 0} />
                        </>
                    ) : null}
                    trailing={(
                        <button
                            type="button"
                            onClick={handleAnalyze}
                            disabled={!script || isAnalyzing || isWaitingForSave}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 font-sans text-[0.8125rem] font-semibold text-on-accent shadow-[var(--btn-pri-glow),inset_0_1.5px_0_rgba(255,255,255,0.14)] transition-all duration-fast ease-out-quart hover:bg-primary-hover disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            {isAnalyzing || isWaitingForSave ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                            <span>{isWaitingForSave ? tp("saving") : isAnalyzing ? ts("analyzingScript") : ts("extractEntities")}</span>
                        </button>
                    )}
                />
                <div className="flex-1 relative p-6 bg-surface overflow-hidden flex flex-col gap-2">
                    {draft.error ? (
                        <div role="alert" className="text-sm text-status-failed-fg">
                            {tp(draft.error)}
                            {draft.error !== "conflict" && draft.error !== "missingRevision" && (
                                <button type="button" onClick={() => void saveDraft(true)} className="ml-3 underline">{tp("retry")}</button>
                            )}
                        </div>
                    ) : (
                        <p role="status" className="text-xs text-text-muted">{tp(draft.saving ? "saving" : script === draft.savedText ? "saved" : "unsaved")}</p>
                    )}
                    {(draft.error || script !== draft.savedText) && <p className="text-xs text-text-muted">{tp("sessionDraftHint")}</p>}
                    {(draft.error === 'conflict' || draft.error === 'missingRevision') && (
                        <div className="rounded-lg border border-glass-border bg-elevated p-3 text-sm space-y-2">
                            <button type="button" disabled={currentReview?.status === 'reading'} onClick={() => void readLatest()} className="glass-button px-3 py-1.5">
                                {ts(currentReview?.status === 'reading' ? 'readingLatest' : 'readLatest')}
                            </button>
                            {currentReview?.status === 'failed' && <p role="alert">{ts('readLatestFailed')}</p>}
                            {currentReview?.status === 'ready' && <>
                                <p className="text-text-muted">{ts('latestSavedText')}</p>
                                <p className="max-h-32 overflow-auto whitespace-pre-wrap text-text-secondary">{currentReview.savedText}</p>
                                <div className="flex flex-wrap gap-2">
                                    <button type="button" onClick={() => adoptReview(true)} className="glass-button px-3 py-1.5">{ts('reextractRetainedDraft')}</button>
                                    <button type="button" onClick={() => adoptReview(false)} className="glass-button px-3 py-1.5">{ts('useSavedText')}</button>
                                </div>
                            </>}
                        </div>
                    )}
                    <textarea
                        aria-label={ts("scriptEditor")}
                        value={script}
                        disabled={!draftKey}
                        onChange={(e) => {
                            const newText = e.target.value;
                            if (draftKey && environment && isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                                draftInteractionRef.current += 1;
                                pendingAnalyzeRef.current = null;
                                setIsWaitingForSave(false);
                                changeText(draftKey, newText);
                            }
                        }}
                        onBlur={() => void saveDraft()}
                        placeholder={ts("scriptPlaceholder")}
                        className="w-full flex-1 min-h-0 bg-transparent text-text-secondary font-mono text-base leading-relaxed resize-none focus:outline-none"
                        spellCheck={false}
                    />
                </div>
            </div>

            {/* Right: Previously on... rail (R2V v2 Phase 3).
                Only renders for series-affiliated projects with an
                episode index > 0; the component handles empty/first
                episode state internally with a placeholder. */}
            <div className="w-[340px] shrink-0">
                <PreviousEpisodeSummary scriptId={projectId || null} />
            </div>

            {/* R2V v2 Phase 4 — Reconcile modal (auto-opens after analyze
                for series-affiliated episodes; ignored for standalone). */}
            <ReconcileModal
                isOpen={reconcileOpen}
                scriptId={projectId || null}
                onClose={() => setReconcileOpen(false)}
            />

        </div>
    );
}
