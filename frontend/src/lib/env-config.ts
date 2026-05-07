import type { EnvConfigPayload, ProviderMode, StorageProvider } from "@/lib/api";

export type EnvConfigState = EnvConfigPayload & {
  IMAGE_PROVIDER: "openai" | "dashscope";
  IMAGE_EDIT_PROVIDER: "openai" | "dashscope";
  TTS_PROVIDER: "openai" | "dashscope";
  LLM_PROVIDER: string;
  OPENAI_API_KEY: string;
  OPENAI_BASE_URL: string;
  OPENAI_MODEL: string;
  OPENAI_IMAGE_API_KEY: string;
  OPENAI_IMAGE_EDIT_API_KEY: string;
  OPENAI_IMAGE_BASE_URL: string;
  OPENAI_IMAGE_EDIT_BASE_URL: string;
  OPENAI_IMAGE_MODEL: string;
  OPENAI_IMAGE_EDIT_MODEL: string;
  OPENAI_TTS_API_KEY: string;
  OPENAI_TTS_BASE_URL: string;
  OPENAI_TTS_MODEL: string;
  OPENAI_MULTIMODAL_API_KEY: string;
  OPENAI_MULTIMODAL_BASE_URL: string;
  OPENAI_MULTIMODAL_MODEL: string;
  ARK_API_KEY: string;
  DASHSCOPE_API_KEY: string;
  OBJECT_STORAGE_PROVIDER: StorageProvider;
  OBJECT_STORAGE_BUCKET_NAME: string;
  OBJECT_STORAGE_ENDPOINT: string;
  OBJECT_STORAGE_REGION: string;
  OBJECT_STORAGE_BASE_PATH: string;
  TOS_ACCESS_KEY_ID: string;
  TOS_SECRET_ACCESS_KEY: string;
  ALIBABA_CLOUD_ACCESS_KEY_ID: string;
  ALIBABA_CLOUD_ACCESS_KEY_SECRET: string;
  OSS_BUCKET_NAME: string;
  OSS_ENDPOINT: string;
  OSS_BASE_PATH: string;
  KLING_PROVIDER_MODE: ProviderMode;
  VIDU_PROVIDER_MODE: ProviderMode;
  PIXVERSE_PROVIDER_MODE: ProviderMode;
  KLING_ACCESS_KEY: string;
  KLING_SECRET_KEY: string;
  VIDU_API_KEY: string;
  endpoint_overrides: Record<string, string>;
};

export const ENDPOINT_PROVIDERS = [
  { key: "ARK_BASE_URL", label: "Ark / Seedance", placeholder: "https://ark.cn-beijing.volces.com/api/v3" },
  { key: "DASHSCOPE_BASE_URL", label: "DashScope", placeholder: "https://dashscope.aliyuncs.com" },
  { key: "KLING_BASE_URL", label: "Kling", placeholder: "https://api-beijing.klingai.com/v1" },
  { key: "VIDU_BASE_URL", label: "Vidu", placeholder: "https://api.vidu.cn/ent/v2" },
];

export const OPENAI_TTS_MODEL_PRESETS = [
  { value: "qwen3-tts-flash", label: "qwen3-tts-flash" },
  { value: "gpt-4o-mini-tts", label: "gpt-4o-mini-tts" },
  { value: "tts-1", label: "tts-1" },
];

export const OPENAI_MULTIMODAL_MODEL_PRESETS = [
  { value: "qwen-vl-max", label: "qwen-vl-max" },
  { value: "qwen-vl-max-latest", label: "qwen-vl-max-latest" },
  { value: "qwen3-vl-plus", label: "qwen3-vl-plus" },
  { value: "qwen3-vl-flash", label: "qwen3-vl-flash" },
];

