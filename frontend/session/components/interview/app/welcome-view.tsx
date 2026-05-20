'use client'

import { Button } from '@/components/ui/button'
import { Aura } from '@/components/agents-ui/aura'

interface Props {
  companyName: string
  jobTitle: string
  durationMinutes: number
  startButtonText: string
  mode: 'start' | 'rejoin'
  onStartCall: () => void
  isPending?: boolean
  proctored?: boolean
}

export function WelcomeView({
  companyName,
  jobTitle,
  durationMinutes,
  startButtonText,
  mode,
  onStartCall,
  isPending = false,
  proctored = false,
}: Props) {
  const heading = mode === 'rejoin' ? 'Rejoin your interview' : "You're ready to begin"
  const body =
    mode === 'rejoin'
      ? 'You were disconnected. Click rejoin to continue where you left off.'
      : `${companyName} · ${jobTitle} · ${durationMinutes} minutes`
  const buttonLabel = isPending
    ? mode === 'rejoin' ? 'Rejoining…' : 'Starting…'
    : mode === 'rejoin' ? 'Rejoin interview' : startButtonText

  return (
    <section className="px-cine-bg grid min-h-screen place-items-center p-6">
      <div className="flex max-w-md flex-col items-center text-center">
        <Aura state="listening" audioTrack={undefined} size="xl" className="mb-6" />
        <h1 className="font-serif text-3xl text-px-fg">{heading}</h1>
        <p className="mt-3 text-sm text-px-fg-3">{body}</p>
        {proctored && (
          <div className="px-glass mt-6 rounded-xl px-5 py-4 text-left">
            <p className="text-xs font-semibold uppercase tracking-wider text-px-fg-2">
              This interview is monitored
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-px-fg-3">
              <li>Stay in fullscreen for the whole interview.</li>
              <li>Don&apos;t switch tabs or leave this window.</li>
              <li>Keyboard use and developer tools are detected.</li>
              <li>Repeated or serious violations will end your interview.</li>
            </ul>
          </div>
        )}
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
