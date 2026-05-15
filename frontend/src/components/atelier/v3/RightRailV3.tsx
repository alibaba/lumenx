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
  active: "bg-emerald-400",
  thinking: "bg-blue-300 animate-pulse motion-reduce:animate-none",
  waiting: "bg-amber-300 animate-pulse motion-reduce:animate-none",
  failed: "bg-red-300",
};

const STATUS_LABEL: Record<AgentRailStatus, string> = {
  active: "Active",
  thinking: "Thinking…",
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
      className="inline-flex items-center gap-0.5 rounded-full border border-glass-border bg-glass p-0.5"
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
          className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition ${
            value === m ? "bg-primary text-white" : "text-text-muted hover:text-foreground"
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
        className="absolute right-4 top-4 bottom-4 z-30 flex w-[56px] flex-col items-center justify-start gap-2 rounded-2xl border border-glass-border bg-surface backdrop-blur-md py-3"
      >
        <button
          aria-label="Expand panel"
          onClick={onCollapse}
          className="btn-tip rounded p-1.5 text-text-muted hover:bg-hover-bg hover:text-foreground"
          data-tip="Expand panel"
        >
          <ChevronsLeft size={14} />
        </button>
        <div className="grid h-9 w-9 place-items-center rounded-md bg-primary/20 text-primary">
          <Bot size={16} />
        </div>
      </aside>
    );
  }

  return (
    <aside
      role="region"
      aria-label="Atelier Agent"
      className="absolute right-4 top-4 bottom-4 z-30 flex w-[380px] flex-col rounded-2xl border border-glass-border bg-surface backdrop-blur-md"
    >
      <header className="flex items-center justify-between gap-3 border-b border-border-subtle px-3 py-2.5">
        <div className="flex items-center gap-2">
          <div className="relative">
            <span className="grid h-9 w-9 place-items-center rounded-md bg-primary/20 text-primary">
              <Bot size={16} />
            </span>
            <span
              data-testid="agent-status-dot"
              aria-hidden="true"
              className={`absolute -right-0.5 -bottom-0.5 h-2 w-2 rounded-full ring-2 ring-surface ${STATUS_DOT[agentStatus]}`}
            />
          </div>
          <div>
            <div className="font-display text-sm font-semibold text-foreground">Creative Agent</div>
            <div className="text-[11px] text-text-muted">· {STATUS_LABEL[agentStatus]}</div>
          </div>
        </div>
        <button
          aria-label="Collapse panel"
          onClick={onCollapse}
          className="btn-tip rounded p-1.5 text-text-muted hover:bg-hover-bg hover:text-foreground"
          data-tip="Collapse panel"
        >
          <ChevronsRight size={12} />
        </button>
      </header>

      <div className="border-b border-border-subtle px-3 py-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="font-mono text-[10px] uppercase tracking-wider text-text-muted">Permission</span>
          <span className="text-[10px] text-text-muted truncate ml-2">{PERMISSION_HINT[mode]}</span>
        </div>
        <PermissionSegmented value={mode} onChange={onModeChange} />
      </div>

      <div className="flex flex-1 flex-col overflow-hidden">{children}</div>
    </aside>
  );
}
