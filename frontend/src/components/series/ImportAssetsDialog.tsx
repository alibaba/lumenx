"use client";

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import NextImage from "next/image";
import { X, Download, Users, MapPin, Package, Check, Loader2, ArrowRight, ArrowLeft, Image as ImageIcon } from 'lucide-react';
import { api } from '@/lib/api';
import { assetTypeTerms, messages } from '@/lib/i18n';
import type { Series, Character, Scene, Prop } from '@/store/projectStore';
import { getAssetUrl } from '@/lib/utils';

interface ImportAssetsDialogProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId: string;
    onImported?: () => void;
}

type AssetTab = "characters" | "scenes" | "props";

interface SelectableAsset {
    id: string;
    name: string;
    description?: string;
    imageUrl?: string;
    type: AssetTab;
}

function getAssetImageUrl(asset: Character | Scene | Prop, type: AssetTab): string | undefined {
    if (type === "characters") {
        const char = asset as Character;
        if (char.full_body_asset?.variants?.length) {
            const selected = char.full_body_asset.variants.find(v => v.id === char.full_body_asset?.selected_id);
            return getAssetUrl(selected?.url || char.full_body_asset.variants[0]?.url);
        }
        return getAssetUrl(char.image_url || char.full_body_image_url);
    }
    if (type === "scenes") {
        const scene = asset as Scene;
        if (scene.image_asset?.variants?.length) {
            const selected = scene.image_asset.variants.find(v => v.id === scene.image_asset?.selected_id);
            return getAssetUrl(selected?.url || scene.image_asset.variants[0]?.url);
        }
        return getAssetUrl(scene.image_url);
    }
    const prop = asset as Prop;
    if (prop.image_asset?.variants?.length) {
        const selected = prop.image_asset.variants.find(v => v.id === prop.image_asset?.selected_id);
        return getAssetUrl(selected?.url || prop.image_asset.variants[0]?.url);
    }
    return getAssetUrl(prop.image_url);
}

