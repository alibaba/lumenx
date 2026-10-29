"use client";

import * as React from "react";

import type { AtelierExportRecord } from "@/lib/atelierTypes";
import { getAssetUrl } from "@/lib/utils";

export interface ExportsPanelLabels {
    title: string;
    empty: string;
    loading: string;
    errorTitle: string;
    retry: string;
    download: string;
    copy: string;
    redo: string;
    redoUnavailable: string;
    delete: string;
    retryDelete: string;
    deletionPending: string;
    deletionBlocked: (message: string) => string;
    clipCount: (count: number) => string;
}

export interface ExportsPanelProps {
    records: AtelierExportRecord[];
    loading?: boolean;
    error?: string | null;
    labels?: Partial<ExportsPanelLabels>;
    className?: string;
    resolveAssetUrl?: (url: string | null | undefined) => string;
    onRefresh?: () => void;
    onCopy?: (record: AtelierExportRecord, url: string) => void | Promise<void>;
    onRedo?: (record: AtelierExportRecord) => void;
    actionDisabled?: boolean;
    onDelete?: (record: AtelierExportRecord) => void;
}

const ACTION_BUTTON_CLASS =
    "rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 text-xs text-white/70 transition hover:bg-white/[0.1] hover:text-white";

const DEFAULT_LABELS: ExportsPanelLabels = {
    title: "Exports",
    empty: "Finished exports will appear here.",
    loading: "Loading exports…",
    errorTitle: "Could not load exports.",
    retry: "Retry",
    download: "Download",
    copy: "Copy link",
    redo: "Redo",
    redoUnavailable: "This export cannot be redone because it predates timeline snapshots.",
    delete: "Delete",
    retryDelete: "Retry file delete",
    deletionPending: "Deletion is pending; retry the original request if needed.",
    deletionBlocked: message => message || "File deletion is blocked.",
    clipCount: (count) => `${count} clip${count === 1 ? "" : "s"}`,
};

