'use client'

import { cn } from '@/lib/utils'

export type WizardStepKey = 'consent' | 'otp' | 'cam-mic'

export function WizardStepper({
  current,
  otpRequired,
  className,
}: {
  current: WizardStepKey | 'welcome'
  otpRequired: boolean
  className?: string
}) {
  const steps: { key: WizardStepKey; label: string }[] = [
    { key: 'consent', label: 'Consent' },
    ...(otpRequired ? [{ key: 'otp' as const, label: 'Verify' }] : []),
    { key: 'cam-mic', label: 'Camera & mic' },
  ]
  const currentIdx = steps.findIndex((s) => s.key === current) // -1 for 'welcome'

  return (
    <ol className={cn('flex items-center gap-2', className)} aria-label="Setup progress">
      {steps.map((s, i) => {
        const done = currentIdx > -1 && i < currentIdx
        const active = i === currentIdx
        return (
          <li
            key={s.key}
            data-step={s.key}
            aria-current={active ? 'step' : undefined}
            className="flex items-center gap-2"
          >
            <span
              className={cn(
                'grid size-[18px] place-items-center rounded-full text-[10px] font-semibold',
                done && 'bg-px-accent text-white',
                active && 'bg-px-accent text-white',
                !done && !active && 'bg-px-surface-3 text-px-fg-4',
              )}
            >
              {done ? '✓' : i + 1}
            </span>
            <span
              className={cn(
                'text-[11px] font-medium',
                active ? 'text-px-fg' : 'text-px-fg-4',
              )}
            >
              {s.label}
            </span>
            {i < steps.length - 1 && <span className="h-px w-5 bg-px-hairline" aria-hidden />}
          </li>
        )
      })}
    </ol>
  )
}
