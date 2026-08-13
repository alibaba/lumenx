'use client';

import { useRef, useState, useCallback } from 'react';
import { ImagePlus, Film, X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { API_URL, playgroundApi } from '@/lib/api';
import { usePlaygroundStore, type PlaygroundMode } from './usePlaygroundStore';
import AssetPickerModal from './AssetPickerModal';

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

  const isSeedance = modelId.startsWith('seedance');

  let config = MODE_CONFIG[mode];

  // Override r2v config when Seedance is selected
  if (config && mode === 'r2v' && isSeedance) {
    config = {
      ...config,
      labelKey: 'media.labelRefMaterialAV',
      accept: 'image/*,video/*',
      hintKey: 'r2vSeedance',
    };
  }

  // Don't render for t2v mode (no input media needed)
  if (!config) return null;

  const hasMedia = inputMedia.length > 0;
  const canAddMore = config.multiple && inputMedia.length < config.maxFiles;

  // -------------------------------------------------------------------------
  // Upload handler
  // -------------------------------------------------------------------------

  const handleFiles = async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;

    // Respect max file limit
    const available = config.maxFiles - inputMedia.length;
    const toUpload = fileArray.slice(0, available);

    setUploading(true);
    try {
      const results = await Promise.all(
        toUpload.map((file) => playgroundApi.uploadMedia(file, mode))
      );
      const newPaths = results.map((r) => r.path);

      if (config.multiple) {
        setInputMedia([...inputMedia, ...newPaths]);
      } else {
        setInputMedia(newPaths);
      }
    } catch (err) {
      console.error('[MediaInput] upload failed:', err);
    } finally {
      setUploading(false);
    }
  };

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
    [inputMedia, config]
  );

  const handleRemove = (index: number) => {
    const updated = inputMedia.filter((_, i) => i !== index);
    setInputMedia(updated);
  };

  const handleReplace = () => {
    fileInputRef.current?.click();
  };

  const handleAssetSelect = (path: string) => {
    setInputMedia([...inputMedia, path]);
  };

  // Determine accept type for AssetPickerModal
  const acceptType: 'image' | 'video' | 'all' =
    mode === 'r2v' && isSeedance
      ? 'all'
      : config.icon === 'video'
        ? 'video'
        : 'image';

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

          <span className="text-[0.6875rem] text-text-muted">{t(`media.hints.${config.hintKey}`)}</span>
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
            onClick={() => setShowAssetPicker(true)}
            className={ACTION_BTN_CLASS}
          >
            {t('media.pickFromLibrary')}
          </button>
        </div>

        {fileInput}

        <AssetPickerModal
          isOpen={showAssetPicker}
          onClose={() => setShowAssetPicker(false)}
          onSelect={handleAssetSelect}
          accept={acceptType}
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
          onClick={() => setShowAssetPicker(true)}
          className={ACTION_BTN_CLASS}
        >
          {t('media.pickFromLibrary')}
        </button>
      </div>

      {fileInput}

      <AssetPickerModal
        isOpen={showAssetPicker}
        onClose={() => setShowAssetPicker(false)}
        onSelect={handleAssetSelect}
        accept={acceptType}
      />
    </div>
  );
}
