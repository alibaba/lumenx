"use client";
// SequenceStrip — bottom-rail thumbnail strip for the Atelier canvas.
//
// Extracted from AtelierShellV3.tsx (v0.8 / track K) so the Shell file can
// breathe and so the strip's polish work (real per-clip duration,
// indeterminate export progress, persistent file-ready chip, abortable
// export) stays isolated from other v0.8 tracks. This component owns no
// business logic — every mutation is delegated to the caller via the
// callbacks in `SequenceStripProps`. Visual / interaction state that's
// purely local to the strip (per-clip duration cache, last-export chip,
// in-flight AbortController) lives in here.
//
// Behavior preserved verbatim from the pre-extraction inline JSX:
// - Drop target accepts `application/x-atelier-take` (single object OR
//   array batch from buildBatchPayload).
// - Drag-to-reorder via HTML5 drag using the parent-supplied
//   `seqDragFromIndex` / `seqDragOverIndex` state.
// - Per-thumb 250 ms hover-dwell autoplay (mouseenter), pause+reset on
//   leave; click → preview overlay.
// - Trim popover with In/Out seconds, Reset, Done.
//
// New in v0.8 (K):
// - Per-clip duration captured via `onLoadedMetadata` and cached in a
//   `Map<candidateId, number>` ref; trim popover input bounds and the
//   amber trim-range bar use the real duration when known. A small mono
//   duration badge shows in the bottom gradient bar.
// - Export flow becomes tri-state: Idle → In-flight (Cancel/spinner +
//   indeterminate progress bar under the header) → Cooldown (persistent
//   inline chip beside Export with filename / size / Download / × OR
//   the error chip with Retry / ×).
// - AbortController wired through `api.exportAtelierSequence`. Aborting
//   surfaces a neutral chip "Export canceled".
// - Better error mapping for axios errors (uses response.data.detail
//   when present instead of the raw Error.message).
import * as React from "react";
import { Play, Scissors, X } from "lucide-react";
import axios from "axios";
import { api } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

/** Sequence entry shape — matches the SequenceEntry type used in the Shell. */
export type SequenceEntry = {
  parentId: string;
  candidateId: string;
  trimStart?: number;
  trimEnd?: number;
};

/** Joined view used to render each thumbnail. The Shell precomputes this
 *  via `sequenceEntries = useMemo(...)` to filter stale entries.
 *
 *  Kept narrow (only the fields the strip reads) rather than importing
 *  AtelierNode / AtelierVideoCandidate, so the strip stays decoupled
 *  from api.ts. Callers can pass their full domain objects — TS
 *  structural subtyping makes the wider shape assignable to the
 *  narrower view. */
export interface SequenceEntryViewParent {
  id: string;
  title?: string;
}
export interface SequenceEntryViewCandidate {
  id: string;
  video_url?: string | null;
  status?: string;
}
export type SequenceEntryView = {
  entry: SequenceEntry;
  parent: SequenceEntryViewParent;
  cand: SequenceEntryViewCandidate;
};

/** Persistent chip surfaced after an export completes (success, error,
 *  or canceled). Lives in this component so the chip survives strip
 *  remounts when the user toggles `sequenceVisible`. */
type ExportChip =
  | { kind: "success"; videoUrl: string; filename: string; sizeMb: number; clipCount: number }
  | { kind: "error"; message: string }
  | { kind: "canceled" };

export interface SequenceStripProps {
  projectId: string | null | undefined;
  visible: boolean;
  agentCollapsed: boolean;

  /** Pre-joined entries (filtered against current candidates). */
  sequenceEntries: SequenceEntryView[];
  /** Raw sequence (used to build the export payload). */
  sequence: SequenceEntry[];

  // Drag-to-reorder state — lifted in the Shell since other systems read it.
  seqDragFromIndex: number | null;
  seqDragOverIndex: number | null;
  setSeqDragFromIndex: (n: number | null) => void;
  setSeqDragOverIndex: (n: number | null | ((prev: number | null) => number | null)) => void;
  seqDropActive: boolean;
  setSeqDropActive: (active: boolean) => void;

