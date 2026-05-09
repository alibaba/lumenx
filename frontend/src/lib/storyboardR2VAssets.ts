type AssetVariant = {
    id?: string;
    url?: string;
    video_url?: string;
};

type ImageAssetLike = {
    selected_id?: string | null;
    selected_image_id?: string | null;
    variants?: AssetVariant[];
    image_variants?: AssetVariant[];
};

type VideoAssetLike = {
    selected_video_id?: string | null;
    video_variants?: AssetVariant[];
};

type LegacyVideoTaskLike = {
    id?: string;
    video_url?: string;
    url?: string;
    status?: string;
};

export type StoryboardR2VCharacterLike = {
    id?: string;
    name: string;
    full_body?: ImageAssetLike & VideoAssetLike;
    head_shot?: ImageAssetLike & VideoAssetLike;
    full_body_asset?: ImageAssetLike;
    headshot_asset?: ImageAssetLike;
    video_assets?: LegacyVideoTaskLike[];
    full_body_image_url?: string;
    avatar_url?: string;
};

export type StoryboardR2VAssetLike = {
    id?: string;
    name: string;
    image_asset?: ImageAssetLike;
    video_assets?: LegacyVideoTaskLike[];
    image_url?: string;
    video_url?: string;
};

export type StoryboardR2VResolvedRefs = {
    imageUrls: string[];
    videoUrls: string[];
    missing: string[];
};

export type StoryboardR2VReferenceMode = "image" | "video";

export type StoryboardR2VReferenceIssue =
    | { type: "missing_assets"; refs: string[] }
    | { type: "missing_image_refs" }
    | { type: "missing_video_refs" }
    | { type: "too_many_image_refs"; count: number; max: number }
    | { type: "too_many_video_refs"; count: number; max: number };

export type StoryboardR2VReferenceValidation = {
    mode: StoryboardR2VReferenceMode;
    requiredUrls: string[];
    issues: StoryboardR2VReferenceIssue[];
    canGenerate: boolean;
};

export type StoryboardR2VPromptInsertion = {
    prompt: string;
    cursor: number;
};

export type StoryboardR2VAssetReadiness = {
    hasImageRef: boolean;
    hasVideoRef: boolean;
};

const ASSET_TAG_PATTERN = /\[(character\d+|scene|prop):([^\]]+)\]/g;

const firstDefinedUrl = (variant?: AssetVariant): string | undefined => {
    return variant?.url || variant?.video_url;
};

const selectedImageUrl = (asset?: ImageAssetLike | null): string | undefined => {
    if (!asset) return undefined;
    const selectedId = asset.selected_image_id || asset.selected_id;
    const variants = asset.image_variants || asset.variants || [];
    const selected = selectedId ? variants.find((variant) => variant.id === selectedId) : undefined;
    return firstDefinedUrl(selected) || firstDefinedUrl(variants[0]);
};

const selectedVideoUrl = (asset?: VideoAssetLike | null): string | undefined => {
    if (!asset) return undefined;
    const variants = asset.video_variants || [];
    const selected = asset.selected_video_id
        ? variants.find((variant) => variant.id === asset.selected_video_id)
        : undefined;
    return firstDefinedUrl(selected) || firstDefinedUrl(variants[0]);
};

const selectedLegacyVideoUrl = (tasks?: LegacyVideoTaskLike[]): string | undefined => {
    if (!tasks?.length) return undefined;
    const completed = tasks.find((task) => task.status === "completed" && (task.video_url || task.url));
    const fallback = tasks.find((task) => task.video_url || task.url);
    return completed?.video_url || completed?.url || fallback?.video_url || fallback?.url;
};

const characterImageUrl = (character?: StoryboardR2VCharacterLike): string | undefined => {
    if (!character) return undefined;
    return (
        selectedImageUrl(character.full_body) ||
        selectedImageUrl(character.full_body_asset) ||
        selectedImageUrl(character.head_shot) ||
        selectedImageUrl(character.headshot_asset) ||
        character.full_body_image_url ||
        character.avatar_url
    );
};

const characterVideoUrl = (character?: StoryboardR2VCharacterLike): string | undefined => {
    if (!character) return undefined;
    return (
        selectedVideoUrl(character.full_body) ||
        selectedVideoUrl(character.head_shot) ||
        selectedLegacyVideoUrl(character.video_assets)
    );
};

