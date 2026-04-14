'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { AlertCircle, Check, Loader2 } from 'lucide-react'
import Link from 'next/link'

import { Button } from '@/components/ui/button'
import { PipelineFlowColumn } from './PipelineFlowColumn'
import { StageInspectorPanel } from './StageInspectorPanel'
import { StageConnectorOverlay } from './StageConnectorOverlay'
import { TemplatePickerDialog } from './TemplatePickerDialog'
import {
  useSaveJobPipeline,
  useResetJobPipeline,
  useSwapJobPipeline,
} from '@/lib/hooks/use-save-job-pipeline'
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
import type {
  PipelineStageUpdateInput,
  JobPipelineInstance,
} from '@/lib/api/pipelines'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

function makeBlankStage(position: number): PipelineStageUpdateInput {
  return {
    id: undefined,
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

type Props = {
  job: JobPostingWithSnapshot
  pipeline: JobPipelineInstance
  jobId: string
}

const AUTOSAVE_DEBOUNCE_MS = 800

export function UnifiedPipelineView({ job, pipeline, jobId }: Props) {
  const router = useRouter()
  const searchParams = useSearchParams()

  const saveMutation = useSaveJobPipeline(jobId)
  const resetMutation = useResetJobPipeline(jobId)
  const swapMutation = useSwapJobPipeline(jobId)

  const { data: overview } = useBanksOverview(jobId)

  const [stages, setStages] = useState<PipelineStageUpdateInput[]>(() =>
    pipeline.stages.map((s) => ({ ...s })),
  )
  const [pickerOpen, setPickerOpen] = useState(false)
  const [isDirty, setIsDirty] = useState(false)

  // Autosave plumbing (preserved verbatim from legacy page)
  const saveTimerRef = useRef<number | null>(null)
  const stagesRef = useRef(stages)
  const editGenRef = useRef(0)

  useEffect(() => {
    stagesRef.current = stages
  })

  // Selected stage + tab from URL
  const selectedStageId = searchParams.get('stage')
  const activeTab = (searchParams.get('tab') ?? 'questions') as
    | 'questions'
    | 'config'

  // Stream live bank updates for the currently-selected stage
  useQuestionsStatusStream(jobId, selectedStageId)

  const selectedStage =
    stages.find((s) => s.id !== undefined && s.id === selectedStageId) ?? null
  const selectedIndex = selectedStageId
    ? stages.findIndex((s) => s.id === selectedStageId)
    : -1

  const selectStage = useCallback(
    (stageId: string | null) => {
      const params = new URLSearchParams(searchParams.toString())
      if (stageId) {
        params.set('stage', stageId)
      } else {
        params.delete('stage')
      }
      const qs = params.toString()
      router.replace(`/jobs/${jobId}/pipeline${qs ? `?${qs}` : ''}`, {
        scroll: false,
      })
    },
    [router, searchParams, jobId],
  )

  const setActiveTab = useCallback(
    (tab: 'questions' | 'config') => {
      if (typeof window !== 'undefined') {
        localStorage.setItem('pipeline-inspector-tab', tab)
      }
      const params = new URLSearchParams(searchParams.toString())
      if (tab === 'questions') {
        params.delete('tab')
      } else {
        params.set('tab', tab)
      }
      const qs = params.toString()
      router.replace(`/jobs/${jobId}/pipeline${qs ? `?${qs}` : ''}`, {
        scroll: false,
      })
    },
    [router, searchParams, jobId],
  )

  // Auto-select first un-confirmed stage on initial mount if no ?stage param
  useEffect(() => {
    if (selectedStageId) return
    if (stages.length === 0) return

    const firstUnconfirmed = overview?.banks.find(
      (b) => b.status !== 'confirmed',
    )
    const targetStageId = firstUnconfirmed?.stage_id ?? stages[0]?.id ?? null
    if (targetStageId) {
      const params = new URLSearchParams(searchParams.toString())
      params.set('stage', targetStageId)
      const memoryTab =
        typeof window !== 'undefined'
          ? (localStorage.getItem('pipeline-inspector-tab') as
              | 'questions'
              | 'config'
              | null)
          : null
      if (memoryTab && memoryTab !== 'questions') params.set('tab', memoryTab)
      router.replace(`/jobs/${jobId}/pipeline?${params.toString()}`, {
        scroll: false,
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overview, stages.length])

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
            if (gen === editGenRef.current) {
              setIsDirty(false)
            }
          },
        },
      )
    }, AUTOSAVE_DEBOUNCE_MS)
  }

  // Flush any pending debounced save on unmount
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
    const deletedId = stages[index]?.id
    setStages((prev) => {
      const next = prev
        .filter((_, i) => i !== index)
        .map((s, i) => ({ ...s, position: i }))
      scheduleSave(next)
      return next
    })
    if (deletedId === selectedStageId) {
      selectStage(null)
    }
  }

  function handleReset() {
    if (confirm('Discard your edits and reset to the source template?')) {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      setIsDirty(false)
      resetMutation.mutate(undefined, {
        onSuccess: (fresh) => {
          setStages(fresh.stages.map((s) => ({ ...s })))
          selectStage(null)
        },
      })
    }
  }

  // Keyboard shortcuts
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        return
      }

      if (e.key === 'Escape' && selectedStageId) {
        e.preventDefault()
        selectStage(null)
        return
      }

      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (stages.length === 0) return
        e.preventDefault()
        const currentIdx = selectedStageId
          ? stages.findIndex((s) => s.id === selectedStageId)
          : -1
        const delta = e.key === 'ArrowDown' ? 1 : -1
        const nextIdx = Math.max(
          0,
          Math.min(stages.length - 1, currentIdx + delta),
        )
        const nextStageId = stages[nextIdx]?.id
        if (nextStageId) selectStage(nextStageId)
        return
      }

      if (e.key === 'q' || e.key === 'Q') {
        if (selectedStageId) {
          e.preventDefault()
          setActiveTab('questions')
        }
        return
      }

      if (e.key === 'c' || e.key === 'C') {
        if (selectedStageId) {
          e.preventDefault()
          setActiveTab('config')
        }
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [selectedStageId, stages, selectStage, setActiveTab])

  const isSaving = isDirty || saveMutation.isPending
  const saveFailed = saveMutation.isError && !isSaving
  const confirmedCount =
    overview?.banks.filter((b) => b.status === 'confirmed').length ?? 0
  const totalBanks = overview?.banks.length ?? 0

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)] min-h-[640px]">
      {/* Page header */}
      <div className="mb-4">
        <Link
          href={`/jobs/${jobId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to job
        </Link>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900">
              {job.title}
            </h1>
            <p className="text-sm text-zinc-500">
              Pipeline
              {pipeline.source_template_name &&
                ` · from "${pipeline.source_template_name}"`}
            </p>
          </div>
          <div className="flex items-center gap-3 flex-shrink-0">
            <div
              className="flex items-center gap-1.5 text-xs"
              aria-live="polite"
            >
              {saveFailed ? (
                <>
                  <AlertCircle
                    className="w-3.5 h-3.5 text-red-500"
                    aria-hidden="true"
                  />
                  <span className="text-red-600">Failed to save</span>
                </>
              ) : isSaving ? (
                <>
                  <Loader2
                    className="w-3.5 h-3.5 animate-spin text-zinc-400"
                    aria-hidden="true"
                  />
                  <span className="text-zinc-500">Saving…</span>
                </>
              ) : (
                <>
                  <Check
                    className="w-3.5 h-3.5 text-emerald-500"
                    aria-hidden="true"
                  />
                  <span className="text-zinc-500">All changes saved</span>
                </>
              )}
            </div>
            {totalBanks > 0 && (
              <span
                className={`text-[10px] font-bold px-2 py-1 rounded ${
                  confirmedCount === totalBanks
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-zinc-100 text-zinc-600'
                }`}
              >
                {confirmedCount} of {totalBanks} confirmed
              </span>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPickerOpen(true)}
              disabled={swapMutation.isPending}
            >
              Swap template
            </Button>
            {pipeline.source_template_id && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleReset}
                disabled={resetMutation.isPending}
              >
                Reset to source
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Split view */}
      <div
        className="flex-1 flex gap-0 relative min-h-0 border border-zinc-200 rounded-xl overflow-hidden"
        data-pipeline-container="true"
      >
        <PipelineFlowColumn
          stages={stages}
          selectedStageId={selectedStageId}
          banks={overview?.banks ?? []}
          onStageClick={(stageId) => selectStage(stageId)}
          onStageDelete={stages.length > 1 ? deleteStage : undefined}
          onAddStage={addStage}
        />

        <StageInspectorPanel
          jobId={jobId}
          selectedStage={selectedStage}
          selectedStageIndex={selectedIndex}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          onStageChange={(updated) => {
            if (selectedIndex >= 0) {
              updateStage(selectedIndex, updated)
            }
          }}
        />

        <StageConnectorOverlay selectedStageId={selectedStageId} />
      </div>

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
                  setStages(fresh.stages.map((s) => ({ ...s })))
                  selectStage(null)
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
                  setStages(fresh.stages.map((s) => ({ ...s })))
                  selectStage(null)
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
