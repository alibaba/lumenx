"use client";
import { ChevronDown } from "lucide-react";
import { ReactNode } from "react";

interface Option { value: string; label: string; }

interface Props {
  label: string;        // accessible name + tooltip; not visible in the chip itself
  value: string;        // displayed inside the chip
  options?: Option[];
  onChange?: (v: string) => void;
  primary?: boolean;
  disabled?: boolean;
  children?: ReactNode; // overrides options when provided (advanced popovers)
}

/**
 * ChipDropdown — compact value-first chip. The kind label ("Model", "Aspect"
 * etc.) lives in the popover header + the data-tip tooltip, not on the chip
 * face. Showing both label + value made narrow rows wrap mid-chip; this
 * keeps the chrome row a single line at any reasonable Composer width.
 */
export function ChipDropdown({ label, value, options, onChange, primary, disabled, children }: Props) {
  return (
    <details className="relative inline-block shrink-0">
      <summary
        className={`btn-tip inline-flex h-7 shrink-0 cursor-pointer list-none items-center gap-1 whitespace-nowrap rounded-md border px-2 transition-all duration-150 active:scale-[0.98] ${
          primary
            ? "border-atelier-brand-400/35 bg-atelier-brand-400/[0.08] text-atelier-brand-400 hover:border-atelier-brand-400/55 hover:bg-atelier-brand-400/[0.12] hover:shadow-[0_0_0_1px_rgba(59,107,255,0.2)]"
            : disabled
            ? "border-white/8 bg-black/20 text-text-muted/60 cursor-not-allowed"
            : "border-white/8 bg-black/25 text-foreground/95 hover:border-white/14 hover:bg-white/[0.05]"
        }`}
        aria-label={label}
        aria-disabled={disabled}
        data-tip={label}
      >
        <span className="text-[11.5px] tracking-tight">{value}</span>
        <ChevronDown size={9} className="text-text-muted/70 transition-transform duration-200 group-open:rotate-180" aria-hidden="true" />
      </summary>
      <div className="absolute left-0 top-full z-50 mt-1.5 min-w-[180px] origin-top overflow-hidden rounded-md border border-white/8 bg-[#141416] shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none">
        <div className="border-b border-white/8 px-2.5 py-2 text-[11px] text-white/45">
          {label}
        </div>
        <div className="p-1">
          {children ?? options?.map(o => (
            <button key={o.value}
              type="button"
              onClick={() => onChange?.(o.value)}
              className={`flex w-full items-center justify-between gap-2 rounded px-2 py-[6px] text-left text-[12px] transition-colors hover:bg-white/[0.05] ${
                o.value === value ? "text-atelier-brand-400" : "text-text-secondary hover:text-foreground"
              }`}>
              <span>{o.label}</span>
              {o.value === value ? (
                <span aria-hidden="true" className="text-[10px] tracking-[0.01em] text-atelier-brand-400/85">
                  Current
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </div>
    </details>
  );
}
