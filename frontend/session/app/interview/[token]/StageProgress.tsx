// app/interview/[token]/StageProgress.tsx
'use client'

import { cn } from '@/lib/utils'

/**
 * Minimal multi-step indicator (dots + "Step N of M"). Honors the UX rule to
 * show progress in multi-step flows without the heavier numbered stepper.
 */
export function StageProgress({
  steps,
  currentIndex,
  className,
}: {
  steps: string[]
  currentIndex: number
  className?: string
}) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <ol className="flex items-center gap-1.5" aria-label="Setup progress">
        {steps.map((label, i) => {
          const active = i === currentIndex
          const done = i < currentIndex
          return (
            <li
              key={label}
              aria-current={active ? 'step' : undefined}
              className="flex items-center gap-1.5"
            >
              <span
                className={cn(
                  'h-1.5 rounded-full transition-all duration-300',
                  active ? 'w-6 bg-px-accent' : done ? 'w-1.5 bg-px-accent' : 'w-1.5 bg-px-surface-3',
                )}
              />
              <span className="sr-only">{label}</span>
            </li>
          )
        })}
      </ol>
      <span className="text-[11px] font-medium text-px-fg-4">
        Step {currentIndex + 1} of {steps.length}
      </span>
    </div>
  )
}
