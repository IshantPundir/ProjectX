'use client'

import { cn } from '@/lib/utils'
import { latestSpokenLine, type RawMessage } from './transcript-model'

export function SpokenCaption({
  messages,
  className,
}: {
  messages: RawMessage[]
  className?: string
}) {
  const line = latestSpokenLine(messages)
  if (!line) return null
  return (
    <div
      className={cn(
        'px-glass max-w-[min(54ch,90vw)] rounded-2xl px-4 py-3 text-center',
        className,
      )}
    >
      <p className="font-serif text-[15px] italic leading-snug text-px-fg">{line}</p>
    </div>
  )
}