  // Trim popover ownership — same reasoning as drag indices: the Shell may
  // close the popover when the user clicks elsewhere.
  trimEditingIndex: number | null;
  setTrimEditingIndex: (n: number | null | ((prev: number | null) => number | null)) => void;

  /** Mutate the underlying sequence state. */
  onSequenceChange: (next: SequenceEntry[] | ((prev: SequenceEntry[]) => SequenceEntry[])) => void;
  /** Open the preview overlay. */
  onPreview: (videoUrl: string, parentId: string, candidateId: string) => void;
  /** Toast helper — reuses the Shell's queue so cross-component nudges
   *  stack consistently. */
  pushToast: (kind: "info" | "error" | "success", text: string) => void;

  /** Empty-state slot. Defaults to the standard "Drag a completed take here…"
   *  copy; track M can swap it for a CTA pointing at the curated library. */
  emptyState?: React.ReactNode;
}

/** Default empty state — preserved verbatim from the pre-extraction copy. */
function DefaultEmptyState(): React.JSX.Element {
  return (
    <div className="px-2 py-2 text-[11px] text-text-muted/85">
      Drag a completed take here, or use{" "}
      <span className="text-[11px] text-white/50">Add to sequence</span>{" "}
      from its action bar.
    </div>
  );
}

/** Map an unknown export error to a user-readable string.
 *  - Axios cancellation → handled by the caller (surfaces as "canceled").
 *  - Axios response with `detail` → use the FastAPI HTTPException detail.
 *  - Plain Error → use .message.
 *  - Anything else → String(). */
function extractExportErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { detail?: unknown } | undefined;
    const detail = data?.detail;
    if (typeof detail === "string" && detail.length > 0) return detail;
    if (err.message) return err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export function SequenceStrip(props: SequenceStripProps): React.JSX.Element | null {
  const {
    projectId,
    visible,
    agentCollapsed,
    sequenceEntries,
    sequence,
    seqDragFromIndex,
    seqDragOverIndex,
    setSeqDragFromIndex,
    setSeqDragOverIndex,
    seqDropActive,
    setSeqDropActive,
    trimEditingIndex,
    setTrimEditingIndex,
    onSequenceChange,
    onPreview,
    pushToast,
    emptyState,
  } = props;

  // Per-candidate duration cache. Captured from each <video>'s
  // onLoadedMetadata; survives re-renders so we don't re-measure when the
  // user reorders or toggles the strip.
  const durationsRef = React.useRef<Map<string, number>>(new Map());
  // Bumped each time we record a new duration so consumers (badge, trim
  // bar, trim popover) re-render. We deliberately don't store the Map in
  // state — that'd churn React on every metadata callback.
  const [durationsVersion, setDurationsVersion] = React.useState(0);
  const recordDuration = React.useCallback((candidateId: string, dur: number) => {
    if (!Number.isFinite(dur) || dur <= 0) return;
    const prev = durationsRef.current.get(candidateId);
    if (typeof prev === "number" && Math.abs(prev - dur) < 0.05) return;
    durationsRef.current.set(candidateId, dur);
    setDurationsVersion((v) => v + 1);
  }, []);
  /** Best known duration in seconds. Fallback `5` matches the pre-K
   *  hardcoded value so the amber trim bar still renders before metadata
   *  loads. */
  const getDuration = React.useCallback(
    (candidateId: string): number => durationsRef.current.get(candidateId) ?? 5,
    // durationsVersion intentionally part of deps so callers re-derive
    // when a new duration lands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [durationsVersion],
  );

  // Export flow state. We keep `exporting` (boolean) + `exportChip`
  // (persistent post-export surface) in here rather than in the Shell so
  // the chip survives a remount when `sequenceVisible` toggles off/on.
  const [exporting, setExporting] = React.useState(false);
  const [exportChip, setExportChip] = React.useState<ExportChip | null>(null);
  // Track O (v0.9): determinate progress for the in-flight bar. `null`
  // means "no progress event has landed yet" → render the indeterminate
  // shimmer (matches the `pending` state before the first poll). Any
  // numeric value → switch to the determinate fill so the user sees the
  // bar advance through 0-100%.
  const [exportProgress, setExportProgress] = React.useState<number | null>(null);
  const abortRef = React.useRef<AbortController | null>(null);
  // Snapshot the last payload so the "Retry" affordance on the error
  // chip can re-issue the same export.
  const lastPayloadRef = React.useRef<SequenceEntry[] | null>(null);

  /** Reset the chip whenever the project switches — chips from project A
   *  shouldn't bleed into project B. */
  React.useEffect(() => {
    setExportChip(null);
    abortRef.current?.abort();
    abortRef.current = null;
    setExporting(false);
    setExportProgress(null);
    // lastPayloadRef is wiped because the entries belong to the previous
    // project; Retry would point at stale candidates.
    lastPayloadRef.current = null;
  }, [projectId]);

  const buildPayload = React.useCallback((): SequenceEntry[] => {
    return sequence.map((s) => {
      const out: SequenceEntry = { parentId: s.parentId, candidateId: s.candidateId };
      if (typeof s.trimStart === "number") out.trimStart = s.trimStart;
      if (typeof s.trimEnd === "number") out.trimEnd = s.trimEnd;
      return out;
    });
  }, [sequence]);

  const runExport = React.useCallback(
    (payload: SequenceEntry[]) => {
      if (!projectId) return;
      if (payload.length === 0) return;
      // Wipe any prior chip — the in-flight progress bar is the live
      // signal now; the chip only re-appears at completion.
      setExportChip(null);
      // Reset to `null` so the determinate fill restarts at the
      // indeterminate shimmer until the first GET /sequence/export
      // poll lands.
      setExportProgress(null);
      lastPayloadRef.current = payload;
      const controller = new AbortController();
      abortRef.current = controller;
      setExporting(true);
      void api
        .exportAtelierSequence(projectId, payload, {
          signal: controller.signal,
          // Track O (v0.9): each GET .../sequence/export/{job_id}
          // poll feeds the determinate progress bar. Wrap in
          // setExportProgress so React batches updates without
          // double-rendering.
          onProgress: (pct) => {
            setExportProgress((prev) => {
              if (prev !== null && pct < prev) return prev; // never regress
              return pct;
            });
          },
        })
        .then((res) => {
          setExportChip({
            kind: "success",
            videoUrl: res.video_url,
            filename: res.filename,
            sizeMb: res.size_mb,
            clipCount: res.clip_count,
          });
          pushToast(
            "success",
            `Exported ${res.clip_count} clip${res.clip_count === 1 ? "" : "s"} · ${res.size_mb} MB`,
          );
        })
        .catch((err: unknown) => {
          // Cancellation is a deliberate user action, not an error.
          if (axios.isCancel(err) || (err instanceof Error && err.name === "CanceledError")) {
            setExportChip({ kind: "canceled" });
            return;
          }
          const message = extractExportErrorMessage(err);
          setExportChip({ kind: "error", message });
          pushToast("error", `Export failed: ${message}`);
        })
        .finally(() => {
          setExporting(false);
          setExportProgress(null);
          // Only clear the controller if it's still the one we created
          // for this export — protects against a stale abort wiping a
          // newer in-flight call started during the .then handler.
          if (abortRef.current === controller) {
            abortRef.current = null;
          }
        });
    },
    [projectId, pushToast],
  );

  const handleExportClick = React.useCallback(() => {
    if (!projectId || exporting) return;
    const payload = buildPayload();
    runExport(payload);
  }, [projectId, exporting, buildPayload, runExport]);

  const handleCancelClick = React.useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleRetryClick = React.useCallback(() => {
    if (exporting) return;
    const payload = lastPayloadRef.current ?? buildPayload();
    runExport(payload);
  }, [exporting, buildPayload, runExport]);

  const handleDismissChip = React.useCallback(() => setExportChip(null), []);

  // Tear down the in-flight export when the strip unmounts (user toggled
  // the LeftRail Sequence button mid-export).
  React.useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  if (!visible) return null;

  return (
    <div
      className={`absolute bottom-16 left-[280px] z-20 p-2.5 transition-colors animate-atelier-popover-in motion-reduce:animate-none ${
        seqDropActive ? "rounded-2xl ring-2 ring-atelier-brand-400/35" : ""
      }`}
      style={{ right: agentCollapsed ? 88 : 412 }}
      onDragEnter={(e) => {
        if (!Array.from(e.dataTransfer.types).includes("application/x-atelier-take")) return;
        e.preventDefault();
        setSeqDropActive(true);
      }}
      onDragOver={(e) => {
        if (!Array.from(e.dataTransfer.types).includes("application/x-atelier-take")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(e) => {
        // Only clear when leaving for somewhere outside the strip.
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setSeqDropActive(false);
      }}
      onDrop={(e) => {
        const data = e.dataTransfer.getData("application/x-atelier-take");
        setSeqDropActive(false);
        if (!data) return;
        e.preventDefault();
        try {
          const parsed = JSON.parse(data);
          const batch: Array<{ parentId: string; candidateId: string }> =
            Array.isArray(parsed)
              ? parsed
              : parsed && typeof parsed === "object"
                ? [parsed]
                : [];
          if (batch.length === 0) return;
          let added = 0;
          let skipped = 0;
          onSequenceChange((prev) => {
            const next = [...prev];
            for (const item of batch) {
              if (!item || typeof item.parentId !== "string" || typeof item.candidateId !== "string") {
                continue;
              }
              if (next.some((s) => s.parentId === item.parentId && s.candidateId === item.candidateId)) {
                skipped += 1;
                continue;
              }
              next.push({ parentId: item.parentId, candidateId: item.candidateId });
              added += 1;
            }
            return next;
          });
          if (added === 0 && skipped > 0) {
            pushToast("info", "Already in sequence");
          } else if (added > 0 && skipped > 0) {
            pushToast(
              "success",
              `Added ${added} · skipped ${skipped} duplicate${skipped === 1 ? "" : "s"}`,
            );
          } else if (added === 1) {
            pushToast("success", "Added to sequence");
          } else if (added > 1) {
            pushToast("success", `Added ${added} clips`);
          }
        } catch {
          /* Malformed payload — silently ignore. */
        }
      }}
    >
      {/* Header row — brand dot + "Sequence" + clip count, plus Clear /
          Export / chip area on the right. The indeterminate shimmer sits
          along the bottom edge of the header when exporting. */}
      <div className="relative mb-2 flex items-center justify-between gap-2 border-b border-white/8 px-1 pb-1.5 text-[11px] text-white/45">
        <div className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-[5px] w-[5px] rounded-full bg-atelier-brand-400/70" />
          <span>Sequence</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span aria-hidden="true" className="text-white/35">
            {sequenceEntries.length === 1 ? "clip" : "clips"}
          </span>
          <span className="font-display text-[11px] tabular-nums tracking-tight text-foreground/95">
            {String(sequenceEntries.length).padStart(2, "0")}
          </span>
          {sequenceEntries.length > 0 ? (
            <>
              <button
                onClick={() => onSequenceChange([])}
                disabled={exporting}
                className="ml-1 rounded px-1.5 py-0.5 tracking-[0.24em] text-text-muted/70 transition-colors hover:bg-white/[0.06] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              >
                Clear
              </button>
              {exporting ? (
                // In-flight: a Cancel button. We render the Cancel +
                // spinner together as a compact pair so the export
                // button slot has stable width during the swap.
                <button
                  onClick={handleCancelClick}
                  className="ml-1 inline-flex items-center gap-1 rounded bg-white/[0.06] px-2 py-0.5 text-[11px] font-medium text-foreground/85 transition-colors hover:bg-white/[0.1]"
                  aria-label="Cancel export"
                >
                  <span
                    aria-hidden="true"
                    className="inline-block h-2.5 w-2.5 animate-spin rounded-full border border-white/40 border-t-transparent motion-reduce:animate-none"
                  />
                  <span>Cancel</span>
                </button>
              ) : (
                <button
                  onClick={handleExportClick}
                  disabled={!projectId}
                  className="ml-1 rounded bg-atelier-brand-400/15 px-2 py-0.5 text-[11px] font-medium text-atelier-brand-400/95 transition-colors hover:bg-atelier-brand-400/25 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Export
                </button>
              )}
            </>
          ) : null}
          {exportChip ? (
            <ExportChipView chip={exportChip} onRetry={handleRetryClick} onDismiss={handleDismissChip} />
          ) : null}
        </div>
        {/* Track O (v0.9): the export route is now an async job with
            per-clip progress updates streaming over /sequence/export/
            {job_id}. We render an indeterminate shimmer while
            exportProgress is null (the `pending` window before the
            first poll lands) and switch to a determinate fill once a
            numeric value arrives. */}
        {exporting ? (
          exportProgress === null ? (
            <span
              role="progressbar"
              aria-busy="true"
              aria-label="Exporting sequence"
              className="pointer-events-none absolute -bottom-px left-0 right-0 h-[2px] overflow-hidden bg-white/5"
            >
              <span
                aria-hidden="true"
                className="block h-full w-1/3 animate-atelier-progress-indeterminate bg-atelier-brand-400/70 motion-reduce:animate-none motion-reduce:w-full motion-reduce:opacity-70"
              />
            </span>
          ) : (
            <span
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.max(0, Math.min(100, Math.round(exportProgress)))}
              aria-label="Exporting sequence"
              className="pointer-events-none absolute -bottom-px left-0 right-0 h-[2px] overflow-hidden bg-white/5"
            >
              <span
                aria-hidden="true"
                className="block h-full bg-atelier-brand-400/85 transition-[width] duration-300 ease-out"
                style={{ width: `${Math.max(0, Math.min(100, exportProgress))}%` }}
              />
            </span>
          )
        ) : null}
      </div>

      {sequenceEntries.length === 0 ? (
        emptyState ?? <DefaultEmptyState />
      ) : (
        <div className="flex items-center gap-2 overflow-x-auto">
          {sequenceEntries.map(({ entry, parent, cand }, i) => {
            const dur = getDuration(cand.id);
            const trimmed =
              typeof entry.trimStart === "number" || typeof entry.trimEnd === "number";
            const titleText =
              typeof parent.title === "string" && parent.title.length > 0
                ? parent.title
                : "Untitled";
            const knownDuration = durationsRef.current.has(cand.id);
            const durLabel = knownDuration ? `${dur.toFixed(1)}s` : null;
            return (
              <button
                key={`${entry.parentId}-${entry.candidateId}`}
                type="button"
                draggable
                onDragStart={(e) => {
                  setSeqDragFromIndex(i);
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", String(i));
                }}
                onDragOver={(e) => {
                  if (seqDragFromIndex === null) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  if (seqDragOverIndex !== i) setSeqDragOverIndex(i);
                }}
                onDragLeave={(e) => {
                  if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                  setSeqDragOverIndex((prev) => (prev === i ? null : prev));
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const from = seqDragFromIndex;
                  const to = i;
                  setSeqDragFromIndex(null);
                  setSeqDragOverIndex(null);
                  if (from === null || from === to) return;
                  onSequenceChange((prev) => {
                    const next = prev.slice();
                    const [moved] = next.splice(from, 1);
                    next.splice(to, 0, moved);
                    return next;
                  });
                }}
                onDragEnd={() => {
                  setSeqDragFromIndex(null);
                  setSeqDragOverIndex(null);
                }}
                onClick={() => {
                  if (cand.video_url) onPreview(cand.video_url, parent.id, cand.id);
                }}
                onMouseEnter={(e) => {
                  const v = e.currentTarget.querySelector("video");
                  if (!v) return;
                  window.setTimeout(() => {
                    v.play().catch(() => {/* autoplay may be blocked */});
                  }, 250);
                }}
                onMouseLeave={(e) => {
                  const v = e.currentTarget.querySelector("video");
                  if (!v) return;
                  v.pause();
                  try { v.currentTime = 0; } catch { /* ignore */ }
                }}
                className={`group relative h-[68px] w-[124px] shrink-0 cursor-grab overflow-hidden rounded-[5px] border transition-shadow hover:border-atelier-brand-400/45 hover:shadow-[0_0_0_1px_rgba(59,107,255,0.22)] active:cursor-grabbing ${
                  seqDragFromIndex === i
                    ? "opacity-45 border-atelier-brand-400/55"
                    : seqDragOverIndex === i && seqDragFromIndex !== null && seqDragFromIndex !== i
                    ? "border-atelier-brand-400 ring-2 ring-atelier-brand-400/35"
                    : "border-white/8 bg-[#141416]"
                }`}
                aria-label={`Play ${titleText}, clip ${i + 1}`}
              >
                {cand.video_url ? (
                  <video
                    src={getAssetUrl(cand.video_url)}
                    muted
                    loop
                    playsInline
                    preload="metadata"
                    onLoadedMetadata={(e) => recordDuration(cand.id, e.currentTarget.duration)}
                    aria-label={`${titleText} thumbnail`}
                    className="h-full w-full object-cover"
                  />
                ) : null}
                <span className="pointer-events-none absolute inset-0 m-auto grid h-7 w-7 place-items-center rounded-full bg-black/65 text-white/95 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
                  <Play size={11} aria-hidden="true" />
                </span>
                <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-gradient-to-t from-black/85 via-black/55 to-transparent px-1.5 pb-1 pt-2.5">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span className="truncate text-[10px] text-foreground/95">{titleText}</span>
                    {durLabel ? (
                      <span
                        className="shrink-0 font-mono text-[8.5px] tabular-nums tracking-tight text-white/55"
                        aria-label={`Duration ${durLabel}`}
                      >
                        {durLabel}
                      </span>
                    ) : null}
                  </span>
                  {trimmed ? (
                    <span
                      aria-label="Trim applied"
                      className="font-mono text-[8.5px] tracking-tight text-amber-200/95"
                      data-tip={`Trim ${entry.trimStart ?? 0}s–${entry.trimEnd ?? "end"}s`}
                    >
                      ✁ {Number.isFinite(entry.trimStart ?? NaN) ? (entry.trimStart as number).toFixed(1) : "0.0"}-
                      {Number.isFinite(entry.trimEnd ?? NaN) ? (entry.trimEnd as number).toFixed(1) : "end"}
                    </span>
                  ) : (
                    <span className="font-mono text-[9px] tracking-tight text-text-muted">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                  )}
                </div>
                {/* Amber trim-range bar across the bottom — uses the real
                    duration once metadata is in, falling back to 5s
                    before that. */}
                {trimmed ? (() => {
                  const a = Math.max(0, Math.min(dur, entry.trimStart ?? 0));
                  const b = Math.max(a, Math.min(dur, entry.trimEnd ?? dur));
                  const leftPct = (a / dur) * 100;
                  const widthPct = ((b - a) / dur) * 100;
                  return (
                    <span
                      aria-hidden="true"
                      className="pointer-events-none absolute bottom-0 left-0 right-0 h-[3px] bg-black/35"
                    >
                      <span
                        className="block h-full bg-amber-300/85"
                        style={{ marginLeft: `${leftPct}%`, width: `${widthPct}%` }}
                      />
                    </span>
                  );
                })() : null}
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    setTrimEditingIndex((cur) => (cur === i ? null : i));
                  }}
                  className={`btn-tip absolute left-1 top-1 grid h-4 w-4 place-items-center rounded-full bg-black/65 transition-colors ${
                    trimEditingIndex === i
                      ? "text-amber-200 opacity-100"
                      : trimmed
                      ? "text-amber-200/95 opacity-100"
                      : "text-white/85 opacity-0 hover:bg-amber-400/45 group-hover:opacity-100"
                  }`}
                  aria-label={`Trim clip ${i + 1}`}
                  data-tip="Trim · in / out"
                >
                  <Scissors size={9} aria-hidden="true" />
                </span>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSequenceChange((prev) =>
                      prev.filter(
                        (s) => !(s.parentId === entry.parentId && s.candidateId === entry.candidateId),
                      ),
                    );
                  }}
                  className="absolute right-1 top-1 grid h-4 w-4 place-items-center rounded-full bg-black/65 text-white/85 opacity-0 transition-colors hover:bg-red-500/75 group-hover:opacity-100"
                  aria-label={`Remove ${titleText} from sequence`}
                >
                  <X size={9} aria-hidden="true" />
                </span>
                {trimEditingIndex === i ? (
                  <div
                    role="dialog"
                    aria-label="Trim clip"
                    onClick={(e) => e.stopPropagation()}
                    className="absolute -top-[120px] left-1/2 z-30 w-[200px] -translate-x-1/2 rounded-md border border-white/8 bg-[#141416]/96 p-2 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
                  >
                    <div className="mb-1.5 flex items-center justify-between text-[11px] text-white/55">
                      <span>
                        Trim · clip {i + 1}
                        {durLabel ? <span className="ml-1 font-mono text-text-muted/70">({durLabel})</span> : null}
                      </span>
                      <button
                        type="button"
                        onClick={() => setTrimEditingIndex(null)}
                        className="rounded px-1 hover:bg-hover-bg hover:text-foreground"
                        aria-label="Close trim editor"
                      >
                        <X size={10} aria-hidden="true" />
                      </button>
                    </div>
                    <label className="mb-1 block text-[10px] text-text-secondary">
                      In <span className="font-mono text-text-muted/85">(sec)</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0}
                        max={knownDuration ? dur : undefined}
                        defaultValue={entry.trimStart ?? 0}
                        onChange={(e) => {
                          const v = parseFloat(e.target.value);
                          onSequenceChange((prev) =>
                            prev.map((s, idx) =>
                              idx === i
                                ? { ...s, trimStart: Number.isFinite(v) ? v : undefined }
                                : s,
                            ),
                          );
                        }}
                        className="mt-0.5 w-full rounded border border-white/10 bg-black/40 px-1.5 py-1 font-mono text-[11px] text-foreground outline-none focus:border-atelier-brand-400/60"
                      />
                    </label>
                    <label className="mb-1 block text-[10px] text-text-secondary">
                      Out <span className="font-mono text-text-muted/85">(sec)</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0}
                        max={knownDuration ? dur : undefined}
                        defaultValue={entry.trimEnd ?? ""}
                        placeholder={knownDuration ? dur.toFixed(1) : "end"}
                        onChange={(e) => {
                          const raw = e.target.value;
                          const v = parseFloat(raw);
                          onSequenceChange((prev) =>
                            prev.map((s, idx) =>
                              idx === i
                                ? {
                                    ...s,
                                    trimEnd:
                                      raw === "" || !Number.isFinite(v) ? undefined : v,
                                  }
                                : s,
                            ),
                          );
                        }}
                        className="mt-0.5 w-full rounded border border-white/10 bg-black/40 px-1.5 py-1 font-mono text-[11px] text-foreground outline-none focus:border-atelier-brand-400/60"
                      />
                    </label>
                    <div className="mt-1.5 flex items-center justify-between gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          onSequenceChange((prev) =>
                            prev.map((s, idx) =>
                              idx === i
                                ? { parentId: s.parentId, candidateId: s.candidateId }
                                : s,
                            ),
                          );
                        }}
                        className="rounded-full px-2 py-[3px] text-[11px] text-white/55 transition-colors hover:bg-hover-bg hover:text-foreground"
                      >
                        Reset
                      </button>
                      <button
                        type="button"
                        onClick={() => setTrimEditingIndex(null)}
                        className="rounded-full bg-atelier-brand-400/15 px-2.5 py-[3px] text-[11px] font-medium text-atelier-brand-400 transition-colors hover:bg-atelier-brand-400/25"
                      >
                        Done
                      </button>
                    </div>
                  </div>
                ) : null}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** ──────────────────────────────────────────────────────────────────────
 *  ExportChipView — the persistent post-export chip beside Export.
 *  Three flavors: success (filename / size / Download / ×), error
 *  (message / Retry / ×), canceled (neutral text / ×). Kept inline so
 *  the strip stays a single-file extraction.
 *  ────────────────────────────────────────────────────────────────────── */
