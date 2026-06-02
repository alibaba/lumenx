"use client";
//
// ExportsPanel — left-rail panel body for the v1.1 W "Exports" mode.
//
// Renders the persisted ExportRecord history for the active project as a
// vertical list. Each row carries:
//   - a thumbnail (mp4 poster via <video preload="metadata">)
//   - the filename
//   - relative timestamp ("2m ago", "yesterday", absolute fallback)
//   - size in MB + clip count
//   - 4-icon action rail: Download / Copy share link / Redo / Delete
//
// The store slice (`exports_W`, `loadExports_W`, `deleteExport_W`,
// `redoExport_W`) owns persistence. The panel just renders + dispatches.
//
// File ownership (track W) — this file is new and W-only. Coordinates
// with V (onboarding) only via the data-onboarding-target attributes
// V can add later; we expose `data-onboarding-target="exports-panel"`
// on the panel root so the tour has a stable hook.

import * as React from "react";
import {
  AlertCircle,
  Copy,
  Download,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import type { AtelierExportRecord } from "@/lib/api";
import { useAtelierStore } from "@/store/atelierStore";
import { getAssetUrl } from "@/lib/utils";

interface Props {
  /** Optional toast hook so the panel can surface success / error
   *  feedback through the shell's standard toast stack instead of
   *  inventing its own. When omitted, errors are swallowed and only
   *  the in-panel `exportsError_W` banner shows them. */
  pushToast?: (
    kind: "info" | "success" | "error",
    message: string,
  ) => void;
}

// ─── time helpers ──────────────────────────────────────────────────────

function formatRelative(secondsAgo: number): string {
  if (!Number.isFinite(secondsAgo) || secondsAgo < 0) return "just now";
  if (secondsAgo < 5) return "just now";
  if (secondsAgo < 60) return `${Math.floor(secondsAgo)}s ago`;
  if (secondsAgo < 3600) return `${Math.floor(secondsAgo / 60)}m ago`;
  if (secondsAgo < 86400) return `${Math.floor(secondsAgo / 3600)}h ago`;
  if (secondsAgo < 86400 * 7) return `${Math.floor(secondsAgo / 86400)}d ago`;
  return ""; // fall through to absolute formatter below
}

function formatTimestamp(epochSeconds: number): string {
  const ageSeconds = Date.now() / 1000 - epochSeconds;
  const relative = formatRelative(ageSeconds);
  if (relative) return relative;
  return new Date(epochSeconds * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatSize(sizeMb: number): string {
  if (!Number.isFinite(sizeMb) || sizeMb <= 0) return "—";
  if (sizeMb < 0.1) return `${(sizeMb * 1024).toFixed(0)} KB`;
  if (sizeMb < 10) return `${sizeMb.toFixed(2)} MB`;
  return `${sizeMb.toFixed(1)} MB`;
}

// ─── row ───────────────────────────────────────────────────────────────

interface ExportRowProps {
  record: AtelierExportRecord;
  busy: boolean;
  onCopy: () => void;
  onRedo: () => void;
  onDelete: () => void;
}

function ExportRow({ record, busy, onCopy, onRedo, onDelete }: ExportRowProps) {
  const resolvedUrl = getAssetUrl(record.video_url || "");
  const downloadName = record.filename || "atelier-export.mp4";

  return (
    <li
      data-onboarding-target="exports-panel-row"
      className="rounded-md border border-white/8 bg-black/20 transition-colors hover:bg-black/[0.28]"
    >
      <div className="flex items-stretch gap-2.5 p-2">
        {/* Thumbnail — <video preload="metadata"> renders the first
            frame as a free poster without an extra HTTP fetch. */}
        <div className="relative aspect-video w-[88px] shrink-0 overflow-hidden rounded border border-white/8 bg-black">
          {resolvedUrl ? (
            <video
              src={resolvedUrl}
              preload="metadata"
              muted
              playsInline
              className="h-full w-full object-cover"
              // First frame as a poster — onLoadedMetadata fires once
              // the browser has enough info to seek; we nudge to 0 so
              // the canvas shows the opening frame instead of a black
              // pixel on some codecs.
              onLoadedMetadata={(event) => {
                const v = event.currentTarget;
                try {
                  v.currentTime = 0;
                } catch {
                  /* some browsers reject the assignment; harmless */
                }
              }}
            />
          ) : (
            <div className="grid h-full w-full place-items-center text-[10px] text-text-muted/70">
              no preview
            </div>
          )}
        </div>

        {/* Filename + meta */}
        <div className="min-w-0 flex-1">
          <div
            className="truncate font-sans text-[12px] font-medium tracking-tight text-foreground/95"
            title={downloadName}
          >
            {downloadName}
          </div>
          <div className="mt-0.5 flex items-center gap-2 font-display text-[11px] tabular-nums text-text-muted/85">
            <span title={new Date(record.created_at * 1000).toLocaleString()}>
              {formatTimestamp(record.created_at)}
            </span>
            <span aria-hidden="true" className="text-text-muted/45">·</span>
            <span>{formatSize(record.size_mb)}</span>
            <span aria-hidden="true" className="text-text-muted/45">·</span>
            <span>
              {record.clip_count} clip{record.clip_count === 1 ? "" : "s"}
            </span>
          </div>

          {/* Action rail */}
          <div className="mt-1.5 flex items-center gap-1">
            <a
              href={resolvedUrl || undefined}
              download={downloadName}
              aria-label="Download mp4"
              data-tip="Download mp4"
              className={`btn-tip inline-grid h-7 w-7 place-items-center rounded text-text-muted hover:bg-white/[0.06] hover:text-foreground ${
                resolvedUrl ? "" : "pointer-events-none opacity-40"
              }`}
            >
              <Download size={13} aria-hidden="true" />
            </a>
            <button
              type="button"
              aria-label="Copy share link"
              data-tip="Copy link"
              onClick={onCopy}
              disabled={!resolvedUrl}
              className="btn-tip inline-grid h-7 w-7 place-items-center rounded text-text-muted transition-colors hover:bg-white/[0.06] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Copy size={13} aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label="Re-export with same sequence"
              data-tip="Redo"
              onClick={onRedo}
              disabled={busy}
              className="btn-tip inline-grid h-7 w-7 place-items-center rounded text-text-muted transition-colors hover:bg-white/[0.06] hover:text-atelier-brand-400 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? (
                <RotateCcw size={13} aria-hidden="true" className="animate-spin" />
              ) : (
                <RotateCcw size={13} aria-hidden="true" />
              )}
            </button>
            <button
              type="button"
              aria-label="Delete export"
              data-tip="Delete"
              onClick={onDelete}
              className="btn-tip inline-grid h-7 w-7 place-items-center rounded text-text-muted transition-colors hover:bg-white/[0.06] hover:text-atelier-failed disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Trash2 size={13} aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </li>
  );
}

// ─── panel ─────────────────────────────────────────────────────────────

export function ExportsPanel({ pushToast }: Props) {
  const projectId = useAtelierStore((s) => s.currentProject?.id ?? null);
  const exports = useAtelierStore((s) => s.exports_W);
  const loading = useAtelierStore((s) => s.exportsLoading_W);
  const error = useAtelierStore((s) => s.exportsError_W);
  const loadExports = useAtelierStore((s) => s.loadExports_W);
  const deleteExport = useAtelierStore((s) => s.deleteExport_W);
  const redoExport = useAtelierStore((s) => s.redoExport_W);

  const [busyId, setBusyId] = React.useState<string | null>(null);

  // Initial load on mount + whenever the active project id changes.
  // The panel re-mounts when the rail toggles, so this naturally
  // refreshes when the user reopens the panel.
  React.useEffect(() => {
    if (!projectId) return;
    void loadExports(projectId).catch(() => {
      /* error already captured into exportsError_W */
    });
  }, [projectId, loadExports]);

  const handleCopy = React.useCallback(
    async (record: AtelierExportRecord) => {
      const url = getAssetUrl(record.video_url || "");
      if (!url) {
        pushToast?.("error", "This export has no resolvable URL.");
        return;
      }
      try {
        if (typeof navigator !== "undefined" && navigator.clipboard) {
          await navigator.clipboard.writeText(url);
          pushToast?.("success", "Share link copied.");
          return;
        }
        // Fallback — execCommand is deprecated but still the only
        // path on older Safari + non-secure contexts.
        const ta = document.createElement("textarea");
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
          pushToast?.("success", "Share link copied.");
        } finally {
          document.body.removeChild(ta);
        }
      } catch (err) {
        pushToast?.(
          "error",
          `Copy failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [pushToast],
  );

  const handleRedo = React.useCallback(
    async (record: AtelierExportRecord) => {
      if (!projectId) return;
      setBusyId(record.id);
      try {
        await redoExport(projectId, record.id);
        pushToast?.("info", "Redo queued — new export started in the background.");
        // The redo doesn't add a new ExportRecord until the worker
        // completes, so we don't refresh the list here. Reopening
        // the panel after a few seconds will pick up the new row.
      } catch (err) {
        pushToast?.(
          "error",
          `Redo failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      } finally {
        setBusyId(null);
      }
    },
    [projectId, redoExport, pushToast],
  );

  const handleDelete = React.useCallback(
    async (record: AtelierExportRecord) => {
      if (!projectId) return;
      const confirmed =
        typeof window !== "undefined"
          ? window.confirm(
              `Delete this export?\n\n${record.filename}\n\nThe video file under output/video/ will also be removed.`,
            )
          : true;
      if (!confirmed) return;
      try {
        await deleteExport(projectId, record.id, { deleteFile: true });
        pushToast?.("success", "Export removed.");
      } catch (err) {
        pushToast?.(
          "error",
          `Delete failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [projectId, deleteExport, pushToast],
  );

  // ─── render ────────────────────────────────────────────────────────

  if (!projectId) {
    return (
      <div className="p-4 text-[12px] text-text-muted/85">
        No active project. Open or create one to see export history.
      </div>
    );
  }

  if (loading && exports.length === 0) {
    return (
      <div className="flex items-center gap-2 p-4 text-[12px] text-text-muted/85">
        <RefreshCw size={12} className="animate-spin" aria-hidden="true" />
        Loading exports…
      </div>
    );
  }

  if (error && exports.length === 0) {
    return (
      <div className="space-y-2 p-3 text-[12px] text-atelier-failed">
        <div className="flex items-center gap-1.5">
          <AlertCircle size={13} aria-hidden="true" />
          <span>Couldn’t load exports.</span>
        </div>
        <p className="text-text-muted/85">{error}</p>
        <button
          type="button"
          onClick={() => void loadExports(projectId)}
          className="inline-flex items-center gap-1 rounded border border-white/10 px-2 py-1 text-text-muted hover:bg-white/[0.06]"
        >
          <RefreshCw size={11} aria-hidden="true" />
          Retry
        </button>
      </div>
    );
  }

  if (exports.length === 0) {
    return (
      <div className="space-y-2 p-4 text-[12px] text-text-muted/85">
        <p className="font-medium text-foreground/85">No exports yet.</p>
        <p>
          Build a sequence in the Sequence Strip and hit{" "}
          <span className="font-mono text-atelier-brand-400/85">Export</span>.
          Successful exports land here so you can re-download, share, or
          re-run them later.
        </p>
      </div>
    );
  }

  return (
    <div
      data-onboarding-target="exports-panel"
      className="flex h-full flex-col"
    >
      <div className="flex items-center justify-between px-3 py-2">
        <span className="font-display text-[11px] uppercase tracking-[0.08em] text-text-muted/85">
          {exports.length} record{exports.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => void loadExports(projectId)}
          disabled={loading}
          className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-text-muted hover:bg-white/[0.06] hover:text-foreground disabled:opacity-50"
          aria-label="Refresh"
          data-tip="Refresh"
        >
          <RefreshCw
            size={11}
            className={loading ? "animate-spin" : ""}
            aria-hidden="true"
          />
        </button>
      </div>
      <ul className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {exports.map((record) => (
          <ExportRow
            key={record.id}
            record={record}
            busy={busyId === record.id}
            onCopy={() => void handleCopy(record)}
            onRedo={() => void handleRedo(record)}
            onDelete={() => void handleDelete(record)}
          />
        ))}
      </ul>
    </div>
  );
}

export default ExportsPanel;
