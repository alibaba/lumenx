"use client";
//
// Advanced params popover — wired from the Composer's gear button.
// Surfaces model-specific generation knobs the user actually wants:
//   - Negative prompt (most i2v models support this)
//   - Seed lock (reproducibility — type a number, or click 🎲 to roll)
//   - cfgScale (Kling) — model declares min/max/step/default
//   - mode / sound / movementAmplitude — model-specific extras
//
// Reads `getModelByDisplayName(label).params` to know which fields the
// active model supports; only renders supported fields. Empty state
// (no fields supported) shows a friendly placeholder.
import { useEffect, useRef } from "react";
import { Dice5, Lock, Unlock, X } from "lucide-react";
import { getModelByDisplayName } from "@/lib/modelCatalog";

export interface AdvancedParamsValue {
  negativePrompt?: string;
  seed?: number | null;     // null = unlocked (model rolls), number = locked
  cfgScale?: number;
  mode?: string;
  movementAmplitude?: string;
  sound?: boolean;
}

interface Props {
  open: boolean;
  modelLabel: string;
  value: AdvancedParamsValue;
  onChange: (next: AdvancedParamsValue) => void;
  onClose: () => void;
  /** Anchor position relative to which the popover opens. The Composer
   *  passes the gear button's bounding rect so the popover sits above
   *  it with a small gap. */
  anchorRect?: { left: number; top: number; width: number; height: number };
}