function ExportChipView(props: {
  chip: ExportChip;
  onRetry: () => void;
  onDismiss: () => void;
}): React.JSX.Element {
  const { chip, onRetry, onDismiss } = props;
  const baseClass =
    "ml-1 inline-flex max-w-[320px] items-center gap-1.5 rounded-full px-2 py-[3px] text-[10.5px] font-medium";
  if (chip.kind === "success") {
    return (
      <span
        className={`${baseClass} bg-emerald-400/10 text-emerald-200/95`}
        role="status"
        aria-live="polite"
      >
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-emerald-300" />
        <span className="truncate" title={chip.filename}>{chip.filename}</span>
        <span className="font-mono tabular-nums text-emerald-200/65">
          · {chip.sizeMb} MB
        </span>
        <a
          href={getAssetUrl(chip.videoUrl)}
          download={chip.filename}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-1 rounded-full px-1.5 py-px text-emerald-100 underline-offset-2 transition-colors hover:bg-emerald-400/20 hover:underline"
        >
          Download
        </a>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss export chip"
          className="-mr-0.5 grid h-3.5 w-3.5 place-items-center rounded-full text-emerald-200/70 hover:bg-emerald-400/20 hover:text-emerald-50"
        >
          <X size={9} aria-hidden="true" />
        </button>
      </span>
    );
  }
  if (chip.kind === "error") {
    return (
      <span
        className={`${baseClass} bg-red-500/15 text-red-200/95`}
        role="alert"
      >
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-red-400" />
        <span className="truncate" title={chip.message}>
          Export failed · {chip.message}
        </span>
        <button
          type="button"
          onClick={onRetry}
          className="ml-1 rounded-full px-1.5 py-px text-red-100 transition-colors hover:bg-red-500/25 hover:underline"
        >
          Retry
        </button>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss error chip"
          className="-mr-0.5 grid h-3.5 w-3.5 place-items-center rounded-full text-red-200/70 hover:bg-red-500/25 hover:text-red-50"
        >
          <X size={9} aria-hidden="true" />
        </button>
      </span>
    );
  }
  // canceled
  return (
    <span
      className={`${baseClass} bg-white/[0.07] text-text-muted`}
      role="status"
      aria-live="polite"
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-white/45" />
      <span>Export canceled</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss canceled chip"
        className="-mr-0.5 grid h-3.5 w-3.5 place-items-center rounded-full text-text-muted hover:bg-white/[0.1] hover:text-foreground"
      >
        <X size={9} aria-hidden="true" />
      </button>
    </span>
  );
}
