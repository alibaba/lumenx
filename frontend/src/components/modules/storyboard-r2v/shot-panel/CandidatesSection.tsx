"use client";
/**
 * CandidatesSection — lower half of the attached ShotPanel. Shows
 * every video task this shot has produced, grouped by batch (a
 * cluster of tasks created from one Generate ×N click).
 *
 * Design per grill Q4/Q5/Q6:
 *   - Batch + accumulate: each Generate adds N candidates; old
 *     batches stay (never overwritten)
 *   - Latest batch expanded by default, older batches collapsed
 *   - Filter chips: All / ★ Starred only / 此模型 (current model)
 *   - Sort: by time desc (default) or by model
 *   - Multi-row wrap inside each batch (no horizontal scroll)
 *   - Per-row "复用此批参数" button on each collapsed batch header
 *
 * Batch detection: VideoTask doesn't have a batch_id field; we
 * approximate by clustering tasks whose created_at differs by ≤ a
 * gap window AND share the same (model, prompt, negative_prompt)
 * fingerprint. Crude but matches the user's mental "I clicked
 * Generate once" model.
 */
import { useMemo, useState } from "react";
import { Star, Film, Clock, ArrowDown, ArrowUp, RotateCw } from "lucide-react";
import { useTranslations } from "next-intl";
import type { VideoTask } from "@/lib/api";
import { selectVerifiedAppliedDubUrlForTask, type DubSourceProof, type MediaRefsLike } from "@/lib/dubSource";
import SectionShell from "./SectionShell";
import { usePanelSectionState } from "./usePanelSectionState";
import CandidateThumb from "./CandidateThumb";

interface CandidatesSectionProps {
    shotId: string;
    tasks: VideoTask[];
    /** Tasks pre-filtered to a specific tab (t2i_i2v / direct_r2v) so
     *  candidates from the other tab don't leak in. */
    activeModel?: string;
    compareSelectedIds: Set<string>;
    /** Legacy dub info. Display only when matching source proof is also supplied. */
    dubbedVideoUrl?: string;
    dubbedVideoTaskId?: string;
    frameAudioUrl?: string | null;
    frameMediaRefs?: MediaRefsLike | null;
    dubAppliedSource?: DubSourceProof | null;
    onClickThumb: (task: VideoTask, modifiers: { shift: boolean; meta: boolean }) => void;
    onToggleStar: (task: VideoTask, next: boolean) => Promise<void> | void;
    onSetLabel: (task: VideoTask, next: string | null) => Promise<void> | void;
    /** When provided, each thumb shows a 📌 Set-as-active button (only on
     *  completed tasks). The task with id === activeTaskId is rendered
     *  with the primary border + filled Pin badge. */
    onSetActive?: (task: VideoTask) => Promise<void> | void;
    /** The frame's currently selected video id (frame.selected_video_id),
     *  used to highlight the active thumb. */
    activeTaskId?: string | null;
    onCancel?: (task: VideoTask) => Promise<void> | void;
    onRetry?: (task: VideoTask) => Promise<void> | void;
    onReuseBatchParams?: (batch: BatchSummary) => void;
    onOpenCompare?: () => void;
    resolveUrl?: (url: string) => string;
}

export interface BatchSummary {
    /** Synthetic id derived from earliest task's id. */
    id: string;
    tasks: VideoTask[];
    createdAt: number;
    model: string;
    summary: string;
}

type FilterMode = "all" | "starred" | "this-model";
type SortMode = "time" | "model";

const BATCH_GAP_MS = 15_000; // Tasks within 15s of each other on the same model/prompt cluster together.

function groupIntoBatches(tasks: VideoTask[]): BatchSummary[] {
    if (tasks.length === 0) return [];
    // Sort by created_at asc first so clustering can walk forward.
    const sorted = [...tasks].sort((a, b) => a.created_at - b.created_at);
    const batches: VideoTask[][] = [];
    let current: VideoTask[] = [sorted[0]];
    for (let i = 1; i < sorted.length; i++) {
        const prev = current[current.length - 1];
        const next = sorted[i];
        const sameCluster =
            (next.created_at - prev.created_at) * 1000 < BATCH_GAP_MS &&
            next.model === prev.model &&
            next.prompt === prev.prompt &&
            (next.negative_prompt ?? "") === (prev.negative_prompt ?? "");
        if (sameCluster) {
            current.push(next);
        } else {
            batches.push(current);
            current = [next];
        }
    }
    batches.push(current);
    return batches.map((bt) => {
        const first = bt[0];
        const parts: string[] = [];
        if (first.negative_prompt) parts.push(`neg="${first.negative_prompt.slice(0, 24)}"`);
        if (first.resolution) parts.push(first.resolution);
        if (first.ratio) parts.push(first.ratio);
        const summary = parts.length ? parts.join(" · ") : "default params";
        return {
            id: `batch-${first.id}`,
            tasks: bt,
            createdAt: first.created_at,
            model: first.model || "unknown",
            summary,
        };
    });
}

