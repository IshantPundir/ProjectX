'use client'

import { useEffect, useRef, type MutableRefObject } from 'react'

import type { PlaybackSeekApi } from '../SessionPlayback'

export function TheaterStage({
  signedUrl,
  offsetMs,
  seekApiRef,
  onCurrentMs,
}: {
  signedUrl: string | null
  offsetMs: number
  seekApiRef: MutableRefObject<PlaybackSeekApi | null>
  onCurrentMs: (ms: number) => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    seekApiRef.current = {
      seekToMs: (ms: number) => {
        const v = videoRef.current
        if (!v) return
        v.currentTime = Math.max(0, (ms + offsetMs) / 1000)
        void v.play?.()
      },
    }
    return () => {
      seekApiRef.current = null
    }
  }, [seekApiRef, offsetMs])

  if (!signedUrl) {
    return (
      <div className="grid flex-1 place-items-center text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
        Recording unavailable.
      </div>
    )
  }
  return (
    <video
      ref={videoRef}
      src={signedUrl}
      controls
      playsInline
      aria-label="Interview session recording"
      onTimeUpdate={() => {
        const v = videoRef.current
        if (v) onCurrentMs(v.currentTime * 1000 - offsetMs)
      }}
      className="h-full w-full rounded-xl bg-black object-contain"
    />
  )
}
