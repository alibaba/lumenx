"use client";

import * as React from "react";

import type { AtelierSequenceEntry } from "@/lib/atelierTypes";
import { getAssetUrl } from "@/lib/utils";

export type SequenceExportStatus =
    | "idle"
    | "saving"
    | "exporting"
    | "cancelling"
    | "failed"
    | "repair_required"
    | "paused"
    | "completed";

export interface SequenceExportState {
    status: SequenceExportStatus;
    readableError?: string | null;
    progressLabel?: string | null;
}

export interface SequenceClipView {
    entry: AtelierSequenceEntry;
    title?: string | null;
    subtitle?: string | null;
    mediaUrl?: string | null;
    posterUrl?: string | null;
    durationLabel?: string | null;
    unavailableReason?: string | null;
}

export interface SequenceStripLabels {
    title: string;
    empty: string;
    export: string;
    cancel: string;
    repair: string;
    resume: string;
    remove: string;
    trim: string;
    applyTrim: string;
    cancelTrim: string;
    moveUp: string;
    moveDown: string;
    unavailable: string;
    trimStart: string;
    trimEnd: string;
    invalidTrim: string;
}

export interface SequenceTrimPatch {
    trimStart: number | null;
    trimEnd: number | null;
}

export interface SequenceDropContext {
    index: number;
}

export interface SequenceReorderMeta {
    fromIndex: number;
    toIndex: number;
    clipId: string;
}

export interface SequenceStripProps {
    entries: SequenceClipView[];
    exportState?: SequenceExportState;
    unsaved?: boolean;
    exportDisabledReason?: string | null;
    labels?: Partial<SequenceStripLabels>;
    className?: string;
    resolveAssetUrl?: (url: string | null | undefined) => string;
    onReorder: (next: AtelierSequenceEntry[], meta: SequenceReorderMeta) => void;
    onTrim: (clipId: string, patch: SequenceTrimPatch) => void;
    onRemove: (clipId: string) => void;
    onExport: () => void;
    onCancel?: () => void;
    onRepair?: () => void;
    onResume?: () => void;
    onInsertFromDrop?: (entry: AtelierSequenceEntry, context: SequenceDropContext) => void;
}

const DEFAULT_LABELS: SequenceStripLabels = {
    title: "Sequence",
    empty: "Add completed clips to build an export timeline.",
    export: "Export",
    cancel: "Cancel",
    repair: "Repair",
    resume: "Resume",
    remove: "Remove",
    trim: "Trim",
    applyTrim: "Apply trim",
    cancelTrim: "Cancel",
    moveUp: "Move up",
    moveDown: "Move down",
    unavailable: "Unavailable",
    trimStart: "Start",
    trimEnd: "End",
    invalidTrim: "Use nonnegative seconds; the end must be after the start.",
};

const BUSY_STATUSES = new Set<SequenceExportStatus>(["saving", "exporting", "cancelling"]);
const DROP_MIME = "application/x-atelier-take";
const ACTION_BUTTON_CLASS =
    "rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 text-xs text-white/75 transition hover:bg-white/[0.1] hover:text-white";
const ICON_BUTTON_CLASS =
    "rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-1 text-[11px] text-white/65 transition hover:bg-white/[0.08] hover:text-white disabled:cursor-not-allowed disabled:opacity-35";

export function sequenceClipId(entry: AtelierSequenceEntry, index: number): string {
    if (entry.id) return entry.id;
    return `${entry.parentId}:${entry.candidateId ?? "uploaded"}:${index}`;
}

export function parseSequenceDropPayload(raw: string): AtelierSequenceEntry | null {
    try {
        const parsed = JSON.parse(raw) as unknown;
        const value = Array.isArray(parsed) ? parsed[0] : parsed;
        if (!value || typeof value !== "object") return null;
        const source = value as Record<string, unknown>;
        const parentId = typeof source.parentId === "string" ? source.parentId : source.parent_id;
        if (typeof parentId !== "string" || parentId.length === 0) return null;
        const rawCandidate = source.candidateId ?? source.candidate_id ?? null;
        const candidateId = rawCandidate === null || typeof rawCandidate === "string" ? rawCandidate : null;
        if (rawCandidate !== null && rawCandidate !== undefined && candidateId === null) return null;
        const id = typeof source.id === "string" && source.id.length > 0 ? source.id : undefined;
        return {
            parentId,
            candidateId,
            id,
            trimStart: null,
            trimEnd: null,
            gain: 1,
            transition: { kind: "cut", duration: 0 },
        };
    } catch {
        return null;
    }
}

