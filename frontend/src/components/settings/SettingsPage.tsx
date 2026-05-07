"use client";

import { useState, useEffect } from "react";
import { Save, Loader2, Key, ChevronDown, ChevronRight, Settings, MessageSquareCode } from "lucide-react";
import { api } from "@/lib/api";
import {
  DEFAULT_ENV_CONFIG,
  ENDPOINT_PROVIDERS,
  OPENAI_MULTIMODAL_MODEL_PRESETS,
  OPENAI_TTS_MODEL_PRESETS,
  normalizeEnvConfig,
  type EnvConfigState,
} from "@/lib/env-config";
import { DEFAULT_I2I_MODEL, DEFAULT_I2V_MODEL, DEFAULT_T2I_MODEL, T2I_MODELS, I2I_MODELS, I2V_MODELS, ASPECT_RATIOS } from "@/store/projectStore";
import { Image, Video, Layout, Check, User, Building, Box } from "lucide-react";
import { zhCN } from "@/lib/i18n";

const LS_KEY_MODEL = "lumenx_default_model_settings";
const LS_KEY_PROMPT = "lumenx_default_prompt_config";

interface DefaultModelSettings {
  t2i_model: string;
  i2i_model: string;
  i2v_model: string;
  character_aspect_ratio: string;
  scene_aspect_ratio: string;
  prop_aspect_ratio: string;
  storyboard_aspect_ratio: string;
}

interface DefaultPromptConfig {
  storyboard_polish: string;
  video_polish: string;
  r2v_polish: string;
}

