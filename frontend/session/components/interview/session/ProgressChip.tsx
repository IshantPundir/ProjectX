'use client'

import { cn } from '@/lib/utils'
import { useStageProgress } from '@/components/interview/app/hooks/use-stage-progress'
import { formatClock, questionLabel } from './format-progress'

export function ProgressChip({ className }: { className?: string }) {
  const p = useStageProgress()
  if (!p) return null
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'px-glass-pill flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-px-fg-2',
        className,
      )}
    >
      <span className="font-semibold text-px-fg">{questionLabel(p.currentQuestion, p.totalQuestions)}</span>
      <span aria-hidden className="opacity-40">·</span>
      <span className="font-mono tabular-nums">{formatClock(p.timeRemainingSeconds)} left</span>
    </div>
  )
}