export default function CandidatesSection({
    shotId,
    tasks,
    activeModel,
    compareSelectedIds,
    dubbedVideoUrl,
    dubbedVideoTaskId,
    frameAudioUrl,
    frameMediaRefs,
    dubAppliedSource,
    onClickThumb,
    onToggleStar,
    onSetLabel,
    onSetActive,
    activeTaskId,
    onCancel,
    onRetry,
    onReuseBatchParams,
    onOpenCompare,
    resolveUrl,
}: CandidatesSectionProps) {
    const t = useTranslations("storyboardR2V");
    const [open, setOpen] = usePanelSectionState(shotId, "candidates", true);
    const [filter, setFilter] = useState<FilterMode>("all");
    const [sort, setSort] = useState<SortMode>("time");

    const isActionableCandidate = (task: VideoTask) => task.status !== "needs_confirmation";

    const filteredTasks = useMemo(() => {
        return tasks.filter((t) => {
            if (filter === "starred" && (!isActionableCandidate(t) || !t.is_starred)) return false;
            if (filter === "this-model" && activeModel && t.model !== activeModel) return false;
            return true;
        });
    }, [tasks, filter, activeModel]);

    const batches = useMemo(() => {
        const out = groupIntoBatches(filteredTasks);
        if (sort === "model") {
            return [...out].sort((a, b) =>
                a.model === b.model
                    ? b.createdAt - a.createdAt
                    : a.model.localeCompare(b.model),
            );
        }
        return [...out].sort((a, b) => b.createdAt - a.createdAt);
    }, [filteredTasks, sort]);

    const totalCount = tasks.length;
    const actionableTaskIds = useMemo(() => new Set(tasks.filter(isActionableCandidate).map((task) => task.id)), [tasks]);
    const starredCount = tasks.filter((t) => isActionableCandidate(t) && t.is_starred).length;
    const compareCount = Array.from(compareSelectedIds).filter((id) => actionableTaskIds.has(id)).length;

    return (
        <SectionShell
            title={t("candidates")}
            subtitle={totalCount > 0 ? t("candidatesCount", { count: totalCount }) : undefined}
            open={open}
            onToggle={() => setOpen(!open)}
            trailing={
                <>
                    {/* Filter chips */}
                    <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>
                        {t("filterAll")}
                    </FilterChip>
                    <FilterChip
                        active={filter === "starred"}
                        onClick={() => setFilter("starred")}
                    >
                        <Star size={9} aria-hidden="true" fill={filter === "starred" ? "currentColor" : "none"} />
                        {starredCount > 0 ? `${starredCount}` : ""}
                    </FilterChip>
                    {activeModel ? (
                        <FilterChip
                            active={filter === "this-model"}
                            onClick={() => setFilter("this-model")}
                            title={t("filterThisModelTitle", { model: activeModel })}
                        >
                            <Film size={9} aria-hidden="true" />
                            {t("filterThisModel")}
                        </FilterChip>
                    ) : null}
                    {/* Sort flipper — text label inline so users know what
                        the click toggles. Label updates in lockstep with state. */}
                    <button
                        type="button"
                        onClick={() => setSort((s) => (s === "time" ? "model" : "time"))}
                        title={sort === "time" ? t("sortByModelTitle") : t("sortByTimeTitle")}
                        aria-label={t("sortAriaLabel", { sort })}
                        className="btn-tip -m-1 inline-flex h-7 items-center gap-1 rounded px-1.5 text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        {sort === "time" ? (
                            <Clock size={11} aria-hidden="true" />
                        ) : (
                            <Film size={11} aria-hidden="true" />
                        )}
                        <span className="font-mono text-chrome-sm font-medium uppercase tracking-tight">
                            {t(sort === "time" ? "sortByTime" : "sortByModel")}
                        </span>
                    </button>
                </>
            }
        >
            {totalCount === 0 ? (
                <div className="flex flex-col items-center gap-2 px-3 py-5">
                    <div className="font-display text-display-sm font-semibold tracking-tight text-foreground">
                        {t("noCandidatesTitle")}
                    </div>
                    <ol className="flex flex-col gap-1 text-center font-mono text-chrome-sm tracking-tight text-text-muted">
                        <li>{t("noCandidatesHint1")}</li>
                        <li>{t("noCandidatesHint2")}</li>
                        <li>{t("noCandidatesHint3")}</li>
                    </ol>
                </div>
            ) : batches.length === 0 ? (
                <div className="px-2 py-4 text-center font-mono text-chrome-sm font-medium uppercase text-text-muted">
                    {t("noMatches")}
                </div>
            ) : (
                <div className="space-y-2">
                    {/* Compare callout — promoted to display tier when
                        ≥2 selected. */}
                    {compareCount >= 2 ? (
                        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[14px] border border-status-starred-border bg-status-starred-bg px-3 py-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] sm:gap-3">
                            <span className="font-display text-display-sm font-semibold tracking-tight text-status-starred-fg">
                                {t("compareSelected", { count: compareCount })}
                            </span>
                            <button
                                type="button"
                                onClick={() => onOpenCompare?.()}
                                disabled={!onOpenCompare}
                                className="inline-flex items-center gap-1.5 rounded-md bg-status-starred-solid px-3 py-1.5 font-display text-display-sm font-semibold text-on-warm shadow-[inset_0_1px_0_rgba(255,255,255,0.25)] transition-all duration-fast ease-out-quart hover:brightness-110 active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-starred-border disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {t("compareGo", { count: compareCount })} →
                            </button>
                        </div>
                    ) : null}

                    {batches.map((batch, batchIdx) => (
                        <BatchBlock
                            key={batch.id}
                            shotId={shotId}
                            batch={batch}
                            defaultOpen={batchIdx === 0}
                            compareSelectedIds={compareSelectedIds}
                            activeTaskId={activeTaskId}
                            dubbedVideoUrl={dubbedVideoUrl}
                            dubbedVideoTaskId={dubbedVideoTaskId}
                            frameAudioUrl={frameAudioUrl}
                            frameMediaRefs={frameMediaRefs}
                            dubAppliedSource={dubAppliedSource}
                            resolveUrl={resolveUrl}
                            onClickThumb={onClickThumb}
                            onToggleStar={onToggleStar}
                            onSetLabel={onSetLabel}
                            onSetActive={onSetActive}
                            onCancel={onCancel}
                            onRetry={onRetry}
                            onReuseBatchParams={onReuseBatchParams}
                        />
                    ))}
                </div>
            )}
        </SectionShell>
    );
}

