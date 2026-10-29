"use client";
/**
 * SeriesArtDirectionPanel — series-level art_direction baseline editor.
 * Uses the v2 preset system with category tabs and thumbnail cards.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Palette, Check, Trash2, Save, X, Pencil, Image as ImageIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { publicAssetPath } from "@/lib/publicAsset";
import { persistenceFailure, type PersistenceFailure } from "@/lib/persistenceFeedback";
import { useProjectStore } from "@/store/projectStore";
import type { Series, StyleConfig, StylePreset, StylePresetCategory } from "@/store/projectStore";
import WorkflowActionButton from "@/components/shared/WorkflowActionButton";

interface SeriesArtDirectionPanelProps {
    seriesId: string;
    onSaved: () => void;
}

export default function SeriesArtDirectionPanel({ seriesId, onSaved }: SeriesArtDirectionPanelProps) {
    const t = useTranslations("seriesArtDirection");
    const tp = useTranslations("persistence");

    const [series, setSeries] = useState<Series | null>(null);
    const [presets, setPresets] = useState<StylePreset[]>([]);
    const [categories, setCategories] = useState<StylePresetCategory[]>([]);
    const [activeCategory, setActiveCategory] = useState<string>("all");
    const [selectedStyle, setSelectedStyle] = useState<StyleConfig | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [saveError, setSaveError] = useState<PersistenceFailure | null>(null);
    const savingSeries = useRef(new Set<string>());
    const view = useMemo(() => ({ seriesId }), [seriesId]);
    const activeView = useRef<typeof view | null>(view);
    activeView.current = view;

    // Modal state
    const [modalPreset, setModalPreset] = useState<StylePreset | null>(null);
    const [modalEditing, setModalEditing] = useState(false);
    const [modalPositive, setModalPositive] = useState("");
    const [modalNegative, setModalNegative] = useState("");

    useEffect(() => {
        let cancelled = false;
        activeView.current = view;
        setIsLoading(true);
        setSeries(null);
        setSelectedStyle(null);
        setSaveError(null);
        setIsSaving(savingSeries.current.has(seriesId));
        Promise.all([
            api.getSeries(seriesId).catch(() => null),
            api.getStylePresets().catch(() => ({ presets: [], categories: [] })),
        ]).then(([s, p]: any) => {
            if (cancelled) return;
            setSeries(s);
            setPresets(p?.presets ?? []);
            setCategories(p?.categories ?? []);
            const sel = s?.art_direction?.style_config ?? null;
            setSelectedStyle(sel);
            setIsLoading(false);
        });
        return () => {
            cancelled = true;
            if (activeView.current === view) activeView.current = null;
        };
    }, [seriesId, view]);

    const filteredPresets = useMemo(() => {
        if (activeCategory === "all") return presets;
        return presets.filter(p => p.category === activeCategory);
    }, [presets, activeCategory]);

    const currentStyleName = series?.art_direction?.style_config?.name ?? null;

    const isDirty = useMemo(() => {
        if (!selectedStyle && !currentStyleName) return false;
        if (!selectedStyle || !currentStyleName) return true;
        return selectedStyle.name !== currentStyleName
            || selectedStyle.positive_prompt !== (series?.art_direction?.style_config?.positive_prompt ?? "")
            || selectedStyle.negative_prompt !== (series?.art_direction?.style_config?.negative_prompt ?? "");
    }, [selectedStyle, currentStyleName, series?.art_direction?.style_config]);

    const openPresetModal = (preset: StylePreset) => {
        if (isSaving) return;
        setModalPreset(preset);
        setModalEditing(false);
        setModalPositive(preset.positive_prompt);
        setModalNegative(preset.negative_prompt);
    };

    const closePresetModal = () => {
        setModalPreset(null);
        setModalEditing(false);
    };

    const handleModalApply = () => {
        if (!modalPreset || isSaving) return;
        const isCustomized = modalEditing && (
            modalPositive !== modalPreset.positive_prompt ||
            modalNegative !== modalPreset.negative_prompt
        );
        setSelectedStyle({
            id: modalPreset.id,
            name: modalPreset.name,
            description: "",
            positive_prompt: isCustomized ? modalPositive : modalPreset.positive_prompt,
            negative_prompt: isCustomized ? modalNegative : modalPreset.negative_prompt,
            is_custom: false,
        });
        closePresetModal();
    };

    const persistBaseline = async (artDirection: Series["art_direction"] | null) => {
        if (isLoading || savingSeries.current.has(seriesId) || saveError === "conflict") return;
        if (!series || !Number.isSafeInteger(series.revision) || series.revision < 0) {
            setSaveError("missingRevision");
            return;
        }
        savingSeries.current.add(seriesId);
        setIsSaving(true);
        setSaveError(null);
        try {
            const saved = await api.updateSeries(seriesId, {
                expected_revision: series.revision,
                art_direction: artDirection,
            });
            if (activeView.current !== view) return;
            setSeries(saved);
            useProjectStore.setState(state => ({
                currentSeries: state.currentSeries?.id === seriesId && state.currentSeries.revision > saved.revision
                    ? state.currentSeries : saved,
                seriesList: state.seriesList.map(current => current.id === seriesId && !(current.revision > saved.revision)
                    ? saved : current),
            }));
            if (!artDirection) setSelectedStyle(null);
            onSaved();
        } catch (err) {
            if (activeView.current === view) setSaveError(persistenceFailure(err));
        } finally {
            savingSeries.current.delete(seriesId);
            if (activeView.current?.seriesId === seriesId) setIsSaving(false);
        }
    };

    const handleSaveBaseline = () => {
        if (!selectedStyle) return;
        return persistBaseline({
            selected_style_id: selectedStyle.id,
            style_config: selectedStyle,
            custom_styles: series?.art_direction?.custom_styles ?? [],
            ai_recommendations: series?.art_direction?.ai_recommendations ?? [],
        });
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="flex h-full w-full flex-col overflow-hidden"
        >
            {/* Header */}
            <header className="flex shrink-0 items-center gap-3 border-b border-glass-border bg-surface px-8 py-5">
                <div className="grid h-9 w-9 place-items-center rounded-full border border-[rgba(100,108,255,0.32)] bg-[rgba(100,108,255,0.08)] text-primary">
                    <Palette size={16} strokeWidth={1.5} />
                </div>
                <div className="flex-1 min-w-0">
                    <h2 className="font-display text-display font-medium text-foreground">
                        {t("title")}
                    </h2>
                    <p className="text-xs text-text-secondary mt-0.5">
                        {currentStyleName ? t("currentBaseline", { name: currentStyleName }) : t("noBaseline")}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {currentStyleName && (
                        <WorkflowActionButton
                            variant="ghost"
                            size="sm"
                            leftIcon={<Trash2 />}
                            onClick={() => persistBaseline(null)}
                            disabled={isSaving || isLoading || saveError === "conflict"}
                        >
                            {t("clear")}
                        </WorkflowActionButton>
                    )}
                    <WorkflowActionButton
                        variant="primary"
                        loading={isSaving}
                        leftIcon={<Save />}
                        onClick={handleSaveBaseline}
                        disabled={!isDirty || !selectedStyle || isLoading || saveError === "conflict"}
                    >
                        {isDirty ? t("saveBaseline") : t("baselineSaved")}
                    </WorkflowActionButton>
                </div>
            </header>
            {saveError && <p role="alert" className="px-8 py-3 text-sm text-status-failed-fg">{tp(saveError)}</p>}

            {/* Body */}
            <div className="flex-1 overflow-y-auto bg-surface px-8 py-6 space-y-8 custom-scrollbar">
                {isLoading ? (
                    <div className="grid place-items-center py-12 text-text-muted text-sm">
                        {t("loading")}
                    </div>
                ) : (
                    <>
                        {/* Selected preview */}
                        <section>
                            <h3 className="mb-3 inline-flex items-center gap-2 font-mono text-[0.6875rem] font-medium uppercase tracking-[0.18em] text-text-secondary">
                                <Check size={14} className="text-primary" />
                                {t("currentSelection")}
                            </h3>
                            {selectedStyle ? (
                                <div className="rounded-lg border border-primary/40 bg-primary/10 px-4 py-3">
                                    <p className="font-display text-base font-medium text-foreground">{selectedStyle.name}</p>
                                    {selectedStyle.positive_prompt && (
                                        <p className="mt-2 font-mono text-[0.6875rem] text-text-muted line-clamp-2">
                                            <span className="text-primary mr-1">+</span>
                                            {selectedStyle.positive_prompt}
                                        </p>
                                    )}
                                </div>
                            ) : (
                                <p className="rounded-lg border border-dashed border-glass-border px-4 py-6 text-center text-sm text-text-muted">
                                    {t("emptySelection")}
                                </p>
                            )}
                        </section>

                        {/* Category tabs */}
                        <section>
                            <div className="flex items-center gap-1.5 mb-4 overflow-x-auto pb-1">
                                <button
                                    onClick={() => setActiveCategory("all")}
                                    className={`shrink-0 px-3 py-1.5 rounded-lg text-[0.75rem] font-medium transition-colors ${
                                        activeCategory === "all"
                                            ? "bg-primary/20 text-primary border border-primary/30"
                                            : "bg-elevated text-text-secondary hover:bg-hover-bg border border-transparent"
                                    }`}
                                >
                                    {t("filterAll")}
                                </button>
                                {categories.map(cat => (
                                    <button
                                        key={cat.id}
                                        onClick={() => setActiveCategory(cat.id)}
                                        className={`shrink-0 px-3 py-1.5 rounded-lg text-[0.75rem] font-medium transition-colors ${
                                            activeCategory === cat.id
                                                ? "bg-primary/20 text-primary border border-primary/30"
                                                : "bg-elevated text-text-secondary hover:bg-hover-bg border border-transparent"
                                        }`}
                                    >
                                        {cat.name_zh}
                                    </button>
                                ))}
                            </div>

                            {/* Preset grid with thumbnails */}
                            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                                {filteredPresets.map(preset => (
                                    <motion.div
                                        key={preset.id}
                                        layout
                                        onClick={() => openPresetModal(preset)}
                                        className={`group relative rounded-xl border overflow-hidden cursor-pointer transition-all ${
                                            selectedStyle?.id === preset.id
                                                ? "border-primary shadow-lg shadow-primary/20 ring-1 ring-primary/40"
                                                : "border-glass-border hover:border-foreground/30 hover:shadow-sm"
                                        }`}
                                    >
                                        <div className="relative aspect-[4/3] bg-elevated overflow-hidden">
                                            {preset.thumbnail ? (
                                                <img
                                                    src={publicAssetPath(preset.thumbnail)}
                                                    alt={preset.name_zh}
                                                    className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                                                    style={{ objectPosition: preset.object_position || "center" }}
                                                />
                                            ) : (
                                                <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-glass-border to-glass-border">
                                                    <ImageIcon size={24} className="text-text-muted/40" />
                                                </div>
                                            )}
                                            {selectedStyle?.id === preset.id && (
                                                <div className="absolute top-2 right-2 w-5 h-5 rounded-full bg-primary flex items-center justify-center shadow-md">
                                                    <Check size={11} className="text-foreground" />
                                                </div>
                                            )}
                                        </div>
                                        <div className="px-3 py-2.5">
                                            <h4 className="text-[0.75rem] font-semibold text-foreground leading-tight truncate">
                                                {preset.name_zh}
                                            </h4>
                                            {preset.subtitle_zh && (
                                                <p className="text-[0.625rem] text-text-muted mt-0.5 truncate">
                                                    {preset.subtitle_zh}
                                                </p>
                                            )}
                                        </div>
                                    </motion.div>
                                ))}
                            </div>
                        </section>
                    </>
                )}
            </div>

            {/* Preset Detail Modal */}
            <AnimatePresence>
                {modalPreset && (
                    <SeriesPresetModal
                        preset={modalPreset}
                        isSelected={selectedStyle?.id === modalPreset.id}
                        editing={modalEditing}
                        positivePrompt={modalPositive}
                        negativePrompt={modalNegative}
                        onPositiveChange={setModalPositive}
                        onNegativeChange={setModalNegative}
                        onStartEditing={() => setModalEditing(true)}
                        onApply={handleModalApply}
                        onClose={closePresetModal}
                        sameCategoryPresets={presets.filter(p => p.category === modalPreset.category && p.id !== modalPreset.id)}
                        onSwitchPreset={(p) => {
                            setModalPreset(p);
                            setModalEditing(false);
                            setModalPositive(p.positive_prompt);
                            setModalNegative(p.negative_prompt);
                        }}
                    />
                )}
            </AnimatePresence>
        </motion.div>
    );
}