export function AdvancedPopover({ open, modelLabel, value, onChange, onClose, anchorRect }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  // Esc to close + outside click — wired here rather than in the shell
  // because the popover is short-lived UI scoped to the Composer.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onDown = (e: MouseEvent) => {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener("keydown", onKey);
    // Defer outside-click registration by one frame so the click that
    // opened the popover doesn't immediately close it.
    const id = requestAnimationFrame(() => {
      window.addEventListener("mousedown", onDown);
    });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      cancelAnimationFrame(id);
    };
  }, [open, onClose]);

  if (!open) return null;
  const model = getModelByDisplayName(modelLabel);
  // Catalog params live one level deep — getModelByDisplayName returns
  // the raw catalog entry; access params via the typed catalog.
  const params = (model as unknown as { params?: Record<string, unknown> } | undefined)?.params ?? {};
  const supportsNegative = !!params.negativePrompt;
  const supportsSeed = !!params.seed;
  const cfgScale = params.cfgScale as { min: number; max: number; step: number; default: number } | undefined;
  const supportsMode = !!params.mode;
  const supportsMovement = !!params.movementAmplitude;
  const supportsSound = !!params.sound;
  const anyField =
    supportsNegative || supportsSeed || !!cfgScale || supportsMode || supportsMovement || supportsSound;
  const modeOptions = (params.mode as { options?: string[] } | undefined)?.options ?? [];
  const movementOptions = (params.movementAmplitude as { options?: string[] } | undefined)?.options ?? [];

  // Position: above the anchor with a 10 px gap. Width 320. If the
  // anchor isn't supplied (test / fallback), center near the top of
  // the viewport.
  const popoverWidth = 320;
  const style: React.CSSProperties = anchorRect
    ? {
        left: Math.max(12, anchorRect.left + anchorRect.width / 2 - popoverWidth / 2),
        top: Math.max(12, anchorRect.top - 10 /* will negative-translate via CSS */),
        transform: "translateY(-100%)",
      }
    : { left: 16, top: 16 };

  const seedLocked = typeof value.seed === "number";

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Advanced generation params"
      className="fixed z-[55] w-[320px] overflow-hidden rounded-[12px] border border-white/8 bg-[#141416]/96 shadow-[0_28px_60px_-24px_rgba(0,0,0,0.85),0_8px_20px_-6px_rgba(0,0,0,0.6),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
      style={style}
    >
      <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-primary via-primary/45 to-transparent" />
      <div className="flex items-center justify-between gap-2 border-b border-dashed border-white/8 px-3 py-1.5">
        <span aria-hidden="true" className="font-mono text-[8.5px] font-medium uppercase tracking-[0.32em] text-text-muted/85">
          Atelier · Advanced · {modelLabel}
        </span>
        <button
          type="button"
          aria-label="Close"
          onClick={onClose}
          className="inline-flex h-5 w-5 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
        >
          <X size={11} aria-hidden="true" />
        </button>
      </div>

      {!anyField ? (
        <div className="px-3.5 py-3 text-center font-mono text-[10px] uppercase tracking-[0.22em] text-text-muted/85">
          No advanced params for this model
        </div>
      ) : (
        <div className="space-y-3 px-3.5 py-3">
          {supportsNegative ? (
            <div>
              <label className="mb-1 block font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                Negative prompt
              </label>
              <textarea
                value={value.negativePrompt ?? ""}
                onChange={(e) => onChange({ ...value, negativePrompt: e.target.value })}
                rows={2}
                placeholder="extra fingers, deformed hands, blurry…"
                className="w-full resize-none rounded-md border border-white/8 bg-black/35 px-2.5 py-1.5 text-[12px] leading-[1.45] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-primary/55 focus:bg-black/45"
              />
            </div>
          ) : null}

          {supportsSeed ? (
            <div>
              <label className="mb-1 flex items-center justify-between gap-2 font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                <span>Seed</span>
                <span className="font-display text-[10px] tracking-tight text-text-muted/65">
                  {seedLocked ? "Locked" : "Random"}
                </span>
              </label>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  aria-label={seedLocked ? "Unlock seed (random)" : "Lock seed (reproducible)"}
                  data-tip={seedLocked ? "Click to unlock — model will roll a new seed each run" : "Click to lock — same seed gives controlled variations"}
                  onClick={() =>
                    onChange({
                      ...value,
                      seed: seedLocked ? null : Math.floor(Math.random() * 1_000_000_000),
                    })
                  }
                  className="btn-tip grid h-7 w-7 shrink-0 place-items-center rounded-md border border-white/8 bg-black/25 text-text-secondary transition-colors hover:bg-white/[0.05] hover:text-foreground"
                >
                  {seedLocked ? <Lock size={11} aria-hidden="true" /> : <Unlock size={11} aria-hidden="true" />}
                </button>
                <input
                  type="number"
                  value={typeof value.seed === "number" ? value.seed : ""}
                  onChange={(e) => {
                    const v = e.target.value.trim();
                    onChange({ ...value, seed: v === "" ? null : Number(v) });
                  }}
                  placeholder="random"
                  disabled={!seedLocked}
                  className="flex-1 rounded-md border border-white/8 bg-black/35 px-2 py-[5px] font-mono text-[12px] text-foreground outline-none placeholder:text-text-muted/85 transition-colors focus:border-primary/55 focus:bg-black/45 disabled:cursor-not-allowed disabled:opacity-55"
                />
                <button
                  type="button"
                  aria-label="Roll a new seed"
                  data-tip="Roll new seed"
                  onClick={() =>
                    onChange({ ...value, seed: Math.floor(Math.random() * 1_000_000_000) })
                  }
                  disabled={!seedLocked}
                  className="btn-tip grid h-7 w-7 shrink-0 place-items-center rounded-md border border-white/8 bg-black/25 text-text-secondary transition-colors hover:bg-white/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <Dice5 size={11} aria-hidden="true" />
                </button>
              </div>
            </div>
          ) : null}

          {cfgScale ? (
            <div>
              <label className="mb-1 flex items-center justify-between gap-2 font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                <span>Guidance · cfgScale</span>
                <span className="font-display text-[10px] tracking-tight text-foreground/95">
                  {(value.cfgScale ?? cfgScale.default).toFixed(1)}
                </span>
              </label>
              <input
                type="range"
                min={cfgScale.min}
                max={cfgScale.max}
                step={cfgScale.step}
                value={value.cfgScale ?? cfgScale.default}
                onChange={(e) => onChange({ ...value, cfgScale: Number(e.target.value) })}
                className="h-1 w-full cursor-pointer appearance-none rounded-full bg-white/8 accent-primary"
              />
            </div>
          ) : null}

          {supportsMode && modeOptions.length > 0 ? (
            <div>
              <label className="mb-1 block font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                Mode
              </label>
              <div className="flex items-center gap-1">
                {modeOptions.map((opt) => {
                  const active = (value.mode ?? (params.mode as { default?: string } | undefined)?.default) === opt;
                  return (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => onChange({ ...value, mode: opt })}
                      className={`flex-1 rounded-md px-2 py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.18em] transition-colors ${
                        active
                          ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_rgba(100,108,255,0.3)]"
                          : "border border-white/8 text-text-muted hover:bg-white/[0.04] hover:text-foreground"
                      }`}
                    >
                      {opt}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {supportsMovement && movementOptions.length > 0 ? (
            <div>
              <label className="mb-1 block font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                Movement
              </label>
              <div className="flex flex-wrap items-center gap-1">
                {movementOptions.map((opt) => {
                  const active =
                    (value.movementAmplitude ??
                      (params.movementAmplitude as { default?: string } | undefined)?.default) === opt;
                  return (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => onChange({ ...value, movementAmplitude: opt })}
                      className={`rounded-md px-2 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.18em] transition-colors ${
                        active
                          ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_rgba(100,108,255,0.3)]"
                          : "border border-white/8 text-text-muted hover:bg-white/[0.04] hover:text-foreground"
                      }`}
                    >
                      {opt}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {supportsSound ? (
            <label className="flex items-center justify-between gap-2 font-mono text-[10px] font-medium uppercase tracking-[0.22em] text-text-muted/85">
              <span>Sound</span>
              <input
                type="checkbox"
                checked={!!value.sound}
                onChange={(e) => onChange({ ...value, sound: e.target.checked })}
                className="h-3.5 w-3.5 cursor-pointer accent-primary"
              />
            </label>
          ) : null}
        </div>
      )}
    </div>
  );
}
