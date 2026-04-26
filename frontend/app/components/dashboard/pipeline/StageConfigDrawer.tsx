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
import { participantSlotsFor } from '@/lib/pipelines/categories'

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

  // TODO(Task 19): replace with per-category field rendering once the
  // matrix-driven drawer refactor lands. Until then, we operate on a
  // loose stage object and cast back to the discriminated union type.
  type LooseStage = Record<string, unknown> & { id?: string }
  const loose = stage as LooseStage

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
          {/* --- Basic section --- */}
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
                  value={(loose.duration_minutes as number | undefined) ?? ''}
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
                value={(loose.difficulty as StageDifficulty | undefined) ?? 'easy'}
                onChange={(v) => update('difficulty', v as StageDifficulty)}
              />
            </div>
          </div>

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

          {/* --- Advanced section --- */}
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
                {/* SLA days — per-stage candidate dwell limit */}
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
                    value={(loose.advance_behavior as AdvanceBehavior | undefined) ?? 'auto_advance'}
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
                    value={(loose.pass_criteria as PassCriteria) ?? { type: 'all_knockouts_pass' }}
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
                    value={(loose.signal_filter as SignalFilter) ?? { include_types: [] }}
                    onChange={(sf) => update('signal_filter', sf)}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