export function SequenceStrip({
    entries,
    exportState = { status: "idle" },
    unsaved = false,
    exportDisabledReason,
    labels: labelOverrides,
    className = "",
    resolveAssetUrl = getAssetUrl,
    onReorder,
    onTrim,
    onRemove,
    onExport,
    onCancel,
    onRepair,
    onResume,
    onInsertFromDrop,
}: SequenceStripProps): React.JSX.Element {
    const labels = { ...DEFAULT_LABELS, ...labelOverrides };
    const [editingId, setEditingId] = React.useState<string | null>(null);
    const [draftStart, setDraftStart] = React.useState("");
    const [draftEnd, setDraftEnd] = React.useState("");
    const [dropIndex, setDropIndex] = React.useState<number | null>(null);

    const invalidReason = entries.find((clip) => clip.unavailableReason)?.unavailableReason ?? null;
    const busy = BUSY_STATUSES.has(exportState.status);
    const disabledReason =
        (exportDisabledReason || null) ??
        (busy ? "Export is already in progress." : null) ??
        (unsaved ? "Save the current sequence before exporting." : null) ??
        (invalidReason ? "Resolve unavailable clips before exporting." : null) ??
        (entries.length === 0 ? "Add at least one clip before exporting." : null);
    const canExport = !disabledReason;

    const currentEditingIndex = entries.findIndex(
        (clip, index) => sequenceClipId(clip.entry, index) === editingId,
    );
    const currentEditing = currentEditingIndex >= 0 ? entries[currentEditingIndex] : null;
    const parsedStart = draftToNumber(draftStart);
    const parsedEnd = draftToNumber(draftEnd);
    const invalidTrim = parsedStart === undefined || parsedEnd === undefined
        || (parsedEnd !== null && parsedEnd !== undefined && parsedEnd <= (parsedStart ?? 0));

    const reorder = React.useCallback(
        (fromIndex: number, toIndex: number) => {
            if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return;
            if (fromIndex >= entries.length || toIndex >= entries.length) return;
            const next = entries.map((clip) => clip.entry);
            const [moved] = next.splice(fromIndex, 1);
            if (!moved) return;
            next.splice(toIndex, 0, moved);
            onReorder(next, {
                fromIndex,
                toIndex,
                clipId: sequenceClipId(entries[fromIndex].entry, fromIndex),
            });
        },
        [entries, onReorder],
    );

    const beginTrim = React.useCallback((clip: SequenceClipView, index: number) => {
        setEditingId(sequenceClipId(clip.entry, index));
        setDraftStart(numberToDraft(clip.entry.trimStart));
        setDraftEnd(numberToDraft(clip.entry.trimEnd));
    }, []);

    const applyTrim = React.useCallback(() => {
        if (!currentEditing || currentEditingIndex < 0 || invalidTrim) return;
        const clipId = sequenceClipId(currentEditing.entry, currentEditingIndex);
        onTrim(clipId, {
            trimStart: parsedStart ?? null,
            trimEnd: parsedEnd ?? null,
        });
        setEditingId(null);
    }, [currentEditing, currentEditingIndex, invalidTrim, parsedEnd, parsedStart, onTrim]);

    const cancelTrim = React.useCallback(() => setEditingId(null), []);

    const handleDrop = React.useCallback(
        (event: React.DragEvent, index: number) => {
            if (!onInsertFromDrop) return;
            if (!Array.from(event.dataTransfer.types).includes(DROP_MIME)) return;
            event.preventDefault();
            const entry = parseSequenceDropPayload(event.dataTransfer.getData(DROP_MIME));
            setDropIndex(null);
            if (!entry) return;
            onInsertFromDrop(entry, { index });
        },
        [onInsertFromDrop],
    );

    return (
        <section
            className={`at-glass pointer-events-auto rounded-2xl border border-white/10 bg-black/35 p-3 text-white shadow-2xl ${className}`}
            aria-label={labels.title}
        >
            <header className="mb-3 flex items-center justify-between gap-3">
                <div>
                    <h2 className="font-display text-xs font-semibold uppercase tracking-[0.22em] text-white/80">
                        {labels.title}
                    </h2>
                    {exportState.progressLabel ? (
                        <p className="mt-1 text-[11px] text-white/50">{exportState.progressLabel}</p>
                    ) : null}
                </div>
                <div className="flex items-center gap-2">
                    {busy && onCancel ? (
                        <button type="button" onClick={onCancel} className={ACTION_BUTTON_CLASS}>
                            {labels.cancel}
                        </button>
                    ) : null}
                    {exportState.status === "repair_required" && onRepair ? (
                        <button type="button" onClick={onRepair} className={ACTION_BUTTON_CLASS}>
                            {labels.repair}
                        </button>
                    ) : null}
                    {(exportState.status === "failed" || exportState.status === "paused") && onResume ? (
                        <button type="button" onClick={onResume} className={ACTION_BUTTON_CLASS}>
                            {labels.resume}
                        </button>
                    ) : null}
                    <button
                        type="button"
                        onClick={canExport ? onExport : undefined}
                        disabled={!canExport}
                        title={disabledReason ?? labels.export}
                        aria-describedby={disabledReason ? "sequence-export-disabled-reason" : undefined}
                        className="rounded-full bg-gradient-to-r from-[#646cff] to-[#ff0080] px-3 py-1.5 text-xs font-semibold text-white shadow-lg shadow-[#646cff]/20 disabled:cursor-not-allowed disabled:opacity-45"
                    >
                        {labels.export}
                    </button>
                </div>
            </header>

            {disabledReason ? (
                <p id="sequence-export-disabled-reason" className="mb-2 text-[11px] text-amber-200/85">
                    {disabledReason}
                </p>
            ) : null}
            {exportState.readableError ? (
                <p role="status" className="mb-2 text-[11px] text-red-200/90">
                    {exportState.readableError}
                </p>
            ) : null}

            {entries.length === 0 ? (
                <div className="rounded-xl border border-dashed border-white/15 px-3 py-4 text-xs text-white/50"
                    onDragOver={event => { if (Array.from(event.dataTransfer.types).includes(DROP_MIME)) event.preventDefault(); }}
                    onDrop={event => handleDrop(event, 0)}>
                    {labels.empty}
                </div>
            ) : (
                <ol className="flex max-w-full gap-2 overflow-x-auto pb-1" aria-label="Sequence clips">
                    {entries.map((clip, index) => {
                        const clipId = sequenceClipId(clip.entry, index);
                        const resolvedMedia = resolveSafeAssetUrl(resolveAssetUrl, clip.mediaUrl);
                        const resolvedPoster = resolveSafeAssetUrl(resolveAssetUrl, clip.posterUrl);
                        const isDropTarget = dropIndex === index;
                        return (
                            <li
                                key={clipId}
                                className={`min-w-[168px] rounded-xl border bg-white/[0.04] p-2 transition ${
                                    clip.unavailableReason
                                        ? "border-amber-300/35"
                                        : isDropTarget
                                          ? "border-[#646cff]/70"
                                          : "border-white/10"
                                }`}
                                onDragOver={(event) => {
                                    if (!Array.from(event.dataTransfer.types).includes(DROP_MIME)) return;
                                    event.preventDefault();
                                    setDropIndex(index);
                                }}
                                onDragLeave={() => setDropIndex(null)}
                                onDrop={(event) => handleDrop(event, index)}
                            >
                                <article
                                    tabIndex={0}
                                    onKeyDown={(event) => {
                                        if (!event.altKey) return;
                                        if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
                                            event.preventDefault();
                                            reorder(index, index - 1);
                                        }
                                        if (event.key === "ArrowDown" || event.key === "ArrowRight") {
                                            event.preventDefault();
                                            reorder(index, index + 1);
                                        }
                                    }}
                                    aria-label={`${clip.title ?? "Clip"} ${index + 1}`}
                                    className="outline-none focus-visible:ring-2 focus-visible:ring-[#646cff]"
                                >
                                    <div className="mb-2 flex items-start justify-between gap-2">
                                        <div className="min-w-0">
                                            <h3 className="truncate text-xs font-semibold text-white/90">
                                                {clip.title || `Clip ${index + 1}`}
                                            </h3>
                                            {clip.subtitle && <p className="truncate text-[11px] text-white/45">
                                                {clip.subtitle}
                                            </p>}
                                        </div>
                                        <span className="rounded-full bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/55">
                                            {index + 1}
                                        </span>
                                    </div>

                                    <div className="relative mb-2 aspect-video overflow-hidden rounded-lg bg-black/50">
                                        {resolvedMedia && !clip.unavailableReason ? (
                                            <video
                                                src={resolvedMedia}
                                                poster={resolvedPoster || undefined}
                                                muted
                                                playsInline
                                                preload="metadata"
                                                className="h-full w-full object-cover"
                                                aria-label={clip.title || `Clip ${index + 1}`}
                                            />
                                        ) : (
                                            <div className="grid h-full place-items-center px-3 text-center text-[11px] text-white/45">
                                                {clip.unavailableReason || labels.unavailable}
                                            </div>
                                        )}
                                    </div>

                                    {clip.unavailableReason ? (
                                        <p role="status" className="mb-2 text-[11px] text-amber-200/90">
                                            {clip.unavailableReason}
                                        </p>
                                    ) : null}

                                    <div className="mb-2 flex items-center justify-between text-[11px] text-white/45">
                                        <span>{clip.durationLabel || "—"}</span>
                                        <span>{formatTrim(clip.entry)}</span>
                                    </div>

                                    <div className="grid grid-cols-5 gap-1">
                                        <button
                                            type="button"
                                            onClick={() => reorder(index, index - 1)}
                                            disabled={index === 0}
                                            aria-label={labels.moveUp}
                                            className={ICON_BUTTON_CLASS}
                                        >
                                            ↑
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => reorder(index, index + 1)}
                                            disabled={index === entries.length - 1}
                                            aria-label={labels.moveDown}
                                            className={ICON_BUTTON_CLASS}
                                        >
                                            ↓
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => beginTrim(clip, index)}
                                            aria-label={`${labels.trim} ${index + 1}`}
                                            className={`${ICON_BUTTON_CLASS} col-span-2`}
                                        >
                                            {labels.trim}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => onRemove(clipId)}
                                            aria-label={`${labels.remove} ${index + 1}`}
                                            className={`${ICON_BUTTON_CLASS} text-red-200 hover:text-red-100`}
                                        >
                                            ×
                                        </button>
                                    </div>
                                </article>
                            </li>
                        );
                    })}
                </ol>
            )}

            {currentEditing ? (
                <div role="dialog" aria-label={labels.trim} className="mt-3 rounded-xl border border-white/10 bg-white/[0.05] p-3">
                    {invalidTrim && <p role="alert" className="mb-2 text-xs text-amber-200">{labels.invalidTrim}</p>}
                    <div className="grid grid-cols-2 gap-2">
                        <label className="text-[11px] text-white/60">
                            {labels.trimStart}
                            <input
                                value={draftStart}
                                onChange={(event) => setDraftStart(event.target.value)}
                                inputMode="decimal"
                                className="mt-1 w-full rounded border border-white/10 bg-black/30 px-2 py-1 text-xs text-white"
                            />
                        </label>
                        <label className="text-[11px] text-white/60">
                            {labels.trimEnd}
                            <input
                                value={draftEnd}
                                onChange={(event) => setDraftEnd(event.target.value)}
                                inputMode="decimal"
                                className="mt-1 w-full rounded border border-white/10 bg-black/30 px-2 py-1 text-xs text-white"
                            />
                        </label>
                    </div>
                    <div className="mt-3 flex justify-end gap-2">
                        <button type="button" onClick={cancelTrim} className={ACTION_BUTTON_CLASS}>
                            {labels.cancelTrim}
                        </button>
                        <button type="button" onClick={applyTrim} disabled={invalidTrim} className={`${ACTION_BUTTON_CLASS} bg-white/15 text-white disabled:opacity-40`}>
                            {labels.applyTrim}
                        </button>
                    </div>
                </div>
            ) : null}
        </section>
    );
}

function numberToDraft(value: number | null | undefined): string {
    return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

function draftToNumber(value: string): number | null | undefined {
    const trimmed = value.trim();
    if (!trimmed) return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

function formatTrim(entry: AtelierSequenceEntry): string {
    const start = numberToDraft(entry.trimStart) || "0";
    const end = numberToDraft(entry.trimEnd);
    return end ? `${start}s → ${end}s` : `${start}s → end`;
}

function resolveSafeAssetUrl(
    resolver: (url: string | null | undefined) => string,
    value: string | null | undefined,
): string {
    const resolved = resolver(value);
    if (!resolved) return "";
    try {
        const url = new URL(resolved, "http://lumenx.local");
        return ["http:", "https:", "blob:"].includes(url.protocol) ? resolved : "";
    } catch {
        return "";
    }
}
