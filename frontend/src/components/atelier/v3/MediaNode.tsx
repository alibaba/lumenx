/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ImageIcon, Loader2, Play, RotateCw, Sparkles, Upload, Video, Volume2 } from "lucide-react";
import { useTranslations } from "next-intl";
import type { MediaKind } from "@/components/atelier/v3/types";
import { PortDot } from "./NodePort";
import { DiagnoseModal } from "@/components/shared/PendingTaskAffordance";
import { ATELIER_MEDIA_RENDERED_BOUNDS } from "@/lib/atelierCanvas";

interface Props {
  /** Q5 · demo take strip — candidate thumbnails riding under the media
   *  (58×34 tiles; selected = white ring; processing = % ghost tile).
   *  Clicking a tile selects that take's virtual node on the canvas. */
  takes?: Array<{ id: string; url?: string | null; selected?: boolean; progress?: string | null }>;
  onPickTake?: (candidateId: string) => void;
  id: string;
  kind: MediaKind;
  src?: string;
  poster?: string;
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
  /** Pointer-drag handoff for takes: the wrapper (renderCandidatesAsMedia
   *  Nodes) owns the canvas node-drag gesture for candidate takes, but the
   *  root button stopPropagation()s every pointerdown — without this hook
   *  the drag gesture never reaches it (user: take 拖不动). Invoked after
   *  stopPropagation + onSelect inside the root pointerdown. */
  onDragPointerDown?: (event: React.PointerEvent) => void;
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
  /** v0.6.3 — when present, the output PortDot renders as interactive
   *  (drag-to-connect: hover scale, grab cursor, data-port, tooltip) AND
   *  the dot itself owns the pointer-down gesture, so the shell's
   *  capture-phase node-drag handler bails on [data-port] origins.
   *  When ABSENT, every output PortDot stays decorative (quiet 5px
   *  indicator, no data-port, no hover affordance) — pointer-down "near"
   *  the dot still selects/drags the parent node. The caller (renderer)
   *  decides whether to pass this based on whether handlePortDragOut
   *  would accept the rendered node as a source. */
  onPortDown?: (event: React.PointerEvent) => void;
}

const DEFAULT_SIZE: Record<MediaKind, { w: number; h: number }> = {
  image: { w: 180, h: 180 },
  video: { w: 200, h: 113 },
  audio: { w: 200, h: 56 },
};

const MAX_MEDIA_WIDTH = ATELIER_MEDIA_RENDERED_BOUNDS.image.width;

function ringClass(
  status: Props["status"],
  selected: boolean | undefined,
  selectedAsTake: boolean | undefined,
): string {
  if (selected || selectedAsTake) return "ring-1 ring-white/25";
  if (status === "processing" || status === "pending") return "ring-1 ring-atelier-processing/60";
  if (status === "failed") return "ring-1 ring-atelier-failed/60";
  return "";
}

