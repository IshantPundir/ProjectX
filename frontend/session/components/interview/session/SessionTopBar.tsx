'use client'

import { cn } from '@/lib/utils'
import { EndInterviewControl } from './EndInterviewControl'

export function SessionTopBar({
  companyName,
  jobTitle,
  logo,
  onEnd,
  className,
}: {
  companyName: string
  jobTitle: string
  logo?: string
  onEnd: () => void
  className?: string
}) {
  return (
    <header className={cn('flex items-center justify-between gap-3', className)}>
      <div className="flex items-center gap-2 text-xs font-semibold text-px-fg">
        {logo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={logo} alt="" className="size-5 rounded-md" />
        ) : (
          <span className="grid size-5 place-items-center rounded-md bg-px-accent text-[10px] font-bold text-white">
            {companyName.slice(0, 1).toUpperCase()}
          </span>
        )}
        <span className="max-w-[40vw] truncate">
          {companyName} · {jobTitle}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="px-glass-pill flex items-center gap-1.5 px-2.5 py-1.5 text-[10px] font-semibold text-px-fg-2">
          <span className="size-1.5 animate-pulse rounded-full bg-px-danger motion-reduce:animate-none" aria-hidden />
          Recording
        </span>
        <EndInterviewControl onEnd={onEnd} />
      </div>
    </header>
  )
}
