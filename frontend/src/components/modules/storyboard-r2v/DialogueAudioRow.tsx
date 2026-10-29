"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, Pause, Loader2, AlertCircle, Mic, Film, Undo2, Crosshair, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "framer-motion";
import { api, type DialogueAudioOptionsPatch, type DialogueAudioState } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import { audioStateLabelKey, audioStateMeetsKnownVersions, dialogueAudioOperationKey, validateDialogueAudioState, recordDialogueAudioRead } from "@/lib/dialogueAudioState";
import { confirmRawProjectAck, ProjectReadbackError } from "@/lib/projectReadback";
import { useProjectStore } from "@/store/projectStore";
import { isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { selectAppliedDubForTask, selectPreviewDubForTask, type MediaRefsLike } from "@/lib/dubSource";

interface DialogueAudioRowProps {
    scriptId: string;
    frameId: string;
    dialogue: string | undefined;
    voiceId: string | undefined;
    audioUrl: string | undefined;
    frameMediaRefs?: MediaRefsLike | null;
    audioError: string | null | undefined;
    onAudioUpdated?: () => void | Promise<void>;
    onUpdateDialogue?: (text: string) => void | Promise<void>;
    onDialogueDraftChange?: (text: string, dirty: boolean) => void;
    retainedDialogueDraft?: string;
    onBeforeGenerate?: () => Promise<void>;
    videoUrl?: string;
    videoTaskId?: string;
    videoTaskMediaRefs?: MediaRefsLike | null;
    previewVideoUrl?: string;
    dubbedVideoUrl?: string;
    dubOffsetMs?: number;
    dubPreviewSource?: unknown;
    dubAppliedSource?: unknown;
    onPreviewDub?: (videoTaskId: string, offsetMs: number) => Promise<void>;
    onApplyDub?: () => Promise<void>;
    onRevertDub?: () => Promise<void>;
}

const EMOTION_CHIPS = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "calm",
    "gentle",
    "serious",
] as const;
type EmotionChip = typeof EMOTION_CHIPS[number];
const isEmotionChip = (value: string): value is EmotionChip => (EMOTION_CHIPS as readonly string[]).includes(value);
const errorMessage = (error: unknown, fallback: string): string => error instanceof Error ? error.message : fallback;
const responseDetail = (error: unknown, fallback: string): string => {
    if (error && typeof error === "object" && "response" in error) {
        const response = (error as { response?: { data?: { detail?: unknown } } }).response;
        if (typeof response?.data?.detail === "string") return response.data.detail;
    }
    return errorMessage(error, fallback);
};
type AudioGenerationResponse = { frames?: Array<{ id: string; audio_error?: string | null }> };

