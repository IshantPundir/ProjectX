'use client'

import { VideoTrack, useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { useMemo } from 'react'
import type { TrackReference } from '@livekit/components-react'

import { cn } from '@/lib/utils'

export function SelfView({ className }: { className?: string }) {
  const { localParticipant } = useLocalParticipant()
  const publication = localParticipant.getTrackPublication(Track.Source.Camera)
  const trackRef = useMemo<TrackReference | undefined>(
    () => (publication ? { source: Track.Source.Camera, participant: localParticipant, publication } : undefined),
    [publication, localParticipant],
  )
  const live = trackRef && !trackRef.publication.isMuted

  return (
    <div
      className={cn(
        'relative aspect-[4/3] w-[clamp(128px,22vw,240px)] overflow-hidden rounded-xl border border-px-hairline-strong bg-px-surface-2 shadow-[var(--px-shadow-md)]',
        className,
      )}
    >
      {live ? (
        <VideoTrack trackRef={trackRef} className="size-full object-cover" />
      ) : (
        <div className="grid size-full place-items-center text-[10px] text-px-fg-4">Camera starting…</div>
      )}
      <span className="absolute bottom-1.5 left-2 rounded-md bg-black/50 px-1.5 py-0.5 text-[9px] font-semibold text-white backdrop-blur-sm">
        You
      </span>
    </div>
  )
}
