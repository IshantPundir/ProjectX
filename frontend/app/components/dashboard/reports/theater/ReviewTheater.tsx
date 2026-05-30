'use client'

import { useEffect, useMemo, useRef } from 'react'

import { Dialog, DialogContent } from '@/components/px'
import type { ReportRead } from '@/lib/api/reports'
import { useSessionProctoring } from '@/lib/hooks/use-session-proctoring'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'
import { SessionTimeline } from './SessionTimeline'
import { TheaterStage } from './TheaterStage'
import { TheaterTopBar } from './TheaterTopBar'
import { ThisMomentPanel } from './ThisMomentPanel'
import { buildFlagMarkers, buildQuestionMarkers, densityBuckets } from './timeline-model'
import { useTheaterState } from './useTheaterState'
import './theater.css'

const TOP_FLAGS = 6
const DENSITY_BUCKETS = 48

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

  const durationMs = (rec?.duration_seconds ?? 0) * 1000
  const flaggedRaw = proc && proc.status === 'ready' ? proc.flagged_intervals : []
  const riskBand = proc && proc.status === 'ready' ? proc.risk_band : null

  const markers = useMemo(
    () => buildQuestionMarkers(report.questions, durationMs),
    [report.questions, durationMs],
  )
  const flags = useMemo(
    () => buildFlagMarkers(flaggedRaw, durationMs, TOP_FLAGS),
    [flaggedRaw, durationMs],
  )
  const buckets = useMemo(
    () => (flaggedRaw.length ? densityBuckets(flaggedRaw, durationMs, DENSITY_BUCKETS) : []),
    [flaggedRaw, durationMs],
  )

  const st = useTheaterState({ markers, questions: report.questions, durationMs })

  // When opened from a proctoring "jump to" row, pre-select that flag (which
  // also seeks the video) once the flags have loaded. Runs once per open.
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
    const s = proc && proc.status === 'ready' ? proc.detector_summary : null
    if (!s) return ''
    return `${Math.round(s.off_screen_pct * 100)}% off-screen · ${s.down_glance_count} down-glances`
  }, [proc])

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent showCloseButton={false} widthClass="" className="theater-shell">
        <TheaterTopBar
          report={report}
          candidateName={candidateName}
          subtitle={subtitle}
          riskBand={riskBand}
          onClose={onClose}
        />
        <div className="flex min-h-0 flex-1 gap-3 px-3 pt-3">
          <div className="min-w-0 flex-1">
            <TheaterStage
              signedUrl={rec?.status === 'ready' ? rec.signed_url : null}
              offsetMs={rec?.offset_ms ?? 0}
              seekApiRef={st.seekRef}
              onCurrentMs={st.setCurrentMs}
            />
          </div>
          <div className="w-[260px] flex-none">
            <ThisMomentPanel selection={st.selection} decision={report.decision} onJump={st.seekMs} />
          </div>
        </div>
        <div className="p-3">
          <SessionTimeline
            markers={markers}
            flags={flags}
            buckets={buckets}
            riskBand={riskBand}
            integrityCaption={integrityCaption}
            playheadPct={st.playheadPct}
            activeQuestionId={st.activeId}
            onSelectQuestion={st.selectQuestion}
            onSeekMs={st.seekMs}
            onSelectFlag={st.selectFlag}
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}
