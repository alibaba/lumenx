"use client";
//
// PendingTaskAffordance — drop-in for any "pending" / "processing" UI
// state that's vulnerable to silent stalls. Replaces the bare spinner
// pattern with a self-narrating block that:
//
//   - Shows the live elapsed time ("已等待 2m 14s")
//   - After a soft threshold (default 60s) reveals Cancel + Diagnose
//     buttons, which is what the user actually wants when something
//     looks stuck (the spinner alone gives them nothing to do)
//   - Diagnose opens a modal that pings the backend /health endpoint
//     and shows: "backend reachable / task_id / log file path /
//     open log folder shortcut". This converts "the support workflow
//     for stuck tasks" from a Slack request into a self-serve sheet.
//
// Used by:
//   - Studio ShotCard pending branch
//   - Atelier candidate pending state (via nodeRenderers)
//   - Any future async UI that wants a recoverable spinner
//
// The component is intentionally chrome-light. Parent provides the
// container box; we render content + a modal portal.
import { useEffect, useState } from "react";
import { Loader2, X, AlertTriangle, FileText, Copy, Check } from "lucide-react";
import { api } from "@/lib/api";

interface Props {
    /** Task creation timestamp (epoch seconds). When omitted the
     *  component falls back to its own mount time — fine for stuck-task
     *  detection in the active session, just less precise across
     *  refreshes (timer resets). Pass when known (e.g. pipeline
     *  candidate.created_at). */
    createdAt?: number;
    /** Status copy: "排队中" / "生成中" — fully formatted by parent. */
    statusLabel: string;
    /** Soft threshold beyond which we reveal Cancel + Diagnose. Default 60s. */
    revealAfterMs?: number;
    /** Optional task id for the diagnose modal. */
    taskId?: string;
    /** When set, Cancel calls this. If async, throw on failure. */
    onCancel?: () => Promise<void> | void;
    /** Compact mode: smaller text + icons (used inside dense canvas cards). */
    compact?: boolean;
}

