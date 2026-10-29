"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import type { AtelierCandidateJudgment, AtelierCandidateJudgmentVerdict } from "@/lib/atelierTypes";

type Props = {
    candidateId: string;
    mediaKind: "image" | "video" | "audio";
    history: AtelierCandidateJudgment[];
    open: boolean;
    readOnly?: boolean;
    saving?: boolean;
    onClose: () => void;
    onSave: (input: { verdict: AtelierCandidateJudgmentVerdict; reason: string }) => Promise<void>;
};

const buttonClass = "rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs text-white/75 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-35";

export function latestCandidateJudgment(history: AtelierCandidateJudgment[]): AtelierCandidateJudgment | null {
    return history.length ? history[history.length - 1] : null;
}

export function CandidateJudgmentDialog({ candidateId, mediaKind, history, open, readOnly = false, saving = false, onClose, onSave }: Props) {
    const t = useTranslations("atelier.candidateJudgment");
    const latest = latestCandidateJudgment(history);
    const [verdict, setVerdict] = useState<AtelierCandidateJudgmentVerdict>(latest?.verdict ?? "unreviewed");
    const [reason, setReason] = useState(latest?.reason ?? "");
    const [error, setError] = useState<string | null>(null);
    const reasonRef = useRef<HTMLTextAreaElement>(null);
    const dialogRef = useRef<HTMLElement>(null);
    const openerRef = useRef<HTMLElement | null>(null);
    const latestRef = useRef<{ open: boolean; candidateId: string }>({ open: false, candidateId: "" });
    const latestSnapshotRef = useRef(latest);

    useEffect(() => {
        latestSnapshotRef.current = latest;
    }, [latest]);

    useEffect(() => {
        if (!open) return;
        if (!latestRef.current.open) openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const shouldReset = !latestRef.current.open || latestRef.current.candidateId !== candidateId;
        latestRef.current = { open, candidateId };
        if (!shouldReset) return;
        const initial = latestSnapshotRef.current;
        setVerdict(initial?.verdict ?? "unreviewed");
        setReason(initial?.reason ?? "");
        setError(null);
        window.setTimeout(() => {
            const target = readOnly ? dialogRef.current?.querySelector<HTMLElement>("button") : reasonRef.current;
            target?.focus();
        }, 0);
        return () => {
            openerRef.current?.focus();
            openerRef.current = null;
            latestRef.current = { open: false, candidateId: "" };
        };
    }, [candidateId, open, readOnly]);

    useEffect(() => {
        if (!open) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                onClose();
                return;
            }
            if (event.key !== "Tab") return;
            const focusable = dialogRef.current
                ? Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), textarea:not([disabled]), input:not([disabled])"))
                    .filter(item => item.offsetParent !== null || item === document.activeElement)
                : [];
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        };
        window.addEventListener("keydown", onKeyDown, true);
        return () => window.removeEventListener("keydown", onKeyDown, true);
    }, [onClose, open]);

    const reasonLength = Array.from(reason).length;
    const canSave = !readOnly && !saving && reasonLength <= 2000;
    const orderedHistory = useMemo(() => [...history].reverse(), [history]);

    if (!open || typeof document === "undefined") return null;

    const body = <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" role="presentation" onMouseDown={event => {
        if (event.target === event.currentTarget) onClose();
    }}>
        <section ref={dialogRef} role="dialog" aria-modal="true" aria-label={t("title")} tabIndex={-1} className="max-h-[86vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-white/[0.12] bg-[#090910] p-4 text-white shadow-[0_24px_80px_rgba(0,0,0,0.55)]">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">{t(mediaKind)}</div>
                    <h3 className="mt-1 font-display text-sm font-semibold text-white/85">{t("title")}</h3>
                    <p className="mt-1 max-w-md break-all font-mono text-[11px] text-white/40">{candidateId}</p>
                </div>
                <button type="button" className={buttonClass} onClick={onClose}>{t("close")}</button>
            </div>
            {readOnly ? <p className="mt-3 rounded-xl border border-amber-300/20 bg-amber-500/10 p-3 text-xs text-amber-100">{t("readOnly")}</p> : null}
            <div className="mt-4 grid gap-2 sm:grid-cols-3">
                {(["unreviewed", "keep", "reject"] as const).map(item => <label key={item} className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-xs text-white/70">
                    <input type="radio" name="candidate-judgment-verdict" value={item} checked={verdict === item} disabled={readOnly || saving}
                        onChange={() => setVerdict(item)} />
                    {t(item)}
                </label>)}
            </div>
            <label className="mt-4 block text-xs text-white/60">
                {t("reason")}
                <textarea ref={reasonRef} value={reason} disabled={readOnly || saving} rows={5}
                    onChange={event => setReason(event.target.value)}
                    className="mt-2 w-full resize-y rounded-xl border border-white/[0.1] bg-black/35 px-3 py-2 text-sm text-white/80 outline-none focus:border-white/25" />
            </label>
            <div className={`mt-1 text-right text-[11px] ${reasonLength > 2000 ? "text-rose-200" : "text-white/35"}`} aria-live="polite">
                {reasonLength}/2000
            </div>
            {orderedHistory.length ? <div className="mt-4 max-h-56 space-y-2 overflow-y-auto pr-1">
                <h4 className="text-xs font-medium text-white/60">{t("history")}</h4>
                {orderedHistory.map(item => <div key={item.id} className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-2 text-xs text-white/55">
                    <div className="flex items-center justify-between gap-2">
                        <span>{t(item.verdict)}</span>
                        <span className="font-mono text-[10px] text-white/35">{item.id}</span>
                    </div>
                    {item.reason ? <p className="mt-1 whitespace-pre-wrap text-white/65">{item.reason}</p> : null}
                </div>)}
            </div> : null}
            {error ? <p role="alert" className="mt-3 rounded-xl border border-rose-300/20 bg-rose-500/10 p-3 text-xs text-rose-100">{error}</p> : null}
            <div className="mt-4 flex justify-end gap-2">
                <button type="button" className={buttonClass} onClick={onClose}>{t("cancel")}</button>
                {!readOnly ? <button type="button" className={buttonClass} disabled={!canSave} onClick={() => {
                    setError(null);
                    void onSave({ verdict, reason }).catch(cause => setError(cause instanceof Error ? cause.message : t("saveFailed")));
                }}>{t(saving ? "saving" : "save")}</button> : null}
            </div>
        </section>
    </div>;

    return createPortal(body, document.body);
}
