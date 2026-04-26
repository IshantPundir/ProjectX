'use client'

import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type {
  PipelineStageUpdateInput,
  StageType,
  StageDifficulty,
  AdvanceBehavior,
  PassCriteria,
  SignalFilter,
} from '@/lib/api/pipelines'
import { DifficultySlider } from './DifficultySlider'
import { SignalFilterEditor } from './SignalFilterEditor'
import { PassCriteriaEditor } from './PassCriteriaEditor'
import { StageParticipantsEditor } from './StageParticipantsEditor'
import { stageCategory, participantSlotsFor } from '@/lib/pipelines/categories'

type Props = {
  stage: PipelineStageUpdateInput
  /** When set, enables the participants editor. Omit for template editing. */
  jobId?: string
  onChange: (stage: PipelineStageUpdateInput) => void
  onClose: () => void
}

const STAGE_TYPES: { value: StageType; label: string; disabled?: boolean }[] = [
  { value: 'intake',          label: 'Intake' },
  { value: 'phone_screen',    label: 'Phone Screen' },
  { value: 'ai_screening',    label: 'AI Screening' },
  { value: 'human_interview', label: 'Human Interview' },
  { value: 'debrief',         label: 'Debrief' },
  { value: 'take_home',       label: 'Take-home (Coming soon)', disabled: true },
]

const ADVANCE_BEHAVIORS: { value: AdvanceBehavior; label: string }[] = [
  { value: 'auto_advance', label: 'Auto-advance on pass' },
  { value: 'manual_review', label: 'Manual review' },
]

// Locked field — renders a disabled display chip with a tooltip title.
// Used for review (debrief) stages where pass_criteria and advance_behavior
// are fixed and should not be edited.
function LockedField({
  label,
  value,
  tooltip,
}: {
  label: string
  value: string
  tooltip: string
}) {
  return (
    <div className="space-y-1.5">
      <div
        className="block text-xs font-medium"
        style={{ color: 'var(--px-fg)' }}
      >
        {label}
      </div>
      <div
        aria-disabled="true"
        title={tooltip}
        className="rounded-lg border px-3 py-2 text-sm cursor-not-allowed select-none"
        style={{
          background: 'var(--px-surface-2, #f4f4f5)',
          borderColor: 'var(--px-divider)',
          color: 'var(--px-fg-3)',
        }}
      >
        {value}
      </div>
    </div>
  )
}

