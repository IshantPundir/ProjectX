'use client'

import type { ReportRead } from '@/components/recordings/api/reports'
import { ScoreGauge } from '../ScoreGauge'
import { tierTone, verdictMeta, TONE_BG, TONE_INK } from '../report-format'
import { QuestionRail } from './QuestionRail'
import { ThisMomentPanel } from './ThisMomentPanel'
import type { MomentSelection } from './ThisMomentPanel'
import type { TimelineMarker } from './timeline-model'

const DIMS: { key: string; label: string; short: string }[] = [
  { key: 'overall', label: 'Overall', short: 'Overall' },
  { key: 'technical', label: 'Technical', short: 'Tech' },
  { key: 'behavioral', label: 'Behavioral', short: 'Behav' },
  { key: 'communication', label: 'Comms', short: 'Comms' },
]

/**
 * Mobile-only panel surface for the full-session theater. The desktop side
 * panels (ThisMomentPanel + QuestionRail) and the top-bar gauges don't fit on a
 * phone, so their content lives here: a bottom sheet in portrait, a right-hand
 * drawer in landscape (driven entirely by theater.css). Hidden on desktop
 * (`min-width: 641px`) via `.theater-sheet-root`.
 */
export function TheaterMobileSheet({
  open,
  onClose,
  report,
  railMarkers,
  activeQuestionId,
  selection,
  offScreenPct,
  onSelectQuestion,
  onJump,
}: {
  open: boolean
  onClose: () => void
  report: ReportRead
  railMarkers: TimelineMarker[]
  activeQuestionId: string | null
  selection: MomentSelection
  offScreenPct: number | null
  onSelectQuestion: (questionId: string) => void
  onJump: (ms: number) => void
}) {
  const v = verdictMeta(report.verdict)
  const dims = DIMS.filter(({ key }) => (report.scores as Record<string, { score: number | null } | undefined>)[key]?.score != null)
  return (
    <div className="theater-sheet-root" data-open={open ? 'true' : 'false'} aria-hidden={!open}>
      <button
        type="button"
        className="theater-sheet-backdrop"
        aria-label="Close panel"
        tabIndex={open ? 0 : -1}
        onClick={onClose}
      />
      <div className="theater-sheet" role="dialog" aria-label="Questions and scores" inert={!open}>
        <div className="theater-sheet-grip" aria-hidden="true" />
        <div className="theater-sheet-scroll">
          <div className="mb-3 flex items-center gap-2">
            <span
              className="rounded-full px-2.5 py-0.5 text-[11px] font-bold"
              style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}
            >
              {v.label}
            </span>
            {offScreenPct != null && (
              <span className="text-[11px] font-semibold" style={{ color: 'var(--px-fg-3)' }}>
                {Math.round(offScreenPct * 100)}% off-screen
              </span>
            )}
          </div>

          <div className="mb-4 flex flex-wrap gap-3">
            {dims.map(({ key, label, short }) => {
              const s = (report.scores as Record<string, { score: number | null; tone: string }>)[key]
              return (
                <div key={key} className="flex flex-col items-center gap-1">
                  <ScoreGauge
                    score={s.score}
                    label={label}
                    size={48}
                    hideLabel
                    toneOverride={key === 'overall' ? v.tone : tierTone(s.tone)}
                  />
                  <span className="text-[10px] font-extrabold uppercase tracking-wide" style={{ color: 'var(--px-fg-2)' }}>
                    {short}
                  </span>
                </div>
              )
            })}
          </div>

          {selection && (
            <div className="mb-4">
              <ThisMomentPanel selection={selection} decision={report.decision} onJump={onJump} />
            </div>
          )}

          <QuestionRail
            markers={railMarkers}
            activeQuestionId={activeQuestionId}
            onSelect={onSelectQuestion}
          />
        </div>
      </div>
    </div>
  )
}
