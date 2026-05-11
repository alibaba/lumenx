import type {
    AtelierAgentPlannerReadiness,
    AtelierAgentSessionSummary,
    AtelierAgentTurnSummary,
} from "@/lib/atelierAgentPlanning";

type AgentPanelSessionOverviewProps = {
    summary: AtelierAgentSessionSummary;
};

type AgentPanelHistoryListProps = {
    summaries: AtelierAgentTurnSummary[];
    focusedTurnId: string | null;
    expandedTurnId: string | null;
    onToggleTurn: (turnId: string) => void;
};

type AgentPanelPlannerReadinessProps = {
    readiness: AtelierAgentPlannerReadiness;
};

export function AgentPanelSessionOverview({ summary }: AgentPanelSessionOverviewProps) {
    return (
        <div className="rounded-md border border-white/10 bg-black/20 p-2">
            <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-primary/80">Session</span>
                <span className="shrink-0 rounded border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase text-text-muted">
                    {summary.status}
                </span>
            </div>
            <div className="mb-2 rounded border border-white/10 bg-white/[0.03] px-2 py-1.5">
                <div className="flex items-center justify-between gap-2">
                    <span className="shrink-0 text-[10px] uppercase text-text-muted">Focus</span>
                    <span className="min-w-0 truncate text-right text-[11px] font-semibold text-foreground">
                        {summary.focus.label}
                    </span>
                </div>
                <div className="mt-0.5 truncate text-[10px] text-text-muted">
                    {summary.focus.detail}
                </div>
            </div>
            <div className="grid grid-cols-3 gap-1">
                <SessionStat label="Turns" value={summary.turnCount} />
                <SessionStat label="Planned" value={summary.plannedCallCount} />
                <SessionStat label="Waiting" value={summary.waitingApprovalCount} tone="text-amber-100" />
                <SessionStat label="Done" value={summary.completedCallCount} tone="text-emerald-200" />
                <SessionStat label="Failed" value={summary.failedCallCount} tone="text-red-100" />
                <div className="rounded border border-white/10 bg-white/[0.03] p-1.5">
                    <div className="text-[10px] uppercase text-text-muted">Modes</div>
                    <div className="truncate text-[11px] font-semibold text-text-secondary">
                        {summary.previewTurnCount}p · {summary.executeTurnCount}e
                    </div>
                </div>
            </div>
        </div>
    );
}

export function AgentPanelPlannerReadiness({ readiness }: AgentPanelPlannerReadinessProps) {
    const statusTone =
        readiness.status === "ready"
            ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100"
            : readiness.status === "schema_mismatch"
                ? "border-red-400/30 bg-red-500/10 text-red-100"
                : "border-white/10 bg-white/[0.03] text-text-secondary";

    return (
        <div className={`rounded border px-2 py-1.5 ${statusTone}`}>
            <div className="flex items-center justify-between gap-2">
                <span className="shrink-0 text-[10px] uppercase text-text-muted">Readiness</span>
                <span className="min-w-0 truncate text-right text-[10px] font-semibold">{readiness.label}</span>
            </div>
            <div className="mt-0.5 truncate text-[10px] opacity-80">{readiness.detail}</div>
            <div className="mt-1 grid grid-cols-2 gap-1 text-[10px] text-text-muted">
                <span className="truncate">Planner {readiness.plannerSchemaVersion}</span>
                <span className="truncate text-right">Tools {readiness.toolSchemaVersion}</span>
                <span className="truncate">Adapter {readiness.adapterName}</span>
                <span className="truncate text-right">Trace {readiness.modelTraceId ?? "none"}</span>
            </div>
        </div>
    );
}

export function AgentPanelHistoryList({
    summaries,
    focusedTurnId,
    expandedTurnId,
    onToggleTurn,
}: AgentPanelHistoryListProps) {
    if (summaries.length === 0) return null;

    return (
        <div>
            <div className="mb-1 text-[11px] font-medium text-text-secondary">History</div>
            <div className="space-y-1">
                {summaries.map((turn) => {
                    const isFocused = focusedTurnId === turn.id;
                    const isExpanded = expandedTurnId === turn.id;
                    return (
                        <div
                            key={turn.id}
                            className={`rounded border px-2 py-1.5 ${
                                isFocused
                                    ? "border-primary/40 bg-primary/[0.06]"
                                    : "border-white/10 bg-black/20"
                            }`}
                        >
                            <button
                                type="button"
                                onClick={() => onToggleTurn(turn.id)}
                                className="w-full text-left"
                            >
                                <div className="flex items-center justify-between gap-2">
                                    <span className="min-w-0 flex-1 truncate text-foreground">{turn.title}</span>
                                    <span className="inline-flex shrink-0 items-center gap-1">
                                        {isFocused && (
                                            <span className="rounded border border-primary/35 bg-primary/15 px-1.5 py-0.5 text-[9px] uppercase text-primary/90">
                                                Focus
                                            </span>
                                        )}
                                        <span className="uppercase text-text-muted">{turn.status}</span>
                                    </span>
                                </div>
                                <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-text-muted">
                                    <span className="min-w-0 truncate">{turn.mode} · {turn.callCount} calls</span>
                                    <span className="shrink-0">
                                        {turn.completedCount} ok · {turn.failedCount} failed
                                    </span>
                                </div>
                                {turn.resultSummary && (
                                    <div className="mt-1 truncate text-[11px] text-text-secondary">{turn.resultSummary}</div>
                                )}
                            </button>
                            {isExpanded && (
                                <div className="mt-2 space-y-1 border-t border-white/10 pt-2">
                                    {turn.waitingApprovalCount > 0 && (
                                        <div className="rounded border border-amber-300/25 bg-amber-300/10 px-2 py-1 text-[10px] text-amber-100">
                                            {turn.waitingApprovalCount} waiting approval
                                        </div>
                                    )}
                                    {turn.toolCalls.map((call) => (
                                        <div key={call.callId} className="rounded border border-white/10 bg-white/[0.03] px-2 py-1">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="min-w-0 flex-1 truncate text-[11px] text-foreground">{call.toolName}</span>
                                                <span className="shrink-0 text-[10px] uppercase text-text-muted">{call.status}</span>
                                            </div>
                                            {call.result && (
                                                <div className="mt-0.5 truncate text-[10px] text-text-muted">{call.result}</div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function SessionStat({
    label,
    value,
    tone = "text-foreground",
}: {
    label: string;
    value: number;
    tone?: string;
}) {
    return (
        <div className="rounded border border-white/10 bg-white/[0.03] p-1.5">
            <div className="text-[10px] uppercase text-text-muted">{label}</div>
            <div className={`text-sm font-semibold ${tone}`}>{value}</div>
        </div>
    );
}
