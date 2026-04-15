'use client'

import { useState, useEffect, useRef } from 'react'
import type { PipelineTemplate, StarterTemplate } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { StarterPackBrowser } from './StarterPackBrowser'
import { usePipelineTemplates } from '@/lib/hooks/use-pipeline-templates'

type Props = {
  orgUnitId: string
  open: boolean
  onClose: () => void
  onPickTemplate: (template: PipelineTemplate) => void
  onPickStarter: (starter: StarterTemplate) => void
}

export function TemplatePickerDialog({
  orgUnitId,
  open,
  onClose,
  onPickTemplate,
  onPickStarter,
}: Props) {
  const [tab, setTab] = useState<'library' | 'starters'>('library')
  const { data: templates } = usePipelineTemplates(orgUnitId, { enabled: open })

  // WCAG 2.4.3: focus the close button when the dialog opens. Templates
  // load asynchronously so focusing a template card would be racy — the
  // close button is always rendered, and arrow/tab keys let the user
  // walk into the content from there.
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    closeButtonRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="template-picker-title"
        className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h2 id="template-picker-title" className="text-sm font-semibold">Pick a pipeline</h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="text-zinc-400 hover:text-zinc-900 text-xl leading-none"
          >
            ×
          </button>
        </div>
        <div className="px-5 pt-3">
          <div className="flex gap-1 border-b border-zinc-200">
            <button
              type="button"
              onClick={() => setTab('library')}
              aria-pressed={tab === 'library'}
              className={`text-sm px-3 py-2 border-b-2 ${tab === 'library' ? 'border-blue-600 text-blue-600' : 'border-transparent text-zinc-500'}`}
            >
              Your library
            </button>
            <button
              type="button"
              onClick={() => setTab('starters')}
              aria-pressed={tab === 'starters'}
              className={`text-sm px-3 py-2 border-b-2 ${tab === 'starters' ? 'border-blue-600 text-blue-600' : 'border-transparent text-zinc-500'}`}
            >
              Starter pack
            </button>
          </div>
        </div>
        <div className="px-5 py-4 overflow-y-auto flex-1">
          {tab === 'library' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {templates?.length === 0 && (
                <div className="col-span-2 text-sm text-zinc-500">
                  No templates in your library yet. Try the starter pack tab.
                </div>
              )}
              {templates?.map((t) => (
                <div key={t.id} className="bg-zinc-50 border border-zinc-200 rounded-lg p-4">
                  <div className="text-sm font-semibold mb-1">{t.name}</div>
                  <div className="text-xs text-zinc-500 mb-3">
                    {t.stages.map((s) => s.name).join(' → ')}
                  </div>
                  <Button size="sm" onClick={() => onPickTemplate(t)}>
                    Use this
                  </Button>
                </div>
              ))}
            </div>
          )}
          {tab === 'starters' && <StarterPackBrowser onUse={onPickStarter} />}
        </div>
      </div>
    </div>
  )
}
