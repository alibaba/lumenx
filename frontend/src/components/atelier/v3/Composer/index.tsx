"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { Settings, Wand2, X, Plus, Trash2 } from "lucide-react";
import { CapabilityIcon } from "./CapabilityIcon";
import { ChipDropdown } from "./ChipDropdown";
import { composerPlacement, type ComposerAnchor, type ComposerViewport } from "./positioning";
import { validateAtelierRefs, type AtelierRefKind } from "@/lib/modelCatalog";
import { TearLine } from "../ornaments";
import { AdvancedPopover, type AdvancedParamsValue } from "./AdvancedPopover";

const TABS = ["T2I", "I2I", "T2V", "I2V", "R2V", "V2V", "Audio"] as const;
export type ComposerTab = typeof TABS[number];

// Default option sets — parent can override via *Options props.
const DEFAULT_MODELS = ["Wan 2.7", "HappyHorse R2V", "Vidu Q3", "Seedance 2.0"];
const DEFAULT_ASPECTS = ["16:9 · 720p", "16:9 · 1080p", "9:16 · 720p", "1:1 · 720p"];
const DEFAULT_DURATIONS = ["3s", "5s", "10s"];
const DEFAULT_COUNTS = ["1×", "2×", "4×", "6×"];

export interface ComposerSubmitPayload {
  tab: ComposerTab;
  prompt: string;
  modelLabel: string;
  aspect: string;
  duration: string;
  count: string;
  refs: ComposerRef[];
  /** Advanced model-specific params (negative prompt, seed lock,
   *  cfgScale, mode, motion, sound). Only fields the active model
   *  actually supports populate; the rest stay undefined. Forwarded
   *  by the shell into createVideoCandidates' `params` payload. */
  advanced?: AdvancedParamsValue;
}

export interface ComposerRef {
  src: string;
  role?: string;     // "ref" | "ff" | "vid"
  /** Source-node media type. Wired by the shell from each ref node so the
   *  Composer can validate against the chosen model's accepted reference
   *  types (e.g., I2V models reject video references). When omitted the
   *  ref is treated as `image`. */
  kind?: AtelierRefKind;
}

export interface ComposerMentionable {
  id: string;
  label: string;
  kind?: "image" | "video" | "audio" | "draft" | "idea" | "plan";
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
  modelOptions?: string[];
  aspectOptions?: string[];
  durationOptions?: string[];
  countOptions?: string[];
  /** External override. When `undefined`, the Composer derives the mismatch
   *  state from `modelLabel` + ref `kind`s using the model catalog. Pass
   *  `false` only when you want to force-suppress the banner (e.g., during
   *  preview). */
  showCapabilityMismatch?: boolean;
  onClose?: () => void;
  onSubmit?: (payload: ComposerSubmitPayload) => void;
  onAddRef?: () => void;
  onRemoveRef?: (index: number) => void;
  onAdvanced?: () => void;
  /** Called on textarea blur if the prompt changed since mount/last save —
   *  lets the parent persist the draft so typed content survives close. */
  onPromptCommit?: (next: string) => void;
  /** Nodes available for @ mention. Typing @ in the prompt opens a picker
   *  filtered against these labels; selecting inserts `@<label>`. */
  mentionables?: ComposerMentionable[];

  // Position: either anchor + viewport, OR explicit style.
  anchor?: ComposerAnchor | null;
  viewport?: ComposerViewport;
  style?: React.CSSProperties;

