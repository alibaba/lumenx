"use client";
/**
 * CandidateThumb — single video take cell in the candidates panel.
 * Always-visible affordances (per grill Q6 "一直可见"):
 *   - ★ icon top-right (multi-select shortlist toggle)
 *   - label inline below the thumb (≤20 chars, click-to-edit)
 *   - status overlay if in-flight / failed
 *   - Shift+Click → toggles compare-select (ring highlight)
 *   - regular click → preview (host decides — usually opens player)
 *
 * Compact size: ~140×80 (16:9-ish) so 4-6 fit per row in the panel.
 * Always shows status; PendingTaskAffordance handles stuck > 60s.
 */
import { useEffect, useRef, useState } from "react";
import { Loader2, Star, AlertCircle, Check, Pencil, Pin } from "lucide-react";
import { useTranslations } from "next-intl";
import { PendingTaskAffordance } from "@/components/shared/PendingTaskAffordance";
import PreviewVideo from "@/components/shared/preview/PreviewVideo";
import type { VideoTask } from "@/lib/api";

interface CandidateThumbProps {
    task: VideoTask;
    isCompareSelected: boolean;
    /** True when this task is the frame's currently-active take (i.e.
     *  frame.selected_video_id === task.id). Drives the primary-color
     *  border + the filled Pin badge state. */
    isActive?: boolean;
    /** Legacy dub URL hint. Source-safe callers should pass displayVideoUrl after proof validation. */
    dubbedVideoUrl?: string;
    /** Verified display URL selected by dubSource helpers. */
    displayVideoUrl?: string;
    /** Optional: resolve a URL to display-ready form (asset prefix). */
    resolveUrl?: (url: string) => string;
    onClick: (task: VideoTask, modifiers: { shift: boolean; meta: boolean }) => void;
    onToggleStar: (task: VideoTask, next: boolean) => Promise<void> | void;
    onSetLabel: (task: VideoTask, next: string | null) => Promise<void> | void;
    /** Pin this take as the frame's active video (manual select). When
     *  omitted the Pin badge is hidden — useful for legacy/I2V flows. */
    onSetActive?: (task: VideoTask) => Promise<void> | void;
    onCancel?: (task: VideoTask) => Promise<void> | void;
    onRetry?: (task: VideoTask) => Promise<void> | void;
}

const MAX_LABEL_LEN = 20;

