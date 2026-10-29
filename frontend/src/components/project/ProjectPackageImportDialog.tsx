"use client";

import { useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FileArchive, Loader2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { api, ProjectPackageImportError } from "@/lib/api";
import { isReadConfirmed } from "@/lib/frameCommandSnapshot";
import {
    parseProjectPackageReceipt,
    PROJECT_PACKAGE_MAX_BYTES,
    type ProjectPackageReceipt,
} from "@/lib/projectPackage";
import {
    getRuntimeEnvironment,
    isCurrentRuntimeEnvironment,
    requireActiveRuntimeEnvironment,
    subscribeRuntimeEnvironment,
} from "@/lib/runtimeEnvironment";
import { useProjectStore } from "@/store/projectStore";
import { toast } from "@/store/toastStore";

interface ProjectPackageImportDialogProps {
    isOpen: boolean;
    onClose: () => void;
}

type ImportPhase = "idle" | "importing" | "confirming" | "confirmed";
type ImportRuntimeMode = "idle" | "receipt" | "unknown";

const importRuntime = {
    receipt: null as ProjectPackageReceipt | null,
    mode: "idle" as ImportRuntimeMode,
    message: null as string | null,
    inFlight: false,
    key: null as string | null,
    generation: 0,
    operation: 0,
    listeners: new Set<() => void>(),
};

function notifyImportRuntime() {
    importRuntime.listeners.forEach(listener => listener());
}

function beginImportRuntime(key: string, generation: number, options: { preserveReceipt?: boolean } = {}) {
    const sameOwner = importRuntime.key === key && importRuntime.generation === generation;
    const preservedReceipt = options.preserveReceipt && sameOwner ? importRuntime.receipt : null;
    importRuntime.operation += 1;
    importRuntime.inFlight = true;
    importRuntime.mode = preservedReceipt ? "receipt" : "idle";
    importRuntime.receipt = preservedReceipt;
    importRuntime.message = null;
    importRuntime.key = key;
    importRuntime.generation = generation;
    notifyImportRuntime();
    return importRuntime.operation;
}

function finishImportRuntime(operation: number) {
    if (operation !== importRuntime.operation) return;
    importRuntime.inFlight = false;
    notifyImportRuntime();
}

function setImportReceipt(receipt: ProjectPackageReceipt | null, key: string | null = importRuntime.key, generation = importRuntime.generation) {
    importRuntime.receipt = receipt;
    importRuntime.mode = receipt ? "receipt" : "idle";
    importRuntime.message = null;
    importRuntime.key = key;
    importRuntime.generation = generation;
    notifyImportRuntime();
}

function setImportUnknown(message: string, key: string | null = importRuntime.key, generation = importRuntime.generation) {
    importRuntime.receipt = null;
    importRuntime.mode = "unknown";
    importRuntime.message = message;
    importRuntime.key = key;
    importRuntime.generation = generation;
    notifyImportRuntime();
}

function subscribeImportRuntime(listener: () => void) {
    importRuntime.listeners.add(listener);
    return () => { importRuntime.listeners.delete(listener); };
}

function resetFileInput(input: HTMLInputElement | null) {
    if (input) input.value = "";
}

function isImportRuntimeOwnedByCurrent(): boolean {
    const runtime = getRuntimeEnvironment();
    return !!runtime && importRuntime.key === runtime.key && importRuntime.generation === runtime.generation;
}

function isRuntimeBusyForCurrent(): boolean {
    return isImportRuntimeOwnedByCurrent() && importRuntime.inFlight;
}

function isRuntimeBlockedForCurrent(): boolean {
    return isImportRuntimeOwnedByCurrent() && (importRuntime.inFlight || importRuntime.mode !== "idle");
}

export default function ProjectPackageImportDialog({ isOpen, onClose }: ProjectPackageImportDialogProps) {
    const t = useTranslations("projectPackage");
    const tc = useTranslations("common");
    const [file, setFile] = useState<File | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [phase, setPhase] = useState<ImportPhase>("idle");
    const [, forceRuntime] = useState(0);
    const inputRef = useRef<HTMLInputElement>(null);
    const dialogRef = useRef<HTMLDivElement>(null);
    const sessionRef = useRef(0);
    const mountedRef = useRef(false);
    const openRef = useRef(isOpen);
    const submitInFlightRef = useRef(false);
    const titleId = useId();
    const currentRuntimeBusy = isRuntimeBusyForCurrent();
    const currentRuntimeBlocked = isRuntimeBlockedForCurrent();
    const currentRuntimeHasReceipt = isImportRuntimeOwnedByCurrent() && importRuntime.mode === "receipt" && !!importRuntime.receipt;
    const currentRuntimeUnknown = isImportRuntimeOwnedByCurrent() && importRuntime.mode === "unknown";
    const busy = phase === "importing" || phase === "confirming" || currentRuntimeBusy;
    const canSubmit = !!file && !error && phase === "idle" && !currentRuntimeBlocked;

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            openRef.current = false;
            sessionRef.current += 1;
            submitInFlightRef.current = false;
        };
    }, []);

    useEffect(() => subscribeImportRuntime(() => {
        if (!isRuntimeBusyForCurrent()) {
            submitInFlightRef.current = false;
            setPhase("idle");
        }
        forceRuntime(value => value + 1);
    }), []);

    useEffect(() => subscribeRuntimeEnvironment(() => {
        sessionRef.current += 1;
        submitInFlightRef.current = false;
        setFile(null);
        setError(null);
        setPhase("idle");
        resetFileInput(inputRef.current);
        forceRuntime(value => value + 1);
    }), []);

    useEffect(() => {
        openRef.current = isOpen;
        sessionRef.current += 1;
        if (!isOpen) {
            submitInFlightRef.current = false;
            setPhase("idle");
            return;
        }
        setFile(null);
        resetFileInput(inputRef.current);
        setPhase(currentRuntimeBusy ? "confirming" : "idle");
        setError(currentRuntimeHasReceipt ? t("readbackFailed") : currentRuntimeUnknown ? importRuntime.message ?? t("unknownSubmit") : null);
        const previous = document.activeElement as HTMLElement | null;
        setTimeout(() => dialogRef.current?.querySelector<HTMLElement>("input, button")?.focus(), 0);
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape" && !isRuntimeBusyForCurrent()) handleClose();
        };
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("keydown", onKey);
            previous?.focus?.();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen]);

    if (!isOpen) return null;

    const activeSession = (session: number) => mountedRef.current && openRef.current && sessionRef.current === session;

    const failReadback = (message: string) => {
        setError(message);
        toast.error(message);
    };

    const confirmReceipt = async (receipt: ProjectPackageReceipt, session: number, existingOperation?: number) => {
        const environment = requireActiveRuntimeEnvironment();
        const operation = existingOperation ?? beginImportRuntime(environment.key, environment.generation, { preserveReceipt: true });
        if (activeSession(session)) setPhase("confirming");
        try {
            const latest = await api.getProject(receipt.id);
            if (!isCurrentRuntimeEnvironment(environment.key, environment.generation) || !activeSession(session)) return;
            const latestReceipt = parseProjectPackageReceipt(latest);
            if (latestReceipt.id !== receipt.id || latestReceipt.revision < receipt.revision) {
                throw new Error("Imported project readback did not match the import acknowledgement");
            }
            const result = useProjectStore.getState().applyProjectReadSnapshot(receipt.id, latest, true);
            if (!isReadConfirmed(result)) throw new Error("Imported project readback was not confirmed");
            setImportReceipt(null);
            setPhase("confirmed");
            toast.success(t("imported"));
            onClose();
            window.location.hash = `#/project/${receipt.id}`;
        } catch (readError) {
            if (!isCurrentRuntimeEnvironment(environment.key, environment.generation) || !activeSession(session)) return;
            console.error("Failed to confirm imported project:", readError);
            failReadback(t("readbackFailed"));
        } finally {
            finishImportRuntime(operation);
            if (activeSession(session)) setPhase("idle");
        }
    };

    const handleClose = () => {
        if (isRuntimeBusyForCurrent()) return;
        openRef.current = false;
        sessionRef.current += 1;
        submitInFlightRef.current = false;
        setFile(null);
        setError(null);
        setPhase("idle");
        resetFileInput(inputRef.current);
        onClose();
    };

    const handleFile = (selected: File | null) => {
        setFile(null);
        if (currentRuntimeHasReceipt) {
            setError(t("readbackFailed"));
            return;
        }
        if (currentRuntimeUnknown) {
            setError(importRuntime.message ?? t("unknownSubmit"));
            return;
        }
        setError(null);
        if (!selected) return;
        const lower = selected.name.toLowerCase();
        if (!lower.endsWith(".zip") && !lower.endsWith(".lumenx.zip")) {
            setError(t("invalidType"));
            return;
        }
        if (selected.size > PROJECT_PACKAGE_MAX_BYTES) {
            setError(t("tooLarge"));
            return;
        }
        setFile(selected);
    };

    const handleSubmit = async () => {
        if (!canSubmit || !file || submitInFlightRef.current) return;
        const environment = requireActiveRuntimeEnvironment();
        const session = sessionRef.current;
        submitInFlightRef.current = true;
        setError(null);
        setPhase("importing");
        const operation = beginImportRuntime(environment.key, environment.generation);
        try {
            const ack = await api.importProjectPackage(file);
            if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
            let receipt: ProjectPackageReceipt;
            try {
                receipt = parseProjectPackageReceipt(ack);
            } catch (parseError) {
                console.error("Failed to parse project package acknowledgement:", parseError);
                const message = t("unknownSubmit");
                setImportUnknown(message, environment.key, environment.generation);
                if (activeSession(session)) {
                    setError(message);
                    toast.error(message);
                }
                return;
            }
            setImportReceipt(receipt, environment.key, environment.generation);
            if (!activeSession(session)) return;
            await confirmReceipt(receipt, session, operation);
        } catch (importError) {
            if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
            console.error("Failed to import project package:", importError);
            const outcomeUnknown = importError instanceof ProjectPackageImportError && importError.outcomeUnknown;
            const message = outcomeUnknown
                ? t("unknownSubmit")
                : importError instanceof Error ? importError.message : t("importFailed");
            if (outcomeUnknown) {
                setImportUnknown(message, environment.key, environment.generation);
            }
            if (activeSession(session)) {
                setError(message);
                toast.error(message);
            }
        } finally {
            finishImportRuntime(operation);
            if (activeSession(session)) setPhase("idle");
        }
    };

    const retryReadback = async () => {
        const receipt = importRuntime.receipt;
        const runtime = getRuntimeEnvironment();
        if (!receipt || isRuntimeBusyForCurrent() || !runtime || importRuntime.key !== runtime.key
            || importRuntime.generation !== runtime.generation) return;
        await confirmReceipt(receipt, sessionRef.current);
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" role="presentation">
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className="glass-panel w-full max-w-[520px] rounded-2xl border border-glass-border p-5 shadow-2xl"
            >
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <p className="font-mono text-[0.625rem] uppercase tracking-[0.2em] text-text-muted">LumenX Package</p>
                        <h2 id={titleId} className="mt-1 font-display text-[1.375rem] font-semibold text-foreground">
                            {t("importTitle")}
                        </h2>
                    </div>
                    <button
                        type="button"
                        onClick={handleClose}
                        disabled={busy}
                        className="grid h-8 w-8 place-items-center rounded-lg text-text-muted hover:bg-hover-bg hover:text-foreground disabled:opacity-50"
                        aria-label={tc("close")}
                    >
                        <X size={16} />
                    </button>
                </div>

                <p className="mt-3 text-sm leading-6 text-text-secondary">{t("importBody")}</p>

                <label className="mt-5 flex cursor-pointer flex-col items-center gap-3 rounded-xl border border-dashed border-glass-border bg-surface-inset px-4 py-7 text-center transition-colors hover:border-primary/60">
                    <FileArchive size={28} className="text-primary" />
                    <span className="text-sm font-semibold text-foreground">
                        {file ? file.name : t("chooseFile")}
                    </span>
                    <span className="text-xs text-text-muted">{t("fileHint")}</span>
                    <input
                        ref={inputRef}
                        aria-label={t("chooseFile")}
                        type="file"
                        accept=".zip,.lumenx.zip,application/zip,application/octet-stream"
                        className="sr-only"
                        disabled={busy}
                        onChange={(event) => handleFile(event.target.files?.[0] ?? null)}
                    />
                </label>

                {error ? (
                    <div role="alert" className="mt-4 rounded-xl border border-status-error-border bg-status-error-bg px-3 py-2 text-sm text-status-error-fg">
                        <div className="flex items-start gap-2">
                            <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
                            <div className="flex-1">
                                <p>{error}</p>
                                {currentRuntimeHasReceipt ? (
                                    <button
                                        type="button"
                                        onClick={() => void retryReadback()}
                                        disabled={busy}
                                        className="mt-2 rounded-md border border-status-error-border px-2 py-1 text-xs font-semibold hover:bg-black/10 disabled:opacity-50"
                                    >
                                        {t("retryReadback")}
                                    </button>
                                ) : null}
                            </div>
                        </div>
                    </div>
                ) : null}

                {phase === "confirmed" ? (
                    <div className="mt-4 flex items-center gap-2 rounded-xl border border-status-completed-border bg-status-completed-bg px-3 py-2 text-sm text-status-completed-fg">
                        <CheckCircle2 size={16} />
                        {t("imported")}
                    </div>
                ) : null}

                <div className="mt-5 flex justify-end gap-2">
                    <button
                        type="button"
                        onClick={handleClose}
                        disabled={busy}
                        className="glass-button px-4 py-2 text-sm font-semibold disabled:opacity-50"
                    >
                        {tc("cancel")}
                    </button>
                    <button
                        type="button"
                        onClick={() => void handleSubmit()}
                        disabled={!canSubmit}
                        className="inline-flex items-center gap-2 rounded-[10px] bg-primary px-4 py-2 text-sm font-semibold text-on-accent shadow-[var(--glow-primary)] transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {busy ? <Loader2 size={15} className="animate-spin" /> : <FileArchive size={15} />}
                        {phase === "importing" ? t("importing") : phase === "confirming" ? t("confirming") : t("importAction")}
                    </button>
                </div>
            </div>
        </div>
    );
}
