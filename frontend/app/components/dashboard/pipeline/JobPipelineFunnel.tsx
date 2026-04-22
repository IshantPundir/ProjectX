'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, Check, Loader2 } from 'lucide-react'

import {
  useResetJobPipeline,
  useSaveJobPipeline,
  useSwapJobPipeline,
} from '@/lib/hooks/use-save-job-pipeline'
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import type {
  JobPipelineInstance,
  PipelineStageUpdateInput,
  StageType,
  AdvanceBehavior,
  StageDifficulty,
} from '@/lib/api/pipelines'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'
import { TemplatePickerDialog } from './TemplatePickerDialog'

const AUTOSAVE_DEBOUNCE_MS = 800

/* ─── Icons ───────────────────────────────────────────────── */

function SparkIcon({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
    </svg>
  )
}

function PlusIcon({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

/* ─── Stage presentation helpers ──────────────────────────── */

const STAGE_TYPE_LABEL: Record<StageType, string> = {
  intake: 'Intake',
  phone_screen: 'Phone Screen',
  ai_screening: 'AI Screening',
  human_interview: 'Human Interview',
  debrief: 'Debrief',
  take_home: 'Take-home',
}

function stageGate(stageType: StageType, advance: AdvanceBehavior): string {
  if (advance === 'auto_advance') {
    if (stageType === 'ai_screening') return 'auto — Copilot'
    if (stageType === 'intake') return 'auto — recruiter inbox'
    return 'auto-scored'
  }
  if (stageType === 'human_interview') return 'human-led'
  if (stageType === 'debrief') return 'HM + recruiter'
  return 'manual review'
}

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
    sla_days: null,
  }
}

/* ─── Main component ──────────────────────────────────────── */

type Props = {
  job: JobPostingWithSnapshot
  pipeline: JobPipelineInstance
  jobId: string
}

