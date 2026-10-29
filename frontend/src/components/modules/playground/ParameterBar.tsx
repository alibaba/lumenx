'use client';

import { useState, useRef, useEffect } from 'react';
import { usePlaygroundStore, type PlaygroundParameters } from './usePlaygroundStore';
import { getModelParams, getModelDuration } from './playgroundModels';
import {
  getEffectivePlaygroundParameters,
  getPlaygroundBooleanDefault,
  hasInactivePlaygroundParameterDrafts,
  isMigratedPlaygroundModel,
  supportsPlaygroundParameter,
} from './playgroundParameterContract';
import { isWan27TextToImage, isQwen20TextToImage, imageDraftValueText } from './playgroundImageContract';
import { ChevronDown, Check } from 'lucide-react';
import { useTranslations } from 'next-intl';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VIDEO_MODES = new Set(['t2v', 'i2v', 'r2v', 'v2v']);
const BATCH_OPTIONS = [1, 2, 4] as const;

const FALLBACK_RATIOS = ['16:9', '9:16', '1:1'];
const FALLBACK_RESOLUTIONS = ['720P', '1080P'];

// ---------------------------------------------------------------------------
// ParamDropdown — custom styled dropdown (replaces native <select>)
// ---------------------------------------------------------------------------

/** Compute aspect ratio label from a "WxH" or "W*H" size string. */
function sizeToRatioLabel(size: string): string | null {
  const m = size.match(/^(\d+)[x*×](\d+)$/i);
  if (!m) return null;
  const w = parseInt(m[1], 10);
  const h = parseInt(m[2], 10);
  if (!w || !h) return null;
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  const d = gcd(w, h);
  return `${w / d}:${h / d}`;
}

