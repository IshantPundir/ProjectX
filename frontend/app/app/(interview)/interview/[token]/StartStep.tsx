'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import type { CandidateSessionError } from '@/lib/api/candidate-session'
import { useStartSession } from '@/lib/hooks/use-start-session'

interface Props {
  token: string
}

function asCandidateError(err: Error): CandidateSessionError | null {
  if (err && typeof err === 'object' && 'status' in err) {
    return err as CandidateSessionError
  }
  return null
}

export function StartStep({ token }: Props) {
  const start = useStartSession(token)
  const [outcome, setOutcome] = useState<'pending' | 'replay' | null>(null)

  const onStart = () => {
    start.mutate(undefined, {
      onSuccess: () => setOutcome('pending'),
      onError: (err) => {
        const ce = asCandidateError(err)
        if (ce?.status === 409 || ce?.code === 'TOKEN_ALREADY_USED') {
          setOutcome('replay')
        } else {
          toast.error(err.message)
        }
      },
    })
  }

  if (outcome === 'pending') {
    return (
      <section
        className="rounded-[12px] border p-10 text-center"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <h2
          className="px-serif m-0 mb-3 text-[28px] font-normal"
          style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
        >
          Interview coming soon
        </h2>
        <p
          className="mx-auto max-w-md text-[14px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
        >
          We&apos;ve received your pre-check. The live interview experience
          rolls out in the next release — we&apos;ll email you when it&apos;s
          ready.
        </p>
      </section>
    )
  }

  if (outcome === 'replay') {
    return (
      <section
        className="rounded-[12px] border p-10 text-center"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <h2
          className="px-serif m-0 mb-3 text-[28px] font-normal"
          style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
        >
          This session has already started
        </h2>
        <p
          className="mx-auto max-w-md text-[14px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
        >
          You&apos;ve already completed the pre-check for this invite. If you
          were disconnected, please contact the recruiter.
        </p>
      </section>
    )
  }

  return (
    <section className="space-y-6">
      <div
        className="rounded-[12px] border p-6"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="mb-2 text-[10.5px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Ready
        </div>
        <h2
          className="px-serif m-0 mb-2 text-[24px] font-normal"
          style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}
        >
          Let&apos;s begin
        </h2>
        <p
          className="mb-4 text-[14px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
        >
          Click <strong>Start interview</strong> when you&apos;re ready. You
          can only start once.
        </p>
        <Button size="lg" onClick={onStart} disabled={start.isPending}>
          {start.isPending ? 'Starting…' : 'Start interview →'}
        </Button>
      </div>
    </section>
  )
}
