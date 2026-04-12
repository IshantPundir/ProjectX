'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import { StageConfigDrawer } from '@/components/dashboard/pipeline/StageConfigDrawer'
import { usePipelineTemplates } from '@/lib/hooks/use-pipeline-templates'
import { useUpdateTemplate } from '@/lib/hooks/use-save-pipeline-template'
import type { PipelineStageUpdateInput, PipelineTemplate } from '@/lib/api/pipelines'

function makeBlankStage(position: number): PipelineStageUpdateInput {
  return {
    id: undefined, // new stage — backend will assign a UUID
    position,
    name: 'New Stage',
    stage_type: 'phone_screen',
    duration_minutes: 10,
    difficulty: 'easy',
    signal_filter: {
      include_types: ['competency', 'experience', 'credential', 'behavioral'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

type EditFormProps = {
  template: PipelineTemplate
  unitId: string
  templateId: string
}

function EditTemplateForm({ template, unitId, templateId }: EditFormProps) {
  const router = useRouter()

  const [name, setName] = useState(() => template.name)
  const [description, setDescription] = useState(() => template.description ?? '')
  const [stages, setStages] = useState<PipelineStageUpdateInput[]>(() =>
    template.stages.map((s) => ({ ...s })),
  )
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)

  const updateMutation = useUpdateTemplate(unitId, templateId)

  function updateStage(index: number, updated: PipelineStageUpdateInput) {
    setStages(stages.map((s, i) => (i === index ? updated : s)))
  }
  function addStage() {
    setStages([...stages, makeBlankStage(stages.length)])
  }
  function deleteStage(index: number) {
    setStages(stages.filter((_, i) => i !== index).map((s, i) => ({ ...s, position: i })))
    setSelectedIndex(null)
  }
  function handleSave() {
    if (!name.trim()) {
      toast.error('Template name is required')
      return
    }
    updateMutation.mutate(
      { name: name.trim(), description: description.trim() || null, stages },
      { onSuccess: () => router.push(`/settings/org-units/${unitId}/pipeline-templates`) },
    )
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <Link
          href={`/settings/org-units/${unitId}/pipeline-templates`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to templates
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900">Edit Template</h1>
      </div>

      <div className="space-y-4 mb-6">
        <div>
          <Label htmlFor="name">Name</Label>
          <Input id="name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
        </div>
        <div>
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="mt-1"
            rows={2}
          />
        </div>
      </div>

      <div className="bg-gradient-to-b from-zinc-50 to-white rounded-lg border border-zinc-200 p-8 mb-4">
        <h2 className="text-sm font-semibold text-zinc-900 mb-0.5">Interview Pipeline</h2>
        <p className="text-xs text-zinc-500 mb-4">Stages candidates move through in order</p>
        <PipelineFunnel
          stages={stages}
          onStageClick={setSelectedIndex}
          onStageDelete={stages.length > 1 ? (i) => deleteStage(i) : undefined}
          selectedIndex={selectedIndex ?? undefined}
        />
        <div className="flex justify-center mt-4">
          <Button variant="outline" size="sm" onClick={addStage}>
            + Add stage
          </Button>
        </div>
      </div>

      <div className="flex gap-2">
        <Button onClick={handleSave} disabled={updateMutation.isPending}>
          {updateMutation.isPending ? 'Saving…' : 'Save changes'}
        </Button>
        <Link href={`/settings/org-units/${unitId}/pipeline-templates`}>
          <Button variant="outline">Cancel</Button>
        </Link>
      </div>

      {selectedIndex !== null && stages[selectedIndex] !== undefined && (
        <StageConfigDrawer
          stage={stages[selectedIndex]}
          onChange={(updated) => updateStage(selectedIndex, updated)}
          onClose={() => setSelectedIndex(null)}
        />
      )}
    </div>
  )
}

export default function EditTemplatePage() {
  const params = useParams<{ unitId: string; templateId: string }>()
  const unitId = params.unitId
  const templateId = params.templateId

  const { data: templates, isLoading } = usePipelineTemplates(unitId)
  const template = templates?.find((t) => t.id === templateId)

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading template…</div>
  }
  if (!template) {
    return (
      <div className="text-sm text-zinc-500">
        Template not found.{' '}
        <Link
          href={`/settings/org-units/${unitId}/pipeline-templates`}
          className="text-blue-600 hover:underline"
        >
          Back to templates
        </Link>
      </div>
    )
  }

  return <EditTemplateForm key={template.id} template={template} unitId={unitId} templateId={templateId} />
}
