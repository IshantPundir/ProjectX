'use client'

import { useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'

import { Button } from '@/components/ui/button'
import { TemplateLibraryCard } from '@/components/dashboard/pipeline/TemplateLibraryCard'
import { StarterPackBrowser } from '@/components/dashboard/pipeline/StarterPackBrowser'
import { usePipelineTemplates } from '@/lib/hooks/use-pipeline-templates'
import {
  useCreateTemplate,
  useSetDefault,
  useDeleteTemplate,
} from '@/lib/hooks/use-save-pipeline-template'
import type { StarterTemplate } from '@/lib/api/pipelines'

export default function PipelineTemplatesPage() {
  const params = useParams<{ unitId: string }>()
  const unitId = params.unitId

  const [showStarters, setShowStarters] = useState(false)
  const { data: templates, isLoading } = usePipelineTemplates(unitId)
  const createMutation = useCreateTemplate(unitId)
  const setDefaultMutation = useSetDefault(unitId)
  const deleteMutation = useDeleteTemplate(unitId)

  function handleUseStarter(starter: StarterTemplate) {
    createMutation.mutate(
      {
        source: 'starter',
        starter_key: starter.key,
        name: starter.name,
        description: starter.description,
        is_default: templates?.length === 0, // first template becomes default
      },
      {
        onSuccess: () => setShowStarters(false),
      },
    )
  }

  function handleDelete(templateId: string) {
    if (confirm('Delete this template? This cannot be undone.')) {
      deleteMutation.mutate(templateId)
    }
  }

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <Link
          href={`/settings/org-units/${unitId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to org unit
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900">Pipeline Templates</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Reusable interview pipelines for jobs in this org unit.
        </p>
      </div>

      <div className="flex gap-2 mb-6">
        <Button onClick={() => setShowStarters(!showStarters)} variant="outline">
          {showStarters ? 'Hide starter pack' : 'Browse starter pack'}
        </Button>
        <Link href={`/settings/org-units/${unitId}/pipeline-templates/new`}>
          <Button>+ Create custom template</Button>
        </Link>
      </div>

      {showStarters && (
        <div className="mb-6 p-5 bg-zinc-50 rounded-lg border border-zinc-200">
          <h2 className="text-sm font-semibold mb-3">Starter Pack</h2>
          <StarterPackBrowser onUse={handleUseStarter} />
        </div>
      )}

      {isLoading && <div className="text-sm text-zinc-500">Loading templates…</div>}

      {templates && templates.length === 0 && !showStarters && (
        <div className="bg-white border border-dashed border-zinc-300 rounded-lg p-12 text-center">
          <h2 className="text-lg font-semibold text-zinc-900 mb-2">No templates yet</h2>
          <p className="text-sm text-zinc-500 mb-6">
            Browse the starter pack to get going quickly, or create a custom template from scratch.
          </p>
          <Button onClick={() => setShowStarters(true)}>Browse starter pack</Button>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {templates?.map((tpl) => (
          <TemplateLibraryCard
            key={tpl.id}
            template={tpl}
            editHref={`/settings/org-units/${unitId}/pipeline-templates/${tpl.id}`}
            onSetDefault={() => setDefaultMutation.mutate(tpl.id)}
            onDelete={() => handleDelete(tpl.id)}
          />
        ))}
      </div>
    </div>
  )
}
