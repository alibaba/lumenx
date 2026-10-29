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

// R2 · demo rail slot — 38px icon-only cell with the hover-scale spring and
// a right-floating tip label (demo .rail .slot / .tip). Shared by mode
// buttons and the bottom undo/redo/help cluster (add-only features).
function RailSlot({
  label,
  active,
  disabled,
  onClick,
  children,
  role,
  ariaSelected,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
  role?: string;
  ariaSelected?: boolean;
}) {
  return (
    <button
      type="button"
      role={role}
      aria-selected={ariaSelected}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={`group relative flex h-[38px] w-[38px] items-center justify-center rounded-[11px] transition-[background,color,transform] duration-[var(--at-dur-fast)] ease-[var(--at-ease-spring)] ${
        active
          ? "bg-white/10 text-white/[0.92]"
          : "text-white/50 hover:scale-[1.08] hover:bg-white/[0.07] hover:text-white/90"
      } disabled:cursor-not-allowed disabled:text-white/25 disabled:hover:scale-100 disabled:hover:bg-transparent`}
    >
      {children}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-[48px] top-1/2 -translate-y-1/2 translate-x-[-4px] whitespace-nowrap rounded-[7px] border border-white/10 bg-[#0a0a0e]/90 px-2.5 py-1 text-[11px] text-white/85 opacity-0 transition-[opacity,transform] duration-[var(--at-dur-fast)] ease-[var(--at-ease-spring)] group-hover:translate-x-0 group-hover:opacity-100"
      >
        {label}
      </span>
    </button>
  );
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
      // R2 · converged-v0.2 rail — a vertically-centered floating glass
      // capsule (demo .rail): 52px wide, at-glass card, icon-only 38px
      // slots with hover-scale + right-floating tip labels. Replaces the
      // full-height bare column. (The old red-boxed complaint was about a
      // full-height white-edged SLAB — this centered capsule is the
      // demo's answer, not a regression to it.) Undo/redo/help stay as a
      // bottom cluster inside the same capsule — add-only.
      className="at-glass absolute left-4 top-1/2 z-30 flex w-[52px] -translate-y-1/2 flex-col items-center gap-0.5 py-2"
    >
      {MODES.map((m) => {
        const active = activeMode === m.key;
        const Icon = m.Icon;
        const label = m.shortcut && m.shortcut.length <= 2 ? `${m.label} · ${m.shortcut}` : m.label;
        return (
          <RailSlot key={m.key} role="tab" ariaSelected={active} active={active} label={label} onClick={() => onModeToggle(m.key)}>
            <Icon size={16} aria-hidden="true" />
          </RailSlot>
        );
      })}

      <div aria-hidden="true" className="my-1.5 h-px w-[22px] bg-white/[0.08]" />

      <RailSlot label="Undo · ⌘Z" onClick={onUndo} disabled={!canUndo}>
        <Undo2 size={15} aria-hidden="true" />
      </RailSlot>
      <RailSlot label="Redo · ⌘⇧Z" onClick={onRedo} disabled={!canRedo}>
        <Redo2 size={15} aria-hidden="true" />
      </RailSlot>
      <RailSlot label="Shortcuts · ?" onClick={onHelp}>
        <HelpCircle size={15} aria-hidden="true" />
      </RailSlot>
    </aside>
  );
}