export default function DialogueAudioRow({
    scriptId,
    frameId,
    dialogue,
    voiceId,
    audioUrl,
    frameMediaRefs,
    audioError,
    onAudioUpdated,
    onUpdateDialogue,
    onDialogueDraftChange,
    retainedDialogueDraft,
    onBeforeGenerate,
    videoUrl,
    videoTaskId,
    videoTaskMediaRefs,
    previewVideoUrl: storedPreviewUrl,
    dubbedVideoUrl: storedAppliedUrl,
    dubPreviewSource,
    dubAppliedSource,
    onPreviewDub,
    onApplyDub,
    onRevertDub,
}: DialogueAudioRowProps) {
    const t = useTranslations("dialogueAudio");
    const [modalOpen, setModalOpen] = useState(false);

    const task = { id: videoTaskId, frame_id: frameId, video_url: videoUrl, media_refs: videoTaskMediaRefs, status: 'completed' };
    const sourceFrame = { id: frameId, audio_url: audioUrl, media_refs: frameMediaRefs, preview_video_url: storedPreviewUrl,
        dubbed_video_url: storedAppliedUrl, dub_preview_source: dubPreviewSource, dub_applied_source: dubAppliedSource };
    const preview = selectPreviewDubForTask(sourceFrame, task);
    const applied = selectAppliedDubForTask(sourceFrame, task);
    const previewVideoUrl = preview.kind === 'current' ? preview.url : undefined;
    const dubbedVideoUrl = applied.kind === 'current' ? applied.url : undefined;
    const dubOffsetMs = preview.kind === 'current' ? preview.source.offset_ms : applied.kind === 'current' ? applied.source.offset_ms : 0;
    const hasHistoricalDub = !!((storedPreviewUrl && !previewVideoUrl) || (storedAppliedUrl && !dubbedVideoUrl));

    const hasAudio = !!audioUrl;
    const hasVideo = !!(videoUrl && videoTaskId);
    const hasDub = !!dubbedVideoUrl;
    const hasPreview = !!previewVideoUrl;
    const displayDialogue = (retainedDialogueDraft ?? dialogue ?? '').trim();

    return (
        <>
            <button
                type="button"
                onClick={() => setModalOpen(true)}
                className="w-full rounded-[14px] border border-glass-border bg-black/20 px-3.5 py-2.5 text-left hover:border-foreground/30 hover:bg-hover-bg/30 transition-colors group"
            >
                <div className="flex items-center gap-2">
                    <Mic size={12} className="text-text-muted shrink-0" />
                    <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-text-muted">
                        {t("title")}
                    </span>

                    {audioError && (
                        <span className="px-1.5 py-0.5 rounded font-mono text-[0.59375rem] uppercase tracking-[0.14em] bg-status-failed-bg text-status-failed-fg">
                            {t("state.error")}
                        </span>
                    )}
                    {hasAudio && !audioError && (
                        <span className="px-1.5 py-0.5 rounded font-mono text-[0.59375rem] uppercase tracking-[0.14em] bg-primary/10 text-primary">
                            {t("state.available")}
                        </span>
                    )}
                    {hasDub && (
                        <span className="px-1.5 py-0.5 rounded font-mono text-[0.59375rem] uppercase tracking-[0.14em] bg-status-completed-bg text-status-completed-fg">
                            {t("overridden")}
                        </span>
                    )}
                    {hasPreview && !hasDub && (
                        <span className="px-1.5 py-0.5 rounded font-mono text-[0.59375rem] uppercase tracking-[0.14em] bg-accent/10 text-accent">
                            {t("previewingBadge")}
                        </span>
                    )}

                    <span className="ml-auto text-[0.6875rem] text-text-muted group-hover:text-text-secondary transition-colors">
                        {hasVideo && hasAudio ? t("openWorkbench") : t("openVoiceGen")}
                    </span>
                </div>

                {displayDialogue && (
                    <p className="mt-1 text-[0.6875rem] text-text-muted truncate">
                        「{displayDialogue.slice(0, 60)}{displayDialogue.length > 60 ? "..." : ""}」
                    </p>
                )}
            </button>

            {modalOpen && <DialogueWorkbenchModal
                key={`${scriptId}:${frameId}`}
                isOpen={modalOpen}
                onClose={() => setModalOpen(false)}
                scriptId={scriptId}
                frameId={frameId}
                dialogue={dialogue}
                voiceId={voiceId}
                audioUrl={audioUrl}
                audioError={audioError}
                onAudioUpdated={onAudioUpdated}
                onUpdateDialogue={onUpdateDialogue}
                onDialogueDraftChange={onDialogueDraftChange}
                retainedDialogueDraft={retainedDialogueDraft}
                onBeforeGenerate={onBeforeGenerate}
                videoUrl={videoUrl}
                videoTaskId={videoTaskId}
                previewVideoUrl={previewVideoUrl}
                dubbedVideoUrl={dubbedVideoUrl}
                dubOffsetMs={dubOffsetMs}
                previewOffsetMs={preview.kind === 'current' ? preview.source.offset_ms : undefined}
                hasHistoricalDub={hasHistoricalDub}
                onPreviewDub={onPreviewDub}
                onApplyDub={onApplyDub}
                onRevertDub={onRevertDub}
            />}
        </>
    );
}


