"use client";

import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, FileText, RotateCcw, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useProjectStore } from '@/store/projectStore';
import { api } from '@/lib/api';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { ProjectReadbackError, readProjectOnce } from '@/lib/projectReadback';

interface PromptConfigModalProps {
    isOpen: boolean;
    onClose: () => void;
}

interface PromptDefaults {
    storyboard_polish: string;
    video_polish: string;
    r2v_polish: string;
}

const SECTIONS = [
    {
        key: 'storyboard_polish' as const,
        label: 'Storyboard Polish (Prompt C)',
        description: 'System prompt for storyboard/image prompt polishing. Placeholders: {ASSETS} (asset context), {DRAFT} (user draft prompt).',
    },
    {
        key: 'video_polish' as const,
        label: 'Video I2V Polish (Prompt D)',
        description: 'System prompt for Image-to-Video prompt polishing. No dynamic placeholders needed.',
    },
    {
        key: 'r2v_polish' as const,
        label: 'Video R2V Polish (Prompt E)',
        description: 'System prompt for Reference-to-Video prompt polishing. Placeholder: {SLOTS} (character slot context).',
    },
];

export default function PromptConfigModal({ isOpen, onClose }: PromptConfigModalProps) {
    const currentProject = useProjectStore((state) => state.currentProject);
    const t = useTranslations("project");
    const tc = useTranslations("common");
    const tf = useTranslations("frameCommands");

    const [config, setConfig] = useState({ storyboard_polish: '', video_polish: '', r2v_polish: '', polish_model: '' });
    const [defaults, setDefaults] = useState<PromptDefaults | null>(null);
    const [expandedDefault, setExpandedDefault] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const sessionRef = useRef(0);
    const savingRef = useRef(false);
    const pendingReadRevisionRef = useRef<number | undefined>(undefined);
    const initialRuntime = getRuntimeEnvironment();
    const latestRef = useRef({
        isOpen,
        projectId: currentProject?.id,
        environment: currentProject?.readEnvironment,
        generation: initialRuntime && initialRuntime.key === currentProject?.readEnvironment ? initialRuntime.generation : undefined,
    });

    useEffect(() => {
        const runtime = getRuntimeEnvironment();
        latestRef.current = {
            isOpen,
            projectId: currentProject?.id,
            environment: currentProject?.readEnvironment,
            generation: runtime && runtime.key === currentProject?.readEnvironment ? runtime.generation : undefined,
        };
    });

    const isCurrentSession = (token: number, projectId: string) => (
        sessionRef.current === token
        && latestRef.current.isOpen
        && latestRef.current.projectId === projectId
        && !!latestRef.current.environment
        && isCurrentRuntimeEnvironment(latestRef.current.environment, latestRef.current.generation)
    );

    const readAckRevision = (value: unknown) => {
        const revision = (value as { revision?: unknown } | null)?.revision;
        if (typeof revision !== 'number' || !Number.isSafeInteger(revision) || revision < 0) {
            throw new Error('Prompt config response did not include a committed revision');
        }
        return revision;
    };

    useEffect(() => {
        sessionRef.current += 1;
        savingRef.current = false;
        setIsSaving(false);
        setSaveError(null);
        pendingReadRevisionRef.current = undefined;
        if (isOpen && currentProject) {
            const projectId = currentProject.id;
            const token = sessionRef.current;
            setIsLoading(true);
            setLoadError(null);
            setExpandedDefault(null);
            api.getPromptConfig(projectId)
                .then((data) => {
                    if (!isCurrentSession(token, projectId)) return;
                    const revision = readAckRevision(data);
                    const confirmed = useProjectStore.getState().currentProject;
                    if (revision < (confirmed?.revision ?? 0)) throw new Error('Prompt config read is older than the confirmed project');
                    setConfig(data.prompt_config);
                    setDefaults(data.defaults);
                })
                .catch((err) => {
                    console.error("Failed to load prompt config:", err);
                    if (isCurrentSession(token, projectId)) setLoadError(t("promptLoadFailed"));
                })
                .finally(() => {
                    if (isCurrentSession(token, projectId)) setIsLoading(false);
                });
        }
        return () => {
            sessionRef.current += 1;
            savingRef.current = false;
        };
    }, [isOpen, currentProject?.id, currentProject?.readEnvironment]);

    const handleReadAgain = async () => {
        if (!currentProject || savingRef.current) return;
        savingRef.current = true;
        setIsSaving(true);
        const projectId = currentProject.id;
        const token = sessionRef.current;
        setSaveError(null);
        try {
            await readProjectOnce(projectId, pendingReadRevisionRef.current, { isCurrent: () => isCurrentSession(token, projectId) });
            if (isCurrentSession(token, projectId)) {
                pendingReadRevisionRef.current = undefined;
                onClose();
            }
        } catch {
            if (isCurrentSession(token, projectId)) setSaveError(tf("savedReadFailed"));
        } finally {
            if (isCurrentSession(token, projectId)) setIsSaving(false);
            if (sessionRef.current === token) savingRef.current = false;
        }
    };

    const handleSave = async () => {
        if (!currentProject || savingRef.current || pendingReadRevisionRef.current !== undefined) return;
        const projectId = currentProject.id;
        const token = sessionRef.current;
        savingRef.current = true;
        setIsSaving(true);
        setSaveError(null);
        try {
            const result = await api.updatePromptConfig(projectId, config);
            if (!isCurrentSession(token, projectId)) return;
            const revision = readAckRevision(result);
            pendingReadRevisionRef.current = revision;
            if (!isCurrentSession(token, projectId)) return;
            await readProjectOnce(projectId, revision, { isCurrent: () => isCurrentSession(token, projectId) });
            if (isCurrentSession(token, projectId)) {
                pendingReadRevisionRef.current = undefined;
                onClose();
            }
        } catch (error) {
            console.error("Failed to save prompt config:", error);
            if (isCurrentSession(token, projectId)) {
                setSaveError(error instanceof ProjectReadbackError ? tf("savedReadFailed") : t("promptSaveFailed"));
            }
        } finally {
            if (isCurrentSession(token, projectId)) setIsSaving(false);
            if (sessionRef.current === token) savingRef.current = false;
        }
    };

    const handleReset = (key: keyof PromptDefaults) => {
        setConfig(prev => ({ ...prev, [key]: '' }));
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 bg-overlay backdrop-blur-sm flex items-center justify-center p-4"
                onClick={onClose}
            >
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-elevated rounded-2xl border border-glass-border w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col"
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="p-6 border-b border-glass-border flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-purple-500/20 rounded-lg">
                                <FileText size={20} className="text-purple-400" />
                            </div>
                            <div>
                                <h2 className="text-lg font-bold text-foreground">{t("promptConfig")}</h2>
                                <p className="text-xs text-text-secondary">{t("promptConfigSub")}</p>
                            </div>
                        </div>
                        <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-lg transition-colors">
                            <X size={20} className="text-text-secondary" />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
                        {isLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <Loader2 size={24} className="animate-spin text-purple-400" />
                                <span className="ml-2 text-text-secondary">{t("loadingConfig")}</span>
                            </div>
                        ) : loadError ? (
                            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
                                {loadError}
                            </div>
                        ) : (
                            <>
                                <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 text-xs text-blue-300">
                                    {t("promptEmptyHint")}
                                </div>

                                {/* Issue 13: polish 用的 LLM 模型选择。优先项目级 → 系列级 →
                                    LLMAdapter 默认（qwen3.6-plus）。三个推荐选项都是
                                    vision-capable，能让带首帧/参考图的润色更准确。 */}
                                <div className="space-y-2">
                                    <div>
                                        <h3 className="text-sm font-bold text-foreground">Polish 模型</h3>
                                        <p className="text-[0.625rem] text-text-muted mt-0.5">
                                            选择 AI 润色调用的 LLM 模型。三个选项都支持视觉理解，能在润色时参考首帧/参考图。
                                        </p>
                                    </div>
                                    <select
                                        value={config.polish_model || "qwen3.7-plus"}
                                        onChange={(e) => setConfig(prev => ({ ...prev, polish_model: e.target.value }))}
                                        className="w-full bg-surface border border-glass-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-purple-500/50"
                                    >
                                        <option value="qwen3.7-plus">qwen3.7-plus · 通义千问 3.7 Plus（最新）</option>
                                        <option value="qwen3.6-plus">qwen3.6-plus · 通义千问 3.6 Plus（视觉）</option>
                                        <option value="qwen3.6-flash">qwen3.6-flash · 通义千问 3.6 Flash（更快）</option>
                                        <option value="kimi-k2.6">kimi-k2.6 · Moonshot Kimi K2.6（视觉）</option>
                                    </select>
                                    <div className="border-b border-border-subtle pt-1" />
                                </div>

                                {SECTIONS.map((section) => (
                                    <div key={section.key} className="space-y-2">
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <h3 className="text-sm font-bold text-foreground">{section.label}</h3>
                                                <p className="text-[0.625rem] text-text-muted mt-0.5">{section.description}</p>
                                            </div>
                                            <button
                                                onClick={() => handleReset(section.key)}
                                                disabled={!config[section.key]}
                                                className="text-[0.625rem] text-text-secondary hover:text-foreground flex items-center gap-1 px-2 py-1 rounded hover:bg-hover-bg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                            >
                                                <RotateCcw size={10} /> {t("resetToDefault")}
                                            </button>
                                        </div>

                                        <textarea
                                            value={config[section.key]}
                                            onChange={(e) => setConfig(prev => ({ ...prev, [section.key]: e.target.value }))}
                                            placeholder={defaults ? defaults[section.key].slice(0, 150) + '...' : 'Loading default...'}
                                            className="w-full h-32 bg-surface border border-glass-border rounded-lg p-3 text-xs text-foreground resize-y focus:outline-none focus:border-purple-500/50 font-mono placeholder-text-muted"
                                        />

                                        {/* Expandable default prompt viewer */}
                                        {defaults && (
                                            <div>
                                                <button
                                                    onClick={() => setExpandedDefault(expandedDefault === section.key ? null : section.key)}
                                                    className="text-[0.625rem] text-text-muted hover:text-foreground flex items-center gap-1 transition-colors"
                                                >
                                                    {expandedDefault === section.key ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                                                    {t("viewDefault")}
                                                </button>
                                                {expandedDefault === section.key && (
                                                    <pre className="mt-2 bg-surface border border-border-subtle rounded-lg p-3 text-[0.625rem] text-text-muted overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap font-mono">{defaults[section.key]}</pre>
                                                )}
                                            </div>
                                        )}

                                        {section.key !== 'r2v_polish' && (
                                            <div className="border-b border-border-subtle" />
                                        )}
                                    </div>
                                ))}
                            </>
                        )}
                    </div>

                    {/* Footer */}
                    {saveError && (
                        <div role="alert" className="mx-6 mb-3 bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-sm text-red-300 flex items-center justify-between gap-3">
                            <span>{saveError}</span>
                            {saveError === tf("savedReadFailed") && (
                                <button type="button" onClick={handleReadAgain} className="text-xs text-red-100 underline whitespace-nowrap">
                                    {t("readAgain")}
                                </button>
                            )}
                        </div>
                    )}
                    <div className="p-6 border-t border-glass-border flex justify-end gap-3">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-text-secondary hover:text-foreground transition-colors"
                        >
                            {tc("cancel")}
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={isSaving || isLoading || !!loadError || pendingReadRevisionRef.current !== undefined}
                            className="px-6 py-2 text-sm font-medium bg-purple-600 hover:bg-purple-500 text-foreground rounded-lg transition-colors disabled:opacity-50 flex items-center gap-2"
                        >
                            {isSaving && <Loader2 size={14} className="animate-spin" />}
                            {tc("save")}
                        </button>
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
