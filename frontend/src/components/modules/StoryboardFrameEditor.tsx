"use client";

import { useState, useEffect, useMemo } from "react";
import { motion } from "framer-motion";
import NextImage from "next/image";
import { Image as ImageIcon, Layers3, Loader2, ShieldCheck, ShieldOff, Sparkles, X } from "lucide-react";
import { api } from "@/lib/api";
import { VariantSelector } from "../common/VariantSelector";
import PromptQualityPanel from "../common/PromptQualityPanel";
import { useProjectStore } from "@/store/projectStore";
import type { CodexImagegenMode, StoryboardFrame, StoryBeat } from "@/store/projectStore";
import { messages } from "@/lib/i18n";
import { getAssetUrl } from "@/lib/utils";
import { getGenerationBadgeText, getGenerationTooltip, isGenerationDegraded } from "@/lib/generation-provenance";
import {
    buildStoryboardCompositionData,
    buildStoryboardReferencePreview,
    getArtDirectionPromptPrefix,
    getPreviousSameSceneFrame,
    getNextSameSceneFrame,
    getSelectedFrameReference,
    normalizeCodexImagegenRecommendation,
    recommendCodexImagegenMode,
} from "@/lib/storyboard-references";
import {
    formatPromptIssues,
    hasBlockingPromptIssues,
    inspectStoryboardPrompt,
} from "@/lib/prompt-quality";

const copy = messages.modules.storyboardFrameEditor;

function formatChapterLabel(order?: number | null, title?: string | null) {
    if (order == null && !title) return "未分章正文";
    if (order == null) return title || "未分章正文";
    if (!title) return `第${order}章`;
    if (/^\s*第?\s*\d+\s*章/.test(title)) return title;
    return `第${order}章 · ${title}`;
}

function formatBeatLabel(order?: number | null, title?: string | null) {
    if (order == null && !title) return "未归属场次";
    if (order == null) return title || "未归属场次";
    if (title && /^\s*(第?\s*\d+\s*场|场次\s*\d+)/.test(title)) return title;
    return `场次 ${order} · ${title || "未归属场次"}`;
}

function getContinuityLock(frame: StoryboardFrame, fallback: boolean): boolean {
    const lock = frame.composition_data?.continuity_lock;
    return typeof lock === "boolean" ? lock : fallback;
}

