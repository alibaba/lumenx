export function getSelectedVariantUrl(asset: any): string | null {
  if (!asset || !asset.variants || asset.variants.length === 0) {
    return null;
  }

  if (asset.selected_id) {
    const selectedVariant = asset.variants.find((variant: any) => variant.id === asset.selected_id);
    if (selectedVariant?.url) {
      return selectedVariant.url;
    }
  }

  return asset.variants[0]?.url || null;
}

export function getSelectedFrameReference(frame: any): string | null {
  if (frame?.rendered_image_asset?.selected_id) {
    const selectedVariant = frame.rendered_image_asset.variants?.find(
      (variant: any) => variant.id === frame.rendered_image_asset.selected_id,
    );
    if (selectedVariant?.url) {
      return selectedVariant.url;
    }
  }

  return frame?.rendered_image_url || frame?.image_url || null;
}

export function getPreviousSameSceneFrame(project: any, frame: any) {
  const frameIndex = project?.frames?.findIndex((item: any) => item.id === frame?.id) ?? -1;
  if (frameIndex <= 0) {
    return null;
  }

  const previousFrame = project.frames?.[frameIndex - 1];
  return previousFrame?.scene_id === frame?.scene_id ? previousFrame : null;
}

export function getNextSameSceneFrame(project: any, frame: any) {
  const frameIndex = project?.frames?.findIndex((item: any) => item.id === frame?.id) ?? -1;
  if (frameIndex < 0 || frameIndex >= (project?.frames?.length ?? 0) - 1) {
    return null;
  }

  const nextFrame = project.frames?.[frameIndex + 1];
  return nextFrame?.scene_id === frame?.scene_id ? nextFrame : null;
}

export function getArtDirectionPromptPrefix(styleConfig: any): string {
  return [styleConfig?.positive_prompt, styleConfig?.moodboard_notes]
    .filter((value) => typeof value === "string" && value.trim().length > 0)
    .join(", ");
}

export function getArtDirectionReferenceImages(styleConfig: any): string[] {
  if (!Array.isArray(styleConfig?.reference_images)) {
    return [];
  }

  return styleConfig.reference_images.filter((value: unknown): value is string =>
    typeof value === "string" && value.trim().length > 0,
  );
}

function pushUnique(urls: string[], candidate?: string | null) {
  if (candidate && !urls.includes(candidate)) {
    urls.push(candidate);
  }
}

export interface StoryboardReferencePreviewItem {
  id: string;
  name: string;
  type: "continuity" | "scene" | "character" | "prop" | "style";
  url: string | null;
  required: boolean;
  locked: boolean;
  status: "ready" | "missing";
  source: string;
}

function buildPreviewItem(
  item: Omit<StoryboardReferencePreviewItem, "status">,
): StoryboardReferencePreviewItem {
  return {
    ...item,
    status: item.url ? "ready" : "missing",
  };
}

export function buildStoryboardReferencePreview(
  project: any,
  frame: any,
  options?: {
    continuityLock?: boolean;
    includeStyleReferences?: boolean;
  },
): StoryboardReferencePreviewItem[] {
  const composition = frame?.composition_data || {};
  const bindings = composition.reference_binding_version ? composition : {};
  const continuityLock = options?.continuityLock ?? composition.continuity_lock ?? true;
  const previewItems: StoryboardReferencePreviewItem[] = [];

  const previousSameSceneFrame = getPreviousSameSceneFrame(project, frame);
  const continuityUrl = continuityLock ? getSelectedFrameReference(previousSameSceneFrame) : null;
  if (continuityLock) {
    previewItems.push(buildPreviewItem({
      id: previousSameSceneFrame?.id || "previous-frame",
      name: previousSameSceneFrame ? `上一帧 #${previousSameSceneFrame.id?.slice(0, 8)}` : "上一帧连续参考",
      type: "continuity",
      url: continuityUrl,
      required: Boolean(bindings.continuity?.prefer_previous_frame),
      locked: true,
      source: "同场景连续镜头",
    }));
  }

  if (frame?.scene_id) {
    const scene = project?.scenes?.find((sceneItem: any) => sceneItem.id === frame.scene_id);
    previewItems.push(buildPreviewItem({
      id: scene?.id || frame.scene_id,
      name: scene?.name || "未知场景",
      type: "scene",
      url: getSelectedVariantUrl(scene?.image_asset) || scene?.image_url || null,
      required: bindings.scene?.required !== false,
      locked: Boolean(scene?.locked || bindings.scene?.lock),
      source: "场景主参考",
    }));
  }

  (frame?.character_ids || []).forEach((characterId: string) => {
    const character = project?.characters?.find((item: any) => item.id === characterId);
    previewItems.push(buildPreviewItem({
      id: character?.id || characterId,
      name: character?.name || "未知角色",
      type: "character",
      url:
        getSelectedVariantUrl(character?.three_view_asset)
        || getSelectedVariantUrl(character?.full_body_asset)
        || getSelectedVariantUrl(character?.headshot_asset)
        || character?.three_view_image_url
        || character?.full_body_image_url
        || character?.headshot_image_url
        || character?.avatar_url
        || character?.image_url
        || null,
      required: true,
      locked: Boolean(character?.locked),
      source: "角色主参考",
    }));
  });

  (frame?.prop_ids || []).forEach((propId: string) => {
    const prop = project?.props?.find((item: any) => item.id === propId);
    previewItems.push(buildPreviewItem({
      id: prop?.id || propId,
      name: prop?.name || "未知道具",
      type: "prop",
      url: getSelectedVariantUrl(prop?.image_asset) || prop?.image_url || null,
      required: true,
      locked: Boolean(prop?.locked),
      source: "道具主参考",
    }));
  });

  if (options?.includeStyleReferences !== false) {
    getArtDirectionReferenceImages(project?.art_direction?.style_config).forEach((url: string, index: number) => {
      previewItems.push(buildPreviewItem({
        id: `style-${index}`,
        name: project?.art_direction?.style_config?.name || "风格参考",
        type: "style",
        url,
        required: false,
        locked: Boolean(bindings.style?.lock),
        source: "美术指导参考",
      }));
    });
  }

  return previewItems;
}

