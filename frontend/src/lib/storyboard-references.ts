interface ReferenceImageVariant {
  id: string;
  url?: string | null;
}

interface ReferenceImageAsset {
  selected_id?: string | null;
  variants?: ReferenceImageVariant[] | null;
}

interface ReferenceCompositionBindings {
  prefer_previous_frame?: boolean;
  required?: boolean;
  lock?: boolean;
}

interface ReferenceCompositionData {
  reference_binding_version?: unknown;
  reference_image_url?: unknown;
  reference_image_urls?: unknown;
  continuity_lock?: boolean;
  continuity?: ReferenceCompositionBindings;
  scene?: ReferenceCompositionBindings;
  style?: ReferenceCompositionBindings;
  [key: string]: unknown;
}

interface ReferenceFrame {
  id?: string;
  scene_id?: string | null;
  rendered_image_asset?: ReferenceImageAsset | null;
  rendered_image_url?: string | null;
  image_url?: string | null;
  composition_data?: ReferenceCompositionData | null;
  character_ids?: string[] | null;
  prop_ids?: string[] | null;
}

interface ReferenceScene {
  id: string;
  name?: string | null;
  image_asset?: ReferenceImageAsset | null;
  image_url?: string | null;
  locked?: boolean;
}

interface ReferenceCharacter {
  id: string;
  name?: string | null;
  three_view_asset?: ReferenceImageAsset | null;
  full_body_asset?: ReferenceImageAsset | null;
  headshot_asset?: ReferenceImageAsset | null;
  three_view_image_url?: string | null;
  full_body_image_url?: string | null;
  headshot_image_url?: string | null;
  avatar_url?: string | null;
  image_url?: string | null;
  locked?: boolean;
}

interface ReferenceProp {
  id: string;
  name?: string | null;
  image_asset?: ReferenceImageAsset | null;
  image_url?: string | null;
  locked?: boolean;
}

interface ReferenceStyleConfig {
  name?: string | null;
  positive_prompt?: string | null;
  moodboard_notes?: string | null;
  reference_images?: unknown;
}

interface ReferenceProject {
  frames?: ReferenceFrame[] | null;
  scenes?: ReferenceScene[] | null;
  characters?: ReferenceCharacter[] | null;
  props?: ReferenceProp[] | null;
  art_direction?: {
    style_config?: ReferenceStyleConfig | null;
  } | null;
}

export function getSelectedVariantUrl(asset?: ReferenceImageAsset | null): string | null {
  if (!asset || !asset.variants || asset.variants.length === 0) {
    return null;
  }

  if (asset.selected_id) {
    const selectedVariant = asset.variants.find((variant) => variant.id === asset.selected_id);
    if (selectedVariant?.url) {
      return selectedVariant.url;
    }
  }

  return asset.variants[0]?.url || null;
}

export function getSelectedFrameReference(frame?: ReferenceFrame | null): string | null {
  if (frame?.rendered_image_asset?.selected_id) {
    const selectedVariant = frame.rendered_image_asset.variants?.find(
      (variant) => variant.id === frame.rendered_image_asset?.selected_id,
    );
    if (selectedVariant?.url) {
      return selectedVariant.url;
    }
  }

  return frame?.rendered_image_url || frame?.image_url || null;
}

export function getPreviousSameSceneFrame(project?: ReferenceProject | null, frame?: ReferenceFrame | null) {
  if (!project?.frames || !frame?.id) {
    return null;
  }

  const frameIndex = project.frames.findIndex((item) => item.id === frame.id);
  if (frameIndex <= 0) {
    return null;
  }

  const previousFrame = project.frames?.[frameIndex - 1];
  return previousFrame?.scene_id === frame?.scene_id ? previousFrame : null;
}

export function getNextSameSceneFrame(project?: ReferenceProject | null, frame?: ReferenceFrame | null) {
  if (!project?.frames || !frame?.id) {
    return null;
  }

  const frameIndex = project.frames.findIndex((item) => item.id === frame.id);
  if (frameIndex < 0 || frameIndex >= project.frames.length - 1) {
    return null;
  }

  const nextFrame = project.frames?.[frameIndex + 1];
  return nextFrame?.scene_id === frame?.scene_id ? nextFrame : null;
}

export function getArtDirectionPromptPrefix(styleConfig?: ReferenceStyleConfig | null): string {
  return [styleConfig?.positive_prompt, styleConfig?.moodboard_notes]
    .filter((value) => typeof value === "string" && value.trim().length > 0)
    .join(", ");
}

export function getArtDirectionReferenceImages(styleConfig?: ReferenceStyleConfig | null): string[] {
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
  project?: ReferenceProject | null,
  frame?: ReferenceFrame | null,
  options?: {
    continuityLock?: boolean;
    includeStyleReferences?: boolean;
  },
): StoryboardReferencePreviewItem[] {
  const composition = frame?.composition_data || {};
  const bindings: ReferenceCompositionData = composition.reference_binding_version ? composition : {};
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
    const scene = project?.scenes?.find((sceneItem) => sceneItem.id === frame.scene_id);
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
    const character = project?.characters?.find((item) => item.id === characterId);
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
    const prop = project?.props?.find((item) => item.id === propId);
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
  project?: ReferenceProject | null,
  frame?: ReferenceFrame | null,
  options?: {
    continuityLock?: boolean;
    includeStyleReferences?: boolean;
  },
) {
  const continuityLock = options?.continuityLock ?? true;
  const baseComposition = frame?.composition_data || {};
  const preserveLegacyReferences = !baseComposition.reference_binding_version;
  const referenceImageUrls: string[] = [];

  // Managed bindings are rebuilt from current project state on every render.
  // Carrying old reference_image_urls forward can balloon the payload after repeated edits.
  if (preserveLegacyReferences) {
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
  }

  if (frame?.scene_id) {
    const scene = project?.scenes?.find((item) => item.id === frame.scene_id);
    pushUnique(referenceImageUrls, getSelectedVariantUrl(scene?.image_asset) || scene?.image_url);
  }

  if (Array.isArray(frame?.character_ids)) {
    frame.character_ids.forEach((characterId: string) => {
      const character = project?.characters?.find((item) => item.id === characterId);
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
      const prop = project?.props?.find((item) => item.id === propId);
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
