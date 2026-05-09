"use client";

import type { StoryboardR2VAssetLike, StoryboardR2VCharacterLike } from "@/lib/storyboardR2VAssets";

interface AssetChipBarProps {
    characters: StoryboardR2VCharacterLike[];
    scenes: StoryboardR2VAssetLike[];
    props: StoryboardR2VAssetLike[];
    onInsertAsset: (type: string, name: string) => void;
}

export default function AssetChipBar({ characters, scenes, props, onInsertAsset }: AssetChipBarProps) {
    if (characters.length === 0 && scenes.length === 0 && props.length === 0) {
        return null;
    }

    return (
        <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-hide py-1">
            {characters.map((c, i) => (
                <button
                    key={c.id}
                    onClick={() => onInsertAsset(`character${i + 1}`, c.name)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-blue-400/30 bg-blue-400/10 text-[11px] text-foreground hover:bg-blue-400/20 transition-colors shrink-0"
                >
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                    {c.name}
                </button>
            ))}
            {scenes.map((s) => (
                <button
                    key={s.id}
                    onClick={() => onInsertAsset("scene", s.name)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-green-400/30 bg-green-400/10 text-[11px] text-foreground hover:bg-green-400/20 transition-colors shrink-0"
                >
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                    {s.name}
                </button>
            ))}
            {props.map((p) => (
                <button
                    key={p.id}
                    onClick={() => onInsertAsset("prop", p.name)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-orange-400/30 bg-orange-400/10 text-[11px] text-foreground hover:bg-orange-400/20 transition-colors shrink-0"
                >
                    <span className="w-1.5 h-1.5 rounded-full bg-orange-400" />
                    {p.name}
                </button>
            ))}
        </div>
    );
}
