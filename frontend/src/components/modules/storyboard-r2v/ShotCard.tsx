"use client";

import { useRef, useCallback } from "react";
import { motion } from "framer-motion";
import {
    Play,
    Loader2,
    Trash2,
    ChevronUp,
    ChevronDown,
    Copy,
    Sparkles,
    Video,
    ImageIcon,
    AtSign,
} from "lucide-react";
import { useTranslations } from "next-intl";
import {
    insertStoryboardR2VTag,
    type StoryboardR2VAssetLike,
    type StoryboardR2VCharacterLike,
    type StoryboardR2VReferenceIssue,
    type StoryboardR2VReferenceValidation,
} from "@/lib/storyboardR2VAssets";
import AssetChipBar from "./AssetChipBar";
import { PendingTaskAffordance } from "@/components/shared/PendingTaskAffordance";

export interface ShotNode {
    id: string;
    prompt: string;
    tabMode: "t2i_i2v" | "direct_r2v";

    // T2I stage (only for t2i_i2v mode)
    t2iImageUrl?: string;
    t2iTaskId?: string;
    t2iStatus?: "pending" | "processing" | "completed" | "failed";

    // Video stage (shared)
    videoUrl?: string;
    videoTaskId?: string;
    videoStatus?: "pending" | "processing" | "completed" | "failed";
    imageUrl?: string;
}

interface ShotCardProps {
    shot: ShotNode;
    index: number;
    totalShots: number;
    characters: StoryboardR2VCharacterLike[];
    scenes: StoryboardR2VAssetLike[];
    props: StoryboardR2VAssetLike[];
    referenceValidation?: StoryboardR2VReferenceValidation;
    onUpdatePrompt: (prompt: string) => void;
    onGenerateT2I: () => void;
    onGenerateVideo: () => void;
    onDelete: () => void;
    onMoveUp: () => void;
    onMoveDown: () => void;
    onDuplicate: () => void;
    onSetTabMode: (mode: "t2i_i2v" | "direct_r2v") => void;
    onOpenDrawer: () => void;
    onRegisterTextarea?: (element: HTMLTextAreaElement | null) => void;
    /** Optional: when present, shown as a Cancel CTA inside the
     *  pending-state affordance after the soft-stuck threshold. Caller
     *  is expected to flip the local task status / refresh. */
    onCancelVideo?: () => Promise<void> | void;
}

