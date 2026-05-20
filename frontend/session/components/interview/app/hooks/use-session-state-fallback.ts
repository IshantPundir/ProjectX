'use client'

import { useEffect, useState } from 'react'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import type { CandidateSessionState } from '@/lib/api/candidate-session'

const POLL_INTERVAL_MS = 5000
const TERMINAL_STATES = new Set(['error', 'completed', 'cancelled', 'terminated'])

/**
 * Polls `/api/candidate-session/{token}/state` every 5s while enabled.
 *
 * Stops polling once a terminal state is observed (error / completed /
 * cancelled). Mirrors the engine's `session_outcome` room attribute path
 * for cases where the engine crashed before publishing the attribute
 * (pre-room-connect failures). When the LK attribute also arrives,
 * OutcomeWatcher's precedence rule wins on whichever surfaces first.
 *
 * Network errors keep the loop alive — transient failures should not
 * blind the candidate to a real engine failure. 4xx surfaces (e.g.
 * `TOKEN_SUPERSEDED`) are handled by the existing token-error landing
 * page, not by this hook.
 */
export function useSessionStateFallback(
  token: string,
  enabled: boolean,
): CandidateSessionState | null {
  const [state, setState] = useState<CandidateSessionState | null>(null)

  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      if (stopped) return
      try {
        const next = await candidateSessionApi.getState(token)
        if (stopped) return
        setState(next)
        if (TERMINAL_STATES.has(next.state)) {
          return  // terminal — exit the loop
        }
      } catch {
        // Network/transient — keep polling.
      }
      if (!stopped) {
        timer = setTimeout(tick, POLL_INTERVAL_MS)
      }
    }
    tick()

    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [token, enabled])

  return state
}
