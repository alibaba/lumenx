"use client";

import { Fragment, useMemo, useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    Layout, Image as ImageIcon, Box, Type, Move,
    ZoomIn, ZoomOut, Layers, Settings, Play,
    ChevronRight, ChevronLeft, Trash2, Copy, Wand2, Users, FileText, RefreshCw, Loader2, X, Lock, Unlock,
    Plus, ArrowUp, ArrowDown, Zap, Upload, Film
} from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, API_URL, crudApi } from "@/lib/api";
import { appendAssetQueryParam, getAssetUrl, getAssetUrlWithTimestamp, extractErrorDetail } from "@/lib/utils";
import { getCameraTerm, messages, shotTerms } from "@/lib/i18n";
import { buildStoryboardCompositionData, getArtDirectionPromptPrefix } from "@/lib/storyboard-references";

import StoryboardFrameEditor from "./StoryboardFrameEditor";

const copy = messages.modules.storyboardComposer;
const commonActions = messages.common.actions;
const commonLabels = messages.common.labels;

type FrameViewMode = "sequence" | "beat";

interface FrameEntry {
    frame: any;
    index: number;
}

interface StoryBeatMeta {
    order?: number;
    title?: string;
    summary?: string;
    chapterOrder?: number;
    chapterTitle?: string;
    sceneName?: string;
    qualityFlags: string[];
}

interface StoryBeatGroup {
    key: string;
    title: string;
    summary: string;
    frameCount: number;
    order: number;
    isUnassigned: boolean;
    chapterOrder?: number;
    chapterTitle?: string;
    sceneName?: string;
    qualityFlags: string[];
    entries: FrameEntry[];
}

function formatStoryBeatLabel(order?: number | null, title?: string | null) {
    if (!title && order == null) return copy.unassignedBeat;
    if (order == null) return title || copy.unassignedBeat;
    if (title && /^\s*(第?\s*\d+\s*场|场次\s*\d+)/.test(title)) return title;
    return copy.storyBeatBadge(order, title || copy.unassignedBeat);
}

function formatChapterLabel(order?: number | null, title?: string | null) {
    if (order == null && !title) return copy.unchapteredLabel;
    if (order == null) return title || copy.unchapteredLabel;
    if (!title) return `第${order}章`;
    if (/^\s*第?\s*\d+\s*章/.test(title)) return title;
    return `第${order}章 · ${title}`;
}

const STORY_BEAT_QUALITY_LABELS: Record<string, string> = {
    title_only: copy.titleOnlyFlag,
    no_characters: copy.noCharactersFlag,
    no_scene: copy.noSceneFlag,
    over_segmented: copy.overSegmentedFlag,
};

function formatStoryBeatQualityFlag(flag: string) {
    return STORY_BEAT_QUALITY_LABELS[flag] || flag;
}

