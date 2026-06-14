// components/dashboard/reports/theater/ReviewTheater.tsx
'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Dialog, DialogContent } from '@/components/px'
import type { ReportRead } from '@/lib/api/reports'
import type { PlaybackSeekApi } from '../SessionPlayback'
import { useSessionProctoring } from '@/lib/hooks/use-session-proctoring'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'
import { Filmstrip } from './Filmstrip'
import { GlassLayer, GlassProvider } from './GlassBackdrop'
import { ScoreRail } from './ScoreRail'
import { TheaterStage } from './TheaterStage'
import { TheaterTopBar } from './TheaterTopBar'
import { ThisMomentPanel } from './ThisMomentPanel'
import { VideoControls } from './VideoControls'
import { buildFlagMarkers, buildQuestionMarkers, pickPosterUrl } from './timeline-model'
import { useTheaterState } from './useTheaterState'
import { useVideoController } from './useVideoController'
import './theater.css'

const HIDE_AFTER_MS = 2500

export function ReviewTheater({
  open,
  report,
  candidateName,
  subtitle,
  initialFlagStartMs = null,
  onClose,
}: {
  open: boolean
  report: ReportRead
  candidateName: string
  subtitle: string
  initialFlagStartMs?: number | null
  onClose: () => void
}) {
  const sessionId = report.session_id ?? ''
  const { data: rec, isLoading: recLoading } = useSessionRecording(open ? sessionId : '')
  const { data: proc, isLoading: procLoading } = useSessionProctoring(open ? sessionId : '')

  const apiDurationMs = (rec?.duration_seconds ?? 0) * 1000
  const signedUrl = rec?.status === 'ready' ? rec.signed_url : null
  const offsetMs = rec?.offset_ms ?? 0
  // The recording isn't watchable yet while the first fetch is in flight or egress
  // is still finalizing it — show a loader rather than the "unavailable" fallback.
  const recPending = recLoading || rec?.status === 'recording'
  // Proctoring runs offline; marks arrive later. Hint at it instead of silence.
  const procPending = procLoading || proc?.status === 'pending' || proc?.status === 'running'
  const flaggedRaw = useMemo(
    () => (proc && proc.status === 'ready' ? proc.flagged_intervals : []),
    [proc],
  )
  const riskBand = proc && proc.status === 'ready' ? proc.risk_band : null
  const offScreenPct =
    proc && proc.status === 'ready' && proc.detector_summary
      ? proc.detector_summary.off_screen_pct
      : null

  // Transport state is owned here (not inside useTheaterState) so the video
  // controller can be created first — its intrinsic duration is the fallback
  // when the API didn't report one.
  //
  // The <video> lives inside the dialog portal, which mounts it a tick after this
  // commits and remounts it on every reopen. We track the live element NODE in
  // state (via a callback ref) so the controller + glass layer re-bind to it each
  // time — a ref object would miss the new element on reopen and freeze playback.
  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null)
  const seekRef = useRef<PlaybackSeekApi | null>(null)
  const [currentMs, setCurrentMs] = useState(0)
  const ctrl = useVideoController(videoEl, !!signedUrl, offsetMs, seekRef, setCurrentMs)

  // Egress sometimes finishes without a duration (recording_duration_seconds stays
  // NULL even when status='ready'); without it the timeline, flag positions and
  // density all collapse to zero width. Fall back to the <video> element's own
  // metadata duration, which the controller exposes reactively.
  const durationMs = apiDurationMs || ctrl.durationSec * 1000

  const markers = useMemo(
    () => buildQuestionMarkers(report.questions, durationMs),
    [report.questions, durationMs],
  )
  // The opening frames are usually blurry; poster the <video> with a real
  // mid-interview question frame instead (null → no poster attribute).
  const posterUrl = useMemo(
    () => pickPosterUrl(report.questions, durationMs),
    [report.questions, durationMs],
  )
  // all flags are clickable ticks; only the top-N carry thumbnails from the API
  const flags = useMemo(
    () => buildFlagMarkers(flaggedRaw, durationMs, flaggedRaw.length),
    [flaggedRaw, durationMs],
  )

  const st = useTheaterState({
    markers,
    questions: report.questions,
    durationMs,
    seekRef,
    currentMs,
  })

  const ctrlRef = useRef(ctrl)
  // keep the controller ref current — no dep array on purpose (runs after every render)
  useEffect(() => {
    ctrlRef.current = ctrl
  })

  // fullscreen targets the theater root
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
    root.addEventListener('pointerenter', show)
    show()
    return () => {
      root.removeEventListener('pointermove', show)
      root.removeEventListener('pointerdown', show)
      root.removeEventListener('pointerenter', show)
      window.clearTimeout(timer)
    }
  }, [open])

  // keyboard shortcuts (read ctrl through a ref so listeners don't rebind each tick)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      const c = ctrlRef.current
      if (e.key === ' ') {
        e.preventDefault()
        c.togglePlay()
      } else if (e.key === 'ArrowRight') {
        c.seekToSec(c.currentSec + 5)
      } else if (e.key === 'ArrowLeft') {
        c.seekToSec(Math.max(0, c.currentSec - 5))
      } else if (e.key === 'f' || e.key === 'F') {
        toggleFullscreen()
      } else if (e.key === 'm' || e.key === 'M') {
        c.toggleMute()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, toggleFullscreen])

  // pre-select a flag when opened from a proctoring "jump to" row
  const { selectFlag } = st
  const appliedFlagRef = useRef(false)
  useEffect(() => {
    if (appliedFlagRef.current || initialFlagStartMs == null) return
    const f = flags.find((x) => x.startMs === initialFlagStartMs)
    if (f) {
      appliedFlagRef.current = true
      selectFlag(f)
    }
  }, [initialFlagStartMs, flags, selectFlag])

  // Exit animation: the shared px Dialog unmounts instantly on close, so we keep
  // it mounted through a brief "closing" phase (data-closing drives the CSS exit
  // keyframe), then actually close once it's played.
  const EXIT_MS = 280
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

  const integrityCaption = useMemo(() => {
    const riskText =
      riskBand === 'high' ? '⚠ HIGH RISK' : riskBand === 'medium' ? '⚠ MEDIUM RISK' : '⚠ INTEGRITY'
    const s = proc && proc.status === 'ready' ? proc.detector_summary : null
    if (!s) return riskText
    return `${riskText} · ${Math.round(s.off_screen_pct * 100)}% off-screen · ${s.down_glance_count} down-glances`
  }, [proc, riskBand])

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
            poster={posterUrl}
            loading={recPending}
            playing={ctrl.playing}
            onTogglePlay={ctrl.togglePlay}
          />

          {/* single blurred-video layer behind all panels (clipped to their rects) */}
          <GlassLayer />

          <div className="theater-topbar-slot">
            <TheaterTopBar report={report} riskBand={riskBand} onClose={requestClose} />
          </div>

          {/* left rail: candidate identity + all gauges + proctoring integrity */}
          <div className="theater-rail-slot">
            <ScoreRail
              report={report}
              candidateName={candidateName}
              subtitle={subtitle}
              offScreenPct={offScreenPct}
            />
          </div>

          <div className="theater-moment-slot">
            <ThisMomentPanel selection={st.selection} decision={report.decision} onJump={st.seekMs} />
          </div>

          <div className="theater-bottom">
            {/* row 1: a light "Question timeline" tag + an optional integrity
                chip, floating over the video (no heavy box) */}
            <div className="flex items-center gap-2 pl-0.5">
              <span className="theater-tl-label">Question timeline</span>
              {proc?.status === 'ready' && integrityCaption && (
                <span className="theater-tl-integrity">{integrityCaption}</span>
              )}
              {procPending && (
                <span className="theater-tl-pending">
                  <span className="theater-spinner-sm" aria-hidden="true" />
                  Analyzing integrity…
                </span>
              )}
            </div>
            {/* row 2: lively, status-tinted question cards floating free */}
            <Filmstrip
              markers={markers}
              activeQuestionId={st.activeId}
              onSelect={st.selectQuestion}
            />
            {/* row 3: controls pinned at the very bottom, with proctoring flag
                ticks + question nodes merged onto the scrubber */}
            {signedUrl && (
              <VideoControls
                controller={ctrl}
                visible={controlsVisible}
                onToggleFullscreen={toggleFullscreen}
                markers={markers}
                flags={flags}
                activeQuestionId={st.activeId}
                onSeekMs={st.seekMs}
                onSelectFlag={st.selectFlag}
              />
            )}
          </div>
        </div>
        </GlassProvider>
      </DialogContent>
    </Dialog>
  )
}
