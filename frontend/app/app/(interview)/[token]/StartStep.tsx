'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
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
      <section className="rounded-lg border border-zinc-200 bg-white p-8 text-center">
        <h2 className="text-xl font-semibold">
          Interview integration coming soon
        </h2>
        <p className="mt-3 text-sm text-zinc-600">
          We&apos;ve received your pre-check. The live interview experience
          rolls out in the next release — we&apos;ll email you when it&apos;s
          ready.
        </p>
      </section>
    )
  }

  if (outcome === 'replay') {
    return (
      <section className="rounded-lg border border-zinc-200 bg-white p-8 text-center">
        <h2 className="text-xl font-semibold">
          This session has already started
        </h2>
        <p className="mt-3 text-sm text-zinc-600">
          You&apos;ve already completed the pre-check for this invite. If you
          were disconnected, please contact the recruiter.
        </p>
      </section>
    )
  }

  return (
    <section className="space-y-6">
      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <h2 className="text-lg font-semibold">Ready to begin</h2>
        <p className="mt-2 text-sm text-zinc-600">
          Click <strong>Start Interview</strong> when you&apos;re ready. You can
          only start once.
        </p>
        <Button onClick={onStart} disabled={start.isPending} className="mt-4">
          {start.isPending ? 'Starting…' : 'Start Interview'}
        </Button>
      </div>
    </section>
  )
}