export function StageConfigDrawer({ stage, jobId, onChange, onClose }: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  // WCAG 2.4.3: when the modal mounts (parent renders it conditionally
  // when a stage is selected), move focus to the first interactive
  // element so keyboard users start inside the dialog instead of
  // wherever they triggered it from.
  const nameInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameInputRef.current?.focus()
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const category = stageCategory(stage.stage_type)
  const isScreening = category === 'human_led' || category === 'ai_led'
  const isReview = category === 'review'

  // For screening stages we need a narrowed type to access screening-only fields.
  // We cast to a local widened type — this is safe because we only access these
  // fields inside `isScreening` guards below.
  type ScreeningFields = {
    duration_minutes?: number
    difficulty?: StageDifficulty
    signal_filter?: SignalFilter
    pass_criteria?: PassCriteria
    advance_behavior?: AdvanceBehavior
    otp_required?: boolean
  }
  const screeningStage = isScreening ? (stage as PipelineStageUpdateInput & ScreeningFields) : null

  function update(key: string, value: unknown) {
    onChange({ ...stage, [key]: value } as PipelineStageUpdateInput)
  }

  function handleTypeChange(next: StageType) {
    const newSlot = participantSlotsFor(next)[0]?.role ?? null
    const currentParticipants = stage.participants ?? []
    const filtered = newSlot === null
      ? []
      : currentParticipants.filter((p) => p.role === newSlot)
    onChange({ ...stage, stage_type: next, participants: filtered } as PipelineStageUpdateInput)
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="stage-config-heading"
        className="rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col"
        style={{ background: 'var(--px-surface)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-4 border-b"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          {/* TODO(design-review): no px-token equivalent for text-zinc-900 heading */}
          <h3 id="stage-config-heading" className="text-base font-semibold text-zinc-900">
            Configure Stage
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            // TODO(design-review): no px-token equivalent for hover:text-zinc-900 or hover:bg-zinc-100
            className="hover:text-zinc-900 text-xl leading-none p-1 rounded hover:bg-zinc-100 transition"
            style={{ color: 'var(--px-fg-4)' }}
          >
            ×
          </button>
        </div>

        {/* Body — scrollable */}
        <div className="p-5 space-y-5 overflow-y-auto">
          {/* --- Always-visible fields: Name + Stage type --- */}
          <div className="space-y-4">
            {/* Name */}
            <div>
              <label
                htmlFor="stage-name"
                className="block text-xs font-medium mb-1.5"
                style={{ color: 'var(--px-fg)' }}
              >
                Name
              </label>
              <input
                ref={nameInputRef}
                id="stage-name"
                type="text"
                value={stage.name}
                onChange={(e) => update('name', e.target.value)}
                className="w-full text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                style={{ borderColor: 'var(--px-divider)' }}
              />
            </div>

            {/* Stage type */}
            <div>
              <label
                htmlFor="stage-type"
                className="block text-xs font-medium mb-1.5"
                style={{ color: 'var(--px-fg)' }}
              >
                Stage type
              </label>
              <select
                id="stage-type"
                aria-label="Stage type"
                value={stage.stage_type}
                onChange={(e) => handleTypeChange(e.target.value as StageType)}
                className="w-full text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                style={{
                  background: 'var(--px-surface)',
                  borderColor: 'var(--px-divider)',
                }}
              >
                {STAGE_TYPES.map((t) => (
                  <option key={t.value} value={t.value} disabled={t.disabled}>{t.label}</option>
                ))}
              </select>
            </div>

            {/* SLA days — visible on all categories */}
            <div>
              <label
                htmlFor="stage-sla-days"
                className="block text-xs font-medium mb-1.5"
                style={{ color: 'var(--px-fg)' }}
              >
                Stage SLA
              </label>
              <div className="relative">
                <input
                  id="stage-sla-days"
                  type="number"
                  min={1}
                  value={stage.sla_days ?? ''}
                  onChange={(e) => {
                    const v = e.target.value
                    update(
                      'sla_days',
                      v === '' ? null : Math.max(1, parseInt(v) || 1),
                    )
                  }}
                  placeholder="No SLA"
                  className="w-full text-sm border rounded-lg px-3 py-2 pr-14 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                  style={{ borderColor: 'var(--px-divider)' }}
                />
                <span
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-xs pointer-events-none"
                  style={{ color: 'var(--px-fg-4)' }}
                >
                  days
                </span>
              </div>
              <p
                className="text-[11px] mt-1"
                style={{ color: 'var(--px-fg-3)' }}
              >
                Max days a candidate can sit in this stage before being
                flagged stalled. Leave blank for no SLA.
              </p>
            </div>

            {/* --- Screening-only fields: Duration + Difficulty --- */}
            {isScreening && (
              <>
                {/* Duration */}
                <div>
                  <label
                    htmlFor="stage-duration"
                    className="block text-xs font-medium mb-1.5"
                    style={{ color: 'var(--px-fg)' }}
                  >
                    Duration
                  </label>
                  <div className="relative">
                    <input
                      id="stage-duration"
                      type="number"
                      min={1}
                      max={240}
                      value={screeningStage?.duration_minutes ?? ''}
                      onChange={(e) => update('duration_minutes', parseInt(e.target.value) || 1)}
                      className="w-full text-sm border rounded-lg px-3 py-2 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                      style={{ borderColor: 'var(--px-divider)' }}
                    />
                    <span
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-xs pointer-events-none"
                      style={{ color: 'var(--px-fg-4)' }}
                    >
                      min
                    </span>
                  </div>
                </div>

                {/* Difficulty slider */}
                <div>
                  <label
                    htmlFor="stage-difficulty"
                    className="block text-xs font-medium mb-2"
                    style={{ color: 'var(--px-fg)' }}
                  >
                    Difficulty
                  </label>
                  <DifficultySlider
                    id="stage-difficulty"
                    value={screeningStage?.difficulty ?? 'easy'}
                    onChange={(v) => update('difficulty', v as StageDifficulty)}
                  />
                </div>
              </>
            )}
          </div>

          {/* --- Review (debrief) locked fields --- */}
          {isReview && (
            <div
              className="border-t pt-4 space-y-4"
              style={{ borderColor: 'var(--px-hairline)' }}
            >
              <LockedField
                label="Pass criteria"
                value="Manual review (HM decides)"
                tooltip="Debrief is always manual review — the hiring manager makes the final call."
              />
              <LockedField
                label="Advance behavior"
                value="Manual review (terminal)"
                tooltip="Debrief is the final decision step and cannot auto-advance."
              />
            </div>
          )}

          {/* --- Participants editor --- */}
          {jobId !== undefined && participantSlotsFor(stage.stage_type).length > 0 && (
            <div
              className="border-t pt-4"
              style={{ borderColor: 'var(--px-hairline)' }}
            >
              <StageParticipantsEditor
                jobId={jobId}
                stage={{
                  stage_type: stage.stage_type,
                  // Drawer's stage is PipelineStageUpdateInput (input shape).
                  // For display we synthesize participant-response-shape using what
                  // the drawer has. Parent components that have richer data may pass
                  // enhanced stage objects; for now we fall back to empty display
                  // fields — the combobox populates new adds with real names.
                  participants: (stage.participants ?? []).map((p) => ({
                    ...p,
                    full_name: '',
                    email: '',
                  })),
                }}
                onChange={(next) => onChange({ ...stage, participants: next })}
              />
            </div>
          )}

          {/* --- Screening-only: Pass criteria, Advance behavior, Signal types --- */}
          {isScreening && (
            <div
              className="border-t pt-4 space-y-4"
              style={{ borderColor: 'var(--px-hairline)' }}
            >
              {/* Advance behavior */}
              <div>
                <label
                  htmlFor="stage-advance"
                  className="block text-xs font-medium mb-1.5"
                  style={{ color: 'var(--px-fg)' }}
                >
                  Advance behavior
                </label>
                <select
                  id="stage-advance"
                  aria-label="Advance behavior"
                  value={screeningStage?.advance_behavior ?? 'auto_advance'}
                  onChange={(e) => update('advance_behavior', e.target.value as AdvanceBehavior)}
                  className="w-full text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                  style={{
                    background: 'var(--px-surface)',
                    borderColor: 'var(--px-divider)',
                  }}
                >
                  {ADVANCE_BEHAVIORS.map((a) => (
                    <option key={a.value} value={a.value}>{a.label}</option>
                  ))}
                </select>
              </div>

              {/* Pass criteria */}
              <div>
                <div
                  className="block text-xs font-medium mb-1.5"
                  style={{ color: 'var(--px-fg)' }}
                >
                  Pass criteria
                </div>
                <PassCriteriaEditor
                  value={screeningStage?.pass_criteria ?? { type: 'all_knockouts_pass' }}
                  onChange={(pc) => update('pass_criteria', pc)}
                />
              </div>

              {/* Signal types */}
              <div>
                <div
                  className="block text-xs font-medium mb-1.5"
                  style={{ color: 'var(--px-fg)' }}
                >
                  Signal types
                </div>
                <SignalFilterEditor
                  value={screeningStage?.signal_filter ?? { include_types: [] }}
                  onChange={(sf) => update('signal_filter', sf)}
                />
              </div>
            </div>
          )}

          {/* --- Screening-only Advanced section (OTP and secondary options) --- */}
          {/* Not rendered at all for IO/review/disabled categories */}
          {isScreening && (
            <div
              className="border-t pt-4"
              style={{ borderColor: 'var(--px-hairline)' }}
            >
              <button
                type="button"
                onClick={() => setAdvancedOpen((v) => !v)}
                aria-expanded={advancedOpen}
                aria-controls="advanced-section"
                // TODO(design-review): no px-token equivalent for hover:text-zinc-900
                className="w-full flex items-center justify-between text-xs font-medium hover:text-zinc-900 transition"
                style={{ color: 'var(--px-fg)' }}
              >
                <span>Advanced settings</span>
                <ChevronDown
                  className={`w-4 h-4 transition-transform duration-200 ${advancedOpen ? 'rotate-180' : ''}`}
                />
              </button>

              {advancedOpen && (
                <div id="advanced-section" className="mt-4 space-y-4">
                  {/* OTP required toggle placeholder — Phase 3 */}
                  <p
                    className="text-xs"
                    style={{ color: 'var(--px-fg-3)' }}
                  >
                    Additional stage options (e.g. OTP verification) will appear here.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
