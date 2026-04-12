'use client'

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { AlertCircle, Check, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import { StageConfigDrawer } from '@/components/dashboard/pipeline/StageConfigDrawer'
import { TemplatePickerDialog } from '@/components/dashboard/pipeline/TemplatePickerDialog'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useCreateJobPipeline } from '@/lib/hooks/use-create-job-pipeline'
import { useSaveJobPipeline, useResetJobPipeline, useSwapJobPipeline } from '@/lib/hooks/use-save-job-pipeline'
import type {
  PipelineStageUpdateInput,
  JobPipelineInstance,
} from '@/lib/api/pipelines'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

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

type EditorProps = {
  job: JobPostingWithSnapshot
  pipeline: JobPipelineInstance
  jobId: string
}

const AUTOSAVE_DEBOUNCE_MS = 800

function JobPipelineEditor({ job, pipeline, jobId }: EditorProps) {
  const saveMutation = useSaveJobPipeline(jobId)
  const resetMutation = useResetJobPipeline(jobId)
  const swapMutation = useSwapJobPipeline(jobId)

  const [stages, setStages] = useState<PipelineStageUpdateInput[]>(() =>
    pipeline.stages.map((s) => ({ ...s, id: s.id })),
  )
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [isDirty, setIsDirty] = useState(false)

  // Auto-save plumbing. stagesRef keeps the latest stages for the unmount flush;
  // editGenRef prevents a late onSuccess from clearing isDirty if the user
  // edited again while the mutation was in flight.
  const saveTimerRef = useRef<number | null>(null)
  const stagesRef = useRef(stages)
  const editGenRef = useRef(0)

  useEffect(() => {
    stagesRef.current = stages
  })

  function scheduleSave(nextStages: PipelineStageUpdateInput[]) {
    editGenRef.current += 1
    const gen = editGenRef.current
    setIsDirty(true)
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null
      saveMutation.mutate(
        { stages: nextStages },
        {
          onSuccess: () => {
            // Only mark clean if no newer edit happened while this save was in flight
            if (gen === editGenRef.current) {
              setIsDirty(false)
            }
          },
        },
      )
    }, AUTOSAVE_DEBOUNCE_MS)
  }

  // Flush any pending debounced save on unmount so last-second edits aren't lost
  useEffect(() => {
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
        saveMutation.mutate({ stages: stagesRef.current })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function updateStage(index: number, updated: PipelineStageUpdateInput) {
    setStages((prev) => {
      const next = prev.map((s, i) => (i === index ? updated : s))
      scheduleSave(next)
      return next
    })
  }
  function addStage() {
    setStages((prev) => {
      const next = [...prev, makeBlankStage(prev.length)]
      scheduleSave(next)
      return next
    })
  }
  function deleteStage(index: number) {
    setStages((prev) => {
      const next = prev.filter((_, i) => i !== index).map((s, i) => ({ ...s, position: i }))
      scheduleSave(next)
      return next
    })
    setSelectedIndex(null)
  }
  function handleReset() {
    if (confirm('Discard your edits and reset to the source template?')) {
      // Pending local edits are about to be replaced — drop the debounced save
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      setIsDirty(false)
      resetMutation.mutate(undefined, {
        onSuccess: (fresh) => {
          setStages(fresh.stages.map((s) => ({ ...s, id: s.id })))
          setSelectedIndex(null)
        },
      })
    }
  }

  const isSaving = isDirty || saveMutation.isPending
  const saveFailed = saveMutation.isError && !isSaving

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

      <div className="flex flex-wrap items-center gap-3 mb-6">
        {/* Auto-save status indicator — replaces the former Save button.
            Changes are persisted automatically 800ms after the last edit. */}
        <div
          className="flex items-center gap-1.5 text-xs"
          aria-live="polite"
        >
          {saveFailed ? (
            <>
              <AlertCircle className="w-3.5 h-3.5 text-red-500" aria-hidden="true" />
              <span className="text-red-600">Failed to save</span>
            </>
          ) : isSaving ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-400" aria-hidden="true" />
              <span className="text-zinc-500">Saving…</span>
            </>
          ) : (
            <>
              <Check className="w-3.5 h-3.5 text-emerald-500" aria-hidden="true" />
              <span className="text-zinc-500">All changes saved</span>
            </>
          )}
        </div>
        <div className="flex-1" />
        <Button variant="outline" onClick={() => setPickerOpen(true)} disabled={swapMutation.isPending}>
          Swap template
        </Button>
        {pipeline.source_template_id && (
          <Button variant="outline" onClick={handleReset} disabled={resetMutation.isPending}>
            Reset to source
          </Button>
        )}
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

      {selectedIndex !== null && stages[selectedIndex] !== undefined && (
        <StageConfigDrawer
          stage={stages[selectedIndex]}
          onChange={(updated) => updateStage(selectedIndex, updated)}
          onClose={() => setSelectedIndex(null)}
        />
      )}

      {pickerOpen && (
        <TemplatePickerDialog
          orgUnitId={job.org_unit_id}
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          onPickTemplate={(t) =>
            swapMutation.mutate(
              { source: 'template', template_id: t.id },
              {
                onSuccess: (fresh) => {
                  setStages(fresh.stages.map((s) => ({ ...s, id: s.id })))
                  setSelectedIndex(null)
                  setPickerOpen(false)
                },
              },
            )
          }
          onPickStarter={(s) =>
            swapMutation.mutate(
              { source: 'starter', starter_key: s.key },
              {
                onSuccess: (fresh) => {
                  setStages(fresh.stages.map((s) => ({ ...s, id: s.id })))
                  setSelectedIndex(null)
                  setPickerOpen(false)
                },
              },
            )
          }
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

  if (!job.can_manage || job.status !== 'signals_confirmed') {
    return (
      <div className="max-w-4xl">
        <Link
          href={`/jobs/${jobId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to job
        </Link>
        <p className="text-sm text-zinc-500 mt-4">
          This pipeline is not available for editing.
        </p>
      </div>
    )
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
        <Button onClick={() => setPickerOpen(true)} disabled={createMutation.isPending}>Pick a pipeline</Button>
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
