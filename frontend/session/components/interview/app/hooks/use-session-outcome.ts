'use client'

import { useRef } from 'react'
import { useRemoteParticipants } from '@livekit/components-react'

import { isSessionOutcome, type SessionOutcome } from '../../lib/session-outcome'

/**
 * Reads the agent participant's `session_outcome` attribute and holds it in a ref
 * so the value survives the moment the agent participant is removed from the
 * remote participants list (which happens immediately on Disconnected).
 *
 * The ref is updated synchronously during render (not in useEffect) so the
 * value is available on the same render in which the agent is visible.
 * Once set, it is never clobbered back to null — last seen value sticks even
 * after the participant is removed from the list.
 *
 * Engine writes one of 6 SessionOutcome values before calling shutdown; see
 * docs/superpowers/specs/2026-05-03-engine-redesign-phase-5-knockout-policy-design.md
 * §3.6.
 *
 * Defensive: an unrecognized outcome string is dropped to null rather than
 * coerced. Defends against backend/frontend version skew.
 */
export function useSessionOutcome(): SessionOutcome | null {
  const remotes = useRemoteParticipants()
  const ref = useRef<SessionOutcome | null>(null)

  const agent = remotes.find((p) => p.identity.startsWith('agent-'))
  const raw = agent?.attributes?.['session_outcome']
  if (raw && isSessionOutcome(raw)) ref.current = raw

  return ref.current
}
