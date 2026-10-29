"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { Check, Loader2, Film, AlertTriangle, Layout, Clock, FileText, Download, Music, Sliders, Package } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { API_URL, api, type BgmPreset } from "@/lib/api";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { getAssetUrl, extractErrorDetail } from "@/lib/utils";
import { selectFrameAssemblyVideo, selectVerifiedAppliedDubUrlForTask } from "@/lib/dubSource";
import StepPageHeader, { StepPill } from "@/components/shared/StepPageHeader";
import SidePanelHeader from "@/components/shared/SidePanelHeader";
import { confirmRawProjectAck, ProjectReadbackError } from "@/lib/projectReadback";
import { appAccessFetch } from "@/lib/appAccess";
import { mergeVideoDisplayState, type MergeVideoDisplayState } from "@/lib/mergeFreshness";

type AssemblyPhase = "takes" | "mix" | "export";

const DEFAULT_MIX_SETTINGS = { dialogue: 100, bgm: 35, sfx: 60 };

function currentProjectId() {
    return useProjectStore.getState().currentProject?.id ?? null;
}

function projectReadEnvironment(project: { readEnvironment?: string } | null | undefined) {
    return project?.readEnvironment;
}

function currentProjectEnvironment() {
    return projectReadEnvironment(useProjectStore.getState().currentProject);
}

function isCurrentProject(projectId: string, environment: string | undefined, generation: number | undefined) {
    const project = useProjectStore.getState().currentProject;
    return !!environment && project?.id === projectId && projectReadEnvironment(project) === environment
        && isCurrentRuntimeEnvironment(environment, generation);
}

