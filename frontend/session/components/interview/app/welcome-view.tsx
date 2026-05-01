'use client'

import { Button } from '@/components/ui/button'

interface Props {
  companyName: string
  jobTitle: string
  durationMinutes: number
  startButtonText: string
  mode: 'start' | 'rejoin'
  onStartCall: () => void
  isPending?: boolean
}

export function WelcomeView({
  companyName,
  jobTitle,
  durationMinutes,
  startButtonText,
  mode,
  onStartCall,
  isPending = false,
}: Props) {
  const heading =
    mode === 'rejoin' ? 'Rejoin your interview' : "You're ready to begin"

  const body =
    mode === 'rejoin'
      ? 'You were disconnected. Click rejoin to continue where you left off.'
      : `${companyName} · ${jobTitle} · ${durationMinutes} minutes`

  const buttonLabel = isPending
    ? mode === 'rejoin'
      ? 'Rejoining…'
      : 'Starting…'
    : mode === 'rejoin'
      ? 'Rejoin interview'
      : startButtonText

  return (
    <section className="grid min-h-screen place-items-center bg-background p-6">
      <div className="max-w-md text-center">
        <h1 className="text-3xl font-semibold text-foreground">{heading}</h1>
        <p className="mt-3 text-sm text-muted-foreground">{body}</p>
        <Button
          size="lg"
          onClick={onStartCall}
          disabled={isPending}
          className="mt-8 w-64 rounded-full font-mono text-xs font-bold uppercase tracking-wider"
        >
          {buttonLabel}
        </Button>
      </div>
    </section>
  )
}