export default function StoryboardComposer() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const selectedFrameId = useProjectStore((state) => state.selectedFrameId);
    const setSelectedFrameId = useProjectStore((state) => state.setSelectedFrameId);
    const updateProject = useProjectStore((state) => state.updateProject);

    // Use global rendering state (persists across module switches)
    const renderingFrames = useProjectStore((state) => state.renderingFrames);
    const addRenderingFrame = useProjectStore((state) => state.addRenderingFrame);
    const removeRenderingFrame = useProjectStore((state) => state.removeRenderingFrame);

    // Use global storyboard analysis state (persists across tab switches)
    const isAnalyzing = useProjectStore((state) => state.isAnalyzingStoryboard);
    const setIsAnalyzing = useProjectStore((state) => state.setIsAnalyzingStoryboard);

    const [editingFrameId, setEditingFrameId] = useState<string | null>(null);
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
    const [insertIndex, setInsertIndex] = useState<number | null>(null);
    const [extractingFrameId, setExtractingFrameId] = useState<string | null>(null);
    const [showScriptOverlay, setShowScriptOverlay] = useState(false);
    const [frameViewMode, setFrameViewMode] = useState<FrameViewMode>("sequence");

    const fileInputRef = useRef<HTMLInputElement>(null);
    const [uploadTargetFrameId, setUploadTargetFrameId] = useState<string | null>(null);



    // NEW: Analyze script text to generate storyboard frames
    const handleAnalyzeToStoryboard = async () => {
        if (!currentProject) return;

        const text = currentProject.originalText;
        if (!text || !text.trim()) {
            alert(copy.analyzeScriptRequired);
            return;
        }

        if (currentProject.frames?.length > 0) {
            if (!confirm(copy.overwriteConfirm)) return;
        }

        setIsAnalyzing(true);
        try {
            const updatedProject = await api.analyzeToStoryboard(currentProject.id, text);
            const frameCount = updatedProject.frames?.length || 0;
            if (frameCount > 0) {
                updateProject(currentProject.id, updatedProject);
                alert(copy.analyzeSuccess(frameCount));
            } else {
                alert(copy.analyzeEmpty);
            }
        } catch (error: any) {
            console.error("Analyze to storyboard failed:", error);
            const detail = extractErrorDetail(error, "");
            if (detail.includes("JSON") || /格式/.test(detail)) {
                alert(copy.analyzeFormatFailed);
            } else {
                alert(copy.analyzeFailed(detail || copy.defaultAnalyzeFailureDetail));
            }
        } finally {
            setIsAnalyzing(false);
        }
    };

    const handleImageClick = (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setEditingFrameId(frameId);
    };

    const handleDeleteFrame = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject) return;
        if (!confirm(copy.deleteConfirm)) return;

        try {
            await crudApi.deleteFrame(currentProject.id, frameId);
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete frame:", error);
            alert(copy.failedToDeleteFrame);
        }
    };

    const handleCopyFrame = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject) return;

        try {
            await crudApi.copyFrame(currentProject.id, frameId);
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to copy frame:", error);
            alert(copy.failedToCopyFrame);
        }
    };

    const handleCreateFrame = async (data: any) => {
        if (!currentProject) return;

        try {
            await crudApi.createFrame(currentProject.id, {
                ...data,
                insert_at: insertIndex !== null ? insertIndex : undefined
            });
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            setIsCreateDialogOpen(false);
            setInsertIndex(null);
        } catch (error) {
            console.error("Failed to create frame:", error);
            alert(copy.failedToCreateFrame);
        }
    };

    const handleMoveFrame = async (index: number, direction: 'up' | 'down', e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject || !currentProject.frames) return;

        const newIndex = direction === 'up' ? index - 1 : index + 1;
        if (newIndex < 0 || newIndex >= currentProject.frames.length) return;

        // Create new order
        const newFrames = [...currentProject.frames];
        const [movedFrame] = newFrames.splice(index, 1);
        newFrames.splice(newIndex, 0, movedFrame);

        const newOrderIds = newFrames.map((f: any) => f.id);

        try {
            // Optimistic update
            updateProject(currentProject.id, { ...currentProject, frames: newFrames });

            await crudApi.reorderFrames(currentProject.id, newOrderIds);
            // No need to fetch again if optimistic update was correct, but good for safety
        } catch (error) {
            console.error("Failed to reorder frames:", error);
            alert(copy.failedToReorderFrame);
            // Revert on error would be ideal here by fetching project again
            const project = await api.getProject(currentProject.id);
            updateProject(currentProject.id, project);
        }
    };

    const handleExtractLastFrame = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject?.frames) return;

        const frameIndex = currentProject.frames.findIndex((f: any) => f.id === frameId);
        if (frameIndex <= 0) return;

        // Find the previous frame's selected video
        const prevFrame = currentProject.frames[frameIndex - 1];
        if (!prevFrame.selected_video_id) {
            alert(copy.previousFrameNoVideo);
            return;
        }

        const prevVideo = currentProject.video_tasks?.find(
            (t: any) => t.id === prevFrame.selected_video_id && t.status === "completed"
        );
        if (!prevVideo) {
            alert(copy.previousFrameVideoIncomplete);
            return;
        }

        setExtractingFrameId(frameId);
        try {
            const updatedProject = await api.extractLastFrame(currentProject.id, frameId, prevVideo.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error: any) {
            console.error("Failed to extract last frame:", error);
            alert(error?.response?.data?.detail || copy.failedToExtractLastFrame);
        } finally {
            setExtractingFrameId(null);
        }
    };

    const handleUploadFrameImage = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setUploadTargetFrameId(frameId);
        fileInputRef.current?.click();
    };

    const handleFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !uploadTargetFrameId || !currentProject) return;

        try {
            const updatedProject = await api.uploadFrameImage(currentProject.id, uploadTargetFrameId, file);
            updateProject(currentProject.id, updatedProject);
        } catch (error: any) {
            console.error("Failed to upload frame image:", error);
            alert(error?.message || copy.failedToUploadFrameImage);
        } finally {
            setUploadTargetFrameId(null);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const handleRenderFrame = async (frame: any, batchSize: number = 1, e?: React.MouseEvent) => {
        e?.stopPropagation();
        if (!currentProject) return;

        addRenderingFrame(frame.id);
        try {
            const compositionData = buildStoryboardCompositionData(currentProject, frame, {
                continuityLock: true,
                includeStyleReferences: false,
            });

            // Construct enhanced prompt using Art Direction style config.
            const artDirection = currentProject?.art_direction;
            const globalStylePrompt = getArtDirectionPromptPrefix(artDirection?.style_config);

            // Construct final prompt:
            // If image_prompt exists (polished or manually edited), it already contains action/dialogue,
            // so only prepend the style. Otherwise, build from action_description and dialogue.
            let finalPrompt = "";

            if (frame.image_prompt && frame.image_prompt.trim()) {
                // User has a custom/polished prompt - only add style prefix
                finalPrompt = globalStylePrompt
                    ? `${globalStylePrompt} . ${frame.image_prompt}`
                    : frame.image_prompt;
            } else {
                // No custom prompt - build from action_description and dialogue
                const parts = [
                    globalStylePrompt,
                    frame.action_description,
                    frame.dialogue ? `Dialogue context: "${frame.dialogue}"` : ""
                ].filter(Boolean);
                finalPrompt = parts.join(" . ");
            }

            await api.renderFrame(currentProject.id, frame.id, compositionData, finalPrompt, batchSize);

            // Fetch updated project to get new image URL and timestamp
            const updatedProject = await api.getProject(currentProject.id);
            useProjectStore.getState().updateProject(currentProject.id, updatedProject);

        } catch (error) {
            console.error("Render failed:", error);
            alert(copy.renderFailed);
        } finally {
            removeRenderingFrame(frame.id);
        }
    };

    const frameEntries = useMemo<FrameEntry[]>(
        () => (currentProject?.frames || []).map((frame: any, index: number) => ({ frame, index })),
        [currentProject?.frames],
    );

    const sceneLookup = useMemo(() => {
        const lookup = new Map<string, string>();
        currentProject?.scenes?.forEach((scene: any) => {
            if (scene?.id && scene?.name) {
                lookup.set(scene.id, scene.name);
            }
        });
        return lookup;
    }, [currentProject?.scenes]);

    const storyBeatLookup = useMemo(() => {
        const lookup = new Map<string, StoryBeatMeta>();

        currentProject?.story_analysis?.scene_beats?.forEach((beat: any) => {
            lookup.set(beat.id, {
                order: beat.order,
                title: beat.title,
                summary: beat.summary,
                chapterOrder: beat.chapter_order,
                chapterTitle: beat.chapter_title,
                sceneName: beat.scene_name,
                qualityFlags: beat.quality_flags || [],
            });
        });

        frameEntries.forEach(({ frame }) => {
            if (!frame.story_beat_id || lookup.has(frame.story_beat_id)) return;
            lookup.set(frame.story_beat_id, {
                order: frame.story_beat_order,
                title: frame.story_beat_title,
                summary: "",
                chapterOrder: frame.chapter_order,
                chapterTitle: frame.chapter_title,
                sceneName: sceneLookup.get(frame.scene_id),
                qualityFlags: [],
            });
        });

        return lookup;
    }, [currentProject?.story_analysis?.scene_beats, frameEntries, sceneLookup]);

    const beatFrameCounts = useMemo(() => {
        const counts = new Map<string, number>();
        frameEntries.forEach(({ frame }) => {
            const key = frame.story_beat_id || "__unassigned__";
            counts.set(key, (counts.get(key) || 0) + 1);
        });
        return counts;
    }, [frameEntries]);

    const storyBeatGroups = useMemo<StoryBeatGroup[]>(() => {
        const groups = new Map<string, StoryBeatGroup>();

        frameEntries.forEach((entry) => {
            const beatId = entry.frame.story_beat_id || "__unassigned__";
            const beatMeta = entry.frame.story_beat_id ? storyBeatLookup.get(entry.frame.story_beat_id) : null;
            if (!groups.has(beatId)) {
                groups.set(beatId, {
                    key: beatId,
                    title: formatStoryBeatLabel(entry.frame.story_beat_order ?? beatMeta?.order, entry.frame.story_beat_title || beatMeta?.title),
                    summary: beatMeta?.summary || "",
                    frameCount: beatFrameCounts.get(beatId) || 0,
                    order: entry.frame.story_beat_order ?? beatMeta?.order ?? Number.MAX_SAFE_INTEGER,
                    isUnassigned: !entry.frame.story_beat_id,
                    chapterOrder: entry.frame.chapter_order ?? beatMeta?.chapterOrder,
                    chapterTitle: entry.frame.chapter_title || beatMeta?.chapterTitle,
                    sceneName: beatMeta?.sceneName || sceneLookup.get(entry.frame.scene_id),
                    qualityFlags: beatMeta?.qualityFlags || [],
                    entries: [],
                });
            }
            groups.get(beatId)?.entries.push(entry);
        });

        return Array.from(groups.values()).sort((left, right) => {
            if (left.isUnassigned !== right.isUnassigned) return left.isUnassigned ? 1 : -1;
            if (left.order !== right.order) return left.order - right.order;
            return left.entries[0].index - right.entries[0].index;
        });
    }, [beatFrameCounts, frameEntries, sceneLookup, storyBeatLookup]);

    const renderFrameCard = ({ frame, index }: FrameEntry) => {
        const beatMeta = frame.story_beat_id ? storyBeatLookup.get(frame.story_beat_id) : null;
        const beatKey = frame.story_beat_id || "__unassigned__";
        const beatLabel = formatStoryBeatLabel(frame.story_beat_order ?? beatMeta?.order, frame.story_beat_title || beatMeta?.title);
        const beatFrameCount = beatFrameCounts.get(beatKey) || 0;
        const chapterOrder = frame.chapter_order ?? beatMeta?.chapterOrder;
        const chapterTitle = frame.chapter_title || beatMeta?.chapterTitle;
        const sceneName = beatMeta?.sceneName || sceneLookup.get(frame.scene_id);
        const qualityFlags = beatMeta?.qualityFlags || [];

        return (
            <Fragment key={frame.id}>
                <motion.div
                    layoutId={frame.id}
                    onClick={() => setSelectedFrameId(frame.id)}
                    className={`group relative flex gap-6 p-4 rounded-xl border transition-all cursor-pointer ${selectedFrameId === frame.id
                        ? "bg-white/5 border-primary ring-1 ring-primary"
                        : "bg-[#161616] border-white/5 hover:border-white/20"
                        }`}
                >
                    <div className="absolute -left-3 -top-3 w-8 h-8 rounded-full bg-[#222] border border-white/10 flex items-center justify-center text-xs font-bold text-gray-400 shadow-lg z-10">
                        {index + 1}
                    </div>

                    <div className="w-64 aspect-video bg-black/40 rounded-lg border border-white/5 overflow-hidden flex-shrink-0 relative">
                        {frame.rendered_image_url || frame.image_url ? (
                            <ImageWithRetry
                                key={frame.id + (frame.updated_at || 0)}
                                src={getAssetUrlWithTimestamp(frame.rendered_image_url || frame.image_url, frame.updated_at)}
                                alt={copy.frameAlt(index + 1)}
                                className="w-full h-full object-cover cursor-zoom-in"
                                onClick={(e: React.MouseEvent) => handleImageClick(frame.id, e)}
                            />
                        ) : (
                            <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 gap-2">
                                <ImageIcon size={24} className="opacity-20" />
                                <span className="text-[10px]">{copy.noImage}</span>
                            </div>
                        )}

                        <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2 pointer-events-none">
                            <button
                                onClick={async (e) => {
                                    e.stopPropagation();
                                    if (!currentProject) return;
                                    try {
                                        await api.toggleFrameLock(currentProject.id, frame.id);
                                        const updated = await api.getProject(currentProject.id);
                                        updateProject(currentProject.id, updated);
                                    } catch (error) {
                                        console.error("Toggle lock failed:", error);
                                    }
                                }}
                                className="p-2 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1 pointer-events-auto"
                                title={frame.locked ? copy.unlock : copy.lock}
                            >
                                {frame.locked ? <Unlock size={14} /> : <Lock size={14} />}
                            </button>

                            {!frame.locked && (
                                <div className="flex items-center gap-1 pointer-events-auto">
                                    {renderingFrames.has(frame.id) ? (
                                        <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-700 rounded-lg">
                                            <Loader2 size={14} className="animate-spin text-white" />
                                            <span className="text-xs text-white">{copy.generating}</span>
                                        </div>
                                    ) : (
                                        <>
                                            {[1, 2, 3, 4].map(size => (
                                                <button
                                                    key={size}
                                                    onClick={(e) => { e.stopPropagation(); handleRenderFrame(frame, size); }}
                                                    className="px-2 py-1.5 bg-primary/80 hover:bg-primary text-white rounded text-xs font-bold transition-colors"
                                                    title={copy.generateVariants(size)}
                                                >
                                                    <div className="flex items-center gap-1">
                                                        <Wand2 size={12} />
                                                        <span>×{size}</span>
                                                    </div>
                                                </button>
                                            ))}
                                        </>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="flex-1 flex flex-col gap-3">
                        <div className="flex items-start justify-between">
                            <div className="space-y-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-xs font-bold text-gray-500 uppercase tracking-wider">{copy.action}</span>
                                    {frame.camera_movement && (
                                        <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded border border-blue-500/30">
                                            {getCameraTerm(frame.camera_movement)?.label || frame.camera_movement}
                                        </span>
                                    )}
                                    <span
                                        data-testid={`frame-story-beat-${frame.id}`}
                                        className={`text-[10px] px-2 py-0.5 rounded-full border ${frame.story_beat_id
                                            ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
                                            : "border-amber-400/30 bg-amber-400/10 text-amber-200"
                                            }`}
                                    >
                                        {beatLabel}
                                    </span>
                                    <span className="text-[10px] px-2 py-0.5 rounded-full border border-cyan-400/20 bg-cyan-400/10 text-cyan-200">
                                        {formatChapterLabel(chapterOrder, chapterTitle)}
                                    </span>
                                    <span className="text-[10px] px-2 py-0.5 rounded-full border border-white/10 bg-white/5 text-gray-300">
                                        {copy.storyBeatFrameCount(beatFrameCount)}
                                    </span>
                                </div>
                                {(chapterOrder != null || chapterTitle || sceneName || qualityFlags.length > 0) && (
                                    <div
                                        data-testid={`frame-story-beat-meta-${frame.id}`}
                                        className="flex flex-wrap items-center gap-2 text-[10px]"
                                    >
                                        {(chapterOrder != null || chapterTitle) && (
                                            <span className="rounded-full border border-cyan-400/20 bg-cyan-400/10 px-2 py-0.5 text-cyan-200">
                                                {copy.storyBeatChapter(formatChapterLabel(chapterOrder, chapterTitle))}
                                            </span>
                                        )}
                                        {sceneName && (
                                            <span className="rounded-full border border-fuchsia-400/20 bg-fuchsia-400/10 px-2 py-0.5 text-fuchsia-200">
                                                {copy.storyBeatScene(sceneName)}
                                            </span>
                                        )}
                                        {qualityFlags.map((flag) => (
                                            <span
                                                key={`${frame.id}-${flag}`}
                                                className="rounded-full border border-amber-400/20 bg-amber-400/10 px-2 py-0.5 text-amber-200"
                                            >
                                                {formatStoryBeatQualityFlag(flag)}
                                            </span>
                                        ))}
                                    </div>
                                )}
                                <p className="text-sm text-gray-200 leading-relaxed line-clamp-3">
                                    {frame.action_description}
                                </p>
                            </div>
                        </div>

                        {frame.dialogue && (
                            <div className="mt-auto pt-3 border-t border-white/5">
                                <span className="text-xs font-bold text-gray-500 uppercase tracking-wider block mb-1">{commonLabels.dialogue}</span>
                                <p className="text-sm text-gray-400 italic">"{frame.dialogue}"</p>
                            </div>
                        )}

                        <div className="flex justify-end gap-2 mt-2 pt-2 border-t border-white/5">
                            <div className="flex items-center gap-1 mr-auto">
                                <button
                                    onClick={(e) => handleMoveFrame(index, "up", e)}
                                    disabled={index === 0}
                                    className="btn-tip p-2 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                    data-tip={copy.moveUp}
                                >
                                    <ArrowUp size={14} />
                                </button>
                                <button
                                    onClick={(e) => handleMoveFrame(index, "down", e)}
                                    disabled={index === (currentProject?.frames?.length || 0) - 1}
                                    className="btn-tip p-2 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                    data-tip={copy.moveDown}
                                >
                                    <ArrowDown size={14} />
                                </button>
                            </div>

                            <button
                                onClick={(e) => handleCopyFrame(frame.id, e)}
                                className="btn-tip p-2 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors"
                                data-tip={copy.duplicate}
                            >
                                <Copy size={14} />
                            </button>
                            <button
                                onClick={(e) => handleUploadFrameImage(frame.id, e)}
                                className="btn-tip p-2 hover:bg-blue-500/20 text-gray-400 hover:text-blue-400 rounded-lg transition-colors"
                                data-tip={copy.uploadImage}
                            >
                                <Upload size={14} />
                            </button>
                            {index > 0 && (() => {
                                const prevFrame = currentProject?.frames?.[index - 1];
                                const prevVideoCompleted = prevFrame?.selected_video_id && currentProject?.video_tasks?.find(
                                    (t: any) => t.id === prevFrame.selected_video_id && t.status === "completed"
                                );
                                return prevVideoCompleted ? (
                                    <button
                                        onClick={(e) => handleExtractLastFrame(frame.id, e)}
                                        disabled={extractingFrameId === frame.id}
                                        className="btn-tip p-2 hover:bg-purple-500/20 text-gray-400 hover:text-purple-400 rounded-lg transition-colors disabled:opacity-50"
                                        data-tip={copy.usePrevEndFrame}
                                    >
                                        {extractingFrameId === frame.id ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
                                    </button>
                                ) : null;
                            })()}
                            <button
                                onClick={(e) => handleDeleteFrame(frame.id, e)}
                                className="btn-tip p-2 hover:bg-red-500/20 text-gray-400 hover:text-red-400 rounded-lg transition-colors"
                                data-tip={copy.delete}
                            >
                                <Trash2 size={14} />
                            </button>
                        </div>
                    </div>
                </motion.div>

                <div className="flex justify-center opacity-0 hover:opacity-100 transition-opacity -my-3 z-10 relative">
                    <button
                        onClick={() => { setInsertIndex(index + 1); setIsCreateDialogOpen(true); }}
                        className="p-1 bg-[#222] border border-white/20 rounded-full text-gray-400 hover:text-white hover:border-primary hover:bg-primary/20 transition-all transform hover:scale-110"
                        title={copy.insertFrameHere}
                    >
                        <Plus size={16} />
                    </button>
                </div>
            </Fragment>
        );
    };

    return (
        <div className="flex flex-col h-full text-white overflow-hidden">
            {/* Top Toolbar */}
            <div className="flex-shrink-0 p-4 border-b border-white/10 flex items-center justify-between bg-black/20">
                <h3 className="font-bold text-sm flex items-center gap-2">
                    <Layout size={16} className="text-primary" /> {copy.title}
                </h3>
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => setShowScriptOverlay(true)}
                        className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white px-2.5 py-1.5 rounded-lg hover:bg-white/5 transition-colors"
                        title={copy.viewRawScript}
                    >
                        <FileText size={14} />
                        {copy.viewScript}
                    </button>
                    <div className="w-px h-4 bg-white/10" />
                    <button
                        onClick={handleAnalyzeToStoryboard}
                        disabled={isAnalyzing}
                        className="flex items-center gap-1.5 text-xs bg-primary/80 hover:bg-primary px-3 py-1.5 rounded-lg text-white transition-colors disabled:opacity-50"
                        title={copy.analyzeFromScript}
                    >
                        {isAnalyzing ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                        {isAnalyzing ? copy.generating : copy.generateStoryboard}
                    </button>
                    <div className="w-px h-4 bg-white/10" />
                    <div className="flex items-center gap-1 rounded-lg border border-white/10 bg-white/5 p-1">
                        <button
                            type="button"
                            onClick={() => setFrameViewMode("sequence")}
                            className={`rounded-md px-2.5 py-1 text-xs transition-colors ${frameViewMode === "sequence" ? "bg-white/15 text-white" : "text-gray-400 hover:text-white"}`}
                        >
                            {copy.sequenceView}
                        </button>
                        <button
                            type="button"
                            onClick={() => setFrameViewMode("beat")}
                            className={`rounded-md px-2.5 py-1 text-xs transition-colors ${frameViewMode === "beat" ? "bg-white/15 text-white" : "text-gray-400 hover:text-white"}`}
                        >
                            {copy.beatView}
                        </button>
                    </div>
                    <div className="w-px h-4 bg-white/10" />
                    <span className="text-xs text-gray-500 font-mono">
                        {copy.frameCount(currentProject?.frames?.length || 0)}
                    </span>
                </div>
            </div>

            {/* Frame List — full width */}
            <div className="flex-1 overflow-y-auto p-8">
                <div className="max-w-4xl mx-auto space-y-6">
                    <div className="flex justify-center">
                        <button
                            onClick={() => { setInsertIndex(0); setIsCreateDialogOpen(true); }}
                            className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors border border-dashed border-white/10 hover:border-white/30"
                        >
                            <Plus size={16} />
                            <span className="text-sm font-medium">{copy.insertFrameAtStart}</span>
                        </button>
                    </div>

                    {frameViewMode === "beat" ? (
                        storyBeatGroups.map((group) => (
                            <section key={group.key} data-testid={`story-beat-group-${group.key}`} className="space-y-4">
                                <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                                    <div className="flex flex-wrap items-center justify-between gap-3">
                                        <div>
                                            <div className="text-xs uppercase tracking-[0.18em] text-gray-500">{copy.beatGroupLabel}</div>
                                            <h4 className="mt-1 text-sm font-semibold text-white">{group.title}</h4>
                                        </div>
                                        <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-gray-300">
                                            {copy.storyBeatFrameCount(group.frameCount)}
                                        </span>
                                    </div>
                                    {(group.chapterOrder != null || group.chapterTitle || group.sceneName || group.qualityFlags.length > 0) && (
                                        <div
                                            data-testid={`story-beat-group-meta-${group.key}`}
                                            className="mt-3 flex flex-wrap items-center gap-2 text-[11px]"
                                        >
                                            {(group.chapterOrder != null || group.chapterTitle) && (
                                                <span className="rounded-full border border-cyan-400/20 bg-cyan-400/10 px-2.5 py-1 text-cyan-200">
                                                    {copy.storyBeatChapter(formatChapterLabel(group.chapterOrder, group.chapterTitle))}
                                                </span>
                                            )}
                                            {group.sceneName && (
                                                <span className="rounded-full border border-fuchsia-400/20 bg-fuchsia-400/10 px-2.5 py-1 text-fuchsia-200">
                                                    {copy.storyBeatScene(group.sceneName)}
                                                </span>
                                            )}
                                            {group.qualityFlags.map((flag) => (
                                                <span
                                                    key={`${group.key}-${flag}`}
                                                    className="rounded-full border border-amber-400/20 bg-amber-400/10 px-2.5 py-1 text-amber-200"
                                                >
                                                    {formatStoryBeatQualityFlag(flag)}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                    {group.summary ? (
                                        <p className="mt-3 text-sm leading-6 text-gray-400">{group.summary}</p>
                                    ) : (
                                        <p className="mt-3 text-sm leading-6 text-gray-500">{copy.unassignedBeatHint}</p>
                                    )}
                                </div>
                                <div className="space-y-6">
                                    {group.entries.map(renderFrameCard)}
                                </div>
                            </section>
                        ))
                    ) : (
                        frameEntries.map(renderFrameCard)
                    )}
                </div>
            </div>

            {/* Script Overlay */}
            <AnimatePresence>
                {showScriptOverlay && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2 }}
                        className="absolute inset-0 z-40 flex items-center justify-center bg-black/70 backdrop-blur-sm"
                        onClick={() => setShowScriptOverlay(false)}
                    >
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95, y: 16 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: 16 }}
                            transition={{ duration: 0.25, ease: [0.25, 1, 0.5, 1] }}
                            className="w-full max-w-2xl max-h-[80vh] bg-[#1a1a1a] border border-white/10 rounded-2xl shadow-2xl overflow-hidden flex flex-col"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 bg-black/20">
                                <div className="flex items-center gap-3">
                                    <FileText size={18} className="text-primary" />
                                    <h3 className="text-sm font-bold text-white">{copy.rawScriptTitle}</h3>
                                </div>
                                <button
                                    onClick={() => setShowScriptOverlay(false)}
                                    className="p-1.5 hover:bg-white/10 rounded-lg transition-colors"
                                >
                                    <X size={16} className="text-gray-400" />
                                </button>
                            </div>
                            <div className="flex-1 overflow-y-auto p-6">
                                <pre className="text-sm text-gray-300 whitespace-pre-wrap font-sans leading-relaxed">
                                    {currentProject?.originalText || copy.noScriptContent}
                                </pre>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Storyboard Frame Editor Modal */}
            <AnimatePresence>
                {editingFrameId && currentProject?.frames?.find((f: any) => f.id === editingFrameId) && (
                    <StoryboardFrameEditor
                        frame={currentProject.frames.find((f: any) => f.id === editingFrameId)}
                        onClose={() => setEditingFrameId(null)}
                    />
                )}
            </AnimatePresence>

            {/* Create Frame Dialog */}
            <AnimatePresence>
                {isCreateDialogOpen && (
                    <CreateFrameDialog
                        onClose={() => { setIsCreateDialogOpen(false); setInsertIndex(null); }}
                        onCreate={handleCreateFrame}
                        scenes={currentProject?.scenes || []}
                    />
                )}
            </AnimatePresence>

            {/* Hidden file input for frame image upload */}
            <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleFileSelected}
            />
        </div >
    );
}

function CreateFrameDialog({ onClose, onCreate, scenes }: { onClose: () => void; onCreate: (data: any) => void; scenes: any[] }) {
    const [action, setAction] = useState("");
    const [dialogue, setDialogue] = useState("");
    const [sceneId, setSceneId] = useState(scenes[0]?.id || "");
    const [isSubmitting, setIsSubmitting] = useState(false);

        const handleSubmit = async () => {
        if (!action.trim()) {
            alert(copy.actionRequired);
            return;
        }
        if (!sceneId && scenes.length > 0) {
            alert(copy.sceneRequired);
            return;
        }

        setIsSubmitting(true);
        try {
            await onCreate({
                action_description: action.trim(),
                dialogue: dialogue.trim(),
                scene_id: sceneId,
                camera_angle: shotTerms.mediumShot.value
            });
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl"
            >
                <div className="p-6 border-b border-white/10 flex justify-between items-center bg-black/20">
                    <div className="flex items-center gap-3">
                        <Plus className="text-primary" size={20} />
                        <h2 className="text-lg font-bold text-white">{copy.createDialog.title}</h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg transition-colors">
                        <X size={20} className="text-gray-400" />
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">{copy.createDialog.scene}</label>
                        <select
                            value={sceneId}
                            onChange={(e) => setSceneId(e.target.value)}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white focus:border-primary/50 focus:outline-none appearance-none"
                        >
                            <option value="" disabled>{copy.createDialog.selectScene}</option>
                            {scenes.map((s: any) => (
                                <option key={s.id} value={s.id}>{s.name}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">{copy.createDialog.actionDescription}</label>
                        <textarea
                            value={action}
                            onChange={(e) => setAction(e.target.value)}
                            placeholder={copy.createDialog.actionPlaceholder}
                            rows={3}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">{copy.createDialog.dialogueOptional}</label>
                        <textarea
                            value={dialogue}
                            onChange={(e) => setDialogue(e.target.value)}
                            placeholder={copy.createDialog.dialoguePlaceholder}
                            rows={2}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                </div>

                <div className="p-6 border-t border-white/10 flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-6 py-2 bg-white/5 hover:bg-white/10 text-white rounded-lg transition-colors"
                    >
                        {commonActions.cancel}
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={isSubmitting || !action.trim()}
                        className="px-6 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {isSubmitting && <RefreshCw size={16} className="animate-spin" />}
                        {copy.createDialog.create}
                    </button>
                </div>
            </motion.div>
        </div>
    );
}

function ImageWithRetry({ src, alt, className, onClick }: { src: string, alt: string, className?: string, onClick?: (e: React.MouseEvent) => void }) {
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(false);
    const [retryCount, setRetryCount] = useState(0);
    const imgRef = useRef<HTMLImageElement>(null);

    // Reset state when src changes
    useEffect(() => {
        setIsLoading(true);
        setError(false);
        setRetryCount(0);
    }, [src]);

    useEffect(() => {
        if (imgRef.current && imgRef.current.complete) {
            if (imgRef.current.naturalWidth > 0) {
                setIsLoading(false);
            }
        }
    }, [src]);

    useEffect(() => {
        if (error && retryCount < 10) {
            const timer = setTimeout(() => {
                setRetryCount(prev => prev + 1);
                setError(false);
            }, 1000 * (retryCount + 1)); // Exponential backoff
            return () => clearTimeout(timer);
        }
    }, [error, retryCount]);

    const displaySrc = retryCount > 0 ? appendAssetQueryParam(src, "retry", retryCount) : src;

    return (
        <div className={`relative ${className}`}>
            {isLoading && (
                <div className="absolute inset-0 flex items-center justify-center bg-white/5 backdrop-blur-sm z-10">
                    <RefreshCw className="animate-spin text-white/50" size={24} />
                </div>
            )}
            <img
                key={`${displaySrc}-${retryCount}`}
                ref={imgRef}
                src={displaySrc}
                alt={alt}
                className={`${className} ${isLoading ? 'opacity-50' : 'opacity-100'} transition-opacity duration-300`}
                onLoad={() => {
                    setError(false);
                    setIsLoading(false);
                }}
                onError={() => {
                    setError(true);
                    setIsLoading(true); // Keep showing loader while retrying
                }}
                onClick={onClick}
            />
            {error && retryCount >= 10 && (
                <div className="absolute inset-0 flex flex-col items-center justify-center bg-red-500/10 backdrop-blur-sm z-20 p-2 text-center">
                    <span className="text-xs text-red-400 font-bold">{copy.imageLoadFailed}</span>
                </div>
            )}
        </div>
    );
}