function loadFromLS<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export default function SettingsPage() {
  const copy = zhCN.settingsPage;
  const commonCopy = zhCN.common;
  const promptCopy = zhCN.promptConfig.sections;

  // ── API Config ──
  const [config, setConfig] = useState<EnvConfigState>(DEFAULT_ENV_CONFIG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [endpointsOpen, setEndpointsOpen] = useState(false);

  // ── Default Model Settings ──
  const [modelSettings, setModelSettings] = useState<DefaultModelSettings>(() =>
    loadFromLS(LS_KEY_MODEL, {
      t2i_model: DEFAULT_T2I_MODEL,
      i2i_model: DEFAULT_I2I_MODEL,
      i2v_model: DEFAULT_I2V_MODEL,
      character_aspect_ratio: "9:16",
      scene_aspect_ratio: "16:9",
      prop_aspect_ratio: "1:1",
      storyboard_aspect_ratio: "16:9",
    })
  );

  // ── Default Prompt Config ──
  const [promptConfig, setPromptConfig] = useState<DefaultPromptConfig>(() =>
    loadFromLS(LS_KEY_PROMPT, { storyboard_polish: "", video_polish: "", r2v_polish: "" })
  );

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.getEnvConfig();
      setConfig((prev) => normalizeEnvConfig(prev, data));
    } catch {
      setLoadError(copy.apiConfig.loadError);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveApiConfig = async () => {
    setSaving(true);
    try {
      await api.saveEnvConfig(config);
      alert(commonCopy.messages.configurationSaved);
    } catch {
      alert(commonCopy.messages.saveFailed);
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (key: keyof EnvConfigState, value: string) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  const handleEndpointChange = (envKey: string, value: string) => {
    setConfig((prev) => ({
      ...prev,
      endpoint_overrides: { ...prev.endpoint_overrides, [envKey]: value },
    }));
  };

  const handleSaveModelDefaults = () => {
    localStorage.setItem(LS_KEY_MODEL, JSON.stringify(modelSettings));
    alert(copy.apiConfig.modelDefaultsSaved);
  };

  const handleSavePromptDefaults = () => {
    localStorage.setItem(LS_KEY_PROMPT, JSON.stringify(promptConfig));
    alert(copy.apiConfig.promptDefaultsSaved);
  };

  const inputClass =
    "w-full bg-black/30 border border-white/10 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary/50 transition-colors";
  const modeButtonClass = (active: boolean) =>
    `px-3 py-1.5 text-xs rounded-md border transition-colors ${active ? "border-amber-500/60 bg-amber-500/15 text-amber-200" : "border-white/10 bg-white/5 text-gray-400 hover:text-gray-200"}`;
  const presetButtonClass =
    "px-2.5 py-1 text-[11px] rounded-md border border-white/10 bg-white/5 text-gray-300 hover:text-white hover:border-white/20 transition-colors";
  const showBananaKeyWarning =
    config.IMAGE_PROVIDER === "openai" &&
    config.OPENAI_IMAGE_BASE_URL.includes("api.bltcy.ai") &&
    !config.OPENAI_IMAGE_API_KEY.trim() &&
    !config.OPENAI_IMAGE_EDIT_API_KEY.trim() &&
    !!config.OPENAI_API_KEY.trim();
  const showBananaEditKeyWarning =
    config.IMAGE_EDIT_PROVIDER === "openai" &&
    config.OPENAI_IMAGE_EDIT_BASE_URL.includes("api.bltcy.ai") &&
    !config.OPENAI_IMAGE_EDIT_API_KEY.trim() &&
    !config.OPENAI_IMAGE_API_KEY.trim() &&
    !!config.OPENAI_API_KEY.trim();

  return (
    <div className="container mx-auto px-6 py-8 max-w-4xl space-y-8">
      <h1 className="text-2xl font-display font-bold text-white">{copy.title}</h1>

      {/* ── Section 1: API Configuration ── */}
      <section className="glass-panel rounded-xl p-6 space-y-6">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-gradient-to-br from-amber-500/20 to-orange-500/20 rounded-lg">
            <Key size={20} className="text-amber-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">{copy.apiConfig.title}</h2>
            <p className="text-xs text-gray-500">{copy.apiConfig.subtitle}</p>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={24} className="animate-spin text-amber-400" />
            <span className="ml-2 text-gray-400">{commonCopy.messages.loadingConfiguration}</span>
          </div>
        ) : loadError ? (
          <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
            {loadError}
          </div>
        ) : (
          <>
            <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
              <div>
                <h3 className="text-sm font-bold text-white mb-2">{copy.apiConfig.llmProviderTitle}</h3>
                <p className="text-xs text-gray-500">{copy.apiConfig.llmProviderDescription}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => handleChange("LLM_PROVIDER", "openai")} className={modeButtonClass(config.LLM_PROVIDER === "openai")}>
                  {copy.apiConfig.openAiCompatible}
                </button>
                <button type="button" onClick={() => handleChange("LLM_PROVIDER", "dashscope")} className={modeButtonClass(config.LLM_PROVIDER === "dashscope")}>
                  {copy.apiConfig.dashScope}
                </button>
              </div>
              {config.LLM_PROVIDER === "openai" ? (
                <>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>{copy.apiConfig.openAiApiKey}</span>
                      <span className="text-gray-600 font-normal text-xs">{copy.apiConfig.openAiApiKeyHint}</span>
                    </label>
                    <input type="password" value={config.OPENAI_API_KEY} onChange={(e) => handleChange("OPENAI_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>{copy.apiConfig.openAiBaseUrl}</span>
                      <span className="text-gray-600 font-normal text-xs">{copy.apiConfig.openAiBaseUrlHint}</span>
                    </label>
                    <input type="text" value={config.OPENAI_BASE_URL} onChange={(e) => handleChange("OPENAI_BASE_URL", e.target.value)} placeholder="https://yunwu.ai/v1" className={inputClass} />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>{copy.apiConfig.openAiModel}</span>
                      <span className="text-gray-600 font-normal text-xs">{copy.apiConfig.openAiModelHint}</span>
                    </label>
                    <input type="text" value={config.OPENAI_MODEL} onChange={(e) => handleChange("OPENAI_MODEL", e.target.value)} placeholder="qwen3.6-plus" className={inputClass} />
                  </div>
                </>
              ) : (
                <div>
                  <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                    <span>{copy.apiConfig.dashScopeApiKey}</span>
                    <span className="text-gray-600 font-normal text-xs">{copy.apiConfig.dashScopeOptionalHint}</span>
                  </label>
                  <input type="password" value={config.DASHSCOPE_API_KEY} onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                </div>
              )}
            </div>

            <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
              <div>
                <h3 className="text-sm font-bold text-white mb-2">生图 Provider</h3>
                <p className="text-xs text-gray-500">文生图和图编已经拆成两条独立链路。两把图像 key 会互相兜底：生图优先图像 Key，图编优先图像编辑 Key。</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => handleChange("IMAGE_PROVIDER", "openai")} className={modeButtonClass(config.IMAGE_PROVIDER === "openai")}>
                  OpenAI-compatible
                </button>
                <button type="button" onClick={() => handleChange("IMAGE_PROVIDER", "dashscope")} className={modeButtonClass(config.IMAGE_PROVIDER === "dashscope")}>
                  DashScope 兼容
                </button>
              </div>

              {config.IMAGE_PROVIDER === "openai" ? (
                <>
                  <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                    截图里的 `gpt-image-2` 同时支持 `/v1/images/generations` 和 `/v1/images/edits`。当前建议把 `gpt-image-2` 作为主用模型，`gpt-image-2-all` 只保留为备用测试，不要覆盖主配置。
                  </div>
                  {showBananaKeyWarning ? (
                    <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-3 text-xs text-amber-200">
                      当前主用和备用图像 Key 都是空的，后端会暂时沿用上面的 `OPENAI_API_KEY`。如果那把 key 不是 `api.bltcy.ai` 的专用 key，就会出现“无效令牌”。请至少保留一把图像专用 key。
                    </div>
                  ) : null}
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>图像 API Key（主用）</span>
                      <span className="text-gray-600 font-normal text-xs">优先生图；留空则回退图编 Key / OPENAI_API_KEY</span>
                    </label>
                    <input type="password" value={config.OPENAI_IMAGE_API_KEY} onChange={(e) => handleChange("OPENAI_IMAGE_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>图像 Base URL（可选）</span>
                      <span className="text-gray-600 font-normal text-xs">填写到 /v1 即可，例如 https://yunwu.ai/v1</span>
                    </label>
                    <input type="text" value={config.OPENAI_IMAGE_BASE_URL} onChange={(e) => handleChange("OPENAI_IMAGE_BASE_URL", e.target.value)} placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_BASE_URL} className={inputClass} />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>生图模型</span>
                      <span className="text-gray-600 font-normal text-xs">主用建议 gpt-image-2；gpt-image-2-all 仅备用测试</span>
                    </label>
                    <input type="text" value={config.OPENAI_IMAGE_MODEL} onChange={(e) => handleChange("OPENAI_IMAGE_MODEL", e.target.value)} placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_MODEL} className={inputClass} />
                  </div>
                </>
              ) : (
                <div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>{copy.apiConfig.dashScopeApiKey}</span>
                      <span className="text-gray-600 font-normal text-xs">仅旧版 Wan 文生图兼容链路需要</span>
                    </label>
                    <input type="password" value={config.DASHSCOPE_API_KEY} onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                  </div>
                </div>
              )}

              <div className="border-t border-white/10 pt-4 space-y-4">
                <div>
                  <h4 className="text-sm font-bold text-white mb-2">图编 Provider</h4>
                  <p className="text-xs text-gray-500">分镜重绘、首帧重绘和参考图编辑可单独指定接口，不会影响文生图链路。</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={() => handleChange("IMAGE_EDIT_PROVIDER", "openai")} className={modeButtonClass(config.IMAGE_EDIT_PROVIDER === "openai")}>
                    OpenAI-compatible
                  </button>
                  <button type="button" onClick={() => handleChange("IMAGE_EDIT_PROVIDER", "dashscope")} className={modeButtonClass(config.IMAGE_EDIT_PROVIDER === "dashscope")}>
                    DashScope 兼容
                  </button>
                </div>

                {config.IMAGE_EDIT_PROVIDER === "openai" ? (
                  <>
                    <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                      默认图编也会走 Banana，但你现在可以把图编单独切到另一家更稳的 OpenAI-compatible `/images/edits` 接口，不影响文生图。
                    </div>
                    {showBananaEditKeyWarning ? (
                      <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-3 text-xs text-amber-200">
                        当前主用和备用图像 Key 都是空的，后端会回退 `OPENAI_API_KEY`。如果 Banana 图编链路仍然不稳，请在这里保留一把图编专用 key，作为重绘优先 key。
                      </div>
                    ) : null}
                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                        <span>图像编辑 API Key（图编主用 / 生图备用）</span>
                        <span className="text-gray-600 font-normal text-xs">优先重绘；留空则回退图像 Key / OPENAI_API_KEY</span>
                      </label>
                      <input type="password" value={config.OPENAI_IMAGE_EDIT_API_KEY} onChange={(e) => handleChange("OPENAI_IMAGE_EDIT_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                    </div>
                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                        <span>图编 Base URL（可选）</span>
                        <span className="text-gray-600 font-normal text-xs">填写到 /v1 即可，后端会拼 `/images/edits`</span>
                      </label>
                      <input type="text" value={config.OPENAI_IMAGE_EDIT_BASE_URL} onChange={(e) => handleChange("OPENAI_IMAGE_EDIT_BASE_URL", e.target.value)} placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_EDIT_BASE_URL} className={inputClass} />
                    </div>
                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                        <span>图编模型（可选）</span>
                        <span className="text-gray-600 font-normal text-xs">截图里的 gpt-image-2 支持 `/images/edits`</span>
                      </label>
                      <input type="text" value={config.OPENAI_IMAGE_EDIT_MODEL} onChange={(e) => handleChange("OPENAI_IMAGE_EDIT_MODEL", e.target.value)} placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_EDIT_MODEL} className={inputClass} />
                    </div>
                  </>
                ) : (
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>图编 DashScope API Key</span>
                      <span className="text-gray-600 font-normal text-xs">切回 Wan 图编时使用，与旧版兼容链路共用</span>
                    </label>
                    <input type="password" value={config.DASHSCOPE_API_KEY} onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                  </div>
                )}
              </div>
            </div>

            <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
              <div>
                <h3 className="text-sm font-bold text-white mb-2">配音 Provider</h3>
                <p className="text-xs text-gray-500">默认优先使用 OpenAI-compatible / 中转 TTS。DashScope CosyVoice 仅保留为兼容回退。</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => handleChange("TTS_PROVIDER", "openai")} className={modeButtonClass(config.TTS_PROVIDER === "openai")}>
                  OpenAI-compatible
                </button>
                <button type="button" onClick={() => handleChange("TTS_PROVIDER", "dashscope")} className={modeButtonClass(config.TTS_PROVIDER === "dashscope")}>
                  DashScope 兼容
                </button>
              </div>

              {config.TTS_PROVIDER === "openai" ? (
                <>
                  <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                    TTS API Key / Base URL 留空时，会自动沿用上面的文本模型 OpenAI-compatible 配置。模型请填写支持 <code>/audio/speech</code> 的语音模型。
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>TTS API Key（可选）</span>
                      <span className="text-gray-600 font-normal text-xs">留空则沿用 OPENAI_API_KEY</span>
                    </label>
                    <input type="password" value={config.OPENAI_TTS_API_KEY} onChange={(e) => handleChange("OPENAI_TTS_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>TTS Base URL（可选）</span>
                      <span className="text-gray-600 font-normal text-xs">留空则沿用 OPENAI_BASE_URL</span>
                    </label>
                    <input type="text" value={config.OPENAI_TTS_BASE_URL} onChange={(e) => handleChange("OPENAI_TTS_BASE_URL", e.target.value)} placeholder="https://yunwu.ai/v1" className={inputClass} />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>配音模型</span>
                      <span className="text-gray-600 font-normal text-xs">例如 gpt-4o-mini-tts / 兼容的中转语音模型</span>
                    </label>
                    <input type="text" value={config.OPENAI_TTS_MODEL} onChange={(e) => handleChange("OPENAI_TTS_MODEL", e.target.value)} placeholder={DEFAULT_ENV_CONFIG.OPENAI_TTS_MODEL} className={inputClass} />
                    <div className="mt-2 flex flex-wrap gap-2">
                      {OPENAI_TTS_MODEL_PRESETS.map((preset) => (
                        <button
                          key={preset.value}
                          type="button"
                          onClick={() => handleChange("OPENAI_TTS_MODEL", preset.value)}
                          className={presetButtonClass}
                        >
                          {preset.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <div>
                  <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                    <span>{copy.apiConfig.dashScopeApiKey}</span>
                    <span className="text-gray-600 font-normal text-xs">仅 DashScope CosyVoice 兼容链路需要</span>
                  </label>
                  <input type="password" value={config.DASHSCOPE_API_KEY} onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
                </div>
              )}
            </div>

            <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
              <div>
                <h3 className="text-sm font-bold text-white mb-2">提示词优化 / 多模态</h3>
                <p className="text-xs text-gray-500">用于参考图提示词优化链。默认优先使用 OpenAI-compatible 多模态接口，留空时会自动沿用文本模型配置。</p>
              </div>
              <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                如果只保留 DashScope Key，后端也会自动兼容回退到 DashScope 的 OpenAI-compatible 地址；但推荐直接填写独立的多模态配置。
              </div>
              <div>
                <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                  <span>多模态 API Key（可选）</span>
                  <span className="text-gray-600 font-normal text-xs">留空则优先沿用 OPENAI_API_KEY</span>
                </label>
                <input type="password" value={config.OPENAI_MULTIMODAL_API_KEY} onChange={(e) => handleChange("OPENAI_MULTIMODAL_API_KEY", e.target.value)} placeholder={copy.apiConfig.fillLater} className={inputClass} />
              </div>
              <div>
                <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                  <span>多模态 Base URL（可选）</span>
                  <span className="text-gray-600 font-normal text-xs">留空则沿用 OPENAI_BASE_URL</span>
                </label>
                <input type="text" value={config.OPENAI_MULTIMODAL_BASE_URL} onChange={(e) => handleChange("OPENAI_MULTIMODAL_BASE_URL", e.target.value)} placeholder="https://yunwu.ai/v1" className={inputClass} />
              </div>
              <div>
                <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                  <span>多模态模型</span>
                  <span className="text-gray-600 font-normal text-xs">例如 qwen-vl-max / 兼容视觉模型</span>
                </label>
                <input type="text" value={config.OPENAI_MULTIMODAL_MODEL} onChange={(e) => handleChange("OPENAI_MULTIMODAL_MODEL", e.target.value)} placeholder={DEFAULT_ENV_CONFIG.OPENAI_MULTIMODAL_MODEL} className={inputClass} />
                <div className="mt-2 flex flex-wrap gap-2">
                  {OPENAI_MULTIMODAL_MODEL_PRESETS.map((preset) => (
                    <button
                      key={preset.value}
                      type="button"
                      onClick={() => handleChange("OPENAI_MULTIMODAL_MODEL", preset.value)}
                      className={presetButtonClass}
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div>
              <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                <span>{copy.apiConfig.arkApiKey}</span>
                <span className="text-gray-600 font-normal text-xs">{copy.apiConfig.arkApiKeyHint}</span>
              </label>
              <input type="password" value={config.ARK_API_KEY} onChange={(e) => handleChange("ARK_API_KEY", e.target.value)} placeholder={copy.apiConfig.arkApiKeyPlaceholder} className={inputClass} />
            </div>

            <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
              <div>
                <h3 className="text-sm font-bold text-white mb-2">{copy.apiConfig.storageTitle}</h3>
                <p className="text-xs text-gray-500">
                  {copy.apiConfig.storageDescription}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => handleChange("OBJECT_STORAGE_PROVIDER", "")}
                  className={modeButtonClass(config.OBJECT_STORAGE_PROVIDER === "")}
                >
                  {copy.apiConfig.localOnly}
                </button>
                <button
                  type="button"
                  onClick={() => handleChange("OBJECT_STORAGE_PROVIDER", "tos")}
                  className={modeButtonClass(config.OBJECT_STORAGE_PROVIDER === "tos")}
                >
                  {copy.apiConfig.byteTos}
                </button>
                <button
                  type="button"
                  onClick={() => handleChange("OBJECT_STORAGE_PROVIDER", "oss")}
                  className={modeButtonClass(config.OBJECT_STORAGE_PROVIDER === "oss")}
                >
                  {copy.apiConfig.alibabaOss}
                </button>
              </div>

              {config.OBJECT_STORAGE_PROVIDER !== "" && (
                <div className="space-y-4 pt-2 border-t border-white/10">
                  <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                    {copy.apiConfig.privateBucketNotice}
                  </div>

                  {config.OBJECT_STORAGE_PROVIDER === "tos" ? (
                    <>
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.tosAccessKeyId}</label>
                        <input
                          type="password"
                          value={config.TOS_ACCESS_KEY_ID}
                          onChange={(e) => handleChange("TOS_ACCESS_KEY_ID", e.target.value)}
                          placeholder={copy.apiConfig.tosAccessKeyId}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.tosSecretAccessKey}</label>
                        <input
                          type="password"
                          value={config.TOS_SECRET_ACCESS_KEY}
                          onChange={(e) => handleChange("TOS_SECRET_ACCESS_KEY", e.target.value)}
                          placeholder={copy.apiConfig.tosSecretAccessKey}
                          className={inputClass}
                        />
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.alibabaAccessKeyId}</label>
                        <input
                          type="password"
                          value={config.ALIBABA_CLOUD_ACCESS_KEY_ID}
                          onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_ID", e.target.value)}
                          placeholder={copy.apiConfig.alibabaAccessKeyId}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.alibabaAccessKeySecret}</label>
                        <input
                          type="password"
                          value={config.ALIBABA_CLOUD_ACCESS_KEY_SECRET}
                          onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_SECRET", e.target.value)}
                          placeholder={copy.apiConfig.alibabaAccessKeySecret}
                          className={inputClass}
                        />
                      </div>
                    </>
                  )}

                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.bucketName}</label>
                    <input
                      type="text"
                      value={config.OBJECT_STORAGE_BUCKET_NAME}
                      onChange={(e) => {
                        handleChange("OBJECT_STORAGE_BUCKET_NAME", e.target.value);
                        handleChange("OSS_BUCKET_NAME", e.target.value);
                      }}
                      placeholder="ark-auto-2104181120-cn-beijing-default"
                      className={inputClass}
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.endpoint}</label>
                    <input
                      type="text"
                      value={config.OBJECT_STORAGE_ENDPOINT}
                      onChange={(e) => {
                        handleChange("OBJECT_STORAGE_ENDPOINT", e.target.value);
                        handleChange("OSS_ENDPOINT", e.target.value);
                      }}
                      placeholder={config.OBJECT_STORAGE_PROVIDER === "tos" ? "https://tos-cn-beijing.volces.com" : "oss-cn-beijing.aliyuncs.com"}
                      className={inputClass}
                    />
                  </div>

                  {config.OBJECT_STORAGE_PROVIDER === "tos" && (
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.region}</label>
                      <input
                        type="text"
                        value={config.OBJECT_STORAGE_REGION}
                        onChange={(e) => handleChange("OBJECT_STORAGE_REGION", e.target.value)}
                        placeholder="cn-beijing"
                        className={inputClass}
                      />
                    </div>
                  )}

                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.uploadPrefix}</label>
                    <input
                      type="text"
                      value={config.OBJECT_STORAGE_BASE_PATH}
                      onChange={(e) => {
                        handleChange("OBJECT_STORAGE_BASE_PATH", e.target.value);
                        handleChange("OSS_BASE_PATH", e.target.value);
                      }}
                      placeholder="seedance-inputs"
                      className={inputClass}
                    />
                  </div>
                </div>
              )}
            </div>

            <div className="pt-4 border-t border-white/10">
              <h3 className="text-sm font-bold text-white mb-4">{copy.apiConfig.klingProvider}</h3>
              <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={() => handleChange("KLING_PROVIDER_MODE", "dashscope")} className={modeButtonClass(config.KLING_PROVIDER_MODE === "dashscope")}>
                    {copy.apiConfig.dashScope}
                  </button>
                  <button type="button" onClick={() => handleChange("KLING_PROVIDER_MODE", "vendor")} className={modeButtonClass(config.KLING_PROVIDER_MODE === "vendor")}>
                    {copy.apiConfig.vendorDirect}
                  </button>
                </div>
                <p className="text-xs text-gray-500">
                  {copy.apiConfig.klingProviderDescription}
                </p>
                {config.KLING_PROVIDER_MODE === "vendor" && (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.klingAccessKey} <span className="text-red-500">*</span></label>
                      <input type="password" value={config.KLING_ACCESS_KEY} onChange={(e) => handleChange("KLING_ACCESS_KEY", e.target.value)} placeholder={copy.apiConfig.klingAccessKey} className={inputClass} />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.klingSecretKey} <span className="text-red-500">*</span></label>
                      <input type="password" value={config.KLING_SECRET_KEY} onChange={(e) => handleChange("KLING_SECRET_KEY", e.target.value)} placeholder={copy.apiConfig.klingSecretKey} className={inputClass} />
                    </div>
                  </>
                )}
              </div>
            </div>

            <div className="pt-4 border-t border-white/10">
              <h3 className="text-sm font-bold text-white mb-4">{copy.apiConfig.viduProvider}</h3>
              <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={() => handleChange("VIDU_PROVIDER_MODE", "dashscope")} className={modeButtonClass(config.VIDU_PROVIDER_MODE === "dashscope")}>
                    {copy.apiConfig.dashScope}
                  </button>
                  <button type="button" onClick={() => handleChange("VIDU_PROVIDER_MODE", "vendor")} className={modeButtonClass(config.VIDU_PROVIDER_MODE === "vendor")}>
                    {copy.apiConfig.vendorDirect}
                  </button>
                </div>
                <p className="text-xs text-gray-500">
                  {copy.apiConfig.viduProviderDescription}
                </p>
                {config.VIDU_PROVIDER_MODE === "vendor" && (
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-2">{copy.apiConfig.viduApiKey} <span className="text-red-500">*</span></label>
                    <input type="password" value={config.VIDU_API_KEY} onChange={(e) => handleChange("VIDU_API_KEY", e.target.value)} placeholder={copy.apiConfig.viduApiKey} className={inputClass} />
                  </div>
                )}
              </div>
            </div>

            <div className="pt-4 border-t border-white/10">
              <button type="button" onClick={() => setEndpointsOpen(!endpointsOpen)} className="flex items-center gap-2 text-sm font-medium text-gray-400 hover:text-gray-200 transition-colors">
                {endpointsOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                {copy.apiConfig.advancedEndpoints}
              </button>
              {endpointsOpen && (
                <div className="mt-4 space-y-4">
                  <p className="text-xs text-gray-500">{copy.apiConfig.advancedEndpointsDescription}</p>
                  {ENDPOINT_PROVIDERS.map(({ key, label, placeholder }) => (
                    <div key={key}>
                      <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                        <span>{label} {copy.apiConfig.baseUrl}</span>
                        <span className="text-gray-600 font-normal text-xs">{placeholder}</span>
                      </label>
                      <input type="text" value={config.endpoint_overrides[key] || ""} onChange={(e) => handleEndpointChange(key, e.target.value)} placeholder={placeholder} className={inputClass + " text-sm"} />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex justify-end">
              <button
                onClick={handleSaveApiConfig}
                disabled={saving || loading}
                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
              >
                {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                {saving ? copy.apiConfig.savingConfiguration : copy.apiConfig.saveConfiguration}
              </button>
            </div>
          </>
        )}
      </section>

      {/* ── Section 2: Default Model Settings ── */}
      <section className="glass-panel rounded-xl p-6 space-y-6">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-gradient-to-br from-blue-500/20 to-purple-500/20 rounded-lg">
            <Settings size={20} className="text-blue-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">{copy.defaultModelSettings.title}</h2>
            <p className="text-xs text-gray-500">{copy.defaultModelSettings.subtitle}</p>
          </div>
        </div>

        <div className="space-y-5">
          <div className="flex items-center gap-2 text-sm font-bold text-white">
            <Image size={16} className="text-green-400" />
            <span>{copy.defaultModelSettings.textToImage}</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {T2I_MODELS.map((model) => (
              <button
                key={model.id}
                onClick={() => setModelSettings((s) => ({ ...s, t2i_model: model.id }))}
                className={`relative flex flex-col items-start p-3 rounded-lg border transition-all text-left ${modelSettings.t2i_model === model.id ? "border-green-500/50 bg-green-500/10" : "border-white/10 hover:border-white/20 bg-white/5"}`}
              >
                {modelSettings.t2i_model === model.id && <div className="absolute top-2 right-2"><Check size={14} className="text-green-400" /></div>}
                <span className="text-sm font-medium text-white">{model.name}</span>
                <span className="text-xs text-gray-500">{model.description}</span>
              </button>
            ))}
          </div>

          <div className="grid grid-cols-3 gap-4">
            {(
              [
                { key: "character_aspect_ratio" as const, label: copy.defaultModelSettings.character, icon: User },
                { key: "scene_aspect_ratio" as const, label: copy.defaultModelSettings.scene, icon: Building },
                { key: "prop_aspect_ratio" as const, label: copy.defaultModelSettings.prop, icon: Box },
              ] as const
            ).map(({ key, label, icon: Icon }) => (
              <div key={key} className="space-y-2">
                <div className="flex items-center gap-1 text-xs text-gray-400"><Icon size={12} /><label>{label}</label></div>
                <div className="space-y-1">
                  {ASPECT_RATIOS.map((ratio) => (
                    <button key={ratio.id} onClick={() => setModelSettings((s) => ({ ...s, [key]: ratio.id }))} className={`w-full flex flex-col items-center py-2 px-2 rounded border transition-all ${modelSettings[key] === ratio.id ? "border-green-500/50 bg-green-500/10" : "border-white/10 hover:border-white/20 bg-white/5"}`}>
                      <span className="text-xs font-medium text-white">{ratio.name}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="border-t border-white/10 pt-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <Layout size={16} className="text-blue-400" />
              <span>{copy.defaultModelSettings.storyboard}</span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {I2I_MODELS.map((model) => (
                <button key={model.id} onClick={() => setModelSettings((s) => ({ ...s, i2i_model: model.id }))} className={`relative flex flex-col items-start p-3 rounded-lg border transition-all text-left ${modelSettings.i2i_model === model.id ? "border-blue-500/50 bg-blue-500/10" : "border-white/10 hover:border-white/20 bg-white/5"}`}>
                  {modelSettings.i2i_model === model.id && <div className="absolute top-2 right-2"><Check size={14} className="text-blue-400" /></div>}
                  <span className="text-sm font-medium text-white">{model.name}</span>
                  <span className="text-xs text-gray-500">{model.description}</span>
                </button>
              ))}
            </div>
            <div className="mt-3 space-y-2">
              <label className="text-xs text-gray-400">{copy.defaultModelSettings.storyboardAspectRatio}</label>
              <div className="grid grid-cols-3 gap-2">
                {ASPECT_RATIOS.map((ratio) => (
                  <button key={ratio.id} onClick={() => setModelSettings((s) => ({ ...s, storyboard_aspect_ratio: ratio.id }))} className={`flex flex-col items-center p-3 rounded-lg border transition-all ${modelSettings.storyboard_aspect_ratio === ratio.id ? "border-blue-500/50 bg-blue-500/10" : "border-white/10 hover:border-white/20 bg-white/5"}`}>
                    <span className="text-sm font-medium text-white">{ratio.name}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="border-t border-white/10 pt-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <Video size={16} className="text-purple-400" />
              <span>{copy.defaultModelSettings.motion}</span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {I2V_MODELS.map((model) => (
                <button key={model.id} onClick={() => setModelSettings((s) => ({ ...s, i2v_model: model.id }))} className={`relative flex flex-col items-start p-3 rounded-lg border transition-all text-left ${modelSettings.i2v_model === model.id ? "border-purple-500/50 bg-purple-500/10" : "border-white/10 hover:border-white/20 bg-white/5"}`}>
                  {modelSettings.i2v_model === model.id && <div className="absolute top-2 right-2"><Check size={14} className="text-purple-400" /></div>}
                  <span className="text-sm font-medium text-white">{model.name}</span>
                  <span className="text-xs text-gray-500">{model.description}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex justify-end">
          <button onClick={handleSaveModelDefaults} className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white text-sm font-medium rounded-lg transition-all">
            <Save size={16} />
            {copy.defaultModelSettings.saveDefaults}
          </button>
        </div>
      </section>

      {/* ── Section 3: Default Prompt Config ── */}
      <section className="glass-panel rounded-xl p-6 space-y-6">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-purple-500/20 rounded-lg">
            <MessageSquareCode size={20} className="text-purple-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">{copy.defaultPromptConfig.title}</h2>
            <p className="text-xs text-gray-500">{copy.defaultPromptConfig.subtitle}</p>
          </div>
        </div>

        {(
          [
            { key: "storyboard_polish" as const, label: promptCopy.storyboardPolish.label, desc: promptCopy.storyboardPolish.description },
            { key: "video_polish" as const, label: promptCopy.videoI2vPolish.label, desc: promptCopy.videoI2vPolish.description },
            { key: "r2v_polish" as const, label: promptCopy.videoR2vPolish.label, desc: promptCopy.videoR2vPolish.description },
          ] as const
        ).map((section) => (
          <div key={section.key} className="space-y-2">
            <h3 className="text-sm font-bold text-white">{section.label}</h3>
            <p className="text-[10px] text-gray-500">{section.desc}</p>
            <textarea
              value={promptConfig[section.key]}
              onChange={(e) => setPromptConfig((prev) => ({ ...prev, [section.key]: e.target.value }))}
              placeholder={copy.defaultPromptConfig.leaveEmptyPlaceholder}
              className="w-full h-32 bg-black/30 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-y focus:outline-none focus:border-purple-500/50 font-mono placeholder-gray-600"
            />
          </div>
        ))}

        <div className="flex justify-end">
          <button onClick={handleSavePromptDefaults} className="px-6 py-2 text-sm font-medium bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors flex items-center gap-2">
            <Save size={16} />
            {copy.defaultPromptConfig.saveDefaults}
          </button>
        </div>
      </section>

      <div className="pb-8" />
    </div>
  );
}
