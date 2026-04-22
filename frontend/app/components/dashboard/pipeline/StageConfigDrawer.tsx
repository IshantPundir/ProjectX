'use client'

import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { PipelineStageInput, PipelineStageUpdateInput, StageType, StageDifficulty, AdvanceBehavior } from '@/lib/api/pipelines'
import { DifficultySlider } from './DifficultySlider'
import { SignalFilterEditor } from './SignalFilterEditor'
import { PassCriteriaEditor } from './PassCriteriaEditor'
import { StageParticipantsEditor } from './StageParticipantsEditor'
import { participantSlotsFor } from '@/lib/pipelines/categories'
import type { StageParticipantInput } from '@/lib/api/pipelines'

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

  function update<K extends keyof PipelineStageInput>(key: K, value: PipelineStageInput[K]) {
    onChange({ ...stage, [key]: value })
  }

  function handleTypeChange(next: StageType) {
    const newSlot = participantSlotsFor(next)[0]?.role ?? null
    const currentParticipants = stage.participants ?? []
    const filtered = newSlot === null
      ? []
      : currentParticipants.filter((p) => p.role === newSlot)
    onChange({ ...stage, stage_type: next, participants: filtered })
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
        className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h3 id="stage-config-heading" className="text-base font-semibold text-zinc-900">
            Configure Stage
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="text-zinc-400 hover:text-zinc-900 text-xl leading-none p-1 rounded hover:bg-zinc-100 transition"
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
              <label htmlFor="stage-name" className="block text-xs font-medium text-zinc-700 mb-1.5">
                Name
              </label>
              <input
                ref={nameInputRef}
                id="stage-name"
                type="text"
                value={stage.name}
                onChange={(e) => update('name', e.target.value)}
                className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
              />
            </div>

            {/* Stage type */}
            <div>
              <label htmlFor="stage-type" className="block text-xs font-medium text-zinc-700 mb-1.5">
                Stage type
              </label>
              <select
                id="stage-type"
                value={stage.stage_type}
                onChange={(e) => handleTypeChange(e.target.value as StageType)}
                className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
              >
                {STAGE_TYPES.map((t) => (
                  <option key={t.value} value={t.value} disabled={t.disabled}>{t.label}</option>
                ))}
              </select>
            </div>

            {/* Duration */}
            <div>
              <label htmlFor="stage-duration" className="block text-xs font-medium text-zinc-700 mb-1.5">
                Duration
              </label>
              <div className="relative">
                <input
                  id="stage-duration"
                  type="number"
                  min={1}
                  max={240}
                  value={stage.duration_minutes}
                  onChange={(e) => update('duration_minutes', parseInt(e.target.value) || 1)}
                  className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-zinc-400 pointer-events-none">
                  min
                </span>
              </div>
            </div>

            {/* Difficulty slider */}
            <div>
              <label htmlFor="stage-difficulty" className="block text-xs font-medium text-zinc-700 mb-2">
                Difficulty
              </label>
              <DifficultySlider
                id="stage-difficulty"
                value={stage.difficulty}
                onChange={(v) => update('difficulty', v as StageDifficulty)}
              />
            </div>
          </div>

          {/* --- Participants editor --- */}
          {jobId !== undefined && participantSlotsFor(stage.stage_type).length > 0 && (
            <div className="border-t border-zinc-100 pt-4">
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
          <div className="border-t border-zinc-100 pt-4">
            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              aria-expanded={advancedOpen}
              aria-controls="advanced-section"
              className="w-full flex items-center justify-between text-xs font-medium text-zinc-700 hover:text-zinc-900 transition"
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
                  <label htmlFor="stage-sla-days" className="block text-xs font-medium text-zinc-700 mb-1.5">
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
                      className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 pr-14 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                    />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-zinc-400 pointer-events-none">
                      days
                    </span>
                  </div>
                  <p className="text-[11px] text-zinc-500 mt-1">
                    Max days a candidate can sit in this stage before being
                    flagged stalled. Leave blank for no SLA.
                  </p>
                </div>

                {/* Advance behavior */}
                <div>
                  <label htmlFor="stage-advance" className="block text-xs font-medium text-zinc-700 mb-1.5">
                    Advance behavior
                  </label>
                  <select
                    id="stage-advance"
                    value={stage.advance_behavior}
                    onChange={(e) => update('advance_behavior', e.target.value as AdvanceBehavior)}
                    className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                  >
                    {ADVANCE_BEHAVIORS.map((a) => (
                      <option key={a.value} value={a.value}>{a.label}</option>
                    ))}
                  </select>
                </div>

                {/* Pass criteria */}
                <div>
                  <div className="block text-xs font-medium text-zinc-700 mb-1.5">Pass criteria</div>
                  <PassCriteriaEditor
                    value={stage.pass_criteria}
                    onChange={(pc) => update('pass_criteria', pc)}
                  />
                </div>

                {/* Signal types */}
                <div>
                  <div className="block text-xs font-medium text-zinc-700 mb-1.5">Signal types</div>
                  <SignalFilterEditor
                    value={stage.signal_filter}
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
