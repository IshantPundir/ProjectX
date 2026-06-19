'use client'

import type { QuestionLevel, ReportRead, SignalAssessmentOut } from '@/lib/api/reports'
import { CompetencyRadar } from './CompetencyRadar'
import { ScoreRing } from './ScoreRing'

// ---------------------------------------------------------------------------
// Derivation helpers
// ---------------------------------------------------------------------------

/** Extract first sentence from a string (up to the first '.', '!', or '?'). */
export function firstSentence(text: string): string {
  const m = text.match(/^[^.!?]*[.!?]/)
  return m ? m[0].trim() : text.trim()
}

const STRENGTH_LEVELS: ReadonlySet<QuestionLevel> = new Set(['solid', 'strong'])
const WATCHOUT_LEVELS: ReadonlySet<QuestionLevel> = new Set(['thin', 'absent', 'not_reached'])

/** Top-3 solid/strong signals by weight descending, signal-name tie-broken. */
export function deriveStrengths(assessments: SignalAssessmentOut[]): SignalAssessmentOut[] {
  return assessments
    .filter((a) => STRENGTH_LEVELS.has(a.level))
    .sort((a, b) => b.weight - a.weight || a.signal.localeCompare(b.signal))
    .slice(0, 3)
}

/** Top-3 required/knockout thin+absent+not_reached signals by weight desc, signal-name tie-broken. */
export function deriveWatchouts(assessments: SignalAssessmentOut[]): SignalAssessmentOut[] {
  return assessments
    .filter(
      (a) =>
        (a.knockout === true || a.priority === 'required') &&
        WATCHOUT_LEVELS.has(a.level),
    )
    .sort((a, b) => b.weight - a.weight || a.signal.localeCompare(b.signal))
    .slice(0, 3)
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface Props {
  report: ReportRead
}

export function AtAGlanceBand({ report }: Props) {
  const { signal_assessments, scores, quick_summary } = report

  const lede = quick_summary ? firstSentence(quick_summary) : 'No summary available.'

  const strengths = deriveStrengths(signal_assessments)
  const watchouts = deriveWatchouts(signal_assessments)

  // Safely pull dimension score values (null when not scored or key absent)
  const overallScore   = scores['overall']?.score    ?? null
  const technicalScore = scores['technical']?.score  ?? null
  const commsScore     = scores['communication']?.score ?? null

  // Tone pass-through to ScoreRing (ScoreRing resolves to 'neutral' for unknown tones)
  const overallTone   = scores['overall']?.tone
  const technicalTone = scores['technical']?.tone
  const commsTone     = scores['communication']?.tone

  return (
    <div className="rounded-2xl border border-[var(--px-border)] bg-[var(--px-surface-1)] shadow-sm overflow-hidden">
      {/* ── Top zone: radar left, rings + lede right ── */}
      <div className="flex items-center gap-10 px-7 py-7">
        {/* Radar — fixed 240×230 as per mockup */}
        <div className="flex-none w-[240px] h-[230px]">
          <CompetencyRadar assessments={signal_assessments} />
        </div>

        {/* Right side: rings + lede */}
        <div className="flex-1 min-w-0">
          {/* Three evenly-spaced rings — equal size */}
          <div className="flex justify-between mb-5">
            <ScoreRing valueTen={overallScore}   label="Overall"       tone={overallTone}   size={92} />
            <ScoreRing valueTen={technicalScore} label="Technical"     tone={technicalTone} size={92} />
            <ScoreRing valueTen={commsScore}     label="Communication" tone={commsTone}      size={92} />
          </div>

          {/* One-line lede */}
          <p className="text-sm leading-snug text-[var(--px-fg-2)]">
            {lede}
          </p>
        </div>
      </div>

      {/* ── Strip: strengths + watch-outs ── */}
      {(strengths.length > 0 || watchouts.length > 0) && (
        <div className="flex flex-wrap gap-10 px-7 py-4 border-t border-[var(--px-border)] bg-[var(--px-surface-0)]">
          {strengths.length > 0 && (
            <div>
              <div
                className="text-[10px] font-extrabold uppercase tracking-[0.07em] mb-2"
                style={{ color: 'var(--px-ok)' }}
              >
                ✓ Top strengths
              </div>
              <div className="flex flex-wrap gap-2">
                {strengths.map((s) => (
                  <span
                    key={s.signal}
                    className="inline-block text-xs font-semibold px-3 py-1 rounded-full"
                    style={{
                      background: 'var(--px-ok-bg)',
                      color: 'var(--px-ok)',
                    }}
                  >
                    {s.signal}
                  </span>
                ))}
              </div>
            </div>
          )}

          {watchouts.length > 0 && (
            <div>
              <div
                className="text-[10px] font-extrabold uppercase tracking-[0.07em] mb-2"
                style={{ color: 'var(--px-caution)' }}
              >
                ! Watch-outs
              </div>
              <div className="flex flex-wrap gap-2">
                {watchouts.map((w) => (
                  <span
                    key={w.signal}
                    className="inline-block text-xs font-semibold px-3 py-1 rounded-full"
                    style={{
                      background: 'var(--px-caution-bg)',
                      color: 'var(--px-caution)',
                    }}
                  >
                    {w.signal}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
