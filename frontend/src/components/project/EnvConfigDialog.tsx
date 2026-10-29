"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Save, ChevronDown, ChevronRight, Loader2, Key } from "lucide-react";
import { useTranslations } from "next-intl";
import { api, type EnvConfigPayload, type ProviderMode } from "@/lib/api";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment, type RuntimeEnvironment } from "@/lib/runtimeEnvironment";

interface EnvConfigDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

type EnvConfigOwner = { key: string; generation: number };

type EnvConfig = EnvConfigPayload & {
  DASHSCOPE_API_KEY: string;
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
  MULEROUTER_API_KEY: string;
  MULERUN_CLI_LOGGED_IN?: boolean;
  endpoint_overrides: Record<string, string>;
};

const ENDPOINT_PROVIDERS = [
  { key: "DASHSCOPE_BASE_URL", label: "DashScope", placeholder: "https://dashscope.aliyuncs.com" },
  { key: "KLING_BASE_URL", label: "Kling", placeholder: "https://api-beijing.klingai.com/v1" },
  { key: "VIDU_BASE_URL", label: "Vidu", placeholder: "https://api.vidu.cn/ent/v2" },
  { key: "MULEROUTER_BASE_URL", label: "MuleRouter", placeholder: "https://api.mulerouter.ai" },
];

const DEFAULT_CONFIG: EnvConfig = {
  DASHSCOPE_API_KEY: "",
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
  MULEROUTER_API_KEY: "",
  endpoint_overrides: {},
};

const normalizeProviderMode = (mode?: string): ProviderMode => (mode === "vendor" ? "vendor" : "dashscope");

const normalizeEnvConfig = (existing: EnvConfig, data?: EnvConfigPayload): EnvConfig => ({
  ...existing,
  ...data,
  KLING_PROVIDER_MODE: normalizeProviderMode(data?.KLING_PROVIDER_MODE ?? existing.KLING_PROVIDER_MODE),
  VIDU_PROVIDER_MODE: normalizeProviderMode(data?.VIDU_PROVIDER_MODE ?? existing.VIDU_PROVIDER_MODE),
  PIXVERSE_PROVIDER_MODE: normalizeProviderMode(data?.PIXVERSE_PROVIDER_MODE ?? existing.PIXVERSE_PROVIDER_MODE),
  endpoint_overrides: data?.endpoint_overrides ?? existing.endpoint_overrides ?? {},
});

const getValidationErrors = (env: EnvConfig): string[] => {
  const errors: string[] = [];

  if (env.KLING_PROVIDER_MODE === "vendor") {
    if (!env.KLING_ACCESS_KEY?.trim()) {
      errors.push("Kling Access Key (vendor mode)");
    }
    if (!env.KLING_SECRET_KEY?.trim()) {
      errors.push("Kling Secret Key (vendor mode)");
    }
  }
  if (env.VIDU_PROVIDER_MODE === "vendor" && !env.VIDU_API_KEY?.trim()) {
    errors.push("Vidu API Key (vendor mode)");
  }

  return errors;
};

