"use client";
import { useMemo } from "react";
import { Settings, Wand2, X, Plus } from "lucide-react";
import { CapabilityIcon } from "./CapabilityIcon";
import { ChipDropdown } from "./ChipDropdown";
import { composerPlacement, type ComposerAnchor, type ComposerViewport } from "./positioning";

const TABS = ["T2I", "I2I", "T2V", "I2V", "R2V", "V2V", "Audio"] as const;
export type ComposerTab = typeof TABS[number];

export interface ComposerSubmitPayload {
  tab: ComposerTab;
  prompt: string;
  modelLabel: string;
  aspect: string;
  duration: string;
  count: string;
  refs: ComposerRef[];
}

export interface ComposerRef {
  src: string;
  role?: string;     // "ref" | "ff" | "vid"
}

interface Props {
  activeTab?: ComposerTab;
  onTabChange?: (tab: ComposerTab) => void;
  refs?: ComposerRef[];
  prompt?: string;
  modelLabel?: string;
  aspect?: string;
  duration?: string;
  count?: string;
  showCapabilityMismatch?: boolean;
  onClose?: () => void;
  onSubmit?: (payload: ComposerSubmitPayload) => void;

  // Position: either anchor + viewport, OR explicit style.
  anchor?: ComposerAnchor | null;
  viewport?: ComposerViewport;
  style?: React.CSSProperties;
}

export function Composer({
  activeTab = "I2V",
  onTabChange,
  refs = [],
  prompt = "",
  modelLabel = "Wan 2.7",
  aspect = "16:9 · 720p",
  duration = "5s",
  count = "4×",
  showCapabilityMismatch = false,
  onClose,
  onSubmit,
  anchor,
  viewport,
  style,
}: Props) {
  const computedStyle = useMemo<React.CSSProperties>(() => {
    if (style) return style;
    if (viewport) {
      const placement = composerPlacement(anchor ?? null, viewport, { width: 520 });
      return { left: placement.left, top: placement.top };
    }
    return {};
  }, [anchor, viewport, style]);

  return (
    <section
      role="dialog"
      aria-label="Generation composer"
      className="absolute z-40 w-[520px] rounded-xl border border-glass-border bg-elevated/96 shadow-2xl shadow-black/40 backdrop-blur-xl"
      style={computedStyle}
    >
      {/* Tabs */}
      <div className="flex items-center justify-between gap-1 border-b border-border-subtle px-2 py-1.5">
        <div role="tablist" aria-label="Generation type" className="flex items-center gap-0.5">
          {TABS.map(t => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={t === activeTab}
              onClick={() => onTabChange?.(t)}
              className={`rounded-md px-2 py-1 text-[11px] font-medium transition ${
                t === activeTab ? "bg-primary/15 text-primary" : "text-text-muted hover:text-foreground hover:bg-hover-bg"
              }`}>
              {t}
            </button>
          ))}
        </div>
        <button type="button" aria-label="Close composer" data-tip="Close" onClick={onClose}
                className="btn-tip rounded p-1 text-text-muted hover:bg-hover-bg hover:text-foreground">
          <X size={12} aria-hidden="true" />
        </button>
      </div>

      {/* Capability mismatch banner */}
      {showCapabilityMismatch && (
        <div role="alert" className="mx-3 mt-2 rounded-md border border-amber-300/40 bg-amber-400/[0.06] px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-100">
          <strong className="font-semibold">{modelLabel}</strong> doesn&apos;t accept some of the attached references.
        </div>
      )}

      {/* Reference row */}
      <div className="flex items-center gap-1.5 px-3 pt-2.5">
        {refs.map((r, i) => (
          <div key={i} className="relative h-10 w-14 overflow-hidden rounded border border-white/10 bg-black/30">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={r.src} alt={`Reference ${i + 1}`} loading="lazy" decoding="async" className="h-full w-full object-cover" />
            {r.role && <span aria-hidden="true" className="absolute right-0 top-0 rounded-bl bg-black/60 px-1 py-0.5 text-[9px] uppercase text-white/80">{r.role}</span>}
          </div>
        ))}
        <button type="button" aria-label="Add reference" data-tip="Add reference"
                className="btn-tip grid h-10 w-10 place-items-center rounded border border-dashed border-glass-border text-text-muted hover:border-primary/60 hover:text-primary">
          <Plus size={14} aria-hidden="true" />
        </button>
        {refs.length > 0 && (
          <span className="ml-1 font-mono text-[10px] text-text-muted">{refs.length} ref · drag to reorder</span>
        )}
      </div>

      {/* Prompt */}
      <div className="px-3 pt-2">
        <textarea
          aria-label="Prompt"
          rows={3}
          defaultValue={prompt}
          placeholder="Describe what you want to generate. Use @ to mention a canvas node."
          className="w-full resize-none rounded-md border border-glass-border bg-input-bg px-2.5 py-2 text-[13px] text-foreground placeholder:text-text-muted outline-none focus:border-primary/60"
        />
      </div>

      {/* Chip row */}
      <div className="flex items-center justify-between gap-1.5 px-3 pb-3 pt-2">
        <div className="flex items-center gap-1.5">
          <ChipDropdown label="Model"    value={modelLabel} primary />
          <ChipDropdown label="Aspect"   value={aspect} />
          <ChipDropdown label="Duration" value={duration} />
          <button type="button" aria-label="More params" data-tip="Seed / guidance / motion"
                  className="btn-tip rounded-md border border-glass-border bg-glass p-1.5 text-text-secondary hover:bg-hover-bg hover:text-foreground">
            <Settings size={12} aria-hidden="true" />
          </button>
          <ChipDropdown label="Count" value={count} />
        </div>
        <button
          type="button"
          aria-label="Submit"
          data-tip="Submit (⌘⏎)"
          disabled={showCapabilityMismatch}
          onClick={() => onSubmit?.({ tab: activeTab, prompt, modelLabel, aspect, duration, count, refs })}
          className={`btn-tip grid h-7 w-7 place-items-center rounded-full transition ${
            showCapabilityMismatch
              ? "bg-primary/40 text-white/70 cursor-not-allowed"
              : "bg-primary text-white hover:bg-primary/90"
          }`}>
          <Wand2 size={12} aria-hidden="true" />
        </button>
      </div>
    </section>
  );
}

export { CapabilityIcon } from "./CapabilityIcon";
export { ChipDropdown } from "./ChipDropdown";
export { composerPlacement } from "./positioning";
export type { ComposerAnchor, ComposerViewport, ComposerPlacement, ComposerSize } from "./positioning";
