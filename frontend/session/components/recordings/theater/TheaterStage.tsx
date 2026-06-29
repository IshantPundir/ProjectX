// components/dashboard/reports/theater/TheaterStage.tsx
'use client'

import { Play } from 'lucide-react'
import type { Ref } from 'react'

export function TheaterStage({
  videoRef,
  signedUrl,
  poster,
  loading,
  playing,
  onTogglePlay,
}: {
  // callback ref (or ref object) — owner tracks the live node to re-bind effects
  videoRef: Ref<HTMLVideoElement>
  signedUrl: string | null
  // a mid-interview frame for the <video> poster; omitted when none qualifies
  poster?: string | null
  loading: boolean
  playing: boolean
  onTogglePlay: () => void
}) {
  if (!signedUrl) {
    return (
      <div className="absolute inset-0 grid place-items-center">
        {loading ? (
          <div className="flex flex-col items-center gap-3">
            <span className="theater-spinner" aria-hidden="true" />
            <span className="text-[12px] font-semibold" style={{ color: 'rgba(20,40,60,0.6)' }}>
              Loading recording…
            </span>
          </div>
        ) : (
          <span className="text-[12px] text-[rgba(20,40,60,0.55)]">Recording unavailable.</span>
        )}
      </div>
    )
  }
  return (
    <>
      {/* interview recording — no caption track available */}
      <video
        ref={videoRef}
        src={signedUrl}
        {...(poster ? { poster } : {})}
        playsInline
        aria-label="Interview session recording"
        onClick={onTogglePlay}
        className="absolute inset-0 h-full w-full bg-black object-cover max-[640px]:object-contain"
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
