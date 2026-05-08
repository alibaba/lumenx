"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Save, ChevronDown, ChevronRight, Loader2, Key } from "lucide-react";
import { api } from "@/lib/api";
import {
  DEFAULT_ENV_CONFIG,
  ENDPOINT_PROVIDERS,
  OPENAI_MULTIMODAL_MODEL_PRESETS,
  OPENAI_TTS_MODEL_PRESETS,
  normalizeEnvConfig,
  type EnvConfigState,
} from "@/lib/env-config";
import { messages } from "@/lib/i18n";

interface EnvConfigDialogProps {
  isOpen: boolean;
  onClose: () => void;
  isRequired?: boolean;
}

export default function EnvConfigDialog({ isOpen, onClose }: EnvConfigDialogProps) {
  const copy = messages.settingsPage.apiConfig;
  const commonActions = messages.common.actions;
  const commonMessages = messages.common.messages;
  const [config, setConfig] = useState<EnvConfigState>(DEFAULT_ENV_CONFIG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [endpointsOpen, setEndpointsOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.getEnvConfig();
      setConfig((prev) => normalizeEnvConfig(prev, data));
    } catch (error) {
      console.error("Failed to load env config:", error);
      setLoadError(copy.loadError);
    } finally {
      setLoading(false);
    }
  }, [copy.loadError]);

  useEffect(() => {
    if (isOpen) {
      loadConfig();
    }
  }, [isOpen, loadConfig]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.saveEnvConfig(config);
      alert(commonMessages.configurationSaved);
      onClose();
    } catch (error) {
      console.error("Failed to save env config:", error);
      alert(commonMessages.saveFailed);
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

  const requestClose = () => {
    onClose();
  };

  if (!isOpen) return null;

  const inputClass = "w-full bg-black/30 border border-white/10 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary/50 transition-colors";
  const modeButtonClass = (active: boolean) =>
    `px-3 py-1.5 text-xs rounded-md border transition-colors ${active ? "border-amber-500/60 bg-amber-500/15 text-amber-200" : "border-white/10 bg-white/5 text-gray-400 hover:text-gray-200"}`;
  const presetButtonClass =
    "px-2.5 py-1 text-[11px] rounded-md border border-white/10 bg-white/5 text-gray-300 hover:text-white hover:border-white/20 transition-colors";
  const isTosStorage = config.OBJECT_STORAGE_PROVIDER === "tos";
  const objectStorageEndpointPlaceholder = isTosStorage
    ? "https://tos-cn-beijing.volces.com"
    : "https://oss-cn-beijing.aliyuncs.com";
  const showImage2KeyWarning =
    config.IMAGE_PROVIDER === "openai" &&
    config.OPENAI_IMAGE_BASE_URL.includes("api.bltcy.ai") &&
    !config.OPENAI_IMAGE_API_KEY.trim() &&
    !config.OPENAI_IMAGE_EDIT_API_KEY.trim() &&
    !!config.OPENAI_API_KEY.trim();
  const showImage2EditKeyWarning =
    config.IMAGE_EDIT_PROVIDER === "openai" &&
    config.OPENAI_IMAGE_EDIT_BASE_URL.includes("api.bltcy.ai") &&
    !config.OPENAI_IMAGE_EDIT_API_KEY.trim() &&
    !config.OPENAI_IMAGE_API_KEY.trim() &&
    !!config.OPENAI_API_KEY.trim();

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
        onClick={requestClose}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.95 }}
          className="bg-[#1a1a1a] rounded-2xl border border-white/10 w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between p-6 border-b border-white/10">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-gradient-to-br from-amber-500/20 to-orange-500/20 rounded-lg">
                <Key size={20} className="text-amber-400" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-white">{copy.entryDialogTitle}</h2>
                <p className="text-xs text-gray-500">{copy.entryDialogSubtitle}</p>
              </div>
            </div>
            <button
              onClick={requestClose}
              className="p-2 hover:bg-white/10 rounded-lg transition-colors"
            >
              <X size={20} className="text-gray-400" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
              {copy.entryDialogNotice}
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={24} className="animate-spin text-amber-400" />
                <span className="ml-2 text-gray-400">{commonMessages.loadingConfiguration}</span>
              </div>
            ) : loadError ? (
              <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
                {loadError}
              </div>
            ) : (
              <>
                <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                  <div>
                    <h3 className="text-sm font-bold text-white mb-2">{copy.llmProviderTitle}</h3>
                    <p className="text-xs text-gray-500">{copy.llmProviderDescription}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => handleChange("LLM_PROVIDER", "openai")}
                      className={modeButtonClass(config.LLM_PROVIDER === "openai")}
                    >
                      {copy.openAiCompatible}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleChange("LLM_PROVIDER", "dashscope")}
                      className={modeButtonClass(config.LLM_PROVIDER === "dashscope")}
                    >
                      {copy.dashScope}
                    </button>
                  </div>

                  {config.LLM_PROVIDER === "openai" ? (
                    <>
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>{copy.openAiApiKey}</span>
                          <span className="text-gray-600 font-normal text-xs">{copy.openAiApiKeyHint}</span>
                        </label>
                        <input
                          type="password"
                          value={config.OPENAI_API_KEY}
                          onChange={(e) => handleChange("OPENAI_API_KEY", e.target.value)}
                          placeholder={copy.fillLater}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>{copy.openAiBaseUrl}</span>
                          <span className="text-gray-600 font-normal text-xs">{copy.openAiBaseUrlHint}</span>
                        </label>
                        <input
                          type="text"
                          value={config.OPENAI_BASE_URL}
                          onChange={(e) => handleChange("OPENAI_BASE_URL", e.target.value)}
                          placeholder={DEFAULT_ENV_CONFIG.OPENAI_BASE_URL}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>{copy.openAiModel}</span>
                          <span className="text-gray-600 font-normal text-xs">{copy.openAiModelHint}</span>
                        </label>
                        <input
                          type="text"
                          value={config.OPENAI_MODEL}
                          onChange={(e) => handleChange("OPENAI_MODEL", e.target.value)}
                          placeholder={DEFAULT_ENV_CONFIG.OPENAI_MODEL}
                          className={inputClass}
                        />
                      </div>
                    </>
                  ) : (
                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                        <span>{copy.dashScopeApiKey}</span>
                        <span className="text-gray-600 font-normal text-xs">{copy.dashScopeOptionalHint}</span>
                      </label>
                      <input
                        type="password"
                        value={config.DASHSCOPE_API_KEY}
                        onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)}
                        placeholder={copy.fillLater}
                        className={inputClass}
                      />
                    </div>
                  )}
                </div>

                <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                  <div>
                    <h3 className="text-sm font-bold text-white mb-2">生图 Provider</h3>
                    <p className="text-xs text-gray-500">文生图和图编已经拆成两条独立链路。两把图像 key 会互相兜底：生图优先图像 Key，图编优先图像编辑 Key。</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => handleChange("IMAGE_PROVIDER", "openai")}
                      className={modeButtonClass(config.IMAGE_PROVIDER === "openai")}
                    >
                      OpenAI-compatible
                    </button>
                    <button
                      type="button"
                      onClick={() => handleChange("IMAGE_PROVIDER", "dashscope")}
                      className={modeButtonClass(config.IMAGE_PROVIDER === "dashscope")}
                    >
                      DashScope 兼容
                    </button>
                  </div>

                {config.IMAGE_PROVIDER === "openai" ? (
                  <>
                    <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                        Image2 首选（`gpt-image2`）同时支持 `/v1/images/generations` 和 `/v1/images/edits`。当前建议把 `gpt-image2` 作为首选模型，备用测试别名不要覆盖主配置。
                    </div>
                    {showImage2KeyWarning ? (
                      <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-3 text-xs text-amber-200">
                          当前主用和备用图像 Key 都是空的，后端会暂时沿用上面的 `OPENAI_API_KEY`。如果那把 key 不是 `api.bltcy.ai` 的专用 key，就会出现“无效令牌”。请至少保留一把图像专用 key。
                      </div>
                      ) : null}
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>图像 API Key（主用）</span>
                          <span className="text-gray-600 font-normal text-xs">优先生图；留空则回退图编 Key / OPENAI_API_KEY</span>
                        </label>
                        <input
                          type="password"
                          value={config.OPENAI_IMAGE_API_KEY}
                          onChange={(e) => handleChange("OPENAI_IMAGE_API_KEY", e.target.value)}
                          placeholder={copy.fillLater}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>图像 Base URL（可选）</span>
                          <span className="text-gray-600 font-normal text-xs">填写到 /v1 即可，例如 https://yunwu.ai/v1</span>
                        </label>
                        <input
                          type="text"
                          value={config.OPENAI_IMAGE_BASE_URL}
                          onChange={(e) => handleChange("OPENAI_IMAGE_BASE_URL", e.target.value)}
                          placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_BASE_URL}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>生图模型</span>
                          <span className="text-gray-600 font-normal text-xs">首选 gpt-image2；备用测试别名不要覆盖主配置</span>
                        </label>
                        <input
                          type="text"
                          value={config.OPENAI_IMAGE_MODEL}
                          onChange={(e) => handleChange("OPENAI_IMAGE_MODEL", e.target.value)}
                          placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_MODEL}
                          className={inputClass}
                        />
                      </div>
                    </>
                  ) : (
                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                        <span>{copy.dashScopeApiKey}</span>
                        <span className="text-gray-600 font-normal text-xs">仅旧版 Wan 文生图兼容链路需要</span>
                      </label>
                      <input
                        type="password"
                        value={config.DASHSCOPE_API_KEY}
                        onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)}
                        placeholder={copy.fillLater}
                        className={inputClass}
                      />
                    </div>
                  )}

                  <div className="border-t border-white/10 pt-4 space-y-4">
                    <div>
                      <h4 className="text-sm font-bold text-white mb-2">图编 Provider</h4>
                      <p className="text-xs text-gray-500">分镜重绘、首帧重绘和参考图编辑可单独指定接口，不会影响文生图链路。</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleChange("IMAGE_EDIT_PROVIDER", "openai")}
                        className={modeButtonClass(config.IMAGE_EDIT_PROVIDER === "openai")}
                      >
                        OpenAI-compatible
                      </button>
                      <button
                        type="button"
                        onClick={() => handleChange("IMAGE_EDIT_PROVIDER", "dashscope")}
                        className={modeButtonClass(config.IMAGE_EDIT_PROVIDER === "dashscope")}
                      >
                        DashScope 兼容
                      </button>
                    </div>

                    {config.IMAGE_EDIT_PROVIDER === "openai" ? (
                      <>
                        <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                          默认图编也首选 Image2（`gpt-image2`），但你现在可以把图编单独切到另一家更稳的 OpenAI-compatible `/images/edits` 接口，不影响文生图。
                        </div>
                        {showImage2EditKeyWarning ? (
                          <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-3 text-xs text-amber-200">
                            当前主用和备用图像 Key 都是空的，后端会回退 `OPENAI_API_KEY`。如果 Image2 图编链路仍然不稳，请在这里保留一把图编专用 key，作为重绘优先 key。
                          </div>
                        ) : null}
                        <div>
                          <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                            <span>图像编辑 API Key（图编主用 / 生图备用）</span>
                            <span className="text-gray-600 font-normal text-xs">优先重绘；留空则回退图像 Key / OPENAI_API_KEY</span>
                          </label>
                          <input
                            type="password"
                            value={config.OPENAI_IMAGE_EDIT_API_KEY}
                            onChange={(e) => handleChange("OPENAI_IMAGE_EDIT_API_KEY", e.target.value)}
                            placeholder={copy.fillLater}
                            className={inputClass}
                          />
                        </div>
                        <div>
                          <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                            <span>图编 Base URL（可选）</span>
                            <span className="text-gray-600 font-normal text-xs">填写到 /v1 即可，后端会拼 `/images/edits`</span>
                          </label>
                          <input
                            type="text"
                            value={config.OPENAI_IMAGE_EDIT_BASE_URL}
                            onChange={(e) => handleChange("OPENAI_IMAGE_EDIT_BASE_URL", e.target.value)}
                            placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_EDIT_BASE_URL}
                            className={inputClass}
                          />
                        </div>
                        <div>
                          <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                            <span>图编模型（可选）</span>
                            <span className="text-gray-600 font-normal text-xs">首选 gpt-image2；支持 `/images/edits`</span>
                          </label>
                          <input
                            type="text"
                            value={config.OPENAI_IMAGE_EDIT_MODEL}
                            onChange={(e) => handleChange("OPENAI_IMAGE_EDIT_MODEL", e.target.value)}
                            placeholder={DEFAULT_ENV_CONFIG.OPENAI_IMAGE_EDIT_MODEL}
                            className={inputClass}
                          />
                        </div>
                      </>
                    ) : (
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>图编 DashScope API Key</span>
                          <span className="text-gray-600 font-normal text-xs">切回 Wan 图编时使用，与旧版兼容链路共用</span>
                        </label>
                        <input
                          type="password"
                          value={config.DASHSCOPE_API_KEY}
                          onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)}
                          placeholder={copy.fillLater}
                          className={inputClass}
                        />
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
                    <button
                      type="button"
                      onClick={() => handleChange("TTS_PROVIDER", "openai")}
                      className={modeButtonClass(config.TTS_PROVIDER === "openai")}
                    >
                      OpenAI-compatible
                    </button>
                    <button
                      type="button"
                      onClick={() => handleChange("TTS_PROVIDER", "dashscope")}
                      className={modeButtonClass(config.TTS_PROVIDER === "dashscope")}
                    >
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
                        <input
                          type="password"
                          value={config.OPENAI_TTS_API_KEY}
                          onChange={(e) => handleChange("OPENAI_TTS_API_KEY", e.target.value)}
                          placeholder={copy.fillLater}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>TTS Base URL（可选）</span>
                          <span className="text-gray-600 font-normal text-xs">留空则沿用 OPENAI_BASE_URL</span>
                        </label>
                        <input
                          type="text"
                          value={config.OPENAI_TTS_BASE_URL}
                          onChange={(e) => handleChange("OPENAI_TTS_BASE_URL", e.target.value)}
                          placeholder={DEFAULT_ENV_CONFIG.OPENAI_TTS_BASE_URL}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>配音模型</span>
                          <span className="text-gray-600 font-normal text-xs">例如 gpt-4o-mini-tts / 兼容的中转语音模型</span>
                        </label>
                        <input
                          type="text"
                          value={config.OPENAI_TTS_MODEL}
                          onChange={(e) => handleChange("OPENAI_TTS_MODEL", e.target.value)}
                          placeholder={DEFAULT_ENV_CONFIG.OPENAI_TTS_MODEL}
                          className={inputClass}
                        />
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
                        <span>{copy.dashScopeApiKey}</span>
                        <span className="text-gray-600 font-normal text-xs">仅 DashScope CosyVoice 兼容链路需要</span>
                      </label>
                      <input
                        type="password"
                        value={config.DASHSCOPE_API_KEY}
                        onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)}
                        placeholder={copy.fillLater}
                        className={inputClass}
                      />
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
                    <input
                      type="password"
                      value={config.OPENAI_MULTIMODAL_API_KEY}
                      onChange={(e) => handleChange("OPENAI_MULTIMODAL_API_KEY", e.target.value)}
                      placeholder={copy.fillLater}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>多模态 Base URL（可选）</span>
                      <span className="text-gray-600 font-normal text-xs">留空则沿用 OPENAI_BASE_URL</span>
                    </label>
                    <input
                      type="text"
                      value={config.OPENAI_MULTIMODAL_BASE_URL}
                      onChange={(e) => handleChange("OPENAI_MULTIMODAL_BASE_URL", e.target.value)}
                      placeholder={DEFAULT_ENV_CONFIG.OPENAI_MULTIMODAL_BASE_URL}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>多模态模型</span>
                      <span className="text-gray-600 font-normal text-xs">例如 qwen-vl-max / 兼容视觉模型</span>
                    </label>
                    <input
                      type="text"
                      value={config.OPENAI_MULTIMODAL_MODEL}
                      onChange={(e) => handleChange("OPENAI_MULTIMODAL_MODEL", e.target.value)}
                      placeholder={DEFAULT_ENV_CONFIG.OPENAI_MULTIMODAL_MODEL}
                      className={inputClass}
                    />
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
                    <span>{copy.arkApiKey}</span>
                    <span className="text-gray-600 font-normal text-xs">{copy.arkApiKeyHint}</span>
                  </label>
                  <input
                    type="password"
                    value={config.ARK_API_KEY}
                    onChange={(e) => handleChange("ARK_API_KEY", e.target.value)}
                    placeholder={copy.fillLater}
                    className={inputClass}
                  />
                </div>

                <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                  <div>
                    <h3 className="text-sm font-bold text-white mb-2">{copy.storageTitle}</h3>
                    <p className="text-xs text-gray-500">{copy.storageDescription}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => handleChange("OBJECT_STORAGE_PROVIDER", "")}
                      className={modeButtonClass(config.OBJECT_STORAGE_PROVIDER === "")}
                    >
                      {copy.localOnly}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleChange("OBJECT_STORAGE_PROVIDER", "tos")}
                      className={modeButtonClass(config.OBJECT_STORAGE_PROVIDER === "tos")}
                    >
                      {copy.byteTos}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleChange("OBJECT_STORAGE_PROVIDER", "oss")}
                      className={modeButtonClass(config.OBJECT_STORAGE_PROVIDER === "oss")}
                    >
                      {copy.alibabaOss}
                    </button>
                  </div>

                  {config.OBJECT_STORAGE_PROVIDER !== "" && (
                    <div className="space-y-4 pt-2 border-t border-white/10">
                      <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-200">
                        {copy.privateBucketNotice}
                      </div>

                      {config.OBJECT_STORAGE_PROVIDER === "tos" ? (
                        <>
                          <div>
                            <label className="block text-sm font-medium text-gray-300 mb-2">
                              {copy.tosAccessKeyId}
                            </label>
                            <input
                              type="password"
                              value={config.TOS_ACCESS_KEY_ID}
                              onChange={(e) => handleChange("TOS_ACCESS_KEY_ID", e.target.value)}
                              placeholder={copy.tosAccessKeyId}
                              className={inputClass}
                            />
                          </div>
                          <div>
                            <label className="block text-sm font-medium text-gray-300 mb-2">
                              {copy.tosSecretAccessKey}
                            </label>
                            <input
                              type="password"
                              value={config.TOS_SECRET_ACCESS_KEY}
                              onChange={(e) => handleChange("TOS_SECRET_ACCESS_KEY", e.target.value)}
                              placeholder={copy.tosSecretAccessKey}
                              className={inputClass}
                            />
                          </div>
                        </>
                      ) : (
                        <>
                          <div>
                            <label className="block text-sm font-medium text-gray-300 mb-2">
                              {copy.alibabaAccessKeyId}
                            </label>
                            <input
                              type="password"
                              value={config.ALIBABA_CLOUD_ACCESS_KEY_ID}
                              onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_ID", e.target.value)}
                              placeholder={copy.alibabaAccessKeyId}
                              className={inputClass}
                            />
                          </div>
                          <div>
                            <label className="block text-sm font-medium text-gray-300 mb-2">
                              {copy.alibabaAccessKeySecret}
                            </label>
                            <input
                              type="password"
                              value={config.ALIBABA_CLOUD_ACCESS_KEY_SECRET}
                              onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_SECRET", e.target.value)}
                              placeholder={copy.alibabaAccessKeySecret}
                              className={inputClass}
                            />
                          </div>
                        </>
                      )}

                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>{copy.bucketName}</span>
                          <span className="text-gray-600 font-normal text-xs">{copy.bucketNameHint}</span>
                        </label>
                        <input
                          type="text"
                          value={config.OBJECT_STORAGE_BUCKET_NAME}
                          onChange={(e) => {
                            handleChange("OBJECT_STORAGE_BUCKET_NAME", e.target.value);
                            handleChange("OSS_BUCKET_NAME", e.target.value);
                          }}
                          placeholder={copy.bucketName}
                          className={inputClass}
                        />
                      </div>

                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>{copy.endpoint}</span>
                          <span className="text-gray-600 font-normal text-xs">
                            {isTosStorage ? copy.tosEndpointHint : copy.ossEndpointHint}
                          </span>
                        </label>
                        <input
                          type="text"
                          value={config.OBJECT_STORAGE_ENDPOINT}
                          onChange={(e) => {
                            handleChange("OBJECT_STORAGE_ENDPOINT", e.target.value);
                            handleChange("OSS_ENDPOINT", e.target.value);
                          }}
                          placeholder={objectStorageEndpointPlaceholder}
                          className={inputClass}
                        />
                      </div>

                      {config.OBJECT_STORAGE_PROVIDER === "tos" && (
                        <div>
                          <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                            <span>{copy.region}</span>
                            <span className="text-gray-600 font-normal text-xs">{copy.regionHint}</span>
                          </label>
                          <input
                            type="text"
                            value={config.OBJECT_STORAGE_REGION}
                            onChange={(e) => handleChange("OBJECT_STORAGE_REGION", e.target.value)}
                            placeholder={DEFAULT_ENV_CONFIG.OBJECT_STORAGE_REGION}
                            className={inputClass}
                          />
                        </div>
                      )}

                      <div>
                        <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                          <span>{copy.uploadPrefix}</span>
                          <span className="text-gray-600 font-normal text-xs">{copy.uploadPrefixHint}</span>
                        </label>
                        <input
                          type="text"
                          value={config.OBJECT_STORAGE_BASE_PATH}
                          onChange={(e) => {
                            handleChange("OBJECT_STORAGE_BASE_PATH", e.target.value);
                            handleChange("OSS_BASE_PATH", e.target.value);
                          }}
                          placeholder={DEFAULT_ENV_CONFIG.OBJECT_STORAGE_BASE_PATH}
                          className={inputClass}
                        />
                      </div>
                    </div>
                  )}
                </div>

                <div className="pt-4 border-t border-white/10">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-bold text-white">{copy.klingProvider}</h3>
                    <span className="text-[10px] text-gray-500">{copy.providerModeHint}</span>
                  </div>
                  <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleChange("KLING_PROVIDER_MODE", "dashscope")}
                        className={modeButtonClass(config.KLING_PROVIDER_MODE === "dashscope")}
                      >
                        {copy.dashScope}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleChange("KLING_PROVIDER_MODE", "vendor")}
                        className={modeButtonClass(config.KLING_PROVIDER_MODE === "vendor")}
                      >
                        {copy.vendorDirect}
                      </button>
                    </div>
                    <p className="text-xs text-gray-500">
                      {copy.klingProviderDescription}
                    </p>

                    {config.KLING_PROVIDER_MODE === "vendor" && (
                      <>
                        <div>
                          <label className="block text-sm font-medium text-gray-300 mb-2">
                            {copy.klingAccessKey} <span className="text-red-500">*</span>
                          </label>
                          <input
                            type="password"
                            value={config.KLING_ACCESS_KEY}
                            onChange={(e) => handleChange("KLING_ACCESS_KEY", e.target.value)}
                            placeholder={copy.klingAccessKey}
                            className={inputClass}
                          />
                        </div>

                        <div>
                          <label className="block text-sm font-medium text-gray-300 mb-2">
                            {copy.klingSecretKey} <span className="text-red-500">*</span>
                          </label>
                          <input
                            type="password"
                            value={config.KLING_SECRET_KEY}
                            onChange={(e) => handleChange("KLING_SECRET_KEY", e.target.value)}
                            placeholder={copy.klingSecretKey}
                            className={inputClass}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <div className="pt-4 border-t border-white/10">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-bold text-white">{copy.viduProvider}</h3>
                    <span className="text-[10px] text-gray-500">{copy.providerModeHint}</span>
                  </div>
                  <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleChange("VIDU_PROVIDER_MODE", "dashscope")}
                        className={modeButtonClass(config.VIDU_PROVIDER_MODE === "dashscope")}
                      >
                        {copy.dashScope}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleChange("VIDU_PROVIDER_MODE", "vendor")}
                        className={modeButtonClass(config.VIDU_PROVIDER_MODE === "vendor")}
                      >
                        {copy.vendorDirect}
                      </button>
                    </div>
                    <p className="text-xs text-gray-500">
                      {copy.viduProviderDescription}
                    </p>

                    {config.VIDU_PROVIDER_MODE === "vendor" && (
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-2">
                          {copy.viduApiKey} <span className="text-red-500">*</span>
                        </label>
                        <input
                          type="password"
                          value={config.VIDU_API_KEY}
                          onChange={(e) => handleChange("VIDU_API_KEY", e.target.value)}
                          placeholder={copy.viduApiKey}
                          className={inputClass}
                        />
                      </div>
                    )}
                  </div>
                </div>

                <div className="pt-4 border-t border-white/10">
                  <button
                    type="button"
                    onClick={() => setEndpointsOpen(!endpointsOpen)}
                    aria-expanded={endpointsOpen}
                    className="flex items-center gap-2 text-sm font-medium text-gray-400 hover:text-gray-200 transition-colors"
                  >
                    {endpointsOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    {copy.advancedEndpoints}
                  </button>

                  {endpointsOpen && (
                    <div className="mt-4 space-y-4">
                      <p className="text-xs text-gray-500">{copy.advancedEndpointsDescription}</p>
                      {ENDPOINT_PROVIDERS.map(({ key, label, placeholder }) => (
                        <div key={key}>
                          <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                            <span>{label} {copy.baseUrl}</span>
                            <span className="text-gray-600 font-normal text-xs">{placeholder}</span>
                          </label>
                          <input
                            type="text"
                            value={config.endpoint_overrides[key] || ""}
                            onChange={(e) => handleEndpointChange(key, e.target.value)}
                            placeholder={placeholder}
                            className={inputClass + " text-sm"}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          <div className="flex justify-end gap-3 p-6 border-t border-white/10">
            <button
              onClick={requestClose}
              className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
            >
              {commonActions.close}
            </button>
            <button
              onClick={handleSave}
              disabled={saving || loading || !!loadError}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
              >
              {saving ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  {copy.savingConfiguration}
                </>
              ) : (
                <>
                  <Save size={16} />
                  {copy.saveConfiguration}
                </>
              )}
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
