"use client";
import { useRef } from "react";
import { AlertTriangle, Check, ImageIcon, Loader2, Play, RotateCw, Sparkles, Upload, Video, Volume2 } from "lucide-react";
import type { MediaKind } from "@/components/atelier/v3/types";
import { TearLine } from "./ornaments";

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
  /** When provided on an empty image node, the placeholder becomes a
   *  unified actionable card with Upload + Generate buttons baked into the
   *  same bordered box (no separate floating overlay). */
  onUpload?: (id: string) => void;
  onGenerate?: (id: string) => void;
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
  if (selected || selectedAsTake) return "ring-2 ring-primary";
  if (status === "processing" || status === "pending") return "ring-1 ring-blue-400/60";
  if (status === "failed") return "ring-1 ring-red-400/60";
  return "";
}

function TypeChip({ kind }: { kind: MediaKind }) {
  const Icon = kind === "video" ? Video : kind === "audio" ? Volume2 : ImageIcon;
  // Stamped feel: dashed inset border on a darkened pill, matches the
  // atelier "stamped from a rubber" badge vocabulary used elsewhere.
  const label = kind === "image" ? "img" : kind === "video" ? "vid" : "aud";
  return (
    <span className="pointer-events-none absolute left-1.5 top-1.5 hidden items-center gap-1 rounded-[3px] border border-dashed border-white/22 bg-black/70 px-1.5 py-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-white/90 backdrop-blur-sm group-hover:inline-flex">
      <Icon size={9} aria-hidden="true" className="text-primary/85" />
      {label}
    </span>
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
}: Props) {
  const def = DEFAULT_SIZE[kind];
  const w = Math.max(40, Math.min(width ?? def.w, MAX_WIDTH));
  const h = Math.max(24, height ?? def.h);
  const ring = ringClass(status, selected, selectedAsTake);
  const showProcessing = status === "processing" || status === "pending";
  const showFailed = status === "failed";
  // An empty image node with upload/generate callbacks becomes a unified
  // actionable card. We hide the bare placeholder + the hover TypeChip in
  // that mode so the buttons don't compete with stacked chrome.
  const isEmptyImageActionable =
    kind === "image" && !src && !!(onUpload || onGenerate) &&
    status !== "processing" && status !== "pending" && status !== "failed";

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

  // ── Image card mode ────────────────────────────────────────────────
  // When an image node has uploaded media, render the same card chrome
  // as DraftNode (244 wide, header row + thumb + tear footer) so the
  // canvas reads as a uniform set of cards rather than a mix of bare
  // thumbnails and bordered cards. Empty image drafts keep their
  // upload+generate inline UX; videos and audio stay thumbnail-only.
  if (kind === "image" && src && !isEmptyImageActionable) {
    const stampNum = id.slice(-3).toUpperCase();
    const cardBorder = selected || selectedAsTake
      ? "ring-2 ring-primary border-primary/50"
      : status === "failed"
        ? "border-red-400/45"
        : "border-glass-border";
    const cardName = filename || "Image";
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
          backgroundImage:
            "linear-gradient(to bottom, rgba(255,255,255,0.018) 0%, rgba(255,255,255,0) 32%)",
        }}
        className={`group absolute w-[244px] overflow-hidden rounded-lg border bg-[#141416] shadow-[0_18px_40px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.6),inset_0_1px_0_0_rgba(255,255,255,0.06)] transition-shadow duration-200 ${cardBorder}`}
      >
        {/* Header row — same vocabulary as DraftNode (sparkles + display
            font title). 'IMG' caption sits at the trailing edge so the
            card is identifiable at a glance even when the thumbnail is
            similar across siblings. */}
        <div className="flex items-center gap-1.5 px-3.5 pb-1.5 pt-3 text-foreground">
          <ImageIcon size={11} className="shrink-0 text-primary" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate font-display text-[13px] font-medium tracking-[-0.005em]">
            {cardName}
          </span>
          <span className="shrink-0 rounded-[3px] border border-dashed border-white/22 px-1.5 py-[2px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] text-text-muted/85">
            Img
          </span>
        </div>
        {/* Thumbnail body — fixed aspect 4:3 keeps every image card the
            same height regardless of source ratio. */}
        <div className="px-3 pb-1.5">
          <div className="relative overflow-hidden rounded-md border border-white/8 bg-black/40" style={{ aspectRatio: "4 / 3" }}>
            <img
              src={src}
              alt={filename ?? ""}
              loading="lazy"
              decoding="async"
              className="absolute inset-0 h-full w-full object-cover"
            />
            {showProcessing ? (
              <div className="absolute inset-0 grid place-items-center bg-blue-400/[0.18] backdrop-blur-[1px]">
                <div className="flex flex-col items-center gap-1">
                  <Loader2 className="animate-spin text-blue-100" size={18} />
                  {typeof progress === "number" ? (
                    <span className="font-mono text-[10px] font-semibold text-blue-50">{Math.round(progress)}%</span>
                  ) : null}
                </div>
              </div>
            ) : null}
            {showFailed ? (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-red-400/[0.18] backdrop-blur-[1px]">
                <AlertTriangle size={16} className="text-red-200" aria-hidden="true" />
                <span className="font-mono text-[9px] uppercase tracking-wider text-red-100">
                  {errorMessage ? "Failed" : "Generation failed"}
                </span>
                {onRetry ? (
                  <button
                    type="button"
                    aria-label="Retry generation"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      onRetry(id);
                    }}
                    className="btn-tip mt-0.5 inline-flex items-center gap-1 rounded-full bg-red-400/25 px-2 py-0.5 text-[10px] font-semibold text-red-50 hover:bg-red-400/40"
                    data-tip={errorMessage || "Retry generation"}
                  >
                    <RotateCw size={10} aria-hidden="true" /> Retry
                  </button>
                ) : null}
              </div>
            ) : null}
            {selectedAsTake ? (
              <span className="pointer-events-none absolute left-1.5 bottom-1.5 inline-flex items-center gap-1 rounded-full border border-dashed border-white/35 bg-primary px-2 py-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-white shadow-[0_0_0_2px_rgba(0,0,0,0.45)]">
                <Check size={9} aria-hidden="true" /> selected
              </span>
            ) : null}
          </div>
        </div>
        {/* Tear-stamp footer — receipt-style index, matches IdeaNode and
            DraftNode footers. */}
        <div className="px-3 pb-2.5">
          <TearLine label={`Image · No ${stampNum}`} />
        </div>
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
      className={`group absolute overflow-hidden rounded-md bg-black/40 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.55)] transition-shadow duration-200 ${
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
            ? "border border-primary/45"
            : "border border-dashed border-primary/22 hover:border-primary/35"
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
        <div className="flex h-full w-full flex-col gap-2.5 px-3.5 py-3.5">
          {/* Heading row: small mono caps + ImageIcon, anchored top */}
          <div className="flex items-center gap-1.5">
            <ImageIcon size={11} aria-hidden="true" className={selected ? "text-primary" : "text-primary/75"} />
            <span className={`font-mono text-[9px] uppercase tracking-[0.22em] ${
              selected ? "text-primary" : "text-primary/75"
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
                className="inline-flex items-center justify-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 font-display text-[12px] font-medium text-white shadow-[0_1px_0_0_rgba(255,255,255,0.08)_inset,0_4px_10px_-3px_rgba(100,108,255,0.5)] transition-all duration-200 hover:bg-primary/92 hover:shadow-[0_1px_0_0_rgba(255,255,255,0.12)_inset,0_6px_14px_-3px_rgba(100,108,255,0.6)] active:scale-[0.97]"
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
                className="inline-flex items-center justify-center gap-1.5 rounded-md border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-[12px] text-text-secondary transition-all duration-200 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97]"
              >
                <Sparkles size={11} aria-hidden="true" />
                Generate
              </button>
            ) : null}
          </div>
        </div>
      ) : !src && kind === "image" && status !== "processing" && status !== "pending" && status !== "failed" ? (
        <div className="grid h-full w-full place-items-center text-center">
          <div className="space-y-1 px-3 text-text-muted">
            <ImageIcon className="mx-auto" size={20} />
            <div className="font-mono text-[10px] uppercase tracking-wider">image</div>
            <div className="text-[10px] leading-tight">{filename || "no media yet"}</div>
          </div>
        </div>
      ) : null}
      {!src && kind === "video" && status !== "processing" && status !== "pending" && status !== "failed" ? (
        <div className="grid h-full w-full place-items-center text-center">
          <div className="space-y-1 px-3 text-text-muted">
            <Video className="mx-auto" size={20} />
            <div className="font-mono text-[10px] uppercase tracking-wider">video</div>
            <div className="text-[10px] leading-tight">{filename || "no take yet"}</div>
          </div>
        </div>
      ) : null}

      {showProcessing ? (
        <>
          <div className="absolute inset-0 grid place-items-center bg-blue-400/[0.18] backdrop-blur-[1px]">
            <div className="flex flex-col items-center gap-1">
              <Loader2 className="animate-spin text-blue-100" size={20} />
              {typeof progress === "number" ? (
                <span className="font-mono text-[11px] font-semibold text-blue-50">{Math.round(progress)}%</span>
              ) : null}
              {typeof etaSeconds === "number" && etaSeconds > 0 ? (
                <span className="font-mono text-[9px] text-blue-100/85">~{etaSeconds}s left</span>
              ) : null}
            </div>
          </div>
          {typeof progress === "number" ? (
            <div className="absolute inset-x-0 bottom-0 h-[3px] bg-black/40">
              <div
                className="h-full bg-blue-300 transition-[width] duration-300"
                style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
              />
            </div>
          ) : null}
        </>
      ) : null}

      {showFailed ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-red-400/[0.18] backdrop-blur-[1px]">
          <AlertTriangle size={18} className="text-red-200" aria-hidden="true" />
          <span className="font-mono text-[9px] uppercase tracking-wider text-red-100">
            {errorMessage ? "Failed" : "Generation failed"}
          </span>
          {onRetry ? (
            <button
              type="button"
              aria-label="Retry generation"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onRetry(id);
              }}
              className="btn-tip mt-0.5 inline-flex items-center gap-1 rounded-full bg-red-400/25 px-2 py-0.5 text-[10px] font-semibold text-red-50 hover:bg-red-400/40"
              data-tip={errorMessage || "Retry generation"}
            >
              <RotateCw size={10} aria-hidden="true" /> Retry
            </button>
          ) : null}
        </div>
      ) : null}

      {/* Suppress the hover type chip + filename strip when the actionable
          empty-image card is showing — they're redundant with the inline
          'Image draft' label and would crowd the buttons. */}
      {!isEmptyImageActionable ? <TypeChip kind={kind} /> : null}

      {filename && !isEmptyImageActionable ? (
        <span className="pointer-events-none absolute right-1.5 bottom-1.5 hidden max-w-[70%] truncate rounded-[3px] bg-black/70 px-1.5 py-[3px] font-mono text-[9px] tracking-tight text-white/80 backdrop-blur-sm group-hover:inline-block">
          {filename}
        </span>
      ) : null}

      {selectedAsTake ? (
        // Selected take = "stamped approved". Dashed inset reinforces the
        // rubber-stamp identity vs. a flat label pill.
        <span className="pointer-events-none absolute left-1.5 bottom-1.5 inline-flex items-center gap-1 rounded-full border border-dashed border-white/35 bg-primary px-2 py-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-white shadow-[0_0_0_2px_rgba(0,0,0,0.45)]">
          <Check size={9} aria-hidden="true" /> selected
        </span>
      ) : duration ? (
        <span className="pointer-events-none absolute left-1.5 bottom-1.5 hidden max-w-[70%] truncate rounded-[3px] bg-black/70 px-1.5 py-[3px] font-mono text-[9px] tracking-tight text-white/80 backdrop-blur-sm group-hover:inline-block">
          {duration}
        </span>
      ) : null}
    </div>
  );
}
