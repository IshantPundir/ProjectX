'use client'

import type { PipelineStageInput } from '@/lib/api/pipelines'

type Props = {
  stage: PipelineStageInput
  selected?: boolean
  onClick?: () => void
}

const STAGE_TYPE_LABELS: Record<string, string> = {
  phone_screen: '📞 Phone Screen',
  ai_interview: '🤖 AI Interview',
  human_interview: '👤 Human Interview',
  panel_interview: '👥 Panel',
  take_home: '📝 Take-home',
}

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: 'bg-green-50 text-green-700 border-green-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  hard: 'bg-red-50 text-red-700 border-red-200',
}

export function StageSlab({ stage, selected, onClick }: Props) {
  const border = selected ? 'border-blue-500 ring-2 ring-blue-200' : 'border-zinc-200'
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left bg-white border ${border} rounded-lg px-5 py-3 hover:border-blue-400 transition`}
    >
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-zinc-900">{stage.name}</div>
          <div className="text-xs text-zinc-500 mt-0.5">
            {STAGE_TYPE_LABELS[stage.stage_type] ?? stage.stage_type} · {stage.duration_minutes} min
          </div>
        </div>
        <span
          className={`text-xs font-medium px-2 py-0.5 rounded-full border ${DIFFICULTY_COLORS[stage.difficulty] ?? ''}`}
        >
          {stage.difficulty}
        </span>
      </div>
    </button>
  )
}
