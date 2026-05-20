'use client'

import type { AgentState } from '@livekit/components-react'
import type { LocalAudioTrack, RemoteAudioTrack } from 'livekit-client'
import type { TrackReferenceOrPlaceholder } from '@livekit/components-react'

import { cn } from '@/lib/utils'
import { Aura } from '@/components/agents-ui/aura'

const STATE_LABEL: Partial<Record<AgentState, string>> = {
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
}

export function AuraStage({
  state,
  audioTrack,
  className,
}: {
  state?: AgentState
  audioTrack?: LocalAudioTrack | RemoteAudioTrack | TrackReferenceOrPlaceholder
  className?: string
}) {
  const label = state ? STATE_LABEL[state] : undefined
  return (
    <div className={cn('flex flex-col items-center justify-center gap-4', className)}>
      <Aura state={state} audioTrack={audioTrack} size="xl" />
      {label && <p className="text-xs tracking-wide text-px-accent-soft">{label}</p>}
    </div>
  )
}
