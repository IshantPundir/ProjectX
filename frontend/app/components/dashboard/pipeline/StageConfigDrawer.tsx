'use client'

import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { PipelineStageInput, PipelineStageUpdateInput, StageType, StageDifficulty, AdvanceBehavior } from '@/lib/api/pipelines'
import { DifficultySlider } from './DifficultySlider'
import { SignalFilterEditor } from './SignalFilterEditor'
import { PassCriteriaEditor } from './PassCriteriaEditor'

type Props = {
  stage: PipelineStageUpdateInput
  onChange: (stage: PipelineStageUpdateInput) => void
  onClose: () => void
}

const STAGE_TYPES: { value: StageType; label: string }[] = [
  { value: 'phone_screen', label: 'Phone Screen' },
  { value: 'ai_interview', label: 'AI Interview' },
  { value: 'human_interview', label: 'Human Interview' },
  { value: 'panel_interview', label: 'Panel Interview' },
  { value: 'take_home', label: 'Take-home' },
]

const ADVANCE_BEHAVIORS: { value: AdvanceBehavior; label: string }[] = [
  { value: 'auto_advance', label: 'Auto-advance on pass' },
  { value: 'manual_review', label: 'Manual review' },
]

export function StageConfigDrawer({ stage, onChange, onClose }: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false)

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
                onChange={(e) => update('stage_type', e.target.value as StageType)}
                className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
              >
                {STAGE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
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
