"use client";

import React, { useState, useEffect, useRef } from 'react';
import { ImageAsset, ImageVariant } from '@/store/projectStore';
import { Trash2, Check, ChevronLeft, ChevronRight, Layers, X, Maximize2, Star } from 'lucide-react';
import { API_URL } from '@/lib/api';
import { getAssetUrl } from '@/lib/utils';
import { useTranslations } from "next-intl";

interface VariantSelectorProps {
    asset: ImageAsset | undefined;
    currentImageUrl?: string; // Fallback/Legacy URL
    onSelect: (variantId: string) => void;
    onDelete: (variantId: string) => void;
    onFavorite?: (variantId: string, isFavorited: boolean) => void;
    onGenerate: (batchSize: number) => void;
    onBatchSizeChange?: (batchSize: number) => boolean | void;
    isGenerating: boolean;
    generateDisabled?: boolean;
    generatingBatchSize?: number; // Persisted batch size from parent/store
    className?: string;
    aspectRatio?: string; // e.g., "9:16", "16:9", "1:1"
}

// Use the API_URL constant for consistent behavior
const getApiBaseUrl = () => API_URL;

export const VariantSelector: React.FC<VariantSelectorProps> = ({
    asset,
    currentImageUrl,
    onSelect,
    onDelete,
    onFavorite,
    onGenerate,
    onBatchSizeChange,
    isGenerating,
    generateDisabled = false,
    generatingBatchSize: propGeneratingBatchSize,
    className = "",
    aspectRatio = "9:16"
}) => {
    const t = useTranslations("assets");
    const [batchSize, setBatchSize] = useState(1);
    const [localGeneratingBatchSize, setLocalGeneratingBatchSize] = useState(1); // Track the batch size when generation started locally
    const [zoomedImage, setZoomedImage] = useState<string | null>(null);
    const prevIsGenerating = useRef(isGenerating);

    // Automatically save batchSize when generation starts
    useEffect(() => {
        // Detect when isGenerating changes from false to true (generation just started)
        if (isGenerating && !prevIsGenerating.current) {
            setLocalGeneratingBatchSize(batchSize);
        }
        prevIsGenerating.current = isGenerating;
    }, [isGenerating, batchSize]);

    // Use prop if provided (for persistence), otherwise use local state
    const displayGeneratingBatchSize = propGeneratingBatchSize || localGeneratingBatchSize;

    // Determine the image to display
    const selectedVariant = asset?.variants?.find(v => v.id === asset.selected_id);
    const apiBase = getApiBaseUrl();
    const displayUrl = selectedVariant ?
        getAssetUrl(selectedVariant.url) :
        getAssetUrl(currentImageUrl);

    const variants = asset?.variants || [];

    // Helper to calculate aspect ratio class
    const getAspectRatioClass = () => {
        switch (aspectRatio) {
            case "16:9": return "aspect-video";
            case "1:1": return "aspect-square";
            case "9:16": return "aspect-[9/16]";
            default: return "aspect-[9/16]";
        }
    };

    return (
        <div className={`flex flex-col gap-4 ${className}`}>
            {/* Main Viewer */}
            <div
                className={`relative w-full ${getAspectRatioClass()} bg-elevated rounded-lg overflow-hidden border border-glass-border group cursor-pointer`}
                onClick={() => displayUrl && setZoomedImage(displayUrl)}
            >
                {displayUrl ? (
                    <>
                        <img
                            src={displayUrl}
                            alt="Selected Variant"
                            className="w-full h-full object-contain"
                        />
                        {/* Zoom hint */}
                        <div className="absolute bottom-2 left-2 opacity-0 group-hover:opacity-100 transition-opacity">
                            <div className="flex items-center gap-1 px-2 py-1 bg-overlay rounded-md backdrop-blur-sm">
                                <Maximize2 size={12} className="text-foreground/80" />
                                <span className="text-xs text-foreground/80">{t("clickToZoom")}</span>
                            </div>
                        </div>
                    </>
                ) : (
                    <div className="w-full h-full flex items-center justify-center text-text-muted">
                        {t("noImageGenerated")}
                    </div>
                )}

                {/* Overlay Actions */}
                <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
                    {selectedVariant && (
                        <button
                            onClick={(e) => { e.stopPropagation(); onDelete(selectedVariant.id); }}
                            className="p-2 bg-red-500/80 hover:bg-red-600 text-white rounded-full backdrop-blur-sm"
                            title={t("deleteVariant")}
                        >
                            <Trash2 size={16} />
                        </button>
                    )}
                </div>

                {isGenerating && (
                    <div className="absolute inset-0 bg-overlay flex items-center justify-center z-10 backdrop-blur-sm">
                        <div className="flex flex-col items-center gap-3">
                            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-white"></div>
                            <span className="text-white font-medium">{t("generatingVariants", { count: displayGeneratingBatchSize })}</span>
                        </div>
                    </div>
                )}
            </div>

            {/* Controls & Filmstrip */}
            <div className="flex flex-col gap-3">
                {/* Generation Controls */}
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 bg-elevated rounded-lg p-1 border border-glass-border">
                        {[1, 2, 3, 4].map(size => (
                            <button
                                key={size}
                                onClick={() => {
                                    // The owner can reject an event retained by an exiting modal.
                                    if (size !== batchSize && onBatchSizeChange?.(size) !== false) setBatchSize(size);
                                }}
                                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${batchSize === size
                                    ? 'bg-blue-600 text-white'
                                    : 'text-text-secondary hover:text-foreground hover:bg-hover-bg'
                                    }`}
                            >
                                x{size}
                            </button>
                        ))}
                    </div>

                    <button
                        onClick={() => onGenerate(batchSize)}
                        disabled={isGenerating || generateDisabled}
                        className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${isGenerating
                            ? 'bg-gray-700 text-text-secondary cursor-not-allowed'
                            : 'bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white shadow-lg shadow-blue-500/20'
                            }`}
                    >
                        <Layers size={16} />
                        {t("generate")}
                    </button>
                </div>

                {/* Variants Filmstrip */}
                {variants.length > 0 && (
                    <div className="relative">
                        <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent snap-x">
                            {variants.map((variant) => {
                                const isSelected = variant.id === asset?.selected_id;
                                const isFavorited = (variant as any).is_favorited || false;
                                const url = getAssetUrl(variant.url);

                                return (
                                    <div
                                        key={variant.id}
                                        className={`
                                            relative flex-shrink-0 w-20 h-20 rounded-md overflow-hidden border-2 transition-all snap-start group/variant
                                            ${isSelected ? 'border-blue-500 ring-2 ring-blue-500/30' : isFavorited ? 'border-yellow-500/50' : 'border-transparent hover:border-gray-500'}
                                        `}
                                    >
                                        {/* Clickable image area */}
                                        <img
                                            src={url}
                                            alt="Variant"
                                            loading="lazy"
                                            className="w-full h-full object-cover cursor-pointer"
                                            onClick={() => onSelect(variant.id)}
                                        />

                                        {/* Selected indicator */}
                                        {isSelected && (
                                            <div className="absolute top-1 left-1 bg-blue-500 rounded-full p-0.5">
                                                <Check size={10} className="text-white" />
                                            </div>
                                        )}

                                        {/* Favorite button */}
                                        {onFavorite && (
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    onFavorite(variant.id, !isFavorited);
                                                }}
                                                className={`absolute top-1 right-1 p-1 rounded-full transition-all ${isFavorited
                                                    ? 'bg-yellow-500 text-white'
                                                    : 'bg-overlay/50 text-text-secondary opacity-0 group-hover/variant:opacity-100 hover:bg-yellow-500 hover:text-white'
                                                    }`}
                                                title={isFavorited ? t("clickToUnfavorite") : t("clickToFavorite")}
                                            >
                                                <Star size={12} fill={isFavorited ? 'currentColor' : 'none'} />
                                            </button>
                                        )}

                                        {/* Delete button - only show if NOT favorited */}
                                        {!isFavorited && (
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    if (confirm(t("confirmDeleteVariant"))) {
                                                        onDelete(variant.id);
                                                    }
                                                }}
                                                className="absolute bottom-1 right-1 p-1 bg-red-500/80 hover:bg-red-500 rounded-full text-white opacity-0 group-hover/variant:opacity-100 transition-all"
                                                title={t("deleteVariant")}
                                            >
                                                <Trash2 size={10} />
                                            </button>
                                        )}

                                        {/* Favorited protection indicator */}
                                        {isFavorited && (
                                            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-yellow-900/80 to-transparent py-1 px-1">
                                                <span className="text-[0.5rem] text-yellow-200 font-medium">{t("protected")}</span>
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}
            </div>

            {/* Lightbox Modal */}
            {zoomedImage && (
                <div
                    className="fixed inset-0 z-[100] bg-overlay flex items-center justify-center p-8"
                    onClick={() => setZoomedImage(null)}
                >
                    <button
                        className="absolute top-4 right-4 p-3 bg-hover-bg hover:bg-hover-bg rounded-full text-foreground transition-colors"
                        onClick={() => setZoomedImage(null)}
                    >
                        <X size={24} />
                    </button>
                    <img
                        src={zoomedImage}
                        alt="Zoomed View"
                        className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
                        onClick={(e) => e.stopPropagation()}
                    />
                </div>
            )}
        </div>
    );
};
