"use client";

import { useState, useEffect, useMemo } from "react";
import { motion } from "framer-motion";
import { Image as ImageIcon, X } from "lucide-react";
import { api } from "@/lib/api";
import { VariantSelector } from "../common/VariantSelector";
import PromptQualityPanel from "../common/PromptQualityPanel";
import { useProjectStore } from "@/store/projectStore";
import { messages } from "@/lib/i18n";
import { getAssetUrl } from "@/lib/utils";
import {
    buildStoryboardCompositionData,
    buildStoryboardReferencePreview,
    getArtDirectionPromptPrefix,
    getPreviousSameSceneFrame,
    getNextSameSceneFrame,
    getSelectedFrameReference,
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

interface StoryboardFrameEditorProps {
    frame: any;
    onClose: () => void;
}

export default function StoryboardFrameEditor({ frame: initialFrame, onClose }: StoryboardFrameEditorProps) {
    const currentProject = useProjectStore(state => state.currentProject);
    const updateProject = useProjectStore(state => state.updateProject);

    // Get the latest frame data from the store (instead of using stale prop)
    const frame = useMemo(() => {
        if (!currentProject?.frames) return initialFrame;
        return currentProject.frames.find((f: any) => f.id === initialFrame.id) || initialFrame;
    }, [currentProject?.frames, initialFrame.id, initialFrame]);

    const [prompt, setPrompt] = useState(frame.image_prompt || frame.action_description || "");
    const [isGenerating, setIsGenerating] = useState(false);
    const previousFrame = getPreviousSameSceneFrame(currentProject, frame);
    const nextFrame = getNextSameSceneFrame(currentProject, frame);
    const frameBeatMeta = useMemo(
        () => currentProject?.story_analysis?.scene_beats?.find((beat: any) => beat.id === frame.story_beat_id) || null,
        [currentProject?.story_analysis?.scene_beats, frame.story_beat_id],
    );
    const [continuityLock, setContinuityLock] = useState<boolean>(
        frame.composition_data?.continuity_lock ?? Boolean(previousFrame || nextFrame),
    );
    const previousFrameReference = getSelectedFrameReference(previousFrame);
    const sameSceneContinuity = Boolean(previousFrame || nextFrame);
    const beatLabel = formatBeatLabel(frame.story_beat_order ?? frameBeatMeta?.order, frame.story_beat_title || frameBeatMeta?.title);
    const chapterLabel = formatChapterLabel(frame.chapter_order ?? frameBeatMeta?.chapter_order, frame.chapter_title || frameBeatMeta?.chapter_title);
    const referencePreviewItems = useMemo(
        () => buildStoryboardReferencePreview(currentProject, frame, {
            continuityLock,
            includeStyleReferences: true,
        }),
        [currentProject, frame, continuityLock],
    );
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
        setContinuityLock(frame.composition_data?.continuity_lock ?? Boolean(previousFrame || nextFrame));
    }, [frame.id, frame.composition_data?.continuity_lock, previousFrame, nextFrame]);

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
                                    <span className="font-bold text-gray-500 not-italic">{copy.dialogue}:</span> "{frame.dialogue}"
                                </p>
                            )}
                            <div className="mt-3 flex flex-wrap gap-2">
                                <span className="rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2.5 py-1 text-[11px] text-emerald-200">
                                    {beatLabel}
                                </span>
                                <span className="rounded-full border border-cyan-400/20 bg-cyan-400/10 px-2.5 py-1 text-[11px] text-cyan-200">
                                    {chapterLabel}
                                </span>
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
                                            <img
                                                src={getAssetUrl(previousFrameReference)}
                                                alt={copy.previousReferenceAlt}
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
                                                <img
                                                    src={getAssetUrl(item.url)}
                                                    alt={item.name}
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
