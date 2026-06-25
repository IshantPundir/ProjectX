import type { ReportRead, SignalAssessmentOut } from '@/lib/api/reports'
import { ScoreBar } from './ScoreBar'
import { confidenceLabel, TONE_INK, verdictMeta } from './report-format'
import './report.css'

const DIMS: { key: string; label: string }[] = [
  { key: 'technical', label: 'Technical' },
  { key: 'behavioral', label: 'Behavioral' },
  { key: 'communication', label: 'Communication' },
]

/** Highest weight first; stable tiebreak by name. */
function byWeightDesc(a: SignalAssessmentOut, b: SignalAssessmentOut): number {
  if (b.weight !== a.weight) return b.weight - a.weight
  return a.signal.localeCompare(b.signal)
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2.5 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
      {children}
    </div>
  )
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>{label}</div>
      <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{value}</div>
    </div>
  )
}

export function GlanceBand({ report }: { report: ReportRead }): React.ReactElement {
  const overall = report.scores.overall
  const meta = verdictMeta(report.verdict)
  const dims = DIMS.filter(({ key }) => report.scores[key]?.score != null)

  const mustHaves = report.signal_assessments.filter((a) => a.knockout).sort(byWeightDesc)
  const others = report.signal_assessments.filter((a) => !a.knockout).sort(byWeightDesc)
  const hasSignals = report.signal_assessments.length > 0

  return (
    <section
      aria-label="Candidate at a glance"
      className="px-card rounded-2xl border bg-white p-5 sm:p-6"
      style={{ borderColor: 'var(--px-hairline)' }}
    >
      {/* Every score bar gets its own full-width line — no side-by-side cramming,
          so long competency labels never truncate. */}
      <div className="flex flex-col gap-6">
        {/* Overall */}
        <div>
          <SectionLabel>Overall</SectionLabel>
          {/* Overall is toned by the verdict (not the raw score band): the verdict
              is the authoritative read, so an advance-verdict bar stays positive even
              if a ceiling-cap/holistic adjustment leaves the raw score near the marker. */}
          <ScoreBar score={overall?.score ?? null} label="Overall" variant="hero" toneOverride={meta.tone} />
        </div>

        {/* Dimensions */}
        {dims.length > 0 && (
          <div>
            <SectionLabel>Dimensions</SectionLabel>
            <div className="flex flex-col gap-3.5">
              {dims.map(({ key, label }) => (
                <ScoreBar key={key} score={report.scores[key]?.score ?? null} label={label} variant="row" />
              ))}
            </div>
          </div>
        )}

        {/* Must-have competencies */}
        {hasSignals && mustHaves.length > 0 && (
          <div>
            <SectionLabel>Must-have competencies</SectionLabel>
            <div className="flex flex-col gap-3.5">
              {mustHaves.map((a) => (
                <ScoreBar key={a.signal} score={a.score} label={a.signal} variant="row"
                  mustHave notReached={a.provenance === 'not_reached'} />
              ))}
            </div>
          </div>
        )}

        {/* Other competencies */}
        {hasSignals && others.length > 0 && (
          <div>
            <SectionLabel>Other competencies</SectionLabel>
            <div className="flex flex-col gap-3.5">
              {others.map((a) => (
                <ScoreBar key={a.signal} score={a.score} label={a.signal} variant="row"
                  notReached={a.provenance === 'not_reached'} />
              ))}
            </div>
          </div>
        )}

        {/* Verdict + explanation — at the bottom, after all the scores */}
        <div className="mt-1 border-t pt-4" style={{ borderColor: 'var(--px-hairline)' }}>
          <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
            <div className="min-w-0 flex-1">
              <SectionLabel>AI recommendation</SectionLabel>
              <div className="text-[26px] font-extrabold leading-tight tracking-tight" style={{ color: TONE_INK[meta.tone] }}>
                {meta.label}
              </div>
              <p className="mt-1.5 text-[13px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>
                {report.decision.headline}
              </p>
            </div>
            <div className="flex shrink-0 gap-5">
              <Chip label="Coverage" value={(overall?.coverage ?? 0).toFixed(2)} />
              <Chip label="Confidence" value={confidenceLabel(overall?.confidence ?? 'low')} />
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
