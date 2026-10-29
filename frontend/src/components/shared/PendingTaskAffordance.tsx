"use client";
//
// PendingTaskAffordance — drop-in for any "pending" / "processing" UI
// state that's vulnerable to silent stalls. Replaces the bare spinner
// pattern with a self-narrating block that:
//
//   - Shows the live elapsed time ("已等待 2m 14s")
//   - After a soft threshold (default 60s) reveals Cancel + Diagnose
//     buttons. The spinner alone gives the user nothing to do — these
//     turn "looks stuck" into "I can recover".
//   - Diagnose opens a modal that pings the backend /health endpoint
//     and shows: "backend reachable / task_id / log file path". This
//     converts the support workflow for stuck tasks from a Slack
//     request into a self-serve sheet.
//
// Used by: Studio ShotCard pending branch. Container is provided by the
// parent; we render content + a modal portal.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, X, AlertTriangle, FileText, Copy, Check, RefreshCw, Terminal } from "lucide-react";
import { diagnosticsApi } from "@/lib/diagnosticsApi";

interface Props {
    /** Task creation timestamp (epoch seconds). When omitted the
     *  component falls back to its own mount time — fine for stuck-
     *  task detection in the active session, just less precise across
     *  refreshes (timer resets). */
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

    const elapsedClass = compact ? "text-[0.625rem]" : "text-[0.6875rem]";
    const statusClass = compact ? "text-[0.625rem]" : "text-[0.6875rem]";

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
                            className="rounded-md border border-red-300/30 bg-red-400/10 px-2 py-[3px] font-mono text-[0.59375rem] font-medium uppercase tracking-[0.2em] text-red-200/95 transition-colors hover:bg-red-400/20 disabled:cursor-wait disabled:opacity-60"
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
                        className="rounded-md border border-foreground/15 bg-black/30 px-2 py-[3px] font-mono text-[0.59375rem] font-medium uppercase tracking-[0.2em] text-text-secondary/95 transition-colors hover:border-primary/45 hover:text-foreground"
                    >
                        Diagnose
                    </button>
                </div>
            ) : null}
            {cancelError ? (
                <span className="font-mono text-[0.5625rem] text-red-300/85">
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
        | { kind: "ok"; data: Awaited<ReturnType<typeof diagnosticsApi.healthCheck>> }
        | { kind: "error"; message: string };
    type LogState =
        | { kind: "idle" }
        | { kind: "loading" }
        | { kind: "ok"; data: Awaited<ReturnType<typeof diagnosticsApi.diagnoseLogTail>> }
        | { kind: "error"; message: string };
    const [health, setHealth] = useState<HealthState>({ kind: "loading" });
    const [log, setLog] = useState<LogState>({ kind: "idle" });
    const [copied, setCopied] = useState<"task" | "log_path" | "log_text" | null>(null);

    const loadLog = () => {
        setLog({ kind: "loading" });
        diagnosticsApi.diagnoseLogTail(200)
            .then((data) => setLog({ kind: "ok", data }))
            .catch((err) =>
                setLog({ kind: "error", message: err instanceof Error ? err.message : String(err) }),
            );
    };

    // Auto-fetch health on mount; auto-fetch log too so the user
    // doesn't need to click anything to see what's wrong.
    useEffect(() => {
        let cancelled = false;
        diagnosticsApi.healthCheck()
            .then((data) => {
                if (!cancelled) setHealth({ kind: "ok", data });
            })
            .catch((err) => {
                if (!cancelled) setHealth({ kind: "error", message: err instanceof Error ? err.message : String(err) });
            });
        loadLog();
        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const copy = async (text: string, kind: "task" | "log_path" | "log_text") => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(kind);
            window.setTimeout(() => setCopied((c) => (c === kind ? null : c)), 1400);
        } catch {
            /* ignore */
        }
    };

    // Esc to close — convenient when the modal is portaled out of focus.
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [onClose]);

    const modal = (
        <>
            <div
                aria-hidden="true"
                className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-sm"
                onClick={onClose}
            />
            <div
                role="dialog"
                aria-label="Diagnose stuck task"
                className="fixed left-1/2 top-1/2 z-[61] flex w-[min(720px,94vw)] max-h-[85vh] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-[12px] border border-glass-border bg-elevated shadow-[0_24px_48px_-22px_rgba(0,0,0,0.85),inset_0_1px_0_0_rgba(255,255,255,0.06)]"
            >
                <div aria-hidden="true" className="h-[2px] shrink-0 bg-gradient-to-r from-amber-300/85 via-amber-300/35 to-transparent" />
                <header className="flex shrink-0 items-center justify-between gap-3 border-b border-glass-border px-4 py-3">
                    <div className="flex items-center gap-2">
                        <AlertTriangle size={14} className="text-amber-300" aria-hidden="true" />
                        <div className="font-display text-[0.875rem] font-medium tracking-[-0.005em] text-foreground">
                            Diagnose stuck task
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close"
                        className="grid h-7 w-7 place-items-center rounded text-text-muted hover:bg-hover-bg hover:text-foreground"
                    >
                        <X size={14} aria-hidden="true" />
                    </button>
                </header>
                <div className="space-y-3 overflow-y-auto px-4 py-4 text-[0.78125rem] leading-[1.55] text-text-secondary/95">
                    <Row label="Elapsed">
                        <span className="font-mono">{elapsedLabel}</span>
                    </Row>
                    {taskId ? (
                        <Row label="Task ID">
                            <button
                                type="button"
                                onClick={() => copy(taskId, "task")}
                                className="inline-flex items-center gap-1.5 rounded border border-glass-border bg-black/35 px-2 py-[3px] font-mono text-[0.65625rem] tracking-tight text-foreground transition-colors hover:border-primary/45"
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
                            <span className="font-mono text-emerald-300">reachable · {health.data.studio_projects} project(s)</span>
                        ) : (
                            <span className="font-mono text-red-300">unreachable · {health.message}</span>
                        )}
                    </Row>
                    {health.kind === "ok" ? (
                        <Row label="Log file">
                            <button
                                type="button"
                                onClick={() => copy(health.data.log_file, "log_path")}
                                className="inline-flex items-center gap-1.5 rounded border border-glass-border bg-black/35 px-2 py-[3px] font-mono text-[0.65625rem] tracking-tight text-foreground transition-colors hover:border-primary/45"
                                title="Copy log path"
                            >
                                <FileText size={11} aria-hidden="true" />
                                <span className="truncate max-w-[420px]">{health.data.log_file}</span>
                                {copied === "log_path" ? <Check size={11} /> : <Copy size={11} />}
                            </button>
                        </Row>
                    ) : null}

                    {/* Inline log tail — auto-fetched. The user doesn't need
                        to leave the app to investigate the typical "stuck"
                        case (provider auth, network out, model misuse).
                        Two clearly-labeled sub-panels: errors digest
                        on top (just the ERROR rows from the tail —
                        usually the root cause) and the full chronological
                        tail below for context. */}
                    <div className="rounded-md border border-glass-border bg-black/30">
                        <div className="flex items-center justify-between gap-2 border-b border-glass-border px-3 py-1.5 font-mono text-[0.5625rem] font-medium uppercase tracking-[0.24em] text-text-muted/85">
                            <span className="inline-flex items-center gap-1.5">
                                <Terminal size={11} aria-hidden="true" />
                                Backend log
                            </span>
                            <div className="flex items-center gap-1">
                                <button
                                    type="button"
                                    onClick={loadLog}
                                    aria-label="Reload log"
                                    title="Reload"
                                    className="grid h-6 w-6 place-items-center rounded text-text-muted hover:bg-hover-bg hover:text-foreground"
                                >
                                    <RefreshCw size={11} aria-hidden="true" />
                                </button>
                                {log.kind === "ok" && log.data.lines.length > 0 ? (
                                    <button
                                        type="button"
                                        onClick={() => copy(log.data.lines.join("\n"), "log_text")}
                                        aria-label="Copy full log text"
                                        title="Copy full tail"
                                        className="grid h-6 w-6 place-items-center rounded text-text-muted hover:bg-hover-bg hover:text-foreground"
                                    >
                                        {copied === "log_text" ? <Check size={11} /> : <Copy size={11} />}
                                    </button>
                                ) : null}
                            </div>
                        </div>
                        {/* Errors-only digest — pinned to top so the
                            user lands on the actionable signal first.
                            Labeled "Errors only" so it's not confused
                            with the full tail right below. */}
                        {log.kind === "ok" && log.data.errors.length > 0 ? (
                            <>
                                <div className="flex items-center justify-between gap-2 border-b border-red-400/15 bg-red-500/[0.08] px-3 py-1 font-mono text-[0.5625rem] font-medium uppercase tracking-[0.22em] text-red-200/95">
                                    <span>① Errors only · {log.data.errors.length} rows</span>
                                    <span className="text-red-200/70 normal-case tracking-tight">root cause is usually here</span>
                                </div>
                                <div className="max-h-[120px] overflow-y-auto border-b border-glass-border bg-red-500/[0.05] px-3 py-1.5 font-mono text-[0.625rem] leading-[1.6] text-red-200/95">
                                    {log.data.errors.map((line, i) => (
                                        <div key={i} className="whitespace-pre-wrap break-words">{line}</div>
                                    ))}
                                </div>
                            </>
                        ) : null}
                        {/* Full chronological tail — gives context
                            around the errors above (what was running,
                            what the request looked like, etc.). */}
                        <div className="border-b border-glass-border bg-black/20 px-3 py-1 font-mono text-[0.5625rem] font-medium uppercase tracking-[0.22em] text-text-muted/85">
                            ② Full tail · last {log.kind === "ok" ? log.data.returned_lines ?? log.data.lines.length : "200"} lines
                        </div>
                        <div className="max-h-[280px] overflow-y-auto px-3 py-2 font-mono text-[0.625rem] leading-[1.55] text-text-secondary/95">
                            {log.kind === "loading" ? (
                                <div className="text-text-muted/85">loading…</div>
                            ) : log.kind === "error" ? (
                                <div className="text-red-300">Could not read log: {log.message}</div>
                            ) : log.kind === "ok" && log.data.missing ? (
                                <div className="text-text-muted/85">Log file does not exist yet at {log.data.path}.</div>
                            ) : log.kind === "ok" ? (
                                log.data.lines.length === 0 ? (
                                    <div className="text-text-muted/85">Log is empty.</div>
                                ) : (
                                    log.data.lines.map((line, i) => (
                                        <div key={i} className="whitespace-pre-wrap break-words">{line}</div>
                                    ))
                                )
                            ) : null}
                        </div>
                    </div>

                    <div className="rounded-md border border-dashed border-glass-border bg-black/20 px-3 py-2.5 text-[0.71875rem] leading-[1.55] text-text-secondary/85">
                        <div className="mb-1 font-mono text-[0.5625rem] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                            Quick checks
                        </div>
                        <ol className="list-decimal space-y-1 pl-4">
                            <li>Press F5 to refresh — polling may have stalled.</li>
                            <li>If backend is unreachable, the desktop app or <code className="rounded bg-elevated px-1 font-mono text-[0.65625rem]">./start_backend.sh</code> may have stopped. Restart it.</li>
                            <li>Look at the red rows above for the immediate cause (provider auth, network, model misuse).</li>
                            <li>After restart, check the saved task status. An uncertain submission may still be running; confirm its outcome before starting another generation.</li>
                        </ol>
                    </div>
                </div>
            </div>
        </>
    );

    // Portal escapes any transformed/overflow-clipped ancestor (the
    // ShotCard's animated motion.div was the original culprit). Render
    // straight to <body> so a fixed-position modal really is fixed.
    if (typeof window === "undefined") return null;
    return createPortal(modal, document.body);
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="flex items-center justify-between gap-3">
            <span className="font-mono text-[0.59375rem] font-medium uppercase tracking-[0.24em] text-text-muted/85">
                {label}
            </span>
            <div className="min-w-0 flex-1 text-right">{children}</div>
        </div>
    );
}