function SeriesPresetModal({ preset, isSelected, editing, positivePrompt, negativePrompt, onPositiveChange, onNegativeChange, onStartEditing, onApply, onClose, sameCategoryPresets, onSwitchPreset }: {
    preset: StylePreset;
    isSelected: boolean;
    editing: boolean;
    positivePrompt: string;
    negativePrompt: string;
    onPositiveChange: (v: string) => void;
    onNegativeChange: (v: string) => void;
    onStartEditing: () => void;
    onApply: () => void;
    onClose: () => void;
    sameCategoryPresets: StylePreset[];
    onSwitchPreset: (p: StylePreset) => void;
}) {
    const t = useTranslations("seriesArtDirection");
    const tCommon = useTranslations("common");
    const isCustomized = editing && (
        positivePrompt !== preset.positive_prompt ||
        negativePrompt !== preset.negative_prompt
    );

    return (
        <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-[100] bg-overlay backdrop-blur-sm grid place-items-center p-6"
            onClick={onClose}
        >
            <motion.div
                initial={{ opacity: 0, scale: 0.96, y: 8 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96, y: 8 }}
                transition={{ duration: 0.2 }}
                className="w-[70vw] max-w-[1100px] min-w-[640px] max-h-[90vh] rounded-2xl border border-glass-border bg-elevated shadow-[0_24px_64px_-12px_rgba(0,0,0,0.7)] overflow-hidden flex flex-col"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Header */}
                <header className="flex items-center justify-between px-6 py-4 border-b border-glass-border shrink-0">
                    <div>
                        <h2 className="text-[1.125rem] font-bold text-foreground">{preset.name_zh}</h2>
                        <p className="text-[0.75rem] text-text-muted mt-0.5">{preset.name}</p>
                    </div>
                    <button onClick={onClose} className="p-2 rounded-lg hover:bg-hover-bg text-text-muted hover:text-foreground transition-colors">
                        <X size={18} />
                    </button>
                </header>

                {/* Body */}
                <div className="flex-1 min-h-0 grid grid-cols-[1fr_1fr] overflow-hidden">
                    <div className="bg-black/40 flex items-center justify-center p-4 overflow-hidden">
                        {preset.thumbnail ? (
                            <img src={publicAssetPath(preset.thumbnail)} alt={preset.name_zh} className="max-w-full max-h-full object-contain rounded-lg" />
                        ) : (
                            <div className="w-full h-full flex items-center justify-center">
                                <ImageIcon size={48} className="text-text-muted/30" />
                            </div>
                        )}
                    </div>

                    <div className="p-6 space-y-5 overflow-y-auto">
                        {preset.description && (
                            <p className="text-[0.8125rem] text-text-secondary leading-relaxed">{preset.description}</p>
                        )}

                        {preset.best_for && preset.best_for.length > 0 && (
                            <div className="flex flex-wrap gap-2">
                                {preset.best_for.map((tag, i) => (
                                    <span key={i} className="text-[0.6875rem] px-2.5 py-1 rounded-md bg-green-500/10 text-green-300 border border-green-500/20">{tag}</span>
                                ))}
                                {preset.avoid_for?.map((tag, i) => (
                                    <span key={`avoid-${i}`} className="text-[0.6875rem] px-2.5 py-1 rounded-md bg-red-500/10 text-red-300/70 border border-red-500/15 line-through">{tag}</span>
                                ))}
                            </div>
                        )}

                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <p className="text-[0.6875rem] uppercase tracking-wider text-text-muted font-medium">{t("promptLabel")}</p>
                                {!editing && (
                                    <button onClick={onStartEditing} className="flex items-center gap-1.5 text-[0.6875rem] text-text-muted hover:text-foreground transition-colors">
                                        <Pencil size={12} /><span>{t("customize")}</span>
                                    </button>
                                )}
                                {editing && isCustomized && (
                                    <span className="text-[0.625rem] text-amber-300 font-medium">{t("modified")}</span>
                                )}
                            </div>

                            {!editing ? (
                                <>
                                    <div>
                                        <p className="text-[0.625rem] text-text-muted mb-1.5">{t("positive")}</p>
                                        <p className="text-[0.8125rem] text-text-secondary leading-relaxed">{preset.positive_prompt}</p>
                                    </div>
                                    <div>
                                        <p className="text-[0.625rem] text-text-muted mb-1.5">{t("negative")}</p>
                                        <p className="text-[0.8125rem] text-text-secondary leading-relaxed">{preset.negative_prompt}</p>
                                    </div>
                                </>
                            ) : (
                                <>
                                    <div>
                                        <p className="text-[0.625rem] text-text-muted mb-1.5">{t("positive")}</p>
                                        <textarea value={positivePrompt} onChange={(e) => onPositiveChange(e.target.value)} rows={5} className="w-full bg-input-bg border border-glass-border rounded-lg p-3 text-[0.8125rem] text-foreground focus:border-primary focus:outline-none resize-none" />
                                    </div>
                                    <div>
                                        <p className="text-[0.625rem] text-text-muted mb-1.5">{t("negative")}</p>
                                        <textarea value={negativePrompt} onChange={(e) => onNegativeChange(e.target.value)} rows={3} className="w-full bg-input-bg border border-glass-border rounded-lg p-3 text-[0.8125rem] text-foreground focus:border-primary focus:outline-none resize-none" />
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                </div>

                {/* Same-category strip */}
                {sameCategoryPresets.length > 0 && (
                    <div className="border-t border-glass-border px-6 py-3 shrink-0">
                        <p className="text-[0.625rem] uppercase tracking-wider text-text-muted mb-2">{t("similarStyles")}</p>
                        <div className="flex gap-2.5 overflow-x-auto pb-1">
                            {sameCategoryPresets.slice(0, 5).map(p => (
                                <button key={p.id} onClick={() => onSwitchPreset(p)} className="shrink-0 w-24 rounded-lg overflow-hidden border border-glass-border hover:border-foreground/30 transition-colors">
                                    {p.thumbnail ? (
                                        <img src={publicAssetPath(p.thumbnail)} alt={p.name_zh} className="w-full aspect-[16/9] object-cover" style={{ objectPosition: p.object_position || "center" }} />
                                    ) : (
                                        <div className="w-full aspect-[16/9] bg-elevated flex items-center justify-center"><ImageIcon size={12} className="text-text-muted/40" /></div>
                                    )}
                                    <p className="text-[0.625rem] text-text-muted px-1.5 py-1 truncate">{p.name_zh}</p>
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                <footer className="flex items-center justify-end gap-3 px-6 py-4 border-t border-glass-border shrink-0">
                    <WorkflowActionButton variant="ghost" onClick={onClose}>{tCommon("cancel")}</WorkflowActionButton>
                    <WorkflowActionButton variant="primary" leftIcon={<Check />} onClick={onApply}>
                        {isSelected ? t("selected") : isCustomized ? t("applyCustomStyle") : t("useThisStyle")}
                    </WorkflowActionButton>
                </footer>
            </motion.div>
        </motion.div>
    );
}
