'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { ImagePlus, Film, X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { API_URL, playgroundApi } from '@/lib/api';
import { usePlaygroundStore, type PlaygroundMode } from './usePlaygroundStore';
import AssetPickerModal from './AssetPickerModal';
import { getModelOption, isReferenceImagePathAllowed } from './playgroundModels';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment, type RuntimeEnvironment } from '@/lib/runtimeEnvironment';

// ---------------------------------------------------------------------------
// Mode config
// ---------------------------------------------------------------------------

interface ModeConfig {
  labelKey: string;
  accept: string;
  hintKey: string;
  multiple: boolean;
  maxFiles: number;
  icon: 'image' | 'video';
}

interface MediaInputOwner {
  activation: number;
  mode: PlaygroundMode;
  modelId: string;
  environment: RuntimeEnvironment | null;
}

const MODE_CONFIG: Partial<Record<PlaygroundMode, ModeConfig>> = {
  t2i: {
    labelKey: 'media.labelReferenceOptional',
    accept: 'image/*',
    hintKey: 't2i',
    multiple: true,
    maxFiles: 9,
    icon: 'image',
  },
  i2i: {
    labelKey: 'compose.mediaReference',
    accept: 'image/*',
    hintKey: 'i2i',
    multiple: false,
    maxFiles: 1,
    icon: 'image',
  },
  i2v: {
    labelKey: 'compose.mediaFirstFrame',
    accept: 'image/*',
    hintKey: 'i2v',
    multiple: false,
    maxFiles: 1,
    icon: 'image',
  },
  r2v: {
    labelKey: 'compose.mediaReference',
    accept: 'image/*',
    hintKey: 'r2v',
    multiple: true,
    maxFiles: 9,
    icon: 'image',
  },
  v2v: {
    labelKey: 'compose.mediaSourceVideo',
    accept: 'video/*',
    hintKey: 'v2v',
    multiple: false,
    maxFiles: 1,
    icon: 'video',
  },
};

function isReferenceImageFileAllowed(file: File, allowedTypes: string[] | undefined): boolean {
  if (!allowedTypes?.length) return true;
  if (file.type) return allowedTypes.includes(file.type);
  return isReferenceImagePathAllowed(file.name, allowedTypes);
}

// ---------------------------------------------------------------------------
// Shared style tokens (Line B — semantic tokens only, theme-safe)
// ---------------------------------------------------------------------------

// Neutral glass action button (本地上传 / 替换文件 / 从资产库选取). Replaces the
// old `border-primary/30 text-primary` accent so the panel reads quiet in Line B.
const ACTION_BTN_CLASS =
  'flex-1 px-3 py-1.5 rounded-full text-xs border border-border-subtle ' +
  'text-foreground/80 hover:bg-hover-bg hover:text-foreground ' +
  'transition-colors disabled:opacity-40';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getFileName(path: string): string {
  const parts = path.split('/');
  return parts[parts.length - 1] || path;
}

function isVideoPath(path: string): boolean {
  return /\.(mp4|mov|webm|avi|mkv)$/i.test(path);
}


function r2vImageLimitForModel(modelId: string): number | null {
  const limit = getModelOption(modelId)?.maxReferenceImages ?? 0;
  return limit > 0 ? limit : null;
}

function isSameRuntime(environment: RuntimeEnvironment | null): boolean {
  if (!environment) return getRuntimeEnvironment() === null;
  return isCurrentRuntimeEnvironment(environment.key, environment.generation);
}

// Resolve a stored media path to a browser-loadable URL. Local `output/...` paths
// are served via the backend /files static mount; absolute (http(s)/blob/data) and
// root-relative (/files/...) URLs pass through untouched. The raw path is still kept
// in store state + the generate payload — only the <img>/<video> src is resolved.
function resolveMediaSrc(path: string): string {
  if (/^(https?:|blob:|data:|\/)/i.test(path)) return path;
  return `${API_URL}/files/${path.replace(/^output\//, '')}`;
}

