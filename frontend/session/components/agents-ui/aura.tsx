'use client'

import { type LocalAudioTrack, type RemoteAudioTrack } from 'livekit-client'
import { type AgentState, type TrackReferenceOrPlaceholder } from '@livekit/components-react'

import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura'
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'
import { cn } from '@/lib/utils'

/** Base hue for the shader; colorShift=2 makes it cycle through many hues. */
const AURA_COLOR = '#1FD5F9'
const AURA_COLOR_SHIFT = 2

type AuraSize = 'sm' | 'md' | 'lg' | 'xl'

const FALLBACK_SIZE: Record<AuraSize, string> = {
  sm: 'size-[120px]',
  md: 'size-[180px]',
  lg: 'size-[260px]',
  xl: 'size-[340px]',
}

export interface AuraProps {
  state?: AgentState
  audioTrack?: LocalAudioTrack | RemoteAudioTrack | TrackReferenceOrPlaceholder
  size?: AuraSize
  className?: string
}

/**
 * The AI interviewer's presence. Renders LiveKit's stock WebGL aura shader
 * (colorShift 2, light theme); under prefers-reduced-motion it renders a static
 * multi-hue gradient orb instead (no WebGL).
 */
export function Aura({ state = 'connecting', audioTrack, size = 'xl', className }: AuraProps) {
  const reduced = usePrefersReducedMotion()

  if (reduced) {
    return (
      <span
        role="img"
        aria-label="AI interviewer"
        className={cn('aura-mark block', FALLBACK_SIZE[size], className)}
      />
    )
  }

  return (
    <AgentAudioVisualizerAura
      role="img"
      aria-label="AI interviewer"
      state={state}
      audioTrack={audioTrack}
      size={size}
      color={AURA_COLOR}
      colorShift={AURA_COLOR_SHIFT}
      themeMode="light"
      className={className}
    />
  )
}
