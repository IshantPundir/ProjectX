'use client'

import { useStageProgress } from './hooks/use-stage-progress'

export function ProgressBanner() {
  const p = useStageProgress()
  if (!p) return null
  const minutes = Math.floor(p.timeRemainingSeconds / 60)
  return (
    <div className="sticky top-0 z-10 bg-zinc-50 border-b border-zinc-200 px-6 py-3 text-sm text-zinc-700">
      Q{p.currentQuestion + 1} of {p.totalQuestions} · {minutes} min remaining
    </div>
  )
}