export function JobPipelineFunnel({ job, pipeline, jobId }: Props) {
  const saveMutation = useSaveJobPipeline(jobId)
  const resetMutation = useResetJobPipeline(jobId)
  const swapMutation = useSwapJobPipeline(jobId)
  const { data: overview } = useBanksOverview(jobId)

  const [stages, setStages] = useState<PipelineStageUpdateInput[]>(() =>
    pipeline.stages.map((s) => ({ ...s })),
  )
  const [activeId, setActiveId] = useState<string | null>(
    pipeline.stages[0]?.id ?? null,
  )
  const [pickerOpen, setPickerOpen] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [overIdx, setOverIdx] = useState<number | null>(null)

  // Autosave plumbing
  const saveTimerRef = useRef<number | null>(null)
  const stagesRef = useRef(stages)
  const editGenRef = useRef(0)
  useEffect(() => {
    stagesRef.current = stages
  })

  const scheduleSave = useCallback(
    (nextStages: PipelineStageUpdateInput[]) => {
      editGenRef.current += 1
      const gen = editGenRef.current
      setIsDirty(true)
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current)
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null
        saveMutation.mutate(
          { stages: nextStages },
          {
            onSuccess: (updated) => {
              // Merge backend-assigned IDs into local stages without one (newly added)
              setStages((prev) => {
                if (prev.every((s) => s.id !== undefined)) return prev
                const byPosition = new Map(updated.stages.map((s) => [s.position, s.id]))
                return prev.map((s) =>
                  s.id === undefined
                    ? { ...s, id: byPosition.get(s.position) ?? s.id }
                    : s,
                )
              })
              if (gen === editGenRef.current) setIsDirty(false)
            },
          },
        )
      }, AUTOSAVE_DEBOUNCE_MS)
    },
    [saveMutation],
  )

  // Flush pending save on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
        saveMutation.mutate({ stages: stagesRef.current })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function updateStageById(id: string, patch: Partial<PipelineStageUpdateInput>) {
    setStages((prev) => {
      const next = prev.map((s) => (s.id === id ? { ...s, ...patch } : s))
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

  function deleteStage(id: string) {
    setStages((prev) => {
      const next = prev
        .filter((s) => s.id !== id)
        .map((s, i) => ({ ...s, position: i }))
      scheduleSave(next)
      return next
    })
    if (activeId === id) setActiveId(stages[0]?.id ?? null)
  }

  function onDrop() {
    if (dragIdx === null || overIdx === null || dragIdx === overIdx) {
      setDragIdx(null)
      setOverIdx(null)
      return
    }
    // Position 0 is the fixed intake/applied slot — candidates always
    // enter there, so neither dragging from it nor dropping at it is
    // allowed. The funnel UI also hides the drag handle on row 0.
    if (dragIdx === 0 || overIdx === 0) {
      setDragIdx(null)
      setOverIdx(null)
      return
    }
    const next = stages.slice()
    const [moved] = next.splice(dragIdx, 1)
    next.splice(overIdx, 0, moved)
    const reindexed = next.map((s, i) => ({ ...s, position: i }))
    setStages(reindexed)
    scheduleSave(reindexed)
    setDragIdx(null)
    setOverIdx(null)
  }

  function handleReset() {
    if (!confirm('Discard your edits and reset to the source template?')) return
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    setIsDirty(false)
    resetMutation.mutate(undefined, {
      onSuccess: (fresh) => {
        setStages(fresh.stages.map((s) => ({ ...s })))
        setActiveId(fresh.stages[0]?.id ?? null)
      },
    })
  }

  const activeStage = stages.find((s) => s.id === activeId) ?? stages[0] ?? null
  const isSaving = isDirty || saveMutation.isPending
  const saveFailed = saveMutation.isError && !isSaving

  // Banks overview for per-stage counts
  const banks = overview?.banks ?? []
  const confirmedBanks = banks.filter((b) => b.status === 'confirmed').length

  return (
    <div>
      {/* ─── Header: title + save indicator + actions ─── */}
      <div className="mb-4 flex items-end gap-4">
        <div className="flex-1">
          <div
            className="text-[11px] font-semibold uppercase"
            style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
          >
            Pipeline
          </div>
          <p className="mt-0.5 text-[13px]" style={{ color: 'var(--px-fg-3)' }}>
            Shape the path candidates take. Drag to reorder. Changes apply to
            new candidates only.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div
            className="flex items-center gap-1.5 text-[11.5px]"
            aria-live="polite"
          >
            {saveFailed ? (
              <>
                <AlertCircle
                  className="h-3.5 w-3.5"
                  style={{ color: 'var(--px-danger)' }}
                  aria-hidden="true"
                />
                <span style={{ color: 'var(--px-danger)' }}>Failed to save</span>
              </>
            ) : isSaving ? (
              <>
                <Loader2
                  className="h-3.5 w-3.5 animate-spin"
                  style={{ color: 'var(--px-fg-4)' }}
                  aria-hidden="true"
                />
                <span style={{ color: 'var(--px-fg-3)' }}>Saving…</span>
              </>
            ) : (
              <>
                <Check
                  className="h-3.5 w-3.5"
                  style={{ color: 'var(--px-ok)' }}
                  aria-hidden="true"
                />
                <span style={{ color: 'var(--px-fg-3)' }}>All changes saved</span>
              </>
            )}
          </div>

          {banks.length > 0 && (
            <span
              className="rounded-full border px-2 py-0.5 text-[10.5px] font-semibold"
              style={
                confirmedBanks === banks.length
                  ? {
                      background: 'var(--px-ok-bg)',
                      borderColor: 'var(--px-ok-line)',
                      color: 'var(--px-ok)',
                    }
                  : {
                      background: 'var(--px-surface-2)',
                      borderColor: 'var(--px-hairline)',
                      color: 'var(--px-fg-3)',
                    }
              }
            >
              {confirmedBanks} of {banks.length} banks confirmed
            </span>
          )}

          <button
            className="px-btn outline sm"
            type="button"
            onClick={() => setPickerOpen(true)}
            disabled={swapMutation.isPending}
          >
            Swap template
          </button>

          {pipeline.source_template_id && (
            <button
              className="px-btn outline sm"
              type="button"
              onClick={handleReset}
              disabled={resetMutation.isPending}
            >
              Reset to source
            </button>
          )}
        </div>
      </div>

      {/* ─── Funnel visualization ─── */}
      <Funnel
        stages={stages}
        activeId={activeId}
        onPick={setActiveId}
        onDelete={deleteStage}
        onAddStage={addStage}
        dragIdx={dragIdx}
        overIdx={overIdx}
        onDragStart={(i) => setDragIdx(i)}
        onDragOver={(i) => setOverIdx(i)}
        onDrop={onDrop}
        banks={banks}
      />

      {/* ─── Stage detail + interviewer slots ─── */}
      {activeStage && (
        <div
          className="mt-5 grid gap-4"
          style={{ gridTemplateColumns: '1.1fr 1fr' }}
        >
          <StageDetailEditor
            key={activeStage.id ?? `pos-${activeStage.position}`}
            stage={activeStage}
            jobId={jobId}
            onChange={(patch) => {
              if (activeStage.id) updateStageById(activeStage.id, patch)
            }}
          />
          <InterviewersPanel stage={activeStage} />
        </div>
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
                  setStages(fresh.stages.map((s) => ({ ...s })))
                  setActiveId(fresh.stages[0]?.id ?? null)
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
                  setActiveId(fresh.stages[0]?.id ?? null)
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

/* ─── Funnel visualization ───────────────────────────────── */

type BankLite = { stage_id: string; question_count: number; status: string }

function Funnel({
  stages,
  activeId,
  onPick,
  onDelete,
  onAddStage,
  dragIdx,
  overIdx,
  onDragStart,
  onDragOver,
  onDrop,
  banks,
}: {
  stages: PipelineStageUpdateInput[]
  activeId: string | null
  onPick: (id: string) => void
  onDelete: (id: string) => void
  onAddStage: () => void
  dragIdx: number | null
  overIdx: number | null
  onDragStart: (i: number) => void
  onDragOver: (i: number) => void
  onDrop: () => void
  banks: BankLite[]
}) {
  const banksByStage = new Map(banks.map((b) => [b.stage_id, b]))
  // Synthetic count so the funnel has visual weight even without real
  // candidate stats — each stage gets a decaying count based on position.
  const syntheticCount = (i: number, total: number) => {
    const base = 120
    const ratio = (total - i) / total
    return Math.max(3, Math.round(base * Math.pow(ratio, 0.9)))
  }
  const maxCount = Math.max(...stages.map((_, i) => syntheticCount(i, stages.length)))

  const widthFor = (c: number) => 26 + Math.sqrt(c / maxCount) * 74

  return (
    <div
      className="rounded-[12px] border px-5 pb-4 pt-5"
      style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
    >
      <div className="mb-3 flex items-center justify-between px-1">
        <div
          className="text-[11px] font-semibold uppercase"
          style={{ letterSpacing: '0.4px', color: 'var(--px-fg-4)' }}
        >
          Funnel — this role
        </div>
        <div
          className="flex gap-2.5 text-[11px]"
          style={{ color: 'var(--px-fg-4)' }}
        >
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: 'var(--px-accent)' }}
              aria-hidden="true"
            />
            active candidate
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-1.5 w-1.5 rounded-full opacity-50"
              style={{ background: 'var(--px-fg-4)' }}
              aria-hidden="true"
            />
            stalled &gt; SLA
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        {stages.map((s, i) => {
          const count = syntheticCount(i, stages.length)
          const w = widthFor(count)
          const prev = i > 0 ? syntheticCount(i - 1, stages.length) : count
          const dropoff = i > 0 ? Math.round((1 - count / prev) * 100) : 0
          const isActive = activeId === s.id
          const isDragging = dragIdx === i
          const isDragOver = overIdx === i && dragIdx !== null && dragIdx !== i
          const bank = s.id ? banksByStage.get(s.id) : null
          // The first stage is the fixed intake slot — it represents every
          // candidate's "applied" state and cannot be reordered or removed.
          const isIntake = i === 0

          return (
            <div
              key={s.id ?? `pos-${s.position}`}
              onDragOver={(e) => {
                e.preventDefault()
                // Never accept a drop on the intake slot.
                if (!isIntake) onDragOver(i)
              }}
              onDrop={onDrop}
              className="relative transition-all"
              style={{
                paddingTop: isDragOver ? 6 : 0,
                borderTop: isDragOver ? '2px solid var(--px-accent)' : '2px solid transparent',
                background: isIntake ? 'var(--px-accent-tint)' : 'transparent',
                borderRadius: isIntake ? 8 : 0,
                padding: isIntake ? '4px 6px' : undefined,
                marginLeft: isIntake ? -6 : 0,
                marginRight: isIntake ? -6 : 0,
              }}
            >
              <div
                className="grid items-center gap-3.5"
                style={{
                  gridTemplateColumns: '150px 1fr 170px',
                  opacity: isDragging ? 0.4 : 1,
                }}
              >
                {/* Left: drag handle (hidden for intake) + label */}
                <div
                  draggable={!isIntake}
                  onDragStart={() => !isIntake && onDragStart(i)}
                  className={`flex items-center gap-2 py-1 ${isIntake ? '' : 'cursor-grab'}`}
                >
                  {isIntake ? (
                    <span
                      aria-hidden="true"
                      title="Intake is fixed"
                      className="inline-flex h-3 w-3 items-center justify-center"
                      style={{ color: 'var(--px-accent)' }}
                    >
                      <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
                        <path d="M5 11h14v10H5z" />
                        <path d="M8 11V7a4 4 0 118 0v4" />
                      </svg>
                    </span>
                  ) : (
                    <span className="flex h-3 w-3 flex-col justify-between" aria-hidden="true">
                      {[0, 1, 2].map((k) => (
                        <span
                          key={k}
                          className="h-0.5 rounded-sm"
                          style={{ background: 'var(--px-fg-4)' }}
                        />
                      ))}
                    </span>
                  )}
                  <div className="min-w-0">
                    <div
                      className="px-mono flex items-center gap-1.5 truncate text-[9.5px] uppercase"
                      style={{
                        letterSpacing: '0.4px',
                        color: isIntake ? 'var(--px-accent)' : 'var(--px-fg-4)',
                      }}
                    >
                      <span>
                        0{i + 1} · {STAGE_TYPE_LABEL[s.stage_type]}
                      </span>
                      {isIntake && (
                        <span
                          className="rounded-sm px-1 py-px text-[8.5px] font-bold"
                          style={{
                            background: 'var(--px-accent)',
                            color: '#fff',
                            letterSpacing: '0.4px',
                          }}
                        >
                          FIXED
                        </span>
                      )}
                    </div>
                    <div
                      className="truncate text-[12px] font-medium"
                      style={{ color: 'var(--px-fg)' }}
                    >
                      {s.name}
                    </div>
                  </div>
                </div>

                {/* Middle: funnel bar with droplets */}
                <button
                  type="button"
                  onClick={() => s.id && onPick(s.id)}
                  className="relative h-12 cursor-pointer border-none bg-transparent"
                  aria-label={`${s.name} stage — ${count} candidates`}
                >
                  <div
                    className="absolute inset-y-1 left-1/2 -translate-x-1/2 overflow-hidden rounded-md transition-all"
                    style={{
                      width: `${w}%`,
                      background: isActive
                        ? 'var(--px-accent-tint)'
                        : 'rgba(14, 111, 99, 0.06)',
                      border: `1px solid ${
                        isActive ? 'var(--px-accent)' : 'var(--px-accent-line)'
                      }`,
                    }}
                  >
                    <Droplets count={count} max={maxCount} stalled={i === 2 ? 6 : i === 4 ? 3 : 0} />
                  </div>
                  {i > 0 && dropoff > 0 && (
                    <div
                      className="px-mono absolute -top-2 left-3 text-[10px]"
                      style={{ color: 'var(--px-fg-4)' }}
                    >
                      −{dropoff}%
                    </div>
                  )}
                </button>

                {/* Right: count + duration + auto badge */}
                <div className="flex items-center justify-end gap-2.5">
                  <div className="text-right">
                    <div
                      className="px-mono text-[20px] font-medium leading-none"
                      style={{
                        color: 'var(--px-fg)',
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {count}
                    </div>
                    <div
                      className="mt-0.5 text-[10.5px]"
                      style={{ color: 'var(--px-fg-4)' }}
                    >
                      {s.duration_minutes}m avg
                    </div>
                  </div>
                  {s.advance_behavior === 'auto_advance' && (
                    <span
                      title="Auto-advance"
                      className="inline-flex h-5 w-5 items-center justify-center rounded-full"
                      style={{
                        background: 'var(--px-accent-tint)',
                        color: 'var(--px-accent)',
                      }}
                    >
                      <SparkIcon size={10} />
                    </span>
                  )}
                  {bank && (
                    <span
                      className="px-mono rounded border px-1.5 py-0.5 text-[9.5px] font-medium"
                      style={{
                        background:
                          bank.status === 'confirmed'
                            ? 'var(--px-ok-bg)'
                            : 'var(--px-surface-2)',
                        borderColor:
                          bank.status === 'confirmed'
                            ? 'var(--px-ok-line)'
                            : 'var(--px-hairline)',
                        color:
                          bank.status === 'confirmed'
                            ? 'var(--px-ok)'
                            : 'var(--px-fg-3)',
                      }}
                    >
                      {bank.question_count}Q
                    </span>
                  )}
                  {!isIntake && stages.length > 1 && s.id && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        if (confirm(`Delete stage "${s.name}"?`)) onDelete(s.id!)
                      }}
                      aria-label={`Delete ${s.name}`}
                      className="cursor-pointer border-none bg-transparent text-[11px]"
                      style={{ color: 'var(--px-fg-4)' }}
                    >
                      ×
                    </button>
                  )}
                </div>
              </div>
            </div>
          )
        })}

        {/* Add stage */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            onDragOver(stages.length)
          }}
          onDrop={onDrop}
          className="mt-1"
        >
          <button
            type="button"
            onClick={onAddStage}
            className="px-btn ghost xs"
            style={{ height: 28 }}
          >
            <PlusIcon size={10} />
            Add stage
          </button>
        </div>
      </div>
    </div>
  )
}

/* ─── Droplets rendered inside each funnel bar ─────────── */

function Droplets({
  count,
  max,
  stalled = 0,
}: {
  count: number
  max: number
  stalled?: number
}) {
  const show = Math.min(60, Math.max(3, Math.round((count / max) * 60)))
  const stalledShow = Math.min(stalled, show)
  return (
    <div
      className="absolute inset-1.5 flex flex-wrap content-center gap-[3px]"
      aria-hidden="true"
    >
      {Array.from({ length: show }).map((_, i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background:
              i < stalledShow
                ? 'rgba(154, 147, 136, 0.55)'
                : 'var(--px-accent)',
            opacity: i < stalledShow ? 0.6 : 0.85,
          }}
        />
      ))}
    </div>
  )
}

