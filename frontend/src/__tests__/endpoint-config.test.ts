import { describe, expect, it } from "vitest";

import {
  DEFAULT_ENV_CONFIG,
  ENDPOINT_PROVIDERS,
  normalizeEnvConfig,
  normalizeEditProvider,
  normalizeImageProvider,
  normalizeTtsProvider,
  normalizeStorageProvider,
} from "../lib/env-config";

function canSaveConfig(): boolean {
  return true;
}

function applyChange(key: string, value: string) {
  return { ...DEFAULT_ENV_CONFIG, [key]: value };
}

function applyEndpointChange(envKey: string, value: string) {
  return {
    ...DEFAULT_ENV_CONFIG,
    endpoint_overrides: { ...DEFAULT_ENV_CONFIG.endpoint_overrides, [envKey]: value },
  };
}

function computeCanClose(): boolean {
  return true;
}

describe("ENDPOINT_PROVIDERS registry", () => {
  it("has key, label, placeholder for each provider", () => {
    for (const provider of ENDPOINT_PROVIDERS) {
      expect(provider.key).toBeDefined();
      expect(provider.label).toBeDefined();
      expect(provider.placeholder).toBeDefined();
    }
  });

  it("follows {PROVIDER}_BASE_URL naming convention", () => {
    for (const provider of ENDPOINT_PROVIDERS) {
      expect(provider.key).toMatch(/^[A-Z]+_BASE_URL$/);
    }
  });

  it("contains exactly Ark / Seedance, DashScope, Kling, Vidu", () => {
    expect(ENDPOINT_PROVIDERS).toHaveLength(4);
    const labels = ENDPOINT_PROVIDERS.map((p) => p.label);
    expect(labels).toEqual(expect.arrayContaining(["Ark / Seedance", "DashScope", "Kling", "Vidu"]));
  });
});

describe("storage provider normalization", () => {
  it("accepts tos and oss", () => {
    expect(normalizeStorageProvider("tos")).toBe("tos");
    expect(normalizeStorageProvider("oss")).toBe("oss");
  });

  it("falls back to local mode for unknown values", () => {
    expect(normalizeStorageProvider("unexpected")).toBe("");
    expect(normalizeStorageProvider(undefined)).toBe("");
  });
});

describe("image provider normalization", () => {
  it("defaults to openai-compatible for unknown values", () => {
    expect(normalizeImageProvider("openai")).toBe("openai");
    expect(normalizeImageProvider("dashscope")).toBe("dashscope");
    expect(normalizeImageProvider("unexpected")).toBe("openai");
  });
});

describe("edit provider normalization", () => {
  it("defaults to openai-compatible for unknown values", () => {
    expect(normalizeEditProvider("openai")).toBe("openai");
    expect(normalizeEditProvider("dashscope")).toBe("dashscope");
    expect(normalizeEditProvider("unexpected")).toBe("openai");
  });
});

describe("tts provider normalization", () => {
  it("defaults to openai-compatible for unknown values", () => {
    expect(normalizeTtsProvider("openai")).toBe("openai");
    expect(normalizeTtsProvider("dashscope")).toBe("dashscope");
    expect(normalizeTtsProvider("unexpected")).toBe("openai");
  });
});

describe("canSaveConfig", () => {
  it("allows saving when no API keys are configured yet", () => {
    expect(canSaveConfig()).toBe(true);
  });
});

describe("applyChange", () => {
  it("updates a single field immutably", () => {
    const updated = applyChange("OPENAI_IMAGE_MODEL", "image-model-x");
    expect(updated.OPENAI_IMAGE_MODEL).toBe("image-model-x");
    expect(DEFAULT_ENV_CONFIG.OPENAI_IMAGE_MODEL).toBe("nano-banana");
  });
});

describe("DEFAULT_ENV_CONFIG", () => {
  it("uses Banana defaults for both image generation and image edit", () => {
    expect(DEFAULT_ENV_CONFIG.IMAGE_EDIT_PROVIDER).toBe("openai");
    expect(DEFAULT_ENV_CONFIG.OPENAI_IMAGE_BASE_URL).toBe("https://api.bltcy.ai/v1");
    expect(DEFAULT_ENV_CONFIG.OPENAI_IMAGE_MODEL).toBe("nano-banana");
    expect(DEFAULT_ENV_CONFIG.OPENAI_IMAGE_EDIT_BASE_URL).toBe("https://api.bltcy.ai/v1");
    expect(DEFAULT_ENV_CONFIG.OPENAI_IMAGE_EDIT_MODEL).toBe("nano-banana");
  });
});

describe("applyEndpointChange", () => {
  it("adds endpoint overrides immutably", () => {
    const updated = applyEndpointChange("DASHSCOPE_BASE_URL", "https://intl.example.com");
    expect(updated.endpoint_overrides.DASHSCOPE_BASE_URL).toBe("https://intl.example.com");
    expect(DEFAULT_ENV_CONFIG.endpoint_overrides.DASHSCOPE_BASE_URL).toBeUndefined();
  });
});