function ParamDropdown({
  label,
  value,
  options,
  onChange,
  disabled,
  formatOption,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  disabled?: boolean;
  formatOption?: (opt: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  const display = formatOption ?? ((o: string) => o);

  return (
    <div className="flex flex-col gap-[6px]">
      <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">{label}</span>
      <div ref={containerRef} className="relative">
        <button
          type="button"
          disabled={disabled}
          onClick={() => !disabled && setOpen((o) => !o)}
          className={`w-full flex items-center justify-between px-3 py-2.5 rounded-[14px] bg-surface-inset border border-border-subtle text-foreground text-xs font-medium transition cursor-pointer ${
            disabled
              ? 'opacity-50 cursor-not-allowed'
              : 'hover:border-foreground/30'
          }`}
        >
          <span>{display(value)}</span>
          {!disabled && <ChevronDown className={`w-3 h-3 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />}
        </button>

        {open && (
          <div className="absolute top-full mt-1 w-full bg-elevated atelier-card border border-border-subtle z-30 max-h-48 overflow-y-auto">
            {options.map((opt) => (
              <div
                key={opt}
                onClick={() => {
                  onChange(opt);
                  setOpen(false);
                }}
                className="px-3 py-2 text-xs flex items-center justify-between hover:bg-hover-bg cursor-pointer"
              >
                <span className={opt === value ? 'text-foreground' : 'text-text-secondary'}>
                  {display(opt)}
                </span>
                {opt === value && <Check className="w-3 h-3 text-primary" />}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Format image size with aspect ratio: "1024x1024" → "1024×1024 (1:1)" */
function formatImageSize(size: string): string {
  const normalized = size.replace(/[*x]/g, '×');
  const ratio = sizeToRatioLabel(size);
  return ratio ? `${normalized} (${ratio})` : normalized;
}

// ---------------------------------------------------------------------------
// PillToggle — ON / OFF pill selector
// ---------------------------------------------------------------------------

function PillToggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex flex-col gap-[6px]">
      <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">{label}</span>
      <div className="flex gap-[2px] p-[3px] bg-surface-inset rounded-full atelier-pill-tabs">
        <button
          type="button"
          onClick={() => onChange(true)}
          className={`flex-1 rounded-full px-3 py-1.5 text-[0.6875rem] font-medium text-center cursor-pointer transition-all ${
            value
              ? 'bg-primary text-on-accent'
              : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
          }`}
        >
          ON
        </button>
        <button
          type="button"
          onClick={() => onChange(false)}
          className={`flex-1 rounded-full px-3 py-1.5 text-[0.6875rem] font-medium text-center cursor-pointer transition-all ${
            !value
              ? 'bg-primary text-on-accent'
              : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
          }`}
        >
          OFF
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DurationStepper — +/- stepper for integer seconds
// ---------------------------------------------------------------------------

function DurationStepper({
  value,
  min,
  max,
  step,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  const t = useTranslations('playground');
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value.replace(/[^0-9]/g, '');
    if (raw === '') return;
    const num = parseInt(raw, 10);
    onChange(Math.max(min, Math.min(max, num)));
  };

  const handleBlur = () => {
    onChange(Math.max(min, Math.min(max, value)));
  };

  return (
    <div className="flex flex-col gap-[6px]">
      <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">{t('parameters.duration')}</span>
      <div className="flex items-center gap-0 rounded-[14px] border border-border-subtle bg-surface-inset overflow-hidden">
        <button
          type="button"
          disabled={value <= min}
          onClick={() => onChange(Math.max(min, value - step))}
          className="px-3 py-2.5 text-text-secondary hover:text-foreground hover:bg-hover-bg transition disabled:opacity-30 disabled:cursor-not-allowed text-sm font-medium shrink-0"
        >
          −
        </button>
        <div className="flex-1 flex items-center justify-center gap-0.5 py-2.5">
          <input
            type="text"
            inputMode="numeric"
            value={value}
            onChange={handleInputChange}
            onBlur={handleBlur}
            className="w-8 bg-transparent text-center font-mono text-xs font-medium text-foreground outline-none"
          />
          <span className="text-[0.625rem] text-text-muted font-mono">s</span>
        </div>
        <button
          type="button"
          disabled={value >= max}
          onClick={() => onChange(Math.min(max, value + step))}
          className="px-3 py-2.5 text-text-secondary hover:text-foreground hover:bg-hover-bg transition disabled:opacity-30 disabled:cursor-not-allowed text-sm font-medium shrink-0"
        >
          +
        </button>
      </div>
      <div className="flex justify-between px-1">
        <span className="text-[0.5625rem] text-text-muted font-mono">{min}s</span>
        <span className="text-[0.5625rem] text-text-muted font-mono">{max}s</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ParameterBar
// ---------------------------------------------------------------------------

export default function ParameterBar() {
  const t = useTranslations('playground');
  const mode = usePlaygroundStore((s) => s.mode);
  const modelId = usePlaygroundStore((s) => s.modelId);
  const parameters = usePlaygroundStore((s) => s.parameters);
  const inputMedia = usePlaygroundStore((s) => s.inputMedia);
  const batchSize = usePlaygroundStore((s) => s.batchSize);
  const setParameters = usePlaygroundStore((s) => s.setParameters);
  const setBatchSize = usePlaygroundStore((s) => s.setBatchSize);

  const [showAdvanced, setShowAdvanced] = useState(false);

  const isVideoMode = VIDEO_MODES.has(mode);
  const strictImage = isWan27TextToImage(modelId, mode, inputMedia);
  const qwenImage = isQwen20TextToImage(modelId, mode, inputMedia);
  const strictImageParameters = strictImage || qwenImage;

  // Read model-specific params and duration from catalog
  const modelParams = getModelParams(modelId);
  const migratedModel = isMigratedPlaygroundModel(modelId);
  const effectiveParameters = getEffectivePlaygroundParameters(modelId, parameters);
  const modelDuration = getModelDuration(modelId, effectiveParameters.resolution);
  const hasInactiveDrafts = hasInactivePlaygroundParameterDrafts(modelId, parameters);

  // Derive options with fallbacks — IMAGE uses size, VIDEO uses resolution/ratio
  const hasSize = !!modelParams?.size;
  const hasResolution = !!modelParams?.resolution;
  const hasRatio = !!modelParams?.ratio;
  const hasQuality = !!modelParams?.quality;
  const hasAudioSetting = !!modelParams?.audioSetting;
  const videoQuality = migratedModel ? modelParams?.mode : undefined;
  const shotType = migratedModel ? modelParams?.shotType : undefined;
  const supportsAudio = migratedModel && supportsPlaygroundParameter(modelId, 'audio');

  const sizeOptions = modelParams?.size?.options ?? [];
  const sizeDefault = modelParams?.size?.default ?? sizeOptions[0] ?? '1024*1024';
  const ratioOptions = modelParams?.ratio?.options ?? FALLBACK_RATIOS;
  const ratioDefault = modelParams?.ratio?.default ?? ratioOptions[0];
  const resolutionOptions = modelParams?.resolution?.options ?? FALLBACK_RESOLUTIONS;
  const resolutionDefault = modelParams?.resolution?.default ?? resolutionOptions[0];
  const qualityOptions = modelParams?.quality?.options ?? [];
  const qualityDefault = modelParams?.quality?.default ?? qualityOptions[0] ?? 'high';
  const audioSettingOptions = modelParams?.audioSetting?.options ?? [];
  const audioSettingDefault = modelParams?.audioSetting?.default ?? audioSettingOptions[0] ?? 'auto';

  // Boolean feature flags from model
  const supportsSeed = migratedModel ? supportsPlaygroundParameter(modelId, 'seed') : modelParams?.seed !== false;
  const supportsPromptExtend = !strictImage && (migratedModel ? supportsPlaygroundParameter(modelId, 'promptExtend') : modelParams?.promptExtend !== false);
  const supportsWatermark = migratedModel ? supportsPlaygroundParameter(modelId, 'watermark') : modelParams?.watermark !== false;
  const supportsDuration = migratedModel ? supportsPlaygroundParameter(modelId, 'duration') : true;
  const hasAnyAdvanced = supportsSeed || supportsPromptExtend || supportsWatermark || supportsAudio;

  // Only legacy model changes normalize options; adding a reference keeps the current draft.
  useEffect(() => {
    if (migratedModel || strictImageParameters || isQwen20TextToImage(modelId, mode, [])) return;
    const patches: PlaygroundParameters = {};

    if (hasSize) {
      const cur = parameters.size as string | undefined;
      if (cur && !sizeOptions.includes(cur)) patches.size = sizeDefault;
    }
    if (hasRatio) {
      const cur = parameters.aspect_ratio as string | undefined;
      if (cur && !ratioOptions.includes(cur)) patches.aspect_ratio = ratioDefault;
    }
    if (hasResolution) {
      const cur = parameters.resolution as string | undefined;
      if (cur && !resolutionOptions.includes(cur)) patches.resolution = resolutionDefault;
    }
    if (hasQuality) {
      const cur = parameters.quality as string | undefined;
      if (cur && !qualityOptions.includes(cur)) patches.quality = qualityDefault;
    }
    if (hasAudioSetting) {
      const cur = parameters.audio_setting as string | undefined;
      if (cur && !audioSettingOptions.includes(cur)) patches.audio_setting = audioSettingDefault;
    }

    if (isVideoMode && modelDuration) {
      const currentDur = parameters.duration as number | undefined;
      if (modelDuration.type === 'slider') {
        if (currentDur != null && (currentDur < modelDuration.min || currentDur > modelDuration.max))
          patches.duration = modelDuration.default;
      } else if (modelDuration.type === 'buttons') {
        if (currentDur != null && !modelDuration.options.includes(currentDur))
          patches.duration = modelDuration.default;
      } else if (modelDuration.type === 'fixed') {
        patches.duration = modelDuration.value;
      }
    }

    if (Object.keys(patches).length > 0) setParameters({ ...parameters, ...patches });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId, migratedModel]);

  const updateParam = (key: string, value: PlaygroundParameters[string]) => {
    setParameters({ ...parameters, [key]: value });
  };

  // Duration state (video only)
  const durationValue = (effectiveParameters.duration as number | undefined)
    ?? (modelDuration?.type === 'fixed' ? modelDuration.value
      : (modelDuration?.type === 'slider' || modelDuration?.type === 'buttons') ? modelDuration.default : 5);
  const durationFixed = modelDuration?.type === 'fixed';
  const promptExtendValue = (effectiveParameters.prompt_extend as boolean | undefined)
    ?? getPlaygroundBooleanDefault(modelId, 'promptExtend')
    ?? (parameters.prompt_extend !== false);
  const watermarkValue = (effectiveParameters.watermark as boolean | undefined)
    ?? getPlaygroundBooleanDefault(modelId, 'watermark')
    ?? (parameters.watermark === true);

  // Batch pill renderer (reused for both image and video)
  const batchPills = (
    <div className="flex flex-col gap-[6px]">
      <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">{t('parameters.batchSize')}</span>
      <div className="flex gap-[2px] p-[3px] bg-surface-inset rounded-full atelier-pill-tabs">
        {BATCH_OPTIONS.map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => setBatchSize(n)}
            className={`flex-1 rounded-full px-3 py-1.5 font-mono text-[0.6875rem] font-medium cursor-pointer transition-all text-center ${
              batchSize === n
                ? 'bg-primary text-on-accent'
                : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
            }`}
          >
            x{n}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="space-y-3">
      {strictImage && <p className="text-[0.6875rem] leading-relaxed text-text-muted">{t('imageContract.supported')}</p>}
      {qwenImage && <p className="text-[0.6875rem] leading-relaxed text-text-muted">{t('imageContract.qwenSupported')}</p>}
      <div className="grid grid-cols-2 gap-3">

        {/* ── IMAGE MODE PARAMS ── */}
        {!isVideoMode && (
          <>
            {/* Size (image-specific, replaces resolution) */}
            {hasSize && (
              <ParamDropdown
                label={t('parameters.imageSize')}
                value={effectiveParameters.size !== undefined
                  ? typeof effectiveParameters.size === 'string' ? effectiveParameters.size : imageDraftValueText(effectiveParameters.size)
                  : sizeDefault}
                options={sizeOptions}
                onChange={(v) => updateParam('size', v)}
                formatOption={formatImageSize}
              />
            )}

            {/* Quality (GPT-Image-2 specific) */}
            {hasQuality && (
              <ParamDropdown
                label={t('parameters.quality')}
                value={(effectiveParameters.quality as string) ?? qualityDefault}
                options={qualityOptions}
                onChange={(v) => updateParam('quality', v)}
              />
            )}

            {/* Batch — spans full width if no quality, else single col */}
            <div className={!hasQuality && !hasSize ? 'col-span-2' : hasSize && !hasQuality ? '' : ''}>
              {batchPills}
            </div>
          </>
        )}

        {/* ── VIDEO MODE PARAMS ── */}
        {isVideoMode && (
          <>
            {/* Ratio */}
            {hasRatio && (
              <ParamDropdown
                label={t('parameters.aspectRatio')}
                value={(effectiveParameters.aspect_ratio as string) ?? ratioDefault}
                options={ratioOptions}
                onChange={(v) => updateParam('aspect_ratio', v)}
              />
            )}

            {/* Resolution */}
            {hasResolution && (
              <ParamDropdown
                label={t('parameters.resolution')}
                value={(effectiveParameters.resolution as string) ?? resolutionDefault}
                options={resolutionOptions}
                onChange={(v) => updateParam('resolution', v)}
              />
            )}

            {/* Fallback: show ratio + resolution even if model doesn't declare them */}
            {!migratedModel && !hasRatio && !hasResolution && (
              <>
                <ParamDropdown
                  label={t('parameters.aspectRatio')}
                  value={(effectiveParameters.aspect_ratio as string) ?? FALLBACK_RATIOS[0]}
                  options={FALLBACK_RATIOS}
                  onChange={(v) => updateParam('aspect_ratio', v)}
                />
                <ParamDropdown
                  label={t('parameters.resolution')}
                  value={(effectiveParameters.resolution as string) ?? FALLBACK_RESOLUTIONS[0]}
                  options={FALLBACK_RESOLUTIONS}
                  onChange={(v) => updateParam('resolution', v)}
                />
              </>
            )}

            {videoQuality && (
              <ParamDropdown
                label={t('parameters.videoQuality')}
                value={(effectiveParameters.mode as string) ?? videoQuality.default}
                options={videoQuality.options}
                onChange={(value) => updateParam('mode', value)}
                formatOption={(option) => t(`parameters.videoQualityOptions.${option}`)}
              />
            )}

            {shotType && (
              <ParamDropdown
                label={t('parameters.shotType')}
                value={(effectiveParameters.shot_type as string) ?? shotType.default}
                options={shotType.options}
                onChange={(value) => updateParam('shot_type', value)}
                formatOption={(option) => t(`parameters.shotTypeOptions.${option}`)}
              />
            )}

            {hasAudioSetting && (
              <ParamDropdown
                label={t('parameters.audioSetting')}
                value={(effectiveParameters.audio_setting as string) ?? audioSettingDefault}
                options={audioSettingOptions}
                onChange={(v) => updateParam('audio_setting', v)}
                formatOption={(opt) => t(`parameters.audioSettingOptions.${opt}`)}
              />
            )}

            {/* Duration */}
            {!supportsDuration ? (
              <div className="col-span-2 flex flex-col gap-[6px]">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">{t('parameters.sourceVideoDurationLabel')}</span>
                <div className="w-full rounded-[14px] border border-border-subtle bg-surface-inset px-3 py-2.5 text-xs leading-relaxed text-text-muted">
                  {t('parameters.sourceVideoDurationHint')}
                </div>
              </div>
            ) : durationFixed ? (
              <div className="flex flex-col gap-[6px]">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">{t('parameters.duration')}</span>
                <div className="w-full flex items-center px-3 py-2.5 rounded-[14px] bg-surface-inset border border-border-subtle text-text-muted text-xs font-medium">
                  {durationValue}s {t('parameters.durationFixedSuffix')}
                </div>
              </div>
            ) : modelDuration?.type === 'buttons' && modelDuration.options.length > 4 ? (
              <ParamDropdown
                label={t('parameters.duration')}
                value={String(durationValue)}
                options={modelDuration.options.map(String)}
                onChange={(v) => updateParam('duration', Number.parseInt(v, 10))}
                formatOption={(opt) => (opt === '0' ? t('parameters.durationKeepSource') : `${opt}s`)}
              />
            ) : modelDuration?.type === 'buttons' ? (
              <div className="flex flex-col gap-[6px]">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">{t('parameters.duration')}</span>
                <div className="flex gap-[2px] p-[3px] bg-surface-inset rounded-full atelier-pill-tabs">
                  {modelDuration.options.map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => updateParam('duration', n)}
                      className={`flex-1 rounded-full px-3 py-1.5 font-mono text-[0.6875rem] font-medium cursor-pointer transition-all text-center ${
                        durationValue === n
                          ? 'bg-primary text-on-accent'
                          : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
                      }`}
                    >
                      {n === 0 ? t('parameters.durationKeepSource') : `${n}s`}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <DurationStepper
                value={durationValue}
                min={modelDuration?.type === 'slider' ? modelDuration.min : 1}
                max={modelDuration?.type === 'slider' ? modelDuration.max : 15}
                step={modelDuration?.type === 'slider' ? modelDuration.step : 1}
                onChange={(v) => updateParam('duration', v)}
              />
            )}

            {/* Batch */}
            {batchPills}
          </>
        )}
      </div>

      {/* Advanced params — only show controls the model actually supports */}
      {hasAnyAdvanced && (
        <div>
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="inline-flex items-center gap-1 text-[0.6875rem] font-medium text-text-muted hover:text-foreground transition-colors cursor-pointer"
          >
            <ChevronDown className={`w-3 h-3 text-text-muted shrink-0 transition-transform ${showAdvanced ? 'rotate-180' : ''}`} />
            {t('parameters.advanced')}
          </button>

          {showAdvanced && (
            <div className="grid grid-cols-2 gap-3 mt-3">
              {supportsSeed && (
                <div className="flex flex-col gap-[6px] atelier-field">
                  <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted">Seed</span>
                  <input
                    type="number"
                    aria-label="Seed"
                    min={strictImageParameters ? 0 : undefined}
                    max={strictImageParameters ? 2147483647 : undefined}
                    step={strictImageParameters ? 1 : undefined}
                    placeholder={t('parameters.seedPlaceholder')}
                    className="glass-input w-full text-xs text-foreground font-mono placeholder:text-text-muted bg-surface-inset rounded-[14px]"
                    value={typeof parameters.seed === 'number' || typeof parameters.seed === 'string' ? parameters.seed : ''}
                    onChange={(e) => {
                      const val = e.target.value;
                      if (val === '') {
                        updateParam('seed', undefined);
                        return;
                      }
                      const parsed = strictImageParameters ? Number(val) : Number.parseInt(val, 10);
                      updateParam('seed', Number.isNaN(parsed) ? undefined : parsed);
                    }}
                  />
                </div>
              )}

              {supportsPromptExtend && (
                <PillToggle
                  label={t('parameters.promptExtend')}
                  value={promptExtendValue}
                  onChange={(v) => updateParam('prompt_extend', v)}
                />
              )}

              {supportsAudio && (
                <PillToggle
                  label={t('parameters.generateAudio')}
                  value={effectiveParameters.audio === true}
                  onChange={(value) => updateParam('audio', value)}
                />
              )}

              {supportsWatermark && (
                <PillToggle
                  label={t('parameters.watermark')}
                  value={watermarkValue}
                  onChange={(v) => updateParam('watermark', v)}
                />
              )}
            </div>
          )}
        </div>
      )}
      {hasInactiveDrafts && (
        <p className="text-[0.6875rem] leading-relaxed text-text-muted">
          {t('parameters.inactiveDraftHint')}
        </p>
      )}
    </div>
  );
}
