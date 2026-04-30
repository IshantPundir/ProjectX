'use client'

import { useLocalParticipant, VideoTrack } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { useEffect } from 'react'

interface Props {
  onMediaLost: () => void
}

export function CandidateSelfView({ onMediaLost }: Props) {
  const { localParticipant } = useLocalParticipant()
  const camPub = localParticipant.getTrackPublication(Track.Source.Camera)
  const micPub = localParticipant.getTrackPublication(Track.Source.Microphone)
  const camTrack = camPub?.track
  const micTrack = micPub?.track

  useEffect(() => {
    if (!camTrack || !micTrack) return
    const handler = () => {
      // If the candidate explicitly muted via the UI, ignore. Only fire on
      // hardware/permission loss (track ended).
      if (camTrack.isMuted && !camTrack.mediaStreamTrack?.enabled) onMediaLost()
      if (micTrack.isMuted && !micTrack.mediaStreamTrack?.enabled) onMediaLost()
    }
    camTrack.on('muted', handler)
    micTrack.on('muted', handler)
    return () => {
      camTrack.off('muted', handler)
      micTrack.off('muted', handler)
    }
  }, [camTrack, micTrack, onMediaLost])

  return (
    <div className="rounded-2xl bg-zinc-900 overflow-hidden aspect-video">
      {camTrack && camPub ? (
        <VideoTrack
          trackRef={{
            participant: localParticipant,
            source: Track.Source.Camera,
            publication: camPub,
          }}
        />
      ) : (
        <div className="size-full grid place-items-center text-zinc-500">camera off</div>
      )}
    </div>
  )
}
