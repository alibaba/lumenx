"use client";
import { ChevronDown } from "lucide-react";
import { ReactNode } from "react";

interface Option { value: string; label: string; }

interface Props {
  label: string;        // accessible name + mono caps tag prepended to the chip
  value: string;        // displayed inside the chip
  options?: Option[];
  onChange?: (v: string) => void;
  primary?: boolean;
  disabled?: boolean;
  children?: ReactNode; // overrides options when provided (advanced popovers)
}

/**
 * ChipDropdown — pairs a small mono-caps label ('MODEL', 'ASPECT', etc) with
 * the active value in display weight. Reads as instrument readout, matches
 * the chrome metadata vocabulary used on save chip / inspector pill.
 */
export function ChipDropdown({ label, value, options, onChange, primary, disabled, children }: Props) {
  const labelTone = primary ? "text-primary/85" : "text-text-muted/70";
  return (
    <details className="relative inline-block">
      <summary
        className={`btn-tip inline-flex h-7 cursor-pointer list-none items-center gap-1.5 rounded-md border px-2 transition-colors ${
          primary
            ? "border-primary/30 bg-primary/[0.06] text-foreground hover:border-primary/45 hover:bg-primary/[0.1]"
            : disabled
            ? "border-white/8 bg-black/20 text-text-muted/60 cursor-not-allowed"
            : "border-white/8 bg-black/25 text-text-secondary hover:border-white/14 hover:bg-white/[0.04] hover:text-foreground"
        }`}
        aria-label={label}
        aria-disabled={disabled}
        data-tip={label}
      >
        <span className={`font-mono text-[9px] font-medium uppercase tracking-[0.18em] ${labelTone}`}>
          {label}
        </span>
        <span className={`text-[11.5px] tracking-tight ${primary ? "text-primary" : "text-foreground/95"}`}>
          {value}
        </span>
        <ChevronDown size={9} className="text-text-muted/70" aria-hidden="true" />
      </summary>
      <div className="absolute left-0 top-full z-50 mt-1.5 min-w-[180px] rounded-md border border-white/8 bg-[#141416] p-1 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl">
        <div className="px-2 pb-1 pt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-text-muted/80">
          {label}
        </div>
        {children ?? options?.map(o => (
          <button key={o.value}
            type="button"
            onClick={() => onChange?.(o.value)}
            className={`flex w-full items-center justify-between gap-2 rounded px-2 py-[6px] text-left text-[12px] transition-colors hover:bg-white/[0.05] ${
              o.value === value ? "text-primary" : "text-text-secondary hover:text-foreground"
            }`}>
            <span>{o.label}</span>
            {o.value === value ? (
              <span aria-hidden="true" className="font-mono text-[9px] uppercase tracking-[0.2em] text-primary/80">
                Current
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </details>
  );
}
