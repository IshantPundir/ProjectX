// components/dashboard/reports/theater/TheaterStage.tsx
'use client'

import { Play } from 'lucide-react'
import type { RefObject } from 'react'

export function TheaterStage({
  videoRef,
  signedUrl,
  playing,
  onTogglePlay,
}: {
  videoRef: RefObject<HTMLVideoElement | null>
  signedUrl: string | null
  playing: boolean
  onTogglePlay: () => void
}) {
  if (!signedUrl) {
    return (
      <div className="absolute inset-0 grid place-items-center text-[12px] text-[rgba(224,235,242,0.7)]">
        Recording unavailable.
      </div>
    )
  }
  return (
    <>
      {/* interview recording — no caption track available */}
      <video
        ref={videoRef}
        src={signedUrl}
        playsInline
        aria-label="Interview session recording"
        onClick={onTogglePlay}
        className="absolute inset-0 h-full w-full bg-black object-cover"
      />
      <div className="theater-scrim-top" aria-hidden="true" />
      <div className="theater-scrim-bottom" aria-hidden="true" />
      {!playing && (
        <button
          type="button"
          onClick={onTogglePlay}
          aria-label="Play"
          className="theater-centerplay absolute left-1/2 top-1/2 z-20 grid h-16 w-16 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full"
        >
          <Play className="h-7 w-7" />
        </button>
      )}
    </>
  )
}