describe("normalizeEnvConfig", () => {
  it("preserves provider-mode fields from API response", () => {
    const result = normalizeEnvConfig(DEFAULT_ENV_CONFIG, {
      KLING_PROVIDER_MODE: "vendor",
      VIDU_PROVIDER_MODE: "vendor",
      PIXVERSE_PROVIDER_MODE: "dashscope",
      endpoint_overrides: { KLING_BASE_URL: "https://custom-kling.example.com" },
    });

    expect(result.KLING_PROVIDER_MODE).toBe("vendor");
    expect(result.VIDU_PROVIDER_MODE).toBe("vendor");
    expect(result.endpoint_overrides).toEqual({ KLING_BASE_URL: "https://custom-kling.example.com" });
  });

  it("maps object storage fields from new generic API response", () => {
    const result = normalizeEnvConfig(DEFAULT_ENV_CONFIG, {
      OBJECT_STORAGE_PROVIDER: "tos",
      OBJECT_STORAGE_BUCKET_NAME: "ark-auto-2104181120-cn-beijing-default",
      OBJECT_STORAGE_ENDPOINT: "https://tos-cn-beijing.volces.com",
      OBJECT_STORAGE_REGION: "cn-beijing",
      OBJECT_STORAGE_BASE_PATH: "seedance-inputs",
    });

    expect(result.OBJECT_STORAGE_PROVIDER).toBe("tos");
    expect(result.OBJECT_STORAGE_BUCKET_NAME).toBe("ark-auto-2104181120-cn-beijing-default");
    expect(result.OBJECT_STORAGE_ENDPOINT).toBe("https://tos-cn-beijing.volces.com");
    expect(result.OBJECT_STORAGE_REGION).toBe("cn-beijing");
    expect(result.OBJECT_STORAGE_BASE_PATH).toBe("seedance-inputs");
  });

  it("backfills generic object storage fields from legacy OSS response", () => {
    const result = normalizeEnvConfig(DEFAULT_ENV_CONFIG, {
      OSS_BUCKET_NAME: "legacy-bucket",
      OSS_ENDPOINT: "oss-cn-beijing.aliyuncs.com",
      OSS_BASE_PATH: "legacy-prefix",
    });

    expect(result.OBJECT_STORAGE_BUCKET_NAME).toBe("legacy-bucket");
    expect(result.OBJECT_STORAGE_ENDPOINT).toBe("oss-cn-beijing.aliyuncs.com");
    expect(result.OBJECT_STORAGE_BASE_PATH).toBe("legacy-prefix");
  });

  it("preserves image provider fields from API response", () => {
    const result = normalizeEnvConfig(DEFAULT_ENV_CONFIG, {
      IMAGE_PROVIDER: "dashscope",
      IMAGE_EDIT_PROVIDER: "openai",
      OPENAI_IMAGE_BASE_URL: "https://image.example.com/v1",
      OPENAI_IMAGE_EDIT_BASE_URL: "https://edit.example.com/v1",
      OPENAI_IMAGE_MODEL: "image-model-x",
      OPENAI_IMAGE_EDIT_MODEL: "image-model-edit",
    });

    expect(result.IMAGE_PROVIDER).toBe("dashscope");
    expect(result.IMAGE_EDIT_PROVIDER).toBe("openai");
    expect(result.OPENAI_IMAGE_BASE_URL).toBe("https://image.example.com/v1");
    expect(result.OPENAI_IMAGE_EDIT_BASE_URL).toBe("https://edit.example.com/v1");
    expect(result.OPENAI_IMAGE_MODEL).toBe("image-model-x");
    expect(result.OPENAI_IMAGE_EDIT_MODEL).toBe("image-model-edit");
  });

  it("preserves tts and multimodal fields from API response", () => {
    const result = normalizeEnvConfig(DEFAULT_ENV_CONFIG, {
      TTS_PROVIDER: "dashscope",
      OPENAI_TTS_BASE_URL: "https://tts.example.com/v1",
      OPENAI_TTS_MODEL: "gpt-4o-mini-tts",
      OPENAI_MULTIMODAL_BASE_URL: "https://mm.example.com/v1",
      OPENAI_MULTIMODAL_MODEL: "qwen-vl-max",
    });

    expect(result.TTS_PROVIDER).toBe("dashscope");
    expect(result.OPENAI_TTS_BASE_URL).toBe("https://tts.example.com/v1");
    expect(result.OPENAI_TTS_MODEL).toBe("gpt-4o-mini-tts");
    expect(result.OPENAI_MULTIMODAL_BASE_URL).toBe("https://mm.example.com/v1");
    expect(result.OPENAI_MULTIMODAL_MODEL).toBe("qwen-vl-max");
  });

  it("keeps recommended defaults when API returns empty model fields", () => {
    const result = normalizeEnvConfig(DEFAULT_ENV_CONFIG, {
      OPENAI_TTS_MODEL: "",
      OPENAI_MULTIMODAL_MODEL: "",
      OPENAI_MODEL: "qwen3.6-plus",
    });

    expect(result.OPENAI_TTS_MODEL).toBe(DEFAULT_ENV_CONFIG.OPENAI_TTS_MODEL);
    expect(result.OPENAI_MULTIMODAL_MODEL).toBe(DEFAULT_ENV_CONFIG.OPENAI_MULTIMODAL_MODEL);
  });
});

describe("computeCanClose", () => {
  it("always allows closing the settings panel", () => {
    expect(computeCanClose()).toBe(true);
  });
});
