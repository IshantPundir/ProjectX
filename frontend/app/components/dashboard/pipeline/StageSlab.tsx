'use client'

import {
  Inbox,
  Phone,
  Bot,
  Users,
  Gavel,
  FileText,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { PipelineStageInput, StageType, StageDifficulty } from '@/lib/api/pipelines'
import { StageActionsMenu } from './StageActionsMenu'
import { isStageUnstaffed } from '@/lib/pipelines/categories'

// StageSlab reads stage metadata for display only — never touches
// participants — so accept the non-participants slice of both Input
// and UpdateInput shapes.
type SlabStage = Omit<PipelineStageInput, 'participants'> & { id?: string }

type Props = {
  stage: SlabStage
  position: number
  selected?: boolean
  onClick?: () => void
  onDelete?: () => void
}

const STAGE_TYPE_LABELS: Record<StageType, string> = {
  intake: 'Intake',
  phone_screen: 'Phone Screen',
  ai_screening: 'AI Screening',
  human_interview: 'Human Interview',
  debrief: 'Debrief',
  take_home: 'Take-home',
}

const STAGE_TYPE_ICONS: Record<StageType, LucideIcon> = {
  intake: Inbox,
  phone_screen: Phone,
  ai_screening: Bot,
  human_interview: Users,
  debrief: Gavel,
  take_home: FileText,
}

const STAGE_TYPE_ACCENT: Record<StageType, string> = {
  intake: 'bg-zinc-400',
  phone_screen: 'bg-blue-500',
  ai_screening: 'bg-violet-500',
  human_interview: 'bg-emerald-500',
  debrief: 'bg-amber-500',
  take_home: 'bg-zinc-300',
}

const STAGE_TYPE_TEXT: Record<StageType, string> = {
  intake: 'text-zinc-600',
  phone_screen: 'text-blue-600',
  ai_screening: 'text-violet-600',
  human_interview: 'text-emerald-600',
  debrief: 'text-amber-600',
  take_home: 'text-zinc-500',
}

const DIFFICULTY_CHIP: Record<StageDifficulty, string> = {
  easy: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  hard: 'bg-red-50 text-red-700 border-red-200',
}

export function StageSlab({ stage, position, selected, onClick, onDelete }: Props) {
  const Icon = STAGE_TYPE_ICONS[stage.stage_type] ?? Phone
  const accent = STAGE_TYPE_ACCENT[stage.stage_type] ?? 'bg-zinc-400'
  const typeText = STAGE_TYPE_TEXT[stage.stage_type] ?? 'text-zinc-600'
  const ring = selected ? 'ring-2 ring-blue-400 ring-offset-2' : ''

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick?.()
        }
      }}
      className={`group relative bg-white border border-zinc-200 rounded-xl shadow-sm hover:shadow-md hover:border-zinc-300 transition cursor-pointer ${ring}`}
    >
      {/* Left accent bar — rounded-l-xl so corners match the card without needing overflow-hidden on the root (which would clip the dropdown menu) */}
      <div className={`absolute left-0 top-0 bottom-0 w-1.5 rounded-l-xl ${accent}`} aria-hidden="true" />

      <div className="flex items-center gap-3 pl-5 pr-2 py-3">
        {/* Number circle */}
        <div
          className={`flex-shrink-0 w-8 h-8 rounded-full ${accent} text-white text-sm font-semibold flex items-center justify-center shadow-sm`}
        >
          {position}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-zinc-900 truncate">{stage.name}</div>
          <div className="flex items-center gap-1.5 text-xs text-zinc-500 mt-0.5">
            <Icon className={`w-3.5 h-3.5 ${typeText}`} />
            <span className={typeText}>{STAGE_TYPE_LABELS[stage.stage_type] ?? stage.stage_type}</span>
            <span className="text-zinc-300">·</span>
            <span>{stage.duration_minutes} min</span>
            <span className="text-zinc-300">·</span>
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded-full border text-[10px] font-medium ${DIFFICULTY_CHIP[stage.difficulty]}`}
            >
              {stage.difficulty}
            </span>
            {isStageUnstaffed(stage) && (
              <>
                <span className="text-zinc-300">·</span>
                <span
                  title="No interviewers/reviewers assigned"
                  className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-100 text-amber-700"
                >
                  Unstaffed
                </span>
              </>
            )}
          </div>
        </div>

        {/* 3-dot actions menu */}
        <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          <StageActionsMenu onEdit={() => onClick?.()} onDelete={onDelete} />
        </div>
      </div>
    </div>
  )
}
