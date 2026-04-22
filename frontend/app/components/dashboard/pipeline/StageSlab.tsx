'use client'

import {
  Bot,
  Briefcase,
  CheckCircle2,
  FileText,
  Gift,
  MessageSquare,
  Phone,
  User,
  Users,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { PipelineStageInput, StageType, StageDifficulty } from '@/lib/api/pipelines'
import { StageActionsMenu } from './StageActionsMenu'

type Props = {
  stage: PipelineStageInput
  position: number
  selected?: boolean
  onClick?: () => void
  onDelete?: () => void
}

const STAGE_TYPE_LABELS: Record<StageType, string> = {
  phone_screen: 'Phone Screen',
  ai_interview: 'AI Interview',
  human_interview: 'Human Interview',
  panel_interview: 'Panel',
  take_home: 'Take-home',
  intake: 'Intake',
  recruiter: 'Recruiter Screen',
  debrief: 'Debrief',
  offer: 'Offer',
}

const STAGE_TYPE_ICONS: Record<StageType, LucideIcon> = {
  phone_screen: Phone,
  ai_interview: Bot,
  human_interview: User,
  panel_interview: Users,
  take_home: FileText,
  intake: Briefcase,
  recruiter: MessageSquare,
  debrief: CheckCircle2,
  offer: Gift,
}

const STAGE_TYPE_ACCENT: Record<StageType, string> = {
  phone_screen: 'bg-blue-500',
  ai_interview: 'bg-violet-500',
  human_interview: 'bg-amber-500',
  panel_interview: 'bg-orange-500',
  take_home: 'bg-emerald-500',
  intake: 'bg-zinc-500',
  recruiter: 'bg-blue-500',
  debrief: 'bg-emerald-500',
  offer: 'bg-amber-500',
}

const STAGE_TYPE_TEXT: Record<StageType, string> = {
  phone_screen: 'text-blue-600',
  ai_interview: 'text-violet-600',
  human_interview: 'text-amber-600',
  panel_interview: 'text-orange-600',
  take_home: 'text-emerald-600',
  intake: 'text-zinc-600',
  recruiter: 'text-blue-600',
  debrief: 'text-emerald-600',
  offer: 'text-amber-600',
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
