'use client'

import { useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import { StageConfigDrawer } from '@/components/dashboard/pipeline/StageConfigDrawer'
import { TemplatePickerDialog } from '@/components/dashboard/pipeline/TemplatePickerDialog'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useCreateJobPipeline } from '@/lib/hooks/use-create-job-pipeline'
import { useSaveJobPipeline, useResetJobPipeline } from '@/lib/hooks/use-save-job-pipeline'
import type {
  PipelineStageInput,
  JobPipelineInstance,
} from '@/lib/api/pipelines'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

function makeBlankStage(position: number): PipelineStageInput {
  return {
    position,
    name: 'New Stage',
    stage_type: 'phone_screen',
    duration_minutes: 10,
    difficulty: 'easy',
    signal_filter: {
      include_types: ['competency', 'experience', 'credential', 'behavioral'],
      include_stages: ['screen'],
      include_weights: [1, 2, 3],
      include_priority: ['required', 'preferred'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function stripId({ id: _id, ...rest }: { id: string } & PipelineStageInput): PipelineStageInput {
  return rest
}

type EditorProps = {
  job: JobPostingWithSnapshot
  pipeline: JobPipelineInstance
  jobId: string
}

function JobPipelineEditor({ job, pipeline, jobId }: EditorProps) {
  const saveMutation = useSaveJobPipeline(jobId)
  const resetMutation = useResetJobPipeline(jobId)

  const [stages, setStages] = useState<PipelineStageInput[]>(() =>
    pipeline.stages.map(stripId),
  )
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)

  function updateStage(index: number, updated: PipelineStageInput) {
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
    saveMutation.mutate({ stages })
  }
  function handleReset() {
    if (confirm('Discard your edits and reset to the source template?')) {
      resetMutation.mutate()
    }
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <Link href={`/jobs/${jobId}`} className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block">
          ← Back to job
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900">{job.title}</h1>
        <p className="text-sm text-zinc-500">
          Pipeline{pipeline.source_template_name && ` · from "${pipeline.source_template_name}"`}
        </p>
      </div>

      <div className="flex flex-wrap gap-2 mb-6">
        <Button onClick={handleSave} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button variant="outline" onClick={() => setPickerOpen(true)}>
          Swap template
        </Button>
        {pipeline.source_template_id && (
          <Button variant="outline" onClick={handleReset} disabled={resetMutation.isPending}>
            Reset to source
          </Button>
        )}
      </div>

      <div className="bg-zinc-50 rounded-lg border border-zinc-200 p-6 mb-4">
        <h2 className="text-sm font-semibold mb-3">Stages</h2>
        <PipelineFunnel
          stages={stages}
          onStageClick={setSelectedIndex}
          selectedIndex={selectedIndex ?? undefined}
        />
        <div className="flex justify-center mt-4">
          <Button variant="outline" size="sm" onClick={addStage}>
            + Add stage
          </Button>
        </div>
      </div>

      {selectedIndex !== null && stages[selectedIndex] !== undefined && (
        <StageConfigDrawer
          stage={stages[selectedIndex]}
          onChange={(updated) => updateStage(selectedIndex, updated)}
          onClose={() => setSelectedIndex(null)}
          onDelete={stages.length > 1 ? () => deleteStage(selectedIndex) : undefined}
        />
      )}

      {pickerOpen && (
        <TemplatePickerDialog
          orgUnitId={job.org_unit_id}
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          onPickTemplate={() => {
            toast.error('Swapping template not yet implemented — delete and recreate')
            setPickerOpen(false)
          }}
          onPickStarter={() => {
            toast.error('Swapping template not yet implemented — delete and recreate')
            setPickerOpen(false)
          }}
        />
      )}
    </div>
  )
}

export default function JobPipelinePage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  const { data: job, isLoading: jobLoading } = useJob(jobId)
  const { data: pipeline, isLoading: pipelineLoading } = useJobPipeline(jobId)
  const createMutation = useCreateJobPipeline(jobId)

  const [pickerOpen, setPickerOpen] = useState(false)

  if (jobLoading || pipelineLoading || !job) {
    return <div className="text-sm text-zinc-500">Loading pipeline…</div>
  }

  if (!pipeline) {
    return (
      <div className="max-w-4xl">
        <Link href={`/jobs/${jobId}`} className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block">
          ← Back to job
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900 mb-2">No pipeline yet</h1>
        <p className="text-sm text-zinc-500 mb-6">
          Pick a template from your library, the starter pack, or build from scratch.
        </p>
        <Button onClick={() => setPickerOpen(true)}>Pick a pipeline</Button>
        {pickerOpen && (
          <TemplatePickerDialog
            orgUnitId={job.org_unit_id}
            open={pickerOpen}
            onClose={() => setPickerOpen(false)}
            onPickTemplate={(t) =>
              createMutation.mutate(
                { source: 'template', template_id: t.id },
                { onSuccess: () => setPickerOpen(false) },
              )
            }
            onPickStarter={(s) =>
              createMutation.mutate(
                { source: 'starter', starter_key: s.key },
                { onSuccess: () => setPickerOpen(false) },
              )
            }
          />
        )}
      </div>
    )
  }

  return (
    <JobPipelineEditor
      key={pipeline.id}
      job={job}
      pipeline={pipeline}
      jobId={jobId}
    />
  )
}