export function ExportsPanel({
    records,
    loading = false,
    error = null,
    labels: labelOverrides,
    className = "",
    resolveAssetUrl = getAssetUrl,
    onRefresh,
    onCopy,
    onRedo,
    actionDisabled = false,
    onDelete,
}: ExportsPanelProps): React.JSX.Element {
    const labels = { ...DEFAULT_LABELS, ...labelOverrides };
    const [copyError, setCopyError] = React.useState<string | null>(null);

    const copyRecord = React.useCallback(
        async (record: AtelierExportRecord, url: string) => {
            setCopyError(null);
            try {
                if (onCopy) await onCopy(record, url);
                else await navigator.clipboard.writeText(url);
            } catch (err) {
                setCopyError(err instanceof Error ? err.message : String(err));
            }
        },
        [onCopy],
    );

    return (
        <section className={`at-glass rounded-2xl border border-white/10 bg-black/35 p-3 text-white ${className}`}>
            <header className="mb-3 flex items-center justify-between gap-3">
                <h2 className="font-display text-xs font-semibold uppercase tracking-[0.22em] text-white/80">
                    {labels.title}
                </h2>
                {onRefresh ? (
                    <button type="button" onClick={onRefresh} className={ACTION_BUTTON_CLASS}>
                        {labels.retry}
                    </button>
                ) : null}
            </header>

            {loading && records.length === 0 ? (
                <p role="status" className="text-xs text-white/55">{labels.loading}</p>
            ) : null}
            {error ? (
                <div role="alert" className="mb-3 rounded-xl border border-red-300/20 bg-red-500/10 p-3 text-xs text-red-100">
                    <p className="font-semibold">{labels.errorTitle}</p>
                    <p className="mt-1 text-red-100/80">{error}</p>
                </div>
            ) : null}
            {copyError ? (
                <p role="alert" className="mb-3 text-xs text-red-100">{copyError}</p>
            ) : null}
            {!loading && visibleRecords(records).length === 0 && !error ? (
                <p className="rounded-xl border border-dashed border-white/15 px-3 py-4 text-xs text-white/50">
                    {labels.empty}
                </p>
            ) : null}

            {visibleRecords(records).length > 0 ? (
                <ul className="space-y-2" aria-label="Export history">
                    {visibleRecords(records).map((record) => {
                        const resolvedUrl = resolveSafeExportUrl(resolveAssetUrl, record.video_url);
                        const redoReason = record.deletion ? null : redoUnavailableReason(record, labels.redoUnavailable);
                        const deletionMessage = deletionStatusMessage(record, labels);
                        const canRedo = onRedo && !record.deletion && !redoReason;
                        return (
                            <li key={record.id} className="rounded-xl border border-white/10 bg-white/[0.04] p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <h3 className="truncate text-sm font-semibold text-white/90">
                                            {record.filename || `${record.id}.mp4`}
                                        </h3>
                                        <p className="mt-1 text-[11px] text-white/50">
                                            {formatExportMeta(record)}
                                        </p>
                                    </div>
                                    <span className="rounded-full bg-white/10 px-2 py-1 font-mono text-[10px] text-white/55">
                                        {labels.clipCount(record.clip_count)}
                                    </span>
                                </div>
                                {redoReason ? <p className="mt-2 text-[11px] text-white/45">{redoReason}</p> : null}
                                {deletionMessage ? <p role="alert" className="mt-2 text-[11px] text-amber-200">{deletionMessage}</p> : null}
                                <div className="mt-3 flex flex-wrap gap-2">
                                    <a
                                        href={resolvedUrl || undefined}
                                        download={record.filename || undefined}
                                        aria-disabled={!resolvedUrl}
                                        className={`${ACTION_BUTTON_CLASS} ${resolvedUrl ? "" : "pointer-events-none opacity-40"}`}
                                    >
                                        {labels.download}
                                    </a>
                                    <button
                                        type="button"
                                        onClick={() => resolvedUrl && void copyRecord(record, resolvedUrl)}
                                        disabled={!resolvedUrl}
                                        className={`${ACTION_BUTTON_CLASS} disabled:cursor-not-allowed disabled:opacity-40`}
                                    >
                                        {labels.copy}
                                    </button>
                                    {canRedo ? (
                                        <button type="button" onClick={() => onRedo(record)} disabled={actionDisabled} className={`${ACTION_BUTTON_CLASS} disabled:cursor-not-allowed disabled:opacity-40`}>
                                            {labels.redo}
                                        </button>
                                    ) : null}
                                    {onDelete ? (
                                        <button type="button" onClick={() => onDelete(record)} disabled={actionDisabled} className={`${ACTION_BUTTON_CLASS} text-red-100 disabled:cursor-not-allowed disabled:opacity-40`}>
                                            {record.deletion ? labels.retryDelete : labels.delete}
                                        </button>
                                    ) : null}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            ) : null}
        </section>
    );
}


function visibleRecords(records: AtelierExportRecord[]): AtelierExportRecord[] {
    return records.filter(record => !record.deletion || record.deletion.file_status === "pending" || record.deletion.file_status === "blocked");
}

function deletionStatusMessage(record: AtelierExportRecord, labels: ExportsPanelLabels): string | null {
    const deletion = record.deletion;
    if (!deletion) return null;
    if (deletion.file_status === "pending") return labels.deletionPending;
    if (deletion.file_status === "blocked") return labels.deletionBlocked(deletion.error_message ?? deletion.error_code ?? "");
    return null;
}

export function resolveSafeExportUrl(
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

function formatExportMeta(record: AtelierExportRecord): string {
    const date = Number.isFinite(record.created_at)
        ? new Date(record.created_at * 1000).toLocaleString()
        : "Unknown time";
    return `${date} · ${formatSizeMb(record.size_mb)} MB`;
}

function formatSizeMb(value: number): string {
    if (!Number.isFinite(value)) return "0";
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function redoUnavailableReason(record: AtelierExportRecord, label: string): string | null {
    const snapshot = record.timeline_snapshot;
    if (!snapshot || snapshot.schema_version !== "atelier.timeline.v1") return label;
    return null;
}
