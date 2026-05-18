"use client";
//
// Lightweight confirm + prompt dialogs in the Atelier visual vocabulary so
// destructive actions and renames don't break the cinematic frame with the
// browser-native window.confirm / window.prompt chrome. Both dialogs share
// the modal-overlay-in / modal-content-in keyframes that the rest of the
// shell uses.
//
// Usage:
//   <ConfirmDialog open={...} title="Delete this node?" body="..."
//                  confirmLabel="Delete" tone="danger"
//                  onConfirm={...} onCancel={...} />
//   <PromptDialog open={...} title="Rename project"
//                 initialValue={p.title} placeholder="Untitled"
//                 onSubmit={(v) => ...} onCancel={...} />
//
// Both close on Escape; PromptDialog also commits on Enter. Focus is
// restored to the previously focused element when the dialog unmounts (the
// existing focus-restore effect in AtelierShellV3 already covers this since
// our dialogs render with role="dialog").
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

// Minimal focus trap: on Tab, cycle through focusable descendants of `root`
// instead of letting focus drift to the underlying canvas. Returns nothing
// because we attach the listener inside an effect — cleanup on unmount.
function useFocusTrap(rootRef: React.RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const root = rootRef.current;
    if (!root) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusables = root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const activeEl = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (activeEl === first || !root.contains(activeEl)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (activeEl === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rootRef, active]);
}

interface ConfirmProps {
  open: boolean;
  title: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "primary",
  onConfirm,
  onCancel,
}: ConfirmProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, open);
  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter") {
        e.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel, onConfirm]);

  if (!open) return null;
  const isDanger = tone === "danger";
  const accentRail = isDanger
    ? "from-red-400 via-red-400/45 to-transparent"
    : "from-primary via-primary/45 to-transparent";
  const confirmClass = isDanger
    ? "bg-red-500/85 text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_14px_-4px_rgba(248,113,113,0.55)] hover:bg-red-500/95"
    : "bg-primary text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_14px_-4px_rgba(100,108,255,0.55)] hover:bg-primary/92";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-[60] grid place-items-center bg-black/75 backdrop-blur-md animate-atelier-modal-overlay-in motion-reduce:animate-none"
      onClick={onCancel}
    >
      <div
        ref={dialogRef}
        className="w-[400px] overflow-hidden rounded-[14px] border border-white/8 bg-[#141416] shadow-[0_32px_60px_-26px_rgba(0,0,0,0.85),0_8px_18px_-6px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] animate-atelier-modal-content-in motion-reduce:animate-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div aria-hidden="true" className={`h-[2px] bg-gradient-to-r ${accentRail}`} />
        <div className="px-4 pb-3.5 pt-3.5">
          <div className="mb-2.5 flex items-start justify-between gap-3">
            <h3 className="font-display text-[14px] font-medium tracking-[-0.005em] text-foreground">
              {title}
            </h3>
            <button
              onClick={onCancel}
              aria-label="Close"
              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
            >
              <X size={13} aria-hidden="true" />
            </button>
          </div>
          {body ? (
            <p className="mb-4 text-[12.5px] leading-[1.55] text-text-secondary/95">{body}</p>
          ) : (
            <div className="mb-4" />
          )}
          <div className="grid grid-cols-2 gap-2">
            <button
              ref={confirmRef}
              onClick={onConfirm}
              className={`inline-flex items-center justify-center rounded-md px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.18em] transition-all duration-200 hover:scale-[1.02] active:scale-[0.97] ${confirmClass}`}
            >
              {confirmLabel}
            </button>
            <button
              onClick={onCancel}
              className="rounded-md border border-white/10 bg-black/25 px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97]"
            >
              {cancelLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

interface PromptProps {
  open: boolean;
  title: string;
  description?: string;
  initialValue?: string;
  placeholder?: string;
  submitLabel?: string;
  cancelLabel?: string;
  /** When true, render a multi-line textarea instead of a single-line
   *  input. Trimmed-empty strings still block submit. */
  multiline?: boolean;
  /** Allow saving an empty value. Default false (single-line names like
   *  project title need a value); multi-line descriptions can be
   *  cleared. */
  allowEmpty?: boolean;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

export function PromptDialog({
  open,
  title,
  description,
  initialValue = "",
  placeholder,
  submitLabel = "Save",
  cancelLabel = "Cancel",
  multiline = false,
  allowEmpty = false,
  onSubmit,
  onCancel,
}: PromptProps) {
  const [value, setValue] = useState(initialValue);
  // The ref's element type depends on `multiline`; we keep one ref of the
  // union type and cast at the assignment site to keep React happy.
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, open);

  // Reset draft to the latest initial value every time the dialog opens —
  // re-using the same dialog for different rows wouldn't carry stale state.
  useEffect(() => {
    if (open) {
      setValue(initialValue);
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [open, initialValue]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;
  const trimmed = value.trim();

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-[60] grid place-items-center bg-black/75 backdrop-blur-md animate-atelier-modal-overlay-in motion-reduce:animate-none"
      onClick={onCancel}
    >
      <div
        ref={dialogRef}
        className="w-[420px] overflow-hidden rounded-[14px] border border-white/8 bg-[#141416] shadow-[0_32px_60px_-26px_rgba(0,0,0,0.85),0_8px_18px_-6px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] animate-atelier-modal-content-in motion-reduce:animate-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-primary via-primary/45 to-transparent" />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!allowEmpty && !trimmed) return;
            onSubmit(trimmed);
          }}
          className="px-4 pb-3.5 pt-3.5"
        >
          <div className="mb-2.5 flex items-start justify-between gap-3">
            <h3 className="font-display text-[14px] font-medium tracking-[-0.005em] text-foreground">
              {title}
            </h3>
            <button
              type="button"
              onClick={onCancel}
              aria-label="Close"
              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
            >
              <X size={13} aria-hidden="true" />
            </button>
          </div>
          {description ? (
            <p className="mb-2.5 text-[12.5px] leading-[1.55] text-text-secondary/95">{description}</p>
          ) : null}
          {multiline ? (
            <textarea
              ref={(el) => { inputRef.current = el; }}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={placeholder}
              rows={4}
              className="mb-3.5 w-full resize-none rounded-md border border-white/8 bg-black/35 px-3 py-2 text-[13px] leading-[1.55] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-primary/55 focus:bg-black/45"
            />
          ) : (
            <input
              ref={(el) => { inputRef.current = el; }}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={placeholder}
              className="mb-3.5 w-full rounded-md border border-white/8 bg-black/35 px-3 py-2 text-[13px] leading-[1.55] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-primary/55 focus:bg-black/45"
            />
          )}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="submit"
              disabled={!allowEmpty && !trimmed}
              className="inline-flex items-center justify-center rounded-md bg-primary px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_14px_-4px_rgba(100,108,255,0.55)] transition-all duration-200 hover:scale-[1.02] hover:bg-primary/92 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:scale-100"
            >
              {submitLabel}
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-white/10 bg-black/25 px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97]"
            >
              {cancelLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
