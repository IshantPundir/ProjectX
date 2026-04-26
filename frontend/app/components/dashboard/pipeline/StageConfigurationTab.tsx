'use client'

import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type {
  AdvanceBehavior,
  PipelineStageUpdateInput,
  StageDifficulty,
  StageParticipantInput,
  StageType,
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

export function StageConfigurationTab({ stage, jobId, onChange }: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false)

  // TODO(Task 20): replace with per-category field rendering once the
  // matrix-driven tab refactor lands. Until then, we operate on a
  // loose stage object and cast back to the discriminated union type.
  type LooseStage = Record<string, unknown> & { id?: string }
  const loose = stage as LooseStage

  function update(key: string, value: unknown) {
    onChange({ ...stage, [key]: value } as PipelineStageUpdateInput)
  }

  function handleTypeChange(next: StageType) {
    const newSlot = participantSlotsFor(next)[0]?.role ?? null
    const currentParticipants = stage.participants ?? []
    const filtered: StageParticipantInput[] = newSlot === null
      ? []
      : currentParticipants.filter((p) => p.role === newSlot)
    onChange({ ...stage, stage_type: next, participants: filtered } as PipelineStageUpdateInput)
  }

  return (
    <div className="p-6 space-y-5 max-w-2xl">
      {/* --- Basic section --- */}
      <div className="space-y-4">
        {/* Name */}
        <div>
          <label
            htmlFor="stage-name"
            className="block text-xs font-medium text-zinc-700 mb-1.5"
          >
            Name
          </label>
          <input
            id="stage-name"
            type="text"
            value={stage.name}
            onChange={(e) => update('name', e.target.value)}
            className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
          />
        </div>

        {/* Stage type */}
        <div>
          <label
            htmlFor="stage-type"
            className="block text-xs font-medium text-zinc-700 mb-1.5"
          >
            Stage type
          </label>
          <select
            id="stage-type"
            value={stage.stage_type}
            onChange={(e) => handleTypeChange(e.target.value as StageType)}
            className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
          >
            {STAGE_TYPES.map((t) => (
              <option key={t.value} value={t.value} disabled={t.disabled}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {/* Duration */}
        <div>
          <label
            htmlFor="stage-duration"
            className="block text-xs font-medium text-zinc-700 mb-1.5"
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
              onChange={(e) =>
                update('duration_minutes', parseInt(e.target.value) || 1)
              }
              className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-zinc-400 pointer-events-none">
              min
            </span>
          </div>
        </div>

        {/* Difficulty slider */}
        <div>
          <label
            htmlFor="stage-difficulty"
            className="block text-xs font-medium text-zinc-700 mb-2"
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
        <div className="border-t border-zinc-100 pt-4">
          <StageParticipantsEditor
            jobId={jobId}
            stage={{
              stage_type: stage.stage_type,
              participants: (stage.participants ?? []).map((p) => ({
                ...p,
                full_name: '',
                email: '',
              })),
            }}
            onChange={(next) => onChange({ ...stage, participants: next } as PipelineStageUpdateInput)}
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
            className={`w-4 h-4 transition-transform duration-200 ${
              advancedOpen ? 'rotate-180' : ''
            }`}
          />
        </button>

        {advancedOpen && (
          <div id="advanced-section" className="mt-4 space-y-4">
            {/* Advance behavior */}
            <div>
              <label
                htmlFor="stage-advance"
                className="block text-xs font-medium text-zinc-700 mb-1.5"
              >
                Advance behavior
              </label>
              <select
                id="stage-advance"
                value={(loose.advance_behavior as AdvanceBehavior | undefined) ?? 'auto_advance'}
                onChange={(e) =>
                  update('advance_behavior', e.target.value as AdvanceBehavior)
                }
                className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
              >
                {ADVANCE_BEHAVIORS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Pass criteria */}
            <div>
              <div className="block text-xs font-medium text-zinc-700 mb-1.5">
                Pass criteria
              </div>
              <PassCriteriaEditor
                value={(loose.pass_criteria as PassCriteria) ?? { type: 'all_knockouts_pass' }}
                onChange={(pc) => update('pass_criteria', pc)}
              />
            </div>

            {/* Signal types */}
            <div>
              <div className="block text-xs font-medium text-zinc-700 mb-1.5">
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
  )
}
