// components/dashboard/reports/theater/VideoControls.tsx
'use client'

import { Maximize, Pause, Play, Volume2, VolumeX } from 'lucide-react'

import { formatTimestamp } from '../report-format'
import { GlassBackdrop } from './GlassBackdrop'
import { clockFromSec, type VideoController } from './useVideoController'
import type { FlagMarker, TimelineMarker } from './timeline-model'
import './theater.css'

const FLAG_KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}
const DOWN_KIND = 'down_glance'

export function VideoControls({
  controller,
  visible,
  onToggleFullscreen,
  markers = [],
  flags = [],
  activeQuestionId = null,
  onSeekMs,
  onSelectFlag,
}: {
  controller: VideoController
  visible: boolean
  onToggleFullscreen: () => void
  markers?: TimelineMarker[]
  flags?: FlagMarker[]
  activeQuestionId?: string | null
  onSeekMs?: (ms: number) => void
  onSelectFlag?: (flag: FlagMarker) => void
}) {
  const c = controller
  const pct = c.durationSec > 0 ? (c.currentSec / c.durationSec) * 100 : 0
  const buf = c.durationSec > 0 ? (c.bufferedSec / c.durationSec) * 100 : 0
  const silent = c.muted || c.volume === 0
  return (
    <div
      className="theater-controls theater-glass flex items-center gap-3 rounded-2xl px-4 py-2"
      data-visible={visible ? 'true' : 'false'}
    >
      <GlassBackdrop />
      <button
        type="button"
        onClick={c.togglePlay}
        aria-label={c.playing ? 'Pause' : 'Play'}
        className="theater-playbtn grid h-9 w-9 flex-none place-items-center rounded-full"
      >
        {c.playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </button>

      <span className="flex-none text-[11px] tabular-nums" style={{ color: 'var(--px-fg-3)' }}>
        {clockFromSec(c.currentSec)}
      </span>

      <div className="theater-scrub relative flex-1">
        <div className="theater-scrub-track">
          <div className="theater-scrub-buf" style={{ width: `${buf}%` }} />
          <div className="theater-scrub-fill" style={{ width: `${pct}%` }} />
        </div>
        {/* proctoring flag ticks merged onto the scrubber (below the input so the
            range still owns keyboard/drag, but each tick is independently clickable) */}
        {flags.map((f, i) => (
          <button
            key={`flag-${i}`}
            type="button"
            onClick={() => onSelectFlag?.(f)}
            aria-label={`${FLAG_KIND_LABEL[f.kind] ?? f.kind} at ${formatTimestamp(f.startMs)}`}
            className="theater-scrub-flag"
            style={{
              left: `${f.positionPct}%`,
              background: f.kind === DOWN_KIND ? 'var(--px-caution-fill)' : 'var(--px-danger-fill)',
            }}
          >
            {f.thumbnailUrl && (
              <span className="theater-flagtip">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={f.thumbnailUrl} alt="" className="block w-full" />
              </span>
            )}
          </button>
        ))}
        {/* question markers as small nodes on the same track */}
        {markers.map((m) =>
          m.positionPct == null || m.askedAtMs == null ? null : (
            <button
              key={`q-${m.questionId}`}
              type="button"
              data-active={m.questionId === activeQuestionId ? 'true' : 'false'}
              onClick={() => onSeekMs?.(m.askedAtMs as number)}
              aria-label={`Q${m.seq} jump to ${formatTimestamp(m.askedAtMs)}`}
              className="theater-scrub-node"
              style={{ left: `${m.positionPct}%` }}
            />
          ),
        )}
        <input
          type="range"
          min={0}
          max={Math.max(0, c.durationSec)}
          step={0.1}
          value={c.currentSec}
          aria-label="Seek"
          onChange={(e) => c.seekToSec(Number(e.target.value))}
          className="theater-scrub-input"
        />
      </div>

      <span className="flex-none text-[11px] tabular-nums" style={{ color: 'var(--px-fg-3)' }}>
        {clockFromSec(c.durationSec)}
      </span>

      <button
        type="button"
        onClick={c.cycleRate}
        aria-label={`Playback speed: ${c.rate}×`}
        className="theater-ctrlbtn flex-none text-[11px] font-bold tabular-nums"
      >
        {c.rate}×
      </button>

      <button
        type="button"
        onClick={c.toggleMute}
        aria-label={silent ? 'Unmute' : 'Mute'}
        className="theater-ctrlbtn grid h-7 w-7 flex-none place-items-center"
      >
        {silent ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
      </button>

      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={silent ? 0 : c.volume}
        aria-label="Volume"
        onChange={(e) => c.setVolume(Number(e.target.value))}
        className="theater-vol flex-none"
      />

      <button
        type="button"
        onClick={onToggleFullscreen}
        aria-label={c.isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
        className="theater-ctrlbtn grid h-7 w-7 flex-none place-items-center"
      >
        <Maximize className="h-4 w-4" />
      </button>
    </div>
  )
}
