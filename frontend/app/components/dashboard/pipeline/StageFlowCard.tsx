'use client'

import {
  Inbox,
  Phone,
  Bot,
  Users,
  Gavel,
  FileText,
  Trash2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type {
  PipelineStageUpdateInput,
  StageDifficulty,
  StageType,
} from '@/lib/api/pipelines'
import type { BankStatus } from '@/lib/api/question-banks'
import { isStageUnstaffed } from '@/lib/pipelines/categories'

type Props = {
  stage: PipelineStageUpdateInput
  position: number
  selected: boolean
  bankStatus: BankStatus | null
  onClick: () => void
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

function statusDotClass(status: BankStatus | null): string {
  switch (status) {
    case 'draft':
      return 'bg-zinc-300'
    case 'generating':
      return 'bg-blue-400 animate-pulse'
    case 'reviewing':
      return 'bg-amber-400'
    case 'confirmed':
      return 'bg-emerald-500'
    case 'failed':
      return 'bg-red-500'
    default:
      return 'bg-zinc-200'
  }
}

function statusLabel(status: BankStatus | null): string {
  switch (status) {
    case 'draft':
      return 'Draft'
    case 'generating':
      return 'Generating questions'
    case 'reviewing':
      return 'Awaiting review'
    case 'confirmed':
      return 'Confirmed'
    case 'failed':
      return 'Generation failed'
    default:
      return 'No bank yet'
  }
}

export function StageFlowCard({
  stage,
  position,
  selected,
  bankStatus,
  onClick,
  onDelete,
}: Props) {
  const Icon = STAGE_TYPE_ICONS[stage.stage_type] ?? Phone
  const accent = STAGE_TYPE_ACCENT[stage.stage_type] ?? 'bg-zinc-400'
  const typeText = STAGE_TYPE_TEXT[stage.stage_type] ?? 'text-zinc-600'
  const hasId = !!stage.id
  const isGenerating = bankStatus === 'generating'

  const baseClasses = selected
    ? 'border-blue-500 bg-blue-50/50 shadow-md'
    : 'border-zinc-200 bg-white shadow-sm hover:border-zinc-300 hover:shadow-md'

  return (
    <div
      role="button"
      tabIndex={hasId ? 0 : -1}
      aria-pressed={selected}
      aria-disabled={!hasId}
      data-stage-card-id={stage.id ?? ''}
      onClick={() => {
        if (hasId) onClick()
      }}
      onKeyDown={(e) => {
        if (!hasId) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      className={`group relative w-full max-w-[340px] rounded-xl border transition ${baseClasses} ${
        hasId ? 'cursor-pointer' : 'cursor-wait opacity-80'
      } ${
        isGenerating
          ? 'overflow-hidden stage-generating-glow stage-generating-shimmer'
          : ''
      }`}
    >
      {/* Selected accent bar on the left edge */}
      {selected && (
        <div
          className="absolute left-0 top-0 bottom-0 w-1 rounded-l-xl bg-blue-500"
          aria-hidden="true"
        />
      )}

      <div className="flex items-center gap-3 pl-4 pr-2 py-3">
        {/* Position circle */}
        <div
          className={`flex-shrink-0 w-8 h-8 rounded-full ${accent} text-white text-sm font-semibold flex items-center justify-center shadow-sm`}
        >
          {position}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <div className="text-sm font-semibold text-zinc-900 truncate">
              {stage.name}
            </div>
            <span
              className={`flex-shrink-0 w-2 h-2 rounded-full ${statusDotClass(bankStatus)}`}
              aria-label={statusLabel(bankStatus)}
              title={statusLabel(bankStatus)}
            />
          </div>
          <div className="flex items-center gap-1.5 text-xs text-zinc-500 mt-0.5">
            <Icon className={`w-3.5 h-3.5 ${typeText}`} aria-hidden="true" />
            <span className={typeText}>
              {STAGE_TYPE_LABELS[stage.stage_type] ?? stage.stage_type}
            </span>
            <span className="text-zinc-300">·</span>
            {/* TODO(Task 19/25): narrow by stage_type once matrix-driven drawer lands */}
            <span>{(stage as { duration_minutes?: number }).duration_minutes} min</span>
            <span className="text-zinc-300">·</span>
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded-full border text-[10px] font-medium ${DIFFICULTY_CHIP[(stage as { difficulty?: StageDifficulty }).difficulty ?? 'easy']}`}
            >
              {(stage as { difficulty?: StageDifficulty }).difficulty}
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
          {!hasId && (
            <div className="text-[10px] text-zinc-400 mt-1 italic">Saving…</div>
          )}
        </div>

        {/* Delete button (only on hover) */}
        {onDelete && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onDelete()
            }}
            aria-label="Delete stage"
            className="flex-shrink-0 p-1.5 rounded-md text-zinc-400 hover:text-red-600 hover:bg-red-50 opacity-0 group-hover:opacity-100 focus:opacity-100 transition"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Indeterminate progress bar — only while generating */}
      {isGenerating && (
        <div className="stage-generating-progress" aria-hidden="true" />
      )}
    </div>
  )
}
