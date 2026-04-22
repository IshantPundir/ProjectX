'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { Input } from '@/components/px'
import { Textarea } from '@/components/px'
import { Label } from '@/components/px'
import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import { StageConfigDrawer } from '@/components/dashboard/pipeline/StageConfigDrawer'
import { useCreateTemplate } from '@/lib/hooks/use-save-pipeline-template'
import type { PipelineStageInput } from '@/lib/api/pipelines'

function makeBlankStage(position: number): PipelineStageInput {
  return {
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

export default function NewTemplatePage() {
  const params = useParams<{ unitId: string }>()
  const unitId = params.unitId
  const router = useRouter()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [stages, setStages] = useState<PipelineStageInput[]>([makeBlankStage(0)])
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)

  const createMutation = useCreateTemplate(unitId)

  function updateStage(index: number, updated: PipelineStageInput) {
    setStages(stages.map((s, i) => (i === index ? updated : s)))
  }

  function addStage() {
    setStages([...stages, makeBlankStage(stages.length)])
  }

  function deleteStage(index: number) {
    setStages(
      stages.filter((_, i) => i !== index).map((s, i) => ({ ...s, position: i })),
    )
    setSelectedIndex(null)
  }

  function handleSave() {
    if (!name.trim()) {
      toast.error('Template name is required')
      return
    }
    createMutation.mutate(
      {
        source: 'scratch',
        name: name.trim(),
        description: description.trim() || null,
        is_default: false,
        stages,
      },
      {
        onSuccess: () => router.push(`/settings/org-units/${unitId}/pipeline-templates`),
      },
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
        <h1 className="text-2xl font-semibold text-zinc-900">New Template</h1>
      </div>

      <div className="space-y-4 mb-6">
        <div>
          <Label htmlFor="name">Name</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Engineering — Custom Pipeline"
            className="mt-1"
          />
        </div>
        <div>
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional"
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
        <Button onClick={handleSave} disabled={createMutation.isPending}>
          {createMutation.isPending ? 'Saving…' : 'Save template'}
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
