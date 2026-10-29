"use client";
/**
 * TaskQueuePanel — slide-out side panel from the right edge that
 * lists every video task across the entire project (跨 shot 总览).
 * Per grill Q10:
 *   - 360px wide, push main area (caller controls layout)
 *   - Tabs: Active | Done | Failed (default Active)
 *   - Single-line cards: `● Shot X · Batch Y · status · jump → · ×`
 *   - 1-click cancel (no 60s wait — that's the candidate-thumb's job)
 *   - jump-to-shot scrolls + opens that shot's panel
 *
 * The panel takes a flat task list + a shot→title map. It's purely
 * presentational; the host wires which tasks to show and what
 * "jump to shot" does.
 */
import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { X, ArrowRight, Loader2, Copy, Check, RefreshCw, ChevronDown, ChevronRight, ListChecks, Play } from "lucide-react";
import type { VideoTask } from "@/lib/api";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import PreviewVideo from "@/components/shared/preview/PreviewVideo";
import SidePanelHeader from "@/components/shared/SidePanelHeader";

type TabKey = "active" | "done" | "review" | "failed";

interface TaskQueuePanelProps {
    open: boolean;
    onClose: () => void;
    tasks: VideoTask[];
    /** Map shot.id → human label (e.g. "Shot 2"). Tasks not matching
     *  any shot still render with their frame_id as fallback. */
    shotLabelByFrameId?: Record<string, string>;
    onJumpToShot: (frameId: string) => void;
    onCancel?: (task: VideoTask) => Promise<void> | void;
    onRetry?: (task: VideoTask) => Promise<void> | void;
    onContinue?: (task: VideoTask) => Promise<void> | void;
}

export function canContinueProviderTask(task: VideoTask): boolean {
    const record = task.provider_execution;
    return task.status === "needs_confirmation"
        && record?.phase === "accepted"
        && typeof record.attempt_id === "string"
        && record.attempt_id.length > 0
        && typeof record.fingerprint === "string"
        && /^[0-9a-f]{64}$/.test(record.fingerprint)
        && record.handle == null
        && record.observation == null
        && record.prepared_artifact == null
        && record.artifact == null;
}

