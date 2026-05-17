"use client";
import * as React from "react";
import { Film, ImageIcon, Lightbulb, Undo2, Redo2, Sparkles } from "lucide-react";

export type CreateKind = "video" | "image" | "idea";

interface Props {
  onCreate: (kind: CreateKind) => void;
  onAskAgent: () => void;
  onUndo: () => void;
  onRedo: () => void;
  askActive?: boolean;
  canUndo?: boolean;
  canRedo?: boolean;
}

// Cinematic toolbar capsule. Single 48-tall pill, mono caps for the only
// labeled action ('VIDEO'), icon-only for everything else with consistent
// 28×28 hit targets. Inset top edge highlight signs the surface as 'lit
// from above'; hover bg/[0.06] + 150ms transitions on every hit target.
const HIT_BASE =
  "btn-tip inline-flex h-7 w-7 items-center justify-center rounded-md transition-all duration-150 active:scale-[0.94]";
const HIT_ENABLED = "text-text-secondary hover:bg-white/[0.06] hover:text-foreground";
const HIT_DISABLED = "text-text-muted/50 cursor-not-allowed";

export function ToolbarV3({
  onCreate,
  onAskAgent,
  onUndo,
  onRedo,
  askActive = false,
  canUndo = true,
  canRedo = true,
}: Props) {
  const undoClass = `${HIT_BASE} ${canUndo ? HIT_ENABLED : HIT_DISABLED}`;
  const redoClass = `${HIT_BASE} ${canRedo ? HIT_ENABLED : HIT_DISABLED}`;
  const askClass = `${HIT_BASE} h-7 w-auto px-2 gap-1.5 ${
    askActive
      ? "bg-hover-bg text-foreground"
      : "text-text-secondary hover:bg-hover-bg hover:text-foreground"
  }`;

  return (
    <div
      role="toolbar"
      aria-label="Atelier toolbar"
      className="absolute left-4 top-4 z-30 flex h-9 items-center gap-0.5 rounded-full border border-white/8 bg-[#141416]/96 px-1 shadow-[0_14px_30px_-18px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl"
    >
      {/* Primary action: New Video. Pill anchored at the head, mono caps
          label keeps it kinetic without being SaaS-cute. */}
      <button
        type="button"
        aria-label="New Video Node"
        data-tip="New Video Node (V)"
        onClick={() => onCreate("video")}
        className="btn-tip inline-flex h-7 items-center gap-1.5 rounded-full bg-primary pl-2 pr-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_12px_-4px_rgba(100,108,255,0.5)] transition-all duration-200 hover:scale-[1.04] hover:bg-primary/92 hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.22),0_6px_16px_-4px_rgba(100,108,255,0.6)] active:scale-[0.96]"
      >
        <Film size={12} aria-hidden="true" />
        Video
      </button>

      <button
        type="button"
        aria-label="New Image Node"
        data-tip="New Image Node (I)"
        onClick={() => onCreate("image")}
        className={`${HIT_BASE} ${HIT_ENABLED}`}
      >
        <ImageIcon size={13} aria-hidden="true" />
      </button>
      <button
        type="button"
        aria-label="New Idea Node"
        data-tip="New Idea Node (T)"
        onClick={() => onCreate("idea")}
        className={`${HIT_BASE} ${HIT_ENABLED}`}
      >
        <Lightbulb size={13} aria-hidden="true" />
      </button>

      <span aria-hidden="true" className="mx-1 h-4 w-px bg-white/8" />

      <button
        type="button"
        aria-label="Undo"
        data-tip="Undo (⌘Z)"
        onClick={onUndo}
        disabled={!canUndo}
        className={undoClass}
      >
        <Undo2 size={13} aria-hidden="true" />
      </button>
      <button
        type="button"
        aria-label="Redo"
        data-tip="Redo (⌘⇧Z)"
        onClick={onRedo}
        disabled={!canRedo}
        className={redoClass}
      >
        <Redo2 size={13} aria-hidden="true" />
      </button>

      <span aria-hidden="true" className="mx-1 h-4 w-px bg-white/8" />

      <button
        type="button"
        aria-label="Ask Agent"
        data-tip="Ask Agent (/)"
        onClick={onAskAgent}
        className={askClass}
      >
        <Sparkles size={12} aria-hidden="true" />
        <span className="font-mono text-[10px] font-medium uppercase tracking-[0.18em]">
          Agent
        </span>
      </button>
    </div>
  );
}
