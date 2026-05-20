'use client'

import { useEffect, useRef } from 'react'
import type { Room } from 'livekit-client'

/**
 * Mic + camera are always on for the candidate (no toggle UI). Once the room
 * is connected, enable both publications. Idempotent — runs at most once per
 * connect. Camera failure is swallowed (SelfView shows a placeholder); mic
 * failure is logged but does not crash the session. Audio constraints come
 * from room.options.audioCaptureDefaults (set in app.tsx from /start hints) —
 * do NOT pass capture options here.
 */
export function useEnsureMediaPublished(room: Room | undefined): void {
  const doneRef = useRef(false)

  useEffect(() => {
    if (!room || doneRef.current) return
    if (room.state !== 'connected') return
    doneRef.current = true
    void (async () => {
      try {
        await room.localParticipant.setMicrophoneEnabled(true)
      } catch (err) {
        console.warn('[interview] failed to enable microphone', err)
      }
      try {
        await room.localParticipant.setCameraEnabled(true)
      } catch {
        // SelfView renders a calm placeholder; do not surface an error.
      }
    })()
  }, [room, room?.state])
}
