'use client'

import type { HumanDecisionValue, ReportRead } from '@/lib/api/reports'
import { AiRecommendationCard } from './AiRecommendationCard'
import { HumanDecisionPanel } from './HumanDecisionPanel'
import { QaEvidencePanel } from './QaEvidencePanel'
import { ReportMethodologyFooter } from './ReportMethodologyFooter'
import { ReportSummary } from './ReportSummary'
import { ReportTopBar } from './ReportTopBar'
import { SessionPlaybackStub } from './SessionPlaybackStub'
import { SignalScorecards } from './SignalScorecards'
import { SignalSpiderChart } from './SignalSpiderChart'

interface Props {
  report: ReportRead
  candidateName: string
  candidateId: string
  title?: string
  subtitle?: string
  canRegenerate: boolean
  onRegenerate: () => void
  onDecision: (decision: HumanDecisionValue, rationale: string) => void
  isSubmitting: boolean
}

export function ReportView({
  report, candidateName, candidateId, title = 'Interview', subtitle = '',
  canRegenerate, onRegenerate, onDecision, isSubmitting,
}: Props) {
  const spider = <SignalSpiderChart signals={report.signal_scorecards} />
  return (
    <div className="mx-auto max-w-[1400px] px-6 pb-10 pt-5">
      <ReportTopBar
        candidateName={candidateName} candidateId={candidateId}
        title={title} subtitle={subtitle} verdict={report.verdict}
        canRegenerate={canRegenerate} onRegenerate={onRegenerate}
      />
      <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-[1.85fr_1fr]">
        {/* MAIN */}
        <div className="space-y-3.5">
          <SessionPlaybackStub />
          {spider && (
            <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
              <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Signal profile — 0–10</h2>
              <div className="flex justify-center">{spider}</div>
            </section>
          )}
          <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
            <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Knockouts &amp; signals — evidence inline</h2>
            <SignalScorecards knockouts={report.knockout_results} signals={report.signal_scorecards} />
          </section>
          <ReportSummary summary={report.summary} />
        </div>
        {/* SIDE */}
        <div className="space-y-3.5">
          <AiRecommendationCard report={report} />
          <HumanDecisionPanel verdict={report.verdict} decision={report.human_decision} onSubmit={onDecision} isSubmitting={isSubmitting} />
          <QaEvidencePanel questionScorecards={report.question_scorecards} />
        </div>
      </div>
      <ReportMethodologyFooter manifest={report.scoring_manifest} />
    </div>
  )
}
