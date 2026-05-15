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

const ICON_BTN_BASE =
  "btn-tip inline-flex items-center justify-center rounded-md p-1.5";
const ICON_BTN_ENABLED =
  "text-text-secondary hover:bg-hover-bg hover:text-foreground";
const ICON_BTN_DISABLED = "text-text-muted/60 cursor-not-allowed";

export function ToolbarV3({
  onCreate,
  onAskAgent,
  onUndo,
  onRedo,
  askActive = false,
  canUndo = true,
  canRedo = true,
}: Props) {
  const undoClass = `${ICON_BTN_BASE} ${canUndo ? ICON_BTN_ENABLED : ICON_BTN_DISABLED}`;
  const redoClass = `${ICON_BTN_BASE} ${canRedo ? ICON_BTN_ENABLED : ICON_BTN_DISABLED}`;
  const askClass = askActive
    ? "btn-tip inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-text-secondary hover:bg-hover-bg hover:text-foreground bg-hover-bg text-foreground"
    : "btn-tip inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-text-secondary hover:bg-hover-bg hover:text-foreground";

  return (
    <div
      role="toolbar"
      aria-label="Atelier toolbar"
      className="absolute left-4 top-4 z-30 flex items-center gap-1 rounded-full border border-glass-border bg-glass p-1 backdrop-blur-md"
    >
      <button
        type="button"
        aria-label="New Video Node"
        data-tip="New Video Node (V)"
        onClick={() => onCreate("video")}
        className="btn-tip inline-flex items-center gap-1.5 rounded-md bg-primary px-2 py-1.5 text-xs font-medium text-white hover:bg-primary/90"
      >
        <Film size={14} />
        <span>Video</span>
      </button>
      <button
        type="button"
        aria-label="New Image Node"
        data-tip="New Image Node (I)"
        onClick={() => onCreate("image")}
        className={`${ICON_BTN_BASE} ${ICON_BTN_ENABLED}`}
      >
        <ImageIcon size={14} />
      </button>
      <button
        type="button"
        aria-label="New Idea Node"
        data-tip="New Idea Node (T)"
        onClick={() => onCreate("idea")}
        className={`${ICON_BTN_BASE} ${ICON_BTN_ENABLED}`}
      >
        <Lightbulb size={14} />
      </button>
      <span className="mx-1 h-5 w-px bg-glass-border" />
      <button
        type="button"
        aria-label="Undo"
        data-tip="Undo (⌘Z)"
        onClick={onUndo}
        disabled={!canUndo}
        className={undoClass}
      >
        <Undo2 size={14} />
      </button>
      <button
        type="button"
        aria-label="Redo"
        data-tip="Redo (⌘⇧Z)"
        onClick={onRedo}
        disabled={!canRedo}
        className={redoClass}
      >
        <Redo2 size={14} />
      </button>
      <span className="mx-1 h-5 w-px bg-glass-border" />
      <button
        type="button"
        aria-label="Ask Agent"
        data-tip="Ask Agent (/)"
        onClick={onAskAgent}
        className={askClass}
      >
        <Sparkles size={14} />
        <span>Ask Agent</span>
      </button>
    </div>
  );
}
