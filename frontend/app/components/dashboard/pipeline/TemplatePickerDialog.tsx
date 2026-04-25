'use client'

import { useState } from 'react'
import type { PipelineTemplate, StarterTemplate } from '@/lib/api/pipelines'
import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/px'
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

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent widthClass="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Pick a pipeline</DialogTitle>
        </DialogHeader>
        <div className="px-5 pt-3">
          <div
            role="tablist"
            aria-label="Template source"
            className="flex gap-1 border-b border-zinc-200"
          >
            <button
              type="button"
              role="tab"
              id="tpd-tab-library"
              aria-selected={tab === 'library'}
              aria-controls="tpd-panel-library"
              tabIndex={tab === 'library' ? 0 : -1}
              onClick={() => setTab('library')}
              className={`text-sm px-3 py-2 border-b-2 ${tab === 'library' ? 'border-blue-600 text-blue-600' : 'border-transparent text-zinc-500'}`}
            >
              Your library
            </button>
            <button
              type="button"
              role="tab"
              id="tpd-tab-starters"
              aria-selected={tab === 'starters'}
              aria-controls="tpd-panel-starters"
              tabIndex={tab === 'starters' ? 0 : -1}
              onClick={() => setTab('starters')}
              className={`text-sm px-3 py-2 border-b-2 ${tab === 'starters' ? 'border-blue-600 text-blue-600' : 'border-transparent text-zinc-500'}`}
            >
              Starter pack
            </button>
          </div>
        </div>
        <div className="px-5 py-4 overflow-y-auto flex-1">
          {tab === 'library' && (
            <div
              role="tabpanel"
              id="tpd-panel-library"
              aria-labelledby="tpd-tab-library"
              className="grid grid-cols-1 md:grid-cols-2 gap-4"
            >
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
          {tab === 'starters' && (
            <div
              role="tabpanel"
              id="tpd-panel-starters"
              aria-labelledby="tpd-tab-starters"
            >
              <StarterPackBrowser onUse={onPickStarter} />
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