/* ─── Stage detail editor (under funnel) ─────────────────── */

function StageDetailEditor({
  stage,
  jobId,
  onChange,
}: {
  stage: PipelineStageUpdateInput
  jobId: string
  onChange: (patch: Partial<PipelineStageUpdateInput>) => void
}) {
  const difficulties: StageDifficulty[] = ['easy', 'medium', 'hard']
  const advanceOptions: { k: AdvanceBehavior; label: string }[] = [
    { k: 'auto_advance', label: 'Auto-advance' },
    { k: 'manual_review', label: 'Manual review' },
  ]
  const stageTypeOptions: { value: StageType; label: string; disabled?: boolean }[] = [
    { value: 'intake',          label: 'Intake' },
    { value: 'phone_screen',    label: 'Phone Screen' },
    { value: 'ai_screening',    label: 'AI Screening' },
    { value: 'human_interview', label: 'Human Interview' },
    { value: 'debrief',         label: 'Debrief' },
    { value: 'take_home',       label: 'Take-home (Coming soon)', disabled: true },
  ]

  return (
    <div
      className="rounded-[10px] border p-5"
      style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
    >
      <div className="mb-4 flex items-center gap-2">
        <h3
          className="px-eyebrow"
          style={{ margin: 0 }}
        >
          Stage
        </h3>
        <span
          className="px-serif text-[20px] font-normal"
          style={{ color: 'var(--px-fg)' }}
        >
          {stage.name}
        </span>
        <span
          className="px-mono rounded px-1.5 py-0.5 text-[10px]"
          style={{
            background: 'var(--px-bg-2)',
            color: 'var(--px-fg-4)',
          }}
        >
          {STAGE_TYPE_LABEL[stage.stage_type]}
        </span>
      </div>

      <div className="mb-3.5 grid grid-cols-[1fr_1.2fr] gap-3">
        <div>
          <label className="px-label">Stage type</label>
          <select
            className="px-input"
            value={stage.stage_type}
            onChange={(e) =>
              onChange({ stage_type: e.target.value as StageType })
            }
          >
            {stageTypeOptions.map((o) => (
              <option key={o.value} value={o.value} disabled={o.disabled}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="px-label">Stage name</label>
          <input
            className="px-input"
            value={stage.name}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </div>
      </div>

      <div className="mb-3.5 grid grid-cols-2 gap-3">
        <div>
          <label className="px-label">Duration (min)</label>
          <input
            className="px-input sm"
            type="number"
            min={1}
            value={stage.duration_minutes}
            onChange={(e) =>
              onChange({ duration_minutes: Math.max(1, Number(e.target.value) || 1) })
            }
          />
        </div>
        <div>
          <label className="px-label">Difficulty</label>
          <select
            className="px-input sm"
            value={stage.difficulty}
            onChange={(e) =>
              onChange({ difficulty: e.target.value as StageDifficulty })
            }
          >
            {difficulties.map((d) => (
              <option key={d} value={d}>
                {d.charAt(0).toUpperCase() + d.slice(1)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mb-3.5">
        <label className="px-label">Gate — who can advance</label>
        <div className="flex flex-wrap gap-1.5">
          {advanceOptions.map((opt) => {
            const active = stage.advance_behavior === opt.k
            return (
              <button
                key={opt.k}
                type="button"
                className={`px-chip ${active ? 'active' : ''}`}
                style={{ fontSize: 11 }}
                onClick={() => onChange({ advance_behavior: opt.k })}
              >
                {opt.label}
              </button>
            )
          })}
        </div>
        <p className="px-hint">
          Currently: {stageGate(stage.stage_type, stage.advance_behavior)}.
        </p>
      </div>

      <div className="mb-3.5">
        <label className="px-label">SLA — nudge if stalled beyond</label>
        <div className="flex items-center gap-2.5">
          <input
            className="px-input sm"
            style={{ width: 72 }}
            type="number"
            min={0}
            value={stage.sla_days ?? ''}
            onChange={(e) => {
              const v = e.target.value === '' ? null : Number(e.target.value)
              onChange({ sla_days: v })
            }}
          />
          <span className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
            days
          </span>
          <div className="flex-1" />
          <span className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
            jobId {jobId.slice(0, 6)}…
          </span>
        </div>
      </div>
    </div>
  )
}

/* ─── Interviewer slots panel (decorative/placeholder — backend wiring not in Phase 2C.1) ─── */

function InterviewersPanel({ stage }: { stage: PipelineStageUpdateInput }) {
  const lead = stage.stage_type === 'human_interview'
  const slots = lead
    ? [
        { who: 'Assign an interviewer', slot: 'Systems Design', lead: true },
        { who: 'Assign an interviewer', slot: 'Coding' },
        { who: 'Assign an interviewer', slot: 'Values' },
      ]
    : [{ who: '—', slot: stage.stage_type.replace('_', ' ') }]

  return (
    <div
      className="rounded-[10px] border p-5"
      style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
    >
      <div className="mb-4 flex items-center">
        <h3 className="px-eyebrow" style={{ margin: 0 }}>
          Interviewers
        </h3>
        <div className="flex-1" />
        <span className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
          {slots.length} slots
        </span>
      </div>

      <div className="flex flex-col gap-1.5">
        {slots.map((r, i) => (
          <div
            key={i}
            className="flex items-center gap-2.5 rounded-md border px-3 py-2"
            style={{
              background: 'var(--px-bg-2)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <div
              className="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-semibold"
              style={{
                background: 'var(--px-accent-tint)',
                color: 'var(--px-accent)',
              }}
            >
              {r.who === '—'
                ? '—'
                : r.who
                    .split(' ')
                    .slice(0, 2)
                    .map((n) => n[0]?.toUpperCase() ?? '')
                    .join('')}
            </div>
            <div className="flex-1">
              <div
                className="text-[12.5px] font-medium"
                style={{ color: 'var(--px-fg)' }}
              >
                {r.who}
              </div>
              <div className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
                {r.slot}
                {r.lead && ' · lead'}
              </div>
            </div>
          </div>
        ))}
      </div>

      <button type="button" className="px-btn ghost xs mt-2.5">
        <PlusIcon size={10} /> Add interviewer
      </button>

      <p
        className="mt-3 text-[10.5px]"
        style={{ color: 'var(--px-fg-4)', fontStyle: 'italic' }}
      >
        Interviewer rostering ships with Phase 3 session scheduling.
      </p>
    </div>
  )
}