export default function CandidateThumb({
    task,
    isCompareSelected,
    isActive = false,
    displayVideoUrl,
    resolveUrl,
    onClick,
    onToggleStar,
    onSetLabel,
    onSetActive,
    onCancel,
    onRetry,
}: CandidateThumbProps) {
    const t = useTranslations("storyboardR2V");
    const status = task.status;
    const isProcessing = status === "pending" || status === "processing";
    const isFailed = status === "failed";
    const isNeedsConfirmation = status === "needs_confirmation";
    const isCompleted = status === "completed";
    const display = (u?: string | null) => (u && resolveUrl ? resolveUrl(u) : u ?? undefined);

    const videoUrl = isCompleted ? display(displayVideoUrl || task.video_url) : undefined;

    const [editingLabel, setEditingLabel] = useState(false);
    const [labelDraft, setLabelDraft] = useState(task.label ?? "");
    const labelInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (editingLabel && labelInputRef.current) {
            labelInputRef.current.focus();
            labelInputRef.current.select();
        }
    }, [editingLabel]);

    useEffect(() => {
        // Sync draft when task.label changes from outside (e.g. annotate
        // round-trip refresh).
        setLabelDraft(task.label ?? "");
    }, [task.label]);

    const commitLabel = async () => {
        setEditingLabel(false);
        const trimmed = labelDraft.trim().slice(0, MAX_LABEL_LEN);
        const current = task.label ?? "";
        if (trimmed === current) return;
        await onSetLabel(task, trimmed.length === 0 ? null : trimmed);
    };

    return (
        <div className="flex w-full flex-col gap-1">
            <div
                role="button"
                tabIndex={0}
                aria-label={`Candidate ${task.id.slice(0, 6)}${task.is_starred ? ", starred" : ""}${task.label ? ", labeled " + task.label : ""}, ${status}`}
                onClick={(e) => {
                    if (isNeedsConfirmation) return;
                    onClick(task, { shift: e.shiftKey, meta: e.metaKey || e.ctrlKey });
                }}
                onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        if (isNeedsConfirmation) return;
                        onClick(task, { shift: e.shiftKey, meta: e.metaKey || e.ctrlKey });
                    }
                }}
                className={`group relative aspect-video overflow-hidden rounded-[14px] border bg-black/40 shadow-[var(--shadow-rest)] transition-all duration-base ease-out-quart hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-1 focus-visible:ring-offset-black ${
                    isCompareSelected
                        ? "border-status-starred-border ring-2 ring-status-starred-bg"
                        : isActive
                            ? "border-primary ring-1 ring-primary/40 shadow-[var(--glow-primary)]"
                            : isCompleted
                                ? "border-glass-border hover:border-primary/45"
                                : isFailed
                                    ? "border-status-failed-border"
                                    : isNeedsConfirmation
                                        ? "border-amber-500/45"
                                        : "border-status-pending-border"
                }`}
                title={
                    isActive
                        ? "Active take · Click to play · Shift+Click to add to Compare"
                        : "Click to play · Shift+Click to add to Compare"
                }
            >
                {/* Thumbnail / preview — Issue 14: routes through PreviewVideo
                    for onError fallback + lightbox affordance. Click thumb
                    body is still owned by parent for select/play, but the
                    🔍 magnify button on hover opens fullscreen lightbox. */}
                {videoUrl ? (
                    <PreviewVideo
                        src={videoUrl}
                        alt={`Take ${task.id}`}
                        className="h-full w-full"
                    />
                ) : (
                    <div className="grid h-full w-full place-items-center">
                        {isProcessing ? (
                            <PendingTaskAffordance
                                statusLabel={status === "pending" ? "Queued" : "Generating"}
                                taskId={task.id}
                                compact
                                onCancel={onCancel ? () => onCancel(task) : undefined}
                            />
                        ) : isNeedsConfirmation ? (
                            <div className="flex flex-col items-center gap-1 px-2 text-center">
                                <AlertCircle size={14} className="text-amber-300" aria-hidden="true" />
                                <span className="font-mono text-chrome-sm font-medium uppercase text-amber-200">
                                    {t("statusNeedsConfirmation")}
                                </span>
                            </div>
                        ) : isFailed ? (
                            <div className="flex flex-col items-center gap-1 px-2 text-center">
                                <AlertCircle size={14} className="text-status-failed-fg" aria-hidden="true" />
                                <span className="font-mono text-chrome-sm font-medium uppercase text-status-failed-fg">
                                    Failed
                                </span>
                                {onRetry ? (
                                    <button
                                        type="button"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            void onRetry(task);
                                        }}
                                        className="rounded border border-status-failed-border bg-status-failed-bg px-1.5 py-[1px] font-mono text-chrome-sm font-medium uppercase text-status-failed-fg transition-colors duration-fast ease-out-quart hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                                    >
                                        Retry
                                    </button>
                                ) : null}
                            </div>
                        ) : (
                            <Loader2 size={14} className="animate-spin text-text-muted" />
                        )}
                    </div>
                )}

                {/* 📌 Set-as-active button (top-right, left of ★).
                    Hidden for in-flight / failed tasks (nothing to pin
                    yet). Active state = filled blue badge always visible;
                    inactive = ghost badge that fades in on hover. */}
                {onSetActive && isCompleted ? (
                    <button
                        type="button"
                        aria-pressed={isActive}
                        aria-label={isActive ? "Active take (click to keep pinned)" : "Set as active take"}
                        onClick={(e) => {
                            e.stopPropagation();
                            void onSetActive(task);
                        }}
                        className="absolute right-[34px] top-0.5 grid h-7 w-7 place-items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                    >
                        <span
                            aria-hidden="true"
                            className={`grid h-[22px] w-[22px] place-items-center rounded-full border transition-all duration-fast ease-out-quart active:scale-90 ${
                                isActive
                                    ? "border-primary bg-primary/85 text-white shadow-[var(--glow-primary)]"
                                    : "border-foreground/15 bg-black/55 text-foreground/80 opacity-0 group-hover:opacity-100 hover:text-primary"
                            }`}
                        >
                            <Pin
                                size={11}
                                aria-hidden="true"
                                fill={isActive ? "currentColor" : "none"}
                                strokeWidth={isActive ? 0 : 1.5}
                            />
                        </span>
                    </button>
                ) : null}

                {/* ★ marker/toggle top-right. Recovered tasks keep old annotations as read-only. */}
                {isNeedsConfirmation ? (
                    task.is_starred ? (
                        <span
                            aria-label="Starred recovered candidate"
                            className="absolute right-0.5 top-0.5 grid h-7 w-7 place-items-center rounded-full"
                        >
                            <span className="grid h-[22px] w-[22px] place-items-center rounded-full border border-status-starred-border bg-status-starred-bg text-status-starred-fg shadow-[0_0_10px_-2px_var(--color-status-starred-bg)]">
                                <Star size={11} aria-hidden="true" fill="currentColor" strokeWidth={0} />
                            </span>
                        </span>
                    ) : null
                ) : (
                    <button
                        type="button"
                        aria-pressed={task.is_starred}
                        aria-label={task.is_starred ? "Unstar candidate" : "Star candidate"}
                        onClick={(e) => {
                            e.stopPropagation();
                            void onToggleStar(task, !task.is_starred);
                        }}
                        className="absolute right-0.5 top-0.5 grid h-7 w-7 place-items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-starred-border"
                    >
                        <span
                            aria-hidden="true"
                            className={`grid h-[22px] w-[22px] place-items-center rounded-full border transition-all duration-fast ease-out-quart active:scale-90 ${
                                task.is_starred
                                    ? "border-status-starred-border bg-status-starred-bg text-status-starred-fg shadow-[0_0_10px_-2px_var(--color-status-starred-bg)]"
                                    : "border-foreground/15 bg-black/55 text-foreground/80 hover:text-status-starred-fg"
                            }`}
                        >
                            <Star
                                size={11}
                                aria-hidden="true"
                                fill={task.is_starred ? "currentColor" : "none"}
                                strokeWidth={task.is_starred ? 0 : 1.5}
                            />
                        </span>
                    </button>
                )}

                {/* Compare-select corner badge */}
                {isCompareSelected ? (
                    <span
                        aria-hidden="true"
                        className="absolute left-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-status-starred-solid text-on-warm"
                    >
                        <Check size={11} aria-hidden="true" strokeWidth={3} />
                    </span>
                ) : null}
            </div>

            {/* Label row — always visible per design grill ("不覆盖在视频上") */}
            {editingLabel ? (
                <input
                    ref={labelInputRef}
                    value={labelDraft}
                    maxLength={MAX_LABEL_LEN}
                    onChange={(e) => setLabelDraft(e.target.value)}
                    onBlur={() => { void commitLabel(); }}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") {
                            e.preventDefault();
                            void commitLabel();
                        } else if (e.key === "Escape") {
                            e.preventDefault();
                            setEditingLabel(false);
                            setLabelDraft(task.label ?? "");
                        }
                    }}
                    placeholder="short note"
                    className="rounded border border-primary/55 bg-black/30 px-1.5 py-[3px] font-mono text-chrome text-foreground placeholder:text-text-muted outline-none focus-visible:ring-2 focus-visible:ring-primary/45"
                />
            ) : (
                <button
                    type="button"
                    onClick={(e) => {
                        e.stopPropagation();
                        setEditingLabel(true);
                    }}
                    className="group/label flex min-h-[24px] items-center gap-1 truncate rounded px-1.5 py-[2px] text-left font-mono text-chrome tracking-tight text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45"
                    title="Click to edit label (≤20 chars)"
                >
                    {task.label ? (
                        <span className="truncate">{task.label}</span>
                    ) : (
                        <>
                            <Pencil size={9} className="text-text-muted opacity-0 transition-opacity duration-fast ease-out-quart group-hover/label:opacity-100" aria-hidden="true" />
                            <span className="text-text-muted italic">add label…</span>
                        </>
                    )}
                </button>
            )}
        </div>
    );
}
