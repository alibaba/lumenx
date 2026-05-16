"use client";
import { AlertTriangle, Check, ImageIcon, Loader2, Play, Video, Volume2 } from "lucide-react";
import type { MediaKind } from "@/components/atelier/v3/types";

interface Props {
  id: string;
  kind: MediaKind;
  src?: string;
  filename?: string;
  duration?: string;
  status: "draft" | "pending" | "processing" | "completed" | "failed";
  progress?: number;
  etaSeconds?: number;
  selected?: boolean;
  selectedAsTake?: boolean;
  x: number;
  y: number;
  width?: number;
  height?: number;
  onSelect?: (id: string) => void;
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
  return (
    <span className="absolute left-1 top-1 hidden bg-black/65 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-white/85 group-hover:block">
      <Icon size={10} className="inline-block align-middle" /> {kind}
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
  selected,
  selectedAsTake,
  x,
  y,
  width,
  height,
  onSelect,
}: Props) {
  const def = DEFAULT_SIZE[kind];
  const w = Math.max(40, Math.min(width ?? def.w, MAX_WIDTH));
  const h = Math.max(24, height ?? def.h);
  const ring = ringClass(status, selected, selectedAsTake);
  const showProcessing = status === "processing" || status === "pending";
  const showFailed = status === "failed";

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
      className={`group absolute overflow-hidden rounded-md bg-black/40 shadow-2xl shadow-black/40 ${
        // When media is missing, give the box a visible frame so it doesn't
        // melt into the canvas. With media (image/video src) we let the
        // image dominate; the ring/shadow still keep edges legible.
        !src && kind !== "audio"
          ? "border border-dashed border-white/15 bg-white/[0.04]"
          : "border border-white/10"
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
          <img src={src} alt={filename ?? ""} className="h-full w-full object-cover" />
          {status === "completed" ? (
            <span className="pointer-events-none absolute left-1/2 top-1/2 grid h-8 w-8 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/55 text-white/95 opacity-60 group-hover:opacity-100">
              <Play size={16} />
            </span>
          ) : null}
        </>
      ) : null}

      {kind === "audio" ? <AudioWaveform /> : null}

      {/* Empty placeholder when image/video has no src — would otherwise be
          an invisible box on the dark canvas. */}
      {!src && kind === "image" && status !== "processing" && status !== "pending" && status !== "failed" ? (
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
        <div className="absolute inset-0 grid place-items-center bg-red-400/[0.18]">
          <AlertTriangle size={18} className="text-red-200" />
        </div>
      ) : null}

      <TypeChip kind={kind} />

      {filename ? (
        <span className="absolute right-1 bottom-1 hidden max-w-[70%] truncate bg-black/65 px-1.5 py-0.5 font-mono text-[10px] text-white/80 group-hover:block">
          {filename}
        </span>
      ) : null}

      {selectedAsTake ? (
        <span className="absolute left-1 bottom-1 inline-flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold text-white shadow-[0_0_0_2px_rgba(0,0,0,0.35)]">
          <Check size={10} /> Selected
        </span>
      ) : duration ? (
        <span className="absolute left-1 bottom-1 hidden max-w-[70%] truncate bg-black/65 px-1.5 py-0.5 font-mono text-[10px] text-white/80 group-hover:block">
          {duration}
        </span>
      ) : null}
    </div>
  );
}