const assetImageUrl = (asset?: StoryboardR2VAssetLike): string | undefined => {
    if (!asset) return undefined;
    return selectedImageUrl(asset.image_asset) || asset.image_url;
};

const assetVideoUrl = (asset?: StoryboardR2VAssetLike): string | undefined => {
    if (!asset) return undefined;
    return selectedLegacyVideoUrl(asset.video_assets) || asset.video_url;
};

export const getStoryboardR2VAssetReadiness = (
    item: StoryboardR2VCharacterLike | StoryboardR2VAssetLike,
    type: "character" | "scene" | "prop"
): StoryboardR2VAssetReadiness => {
    if (type === "character") {
        const character = item as StoryboardR2VCharacterLike;
        return {
            hasImageRef: !!characterImageUrl(character),
            hasVideoRef: !!characterVideoUrl(character),
        };
    }

    const asset = item as StoryboardR2VAssetLike;
    return {
        hasImageRef: !!assetImageUrl(asset),
        hasVideoRef: !!assetVideoUrl(asset),
    };
};

const pushUnique = (values: string[], value?: string) => {
    if (value && !values.includes(value)) {
        values.push(value);
    }
};

export const stripStoryboardR2VTags = (prompt: string): string => {
    return prompt.replace(ASSET_TAG_PATTERN, (_, __, name: string) => name).replace(/\s+/g, " ").trim();
};

export const insertStoryboardR2VTag = (
    prompt: string,
    tag: string,
    start: number,
    end: number
): StoryboardR2VPromptInsertion => {
    const before = prompt.slice(0, start);
    const after = prompt.slice(end);
    const prefix = before.length > 0 && !/\s$/.test(before) ? " " : "";
    const suffix = after.length > 0 && !/^\s/.test(after) ? " " : "";

    return {
        prompt: `${before}${prefix}${tag}${suffix}${after}`,
        cursor: before.length + prefix.length + tag.length,
    };
};

export const resolveStoryboardR2VRefs = (
    prompt: string,
    characters: StoryboardR2VCharacterLike[],
    scenes: StoryboardR2VAssetLike[],
    props: StoryboardR2VAssetLike[]
): StoryboardR2VResolvedRefs => {
    const imageUrls: string[] = [];
    const videoUrls: string[] = [];
    const missing: string[] = [];

    const tagPattern = new RegExp(ASSET_TAG_PATTERN);
    let match: RegExpExecArray | null;

    while ((match = tagPattern.exec(prompt)) !== null) {
        const [, type, name] = match;
        if (type.startsWith("character")) {
            const character = characters.find((candidate) => candidate.name === name);
            pushUnique(imageUrls, characterImageUrl(character));
            pushUnique(videoUrls, characterVideoUrl(character));
            if (!character) missing.push(`${type}:${name}`);
            continue;
        }

        if (type === "scene") {
            const scene = scenes.find((candidate) => candidate.name === name);
            pushUnique(imageUrls, assetImageUrl(scene));
            pushUnique(videoUrls, assetVideoUrl(scene));
            if (!scene) missing.push(`${type}:${name}`);
            continue;
        }

        if (type === "prop") {
            const prop = props.find((candidate) => candidate.name === name);
            pushUnique(imageUrls, assetImageUrl(prop));
            pushUnique(videoUrls, assetVideoUrl(prop));
            if (!prop) missing.push(`${type}:${name}`);
        }
    }

    return { imageUrls, videoUrls, missing };
};

export const validateStoryboardR2VRefs = (
    refs: StoryboardR2VResolvedRefs,
    mode: StoryboardR2VReferenceMode,
    maxRefs?: number
): StoryboardR2VReferenceValidation => {
    const requiredUrls = mode === "image" ? refs.imageUrls : refs.videoUrls;
    const issues: StoryboardR2VReferenceIssue[] = [];

    if (refs.missing.length > 0) {
        issues.push({ type: "missing_assets", refs: refs.missing });
    }

    if (requiredUrls.length === 0) {
        issues.push({ type: mode === "image" ? "missing_image_refs" : "missing_video_refs" });
    }

    if (maxRefs && requiredUrls.length > maxRefs) {
        issues.push({
            type: mode === "image" ? "too_many_image_refs" : "too_many_video_refs",
            count: requiredUrls.length,
            max: maxRefs,
        });
    }

    return {
        mode,
        requiredUrls,
        issues,
        canGenerate: issues.length === 0,
    };
};
