'use client'

import { useRef } from 'react'
import { useRemoteParticipants } from '@livekit/components-react'

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
 * Engine writes 'completed' or 'error' before calling shutdown; see
 * docs/superpowers/specs/2026-04-30-livekit-frontend-template-port-design.md
 * § "Graceful disconnect signal".
 */
export function useSessionOutcome(): string | null {
  const remotes = useRemoteParticipants()
  const ref = useRef<string | null>(null)

  const agent = remotes.find((p) => p.identity.startsWith('agent-'))
  const outcome = agent?.attributes?.['session_outcome']
  if (outcome) ref.current = outcome

  return ref.current
}