function TypeChip({ kind }: { kind: MediaKind }) {
  const t = useTranslations("atelier.node");
  const Icon = kind === "video" ? Video : kind === "audio" ? Volume2 : ImageIcon;
  // Quiet frosted caption — sentence-case Inter on a soft glass pill, no
  // mono-caps (§9.1). Reads as a gentle hint, not a terminal stamp.
  const label = kind === "image" ? t("typeImage") : kind === "video" ? t("typeVideo") : t("typeAudio");
  return (
    <span className="pointer-events-none absolute left-2 top-2 hidden items-center gap-1 rounded-md border border-white/[0.08] bg-black/45 px-1.5 py-1 text-[11px] text-white/45 lowercase backdrop-blur-md group-hover:inline-flex">
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
  const t = useTranslations("atelier.node");
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
          data-tip={errorMessage || t("retryTipSame")}
        >
          <RotateCw size={10} aria-hidden="true" /> {t("retry")}
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
          data-tip={t("retryTipPick")}
        >
          <ChevronDown size={10} aria-hidden="true" />
        </button>
      ) : null}
      {pickerOpen && hasOptions ? (
        <div
          role="menu"
          aria-label="Retry with model"
          className="absolute right-0 top-full z-30 mt-1 w-[180px] origin-top overflow-hidden rounded-md border border-white/[0.08] bg-[#141416]/96 p-1 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
        >
          <div className="border-b border-white/[0.08] px-2 py-1.5 text-[11px] text-white/45">
            {t("retryWith")}
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
  poster,
  filename,
  duration,
  status,
  takes,
  onPickTake,
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
  onDragPointerDown,
  onRetry,
  onUpload,
  onGenerate,
  retryModelOptions,
  onRetryWithModel,
  onCancel,
  onPortDown,
}: Props) {
  const t = useTranslations("atelier.node");
  const def = DEFAULT_SIZE[kind];
  const w = Math.max(40, Math.min(width ?? def.w, MAX_MEDIA_WIDTH));
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
      [kind === "video" ? t("videoWord") : t("imageWord"), duration].filter(Boolean).join(" · ");
    const previewRing = selected || selectedAsTake ? "ring-1 ring-white/25" : "";
    return (
      <div
        style={{ left: 0, top: 0, width: `${MAX_MEDIA_WIDTH}px`, transform: `translate(${x}px, ${y}px)` }}
        className="atelier-bloom atelier-bloom-secondary absolute overflow-visible rounded-[20px]"
      >
        <div
          role="button"
          tabIndex={0}
          onPointerDown={(event) => {
            event.stopPropagation();
            onSelect?.(id);
            onDragPointerDown?.(event);
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
                  poster={poster}
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
                {/* R6b · demo video compact kit (§226-236): resident duration
                    badge top-left + a hover scrubline above the scrim that
                    fills as the hover-preview plays. */}
                {duration ? (
                  <span aria-hidden="true" className="at-dur-badge">{duration}</span>
                ) : null}
                <span aria-hidden="true" className="at-scrubline !bottom-[64px] opacity-0 transition-opacity duration-[var(--at-dur-fast)] group-hover:opacity-100">
                  <i className="group-hover:!w-full !w-0" style={{ transition: "width 3s linear" }} />
                </span>
              </>
            )}
            {/* ADR-0004 · result actions have ONE entry: the upper satellite
                panel. The hover cluster that used to sit here was five
                buttons whose onClick only called stopPropagation — real
                affordances now live in the panel, so it is gone. */}
            {/* Bottom gradient scrim — title + description sit on the media.
                PX · demo at-scrim ladder: .t 13px/500 · .m 11px white/.55. */}
            <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-[2px] bg-gradient-to-t from-black/85 via-black/45 to-transparent px-3.5 pb-3 pt-8">
              <span className="font-sans text-[13px] font-medium leading-tight text-white/[0.92]">{t("finalResult")}</span>
              {resultDescription ? (
                <span className="truncate text-[11px] leading-[1.5] text-white/55">
                  {resultDescription}
                  {takes && takes.length > 0 ? (
                    <> · {takes.some((tk) => tk.selected)
                      ? t("takeOfSelected", { n: takes.findIndex((tk) => tk.selected) + 1, m: takes.length })
                      : t("takeOf", { n: "–", m: takes.length })}</>
                  ) : null}
                </span>
              ) : null}
            </div>
            {selectedAsTake ? (
              <span className="pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-atelier-brand-400 px-2 py-[3px] text-[10px] font-medium text-white shadow-[0_2px_8px_-2px_rgba(0,0,0,0.6)]">
                <Check size={9} aria-hidden="true" /> {t("selectedPill")}
              </span>
            ) : null}
          </div>
          {/* Q5 · demo take strip — candidate tiles under the media (58×34,
              selected = white ring + check tint, processing = ghost % tile).
              Clicking a tile selects that take's virtual node so the full
              pick/retry action set stays one click away (功能只多不少). */}
          {takes && takes.length > 0 ? (
            <div
              className="flex items-center gap-1.5 px-3.5 py-2.5"
              onPointerDown={(e) => e.stopPropagation()}
            >
              {takes.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  aria-label={t.selected ? "Selected take" : "View take"}
                  onClick={(e) => {
                    e.stopPropagation();
                    onPickTake?.(t.id);
                  }}
                  className={`relative h-[34px] w-[58px] shrink-0 overflow-hidden rounded-[7px] border transition-[border-color,transform] duration-[var(--at-dur-fast)] hover:-translate-y-px ${
                    t.selected
                      ? "border-white/80 ring-1 ring-white/40"
                      : "border-white/10 hover:border-white/30"
                  }`}
                >
                  {t.url ? (
                    <video src={t.url} muted playsInline preload="metadata" className="h-full w-full object-cover" />
                  ) : (
                    <span className="grid h-full w-full place-items-center bg-white/[0.04] font-mono text-[10px] text-white/45">
                      {t.progress ?? "…"}
                    </span>
                  )}
                </button>
              ))}
            </div>
          ) : null}
          {/* Output port — completed media exposes a blue port on the right
              edge. v0.6.3: interactive ONLY when the renderer wired
              onPortDown — image media is always a valid handlePortDragOut
              source, but a top-level (non-candidate) video isn't, so the
              renderer skips onPortDown there and the dot stays decorative.
              When interactive, the dot IS the drag source — onPortDown
              wires to handlePortDragOut, and the shell's capture-phase
              node-drag bails on [data-port] origins so this gesture wins. */}
          <PortDot
            kind="output"
            interactive={!!onPortDown}
            onPointerDown={onPortDown}
            className="absolute right-1 top-1/2 -translate-y-1/2 z-10"
          />
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
      ? "ring-1 ring-white/25 border-white/20"
      : status === "failed"
        ? "border-atelier-failed/45"
        : "border-glass-border";
    const cardName = filename || t("imageWord");
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
          left: 0,
          top: 0,
          width: `${MAX_MEDIA_WIDTH}px`,
          transform: `translate(${x}px, ${y}px)`,
        }}
        className={`group atelier-node-shell absolute overflow-hidden transition-[box-shadow,border-color] duration-200 ease-out ${cardBorder}`}
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
          <span className="shrink-0 rounded-md border border-white/[0.08] px-1.5 py-0.5 text-[11px] text-white/45">
            {t("typeImage")}
          </span>
        </div>
        {/* Thumbnail body — adapts to the source's natural aspect (read
            once on load), clamped to [0.6, 2.4] so portraits and panos
            stay readable on a 244-wide card. Fallback 4:3 while loading
            keeps the card height stable. */}
        <div className="px-[18px] pb-3">
          <div
            className="relative overflow-hidden rounded-md border border-white/[0.08] bg-black/40"
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
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/55 backdrop-blur-[2px]">
                <AlertTriangle size={16} className="text-atelier-failed" aria-hidden="true" />
                <span className="text-[11px] text-atelier-failed">
                  {errorMessage ? t("failed") : t("generationFailed")}
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
                <Check size={9} aria-hidden="true" /> {t("selectedPill")}
              </span>
            ) : null}
          </div>
        </div>
        {/* Quiet footer — sentence-case label + a tiny neutral dot (§9.6),
            no mono-caps tearline. The id stamp trails as a muted reference. */}
        <div className="mt-3 flex items-center gap-2 border-t border-white/[0.08] px-[18px] pb-3.5 pt-3">
          <span aria-hidden="true" className="h-[5px] w-[5px] shrink-0 rounded-full bg-white/30" />
          <span className="text-[11px] text-white/45">{t("imageWord")}</span>
          <span className="ml-auto text-[11px] tabular-nums text-white/40">{stampNum}</span>
        </div>
        {/* Output port — completed image cards expose a blue port on the
            right edge. v0.6.3: completed image media IS a valid
            handlePortDragOut source (isImageSource), so the renderer
            wires onPortDown and the dot becomes interactive. If
            onPortDown is somehow missing (defensive: empty media_urls,
            non-image consumer, etc.) we fall back to a decorative dot
            so the user can't trigger a no-op drag. */}
        {status === "completed" ? (
          <PortDot
            kind="output"
            interactive={!!onPortDown}
            onPointerDown={onPortDown}
            className="absolute right-1 top-1/2 -translate-y-1/2 z-10"
          />
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
          ? "border border-dashed border-white/[0.12]"
          : ""
      } ${ring}`}
      style={{
        left: 0,
        top: 0,
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

      {kind === "audio" ? (src ? <audio aria-label={filename ?? t("typeAudio")} controls preload="metadata" src={src} className="h-full w-full" onPointerDown={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()} /> : <AudioWaveform />) : null}

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
              {t("imageDraft")}
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
                className="inline-flex items-center justify-center gap-1.5 rounded-full border border-white/[0.12] bg-white/[0.04] px-2.5 py-1.5 text-[12px] font-medium text-white/75 transition-colors duration-200 hover:border-white/20 hover:bg-white/[0.07] hover:text-white active:scale-[0.97]"
              >
                <Upload size={11} aria-hidden="true" />
                {t("upload")}
              </button>
            ) : null}
            {onGenerate ? (
              <button
                type="button"
                aria-label="Generate image from prompt"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => { e.stopPropagation(); onGenerate(id); }}
                className="inline-flex items-center justify-center gap-1.5 rounded-full bg-atelier-port-positive px-2.5 py-1.5 text-[12px] font-medium text-black shadow-[0_3px_10px_-4px_rgba(61,220,132,0.45)] transition-all duration-200 hover:brightness-110 active:scale-[0.97]"
              >
                <Sparkles size={11} aria-hidden="true" />
                {t("generate")}
              </button>
            ) : null}
          </div>
        </div>
      ) : !src && kind === "image" && status !== "processing" && status !== "pending" && status !== "failed" ? (
        <div className="grid h-full w-full place-items-center text-center">
          <div className="space-y-1.5 px-3 text-white/45">
            <ImageIcon className="mx-auto text-white/40" size={20} />
            <div className="text-[11px]">{t("imageWord")}</div>
            <div className="text-[12px] italic leading-snug text-white/35">{filename || t("noMediaYet")}</div>
          </div>
        </div>
      ) : null}
      {!src && kind === "video" && status !== "processing" && status !== "pending" && status !== "failed" ? (
        <div className="grid h-full w-full place-items-center text-center">
          <div className="space-y-1.5 px-3 text-white/45">
            <Video className="mx-auto text-white/40" size={20} />
            <div className="text-[11px]">{t("videoWord")}</div>
            <div className="text-[12px] italic leading-snug text-white/35">{filename || t("noTakeYet")}</div>
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
                <span className="text-[11px] tabular-nums text-white/55">{t("etaLeft", { eta: etaSeconds })}</span>
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
                      {canceling ? "…" : t("cancel")}
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
                    {t("diagnose")}
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
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/55 backdrop-blur-[2px]">
          <AlertTriangle size={18} className="text-atelier-failed" aria-hidden="true" />
          <span className="text-[11px] text-atelier-failed">
            {errorMessage ? t("failed") : t("generationFailed")}
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
          <Check size={9} aria-hidden="true" /> {t("selectedPill")}
        </span>
      ) : duration ? (
        <span className="pointer-events-none absolute left-2 bottom-2 hidden max-w-[70%] truncate rounded-md bg-black/55 px-1.5 py-1 text-[11px] text-white/70 backdrop-blur-md group-hover:inline-block">
          {duration}
        </span>
      ) : null}

      {/* Output port — completed media (audio is the typical case here;
          image+completed+src takes the card-mode branch above, and
          video+completed+src takes the preview-card branch above). v0.6.3:
          handlePortDragOut has no audio path, so this dot is decorative by
          default. We still honor onPortDown if the renderer wires one (for
          forward-compatibility once audio takes are accepted as a source),
          but today the renderer skips it for audio and the dot stays a
          quiet visual indicator. */}
      {src && status === "completed" ? (
        <PortDot
          kind="output"
          interactive={!!onPortDown}
          onPointerDown={onPortDown}
          className="absolute right-1 top-1/2 -translate-y-1/2 z-10"
        />
      ) : null}
    </div>
  );
}
