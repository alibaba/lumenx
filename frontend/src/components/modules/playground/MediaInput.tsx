'use client';

import { useRef, useState, useCallback } from 'react';
import { ImagePlus, Film, X } from 'lucide-react';
import { playgroundApi } from '@/lib/api';
import { usePlaygroundStore, type PlaygroundMode } from './usePlaygroundStore';
import AssetPickerModal from './AssetPickerModal';

// ---------------------------------------------------------------------------
// Mode config
// ---------------------------------------------------------------------------

interface ModeConfig {
  label: string;
  accept: string;
  hint: string;
  multiple: boolean;
  maxFiles: number;
  icon: 'image' | 'video';
}

const MODE_CONFIG: Partial<Record<PlaygroundMode, ModeConfig>> = {
  t2i: {
    label: '参考图片（可选）',
    accept: 'image/*',
    hint: '上传参考图自动切换为图像编辑模式',
    multiple: true,
    maxFiles: 9,
    icon: 'image',
  },
  i2i: {
    label: '参考图片',
    accept: 'image/*',
    hint: '支持 JPG / PNG / WebP，建议尺寸不小于 512px',
    multiple: false,
    maxFiles: 1,
    icon: 'image',
  },
  i2v: {
    label: '首帧图片',
    accept: 'image/*',
    hint: '支持 JPG / PNG / WebP，建议尺寸不小于 720px',
    multiple: false,
    maxFiles: 1,
    icon: 'image',
  },
  r2v: {
    label: '参考图片',
    accept: 'image/*',
    hint: '支持 JPG / PNG / WebP，最多 9 张参考图',
    multiple: true,
    maxFiles: 9,
    icon: 'image',
  },
  v2v: {
    label: '源视频',
    accept: 'video/*',
    hint: '支持 MP4 / MOV / WebM',
    multiple: false,
    maxFiles: 1,
    icon: 'video',
  },
};

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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MediaInput() {
  const mode = usePlaygroundStore((s) => s.mode);
  const modelId = usePlaygroundStore((s) => s.modelId);
  const inputMedia = usePlaygroundStore((s) => s.inputMedia);
  const setInputMedia = usePlaygroundStore((s) => s.setInputMedia);

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
      label: '参考素材（图片/视频/音频）',
      accept: 'image/*,video/*,audio/*',
      hint: 'Seedance 支持图片、视频、音频作为参考素材',
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
        toUpload.map((file) => playgroundApi.uploadMedia(file))
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
  // Render: empty state
  // -------------------------------------------------------------------------

  if (!hasMedia) {
    return (
      <div className="space-y-2">
        <label className="block text-xs font-medium text-text-secondary">
          {config.label}
        </label>

        <div
          onClick={handleClick}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`
            border border-dashed rounded-xl p-6
            flex flex-col items-center gap-3 text-center cursor-pointer
            transition-colors
            ${
              dragOver
                ? 'border-primary/50 bg-primary/5'
                : 'border-glass-border hover:border-primary/40 hover:bg-hover-bg'
            }
            ${uploading ? 'pointer-events-none opacity-60' : ''}
          `}
        >
          {config.icon === 'video' ? (
            <Film className="w-8 h-8 text-text-secondary" />
          ) : (
            <ImagePlus className="w-8 h-8 text-text-secondary" />
          )}

          <span className="text-xs text-text-secondary">
            {uploading ? '上传中...' : '拖拽或点击上传'}
          </span>

          <span className="text-[11px] text-text-muted">{config.hint}</span>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleClick}
            disabled={uploading}
            className="
              flex-1 px-3 py-1.5 rounded-lg text-xs
              border border-glass-border text-text-secondary
              hover:bg-hover-bg hover:text-foreground
              transition-colors disabled:opacity-40
            "
          >
            本地上传
          </button>
          <button
            type="button"
            onClick={() => setShowAssetPicker(true)}
            className="
              flex-1 px-3 py-1.5 rounded-lg text-xs
              border border-primary/30 text-primary
              hover:bg-primary/10
              transition-colors
            "
          >
            从资产库选取
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
  // -------------------------------------------------------------------------

  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-text-secondary">
        {config.label}
      </label>

      <div className="border border-solid border-glass-border rounded-xl p-3 space-y-3 bg-glass">
        {/* Thumbnail row */}
        <div className="flex flex-wrap gap-2">
          {inputMedia.map((path, index) => (
            <div
              key={path + index}
              className="group relative w-20 h-[60px] rounded-lg overflow-hidden bg-input-bg border border-glass-border"
            >
              {isVideoPath(path) ? (
                <video
                  src={path}
                  className="w-full h-full object-cover"
                  muted
                />
              ) : (
                <img
                  src={path}
                  alt=""
                  className="w-full h-full object-cover"
                />
              )}

              {/* Remove button on hover */}
              <button
                type="button"
                onClick={() => handleRemove(index)}
                className="
                  absolute top-0.5 right-0.5
                  w-4 h-4 rounded-full
                  bg-black/70 text-white/80
                  flex items-center justify-center
                  opacity-0 group-hover:opacity-100
                  transition-opacity
                "
              >
                <X className="w-3 h-3" />
              </button>

              {/* File name tooltip */}
              <div className="absolute bottom-0 left-0 right-0 px-1 py-0.5 bg-black/60 text-[9px] text-white/70 truncate">
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
                w-20 h-[60px] rounded-lg
                border border-dashed border-glass-border
                flex items-center justify-center
                text-text-muted hover:text-text-secondary hover:border-primary/40
                transition-colors disabled:opacity-40
              "
            >
              <ImagePlus className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* File count for r2v */}
        {config.multiple && (
          <div className="text-[11px] text-text-secondary">
            {inputMedia.length} / {config.maxFiles} 张
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleReplace}
          disabled={uploading}
          className="
            flex-1 px-3 py-1.5 rounded-lg text-xs
            border border-glass-border text-text-secondary
            hover:bg-hover-bg hover:text-foreground
            transition-colors disabled:opacity-40
          "
        >
          {uploading ? '上传中...' : '替换文件'}
        </button>
        <button
          type="button"
          onClick={() => setShowAssetPicker(true)}
          className="
            flex-1 px-3 py-1.5 rounded-lg text-xs
            border border-primary/30 text-primary
            hover:bg-primary/10
            transition-colors
          "
        >
          从资产库选取
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
