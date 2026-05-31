// components/dashboard/reports/theater/ReviewTheater.tsx
'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Dialog, DialogContent } from '@/components/px'
import type { ReportRead } from '@/lib/api/reports'
import type { PlaybackSeekApi } from '../SessionPlayback'
import { useSessionProctoring } from '@/lib/hooks/use-session-proctoring'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'
import { Filmstrip } from './Filmstrip'
import { TheaterStage } from './TheaterStage'
import { TheaterTopBar } from './TheaterTopBar'
import { ThisMomentPanel } from './ThisMomentPanel'
import { VideoControls } from './VideoControls'
import { buildFlagMarkers, buildQuestionMarkers } from './timeline-model'
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
  const { data: rec } = useSessionRecording(open ? sessionId : '')
  const { data: proc } = useSessionProctoring(open ? sessionId : '')

  const apiDurationMs = (rec?.duration_seconds ?? 0) * 1000
  const signedUrl = rec?.status === 'ready' ? rec.signed_url : null
  const offsetMs = rec?.offset_ms ?? 0
  const flaggedRaw = useMemo(
    () => (proc && proc.status === 'ready' ? proc.flagged_intervals : []),
    [proc],
  )
  const riskBand = proc && proc.status === 'ready' ? proc.risk_band : null

  const markers = useMemo(
    () => buildQuestionMarkers(report.questions, durationMs),
    [report.questions, durationMs],
  )
  // all flags are clickable ticks; only the top-N carry thumbnails from the API
  const flags = useMemo(
    () => buildFlagMarkers(flaggedRaw, durationMs, flaggedRaw.length),
    [flaggedRaw, durationMs],
  )
  const st = useTheaterState({ markers, questions: report.questions, durationMs })

  // custom video transport (replaces native controls)
  const videoRef = useRef<HTMLVideoElement>(null)
  const ctrl = useVideoController(videoRef, !!signedUrl, offsetMs, st.seekRef, st.setCurrentMs)
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

  const integrityCaption = useMemo(() => {
    const riskText =
      riskBand === 'high' ? '⚠ HIGH RISK' : riskBand === 'medium' ? '⚠ MEDIUM RISK' : '⚠ INTEGRITY'
    const s = proc && proc.status === 'ready' ? proc.detector_summary : null
    if (!s) return riskText
    return `${riskText} · ${Math.round(s.off_screen_pct * 100)}% off-screen · ${s.down_glance_count} down-glances`
  }, [proc, riskBand])

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent showCloseButton={false} widthClass="" className="theater-shell">
        <div ref={shellRef} className="theater-root">
          <TheaterStage
            videoRef={videoRef}
            signedUrl={signedUrl}
            playing={ctrl.playing}
            onTogglePlay={ctrl.togglePlay}
          />

          <div className="theater-topbar-slot">
            <TheaterTopBar
              report={report}
              candidateName={candidateName}
              subtitle={subtitle}
              riskBand={riskBand}
              onClose={onClose}
            />
          </div>

          <div className="theater-moment-slot">
            <ThisMomentPanel selection={st.selection} decision={report.decision} onJump={st.seekMs} />
          </div>

          <div className="theater-bottom">
            {/* row 1: question thumbnail strip + a compact integrity caption */}
            <div className="theater-glass rounded-2xl px-3 py-2">
              {integrityCaption && (
                <div
                  className="mb-1.5 text-[10px] font-bold"
                  style={{ color: 'var(--px-danger)' }}
                >
                  {integrityCaption}
                </div>
              )}
              <Filmstrip
                markers={markers}
                activeQuestionId={st.activeId}
                onSelect={st.selectQuestion}
              />
            </div>
            {/* row 2: controls pinned at the very bottom, with proctoring flag
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
      </DialogContent>
    </Dialog>
  )
}
