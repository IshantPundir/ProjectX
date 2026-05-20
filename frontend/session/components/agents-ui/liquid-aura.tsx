'use client'

import { type CSSProperties, useMemo } from 'react'
import { type LocalAudioTrack, type RemoteAudioTrack } from 'livekit-client'
import {
  type AgentState,
  type TrackReferenceOrPlaceholder,
  useMultibandTrackVolume,
} from '@livekit/components-react'

import { cn } from '@/lib/utils'

export interface LiquidAuraProps {
  /** Current agent state — drives the CSS state modulation. */
  state?: AgentState
  /** Agent audio track; amplitude is derived from it. */
  audioTrack?: LocalAudioTrack | RemoteAudioTrack | TrackReferenceOrPlaceholder
  /** Hero (full size) or mark (small avatar / minimized). */
  size?: 'hero' | 'mark'
  /** Optional accent override (defaults to the theme's --px-accent). */
  color?: `#${string}`
  className?: string
}

const SIZE_CLASS: Record<NonNullable<LiquidAuraProps['size']>, string> = {
  hero: 'size-[260px] sm:size-[340px] md:size-[420px]',
  mark: 'size-[22px]',
}

/**
 * Bespoke audio-reactive "Liquid aurora" — the AI interviewer's on-screen
 * presence. All motion is CSS (see .liquid-aura* in globals.css); this
 * component only maps audio amplitude to the --amp CSS variable and the
 * agent state to data-lk-state. Honors prefers-reduced-motion via CSS.
 */
export function LiquidAura({
  state = 'connecting',
  audioTrack,
  size = 'hero',
  color,
  className,
}: LiquidAuraProps) {
  // One band = overall loudness. loPass/hiPass mirror the bar visualizer.
  const bands = useMultibandTrackVolume(audioTrack, { bands: 1, loPass: 100, hiPass: 200 })

  const amp = useMemo(() => {
    const raw = bands.length > 0 ? bands[0] : 0
    if (!Number.isFinite(raw) || raw <= 0) return 0
    return Math.min(1, raw)
  }, [bands])

  const style = {
    '--amp': String(amp),
    ...(color ? { color } : {}),
  } as CSSProperties

  return (
    <div
      role="img"
      aria-label="AI interviewer"
      data-lk-state={state}
      data-aura-size={size}
      style={style}
      className={cn('liquid-aura', SIZE_CLASS[size], className)}
    >
      <div className="liquid-aura__glow" />
      <div className="liquid-aura__body">
        <div className="liquid-aura__sheen" />
      </div>
    </div>
  )
}
