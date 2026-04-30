'use client'

import { useRemoteParticipants } from '@livekit/components-react'
import { useEffect, useRef } from 'react'

interface Opts {
  graceMs: number
}

export function useAgentGraceTimeout(onNoShow: () => void, { graceMs }: Opts) {
  const remotes = useRemoteParticipants()
  const remotesRef = useRef(remotes)
  const firedRef = useRef(false)

  useEffect(() => {
    remotesRef.current = remotes
  }, [remotes])

  useEffect(() => {
    const t = setTimeout(() => {
      if (firedRef.current) return
      const hasAgent = remotesRef.current.some((p) =>
        p.identity.startsWith('agent-'),
      )
      if (!hasAgent) {
        firedRef.current = true
        onNoShow()
      }
    }, graceMs)
    return () => clearTimeout(t)
    // remotes intentionally omitted: read from remotesRef at fire-time so
    // unrelated participant events don't reset the grace deadline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graceMs, onNoShow])
}
