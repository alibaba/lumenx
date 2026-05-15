"use client";
import { ChevronDown } from "lucide-react";
import { ReactNode } from "react";

interface Option { value: string; label: string; }

interface Props {
  label: string;        // accessible name (e.g. "Model")
  value: string;        // displayed inside the chip
  options?: Option[];
  onChange?: (v: string) => void;
  primary?: boolean;
  disabled?: boolean;
  children?: ReactNode; // overrides options when provided (advanced popovers)
}

export function ChipDropdown({ label, value, options, onChange, primary, disabled, children }: Props) {
  return (
    <details className="relative inline-block">
      <summary
        className={`btn-tip inline-flex cursor-pointer list-none items-center gap-1 rounded-md border border-glass-border px-2 py-1.5 text-[11px] transition ${
          primary ? "bg-glass text-foreground hover:bg-hover-bg" :
          disabled ? "bg-glass text-text-muted/60 cursor-not-allowed" :
                     "bg-glass text-text-secondary hover:bg-hover-bg hover:text-foreground"
        }`}
        aria-label={label}
        aria-disabled={disabled}
        data-tip={label}
      >
        <span className="font-medium">{value}</span>
        <ChevronDown size={10} className="text-text-muted" aria-hidden="true" />
      </summary>
      <div className="absolute left-0 top-full z-50 mt-1 min-w-[160px] rounded-md border border-glass-border bg-elevated p-1 shadow-2xl shadow-black/40">
        {children ?? options?.map(o => (
          <button key={o.value}
            type="button"
            onClick={() => onChange?.(o.value)}
            className={`block w-full rounded px-2 py-1 text-left text-[12px] hover:bg-hover-bg ${
              o.value === value ? "text-primary" : "text-text-secondary"
            }`}>
            {o.label}
          </button>
        ))}
      </div>
    </details>
  );
}
