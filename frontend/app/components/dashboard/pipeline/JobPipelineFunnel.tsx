'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, Check, Loader2 } from 'lucide-react'

import { toast } from 'sonner'

import { DangerConfirmDialog } from '@/components/px'
import {
  useResetJobPipeline,
  useSaveAsTemplate,
  useSaveJobPipeline,
  useSwapJobPipeline,
  useUpdateSourceTemplate,
} from '@/lib/hooks/use-save-job-pipeline'
import { useActivateJob } from '@/lib/hooks/use-activate-job'
import { usePipelineClassify } from '@/lib/hooks/use-pipeline-classify'
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import type {
  JobPipelineInstance,
  PipelineStageUpdateInput,
  StageParticipantInput,
  StageParticipantResponse,
  StageType,
  AdvanceBehavior,
  StageDifficulty,
} from '@/lib/api/pipelines'
import { useAssignableUsers } from '@/lib/hooks/use-assignable-users'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'
import { TemplatePickerDialog } from './TemplatePickerDialog'
import { StageParticipantsEditor } from './StageParticipantsEditor'
import { DifficultySlider } from './DifficultySlider'
import { participantSlotsFor } from '@/lib/pipelines/categories'
import { useGenerateAllQuestions } from '@/lib/hooks/use-generate-questions'
import { SourcePill } from './SourcePill'
import { ActivationGate } from './ActivationGate'
import { EditCategoryWarningModal } from './EditCategoryWarningModal'
import { computeActivationFailures } from '@/lib/pipelines/activation'

const AUTOSAVE_DEBOUNCE_MS = 800

/* ─── Icons ───────────────────────────────────────────────── */

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

function CloseIcon({ size = 12 }: { size?: number }) {
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
      <path d="M18 6L6 18M6 6l12 12" />
    </svg>
  )
}

function EditIcon({ size = 11 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  )
}

function UsersIcon({ size = 11 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a4 4 0 010 7.75" />
    </svg>
  )
}

/* ─── Stage-type visual meta ─────────────────────────────── */

const STAGE_TYPE_LABEL: Record<StageType, string> = {
  intake: 'Intake',
  phone_screen: 'Phone Screen',
  ai_screening: 'AI Screening',
  human_interview: 'Human Interview',
  debrief: 'Debrief',
  take_home: 'Take-home',
}

type StagePalette = { fill: string; edge: string; label: string }

// Palette flows teal → ochre, keyed by stage type so each type is
// visually consistent across roles and orgs. Order roughly mirrors
// a candidate's journey: pale intake → saturated evaluation → warm
// human decision → sage offer.
const STAGE_TYPE_PALETTE: Record<StageType, StagePalette> = {
  intake:          { fill: '#E3EFEC', edge: '#9EC4BD', label: '#0A564D' },
  phone_screen:    { fill: '#CFE4DF', edge: '#7FB0A7', label: '#0A564D' },
  take_home:       { fill: '#B6D4CD', edge: '#5C9D92', label: '#0A4A42' },
  ai_screening:    { fill: '#98C2BA', edge: '#3E877B', label: '#083C35' },
  human_interview: { fill: '#E8D4B0', edge: '#C29A5E', label: '#6B4918' },
  debrief:         { fill: '#E3BF91', edge: '#B5844C', label: '#5A3810' },
}

function paletteFor(type: StageType): StagePalette {
  return STAGE_TYPE_PALETTE[type]
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

// TODO(Task 25): replace loose-stage helpers with per-category constructors once
// the matrix-driven funnel refactor lands.
type LooseStageInput = { [K in string]: unknown } & { id?: string; position: number; name: string; stage_type: StageType }

function makeBlankStage(
  position: number,
  overrides: Partial<LooseStageInput> = {},
): PipelineStageUpdateInput {
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
    ...overrides,
  } as PipelineStageUpdateInput
}

// A pipeline MUST start with Intake and end with Debrief — these are the
// fixed entry and exit stages of every role. If the incoming pipeline is
// missing either, insert a default one. Re-indexes positions after.
//
// Participants are passed through as-is: the server returns
// StageParticipantResponse (with full_name/email) which the UI needs for
// rendering pills. Stripping those extras happens at the API boundary via
// `stripStagesForApi` below — not here.
function normalizeStages(
  stages: PipelineStageUpdateInput[],
): PipelineStageUpdateInput[] {
  const next: PipelineStageUpdateInput[] = stages.map((s) => ({ ...s }))

  if (next[0]?.stage_type !== 'intake') {
    next.unshift(
      makeBlankStage(0, {
        name: 'Intake',
        stage_type: 'intake',
        // Backend requires duration_minutes > 0; intake is auto-advance
        // so the value is cosmetic — 1 minute is the smallest legal value.
        duration_minutes: 1,
        advance_behavior: 'auto_advance',
      }),
    )
  }

  if (next[next.length - 1]?.stage_type !== 'debrief') {
    next.push(
      makeBlankStage(next.length, {
        name: 'Debrief',
        stage_type: 'debrief',
        duration_minutes: 30,
        advance_behavior: 'manual_review',
      }),
    )
  }

  return next.map((s, i) => ({ ...s, position: i } as PipelineStageUpdateInput))
}

