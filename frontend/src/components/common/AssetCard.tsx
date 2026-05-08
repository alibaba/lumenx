"use client";

import NextImage from "next/image";
import { Image as ImageIcon } from "lucide-react";
import type { Character, Scene, Prop } from "@/store/projectStore";
import { getAssetUrl } from "@/lib/utils";

type AssetTab = "characters" | "scenes" | "props";

interface AssetCardProps {
  asset: Character | Scene | Prop;
  type: AssetTab;
}

function getImageUrl(asset: Character | Scene | Prop, type: AssetTab): string | undefined {
  if (type === "characters") {
    const char = asset as Character;
    if (char.full_body_asset?.variants?.length) {
      const selected = char.full_body_asset.variants.find(
        (v) => v.id === char.full_body_asset?.selected_id
      );
      return selected?.url || char.full_body_asset.variants[0]?.url;
    }
    return char.image_url || char.full_body_image_url;
  }
  if (type === "scenes") {
    const scene = asset as Scene;
    if (scene.image_asset?.variants?.length) {
      const selected = scene.image_asset.variants.find(
        (v) => v.id === scene.image_asset?.selected_id
      );
      return selected?.url || scene.image_asset.variants[0]?.url;
    }
    return scene.image_url;
  }
  const prop = asset as Prop;
  if (prop.image_asset?.variants?.length) {
    const selected = prop.image_asset.variants.find(
      (v) => v.id === prop.image_asset?.selected_id
    );
    return selected?.url || prop.image_asset.variants[0]?.url;
  }
  return prop.image_url;
}

export default function AssetCard({ asset, type }: AssetCardProps) {
  const imageUrl = getImageUrl(asset, type);
  const displayUrl = getAssetUrl(imageUrl);

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="aspect-square bg-gray-800/50 flex items-center justify-center overflow-hidden">
        {displayUrl ? (
          <div className="relative h-full w-full">
            <NextImage
              src={displayUrl}
              alt={asset.name}
              fill
              sizes="(max-width: 768px) 50vw, 20vw"
              unoptimized
              className="object-cover"
            />
          </div>
        ) : (
          <ImageIcon size={32} className="text-gray-600" />
        )}
      </div>
      <div className="p-3">
        <h4 className="text-sm font-medium text-white truncate">{asset.name}</h4>
        {asset.description && (
          <p className="text-xs text-gray-400 mt-1 line-clamp-2">{asset.description}</p>
        )}
      </div>
    </div>
  );
}
