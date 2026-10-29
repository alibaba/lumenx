"use client";

import { useState, useEffect, useCallback, useRef, type ReactNode } from "react";
import { Save, Loader2, ChevronDown, ChevronRight, FolderOpen, WifiOff, Copy, Check } from "lucide-react";
import { useTranslations } from "next-intl";
import { api, type EnvConfigPayload, type ProviderMode, API_URL } from "@/lib/api";
import {
  environmentLocalStorageKey,
  getRuntimeEnvironment,
  isCurrentRuntimeEnvironment,
  subscribeRuntimeEnvironment,
  type RuntimeEnvironment,
} from "@/lib/runtimeEnvironment";
import { ASPECT_RATIOS } from "@/store/projectStore";
import {
  DEFAULT_MODEL_SETTINGS,
  GLOBAL_I2V_MODELS,
  GLOBAL_R2V_MODELS,
  GLOBAL_IMAGE_MODELS,
  normalizeModelSettings,
  type FrontendModelSettings,
} from "@/lib/modelCatalog";
import { useSettingsStore, type Locale, type ThemePreset } from "@/store/settingsStore";
import { toast } from "@/store/toastStore";
import { rovingKeyDown } from "@/lib/a11y";
import { Image as ImageIcon, Video, Layout, User, Building, Box } from "lucide-react";
import GroupedModelGrid from "@/components/common/GroupedModelGrid";
import LumenXBranding from "@/components/layout/LumenXBranding";
import UpdateChecker from "./UpdateChecker";
type SettingsCategory = "general" | "models" | "prompts" | "apikeys" | "storage" | "about";
import {
  FormRow,
  FieldLabel,
  KeyField,
  Toggle,
  ModeSegment,
  settingsInputClass,
} from "./SettingsControls";

const APP_VERSION = "v0.2.0";

type EnvConfigOwner = { key: string; generation: number };

type EnvConfig = EnvConfigPayload & {
  DASHSCOPE_API_KEY: string;
  ALIBABA_CLOUD_ACCESS_KEY_ID: string;
  ALIBABA_CLOUD_ACCESS_KEY_SECRET: string;
  OSS_ENABLE: boolean;
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
  OSS_ENABLE: true,
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
    if (!env.KLING_ACCESS_KEY?.trim()) errors.push("Kling Access Key (vendor mode)");
    if (!env.KLING_SECRET_KEY?.trim()) errors.push("Kling Secret Key (vendor mode)");
  }
  if (env.VIDU_PROVIDER_MODE === "vendor" && !env.VIDU_API_KEY?.trim()) {
    errors.push("Vidu API Key (vendor mode)");
  }
  return errors;
};

const LS_KEY_MODEL = "lumenx_default_model_settings";
const LS_KEY_PROMPT = "lumenx_default_prompt_config";

interface DefaultPromptConfig {
  storyboard_polish: string;
  video_polish: string;
  r2v_polish: string;
  entity_extraction: string;
  style_analysis: string;
  storyboard_extraction: string;
}

const EMPTY_PROMPT_CONFIG: DefaultPromptConfig = {
  storyboard_polish: "",
  video_polish: "",
  r2v_polish: "",
  entity_extraction: "",
  style_analysis: "",
  storyboard_extraction: "",
};

function loadFromLS<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}

// `name` / `desc` hold i18n keys (relative to the `settings` namespace) so the
// module-scope list can be resolved with t(...) at render time.
const THEME_OPTIONS: { id: ThemePreset; name: string; desc: string; base: string; primary: string; accent: string }[] = [
  { id: "atelier-dark",  name: "themeAtelierDark",  desc: "themeAtelierDarkDesc",  base: "#0c0b0e", primary: "#34d8c4", accent: "#ffa94d" },
  { id: "bridge-dark",   name: "themeBridgeDark",   desc: "themeBridgeDarkDesc",   base: "#0a0a0d", primary: "#646cff", accent: "#ffa94d" },
  { id: "brand-dark",    name: "themeBrandDark",    desc: "themeBrandDarkDesc",    base: "#050508", primary: "#646cff", accent: "#ff0080" },
  { id: "atelier-light", name: "themeAtelierLight", desc: "themeAtelierLightDesc", base: "#f6f1e9", primary: "#1d9c8d", accent: "#e8852b" },
  { id: "brand-light",   name: "themeBrandLight",   desc: "themeBrandLightDesc",   base: "#f8f9fa", primary: "#646cff", accent: "#ff0080" },
];

interface SystemReport {
  ffmpeg?: { available: boolean; message: string; path: string | null };
  status?: string;
}

/* Atelier section panel — restored per Line B mockup `.panel` (translucent
   warm-graphite card via glass-panel + atelier-card: surface + blur + soft
   shadow + hairline border, so sections read as distinct grouped cards).
   The page <header> stays frameless (mockup .main-head has no bg); only the
   content sections are carded. Model cards / inputs keep their own surfaces. */