export default function TaskQueuePanel({
    open,
    onClose,
    tasks,
    shotLabelByFrameId,
    onJumpToShot,
    onCancel,
    onRetry,
    onContinue,
}: TaskQueuePanelProps) {
    const t = useTranslations("storyboardR2V");
    const [tab, setTab] = useState<TabKey>("active");

    const buckets = useMemo(() => {
        const active: VideoTask[] = [];
        const done: VideoTask[] = [];
        const review: VideoTask[] = [];
        const failed: VideoTask[] = [];
        for (const t of tasks) {
            if (t.status === "pending" || t.status === "processing") active.push(t);
            else if (t.status === "completed") done.push(t);
            else if (t.status === "needs_confirmation") review.push(t);
            else if (t.status === "failed") failed.push(t);
        }
        // Newest first within each bucket.
        const byTimeDesc = (a: VideoTask, b: VideoTask) => b.created_at - a.created_at;
        active.sort(byTimeDesc);
        done.sort(byTimeDesc);
        review.sort(byTimeDesc);
        failed.sort(byTimeDesc);
        return { active, done, review, failed };
    }, [tasks]);

    const visibleTasks = buckets[tab];

    if (!open) return null;

    return (
        <>
            {/* Backdrop — only renders below xl, where the panel is an
                overlay rather than a layout-pushing column. Click to
                dismiss matches the modal idiom for narrower screens. */}
            <div
                aria-hidden="true"
                onClick={onClose}
                className="absolute inset-0 z-20 bg-black/55 backdrop-blur-[1px] motion-safe:animate-[fadeInBackdrop_180ms_cubic-bezier(0.22,1,0.36,1)_both] xl:hidden"
            />
            <aside
                role="region"
                aria-label="Task queue"
                className={[
                    // Always: flex layout, glass surface, slide-in entry.
                    "flex h-full shrink-0 flex-col border-l border-glass-border bg-surface/55 backdrop-blur-xl",
                    "motion-safe:animate-[queuePanelIn_280ms_cubic-bezier(0.22,1,0.36,1)_both]",
                    // ≥xl (1280): push column — mock queue width 344px.
                    "xl:static xl:w-[344px] xl:shadow-none xl:z-auto",
                    // md–lg (768–1279): overlay panel. Floats over main
                    // content rather than pushing it, since narrow
                    // viewports can't spare 344px of horizontal real
                    // estate without the shot list becoming unusable.
                    "absolute inset-y-0 right-0 z-30 w-[340px] max-w-[min(340px,calc(100vw-48px))]",
                    "shadow-[-12px_0_32px_-12px_rgba(0,0,0,0.55)]",
                    // <md (768): full-width drawer. Smaller phones get
                    // the queue as a modal full-screen takeover; the
                    // close button + backdrop both dismiss.
                    "max-md:w-screen max-md:max-w-none",
                ].join(" ")}
            >
            <SidePanelHeader
                icon={<ListChecks />}
                title={t("queueTitle")}
                subtitle={t("queueTotal", { count: tasks.length })}
                trailing={(
                    <button
                        type="button"
                        aria-label="Close queue"
                        onClick={onClose}
                        className="p-1.5 hover:bg-hover-bg rounded-md text-text-secondary hover:text-foreground transition-colors"
                    >
                        <X size={13} aria-hidden="true" />
                    </button>
                )}
            />

            <div role="tablist" className="flex shrink-0 border-b border-glass-border px-3 py-1.5">
                {(
                    [
                        ["active", "queueActive", buckets.active.length, "text-status-processing-fg"],
                        ["done", "queueDone", buckets.done.length, "text-status-completed-fg"],
                        ["review", "queueNeedsConfirmation", buckets.review.length, "text-amber-300"],
                        ["failed", "queueFailed", buckets.failed.length, "text-status-failed-fg"],
                    ] as Array<[TabKey, string, number, string]>
                ).map(([key, labelKey, count, colorClass]) => (
                    <button
                        key={key}
                        type="button"
                        role="tab"
                        aria-selected={tab === key}
                        onClick={() => setTab(key)}
                        className={`min-h-[32px] flex-1 rounded-md py-1.5 font-mono text-chrome-sm font-medium uppercase transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 ${
                            tab === key
                                ? "bg-glass text-foreground"
                                : "text-text-muted hover:text-foreground"
                        }`}
                    >
                        {t(labelKey)}
                        {count > 0 ? (
                            <span className={`ml-1.5 ${colorClass}`}>{count}</span>
                        ) : null}
                    </button>
                ))}
            </div>

            <div className="flex-1 overflow-y-auto px-2.5 py-2.5">
                {visibleTasks.length === 0 ? (
                    <div className="grid h-full place-items-center px-3 text-center font-mono text-chrome-sm font-medium uppercase text-text-muted">
                        {tab === "active"
                            ? t("queueEmptyActive")
                            : tab === "done"
                                ? t("queueEmptyDone")
                                : tab === "review"
                                    ? t("queueEmptyNeedsConfirmation")
                                    : t("queueEmptyFailed")}
                    </div>
                ) : (
                    <ul className="space-y-1">
                        {visibleTasks.map((task) => (
                            <li key={task.id}>
                                <TaskRow
                                    task={task}
                                    shotLabel={shotLabelByFrameId?.[task.frame_id ?? ""] ?? task.frame_id ?? "—"}
                                    onJumpToShot={onJumpToShot}
                                    onCancel={onCancel}
                                    onRetry={onRetry}
                                    onContinue={onContinue}
                                />
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </aside>
        </>
    );
}

/**
 * TaskRow — two-density card (Issue 17 plan B + Q3b row-expand).
 *
 * Compact (default): status dot + shot label + thumb + clipped prompt
 * + meta line. Optimized for "10 active tasks" panoramic scan.
 *
 * Expanded: full prompt (no clamp), 96×54 thumbs (input + output side
 * by side), full params line including duration/seed, full provider
 * IDs with copy buttons, full error text wrap, retry + copy-diagnose.
 *
 * Failed tasks default-expanded — when something failed, the user
 * always wants the diagnose surface immediately. Active/completed
 * default-collapsed; user toggles via the row-level chevron.
 */
function TaskRow({
    task,
    shotLabel,
    onJumpToShot,
    onCancel,
    onRetry,
    onContinue,
}: {
    task: VideoTask;
    shotLabel: string;
    onJumpToShot: (frameId: string) => void;
    onCancel?: (task: VideoTask) => Promise<void> | void;
    onRetry?: (task: VideoTask) => Promise<void> | void;
    onContinue?: (task: VideoTask) => Promise<void> | void;
}) {
    const t = useTranslations("storyboardR2V");
    const [copiedField, setCopiedField] = useState<"providerId" | "providerRequest" | "diagnose" | null>(null);
    const [retrying, setRetrying] = useState(false);
    const [continuing, setContinuing] = useState(false);
    const isFailed = task.status === "failed";
    const isNeedsConfirmation = task.status === "needs_confirmation";
    const isCompleted = task.status === "completed";
    const isInFlight = task.status === "pending" || task.status === "processing";
    const canContinue = canContinueProviderTask(task);
    // Failed tasks always come up expanded — user wants the diagnose
    // surface immediately. Others default collapsed; user toggles.
    const [expanded, setExpanded] = useState<boolean>(isFailed || isNeedsConfirmation);

    const dotClass =
        task.status === "completed"
            ? "bg-status-completed-fg"
            : task.status === "failed"
                ? "bg-status-failed-fg"
                : task.status === "needs_confirmation"
                    ? "bg-amber-300"
                    : task.status === "processing"
                    ? "bg-status-processing-fg animate-pulse"
                    : "bg-status-pending-fg";

    const elapsedS = Math.max(0, Math.floor(Date.now() / 1000 - task.created_at));
    const elapsedLabel = elapsedS < 60
        ? `${elapsedS}s`
        : elapsedS < 3600
            ? `${Math.floor(elapsedS / 60)}m`
            : `${Math.floor(elapsedS / 3600)}h`;

    const promptPreview = (task.prompt || "").trim().slice(0, 60) || "—";

    // Output (when completed) overrides input thumb; otherwise show input frame.
    // 48×27 = 16:9 minimum readable scale.
    const inputThumbUrl = task.image_url || undefined;
    const outputVideoUrl = task.video_url || undefined;

    // Provider ID display: when provider_name=dashscope, label as "百炼"
    // (user-friendly Chinese name) since that's the console they'll paste into.
    const providerLabel =
        task.provider_name === "dashscope" ? t("providerDashscope")
            : task.provider_name === "kling" ? t("providerKling")
                : task.provider_name === "vidu" ? "Vidu"
                    : task.provider_name === "pixverse" ? "PixVerse"
                        : task.provider_name || "provider";

    // Build diagnose blob — copy-pasteable into a support ticket.
    const diagnoseBlob = [
        `Local task: ${task.id}`,
        task.provider_name && task.provider_task_id
            ? `${providerLabel} task: ${task.provider_task_id}`
            : null,
        task.provider_request_id
            ? `${providerLabel} request: ${task.provider_request_id}`
            : null,
        `Model: ${task.model || "?"}`,
        `Status: ${task.status}`,
        task.error ? `Error: ${task.error}` : null,
    ].filter(Boolean).join("\n");

    const handleCopy = async (field: "providerId" | "providerRequest" | "diagnose", text: string) => {
        try {
            await navigator.clipboard.writeText(text);
            setCopiedField(field);
            window.setTimeout(() => setCopiedField(prev => prev === field ? null : prev), 1500);
        } catch {
            /* clipboard blocked */
        }
    };

    const handleRetry = async () => {
        if (!onRetry || retrying) return;
        setRetrying(true);
        try {
            await onRetry(task);
        } finally {
            // Clear after a short delay even on success — the task disappears
            // from "failed" bucket once retried, so spinner becomes moot.
            window.setTimeout(() => setRetrying(false), 800);
        }
    };

    const handleContinue = async () => {
        if (!onContinue || continuing || !canContinue) return;
        setContinuing(true);
        try {
            await onContinue(task);
        } finally {
            window.setTimeout(() => setContinuing(false), 800);
        }
    };

    const fullPrompt = (task.prompt || "").trim();

    return (
        <div
            className="group/row space-y-1.5 rounded-md border border-glass-border bg-glass px-2.5 py-2 transition-colors duration-fast ease-out-quart hover:border-foreground/30"
            title={`Task id: ${task.id}`}
        >
            {/* Header row — chevron + status + shot label + actions */}
            <div className="flex items-center gap-1.5">
                <button
                    type="button"
                    onClick={() => setExpanded(v => !v)}
                    aria-expanded={expanded}
                    aria-label={expanded ? "Collapse task details" : "Expand task details"}
                    title={expanded ? t("queueCollapse") : t("queueExpandDetails")}
                    className="-m-1 grid h-6 w-6 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                >
                    {expanded ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
                </button>
                <span className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`} aria-hidden="true" />
                <span className="truncate font-sans text-body-sm font-medium text-foreground">
                    {shotLabel}
                </span>
                {isInFlight ? (
                    <Loader2 size={10} className="animate-spin text-status-processing-fg" aria-hidden="true" />
                ) : null}
                <span className="ml-auto flex shrink-0 items-center gap-1">
                    {task.frame_id ? (
                        <button
                            type="button"
                            aria-label="Jump to shot"
                            title={t("queueJumpToShot")}
                            onClick={() => onJumpToShot(task.frame_id!)}
                            className="-m-1 grid h-7 w-7 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            <ArrowRight size={11} aria-hidden="true" />
                        </button>
                    ) : null}
                    {isInFlight && onCancel ? (
                        <button
                            type="button"
                            aria-label="Cancel task"
                            title="Cancel"
                            onClick={() => { void onCancel(task); }}
                            className="-m-1 grid h-7 w-7 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-status-failed-bg hover:text-status-failed-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                        >
                            <X size={11} aria-hidden="true" />
                        </button>
                    ) : null}
                </span>
            </div>

            {!expanded ? (
                /* Compact body — single 64×36 thumb + clipped prompt + meta line */
                <div className="flex items-start gap-2">
                    <div className="h-[36px] w-[64px] shrink-0 overflow-hidden rounded border border-glass-border bg-black/40">
                        {isCompleted && outputVideoUrl ? (
                            <PreviewVideo src={outputVideoUrl} alt="output" className="h-full w-full" hoverPlay={false} alwaysShowMagnify clickToLightbox />
                        ) : inputThumbUrl ? (
                            <PreviewImage src={inputThumbUrl} alt="input" className="h-full w-full" alwaysShowMagnify clickToLightbox />
                        ) : (
                            <div className="grid h-full w-full place-items-center font-mono text-[0.5625rem] uppercase text-text-muted">
                                no thumb
                            </div>
                        )}
                    </div>
                    <div className="min-w-0 flex-1 space-y-0.5">
                        <p className="line-clamp-2 font-sans text-body-sm leading-snug text-foreground" title={fullPrompt}>
                            {promptPreview}
                        </p>
                        <p className="truncate font-mono text-chrome-sm tracking-tight text-text-muted">
                            {task.model || "—"}
                            {task.resolution ? ` · ${task.resolution}` : ""}
                            {` · ${elapsedLabel} ago`}
                        </p>
                    </div>
                </div>
            ) : (
                /* Expanded body — bigger thumbs side-by-side + full prompt
                   + full params + provider IDs + error + actions. */
                <div className="space-y-2">
                    {/* Thumbs — input always; output added when completed */}
                    {(inputThumbUrl || (isCompleted && outputVideoUrl)) ? (
                        <div className="flex flex-wrap items-start gap-2">
                            {inputThumbUrl ? (
                                <div className="space-y-0.5">
                                    <p className="font-mono text-[0.5625rem] uppercase tracking-wider text-text-muted">input</p>
                                    <div className="h-[68px] w-[120px] overflow-hidden rounded border border-glass-border bg-black/40">
                                        <PreviewImage src={inputThumbUrl} alt="input" className="h-full w-full" alwaysShowMagnify clickToLightbox />
                                    </div>
                                </div>
                            ) : null}
                            {isCompleted && outputVideoUrl ? (
                                <div className="space-y-0.5">
                                    <p className="font-mono text-[0.5625rem] uppercase tracking-wider text-text-muted">output</p>
                                    <div className="h-[68px] w-[120px] overflow-hidden rounded border border-glass-border bg-black/40">
                                        <PreviewVideo src={outputVideoUrl} alt="output" className="h-full w-full" alwaysShowMagnify clickToLightbox />
                                    </div>
                                </div>
                            ) : null}
                        </div>
                    ) : null}

                    {/* Full prompt — preserves whitespace, no clamp */}
                    {fullPrompt ? (
                        <div className="space-y-0.5">
                            <p className="font-mono text-[0.5625rem] uppercase tracking-wider text-text-muted">prompt</p>
                            <p className="whitespace-pre-wrap rounded border border-glass-border/60 bg-black/30 px-2 py-1.5 font-sans text-body-sm leading-snug text-foreground">
                                {fullPrompt}
                            </p>
                        </div>
                    ) : null}

                    {/* Full params — model · res · duration · seed · timeAgo · mode */}
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-chrome-sm text-text-muted">
                        <span className="text-text-secondary">{task.model || "—"}</span>
                        {task.resolution ? <span>· {task.resolution}</span> : null}
                        {task.duration ? <span>· {task.duration}s</span> : null}
                        {typeof task.seed === "number" ? <span>· seed {task.seed}</span> : null}
                        <span>· {elapsedLabel} ago</span>
                        {task.generation_mode ? <span>· {task.generation_mode}</span> : null}
                    </div>

                    {isNeedsConfirmation ? (
                        <div className="space-y-0.5">
                            <p className="font-mono text-[0.5625rem] uppercase tracking-wider text-amber-200/80">{t("statusNeedsConfirmation")}</p>
                            <p className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 font-sans text-body-sm leading-snug text-amber-100/85">
                                {t("needsConfirmationDetail")}
                            </p>
                            {task.error ? (
                                <p className="whitespace-pre-wrap rounded border border-amber-500/30 bg-black/30 px-2 py-1.5 font-mono text-chrome-sm leading-snug text-amber-100/85">
                                    ⚠ {task.error}
                                </p>
                            ) : null}
                            {canContinue ? (
                                <p className="rounded border border-primary/25 bg-primary/10 px-2 py-1.5 font-sans text-body-sm leading-snug text-primary/90">
                                    {t("queueContinueOriginalHint")}
                                </p>
                            ) : null}
                        </div>
                    ) : null}

                    {/* Failure — full error text wrap */}
                    {isFailed && task.error ? (
                        <div className="space-y-0.5">
                            <p className="font-mono text-[0.5625rem] uppercase tracking-wider text-status-failed-fg/80">error</p>
                            <p className="whitespace-pre-wrap rounded border border-status-failed-border/40 bg-status-failed-bg/60 px-2 py-1.5 font-mono text-chrome-sm leading-snug text-status-failed-fg">
                                ⚠ {task.error}
                            </p>
                        </div>
                    ) : null}

                    {/* Provider IDs — full + copy buttons */}
                    {task.provider_task_id || task.provider_request_id ? (
                        <div className="space-y-1 rounded border border-glass-border/60 bg-black/30 px-2 py-1.5">
                            <p className="font-mono text-[0.5625rem] uppercase tracking-wider text-text-muted">{providerLabel} ids</p>
                            {task.provider_task_id ? (
                                <div className="flex items-center gap-1.5">
                                    <span className="font-mono text-chrome-sm text-text-muted">task:</span>
                                    <code className="min-w-0 flex-1 truncate font-mono text-chrome-sm text-foreground" title={task.provider_task_id}>
                                        {task.provider_task_id}
                                    </code>
                                    <button
                                        type="button"
                                        onClick={() => void handleCopy("providerId", task.provider_task_id!)}
                                        title={t("queueCopyTaskId")}
                                        aria-label="Copy task ID"
                                        className="-m-1 grid h-6 w-6 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                    >
                                        {copiedField === "providerId" ? <Check size={10} /> : <Copy size={10} />}
                                    </button>
                                </div>
                            ) : null}
                            {task.provider_request_id ? (
                                <div className="flex items-center gap-1.5">
                                    <span className="font-mono text-chrome-sm text-text-muted">req:</span>
                                    <code className="min-w-0 flex-1 truncate font-mono text-chrome-sm text-foreground" title={task.provider_request_id}>
                                        {task.provider_request_id}
                                    </code>
                                    <button
                                        type="button"
                                        onClick={() => void handleCopy("providerRequest", task.provider_request_id!)}
                                        title={t("queueCopyRequestId")}
                                        aria-label="Copy request ID"
                                        className="-m-1 grid h-6 w-6 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                    >
                                        {copiedField === "providerRequest" ? <Check size={10} /> : <Copy size={10} />}
                                    </button>
                                </div>
                            ) : null}
                        </div>
                    ) : null}

                    {/* Local task id — for support tickets */}
                    <div className="flex items-center gap-1.5 font-mono text-chrome-sm text-text-muted">
                        <span>local:</span>
                        <code className="truncate text-text-muted/80" title={task.id}>{task.id.slice(0, 18)}…</code>
                    </div>

                    {/* Actions */}
                    {(isFailed || isCompleted || isNeedsConfirmation) ? (
                        <div className="flex items-center justify-end gap-1 pt-0.5">
                            <button
                                type="button"
                                onClick={() => void handleCopy("diagnose", diagnoseBlob)}
                                title={t("queueCopyDiagnose")}
                                className="inline-flex min-h-[24px] items-center gap-1 rounded border border-glass-border bg-black/30 px-2 py-[2px] font-mono text-chrome-sm font-medium text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                            >
                                {copiedField === "diagnose" ? <Check size={10} /> : <Copy size={10} />}
                                {copiedField === "diagnose" ? t("queueCopied") : t("queueCopyDiagnoseShort")}
                            </button>
                            {isNeedsConfirmation && canContinue && onContinue ? (
                                <button
                                    type="button"
                                    aria-label="Continue original submission"
                                    title={t("queueContinueOriginal")}
                                    disabled={continuing}
                                    onClick={() => void handleContinue()}
                                    className="inline-flex min-h-[24px] items-center gap-1 rounded border border-amber-500/35 bg-amber-500/12 px-2 py-[2px] font-mono text-chrome-sm font-medium uppercase text-amber-100 transition-colors duration-fast ease-out-quart hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:cursor-not-allowed disabled:opacity-70"
                                >
                                    {continuing ? (
                                        <Loader2 size={10} className="animate-spin" />
                                    ) : (
                                        <Play size={10} />
                                    )}
                                    {continuing ? t("queueContinuingOriginal") : t("queueContinueOriginal")}
                                </button>
                            ) : null}
                            {isFailed && onRetry ? (
                                <button
                                    type="button"
                                    aria-label="Retry task"
                                    title="Retry"
                                    disabled={retrying}
                                    onClick={() => void handleRetry()}
                                    className="inline-flex min-h-[24px] items-center gap-1 rounded border border-status-failed-border bg-status-failed-bg px-2 py-[2px] font-mono text-chrome-sm font-medium uppercase text-status-failed-fg transition-colors duration-fast ease-out-quart hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border disabled:cursor-not-allowed disabled:opacity-70"
                                >
                                    {retrying ? (
                                        <Loader2 size={10} className="animate-spin" />
                                    ) : (
                                        <RefreshCw size={10} />
                                    )}
                                    {retrying ? "Retrying…" : "Retry"}
                                </button>
                            ) : null}
                        </div>
                    ) : null}
                </div>
            )}
        </div>
    );
}
