'use client'

import { useEffect, useState } from 'react'
import { useRoomContext } from '@livekit/components-react'
import {
  RoomEvent,
  type LocalParticipant,
  type RemoteParticipant,
} from 'livekit-client'

export type AgentState =
  | 'initializing'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'unknown'

/**
 * Subscribes to the agent participant's `lk.agent.state` attribute.
 * Returns the current state and a `hasSpoken` boolean that flips True
 * on the FIRST transition to "speaking" — used to dismiss the
 * IntroLoader once the agent has started speaking.
 *
 * See docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §3.
 */
export function useAgentState(): {
  state: AgentState
  hasSpoken: boolean
} {
  const room = useRoomContext()
  const [state, setState] = useState<AgentState>('unknown')
  const [hasSpoken, setHasSpoken] = useState(false)

  useEffect(() => {
    if (!room) return

    function getAgentParticipant(): RemoteParticipant | undefined {
      for (const p of room.remoteParticipants.values()) {
        if (p.attributes?.['lk.agent.state'] !== undefined) {
          return p
        }
        if (p.attributes?.role === 'agent') {
          return p
        }
      }
      return undefined
    }

    function readState(p: RemoteParticipant | LocalParticipant) {
      const raw = p.attributes?.['lk.agent.state']
      const next = (raw ?? 'unknown') as AgentState
      setState(next)
      if (next === 'speaking') {
        setHasSpoken(true)
      }
    }

    function handleAttributesChanged(
      _changed: Record<string, string>,
      participant: RemoteParticipant | LocalParticipant,
    ) {
      if (participant === getAgentParticipant()) {
        readState(participant)
      }
    }

    function handleParticipantConnected(p: RemoteParticipant) {
      readState(p)
    }

    // Initial read (in case the agent attribute was already set when we mounted)
    const agent = getAgentParticipant()
    if (agent) readState(agent)

    // Subscribe to changes
    room.on(RoomEvent.ParticipantAttributesChanged, handleAttributesChanged)
    room.on(RoomEvent.ParticipantConnected, handleParticipantConnected)

    return () => {
      room.off(RoomEvent.ParticipantAttributesChanged, handleAttributesChanged)
      room.off(RoomEvent.ParticipantConnected, handleParticipantConnected)
    }
  }, [room])

  return { state, hasSpoken }
}
