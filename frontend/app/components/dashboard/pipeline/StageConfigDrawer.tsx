'use client'

import type { PipelineStageInput, StageType, StageDifficulty, AdvanceBehavior } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { SignalFilterEditor } from './SignalFilterEditor'
import { PassCriteriaEditor } from './PassCriteriaEditor'

type Props = {
  stage: PipelineStageInput
  onChange: (stage: PipelineStageInput) => void
  onClose: () => void
  onDelete?: () => void
}

const STAGE_TYPES: StageType[] = [
  'phone_screen',
  'ai_interview',
  'human_interview',
  'panel_interview',
  'take_home',
]
const DIFFICULTIES: StageDifficulty[] = ['easy', 'medium', 'hard']
const ADVANCE_BEHAVIORS: AdvanceBehavior[] = ['auto_advance', 'manual_review']

export function StageConfigDrawer({ stage, onChange, onClose, onDelete }: Props) {
  function update<K extends keyof PipelineStageInput>(key: K, value: PipelineStageInput[K]) {
    onChange({ ...stage, [key]: value })
  }

  return (
    <aside className="fixed right-0 top-0 h-screen w-96 bg-white border-l border-zinc-200 shadow-xl z-50 overflow-y-auto">
      <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
        <h3 className="text-sm font-semibold">Configure Stage</h3>
        <button onClick={onClose} className="text-zinc-400 hover:text-zinc-900 text-xl leading-none">
          ×
        </button>
      </div>
      <div className="p-5 space-y-4">
        <div>
          <label className="text-xs font-medium text-zinc-700">Name</label>
          <input
            value={stage.name}
            onChange={(e) => update('name', e.target.value)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          />
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Stage type</label>
          <select
            value={stage.stage_type}
            onChange={(e) => update('stage_type', e.target.value as StageType)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          >
            {STAGE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Duration (minutes)</label>
          <input
            type="number"
            min={1}
            max={240}
            value={stage.duration_minutes}
            onChange={(e) => update('duration_minutes', parseInt(e.target.value) || 1)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          />
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Difficulty</label>
          <select
            value={stage.difficulty}
            onChange={(e) => update('difficulty', e.target.value as StageDifficulty)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          >
            {DIFFICULTIES.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Advance behavior</label>
          <select
            value={stage.advance_behavior}
            onChange={(e) => update('advance_behavior', e.target.value as AdvanceBehavior)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          >
            {ADVANCE_BEHAVIORS.map((a) => (
              <option key={a} value={a}>
                {a === 'auto_advance' ? 'Auto-advance on pass' : 'Manual review'}
              </option>
            ))}
          </select>
        </div>

        <div>
          <div className="text-xs font-medium text-zinc-700 mb-1">Signal filter</div>
          <SignalFilterEditor
            value={stage.signal_filter}
            onChange={(sf) => update('signal_filter', sf)}
          />
        </div>

        <div>
          <div className="text-xs font-medium text-zinc-700 mb-1">Pass criteria</div>
          <PassCriteriaEditor
            value={stage.pass_criteria}
            onChange={(pc) => update('pass_criteria', pc)}
          />
        </div>

        {onDelete && (
          <div className="pt-3 border-t border-zinc-100">
            <Button variant="destructive" size="sm" onClick={onDelete}>
              Delete stage
            </Button>
          </div>
        )}
      </div>
    </aside>
  )
}
