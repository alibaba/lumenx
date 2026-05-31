"use client";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Bookmark, Check, ChevronDown, Copy, Download, ImageIcon, Loader2, Maximize2, Play, RotateCw, Sparkles, Upload, Video, Volume2 } from "lucide-react";
import type { MediaKind } from "@/components/atelier/v3/types";
import { PortDot } from "./NodePort";
import { DiagnoseModal } from "@/components/shared/PendingTaskAffordance";

interface Props {
  id: string;
  kind: MediaKind;
  src?: string;
  filename?: string;
  duration?: string;
  status: "draft" | "pending" | "processing" | "completed" | "failed";
  progress?: number;
  etaSeconds?: number;
  errorMessage?: string;
  selected?: boolean;
  selectedAsTake?: boolean;
  x: number;
  y: number;
  width?: number;
  height?: number;
  onSelect?: (id: string) => void;
  /** When provided on a failed node, an inline Retry button is shown. */
  onRetry?: (id: string) => void;
  /** Optional list of model display names the failed take can be re-run
   *  with. When non-empty, the Retry button gets a chevron that opens a
   *  small popup; picking a model fires `onRetryWithModel(id, model)`.
   *  The list is the user's choice — caller decides whether to include
   *  the original model, all i2v candidates, or anything else. */
  retryModelOptions?: string[];
  onRetryWithModel?: (id: string, modelLabel: string) => void;
  /** When provided on an empty image node, the placeholder becomes a
   *  unified actionable card with Upload + Generate buttons baked into the
   *  same bordered box (no separate floating overlay). */
  onUpload?: (id: string) => void;
  onGenerate?: (id: string) => void;
  /** Cancel callback for pending/processing tasks. When provided, after a
   *  soft stuck threshold the overlay surfaces a Cancel button next to
   *  the spinner. Caller is responsible for hitting the backend cancel
   *  endpoint and refreshing local state. */
  onCancel?: (id: string) => Promise<void> | void;
}

const DEFAULT_SIZE: Record<MediaKind, { w: number; h: number }> = {
  image: { w: 180, h: 180 },
  video: { w: 200, h: 113 },
  audio: { w: 200, h: 56 },
};

const MAX_WIDTH = 240;

function ringClass(
  status: Props["status"],
  selected: boolean | undefined,
  selectedAsTake: boolean | undefined,
): string {
  if (selected || selectedAsTake) return "ring-2 ring-atelier-brand-400";
  if (status === "processing" || status === "pending") return "ring-1 ring-atelier-processing/60";
  if (status === "failed") return "ring-1 ring-atelier-failed/60";
  return "";
}

function TypeChip({ kind }: { kind: MediaKind }) {
  const Icon = kind === "video" ? Video : kind === "audio" ? Volume2 : ImageIcon;
  // Quiet frosted caption — sentence-case Inter on a soft glass pill, no
  // mono-caps (§9.1). Reads as a gentle hint, not a terminal stamp.
  const label = kind === "image" ? "image" : kind === "video" ? "video" : "audio";
  return (
    <span className="pointer-events-none absolute left-2 top-2 hidden items-center gap-1 rounded-md border border-white/8 bg-black/45 px-1.5 py-1 text-[11px] text-white/45 lowercase backdrop-blur-md group-hover:inline-flex">
      <Icon size={10} aria-hidden="true" className="text-white/40" />
      {label}
    </span>
  );
}

