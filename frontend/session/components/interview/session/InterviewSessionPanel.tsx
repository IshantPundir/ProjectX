'use client'

import { useState } from 'react'
import { Minus } from 'lucide-react'
import type { AgentState } from '@livekit/components-react'

import { cn } from '@/lib/utils'
import { toTurns, type RawMessage } from './transcript-model'

function LivePill() {
  return (
    <span className="flex items-center gap-1.5 rounded-full border border-px-ok-line bg-px-ok-bg px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-px-ok">
      <span className="size-1.5 rounded-full bg-px-ok" aria-hidden />
      Live
    </span>
  )
}

export function InterviewSessionPanel({
  messages,
  agentState,
  className,
}: {
  messages: RawMessage[]
  agentState?: AgentState
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const turns = toTurns(messages)

  if (!open) {
    return (
      <button
        type="button"
        aria-label="Open transcript"
        onClick={() => setOpen(true)}
        className={cn(
          'px-glass-pill flex items-center gap-2 px-3 py-2 text-px-fg transition-colors hover:bg-px-glass-bg-strong',
          className,
        )}
      >
        <span aria-hidden className="aura-mark block size-[22px]" />
        <span className="font-serif text-sm italic">Interview Session</span>
        <LivePill />
      </button>
    )
  }

  return (
    <section
      aria-label="Interview Session transcript"
      className={cn('px-glass flex flex-col overflow-hidden rounded-2xl', className)}
    >
      <header className="flex items-center gap-2 border-b border-px-hairline px-3 py-2.5">
        <span aria-hidden className="aura-mark block size-[22px]" />
        <span className="font-serif text-sm italic text-px-fg">Interview Session</span>
        <LivePill />
        <button
          type="button"
          aria-label="Minimize transcript"
          onClick={() => setOpen(false)}
          className="ml-auto grid size-6 place-items-center rounded-md border border-px-hairline text-px-fg-3 hover:text-px-fg"
        >
          <Minus className="size-3.5" />
        </button>
      </header>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto px-3 py-3">
        {turns.map((t) => (
          <div key={t.id} className={cn('flex', t.who === 'you' && 'justify-end')}>
            <div
              className={cn(
                'max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-snug',
                t.who === 'ai'
                  ? 'rounded-bl-sm bg-px-surface text-px-fg'
                  : 'rounded-br-sm border border-px-accent-line bg-px-accent-tint text-px-fg',
              )}
            >
              <div className="mb-0.5 text-[9px] font-bold uppercase tracking-wide opacity-60">
                {t.who === 'ai' ? 'Interviewer' : 'You (heard)'}
              </div>
              {t.text}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
