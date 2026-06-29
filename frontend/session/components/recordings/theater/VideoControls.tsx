// components/dashboard/reports/theater/VideoControls.tsx
'use client'

import { useRef, useState } from 'react'
import { createPortal } from 'react-dom'
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
  fullscreenSupported = true,
}: {
  controller: VideoController
  visible: boolean
  onToggleFullscreen: () => void
  markers?: TimelineMarker[]
  flags?: FlagMarker[]
  activeQuestionId?: string | null
  onSeekMs?: (ms: number) => void
  fullscreenSupported?: boolean
}) {
  const c = controller
  const pct = c.durationSec > 0 ? (c.currentSec / c.durationSec) * 100 : 0
  const buf = c.durationSec > 0 ? (c.bufferedSec / c.durationSec) * 100 : 0
  const silent = c.muted || c.volume === 0

  // Proctoring violations are hover-only: a pointer-following card over the
  // scrubber. We hit-test the pointer against each band's rendered px rect
  // (matching the min-width) rather than capturing pointer events on the bands —
  // that way the bands never block scrubbing.
  const scrubRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<{ flag: FlagMarker; x: number; y: number } | null>(null)
  const hitFlagAt = (e: React.PointerEvent<HTMLDivElement>): { flag: FlagMarker; x: number; y: number } | null => {
    const el = scrubRef.current
    if (!el || flags.length === 0) return null
    const rect = el.getBoundingClientRect()
    const x = e.clientX - rect.left
    const f = flags.find((fl) => {
      const left = (fl.positionPct / 100) * rect.width
      const width = Math.max((fl.widthPct / 100) * rect.width, 4)
      return x >= left && x <= left + width
    })
    if (!f) return null
    // clamp the card center so a 158px-wide card stays fully on-screen
    const half = 80
    const cx = Math.min(Math.max(e.clientX, half), window.innerWidth - half)
    return { flag: f, x: cx, y: rect.top }
  }
  const onScrubMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const hit = hitFlagAt(e)
    setHover((prev) => (hit ? hit : prev === null ? prev : null))
  }
  // touch has no hover: a tap on a band toggles the detail card; a tap elsewhere clears it
  const onScrubDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== 'touch') return
    const hit = hitFlagAt(e)
    setHover((prev) => (hit && prev?.flag !== hit.flag ? hit : null))
  }
  const clearHover = () => setHover((prev) => (prev === null ? prev : null))

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

      <div
        ref={scrubRef}
        className="theater-scrub relative flex-1"
        onPointerDown={onScrubDown}
        onPointerMove={onScrubMove}
        onPointerLeave={clearHover}
      >
        <div className="theater-scrub-track">
          <div className="theater-scrub-buf" style={{ width: `${buf}%` }} />
          <div className="theater-scrub-fill" style={{ width: `${pct}%` }} />
        </div>
        {/* proctoring violations as duration bands — each spans its full
            start→end range (min-width keeps brief ones visible). Purely visual
            (pointer-events:none) so they never block scrubbing; detail surfaces
            in the hover card below. */}
        {flags.map((f, i) => (
          <div
            key={`flag-${i}`}
            aria-hidden="true"
            data-hover={hover?.flag === f ? 'true' : 'false'}
            className="theater-scrub-flag"
            style={{
              left: `${f.positionPct}%`,
              width: `${f.widthPct}%`,
              background: f.kind === DOWN_KIND ? 'var(--px-caution-fill)' : 'var(--px-danger-fill)',
            }}
          />
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
        className="theater-ctrlbtn flex-none text-[11px] font-bold tabular-nums max-[640px]:ml-auto"
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

      {fullscreenSupported && (
        <button
          type="button"
          onClick={onToggleFullscreen}
          aria-label={c.isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
          className="theater-ctrlbtn grid h-7 w-7 flex-none place-items-center"
        >
          <Maximize className="h-4 w-4" />
        </button>
      )}

      {/* hover card for the violation band under the pointer — portaled to body
          (fixed) so the controls bar's overflow:hidden can't clip it */}
      {hover &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            className="theater-flagcard"
            style={{ left: hover.x, top: hover.y }}
            aria-hidden="true"
          >
            {hover.flag.thumbnailUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={hover.flag.thumbnailUrl} alt="" className="theater-flagcard-img" />
            )}
            <div className="theater-flagcard-body">
              <div
                className="theater-flagcard-title"
                style={{ color: hover.flag.kind === DOWN_KIND ? 'var(--px-caution)' : 'var(--px-danger)' }}
              >
                {FLAG_KIND_LABEL[hover.flag.kind] ?? hover.flag.kind}
              </div>
              <div className="theater-flagcard-meta">
                {formatTimestamp(hover.flag.startMs)}–{formatTimestamp(hover.flag.endMs)} ·{' '}
                {Math.round(hover.flag.confidence * 100)}% confidence
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  )
}