  /** When true, the Composer renders as a regular block element with no
   *  absolute positioning, no fixed width, no entry animation, no
   *  shadow/border (the parent provides the chrome). Designed to be
   *  embedded *inside* a draft node so the node itself becomes the
   *  generation workbench — RHTV / LibTV pattern (see Atelier
   *  competitive research, §4.4 / §6.1 / §7.4). When false (default),
   *  the Composer floats above the canvas via anchor + viewport
   *  positioning. */
  inline?: boolean;
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
  modelOptions = DEFAULT_MODELS,
  aspectOptions = DEFAULT_ASPECTS,
  durationOptions = DEFAULT_DURATIONS,
  countOptions = DEFAULT_COUNTS,
  showCapabilityMismatch,
  onClose,
  onSubmit,
  onAddRef,
  onRemoveRef,
  onAdvanced,
  onPromptCommit,
  mentionables,
  anchor,
  viewport,
  style,
  inline = false,
}: Props) {
  const computedStyle = useMemo<React.CSSProperties>(() => {
    if (style) return style;
    if (viewport) {
      const placement = composerPlacement(anchor ?? null, viewport, { width: 520 });
      return { left: placement.left, top: placement.top };
    }
    return {};
  }, [anchor, viewport, style]);

  const [draft, setDraft] = useState(prompt);
  useEffect(() => { setDraft(prompt); }, [prompt]);

  // Local controlled chip state — initialized from props, kept in sync if
  // parent passes new values, but user picks update internal state which the
  // submit payload reads.
  const [m, setM] = useState(modelLabel);
  const [a, setA] = useState(aspect);
  const [d, setD] = useState(duration);
  const [c, setC] = useState(count);
  useEffect(() => setM(modelLabel), [modelLabel]);
  useEffect(() => setA(aspect),     [aspect]);
  useEffect(() => setD(duration),   [duration]);
  useEffect(() => setC(count),      [count]);

  // Advanced popover state — Composer-local; values flow into the
  // submit payload as `advanced`. The popover reads model capabilities
  // via the catalog and only renders supported fields (negative prompt,
  // seed lock, cfgScale, mode, movementAmplitude, sound).
  const [advanced, setAdvanced] = useState<AdvancedParamsValue>({});
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const advancedAnchorRef = useRef<HTMLButtonElement>(null);
  const [advancedAnchorRect, setAdvancedAnchorRect] = useState<{ left: number; top: number; width: number; height: number } | undefined>();
  // Switching to a different model resets advanced fields the new
  // model doesn't support — otherwise a stale negative_prompt could
  // ride along to a model that has no negative-prompt capability.
  useEffect(() => {
    setAdvanced({});
  }, [m]);

  // ── Capability mismatch (real validation) ───────────────────────────
  // Derived from the model catalog: each catalog entry declares its
  // `inputs.reference_images.{max, reference_type}` contract. We honor the
  // `showCapabilityMismatch` prop as an override (parent can force-clear
  // during preview), otherwise compute from current chip + ref kinds. The
  // banner shows the actual reason (e.g., "doesn't accept video references")
  // rather than a generic "some refs don't match" — gives the creator a clear
  // remediation path: swap model or detach the offending ref.
  const capabilityCheck = useMemo(
    () => validateAtelierRefs(m, refs.map((r) => ({ kind: r.kind }))),
    [m, refs],
  );
  const mismatchActive =
    typeof showCapabilityMismatch === "boolean"
      ? showCapabilityMismatch
      : !capabilityCheck.ok;
  const mismatchReason = capabilityCheck.reason;

  // ── @ mention picker ─────────────────────────────────────────────────
  // When the user types `@` in the prompt, we surface a popover of
  // matching nodes above the textarea. Tracks the @ position in the draft
  // so Enter / click can replace from `@<query>` → `@<label> ` cleanly.
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const filteredMentionables = useMemo(() => {
    if (!mention || !mentionables) return [];
    const q = mention.query.toLowerCase();
    if (q.length === 0) return mentionables.slice(0, 8);
    return mentionables
      .filter((m) => m.label.toLowerCase().includes(q))
      .slice(0, 8);
  }, [mention, mentionables]);
  // Re-clamp the active index whenever the filtered list shrinks under it.
  useEffect(() => {
    if (filteredMentionables.length === 0) {
      setMentionIndex(0);
      return;
    }
    if (mentionIndex >= filteredMentionables.length) setMentionIndex(0);
  }, [filteredMentionables, mentionIndex]);

  const detectMention = (value: string, cursor: number) => {
    if (!mentionables || mentionables.length === 0) {
      setMention(null);
      return;
    }
    const before = value.slice(0, cursor);
    const at = before.lastIndexOf("@");
    if (at < 0) {
      setMention(null);
      return;
    }
    // Must be at start of input or preceded by whitespace, not part of an
    // email-ish token.
    const prevChar = at > 0 ? before[at - 1] : "";
    if (prevChar && !/\s/.test(prevChar)) {
      setMention(null);
      return;
    }
    const between = before.slice(at + 1);
    if (/\s/.test(between)) {
      setMention(null);
      return;
    }
    setMention({ start: at, query: between });
  };

  const insertMention = (m: ComposerMentionable) => {
    if (!mention) return;
    const before = draft.slice(0, mention.start);
    const tail = draft.slice(mention.start + 1 + mention.query.length);
    const inserted = `@${m.label} `;
    const next = `${before}${inserted}${tail}`;
    setDraft(next);
    setMention(null);
    // Restore caret right after the inserted mention so the user keeps typing.
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (!ta) return;
      const caret = before.length + inserted.length;
      ta.focus();
      ta.setSelectionRange(caret, caret);
    });
  };

  const submit = () => {
    if (mismatchActive) return;
    onSubmit?.({ tab: activeTab, prompt: draft, modelLabel: m, aspect: a, duration: d, count: c, refs, advanced });
  };

  const tabIndexOf = (t: ComposerTab) => TABS.indexOf(t);
  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, t: ComposerTab) => {
    const i = tabIndexOf(t);
    let next: ComposerTab | null = null;
    if (event.key === "ArrowRight") next = TABS[(i + 1) % TABS.length];
    else if (event.key === "ArrowLeft") next = TABS[(i - 1 + TABS.length) % TABS.length];
    else if (event.key === "Home") next = TABS[0];
    else if (event.key === "End") next = TABS[TABS.length - 1];
    if (next != null) {
      event.preventDefault();
      onTabChange?.(next);
    }
  };

  // Two layout modes:
  //   - floating (default): absolute-positioned popup that hovers above
  //     the canvas, sized 520, with full chrome (shadow + border + blur).
  //   - inline: renders as a normal block, parent provides the chrome.
  //     This is the RHTV/LibTV in-node workbench pattern.
  const sectionClass = inline
    ? "relative w-full overflow-hidden bg-transparent"
    : "absolute z-40 w-[520px] origin-top overflow-hidden rounded-[14px] border border-white/8 bg-[#141416]/96 shadow-[0_28px_60px_-24px_rgba(0,0,0,0.85),0_8px_20px_-6px_rgba(0,0,0,0.6),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-composer-in motion-reduce:animate-none";
  return (
    <section
      role={inline ? "group" : "dialog"}
      aria-label="Generation composer"
      className={sectionClass}
      style={inline ? undefined : computedStyle}
    >
      {/* Editorial slip header — a faint top strip with mono-caps "OFFICIAL ·
          COMPOSER · NO. 001" that anchors the panel as an issued ticket. The
          number is fixed-style 001 just for that "rubber-stamped serial"
          reading; it isn't a real id. */}
      <div className="flex items-center justify-between gap-2 border-b border-dashed border-white/8 px-3 py-1.5">
        <span aria-hidden="true" className="font-mono text-[8.5px] font-medium uppercase tracking-[0.32em] text-text-muted/85">
          Atelier · Composer · No 001
        </span>
        <span aria-hidden="true" className="font-mono text-[8.5px] uppercase tracking-[0.28em] text-text-muted/55">
          {activeTab}
        </span>
      </div>

      {/* Tabs — mono caps with spaced tracking gives the row a "transport
          control" feel; active tab earns a primary tint pill. */}
      <div className="flex items-center justify-between gap-1 border-b border-white/6 px-2 py-2">
        <div role="tablist" aria-label="Generation type" className="flex items-center gap-px">
          {TABS.map(t => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={t === activeTab}
              tabIndex={t === activeTab ? 0 : -1}
              onClick={() => onTabChange?.(t)}
              onKeyDown={(e) => handleTabKeyDown(e, t)}
              className={`rounded-md px-2 py-[5px] font-mono text-[10px] font-medium uppercase tracking-[0.22em] transition-colors ${
                t === activeTab
                  ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_rgba(100,108,255,0.3)]"
                  : "text-text-muted hover:bg-white/[0.04] hover:text-foreground/90"
              }`}>
              {t}
            </button>
          ))}
        </div>
        <button type="button" aria-label="Close composer" data-tip="Close" onClick={onClose}
                className="btn-tip inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground">
          <X size={12} aria-hidden="true" />
        </button>
      </div>

      {/* Capability mismatch banner — shows the real catalog-derived reason
          (e.g., "doesn't accept video references"), not a generic blurb.
          Dashed border + tracked mono caps continues the receipt voice. */}
      {mismatchActive && (
        <div role="alert" className="mx-3 mt-3 rounded-md border border-dashed border-amber-300/35 bg-amber-400/[0.05] px-2.5 py-2 text-[11.5px] leading-relaxed text-amber-100/95">
          <span className="font-mono text-[9.5px] font-medium uppercase tracking-[0.28em] text-amber-200/85">Mismatch · Notice</span>
          <div className="mt-0.5">
            <strong className="font-medium text-amber-100">{m}</strong>{" "}
            {mismatchReason ?? "doesn’t accept some of the attached references"}.
          </div>
        </div>
      )}

      {/* Reference row — tighter 36×52 thumbs, hairline border, 'N REF'
          mono caps tag at the end matches the chrome metadata voice. */}
      <div className="flex items-center gap-1.5 px-3 pt-3">
        {refs.map((r, i) => (
          <div key={i} className="group/ref relative h-9 w-[52px] overflow-hidden rounded-[5px] border border-white/8 bg-black/30">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={r.src} alt={`Reference ${i + 1}`} loading="lazy" decoding="async" className="h-full w-full object-cover" />
            {r.role && (
              <span aria-hidden="true" className="absolute right-0 top-0 rounded-bl-[3px] border border-dashed border-white/35 bg-black/70 px-1 py-[1px] font-mono text-[8px] font-medium uppercase tracking-[0.24em] text-white/90">
                {r.role}
              </span>
            )}
            {onRemoveRef ? (
              <button
                type="button"
                aria-label={`Remove reference ${i + 1}`}
                onClick={() => onRemoveRef(i)}
                className="absolute inset-0 grid place-items-center bg-black/65 text-red-200 opacity-0 transition-opacity hover:opacity-100 group-hover/ref:opacity-100"
              >
                <Trash2 size={13} aria-hidden="true" />
              </button>
            ) : null}
          </div>
        ))}
        <button
          type="button"
          aria-label="Add reference"
          data-tip="Add reference"
          onClick={onAddRef}
          disabled={!onAddRef}
          className="btn-tip grid h-9 w-9 place-items-center rounded-[5px] border border-dashed border-white/12 text-text-muted transition-colors hover:border-primary/55 hover:text-primary disabled:cursor-not-allowed disabled:opacity-45"
        >
          <Plus size={13} aria-hidden="true" />
        </button>
        {refs.length > 0 && (
          <span className="ml-auto font-mono text-[9px] uppercase tracking-[0.2em] text-text-muted">
            {refs.length} ref{refs.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {/* Prompt */}
      <div className="relative px-3 pt-2">
        <textarea
          ref={textareaRef}
          aria-label="Prompt"
          rows={3}
          value={draft}
          onChange={(e) => {
            const next = e.target.value;
            setDraft(next);
            detectMention(next, e.target.selectionStart ?? next.length);
          }}
          onSelect={(e) => {
            const ta = e.currentTarget;
            detectMention(ta.value, ta.selectionStart ?? ta.value.length);
          }}
          onBlur={(e) => {
            // Don't dismiss the mention picker just because the textarea lost
            // focus — clicks land on the picker before blur fully settles.
            // The picker manages its own dismissal via outside-click.
            if (onPromptCommit && draft !== (prompt ?? "")) {
              onPromptCommit(draft);
            }
            // Defer hiding so a click-on-item can still fire its onMouseDown.
            const related = e.relatedTarget as HTMLElement | null;
            if (related && related.closest('[role="listbox"][aria-label="Mention picker"]')) return;
            setMention(null);
          }}
          onKeyDown={(e) => {
            if (mention && filteredMentionables.length > 0) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setMentionIndex((i) => (i + 1) % filteredMentionables.length);
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setMentionIndex((i) => (i - 1 + filteredMentionables.length) % filteredMentionables.length);
                return;
              }
              if (e.key === "Enter" && !(e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                insertMention(filteredMentionables[mentionIndex]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setMention(null);
                return;
              }
              if (e.key === "Tab") {
                e.preventDefault();
                insertMention(filteredMentionables[mentionIndex]);
                return;
              }
            }
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          autoFocus
          placeholder="Describe the shot. Use @ to mention a canvas node."
          className="w-full resize-none rounded-md border border-white/6 bg-black/35 px-3 py-2.5 text-[13px] leading-[1.55] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-primary/55 focus:bg-black/45"
        />
        {mention && filteredMentionables.length > 0 ? (
          <ul
            role="listbox"
            aria-label="Mention picker"
            className="absolute left-3 right-3 top-full z-10 mt-1 max-h-[200px] origin-top overflow-y-auto rounded-md border border-white/8 bg-[#141416] p-1 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
          >
            <div className="px-2 pb-1 pt-1 font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-text-muted/80">
              Mention
              {mention.query ? (
                <>
                  {" "}<span aria-hidden="true" className="text-text-muted/45">·</span>{" "}
                  <span className="text-foreground/95">@{mention.query}</span>
                </>
              ) : null}
            </div>
            {filteredMentionables.map((m, i) => {
              const active = i === mentionIndex;
              const kindIconColor =
                m.kind === "video" || m.kind === "draft" ? "text-primary/85" :
                m.kind === "image" ? "text-amber-200/85" :
                m.kind === "audio" ? "text-emerald-200/85" :
                m.kind === "idea" ? "text-amber-300/85" :
                m.kind === "plan" ? "text-blue-200/85" :
                "text-text-muted/85";
              return (
                <li key={m.id} role="none">
                  <button
                    role="option"
                    type="button"
                    aria-selected={active}
                    onMouseDown={(e) => {
                      // mousedown so the click commits before the textarea's
                      // blur path can dismiss the picker.
                      e.preventDefault();
                      insertMention(m);
                    }}
                    onMouseEnter={() => setMentionIndex(i)}
                    className={`flex w-full items-center justify-between gap-2 rounded px-2 py-[6px] text-left text-[12px] transition-colors ${
                      active
                        ? "bg-white/[0.06] text-foreground"
                        : "text-text-secondary/95 hover:bg-white/[0.04] hover:text-foreground"
                    }`}
                  >
                    <span className="truncate">{m.label}</span>
                    {m.kind ? (
                      <span className={`shrink-0 font-mono text-[9px] uppercase tracking-[0.2em] ${kindIconColor}`}>
                        {m.kind}
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>

      {/* Chip row — config strip prefaced by a dashed perforation reading
          "TEAR HERE TO GENERATE", carrying the receipt metaphor through.
          The Generate button on the right gets its own visual weight
          (primary halo shadow) so the eye lands there at submit time. */}
      <div className="px-3 pt-2.5">
        <TearLine label="Tear here to generate" />
      </div>
      <div className="flex items-center justify-between gap-1.5 px-3 pb-3 pt-2.5">
        <div className="flex items-center gap-1.5">
          <ChipDropdown label="Model"    value={m} primary
            options={modelOptions.map((v) => ({ value: v, label: v }))}
            onChange={(v) => setM(v)} />
          <ChipDropdown label="Aspect"   value={a}
            options={aspectOptions.map((v) => ({ value: v, label: v }))}
            onChange={(v) => setA(v)} />
          <ChipDropdown label="Duration" value={d}
            options={durationOptions.map((v) => ({ value: v, label: v }))}
            onChange={(v) => setD(v)} />
          <button
            type="button"
            ref={advancedAnchorRef}
            aria-label="Advanced params"
            data-tip="Negative prompt · Seed · Guidance"
            onClick={() => {
              const r = advancedAnchorRef.current?.getBoundingClientRect();
              if (r) {
                setAdvancedAnchorRect({ left: r.left, top: r.top, width: r.width, height: r.height });
              }
              setAdvancedOpen((v) => !v);
              onAdvanced?.();
            }}
            className={`btn-tip inline-flex h-7 w-7 items-center justify-center rounded-md border bg-black/25 text-text-secondary transition-colors hover:bg-white/[0.05] hover:text-foreground ${
              // Lit ring when any advanced field has a non-default value,
              // so the user can see at a glance that the run will pick
              // up custom params.
              advanced.negativePrompt || typeof advanced.seed === "number" || typeof advanced.cfgScale === "number" || advanced.mode || advanced.movementAmplitude || advanced.sound
                ? "border-primary/55 text-primary"
                : "border-white/8"
            }`}>
            <Settings size={12} aria-hidden="true" />
          </button>
          <ChipDropdown label="Count" value={c}
            options={countOptions.map((v) => ({ value: v, label: v }))}
            onChange={(v) => setC(v)} />
        </div>
        <button
          type="button"
          aria-label="Submit"
          data-tip="Generate (⌘⏎)"
          disabled={mismatchActive}
          onClick={submit}
          className={`btn-tip inline-flex h-8 w-8 items-center justify-center rounded-full transition-all duration-200 active:scale-[0.94] ${
            mismatchActive
              ? "cursor-not-allowed bg-primary/30 text-white/50"
              : `bg-primary text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_14px_-4px_rgba(100,108,255,0.55)] hover:bg-primary/92 hover:scale-[1.04] hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.2),0_6px_18px_-4px_rgba(100,108,255,0.65)] ${
                  draft.trim().length > 0 ? "motion-safe:animate-atelier-pulse-soft" : ""
                }`
          }`}>
          <Wand2 size={13} aria-hidden="true" className="transition-transform duration-200 group-hover:rotate-12" />
        </button>
      </div>

      <AdvancedPopover
        open={advancedOpen}
        modelLabel={m}
        value={advanced}
        onChange={setAdvanced}
        onClose={() => setAdvancedOpen(false)}
        anchorRect={advancedAnchorRect}
      />
    </section>
  );
}

export { CapabilityIcon } from "./CapabilityIcon";
export { ChipDropdown } from "./ChipDropdown";
export { composerPlacement } from "./positioning";
export type { ComposerAnchor, ComposerViewport, ComposerPlacement, ComposerSize } from "./positioning";
