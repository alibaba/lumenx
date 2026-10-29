import {
    DEFAULT_I2V_MODEL_ID,
    VIDEO_I2V_MODELS,
} from "@/lib/modelCatalog";
import { ORDERED_PROVIDER_VIDEO_MODELS } from "@/lib/providerVideoTaskEntry";

const ORDERED_WAN3_VIDEO_MODEL_IDS = new Set<string>(ORDERED_PROVIDER_VIDEO_MODELS);
const ORDERED_WAN3_MODEL_ALIASES = new Map<string, string>([
    ["wan/wan3.0-video#video", "wan3.0-video"],
    ["wan/wan3.0-video-prime#video", "wan3.0-video-prime"],
]);

const normalizeLegacyVideoModelId = (modelId: string | null | undefined): string | undefined => {
    if (!modelId) return undefined;
    return ORDERED_WAN3_MODEL_ALIASES.get(modelId) ?? modelId;
};

export const LEGACY_VIDEO_I2V_MODELS = VIDEO_I2V_MODELS.filter((model) => {
    const normalized = normalizeLegacyVideoModelId(model.id);
    return !normalized || !ORDERED_WAN3_VIDEO_MODEL_IDS.has(normalized);
});

export const isOrderedWan3VideoModelId = (modelId: string | null | undefined): boolean => {
    const normalized = normalizeLegacyVideoModelId(modelId);
    return !!normalized && ORDERED_WAN3_VIDEO_MODEL_IDS.has(normalized);
};

export const resolveLegacyVideoSidebarModelId = (modelId: string | null | undefined): string => {
    const normalized = normalizeLegacyVideoModelId(modelId);
    if (normalized && LEGACY_VIDEO_I2V_MODELS.some((model) => model.id === normalized)) return normalized;
    return LEGACY_VIDEO_I2V_MODELS.find((model) => model.id === DEFAULT_I2V_MODEL_ID)?.id
        ?? LEGACY_VIDEO_I2V_MODELS[0]?.id
        ?? DEFAULT_I2V_MODEL_ID;
};
