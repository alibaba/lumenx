"use client";

import { useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Upload, FileText, Loader2, ChevronLeft, ChevronRight, Check, BookOpen } from "lucide-react";
import { api } from "@/lib/api";
import { messages } from "@/lib/i18n";

interface ImportFileDialogProps {
    isOpen: boolean;
    onClose: () => void;
    onSuccess?: (result: any) => void;
}

interface EpisodePreview {
    episode_number: number;
    title: string;
    summary: string;
    estimated_duration?: string;
}

interface PreviewResult {
    episodes: EpisodePreview[];
    import_id?: string;
    text?: string;
}

type Step = 1 | 2 | 3;

export default function ImportFileDialog({ isOpen, onClose, onSuccess }: ImportFileDialogProps) {
    const copy = messages.seriesPage.importFileDialog;
    const commonActions = messages.common.actions;
    // Step state
    const [step, setStep] = useState<Step>(1);

    // Step 1 state
    const [file, setFile] = useState<File | null>(null);
    const [seriesTitle, setSeriesTitle] = useState("");
    const [description, setDescription] = useState("");
    const [suggestedEpisodes, setSuggestedEpisodes] = useState(3);
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Step 2 state
    const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);
    const [isCreating, setIsCreating] = useState(false);

    // Step 3 state
    const [createdResult, setCreatedResult] = useState<{ series_id: string; episode_count: number } | null>(null);

    // Error state
    const [error, setError] = useState<string | null>(null);

    // Drag and drop state
    const [isDragOver, setIsDragOver] = useState(false);

    const resetState = () => {
        setStep(1);
        setFile(null);
        setSeriesTitle("");
        setDescription("");
        setSuggestedEpisodes(3);
        setIsAnalyzing(false);
        setPreviewResult(null);
        setIsCreating(false);
        setCreatedResult(null);
        setError(null);
        setIsDragOver(false);
    };

    const handleClose = () => {
        resetState();
        onClose();
    };

    const handleFileSelect = useCallback((selectedFile: File) => {
        const ext = selectedFile.name.split('.').pop()?.toLowerCase();
        if (ext !== 'txt' && ext !== 'md') {
            setError(copy.invalidFileType);
            return;
        }
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
        setIsDragOver(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
    }, []);

    // Step 1 -> Step 2: Analyze file
    const handleAnalyze = async () => {
        if (!file || !seriesTitle.trim()) return;
        setIsAnalyzing(true);
        setError(null);
        try {
            const result = await api.importFilePreview(file, suggestedEpisodes);
            setPreviewResult(result);
            setStep(2);
        } catch (err: any) {
            const msg = err?.response?.data?.detail || err?.message || copy.analyzeFailed;
            setError(msg);
        } finally {
            setIsAnalyzing(false);
        }
    };

    // Step 2 -> Step 3: Confirm creation
    const handleConfirm = async () => {
        if (!previewResult) return;
        setIsCreating(true);
        setError(null);
        try {
            const result = await api.importFileConfirm({
                title: seriesTitle.trim(),
                description: description.trim() || undefined,
                import_id: previewResult.import_id,
                text: previewResult.text,
                episodes: previewResult.episodes,
            });
            setCreatedResult({
                series_id: result.series_id,
                episode_count: result.episodes?.length ?? previewResult.episodes.length,
            });
            setStep(3);
        } catch (err: any) {
            const msg = err?.response?.data?.detail || err?.message || copy.createFailed;
            setError(msg);
        } finally {
            setIsCreating(false);
        }
    };

    // Step 3: View series
    const handleViewSeries = () => {
        if (createdResult) {
            onSuccess?.(createdResult);
            window.location.hash = `#/series/${createdResult.series_id}`;
        }
        handleClose();
    };

    const stepLabels = [copy.steps.uploadFile, copy.steps.previewEpisodes, copy.steps.complete];

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-6"
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
                            <h2 className="text-2xl font-display font-bold text-white">{copy.title}</h2>
                            <button
                                onClick={handleClose}
                                className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
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
                                            <div className={`w-8 h-px ${isCompleted || isActive ? 'bg-primary' : 'bg-gray-600'}`} />
                                        )}
                                        <div className="flex items-center gap-2">
                                            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
                                                isCompleted ? 'bg-primary text-white' :
                                                isActive ? 'bg-primary/20 text-primary border border-primary' :
                                                'bg-gray-700 text-gray-400'
                                            }`}>
                                                {isCompleted ? <Check size={14} /> : stepNum}
                                            </div>
                                            <span className={`text-sm ${isActive ? 'text-white font-medium' : 'text-gray-500'}`}>
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
                                        onClick={() => fileInputRef.current?.click()}
                                        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
                                            isDragOver
                                                ? 'border-primary bg-primary/10'
                                                : file
                                                    ? 'border-green-500/50 bg-green-500/5'
                                                    : 'border-gray-600 hover:border-gray-400 bg-white/5'
                                        }`}
                                    >
                                        <input
                                            ref={fileInputRef}
                                            type="file"
                                            accept=".txt,.md"
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
                                                    <p className="text-white font-medium">{file.name}</p>
                                                    <p className="text-gray-400 text-sm">{(file.size / 1024).toFixed(1)} KB</p>
                                                </div>
                                            </div>
                                        ) : (
                                            <>
                                                <Upload size={32} className="mx-auto mb-3 text-gray-400" />
                                                <p className="text-gray-300 mb-1">{copy.dropOrClick}</p>
                                                <p className="text-gray-500 text-sm">{copy.supportedFormats}</p>
                                            </>
                                        )}
                                    </div>

                                    {/* Series Title */}
                                    <div>
                                        <label className="block text-sm font-medium text-gray-300 mb-2">
                                            {copy.titleLabel} <span className="text-red-400">*</span>
                                        </label>
                                        <input
                                            type="text"
                                            value={seriesTitle}
                                            onChange={(e) => setSeriesTitle(e.target.value)}
                                            placeholder={copy.titlePlaceholder}
                                            className="glass-input w-full"
                                        />
                                    </div>

                                    {/* Description */}
                                    <div>
                                        <label className="block text-sm font-medium text-gray-300 mb-2">
                                            {copy.descriptionLabel}
                                        </label>
                                        <textarea
                                            value={description}
                                            onChange={(e) => setDescription(e.target.value)}
                                            placeholder={copy.descriptionPlaceholder}
                                            rows={3}
                                            className="glass-input w-full resize-none"
                                        />
                                    </div>

                                    {/* Suggested Episodes */}
                                    <div>
                                        <label className="block text-sm font-medium text-gray-300 mb-2">
                                            {copy.suggestedEpisodes}
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
                                            {commonActions.cancel}
                                        </button>
                                        <button
                                            onClick={handleAnalyze}
                                            disabled={!file || !seriesTitle.trim() || isAnalyzing}
                                            className="flex-1 bg-primary hover:bg-primary/90 text-white px-6 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                                        >
                                            {isAnalyzing ? (
                                                <>
                                                    <Loader2 size={18} className="animate-spin" />
                                                    {copy.analyzing}
                                                </>
                                            ) : (
                                                copy.analyze
                                            )}
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* Step 2: Preview Episodes */}
                            {step === 2 && previewResult && (
                                <div className="space-y-4">
                                    <p className="text-gray-400 text-sm">
                                        {copy.previewIntro(previewResult.episodes.length)}
                                    </p>

                                    {/* Episodes List */}
                                    <div className="space-y-3 max-h-[40vh] overflow-y-auto pr-1">
                                        {previewResult.episodes.map((ep) => (
                                            <div
                                                key={ep.episode_number}
                                                className="bg-white/5 rounded-xl p-4 border border-white/10"
                                            >
                                                <div className="flex items-center gap-3 mb-2">
                                                    <span className="bg-primary/20 text-primary text-xs font-bold px-2 py-1 rounded-full">
                                                        EP{ep.episode_number}
                                                    </span>
                                                    <h4 className="text-white font-medium flex-1 truncate">
                                                        {ep.title}
                                                    </h4>
                                                    {ep.estimated_duration && (
                                                        <span className="text-gray-500 text-xs">
                                                            ~{ep.estimated_duration}
                                                        </span>
                                                    )}
                                                </div>
                                                <p className="text-gray-400 text-sm line-clamp-2">
                                                    {ep.summary}
                                                </p>
                                            </div>
                                        ))}
                                    </div>

                                    {/* Action Buttons */}
                                    <div className="flex gap-3 pt-4">
                                        <button
                                            onClick={() => { setStep(1); setError(null); }}
                                            className="flex-1 glass-button flex items-center justify-center gap-2"
                                        >
                                            <ChevronLeft size={16} />
                                            {copy.backToEdit}
                                        </button>
                                        <button
                                            onClick={handleConfirm}
                                            disabled={isCreating}
                                            className="flex-1 bg-primary hover:bg-primary/90 text-white px-6 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                                        >
                                            {isCreating ? (
                                                <>
                                                    <Loader2 size={18} className="animate-spin" />
                                                    {copy.creating}
                                                </>
                                            ) : (
                                                <>
                                                    {copy.confirmCreate}
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
                                        <h3 className="text-xl font-bold text-white mb-2">{copy.successTitle}</h3>
                                        <p className="text-gray-400">
                                            {copy.successSummary(seriesTitle, createdResult.episode_count)}
                                        </p>
                                    </div>
                                    <button
                                        onClick={handleViewSeries}
                                        className="bg-primary hover:bg-primary/90 text-white px-8 py-3 rounded-lg font-medium transition-colors flex items-center gap-2"
                                    >
                                        <BookOpen size={18} />
                                        {copy.viewSeries}
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
