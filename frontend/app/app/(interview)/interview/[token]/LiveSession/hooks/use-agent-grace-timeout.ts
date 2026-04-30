'use client'

import { useRemoteParticipants } from '@livekit/components-react'
import { useEffect, useRef } from 'react'

interface Opts {
  graceMs: number
}

export function useAgentGraceTimeout(onNoShow: () => void, { graceMs }: Opts) {
  const remotes = useRemoteParticipants()
  const firedRef = useRef(false)

  useEffect(() => {
    if (firedRef.current) return
    const t = setTimeout(() => {
      const hasAgent = remotes.some((p) => p.identity.startsWith('agent-'))
      if (!hasAgent) {
        firedRef.current = true
        onNoShow()
      }
    }, graceMs)
    return () => clearTimeout(t)
  }, [graceMs, onNoShow, remotes])
}
