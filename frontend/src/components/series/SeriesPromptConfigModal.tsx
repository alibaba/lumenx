"use client";

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, FileText, RotateCcw, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { messages } from '@/lib/i18n';
import { getPromptConfigPresets, type PromptConfigSectionKey } from '@/lib/prompt-config-presets';

interface SeriesPromptConfigModalProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId: string;
    onSaved?: () => void;
}

interface PromptDefaults {
    storyboard_polish: string;
    video_polish: string;
    r2v_polish: string;
}

const SECTIONS = [
    {
        key: 'storyboard_polish' as PromptConfigSectionKey,
        label: messages.promptConfig.sections.storyboardPolish.labelWithCode,
        description: messages.promptConfig.sections.storyboardPolish.description,
    },
    {
        key: 'video_polish' as PromptConfigSectionKey,
        label: messages.promptConfig.sections.videoI2vPolish.labelWithCode,
        description: messages.promptConfig.sections.videoI2vPolish.description,
    },
    {
        key: 'r2v_polish' as PromptConfigSectionKey,
        label: messages.promptConfig.sections.videoR2vPolish.labelWithCode,
        description: messages.promptConfig.sections.videoR2vPolish.description,
    },
];

export default function SeriesPromptConfigModal({ isOpen, onClose, seriesId, onSaved }: SeriesPromptConfigModalProps) {
    const copy = messages.promptConfig;
    const seriesCopy = messages.seriesPage.promptConfigModal;
    const commonActions = messages.common.actions;
    const [config, setConfig] = useState({ storyboard_polish: '', video_polish: '', r2v_polish: '' });
    const [defaults, setDefaults] = useState<PromptDefaults | null>(null);
    const [expandedDefault, setExpandedDefault] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    useEffect(() => {
        if (isOpen && seriesId) {
            setIsLoading(true);
            setLoadError(null);
            setExpandedDefault(null);
            api.getSeriesPromptConfig(seriesId)
                .then((data) => {
                    setConfig(data.prompt_config);
                    setDefaults(data.defaults ?? null);
                })
                .catch((err) => {
                    console.error("Failed to load series prompt config:", err);
                    setLoadError(copy.loadError);
                })
                .finally(() => setIsLoading(false));
        }
    }, [isOpen, seriesId, copy.loadError]);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await api.updateSeriesPromptConfig(seriesId, config);
            onSaved?.();
            onClose();
        } catch (error) {
            console.error("Failed to save series prompt config:", error);
            alert(copy.saveError);
        } finally {
            setIsSaving(false);
        }
    };

    const handleReset = (key: keyof PromptDefaults) => {
        setConfig(prev => ({ ...prev, [key]: '' }));
    };

    const handleApplyPreset = (key: PromptConfigSectionKey, prompt: string) => {
        setConfig((prev) => ({ ...prev, [key]: prompt }));
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
                onClick={onClose}
            >
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-[#1a1a1a] rounded-2xl border border-white/10 w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col"
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="p-6 border-b border-white/10 flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-purple-500/20 rounded-lg">
                                <FileText size={20} className="text-purple-400" />
                            </div>
                            <div>
                                <h2 className="text-lg font-bold text-white">{seriesCopy.title}</h2>
                                <p className="text-xs text-gray-400">{seriesCopy.subtitle}</p>
                            </div>
                        </div>
                        <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg transition-colors">
                            <X size={20} className="text-gray-400" />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
                        {isLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <Loader2 size={24} className="animate-spin text-purple-400" />
                                <span className="ml-2 text-gray-400">{messages.common.messages.loadingConfiguration}</span>
                            </div>
                        ) : loadError ? (
                            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
                                {loadError}
                            </div>
                        ) : (
                            <>
                                <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 text-xs text-blue-300">
                                    {seriesCopy.emptyHint}
                                </div>

                                {SECTIONS.map((section) => (
                                    <div key={section.key} className="space-y-2">
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <h3 className="text-sm font-bold text-white">{section.label}</h3>
                                                <p className="text-[10px] text-gray-500 mt-0.5">{section.description}</p>
                                            </div>
                                            <button
                                                onClick={() => handleReset(section.key)}
                                                disabled={!config[section.key]}
                                                className="text-[10px] text-gray-400 hover:text-white flex items-center gap-1 px-2 py-1 rounded hover:bg-white/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                            >
                                                <RotateCcw size={10} /> {copy.resetToDefault}
                                            </button>
                                        </div>

                                        <textarea
                                            value={config[section.key]}
                                            onChange={(e) => setConfig(prev => ({ ...prev, [section.key]: e.target.value }))}
                                            placeholder={defaults ? defaults[section.key].slice(0, 150) + '...' : copy.loadingDefault}
                                            className="w-full h-32 bg-black/30 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-y focus:outline-none focus:border-purple-500/50 font-mono placeholder-gray-600"
                                        />

                                        <div className="space-y-2">
                                            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">
                                                {copy.generateFromTemplate}
                                            </p>
                                            <div className="flex flex-wrap gap-2">
                                                {getPromptConfigPresets(section.key).map((preset) => (
                                                    <button
                                                        key={preset.id}
                                                        type="button"
                                                        onClick={() => handleApplyPreset(section.key, preset.prompt)}
                                                        className="rounded-full border border-purple-400/20 bg-purple-500/10 px-3 py-1 text-[11px] text-purple-100 transition hover:bg-purple-500/20"
                                                        title={preset.summary}
                                                    >
                                                        {preset.title}
                                                    </button>
                                                ))}
                                            </div>
                                        </div>

                                        {defaults && (
                                            <div>
                                                <button
                                                    onClick={() => setExpandedDefault(expandedDefault === section.key ? null : section.key)}
                                                    className="text-[10px] text-gray-500 hover:text-gray-300 flex items-center gap-1 transition-colors"
                                                >
                                                    {expandedDefault === section.key ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                                                    {copy.viewFullDefaultPrompt}
                                                </button>
                                                {expandedDefault === section.key && (
                                                    <pre className="mt-2 bg-black/40 border border-white/5 rounded-lg p-3 text-[10px] text-gray-500 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap font-mono">{defaults[section.key]}</pre>
                                                )}
                                            </div>
                                        )}

                                        {section.key !== 'r2v_polish' && (
                                            <div className="border-b border-white/5" />
                                        )}
                                    </div>
                                ))}
                            </>
                        )}
                    </div>

                    {/* Footer */}
                    <div className="p-6 border-t border-white/10 flex justify-end gap-3">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
                        >
                            {commonActions.cancel}
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={isSaving || isLoading || !!loadError}
                            className="px-6 py-2 text-sm font-medium bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors disabled:opacity-50 flex items-center gap-2"
                        >
                            {isSaving && <Loader2 size={14} className="animate-spin" />}
                            {commonActions.save}
                        </button>
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
