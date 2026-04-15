'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { AlertCircle, Check, Loader2 } from 'lucide-react'

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
  const [isDragging, setIsDragging] = useState(false)

  // Autosave plumbing (preserved verbatim from legacy page)
  const saveTimerRef = useRef<number | null>(null)
  const stagesRef = useRef(stages)
  const editGenRef = useRef(0)

  // Selected stage + tab from URL
  const selectedStageId = searchParams.get('stage')
  const activeTab = (searchParams.get('tab') ?? 'questions') as
    | 'questions'
    | 'config'

  // Mirror the latest stages + selectedStageId into refs so the keyboard
  // handler effect below can read fresh values without re-binding the
  // listener on every keystroke. Without this, depending on `stages` in
  // the keyboard effect tears down + re-adds the document listener every
  // single edit during pipeline editing.
  const selectedStageIdRef = useRef(selectedStageId)

  useEffect(() => {
    stagesRef.current = stages
    selectedStageIdRef.current = selectedStageId
  })

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

  // Combined write: set stage and tab in a single router.replace. Used by
  // the auto-select effect below to avoid racing two separate useCallback
  // helpers on the same searchParams snapshot.
  const selectStageAndTab = useCallback(
    (stageId: string, tab: 'questions' | 'config') => {
      const params = new URLSearchParams(searchParams.toString())
      params.set('stage', stageId)
      if (tab === 'questions') {
        params.delete('tab')
      } else {
        params.set('tab', tab)
      }
      router.replace(`/jobs/${jobId}/pipeline?${params.toString()}`, {
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

  // Auto-select first un-confirmed stage on initial mount if no ?stage param.
  //
  // Uses the stable selectStage / selectStageAndTab useCallbacks and reads
  // stages through stagesRef so we don't need `stages` itself in the dep
  // array (which would rerun this on every keystroke). The effect is
  // guarded by `selectedStageId` — once a stage is chosen it becomes a
  // no-op until the user explicitly clears the selection.
  const stagesLen = stages.length
  useEffect(() => {
    if (selectedStageId) return
    if (stagesLen === 0) return

    const firstUnconfirmed = overview?.banks.find(
      (b) => b.status !== 'confirmed',
    )
    const targetStageId =
      firstUnconfirmed?.stage_id ?? stagesRef.current[0]?.id ?? null
    if (!targetStageId) return

    const memoryTab =
      typeof window !== 'undefined'
        ? (localStorage.getItem('pipeline-inspector-tab') as
            | 'questions'
            | 'config'
            | null)
        : null

    if (memoryTab && memoryTab !== 'questions') {
      selectStageAndTab(targetStageId, memoryTab)
    } else {
      selectStage(targetStageId)
    }
  }, [
    overview,
    stagesLen,
    selectedStageId,
    selectStage,
    selectStageAndTab,
  ])

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
          onSuccess: (updated) => {
            // Merge backend-assigned IDs into any local stages that don't
            // have one yet (newly added via "Add stage"). Match by position
            // since the backend preserves it and Phase 2C.1's stable-ID
            // guarantee means existing IDs are never renumbered. Without
            // this merge, new stages stay with id=undefined forever and
            // show "Saving…" until a full page refresh re-fetches them.
            setStages((prev) => {
              if (prev.every((s) => s.id !== undefined)) return prev
              const byPosition = new Map(
                updated.stages.map((s) => [s.position, s.id]),
              )
              return prev.map((s) =>
                s.id === undefined
                  ? { ...s, id: byPosition.get(s.position) ?? s.id }
                  : s,
              )
            })
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

  function reorderStages(nextStagesRaw: PipelineStageUpdateInput[]) {
    // Re-assign position by array index so the backend diff + sync sees the
    // new order. The drag source kept stage objects intact, so we only
    // rewrite `position`.
    const nextStages = nextStagesRaw.map((s, i) => ({ ...s, position: i }))
    setStages(nextStages)
    scheduleSave(nextStages)
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
  //
  // The handler reads stages + selectedStageId from refs (mirrored above)
  // rather than closure-captured values, so the effect dep array can stay
  // stable at [selectStage, setActiveTab]. Both of those are useCallback'd
  // against [router, searchParams, jobId], so they only change when the
  // route does — NOT on every keystroke. Depending on `stages` directly
  // here would tear down + re-attach the document listener on every edit
  // during pipeline editing, which is churny (even if not broken).
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

      const currentStages = stagesRef.current
      const currentSelectedId = selectedStageIdRef.current

      if (e.key === 'Escape' && currentSelectedId) {
        e.preventDefault()
        selectStage(null)
        return
      }

      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (currentStages.length === 0) return
        e.preventDefault()
        const currentIdx = currentSelectedId
          ? currentStages.findIndex((s) => s.id === currentSelectedId)
          : -1
        const delta = e.key === 'ArrowDown' ? 1 : -1
        const nextIdx = Math.max(
          0,
          Math.min(currentStages.length - 1, currentIdx + delta),
        )
        const nextStageId = currentStages[nextIdx]?.id
        if (nextStageId) selectStage(nextStageId)
        return
      }

      if (e.key === 'q' || e.key === 'Q') {
        if (currentSelectedId) {
          e.preventDefault()
          setActiveTab('questions')
        }
        return
      }

      if (e.key === 'c' || e.key === 'C') {
        if (currentSelectedId) {
          e.preventDefault()
          setActiveTab('config')
        }
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [selectStage, setActiveTab])

  const isSaving = isDirty || saveMutation.isPending
  const saveFailed = saveMutation.isError && !isSaving
  const confirmedCount =
    overview?.banks.filter((b) => b.status === 'confirmed').length ?? 0
  const totalBanks = overview?.banks.length ?? 0

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)] min-h-[640px]">
      {/* Pipeline meta + actions */}
      <div className="flex items-center justify-between gap-4 mb-4">
        <p className="text-sm text-zinc-500">
          Pipeline
          {pipeline.source_template_name &&
            ` · from "${pipeline.source_template_name}"`}
        </p>
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
          onReorder={reorderStages}
          onDragStateChange={setIsDragging}
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

        <StageConnectorOverlay
          selectedStageId={selectedStageId}
          hidden={isDragging}
        />
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
