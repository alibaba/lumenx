"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useAtelierStore, type AtelierAgentTransportOptions } from "@/store/atelierStore";
import { atelierAgentLoopApi } from "@/lib/atelierAgentLoopApi";
import type { AtelierNode, AtelierProductSkillCatalogItem, AtelierProject, JsonRecord, JsonValue } from "@/lib/atelierTypes";

const buttonClass = "rounded-xl border border-white/[0.1] bg-white/[0.06] px-3 py-2 text-xs text-white/70 transition hover:border-white/[0.18] hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-40";
const fieldClass = "w-full rounded-xl border border-white/[0.1] bg-black/35 px-3 py-2 text-sm text-white/80 outline-none transition focus:border-white/25";

type ToolCall = JsonRecord & { call_id: string; tool_name: string };

function isRecord(value: unknown): value is JsonRecord {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: JsonValue | undefined): string | undefined {
    return typeof value === "string" ? value : undefined;
}

function numberValue(value: JsonValue | undefined): number | undefined {
    return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function callsOf(turn: JsonRecord): ToolCall[] {
    return Array.isArray(turn.tool_calls) ? turn.tool_calls.filter((item): item is ToolCall => isRecord(item) && typeof item.call_id === "string" && typeof item.tool_name === "string") : [];
}

function previewBinding(call: ToolCall): JsonRecord | null {
    return isRecord(call.preview_binding) ? call.preview_binding : null;
}

function canvasBinding(call: ToolCall): JsonRecord | null {
    return isRecord(call.canvas_approval_binding) ? call.canvas_approval_binding : null;
}

function loopContext(turn: JsonRecord): JsonRecord | null {
    return isRecord(turn.loop_start_context) ? turn.loop_start_context : null;
}

function publicRecord(call: ToolCall): JsonRecord | null {
    const binding = previewBinding(call);
    return binding && isRecord(binding.preview_public_record) ? binding.preview_public_record : null;
}

function rawRequest(call: ToolCall): JsonRecord | null {
    const record = publicRecord(call);
    return record && isRecord(record.raw_request) ? record.raw_request : null;
}

function intent(call: ToolCall): JsonRecord | null {
    const record = publicRecord(call);
    return record && isRecord(record.intent) ? record.intent : null;
}

function firstFrame(call: ToolCall): JsonRecord | null {
    const record = publicRecord(call);
    if (record && isRecord(record.first_frame)) return record.first_frame;
    const raw = rawRequest(call);
    return raw && isRecord(raw.first_frame) ? raw.first_frame : null;
}

function mediaRecords(call: ToolCall): JsonRecord[] {
    const media = publicRecord(call)?.media;
    return Array.isArray(media) ? media.filter(isRecord) : [];
}

function shortHash(value: string | undefined): string {
    return value ? `${value.slice(0, 10)}…` : "—";
}

function formatBytes(value: number | undefined): string {
    if (value === undefined) return "—";
    return `${value} B`;
}

function fixedInputSummary(call: ToolCall): string {
    const frame = firstFrame(call);
    const media = mediaRecords(call).find(item => stringValue(item.role) === "first_frame") ?? mediaRecords(call)[0];
    const source = stringValue(frame?.node_id) ?? "—";
    if (!media) return source;
    const role = stringValue(media.role) ?? "media";
    return `${role} · ${source} · ${shortHash(stringValue(media.sha256))} · ${formatBytes(numberValue(media.size_bytes))}`;
}

function fullMediaProof(call: ToolCall): string {
    const media = mediaRecords(call);
    if (!media.length) return "—";
    return media.map(item => `${stringValue(item.role) ?? "media"}:${stringValue(item.sha256) ?? "—"}:${formatBytes(numberValue(item.size_bytes))}`).join(" · ");
}

function resultSnapshot(call: ToolCall): JsonRecord | null {
    return isRecord(call.result_snapshot) ? call.result_snapshot : null;
}

function authRef(call: ToolCall): JsonRecord | null {
    const binding = previewBinding(call);
    if (binding && isRecord(binding.agent_authorization_ref)) return binding.agent_authorization_ref;
    const canvas = canvasBinding(call);
    return canvas && isRecord(canvas.canvas_authorization_ref) ? canvas.canvas_authorization_ref : null;
}

function approval(call: ToolCall): JsonRecord | null {
    if (isRecord(call.approval_record)) return call.approval_record;
    return isRecord(call.canvas_approval_record) ? call.canvas_approval_record : null;
}

function canvasArguments(call: ToolCall): JsonRecord | null {
    const binding = canvasBinding(call);
    return binding && isRecord(binding.normalized_arguments) ? binding.normalized_arguments : null;
}

function canvasSummary(call: ToolCall): string {
    const args = canvasArguments(call);
    const title = stringValue(args?.title);
    const prompt = stringValue(args?.prompt);
    return [title, prompt].filter(Boolean).join(" · ") || "—";
}

function paramsSummary(call: ToolCall): string {
    const data = intent(call)?.params;
    if (!isRecord(data) || !Object.keys(data).length) return "—";
    return Object.entries(data).map(([key, value]) => `${key}: ${String(value)}`).join(" · ");
}

function isLoopTurn(turn: JsonRecord): boolean {
    return loopContext(turn) !== null;
}

function allCalls(project: AtelierProject): Array<{ turn: JsonRecord; call: ToolCall }> {
    return project.agent_turns
        .filter(isRecord)
        .flatMap(turn => callsOf(turn).map(call => ({ turn, call })))
        .sort((left, right) => (numberValue(right.turn.created_at) ?? 0) - (numberValue(left.turn.created_at) ?? 0));
}

function loopTurnsOf(project: AtelierProject): JsonRecord[] {
    return project.agent_turns.filter((turn): turn is JsonRecord => isRecord(turn) && isLoopTurn(turn))
        .sort((left, right) => (numberValue(right.created_at) ?? 0) - (numberValue(left.created_at) ?? 0));
}

function generationResultCalls(turn: JsonRecord): ToolCall[] {
    return callsOf(turn).filter(call => call.tool_name === "generation.createVideoCandidates" && resultSnapshot(call));
}

function isKnownLoopTool(call: ToolCall): boolean {
    return call.tool_name === "generation.createVideoCandidates"
        || call.tool_name === "canvas.createVideoNode"
        || call.tool_name === "canvas.updateNodePrompt"
        || call.tool_name === "canvas.readProject";
}

function hasActionablePerCallControl(call: ToolCall): boolean {
    if (call.tool_name !== "generation.createVideoCandidates" && call.tool_name !== "canvas.createVideoNode" && call.tool_name !== "canvas.updateNodePrompt") return false;
    const decision = stringValue(approval(call)?.decision);
    const terminal = isTerminalStatus(stringValue(call.status)) || decision === "denied" || decision === "revoked";
    if (terminal) return false;
    if (!approval(call) && stringValue(call.status) === "approval_required") return true;
    return decision === "approved" && !resultSnapshot(call);
}

function loopNeedsTurnCard(turn: JsonRecord): boolean {
    const calls = callsOf(turn);
    if (calls.length === 0) return true;
    if (isTerminalStatus(stringValue(turn.status))) return false;
    if (calls.some(call => !isKnownLoopTool(call) && !isTerminalStatus(stringValue(call.status)) && !resultSnapshot(call))) return false;
    return !calls.some(hasActionablePerCallControl);
}

function activeGenerationSummary(project: AtelierProject, turn: JsonRecord): string | null {
    for (const call of generationResultCalls(turn)) {
        const attemptId = stringValue(resultSnapshot(call)?.attempt_id);
        const attempt = attemptId ? project.generation_attempts.find(item => item.attempt_id === attemptId) : null;
        if (attemptId && attempt && !isTerminalStatus(attempt.status)) return attemptId;
    }
    return null;
}

function isTerminalStatus(value: string | undefined): boolean {
    return value === "completed" || value === "failed" || value === "canceled" || value === "cancelled";
}

export function AgentTurnPanel({ project, selectedNode, ownerKey, disabled }: {
    project: AtelierProject;
    selectedNode: AtelierNode | null;
    ownerKey: string;
    disabled: boolean;
}) {
    const t = useTranslations("atelier.agentTurnPanel");
    const startAgentTurn = useAtelierStore(state => state.startAgentTurn);
    const startAgentLoop = useAtelierStore(state => state.startAgentLoop);
    const loadPendingAgentTurns = useAtelierStore(state => state.loadPendingAgentTurns);
    const readbackPendingAgentTurn = useAtelierStore(state => state.readbackPendingAgentTurn);
    const retryPendingAgentTurn = useAtelierStore(state => state.retryPendingAgentTurn);
    const approveAgentCall = useAtelierStore(state => state.approveAgentCall);
    const denyAgentCall = useAtelierStore(state => state.denyAgentCall);
    const revokeAgentCall = useAtelierStore(state => state.revokeAgentCall);
    const continueAgentCall = useAtelierStore(state => state.continueAgentCall);
    const recordAgentLoopCanvasApproval = useAtelierStore(state => state.recordAgentLoopCanvasApproval);
    const resumeAgentLoop = useAtelierStore(state => state.resumeAgentLoop);
    const refreshAgentTurn = useAtelierStore(state => state.refreshAgentTurn);
    const refreshAgentLoop = useAtelierStore(state => state.refreshAgentLoop);
    const cancelAgentTurn = useAtelierStore(state => state.cancelAgentTurn);
    const pendingAgentTurns = useAtelierStore(state => state.pendingAgentTurns);
    const isSaving = useAtelierStore(state => state.isSaving);
    const needsRefresh = useAtelierStore(state => state.needsRefresh);

    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [streamStage, setStreamStage] = useState<string | null>(null);
    const [productSkills, setProductSkills] = useState<AtelierProductSkillCatalogItem[]>([]);
    const [skillsLoaded, setSkillsLoaded] = useState(false);
    const alive = useRef(true);
    const ownerRef = useRef(ownerKey);
    const projectRef = useRef(project.id);
    const activeController = useRef<AbortController | null>(null);

    useEffect(() => {
        alive.current = true;
        ownerRef.current = ownerKey;
        projectRef.current = project.id;
        setBusy(null);
        setNotice(null);
        setError(null);
        setStreamStage(null);
        try { loadPendingAgentTurns(); } catch (cause) { setError(cause instanceof Error ? cause.message : t("storageBlocked")); }
        return () => {
            alive.current = false;
            activeController.current?.abort();
            activeController.current = null;
        };
    }, [loadPendingAgentTurns, ownerKey, project.id, t]);

    useEffect(() => {
        let cancelled = false;
        setSkillsLoaded(false);
        atelierAgentLoopApi.listProductSkills()
            .then(items => { if (!cancelled) setProductSkills(items); })
            .catch(() => { if (!cancelled) setProductSkills([]); })
            .finally(() => { if (!cancelled) setSkillsLoaded(true); });
        return () => { cancelled = true; };
    }, [project.id, ownerKey]);

    const turns = useMemo(() => allCalls(project), [project]);
    const loopTurnCards = useMemo(() => loopTurnsOf(project).filter(loopNeedsTurnCard), [project]);
    const blocked = disabled || isSaving || needsRefresh || busy !== null;
    const readBlocked = busy !== null;

    const makeStreamOptions = useCallback((owner: string, projectId: string): { controller: AbortController; options: AtelierAgentTransportOptions } => {
        activeController.current?.abort();
        const controller = new AbortController();
        activeController.current = controller;
        return {
            controller,
            options: {
                transport: "stream",
                signal: controller.signal,
                onEvent: event => {
                    if (!alive.current || ownerRef.current !== owner || projectRef.current !== projectId || controller.signal.aborted) return;
                    setStreamStage(event.type === "agent_error" ? null : t(`stage.${event.type}`));
                },
            },
        };
    }, [t]);

    const run = useCallback(async (key: string, action: () => Promise<unknown>, success?: string, controller?: AbortController) => {
        const owner = ownerRef.current;
        const projectId = projectRef.current;
        setBusy(key);
        setError(null);
        setNotice(null);
        setStreamStage(null);
        try {
            await action();
            if (alive.current && ownerRef.current === owner && projectRef.current === projectId && success) {
                setNotice(success);
                setStreamStage(null);
            }
        } catch (cause) {
            if (alive.current && ownerRef.current === owner && projectRef.current === projectId) {
                setStreamStage(null);
                const rejected = cause instanceof Error && "code" in cause && cause.code === "atelier_agent_start_rejected";
                setError(rejected ? t("startRejected") : cause instanceof Error ? cause.message : t("actionFailed"));
            }
        } finally {
            if (controller && activeController.current === controller) activeController.current = null;
            if (alive.current && ownerRef.current === owner && projectRef.current === projectId) setBusy(current => current === key ? null : current);
        }
    }, [t]);

    const start = (entrypoint: "single" | "loop" | "model-loop", skillName?: string) => {
        const trimmed = message.trim();
        if (!trimmed) return;
        const owner = ownerRef.current;
        const projectId = projectRef.current;
        const stream = makeStreamOptions(owner, projectId);
        if (entrypoint === "loop" || entrypoint === "model-loop") {
            const key = skillName ? `start-skill:${skillName}` : entrypoint === "model-loop" ? "start-model-loop" : "start-loop";
            void run(key, () => startAgentLoop(
                { userMessage: trimmed, selectedNodeId: selectedNode?.id ?? null, ...(entrypoint === "model-loop" ? { planner: "model_adapter" as const } : {}), ...(skillName === undefined ? {} : { skillName }) },
                stream.options,
            ), skillName ? t("skillStarted") : entrypoint === "model-loop" ? t("modelLoopStarted") : t("loopStarted"), stream.controller);
            return;
        }
        void run("start", () => startAgentTurn(
            { userMessage: trimmed, selectedNodeId: selectedNode?.id ?? null },
            stream.options,
        ), t("started"), stream.controller);
    };

    return <section className="space-y-3 rounded-2xl border border-white/[0.08] bg-white/[0.04] p-3" aria-label={t("title")}>
        <div>
            <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">{t("eyebrow")}</div>
            <h3 className="mt-1 font-display text-sm font-semibold text-white/85">{t("title")}</h3>
            <p className="mt-1 text-xs leading-5 text-white/45">{selectedNode ? t("scopeNode", { node: selectedNode.title || selectedNode.id }) : t("scopeProject")}</p>
        </div>

        {pendingAgentTurns.length ? <div className="space-y-2 rounded-xl border border-amber-300/20 bg-amber-500/10 p-3 text-xs text-amber-100">
            <p>{t("pendingStart")}</p>
            {pendingAgentTurns.map(request => <div key={request.clientTurnId} className="flex flex-wrap gap-2">
                <span className="basis-full font-mono text-[11px] text-amber-100/80">{request.entrypoint === "agent_loop" ? t("pendingGuided") : t("pendingOneStep")} · {request.clientTurnId}</span>
                <button type="button" className={buttonClass} disabled={readBlocked} onClick={() => void run(`read:${request.clientTurnId}`, () => readbackPendingAgentTurn(request.clientTurnId), t("readbackDone"))}>{t("readback")}</button>
                <button type="button" className={buttonClass} disabled={readBlocked} onClick={() => void run(`retry:${request.clientTurnId}`, () => retryPendingAgentTurn(request.clientTurnId), t("retryDone"))}>{t("retryOriginal")}</button>
            </div>)}
        </div> : null}

        <label className="block text-xs text-white/45">
            {t("message")}
            <textarea className={`${fieldClass} mt-1 min-h-[72px]`} aria-label={t("message")} value={message} onChange={event => setMessage(event.target.value)} placeholder={selectedNode ? t("placeholderNode") : t("placeholder")} />
        </label>
        <div className="flex flex-wrap gap-2">
            <button type="button" className={buttonClass} disabled={blocked || !message.trim()} onClick={() => start("loop")}>{busy === "start-loop" ? t("working") : t("startGuided")}</button>
            <button type="button" className={buttonClass} disabled={blocked || !message.trim()} onClick={() => start("model-loop")}>{busy === "start-model-loop" ? t("working") : t("startModelGuided")}</button>
            <button type="button" className={buttonClass} disabled={blocked || !message.trim()} onClick={() => start("single")}>{busy === "start" ? t("working") : t("start")}</button>
        </div>
        <p className="text-[11px] leading-4 text-white/38">{t("modelGuidedNote")}</p>
        {skillsLoaded && productSkills.length ? <div className="space-y-2 rounded-xl border border-white/[0.08] bg-black/20 p-3" aria-label={t("productSkills")}>
            <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-medium text-white/65">{t("productSkills")}</span>
                <span className="text-[11px] text-white/35">{t("productSkillsModelOnly")}</span>
            </div>
            <div className="grid gap-2">
                {productSkills.map(item => {
                    const startKey = `start-skill:${item.name}`;
                    const disabledCard = blocked || !message.trim() || !item.enabled;
                    return <button key={item.name} type="button" className="rounded-xl border border-white/[0.08] bg-white/[0.04] p-3 text-left transition hover:border-white/[0.16] hover:bg-white/[0.07] disabled:cursor-not-allowed disabled:opacity-45" disabled={disabledCard} onClick={() => start("model-loop", item.name)}>
                        <span className="block text-xs font-semibold text-white/75">{busy === startKey ? t("working") : item.title}</span>
                        <span className="mt-1 block text-[11px] leading-4 text-white/45">{item.description}</span>
                        <span className="mt-2 block text-[11px] text-white/35">{item.enabled ? t("skillEnabled", { version: item.version }) : t("skillDisabled", { reason: item.disabled_reason ?? t("skillUnavailable") })}</span>
                    </button>;
                })}
            </div>
        </div> : null}
        {streamStage ? <p role="status" className="rounded-xl border border-sky-300/20 bg-sky-500/10 p-3 text-xs text-sky-100">{streamStage}</p> : null}

        {turns.length || loopTurnCards.length ? <div className="space-y-2">
            <h4 className="text-xs font-medium text-white/65">{t("turns")}</h4>
            {loopTurnCards.map(turn => {
                const turnId = stringValue(turn.id) ?? "";
                const terminal = isTerminalStatus(stringValue(turn.status));
                const activeAttempt = activeGenerationSummary(project, turn);
                return <div key={`${turnId}:loop`} className="space-y-2 rounded-xl border border-white/[0.08] bg-black/20 p-3 text-xs text-white/55">
                    <div className="flex items-center justify-between gap-3">
                        <span className="font-mono text-[11px] text-white/45">{t("guidedRun")} · {turnId}</span>
                        <span>{terminal ? t(`terminal.${stringValue(turn.status) === "cancelled" ? "canceled" : stringValue(turn.status) ?? "completed"}`) : t("loopWaiting")}</span>
                    </div>
                    {typeof turn.skill_name === "string" ? <p className="text-[11px] text-violet-100/70">{t("activeSkill", { skill: turn.skill_name })}</p> : null}
                    <p className="text-white/50">{terminal ? t("historyOnly") : activeAttempt ? t("loopGenerationWaiting", { attempt: activeAttempt }) : t("loopRecoveryHint")}</p>
                    <div className="flex flex-wrap gap-2">
                        <button type="button" className={buttonClass} disabled={readBlocked} onClick={() => void run(`refresh:${turnId}`, () => refreshAgentLoop(turnId), t("readbackDone"))}>{t("refreshTurn")}</button>
                        {!terminal ? <button type="button" className={buttonClass} disabled={blocked} onClick={() => {
                            const owner = ownerRef.current;
                            const projectId = projectRef.current;
                            const stream = makeStreamOptions(owner, projectId);
                            void run(`resume:${turnId}`, async () => {
                                const ack = await resumeAgentLoop(turnId, stream.options);
                                if (ack.schedule_error) throw new Error(t("scheduleFailed", { error: ack.schedule_error }));
                            }, t("loopContinued"), stream.controller);
                        }}>{t("continueGuided")}</button> : null}
                        {!terminal ? <button type="button" className={buttonClass} disabled={blocked} onClick={() => void run(`cancel:${turnId}`, () => cancelAgentTurn(turnId), t("cancelled"))}>{t("cancel")}</button> : null}
                    </div>
                </div>;
            })}
            {turns.map(({ turn, call }) => {
                const raw = rawRequest(call);
                const ref = authRef(call);
                const record = approval(call);
                const result = resultSnapshot(call);
                const decision = stringValue(record?.decision);
                const turnId = stringValue(turn.id) ?? "";
                const callId = call.call_id;
                const loop = isLoopTurn(turn);
                const knownGenerationTool = call.tool_name === "generation.createVideoCandidates";
                const knownCanvasTool = call.tool_name === "canvas.createVideoNode" || call.tool_name === "canvas.updateNodePrompt";
                const knownReadOnlyTool = call.tool_name === "canvas.readProject";
                const knownTool = knownGenerationTool || knownCanvasTool || knownReadOnlyTool;
                const terminal = isTerminalStatus(stringValue(turn.status)) || isTerminalStatus(stringValue(call.status)) || decision === "denied" || decision === "revoked";
                const canApproveGeneration = !terminal && knownGenerationTool && !record && stringValue(call.status) === "approval_required";
                const canApproveCanvas = loop && !terminal && knownCanvasTool && !record && stringValue(call.status) === "approval_required";
                const canApprove = canApproveGeneration || canApproveCanvas;
                const canContinueGeneration = !loop && knownGenerationTool && decision === "approved" && !terminal && !result;
                const canContinueLoop = loop && (knownGenerationTool || knownCanvasTool) && decision === "approved" && !terminal && !result;
                const canContinue = canContinueGeneration || canContinueLoop;
                const canRevokeGeneration = knownGenerationTool && decision === "approved" && !terminal && !result;
                const canRevokeCanvas = loop && knownCanvasTool && decision === "approved" && !terminal && !result;
                const canRevoke = canRevokeGeneration || canRevokeCanvas;
                const canCancel = !terminal;
                const terminalStatus = isTerminalStatus(stringValue(turn.status)) ? stringValue(turn.status) : stringValue(call.status);
                const terminalKey = terminalStatus === "cancelled" ? "canceled" : terminalStatus ?? "completed";
                const statusLabel = !knownTool ? t("unsupportedTool", { tool: call.tool_name })
                    : knownReadOnlyTool ? t("readOnlyResult")
                        : decision ? t(`decision.${decision}`)
                            : terminal ? t(`terminal.${terminalKey}`) : t("waitingApproval");
                return <div key={`${turnId}:${callId}`} className="space-y-2 rounded-xl border border-white/[0.08] bg-black/20 p-3 text-xs text-white/55">
                    <div className="flex items-center justify-between gap-3">
                        <span className="font-mono text-[11px] text-white/45">{loop ? `${t("guidedRun")} · ${turnId}` : turnId}</span>
                        <span>{statusLabel}</span>
                    </div>
                    <dl className="grid grid-cols-1 gap-1 text-white/60">
                        {knownReadOnlyTool ? <div><dt className="inline text-white/35">{t("readOnlyResult")}: </dt><dd className="inline">{t("readProjectSummary")}</dd></div> : null}
                        {!knownReadOnlyTool && knownCanvasTool ? <div><dt className="inline text-white/35">{t("canvasChange")}: </dt><dd className="inline">{canvasSummary(call)}</dd></div> : null}
                        <div><dt className="inline text-white/35">{t("prompt")}: </dt><dd className="inline">{knownCanvasTool ? stringValue(canvasArguments(call)?.prompt) ?? stringValue(turn.user_message) ?? "—" : stringValue(raw?.prompt) ?? stringValue(turn.user_message) ?? "—"}</dd></div>
                        {!knownReadOnlyTool ? <div><dt className="inline text-white/35">{t("model")}: </dt><dd className="inline">{knownCanvasTool ? t("localCanvas") : stringValue(raw?.model_id) ?? "—"}</dd></div> : null}
                        {!knownReadOnlyTool ? <div><dt className="inline text-white/35">{t("params")}: </dt><dd className="inline">{knownCanvasTool ? canvasSummary(call) : paramsSummary(call)}</dd></div> : null}
                        {!knownReadOnlyTool ? <div><dt className="inline text-white/35">{t("fixedInput")}: </dt><dd className="inline">{knownCanvasTool ? t("canvasSnapshot") : fixedInputSummary(call)}</dd></div> : null}
                        {!knownReadOnlyTool ? <div><dt className="inline text-white/35">{t("price")}: </dt><dd className="inline">{knownCanvasTool ? t("noProviderCost") : t("priceUnknown")}</dd></div> : null}
                        {knownGenerationTool && result ? <div><dt className="inline text-white/35">{t("attempt")}: </dt><dd className="inline font-mono">{stringValue(result.attempt_id) ?? "—"} · {t("attemptSaved")}</dd></div> : null}
                        {!knownTool ? <div className="text-amber-100/80">{t("unsupportedDiagnostic")}</div> : null}
                        {terminal && !result ? <div className="text-white/40">{t("historyOnly")}</div> : null}
                    </dl>
                    <details className="rounded-lg border border-white/[0.06] bg-white/[0.03] p-2">
                        <summary className="cursor-pointer text-white/45">{t("details")}</summary>
                        <dl className="mt-2 space-y-1 text-[11px] text-white/45">
                            <div><dt className="inline">{t("storage")}: </dt><dd className="inline font-mono">{stringValue(ref?.server_storage_identity) ?? "—"}</dd></div>
                            <div><dt className="inline">{t("provider")}: </dt><dd className="inline font-mono">{knownCanvasTool || knownReadOnlyTool ? t("localCanvas") : stringValue(publicRecord(call)?.provider_model_id) ?? "—"}</dd></div>
                            <div><dt className="inline">{t("authorization")}: </dt><dd className="inline font-mono">{stringValue(ref?.approval_id) ?? "—"}</dd></div>
                            <div><dt className="inline">{t("mediaProof")}: </dt><dd className="inline font-mono break-all">{fullMediaProof(call)}</dd></div>
                        </dl>
                    </details>
                    <div className="flex flex-wrap gap-2">
                        {canApprove ? <button type="button" className={buttonClass} disabled={blocked} onClick={() => void run(`approve:${callId}`, () => canApproveCanvas ? recordAgentLoopCanvasApproval(turnId, callId, "approved") : approveAgentCall(turnId, callId), t("approved"))}>{canApproveCanvas ? t("approveCanvas") : t("approve")}</button> : null}
                        {canApprove ? <button type="button" className={buttonClass} disabled={blocked} onClick={() => void run(`deny:${callId}`, () => canApproveCanvas ? recordAgentLoopCanvasApproval(turnId, callId, "denied") : denyAgentCall(turnId, callId), t("denied"))}>{canApproveCanvas ? t("denyCanvas") : t("deny")}</button> : null}
                        {canContinue ? <button type="button" className={buttonClass} disabled={blocked} onClick={() => {
                            const owner = ownerRef.current;
                            const projectId = projectRef.current;
                            const stream = makeStreamOptions(owner, projectId);
                            void run(`continue:${callId}`, async () => {
                                if (canContinueLoop) {
                                    const ack = await resumeAgentLoop(turnId, stream.options);
                                    if (ack.schedule_error) throw new Error(t("scheduleFailed", { error: ack.schedule_error }));
                                    return;
                                }
                                const ack = await continueAgentCall(turnId, callId, stream.options);
                                if (ack.schedule_error) throw new Error(t("scheduleFailed", { error: ack.schedule_error }));
                            }, canContinueLoop ? t("loopContinued") : t("continued"), stream.controller);
                        }}>{canContinueLoop ? t("continueGuided") : t("continue")}</button> : null}
                        {canContinue ? <button type="button" className={buttonClass} disabled={readBlocked} onClick={() => void run(`refresh:${turnId}`, () => loop ? refreshAgentLoop(turnId) : refreshAgentTurn(turnId), t("readbackDone"))}>{t("refreshTurn")}</button> : null}
                        {canRevoke ? <button type="button" className={buttonClass} disabled={blocked} onClick={() => void run(`revoke:${callId}`, () => canRevokeCanvas ? recordAgentLoopCanvasApproval(turnId, callId, "revoked") : revokeAgentCall(turnId, callId), t("revoked"))}>{canRevokeCanvas ? t("revokeCanvas") : t("revoke")}</button> : null}
                        {canCancel ? <button type="button" className={buttonClass} disabled={blocked} onClick={() => void run(`cancel:${turnId}`, () => cancelAgentTurn(turnId), t("cancelled"))}>{t("cancel")}</button> : null}
                    </div>
                </div>;
            })}
        </div> : <p className="rounded-xl border border-white/[0.08] bg-black/25 p-3 text-xs text-white/45">{t("empty")}</p>}

        {notice ? <p role="status" className="rounded-xl border border-emerald-300/20 bg-emerald-500/10 p-3 text-xs text-emerald-100">{notice}</p> : null}
        {error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-500/10 p-3 text-xs text-rose-100">{error}</p> : null}
    </section>;
}

export default AgentTurnPanel;
