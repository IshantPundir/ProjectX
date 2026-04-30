'use client'

import { useRemoteParticipants, useVoiceAssistant } from '@livekit/components-react'

export type AgentState = 'connecting' | 'listening' | 'thinking' | 'speaking' | 'disconnected'

export function useAgentState(): AgentState {
  const remotes = useRemoteParticipants()
  const va = useVoiceAssistant()
  const hasAgent = remotes.some((p) => p.identity.startsWith('agent-'))
  if (!hasAgent) return 'connecting'
  switch (va.state) {
    case 'speaking':
      return 'speaking'
    case 'thinking':
      return 'thinking'
    case 'listening':
      return 'listening'
    case 'disconnected':
    case 'failed':
      return 'disconnected'
    default:
      // 'idle', 'connecting', 'pre-connect-buffering', 'initializing' — agent is present but not
      // actively interacting; treat as listening for the UI's purposes.
      return 'listening'
  }
}
