// components/dashboard/reports/theater/ReelTheater.tsx
'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { BrandLogo, Dialog, DialogContent } from '@/components/px'
import type { ReelChapter } from '@/lib/api/reels'
import type { Tone } from '../report-format'
import type { PlaybackSeekApi } from '../SessionPlayback'
import { GlassBackdrop, GlassLayer, GlassProvider } from './GlassBackdrop'
import { TheaterStage } from './TheaterStage'
import { VideoControls } from './VideoControls'
import { clockFromSec } from './useVideoController'
import type { TimelineMarker } from './timeline-model'
import { useVideoController } from './useVideoController'
import './theater.css'

const HIDE_AFTER_MS = 2500
const EXIT_MS = 280

/**
 * Full-screen player for the candidate reel — the SAME theater shell as the
 * Review Theater (glass backdrop, big video stage, branded chrome, scrubber
 * controls), but driven by the reel video + its chapter beats instead of the
 * session recording + question/proctoring timeline.
 */
export function ReelTheater({
  open,
  signedUrl,
  chapters,
  durationSeconds,
  candidateName,
  subtitle,
  onClose,
  showClose = true,
}: {
  open: boolean
  signedUrl: string | null
  chapters: ReelChapter[]
  durationSeconds: number | null
  candidateName: string
  subtitle: string
  onClose: () => void
  // The public recordings page hides the close ✕ — there is nowhere to close to.
  showClose?: boolean
}) {
  // The <video> lives in the dialog portal (mounts a tick late, remounts on
  // reopen); track the live NODE in state so the controller re-binds each time.
  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null)
  const seekRef = useRef<PlaybackSeekApi | null>(null)
  const [currentMs, setCurrentMs] = useState(0)
  const ctrl = useVideoController(videoEl, !!signedUrl, 0, seekRef, setCurrentMs)

  const durationMs = (durationSeconds ?? 0) * 1000 || ctrl.durationSec * 1000

  // Chapters → timeline markers (reusing the recording scrubber's marker shape).
  const markers = useMemo<TimelineMarker[]>(
    () =>
      chapters.map((c, i) => ({
        seq: i + 1,
        questionId: `ch-${i}`,
        title: c.label,
        statusBadge: 'passed',
        tone: 'accent' as Tone,
        askedAtMs: c.start_ms,
        thumbnailUrl: null,
        positionPct:
          durationMs > 0 ? Math.min(100, (c.start_ms / durationMs) * 100) : null,
      })),
    [chapters, durationMs],
  )
  const activeId = useMemo(() => {
    let id: string | null = null
    let best = -1
    for (const m of markers) {
      if (m.askedAtMs != null && m.askedAtMs <= currentMs && m.askedAtMs > best) {
        best = m.askedAtMs
        id = m.questionId
      }
    }
    return id
  }, [markers, currentMs])

  const seekMs = useCallback((ms: number) => seekRef.current?.seekToMs(ms), [])

  const shellRef = useRef<HTMLDivElement>(null)
  const toggleFullscreen = useCallback(() => {
    const el = shellRef.current
    if (!el) return
    if (document.fullscreenElement) void document.exitFullscreen?.()
    else void el.requestFullscreen?.()
  }, [])

  // auto-hide the control bar on pointer idle
  const [controlsVisible, setControlsVisible] = useState(true)
  useEffect(() => {
    if (!open) return
    const root = shellRef.current
    if (!root) return
    let timer = 0
    const show = () => {
      setControlsVisible(true)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => setControlsVisible(false), HIDE_AFTER_MS)
    }
    root.addEventListener('pointermove', show)
    root.addEventListener('pointerdown', show)
    show()
    return () => {
      root.removeEventListener('pointermove', show)
      root.removeEventListener('pointerdown', show)
      window.clearTimeout(timer)
    }
  }, [open])

  // keyboard shortcuts (read ctrl through a ref so listeners don't rebind)
  const ctrlRef = useRef(ctrl)
  useEffect(() => { ctrlRef.current = ctrl })
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      const c = ctrlRef.current
      if (e.key === ' ') { e.preventDefault(); c.togglePlay() }
      else if (e.key === 'ArrowRight') c.seekToSec(c.currentSec + 5)
      else if (e.key === 'ArrowLeft') c.seekToSec(Math.max(0, c.currentSec - 5))
      else if (e.key === 'f' || e.key === 'F') toggleFullscreen()
      else if (e.key === 'm' || e.key === 'M') c.toggleMute()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, toggleFullscreen])

  // exit animation (mirrors ReviewTheater)
  const [closing, setClosing] = useState(false)
  const closingRef = useRef(false)
  const closeTimerRef = useRef(0)
  const requestClose = useCallback(() => {
    if (closingRef.current) return
    closingRef.current = true
    setClosing(true)
    closeTimerRef.current = window.setTimeout(onClose, EXIT_MS)
  }, [onClose])
  useEffect(() => () => window.clearTimeout(closeTimerRef.current), [])

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) requestClose() }}>
      <DialogContent
        showCloseButton={false}
        widthClass=""
        className="theater-shell"
        data-closing={closing ? 'true' : 'false'}
      >
        <GlassProvider src={signedUrl} mainVideo={videoEl} rootRef={shellRef}>
          <div ref={shellRef} className="theater-root">
            <TheaterStage
              videoRef={setVideoEl}
              signedUrl={signedUrl}
              loading={false}
              playing={ctrl.playing}
              onTogglePlay={ctrl.togglePlay}
            />

            <GlassLayer />

            <div className="theater-topbar-slot">
              <div className="pointer-events-none flex items-start justify-between">
                <BrandLogo height={18} className="theater-watermark" />
                <div className="theater-glass pointer-events-auto flex items-center gap-2 rounded-full px-2.5 py-1.5">
                  <GlassBackdrop />
                  <span
                    className="whitespace-nowrap rounded-full px-2.5 py-0.5 text-[10.5px] font-bold"
                    style={{ background: 'var(--px-accent)', color: 'var(--px-accent-ink, #fff)' }}
                  >
                    ★ Highlight reel
                  </span>
                  {showClose && (
                    <button
                      type="button"
                      onClick={requestClose}
                      aria-label="Close"
                      className="grid h-6 w-6 flex-none place-items-center rounded-full border text-[12px]"
                      style={{ borderColor: 'var(--px-hairline-strong)', color: 'var(--px-fg-3)' }}
                    >
                      ✕
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* left rail: candidate identity (mirrors the recording theater's rail slot) */}
            <div className="theater-rail-slot">
              <div className="theater-glass relative rounded-2xl px-4 py-3">
                <GlassBackdrop />
                <div className="text-[13px] font-extrabold" style={{ color: 'var(--px-fg)' }}>
                  {candidateName}
                </div>
                {subtitle && (
                  <div className="mt-0.5 text-[11px] font-semibold" style={{ color: 'var(--px-fg-3)' }}>
                    {subtitle}
                  </div>
                )}
                <div className="mt-2 text-[10.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-accent)' }}>
                  AI highlight reel{durationSeconds ? ` · ${Math.round(durationSeconds)}s` : ''}
                </div>
              </div>
            </div>

            <div className="theater-bottom">
              <div className="flex items-center gap-2 pl-0.5">
                <span className="theater-tl-label">Reel chapters</span>
              </div>
              {/* chapter strip — reuses the theater filmstrip container + card look */}
              <div className="theater-scroll flex gap-2.5 overflow-x-auto pb-1.5 pt-1" aria-label="Reel chapters">
                {markers.map((m) => (
                  <button
                    key={m.questionId}
                    type="button"
                    data-active={m.questionId === activeId ? 'true' : 'false'}
                    onClick={() => m.askedAtMs != null && seekMs(m.askedAtMs)}
                    className="theater-tl-card flex w-[210px] flex-none items-center gap-3 rounded-full p-1.5 pr-5 text-left"
                    style={{ '--tf': 'var(--px-accent)' } as React.CSSProperties}
                  >
                    <span
                      className="grid h-11 w-11 flex-none place-items-center rounded-full text-[13px] font-extrabold text-white"
                      style={{ background: 'var(--px-accent)' }}
                      aria-hidden="true"
                    >
                      {m.seq}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-[9px] font-bold tabular-nums" style={{ color: 'var(--px-fg-4)' }}>
                        {clockFromSec((m.askedAtMs ?? 0) / 1000)}
                      </div>
                      <div className="truncate text-[12px] font-bold leading-snug" style={{ color: 'var(--px-fg)' }} title={m.title}>
                        {m.title}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
              {signedUrl && (
                <VideoControls
                  controller={ctrl}
                  visible={controlsVisible}
                  onToggleFullscreen={toggleFullscreen}
                  markers={markers}
                  flags={[]}
                  activeQuestionId={activeId}
                  onSeekMs={seekMs}
                />
              )}
            </div>
          </div>
        </GlassProvider>
      </DialogContent>
    </Dialog>
  )
}