function DialogueWorkbenchModal({
    isOpen,
    onClose,
    scriptId,
    frameId,
    dialogue,
    voiceId,
    audioUrl,
    audioError,
    onAudioUpdated,
    onUpdateDialogue,
    onDialogueDraftChange,
    retainedDialogueDraft,
    onBeforeGenerate,
    videoUrl,
    videoTaskId,
    previewVideoUrl,
    dubbedVideoUrl,
    dubOffsetMs = 0,
    previewOffsetMs,
    hasHistoricalDub,
    onPreviewDub,
    onApplyDub,
    onRevertDub,
}: {
    isOpen: boolean;
    onClose: () => void;
    scriptId: string;
    frameId: string;
    dialogue: string | undefined;
    voiceId: string | undefined;
    audioUrl: string | undefined;
    audioError: string | null | undefined;
    onAudioUpdated?: () => void | Promise<void>;
    onUpdateDialogue?: (text: string) => void | Promise<void>;
    onDialogueDraftChange?: (text: string, dirty: boolean) => void;
    retainedDialogueDraft?: string;
    onBeforeGenerate?: () => Promise<void>;
    videoUrl?: string;
    videoTaskId?: string;
    previewVideoUrl?: string;
    dubbedVideoUrl?: string;
    dubOffsetMs?: number;
    previewOffsetMs?: number;
    hasHistoricalDub?: boolean;
    onPreviewDub?: (videoTaskId: string, offsetMs: number) => Promise<void>;
    onApplyDub?: () => Promise<void>;
    onRevertDub?: () => Promise<void>;
}) {
    const t = useTranslations("dialogueAudio");
    const [dialogueDraft, setDialogueDraft] = useState(retainedDialogueDraft ?? dialogue ?? "");
    const [emotion, setEmotion] = useState<string>("");
    const [freeText, setFreeText] = useState<string>("");
    const [speedDraft, setSpeedDraft] = useState<string>("");
    const [pitchDraft, setPitchDraft] = useState<string>("");
    const [volumeDraft, setVolumeDraft] = useState<string>("");
    const [audioState, setAudioState] = useState<DialogueAudioState | null>(null);
    const [stateLoading, setStateLoading] = useState(false);
    const [stateError, setStateError] = useState<string | null>(null);
    const touchedOptions = useRef({ speed: false, pitch: false, volume: false, instructions: false });
    const [instructionReset, setInstructionReset] = useState(false);
    const [busy, setBusy] = useState(false);
    const [playing, setPlaying] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [offsetMs, setOffsetMs] = useState(dubOffsetMs || 0);
    const [previewing, setPreviewing] = useState(false);
    const [applying, setApplying] = useState(false);
    const [reverting, setReverting] = useState(false);
    useEffect(() => { setOffsetMs(dubOffsetMs); }, [dubOffsetMs, videoTaskId]);

    const audioRef = useRef<HTMLAudioElement | null>(null);
    const videoRef = useRef<HTMLVideoElement>(null);
    const requestSeqRef = useRef(0);
    const lastAudioRevision = useRef(-1);
    const lastSavedDialogueRef = useRef(dialogue || "");
    const dialogueDirty = useRef(retainedDialogueDraft !== undefined);
    const dialogueValue = useRef(retainedDialogueDraft ?? dialogue ?? "");
    const savingDialogue = useRef<{ text: string; promise: Promise<boolean> } | null>(null);
    const mounted = useRef(true);
    const busyRef = useRef(false);
    const externalRunPending = useRef(false);
    const operationKey = dialogueAudioOperationKey(scriptId);
    const projectBusy = useProjectStore(state => !!state.runningOps[operationKey]);
    const sourceVersions = useProjectStore(state => JSON.stringify([
        state.currentProject?.id, state.currentProject?.revision, state.currentProject?.read_versions,
        state.currentProject?.readEnvironment, state.readWatermarks[requireActiveRuntimeEnvironment().key],
    ]));
    const currentState = audioState && audioStateMeetsKnownVersions(audioState) ? audioState : null;
    const effectiveVoice = currentState?.voice_id;
    const playbackAudio = currentState?.audio_url || audioUrl;
    const rawParameters = currentState?.prepared?.parameters;
    const actualParameters = rawParameters && typeof rawParameters === 'object' && !Array.isArray(rawParameters)
        ? rawParameters as Record<string, unknown> : null;
    const knownReasons = new Set(['dialogue_missing', 'speaker_missing', 'voice_missing', 'tts_unavailable',
        'invalid_audio_parameters', 'unsupported_audio_parameters', 'audio_missing', 'generation_failed',
        'snapshot_missing', 'snapshot_version_unknown', 'snapshot_media_mismatch', 'rules_version_unknown',
        'parameters_changed', 'parameters_match']);
    const markOption = (key: keyof DialogueAudioOptionsPatch) => {
        touchedOptions.current[key] = true;
        if (key === 'instructions') setInstructionReset(false);
    };
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; requestSeqRef.current += 1; };
    }, []);


    const instructions = useMemo(() => {
        const chip = isEmotionChip(emotion) ? emotion : "";
        const free = freeText.trim();
        if (chip && free) return `${chip}; ${free}`;
        return chip || free;
    }, [emotion, freeText]);

    const loadAudioState = useCallback(async () => {
        const seq = ++requestSeqRef.current;
        setStateLoading(true);
        setAudioState(null);
        setStateError(null);
        try {
            const raw = await api.getDialogueAudioState(scriptId, frameId);
            const next = validateDialogueAudioState(scriptId, frameId, raw);
            if (!mounted.current || requestSeqRef.current !== seq) return null;
            if (next.read_versions.project < lastAudioRevision.current || !recordDialogueAudioRead(next)) throw new Error(t("stateReadFailed"));
            lastAudioRevision.current = next.read_versions.project;
            setAudioState(next);
            if (!touchedOptions.current.speed) setSpeedDraft(next.options.speed == null ? "" : String(next.options.speed));
            if (!touchedOptions.current.pitch) setPitchDraft(next.options.pitch == null ? "" : String(next.options.pitch));
            if (!touchedOptions.current.volume) setVolumeDraft(next.options.volume == null ? "" : String(next.options.volume));
            if (!touchedOptions.current.instructions) {
                setEmotion("");
                setFreeText(typeof next.options.instructions === "string" ? next.options.instructions : "");
                setInstructionReset(false);
            }
            return next;
        } catch (e: unknown) {
            if (mounted.current && requestSeqRef.current === seq) setStateError(errorMessage(e, t("stateReadFailed")));
            return null;
        } finally {
            if (mounted.current && requestSeqRef.current === seq) setStateLoading(false);
        }
    }, [scriptId, frameId, t]);

    useEffect(() => {
        if (!isOpen) {
            requestSeqRef.current += 1;
            return;
        }
        if (!busyRef.current) void loadAudioState();
    }, [isOpen, loadAudioState, dialogue, voiceId, audioUrl, audioError]);

    useEffect(() => {
        if (isOpen && !busyRef.current && audioState && !audioStateMeetsKnownVersions(audioState)) void loadAudioState();
    }, [isOpen, sourceVersions, audioState, loadAudioState]);

    useEffect(() => {
        if (projectBusy) {
            if (!busyRef.current) externalRunPending.current = true;
        } else if (isOpen && externalRunPending.current) {
            externalRunPending.current = false;
            void loadAudioState();
        }
    }, [isOpen, projectBusy, loadAudioState]);

    const canDub = !!(playbackAudio && videoUrl && videoTaskId && onPreviewDub);
    const [videoDurationMs, setVideoDurationMs] = useState(5000);

    // Determine which video to show: preview > dubbed > original
    const displayVideoUrl = previewVideoUrl || dubbedVideoUrl || videoUrl;

    useEffect(() => {
        const next = dialogue || "";
        if (!dialogueDirty.current) {
            setDialogueDraft(next); dialogueValue.current = next;
        }
        if (dialogueDirty.current && dialogueValue.current.trim() === next.trim()) {
            dialogueDirty.current = false;
            setError(null);
        }
        lastSavedDialogueRef.current = next;
    }, [dialogue]);
    useEffect(() => {
        if (isOpen) { setError(null); }
    }, [isOpen]);
    useEffect(() => { return () => { audioRef.current?.pause(); }; }, []);

    const handleSaveDialogue = (): Promise<boolean> => {
        const next = dialogueDraft.trim();
        if (savingDialogue.current?.text === next) return savingDialogue.current.promise;
        const previous = savingDialogue.current;
        if (!onUpdateDialogue || (!previous && next === lastSavedDialogueRef.current.trim())) return Promise.resolve(false);
        const pending = { text: next, promise: Promise.resolve(false) };
        // A second blur may restore the old text while the first save is still
        // pending. Compare against the acknowledged value only after it settles.
        pending.promise = (previous?.promise ?? Promise.resolve()).then(() => onUpdateDialogue(next)).then(() => {
            if (mounted.current) {
                lastSavedDialogueRef.current = next;
                if (dialogueValue.current.trim() === next) dialogueDirty.current = false;
            }
            return true;
        }).finally(() => { if (savingDialogue.current === pending) savingDialogue.current = null; });
        savingDialogue.current = pending;
        return pending.promise;
    };

    const optionPatch = (): DialogueAudioOptionsPatch => {
        const patch: DialogueAudioOptionsPatch = {};
        if (touchedOptions.current.speed) patch.speed = speedDraft.trim() === "" ? null : Number(speedDraft);
        if (touchedOptions.current.pitch) patch.pitch = pitchDraft.trim() === "" ? null : Number(pitchDraft);
        if (touchedOptions.current.volume) patch.volume = volumeDraft.trim() === "" ? null : Number(volumeDraft);
        if (touchedOptions.current.instructions) patch.instructions = instructionReset ? null : instructions;
        return patch;
    };

    const handlePlayAudio = async () => {
        if (!playbackAudio) return;
        if (audioRef.current && playing) {
            audioRef.current.pause();
            setPlaying(false);
            return;
        }
        const audio = new Audio(getAssetUrl(playbackAudio));
        audio.onended = () => { setPlaying(false); audioRef.current = null; };
        audio.onerror = () => { setPlaying(false); setError(t("playFailed")); };
        audioRef.current = audio;
        setPlaying(true);
        try { await audio.play(); } catch (e: unknown) { setPlaying(false); setError(errorMessage(e, t("playFailed"))); }
    };

    const handleGenerate = async () => {
        const store = useProjectStore.getState();
        if (store.runningOps[operationKey] || busyRef.current || stateLoading || !currentState) return;
        if (!effectiveVoice) { setError(t("noVoiceBound")); return; }
        const environment = requireActiveRuntimeEnvironment();
        const patch = optionPatch();
        if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; setPlaying(false); }
        busyRef.current = true;
        store.setRunningOp(operationKey, true);
        setError(null);
        setBusy(true);
        try {
            const saved = await handleSaveDialogue();
            if (!mounted.current) return;
            await onBeforeGenerate?.();
            if (!mounted.current) return;
            if (saved && !(await loadAudioState())) throw new Error(t("stateReadFailed"));
            if (!mounted.current) return;
            const result = await api.generateLineAudio(scriptId, frameId, patch) as AudioGenerationResponse;
            if (!mounted.current) return;
            await confirmRawProjectAck(scriptId, result);
            if (!mounted.current) return;
            const failure = result.frames?.find(frame => frame.id === frameId)?.audio_error;
            if (!failure) touchedOptions.current = { speed: false, pitch: false, volume: false, instructions: false };
            const confirmedState = await loadAudioState();
            if (!mounted.current) return;
            if (failure) setError(failure);
            else if (!confirmedState) setError(t("stateReadFailed"));
            await onAudioUpdated?.();
        } catch (e: unknown) {
            if (mounted.current) {
                setAudioState(null);
                setError(e instanceof ProjectReadbackError ? t("savedReadFailed") : errorMessage(e, t("generateFailed")));
            }
        } finally {
            if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                store.setRunningOp(operationKey, false);
            }
            busyRef.current = false;
            if (mounted.current) setBusy(false);
        }
    };

    const handleMarkStart = () => {
        if (videoRef.current) {
            const ms = Math.round(videoRef.current.currentTime * 1000);
            setOffsetMs(ms);
        }
    };

    const runDubOperation = async (action: () => Promise<void>, setPending: (value: boolean) => void, fallback: string) => {
        const store = useProjectStore.getState();
        if (store.runningOps[operationKey]) return;
        const environment = requireActiveRuntimeEnvironment();
        busyRef.current = true;
        store.setRunningOp(operationKey, true);
        setPending(true);
        setError(null);
        try {
            await action();
        } catch (e: unknown) {
            if (mounted.current) setError(e instanceof ProjectReadbackError ? t("dubReadFailed") : responseDetail(e, fallback));
        } finally {
            busyRef.current = false;
            if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                store.setRunningOp(operationKey, false);
            }
            if (mounted.current) setPending(false);
        }
    };

    const handlePreviewDub = async () => {
        if (!onPreviewDub || !videoTaskId || !Number.isSafeInteger(offsetMs) || offsetMs < 0) return;
        await runDubOperation(() => onPreviewDub(videoTaskId, offsetMs), setPreviewing, t("previewFailed"));
    };

    const handleApply = async () => {
        if (!onApplyDub || !previewVideoUrl || offsetMs !== previewOffsetMs) return;
        await runDubOperation(onApplyDub, setApplying, t("applyFailed"));
    };

    const handleRevert = async () => {
        if (!onRevertDub) return;
        await runDubOperation(onRevertDub, setReverting, t("undoFailed"));
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    className="fixed inset-0 z-[100] flex items-center justify-center"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.15 }}
                >
                    <motion.div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />

                    <motion.div
                        className="relative w-full max-w-xl mx-4 rounded-xl border border-glass-border bg-surface/95 backdrop-blur-xl shadow-2xl overflow-hidden max-h-[90vh] flex flex-col"
                        initial={{ scale: 0.95, opacity: 0, y: 10 }}
                        animate={{ scale: 1, opacity: 1, y: 0 }}
                        exit={{ scale: 0.95, opacity: 0, y: 10 }}
                        transition={{ type: "spring", stiffness: 300, damping: 28 }}
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between px-5 py-4 border-b border-glass-border/50 shrink-0">
                            <div className="flex items-center gap-3">
                                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 border border-primary/20">
                                    <Mic size={16} className="text-primary" />
                                </div>
                                <div>
                                    <h3 className="text-[0.875rem] font-medium text-foreground">{t("workbenchTitle")}</h3>
                                    <p className="text-[0.6875rem] text-text-muted mt-0.5">{t("workbenchSubtitle")}</p>
                                </div>
                            </div>
                            <button
                                type="button"
                                onClick={onClose}
                                className="inline-flex items-center justify-center w-7 h-7 rounded-md text-text-muted hover:text-foreground hover:bg-hover-bg transition-colors"
                            >
                                ×
                            </button>
                        </div>

                        {/* Scrollable body */}
                        <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1">
                            {/* Step 1: Dialogue text editing */}
                            <section className="space-y-2">
                                <div className="flex items-center gap-2">
                                    <span className="text-[0.75rem] font-medium text-text-secondary">{t("stepDialogueText")}</span>
                                    {!effectiveVoice && (
                                        <span className="text-[0.625rem] text-accent">{t("needVoiceBindingHint")}</span>
                                    )}
                                </div>
                                <textarea
                                    value={dialogueDraft}
                                    disabled={busy}
                                    onChange={(e) => {
                                        const dirty = e.target.value.trim() !== lastSavedDialogueRef.current.trim() || !!savingDialogue.current;
                                        onDialogueDraftChange?.(e.target.value, dirty);
                                        dialogueDirty.current = dirty; dialogueValue.current = e.target.value; setDialogueDraft(e.target.value);
                                    }}
                                    onBlur={() => void handleSaveDialogue().catch((e: unknown) => { if (mounted.current) setError(errorMessage(e, t("saveDialogueFailed"))); })}
                                    placeholder={t("dialoguePlaceholder")}
                                    rows={2}
                                    className="w-full rounded-md border border-glass-border bg-black/30 px-3 py-2 text-[0.75rem] text-foreground placeholder:text-text-muted focus:outline-none focus:border-primary/40 resize-none"
                                />
                            </section>

                            {/* Step 2: Emotion + TTS generation */}
                            <section className="space-y-2">
                                <div className="flex items-center gap-2">
                                    <span className="text-[0.75rem] font-medium text-text-secondary">{t("stepEmotionGen")}</span>
                                    <span className="px-1.5 py-0.5 rounded font-mono text-[0.5625rem] uppercase tracking-[0.12em] bg-black/30 text-text-muted">
                                        {stateLoading ? t("state.checking") : t(`state.${audioStateLabelKey(currentState)}`)}
                                    </span>
                                    <button
                                        type="button"
                                        onClick={() => void loadAudioState()}
                                        disabled={stateLoading || busy}
                                        className="ml-auto inline-flex items-center gap-1 rounded border border-glass-border bg-black/30 px-2 py-0.5 text-[0.625rem] text-text-muted hover:text-foreground disabled:opacity-40"
                                    >
                                        <RefreshCw size={10} className={stateLoading ? "animate-spin" : ""} />
                                        {t("refreshState")}
                                    </button>
                                </div>
                                {audioState && (
                                    <div className="rounded-md border border-glass-border bg-black/20 px-3 py-2 text-[0.65625rem] text-text-muted">
                                        <div>{currentState?.reason && knownReasons.has(currentState.reason) ? t(`reason.${currentState.reason}`) : t("state.unknown")}</div>
                                        <div>{t("actualVoice", { voice: audioState.voice_id || t("notAvailable") })}</div>
                                    </div>
                                )}
                                {(dialogueDirty.current || Object.values(touchedOptions.current).some(Boolean)) && (
                                    <p className="text-[0.65625rem] text-accent">{t("draftPending")}</p>
                                )}
                                {actualParameters && (
                                    <details className="text-[0.65625rem] text-text-muted">
                                        <summary className="cursor-pointer">{t("effectiveParameters")}</summary>
                                        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 rounded-md bg-black/20 p-2">
                                            {([['speed', 'speech_rate'], ['pitch', 'pitch_rate'], ['volume', 'volume']] as const).map(([field, param]) => {
                                                const value = actualParameters[param];
                                                const source = currentState?.sources?.[field];
                                                return <div key={field}><dt>{t(`option.${field}`)}</dt><dd>
                                                    {typeof value === 'number' ? value : t("notSent")}
                                                    {source === 'override' || source === 'character_binding' ? ` · ${t(`source.${source}`)}` : ''}
                                                </dd></div>;
                                            })}
                                            <div><dt>{t("toneInstructions")}</dt><dd className="break-words">
                                                {typeof (actualParameters.instruction ?? actualParameters.instructions) === 'string'
                                                    ? String(actualParameters.instruction ?? actualParameters.instructions) : t("notSent")}
                                            </dd></div>
                                        </dl>
                                    </details>
                                )}
                                {stateError && (
                                    <div className="rounded-md border border-status-failed-border bg-status-failed-bg px-3 py-2 text-[0.65625rem] text-status-failed-fg">
                                        {stateError}
                                    </div>
                                )}
                                <div className="grid grid-cols-3 gap-2">
                                    {[
                                        ["speed", speedDraft, setSpeedDraft],
                                        ["pitch", pitchDraft, setPitchDraft],
                                        ["volume", volumeDraft, setVolumeDraft],
                                    ].map(([key, value, setter]) => (
                                        <label key={key as string} className="space-y-1">
                                            <span className="block text-[0.625rem] uppercase tracking-[0.12em] text-text-muted">{t(`option.${key}`)}</span>
                                            <input
                                                type="number"
                                                disabled={busy || stateLoading}
                                                value={value as string}
                                                onChange={(e) => {
                                                    (setter as (value: string) => void)(e.target.value);
                                                    markOption(key as keyof DialogueAudioOptionsPatch);
                                                }}
                                                placeholder={t("inherit")}
                                                className="w-full rounded-md border border-glass-border bg-black/30 px-2 py-1 text-[0.6875rem] text-foreground placeholder:text-text-muted focus:outline-none focus:border-primary/40"
                                            />
                                        </label>
                                    ))}
                                </div>
                                <div className="flex flex-wrap items-center gap-1">
                                    {EMOTION_CHIPS.map((chip) => (
                                        <button
                                            key={chip}
                                            disabled={busy || stateLoading}
                                            onClick={() => {
                                                setEmotion(emotion === chip ? "" : chip);
                                                markOption("instructions");
                                            }}
                                            className={`px-2 py-0.5 rounded-full border font-mono text-[0.59375rem] uppercase tracking-[0.12em] transition-colors ${
                                                emotion === chip
                                                    ? "border-primary bg-primary/15 text-primary"
                                                    : "border-glass-border bg-black/30 text-text-muted hover:border-foreground/30 hover:text-text-secondary"
                                            }`}
                                        >
                                            {t(`emotion.${chip}`)}
                                        </button>
                                    ))}
                                </div>
                                <input
                                    type="text"
                                    disabled={busy || stateLoading}
                                    value={freeText}
                                    onChange={(e) => {
                                        setFreeText(e.target.value.slice(0, 80));
                                        markOption("instructions");
                                    }}
                                    placeholder={t("freeTextPlaceholder")}
                                    className="w-full rounded-md border border-glass-border bg-black/30 px-3 py-1.5 text-[0.6875rem] text-foreground placeholder:text-text-muted focus:outline-none focus:border-primary/40"
                                />
                                <button type="button" disabled={busy || stateLoading} onClick={() => {
                                    touchedOptions.current.instructions = true;
                                    setEmotion(""); setFreeText(""); setInstructionReset(true);
                                }} className="text-[0.625rem] text-text-muted hover:text-foreground">
                                    {t("resetInstructions")}
                                </button>
                                {audioState?.historical_instructions && (
                                    <button
                                        type="button"
                                        disabled={busy || stateLoading}
                                        onClick={() => {
                                            setEmotion("");
                                            setFreeText(audioState.historical_instructions || "");
                                            markOption("instructions");
                                        }}
                                        className="text-[0.625rem] text-primary hover:text-primary-hover"
                                    >
                                        {t("reuseHistoricalInstructions")}
                                    </button>
                                )}
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={handleGenerate}
                                        disabled={projectBusy || busy || stateLoading || !effectiveVoice || !dialogueDraft.trim()}
                                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-primary/40 bg-primary/10 text-[0.75rem] font-medium text-primary hover:bg-primary/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                    >
                                        {busy ? <Loader2 size={12} className="animate-spin" /> : <Mic size={12} />}
                                        {playbackAudio ? t("regenerate") : t("generate")}
                                    </button>
                                    {playbackAudio && (
                                        <button
                                            onClick={handlePlayAudio}
                                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-glass-border bg-black/30 text-[0.75rem] text-text-secondary hover:border-foreground/30 hover:text-foreground transition-colors"
                                        >
                                            {playing ? <Pause size={12} /> : <Play size={12} />}
                                            {playing ? t("pause") : t("previewTts")}
                                        </button>
                                    )}
                                    {playbackAudio && (
                                        <span className="text-[0.625rem] text-status-completed-fg">{t("generatedTag")}</span>
                                    )}
                                </div>
                            </section>

                            {hasHistoricalDub && <p className="text-[0.65625rem] text-text-muted">{t("historicalDub")}</p>}
                            {/* Step 3: Dub — visible when audio + video both exist */}
                            {canDub && (
                                <section className="space-y-3 border-t border-glass-border/50 pt-4">
                                    <div className="flex items-center gap-2">
                                        <span className="text-[0.75rem] font-medium text-text-secondary">{t("stepOverride")}</span>
                                        {dubbedVideoUrl && !previewVideoUrl && (
                                            <span className="px-1.5 py-0.5 rounded font-mono text-[0.5625rem] uppercase tracking-[0.14em] bg-status-completed-bg text-status-completed-fg">
                                                {t("overridden")}
                                            </span>
                                        )}
                                        {previewVideoUrl && (
                                            <span className="px-1.5 py-0.5 rounded font-mono text-[0.5625rem] uppercase tracking-[0.14em] bg-accent/10 text-accent">
                                                {t("previewVersion")}
                                            </span>
                                        )}
                                    </div>

                                    {/* Video player */}
                                    <div className="relative rounded-lg overflow-hidden border border-glass-border bg-black">
                                        <video
                                            ref={videoRef}
                                            key={displayVideoUrl}
                                            src={getAssetUrl(displayVideoUrl!)}
                                            className="w-full max-h-[200px] object-contain"
                                            controls
                                            autoPlay={!!previewVideoUrl}
                                            onLoadedMetadata={(e) => {
                                                const dur = (e.currentTarget.duration || 5) * 1000;
                                                setVideoDurationMs(Math.round(dur));
                                            }}
                                        />
                                        {previewVideoUrl && (
                                            <div className="absolute top-2 left-2 px-2 py-0.5 rounded-full bg-accent/20 border border-accent/30 text-[0.5625rem] font-medium text-accent">
                                                {t("previewVersion")}
                                            </div>
                                        )}
                                        {dubbedVideoUrl && !previewVideoUrl && (
                                            <div className="absolute top-2 left-2 px-2 py-0.5 rounded-full bg-status-completed-bg/20 border border-status-completed-border text-[0.5625rem] font-medium text-status-completed-fg">
                                                {t("dubbedVersion")}
                                            </div>
                                        )}
                                    </div>

                                    {/* Mark start point + Offset controls */}
                                    <div className="space-y-2">
                                        <div className="flex items-center gap-2">
                                            <button
                                            type="button"
                                            disabled={projectBusy}
                                            onClick={handleMarkStart}
                                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-primary/30 bg-primary/5 text-[0.6875rem] font-medium text-primary hover:bg-primary/10 transition-colors"
                                            >
                                                <Crosshair size={12} />
                                                {t("markStartPoint")}
                                            </button>
                                            <span className="text-[0.625rem] text-text-muted">{t("markStartHint")}</span>
                                        </div>

                                        <div className="flex items-center gap-2">
                                            <span className="text-[0.6875rem] text-text-muted shrink-0">{t("audioPosition")}</span>
                                            <button
                                                type="button"
                                                disabled={projectBusy}
                                                onClick={() => setOffsetMs(Math.max(0, offsetMs - 50))}
                                                className="w-6 h-6 flex items-center justify-center rounded border border-glass-border bg-black/30 text-text-muted hover:text-foreground hover:border-foreground/30 transition-colors"
                                            >
                                                <ChevronLeft size={12} />
                                            </button>
                                            <input
                                                type="number"
                                                disabled={projectBusy}
                                                aria-label={t("audioPosition")}
                                                value={offsetMs}
                                                onChange={(e) => setOffsetMs(Math.round(Math.max(0, Math.min(videoDurationMs, Number(e.target.value) || 0))))}
                                                className="w-[64px] rounded border border-glass-border bg-black/40 px-1.5 py-0.5 text-center font-mono text-[0.6875rem] text-primary focus:outline-none focus:border-primary/40"
                                            />
                                            <button
                                                type="button"
                                                disabled={projectBusy}
                                                onClick={() => setOffsetMs(Math.min(videoDurationMs, offsetMs + 50))}
                                                className="w-6 h-6 flex items-center justify-center rounded border border-glass-border bg-black/30 text-text-muted hover:text-foreground hover:border-foreground/30 transition-colors"
                                            >
                                                <ChevronRight size={12} />
                                            </button>
                                            <span className="text-[0.625rem] text-text-muted">ms</span>
                                            <input
                                                type="range"
                                                disabled={projectBusy}
                                                aria-label={t("audioPosition")}
                                                min={0}
                                                max={videoDurationMs}
                                                step={50}
                                                value={offsetMs}
                                                onChange={(e) => setOffsetMs(Number(e.target.value))}
                                                className="flex-1 accent-primary h-1 cursor-pointer"
                                            />
                                        </div>
                                    </div>

                                    {/* Action buttons — state-dependent */}
                                    <div className="flex items-center gap-2 flex-wrap">
                                        {/* 预听 — always visible */}
                                        <button
                                            type="button"
                                            onClick={handlePreviewDub}
                                            disabled={projectBusy || previewing}
                                            className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-primary/40 bg-primary/10 text-[0.75rem] font-medium text-primary hover:bg-primary/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                        >
                                            {previewing ? <Loader2 size={12} className="animate-spin" /> : <Film size={12} />}
                                            {previewing ? t("generatingPreview") : t("preview")}
                                        </button>

                                        {/* 应用覆盖 — only when preview exists */}
                                        {previewVideoUrl && onApplyDub && (
                                            <button
                                                type="button"
                                                onClick={handleApply}
                                                disabled={projectBusy || applying || offsetMs !== previewOffsetMs}
                                                className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-status-completed-border bg-status-completed-bg text-[0.75rem] font-medium text-status-completed-fg hover:bg-status-completed-bg/60 hover:border-status-completed-border transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                            >
                                                {applying ? <Loader2 size={12} className="animate-spin" /> : <Film size={12} />}
                                                {t("applyOverride")}
                                            </button>
                                        )}

                                        {/* 撤销覆盖 — only when dubbed exists and no preview */}
                                        {(dubbedVideoUrl || hasHistoricalDub) && !previewVideoUrl && onRevertDub && (
                                            <button
                                                type="button"
                                                onClick={handleRevert}
                                                disabled={projectBusy || reverting}
                                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-accent/30 bg-accent/5 text-[0.75rem] text-accent hover:bg-accent/10 hover:border-accent/50 transition-colors disabled:opacity-40"
                                            >
                                                {reverting ? <Loader2 size={12} className="animate-spin" /> : <Undo2 size={12} />}
                                                {t("undoOverride")}
                                            </button>
                                        )}
                                    </div>

                                    {previewVideoUrl && (
                                        <p className="text-[0.625rem] text-text-muted">
                                            {t("previewHintBody")}
                                        </p>
                                    )}
                                </section>
                            )}

                            {/* Error display */}
                            {(audioError || error) && (
                                <div className="flex items-center gap-2 px-3 py-2 rounded-md border border-status-failed-border bg-status-failed-bg text-[0.6875rem] text-status-failed-fg">
                                    <AlertCircle size={12} className="shrink-0 text-status-failed-fg" />
                                    <span className="break-words flex-1">{error || audioError}</span>
                                    {error && (
                                        <button type="button" onClick={() => setError(null)} className="shrink-0 text-status-failed-fg/60 hover:text-status-failed-fg">×</button>
                                    )}
                                </div>
                            )}
                        </div>

                        {/* Footer */}
                        <div className="px-5 py-3 border-t border-glass-border/50 bg-black/20 shrink-0">
                            <button
                                type="button"
                                onClick={onClose}
                                className="px-3 py-1.5 rounded-md text-[0.75rem] text-text-muted hover:text-text-secondary transition-colors"
                            >
                                {t("close")}
                            </button>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