// Compound retry control — small "Retry" button with an optional
// chevron that opens a popup of alternate models. The popup lists every
// option the caller passed; we don't filter or dedupe on the original
// model because the user explicitly asked for that to be a choice
// ("retry with same model" is a valid pick).
function RetryButton({
  id,
  errorMessage,
  onRetry,
  retryModelOptions,
  onRetryWithModel,
}: {
  id: string;
  errorMessage?: string;
  onRetry?: (id: string) => void;
  retryModelOptions?: string[];
  onRetryWithModel?: (id: string, modelLabel: string) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    const id2 = requestAnimationFrame(() => window.addEventListener("mousedown", onDown));
    return () => {
      cancelAnimationFrame(id2);
      window.removeEventListener("mousedown", onDown);
    };
  }, [pickerOpen]);

  const hasOptions = !!retryModelOptions && retryModelOptions.length > 0 && !!onRetryWithModel;
  if (!onRetry && !hasOptions) return null;

  return (
    <div ref={wrapperRef} className="relative inline-flex items-center gap-px">
      {onRetry ? (
        <button
          type="button"
          aria-label="Retry generation"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onRetry(id);
          }}
          className="btn-tip mt-0.5 inline-flex items-center gap-1 rounded-full bg-atelier-failed/25 px-2 py-0.5 text-[10px] font-semibold text-foreground hover:bg-atelier-failed/40"
          data-tip={errorMessage || "Retry with same model"}
        >
          <RotateCw size={10} aria-hidden="true" /> Retry
        </button>
      ) : null}
      {hasOptions ? (
        <button
          type="button"
          aria-label="Retry with a different model"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            setPickerOpen((v) => !v);
          }}
          className="btn-tip mt-0.5 inline-flex items-center justify-center rounded-full bg-atelier-failed/25 px-1.5 py-[3px] text-foreground hover:bg-atelier-failed/40"
          data-tip="Pick a different model"
        >
          <ChevronDown size={10} aria-hidden="true" />
        </button>
      ) : null}
      {pickerOpen && hasOptions ? (
        <div
          role="menu"
          aria-label="Retry with model"
          className="absolute right-0 top-full z-30 mt-1 w-[180px] origin-top overflow-hidden rounded-md border border-white/8 bg-[#141416]/96 p-1 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
        >
          <div className="border-b border-white/8 px-2 py-1.5 text-[11px] text-white/45">
            Retry with
          </div>
          <div className="max-h-[180px] overflow-y-auto p-1">
            {retryModelOptions!.map((opt) => (
              <button
                key={opt}
                type="button"
                role="menuitem"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  setPickerOpen(false);
                  onRetryWithModel!(id, opt);
                }}
                className="block w-full rounded px-2 py-1 text-left text-[11.5px] text-text-secondary transition-colors hover:bg-white/[0.05] hover:text-foreground"
              >
                {opt}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AudioWaveform() {
  const bars = Array.from({ length: 28 }, (_, i) => 10 + Math.abs(Math.sin(i * 0.7)) * 22);
  return (
    <div className="flex h-full w-full items-center gap-2 px-2">
      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-white/15 text-white/85">
        <Play size={12} />
      </span>
      <div className="flex flex-1 items-center gap-[2px]">
        {bars.map((h, i) => (
          <span
            key={i}
            className="w-[2px] bg-white/35"
            style={{ height: `${h}px` }}
          />
        ))}
      </div>
    </div>
  );
}

export function MediaNode({
  id,
  kind,
  src,
  filename,
  duration,
  status,
  progress,
  etaSeconds,
  errorMessage,
  selected,
  selectedAsTake,
  x,
  y,
  width,
  height,
  onSelect,
  onRetry,
  onUpload,
  onGenerate,
  retryModelOptions,
  onRetryWithModel,
  onCancel,
}: Props) {
  const def = DEFAULT_SIZE[kind];
  const w = Math.max(40, Math.min(width ?? def.w, MAX_WIDTH));
  const h = Math.max(24, height ?? def.h);
  const ring = ringClass(status, selected, selectedAsTake);
  const showProcessing = status === "processing" || status === "pending";
  const showFailed = status === "failed";

  // Stuck-task affordances. When a candidate sits in pending/processing
  // for longer than this threshold, surface Cancel + Diagnose so the user
  // can recover instead of waiting on a frozen spinner. The component
  // itself just times the mount — actual created_at is up the tree (and
  // not always available because candidates are nested inside the parent
  // node's data), so this approximation is good enough for "is the
  // current view session waiting too long?".
  const PENDING_REVEAL_MS = 60_000;
  const [pendingMountedAt] = useState(() => Date.now());
  const [pendingNow, setPendingNow] = useState(() => Date.now());
  useEffect(() => {
    if (!showProcessing) return;
    const id = window.setInterval(() => setPendingNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [showProcessing]);
  const pendingElapsedMs = pendingNow - pendingMountedAt;
  const showStuckActions = showProcessing && pendingElapsedMs >= PENDING_REVEAL_MS;
  const [diagnoseOpen, setDiagnoseOpen] = useState(false);
  const [canceling, setCanceling] = useState(false);
  // An empty image node with upload/generate callbacks becomes a unified
  // actionable card. We hide the bare placeholder + the hover TypeChip in
  // that mode so the buttons don't compete with stacked chrome.
  const isEmptyImageActionable =
    kind === "image" && !src && !!(onUpload || onGenerate) &&
    status !== "processing" && status !== "pending" && status !== "failed";

  // Image card aspect — read off the <img>'s naturalWidth/naturalHeight
  // once it loads. Until then we render with a 4:3 fallback so the card
  // height doesn't pop on load.
  const [imageAspect, setImageAspect] = useState<number | null>(null);

  // Hover-to-preview for completed video takes — pause-on-leave + 250ms
  // dwell delay so a quick hover-pass doesn't trigger flicker. Mute + loop
  // + playsinline keep it sotto voce.
  const videoRef = useRef<HTMLVideoElement>(null);
  const enterTimerRef = useRef<number | null>(null);
  const handleMouseEnter = () => {
    if (kind !== "video" || !src || status !== "completed") return;
    if (enterTimerRef.current) window.clearTimeout(enterTimerRef.current);
    enterTimerRef.current = window.setTimeout(() => {
      const v = videoRef.current;
      if (!v) return;
      v.play().catch(() => {
        /* autoplay can be blocked; silently ignore */
      });
    }, 250);
  };
  const handleMouseLeave = () => {
    if (enterTimerRef.current) {
      window.clearTimeout(enterTimerRef.current);
      enterTimerRef.current = null;
    }
    const v = videoRef.current;
    if (v) {
      v.pause();
      try { v.currentTime = 0; } catch { /* ignore */ }
    }
  };

  // ── Preview result card (spec §2c) ─────────────────────────────────
  // Completed visual media (image / video) renders as a tall "Final
  // result" card: the media fills a clipped glass shell, an iridescent
  // bloom blooms BEHIND it (the outer is overflow-visible so the glow
  // leaks past the card edges), a bottom gradient scrim carries the
  // title + a description line, and a quiet action row sits at the foot.
  if (status === "completed" && src && (kind === "image" || kind === "video")) {
    const previewAspect =
      kind === "video"
        ? 16 / 9
        : imageAspect && imageAspect > 0
          ? Math.max(0.6, Math.min(2.4, imageAspect))
          : 4 / 3;
    const resultDescription =
      filename ||
      [kind === "video" ? "Video" : "Image", duration].filter(Boolean).join(" · ");
    const previewRing = selected || selectedAsTake ? "ring-2 ring-atelier-brand-400" : "";
    const resultActions = [
      { key: "expand", Icon: Maximize2, label: "Expand" },
      { key: "bookmark", Icon: Bookmark, label: "Bookmark" },
      { key: "copy", Icon: Copy, label: "Copy" },
      { key: "refresh", Icon: RotateCw, label: "Refresh" },
    ];
    return (
      <div
        style={{ transform: `translate(${x}px, ${y}px)` }}
        className="atelier-bloom atelier-bloom-secondary absolute w-[260px] overflow-visible rounded-[20px]"
      >
        <div
          role="button"
          tabIndex={0}
          onPointerDown={(event) => {
            event.stopPropagation();
            onSelect?.(id);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onSelect?.(id);
            }
          }}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          className={`group atelier-node-shell relative overflow-hidden transition-[box-shadow,border-color] duration-200 ease-out ${previewRing}`}
        >
          {/* Media fills the card */}
          <div className="relative w-full overflow-hidden" style={{ aspectRatio: `${previewAspect}` }}>
            {kind === "image" ? (
              <img
                src={src}
                alt={filename ?? ""}
                loading="lazy"
                decoding="async"
                onLoad={(e) => {
                  const img = e.currentTarget;
                  if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                    setImageAspect(img.naturalWidth / img.naturalHeight);
                  }
                }}
                className="absolute inset-0 h-full w-full object-cover"
              />
            ) : (
              <>
                <video
                  ref={videoRef}
                  src={src}
                  muted
                  loop
                  playsInline
                  preload="metadata"
                  aria-label={filename ?? "video preview"}
                  className="absolute inset-0 h-full w-full object-cover"
                />
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute left-1/2 top-1/2 grid h-9 w-9 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/45 text-white/95 backdrop-blur-sm transition-opacity duration-200 group-hover:opacity-0"
                >
                  <Play size={16} />
                </span>
              </>
            )}
            {/* Bottom gradient scrim — title + description sit on the media */}
            <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-1 bg-gradient-to-t from-black/85 via-black/45 to-transparent px-4 pb-3.5 pt-12">
              <span className="font-sans text-[15px] font-medium tracking-[-0.01em] leading-tight text-white">Final result</span>
              {resultDescription ? (
                <span className="truncate text-[13px] leading-[1.6] text-white/80">{resultDescription}</span>
              ) : null}
            </div>
            {selectedAsTake ? (
              <span className="pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-atelier-brand-400 px-2 py-[3px] text-[10px] font-medium text-white shadow-[0_2px_8px_-2px_rgba(0,0,0,0.6)]">
                <Check size={9} aria-hidden="true" /> selected
              </span>
            ) : null}
          </div>
          {/* Action row — small dark icon pills + a download (visual chrome) */}
          <div
            className="flex items-center justify-between gap-1.5 px-4 py-3"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-1">
              {resultActions.map((a) => (
                <button
                  key={a.key}
                  type="button"
                  aria-label={a.label}
                  data-tip={a.label}
                  onClick={(e) => e.stopPropagation()}
                  className="btn-tip grid h-7 w-7 place-items-center rounded-md text-white/55 transition-colors hover:bg-white/[0.07] hover:text-white/90"
                >
                  <a.Icon size={13} aria-hidden="true" />
                </button>
              ))}
            </div>
            <button
              type="button"
              aria-label="Download"
              data-tip="Download"
              onClick={(e) => e.stopPropagation()}
              className="btn-tip inline-flex h-7 items-center gap-1.5 rounded-md bg-white/[0.06] px-2.5 text-[11px] text-white/80 transition-colors hover:bg-white/[0.11] hover:text-white"
            >
              <Download size={13} aria-hidden="true" />
              Download
            </button>
          </div>
          {/* Output port — completed media exposes a blue port on the right
              edge so beams plug in. Card is overflow-hidden, so the dot sits
              just inside at right-1. */}
          <PortDot kind="output" className="absolute right-1 top-1/2 -translate-y-1/2 z-10" />
        </div>
      </div>
    );
  }

  // ── Image card mode ────────────────────────────────────────────────
  // When an image node has uploaded media, render the same card chrome
  // as DraftNode (244 wide, header row + thumb + tear footer) so the
  // canvas reads as a uniform set of cards rather than a mix of bare
  // thumbnails and bordered cards. Empty image drafts keep their
  // upload+generate inline UX; videos and audio stay thumbnail-only.
  if (kind === "image" && src && !isEmptyImageActionable) {
    const stampNum = id.slice(-3).toUpperCase();
    const cardBorder = selected || selectedAsTake
      ? "ring-2 ring-atelier-brand-400 border-atelier-brand-400/50"
      : status === "failed"
        ? "border-atelier-failed/45"
        : "border-glass-border";
    const cardName = filename || "Image";
    // Image's natural aspect ratio is read off the loaded <img> via
    // onLoad below. Until it's known, fall back to 4:3 so the card
    // height is stable. Clamp aspect into a reasonable range so a tall
    // portrait doesn't make the card a sliver and a wide pano doesn't
    // shrink the thumb to a hairline.
    const naturalAspect = imageAspect && imageAspect > 0
      ? Math.max(0.6, Math.min(2.4, imageAspect))
      : 4 / 3;
    return (
      <div
        role="button"
        tabIndex={0}
        onPointerDown={(event) => {
          event.stopPropagation();
          onSelect?.(id);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect?.(id);
          }
        }}
        style={{
          transform: `translate(${x}px, ${y}px)`,
        }}
        className={`group atelier-node-shell absolute w-[260px] overflow-hidden transition-[box-shadow,border-color] duration-200 ease-out ${cardBorder}`}
      >
        {/* Header row — same vocabulary as DraftNode (sparkles + display
            font title). 'IMG' caption sits at the trailing edge so the
            card is identifiable at a glance even when the thumbnail is
            similar across siblings. */}
        <div className="flex items-center gap-2 px-[18px] pb-2.5 pt-4 text-foreground">
          <ImageIcon size={12} className="shrink-0 text-white/40" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate font-sans text-[15px] font-medium tracking-[-0.01em] text-white/90">
            {cardName}
          </span>
          <span className="shrink-0 rounded-md border border-white/8 px-1.5 py-0.5 text-[11px] text-white/45">
            image
          </span>
        </div>
        {/* Thumbnail body — adapts to the source's natural aspect (read
            once on load), clamped to [0.6, 2.4] so portraits and panos
            stay readable on a 244-wide card. Fallback 4:3 while loading
            keeps the card height stable. */}
        <div className="px-[18px] pb-3">
          <div
            className="relative overflow-hidden rounded-md border border-white/8 bg-black/40"
            style={{ aspectRatio: `${naturalAspect}` }}
          >
            <img
              src={src}
              alt={filename ?? ""}
              loading="lazy"
              decoding="async"
              onLoad={(e) => {
                const img = e.currentTarget;
                if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                  setImageAspect(img.naturalWidth / img.naturalHeight);
                }
              }}
              className="absolute inset-0 h-full w-full object-cover"
            />
            {showProcessing ? (
              <div className="absolute inset-0 grid place-items-center bg-atelier-processing/[0.18] backdrop-blur-[1px]">
                <div className="flex flex-col items-center gap-1">
                  <Loader2 className="animate-spin text-atelier-processing" size={18} />
                  {typeof progress === "number" ? (
                    <span className="text-[11px] font-semibold tabular-nums text-white/90">{Math.round(progress)}%</span>
                  ) : null}
                </div>
              </div>
            ) : null}
            {showFailed ? (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-atelier-failed/[0.18] backdrop-blur-[1px]">
                <AlertTriangle size={16} className="text-atelier-failed" aria-hidden="true" />
                <span className="text-[11px] text-atelier-failed">
                  {errorMessage ? "Failed" : "Generation failed"}
                </span>
                <RetryButton
                  id={id}
                  errorMessage={errorMessage}
                  onRetry={onRetry}
                  retryModelOptions={retryModelOptions}
                  onRetryWithModel={onRetryWithModel}
                />
              </div>
            ) : null}
            {selectedAsTake ? (
              <span className="pointer-events-none absolute left-2 bottom-2 inline-flex items-center gap-1 rounded-full bg-atelier-brand-400 px-2 py-[3px] text-[10px] font-medium text-white shadow-[0_2px_8px_-2px_rgba(0,0,0,0.6)]">
                <Check size={9} aria-hidden="true" /> selected
              </span>
            ) : null}
          </div>
        </div>
        {/* Quiet footer — sentence-case label + a tiny neutral dot (§9.6),
            no mono-caps tearline. The id stamp trails as a muted reference. */}
        <div className="mt-3 flex items-center gap-2 border-t border-white/8 px-[18px] pb-3.5 pt-3">
          <span aria-hidden="true" className="h-[5px] w-[5px] shrink-0 rounded-full bg-white/30" />
          <span className="text-[11px] text-white/45">Image</span>
          <span className="ml-auto text-[11px] tabular-nums text-white/40">{stampNum}</span>
        </div>
        {/* Output port — completed image cards expose a blue port on the
            right edge so beams plug in. Card is overflow-hidden, so the dot
            sits just inside at right-1 rather than half-outside. */}
        {status === "completed" ? (
          <PortDot kind="output" className="absolute right-1 top-1/2 -translate-y-1/2 z-10" />
        ) : null}
      </div>
    );
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onPointerDown={(event) => {
        event.stopPropagation();
        onSelect?.(id);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect?.(id);
        }
      }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className={`group atelier-node-shell absolute overflow-hidden transition-[box-shadow,border-color] duration-200 ease-out ${
        // Three chrome modes:
        //   1. Actionable empty image draft → primary-tinted hairline that
        //      reads as 'this is editable, drop something here'
        //   2. Empty (non-actionable) image / video → dashed hairline so
        //      the box doesn't melt into the canvas
        //   3. Filled media → no border, image / video dominates
        //      (DESIGN.md §6.1 'default = content itself, no chrome')
        // Audio is treated like filled media because the waveform fills the
        // box on its own.
        isEmptyImageActionable
          ? selected
            ? "border border-atelier-brand-400/45"
            : "border border-dashed border-atelier-brand-400/22 hover:border-atelier-brand-400/35"
          : !src && (kind === "image" || kind === "video")
          ? "border border-dashed border-white/12"
          : ""
      } ${ring}`}
      style={{
        transform: `translate(${x}px, ${y}px)`,
        width: `${w}px`,
        height: `${h}px`,
      }}
    >
      {kind === "image" && src ? (
        <img src={src} alt={filename ?? ""} className="h-full w-full object-cover" />
      ) : null}

      {kind === "video" && src ? (
        <>
          <video
            ref={videoRef}
            src={src}
            muted
            loop
            playsInline
            preload="metadata"
            aria-label={filename ?? "video preview"}
            className="h-full w-full object-cover"
          />
          {status === "completed" ? (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-1/2 top-1/2 grid h-8 w-8 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/55 text-white/95 opacity-90 transition-opacity duration-200 group-hover:opacity-0"
            >
              <Play size={16} />
            </span>
          ) : null}
        </>
      ) : null}

      {kind === "audio" ? <AudioWaveform /> : null}

      {/* Empty image — three forms:
          1. Actionable card (onUpload / onGenerate provided): unified
             upload affordance built into the same bordered box.
          2. Bare placeholder: subtle icon + label, used when the node
             is empty but no actions wired (read-only contexts).
          Audio + video have their own empty-state branches. */}
      {isEmptyImageActionable ? (
        <div className="flex h-full w-full flex-col gap-3 px-[18px] pt-4 pb-3.5">
          {/* Heading row: sentence-case Inter label + ImageIcon, anchored top */}
          <div className="flex items-center gap-2">
            <ImageIcon size={12} aria-hidden="true" className={selected ? "text-atelier-brand-400" : "text-white/45"} />
            <span className={`text-[11px] ${
              selected ? "text-atelier-brand-400" : "text-white/45"
            }`}>
              Image draft
            </span>
          </div>
          {/* Action stack pinned to bottom — the buttons feel placed on a
              surface, not floating in a centered void. */}
          <div className="mt-auto flex flex-col gap-1.5">
            {onUpload ? (
              <button
                type="button"
                aria-label="Upload image"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => { e.stopPropagation(); onUpload(id); }}
                className="inline-flex items-center justify-center gap-1.5 rounded-md bg-atelier-brand-400 px-2.5 py-1.5 font-display text-[12px] font-medium text-white shadow-[0_1px_0_0_rgba(255,255,255,0.08)_inset,0_4px_10px_-3px_rgba(59,107,255,0.5)] transition-all duration-200 hover:bg-atelier-brand-400/92 hover:shadow-[0_1px_0_0_rgba(255,255,255,0.12)_inset,0_6px_14px_-3px_rgba(59,107,255,0.6)] active:scale-[0.97]"
              >
                <Upload size={11} aria-hidden="true" />
                Upload
              </button>
            ) : null}
            {onGenerate ? (
              <button
                type="button"
                aria-label="Generate image from prompt"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => { e.stopPropagation(); onGenerate(id); }}
                className="inline-flex items-center justify-center gap-1.5 rounded-full bg-atelier-port-positive px-2.5 py-1.5 text-[12px] font-medium text-black shadow-[0_4px_12px_-3px_rgba(61,220,132,0.55)] transition-all duration-200 hover:brightness-110 active:scale-[0.97]"
              >
                <Sparkles size={11} aria-hidden="true" />
                Generate
              </button>
            ) : null}
          </div>
        </div>
      ) : !src && kind === "image" && status !== "processing" && status !== "pending" && status !== "failed" ? (
        <div className="grid h-full w-full place-items-center text-center">
          <div className="space-y-1.5 px-3 text-white/45">
            <ImageIcon className="mx-auto text-white/40" size={20} />
            <div className="text-[11px]">Image</div>
            <div className="text-[12px] italic leading-snug text-white/35">{filename || "No media yet"}</div>
          </div>
        </div>
      ) : null}
      {!src && kind === "video" && status !== "processing" && status !== "pending" && status !== "failed" ? (
        <div className="grid h-full w-full place-items-center text-center">
          <div className="space-y-1.5 px-3 text-white/45">
            <Video className="mx-auto text-white/40" size={20} />
            <div className="text-[11px]">Video</div>
            <div className="text-[12px] italic leading-snug text-white/35">{filename || "No take yet"}</div>
          </div>
        </div>
      ) : null}

      {showProcessing ? (
        <>
          <div className="absolute inset-0 grid place-items-center bg-atelier-processing/[0.18] backdrop-blur-[1px]">
            <div className="flex flex-col items-center gap-1">
              <Loader2 className="animate-spin text-atelier-processing" size={20} />
              {typeof progress === "number" ? (
                <span className="text-[11px] font-semibold tabular-nums text-white/90">{Math.round(progress)}%</span>
              ) : null}
              {typeof etaSeconds === "number" && etaSeconds > 0 ? (
                <span className="text-[11px] tabular-nums text-white/55">~{etaSeconds}s left</span>
              ) : null}
              {showStuckActions ? (
                <div
                  className="mt-1 flex items-center gap-1"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                >
                  {onCancel ? (
                    <button
                      type="button"
                      disabled={canceling}
                      onClick={async (e) => {
                        e.stopPropagation();
                        setCanceling(true);
                        try {
                          await onCancel(id);
                        } finally {
                          setCanceling(false);
                        }
                      }}
                      className="rounded-md border border-atelier-failed/40 bg-atelier-failed/15 px-2 py-[2px] text-[10px] font-medium text-foreground transition-colors hover:bg-atelier-failed/25 disabled:cursor-wait disabled:opacity-60"
                    >
                      {canceling ? "…" : "Cancel"}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setDiagnoseOpen(true);
                    }}
                    className="rounded-md border border-white/20 bg-black/40 px-2 py-[2px] text-[10px] font-medium text-foreground/90 transition-colors hover:border-atelier-brand-400/60"
                  >
                    Diagnose
                  </button>
                </div>
              ) : null}
            </div>
          </div>
          {typeof progress === "number" ? (
            <div className="absolute inset-x-0 bottom-0 h-[3px] bg-black/40">
              <div
                className="h-full bg-atelier-processing transition-[width] duration-300"
                style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
              />
            </div>
          ) : null}
          {diagnoseOpen ? (
            <DiagnoseModal
              taskId={id}
              elapsedLabel={`${Math.floor(pendingElapsedMs / 1000)}s`}
              onClose={() => setDiagnoseOpen(false)}
            />
          ) : null}
        </>
      ) : null}

      {showFailed ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-atelier-failed/[0.18] backdrop-blur-[1px]">
          <AlertTriangle size={18} className="text-atelier-failed" aria-hidden="true" />
          <span className="text-[11px] text-atelier-failed">
            {errorMessage ? "Failed" : "Generation failed"}
          </span>
          <RetryButton
            id={id}
            errorMessage={errorMessage}
            onRetry={onRetry}
            retryModelOptions={retryModelOptions}
            onRetryWithModel={onRetryWithModel}
          />
        </div>
      ) : null}

      {/* Suppress the hover type chip + filename strip when the actionable
          empty-image card is showing — they're redundant with the inline
          'Image draft' label and would crowd the buttons. */}
      {!isEmptyImageActionable ? <TypeChip kind={kind} /> : null}

      {filename && !isEmptyImageActionable ? (
        <span className="pointer-events-none absolute right-2 bottom-2 hidden max-w-[70%] truncate rounded-md bg-black/55 px-1.5 py-1 text-[11px] text-white/70 backdrop-blur-md group-hover:inline-block">
          {filename}
        </span>
      ) : null}

      {selectedAsTake ? (
        // Selected take = the chosen output. A clean cobalt pill (selection
        // is the one place cobalt is allowed), sentence-case, no mono-caps.
        <span className="pointer-events-none absolute left-2 bottom-2 inline-flex items-center gap-1 rounded-full bg-atelier-brand-400 px-2 py-[3px] text-[10px] font-medium text-white shadow-[0_2px_8px_-2px_rgba(0,0,0,0.6)]">
          <Check size={9} aria-hidden="true" /> selected
        </span>
      ) : duration ? (
        <span className="pointer-events-none absolute left-2 bottom-2 hidden max-w-[70%] truncate rounded-md bg-black/55 px-1.5 py-1 text-[11px] text-white/70 backdrop-blur-md group-hover:inline-block">
          {duration}
        </span>
      ) : null}

      {/* Output port — completed media (video / audio) exposes a blue port
          on the right edge so beams plug in. Box is overflow-hidden, so the
          dot sits just inside at right-1 rather than half-outside. */}
      {src && status === "completed" ? (
        <PortDot kind="output" className="absolute right-1 top-1/2 -translate-y-1/2 z-10" />
      ) : null}
    </div>
  );
}
