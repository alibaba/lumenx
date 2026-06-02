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
//   - Workflows  — curated preset templates (WorkflowsPanel + workflowTemplates;
//                  6 defaults + user-saved templates in localStorage)
//   - History    — project process gallery of agent turns (HistoryPanel)
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
  Compass,
  Download,
  HelpCircle,
  Layers,
  Plus,
  Redo2,
  Undo2,
  Workflow,
} from "lucide-react";
import BrandMark from "@/components/atelier/v3/BrandMark";

export type LeftRailMode =
  | "add"
  | "assets"
  | "workflows"
  | "history"
  | "director"
  | "agent"
  | "sequence"
  // v1.1 track W: persisted export history panel. Added after sequence
  // because the panel surfaces what HAPPENED to a sequence; it sits at
  // the end of the orchestration cluster rather than the middle of the
  // creation flow.
  | "exports";

export interface LeftRailModeDef {
  key: LeftRailMode;
  label: string;
  Icon: typeof Plus;
  shortcut?: string;
}

// Order is intentional — mirrors the creation → judgment → orchestration
// flow a creator follows during a session. Director sits between
// History and Agent because it's the structured-output sibling of the
// free Agent — same harness, different planner.
const MODES: LeftRailModeDef[] = [
  { key: "add",       label: "Add",       Icon: Plus,        shortcut: "Click to add" },
  { key: "assets",    label: "Assets",    Icon: Layers,      shortcut: "A" },
  { key: "workflows", label: "Workflows", Icon: Workflow },
  { key: "history",   label: "History",   Icon: BookOpen },
  { key: "director",  label: "Director",  Icon: Compass },
  { key: "agent",     label: "Agent",     Icon: Bot,         shortcut: "/" },
  { key: "sequence",  label: "Sequence",  Icon: Clapperboard },
  // v1.1 track W: persisted export history. Distinct from "Sequence"
  // (the live cut on the bottom strip) — "Exports" is the past-tense
  // gallery of mp4s the user has already produced.
  { key: "exports",   label: "Exports",   Icon: Download },
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
      // v0.6.2 — RHTV-style bare-canvas rail.
      // The previous design was a full-height vertical slab with
      // border-r + bg + shadow — the visible white-edge column the
      // user red-boxed. Now the rail is invisible structurally: just a
      // narrow flex column at left edge, items float directly on the
      // canvas and each carry their own hover plate. Active mode keeps
      // its brand-tinted plate at the per-item level (line ~120 below).
      className="absolute left-0 top-0 bottom-0 z-30 flex w-[64px] flex-col items-center justify-between py-3"
    >
      {/* Top — brand mark + mode buttons */}
      <div className="flex flex-col items-center gap-0.5">
        {/* Brand: v0.7 item C — iridescent orb mark replaces the old
            Sparkles glyph so the rail head matches the top-left wordmark
            and the RHTV brand presence. The chip plate is kept as a
            quiet container so the rail isn't faceless. */}
        <span
          aria-hidden="true"
          className="mb-1 grid h-9 w-9 place-items-center rounded-md bg-atelier-brand-400/15 ring-1 ring-inset ring-atelier-brand-400/25"
        >
          <BrandMark size={16} />
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
              className={`btn-tip group/mode relative flex w-12 flex-col items-center justify-center gap-1 rounded-md py-1.5 transition-all duration-150 active:scale-[0.94] ${
                active
                  ? "bg-atelier-brand-400/15 text-atelier-brand-400 shadow-[inset_0_0_0_1px_rgba(59,107,255,0.3)]"
                  : "text-text-muted hover:bg-white/[0.05] hover:text-foreground"
              }`}
            >
              {/* Left-edge primary rail when active — mirrors the
                  selected-node treatment. Subtle but unmistakable. */}
              {active ? (
                <span
                  aria-hidden="true"
                  className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-atelier-brand-400"
                />
              ) : null}
              <Icon size={15} aria-hidden="true" />
              <span className="text-[10px] leading-none text-white/45">{m.label}</span>
            </button>
          );
        })}
      </div>

      {/* Bottom — undo / redo / help */}
      <div className="flex flex-col items-center gap-0.5">
        <button
          type="button"
          aria-label="Undo"
          data-tip="Undo · ⌘Z"
          onClick={onUndo}
          disabled={!canUndo}
          className="btn-tip flex w-12 flex-col items-center justify-center gap-1 rounded-md py-1.5 text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:text-text-muted/45"
        >
          <Undo2 size={14} aria-hidden="true" />
          <span className="text-[10px] leading-none text-white/45">Undo</span>
        </button>
        <button
          type="button"
          aria-label="Redo"
          data-tip="Redo · ⌘⇧Z"
          onClick={onRedo}
          disabled={!canRedo}
          className="btn-tip flex w-12 flex-col items-center justify-center gap-1 rounded-md py-1.5 text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:text-text-muted/45"
        >
          <Redo2 size={14} aria-hidden="true" />
          <span className="text-[10px] leading-none text-white/45">Redo</span>
        </button>
        <button
          type="button"
          aria-label="Help / Shortcuts"
          data-tip="Shortcuts · ?"
          onClick={onHelp}
          className="btn-tip flex w-12 flex-col items-center justify-center gap-1 rounded-md py-1.5 text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground"
        >
          <HelpCircle size={14} aria-hidden="true" />
          <span className="text-[10px] leading-none text-white/45">Help</span>
        </button>
      </div>
    </aside>
  );
}
