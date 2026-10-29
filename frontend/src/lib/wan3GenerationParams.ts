import rawCatalog from "@/generated/modelCatalog.json";
import { getLegacyModelId } from "@/lib/modelCatalog";

type DurationConfig = { type: string; min?: number; max?: number; step?: number; supports_auto?: boolean };
type Wan3CatalogModel = {
    duration?: DurationConfig | null;
    params?: {
        resolution?: { options?: string[] };
        ratio?: { options?: string[] };
    };
};

type Wan3Catalog = { models: Record<string, Wan3CatalogModel> };

const CATALOG = rawCatalog as Wan3Catalog;
const ORDERED_PARAMS = new Set(["duration", "resolution", "ratio", "seed", "prompt_extend", "watermark", "audio"]);

export function isWan3VideoModelId(modelId: string): boolean {
    const legacy = getLegacyModelId(modelId) ?? modelId;
    return legacy === "wan3.0-video" || legacy === "wan3.0-video-prime";
}

function catalogModel(modelId: string): Wan3CatalogModel | null {
    const legacy = getLegacyModelId(modelId) ?? modelId;
    return CATALOG.models[legacy] ?? null;
}

function validDuration(value: unknown, config: DurationConfig | null | undefined): boolean {
    if (typeof value !== "number" || !Number.isFinite(value)) return false;
    if (value === -1) return !!config?.supports_auto;
    if (!config || typeof config.min !== "number" || typeof config.max !== "number" || typeof config.step !== "number") return false;
    if (value < config.min || value > config.max) return false;
    const offset = (value - config.min) / config.step;
    return Number.isInteger(offset);
}

function validOption(value: unknown, options: string[] | undefined): boolean {
    return typeof value === "string" && !!options?.includes(value);
}

export function validateWan3GenerationParams(modelId: string, value: unknown): boolean {
    if (!isWan3VideoModelId(modelId)) return false;
    if (value === undefined) return true;
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const record = value as Record<string, unknown>;
    if (Object.keys(record).some(key => !ORDERED_PARAMS.has(key))) return false;
    const model = catalogModel(modelId);
    if (!model) return false;
    for (const [key, item] of Object.entries(record)) {
        if (key === "duration" && !validDuration(item, model.duration)) return false;
        if (key === "resolution" && !validOption(item, model.params?.resolution?.options)) return false;
        if (key === "ratio" && !validOption(item, model.params?.ratio?.options)) return false;
        if (key === "seed" && (typeof item !== "number" || !Number.isSafeInteger(item) || (item < 0 && item !== -1) || item > 2147483647)) return false;
        if ((key === "prompt_extend" || key === "watermark" || key === "audio") && typeof item !== "boolean") return false;
    }
    return true;
}