export default function ShotCard({
    shot,
    index,
    totalShots,
    characters,
    scenes,
    props,
    referenceValidation,
    onUpdatePrompt,
    onGenerateT2I,
    onGenerateVideo,
    onDelete,
    onMoveUp,
    onMoveDown,
    onDuplicate,
    onSetTabMode,
    onOpenDrawer,
    onRegisterTextarea,
    onCancelVideo,
}: ShotCardProps) {
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const cardRef = useRef<HTMLDivElement>(null);
    const t = useTranslations("storyboardR2V");

    const statusColor: Record<string, string> = {
        pending: "text-amber-400",
        processing: "text-sky-400",
        completed: "text-emerald-400",
        failed: "text-rose-400",
    };

    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        const el = cardRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        el.style.setProperty("--spotlight-x", `${e.clientX - rect.left}px`);
        el.style.setProperty("--spotlight-y", `${e.clientY - rect.top}px`);
    }, []);

    const setTextareaElement = useCallback(
        (element: HTMLTextAreaElement | null) => {
            textareaRef.current = element;
            onRegisterTextarea?.(element);
        },
        [onRegisterTextarea]
    );

    const renderReferenceIssue = (issue: StoryboardR2VReferenceIssue) => {
        if (issue.type === "missing_assets") {
            return t("missingAssetRefs", { refs: issue.refs.join(", ") });
        }
        if (issue.type === "missing_image_refs") {
            return t("missingImageRefs");
        }
        if (issue.type === "missing_video_refs") {
            return t("missingVideoRefs");
        }
        if (issue.type === "too_many_image_refs") {
            return t("tooManyImageRefs", { count: issue.count, max: issue.max });
        }
        return t("tooManyVideoRefs", { count: issue.count, max: issue.max });
    };

    const renderPreview = () => {
        if (shot.tabMode === "t2i_i2v") {
            if (shot.videoUrl) {
                return (
                    <div className="w-full aspect-video relative group/preview">
                        <video
                            src={shot.videoUrl}
                            className="w-full h-full object-cover"
                            muted
                            loop
                            onMouseEnter={(e) => (e.target as HTMLVideoElement).play()}
                            onMouseLeave={(e) => {
                                (e.target as HTMLVideoElement).pause();
                                (e.target as HTMLVideoElement).currentTime = 0;
                            }}
                        />
                        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/preview:opacity-100 transition-opacity duration-300">
                            <div className="w-10 h-10 rounded-full bg-black/50 backdrop-blur-sm flex items-center justify-center">
                                <Play size={18} className="text-white ml-0.5" />
                            </div>
                        </div>
                    </div>
                );
            }
            if (shot.videoStatus === "processing" || shot.videoStatus === "pending") {
                return (
                    <div className="w-full aspect-video flex items-center justify-center">
                        <PendingTaskAffordance
                            statusLabel={shot.videoStatus === "pending" ? t("queued") : t("generatingVideo")}
                            taskId={shot.videoTaskId}
                            onCancel={onCancelVideo}
                        />
                    </div>
                );
            }
            if (shot.videoStatus === "failed") {
                return (
                    <div className="w-full aspect-video flex flex-col items-center justify-center gap-2">
                        <span className="text-[11px] text-rose-400 font-medium">{t("generationFailed")}</span>
                        <button
                            onClick={onGenerateVideo}
                            className="text-[11px] text-primary hover:text-primary/80 transition-colors font-medium"
                        >
                            {t("retry")}
                        </button>
                    </div>
                );
            }
            if (shot.t2iImageUrl) {
                return (
                    <div className="w-full aspect-video relative group/preview">
                        <img src={shot.t2iImageUrl} alt="Generated frame" className="w-full h-full object-cover" />
                        <div className="absolute bottom-2 left-2 text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/90 text-white font-medium backdrop-blur-sm">
                            {t("t2iCompleted")}
                        </div>
                    </div>
                );
            }
            if (shot.t2iStatus === "processing" || shot.t2iStatus === "pending") {
                return (
                    <div className="w-full aspect-video flex items-center justify-center">
                        <PendingTaskAffordance
                            statusLabel={shot.t2iStatus === "pending" ? t("queued") : t("t2iGenerating")}
                            taskId={shot.t2iTaskId}
                        />
                    </div>
                );
            }
            if (shot.t2iStatus === "failed") {
                return (
                    <div className="w-full aspect-video flex flex-col items-center justify-center gap-2">
                        <span className="text-[11px] text-rose-400 font-medium">{t("generationFailed")}</span>
                        <button
                            onClick={onGenerateT2I}
                            className="text-[11px] text-primary hover:text-primary/80 transition-colors font-medium"
                        >
                            {t("retry")}
                        </button>
                    </div>
                );
            }
            return (
                <div className="w-full aspect-video flex flex-col items-center justify-center gap-2 text-text-secondary/60">
                    <div className="w-10 h-10 rounded-xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center">
                        <ImageIcon size={18} strokeWidth={1.5} />
                    </div>
                    <span className="text-[11px] font-medium">{t("generateImage")}</span>
                </div>
            );
        }

        // Direct R2V mode
        if (shot.videoUrl) {
            return (
                <div className="w-full aspect-video relative group/preview">
                    <video
                        src={shot.videoUrl}
                        className="w-full h-full object-cover"
                        muted
                        loop
                        onMouseEnter={(e) => (e.target as HTMLVideoElement).play()}
                        onMouseLeave={(e) => {
                            (e.target as HTMLVideoElement).pause();
                            (e.target as HTMLVideoElement).currentTime = 0;
                        }}
                    />
                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/preview:opacity-100 transition-opacity duration-300">
                        <div className="w-10 h-10 rounded-full bg-black/50 backdrop-blur-sm flex items-center justify-center">
                            <Play size={18} className="text-white ml-0.5" />
                        </div>
                    </div>
                </div>
            );
        }
        if (shot.videoStatus === "processing" || shot.videoStatus === "pending") {
            return (
                <div className="w-full aspect-video flex items-center justify-center">
                    <PendingTaskAffordance
                        statusLabel={shot.videoStatus === "pending" ? t("queued") : t("generatingVideo")}
                        taskId={shot.videoTaskId}
                        onCancel={onCancelVideo}
                    />
                </div>
            );
        }
        if (shot.videoStatus === "failed") {
            return (
                <div className="w-full aspect-video flex flex-col items-center justify-center gap-2">
                    <span className="text-[11px] text-rose-400 font-medium">{t("generationFailed")}</span>
                    <button
                        onClick={onGenerateVideo}
                        className="text-[11px] text-primary hover:text-primary/80 transition-colors font-medium"
                    >
                        {t("retry")}
                    </button>
                </div>
            );
        }
        return (
            <div className="w-full aspect-video flex flex-col items-center justify-center gap-2 text-text-secondary/60">
                <div className="w-10 h-10 rounded-xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center">
                    <Video size={18} strokeWidth={1.5} />
                </div>
                <span className="text-[11px] font-medium">{t("noVideoYet")}</span>
            </div>
        );
    };

    const renderGenerateButton = () => {
        const isProcessing =
            shot.t2iStatus === "processing" ||
            shot.t2iStatus === "pending" ||
            shot.videoStatus === "processing" ||
            shot.videoStatus === "pending";
        const hasReferenceBlocker =
            shot.tabMode === "direct_r2v" && referenceValidation ? !referenceValidation.canGenerate : false;

        const baseButtonClasses =
            "relative flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg overflow-hidden transition-all duration-200 active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100";

        if (shot.tabMode === "t2i_i2v") {
            if (shot.t2iImageUrl && !shot.videoUrl && !shot.videoTaskId) {
                return (
                    <motion.button
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.98 }}
                        onClick={onGenerateVideo}
                        disabled={isProcessing}
                        className={`${baseButtonClasses} bg-primary/90 hover:bg-primary text-white shadow-[0_0_20px_rgba(100,108,255,0.15)] hover:shadow-[0_0_28px_rgba(100,108,255,0.25)]`}
                    >
                        <Sparkles size={13} strokeWidth={2} />
                        {t("generateVideo")}
                    </motion.button>
                );
            }
            return (
                <motion.button
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                    onClick={onGenerateT2I}
                    disabled={!shot.prompt.trim() || isProcessing}
                    className={`${baseButtonClasses} bg-primary/90 hover:bg-primary text-white shadow-[0_0_20px_rgba(100,108,255,0.15)] hover:shadow-[0_0_28px_rgba(100,108,255,0.25)]`}
                >
                    {shot.t2iStatus === "processing" || shot.t2iStatus === "pending" ? (
                        <Loader2 size={13} className="animate-spin" strokeWidth={2} />
                    ) : (
                        <ImageIcon size={13} strokeWidth={2} />
                    )}
                    {t("generateImage")}
                </motion.button>
            );
        }

        return (
            <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                onClick={onGenerateVideo}
                disabled={!shot.prompt.trim() || isProcessing || hasReferenceBlocker}
                className={`${baseButtonClasses} bg-primary/90 hover:bg-primary text-white shadow-[0_0_20px_rgba(100,108,255,0.15)] hover:shadow-[0_0_28px_rgba(100,108,255,0.25)]`}
            >
                {shot.videoStatus === "processing" ? (
                    <Loader2 size={13} className="animate-spin" strokeWidth={2} />
                ) : (
                    <Sparkles size={13} strokeWidth={2} />
                )}
                {t("generateVideo")}
            </motion.button>
        );
    };

    const handleInsertAssetFromChip = (type: string, name: string) => {
        const tag = `[${type}:${name}]`;
        const textarea = textareaRef.current;
        if (textarea) {
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const currentPrompt = shot.prompt;
            const insertion = insertStoryboardR2VTag(currentPrompt, tag, start, end);
            onUpdatePrompt(insertion.prompt);
            setTimeout(() => {
                textarea.selectionStart = textarea.selectionEnd = insertion.cursor;
                textarea.focus();
            }, 0);
        } else {
            onUpdatePrompt(shot.prompt + " " + tag);
        }
    };

    const isActiveT2I = shot.tabMode === "t2i_i2v";

    return (
        <div
            ref={cardRef}
            onMouseMove={handleMouseMove}
            className="relative group"
        >
            {/* Spotlight border glow */}
            <div
                className="absolute -inset-[1px] rounded-xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none z-0"
                style={{
                    background:
                        "radial-gradient(600px circle at var(--spotlight-x, 50%) var(--spotlight-y, 50%), rgba(255,255,255,0.07), transparent 40%)",
                }}
            />

            {/* Liquid Glass card body */}
            <div className="relative backdrop-blur-xl bg-white/[0.02] border border-white/[0.06] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] rounded-xl overflow-hidden z-10">
                {/* Header row: Tab switcher + Shot number */}
                <div className="flex items-center justify-between px-3 pt-3 pb-2">
                    {/* Pill Tab Switcher */}
                    <div className="relative inline-flex items-center p-[3px] bg-black/40 rounded-lg backdrop-blur-sm">
                        <motion.div
                            className="absolute top-[3px] bottom-[3px] rounded-md bg-white/[0.08] shadow-[inset_0_1px_0_rgba(255,255,255,0.1)]"
                            initial={false}
                            animate={{
                                left: isActiveT2I ? 3 : "calc(50% + 1.5px)",
                                width: "calc(50% - 3px)",
                            }}
                            transition={{ type: "spring", stiffness: 350, damping: 32 }}
                        />
                        <button
                            onClick={() => onSetTabMode("t2i_i2v")}
                            className={`relative z-10 flex items-center gap-1.5 px-3 py-1 text-[11px] font-semibold rounded-md transition-colors duration-200 ${
                                isActiveT2I ? "text-foreground" : "text-text-secondary hover:text-text-secondary/80"
                            }`}
                        >
                            <ImageIcon size={12} strokeWidth={1.5} />
                            {t("tabT2iI2v")}
                        </button>
                        <button
                            onClick={() => onSetTabMode("direct_r2v")}
                            className={`relative z-10 flex items-center gap-1.5 px-3 py-1 text-[11px] font-semibold rounded-md transition-colors duration-200 ${
                                !isActiveT2I ? "text-foreground" : "text-text-secondary hover:text-text-secondary/80"
                            }`}
                        >
                            <Video size={12} strokeWidth={1.5} />
                            {t("tabDirectR2v")}
                        </button>
                    </div>

                    {/* Shot number badge */}
                    <div className="flex items-center gap-2">
                        <div className="text-[10px] font-mono text-text-muted tabular-nums">
                            #{String(index + 1).padStart(2, "0")}
                        </div>
                        <div className="w-5 h-5 rounded-full bg-white/[0.06] border border-white/[0.08] flex items-center justify-center">
                            <span className="text-[9px] font-bold text-foreground">{index + 1}</span>
                        </div>
                    </div>
                </div>

                {/* Main content: Preview + Editor */}
                <div className="flex">
                    {/* Left: Preview */}
                    <div className="w-44 shrink-0 bg-black/20 flex flex-col items-center justify-center relative border-r border-white/[0.04]">
                        {renderPreview()}
                    </div>

                    {/* Right: Prompt + Controls */}
                    <div className="flex-1 p-3 flex flex-col gap-2">
                        {/* Prompt Editor */}
                        <textarea
                            ref={setTextareaElement}
                            value={shot.prompt}
                            onChange={(e) => onUpdatePrompt(e.target.value)}
                            placeholder={t("promptPlaceholder")}
                            className="w-full text-sm resize-none leading-relaxed bg-transparent border border-white/[0.06] rounded-lg px-3 py-2.5 text-foreground placeholder:text-text-muted focus:outline-none focus:border-primary/30 focus:bg-white/[0.02] transition-all duration-200"
                            rows={3}
                        />

                        {/* Asset Chip Bar */}
                        <AssetChipBar
                            characters={characters}
                            scenes={scenes}
                            props={props}
                            onInsertAsset={handleInsertAssetFromChip}
                        />

                        {shot.tabMode === "direct_r2v" && referenceValidation && (
                            <div className="flex flex-wrap items-center gap-2 text-[11px]">
                                <span
                                    className={`px-2 py-1 rounded-md border ${
                                        referenceValidation.canGenerate
                                            ? "bg-emerald-500/10 border-emerald-400/20 text-emerald-300"
                                            : "bg-amber-500/10 border-amber-400/20 text-amber-300"
                                    }`}
                                >
                                    {referenceValidation.mode === "image"
                                        ? t("imageRefCount", { count: referenceValidation.requiredUrls.length })
                                        : t("videoRefCount", { count: referenceValidation.requiredUrls.length })}
                                </span>
                                {referenceValidation.issues.map((issue) => (
                                    <span
                                        key={issue.type === "missing_assets" ? `${issue.type}:${issue.refs.join("|")}` : issue.type}
                                        className="text-amber-300/90"
                                    >
                                        {renderReferenceIssue(issue)}
                                    </span>
                                ))}
                            </div>
                        )}

                        {/* Action Bar */}
                        <div className="flex items-center justify-between mt-0.5">
                            <div className="flex items-center gap-0.5">
                                <motion.button
                                    whileHover={{ scale: 1.08 }}
                                    whileTap={{ scale: 0.92 }}
                                    onClick={onOpenDrawer}
                                    className="p-1.5 rounded-lg hover:bg-white/[0.06] text-text-secondary hover:text-foreground transition-colors"
                                    title={t("browseAssets")}
                                >
                                    <AtSign size={14} strokeWidth={2} />
                                </motion.button>
                                <div className="w-px h-3.5 bg-white/[0.06] mx-0.5" />
                                <motion.button
                                    whileHover={{ scale: 1.08 }}
                                    whileTap={{ scale: 0.92 }}
                                    onClick={onMoveUp}
                                    disabled={index === 0}
                                    className="p-1.5 rounded-lg hover:bg-white/[0.06] text-text-secondary hover:text-foreground transition-colors disabled:opacity-20 disabled:hover:bg-transparent"
                                >
                                    <ChevronUp size={14} strokeWidth={1.5} />
                                </motion.button>
                                <motion.button
                                    whileHover={{ scale: 1.08 }}
                                    whileTap={{ scale: 0.92 }}
                                    onClick={onMoveDown}
                                    disabled={index === totalShots - 1}
                                    className="p-1.5 rounded-lg hover:bg-white/[0.06] text-text-secondary hover:text-foreground transition-colors disabled:opacity-20 disabled:hover:bg-transparent"
                                >
                                    <ChevronDown size={14} strokeWidth={1.5} />
                                </motion.button>
                                <motion.button
                                    whileHover={{ scale: 1.08 }}
                                    whileTap={{ scale: 0.92 }}
                                    onClick={onDuplicate}
                                    className="p-1.5 rounded-lg hover:bg-white/[0.06] text-text-secondary hover:text-foreground transition-colors"
                                    title={t("duplicateShot")}
                                >
                                    <Copy size={13} strokeWidth={1.5} />
                                </motion.button>
                                <motion.button
                                    whileHover={{ scale: 1.08 }}
                                    whileTap={{ scale: 0.92 }}
                                    onClick={onDelete}
                                    disabled={totalShots <= 1}
                                    className="p-1.5 rounded-lg hover:bg-white/[0.06] text-text-secondary hover:text-rose-400 transition-colors disabled:opacity-20 disabled:hover:bg-transparent"
                                    title={t("deleteShot")}
                                >
                                    <Trash2 size={13} strokeWidth={1.5} />
                                </motion.button>
                            </div>
                            {renderGenerateButton()}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