export default function ImportAssetsDialog({ isOpen, onClose, seriesId, onImported }: ImportAssetsDialogProps) {
    const copy = messages.seriesPage.importAssetsDialog;
    const commonActions = messages.common.actions;
    const [step, setStep] = useState(1);
    const [allSeries, setAllSeries] = useState<Series[]>([]);
    const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
    const [sourceSeries, setSourceSeries] = useState<Series | null>(null);
    const [activeTab, setActiveTab] = useState<AssetTab>("characters");
    const [selectedAssetIds, setSelectedAssetIds] = useState<Set<string>>(new Set());
    const [isLoadingSeries, setIsLoadingSeries] = useState(false);
    const [isLoadingSource, setIsLoadingSource] = useState(false);
    const [isImporting, setIsImporting] = useState(false);

    // Reset state when dialog opens
    useEffect(() => {
        if (isOpen) {
            setStep(1);
            setSelectedSourceId(null);
            setSourceSeries(null);
            setSelectedAssetIds(new Set());
            setActiveTab("characters");
            setIsLoadingSeries(true);
            api.listSeries()
                .then((data: Series[]) => setAllSeries(data.filter(s => s.id !== seriesId)))
                .catch((err) => console.error("Failed to load series list:", err))
                .finally(() => setIsLoadingSeries(false));
        }
    }, [isOpen, seriesId]);

    const handleSelectSource = (id: string) => {
        setSelectedSourceId(id);
    };

    const handleGoToStep2 = async () => {
        if (!selectedSourceId) return;
        setIsLoadingSource(true);
        try {
            const data = await api.getSeries(selectedSourceId);
            setSourceSeries(data);
            setSelectedAssetIds(new Set());
            setActiveTab("characters");
            setStep(2);
        } catch (err) {
            console.error("Failed to load source series:", err);
        } finally {
            setIsLoadingSource(false);
        }
    };

    const getSourceAssets = (): SelectableAsset[] => {
        if (!sourceSeries) return [];
        const mapAssets = (assets: (Character | Scene | Prop)[], type: AssetTab): SelectableAsset[] =>
            assets.map(a => ({
                id: a.id,
                name: a.name,
                description: a.description,
                imageUrl: getAssetImageUrl(a, type),
                type,
            }));

        if (activeTab === "characters") return mapAssets(sourceSeries.characters || [], "characters");
        if (activeTab === "scenes") return mapAssets(sourceSeries.scenes || [], "scenes");
        return mapAssets(sourceSeries.props || [], "props");
    };

    const getAllSourceAssets = (): SelectableAsset[] => {
        if (!sourceSeries) return [];
        return [
            ...(sourceSeries.characters || []).map(a => ({ id: a.id, name: a.name, description: a.description, imageUrl: getAssetImageUrl(a, "characters"), type: "characters" as AssetTab })),
            ...(sourceSeries.scenes || []).map(a => ({ id: a.id, name: a.name, description: a.description, imageUrl: getAssetImageUrl(a, "scenes"), type: "scenes" as AssetTab })),
            ...(sourceSeries.props || []).map(a => ({ id: a.id, name: a.name, description: a.description, imageUrl: getAssetImageUrl(a, "props"), type: "props" as AssetTab })),
        ];
    };

    const toggleAsset = (id: string) => {
        setSelectedAssetIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const toggleAllInTab = () => {
        const tabAssets = getSourceAssets();
        const allSelected = tabAssets.every(a => selectedAssetIds.has(a.id));
        setSelectedAssetIds(prev => {
            const next = new Set(prev);
            tabAssets.forEach(a => {
                if (allSelected) next.delete(a.id);
                else next.add(a.id);
            });
            return next;
        });
    };

    const handleImport = async () => {
        if (!selectedSourceId || selectedAssetIds.size === 0) return;
        setIsImporting(true);
        try {
            await api.importSeriesAssets(seriesId, selectedSourceId, Array.from(selectedAssetIds));
            onImported?.();
            onClose();
        } catch (err) {
            console.error("Failed to import assets:", err);
            alert(copy.importFailed);
        } finally {
            setIsImporting(false);
        }
    };

    if (!isOpen) return null;

    const selectedAllAssets = getAllSourceAssets().filter(a => selectedAssetIds.has(a.id));
    const selectedCharacters = selectedAllAssets.filter(a => a.type === "characters");
    const selectedScenes = selectedAllAssets.filter(a => a.type === "scenes");
    const selectedProps = selectedAllAssets.filter(a => a.type === "props");

    const tabs: { id: AssetTab; label: string; icon: typeof Users; count: number }[] = sourceSeries ? [
        { id: "characters", label: assetTypeTerms.character.pluralLabel, icon: Users, count: sourceSeries.characters?.length || 0 },
        { id: "scenes", label: assetTypeTerms.scene.pluralLabel, icon: MapPin, count: sourceSeries.scenes?.length || 0 },
        { id: "props", label: assetTypeTerms.prop.pluralLabel, icon: Package, count: sourceSeries.props?.length || 0 },
    ] : [];

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
                    className="bg-[#1a1a1a] rounded-2xl border border-white/10 w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col"
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="p-5 border-b border-white/10 flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-gradient-to-br from-green-500/20 to-blue-500/20 rounded-lg">
                                <Download size={20} className="text-green-400" />
                            </div>
                            <div>
                                <h2 className="text-lg font-bold text-white">{copy.title}</h2>
                                <p className="text-xs text-gray-500">{copy.subtitle}</p>
                            </div>
                        </div>
                        <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg transition-colors">
                            <X size={20} className="text-gray-400" />
                        </button>
                    </div>

                    {/* Step indicator */}
                    <div className="flex items-center justify-center gap-2 py-3 border-b border-white/5">
                        {[1, 2, 3].map((s) => (
                            <div
                                key={s}
                                className={`w-2 h-2 rounded-full transition-colors ${s === step ? 'bg-primary' : s < step ? 'bg-primary/50' : 'bg-white/20'}`}
                            />
                        ))}
                    </div>

                    {/* Content */}
                    <div className="flex-1 overflow-y-auto">
                        {/* Step 1: Select source series */}
                        {step === 1 && (
                            <div className="p-5 space-y-3">
                                <p className="text-sm text-gray-400 mb-4">{copy.selectSourceSeries}</p>
                                {isLoadingSeries ? (
                                    <div className="flex items-center justify-center py-12">
                                        <Loader2 size={24} className="animate-spin text-blue-400" />
                                        <span className="ml-2 text-gray-400">{copy.loadingSeries}</span>
                                    </div>
                                ) : allSeries.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                                        <Package size={40} className="mb-3 text-gray-600" />
                                        <p className="text-sm">{copy.noSeriesAvailable}</p>
                                    </div>
                                ) : (
                                    allSeries.map((s) => {
                                        return (
                                            <button
                                                key={s.id}
                                                onClick={() => handleSelectSource(s.id)}
                                                className={`w-full text-left p-4 rounded-xl border transition-all ${selectedSourceId === s.id
                                                    ? 'border-primary/50 bg-primary/10'
                                                    : 'border-white/10 hover:border-white/20 bg-white/5'
                                                }`}
                                            >
                                                <div className="flex items-center justify-between">
                                                    <div>
                                                        <h3 className="text-sm font-medium text-white">{s.title}</h3>
                                                        {s.description && (
                                                            <p className="text-xs text-gray-500 mt-0.5 line-clamp-1">{s.description}</p>
                                                        )}
                                                        <p className="text-xs text-gray-600 mt-1">
                                                            {copy.assetStats(
                                                                s.characters?.length || 0,
                                                                s.scenes?.length || 0,
                                                                s.props?.length || 0,
                                                            )}
                                                        </p>
                                                    </div>
                                                    {selectedSourceId === s.id && (
                                                        <Check size={18} className="text-primary flex-shrink-0" />
                                                    )}
                                                </div>
                                            </button>
                                        );
                                    })
                                )}
                            </div>
                        )}

                        {/* Step 2: Select assets */}
                        {step === 2 && sourceSeries && (
                            <div className="flex flex-col">
                                <div className="px-5 pt-4 pb-2">
                                    <p className="text-xs text-gray-400">{copy.selectAssetsFrom(sourceSeries.title)}</p>
                                </div>

                                {/* Tabs */}
                                <div className="flex border-b border-gray-700/50">
                                    {tabs.map((tab) => (
                                        <button
                                            key={tab.id}
                                            onClick={() => setActiveTab(tab.id)}
                                            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium transition-colors border-b-2 ${activeTab === tab.id
                                                ? "border-primary text-white"
                                                : "border-transparent text-gray-400 hover:text-gray-200"
                                            }`}
                                        >
                                            <tab.icon size={14} />
                                            {tab.label}
                                            <span className="text-xs bg-white/10 px-1.5 py-0.5 rounded">{tab.count}</span>
                                        </button>
                                    ))}
                                </div>

                                {/* Select all toggle */}
                                {getSourceAssets().length > 0 && (
                                    <div className="px-5 pt-3">
                                        <button
                                            onClick={toggleAllInTab}
                                            className="text-xs text-gray-400 hover:text-white transition-colors"
                                        >
                                            {getSourceAssets().every(a => selectedAssetIds.has(a.id))
                                                ? commonActions.deselectAll
                                                : commonActions.selectAll}
                                        </button>
                                    </div>
                                )}

                                {/* Asset grid */}
                                <div className="p-5 grid grid-cols-2 sm:grid-cols-3 gap-3 overflow-y-auto max-h-[45vh]">
                                    {getSourceAssets().length === 0 ? (
                                        <div className="col-span-full flex flex-col items-center justify-center py-8 text-gray-500">
                                            <ImageIcon size={32} className="mb-2 text-gray-600" />
                                            <p className="text-xs">{copy.noAssetsInType}</p>
                                        </div>
                                    ) : (
                                        getSourceAssets().map((asset) => (
                                            <button
                                                key={asset.id}
                                                onClick={() => toggleAsset(asset.id)}
                                                className={`relative rounded-xl border overflow-hidden transition-all text-left ${selectedAssetIds.has(asset.id)
                                                    ? 'border-primary/50 bg-primary/10 ring-1 ring-primary/30'
                                                    : 'border-white/10 hover:border-white/20 bg-white/5'
                                                }`}
                                            >
                                                {/* Checkbox overlay */}
                                                <div className={`absolute top-2 right-2 z-10 w-5 h-5 rounded border flex items-center justify-center transition-colors ${selectedAssetIds.has(asset.id)
                                                    ? 'bg-primary border-primary'
                                                    : 'border-white/30 bg-black/40'
                                                }`}>
                                                    {selectedAssetIds.has(asset.id) && <Check size={12} className="text-white" />}
                                                </div>

                                                {/* Thumbnail */}
                                                <div className="aspect-square bg-gray-800/50 flex items-center justify-center overflow-hidden">
                                                    {asset.imageUrl ? (
                                                        <NextImage
                                                            src={asset.imageUrl}
                                                            alt={asset.name}
                                                            width={160}
                                                            height={160}
                                                            unoptimized
                                                            className="h-full w-full object-cover"
                                                        />
                                                    ) : (
                                                        <ImageIcon size={24} className="text-gray-600" />
                                                    )}
                                                </div>

                                                {/* Info */}
                                                <div className="p-2">
                                                    <p className="text-xs font-medium text-white truncate">{asset.name}</p>
                                                    {asset.description && (
                                                        <p className="text-[10px] text-gray-500 mt-0.5 line-clamp-1">{asset.description}</p>
                                                    )}
                                                </div>
                                            </button>
                                        ))
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Step 3: Confirm */}
                        {step === 3 && sourceSeries && (
                            <div className="p-5 space-y-4">
                                <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4 text-sm text-blue-300">
                                    {copy.confirmSummary(selectedAssetIds.size, sourceSeries.title)}
                                </div>

                                <div className="space-y-3">
                                    {selectedCharacters.length > 0 && (
                                        <div>
                                            <h4 className="text-xs font-medium text-gray-400 mb-2 flex items-center gap-1">
                                                <Users size={12} /> {copy.sectionCount(assetTypeTerms.character.pluralLabel, selectedCharacters.length)}
                                            </h4>
                                            <div className="space-y-1">
                                                {selectedCharacters.map(a => (
                                                    <div key={a.id} className="flex items-center gap-2 py-1.5 px-2 bg-white/5 rounded-lg">
                                                        <div className="w-8 h-8 rounded bg-gray-800 overflow-hidden flex-shrink-0">
                                                            {a.imageUrl ? (
                                                                <NextImage
                                                                    src={a.imageUrl}
                                                                    alt={a.name}
                                                                    width={32}
                                                                    height={32}
                                                                    unoptimized
                                                                    className="h-full w-full object-cover"
                                                                />
                                                            ) : (
                                                                <div className="w-full h-full flex items-center justify-center">
                                                                    <Users size={12} className="text-gray-600" />
                                                                </div>
                                                            )}
                                                        </div>
                                                        <span className="text-xs text-white">{a.name}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                    {selectedScenes.length > 0 && (
                                        <div>
                                            <h4 className="text-xs font-medium text-gray-400 mb-2 flex items-center gap-1">
                                                <MapPin size={12} /> {copy.sectionCount(assetTypeTerms.scene.pluralLabel, selectedScenes.length)}
                                            </h4>
                                            <div className="space-y-1">
                                                {selectedScenes.map(a => (
                                                    <div key={a.id} className="flex items-center gap-2 py-1.5 px-2 bg-white/5 rounded-lg">
                                                        <div className="w-8 h-8 rounded bg-gray-800 overflow-hidden flex-shrink-0">
                                                            {a.imageUrl ? (
                                                                <NextImage
                                                                    src={a.imageUrl}
                                                                    alt={a.name}
                                                                    width={32}
                                                                    height={32}
                                                                    unoptimized
                                                                    className="h-full w-full object-cover"
                                                                />
                                                            ) : (
                                                                <div className="w-full h-full flex items-center justify-center">
                                                                    <MapPin size={12} className="text-gray-600" />
                                                                </div>
                                                            )}
                                                        </div>
                                                        <span className="text-xs text-white">{a.name}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                    {selectedProps.length > 0 && (
                                        <div>
                                            <h4 className="text-xs font-medium text-gray-400 mb-2 flex items-center gap-1">
                                                <Package size={12} /> {copy.sectionCount(assetTypeTerms.prop.pluralLabel, selectedProps.length)}
                                            </h4>
                                            <div className="space-y-1">
                                                {selectedProps.map(a => (
                                                    <div key={a.id} className="flex items-center gap-2 py-1.5 px-2 bg-white/5 rounded-lg">
                                                        <div className="w-8 h-8 rounded bg-gray-800 overflow-hidden flex-shrink-0">
                                                            {a.imageUrl ? (
                                                                <NextImage
                                                                    src={a.imageUrl}
                                                                    alt={a.name}
                                                                    width={32}
                                                                    height={32}
                                                                    unoptimized
                                                                    className="h-full w-full object-cover"
                                                                />
                                                            ) : (
                                                                <div className="w-full h-full flex items-center justify-center">
                                                                    <Package size={12} className="text-gray-600" />
                                                                </div>
                                                            )}
                                                        </div>
                                                        <span className="text-xs text-white">{a.name}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>

                                <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-3 text-xs text-yellow-300">
                                    {copy.deepCopyHint}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Footer */}
                    <div className="p-5 border-t border-white/10 flex items-center justify-between bg-black/20">
                        <div className="text-xs text-gray-500">
                            {step === 2 && copy.selectionSummary(selectedAssetIds.size)}
                        </div>
                        <div className="flex items-center gap-3">
                            {step === 1 && (
                                <>
                                    <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors">
                                        {commonActions.cancel}
                                    </button>
                                    <button
                                        onClick={handleGoToStep2}
                                        disabled={!selectedSourceId || isLoadingSource}
                                        className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary/90 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
                                    >
                                        {isLoadingSource ? <Loader2 size={14} className="animate-spin" /> : null}
                                        {isLoadingSource ? copy.loadingSourceSeries : commonActions.next}
                                        <ArrowRight size={14} />
                                    </button>
                                </>
                            )}
                            {step === 2 && (
                                <>
                                    <button
                                        onClick={() => setStep(1)}
                                        className="flex items-center gap-1 px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
                                    >
                                        <ArrowLeft size={14} />
                                        {commonActions.back}
                                    </button>
                                    <button
                                        onClick={() => setStep(3)}
                                        disabled={selectedAssetIds.size === 0}
                                        className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary/90 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
                                    >
                                        {commonActions.next}
                                        <ArrowRight size={14} />
                                    </button>
                                </>
                            )}
                            {step === 3 && (
                                <>
                                    <button
                                        onClick={() => setStep(2)}
                                        disabled={isImporting}
                                        className="flex items-center gap-1 px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors disabled:opacity-50"
                                    >
                                        <ArrowLeft size={14} />
                                        {commonActions.back}
                                    </button>
                                    <button
                                        onClick={handleImport}
                                        disabled={isImporting}
                                        className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-green-600 to-blue-600 hover:from-green-500 hover:to-blue-500 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
                                    >
                                        {isImporting ? (
                                            <>
                                                <Loader2 size={14} className="animate-spin" />
                                                {copy.importing}
                                            </>
                                        ) : (
                                            <>
                                                <Download size={14} />
                                                {copy.confirmImport}
                                            </>
                                        )}
                                    </button>
                                </>
                            )}
                        </div>
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