// ---------------------------------------------------------------------------
// Single-reference preview — Line B media-preview-row (thumb + name + meta).
//
// Used for maxFiles=1 modes (i2i / i2v first-frame / v2v source video). Mirrors
// the mockup's `.media-preview-row`: a larger thumbnail on the left, file name +
// "W × H · FORMAT" meta on the right. Dimensions are read from the loaded media
// (onLoad / onLoadedMetadata); format is derived from the extension. File size is
// intentionally omitted — it is not persisted past the upload moment (and is
// absent entirely for asset-library picks), so showing it would be inconsistent.
// Keyed by `path` at the call site so dims reset when the reference changes.
// ---------------------------------------------------------------------------

function SingleRefPreview({
  path,
  onRemove,
}: {
  path: string;
  onRemove: () => void;
}) {
  const [meta, setMeta] = useState<string | null>(null);
  const ext = (path.split('.').pop() || '').toUpperCase();
  const video = isVideoPath(path);

  return (
    <div className="flex items-center gap-3 p-3 rounded-[14px] bg-surface-inset border border-border-subtle">
      <div className="group relative w-20 h-20 shrink-0 rounded-[12px] overflow-hidden bg-elevated border border-border-subtle">
        {video ? (
          <video
            src={resolveMediaSrc(path)}
            className="w-full h-full object-cover"
            muted
            onLoadedMetadata={(e) =>
              setMeta(
                `${e.currentTarget.videoWidth} × ${e.currentTarget.videoHeight} · ${ext}`
              )
            }
          />
        ) : (
          <img
            src={resolveMediaSrc(path)}
            alt=""
            className="w-full h-full object-cover"
            onLoad={(e) =>
              setMeta(
                `${e.currentTarget.naturalWidth} × ${e.currentTarget.naturalHeight} · ${ext}`
              )
            }
          />
        )}

        {/* Remove badge on hover (functional black corner scrim) */}
        <button
          type="button"
          onClick={onRemove}
          className="absolute top-1 right-1 w-4 h-4 rounded-full bg-black/70 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <X className="w-3 h-3" />
        </button>
      </div>

      <div className="min-w-0 flex-1">
        <div className="text-sm text-foreground truncate" title={getFileName(path)}>
          {getFileName(path)}
        </div>
        <div className="font-mono text-[0.6875rem] text-text-muted mt-1">
          {meta ?? ext}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MediaInput() {
  const mode = usePlaygroundStore((s) => s.mode);
  const modelId = usePlaygroundStore((s) => s.modelId);
  const inputMedia = usePlaygroundStore((s) => s.inputMedia);
  const setInputMedia = usePlaygroundStore((s) => s.setInputMedia);
  const t = useTranslations('playground');

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [showAssetPicker, setShowAssetPicker] = useState(false);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const activationRef = useRef(0);
  const pickerOwnerRef = useRef<MediaInputOwner | null>(null);

  useEffect(() => {
    const unsubscribe = subscribeRuntimeEnvironment(() => {
      activationRef.current += 1;
      pickerOwnerRef.current = null;
      setShowAssetPicker(false);
      setUploading(false);
      setMediaError(null);
    });
    return () => {
      activationRef.current += 1;
      pickerOwnerRef.current = null;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    let previous = {
      mode: usePlaygroundStore.getState().mode,
      modelId: usePlaygroundStore.getState().modelId,
    };
    return usePlaygroundStore.subscribe((state) => {
      if (state.mode === previous.mode && state.modelId === previous.modelId) return;
      previous = { mode: state.mode, modelId: state.modelId };
      activationRef.current += 1;
      pickerOwnerRef.current = null;
      setShowAssetPicker(false);
      setUploading(false);
      setMediaError(null);
    });
  }, []);

  const shouldRenderMediaInput = MODE_CONFIG[mode] !== undefined;
  let config = MODE_CONFIG[mode] ?? MODE_CONFIG.t2i!;

  const r2vImageLimit = mode === 'r2v' ? r2vImageLimitForModel(modelId) : null;

  if (config && mode === 'r2v' && r2vImageLimit !== null) {
    config = {
      ...config,
      maxFiles: r2vImageLimit,
    };
  }

  const referenceImageMimeTypes = config.icon === 'image' ? getModelOption(modelId)?.referenceImageMimeTypes : undefined;
  const maxImageBytes = config.icon === 'image' ? getModelOption(modelId)?.maxReferenceImageBytes : undefined;
  const referenceFormats = referenceImageMimeTypes?.map(type => type.split('/')[1].toUpperCase()).join(' / ');
  if (referenceImageMimeTypes?.length) config = { ...config, accept: referenceImageMimeTypes.join(',') };
  const mediaHint = referenceFormats
    ? t('media.hints.modelImageFormats', { formats: referenceFormats })
    : t(`media.hints.${config.hintKey}`);

  const hasMedia = inputMedia.length > 0;
  const overLimit = config.multiple && inputMedia.length > config.maxFiles;
  const canAddMore = config.multiple && inputMedia.length < config.maxFiles;

  const showLimitError = useCallback((current = inputMedia.length, max = config.maxFiles) => {
    setMediaError(t('media.limitReached', { max, current }));
  }, [config.maxFiles, inputMedia.length, t]);

  const isCurrentOwner = useCallback((owner: MediaInputOwner | null) => {
    if (!owner) return false;
    const state = usePlaygroundStore.getState();
    return activationRef.current === owner.activation
      && state.mode === owner.mode
      && state.modelId === owner.modelId
      && isSameRuntime(owner.environment);
  }, []);

  useEffect(() => {
    if (!showAssetPicker) return;
    if (isCurrentOwner(pickerOwnerRef.current)) return;
    pickerOwnerRef.current = null;
    setShowAssetPicker(false);
  }, [isCurrentOwner, mode, modelId, showAssetPicker]);

  // -------------------------------------------------------------------------
  // Upload handler
  // -------------------------------------------------------------------------

  const handleFiles = useCallback(async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;

    const available = config.multiple ? Math.max(0, config.maxFiles - inputMedia.length) : 1;
    if (available <= 0) {
      showLimitError();
      return;
    }

    const toUpload = fileArray.slice(0, available);
    if (maxImageBytes && toUpload.some(file => file.size > maxImageBytes)) {
      setMediaError(t('media.imageTooLarge', { max: maxImageBytes / 1024 / 1024 }));
      return;
    }
    if (toUpload.some(file => !isReferenceImageFileAllowed(file, referenceImageMimeTypes))) {
      setMediaError(t('media.unsupportedImageType', { formats: referenceFormats ?? '' }));
      return;
    }
    if (fileArray.length > available) showLimitError(inputMedia.length + fileArray.length, config.maxFiles);
    else setMediaError(null);

    const capture = {
      activation: activationRef.current,
      mode,
      modelId,
      environment: getRuntimeEnvironment(),
    };

    setUploading(true);
    try {
      const results = await Promise.all(
        toUpload.map((file) => playgroundApi.uploadMedia(file))
      );
      if (!isCurrentOwner(capture)) return;
      const newPaths = results.map((r) => r.path);

      if (config.multiple) {
        const latest = usePlaygroundStore.getState().inputMedia;
        const room = Math.max(0, config.maxFiles - latest.length);
        if (room <= 0) {
          showLimitError(latest.length + newPaths.length, config.maxFiles);
          return;
        }
        const accepted = newPaths.slice(0, room);
        setInputMedia([...latest, ...accepted]);
        if (accepted.length < newPaths.length) showLimitError(latest.length + newPaths.length, config.maxFiles);
      } else {
        setInputMedia(newPaths.slice(0, 1));
      }
    } catch (err) {
      if (!isCurrentOwner(capture)) return;
      console.error('[MediaInput] upload failed:', err);
      setMediaError(t('media.uploadFailed'));
    } finally {
      if (isCurrentOwner(capture)) setUploading(false);
    }
  }, [isCurrentOwner, config.maxFiles, config.multiple, inputMedia.length, mode, modelId, setInputMedia, showLimitError, t, referenceImageMimeTypes, referenceFormats, maxImageBytes]);

  // -------------------------------------------------------------------------
  // Event handlers
  // -------------------------------------------------------------------------

  const handleClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      handleFiles(e.target.files);
    }
    // Reset so re-selecting the same file works
    e.target.value = '';
  };

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      if (e.dataTransfer.files) {
        handleFiles(e.dataTransfer.files);
      }
    },
    [handleFiles]
  );

  const handleRemove = (index: number) => {
    const updated = inputMedia.filter((_, i) => i !== index);
    setMediaError(null);
    setInputMedia(updated);
  };

  const handleReplace = () => {
    fileInputRef.current?.click();
  };

  const openAssetPicker = () => {
    pickerOwnerRef.current = {
      activation: activationRef.current,
      mode,
      modelId,
      environment: getRuntimeEnvironment(),
    };
    setShowAssetPicker(true);
  };

  const closeAssetPicker = () => {
    pickerOwnerRef.current = null;
    setShowAssetPicker(false);
  };

  const handleAssetSelect = (path: string) => {
    if (!isCurrentOwner(pickerOwnerRef.current)) {
      closeAssetPicker();
      return;
    }
    if (!isReferenceImagePathAllowed(path, referenceImageMimeTypes)) {
      setMediaError(t('media.unsupportedImageType', { formats: referenceFormats ?? '' }));
      return;
    }
    if (!config.multiple) {
      setMediaError(null);
      setInputMedia([path]);
      return;
    }
    const latest = usePlaygroundStore.getState().inputMedia;
    if (latest.length >= config.maxFiles) {
      showLimitError(latest.length + 1, config.maxFiles);
      return;
    }
    setMediaError(null);
    setInputMedia([...latest, path]);
  };

  // Determine accept type for AssetPickerModal
  const acceptType: 'image' | 'video' | 'all' =
    config.icon === 'video' ? 'video' : 'image';

  if (!shouldRenderMediaInput) return null;

  // -------------------------------------------------------------------------
  // Render: hidden file input
  // -------------------------------------------------------------------------

  const fileInput = (
    <input
      ref={fileInputRef}
      type="file"
      accept={config.accept}
      multiple={config.multiple}
      onChange={handleFileChange}
      className="hidden"
    />
  );

  // -------------------------------------------------------------------------
  // Render: empty state — Line B reference slot (recessed drop target)
  //
  // The section label is provided by the parent SectionCard (PlaygroundPage),
  // so this component renders only the slot + actions to avoid a double header.
  // -------------------------------------------------------------------------

  if (!hasMedia) {
    return (
      <div className="space-y-2">
        <div
          onClick={handleClick}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`
            border border-dashed rounded-[14px] p-6 bg-input-bg
            flex flex-col items-center gap-3 text-center cursor-pointer
            transition-colors
            ${
              dragOver
                ? 'border-primary/60 bg-primary/8 shadow-[var(--glow-primary)]'
                : 'border-border-subtle hover:border-foreground/30 hover:bg-hover-bg'
            }
            ${uploading ? 'pointer-events-none opacity-60' : ''}
          `}
        >
          {config.icon === 'video' ? (
            <Film className="w-8 h-8 text-text-muted" />
          ) : (
            <ImagePlus className="w-8 h-8 text-text-muted" />
          )}

          <span className="text-xs text-text-secondary">
            {uploading ? t('media.uploading') : t('media.dragOrClick')}
          </span>

          <span className="text-[0.6875rem] text-text-muted">{mediaHint}</span>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleClick}
            disabled={uploading}
            className={ACTION_BTN_CLASS}
          >
            {t('media.localUpload')}
          </button>
          <button
            type="button"
            onClick={openAssetPicker}
            className={ACTION_BTN_CLASS}
          >
            {t('media.pickFromLibrary')}
          </button>
        </div>

        {mediaError && (
          <div role="alert" className="rounded-[12px] border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
            {mediaError}
          </div>
        )}

        {fileInput}

        <AssetPickerModal
          isOpen={showAssetPicker}
          onClose={closeAssetPicker}
          onSelect={handleAssetSelect}
          accept={acceptType}
          allowedImageMimeTypes={referenceImageMimeTypes}
        />
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Render: has media state
  //
  // Multi-reference modes (r2v / t2i, maxFiles>1) → thumbnail tile grid.
  // Single-reference modes (i2i / i2v / v2v, maxFiles=1) → media-preview-row
  // (larger thumb + file name + dimensions·format), per the mockup.
  // -------------------------------------------------------------------------

  return (
    <div className="space-y-2">
      {config.multiple ? (
        <div className="space-y-3">
          {/* Thumbnail row */}
          <div className="flex flex-wrap gap-2">
            {inputMedia.map((path, index) => (
              <div
                key={path + index}
                className="group relative w-[72px] h-[72px] rounded-[14px] overflow-hidden bg-elevated border border-border-subtle"
              >
                {isVideoPath(path) ? (
                  <video
                    src={resolveMediaSrc(path)}
                    className="w-full h-full object-cover"
                    muted
                  />
                ) : (
                  <img
                    src={resolveMediaSrc(path)}
                    alt=""
                    className="w-full h-full object-cover"
                  />
                )}

                {/* Remove badge on hover (functional black corner scrim) */}
                <button
                  type="button"
                  onClick={() => handleRemove(index)}
                  className="
                    absolute top-1 right-1
                    w-4 h-4 rounded-full
                    bg-black/70 text-white
                    flex items-center justify-center
                    opacity-0 group-hover:opacity-100
                    transition-opacity
                  "
                >
                  <X className="w-3 h-3" />
                </button>

                {/* File name — bottom gradient scrim (functional, theme-agnostic) */}
                <div className="absolute bottom-0 left-0 right-0 px-1 py-0.5 bg-gradient-to-t from-black/75 to-transparent text-[0.5625rem] text-white truncate">
                  {getFileName(path)}
                </div>
              </div>
            ))}

            {/* Add more button for r2v */}
            {canAddMore && (
              <button
                type="button"
                onClick={handleClick}
                disabled={uploading}
                className="
                  w-[72px] h-[72px] rounded-[14px] bg-input-bg
                  border border-dashed border-border-subtle
                  flex items-center justify-center
                  text-text-muted hover:text-foreground hover:border-foreground/30 hover:bg-hover-bg
                  transition-colors disabled:opacity-40
                "
              >
                <ImagePlus className="w-5 h-5" />
              </button>
            )}
          </div>

          {/* File count for r2v */}
          <div className="font-mono text-[0.6875rem] text-text-muted">
            {t('media.fileCount', { current: inputMedia.length, max: config.maxFiles })}
          </div>
          {overLimit && (
            <div role="alert" className="rounded-[12px] border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
              {t('media.limitExceeded', { max: config.maxFiles, current: inputMedia.length })}
            </div>
          )}
        </div>
      ) : (
        <SingleRefPreview
          key={inputMedia[0]}
          path={inputMedia[0]}
          onRemove={() => handleRemove(0)}
        />
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleReplace}
          disabled={uploading}
          className={ACTION_BTN_CLASS}
        >
          {uploading ? t('media.uploading') : t('media.replaceFile')}
        </button>
        <button
          type="button"
          onClick={openAssetPicker}
          className={ACTION_BTN_CLASS}
        >
          {t('media.pickFromLibrary')}
        </button>
      </div>

      {mediaError && (
        <div role="alert" className="rounded-[12px] border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {mediaError}
        </div>
      )}

      {fileInput}

      <AssetPickerModal
        isOpen={showAssetPicker}
        onClose={closeAssetPicker}
        onSelect={handleAssetSelect}
        accept={acceptType}
        allowedImageMimeTypes={referenceImageMimeTypes}
      />
    </div>
  );
}