export const DEFAULT_ENV_CONFIG: EnvConfigState = {
  IMAGE_PROVIDER: "openai",
  IMAGE_EDIT_PROVIDER: "openai",
  TTS_PROVIDER: "openai",
  LLM_PROVIDER: "openai",
  OPENAI_API_KEY: "",
  OPENAI_BASE_URL: "https://yunwu.ai/v1",
  OPENAI_MODEL: "qwen3.6-plus",
  OPENAI_IMAGE_API_KEY: "",
  OPENAI_IMAGE_EDIT_API_KEY: "",
  OPENAI_IMAGE_BASE_URL: "https://api.bltcy.ai/v1",
  OPENAI_IMAGE_EDIT_BASE_URL: "https://api.bltcy.ai/v1",
  OPENAI_IMAGE_MODEL: "nano-banana",
  OPENAI_IMAGE_EDIT_MODEL: "nano-banana",
  OPENAI_TTS_API_KEY: "",
  OPENAI_TTS_BASE_URL: "https://yunwu.ai/v1",
  OPENAI_TTS_MODEL: "qwen3-tts-flash",
  OPENAI_MULTIMODAL_API_KEY: "",
  OPENAI_MULTIMODAL_BASE_URL: "https://yunwu.ai/v1",
  OPENAI_MULTIMODAL_MODEL: "qwen-vl-max",
  ARK_API_KEY: "",
  DASHSCOPE_API_KEY: "",
  OBJECT_STORAGE_PROVIDER: "",
  OBJECT_STORAGE_BUCKET_NAME: "",
  OBJECT_STORAGE_ENDPOINT: "",
  OBJECT_STORAGE_REGION: "cn-beijing",
  OBJECT_STORAGE_BASE_PATH: "seedance-inputs",
  TOS_ACCESS_KEY_ID: "",
  TOS_SECRET_ACCESS_KEY: "",
  ALIBABA_CLOUD_ACCESS_KEY_ID: "",
  ALIBABA_CLOUD_ACCESS_KEY_SECRET: "",
  OSS_BUCKET_NAME: "",
  OSS_ENDPOINT: "",
  OSS_BASE_PATH: "",
  KLING_PROVIDER_MODE: "dashscope",
  VIDU_PROVIDER_MODE: "dashscope",
  PIXVERSE_PROVIDER_MODE: "dashscope",
  KLING_ACCESS_KEY: "",
  KLING_SECRET_KEY: "",
  VIDU_API_KEY: "",
  endpoint_overrides: {},
};

export const normalizeProviderMode = (mode?: string): ProviderMode => (mode === "vendor" ? "vendor" : "dashscope");

export const normalizeStorageProvider = (provider?: string): StorageProvider =>
  provider === "tos" || provider === "oss" ? provider : "";

export const normalizeImageProvider = (provider?: string): "openai" | "dashscope" =>
  provider === "dashscope" ? "dashscope" : "openai";

export const normalizeEditProvider = (provider?: string): "openai" | "dashscope" =>
  provider === "dashscope" ? "dashscope" : "openai";

export const normalizeTtsProvider = (provider?: string): "openai" | "dashscope" =>
  provider === "dashscope" ? "dashscope" : "openai";

const pickFirstNonEmpty = (...values: Array<string | undefined>) => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return "";
};

const pickMultimodalModel = (...values: Array<string | undefined>) => {
  for (const value of values) {
    if (typeof value !== "string") continue;
    const normalized = value.trim();
    if (!normalized) continue;
    const lowered = normalized.toLowerCase();
    if (lowered.includes("vl") || lowered.includes("vision") || lowered.includes("qvq")) {
      return normalized;
    }
  }
  return "";
};