function FilterChip({
    children,
    active,
    onClick,
    title,
}: {
    children: React.ReactNode;
    active: boolean;
    onClick: () => void;
    title?: string;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            title={title}
            aria-pressed={active}
            className={`inline-flex min-h-[24px] items-center gap-1 rounded-full border px-2 py-[2px] font-mono text-chrome-sm font-medium uppercase transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 ${
                active
                    ? "border-primary/55 bg-primary/15 text-primary"
                    : "border-glass-border bg-black/20 text-text-muted hover:border-foreground/30 hover:text-foreground"
            }`}
        >
            {children}
        </button>
    );
}

function BatchBlock({
    shotId,
    batch,
    defaultOpen,
    compareSelectedIds,
    activeTaskId,
    dubbedVideoUrl,
    dubbedVideoTaskId,
    frameAudioUrl,
    frameMediaRefs,
    dubAppliedSource,
    resolveUrl,
    onClickThumb,
    onToggleStar,
    onSetLabel,
    onSetActive,
    onCancel,
    onRetry,
    onReuseBatchParams,
}: {
    shotId: string;
    batch: BatchSummary;
    defaultOpen: boolean;
    compareSelectedIds: Set<string>;
    activeTaskId?: string | null;
    dubbedVideoUrl?: string;
    dubbedVideoTaskId?: string;
    frameAudioUrl?: string | null;
    frameMediaRefs?: MediaRefsLike | null;
    dubAppliedSource?: DubSourceProof | null;
    resolveUrl?: (url: string) => string;
    onClickThumb: CandidatesSectionProps["onClickThumb"];
    onToggleStar: CandidatesSectionProps["onToggleStar"];
    onSetLabel: CandidatesSectionProps["onSetLabel"];
    onSetActive?: CandidatesSectionProps["onSetActive"];
    onCancel?: CandidatesSectionProps["onCancel"];
    onRetry?: CandidatesSectionProps["onRetry"];
    onReuseBatchParams?: CandidatesSectionProps["onReuseBatchParams"];
}) {
    const t = useTranslations("storyboardR2V");
    const [open, setOpen] = useState(defaultOpen);
    const failedCount = batch.tasks.filter((t) => t.status === "failed").length;
    const runningCount = batch.tasks.filter((t) => t.status === "pending" || t.status === "processing").length;
    const completedCount = batch.tasks.filter((t) => t.status === "completed").length;

    const formatBatchAge = (ts: number): string => {
        const ageS = Math.max(0, Math.floor(Date.now() / 1000 - ts));
        if (ageS < 60) return t("batchAgeSeconds", { s: ageS });
        if (ageS < 3600) return t("batchAgeMinutes", { m: Math.floor(ageS / 60) });
        if (ageS < 86_400) return t("batchAgeHours", { h: Math.floor(ageS / 3600) });
        return t("batchAgeDays", { d: Math.floor(ageS / 86_400) });
    };

    // Restructured batch header (P2-2): left = model + take count
    // (body tier, the things that disambiguate this batch). Right =
    // status pips (only when nonzero). Params (negative, resolution,
    // ratio) demoted to title attribute so the row breathes — they
    // matter on the rare 复用 click, not on every scan.
    return (
        <div className="rounded-[14px] border border-glass-border bg-black/15">
            <div className="flex items-center gap-2 px-2 py-2">
                <button
                    type="button"
                    onClick={() => setOpen(!open)}
                    aria-expanded={open}
                    title={batch.summary}
                    className="-m-1 flex min-w-0 flex-1 items-center gap-2 rounded p-1 text-left transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                >
                    <span className="text-text-muted">
                        {open ? (
                            <ArrowDown size={11} aria-hidden="true" />
                        ) : (
                            <ArrowUp size={11} aria-hidden="true" className="rotate-180" />
                        )}
                    </span>
                    <span className="truncate font-sans text-body-sm font-medium text-foreground">
                        ×{batch.tasks.length}
                        <span className="ml-1 font-mono text-chrome-sm text-text-muted">{batch.model}</span>
                    </span>
                    <span className="truncate font-mono text-chrome-sm tracking-tight text-text-muted">
                        · {formatBatchAge(batch.createdAt)}
                    </span>
                    <span className="ml-auto flex shrink-0 items-center gap-1.5">
                        {runningCount > 0 ? (
                            <span className="inline-flex items-center gap-0.5 font-mono text-chrome-sm font-medium text-status-processing-fg">
                                <span aria-hidden="true">●</span>
                                <span aria-label={`${runningCount} running`}>{runningCount}</span>
                            </span>
                        ) : null}
                        {completedCount > 0 ? (
                            <span className="inline-flex items-center gap-0.5 font-mono text-chrome-sm font-medium text-status-completed-fg">
                                <span aria-hidden="true">✓</span>
                                <span aria-label={`${completedCount} completed`}>{completedCount}</span>
                            </span>
                        ) : null}
                        {failedCount > 0 ? (
                            <span className="inline-flex items-center gap-0.5 font-mono text-chrome-sm font-medium text-status-failed-fg">
                                <span aria-hidden="true">✗</span>
                                <span aria-label={`${failedCount} failed`}>{failedCount}</span>
                            </span>
                        ) : null}
                    </span>
                </button>
                {onReuseBatchParams ? (
                    <button
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            onReuseBatchParams(batch);
                        }}
                        title={t("batchReuse")}
                        aria-label={t("batchReuse")}
                        className="-m-1 grid h-7 w-7 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        <RotateCw size={11} aria-hidden="true" />
                    </button>
                ) : null}
            </div>
            {open ? (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(152px,1fr))] gap-2.5 border-t border-glass-border p-2.5">
                    {batch.tasks.map((task) => {
                        const verifiedDubbedUrl = task.id === dubbedVideoTaskId
                            ? selectVerifiedAppliedDubUrlForTask({
                                id: shotId,
                                audio_url: frameAudioUrl ?? undefined,
                                media_refs: frameMediaRefs ?? undefined,
                                dubbed_video_url: dubbedVideoUrl,
                                dub_applied_source: dubAppliedSource ?? undefined,
                            }, task)
                            : undefined;
                        return (
                        <CandidateThumb
                            key={task.id}
                            task={task}
                            isCompareSelected={task.status !== "needs_confirmation" && compareSelectedIds.has(task.id)}
                            isActive={task.status !== "needs_confirmation" && !!activeTaskId && task.id === activeTaskId}
                            dubbedVideoUrl={task.id === dubbedVideoTaskId ? dubbedVideoUrl : undefined}
                            displayVideoUrl={verifiedDubbedUrl}
                            resolveUrl={resolveUrl}
                            onClick={task.status === "needs_confirmation" ? () => undefined : onClickThumb}
                            onToggleStar={task.status === "needs_confirmation" ? () => undefined : onToggleStar}
                            onSetLabel={onSetLabel}
                            onSetActive={task.status === "needs_confirmation" ? undefined : onSetActive}
                            onCancel={onCancel}
                            onRetry={onRetry}
                        />
                        );
                    })}
                </div>
            ) : null}
        </div>
    );
}
