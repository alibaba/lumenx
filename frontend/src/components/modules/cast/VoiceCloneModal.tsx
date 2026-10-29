"use client";
/**
 * VoiceCloneModal — PR-3h #4 (revised)
 *
 * Sub-modal for cloning a voice from an audio sample.
 * Captures one audio sample + label, uploads, then calls /voice/clone.
 *
 * Improvements:
 *   - Shows character context (name + description) as reference
 *   - Allow closing during clone with confirmation
 */
import { useRef, useState } from "react";
import { X, Upload, Loader2, Check, Users } from "lucide-react";
import { useTranslations } from "next-intl";
import { API_URL, api, type CustomVoice } from "@/lib/api";
import { appAccessFetch } from "@/lib/appAccess";

const ALLOWED_TYPES = ["audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave", "audio/x-m4a", "audio/mp4"];
const ALLOWED_EXTS = [".mp3", ".wav", ".m4a"];
const MAX_BYTES = 10 * 1024 * 1024;

interface VoiceCloneModalProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId: string;
    characterName?: string;
    characterDescription?: string;
    onCreated: (voice: CustomVoice) => void;
}

type Phase = "pick" | "uploading" | "cloning" | "done" | "error";

export default function VoiceCloneModal({ isOpen, onClose, seriesId, characterName, characterDescription, onCreated }: VoiceCloneModalProps) {
    const t = useTranslations("voiceClone");
    const [file, setFile] = useState<File | null>(null);
    const [label, setLabel] = useState("");
    const [phase, setPhase] = useState<Phase>("pick");
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    const [dragHot, setDragHot] = useState(false);
    const [confirmClose, setConfirmClose] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    if (!isOpen) return null;

    const reset = () => {
        setFile(null);
        setLabel("");
        setPhase("pick");
        setErrorMsg(null);
        setConfirmClose(false);
    };

    const inFlight = phase === "uploading" || phase === "cloning";

    const handleClose = () => {
        if (inFlight) {
            setConfirmClose(true);
            return;
        }
        reset();
        onClose();
    };

    const handleForceClose = () => {
        reset();
        onClose();
    };

    const validate = (f: File): string | null => {
        const lower = f.name.toLowerCase();
        const extOk = ALLOWED_EXTS.some((ext) => lower.endsWith(ext));
        const typeOk = ALLOWED_TYPES.includes(f.type);
        if (!extOk && !typeOk) return t("errorBadType");
        if (f.size > MAX_BYTES) return t("errorTooLarge");
        return null;
    };

    const handleFile = (f: File) => {
        setErrorMsg(null);
        const err = validate(f);
        if (err) {
            setErrorMsg(err);
            return;
        }
        setFile(f);
        if (!label.trim()) {
            const base = f.name.replace(/\.[^.]+$/, "");
            setLabel(base.slice(0, 30));
        }
    };

    const handleSubmit = async () => {
        if (!file || !label.trim()) return;
        setErrorMsg(null);
        setPhase("uploading");
        try {
            const formData = new FormData();
            formData.append("file", file);
            const uploadResp = await appAccessFetch(`${API_URL}/upload`, {
                method: "POST",
                body: formData,
            });
            if (!uploadResp.ok) {
                throw new Error(`Upload failed: ${uploadResp.status}`);
            }
            const { url } = await uploadResp.json();
            if (!url) throw new Error("Upload response missing url");

            setPhase("cloning");
            const voice = await api.cloneVoice({
                series_id: seriesId,
                audio_url: url,
                label: label.trim(),
            });

            setPhase("done");
            setTimeout(() => {
                onCreated(voice);
                reset();
                onClose();
            }, 600);
        } catch (e: any) {
            setErrorMsg(e?.message || "Clone failed");
            setPhase("error");
        }
    };

    return (
        <div className="fixed inset-0 z-[110] grid place-items-center bg-overlay backdrop-blur-sm" onClick={handleClose}>
            <div
                className="w-full max-w-lg rounded-2xl border border-glass-border bg-elevated shadow-[0_24px_64px_-12px_rgba(0,0,0,0.7)]"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Header */}
                <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-glass-border">
                    <h2 className="text-display font-medium text-foreground">{t("title")}</h2>
                    <button
                        onClick={handleClose}
                        aria-label={t("close")}
                        className="p-1.5 rounded-lg hover:bg-hover-bg text-text-muted hover:text-foreground transition-colors"
                    >
                        <X size={15} />
                    </button>
                </div>

                {/* Body */}
                <div className="px-5 py-4 space-y-4">
                    {/* Character context panel */}
                    {(characterName || characterDescription) && (
                        <div className="rounded-lg border border-glass-border bg-black/20 px-4 py-3">
                            <div className="flex items-center gap-2 mb-1.5">
                                <Users size={12} className="text-text-muted" />
                                <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-text-muted">
                                    {t("characterContext")}
                                </span>
                            </div>
                            {characterName && (
                                <p className="text-[0.8125rem] font-medium text-foreground">{characterName}</p>
                            )}
                            {characterDescription && (
                                <p className="mt-1 text-[0.75rem] text-text-secondary leading-relaxed line-clamp-3">
                                    {characterDescription}
                                </p>
                            )}
                        </div>
                    )}

                    {/* Drop zone */}
                    <div
                        onDragOver={(e) => { e.preventDefault(); if (!inFlight) setDragHot(true); }}
                        onDragLeave={() => setDragHot(false)}
                        onDrop={(e) => {
                            e.preventDefault();
                            setDragHot(false);
                            if (inFlight) return;
                            const f = e.dataTransfer.files?.[0];
                            if (f) handleFile(f);
                        }}
                        onClick={() => { if (!inFlight) fileInputRef.current?.click(); }}
                        className={`relative rounded-lg border-2 border-dashed p-6 text-center cursor-pointer transition-colors ${
                            dragHot
                                ? "border-primary bg-primary/10"
                                : file
                                    ? "border-primary/50 bg-primary/5"
                                    : "border-glass-border hover:border-foreground/30 bg-black/30"
                        } ${inFlight ? "opacity-60 cursor-not-allowed" : ""}`}
                    >
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept=".mp3,.wav,.m4a,audio/*"
                            onChange={(e) => {
                                const f = e.target.files?.[0];
                                if (f) handleFile(f);
                            }}
                            className="hidden"
                        />
                        {file ? (
                            <div className="flex items-center justify-center gap-2 text-foreground">
                                <Check size={14} className="text-primary" />
                                <span className="text-[0.8125rem] truncate max-w-[280px]" title={file.name}>{file.name}</span>
                                <span className="font-mono text-[0.625rem] text-text-muted">
                                    {(file.size / 1024 / 1024).toFixed(1)}MB
                                </span>
                            </div>
                        ) : (
                            <div className="flex flex-col items-center gap-2">
                                <Upload size={20} className="text-text-muted" />
                                <p className="text-[0.8125rem] text-text-secondary">{t("dropHint")}</p>
                                <p className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-text-muted">{t("requirements")}</p>
                            </div>
                        )}
                    </div>

                    {/* Label input */}
                    <div>
                        <label className="block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-text-muted mb-1">
                            {t("labelLabel")}
                        </label>
                        <input
                            type="text"
                            value={label}
                            onChange={(e) => setLabel(e.target.value.slice(0, 30))}
                            placeholder={t("labelPlaceholder")}
                            disabled={inFlight}
                            maxLength={30}
                            className="w-full rounded-md border border-glass-border bg-black/30 px-3 py-2 text-[0.8125rem] text-foreground placeholder:text-text-muted focus:outline-none focus:border-primary/40 disabled:opacity-60"
                        />
                    </div>

                    {/* Error */}
                    {errorMsg && (
                        <div className="rounded-md border border-status-failed-border bg-status-failed-bg px-3 py-2 text-body-sm text-status-failed-fg" role="alert">
                            {errorMsg}
                        </div>
                    )}

                    {/* Status banner */}
                    {inFlight && (
                        <div className="flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-body-sm text-foreground">
                            <Loader2 size={13} className="animate-spin text-primary" />
                            <span>{phase === "uploading" ? t("uploading") : t("cloning")}</span>
                        </div>
                    )}
                    {phase === "done" && (
                        <div className="flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-body-sm text-primary">
                            <Check size={13} />
                            <span>{t("doneCloned")}</span>
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-glass-border">
                    <button
                        onClick={handleClose}
                        className="inline-flex items-center px-3 py-1.5 rounded-md bg-glass border border-glass-border text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors text-[0.75rem]"
                    >
                        {t("cancel")}
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={!file || !label.trim() || inFlight || phase === "done"}
                        className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md bg-primary text-white border border-[rgba(100,108,255,0.65)] shadow-[inset_0_1.5px_0_rgba(255,255,255,0.14)] hover:bg-primary-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-[0.75rem] font-semibold"
                    >
                        {phase === "done" ? <Check size={12} /> : null}
                        {phase === "done" ? t("done") : t("submit")}
                    </button>
                </div>
            </div>

            {/* Confirm close dialog during clone */}
            {confirmClose && (
                <div
                    className="fixed inset-0 z-[120] grid place-items-center bg-overlay/60"
                    onClick={(e) => { e.stopPropagation(); setConfirmClose(false); }}
                >
                    <div
                        className="w-full max-w-xs rounded-xl border border-glass-border bg-elevated p-5 shadow-[0_16px_48px_-8px_rgba(0,0,0,0.7)]"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <p className="text-[0.8125rem] text-foreground font-medium mb-1">{t("confirmCloseTitle")}</p>
                        <p className="text-[0.75rem] text-text-secondary mb-4">{t("confirmCloseBody")}</p>
                        <div className="flex items-center gap-2 justify-end">
                            <button
                                onClick={() => setConfirmClose(false)}
                                className="px-3 py-1.5 rounded-md bg-glass border border-glass-border text-text-secondary hover:text-foreground text-[0.75rem] transition-colors"
                            >
                                {t("confirmCloseStay")}
                            </button>
                            <button
                                onClick={handleForceClose}
                                className="px-3 py-1.5 rounded-md bg-status-failed-bg border border-status-failed-border text-status-failed-fg hover:bg-status-failed-bg/80 text-[0.75rem] font-medium transition-colors"
                            >
                                {t("confirmCloseLeave")}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
