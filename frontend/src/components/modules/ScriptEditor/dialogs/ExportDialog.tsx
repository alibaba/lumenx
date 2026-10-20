'use client'

import { useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  X,
  FileText,
  FileCode,
  FileDown,
  Printer,
  FileSpreadsheet,
  Loader2,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react'
import type { Editor } from '@tiptap/react'
import { useTranslations } from 'next-intl'
import { toFountain, toFDX, toPlainText } from './serializers'
import { scriptEditorApi } from '@/lib/scriptEditorApi'

export interface ExportDialogProps {
  open: boolean
  onClose: () => void
  projectId: string
  editor: Editor | null
}

interface FormatOption {
  id: string
  label?: string
  labelKey?: string
  descKey: string
  icon: React.ReactNode
  ext: string
  backend: boolean
}

const FORMAT_OPTIONS: FormatOption[] = [
  {
    id: 'fountain',
    label: 'Fountain',
    descKey: 'dialogs.export.fountainDesc',
    icon: <FileText size={20} className="text-green-400" />,
    ext: '.fountain',
    backend: false,
  },
  {
    id: 'fdx',
    label: 'Final Draft (FDX)',
    descKey: 'dialogs.export.fdxDesc',
    icon: <FileCode size={20} className="text-blue-400" />,
    ext: '.fdx',
    backend: false,
  },
  {
    id: 'txt',
    labelKey: 'dialogs.export.txtLabel',
    descKey: 'dialogs.export.txtDesc',
    icon: <FileDown size={20} className="text-gray-400" />,
    ext: '.txt',
    backend: false,
  },
  {
    id: 'pdf',
    label: 'PDF',
    descKey: 'dialogs.export.pdfDesc',
    icon: <Printer size={20} className="text-red-400" />,
    ext: '.pdf',
    backend: true,
  },
  {
    id: 'docx',
    label: 'DOCX',
    descKey: 'dialogs.export.docxDesc',
    icon: <FileSpreadsheet size={20} className="text-indigo-400" />,
    ext: '.docx',
    backend: true,
  },
]

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  setTimeout(() => {
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, 100)
}

function downloadText(content: string, filename: string, mimeType = 'text/plain') {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` })
  downloadBlob(blob, filename)
}

export default function ExportDialog({ open, onClose, projectId, editor }: ExportDialogProps) {
  const t = useTranslations('scriptEditor')
  const [status, setStatus] = useState<'idle' | 'exporting' | 'success' | 'error'>('idle')
  const [activeFormat, setActiveFormat] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const handleClose = useCallback(() => {
    setStatus('idle')
    setActiveFormat(null)
    setErrorMsg('')
    onClose()
  }, [onClose])

  const handleExport = useCallback(
    async (format: FormatOption) => {
      if (!editor) return

      setActiveFormat(format.id)
      setStatus('exporting')
      setErrorMsg('')

      const doc = editor.getJSON()
      const filename = `script${format.ext}`

      try {
        if (!format.backend) {
          // Frontend serialization
          let content: string
          switch (format.id) {
            case 'fountain':
              content = toFountain(doc)
              break
            case 'fdx':
              content = toFDX(doc)
              downloadText(content, filename, 'application/xml')
              setStatus('success')
              setTimeout(handleClose, 800)
              return
            case 'txt':
              content = toPlainText(doc)
              break
            default:
              content = toPlainText(doc)
          }
          downloadText(content, filename)
          setStatus('success')
          setTimeout(handleClose, 800)
        } else {
          // Backend export (PDF/DOCX)
          const blob = await scriptEditorApi.exportDocument(projectId, doc, format.id)
          downloadBlob(blob, filename)
          setStatus('success')
          setTimeout(handleClose, 800)
        }
      } catch (e: any) {
        setStatus('error')
        setErrorMsg(e?.response?.data?.detail || e?.message || t('dialogs.export.failed'))
      }
    },
    [editor, projectId, handleClose]
  )

  if (!open) return null

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
            className="relative z-10 w-full max-w-md rounded-2xl border border-white/10 bg-[#0a0a0f]/90 p-6 shadow-2xl backdrop-blur-xl"
            initial={{ scale: 0.95, opacity: 0, y: 20 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.95, opacity: 0, y: 20 }}
            transition={{ type: 'spring', damping: 25, stiffness: 300 }}
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-semibold text-white">{t('dialogs.export.title')}</h2>
              <button
                onClick={handleClose}
                className="rounded-lg p-1.5 text-white/50 hover:bg-white/10 hover:text-white transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            {/* Format list */}
            <div className="space-y-2">
              {FORMAT_OPTIONS.map((format) => {
                const isActive = activeFormat === format.id
                const isExporting = isActive && status === 'exporting'
                const isSuccess = isActive && status === 'success'
                const isError = isActive && status === 'error'

                return (
                  <button
                    key={format.id}
                    onClick={() => handleExport(format)}
                    disabled={status === 'exporting'}
                    className={`
                      w-full flex items-center gap-3 rounded-xl px-4 py-3 text-left transition-all
                      ${isActive
                        ? isError
                          ? 'border border-red-500/30 bg-red-500/10'
                          : isSuccess
                            ? 'border border-green-500/30 bg-green-500/10'
                            : 'border border-blue-500/30 bg-blue-500/10'
                        : 'border border-white/5 bg-white/[0.02] hover:bg-white/[0.06] hover:border-white/10'
                      }
                      disabled:opacity-50 disabled:cursor-not-allowed
                    `}
                  >
                    <div className="shrink-0">
                      {isExporting ? (
                        <Loader2 size={20} className="text-blue-400 animate-spin" />
                      ) : isSuccess ? (
                        <CheckCircle2 size={20} className="text-green-400" />
                      ) : isError ? (
                        <AlertCircle size={20} className="text-red-400" />
                      ) : (
                        format.icon
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white">{format.labelKey ? t(format.labelKey) : format.label}</p>
                      <p className="text-xs text-white/50 truncate">
                        {isError ? errorMsg : t(format.descKey)}
                      </p>
                    </div>
                    <span className="shrink-0 text-xs text-white/30">{format.ext}</span>
                  </button>
                )
              })}
            </div>

            {/* Footer */}
            <p className="mt-4 text-xs text-white/30 text-center">
              {t('dialogs.export.footer')}
            </p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
