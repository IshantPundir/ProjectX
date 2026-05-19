'use client'

import { useEffect, useState, type ReactNode } from 'react'

import { useAgentState } from '@/hooks/use-agent-state'

import { IntroLoader } from './intro-loader'

const INTRO_TIMEOUT_MS = 15_000

interface AgentUIWithLoaderProps {
  children: ReactNode
}

/**
 * Gates the live agent UI behind the IntroLoader until the agent emits its
 * first "speaking" attribute, or transitions to an error state after a
 * 15s timeout if the agent never starts speaking.
 *
 * Must be rendered INSIDE a LiveKit RoomContext (e.g. inside
 * <AgentSessionProvider>) because `useAgentState` calls `useRoomContext`.
 *
 * See docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §3.
 */
export function AgentUIWithLoader({ children }: AgentUIWithLoaderProps) {
  const { hasSpoken } = useAgentState()
  const [timedOut, setTimedOut] = useState(false)
  const [shownAt, setShownAt] = useState<number | null>(null)

  useEffect(() => {
    if (hasSpoken || timedOut) return
    const timer = setTimeout(() => {
      setTimedOut(true)
    }, INTRO_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [hasSpoken, timedOut])

  useEffect(() => {
    if (hasSpoken && shownAt !== null) {
      const dismissedAt = Date.now()
      console.log('[intro_loader.dismissed]', {
        shown_at: shownAt,
        dismissed_at: dismissedAt,
        perceived_wait_ms: dismissedAt - shownAt,
      })
    }
  }, [hasSpoken, shownAt])

  if (hasSpoken) {
    return <>{children}</>
  }

  if (timedOut) {
    return (
      <div
        role="alert"
        className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center"
      >
        <p className="text-base text-foreground">
          Sorry — we couldn&apos;t connect you to the interviewer.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded bg-primary px-4 py-2 text-sm text-primary-foreground"
        >
          Try again
        </button>
      </div>
    )
  }

  return (
    <IntroLoader
      onShown={() => {
        const at = Date.now()
        setShownAt(at)
        console.log('[intro_loader.shown]', { at })
      }}
    />
  )
}