function Section({
  id,
  title,
  desc,
  children,
}: {
  id?: string;
  title: string;
  desc?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-title` : undefined}
      className="glass-panel atelier-card rounded-[20px] overflow-hidden"
    >
      <div className="atelier-card-head px-[22px] pt-[18px] pb-3.5 border-b border-glass-border">
        <h2
          id={id ? `${id}-title` : undefined}
          className="font-display atelier-display text-[1.1875rem] font-semibold text-foreground tracking-tight"
        >
          {title}
        </h2>
        {desc && <p className="text-[0.75rem] text-text-secondary mt-1 leading-relaxed">{desc}</p>}
      </div>
      <div className="px-[22px] pt-[18px] pb-[22px]">{children}</div>
    </section>
  );
}

export default function SettingsPage() {
  const t = useTranslations("settings");
  const { locale, theme, animations, setLocale, setTheme, setAnimations } = useSettingsStore();

  const [active, setActive] = useState<SettingsCategory>("general");
  const [runtimeEnvironment, setRuntimeEnvironment] = useState(() => getRuntimeEnvironment());

  // ── API Config ──
  const [config, setConfig] = useState<EnvConfig>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [endpointsOpen, setEndpointsOpen] = useState(false);

  // ── Default Model Settings ──
  const [modelSettings, setModelSettings] = useState<FrontendModelSettings>(() =>
    normalizeModelSettings(
      getRuntimeEnvironment()
        ? loadFromLS(environmentLocalStorageKey(LS_KEY_MODEL, getRuntimeEnvironment()!.key), DEFAULT_MODEL_SETTINGS)
        : DEFAULT_MODEL_SETTINGS,
      "global_settings",
    )
  );

  // ── Default Prompt Config ──
  // `promptConfig` is the displayed/editable text. localStorage (LS_KEY_PROMPT)
  // only ever stores DELTAS: an empty value means "use the built-in default".
  // `promptDefaults` holds the real built-in defaults fetched from the backend
  // so we can pre-fill the fields and run the delta comparison on save.
  const [promptConfig, setPromptConfig] = useState<DefaultPromptConfig>(() =>
    getRuntimeEnvironment()
      ? loadFromLS(environmentLocalStorageKey(LS_KEY_PROMPT, getRuntimeEnvironment()!.key), EMPTY_PROMPT_CONFIG)
      : EMPTY_PROMPT_CONFIG
  );
  const [promptDefaults, setPromptDefaults] = useState<Record<string, string>>({});

  // ── About / system ──
  const [online, setOnline] = useState(true);
  const [dataDir, setDataDir] = useState<string>("");
  const [logDir, setLogDir] = useState<string>("");
  const [system, setSystem] = useState<SystemReport | null>(null);
  const [systemLoading, setSystemLoading] = useState(false);
  const [systemChecked, setSystemChecked] = useState(false);
  const [copiedPath, setCopiedPath] = useState<string | null>(null);
  const [configOwner, setConfigOwner] = useState<EnvConfigOwner | null>(null);
  const configOwnerRef = useRef<EnvConfigOwner | null>(null);
  const configLoadGenerationRef = useRef(0);
  const mountedRef = useRef(false);
  const savingGenerationRef = useRef<number | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      configLoadGenerationRef.current += 1;
      savingGenerationRef.current = null;
    };
  }, []);

  const setLoadedConfigOwner = useCallback((owner: EnvConfigOwner | null) => {
    configOwnerRef.current = owner;
    setConfigOwner(owner);
  }, []);

  const loadConfig = useCallback(async (environment: RuntimeEnvironment | null = getRuntimeEnvironment()) => {
    const loadGeneration = configLoadGenerationRef.current + 1;
    configLoadGenerationRef.current = loadGeneration;
    savingGenerationRef.current = null;
    setSaving(false);
    setLoadedConfigOwner(null);
    setConfig(DEFAULT_CONFIG);
    if (!environment) {
      setLoading(false);
      setLoadError(false);
      return;
    }

    const owner = { key: environment.key, generation: environment.generation };
    const stillCurrent = () => (
      mountedRef.current && loadGeneration === configLoadGenerationRef.current
      && isCurrentRuntimeEnvironment(owner.key, owner.generation)
    );

    setLoading(true);
    setLoadError(false);
    try {
      const data = await api.getEnvConfig();
      if (!stillCurrent()) return;
      setConfig(normalizeEnvConfig(DEFAULT_CONFIG, data));
      setLoadedConfigOwner(owner);
    } catch {
      if (stillCurrent()) {
        setLoadError(true);
        setLoadedConfigOwner(null);
      }
    } finally {
      if (stillCurrent()) setLoading(false);
    }
  }, [setLoadedConfigOwner]);

  useEffect(() => subscribeRuntimeEnvironment(() => {
    setRuntimeEnvironment(getRuntimeEnvironment());
  }), []);

  useEffect(() => {
    if (!runtimeEnvironment) {
      setModelSettings(DEFAULT_MODEL_SETTINGS);
      setPromptConfig(EMPTY_PROMPT_CONFIG);
      setPromptDefaults({});
      return;
    }
    setModelSettings(normalizeModelSettings(
      loadFromLS(environmentLocalStorageKey(LS_KEY_MODEL, runtimeEnvironment.key), DEFAULT_MODEL_SETTINGS),
      "global_settings",
    ));
    setPromptConfig(loadFromLS(environmentLocalStorageKey(LS_KEY_PROMPT, runtimeEnvironment.key), EMPTY_PROMPT_CONFIG));
  }, [runtimeEnvironment]);

  useEffect(() => {
    loadConfig(runtimeEnvironment);
  }, [runtimeEnvironment, loadConfig]);

  // Pre-fill the prompt fields with the real built-in defaults so users can see
  // and edit from them. We remember the fetched defaults for the delta-save
  // comparison, and only fill a field the user has NOT overridden (empty in LS).
  // If the fetch fails we leave the fields empty (placeholder) — no crash.
  useEffect(() => {
    let cancelled = false;
    const environment = runtimeEnvironment;
    if (!environment) return () => {
      cancelled = true;
    };
    (async () => {
      try {
        const defaults = await api.fetchPromptDefaults();
        if (cancelled || !defaults || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        setPromptDefaults(defaults);
        setPromptConfig((prev) => {
          const next = { ...prev };
          (Object.keys(EMPTY_PROMPT_CONFIG) as (keyof DefaultPromptConfig)[]).forEach((k) => {
            const d = defaults[k];
            if (typeof d === "string" && d && !prev[k]) next[k] = d;
          });
          return next;
        });
      } catch {
        /* defaults unavailable — fields fall back to empty placeholders */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runtimeEnvironment]);

  // Online/offline detection for the banner.
  useEffect(() => {
    const update = () => setOnline(typeof navigator === "undefined" ? true : navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  // Pull health (data/log dir) once on mount so About + Storage can show paths.
  useEffect(() => {
    (async () => {
      try {
        const h = await api.healthCheck();
        if (h.log_dir) setLogDir(h.log_dir);
        if (h.log_file) {
          // data dir = parent of logs dir (logs lives under <data>/logs)
          const dir = h.log_dir.replace(/\/logs\/?$/, "");
          setDataDir(dir || h.log_dir);
        }
      } catch {
        /* backend offline — About shows fallback */
      }
    })();
  }, []);

  const loadSystem = useCallback(async () => {
    setSystemLoading(true);
    try {
      const r = await api.checkSystem();
      setSystem({ ffmpeg: r.dependencies?.ffmpeg, status: r.status });
      // Lock only on success so a recovered backend is reflected without
      // forcing a manual retry, while a successful read won't re-run.
      setSystemChecked(true);
    } catch {
      setSystem(null);
      // Leave systemChecked=false: re-entering the About tab retries once.
    } finally {
      setSystemLoading(false);
    }
  }, []);

  const hasCurrentConfigOwner = useCallback(() => {
    const owner = configOwnerRef.current;
    return !!owner && isCurrentRuntimeEnvironment(owner.key, owner.generation);
  }, []);

  // Self-healing lazy load: auto-run the system check when the About tab
  // becomes active and we don't yet have a successful result. Driven off a
  // tab-transition ref so it fires once per entry (no render thrash) and is
  // not retried endlessly while the result is missing.
  const prevActiveRef = useRef<SettingsCategory | null>(null);
  useEffect(() => {
    const enteredAbout = active === "about" && prevActiveRef.current !== "about";
    prevActiveRef.current = active;
    if (active === "about" && !systemChecked && !systemLoading && enteredAbout) {
      loadSystem();
    }
  }, [active, systemChecked, systemLoading, loadSystem]);

  const saveConfig = async (validateProviders: boolean) => {
    if (savingGenerationRef.current !== null || !mountedRef.current) return;
    if (!hasCurrentConfigOwner()) {
      toast.error(t("saveConfigFailed"));
      return;
    }
    const errors = validateProviders ? getValidationErrors(config) : [];
    if (errors.length > 0) {
      toast.error(t("fillRequired"), { body: `- ${errors.join("\n- ")}` });
      return;
    }
    const owner = configOwnerRef.current!;
    const generation = configLoadGenerationRef.current;
    const stillCurrent = () => (
      mountedRef.current
      && generation === configLoadGenerationRef.current
      && isCurrentRuntimeEnvironment(owner.key, owner.generation)
    );
    savingGenerationRef.current = generation;
    setSaving(true);
    try {
      await api.saveEnvConfig(config);
      if (stillCurrent()) toast.success(t("saveSuccess"));
    } catch {
      if (stillCurrent()) toast.error(t("saveConfigFailed"));
    } finally {
      if (savingGenerationRef.current === generation) {
        savingGenerationRef.current = null;
        if (stillCurrent()) setSaving(false);
      }
    }
  };

  const handleSaveApiConfig = () => saveConfig(true);
  // Storage settings can be saved independently of generation credentials.
  const handleSaveStorage = () => saveConfig(false);

  const handleChange = (key: keyof EnvConfig, value: string) => {
    if (!hasCurrentConfigOwner()) return;
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  const handleEndpointChange = (envKey: string, value: string) => {
    if (!hasCurrentConfigOwner()) return;
    setConfig((prev) => ({
      ...prev,
      endpoint_overrides: { ...prev.endpoint_overrides, [envKey]: value },
    }));
  };

  const handleSaveModelDefaults = () => {
    if (!runtimeEnvironment || !isCurrentRuntimeEnvironment(runtimeEnvironment.key, runtimeEnvironment.generation)) return;
    const normalized = normalizeModelSettings(modelSettings, "global_settings");
    // T2I and I2I share one image model in the UI; persist both backend
    // fields plus image_model so per-project backfill stays consistent.
    const merged: FrontendModelSettings = {
      ...normalized,
      i2i_model: normalized.t2i_model,
      image_model: normalized.t2i_model,
    };
    localStorage.setItem(environmentLocalStorageKey(LS_KEY_MODEL, runtimeEnvironment.key), JSON.stringify(merged));
    setModelSettings(merged);
    toast.success(t("saved"));
  };

  const handleSavePromptDefaults = () => {
    if (!runtimeEnvironment || !isCurrentRuntimeEnvironment(runtimeEnvironment.key, runtimeEnvironment.generation)) return;
    // DELTA persistence: a field equal to its built-in default is stored as ""
    // (=> use built-in, no snapshot pinning); only genuine overrides are saved.
    const delta: DefaultPromptConfig = { ...EMPTY_PROMPT_CONFIG };
    (Object.keys(EMPTY_PROMPT_CONFIG) as (keyof DefaultPromptConfig)[]).forEach((k) => {
      const text = promptConfig[k] ?? "";
      delta[k] = text === promptDefaults[k] ? "" : text;
    });
    localStorage.setItem(environmentLocalStorageKey(LS_KEY_PROMPT, runtimeEnvironment.key), JSON.stringify(delta));
    toast.success(t("saved"));
  };

  const copyPath = async (p: string) => {
    if (!p) return;
    try {
      await navigator.clipboard.writeText(p);
      setCopiedPath(p);
      setTimeout(() => setCopiedPath(null), 1200);
    } catch {
      /* clipboard blocked */
    }
  };

  // MuleRun 登录轮询是一次带环境身份的会话：重入、环境切换、卸载都必须让旧回调失效。
  const [mulerunLoginActive, setMulerunLoginActive] = useState(false);
  const mulerunLoginActiveRef = useRef(false);
  const mulerunLoginGenerationRef = useRef(0);
  const mulerunPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mulerunPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setMulerunLoginRunning = useCallback((running: boolean) => {
    mulerunLoginActiveRef.current = running;
    setMulerunLoginActive(running);
  }, []);

  const clearMulerunLoginTimers = useCallback(() => {
    if (mulerunPollRef.current) {
      clearInterval(mulerunPollRef.current);
      mulerunPollRef.current = null;
    }
    if (mulerunPollTimeoutRef.current) {
      clearTimeout(mulerunPollTimeoutRef.current);
      mulerunPollTimeoutRef.current = null;
    }
  }, []);

  const finishMulerunLogin = useCallback((generation: number) => {
    if (generation !== mulerunLoginGenerationRef.current) return;
    mulerunLoginGenerationRef.current += 1;
    clearMulerunLoginTimers();
    setMulerunLoginRunning(false);
  }, [clearMulerunLoginTimers, setMulerunLoginRunning]);

  const cancelMulerunLogin = useCallback(() => {
    mulerunLoginGenerationRef.current += 1;
    clearMulerunLoginTimers();
    setMulerunLoginRunning(false);
  }, [clearMulerunLoginTimers, setMulerunLoginRunning]);

  const getErrorDetail = (error: unknown): string | null => {
    if (!error || typeof error !== "object" || !("response" in error)) return null;
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    return typeof response?.data?.detail === "string" ? response.data.detail : null;
  };

  const startMulerunLogin = useCallback(async () => {
    if (mulerunLoginActiveRef.current) return;
    const environment = getRuntimeEnvironment();
    if (!environment) {
      toast.error(t("loginFailed"));
      return;
    }

    const generation = mulerunLoginGenerationRef.current + 1;
    mulerunLoginGenerationRef.current = generation;
    clearMulerunLoginTimers();
    setMulerunLoginRunning(true);

    const stillCurrent = () => (
      generation === mulerunLoginGenerationRef.current
      && isCurrentRuntimeEnvironment(environment.key, environment.generation)
    );

    try {
      await api.triggerMulerunLogin();
      if (!stillCurrent()) {
        finishMulerunLogin(generation);
        return;
      }

      let pollInFlight = false;
      const poll = async () => {
        if (pollInFlight) return;
        if (!stillCurrent()) {
          finishMulerunLogin(generation);
          return;
        }
        pollInFlight = true;
        try {
          const env = await api.getEnvConfig();
          if (!stillCurrent()) {
            finishMulerunLogin(generation);
            return;
          }
          if (env.MULERUN_CLI_LOGGED_IN) {
            finishMulerunLogin(generation);
            setConfig((current) => ({ ...current, MULERUN_CLI_LOGGED_IN: true }));
          }
        } catch {
          if (!stillCurrent()) finishMulerunLogin(generation);
        } finally {
          if (generation === mulerunLoginGenerationRef.current) pollInFlight = false;
        }
      };

      mulerunPollRef.current = setInterval(() => {
        void poll();
      }, 3000);
      mulerunPollTimeoutRef.current = setTimeout(() => {
        if (!stillCurrent()) {
          finishMulerunLogin(generation);
          return;
        }
        finishMulerunLogin(generation);
        toast.error(t("loginFailed"));
      }, 120000);
    } catch (error: unknown) {
      if (stillCurrent()) toast.error(getErrorDetail(error) || t("loginFailed"));
      finishMulerunLogin(generation);
    }
  }, [clearMulerunLoginTimers, finishMulerunLogin, setMulerunLoginRunning, t]);

  useEffect(() => subscribeRuntimeEnvironment(cancelMulerunLogin), [cancelMulerunLogin]);

  useEffect(() => () => {
    mulerunLoginGenerationRef.current += 1;
    clearMulerunLoginTimers();
    mulerunLoginActiveRef.current = false;
  }, [clearMulerunLoginTimers]);

  const PathField = ({ value, label }: { value: string; label: string }) => (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <div className="flex gap-2">
        <input
          type="text"
          value={value || "—"}
          disabled
          className={settingsInputClass + " font-mono text-[0.71875rem] opacity-70 cursor-not-allowed"}
        />
        <button
          type="button"
          onClick={() => copyPath(value)}
          disabled={!value}
          title={t("copyPath")}
          className="flex-shrink-0 px-3 rounded-md border border-glass-border bg-surface text-text-secondary hover:text-foreground transition-colors disabled:opacity-40 flex items-center gap-1.5 text-xs"
        >
          {copiedPath === value ? <Check size={13} className="text-emerald-400" /> : <FolderOpen size={13} />}
          {copiedPath === value ? t("copied") : t("copy")}
        </button>
      </div>
    </div>
  );

  /* ── Section renderers ──────────────────────────────────────── */

  const renderGeneral = () => (
    <Section id="general" title={t("secGeneralTitle")}>
      <FormRow label={t("language")} hint={t("languageDesc")}>
        <FieldLabel>LANGUAGE</FieldLabel>
        <ModeSegment
          value={locale}
          onChange={(v) => setLocale(v as Locale)}
          options={[
            { id: "zh", label: t("chinese") },
            { id: "en", label: t("english") },
          ]}
        />
      </FormRow>

      <FormRow label={t("theme")} hint={t("themeDesc")}>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2" role="radiogroup" aria-label={t("theme")} onKeyDown={rovingKeyDown}>
          {THEME_OPTIONS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              role="radio"
              aria-checked={theme === preset.id}
              tabIndex={theme === preset.id ? 0 : -1}
              onClick={() => setTheme(preset.id)}
              className={`group relative flex flex-col gap-2 p-3 rounded-xl border text-left transition-all ${
                theme === preset.id
                  ? "border-primary/60 bg-primary/10 ring-1 ring-primary/30"
                  : "border-glass-border bg-hover-bg hover:border-text-muted"
              }`}
            >
              <div
                className="h-10 w-full rounded-lg border border-glass-border overflow-hidden flex items-end p-1.5 gap-1"
                style={{ background: preset.base }}
              >
                <span className="h-3 w-3 rounded-full" style={{ background: preset.primary }} />
                <span className="h-3 w-3 rounded-full" style={{ background: preset.accent }} />
              </div>
              <div className="min-w-0">
                <div className="text-xs font-medium text-foreground truncate">{t(preset.name)}</div>
                <div className="text-[0.625rem] text-text-muted truncate">{t(preset.desc)}</div>
              </div>
              {theme === preset.id && (
                <span className="absolute top-2 right-2 h-2 w-2 rounded-full bg-primary" />
              )}
            </button>
          ))}
        </div>
      </FormRow>

      <FormRow label={t("motionLabel")} hint={t("motionHint")}>
        <Toggle
          checked={animations}
          onChange={setAnimations}
          label={animations ? t("motionOn") : t("motionReduced")}
          sub={t("motionSub")}
          ariaLabel={t("motionToggleAria")}
        />
      </FormRow>
    </Section>
  );

  const aspectButtons = (key: keyof FrontendModelSettings) => (
    <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={t("aspectRatioAria")} onKeyDown={rovingKeyDown}>
      {ASPECT_RATIOS.map((ratio) => (
        <button
          key={ratio.id}
          type="button"
          role="radio"
          aria-checked={modelSettings[key] === ratio.id}
          tabIndex={modelSettings[key] === ratio.id ? 0 : -1}
          onClick={() => setModelSettings((s) => ({ ...s, [key]: ratio.id }))}
          className={`flex flex-col items-center py-2 px-2 rounded-lg border transition-all ${
            modelSettings[key] === ratio.id
              ? "border-primary/50 bg-primary/10"
              : "border-glass-border hover:border-text-muted bg-glass"
          }`}
        >
          <span className="text-xs font-medium text-foreground">{ratio.name}</span>
        </button>
      ))}
    </div>
  );

  const renderModels = () => (
    <Section
      id="models"
      title={t("secModelsTitle")}
      desc={t("secModelsDesc")}
    >
      {/* Image model (T2I + I2I unified) */}
      <FormRow label={t("imageModelLabel")} hint={t("imageModelHint")}>
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <ImageIcon size={15} className="text-emerald-400" />
          <span>{t("imageModelCaption")}</span>
        </div>
        <GroupedModelGrid
          models={GLOBAL_IMAGE_MODELS}
          selectedId={modelSettings.t2i_model}
          onSelect={(id) => setModelSettings((s) => ({ ...s, t2i_model: id, i2i_model: id, image_model: id }))}
        />
      </FormRow>

      {/* Asset aspect ratios */}
      <FormRow label={t("assetAspectLabel")} hint={t("assetAspectHint")}>
        <div className="grid grid-cols-3 gap-4">
          {(
            [
              { key: "character_aspect_ratio" as const, label: t("assetCharacter"), icon: User },
              { key: "scene_aspect_ratio" as const, label: t("assetScene"), icon: Building },
              { key: "prop_aspect_ratio" as const, label: t("assetProp"), icon: Box },
            ] as const
          ).map(({ key, label, icon: Icon }) => (
            <div key={key} className="space-y-2">
              <div className="flex items-center gap-1 text-xs text-text-secondary">
                <Icon size={12} />
                <label>{label}</label>
              </div>
              <div className="space-y-1">
                {ASPECT_RATIOS.map((ratio) => (
                  <button
                    key={ratio.id}
                    type="button"
                    onClick={() => setModelSettings((s) => ({ ...s, [key]: ratio.id }))}
                    className={`w-full flex flex-col items-center py-2 px-2 rounded border transition-all ${
                      modelSettings[key] === ratio.id
                        ? "border-emerald-500/50 bg-emerald-500/10"
                        : "border-glass-border hover:border-text-muted bg-glass"
                    }`}
                  >
                    <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </FormRow>

      {/* Storyboard aspect ratio */}
      <FormRow label={t("storyboardAspectLabel")} hint={t("storyboardAspectHint")}>
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Layout size={15} className="text-primary" />
          <span>Storyboard Aspect Ratio</span>
        </div>
        {aspectButtons("storyboard_aspect_ratio")}
      </FormRow>

      {/* I2V */}
      <FormRow label={t("i2vModelLabel")} hint={t("i2vModelHint")}>
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Video size={15} className="text-purple-400" />
          <span>Image-to-Video</span>
        </div>
        <GroupedModelGrid
          models={GLOBAL_I2V_MODELS}
          selectedId={modelSettings.i2v_model}
          onSelect={(id) => setModelSettings((s) => ({ ...s, i2v_model: id }))}
        />
      </FormRow>

      {/* R2V */}
      <FormRow label={t("r2vModelLabel")} hint={t("r2vModelHint")}>
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Video size={15} className="text-purple-400" />
          <span>Reference-to-Video</span>
        </div>
        <GroupedModelGrid
          models={GLOBAL_R2V_MODELS}
          selectedId={modelSettings.r2v_model ?? ""}
          onSelect={(id) => setModelSettings((s) => ({ ...s, r2v_model: id }))}
        />
      </FormRow>

      <div className="flex justify-end pt-4">
        <button
          type="button"
          onClick={handleSaveModelDefaults}
          className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-on-accent text-sm font-medium rounded-lg transition-all"
        >
          <Save size={16} />
          {t("saveDefaults")}
        </button>
      </div>
    </Section>
  );

  const PROMPT_FIELDS: { key: keyof DefaultPromptConfig; label: string; desc: string }[] = [
    { key: "entity_extraction", label: t("promptEntityLabel"), desc: t("promptEntityDesc") },
    { key: "style_analysis", label: t("promptStyleLabel"), desc: t("promptStyleDesc") },
    { key: "storyboard_extraction", label: t("promptStoryboardExtractLabel"), desc: t("promptStoryboardExtractDesc") },
    { key: "storyboard_polish", label: t("promptStoryboardPolishLabel"), desc: t("promptStoryboardPolishDesc") },
    { key: "video_polish", label: t("promptVideoPolishLabel"), desc: t("promptVideoPolishDesc") },
    { key: "r2v_polish", label: t("promptR2vPolishLabel"), desc: t("promptR2vPolishDesc") },
  ];

  const renderPrompts = () => (
    <Section
      id="prompts"
      title={t("secPromptsTitle")}
      desc={t("secPromptsDesc")}
    >
      <div className="space-y-5">
        {PROMPT_FIELDS.map((f) => (
          <div key={f.key} className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{f.label}</h3>
            <p className="text-[0.6875rem] text-text-muted">{f.desc}</p>
            <textarea
              value={promptConfig[f.key]}
              onChange={(e) => setPromptConfig((prev) => ({ ...prev, [f.key]: e.target.value }))}
              placeholder={t("promptPlaceholder")}
              className="w-full h-32 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-foreground resize-y focus:outline-none focus:border-primary/50 font-mono placeholder-text-muted"
            />
          </div>
        ))}
      </div>
      <div className="flex justify-end pt-4">
        <button
          type="button"
          onClick={handleSavePromptDefaults}
          className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-on-accent text-sm font-medium rounded-lg transition-colors"
        >
          <Save size={16} />
          {t("saveDefaults")}
        </button>
      </div>
    </Section>
  );

  const renderApiKeys = () => (
    <Section
      id="apikeys"
      title={t("secApiTitle")}
      desc={t("secApiDesc")}
    >
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={24} className="animate-spin text-primary" />
          <span className="ml-2 text-text-secondary">{t("loadingConfig")}</span>
        </div>
      ) : loadError ? (
        <div className="bg-status-failed-bg border border-status-failed-border rounded-lg p-4 text-sm text-status-failed-fg">
          {t("loadConfigFailed")}
        </div>
      ) : (
        <div className="space-y-1">
          <FormRow label={t("dashscopeKeyLabel")} hint={t("dashscopeKeyHint")}>
            <FieldLabel>DASHSCOPE_API_KEY</FieldLabel>
            <KeyField
              value={config.DASHSCOPE_API_KEY}
              onChange={(v) => handleChange("DASHSCOPE_API_KEY", v)}
              placeholder="sk-..."
              status={
                config.DASHSCOPE_API_KEY?.trim()
                  ? { kind: "ok", text: t("filled") }
                  : { kind: "warn", text: t("notConfiguredUnavailable") }
              }
            />
          </FormRow>

          <FormRow label={t("klingLabel")} hint={t("klingHint")}>
            <ModeSegment
              value={config.KLING_PROVIDER_MODE}
              onChange={(v) => handleChange("KLING_PROVIDER_MODE", v)}
              options={[
                { id: "dashscope", label: "DashScope" },
                { id: "vendor", label: t("vendorDirect") },
              ]}
            />
            {config.KLING_PROVIDER_MODE === "vendor" && (
              <div className="space-y-3 mt-3">
                <div>
                  <FieldLabel>KLING_ACCESS_KEY *</FieldLabel>
                  <KeyField value={config.KLING_ACCESS_KEY} onChange={(v) => handleChange("KLING_ACCESS_KEY", v)} placeholder="Kling Access Key" />
                </div>
                <div>
                  <FieldLabel>KLING_SECRET_KEY *</FieldLabel>
                  <KeyField value={config.KLING_SECRET_KEY} onChange={(v) => handleChange("KLING_SECRET_KEY", v)} placeholder="Kling Secret Key" />
                </div>
              </div>
            )}
          </FormRow>

          <FormRow label="Vidu" hint={t("viduHint")}>
            <ModeSegment
              value={config.VIDU_PROVIDER_MODE}
              onChange={(v) => handleChange("VIDU_PROVIDER_MODE", v)}
              options={[
                { id: "dashscope", label: "DashScope" },
                { id: "vendor", label: t("vendorDirect") },
              ]}
            />
            {config.VIDU_PROVIDER_MODE === "vendor" && (
              <div className="mt-3">
                <FieldLabel>VIDU_API_KEY *</FieldLabel>
                <KeyField value={config.VIDU_API_KEY} onChange={(v) => handleChange("VIDU_API_KEY", v)} placeholder="Vidu API Key" />
              </div>
            )}
          </FormRow>

          <FormRow label={t("mulerunLabel")} hint={t("mulerunHint")}>
            {!config.MULEROUTER_API_KEY && !config.MULERUN_CLI_LOGGED_IN && (
              <button
                type="button"
                onClick={startMulerunLogin}
                disabled={mulerunLoginActive}
                className="w-full py-2.5 rounded-lg bg-primary text-on-accent text-sm font-medium hover:bg-primary-hover transition-colors mb-3 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {t("mulerunLogin")}
              </button>
            )}
            {!config.MULEROUTER_API_KEY && config.MULERUN_CLI_LOGGED_IN && (
              <div className="flex items-center gap-3 mb-3">
                <div className="flex items-center gap-2 text-sm text-emerald-400">
                  <Check size={16} />
                  {t("mulerunLoggedIn")}
                </div>
                <button
                  type="button"
                  onClick={startMulerunLogin}
                  disabled={mulerunLoginActive}
                  className="text-xs text-text-secondary hover:text-foreground transition-colors underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {t("reLogin")}
                </button>
              </div>
            )}
            <FieldLabel>MULEROUTER_API_KEY</FieldLabel>
            <KeyField
              value={config.MULEROUTER_API_KEY}
              onChange={(v) => setConfig((c) => ({ ...c, MULEROUTER_API_KEY: v }))}
              placeholder="muk-..."
            />
            <details className="group mt-3">
              <summary className="text-xs text-primary cursor-pointer hover:underline flex items-center gap-1">
                <ChevronRight size={12} className="transition-transform group-open:rotate-90" />
                {t("manualGetKey")}
              </summary>
              <div className="mt-2 space-y-2 pl-4 border-l border-glass-border">
                {[
                  { n: "1", label: t("stepInstallCli"), cmd: "npm i -g @mulerunai/cli" },
                  { n: "2", label: t("stepBrowserLogin"), cmd: "mulerun login" },
                  { n: "3", label: t("stepCopyKey"), cmd: "mulerun studio config" },
                ].map((step) => (
                  <div key={step.n} className="flex items-center gap-2 text-xs text-text-secondary">
                    <span className="shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary flex items-center justify-center text-[0.625rem] font-bold">
                      {step.n}
                    </span>
                    <span>{step.label}</span>
                    <code
                      className="ml-auto px-2 py-0.5 bg-glass rounded text-[0.6875rem] font-mono select-all cursor-pointer"
                      onClick={(e) => {
                        navigator.clipboard.writeText(step.cmd);
                        const el = e.currentTarget;
                        el.style.outline = "1px solid var(--color-primary)";
                        setTimeout(() => (el.style.outline = ""), 800);
                      }}
                    >
                      {step.cmd}
                    </code>
                  </div>
                ))}
                <p className="text-[0.6875rem] text-text-muted mt-1">{t("mulerunKeyHint")}</p>
              </div>
            </details>
          </FormRow>

          <FormRow label={t("advancedEndpointsLabel")} hint={t("advancedEndpointsHint")}>
            <button
              type="button"
              onClick={() => setEndpointsOpen(!endpointsOpen)}
              className="flex items-center gap-2 text-sm font-medium text-text-secondary hover:text-foreground transition-colors"
            >
              {endpointsOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              {endpointsOpen ? t("collapseEndpoints") : t("expandEndpoints")}
            </button>
            {endpointsOpen && (
              <div className="mt-3 space-y-3">
                {ENDPOINT_PROVIDERS.map(({ key, label, placeholder }) => (
                  <div key={key}>
                    <FieldLabel>{label} BASE URL</FieldLabel>
                    <input
                      type="text"
                      value={config.endpoint_overrides[key] || ""}
                      onChange={(e) => handleEndpointChange(key, e.target.value)}
                      placeholder={placeholder}
                      className={settingsInputClass + " font-mono text-[0.71875rem]"}
                    />
                  </div>
                ))}
              </div>
            )}
          </FormRow>

          <div className="flex justify-end pt-4">
            <button
              type="button"
              onClick={handleSaveApiConfig}
              disabled={saving || loading || !online || !configOwner}
              className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-on-accent text-sm font-medium rounded-lg transition-all disabled:opacity-50"
            >
              {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
              {saving ? t("saving") : t("saveConfig")}
            </button>
          </div>
        </div>
      )}
    </Section>
  );

  const renderStorage = () => (
    <Section
      id="storage"
      title={t("secStorageTitle")}
      desc={t("secStorageDesc")}
    >
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={24} className="animate-spin text-primary" />
          <span className="ml-2 text-text-secondary">{t("loadingConfig")}</span>
        </div>
      ) : loadError ? (
        <div className="bg-status-failed-bg border border-status-failed-border rounded-lg p-4 text-sm text-status-failed-fg">
          {t("loadConfigFailed")}
        </div>
      ) : (<>
      <FormRow label={t("cloudStorageLabel")}>
        <Toggle
          checked={config.OSS_ENABLE}
          onChange={(v) => setConfig((c) => ({ ...c, OSS_ENABLE: v }))}
          label={t("enableCloudStorage")}
          sub={t("enableCloudStorageSub")}
          ariaLabel={t("enableCloudStorageAria")}
        />
      </FormRow>

      <FormRow label={t("ossAkSkLabel")} hint={t("ossAkSkHint")}>
        <div className="space-y-3">
          <div>
            <FieldLabel>ALIBABA_CLOUD_ACCESS_KEY_ID</FieldLabel>
            <KeyField
              value={config.ALIBABA_CLOUD_ACCESS_KEY_ID}
              onChange={(v) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_ID", v)}
              placeholder={t("ossOptionalMirror")}
            />
          </div>
          <div>
            <FieldLabel>ALIBABA_CLOUD_ACCESS_KEY_SECRET</FieldLabel>
            <KeyField
              value={config.ALIBABA_CLOUD_ACCESS_KEY_SECRET}
              onChange={(v) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_SECRET", v)}
              placeholder={t("ossOptionalMirror")}
            />
          </div>
          <a
            href="https://help.aliyun.com/zh/ram/user-guide/create-an-accesskey-pair"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[0.75rem] text-primary hover:underline"
          >
            {t("howToGetAccessKey")}
          </a>
        </div>
      </FormRow>

      <FormRow label={t("bucketLabel")} hint={t("bucketHint")}>
        <FieldLabel>OSS_BUCKET</FieldLabel>
        <input
          type="text"
          value={config.OSS_BUCKET_NAME}
          onChange={(e) => handleChange("OSS_BUCKET_NAME", e.target.value)}
          placeholder={t("bucketPlaceholder")}
          className={settingsInputClass + " font-mono text-[0.71875rem]"}
        />
      </FormRow>

      <FormRow label="Endpoint" hint={t("endpointHint")}>
        <FieldLabel>OSS_ENDPOINT</FieldLabel>
        <input
          type="text"
          value={config.OSS_ENDPOINT}
          onChange={(e) => handleChange("OSS_ENDPOINT", e.target.value)}
          placeholder={t("endpointPlaceholder")}
          className={settingsInputClass + " font-mono text-[0.71875rem]"}
        />
      </FormRow>

      <FormRow label="Base Path" hint={t("basePathHint")}>
        <FieldLabel>OSS_BASE_PATH</FieldLabel>
        <input
          type="text"
          value={config.OSS_BASE_PATH}
          onChange={(e) => handleChange("OSS_BASE_PATH", e.target.value)}
          placeholder="lumenx"
          className={settingsInputClass + " font-mono text-[0.71875rem]"}
        />
      </FormRow>

      <FormRow label={t("dataDirLabel")} hint={t("dataDirHint")}>
        <PathField value={dataDir} label="DATA_DIR · MANAGED" />
      </FormRow>

      <FormRow label={t("logDirLabel")} hint={t("logDirHint")}>
        <PathField value={logDir} label="LOG_DIR · MANAGED" />
      </FormRow>

      <div className="flex justify-end pt-4">
        <button
          type="button"
          onClick={handleSaveStorage}
          disabled={saving || loading || !online || !configOwner}
          className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-on-accent text-sm font-medium rounded-lg transition-all disabled:opacity-50"
        >
          {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
          {saving ? t("saving") : t("saveConfig")}
        </button>
      </div>
      </>)}
    </Section>
  );

  const renderAbout = () => {
    const ff = system?.ffmpeg;
    const aboutRows: { k: string; v: string; tone?: "ok" | "warn" }[] = [
      { k: t("aboutAppVersion"), v: `LumenX Studio ${APP_VERSION}` },
      { k: t("aboutBackendApi"), v: API_URL },
      { k: t("aboutDataDir"), v: dataDir || "—" },
      { k: t("logDirLabel"), v: logDir || "—" },
    ];
    return (
      <Section id="about" title={t("secAboutTitle")}>
        {/* Line B brand signature block — teal-glow logo, serif name, amber tagline */}
        <div className="flex flex-col items-start gap-3 pb-6 mb-6 border-b border-glass-border">
          <LumenXBranding size="md" showSlogan={false} />
          <p className="font-display atelier-display text-base italic text-accent leading-snug">
            “Render Noise into Narrative”
          </p>
          <div className="font-mono text-[0.625rem] tracking-[0.08em] text-text-muted uppercase">
            VERSION {APP_VERSION.replace(/^v/, "")} · BUILD 20260613
          </div>
          <p className="text-[0.78125rem] text-text-secondary leading-relaxed max-w-md">
            {t("aboutTagline")}
          </p>
        </div>

        {/* Check for updates — compares APP_VERSION against latest GitHub release */}
        <div className="mb-6">
          <UpdateChecker />
        </div>

        {/* Technical info table */}
        <div className="font-mono text-[0.59375rem] uppercase tracking-[0.1em] text-text-muted mb-3">
          {t("aboutTechInfo")}
        </div>
        <div className="space-y-0">
          {aboutRows.map((r) => (
            <div key={r.k} className="flex justify-between items-center py-2.5 border-b border-glass-border last:border-b-0 text-[0.78125rem] gap-3">
              <span className="text-text-secondary shrink-0">{r.k}</span>
              <span className="font-mono text-[0.71875rem] text-foreground truncate text-right">{r.v}</span>
            </div>
          ))}
          {/* FFmpeg row with live detection */}
          <div className="flex justify-between items-center py-2.5 border-b border-glass-border last:border-b-0 text-[0.78125rem] gap-3">
            <span className="text-text-secondary shrink-0">FFmpeg</span>
            <span className="font-mono text-[0.71875rem] text-right truncate">
              {systemLoading ? (
                <span className="inline-flex items-center gap-1.5 text-text-muted">
                  <Loader2 size={12} className="animate-spin" /> {t("ffmpegChecking")}
                </span>
              ) : ff ? (
                ff.available ? (
                  <span className="text-emerald-400" title={ff.message}>{t("ffmpegAvailable")}</span>
                ) : (
                  <span className="text-amber-400" title={ff.message}>{t("ffmpegMissing")}</span>
                )
              ) : (
                <span className="text-text-muted">{t("ffmpegUnknown")}</span>
              )}
            </span>
          </div>
        </div>
        <div className="flex justify-end pt-4">
          <button
            type="button"
            onClick={loadSystem}
            disabled={systemLoading}
            className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-md border border-glass-border bg-surface text-text-secondary hover:text-foreground transition-colors disabled:opacity-50"
          >
            {systemLoading ? <Loader2 size={13} className="animate-spin" /> : <Copy size={13} />}
            {t("recheck")}
          </button>
        </div>
      </Section>
    );
  };

  const renderActive = () => {
    switch (active) {
      case "general":
        return renderGeneral();
      case "models":
        return renderModels();
      case "prompts":
        return renderPrompts();
      case "apikeys":
        return renderApiKeys();
      case "storage":
        return renderStorage();
      case "about":
        return renderAbout();
      default:
        return null;
    }
  };

  const CATEGORY_TITLE: Record<SettingsCategory, string> = {
    general: t("eyebrowGeneral"),
    models: t("eyebrowModels"),
    prompts: t("eyebrowPrompts"),
    apikeys: t("eyebrowApikeys"),
    storage: t("eyebrowStorage"),
    about: t("eyebrowAbout"),
  };

  // 横向 Tab 短标签（取代竖向 SettingsSidebar；与全局品牌侧栏轴向正交，不再撞脸）。
  const TABS: { id: SettingsCategory; label: string }[] = [
    { id: "general", label: t("tabGeneral") },
    { id: "models", label: t("tabModels") },
    { id: "prompts", label: t("eyebrowPrompts") },
    { id: "apikeys", label: t("eyebrowApikeys") },
    { id: "storage", label: t("tabStorage") },
    { id: "about", label: t("eyebrowAbout") },
  ];

  return (
    <div className="relative h-full flex flex-col">
      {/* Atelier signature layers — inert on non-atelier themes. */}
      <div className="atelier-page-bloom" aria-hidden="true" />
      <div className="atelier-page-grain" aria-hidden="true" />

      {/* Head: eyebrow(当前分类) + 「设置」标题 + 横向 Tab —— 取代竖向子栏 */}
      <header className="flex-shrink-0 border-b border-glass-border px-4 md:px-7 pt-6 pb-4 relative z-10">
        <div className="w-full">
        <div className="font-mono text-[0.625rem] font-medium uppercase tracking-[0.2em] text-text-muted">
          SETTINGS · <span className="text-primary font-semibold">{CATEGORY_TITLE[active]}</span>
        </div>
        <h1 className="font-display atelier-display text-[1.625rem] md:text-[2.125rem] font-semibold text-foreground mt-2 tracking-tight">
          {t("title")}
        </h1>
        <nav className="flex flex-wrap gap-1 mt-5" role="tablist" aria-label={t("tabsAria")} onKeyDown={rovingKeyDown}>
          {TABS.map((tab) => {
            const isActive = active === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                tabIndex={isActive ? 0 : -1}
                onClick={() => setActive(tab.id)}
                className={`px-3.5 py-1.5 rounded-full text-[0.8125rem] transition-colors ${
                  isActive
                    ? "bg-primary/10 text-foreground font-semibold"
                    : "text-text-muted hover:text-foreground hover:bg-hover-bg font-medium"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </nav>
        </div>
      </header>

      {/* Scroll area */}
      <div className="flex-1 overflow-y-auto px-10 py-8 relative z-10">
        <div className="max-w-6xl mx-auto flex flex-col gap-6">
          {!online && (
            <div
              role="status"
              className="flex items-center gap-3 px-4 py-3 rounded-lg bg-status-processing-bg border border-status-processing-border"
            >
              <WifiOff size={18} className="text-status-processing-fg flex-shrink-0" />
              <div className="flex-1">
                <div className="text-[0.78125rem] font-semibold text-foreground">{t("offlineTitle")}</div>
                <div className="text-[0.6875rem] text-text-secondary mt-0.5">
                  {t("offlineBody")}
                </div>
              </div>
            </div>
          )}

          {renderActive()}
          <div className="pb-8" />
        </div>
      </div>
    </div>
  );
}