// Strip any extra display fields from participants (full_name, email) before
// sending to the API. State keeps them for rendering; the backend rejects
// anything beyond {user_id, role} due to `extra="forbid"` on the input schema.
function stripStagesForApi(
  stages: PipelineStageUpdateInput[],
): PipelineStageUpdateInput[] {
  return stages.map((s) => ({
    ...s,
    participants: s.participants?.map((p) => ({
      user_id: p.user_id,
      role: p.role,
    })),
  } as PipelineStageUpdateInput))
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
  const saveAsTemplateMutation = useSaveAsTemplate(jobId)
  const updateSourceMutation = useUpdateSourceTemplate(jobId)
  const activateMutation = useActivateJob(jobId)
  const classifyMutation = usePipelineClassify(jobId)
  const { data: overview } = useBanksOverview(jobId)

  const [stages, setStages] = useState<PipelineStageUpdateInput[]>(() =>
    normalizeStages(pipeline.stages),
  )
  // Sheet is hidden by default — no stage is selected on mount. Users
  // click (or right-click) a slice to open the config panel.
  const [activeId, setActiveId] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [overIdx, setOverIdx] = useState<number | null>(null)
  const [confirmingReset, setConfirmingReset] = useState(false)
  // Edit-category warning modal state
  const [warningModal, setWarningModal] = useState<{
    category: 'B' | 'C'
    pendingStages: PipelineStageUpdateInput[]
    inFlight: Record<string, number>
    editGen: number
  } | null>(null)

  // Autosave plumbing
  const saveTimerRef = useRef<number | null>(null)
  const stagesRef = useRef(stages)
  const editGenRef = useRef(0)
  useEffect(() => {
    stagesRef.current = stages
  })

  // Direct save — bypasses classify gate. Called after user confirms a B/C warning,
  // or when the classify result is category A.
  const doSave = useCallback(
    (nextStages: PipelineStageUpdateInput[], gen: number) => {
      saveMutation.mutate(
        { stages: stripStagesForApi(nextStages) },
        {
          onSuccess: (updated) => {
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
    },
    [saveMutation],
  )

  const scheduleSave = useCallback(
    (nextStages: PipelineStageUpdateInput[]) => {
      editGenRef.current += 1
      const gen = editGenRef.current
      setIsDirty(true)
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current)
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null
        void (async () => {
          try {
            const result = await classifyMutation.mutateAsync({
              stages: stripStagesForApi(nextStages),
            })
            // Category D: stage-type change blocked on active jobs
            if (job.status === 'active' && result.category === 'D') {
              toast.error("Stage type can't be changed once the job is active.")
              if (gen === editGenRef.current) setIsDirty(false)
              return
            }
            // Category B or C: show warning modal — save is deferred until confirmed
            if (result.category === 'B' || result.category === 'C') {
              setWarningModal({
                category: result.category,
                pendingStages: nextStages,
                inFlight: result.in_flight,
                editGen: gen,
              })
              // isDirty stays true until confirmed or cancelled
              return
            }
            // Category A (or D on inactive job): proceed directly
            doSave(nextStages, gen)
          } catch {
            // Classify failed — fall back to direct save so edits are not lost
            doSave(nextStages, gen)
          }
        })()
      }, AUTOSAVE_DEBOUNCE_MS)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [saveMutation, classifyMutation, doSave, job.status],
  )

  // Flush pending save on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
        saveMutation.mutate({ stages: stripStagesForApi(stagesRef.current) })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // If normalizeStages added an intake/debrief that wasn't on the server,
  // persist that now so the backend stops returning a broken pipeline on
  // every subsequent load.
  useEffect(() => {
    if (stages.length !== pipeline.stages.length) {
      scheduleSave(stages)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function updateStageById(id: string, patch: Partial<LooseStageInput>) {
    setStages((prev) => {
      const next = prev.map((s) =>
        s.id === id ? ({ ...s, ...patch } as PipelineStageUpdateInput) : s,
      )
      scheduleSave(next)
      return next
    })
  }

  function addStageAt(insertAt: number) {
    setStages((prev) => {
      // Clamp so new stages can never land at position 0 (before Intake)
      // or position length (after Debrief) — both bookends stay fixed.
      const clamped = Math.max(1, Math.min(prev.length - 1, insertAt))
      const inserted = makeBlankStage(clamped)
      const next = [
        ...prev.slice(0, clamped),
        inserted,
        ...prev.slice(clamped),
      ].map((s, i) => ({ ...s, position: i }))
      scheduleSave(next)
      return next
    })
  }

  function addStage() {
    addStageAt(stages.length - 1)
  }

  function deleteStage(id: string) {
    setStages((prev) => {
      const idx = prev.findIndex((s) => s.id === id)
      // Refuse to remove the fixed Intake / Debrief bookends.
      if (idx <= 0 || idx >= prev.length - 1) return prev
      const next = prev
        .filter((s) => s.id !== id)
        .map((s, i) => ({ ...s, position: i }))
      scheduleSave(next)
      return next
    })
    if (activeId === id) setActiveId(null)
  }

  function onDrop() {
    if (dragIdx === null || overIdx === null || dragIdx === overIdx) {
      setDragIdx(null)
      setOverIdx(null)
      return
    }
    // Intake (first) and Debrief (last) are fixed bookends — candidates
    // always enter at Intake and exit at Debrief, so neither slot accepts
    // dragging from or dropping onto it.
    const lastIdx = stages.length - 1
    if (
      dragIdx === 0 ||
      overIdx === 0 ||
      dragIdx === lastIdx ||
      overIdx === lastIdx
    ) {
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
    setConfirmingReset(true)
  }

  async function confirmReset() {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    setIsDirty(false)
    try {
      const fresh = await resetMutation.mutateAsync()
      const normalized = normalizeStages(fresh.stages)
      setStages(normalized)
      setActiveId(null)
      if (normalized.length !== fresh.stages.length) scheduleSave(normalized)
      setConfirmingReset(false)
    } catch {
      // hook surfaces error via toast; keep dialog open
    }
  }

  const activeStage =
    activeId !== null ? stages.find((s) => s.id === activeId) ?? null : null
  const activeIdx = activeStage
    ? stages.findIndex((s) => s.id === activeStage.id)
    : -1
  const isSaving = isDirty || saveMutation.isPending
  const saveFailed = saveMutation.isError && !isSaving

  const banks = overview?.banks ?? []
  const confirmedBanks = banks.filter((b) => b.status === 'confirmed').length
  const banksByStage = new Map(banks.map((b) => [b.stage_id, b]))

  const generateAllMutation = useGenerateAllQuestions(jobId)

  return (
    <div>
      {/* ─── Header ─── */}
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
            className="px-btn sm"
            type="button"
            onClick={() => generateAllMutation.mutate()}
            disabled={generateAllMutation.isPending || isSaving}
            title="Generate question banks for every eligible stage"
          >
            {generateAllMutation.isPending ? 'Generating…' : 'Generate all questions'}
          </button>
        </div>
      </div>

      {/* ─── Source pill + ActivationGate ─── */}
      <div className="mb-4 space-y-3">
        <SourcePill
          sourceTemplateId={pipeline.source_template_id}
          sourceTemplateName={pipeline.source_template_name}
          sourceStarterKey={null}
          diverged={pipeline.pipeline_version > 1}
          canSwap={job.status !== 'active'}
          canUpdateSource={pipeline.source_template_id !== null && job.status !== 'active'}
          onReset={handleReset}
          onSwap={() => setPickerOpen(true)}
          onSaveAsTemplate={() =>
            saveAsTemplateMutation.mutate({
              name: `${job.title} pipeline`,
              description: null,
              is_default: false,
            })
          }
          onUpdateSourceTemplate={() => updateSourceMutation.mutate()}
        />
        <ActivationGate
          failures={computeActivationFailures(pipeline, banks)}
          onActivate={() => activateMutation.mutate()}
          onFocusStage={(stageId) => setActiveId(stageId)}
        />
      </div>

      {/* ─── Hero scene: SVG funnel + floating config sheet ─── */}
      <HeroScene
        job={job}
        stages={stages}
        activeStage={activeStage}
        activeIdx={activeIdx}
        onPick={setActiveId}
        onDelete={deleteStage}
        onAddStage={addStage}
        onAddStageAt={addStageAt}
        onCloseSheet={() => setActiveId(null)}
        dragIdx={dragIdx}
        overIdx={overIdx}
        onDragStart={(i) => setDragIdx(i)}
        onDragOver={(i) => setOverIdx(i)}
        onDrop={onDrop}
        onChangeStage={(patch) => {
          if (activeStage?.id) updateStageById(activeStage.id, patch)
        }}
        onChangeParticipants={(next) => {
          if (activeStage?.id)
            updateStageById(activeStage.id, { participants: next })
        }}
        bank={
          activeStage?.id ? banksByStage.get(activeStage.id) ?? null : null
        }
        jobId={jobId}
      />

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
                  const normalized = normalizeStages(fresh.stages)
                  setStages(normalized)
                  setActiveId(null)
                  if (normalized.length !== fresh.stages.length)
                    scheduleSave(normalized)
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
                  const normalized = normalizeStages(fresh.stages)
                  setStages(normalized)
                  setActiveId(null)
                  if (normalized.length !== fresh.stages.length)
                    scheduleSave(normalized)
                  setPickerOpen(false)
                },
              },
            )
          }
        />
      )}

      <DangerConfirmDialog
        open={confirmingReset}
        title="Reset to source template"
        description="Discard your edits and reset to the source template? This cannot be undone."
        confirmLabel="Reset"
        pendingLabel="Resetting…"
        pending={resetMutation.isPending}
        onConfirm={confirmReset}
        onClose={() => setConfirmingReset(false)}
      />

      <EditCategoryWarningModal
        open={warningModal !== null}
        onOpenChange={(open) => {
          if (!open) {
            // User dismissed: discard the pending save and clear dirty state
            setWarningModal(null)
            setIsDirty(false)
          }
        }}
        category={warningModal?.category ?? null}
        inFlightCounts={warningModal?.inFlight ?? {}}
        onConfirm={() => {
          if (warningModal) {
            doSave(warningModal.pendingStages, warningModal.editGen)
          }
          setWarningModal(null)
        }}
      />
    </div>
  )
}