function formatElapsed(ms: number): string {
    const s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}m ${r}s`;
}

export function PendingTaskAffordance({
    createdAt,
    statusLabel,
    revealAfterMs = 60_000,
    taskId,
    onCancel,
    compact = false,
}: Props) {
    const [now, setNow] = useState(() => Date.now());
    const [mountedAtMs] = useState(() => Date.now());
    useEffect(() => {
        const id = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(id);
    }, []);

    const sinceMs =
        typeof createdAt === "number" && Number.isFinite(createdAt)
            ? createdAt * 1000
            : mountedAtMs;
    const elapsedMs = now - sinceMs;
    const showActions = elapsedMs >= revealAfterMs;
    const elapsedLabel = formatElapsed(elapsedMs);

    const [diagnoseOpen, setDiagnoseOpen] = useState(false);
    const [canceling, setCanceling] = useState(false);
    const [cancelError, setCancelError] = useState<string | null>(null);

    const handleCancel = async () => {
        if (!onCancel) return;
        setCanceling(true);
        setCancelError(null);
        try {
            await onCancel();
        } catch (err) {
            setCancelError(err instanceof Error ? err.message : "Cancel failed");
        } finally {
            setCanceling(false);
        }
    };

    const elapsedClass = compact ? "text-[10px]" : "text-[11px]";
    const statusClass = compact ? "text-[10px]" : "text-[11px]";

    return (
        <div className="flex flex-col items-center justify-center gap-1.5">
            <Loader2
                size={compact ? 18 : 22}
                className="text-primary animate-spin"
            />
            <span className={`${statusClass} font-medium text-amber-400`}>
                {statusLabel}
            </span>
            <span className={`${elapsedClass} font-mono tracking-tight text-text-muted/85`}>
                {elapsedLabel}
            </span>
            {showActions ? (
                <div className="mt-1 flex items-center gap-1.5">
                    {onCancel ? (
                        <button
                            type="button"
                            onClick={(e) => {
                                e.stopPropagation();
                                void handleCancel();
                            }}
                            disabled={canceling}
                            className="rounded-md border border-red-300/30 bg-red-400/10 px-2 py-[3px] font-mono text-[9.5px] font-medium uppercase tracking-[0.2em] text-red-200/95 transition-colors hover:bg-red-400/20 disabled:cursor-wait disabled:opacity-60"
                        >
                            {canceling ? "Canceling…" : "Cancel"}
                        </button>
                    ) : null}
                    <button
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            setDiagnoseOpen(true);
                        }}
                        className="rounded-md border border-white/15 bg-black/30 px-2 py-[3px] font-mono text-[9.5px] font-medium uppercase tracking-[0.2em] text-text-secondary/95 transition-colors hover:border-primary/45 hover:text-foreground"
                    >
                        Diagnose
                    </button>
                </div>
            ) : null}
            {cancelError ? (
                <span className="font-mono text-[9px] text-red-300/85">
                    {cancelError}
                </span>
            ) : null}
            {diagnoseOpen ? (
                <DiagnoseModal
                    taskId={taskId}
                    elapsedLabel={elapsedLabel}
                    onClose={() => setDiagnoseOpen(false)}
                />
            ) : null}
        </div>
    );
}

interface DiagnoseModalProps {
    taskId?: string;
    elapsedLabel: string;
    onClose: () => void;
}

export function DiagnoseModal({ taskId, elapsedLabel, onClose }: DiagnoseModalProps) {
    type HealthState =
        | { kind: "loading" }
        | { kind: "ok"; data: Awaited<ReturnType<typeof api.healthCheck>> }
        | { kind: "error"; message: string };
    const [health, setHealth] = useState<HealthState>({ kind: "loading" });
    const [copied, setCopied] = useState<"task" | "log" | null>(null);

    useEffect(() => {
        let cancelled = false;
        api.healthCheck()
            .then((data) => {
                if (!cancelled) setHealth({ kind: "ok", data });
            })
            .catch((err) => {
                if (!cancelled) setHealth({ kind: "error", message: err instanceof Error ? err.message : String(err) });
            });
        return () => {
            cancelled = true;
        };
    }, []);

    const copy = async (text: string, kind: "task" | "log") => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(kind);
            window.setTimeout(() => setCopied((c) => (c === kind ? null : c)), 1400);
        } catch {
            /* ignore */
        }
    };

    return (
        <>
            <div
                aria-hidden="true"
                className="fixed inset-0 z-[60] bg-black/40"
                onClick={onClose}
            />
            <div
                role="dialog"
                aria-label="Diagnose stuck task"
                className="fixed left-1/2 top-1/2 z-[61] w-[min(540px,92vw)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-[12px] border border-white/8 bg-[#141416]/96 shadow-[0_24px_48px_-22px_rgba(0,0,0,0.85),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl"
            >
                <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-amber-300/85 via-amber-300/35 to-transparent" />
                <header className="flex items-center justify-between gap-3 border-b border-white/6 px-4 py-3">
                    <div className="flex items-center gap-2">
                        <AlertTriangle size={14} className="text-amber-300" aria-hidden="true" />
                        <div className="font-display text-[14px] font-medium tracking-[-0.005em] text-foreground">
                            Diagnose stuck task
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close"
                        className="grid h-7 w-7 place-items-center rounded text-text-muted hover:bg-white/[0.06] hover:text-foreground"
                    >
                        <X size={14} aria-hidden="true" />
                    </button>
                </header>
                <div className="space-y-4 px-4 py-4 text-[12.5px] leading-[1.55] text-text-secondary/95">
                    <Row label="Elapsed">
                        <span className="font-mono">{elapsedLabel}</span>
                    </Row>
                    {taskId ? (
                        <Row label="Task ID">
                            <button
                                type="button"
                                onClick={() => copy(taskId, "task")}
                                className="inline-flex items-center gap-1.5 rounded border border-white/8 bg-black/35 px-2 py-[3px] font-mono text-[10.5px] tracking-tight text-foreground/95 transition-colors hover:border-primary/45"
                            >
                                <span className="truncate">{taskId}</span>
                                {copied === "task" ? <Check size={11} /> : <Copy size={11} />}
                            </button>
                        </Row>
                    ) : null}
                    <Row label="Backend">
                        {health.kind === "loading" ? (
                            <span className="font-mono text-text-muted/85">checking…</span>
                        ) : health.kind === "ok" ? (
                            <span className="font-mono text-emerald-300">reachable · {health.data.studio_projects} studio · {health.data.atelier_projects} atelier</span>
                        ) : (
                            <span className="font-mono text-red-300">unreachable · {health.message}</span>
                        )}
                    </Row>
                    {health.kind === "ok" ? (
                        <Row label="Log file">
                            <button
                                type="button"
                                onClick={() => copy(health.data.log_file, "log")}
                                className="inline-flex items-center gap-1.5 rounded border border-white/8 bg-black/35 px-2 py-[3px] font-mono text-[10.5px] tracking-tight text-foreground/95 transition-colors hover:border-primary/45"
                                title="Copy log path"
                            >
                                <FileText size={11} aria-hidden="true" />
                                <span className="truncate max-w-[300px]">{health.data.log_file}</span>
                                {copied === "log" ? <Check size={11} /> : <Copy size={11} />}
                            </button>
                        </Row>
                    ) : null}
                    <div className="rounded-md border border-dashed border-white/10 bg-black/20 px-3 py-2.5 text-[11.5px] leading-[1.55] text-text-secondary/85">
                        <div className="mb-1 font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                            Quick checks
                        </div>
                        <ol className="list-decimal space-y-1 pl-4">
                            <li>Press F5 to refresh — polling may have stalled.</li>
                            <li>If backend is unreachable, the desktop app or <code className="rounded bg-white/[0.06] px-1 font-mono text-[10.5px]">./start_backend.sh</code> may have stopped. Restart it.</li>
                            <li>Open the log file (path above) and look for <code className="rounded bg-white/[0.06] px-1 font-mono text-[10.5px]">ERROR</code> / <code className="rounded bg-white/[0.06] px-1 font-mono text-[10.5px]">Failed</code> / <code className="rounded bg-white/[0.06] px-1 font-mono text-[10.5px]">Unauthorized</code>.</li>
                            <li>Backend restart wipes in-memory tasks; a stuck task is automatically marked failed at startup, so retry usually works.</li>
                        </ol>
                    </div>
                </div>
            </div>
        </>
    );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="flex items-center justify-between gap-3">
            <span className="font-mono text-[9.5px] font-medium uppercase tracking-[0.24em] text-text-muted/85">
                {label}
            </span>
            <div className="min-w-0 flex-1 text-right">{children}</div>
        </div>
    );
}
