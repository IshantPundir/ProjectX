// components/dashboard/reports/theater/VideoControls.tsx
'use client'

import { Maximize, Pause, Play, Volume2, VolumeX } from 'lucide-react'

import { clockFromSec, type VideoController } from './useVideoController'
import './theater.css'

export function VideoControls({
  controller,
  visible,
  onToggleFullscreen,
}: {
  controller: VideoController
  visible: boolean
  onToggleFullscreen: () => void
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
        aria-label="Playback speed"
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
        aria-label="Fullscreen"
        className="theater-ctrlbtn grid h-7 w-7 flex-none place-items-center"
      >
        <Maximize className="h-4 w-4" />
      </button>
    </div>
  )
}
