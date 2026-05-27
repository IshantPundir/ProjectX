'use client'

import type { HumanDecisionValue, ReportRead } from '@/lib/api/reports'
import { HumanDecisionPanel } from './HumanDecisionPanel'
import { QuestionByQuestion } from './QuestionByQuestion'
import { QuickSummary } from './QuickSummary'
import { ReportMethodologyFooter } from './ReportMethodologyFooter'
import { ReportTopBar } from './ReportTopBar'
import { ScoresCard } from './ScoresCard'
import { SessionPlaybackStub } from './SessionPlaybackStub'
import { SignalAuditTable } from './SignalAuditTable'
import { StrengthsConcerns } from './StrengthsConcerns'
import { WhyContrast } from './WhyContrast'

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
  return (
    <div className="mx-auto max-w-[1400px] px-6 pb-10 pt-5">
      <ReportTopBar
        candidateName={candidateName} candidateId={candidateId}
        title={title} subtitle={subtitle} verdict={report.verdict}
        canRegenerate={canRegenerate} onRegenerate={onRegenerate}
      />
      <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-[1.85fr_1fr]">
        <div className="space-y-3.5">
          <SessionPlaybackStub />
          <WhyContrast decision={report.decision} />
          <QuickSummary text={report.quick_summary} />
          <StrengthsConcerns strengths={report.strengths} concerns={report.concerns} />
          <QuestionByQuestion questions={report.questions} />
          <SignalAuditTable assessments={report.signal_assessments} />
        </div>
        <div className="space-y-3.5">
          <ScoresCard report={report} />
          <HumanDecisionPanel verdict={report.verdict} decision={report.human_decision} onSubmit={onDecision} isSubmitting={isSubmitting} />
        </div>
      </div>
      <ReportMethodologyFooter methodology={report.methodology} manifest={report.scoring_manifest} />
    </div>
  )
}
