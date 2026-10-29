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
import { useTranslations } from "next-intl";

interface ModalFocusOptions {
  initialFocusRef: React.RefObject<HTMLElement | null>;
  restoreFocusRef?: React.RefObject<HTMLElement | null>;
  onEscape: () => void;
}

// Existing dialogs keep their own focus/Enter/Escape lifecycle. Opting in to
// modalOptions adds standalone initial focus, containment, Escape and restore
// for portalled editors without depending on the Shell's dialog observer.
export function useFocusTrap(
  rootRef: React.RefObject<HTMLElement | null>,
  active: boolean,
  modalOptions?: ModalFocusOptions,
) {
  const optionsRef = useRef(modalOptions);
  optionsRef.current = modalOptions;
  const modal = !!modalOptions;
  useEffect(() => {
    if (!active) return;
    const root = rootRef.current;
    if (!root) return;
    const restoreTarget = optionsRef.current?.restoreFocusRef?.current ?? document.activeElement;
    const focusables = () => Array.from(root.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((el) => el.tabIndex >= 0 && !el.closest('[hidden], [inert], [aria-hidden="true"]'));
    const focusInitial = () => (optionsRef.current?.initialFocusRef.current ?? focusables()[0] ?? root).focus();
    const onFocus = (e: FocusEvent) => {
      if (e.target instanceof Node && !root.contains(e.target)) focusInitial();
    };
    const onKey = (e: KeyboardEvent) => {
      if (modal && e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        optionsRef.current?.onEscape();
        return;
      }
      if (e.key !== "Tab") return;
      const elements = focusables();
      if (elements.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      const activeEl = document.activeElement;
      if (e.shiftKey ? activeEl === first || !root.contains(activeEl) : activeEl === last || !root.contains(activeEl)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    if (modal) {
      focusInitial();
      document.addEventListener("focusin", onFocus);
    }
    window.addEventListener("keydown", onKey, modal);
    return () => {
      window.removeEventListener("keydown", onKey, modal);
      if (modal) {
        document.removeEventListener("focusin", onFocus);
        if (restoreTarget instanceof HTMLElement && restoreTarget.isConnected) restoreTarget.focus();
      }
    };
  }, [rootRef, active, modal]);
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
  confirmLabel,
  cancelLabel,
  tone = "primary",
  onConfirm,
  onCancel,
}: ConfirmProps) {
  // i18n · label defaults resolve through the catalog so callers that
  // don't pass explicit labels still localize.
  const t = useTranslations("atelier.dialog");
  const confirmText = confirmLabel ?? t("confirm");
  const cancelText = cancelLabel ?? t("cancel");
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
    : "from-atelier-brand-400 via-atelier-brand-400/45 to-transparent";
  const confirmClass = isDanger
    ? "bg-red-500/85 text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_14px_-4px_rgba(248,113,113,0.55)] hover:bg-red-500/95"
    : "bg-atelier-brand-400 text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_14px_-4px_rgba(59,107,255,0.55)] hover:bg-atelier-brand-400/92";

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
        className="w-[400px] overflow-hidden rounded-[14px] border border-white/[0.08] bg-[#141416] shadow-[0_32px_60px_-26px_rgba(0,0,0,0.85),0_8px_18px_-6px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] animate-atelier-modal-content-in motion-reduce:animate-none"
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
              className={`inline-flex items-center justify-center rounded-md px-3 py-2 text-[11px] font-medium transition-all duration-200 hover:scale-[1.02] active:scale-[0.97] ${confirmClass}`}
            >
              {confirmText}
            </button>
            <button
              onClick={onCancel}
              className="rounded-md border border-white/10 bg-black/25 px-3 py-2 text-[11px] font-medium text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97]"
            >
              {cancelText}
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
  submitLabel,
  cancelLabel,
  multiline = false,
  allowEmpty = false,
  onSubmit,
  onCancel,
}: PromptProps) {
  const t = useTranslations("atelier.dialog");
  const submitText = submitLabel ?? t("save");
  const cancelText = cancelLabel ?? t("cancel");
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
        className="w-[420px] overflow-hidden rounded-[14px] border border-white/[0.08] bg-[#141416] shadow-[0_32px_60px_-26px_rgba(0,0,0,0.85),0_8px_18px_-6px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] animate-atelier-modal-content-in motion-reduce:animate-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-atelier-brand-400 via-atelier-brand-400/45 to-transparent" />
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
              className="mb-3.5 w-full resize-none rounded-md border border-white/[0.08] bg-black/35 px-3 py-2 text-[13px] leading-[1.55] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-atelier-brand-400/55 focus:bg-black/45"
            />
          ) : (
            <input
              ref={(el) => { inputRef.current = el; }}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={placeholder}
              className="mb-3.5 w-full rounded-md border border-white/[0.08] bg-black/35 px-3 py-2 text-[13px] leading-[1.55] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-atelier-brand-400/55 focus:bg-black/45"
            />
          )}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="submit"
              disabled={!allowEmpty && !trimmed}
              className="inline-flex items-center justify-center rounded-md bg-atelier-brand-400 px-3 py-2 text-[11px] font-medium text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_14px_-4px_rgba(59,107,255,0.55)] transition-all duration-200 hover:scale-[1.02] hover:bg-atelier-brand-400/92 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:scale-100"
            >
              {submitText}
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-white/10 bg-black/25 px-3 py-2 text-[11px] font-medium text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97]"
            >
              {cancelText}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