export const normalizeEnvConfig = (
  existing: EnvConfigState,
  data?: EnvConfigPayload,
): EnvConfigState => {
  const merged = {
    ...existing,
    ...data,
  };

  const normalizedStorageProvider = normalizeStorageProvider(
    data?.OBJECT_STORAGE_PROVIDER ?? existing.OBJECT_STORAGE_PROVIDER,
  );

  const objectStorageBucketName =
    data?.OBJECT_STORAGE_BUCKET_NAME ??
    data?.OSS_BUCKET_NAME ??
    existing.OBJECT_STORAGE_BUCKET_NAME;
  const objectStorageEndpoint =
    data?.OBJECT_STORAGE_ENDPOINT ??
    data?.OSS_ENDPOINT ??
    existing.OBJECT_STORAGE_ENDPOINT;
  const objectStorageBasePath =
    data?.OBJECT_STORAGE_BASE_PATH ??
    data?.OSS_BASE_PATH ??
    existing.OBJECT_STORAGE_BASE_PATH;

  return {
    ...merged,
    IMAGE_PROVIDER: normalizeImageProvider(data?.IMAGE_PROVIDER ?? existing.IMAGE_PROVIDER),
    IMAGE_EDIT_PROVIDER: normalizeEditProvider(
      data?.IMAGE_EDIT_PROVIDER ?? data?.IMAGE_PROVIDER ?? existing.IMAGE_EDIT_PROVIDER,
    ),
    TTS_PROVIDER: normalizeTtsProvider(data?.TTS_PROVIDER ?? existing.TTS_PROVIDER),
    OPENAI_BASE_URL: pickFirstNonEmpty(data?.OPENAI_BASE_URL, existing.OPENAI_BASE_URL),
    OPENAI_MODEL: pickFirstNonEmpty(data?.OPENAI_MODEL, existing.OPENAI_MODEL),
    OPENAI_IMAGE_BASE_URL: pickFirstNonEmpty(
      data?.OPENAI_IMAGE_BASE_URL,
      existing.OPENAI_IMAGE_BASE_URL,
    ),
    OPENAI_IMAGE_EDIT_BASE_URL: pickFirstNonEmpty(
      data?.OPENAI_IMAGE_EDIT_BASE_URL,
      existing.OPENAI_IMAGE_EDIT_BASE_URL,
    ),
    OPENAI_IMAGE_MODEL: pickFirstNonEmpty(data?.OPENAI_IMAGE_MODEL, existing.OPENAI_IMAGE_MODEL),
    OPENAI_IMAGE_EDIT_MODEL: pickFirstNonEmpty(
      data?.OPENAI_IMAGE_EDIT_MODEL,
      data?.OPENAI_IMAGE_MODEL,
      existing.OPENAI_IMAGE_EDIT_MODEL,
    ),
    OPENAI_TTS_BASE_URL:
      pickFirstNonEmpty(
        data?.OPENAI_TTS_BASE_URL,
        data?.OPENAI_BASE_URL,
        existing.OPENAI_TTS_BASE_URL,
      ),
    OPENAI_TTS_MODEL:
      pickFirstNonEmpty(
        data?.OPENAI_TTS_MODEL,
        existing.OPENAI_TTS_MODEL,
      ),
    OPENAI_MULTIMODAL_BASE_URL:
      pickFirstNonEmpty(
        data?.OPENAI_MULTIMODAL_BASE_URL,
        data?.OPENAI_BASE_URL,
        existing.OPENAI_MULTIMODAL_BASE_URL,
      ),
    OPENAI_MULTIMODAL_MODEL:
      pickFirstNonEmpty(
        data?.OPENAI_MULTIMODAL_MODEL,
        pickMultimodalModel(data?.OPENAI_MODEL),
        existing.OPENAI_MULTIMODAL_MODEL,
      ),
    OBJECT_STORAGE_PROVIDER: normalizedStorageProvider,
    OBJECT_STORAGE_BUCKET_NAME: objectStorageBucketName,
    OBJECT_STORAGE_ENDPOINT: objectStorageEndpoint,
    OBJECT_STORAGE_REGION:
      data?.OBJECT_STORAGE_REGION ?? existing.OBJECT_STORAGE_REGION,
    OBJECT_STORAGE_BASE_PATH: objectStorageBasePath,
    OSS_BUCKET_NAME: objectStorageBucketName,
    OSS_ENDPOINT: objectStorageEndpoint,
    OSS_BASE_PATH: objectStorageBasePath,
    KLING_PROVIDER_MODE: normalizeProviderMode(data?.KLING_PROVIDER_MODE ?? existing.KLING_PROVIDER_MODE),
    VIDU_PROVIDER_MODE: normalizeProviderMode(data?.VIDU_PROVIDER_MODE ?? existing.VIDU_PROVIDER_MODE),
    PIXVERSE_PROVIDER_MODE: normalizeProviderMode(data?.PIXVERSE_PROVIDER_MODE ?? existing.PIXVERSE_PROVIDER_MODE),
    endpoint_overrides: data?.endpoint_overrides ?? existing.endpoint_overrides ?? {},
  };
};