export default function EnvConfigDialog({ isOpen, onClose }: EnvConfigDialogProps) {
  const [config, setConfig] = useState<EnvConfig>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [endpointsOpen, setEndpointsOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [configOwner, setConfigOwner] = useState<EnvConfigOwner | null>(null);
  const configOwnerRef = useRef<EnvConfigOwner | null>(null);
  const configLoadGenerationRef = useRef(0);
  const dialogSessionRef = useRef(0);
  const mountedRef = useRef(false);
  const openRef = useRef(isOpen);
  const savingSessionRef = useRef<number | null>(null);
  const [runtimeEnvironment, setRuntimeEnvironment] = useState(() => getRuntimeEnvironment());
  const t = useTranslations("project");
  const tc = useTranslations("common");
  const tRef = useRef(t);
  tRef.current = t;

  const setLoadedConfigOwner = useCallback((owner: EnvConfigOwner | null) => {
    configOwnerRef.current = owner;
    setConfigOwner(owner);
  }, []);

  const loadConfig = useCallback(async (
    environment: RuntimeEnvironment | null = getRuntimeEnvironment(),
    dialogSession = dialogSessionRef.current,
  ) => {
    const loadGeneration = configLoadGenerationRef.current + 1;
    configLoadGenerationRef.current = loadGeneration;
    setLoadedConfigOwner(null);
    setConfig(DEFAULT_CONFIG);

    const sessionStillCurrent = () => (
      mountedRef.current
      && openRef.current
      && dialogSession === dialogSessionRef.current
      && loadGeneration === configLoadGenerationRef.current
    );

    if (!environment) {
      if (sessionStillCurrent()) {
        setLoading(false);
        setLoadError(tRef.current("configLoadFailed"));
      }
      return;
    }

    const owner = { key: environment.key, generation: environment.generation };
    const stillCurrent = () => (
      sessionStillCurrent()
      && isCurrentRuntimeEnvironment(owner.key, owner.generation)
    );

    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.getEnvConfig();
      if (!stillCurrent()) return;
      setConfig(normalizeEnvConfig(DEFAULT_CONFIG, data));
      setLoadedConfigOwner(owner);
    } catch (error) {
      if (stillCurrent()) {
        console.error("Failed to load env config:", error);
        setLoadError(tRef.current("configLoadFailed"));
        setLoadedConfigOwner(null);
      }
    } finally {
      if (stillCurrent()) setLoading(false);
    }
  }, [setLoadedConfigOwner]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      openRef.current = false;
      dialogSessionRef.current += 1;
      configLoadGenerationRef.current += 1;
      savingSessionRef.current = null;
      configOwnerRef.current = null;
    };
  }, []);

  useEffect(() => subscribeRuntimeEnvironment(() => {
    setRuntimeEnvironment(getRuntimeEnvironment());
  }), []);

  useEffect(() => {
    openRef.current = isOpen;
    dialogSessionRef.current += 1;
    configLoadGenerationRef.current += 1;
    savingSessionRef.current = null;
    setSaving(false);

    if (isOpen) {
      const dialogSession = dialogSessionRef.current;
      void loadConfig(runtimeEnvironment, dialogSession);
    } else {
      setLoadedConfigOwner(null);
    }
  }, [isOpen, runtimeEnvironment, loadConfig, setLoadedConfigOwner]);

  const handleSave = async () => {
    const dialogSession = dialogSessionRef.current;
    if (savingSessionRef.current !== null) return;

    const owner = configOwnerRef.current;
    const stillCurrent = () => (
      mountedRef.current
      && openRef.current
      && dialogSession === dialogSessionRef.current
      && !!owner
      && isCurrentRuntimeEnvironment(owner.key, owner.generation)
    );

    if (!stillCurrent()) {
      alert(tRef.current("configSaveFailed"));
      return;
    }
    const errors = getValidationErrors(config);
    if (errors.length > 0) {
      alert(tRef.current("requiredFields") + "\n- " + errors.join("\n- "));
      return;
    }

    savingSessionRef.current = dialogSession;
    setSaving(true);
    try {
      await api.saveEnvConfig(config);
      if (!stillCurrent()) return;
      alert(tRef.current("configSaved"));
      onClose();
    } catch (error) {
      if (!stillCurrent()) return;
      console.error("Failed to save env config:", error);
      alert(tRef.current("configSaveFailed"));
    } finally {
      if (savingSessionRef.current === dialogSession) {
        savingSessionRef.current = null;
        if (stillCurrent()) setSaving(false);
      }
    }
  };

  const handleChange = (key: keyof EnvConfig, value: string) => {
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

  const inputClass = "w-full bg-surface border border-glass-border rounded-lg px-4 py-2 text-foreground placeholder-text-muted focus:outline-none focus:border-primary/50 transition-colors";
  const modeButtonClass = (active: boolean) =>
    `px-3 py-1.5 text-xs rounded-md border transition-colors font-medium ${active ? "bg-amber-500 text-white border-amber-500 shadow-sm" : "border-glass-border bg-surface text-text-secondary hover:text-foreground"}`;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm p-4"
        onClick={requestClose}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.95 }}
          className="bg-elevated rounded-2xl border border-glass-border w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between p-6 border-b border-glass-border">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-gradient-to-br from-amber-500/20 to-orange-500/20 rounded-lg">
                <Key size={20} className="text-amber-400" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-foreground">{t("envConfig")}</h2>
                <p className="text-xs text-text-muted">{t("envConfigSub")}</p>
              </div>
            </div>
            <button
              onClick={requestClose}
              className="p-2 hover:bg-hover-bg rounded-lg transition-colors"
            >
              <X size={20} className="text-text-secondary" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={24} className="animate-spin text-amber-400" />
                <span className="ml-2 text-text-secondary">{t("loadingConfig")}</span>
              </div>
            ) : loadError ? (
              <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
                {loadError}
              </div>
            ) : (
              <>
                <div>
                  <label className="flex items-center justify-between text-sm font-medium text-foreground mb-2">
                    <span>DashScope API Key</span>
                    <span className="text-text-muted font-normal text-xs">e.g. sk-xxx</span>
                  </label>
                  <input
                    type="password"
                    value={config.DASHSCOPE_API_KEY}
                    onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)}
                    placeholder={t("dashscopeKeyUsageHint")}
                    className={inputClass}
                  />
                </div>

                <div className="bg-glass border border-glass-border rounded-lg p-4 space-y-4">
                  <div className="text-xs text-text-secondary">
                    {t("ossLocalFirst")}
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-foreground mb-2">
                      Alibaba Cloud Access Key ID
                    </label>
                    <input
                      type="password"
                      value={config.ALIBABA_CLOUD_ACCESS_KEY_ID}
                      onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_ID", e.target.value)}
                      placeholder="Optional, used when OSS mirror is enabled"
                      className={inputClass}
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-foreground mb-2">
                      Alibaba Cloud Access Key Secret
                    </label>
                    <input
                      type="password"
                      value={config.ALIBABA_CLOUD_ACCESS_KEY_SECRET}
                      onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_SECRET", e.target.value)}
                      placeholder="Optional, used when OSS mirror is enabled"
                      className={inputClass}
                    />
                  </div>
                </div>

                <div className="pt-4 border-t border-glass-border">
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h3 className="text-sm font-bold text-foreground">{t("ossMirror")}</h3>
                      <p className="text-[0.625rem] text-text-muted mt-1">{t("ossMirrorDesc")}</p>
                    </div>
                    <a
                      href="https://oss.console.aliyun.com/overview"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-primary hover:text-primary/80 transition-colors"
                    >
                      Open OSS Console &rarr;
                    </a>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-foreground mb-2">
                        <span>OSS Bucket Name</span>
                        <span className="text-text-muted font-normal text-xs">e.g. my-comic-bucket</span>
                      </label>
                      <input
                        type="text"
                        value={config.OSS_BUCKET_NAME}
                        onChange={(e) => handleChange("OSS_BUCKET_NAME", e.target.value)}
                        placeholder="your_bucket_name (optional)"
                        className={inputClass}
                      />
                    </div>

                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-foreground mb-2">
                        <span>OSS Endpoint</span>
                        <span className="text-text-muted font-normal text-xs">e.g. oss-cn-hangzhou.aliyuncs.com</span>
                      </label>
                      <input
                        type="text"
                        value={config.OSS_ENDPOINT}
                        onChange={(e) => handleChange("OSS_ENDPOINT", e.target.value)}
                        placeholder="oss-cn-beijing.aliyuncs.com (optional)"
                        className={inputClass}
                      />
                    </div>

                    <div>
                      <label className="flex items-center justify-between text-sm font-medium text-foreground mb-2">
                        <span>OSS Base Path</span>
                        <span className="text-text-muted font-normal text-xs">e.g. lumenx</span>
                      </label>
                      <input
                        type="text"
                        value={config.OSS_BASE_PATH}
                        onChange={(e) => handleChange("OSS_BASE_PATH", e.target.value)}
                        placeholder="lumenx"
                        className={inputClass}
                      />
                    </div>
                  </div>
                </div>

                <div className="pt-4 border-t border-glass-border">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-bold text-foreground">Kling Provider</h3>
                    <span className="text-[0.625rem] text-text-muted">{t("chooseProvider")}</span>
                  </div>
                  <div className="bg-glass border border-glass-border rounded-lg p-4 space-y-4">
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleChange("KLING_PROVIDER_MODE", "dashscope")}
                        className={modeButtonClass(config.KLING_PROVIDER_MODE === "dashscope")}
                      >
                        DashScope
                      </button>
                      <button
                        type="button"
                        onClick={() => handleChange("KLING_PROVIDER_MODE", "vendor")}
                        className={modeButtonClass(config.KLING_PROVIDER_MODE === "vendor")}
                      >
                        Vendor Direct
                      </button>
                    </div>
                    <p className="text-xs text-text-muted">
                      {t("dashscopeMode")} {t("vendorMode")}
                    </p>

                    {config.KLING_PROVIDER_MODE === "vendor" && (
                      <>
                        <div>
                          <label className="block text-sm font-medium text-foreground mb-2">
                            Kling Access Key <span className="text-red-500">*</span>
                          </label>
                          <input
                            type="password"
                            value={config.KLING_ACCESS_KEY}
                            onChange={(e) => handleChange("KLING_ACCESS_KEY", e.target.value)}
                            placeholder="Kling API Access Key"
                            className={inputClass}
                          />
                        </div>

                        <div>
                          <label className="block text-sm font-medium text-foreground mb-2">
                            Kling Secret Key <span className="text-red-500">*</span>
                          </label>
                          <input
                            type="password"
                            value={config.KLING_SECRET_KEY}
                            onChange={(e) => handleChange("KLING_SECRET_KEY", e.target.value)}
                            placeholder="Kling API Secret Key"
                            className={inputClass}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <div className="pt-4 border-t border-glass-border">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-bold text-foreground">Vidu Provider</h3>
                    <span className="text-[0.625rem] text-text-muted">{t("chooseProvider")}</span>
                  </div>
                  <div className="bg-input-bg border border-glass-border rounded-lg p-4 space-y-4">
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleChange("VIDU_PROVIDER_MODE", "dashscope")}
                        className={modeButtonClass(config.VIDU_PROVIDER_MODE === "dashscope")}
                      >
                        DashScope
                      </button>
                      <button
                        type="button"
                        onClick={() => handleChange("VIDU_PROVIDER_MODE", "vendor")}
                        className={modeButtonClass(config.VIDU_PROVIDER_MODE === "vendor")}
                      >
                        Vendor Direct
                      </button>
                    </div>
                    <p className="text-xs text-text-muted">
                      {t("dashscopeMode")} {t("vendorMode")}
                    </p>

                    {config.VIDU_PROVIDER_MODE === "vendor" && (
                      <div>
                        <label className="block text-sm font-medium text-foreground mb-2">
                          Vidu API Key <span className="text-red-500">*</span>
                        </label>
                        <input
                          type="password"
                          value={config.VIDU_API_KEY}
                          onChange={(e) => handleChange("VIDU_API_KEY", e.target.value)}
                          placeholder="Vidu API Key"
                          className={inputClass}
                        />
                      </div>
                    )}
                  </div>
                </div>

                {/* MuleRun / MuleRouter */}
                <div className="space-y-3 pt-4 border-t border-glass-border">
                  <h4 className="text-sm font-medium text-text-secondary">MuleRun / MuleRouter</h4>
                  <p className="text-xs text-text-secondary/60">用于 Seedance 2.0 视频生成和 GPT-Image-2 图片生成</p>
                  <div>
                    <label className="block text-xs text-text-secondary mb-1">API Key</label>
                    <input
                      type="password"
                      value={config.MULEROUTER_API_KEY}
                      onChange={(e) => handleChange("MULEROUTER_API_KEY", e.target.value)}
                      placeholder="muk-..."
                      className={inputClass}
                    />
                    {!config.MULEROUTER_API_KEY && config.MULERUN_CLI_LOGGED_IN && (
                      <p className="text-[0.6875rem] text-green-400 mt-1">✓ MuleRun CLI 已登录，无需手动填写</p>
                    )}
                  </div>
                  <details className="group">
                    <summary className="text-xs text-primary cursor-pointer hover:underline flex items-center gap-1">
                      <svg className="w-3 h-3 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                      如何获取 MuleRun Key？
                    </summary>
                    <div className="mt-2 space-y-2 pl-4 border-l border-glass-border">
                      <div className="flex items-center gap-2 text-xs text-text-secondary">
                        <span className="shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary flex items-center justify-center text-[0.625rem] font-bold">1</span>
                        <span>安装 CLI</span>
                        <code className="ml-auto px-2 py-0.5 bg-glass rounded text-[0.6875rem] font-mono select-all">npm i -g @mulerunai/cli</code>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-text-secondary">
                        <span className="shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary flex items-center justify-center text-[0.625rem] font-bold">2</span>
                        <span>浏览器登录</span>
                        <code className="ml-auto px-2 py-0.5 bg-glass rounded text-[0.6875rem] font-mono select-all">mulerun login</code>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-text-secondary">
                        <span className="shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary flex items-center justify-center text-[0.625rem] font-bold">3</span>
                        <span>复制 Key</span>
                        <code className="ml-auto px-2 py-0.5 bg-glass rounded text-[0.6875rem] font-mono select-all">mulerun studio config</code>
                      </div>
                      <p className="text-[0.6875rem] text-text-secondary/50 mt-1">Key 格式为 muk-...，粘贴到上方输入框即可。本地开发如已登录 CLI，无需填写。</p>
                    </div>
                  </details>
                </div>

                <div className="pt-4 border-t border-glass-border">
                  <button
                    type="button"
                    onClick={() => setEndpointsOpen(!endpointsOpen)}
                    aria-expanded={endpointsOpen}
                    className="flex items-center gap-2 text-sm font-medium text-text-secondary hover:text-foreground transition-colors"
                  >
                    {endpointsOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    {t("advancedEndpoints")}
                  </button>

                  {endpointsOpen && (
                    <div className="mt-4 space-y-4">
                      <p className="text-xs text-text-muted">
                        {t("endpointsDesc")}
                      </p>
                      {ENDPOINT_PROVIDERS.map(({ key, label, placeholder }) => (
                        <div key={key}>
                          <label className="flex items-center justify-between text-sm font-medium text-foreground mb-2">
                            <span>{label} Base URL</span>
                            <span className="text-text-muted font-normal text-xs">{placeholder}</span>
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

          <div className="flex justify-end gap-3 p-6 border-t border-glass-border">
            <button
              onClick={requestClose}
              className="px-4 py-2 text-sm text-text-secondary hover:text-foreground transition-colors"
            >
              {tc("cancel")}
            </button>
            <button
              onClick={handleSave}
              disabled={saving || loading || !!loadError || !configOwner}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-foreground text-sm font-medium rounded-lg transition-all disabled:opacity-50"
            >
              {saving ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  {t("savingConfig")}
                </>
              ) : (
                <>
                  <Save size={16} />
                  {t("saveConfig")}
                </>
              )}
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
