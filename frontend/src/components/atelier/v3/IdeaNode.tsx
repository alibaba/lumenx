"use client";
import * as React from "react";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { MiniMarkdown } from "@/components/atelier/v3/MiniMarkdown";

interface Props {
  id: string;
  body: string;
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
  /** When true, the shell is overlaying an inline textarea editor — skip
   *  rendering entirely so the editor owns the visual frame. Previously we
   *  kept a chrome-only shell, but that produced a visible "double shell"
   *  around the textarea when widths/borders didn't perfectly align. */
  editing?: boolean;
  /** Uplift b6 · opens the FocusedTextEditor modal — the roomy Edit ⇄
   *  Preview surface for long scripts. Shown for long bodies only; the
   *  inline overlay textarea stays the tool for one-liners. */
  onOpenEditor?: (id: string) => void;
}

// Idea node — a calm editorial slip pinned to the wall of ideas. The frosted
// glass shell (.atelier-node-shell) supplies the depth, hairline highlight and
// float shadow; we lay only a whisper-soft warm-ochre CATEGORY wash over it so
// the slip reads premium and quiet, never garish. The body sits in calm Inter
// body text, signed off by a single muted sentence-case line.
export function IdeaNode({ id, body, selected, x, y, onSelect, editing, onOpenEditor }: Props) {
  const t = useTranslations("atelier.node");
  // Uplift b6 · long text: bodies past the clamp threshold get an
  // expand/collapse toggle + a char-count caption instead of silently
  // truncating (LibTV 文本/脚本 node semantics, §7.4). Hook must run
  // before the `editing` early-return to keep hook order stable.
  const [expanded, setExpanded] = useState(false);
  // While editing, the AtelierShellV3 overlay textarea IS the visual surface
  // — render nothing here to avoid a double-shell frame around the editor.
  if (editing) return null;
  const charCount = body.replace(/\s/g, "").length;
  const isLong = charCount > 120 || body.split(/\n/).length > 5;
  const borderClass = selected
    ? "ring-1 ring-white/25 border-white/20"
    : "border-atelier-ochre/15";
  // Take the last 3 chars of the node id and display them as a stamped
  // index — keeps the slip identifiable without piping a real index in.
  const stampNum = id.slice(-3).toUpperCase();
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
      style={{ transform: `translate(${x}px, ${y}px)` }}
      className={`group absolute w-[260px] overflow-hidden atelier-node-shell transition-[box-shadow,border-color] duration-200 ease-out ${borderClass}`}
    >
      {/* Warm-ochre category wash — a soft frosted tint laid over the glass
          shell (never replacing its depth gradient). Whisper alpha so it reads
          luxe, not a sticker. pointer-events-none so it never eats clicks. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(155deg, rgba(201,168,126,0.055) 0%, rgba(201,168,126,0.015) 55%, rgba(0,0,0,0) 100%)",
        }}
      />
      <div className="relative">
        <div className="px-[18px] pb-3.5 pt-4">
          {body ? (
            <div className="relative">
              {/* Markdown-lite body — **bold** / *italic* / `code` / links
                  render instead of showing raw marks. Collapsed state
                  clamps by height (works across the multiple <p> blocks
                  MiniMarkdown emits, where line-clamp can't) and fades
                  out into the shell. */}
              <div
                className={`overflow-hidden text-[13px] leading-[1.6] text-white/80 ${expanded ? "" : "max-h-[105px]"}`}
              >
                <MiniMarkdown source={body} className="space-y-1.5" />
              </div>
              {isLong && !expanded ? (
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-x-0 bottom-0 h-8"
                  style={{ backgroundImage: "linear-gradient(180deg, rgba(20,20,26,0) 0%, rgba(20,20,26,0.92) 100%)" }}
                />
              ) : null}
            </div>
          ) : (
            <p className="text-[12px] italic text-white/35">
              {t("ideaEmpty")}
            </p>
          )}
        </div>
        {/* Quiet signature line — a tiny soft-ochre category dot + a
            sentence-case Inter label. Long bodies add a char count and an
            expand/collapse toggle on the right. */}
        <div className="flex items-center gap-1.5 px-[18px] pb-3.5 pt-0.5">
          <span
            aria-hidden="true"
            className="h-[5px] w-[5px] shrink-0 rounded-full bg-atelier-ochre/45"
          />
          <span className="text-[11px] text-white/40">
            {isLong ? t("ideaChars", { count: charCount }) : t("ideaStamp", { num: stampNum })}
          </span>
          {/* F2 · demo n-script footer — 展开/编辑 controls live on the card
              (edit is EXPLICIT now; single click only selects). Edit is
              resident for every idea; Expand appears once the body clamps. */}
          <span className="ml-auto flex items-center gap-1">
            {isLong ? (
              <button
                type="button"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  setExpanded((v) => !v);
                }}
                className="rounded-[6px] border border-white/[0.08] bg-black/30 px-2 py-[3px] text-[11px] text-white/60 transition-colors hover:border-white/[0.16] hover:text-white/90"
              >
                {expanded ? t("collapse") : t("expand")}
              </button>
            ) : null}
            {onOpenEditor ? (
              <button
                type="button"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  onOpenEditor(id);
                }}
                className="rounded-[6px] border border-white/[0.08] bg-black/30 px-2 py-[3px] text-[11px] text-white/60 transition-colors hover:border-white/[0.16] hover:text-white/90"
              >
                {t("edit")}
              </button>
            ) : null}
          </span>
        </div>
      </div>
    </div>
  );
}
