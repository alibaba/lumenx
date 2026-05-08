import React, { useState, useEffect, useRef } from 'react';
import NextImage from 'next/image';
import type { VideoTask } from '@/store/projectStore';
import { Trash2, Check, Layers, X, Play, RefreshCw } from 'lucide-react';
import { getAssetUrl } from '@/lib/utils';
import { zhCN } from '@/lib/i18n';

interface VideoVariantSelectorProps {
    videos: VideoTask[];
    onSelect?: (videoId: string) => void;
    onDelete: (videoId: string) => void;
    onGenerate: (duration: number) => void;
    isGenerating: boolean;
    className?: string;
    aspectRatio?: string;
}

export const VideoVariantSelector: React.FC<VideoVariantSelectorProps> = ({
    videos = [],
    onSelect,
    onDelete,
    onGenerate,
    isGenerating,
    className = "",
    aspectRatio = "16:9"
}) => {
    const copy = zhCN.videoVariantSelector;
    const [duration, setDuration] = useState(5);
    const [selectedVideoId, setSelectedVideoId] = useState<string | null>(null);
    const videoRef = useRef<HTMLVideoElement>(null);

    // Auto-select latest completed video if none selected
    useEffect(() => {
        if (!selectedVideoId && videos.length > 0) {
            // Prefer completed videos
            const completed = videos.filter(v => v.status === 'completed');
            if (completed.length > 0) {
                // Sort by created_at desc
                completed.sort((a, b) => b.created_at - a.created_at);
                setSelectedVideoId(completed[0].id);
            } else {
                // Or just the latest one
                const sorted = [...videos].sort((a, b) => b.created_at - a.created_at);
                setSelectedVideoId(sorted[0].id);
            }
        }
    }, [videos, selectedVideoId]);

    const selectedVideo = videos.find(v => v.id === selectedVideoId);

    const getFullUrl = (url: string) => {
        return getAssetUrl(url);
    };

    const displayUrl = selectedVideo?.video_url ? getFullUrl(selectedVideo.video_url) : null;

    const getAspectRatioClass = () => {
        switch (aspectRatio) {
            case "16:9": return "aspect-video";
            case "1:1": return "aspect-square";
            case "9:16": return "aspect-[9/16]";
            default: return "aspect-video";
        }
    };

    return (
        <div className={`flex flex-col gap-4 ${className}`}>
            {/* Main Viewer */}
            <div
                className={`relative w-full ${getAspectRatioClass()} bg-gray-900 rounded-lg overflow-hidden border border-gray-700 group`}
            >
                {displayUrl ? (
                    <video
                        ref={videoRef}
                        src={displayUrl}
                        className="w-full h-full object-contain"
                        controls
                        loop
                        playsInline
                    />
                ) : (
                    <div className="w-full h-full flex items-center justify-center text-gray-500 flex-col gap-2">
                        {isGenerating ? (
                            <>
                                <RefreshCw className="animate-spin" size={24} />
                                <span>{copy.generatingVideo}</span>
                            </>
                        ) : (
                            <span>{copy.noVideoGenerated}</span>
                        )}
                    </div>
                )}

                {/* Overlay Actions */}
                <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex gap-2 z-10">
                    {selectedVideo && (
                        <button
                            onClick={(e) => { e.stopPropagation(); onDelete(selectedVideo.id); }}
                            className="p-2 bg-red-500/80 hover:bg-red-600 text-white rounded-full backdrop-blur-sm"
                            title={copy.deleteVideo}
                        >
                            <Trash2 size={16} />
                        </button>
                    )}
                </div>
            </div>

            {/* Controls & Filmstrip */}
            <div className="flex flex-col gap-3">
                {/* Generation Controls */}
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 bg-gray-800 rounded-lg p-1 border border-gray-700">
                        <span className="text-xs text-gray-400 px-2">{copy.duration}</span>
                        {[5].map(d => (
                            <button
                                key={d}
                                onClick={() => setDuration(d)}
                                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${duration === d
                                    ? 'bg-blue-600 text-white'
                                    : 'text-gray-400 hover:text-white hover:bg-gray-700'
                                    }`}
                            >
                                {d}s
                            </button>
                        ))}
                    </div>

                    <button
                        onClick={() => onGenerate(duration)}
                        disabled={isGenerating}
                        className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${isGenerating
                            ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                            : 'bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white shadow-lg shadow-blue-500/20'
                            }`}
                    >
                        <Layers size={16} />
                        {isGenerating ? copy.generating : copy.generateVideo}
                    </button>
                </div>

                {/* Variants Filmstrip */}
                {videos.length > 0 && (
                    <div className="relative">
                        <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent snap-x">
                            {videos.map((video) => {
                                const isSelected = video.id === selectedVideoId;
                                // Use image_url (snapshot) as thumbnail
                                const thumbUrl = getFullUrl(video.image_url);

                                return (
                                    <div
                                        key={video.id}
                                        className={`
                                            relative flex-shrink-0 w-24 h-16 rounded-md overflow-hidden border-2 transition-all snap-start group/variant cursor-pointer
                                            ${isSelected ? 'border-blue-500 ring-2 ring-blue-500/30' : 'border-transparent hover:border-gray-500'}
                                        `}
                                        onClick={() => {
                                            setSelectedVideoId(video.id);
                                            onSelect?.(video.id);
                                        }}
                                    >
                                        {/* Thumbnail */}
                                        {thumbUrl ? (
                                            <NextImage
                                                src={thumbUrl}
                                                alt={copy.thumbnailAlt}
                                                fill
                                                sizes="96px"
                                                unoptimized
                                                className="w-full h-full object-cover opacity-70 group-hover/variant:opacity-100 transition-opacity"
                                            />
                                        ) : (
                                            <div className="h-full w-full bg-white/5" />
                                        )}

                                        {/* Status Indicator */}
                                        <div className="absolute inset-0 flex items-center justify-center">
                                            {video.status === 'processing' || video.status === 'pending' ? (
                                                <RefreshCw size={16} className="text-white animate-spin" />
                                            ) : video.status === 'failed' ? (
                                                <X size={16} className="text-red-500" />
                                            ) : (
                                                <Play size={16} className="text-white drop-shadow-md" />
                                            )}
                                        </div>

                                        {/* Selected indicator */}
                                        {isSelected && (
                                            <div className="absolute top-1 left-1 bg-blue-500 rounded-full p-0.5">
                                                <Check size={10} className="text-white" />
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
