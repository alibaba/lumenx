'use client'

import { useState, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Upload, FileText, FileCode, X, AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { scriptEditorApi } from '@/lib/scriptEditorApi'

export interface ImportDialogProps {
  open: boolean
  onClose: () => void
  projectId: string
  onImportSuccess: (content: any) => void
}

type FileType = 'fdx' | 'fountain' | 'txt'

const ACCEPTED_EXTENSIONS: Record<string, FileType> = {
  '.fdx': 'fdx',
  '.fountain': 'fountain',
  '.txt': 'txt',
}

const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10 MB

function getFileExtension(filename: string): string {
  const idx = filename.lastIndexOf('.')
  return idx >= 0 ? filename.slice(idx).toLowerCase() : ''
}

function getFileIcon(ext: string) {
  if (ext === '.fdx') return <FileCode size={24} className="text-blue-400" />
  if (ext === '.fountain') return <FileText size={24} className="text-green-400" />
  return <FileText size={24} className="text-gray-400" />
}

export default function ImportDialog({ open, onClose, projectId, onImportSuccess }: ImportDialogProps) {
  const t = useTranslations('scriptEditor')
  const [isDragOver, setIsDragOver] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')
  const [sceneCount, setSceneCount] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const reset = useCallback(() => {
    setSelectedFile(null)
    setStatus('idle')
    setErrorMsg('')
    setSceneCount(0)
  }, [])

  const handleClose = useCallback(() => {
    reset()
    onClose()
  }, [reset, onClose])

  const validateFile = useCallback((file: File): string | null => {
    const ext = getFileExtension(file.name)
    if (!ACCEPTED_EXTENSIONS[ext]) {
      return t('dialogs.import.unsupportedType', { ext })
    }
    if (file.size > MAX_FILE_SIZE) {
      return t('dialogs.import.fileTooLarge', { size: (file.size / 1024 / 1024).toFixed(1) })
    }
    return null
  }, [t])

  const handleFile = useCallback(async (file: File) => {
    const err = validateFile(file)
    if (err) {
      setErrorMsg(err)
      setStatus('error')
      return
    }

    setSelectedFile(file)
    setStatus('uploading')
    setErrorMsg('')

    try {
      const result = await scriptEditorApi.importDocument(projectId, file)
      // Count scenes in the result
      const content = result.content || result
      const scenes = (content.content || []).filter(
        (n: any) => n.type === 'sceneHeading'
      )
      setSceneCount(scenes.length)
      setStatus('success')

      // Auto-close after brief delay
      setTimeout(() => {
        onImportSuccess(content)
        handleClose()
      }, 1200)
    } catch (e: any) {
      setStatus('error')
      setErrorMsg(e?.response?.data?.detail || e?.message || t('dialogs.import.failed'))
    }
  }, [validateFile, projectId, onImportSuccess, handleClose])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragOver(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
  }, [])

  const onFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFile(file)
      // Reset input so same file can be selected again
      e.target.value = ''
    },
    [handleFile]
  )

  if (!open) return null

  const ext = selectedFile ? getFileExtension(selectedFile.name) : ''

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} />

          {/* Dialog */}
          <motion.div
            className="relative z-10 w-full max-w-lg rounded-2xl border border-white/10 bg-[#0a0a0f]/90 p-6 shadow-2xl backdrop-blur-xl"
            initial={{ scale: 0.95, opacity: 0, y: 20 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.95, opacity: 0, y: 20 }}
            transition={{ type: 'spring', damping: 25, stiffness: 300 }}
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-semibold text-white">{t('dialogs.import.title')}</h2>
              <button
                onClick={handleClose}
                className="rounded-lg p-1.5 text-white/50 hover:bg-white/10 hover:text-white transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            {/* Drop Zone */}
            <div
              onDrop={onDrop}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onClick={() => status === 'idle' && fileInputRef.current?.click()}
              className={`
                relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-10 transition-all cursor-pointer
                ${isDragOver
                  ? 'border-blue-400 bg-blue-500/10'
                  : status === 'error'
                    ? 'border-red-400/50 bg-red-500/5'
                    : status === 'success'
                      ? 'border-green-400/50 bg-green-500/5'
                      : 'border-white/20 bg-white/[0.02] hover:border-white/30 hover:bg-white/[0.04]'
                }
              `}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".fdx,.fountain,.txt"
                onChange={onFileSelect}
                className="hidden"
              />

              {status === 'idle' && (
                <>
                  <Upload size={32} className="text-white/40 mb-3" />
                  <p className="text-sm text-white/70 text-center">
                    {t('dialogs.import.dropzone')}
                  </p>
                  <p className="text-xs text-white/40 mt-2">
                    {t('dialogs.import.supportedFormats')}
                  </p>
                </>
              )}

              {status === 'uploading' && (
                <div className="flex flex-col items-center gap-3">
                  <Loader2 size={28} className="text-blue-400 animate-spin" />
                  <div className="flex items-center gap-2">
                    {getFileIcon(ext)}
                    <span className="text-sm text-white/80">{selectedFile?.name}</span>
                  </div>
                  <p className="text-xs text-white/50">{t('dialogs.import.parsing')}</p>
                </div>
              )}

              {status === 'success' && (
                <div className="flex flex-col items-center gap-3">
                  <CheckCircle2 size={28} className="text-green-400" />
                  <div className="flex items-center gap-2">
                    {getFileIcon(ext)}
                    <span className="text-sm text-white/80">{selectedFile?.name}</span>
                  </div>
                  <p className="text-xs text-green-400/80">
                    {t('dialogs.import.success', { count: sceneCount })}
                  </p>
                </div>
              )}

              {status === 'error' && (
                <div className="flex flex-col items-center gap-3">
                  <AlertCircle size={28} className="text-red-400" />
                  <p className="text-sm text-red-300">{errorMsg}</p>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      reset()
                    }}
                    className="text-xs text-white/60 hover:text-white underline mt-1"
                  >
                    {t('dialogs.import.retry')}
                  </button>
                </div>
              )}
            </div>

            {/* Footer hint */}
            <p className="mt-4 text-xs text-white/30 text-center">
              {t('dialogs.import.replaceWarning')}
            </p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
