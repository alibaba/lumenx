"use client";
import * as React from "react";
import { Bot, ChevronsRight, ChevronsLeft } from "lucide-react";

export type AgentRailStatus = "active" | "thinking" | "waiting" | "failed";
export type PermissionMode = "untrusted" | "on_failure" | "on_request" | "never";

interface Props {
  agentStatus: AgentRailStatus;
  mode: PermissionMode;
  onModeChange?: (mode: PermissionMode) => void;
  onCollapse?: () => void;
  collapsed?: boolean;
  children?: React.ReactNode;
}

const STATUS_DOT: Record<AgentRailStatus, string> = {
  active: "bg-emerald-300 shadow-[0_0_0_3px_rgba(110,231,183,0.18)]",
  thinking: "bg-blue-300 shadow-[0_0_0_3px_rgba(96,165,250,0.18)] animate-pulse motion-reduce:animate-none",
  waiting: "bg-amber-300 shadow-[0_0_0_3px_rgba(252,211,77,0.18)] animate-pulse motion-reduce:animate-none",
  failed: "bg-red-300 shadow-[0_0_0_3px_rgba(252,165,165,0.18)]",
};

const STATUS_LABEL: Record<AgentRailStatus, string> = {
  active: "Active",
  thinking: "Thinking",
  waiting: "Awaiting approval",
  failed: "Failed",
};

const PERMISSION_LABELS: Record<PermissionMode, string> = {
  untrusted: "Untrusted",
  on_failure: "On failure",
  on_request: "On request",
  never: "Never",
};

const PERMISSION_HINT: Record<PermissionMode, string> = {
  untrusted: "Ask before canvas or generation actions.",
  on_failure: "Canvas writes may run; generation still asks.",
  on_request: "Ask only for tools marked as approval-only.",
  never: "Run allowed tools within hard limits.",
};

const PERMISSION_ORDER: PermissionMode[] = ["untrusted", "on_failure", "on_request", "never"];

function PermissionSegmented({
  value,
  onChange,
}: {
  value: PermissionMode;
  onChange?: (m: PermissionMode) => void;
}) {
  const idx = PERMISSION_ORDER.indexOf(value);
  const handleKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    let next: PermissionMode | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = PERMISSION_ORDER[(idx + 1) % PERMISSION_ORDER.length];
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = PERMISSION_ORDER[(idx - 1 + PERMISSION_ORDER.length) % PERMISSION_ORDER.length];
    else if (event.key === "Home") next = PERMISSION_ORDER[0];
    else if (event.key === "End") next = PERMISSION_ORDER[PERMISSION_ORDER.length - 1];
    if (next != null) {
      event.preventDefault();
      onChange?.(next);
    }
  };
  return (
    <div
      role="radiogroup"
      aria-label="Permission mode"
      className="inline-flex items-center gap-px rounded-full border border-white/8 bg-black/30 p-[3px]"
    >
      {PERMISSION_ORDER.map((m) => (
        <button
          key={m}
          type="button"
          role="radio"
          aria-checked={value === m}
          aria-label={PERMISSION_LABELS[m]}
          tabIndex={value === m ? 0 : -1}
          onClick={() => onChange?.(m)}
          onKeyDown={handleKey}
          className={`rounded-full px-2.5 py-[5px] text-[10px] tracking-[0.01em] transition-colors ${
            value === m
              ? "bg-atelier-brand-400/20 text-[#6e8fff]"
              : "text-text-muted/80 hover:text-foreground"
          }`}
        >
          {PERMISSION_LABELS[m]}
        </button>
      ))}
    </div>
  );
}

export function RightRailV3({
  agentStatus,
  mode,
  onModeChange,
  onCollapse,
  collapsed,
  children,
}: Props) {
  if (collapsed) {
    return (
      <aside
        role="region"
        aria-label="Atelier Agent (collapsed)"
        className="absolute right-4 top-4 bottom-4 z-30 flex w-[56px] flex-col items-center justify-start gap-3 rounded-2xl atelier-chrome-opaque py-3 shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.03)]"
      >
        <button
          aria-label="Expand panel"
          onClick={onCollapse}
          className="btn-tip inline-flex h-7 w-7 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
          data-tip="Expand panel"
        >
          <ChevronsLeft size={14} aria-hidden="true" />
        </button>
        <div className="relative">
          <span className="grid h-9 w-9 place-items-center rounded-md bg-atelier-brand-400/15 text-atelier-brand-400 ring-1 ring-inset ring-atelier-brand-400/25">
            <Bot size={15} aria-hidden="true" />
          </span>
          <span
            aria-hidden="true"
            className={`absolute -right-0.5 -bottom-0.5 h-2 w-2 rounded-full ring-2 ring-[#0c0c10] ${STATUS_DOT[agentStatus]}`}
          />
        </div>
      </aside>
    );
  }

  return (
    <aside
      role="region"
      aria-label="Atelier Agent"
      className="atelier-chrome-opaque absolute right-4 top-4 bottom-4 z-30 flex w-[380px] flex-col overflow-hidden rounded-2xl shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.03)]"
    >
      {/* Top accent — primary→transparent gradient hairline. Identifies the
          rail as the "agent zone" without shouting. */}
      <div aria-hidden="true" className="h-[2px] shrink-0 bg-gradient-to-r from-atelier-brand-400/85 via-atelier-brand-400/35 to-transparent" />

      {/* Editorial slip: tiny mono caps ribbon above the avatar. Same voice
          as the Composer's "ATELIER · COMPOSER · NO 001" — the rail reads
          as an issued credential, not a chrome panel. */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-white/8 px-3.5 py-1.5">
        <span aria-hidden="true" className="text-[11px] text-white/45">
          Agent
        </span>
        <span aria-hidden="true" className="text-[11px] text-white/45">
          {STATUS_LABEL[agentStatus]}
        </span>
      </div>

      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/6 px-3.5 py-3">
        <div className="flex items-center gap-2.5">
          <div className="relative">
            <span className="grid h-9 w-9 place-items-center rounded-md bg-atelier-brand-400/15 text-atelier-brand-400 ring-1 ring-inset ring-atelier-brand-400/25">
              <Bot size={15} aria-hidden="true" />
            </span>
            <span
              data-testid="agent-status-dot"
              aria-hidden="true"
              className={`absolute -right-0.5 -bottom-0.5 h-2 w-2 rounded-full ring-2 ring-[#0c0c10] ${STATUS_DOT[agentStatus]}`}
            />
          </div>
          <div className="leading-tight">
            <div className="font-display text-[14px] font-medium tracking-[-0.005em] text-foreground">
              Creative <span className="italic">Agent</span>
            </div>
            <div className="mt-[2px] text-[11px] text-white/45">
              {STATUS_LABEL[agentStatus]}
            </div>
          </div>
        </div>
        <button
          aria-label="Collapse panel"
          onClick={onCollapse}
          className="btn-tip inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
          data-tip="Collapse panel"
        >
          <ChevronsRight size={12} aria-hidden="true" />
        </button>
      </header>

      <div className="shrink-0 border-b border-white/6 px-3.5 py-2.5">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className="text-[11px] text-white/45">
            Permission
          </span>
          <span className="truncate text-[10.5px] leading-tight text-text-muted/85">
            {PERMISSION_HINT[mode]}
          </span>
        </div>
        <PermissionSegmented value={mode} onChange={onModeChange} />
      </div>

      <div className="flex flex-1 flex-col overflow-hidden">{children}</div>
    </aside>
  );
}