export default function VideoAssembly() {
    const ta = useTranslations("assembly");
    const tc = useTranslations("frameCommands");
    const tStep = useTranslations("stepHeader");
    const currentProject = useProjectStore((state) => state.currentProject);

    const [phase, setPhase] = useState<AssemblyPhase>("takes");
    const [selectedFrameId, setSelectedFrameId] = useState<string | null>(null);
    const [isMerging, setIsMerging] = useState(false);
    const [mergeProjectId, setMergeProjectId] = useState<string | null>(null);
    const [mergeError, setMergeError] = useState<string | null>(null);
    const [selectingVideoKey, setSelectingVideoKey] = useState<string | null>(null);
    const mergeRequestRef = useRef(0);
    const [isDownloading, setIsDownloading] = useState(false);
    const sessionRef = useRef({
        projectId: currentProject?.id ?? null,
        environment: projectReadEnvironment(currentProject),
        generation: getRuntimeEnvironment()?.generation,
        token: 0,
    });

    useEffect(() => {
        const projectId = currentProject?.id ?? null;
        const environment = currentProject?.readEnvironment;
        const runtime = getRuntimeEnvironment();
        const generation = runtime && runtime.key === environment ? runtime.generation : undefined;
        if (sessionRef.current.projectId !== projectId || sessionRef.current.environment !== environment || sessionRef.current.generation !== generation) {
            sessionRef.current = { projectId, environment, generation, token: sessionRef.current.token + 1 };
            setSelectingVideoKey(null);
        }
    }, [currentProject?.id, currentProject?.readEnvironment]);

    useEffect(() => () => {
        sessionRef.current = {
            projectId: sessionRef.current.projectId,
            environment: sessionRef.current.environment,
            generation: sessionRef.current.generation,
            token: sessionRef.current.token + 1,
        };
    }, []);

    const isActiveSession = (projectId: string, environment: string | undefined, generation: number | undefined, token: number) => (
        sessionRef.current.token === token
            && sessionRef.current.projectId === projectId
            && sessionRef.current.environment === environment
            && sessionRef.current.generation === generation
            && isCurrentProject(projectId, environment, generation)
    );

    // Group videos by frame
    const videosByFrame = useMemo(() => {
        if (!currentProject?.video_tasks) return {};

        const grouped: Record<string, any[]> = {};
        currentProject.video_tasks.forEach((task: any) => {
            if (task.status === "completed" && task.video_url) {
                if (task.frame_id) {
                    if (!grouped[task.frame_id]) grouped[task.frame_id] = [];
                    grouped[task.frame_id].push(task);
                }
            }
        });
        return grouped;
    }, [currentProject?.video_tasks]);

    const handleSelectVideo = async (frameId: string, videoId: string) => {
        if (!currentProject || currentProject.readEnvironment !== getRuntimeEnvironment()?.key) return;
        const projectId = currentProject.id;
        const sessionToken = sessionRef.current.token;
        const environment = sessionRef.current.environment;
        const generation = sessionRef.current.generation;
        const selectionKey = `${projectId}:${frameId}:${videoId}`;
        const isActive = () => isActiveSession(projectId, environment, generation, sessionToken);
        setSelectingVideoKey(selectionKey);
        try {
            const response = await api.selectVideo(projectId, frameId, videoId);
            if (!isActive()) return;
            await confirmRawProjectAck(projectId, response, { isCurrent: () => isActive() });
        } catch (error) {
            if (!isActive()) return;
            console.error("Failed to select video:", error);
            alert(error instanceof ProjectReadbackError ? tc("savedReadFailed") : extractErrorDetail(error, "Failed to select video"));
        } finally {
            if (isActive()) setSelectingVideoKey((current) => current === selectionKey ? null : current);
        }
    };

    const handleMerge = async () => {
        if (!currentProject || currentProject.readEnvironment !== getRuntimeEnvironment()?.key) return;
        const projectId = currentProject.id;
        const sessionToken = sessionRef.current.token;
        const environment = sessionRef.current.environment;
        const generation = sessionRef.current.generation;
        const requestId = mergeRequestRef.current + 1;
        mergeRequestRef.current = requestId;
        setMergeProjectId(projectId);
        setIsMerging(true);
        setMergeError(null);  // Clear previous errors

        const isActive = () => mergeRequestRef.current === requestId && isActiveSession(projectId, environment, generation, sessionToken);
        try {
            const response = await api.mergeVideos(projectId);
            if (!isActive()) return;
            await confirmRawProjectAck(projectId, response, { isCurrent: () => isActive() });
        } catch (error: any) {
            if (!isActive()) return;
            console.error("Failed to merge videos:", error);

            const errorDetail = error instanceof ProjectReadbackError
                ? tc("savedReadFailed")
                : extractErrorDetail(error, "Unknown error occurred during video merge");

            setMergeError(errorDetail);
            alert(error instanceof ProjectReadbackError ? errorDetail : `${ta("mergeFailedAlert")}:\n\n${errorDetail}`);
        } finally {
            if (mergeRequestRef.current === requestId) {
                setIsMerging(false);
                setMergeProjectId((current) => current === projectId ? null : current);
            }
        }
    };


    const handleDownload = async () => {
        if (!currentProject?.merged_video_url) return;
        setIsDownloading(true);
        try {
            const rawPath = currentProject.merged_video_url;
            const cleanPath = rawPath.replace(/^output\//, "").replace(/^\/+/, "");
            const url = rawPath.startsWith("http://") || rawPath.startsWith("https://")
                ? getAssetUrl(rawPath)
                : `${API_URL}/files/${cleanPath}`;

            const response = await appAccessFetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const blob = await response.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = blobUrl;
            a.download = `${currentProject.title || "merged"}_${currentProject.id}.mp4`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
        } catch (error) {
            console.error("Failed to download video:", error);
            alert(ta("downloadFailed"));
        } finally {
            setIsDownloading(false);
        }
    };

    const selectedFrame = useMemo(() => {
        return currentProject?.frames?.find((f: any) => f.id === selectedFrameId);
    }, [currentProject?.frames, selectedFrameId]);

    const variants = selectedFrameId ? videosByFrame[selectedFrameId] || [] : [];
    const mergeDisplay = useMemo(() => {
        const runtime = getRuntimeEnvironment();
        return mergeVideoDisplayState(currentProject, runtime?.key ?? "");
    }, [currentProject]);

    const framesReady = currentProject?.frames?.filter((f: any) => {
        const selected = selectFrameAssemblyVideo(f, currentProject.video_tasks ?? []);
        return selected.kind !== "none";
    }).length ?? 0;
    const framesTotal = currentProject?.frames?.length ?? 0;

    return (
        // Layout v4: outer horizontal split. StepHeader belongs to main
        // column; right Variants panel is floor-to-ceiling with its own
        // SidePanelHeader.
        <div className="h-full flex overflow-hidden">
            {/* Left: main column */}
            <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                <StepPageHeader
                    stepNumber={5}
                    englishName="ASSEMBLY"
                    title={tStep("assemblyTitle")}
                    subtitle={tStep("assemblySubtitle")}
                    pills={framesTotal > 0 ? (
                        <>
                            <StepPill label={ta("framesLabel")} value={framesTotal} />
                            <StepPill label={ta("framesReadyLabel")} value={`${framesReady}/${framesTotal}`} />
                        </>
                    ) : null}
                />
                {/* PR-3k · Phase tabs — Takes / Mix / Export */}
                <div className="flex items-center gap-1 px-6 pt-2 border-b border-glass-border bg-surface">
                    {[
                        { id: "takes" as const,  label: ta("phaseTakes"),  icon: <Film size={12} /> },
                        { id: "mix" as const,    label: ta("phaseMix"),    icon: <Sliders size={12} /> },
                        { id: "export" as const, label: ta("phaseExport"), icon: <Package size={12} /> },
                    ].map((p) => (
                        <button
                            key={p.id}
                            onClick={() => setPhase(p.id)}
                            className={`relative inline-flex items-center gap-1.5 px-3 pb-2 font-mono text-[0.6875rem] uppercase tracking-[0.16em] transition-colors ${
                                phase === p.id
                                    ? "text-foreground"
                                    : "text-text-muted hover:text-text-secondary"
                            }`}
                        >
                            {p.icon}
                            {p.label}
                            {phase === p.id && (
                                <span className="absolute bottom-0 left-2 right-2 h-px bg-primary" aria-hidden="true" />
                            )}
                        </button>
                    ))}
                </div>
                {/* Takes phase body */}
                {phase === "takes" && (
                <div className="flex-1 overflow-y-auto custom-scrollbar p-6 space-y-4">
                        {currentProject?.frames?.map((frame: any, index: number) => {
                            const hasVideos = videosByFrame[frame.id]?.length > 0;
                            const isSelected = frame.id === selectedFrameId;
                            const selectedSource = selectFrameAssemblyVideo(frame, currentProject.video_tasks ?? []);
                            const selectedVideoId = selectedSource.kind !== "none" ? selectedSource.taskId : frame.selected_video_id;
                            const selectedVideo = selectedSource.kind !== "none"
                                ? currentProject.video_tasks?.find((v: any) => v.id === selectedSource.taskId)
                                : undefined;
                            const selectedVideoUrl = selectedSource.kind !== "none" ? selectedSource.url : undefined;

                            return (
                                <motion.div
                                    key={frame.id}
                                    layoutId={`frame-${frame.id}`}
                                    onClick={() => setSelectedFrameId(frame.id)}
                                    className={`group relative flex rounded-xl overflow-hidden cursor-pointer border transition-all bg-glass hover:bg-hover-bg ${isSelected ? "border-primary ring-1 ring-primary/50" :
                                        selectedVideoId ? "border-green-500/30" : "border-glass-border"
                                        }`}
                                >
                                    {/* Left: Preview */}
                                    <div className="w-48 aspect-video relative flex-shrink-0 border-r border-glass-border bg-elevated">
                                        {selectedVideo ? (
                                            <video
                                                src={getAssetUrl(selectedVideoUrl || selectedVideo.video_url)}
                                                className="w-full h-full object-cover"
                                                muted
                                                onMouseOver={(e) => e.currentTarget.play()}
                                                onMouseOut={(e) => {
                                                    e.currentTarget.pause();
                                                    e.currentTarget.currentTime = 0;
                                                }}
                                            />
                                        ) : (
                                            <div className="w-full h-full relative">
                                                {frame.image_url ? (
                                                    <img
                                                        src={getAssetUrl(frame.image_url)}
                                                        className="w-full h-full object-cover opacity-50 grayscale"
                                                    />
                                                ) : (
                                                    <div className="w-full h-full bg-glass" />
                                                )}
                                                <div className="absolute inset-0 flex items-center justify-center">
                                                    {hasVideos ? (
                                                        <div className="bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded text-xs font-bold border border-yellow-500/50">
                                                            {ta("selectVideo")}
                                                        </div>
                                                    ) : (
                                                        <div className="bg-red-500/20 text-red-500 px-2 py-1 rounded text-xs font-bold border border-red-500/50">
                                                            {ta("noVideos")}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                        <div className="absolute top-2 left-2 bg-surface px-2 py-0.5 rounded text-[0.625rem] font-mono text-foreground">
                                            #{index + 1}
                                        </div>
                                    </div>

                                    {/* Right: Details */}
                                    <div className="flex-1 p-4 flex flex-col justify-between min-w-0">
                                        <div className="space-y-2">
                                            <div className="flex items-start gap-2">
                                                <FileText size={14} className="text-text-muted mt-0.5 flex-shrink-0" />
                                                <p className="text-sm text-text-secondary line-clamp-2 leading-relaxed">
                                                    {frame.image_prompt || frame.action_description || ta("noPrompt")}
                                                </p>
                                            </div>
                                            {frame.dialogue && (
                                                <div className="flex items-start gap-2 pl-6 border-l-2 border-glass-border ml-1">
                                                    <p className="text-xs text-text-secondary italic">"{frame.dialogue}"</p>
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex items-center justify-between mt-2 pt-2 border-t border-border-subtle">
                                            <div className="flex items-center gap-4 text-xs text-text-muted">
                                                <span className="flex items-center gap-1">
                                                    <Clock size={12} /> {selectedVideo ? `${selectedVideo.duration}s` : "--"}
                                                </span>
                                                <span className="flex items-center gap-1">
                                                    <Film size={12} /> {videosByFrame[frame.id]?.length || 0} {ta("variants")}
                                                </span>
                                            </div>

                                            {selectedVideoId && (
                                                <div className="flex items-center gap-1 text-green-500 text-xs font-bold">
                                                    <Check size={12} /> Ready
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </motion.div>
                            );
                        })}
                </div>
                )}

                {/* Mix phase body — BGM picker + per-track volume sliders */}
                {phase === "mix" && (
                    <MixPhase scriptId={currentProject?.id ?? null}
                              bgmUrl={currentProject?.bgm_url ?? null}
                              mixSettings={currentProject?.mix_settings as Record<string, number> | undefined}
                    />
                )}

                {/* Export phase body — merge action + final preview + download */}
                {phase === "export" && (
                    <div className="flex-1 overflow-y-auto custom-scrollbar p-8 space-y-6">
                        <ExportPhase
                            mergeDisplay={mergeDisplay}
                            isMerging={isMerging && mergeProjectId === currentProject?.id}
                            isDownloading={isDownloading}
                            mergeError={mergeError}
                            framesReady={framesReady}
                            framesTotal={framesTotal}
                            onMerge={handleMerge}
                            onDownload={handleDownload}
                            onDismissError={() => setMergeError(null)}
                        />
                    </div>
                )}
                </div>

            {/* Right Sidebar - Variants — only visible in Takes phase */}
            {phase === "takes" && (
            <div className="w-[360px] shrink-0 bg-surface flex flex-col z-10 border-l border-glass-border overflow-hidden">
                <SidePanelHeader
                    icon={<Film />}
                    title={ta("variants")}
                    subtitle={selectedFrameId
                        ? `Frame #${(currentProject?.frames?.findIndex((f: any) => f.id === selectedFrameId) ?? -1) + 1}`
                        : undefined}
                />
                <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
                        {selectedFrameId ? (
                            <div className="space-y-4">
                                {variants.length > 0 ? (
                                    variants.map((video: any, idx: number) => {
                                        const isSelected = selectedFrame?.selected_video_id === video.id;
                                        const verifiedVideoUrl = selectedFrame
                                            ? selectVerifiedAppliedDubUrlForTask(selectedFrame, video) || video.video_url
                                            : video.video_url;
                                        return (
                                            <div
                                                key={video.id}
                                                className={`rounded-xl overflow-hidden border transition-all group ${isSelected ? "border-green-500 ring-1 ring-green-500/50 bg-green-500/5" : "border-glass-border bg-glass hover:border-glass-border"
                                                    }`}
                                            >
                                                <div className="aspect-video relative bg-black">
                                                    <video
                                                        src={getAssetUrl(verifiedVideoUrl)}
                                                        className="w-full h-full object-contain"
                                                        controls
                                                    />
                                                    {/* Overlay Info */}
                                                    <div className="absolute top-2 left-2 bg-surface px-1.5 rounded text-[0.625rem] text-foreground opacity-0 group-hover:opacity-100 transition-opacity">
                                                        {video.duration}s
                                                    </div>
                                                </div>
                                                <div className="p-3">
                                                    <div className="flex items-center justify-between mb-2">
                                                        <div className="text-xs text-text-secondary">
                                                            Variant #{idx + 1}
                                                        </div>
                                                        <div className="text-[0.625rem] px-1.5 py-0.5 rounded bg-hover-bg text-text-secondary">
                                                            {video.model}
                                                        </div>
                                                    </div>

                                                    {isSelected ? (
                                                        <div className="w-full py-2 bg-green-500/10 text-green-500 rounded-lg text-xs font-bold flex items-center justify-center gap-2 border border-green-500/20">
                                                            <Check size={14} /> {ta("selected")}
                                                        </div>
                                                    ) : (
                                                        <button
                                                            onClick={() => handleSelectVideo(selectedFrameId, video.id)}
                                                            disabled={selectingVideoKey === `${currentProject?.id}:${selectedFrameId}:${video.id}`}
                                                            className="w-full py-2 bg-hover-bg hover:bg-hover-bg rounded-lg text-xs font-medium transition-colors text-foreground disabled:opacity-50 disabled:cursor-not-allowed"
                                                        >
                                                            {selectingVideoKey === `${currentProject?.id}:${selectedFrameId}:${video.id}` ? <Loader2 size={12} className="mx-auto animate-spin" /> : ta("selectThisVariant")}
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })
                                ) : (
                                    <div className="text-center py-12 text-text-muted flex flex-col items-center">
                                        <AlertTriangle className="mb-3 opacity-50" size={32} />
                                        <p className="text-sm font-medium">{ta("noVideosGenerated")}</p>
                                        <p className="text-xs mt-1 max-w-[200px]">{ta("noVideosHint")}</p>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="h-full flex flex-col items-center justify-center text-text-muted gap-3">
                                <Layout size={48} className="opacity-10" />
                                <p className="text-sm">{ta("selectFrameHint")}</p>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

// ──────────────────────────────────────────────────────────────────
// PR-3k · Phase sub-components
// ──────────────────────────────────────────────────────────────────

function MixPhase({
    scriptId,
    bgmUrl,
    mixSettings,
}: {
    scriptId: string | null;
    bgmUrl: string | null;
    mixSettings: Record<string, number> | undefined;
}) {
    const ta = useTranslations("assembly");
    const tc = useTranslations("frameCommands");
    const [presets, setPresets] = useState<BgmPreset[]>([]);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [draftBgmUrl, setDraftBgmUrl] = useState<string | null>(bgmUrl ?? null);
    const [draftMix, setDraftMix] = useState<Record<string, number>>(mixSettings ?? DEFAULT_MIX_SETTINGS);
    const [hasFailedDraft, setHasFailedDraft] = useState(false);
    const [mixError, setMixError] = useState<string | null>(null);
    const tCommon = useTranslations("common");
    const retryingRef = useRef(false);
    const audioMixQueueRef = useRef<Promise<void>>(Promise.resolve());
    const blockedQueuedSerialRef = useRef(0);
    const draftSerialRef = useRef(0);
    const scriptEnvironment = currentProjectId() === scriptId ? currentProjectEnvironment() : undefined;
    const scriptRuntime = getRuntimeEnvironment();
    const scriptGeneration = scriptRuntime && scriptRuntime.key === scriptEnvironment ? scriptRuntime.generation : undefined;
    const sessionRef = useRef({ projectId: scriptId, environment: scriptEnvironment, generation: scriptGeneration, token: 0 });
    const mix = draftMix;

    useEffect(() => {
        if (sessionRef.current.projectId !== scriptId
            || sessionRef.current.environment !== scriptEnvironment
            || sessionRef.current.generation !== scriptGeneration) {
            sessionRef.current = { projectId: scriptId, environment: scriptEnvironment, generation: scriptGeneration, token: sessionRef.current.token + 1 };
            blockedQueuedSerialRef.current = draftSerialRef.current;
            audioMixQueueRef.current = Promise.resolve();
            retryingRef.current = false;
            setSaving(false);
            setDraftBgmUrl(bgmUrl ?? null);
            setDraftMix(mixSettings ?? DEFAULT_MIX_SETTINGS);
            setHasFailedDraft(false);
            setMixError(null);
        } else if (!hasFailedDraft && !saving) {
            setDraftBgmUrl(bgmUrl ?? null);
            setDraftMix(mixSettings ?? DEFAULT_MIX_SETTINGS);
        }
    }, [bgmUrl, hasFailedDraft, mixSettings, saving, scriptEnvironment, scriptGeneration, scriptId]);

    useEffect(() => () => {
        sessionRef.current = {
            projectId: sessionRef.current.projectId,
            environment: sessionRef.current.environment,
            generation: sessionRef.current.generation,
            token: sessionRef.current.token + 1,
        };
        blockedQueuedSerialRef.current = draftSerialRef.current;
    }, []);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        api.listBgmPresets()
            .then((p) => { if (!cancelled) setPresets(p); })
            .catch(() => { if (!cancelled) setPresets([]); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, []);

    const enqueueAudioMixPatch = (projectId: string, environment: string | undefined, generation: number | undefined, token: number, draftSerial: number, payload: {
        bgm_url?: string | null;
        dialogue_volume?: number;
        bgm_volume?: number;
        sfx_volume?: number;
    }) => {
        setSaving(true);
        setMixError(null);
        const run = async () => {
            const isActive = () => sessionRef.current.token === token
                && sessionRef.current.projectId === projectId
                && sessionRef.current.environment === environment
                && sessionRef.current.generation === generation
                && isCurrentProject(projectId, environment, generation);
            if (!isActive() || draftSerial <= blockedQueuedSerialRef.current) return;
            try {
                const response = await api.updateAudioMix(projectId, payload);
                if (!isActive()) return;
                await confirmRawProjectAck(projectId, response, { isCurrent: () => isActive() });
                const snapshot = useProjectStore.getState().currentProject;
                if (!isActive() || snapshot?.id !== projectId) return;
                if (draftSerialRef.current === draftSerial) {
                    setDraftBgmUrl(snapshot.bgm_url ?? null);
                    setDraftMix(snapshot.mix_settings ?? DEFAULT_MIX_SETTINGS);
                    setHasFailedDraft(false);
                }
            } catch (error) {
                if (!isActive()) return;
                blockedQueuedSerialRef.current = Math.max(blockedQueuedSerialRef.current, draftSerialRef.current);
                setHasFailedDraft(true);
                setMixError(error instanceof ProjectReadbackError ? tc("savedReadFailed") : extractErrorDetail(error, "Failed to update audio mix"));
            }
        };
        const queued = audioMixQueueRef.current.catch(() => undefined).then(run);
        audioMixQueueRef.current = queued;
        queued.finally(() => {
            if (audioMixQueueRef.current === queued) {
                retryingRef.current = false;
                setSaving(false);
            }
        });
    };

    const handlePick = (preset: BgmPreset | null) => {
        if (!scriptId || !isCurrentProject(scriptId, scriptEnvironment, scriptGeneration)) return;
        const projectId = scriptId;
        const token = sessionRef.current.token;
        const environment = sessionRef.current.environment;
        const generation = sessionRef.current.generation;
        const nextBgm = preset ? preset.url : null;
        const draftSerial = draftSerialRef.current + 1;
        draftSerialRef.current = draftSerial;
        setDraftBgmUrl(nextBgm);
        if (!hasFailedDraft) enqueueAudioMixPatch(projectId, environment, generation, token, draftSerial, { bgm_url: nextBgm });
    };

    const handleVolume = (track: "dialogue" | "bgm", value: number) => {
        if (!scriptId || !isCurrentProject(scriptId, scriptEnvironment, scriptGeneration)) return;
        const projectId = scriptId;
        const token = sessionRef.current.token;
        const environment = sessionRef.current.environment;
        const generation = sessionRef.current.generation;
        const draftSerial = draftSerialRef.current + 1;
        draftSerialRef.current = draftSerial;
        setDraftMix((current) => ({ ...current, [track]: value }));
        if (!hasFailedDraft) enqueueAudioMixPatch(projectId, environment, generation, token, draftSerial, { [`${track}_volume`]: value });
    };

    const saveFailedDraft = () => {
        if (!scriptId || !isCurrentProject(scriptId, scriptEnvironment, scriptGeneration) || saving || retryingRef.current) return;
        retryingRef.current = true;
        const serial = ++draftSerialRef.current;
        enqueueAudioMixPatch(scriptId, sessionRef.current.environment, sessionRef.current.generation, sessionRef.current.token, serial, {
            bgm_url: draftBgmUrl,
            dialogue_volume: draftMix.dialogue,
            bgm_volume: draftMix.bgm,
            sfx_volume: draftMix.sfx,
        });
    };

    return (
        <div className="flex-1 overflow-y-auto custom-scrollbar p-8 space-y-8">
            {/* BGM picker */}
            <section>
                <h3 className="mb-3 flex items-center gap-2 font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-text-muted">
                    <Music size={12} className="text-primary" />
                    {ta("mixBgmTitle")}
                    {saving && <Loader2 size={12} className="animate-spin text-primary" />}
                </h3>
                {/* Merge behavior note — no BGM keeps source audio; unavailable selected BGM blocks merge. */}
                <div className="mb-3 flex items-start gap-2 rounded-md border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2">
                    <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-400/85" aria-hidden="true" />
                    <p className="text-[0.71875rem] leading-relaxed text-amber-100/85">
                        {ta("mixBgmPreviewNotice")}
                    </p>
                </div>
                {mixError && (
                    <div role="alert" className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-[0.75rem] text-red-200">
                        {mixError}
                    </div>
                )}
                {hasFailedDraft && (
                    <button type="button" onClick={saveFailedDraft} disabled={saving}
                        className="mb-3 rounded-md bg-primary px-3 py-2 text-sm text-white disabled:opacity-50">
                        {tCommon("save")}
                    </button>
                )}
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                    <button
                        onClick={() => handlePick(null)}
                        className={`rounded-lg border p-3 text-left transition-colors ${
                            !draftBgmUrl
                                ? "border-primary bg-[rgba(100,108,255,0.10)]"
                                : "border-glass-border bg-glass hover:border-foreground/30"
                        }`}
                    >
                        <p className="text-[0.8125rem] font-medium text-foreground">{ta("mixBgmNone")}</p>
                        <p className="mt-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-text-muted">silent</p>
                    </button>
                    {loading ? (
                        <div className="col-span-3 grid place-items-center py-4 text-text-muted">
                            <Loader2 size={16} className="animate-spin" />
                        </div>
                    ) : (
                        presets.map((p) => {
                            const selected = draftBgmUrl === p.url;
                            return (
                                <button
                                    key={p.id}
                                    onClick={() => handlePick(p)}
                                    className={`rounded-lg border p-3 text-left transition-colors ${
                                        selected
                                            ? "border-primary bg-[rgba(100,108,255,0.10)]"
                                            : "border-glass-border bg-glass hover:border-foreground/30"
                                    }`}
                                >
                                    <p className="text-[0.8125rem] font-medium text-foreground truncate">{p.label}</p>
                                    <p className="mt-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-text-muted">{p.mood}</p>
                                </button>
                            );
                        })
                    )}
                </div>
            </section>

            {/* Volume sliders */}
            <section>
                <h3 className="mb-3 flex items-center gap-2 font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-text-muted">
                    <Sliders size={12} className="text-primary" />
                    {ta("mixLevelsTitle")}
                </h3>
                <div className="space-y-3 max-w-lg">
                    {(["dialogue", "bgm"] as const).map((track) => (
                        <div key={track} className="flex items-center gap-3">
                            <span className="w-20 font-mono text-[0.6875rem] uppercase tracking-[0.16em] text-text-muted">{ta(`mixTrack.${track}`)}</span>
                            <input
                                type="range"
                                aria-label={ta(`mixTrack.${track}`)}
                                min={0}
                                max={100}
                                step={1}
                                value={mix[track] ?? 0}
                                onChange={(e) => handleVolume(track, Number(e.target.value))}
                                className="flex-1 accent-primary"
                            />
                            <span className="w-12 text-right font-mono text-[0.6875rem] text-text-secondary">{mix[track] ?? 0}</span>
                        </div>
                    ))}
                </div>
                <p className="mt-3 text-[0.6875rem] text-text-muted max-w-lg">
                    {ta("mixHint")}
                </p>
            </section>
        </div>
    );
}

function ExportPhase({
    mergeDisplay,
    isMerging,
    isDownloading,
    mergeError,
    framesReady,
    framesTotal,
    onMerge,
    onDownload,
    onDismissError,
}: {
    mergeDisplay: MergeVideoDisplayState;
    isMerging: boolean;
    isDownloading: boolean;
    mergeError: string | null;
    framesReady: number;
    framesTotal: number;
    onMerge: () => void;
    onDownload: () => void;
    onDismissError: () => void;
}) {
    const ta = useTranslations("assembly");
    const allReady = framesTotal > 0 && framesReady === framesTotal;
    const mergedVideoUrl = mergeDisplay.url;
    const isCurrentMerge = mergeDisplay.kind === 'current';
    const showHistoricalWarning = mergeDisplay.kind === 'stale' || mergeDisplay.kind === 'unknown';
    return (
        <div className="space-y-6 max-w-3xl">
            <section className="rounded-xl border border-glass-border bg-glass p-6">
                <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                        <h3 className="text-display font-medium text-foreground flex items-center gap-2">
                            <Package size={16} className="text-primary" />
                            {ta("exportTitle")}
                        </h3>
                        <p className="mt-1 text-body-sm text-text-secondary">
                            {ta("exportSubtitle", { ready: framesReady, total: framesTotal })}
                        </p>
                    </div>
                    <button
                        onClick={onMerge}
                        disabled={isMerging || !allReady}
                        className="shrink-0 inline-flex items-center gap-2 bg-primary text-white border border-[rgba(100,108,255,0.65)] shadow-[inset_0_1.5px_0_rgba(255,255,255,0.14)] hover:bg-primary-hover disabled:opacity-40 disabled:cursor-not-allowed px-5 py-2.5 rounded-md font-semibold text-[0.8125rem]"
                    >
                        {isMerging ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
                        {ta("mergeAndProceed")}
                    </button>
                </div>
            </section>

            {mergeError && (
                <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4">
                    <div className="flex items-start gap-3">
                        <AlertTriangle className="text-red-500 flex-shrink-0 mt-0.5" size={20} />
                        <div className="flex-1 min-w-0">
                            <h4 className="text-sm font-bold text-red-400 mb-1">{ta("mergeFailed")}</h4>
                            <p className="text-xs text-red-300/90 whitespace-pre-wrap leading-relaxed font-mono break-all">
                                {mergeError}
                            </p>
                            {mergeError.toLowerCase().includes("ffmpeg") && (
                                <a href="https://ffmpeg.org/download.html" target="_blank" rel="noopener noreferrer" className="text-xs text-blue-400 hover:text-blue-300 underline mt-2 inline-block">
                                    Download FFmpeg →
                                </a>
                            )}
                            <button onClick={onDismissError} className="mt-3 text-xs text-text-secondary hover:text-foreground underline">
                                {ta("dismiss")}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <AnimatePresence>
                {mergedVideoUrl && (
                    <motion.section
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="rounded-xl border border-glass-border bg-elevated overflow-hidden"
                    >
                        <div className="grid md:grid-cols-2 gap-0">
                            <div className="aspect-video bg-black">
                                <video src={getAssetUrl(mergedVideoUrl)} className="w-full h-full object-contain" controls autoPlay />
                            </div>
                            <div className="p-5 flex flex-col justify-center gap-3">
                                <div>
                                    <h3 className="text-display font-medium text-foreground flex items-center gap-2">
                                        {isCurrentMerge ? <Check className="text-green-500" size={16} /> : <AlertTriangle className="text-amber-400" size={16} />}
                                        {isCurrentMerge ? ta("mergedVideoReady") : ta("mergedVideoHistoryTitle")}
                                    </h3>
                                    <p className="text-body-sm text-text-secondary mt-1">
                                        {isCurrentMerge ? ta("mergedVideoDesc") : ta("mergedVideoHistoryDesc")}
                                    </p>
                                </div>
                                {showHistoricalWarning && (
                                    <div role="status" className="rounded-md border border-amber-500/25 bg-amber-500/[0.08] px-3 py-2 text-[0.75rem] leading-relaxed text-amber-100/90">
                                        {mergeDisplay.kind === 'stale' ? ta("mergedVideoStale") : ta("mergedVideoUnknown")}
                                    </div>
                                )}
                                <button
                                    onClick={onDownload}
                                    disabled={isDownloading}
                                    className="self-start inline-flex items-center gap-2 px-5 py-2.5 rounded-md bg-glass border border-glass-border text-foreground hover:bg-hover-bg transition-colors text-[0.8125rem] font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    <Download size={14} />
                                    {isDownloading ? ta("downloading") : isCurrentMerge ? ta("downloadMP4") : ta("downloadHistoryMP4")}
                                </button>
                            </div>
                        </div>
                    </motion.section>
                )}
            </AnimatePresence>
        </div>
    );
}
