"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Upload, FileText, Loader2, ChevronLeft, ChevronRight, Check, BookOpen } from "lucide-react";
import { api, type SeriesImportPreview, type SeriesImportResult } from "@/lib/api";
import { useTranslations } from "next-intl";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";

interface ImportFileDialogProps {
    isOpen: boolean;
    onClose: () => void;
    onSuccess?: (result: any) => void;
}

type Step = 1 | 2 | 3;

type ImportError = { response?: { status?: number; data?: { detail?: unknown } }; message?: unknown };

function importErrorMessage(error: ImportError, fallback: string): string {
    const detail = error?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map(item => typeof item === 'object' && item !== null && 'msg' in item && typeof item.msg === 'string'
            ? item.msg : JSON.stringify(item)).join('; ');
    }
    return typeof error?.message === 'string' ? error.message : fallback;
}

export default function ImportFileDialog({ isOpen, onClose, onSuccess }: ImportFileDialogProps) {
    // Step state
    const [step, setStep] = useState<Step>(1);

    // Step 1 state
    const [file, setFile] = useState<File | null>(null);
    const [seriesTitle, setSeriesTitle] = useState("");
    const [description, setDescription] = useState("");
    const [suggestedEpisodes, setSuggestedEpisodes] = useState(3);
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const selectedFileRef = useRef<File | null>(null);
    const session = useRef(0);
    const mounted = useRef(false);
    const open = useRef(isOpen);
    const analyzing = useRef(false);
    const previewStillCurrent = useRef<(() => boolean) | null>(null);

    // Step 2 state
    const [previewResult, setPreviewResult] = useState<SeriesImportPreview | null>(null);
    const [isCreating, setIsCreating] = useState(false);
    const confirming = useRef(false);
    const needsConfirmationRead = useRef(false);

    // Step 3 state
    const [createdResult, setCreatedResult] = useState<{ series_id: string; episode_count: number } | null>(null);

    // Error state
    const [error, setError] = useState<string | null>(null);

    // Drag and drop state
    const [isDragOver, setIsDragOver] = useState(false);

    const t = useTranslations("series");
    const tc = useTranslations("common");

    const resetState = useCallback(() => {
        session.current += 1;
        selectedFileRef.current = null;
        previewStillCurrent.current = null;
        analyzing.current = false;
        confirming.current = false;
        setStep(1);
        setFile(null);
        setSeriesTitle("");
        setDescription("");
        setSuggestedEpisodes(3);
        setIsAnalyzing(false);
        setPreviewResult(null);
        setIsCreating(false);
        needsConfirmationRead.current = false;
        setCreatedResult(null);
        setError(null);
        setIsDragOver(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
    }, []);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            open.current = false;
            session.current += 1;
        };
    }, []);

    useEffect(() => {
        open.current = isOpen;
        resetState();
    }, [isOpen, resetState]);

    useEffect(() => subscribeRuntimeEnvironment(resetState), [resetState]);

    const captureOwner = () => {
        const environment = getRuntimeEnvironment();
        const ownerSession = session.current;
        return () => mounted.current && open.current && session.current === ownerSession && !!environment
            && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    };

    const handleClose = () => {
        open.current = false;
        resetState();
        onClose();
    };

    const handleFileSelect = useCallback((selectedFile: File) => {
        if (analyzing.current) return;
        const ext = selectedFile.name.split('.').pop()?.toLowerCase();
        if (ext !== 'txt' && ext !== 'md') {
            setError(t("onlyTxtMd"));
            return;
        }
        selectedFileRef.current = selectedFile;
        setFile(selectedFile);
        setError(null);
        // Auto-fill title from filename if empty
        setSeriesTitle((prev) => {
            if (!prev) {
                return selectedFile.name.replace(/\.(txt|md)$/, '');
            }
            return prev;
        });
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
        const droppedFile = e.dataTransfer.files[0];
        if (droppedFile) {
            handleFileSelect(droppedFile);
        }
    }, [handleFileSelect]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        if (!analyzing.current) setIsDragOver(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
    }, []);

    // Step 1 -> Step 2: Analyze file
    const handleAnalyze = async () => {
        if (!file || !selectedFileRef.current || !seriesTitle.trim() || analyzing.current) return;
        const stillCurrent = captureOwner();
        if (!stillCurrent()) return;
        analyzing.current = true;
        setIsAnalyzing(true);
        setError(null);
        try {
            const result = await api.importFilePreview(file, suggestedEpisodes);
            if (!stillCurrent()) return;
            setPreviewResult(result);
            previewStillCurrent.current = stillCurrent;
            needsConfirmationRead.current = false;
            setStep(2);
        } catch (err) {
            if (stillCurrent()) setError(importErrorMessage(err as ImportError, t("analysisFailed")));
        } finally {
            if (stillCurrent()) {
                analyzing.current = false;
                setIsAnalyzing(false);
            }
        }
    };

    // Step 2 -> Step 3: Confirm creation
    const handleConfirm = async () => {
        if (!previewResult || !previewStillCurrent.current?.() || confirming.current) return;
        const stillCurrent = captureOwner();
        confirming.current = true;
        setIsCreating(true);
        setError(null);
        const finish = (result: SeriesImportResult) => {
            if (!stillCurrent()) return;
            setCreatedResult({ series_id: result.series.id, episode_count: result.episodes.length });
            needsConfirmationRead.current = false;
            setStep(3);
        };
        let confirmationSent = false;
        try {
            if (needsConfirmationRead.current) {
                const receipt = await api.importFileStatus(previewResult.import_id);
                if (!stillCurrent()) return;
                if (receipt.status === 'completed') {
                    finish(receipt.result);
                    return;
                }
            }
            needsConfirmationRead.current = true;
            confirmationSent = true;
            const result = await api.importFileConfirm({
                title: seriesTitle.trim(),
                description: description.trim() || undefined,
                import_id: previewResult.import_id,
                episodes: previewResult.episodes,
            });
            finish(result);
        } catch (err) {
            if (!stillCurrent()) return;
            // A lost response is not evidence that creation failed. Resolve the
            // original identity before allowing another confirmation attempt.
            const status = (err as ImportError)?.response?.status;
            if (confirmationSent && (!status || status >= 500)) {
                try {
                    const receipt = await api.importFileStatus(previewResult.import_id);
                    if (!stillCurrent()) return;
                    if (receipt.status === 'completed') {
                        finish(receipt.result);
                        return;
                    }
                    needsConfirmationRead.current = false;
                } catch { /* Preserve unknown state and the original error. */ }
            } else if (confirmationSent) {
                needsConfirmationRead.current = false;
            }
            if (stillCurrent()) setError(importErrorMessage(err as ImportError, t("createFailed")));
        } finally {
            if (stillCurrent()) {
                confirming.current = false;
                setIsCreating(false);
            }
        }
    };

    // Step 3: View series
    const handleViewSeries = () => {
        if (createdResult && previewStillCurrent.current?.()) {
            onSuccess?.(createdResult);
            window.location.hash = `#/series/${createdResult.series_id}`;
        }
        handleClose();
    };

    const stepLabels = [t("stepUpload"), t("stepPreview"), t("stepDone")];

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="fixed inset-0 bg-overlay backdrop-blur-sm z-50 flex items-center justify-center p-6"
                    onClick={handleClose}
                >
                    <motion.div
                        initial={{ scale: 0.9, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0.9, opacity: 0 }}
                        className="glass-panel p-8 rounded-2xl w-full max-w-3xl max-h-[85vh] flex flex-col"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between mb-6">
                            <h2 className="text-2xl font-display font-bold text-foreground">{t("importTitle")}</h2>
                            <button
                                onClick={handleClose}
                                className="p-2 rounded-lg hover:bg-hover-bg text-text-secondary hover:text-foreground transition-colors"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        {/* Step Indicator */}
                        <div className="flex items-center justify-center gap-2 mb-8">
                            {stepLabels.map((label, idx) => {
                                const stepNum = (idx + 1) as Step;
                                const isActive = step === stepNum;
                                const isCompleted = step > stepNum;
                                return (
                                    <div key={idx} className="flex items-center gap-2">
                                        {idx > 0 && (
                                            <div className={`w-8 h-px ${isCompleted || isActive ? 'bg-primary' : 'bg-glass'}`} />
                                        )}
                                        <div className="flex items-center gap-2">
                                            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
                                                isCompleted ? 'bg-primary text-white' :
                                                isActive ? 'bg-primary/20 text-primary border border-primary' :
                                                'bg-surface text-text-secondary'
                                            }`}>
                                                {isCompleted ? <Check size={14} /> : stepNum}
                                            </div>
                                            <span className={`text-sm ${isActive ? 'text-foreground font-medium' : 'text-text-secondary'}`}>
                                                {label}
                                            </span>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>

                        {/* Error Message */}
                        {error && (
                            <div className="mb-4 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                                {error}
                            </div>
                        )}

                        {/* Content Area */}
                        <div className="flex-1 overflow-y-auto min-h-0">
                            {/* Step 1: Upload File */}
                            {step === 1 && (
                                <div className="space-y-4">
                                    {/* File Upload Zone */}
                                    <div
                                        onDrop={handleDrop}
                                        onDragOver={handleDragOver}
                                        onDragLeave={handleDragLeave}
                                        onClick={() => { if (!analyzing.current) fileInputRef.current?.click(); }}
                                        aria-disabled={isAnalyzing}
                                        className={`border-2 border-dashed rounded-xl p-8 text-center ${isAnalyzing ? 'cursor-wait' : 'cursor-pointer'} transition-colors ${
                                            isDragOver
                                                ? 'border-primary bg-primary/10'
                                                : file
                                                    ? 'border-green-500/50 bg-green-500/5'
                                                    : 'border-glass-border hover:border-glass-border bg-glass'
                                        }`}
                                    >
                                        <input
                                            ref={fileInputRef}
                                            type="file"
                                            accept=".txt,.md"
                                            disabled={isAnalyzing}
                                            className="hidden"
                                            onChange={(e) => {
                                                const f = e.target.files?.[0];
                                                if (f) handleFileSelect(f);
                                            }}
                                        />
                                        {file ? (
                                            <div className="flex items-center justify-center gap-3">
                                                <FileText size={24} className="text-green-400" />
                                                <div className="text-left">
                                                    <p className="text-foreground font-medium">{file.name}</p>
                                                    <p className="text-text-secondary text-sm">{(file.size / 1024).toFixed(1)} KB</p>
                                                </div>
                                            </div>
                                        ) : (
                                            <>
                                                <Upload size={32} className="mx-auto mb-3 text-text-secondary" />
                                                <p className="text-text-secondary mb-1">{t("dragHint")}</p>
                                                <p className="text-text-secondary text-sm">{t("supportedFormats")}</p>
                                            </>
                                        )}
                                    </div>

                                    {/* Series Title */}
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-2">
                                            {t("seriesTitle")} <span className="text-red-400">*</span>
                                        </label>
                                        <input
                                            type="text"
                                            value={seriesTitle}
                                            onChange={(e) => setSeriesTitle(e.target.value)}
                                            placeholder={t("seriesTitlePlaceholder")}
                                            className="glass-input w-full"
                                        />
                                    </div>

                                    {/* Description */}
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-2">
                                            {t("descriptionOptional")}
                                        </label>
                                        <textarea
                                            value={description}
                                            onChange={(e) => setDescription(e.target.value)}
                                            placeholder={t("descriptionPlaceholder")}
                                            rows={3}
                                            className="glass-input w-full resize-none"
                                        />
                                    </div>

                                    {/* Suggested Episodes */}
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-2">
                                            {t("suggestedEpisodes")}
                                        </label>
                                        <input
                                            type="number"
                                            value={suggestedEpisodes}
                                            onChange={(e) => setSuggestedEpisodes(Math.max(1, parseInt(e.target.value) || 1))}
                                            min={1}
                                            max={50}
                                            className="glass-input w-24"
                                        />
                                    </div>

                                    {/* Action Button */}
                                    <div className="flex gap-3 pt-4">
                                        <button onClick={handleClose} className="flex-1 glass-button">
                                            {tc("cancel")}
                                        </button>
                                        <button
                                            onClick={handleAnalyze}
                                            disabled={!file || !seriesTitle.trim() || isAnalyzing}
                                            className="flex-1 bg-primary hover:bg-primary/90 text-white px-6 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                                        >
                                            {isAnalyzing ? (
                                                <>
                                                    <Loader2 size={18} className="animate-spin" />
                                                    {t("analyzing")}
                                                </>
                                            ) : (
                                                t("startAnalysis")
                                            )}
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* Step 2: Preview Episodes */}
                            {step === 2 && previewResult && (
                                <div className="space-y-4">
                                    <p className="text-text-secondary text-sm">
                                        {t("previewHint", { count: previewResult.episodes.length })}
                                    </p>

                                    {/* Episodes List */}
                                    <div className="space-y-3 max-h-[40vh] overflow-y-auto pr-1">
                                        {previewResult.episodes.map((ep) => (
                                            <div
                                                key={ep.episode_number}
                                                className="bg-glass rounded-xl p-4 border border-glass-border"
                                            >
                                                <div className="flex items-center gap-3 mb-2">
                                                    <span className="bg-primary/20 text-primary text-xs font-bold px-2 py-1 rounded-full">
                                                        EP{ep.episode_number}
                                                    </span>
                                                    <h4 className="text-foreground font-medium flex-1 truncate">
                                                        {ep.title}
                                                    </h4>
                                                    {ep.estimated_duration && (
                                                        <span className="text-text-secondary text-xs">
                                                            ~{ep.estimated_duration}
                                                        </span>
                                                    )}
                                                </div>
                                                <p className="text-text-secondary text-sm line-clamp-2">
                                                    {ep.summary}
                                                </p>
                                            </div>
                                        ))}
                                    </div>

                                    {/* Action Buttons */}
                                    <div className="flex gap-3 pt-4">
                                        <button
                                            onClick={() => { setStep(1); setError(null); }}
                                            disabled={isCreating}
                                            className="flex-1 glass-button flex items-center justify-center gap-2"
                                        >
                                            <ChevronLeft size={16} />
                                            {t("backToEdit")}
                                        </button>
                                        <button
                                            onClick={handleConfirm}
                                            disabled={isCreating}
                                            className="flex-1 bg-primary hover:bg-primary/90 text-white px-6 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                                        >
                                            {isCreating ? (
                                                <>
                                                    <Loader2 size={18} className="animate-spin" />
                                                    {t("creating")}
                                                </>
                                            ) : (
                                                <>
                                                    {t("confirmCreate")}
                                                    <ChevronRight size={16} />
                                                </>
                                            )}
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* Step 3: Complete */}
                            {step === 3 && createdResult && (
                                <div className="flex flex-col items-center justify-center py-8 space-y-6">
                                    <div className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center">
                                        <Check size={32} className="text-green-400" />
                                    </div>
                                    <div className="text-center">
                                        <h3 className="text-xl font-bold text-foreground mb-2">{t("createSuccess")}</h3>
                                        <p className="text-text-secondary">
                                            {t("createSuccessDetail", { title: seriesTitle, count: createdResult.episode_count })}
                                        </p>
                                    </div>
                                    <button
                                        onClick={handleViewSeries}
                                        className="bg-primary hover:bg-primary/90 text-white px-8 py-3 rounded-lg font-medium transition-colors flex items-center gap-2"
                                    >
                                        <BookOpen size={18} />
                                        {t("viewSeries")}
                                    </button>
                                </div>
                            )}
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
