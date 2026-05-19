"use client";
//
// LeftRailV3 — Activity-Bar-style vertical mode rail at the left edge of
// the canvas. Replaces the old top ToolbarV3 horizontal capsule.
//
// Modes are creator mental-model entries (per Codex competitive research
// §3.3 / §4.2 / §7.2 — RHTV / LibTV / 即梦 all converge on this taxonomy):
//
//   - Add        — open the creation popover (Image / Video / Idea /
//                  Comment / Upload / From Library)
//   - Assets     — toggle the project asset library drawer
//   - Workflows  — placeholder for Sprint C (curated preset templates)
//   - History    — placeholder for Sprint D (project event log)
//   - Agent      — toggle the right-rail Creative Agent panel
//   - Sequence   — toggle the bottom Sequence Strip visibility
//
// Bottom of rail: undo / redo + help (matches RHTV's persistent navigation
// chrome: anything that's true canvas state — not a creation step — sits
// at the bottom).
//
// Active mode highlights with a primary tint + a thin left-edge rail
// accent (the same design language used for selected nodes). Clicking
// the active mode again toggles its panel closed.
import * as React from "react";
import {
  BookOpen,
  Bot,
  Clapperboard,
  HelpCircle,
  Layers,
  Plus,
  Redo2,
  Sparkles,
  Undo2,
  Workflow,
} from "lucide-react";

export type LeftRailMode = "add" | "assets" | "workflows" | "history" | "agent" | "sequence";

export interface LeftRailModeDef {
  key: LeftRailMode;
  label: string;
  Icon: typeof Plus;
  shortcut?: string;
}

// Order is intentional — mirrors the creation → judgment → orchestration
// flow a creator follows during a session.
const MODES: LeftRailModeDef[] = [
  { key: "add",       label: "Add",       Icon: Plus,        shortcut: "Click to add" },
  { key: "assets",    label: "Assets",    Icon: Layers,      shortcut: "A" },
  { key: "workflows", label: "Workflows", Icon: Workflow },
  { key: "history",   label: "History",   Icon: BookOpen },
  { key: "agent",     label: "Agent",     Icon: Bot,         shortcut: "/" },
  { key: "sequence",  label: "Sequence",  Icon: Clapperboard },
];

interface Props {
  /** The mode whose panel is currently visible. null = no panel open
   *  (the rail itself is always visible, the panel is what slides in). */
  activeMode: LeftRailMode | null;
  onModeToggle: (mode: LeftRailMode) => void;
  onUndo: () => void;
  onRedo: () => void;
  canUndo?: boolean;
  canRedo?: boolean;
  onHelp?: () => void;
}

export function LeftRailV3({
  activeMode,
  onModeToggle,
  onUndo,
  onRedo,
  canUndo = true,
  canRedo = true,
  onHelp,
}: Props) {
  return (
    <aside
      role="toolbar"
      aria-label="Atelier mode rail"
      className="absolute left-0 top-0 bottom-0 z-30 flex w-[56px] flex-col items-center justify-between border-r border-white/8 bg-[#0c0c10]/96 py-3 shadow-[2px_0_8px_-4px_rgba(0,0,0,0.5),inset_-1px_0_0_0_rgba(255,255,255,0.04)] backdrop-blur-xl"
    >
      {/* Top — brand mark + mode buttons */}
      <div className="flex flex-col items-center gap-1">
        {/* Brand: a tiny sparkle so the rail isn't faceless. Click jumps
            to the canvas root (deselect). */}
        <span
          aria-hidden="true"
          className="mb-1 grid h-9 w-9 place-items-center rounded-md bg-primary/15 text-primary ring-1 ring-inset ring-primary/25"
        >
          <Sparkles size={14} aria-hidden="true" />
        </span>

        {MODES.map((m) => {
          const active = activeMode === m.key;
          const Icon = m.Icon;
          return (
            <button
              key={m.key}
              type="button"
              role="tab"
              aria-selected={active}
              aria-label={m.label}
              data-tip={m.shortcut ? `${m.label} · ${m.shortcut}` : m.label}
              onClick={() => onModeToggle(m.key)}
              className={`btn-tip group/mode relative grid h-10 w-10 place-items-center rounded-md transition-all duration-150 active:scale-[0.94] ${
                active
                  ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_rgba(100,108,255,0.3)]"
                  : "text-text-muted hover:bg-white/[0.05] hover:text-foreground"
              }`}
            >
              {/* Left-edge primary rail when active — mirrors the
                  selected-node treatment. Subtle but unmistakable. */}
              {active ? (
                <span
                  aria-hidden="true"
                  className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-primary"
                />
              ) : null}
              <Icon size={15} aria-hidden="true" />
            </button>
          );
        })}
      </div>

      {/* Bottom — undo / redo / help */}
      <div className="flex flex-col items-center gap-1">
        <button
          type="button"
          aria-label="Undo"
          data-tip="Undo · ⌘Z"
          onClick={onUndo}
          disabled={!canUndo}
          className="btn-tip grid h-9 w-9 place-items-center rounded-md text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:text-text-muted/45"
        >
          <Undo2 size={14} aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label="Redo"
          data-tip="Redo · ⌘⇧Z"
          onClick={onRedo}
          disabled={!canRedo}
          className="btn-tip grid h-9 w-9 place-items-center rounded-md text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:text-text-muted/45"
        >
          <Redo2 size={14} aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label="Help / Shortcuts"
          data-tip="Shortcuts · ?"
          onClick={onHelp}
          className="btn-tip grid h-9 w-9 place-items-center rounded-md text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground"
        >
          <HelpCircle size={14} aria-hidden="true" />
        </button>
      </div>
    </aside>
  );
}