function formatBytes(value: unknown): string {
    if (typeof value !== "number" || !Number.isFinite(value)) return "0 B";
    if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(2)} MiB`;
    if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
    return `${value} B`;
}

function getReferencePayloadPreflight(frame: StoryboardFrame): Record<string, unknown> | null {
    const value = frame.composition_data?.reference_payload_preflight;
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null;
}

interface StoryboardFrameEditorProps {
    frame: StoryboardFrame;
    onClose: () => void;
}

export default function StoryboardFrameEditor({ frame: initialFrame, onClose }: StoryboardFrameEditorProps) {
    const currentProject = useProjectStore(state => state.currentProject);
    const updateProject = useProjectStore(state => state.updateProject);
    const sceneBeats = useMemo(() => currentProject?.story_analysis?.scene_beats ?? [], [currentProject?.story_analysis?.scene_beats]);

    // Get the latest frame data from the store (instead of using stale prop)
    const frame = useMemo(() => {
        if (!currentProject?.frames) return initialFrame;
        return currentProject.frames.find((f: StoryboardFrame) => f.id === initialFrame.id) || initialFrame;
    }, [currentProject?.frames, initialFrame]);

    const [prompt, setPrompt] = useState(frame.image_prompt || frame.action_description || "");
    const [isGenerating, setIsGenerating] = useState(false);
    const [isUpdatingCodexPolicy, setIsUpdatingCodexPolicy] = useState(false);
    const previousFrame = getPreviousSameSceneFrame(currentProject, frame);
    const nextFrame = getNextSameSceneFrame(currentProject, frame);
    const frameBeatMeta = useMemo(
        () => sceneBeats.find((beat: StoryBeat) => beat.id === frame.story_beat_id) || null,
        [sceneBeats, frame.story_beat_id],
    );
    const continuityLockValue = getContinuityLock(frame, Boolean(previousFrame || nextFrame));
    const [continuityLock, setContinuityLock] = useState<boolean>(continuityLockValue);
    const previousFrameReference = getSelectedFrameReference(previousFrame);
    const sameSceneContinuity = Boolean(previousFrame || nextFrame);
    const beatLabel = formatBeatLabel(frame.story_beat_order ?? frameBeatMeta?.order, frame.story_beat_title || frameBeatMeta?.title);
    const chapterLabel = formatChapterLabel(frame.chapter_order ?? frameBeatMeta?.chapter_order, frame.chapter_title || frameBeatMeta?.chapter_title);
    const generationBadge = getGenerationBadgeText(frame);
    const generationBadgeDegraded = isGenerationDegraded(frame);
    const codexImagegenPolicy = currentProject?.codex_imagegen_policy;
    const codexSafeModeEnabled = codexImagegenPolicy?.enabled ?? true;
    const codexImagegenMode: CodexImagegenMode =
        codexImagegenPolicy?.mode === "two_stage_high_consistency"
            ? "two_stage_high_consistency"
            : "safe_refs_only";
    const codexRecommendationAutoApply = codexImagegenPolicy?.recommendation?.auto_apply ?? false;
    const referencePayloadPreflight = getReferencePayloadPreflight(frame);
    const referencePreviewItems = useMemo(
        () => buildStoryboardReferencePreview(currentProject, frame, {
            continuityLock,
            includeStyleReferences: true,
        }),
        [currentProject, frame, continuityLock],
    );
    const backendCodexImagegenRecommendation = normalizeCodexImagegenRecommendation(frame.composition_data?.codex_imagegen_recommendation);
    const codexImagegenRecommendation = useMemo(
        () => backendCodexImagegenRecommendation
            ? backendCodexImagegenRecommendation
            : recommendCodexImagegenMode(referencePreviewItems),
        [backendCodexImagegenRecommendation, referencePreviewItems],
    );
    const codexImagegenRecommendationLabel =
        codexImagegenRecommendation.mode === "two_stage_high_consistency"
            ? copy.codexImagegenTwoStageMode
            : copy.codexImagegenDirectMode;
    const codexImagegenRecommendationAligned =
        codexSafeModeEnabled && codexImagegenMode === codexImagegenRecommendation.mode;
    const promptIssues = inspectStoryboardPrompt({
        prompt,
        sameSceneContinuity: continuityLock && sameSceneContinuity,
        stylePrompt: getArtDirectionPromptPrefix(currentProject?.art_direction?.style_config),
        referencePreview: referencePreviewItems,
    });

    // Sync prompt when frame changes
    useEffect(() => {
        setPrompt(frame.image_prompt || frame.action_description || "");
    }, [frame.id, frame.image_prompt, frame.action_description]);

    useEffect(() => {
        setContinuityLock(continuityLockValue);
    }, [continuityLockValue]);

    const handleGenerate = async (batchSize: number) => {
        if (!currentProject) return;

        if (hasBlockingPromptIssues(promptIssues)) {
            alert(`${copy.promptQualityBlocked}\n\n${formatPromptIssues(promptIssues.filter((issue) => issue.severity === "error"))}`);
            return;
        }

        setIsGenerating(true);
        try {
            const compositionData = buildStoryboardCompositionData(currentProject, frame, {
                continuityLock,
                includeStyleReferences: false,
                codexRecommendationIncludeStyleReferences: true,
            });

            const updatedProject = await api.renderFrame(
                currentProject.id,
                frame.id,
                compositionData,
                prompt,
                batchSize
            );
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to generate frame:", error);
            alert(copy.failedToGenerateFrame);
        } finally {
            setIsGenerating(false);
        }
    };

    const handleToggleCodexImagegenEnabled = async () => {
        if (!currentProject) return;

        setIsUpdatingCodexPolicy(true);
        try {
            const updatedProject = await api.updateCodexImagegenPolicy(currentProject.id, {
                enabled: !codexSafeModeEnabled,
            });
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to update Codex imagegen policy:", error);
            alert(copy.codexImagegenPolicySaveFailed);
        } finally {
            setIsUpdatingCodexPolicy(false);
        }
    };

    const handleSetCodexImagegenMode = async (mode: CodexImagegenMode) => {
        if (!currentProject || (mode === codexImagegenMode && codexSafeModeEnabled)) return;

        setIsUpdatingCodexPolicy(true);
        try {
            const updatedProject = await api.updateCodexImagegenPolicy(currentProject.id, {
                enabled: true,
                mode,
            });
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to update Codex imagegen mode:", error);
            alert(copy.codexImagegenPolicySaveFailed);
        } finally {
            setIsUpdatingCodexPolicy(false);
        }
    };

    const handleToggleCodexRecommendationAutoApply = async () => {
        if (!currentProject) return;

        setIsUpdatingCodexPolicy(true);
        try {
            const updatedProject = await api.updateCodexImagegenPolicy(currentProject.id, {
                recommendation: {
                    ...(codexImagegenPolicy?.recommendation ?? {}),
                    auto_apply: !codexRecommendationAutoApply,
                },
            });
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to update Codex recommendation policy:", error);
            alert(copy.codexImagegenPolicySaveFailed);
        } finally {
            setIsUpdatingCodexPolicy(false);
        }
    };

    const handleSelectVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
        }
    };

    const handleDeleteVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
        }
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-md p-4 md:p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden shadow-2xl"
            >
                {/* Header */}
                <div className="h-16 border-b border-white/10 flex justify-between items-center px-6 bg-black/20">
                    <div className="flex items-center gap-4">
                        <h2 className="text-xl font-bold text-white">{copy.title} <span className="text-gray-500 font-normal text-sm ml-2">#{frame.id.substring(0, 8)}</span></h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-full text-gray-400 hover:text-white transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 flex overflow-hidden">
                    {/* Left: Variant Selector */}
                    <div className="flex-1 bg-black/40 p-4 flex flex-col overflow-hidden relative">
                        <VariantSelector
                            asset={frame.rendered_image_asset}
                            currentImageUrl={frame.rendered_image_url || frame.image_url}
                            onSelect={handleSelectVariant}
                            onDelete={handleDeleteVariant}
                            onGenerate={handleGenerate}
                            isGenerating={isGenerating}
                            aspectRatio="16:9"
                            className="h-full"
                        />
                    </div>

                    {/* Right: Controls & Prompt */}
                    <div className="w-1/3 min-w-[350px] border-l border-white/10 bg-[#111] flex flex-col">
                        <div className="p-4 border-b border-white/5">
                            <h3 className="font-bold text-sm uppercase tracking-wider text-gray-400 mb-2">
                                {copy.sceneContext}
                            </h3>
                            <p className="text-xs text-gray-300 mb-2">
                                <span className="font-bold text-gray-500">{copy.action}:</span> {frame.action_description}
                            </p>
                            {frame.dialogue && (
                                <p className="text-xs text-gray-300 italic">
                                    <span className="font-bold text-gray-500 not-italic">{copy.dialogue}:</span> &ldquo;{frame.dialogue}&rdquo;
                                </p>
                            )}
                            <div className="mt-3 flex flex-wrap gap-2">
                                <span className="rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2.5 py-1 text-[11px] text-emerald-200">
                                    {beatLabel}
                                </span>
                                <span className="rounded-full border border-cyan-400/20 bg-cyan-400/10 px-2.5 py-1 text-[11px] text-cyan-200">
                                    {chapterLabel}
                                </span>
                                {generationBadge && (
                                    <span
                                        className={`rounded-full border px-2.5 py-1 text-[11px] ${generationBadgeDegraded
                                            ? "border-amber-400/20 bg-amber-400/10 text-amber-200"
                                            : "border-emerald-400/20 bg-emerald-400/10 text-emerald-200"
                                            }`}
                                        title={getGenerationTooltip(frame)}
                                    >
                                        {generationBadge}
                                    </span>
                                )}
                            </div>
                        </div>

                        <div className="flex-1 p-4 flex flex-col">
                            <div className="mb-4 rounded-xl border border-cyan-500/15 bg-cyan-500/8 p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <h3 className="font-bold text-sm text-white">{copy.continuityTitle}</h3>
                                        <p className="mt-1 text-[11px] leading-relaxed text-cyan-100/80">
                                            {copy.continuityHint}
                                        </p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setContinuityLock((value: boolean) => !value)}
                                        aria-label={copy.continuityToggle}
                                        aria-pressed={continuityLock}
                                        className={`w-10 h-5 rounded-full relative transition-colors ${continuityLock ? "bg-cyan-500" : "bg-white/10"}`}
                                    >
                                    <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${continuityLock ? "left-6" : "left-1"}`} />
                                    </button>
                                </div>
                                <p className="mt-3 text-[11px] text-gray-400">
                                    {continuityLock ? copy.continuityEnabled : copy.continuityDisabled}
                                </p>
                                {previousFrameReference && (
                                    <div className="mt-3 rounded-lg border border-white/10 bg-black/20 p-3">
                                        <p className="text-[11px] font-medium text-gray-300">{copy.previousReferenceTitle}</p>
                                        <p className="mt-1 text-[11px] text-gray-500">
                                            {copy.previousReferenceHint(previousFrame?.id?.slice(0, 8) || "")}
                                        </p>
                                        <div className="mt-3 flex items-center gap-3">
                                            <NextImage
                                                src={getAssetUrl(previousFrameReference)}
                                                alt={copy.previousReferenceAlt}
                                                width={64}
                                                height={64}
                                                unoptimized
                                                className="h-16 w-16 rounded-lg object-cover border border-white/10"
                                            />
                                            <div className="text-[11px] leading-relaxed text-gray-400">
                                                {copy.previousReferenceUsage}
                                            </div>
                                        </div>
                                    </div>
                                )}
                                {!previousFrameReference && (
                                    <div className="mt-3 flex items-center gap-2 rounded-lg border border-dashed border-white/10 bg-black/10 px-3 py-2 text-[11px] text-gray-500">
                                        <ImageIcon size={14} />
                                        <span>{copy.previousReferenceEmpty}</span>
                                    </div>
                                )}
                            </div>

                            <div className="mb-4 rounded-xl border border-violet-500/15 bg-violet-500/8 p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <h3 className="font-bold text-sm text-white">{copy.codexImagegenTitle}</h3>
                                        <p className="mt-1 text-[11px] leading-relaxed text-violet-100/80">
                                            {copy.codexImagegenHint}
                                        </p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={handleToggleCodexImagegenEnabled}
                                        aria-label={copy.codexImagegenToggle}
                                        aria-pressed={codexSafeModeEnabled}
                                        disabled={isUpdatingCodexPolicy}
                                        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
                                            codexSafeModeEnabled
                                                ? "border-violet-300/30 bg-violet-400/15 text-violet-100"
                                                : "border-white/10 bg-black/20 text-gray-300"
                                        } ${isUpdatingCodexPolicy ? "opacity-60" : ""}`}
                                    >
                                        {isUpdatingCodexPolicy ? (
                                            <Loader2 size={12} className="animate-spin" />
                                        ) : codexSafeModeEnabled ? (
                                            <ShieldCheck size={12} />
                                        ) : (
                                            <ShieldOff size={12} />
                                        )}
                                        <span>{codexSafeModeEnabled ? copy.codexImagegenEnabled : copy.codexImagegenDisabled}</span>
                                    </button>
                                </div>
                                <div className="mt-3 grid grid-cols-2 gap-2">
                                    <button
                                        type="button"
                                        onClick={() => handleSetCodexImagegenMode("safe_refs_only")}
                                        disabled={isUpdatingCodexPolicy}
                                        className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-left transition-colors ${
                                            codexImagegenMode === "safe_refs_only"
                                                ? "border-violet-300/40 bg-violet-400/15 text-white"
                                                : "border-white/10 bg-black/20 text-gray-300 hover:border-white/20"
                                        } ${isUpdatingCodexPolicy ? "opacity-60" : ""}`}
                                    >
                                        <ShieldCheck size={14} className="mt-0.5 shrink-0" />
                                        <div className="min-w-0">
                                            <div className="text-[11px] font-medium">{copy.codexImagegenDirectMode}</div>
                                            <div className="mt-0.5 text-[10px] leading-relaxed text-gray-400">
                                                {copy.codexImagegenDirectHint}
                                            </div>
                                        </div>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => handleSetCodexImagegenMode("two_stage_high_consistency")}
                                        disabled={isUpdatingCodexPolicy}
                                        className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-left transition-colors ${
                                            codexImagegenMode === "two_stage_high_consistency"
                                                ? "border-cyan-300/40 bg-cyan-400/15 text-white"
                                                : "border-white/10 bg-black/20 text-gray-300 hover:border-white/20"
                                        } ${isUpdatingCodexPolicy ? "opacity-60" : ""}`}
                                    >
                                        <Layers3 size={14} className="mt-0.5 shrink-0" />
                                        <div className="min-w-0">
                                            <div className="text-[11px] font-medium">{copy.codexImagegenTwoStageMode}</div>
                                            <div className="mt-0.5 text-[10px] leading-relaxed text-gray-400">
                                                {copy.codexImagegenTwoStageHint}
                                            </div>
                                        </div>
                                    </button>
                                </div>
                                <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-gray-300">
                                    <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
                                        <p className="text-gray-500">{copy.codexImagegenMode}</p>
                                        <p className="mt-1 font-medium text-white">
                                            {codexImagegenMode}
                                        </p>
                                    </div>
                                    <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
                                        <p className="text-gray-500">{copy.codexImagegenBudget}</p>
                                        <p className="mt-1 font-medium text-white">
                                            {formatBytes(codexImagegenPolicy?.max_total_bytes ?? 1024 * 1024)}
                                        </p>
                                    </div>
                                </div>
                                <div className={`mt-3 rounded-lg border px-3 py-2 text-[11px] ${
                                    codexImagegenRecommendation.mode === "two_stage_high_consistency"
                                        ? "border-cyan-300/20 bg-cyan-400/10"
                                        : "border-violet-300/20 bg-violet-400/10"
                                }`}>
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <p className="flex items-center gap-1.5 font-medium text-white">
                                                <Sparkles size={12} />
                                                {copy.codexImagegenRecommendationTitle}
                                                <span className="text-gray-300">· {codexImagegenRecommendationLabel}</span>
                                            </p>
                                            <p className="mt-1 leading-relaxed text-gray-400">
                                                {codexImagegenRecommendation.reason}
                                            </p>
                                            <p className="mt-1 text-[10px] text-gray-500">
                                                {copy.codexImagegenRecommendationStats(
                                                    codexImagegenRecommendation.metrics.readyCount,
                                                    codexImagegenRecommendation.metrics.totalCount,
                                                    codexImagegenRecommendation.metrics.characterCount,
                                                    codexImagegenRecommendation.metrics.propCount,
                                                    codexImagegenRecommendation.metrics.environmentCount,
                                                    codexImagegenRecommendation.metrics.missingRequiredCount,
                                                )}
                                            </p>
                                            <button
                                                type="button"
                                                aria-label={copy.codexImagegenRecommendationAutoApply}
                                                aria-pressed={codexRecommendationAutoApply}
                                                onClick={handleToggleCodexRecommendationAutoApply}
                                                disabled={isUpdatingCodexPolicy}
                                                className="mt-2 inline-flex items-center gap-2 text-[10px] text-gray-300 transition-colors hover:text-white disabled:opacity-60"
                                            >
                                                <span
                                                    className={`relative h-4 w-7 rounded-full border transition-colors ${
                                                        codexRecommendationAutoApply
                                                            ? "border-emerald-300/40 bg-emerald-400/30"
                                                            : "border-white/15 bg-black/30"
                                                    }`}
                                                >
                                                    <span
                                                        className={`absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-transform ${
                                                            codexRecommendationAutoApply ? "translate-x-3.5" : "translate-x-0.5"
                                                        }`}
                                                    />
                                                </span>
                                                <span>
                                                    {codexRecommendationAutoApply
                                                        ? copy.codexImagegenRecommendationAutoApplyOn
                                                        : copy.codexImagegenRecommendationAutoApplyOff}
                                                </span>
                                            </button>
                                        </div>
                                        {codexImagegenRecommendationAligned ? (
                                            <span className="shrink-0 rounded-full border border-emerald-300/20 bg-emerald-400/10 px-2 py-1 text-[10px] text-emerald-100">
                                                {copy.codexImagegenRecommendationAligned}
                                            </span>
                                        ) : (
                                            <button
                                                type="button"
                                                onClick={() => handleSetCodexImagegenMode(codexImagegenRecommendation.mode)}
                                                disabled={isUpdatingCodexPolicy}
                                                className="shrink-0 rounded-full border border-white/10 bg-black/30 px-2.5 py-1 text-[10px] text-white transition-colors hover:border-white/25 hover:bg-white/10 disabled:opacity-60"
                                            >
                                                {copy.codexImagegenRecommendationApply}
                                            </button>
                                        )}
                                    </div>
                                </div>
                                <div className="mt-3 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-[11px] text-gray-400">
                                    {codexSafeModeEnabled
                                        ? codexImagegenMode === "two_stage_high_consistency"
                                            ? copy.codexImagegenTwoStageSummary
                                            : copy.codexImagegenDirectSummary
                                        : copy.codexImagegenOffHint}
                                </div>
                            </div>

                            <div className="mb-4 rounded-xl border border-amber-500/15 bg-amber-500/8 p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <h3 className="font-bold text-sm text-white">{copy.referencePreviewTitle}</h3>
                                        <p className="mt-1 text-[11px] leading-relaxed text-amber-100/80">
                                            {copy.referencePreviewHint}
                                        </p>
                                    </div>
                                    <span className="rounded-full bg-black/30 px-2 py-1 text-[10px] text-amber-100">
                                        {copy.referencePreviewCount(referencePreviewItems.filter((item) => item.status === "ready").length, referencePreviewItems.length)}
                                    </span>
                                </div>
                                <div className="mt-3 space-y-2">
                                    {referencePreviewItems.map((item) => (
                                        <div
                                            key={`${item.type}-${item.id}`}
                                            className={`flex items-center gap-3 rounded-lg border px-2.5 py-2 ${item.status === "ready" ? "border-white/10 bg-black/20" : "border-red-400/20 bg-red-500/10"}`}
                                        >
                                            {item.url ? (
                                                <NextImage
                                                    src={getAssetUrl(item.url)}
                                                    alt={item.name}
                                                    width={36}
                                                    height={36}
                                                    unoptimized
                                                    className="h-9 w-9 rounded-md object-cover border border-white/10"
                                                />
                                            ) : (
                                                <div className="flex h-9 w-9 items-center justify-center rounded-md border border-dashed border-red-300/30 bg-black/20">
                                                    <ImageIcon size={15} className="text-red-200/70" />
                                                </div>
                                            )}
                                            <div className="min-w-0 flex-1">
                                                <div className="flex items-center gap-2">
                                                    <span className="truncate text-xs font-medium text-gray-100">{item.name}</span>
                                                    {item.locked && (
                                                        <span className="rounded bg-yellow-400/10 px-1.5 py-0.5 text-[10px] text-yellow-200">{copy.referenceLocked}</span>
                                                    )}
                                                </div>
                                                <p className="mt-0.5 text-[10px] text-gray-500">
                                                    {copy.referenceTypeLabels[item.type]} · {item.source}
                                                </p>
                                            </div>
                                            <span className={`text-[10px] font-medium ${item.status === "ready" ? "text-green-300" : "text-red-300"}`}>
                                                {item.status === "ready" ? copy.referenceReady : item.required ? copy.referenceMissingRequired : copy.referenceMissingOptional}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {referencePayloadPreflight && (
                                <div className="mb-4 rounded-xl border border-emerald-500/15 bg-emerald-500/8 p-3">
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <h3 className="font-bold text-sm text-white">{copy.referencePayloadTitle}</h3>
                                            <p className="mt-1 text-[11px] leading-relaxed text-emerald-100/80">
                                                {copy.referencePayloadHint}
                                            </p>
                                        </div>
                                        <span className="rounded-full bg-black/30 px-2 py-1 text-[10px] text-emerald-100">
                                            {referencePayloadPreflight.prepared ? copy.referencePayloadPrepared : copy.referencePayloadWithinBudget}
                                        </span>
                                    </div>
                                    <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-gray-300">
                                        <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
                                            <p className="text-gray-500">{copy.referencePayloadRefs}</p>
                                            <p className="mt-1 font-medium text-white">{String(referencePayloadPreflight.reference_count ?? 0)}</p>
                                        </div>
                                        <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
                                            <p className="text-gray-500">{copy.referencePayloadBudget}</p>
                                            <p className="mt-1 font-medium text-white">{formatBytes(referencePayloadPreflight.max_total_bytes)}</p>
                                        </div>
                                        <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
                                            <p className="text-gray-500">{copy.referencePayloadSource}</p>
                                            <p className="mt-1 font-medium text-white">{formatBytes(referencePayloadPreflight.total_source_bytes)}</p>
                                        </div>
                                        <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
                                            <p className="text-gray-500">{copy.referencePayloadRequest}</p>
                                            <p className="mt-1 font-medium text-white">{formatBytes(referencePayloadPreflight.total_prepared_bytes)}</p>
                                        </div>
                                    </div>
                                </div>
                            )}

                            <h3 className="font-bold text-sm uppercase tracking-wider text-gray-400 mb-2">
                                {copy.generationPrompt}
                            </h3>
                            <textarea
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                className="flex-1 w-full bg-black/20 border border-white/10 rounded-lg p-4 text-sm text-gray-300 resize-none focus:outline-none focus:border-primary/50 font-mono leading-relaxed"
                                placeholder={copy.promptPlaceholder}
                            />
                            <div className="mt-3">
                                <PromptQualityPanel
                                    issues={promptIssues}
                                    title={copy.promptQualityTitle}
                                    compact
                                />
                            </div>
                            <p className="text-xs text-gray-500 mt-2">
                                {copy.promptHint}
                            </p>
                        </div>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}