export function buildStoryboardCompositionData(
  project: any,
  frame: any,
  options?: {
    continuityLock?: boolean;
    includeStyleReferences?: boolean;
  },
) {
  const continuityLock = options?.continuityLock ?? true;
  const baseComposition = frame?.composition_data || {};
  const referenceImageUrls: string[] = [];

  if (typeof baseComposition.reference_image_url === "string") {
    pushUnique(referenceImageUrls, baseComposition.reference_image_url);
  }

  if (Array.isArray(baseComposition.reference_image_urls)) {
    baseComposition.reference_image_urls.forEach((url: unknown) => {
      if (typeof url === "string") {
        pushUnique(referenceImageUrls, url);
      }
    });
  }

  if (frame?.scene_id) {
    const scene = project?.scenes?.find((item: any) => item.id === frame.scene_id);
    pushUnique(referenceImageUrls, getSelectedVariantUrl(scene?.image_asset) || scene?.image_url);
  }

  if (Array.isArray(frame?.character_ids)) {
    frame.character_ids.forEach((characterId: string) => {
      const character = project?.characters?.find((item: any) => item.id === characterId);
      pushUnique(
        referenceImageUrls,
        getSelectedVariantUrl(character?.three_view_asset)
          || getSelectedVariantUrl(character?.full_body_asset)
          || getSelectedVariantUrl(character?.headshot_asset)
          || character?.three_view_image_url
          || character?.full_body_image_url
          || character?.headshot_image_url
          || character?.avatar_url
          || character?.image_url,
      );
    });
  }

  if (Array.isArray(frame?.prop_ids)) {
    frame.prop_ids.forEach((propId: string) => {
      const prop = project?.props?.find((item: any) => item.id === propId);
      pushUnique(referenceImageUrls, getSelectedVariantUrl(prop?.image_asset) || prop?.image_url);
    });
  }

  const previousSameSceneFrame = getPreviousSameSceneFrame(project, frame);
  const continuitySourceUrl = continuityLock ? getSelectedFrameReference(previousSameSceneFrame) : null;
  if (continuitySourceUrl) {
    referenceImageUrls.unshift(continuitySourceUrl);
  }

  if (options?.includeStyleReferences !== false) {
    getArtDirectionReferenceImages(project?.art_direction?.style_config).forEach((url: string) => {
      pushUnique(referenceImageUrls, url);
    });
  }

  const dedupedReferenceUrls = referenceImageUrls.filter((value, index, all) => all.indexOf(value) === index);
  const referencePreview = buildStoryboardReferencePreview(project, frame, options);

  return {
    ...baseComposition,
    character_ids: frame?.character_ids || [],
    prop_ids: frame?.prop_ids || [],
    scene_id: frame?.scene_id,
    continuity_lock: continuityLock,
    continuity_source_frame_id: continuitySourceUrl ? previousSameSceneFrame?.id : undefined,
    reference_image_url: dedupedReferenceUrls[0] || undefined,
    reference_image_urls: dedupedReferenceUrls,
    reference_preview: referencePreview,
  };
}