/* ─── Hero scene: ambient background + funnel + sheet ───── */

type BankLite = { stage_id: string; question_count: number; status: string }

function HeroScene({
  job,
  stages,
  activeStage,
  activeIdx,
  onPick,
  onDelete,
  onAddStage,
  onAddStageAt,
  onCloseSheet,
  dragIdx,
  overIdx,
  onDragStart,
  onDragOver,
  onDrop,
  onChangeStage,
  onChangeParticipants,
  bank,
  jobId,
}: {
  job: JobPostingWithSnapshot
  stages: PipelineStageUpdateInput[]
  activeStage: PipelineStageUpdateInput | null
  activeIdx: number
  onPick: (id: string | null) => void
  onDelete: (id: string) => void
  onAddStage: () => void
  onAddStageAt: (index: number) => void
  onCloseSheet: () => void
  dragIdx: number | null
  overIdx: number | null
  onDragStart: (i: number) => void
  onDragOver: (i: number) => void
  onDrop: () => void
  onChangeStage: (patch: Partial<LooseStageInput>) => void
  onChangeParticipants: (next: StageParticipantInput[]) => void
  bank: BankLite | null
  jobId: string
}) {
  return (
    <div
      className="relative overflow-hidden rounded-[16px] border"
      style={{
        background:
          'linear-gradient(180deg, color-mix(in oklab, var(--px-accent-tint) 30%, var(--px-surface)) 0%, var(--px-surface) 100%)',
        borderColor: 'var(--px-hairline)',
        minHeight: 640,
        padding: '28px 40px 40px',
      }}
    >
      {/* Ambient blobs */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute"
        style={{
          top: -120,
          right: -140,
          width: 420,
          height: 420,
          borderRadius: 999,
          background:
            'radial-gradient(circle, color-mix(in oklab, var(--px-accent-soft) 55%, transparent) 0%, transparent 70%)',
          opacity: 0.4,
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute"
        style={{
          bottom: -120,
          left: -140,
          width: 380,
          height: 380,
          borderRadius: 999,
          background:
            'radial-gradient(circle, color-mix(in oklab, #E8D4B0 60%, transparent) 0%, transparent 70%)',
          opacity: 0.38,
        }}
      />

      {/* Hero header */}
      <div className="relative mb-4 flex items-center">
        <div>
          <div
            className="px-mono text-[10px] font-semibold uppercase"
            style={{ letterSpacing: '1.4px', color: 'var(--px-fg-4)' }}
          >
            Funnel visualization
          </div>
          <div
            className="px-serif mt-0.5 text-[20px] font-normal"
            style={{ color: 'var(--px-fg)' }}
          >
            {job.title}
          </div>
        </div>
        <div className="flex-1" />
        <div className="flex gap-2">
          <button className="px-btn ghost sm" type="button" onClick={onAddStage}>
            <PlusIcon size={11} /> Add stage
          </button>
        </div>
      </div>

      {/* Funnel (centered by default) + sheet (slides in from the right
          when a stage is selected). Using flex + justify-center rather
          than a grid: as the sheet's wrapper animates 0 → sheet width,
          the centered group grows, which visually shifts the funnel
          left. Gap transitions alongside so the pair stays flush while
          the sheet is closed. */}
      <div
        className="relative flex items-start justify-center"
        style={{
          minHeight: 640,
          gap: activeStage ? 32 : 0,
          transition: 'gap 320ms cubic-bezier(0.2, 0.7, 0.2, 1)',
        }}
      >
        <div style={{ flex: '0 1 620px', minWidth: 0 }}>
          <HeroFunnel
            stages={stages}
            activeId={activeStage?.id ?? null}
            onPick={onPick}
            onDelete={onDelete}
            onAddStageAt={onAddStageAt}
            dragIdx={dragIdx}
            overIdx={overIdx}
            onDragStart={onDragStart}
            onDragOver={onDragOver}
            onDrop={onDrop}
          />
        </div>

        <AnimatedSheetWrap active={!!activeStage}>
          {activeStage && (
            <StageSheet
              key={activeStage.id ?? `pos-${activeStage.position}`}
              stage={activeStage}
              stages={stages}
              activeIdx={activeIdx}
              onClose={onCloseSheet}
              onChange={onChangeStage}
              onChangeParticipants={onChangeParticipants}
              bank={bank}
              jobId={jobId}
            />
          )}
        </AnimatedSheetWrap>
      </div>
    </div>
  )
}

/* ─── Animated wrapper for the stage sheet ───────────────
   Outer wrapper transitions its WIDTH so the flex-centered funnel
   shifts left naturally as the sheet appears. Inner wrapper fades +
   translates so the content itself has an entrance animation.
   The wrapper stays mounted with an explicit inner width so content
   doesn't reflow as the outer width interpolates. */

function AnimatedSheetWrap({
  active,
  children,
}: {
  active: boolean
  children: React.ReactNode
}) {
  const sheetWidth = 'min(420px, 42vw)'

  // The exit animation needs the sheet content mounted for its whole
  // duration, but the parent hands us `children = null` as soon as
  // `active` flips false. We cache the most recent non-null children and
  // render them during the exit window. The cache update is keyed off the
  // React element's `key` rather than the JSX reference so the effect
  // only runs when stage identity actually changes (a parent re-render
  // rebuilds the JSX object but not the key).
  const childKey =
    children &&
    typeof children === 'object' &&
    'key' in (children as object)
      ? (children as React.ReactElement).key
      : null
  const [cached, setCached] = useState<React.ReactNode | null>(
    children ?? null,
  )
  useEffect(() => {
    if (children && childKey != null) setCached(children)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [childKey])

  return (
    <>
      <style>{`
        @keyframes px-sheet-enter-v {
          from { opacity: 0; transform: scaleY(0); }
          to   { opacity: 1; transform: scaleY(1); }
        }
        @keyframes px-sheet-exit-v {
          from { opacity: 1; transform: scaleY(1); }
          to   { opacity: 0; transform: scaleY(0); }
        }
      `}</style>
      <div
        aria-hidden={!active}
        style={{
          flex: '0 0 auto',
          // The width transition drives the flex-centred funnel's left/
          // right shift. On both directions we want the sheet's vertical
          // scaleY to be the dominant visible motion, so the width
          // transition is sequenced around it to prevent the old
          // left-to-right "wipe" artefact caused by `overflow: hidden`
          // clipping content while the wrapper grows.
          width: active ? sheetWidth : 0,
          overflow: 'hidden',
          transition: active
            ? // Enter: widen immediately so the funnel shifts while the
              // content-scaleY animation waits out its delay below.
              'width 260ms cubic-bezier(0.2, 0.7, 0.2, 1)'
            : // Exit: delay the width collapse until after the scaleY
              // exit finishes, so the sheet has vanished before the
              // wrapper collapses. No right-to-left wipe on close.
              'width 260ms cubic-bezier(0.2, 0.7, 0.2, 1) 180ms',
        }}
      >
        <div
          style={{
            width: sheetWidth,
            transformOrigin: '50% 50%',
            // Enter: delay scaleY by the full width duration. While the
            // wrapper widens the content is held at scaleY(0)+opacity(0)
            // by `animation-fill-mode: both`, so the growing wrapper has
            // nothing inside to reveal — no horizontal wipe. Once width
            // is full, the content scales up from the middle.
            //
            // Exit: scaleY collapses first, then (because width's delay
            // below matches the scaleY duration) the wrapper shrinks
            // after the sheet has already vanished.
            animation: active
              ? 'px-sheet-enter-v 260ms cubic-bezier(0.2, 0.7, 0.2, 1) 260ms both'
              : 'px-sheet-exit-v 220ms cubic-bezier(0.4, 0, 0.2, 1) both',
          }}
        >
          {children ?? cached}
        </div>
      </div>
    </>
  )
}

/* ─── Hero funnel — stacked SVG trapezoids ──────────────── */

function HeroFunnel({
  stages,
  activeId,
  onPick,
  onDelete,
  onAddStageAt,
  dragIdx,
  overIdx,
  onDragStart,
  onDragOver,
  onDrop,
}: {
  stages: PipelineStageUpdateInput[]
  activeId: string | null
  onPick: (id: string | null) => void
  onDelete: (id: string) => void
  onAddStageAt: (index: number) => void
  dragIdx: number | null
  overIdx: number | null
  onDragStart: (i: number) => void
  onDragOver: (i: number) => void
  onDrop: () => void
}) {
  const VB_W = 620
  const sliceH = 66
  const sliceGap = 2
  const totalH = stages.length * sliceH + (stages.length - 1) * sliceGap
  // Extra room for the drip inflow above the first slice (48) + margin
  // (24) plus the drip exhaust below the last slice (48) + margin (24).
  const topY = 72
  const VB_H = topY + totalH + 72

  const topW = 500
  const botW = 140

  // ── Right-click context menu ──
  // Replaces the inline delete × on each slice. Opens on right-click of a
  // slice with options: add stage above, add stage below, delete stage.
  // Options are disabled appropriately for fixed Intake / Debrief bookends.
  const [contextMenu, setContextMenu] = useState<
    | { idx: number; stageId: string | undefined; stageName: string; x: number; y: number }
    | null
  >(null)
  const [confirmingDelete, setConfirmingDelete] = useState<
    { id: string; name: string } | null
  >(null)

  useEffect(() => {
    if (!contextMenu) return
    const onDocDown = (e: MouseEvent) => {
      const target = e.target as Element | null
      if (target?.closest('[data-funnel-menu]')) return
      setContextMenu(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setContextMenu(null)
    }
    document.addEventListener('mousedown', onDocDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [contextMenu])

  // Hovered slice index — drives the hover-expand animation.
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  // ── Drag-and-drop via pointer events ──
  // HTML5 native drag doesn't work on SVG <g> elements across browsers, so
  // we implement drag manually with pointer capture. The dragged slice
  // follows the cursor via a live Y translate (in viewBox units), and each
  // cursor tick recomputes which slice index the pointer is hovering over.
  const svgRef = useRef<SVGSVGElement | null>(null)
  const dragRef = useRef<{
    idx: number
    startClientY: number
    pointerId: number
  } | null>(null)
  const [liveDragY, setLiveDragY] = useState(0)
  const sliceStride = sliceH + sliceGap

  const pxPerViewUnit = () => {
    const el = svgRef.current
    if (!el) return 1
    return el.getBoundingClientRect().height / VB_H
  }

  const startDrag = (e: React.PointerEvent<SVGGElement>, i: number) => {
    // Intake (0) and Debrief (last) are fixed bookends — can't be dragged.
    if (i === 0 || i === stages.length - 1) return
    e.preventDefault()
    e.stopPropagation()
    const target = e.currentTarget
    try {
      target.setPointerCapture(e.pointerId)
    } catch {
      /* no-op */
    }
    dragRef.current = {
      idx: i,
      startClientY: e.clientY,
      pointerId: e.pointerId,
    }
    setLiveDragY(0)
    onDragStart(i)
  }

  const moveDrag = (e: React.PointerEvent<SVGGElement>) => {
    const d = dragRef.current
    if (!d) return
    const ratio = pxPerViewUnit()
    if (ratio <= 0) return
    const dyUnits = (e.clientY - d.startClientY) / ratio
    setLiveDragY(dyUnits)
    // Translate pointer delta into a target slice index. Clamp to [1, N-2]
    // so drops can never land on the fixed Intake (0) or Debrief (last).
    const delta = Math.round(dyUnits / sliceStride)
    const target = Math.max(
      1,
      Math.min(stages.length - 2, d.idx + delta),
    )
    onDragOver(target)
  }

  const endDrag = (e: React.PointerEvent<SVGGElement>) => {
    const d = dragRef.current
    if (!d) return
    try {
      e.currentTarget.releasePointerCapture(d.pointerId)
    } catch {
      /* no-op */
    }
    dragRef.current = null
    setLiveDragY(0)
    onDrop()
  }

  // Each slice's width is a position-based funnel curve (independent of count)
  // to keep the silhouette smooth even as counts change.
  const widthAt = (i: number, total: number) => {
    const t = i / total
    const tNext = (i + 1) / total
    return {
      top: topW + (botW - topW) * Math.pow(t, 0.85),
      bot: topW + (botW - topW) * Math.pow(tNext, 0.85),
    }
  }

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        width="100%"
        style={{ display: 'block', touchAction: 'none' }}
      >
        {stages.map((s, i) => {
          const { top, bot } = widthAt(i, stages.length)
          const y = topY + i * (sliceH + sliceGap)
          const cx = VB_W / 2
          const tl = cx - top / 2
          const tr = cx + top / 2
          const bl = cx - bot / 2
          const br = cx + bot / 2
          const pal = paletteFor(s.stage_type)
          const isActive = activeId === s.id
          const isDragging = dragIdx === i
          const isDragOver = overIdx === i && dragIdx !== null && dragIdx !== i
          const isIntake = i === 0
          const isLast = i === stages.length - 1
          const isFixed = isIntake || isLast
          const isHover = hoverIdx === i && !isDragging
          const sliceCenterY = y + sliceH / 2
          const pts = `${tl},${y} ${tr},${y} ${br},${y + sliceH} ${bl},${y + sliceH}`

          // Compose transforms: dragging applies a live Y translate; hover
          // applies a subtle scale. We CSS-stack them so a hovered slice
          // mid-drag (shouldn't happen thanks to isHover's isDragging guard)
          // wouldn't compound weirdly.
          const transformPieces: string[] = []
          if (isDragging) transformPieces.push(`translate(0, ${liveDragY}px)`)
          if (isHover) transformPieces.push('scale(1.035)')
          const composedTransform =
            transformPieces.length > 0 ? transformPieces.join(' ') : undefined

          return (
            <g
              key={s.id ?? `pos-${s.position}`}
              style={{
                cursor: 'pointer',
                opacity: isDragging ? 0.7 : 1,
                transform: composedTransform,
                transformOrigin: `${cx}px ${sliceCenterY}px`,
                transformBox: 'view-box',
                filter: isHover
                  ? 'drop-shadow(0 6px 14px rgba(58, 45, 28, 0.16))'
                  : undefined,
                transition: isDragging
                  ? 'none'
                  : 'transform 220ms cubic-bezier(0.2, 0.7, 0.2, 1), filter 220ms ease, opacity 160ms',
              }}
              onMouseEnter={() => {
                if (!dragRef.current) setHoverIdx(i)
              }}
              onMouseLeave={() => {
                setHoverIdx((prev) => (prev === i ? null : prev))
              }}
              onClick={() => {
                // Suppress click right after a drag so release doesn't
                // also "select" the slice.
                if (dragRef.current || liveDragY !== 0) return
                if (s.id) onPick(s.id)
              }}
              onContextMenu={(e) => {
                e.preventDefault()
                setContextMenu({
                  idx: i,
                  stageId: s.id,
                  stageName: s.name,
                  x: e.clientX,
                  y: e.clientY,
                })
              }}
            >
              {/* Active halo */}
              {isActive && (
                <polygon
                  points={pts}
                  fill="none"
                  stroke={pal.edge}
                  strokeWidth={3}
                  style={{ filter: `drop-shadow(0 0 8px ${pal.edge}88)` }}
                />
              )}
              {/* Slice */}
              <polygon
                points={pts}
                fill={pal.fill}
                stroke={isActive ? pal.edge : 'rgba(255,255,255,0.6)'}
                strokeWidth={isActive ? 1.5 : 1}
                style={{ transition: 'all 180ms' }}
              />
              {/* Drop-over indicator */}
              {isDragOver && (
                <line
                  x1={tl}
                  y1={y}
                  x2={tr}
                  y2={y}
                  stroke="var(--px-accent)"
                  strokeWidth={3}
                  strokeLinecap="round"
                />
              )}
              {/* Inner label */}
              <text
                x={cx}
                y={y + sliceH / 2 - 4}
                textAnchor="middle"
                style={{
                  fontFamily: 'var(--px-serif)',
                  fontSize: 16,
                  fontWeight: 400,
                  fill: pal.label,
                  pointerEvents: 'none',
                }}
              >
                {s.name}
              </text>
              <text
                x={cx}
                y={y + sliceH / 2 + 12}
                textAnchor="middle"
                style={{
                  fontFamily: 'var(--px-mono)',
                  fontSize: 10,
                  fontWeight: 500,
                  letterSpacing: 0.6,
                  fill: pal.label,
                  opacity: 0.6,
                  pointerEvents: 'none',
                }}
              >
                {STAGE_TYPE_LABEL[s.stage_type].toUpperCase()}
              </text>

              {/* Left connector + drag handle (intake is FIXED) */}
              <line
                x1={tl - 26}
                y1={y + sliceH / 2}
                x2={tl - 4}
                y2={y + sliceH / 2}
                stroke={pal.edge}
                strokeWidth={1.5}
                strokeDasharray="2 3"
                opacity={0.55}
              />
              {!isFixed ? (
                <g
                  transform={`translate(${tl - 42}, ${y + sliceH / 2 - 10})`}
                  onPointerDown={(e) => startDrag(e, i)}
                  onPointerMove={moveDrag}
                  onPointerUp={endDrag}
                  onPointerCancel={endDrag}
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    cursor: isDragging ? 'grabbing' : 'grab',
                    touchAction: 'none',
                  }}
                >
                  <rect
                    width={20}
                    height={20}
                    rx={5}
                    fill="white"
                    stroke="rgba(0,0,0,0.08)"
                  />
                  <circle cx={7} cy={8} r={1} fill="#999" />
                  <circle cx={13} cy={8} r={1} fill="#999" />
                  <circle cx={7} cy={12} r={1} fill="#999" />
                  <circle cx={13} cy={12} r={1} fill="#999" />
                </g>
              ) : (
                <g transform={`translate(${tl - 44}, ${y + sliceH / 2 - 10})`}>
                  <rect
                    width={24}
                    height={20}
                    rx={5}
                    fill="var(--px-accent-tint)"
                    stroke="var(--px-accent-line)"
                  />
                  <text
                    x={12}
                    y={13}
                    textAnchor="middle"
                    style={{
                      fontFamily: 'var(--px-mono)',
                      fontSize: 8,
                      fontWeight: 700,
                      fill: 'var(--px-accent)',
                      letterSpacing: 0.4,
                    }}
                  >
                    FIX
                  </text>
                </g>
              )}

              {/* Auto-advance badge — sits just right of the slice edge */}
              {(s as LooseStageInput).advance_behavior === 'auto_advance' && (
                <g transform={`translate(${tr + 16}, ${y + sliceH / 2 - 8})`}>
                  <circle
                    cx={8}
                    cy={8}
                    r={8}
                    fill="var(--px-accent-tint)"
                    stroke="var(--px-accent-line)"
                  />
                  <g transform="translate(2.5, 2.5)" stroke="var(--px-accent)" strokeWidth={1.6} fill="none" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5.5 0v2M5.5 9v2M0 5.5h2M9 5.5h2M1.6 1.6l1.4 1.4M8 8l1.4 1.4M1.6 9.4L3 8M8 3l1.4-1.4" />
                  </g>
                </g>
              )}

            </g>
          )
        })}

        {/* Drip inflow above the first slice — candidates rain into Intake.
            Far more bubbles than the Debrief exhaust below: the funnel
            metaphor is "many apply, few hired", so a dense shower at the
            top visually contrasts the 3-bubble exhaust at the bottom.
            Bubbles spread across the full width of the Intake's top edge,
            each with its own start-offset / duration / delay so they
            never land in sync — feels organic rather than choreographed. */}
        {(() => {
          if (stages.length === 0) return null
          const cx = VB_W / 2
          // Fills are edge colours from the top half of STAGE_TYPE_PALETTE
          // (intake / phone_screen / take_home) so the shower harmonises
          // with the pale-teal Intake slice.
          const INFLOW_BUBBLES = [
            { dx: -208, offset: 44, r: 2.5, fill: '#9EC4BD', dur: 1.7, delay: 0.0 },
            { dx: -170, offset: 38, r: 3.0, fill: '#7FB0A7', dur: 1.5, delay: 0.45 },
            { dx: -132, offset: 46, r: 2.5, fill: '#5C9D92', dur: 1.9, delay: 0.2 },
            { dx: -92,  offset: 40, r: 3.0, fill: '#9EC4BD', dur: 1.4, delay: 0.7 },
            { dx: -50,  offset: 48, r: 4.0, fill: '#7FB0A7', dur: 1.6, delay: 0.1 },
            { dx: -10,  offset: 42, r: 2.5, fill: '#5C9D92', dur: 1.85, delay: 0.55 },
            { dx: 34,   offset: 46, r: 3.0, fill: '#9EC4BD', dur: 1.5, delay: 0.25 },
            { dx: 76,   offset: 38, r: 2.5, fill: '#7FB0A7', dur: 1.75, delay: 0.95 },
            { dx: 122,  offset: 44, r: 4.0, fill: '#5C9D92', dur: 1.55, delay: 0.35 },
            { dx: 166,  offset: 48, r: 2.5, fill: '#9EC4BD', dur: 1.9, delay: 0.8 },
            { dx: 208,  offset: 40, r: 3.0, fill: '#7FB0A7', dur: 1.6, delay: 0.15 },
          ]
          return (
            <g>
              {INFLOW_BUBBLES.map((b, i) => (
                <circle
                  key={i}
                  cx={cx + b.dx}
                  cy={topY - b.offset / 2}
                  r={b.r}
                  fill={b.fill}
                >
                  <animate
                    attributeName="cy"
                    values={`${topY - b.offset};${topY}`}
                    dur={`${b.dur}s`}
                    repeatCount="indefinite"
                    begin={`${b.delay}s`}
                  />
                  <animate
                    attributeName="opacity"
                    values="1;0"
                    dur={`${b.dur}s`}
                    repeatCount="indefinite"
                    begin={`${b.delay}s`}
                  />
                </circle>
              ))}
            </g>
          )
        })()}

        {/* Drip exhaust below the last slice */}
        {(() => {
          if (stages.length === 0) return null
          const y = topY + totalH
          const cx = VB_W / 2
          return (
            <g>
              <circle cx={cx} cy={y + 22} r={4} fill="#5E8E73">
                <animate
                  attributeName="cy"
                  values={`${y};${y + 40}`}
                  dur="1.4s"
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="1;0"
                  dur="1.4s"
                  repeatCount="indefinite"
                />
              </circle>
              <circle cx={cx - 8} cy={y + 26} r={3} fill="#B5844C">
                <animate
                  attributeName="cy"
                  values={`${y};${y + 46}`}
                  dur="1.8s"
                  repeatCount="indefinite"
                  begin="0.4s"
                />
                <animate
                  attributeName="opacity"
                  values="1;0"
                  dur="1.8s"
                  repeatCount="indefinite"
                  begin="0.4s"
                />
              </circle>
              <circle cx={cx + 7} cy={y + 30} r={3} fill="#C29A5E">
                <animate
                  attributeName="cy"
                  values={`${y};${y + 48}`}
                  dur="2s"
                  repeatCount="indefinite"
                  begin="0.8s"
                />
                <animate
                  attributeName="opacity"
                  values="1;0"
                  dur="2s"
                  repeatCount="indefinite"
                  begin="0.8s"
                />
              </circle>
            </g>
          )
        })()}
      </svg>

      <div
        className="mt-1.5 text-center text-[11px]"
        style={{ color: 'var(--px-fg-4)' }}
      >
        Candidates flow top → bottom. Click any slice to configure.
        Right-click for stage actions.
      </div>

      {contextMenu && (
        <StageContextMenu
          menu={contextMenu}
          stageCount={stages.length}
          onAddAbove={() => {
            onAddStageAt(contextMenu.idx)
            setContextMenu(null)
          }}
          onAddBelow={() => {
            onAddStageAt(contextMenu.idx + 1)
            setContextMenu(null)
          }}
          onDelete={() => {
            if (contextMenu.stageId) {
              setConfirmingDelete({
                id: contextMenu.stageId,
                name: contextMenu.stageName,
              })
            }
            setContextMenu(null)
          }}
        />
      )}

      <DangerConfirmDialog
        open={confirmingDelete !== null}
        title="Delete stage"
        description={
          <>
            Delete stage <strong>{confirmingDelete?.name ?? ''}</strong>?
          </>
        }
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        onConfirm={() => {
          if (!confirmingDelete) return
          onDelete(confirmingDelete.id)
          setConfirmingDelete(null)
        }}
        onClose={() => setConfirmingDelete(null)}
      />
    </div>
  )
}

/* ─── Stage right-click context menu ────────────────────── */

function StageContextMenu({
  menu,
  stageCount,
  onAddAbove,
  onAddBelow,
  onDelete,
}: {
  menu: { idx: number; stageId: string | undefined; stageName: string; x: number; y: number }
  stageCount: number
  onAddAbove: () => void
  onAddBelow: () => void
  onDelete: () => void
}) {
  const isIntake = menu.idx === 0
  const isDebrief = menu.idx === stageCount - 1
  // Intake can't have a stage above it; Debrief can't have one below.
  // Neither bookend can be deleted.
  const canAddAbove = !isIntake
  const canAddBelow = !isDebrief
  const canDelete = !isIntake && !isDebrief

  return (
    <div
      data-funnel-menu
      role="menu"
      className="fixed z-50 min-w-[180px] overflow-hidden rounded-md border py-1"
      style={{
        top: menu.y,
        left: menu.x,
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline-strong)',
        boxShadow: 'var(--px-shadow-md)',
      }}
    >
      <MenuItem onClick={onAddAbove} disabled={!canAddAbove}>
        <PlusIcon size={11} /> Add stage above
      </MenuItem>
      <MenuItem onClick={onAddBelow} disabled={!canAddBelow}>
        <PlusIcon size={11} /> Add stage below
      </MenuItem>
      <div
        className="my-1 h-px"
        style={{ background: 'var(--px-hairline)' }}
        aria-hidden="true"
      />
      <MenuItem onClick={onDelete} disabled={!canDelete} danger>
        Delete stage
      </MenuItem>
    </div>
  )
}

function MenuItem({
  children,
  onClick,
  disabled,
  danger,
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  danger?: boolean
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      className="flex w-full items-center gap-2 border-none bg-transparent px-3 py-1.5 text-left text-[12.5px]"
      style={{
        color: disabled
          ? 'var(--px-fg-4)'
          : danger
            ? 'var(--px-danger)'
            : 'var(--px-fg)',
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
      onMouseEnter={(e) => {
        if (!disabled)
          e.currentTarget.style.background = danger
            ? 'var(--px-danger-bg)'
            : 'var(--px-surface-2)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
      }}
    >
      {children}
    </button>
  )
}

/* ─── Floating config sheet — config + participants only ─ */

type SheetTab = 'config' | 'participants'

function StageSheet({
  stage,
  stages,
  activeIdx,
  onClose,
  onChange,
  onChangeParticipants,
  bank,
  jobId,
}: {
  stage: PipelineStageUpdateInput
  stages: PipelineStageUpdateInput[]
  activeIdx: number
  onClose: () => void
  onChange: (patch: Partial<PipelineStageUpdateInput>) => void
  onChangeParticipants: (next: StageParticipantInput[]) => void
  bank: BankLite | null
  jobId: string
}) {
  const [tab, setTab] = useState<SheetTab>('config')

  const pal = paletteFor(stage.stage_type)
  const isFixedBookend = activeIdx === 0 || activeIdx === stages.length - 1

  return (
    <div
      className="sticky top-0 overflow-hidden rounded-[14px]"
      style={{
        background: 'var(--px-surface)',
        border: '1px solid rgba(0,0,0,0.06)',
        boxShadow:
          '0 20px 50px -20px rgba(20, 20, 40, 0.2), 0 2px 6px rgba(0,0,0,0.04)',
      }}
    >
      {/* Colored top strip keyed to the slice palette */}
      <div
        style={{
          height: 4,
          background: `linear-gradient(90deg, ${pal.edge} 0%, ${pal.fill} 100%)`,
        }}
      />

      {/* Sheet header */}
      <div className="flex items-start gap-3 px-[18px] pb-3 pt-4">
        <div
          className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg"
          style={{
            background: pal.fill,
            color: pal.label,
            border: `1px solid ${pal.edge}`,
          }}
        >
          <StageTypeGlyph type={stage.stage_type} />
        </div>
        <div className="min-w-0 flex-1">
          <div
            className="px-mono text-[9.5px] font-semibold uppercase"
            style={{
              letterSpacing: '1px',
              color: pal.label,
              opacity: 0.65,
            }}
          >
            Stage 0{activeIdx + 1} · {STAGE_TYPE_LABEL[stage.stage_type]}
          </div>
          <div
            className="px-serif mt-0.5 text-[22px] font-normal leading-none"
            style={{ color: 'var(--px-fg)', letterSpacing: '-0.2px' }}
          >
            {stage.name}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          type="button"
          className="flex h-[26px] w-[26px] items-center justify-center rounded-md border"
          style={{
            borderColor: 'var(--px-hairline)',
            background: 'var(--px-surface)',
            color: 'var(--px-fg-3)',
            cursor: 'pointer',
          }}
        >
          <CloseIcon />
        </button>
      </div>

      {/* Tabs */}
      <div
        className="flex gap-0.5 border-b px-3"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        {(
          [
            { id: 'config', label: 'Config', Icon: EditIcon },
            { id: 'participants', label: 'Participants', Icon: UsersIcon },
          ] as const
        ).map(({ id, label, Icon }) => {
          const active = tab === id
          return (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className="-mb-px flex items-center gap-1.5 border-none bg-transparent px-3 py-2.5 text-[12px]"
              style={{
                borderBottom: `2px solid ${active ? pal.edge : 'transparent'}`,
                color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
                fontWeight: active ? 600 : 500,
                cursor: 'pointer',
              }}
            >
              <Icon size={11} />
              {label}
            </button>
          )
        })}
      </div>

      {/* Tab body */}
      <div className="px-[18px] pb-5 pt-4">
        {tab === 'config' && (
          <ConfigTab
            stage={stage}
            onChange={onChange}
            isFixedBookend={isFixedBookend}
          />
        )}
        {tab === 'participants' && (
          <ParticipantsTab
            stage={stage}
            jobId={jobId}
            onChange={onChangeParticipants}
          />
        )}
      </div>

      {/* Footer — only rendered when there's real data to show (bank status). */}
      {bank && (
        <div
          className="flex items-center justify-end border-t px-[18px] py-3"
          style={{
            borderColor: 'var(--px-hairline)',
            background: 'var(--px-bg-2)',
          }}
        >
          <span
            className="px-mono rounded border px-1.5 py-0.5 text-[9.5px] font-medium"
            style={
              bank.status === 'confirmed'
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
            {bank.question_count}Q · {bank.status}
          </span>
        </div>
      )}
    </div>
  )
}

/* ─── Config tab — stage config form ───────────────────── */

function ConfigTab({
  stage,
  onChange,
  isFixedBookend,
}: {
  stage: PipelineStageUpdateInput
  onChange: (patch: Partial<LooseStageInput>) => void
  isFixedBookend: boolean
}) {
  // TODO(Task 25): replace with per-category field rendering once the
  // matrix-driven funnel refactor lands.
  const loose = stage as LooseStageInput

  const advanceOptions: { k: AdvanceBehavior; label: string }[] = [
    { k: 'auto_advance', label: 'Auto-advance' },
    { k: 'manual_review', label: 'Manual review' },
  ]
  const stageTypeOptions: { value: StageType; label: string; disabled?: boolean }[] = [
    { value: 'intake', label: 'Intake' },
    { value: 'phone_screen', label: 'Phone Screen' },
    { value: 'ai_screening', label: 'AI Screening' },
    { value: 'human_interview', label: 'Human Interview' },
    { value: 'debrief', label: 'Debrief' },
    { value: 'take_home', label: 'Take-home (Coming soon)', disabled: true },
  ]
  const bookendLabel =
    stage.stage_type === 'intake'
      ? 'Intake is the fixed entry point'
      : stage.stage_type === 'debrief'
        ? 'Debrief is the fixed exit point'
        : null

  // Clamp duration into the slider's [5, 60] range with 5-min steps. The
  // backend accepts 1..240; the slider is the UI-facing preset range.
  const clampedDuration = Math.max(
    5,
    Math.min(60, Math.round(((loose.duration_minutes as number | undefined) ?? 0) / 5) * 5 || 5),
  )

  return (
    <div className="flex flex-col gap-3.5">
      <div className="grid grid-cols-2 gap-3">
        <SheetField label="Stage name">
          <input
            className="px-input"
            value={stage.name}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </SheetField>
        <SheetField
          label="Stage type"
          sub={bookendLabel ? `${bookendLabel}.` : undefined}
        >
          <select
            className="px-input"
            value={stage.stage_type}
            disabled={isFixedBookend}
            onChange={(e) => {
              const next = e.target.value as StageType
              // Strip participants whose role doesn't match the new type's
              // slot (e.g. switching away from human_interview drops any
              // interviewer assignments that no longer belong).
              const newSlot = participantSlotsFor(next)[0]?.role ?? null
              const current = stage.participants ?? []
              const filtered =
                newSlot === null
                  ? []
                  : current.filter((p) => p.role === newSlot)
              onChange({ stage_type: next, participants: filtered })
            }}
          >
            {stageTypeOptions.map((o) => (
              <option key={o.value} value={o.value} disabled={o.disabled}>
                {o.label}
              </option>
            ))}
          </select>
        </SheetField>
      </div>

      <SheetField label="Difficulty">
        <DifficultySlider
          value={(loose.difficulty as StageDifficulty | undefined) ?? 'easy'}
          onChange={(d) => onChange({ difficulty: d })}
        />
      </SheetField>

      <SheetField label="Duration">
        <DurationSlider
          value={clampedDuration}
          onChange={(v) => onChange({ duration_minutes: v })}
        />
      </SheetField>

      <SheetField label="SLA (days)">
        <input
          className="px-input"
          type="number"
          min={0}
          value={stage.sla_days ?? ''}
          placeholder="No SLA"
          onChange={(e) => {
            const v = e.target.value === '' ? null : Number(e.target.value)
            onChange({ sla_days: v })
          }}
        />
      </SheetField>

      <SheetField
        label="Advance rule"
        sub={`Currently: ${stageGate(stage.stage_type, (loose.advance_behavior as AdvanceBehavior | undefined) ?? 'manual_review')}.`}
      >
        <div className="flex flex-wrap gap-1.5">
          {advanceOptions.map((opt) => {
            const active = (loose.advance_behavior as AdvanceBehavior | undefined) === opt.k
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
      </SheetField>
    </div>
  )
}

/* ─── Duration slider — 5-min steps, 5..60 min range ────── */

function DurationSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  const min = 5
  const max = 60
  const step = 5
  const pct = ((value - min) / (max - min)) * 100

  return (
    <>
      <style>{`
        .px-range-duration {
          -webkit-appearance: none;
          appearance: none;
          width: 100%;
          height: 6px;
          border-radius: 99px;
          outline: none;
          cursor: pointer;
        }
        .px-range-duration::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 18px;
          height: 18px;
          background: var(--px-accent);
          border: 2px solid var(--px-surface);
          border-radius: 99px;
          cursor: pointer;
          box-shadow: 0 1px 3px rgba(0,0,0,0.18);
        }
        .px-range-duration::-moz-range-thumb {
          width: 18px;
          height: 18px;
          background: var(--px-accent);
          border: 2px solid var(--px-surface);
          border-radius: 99px;
          cursor: pointer;
          box-shadow: 0 1px 3px rgba(0,0,0,0.18);
        }
        .px-range-duration:focus-visible::-webkit-slider-thumb {
          box-shadow: 0 0 0 3px var(--px-accent-tint), 0 1px 3px rgba(0,0,0,0.18);
        }
      `}</style>
      <div className="flex items-center gap-3">
        <input
          type="range"
          className="px-range-duration"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          style={{
            background: `linear-gradient(to right,
              var(--px-accent) 0%,
              var(--px-accent) ${pct}%,
              var(--px-surface-3) ${pct}%,
              var(--px-surface-3) 100%)`,
          }}
          aria-label="Duration in minutes"
        />
        <span
          className="px-mono text-[12.5px]"
          style={{
            color: 'var(--px-fg)',
            fontVariantNumeric: 'tabular-nums',
            minWidth: 42,
            textAlign: 'right',
          }}
        >
          {value}m
        </span>
      </div>
    </>
  )
}

/* ─── Participants tab — wraps StageParticipantsEditor ─── */

function ParticipantsTab({
  stage,
  jobId,
  onChange,
}: {
  stage: PipelineStageUpdateInput
  jobId: string
  onChange: (next: StageParticipantInput[]) => void
}) {
  const slots = participantSlotsFor(stage.stage_type)
  const hasSlots = slots.length > 0
  const slotRole = slots[0]?.role ?? null
  const roleLabel =
    stage.stage_type === 'debrief'
      ? 'Reviewers'
      : stage.stage_type === 'ai_screening'
        ? 'Observers'
        : 'Interviewers'

  // The assignable-users pool is used to enrich newly-added participants
  // with display fields (full_name, email) so the pill renders a name.
  // The editor's onChange only emits {user_id, role}, so without this
  // lookup pills for just-added users would render as empty chips.
  const { data: pool } = useAssignableUsers(jobId, slotRole)

  // Participants already in state keep their display fields (preserved
  // from the server response). The pool covers anyone just added via the
  // picker. Existing data wins when both are present so we don't clobber
  // a loaded display field with an empty pool entry.
  const participantsForEditor = (stage.participants ??
    []) as StageParticipantResponse[]

  return (
    <div>
      <div
        className="px-mono mb-3 text-[10px] font-semibold uppercase"
        style={{ letterSpacing: '0.5px', color: 'var(--px-fg-4)' }}
      >
        {roleLabel}
      </div>
      {hasSlots ? (
        <StageParticipantsEditor
          jobId={jobId}
          stage={{
            stage_type: stage.stage_type,
            participants: participantsForEditor,
          }}
          onChange={(next) => {
            const existingById = new Map(
              participantsForEditor.map((p) => [p.user_id, p]),
            )
            const poolById = new Map(
              (pool ?? []).map((u) => [u.user_id, u]),
            )
            const enriched: StageParticipantResponse[] = next.map((p) => {
              const prior = existingById.get(p.user_id)
              const fromPool = poolById.get(p.user_id)
              return {
                user_id: p.user_id,
                role: p.role,
                full_name: prior?.full_name || fromPool?.full_name || '',
                email: prior?.email || fromPool?.email || '',
              }
            })
            onChange(enriched)
          }}
        />
      ) : (
        <p className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
          {stage.stage_type === 'intake'
            ? 'Intake is an entry point — no interviewers needed.'
            : stage.stage_type === 'take_home'
              ? 'Take-home is coming soon.'
              : 'No staffing required for this stage type.'}
        </p>
      )}
    </div>
  )
}

/* ─── Shared sheet field label ─────────────────────────── */

function SheetField({
  label,
  sub,
  children,
}: {
  label: string
  sub?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div
        className="mb-1.5 text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: '0.5px', color: 'var(--px-fg-4)' }}
      >
        {label}
      </div>
      {children}
      {sub && (
        <div
          className="mt-1.5 text-[11px]"
          style={{ color: 'var(--px-fg-4)' }}
        >
          {sub}
        </div>
      )}
    </div>
  )
}

/* ─── Stage-type glyph for the sheet header icon ─────── */

function StageTypeGlyph({ type }: { type: StageType }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
  switch (type) {
    case 'intake':
      return (
        <svg {...common}>
          <path d="M12 3l9 6-9 6-9-6 9-6z" />
        </svg>
      )
    case 'phone_screen':
      return (
        <svg {...common}>
          <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72c.13.96.37 1.9.72 2.81a2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.91.35 1.85.59 2.81.72A2 2 0 0122 16.92z" />
        </svg>
      )
    case 'ai_screening':
      return (
        <svg {...common}>
          <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
        </svg>
      )
    case 'take_home':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6" />
          <path d="M9 14h6M9 10h6M9 18h3" />
        </svg>
      )
    case 'human_interview':
      return (
        <svg {...common}>
          <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M23 21v-2a4 4 0 00-3-3.87" />
          <path d="M16 3.13a4 4 0 010 7.75" />
        </svg>
      )
    case 'debrief':
      return (
        <svg {...common}>
          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
        </svg>
      )
  }
}
