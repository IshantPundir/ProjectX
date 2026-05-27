'use client'

import type { CSSProperties } from 'react'

import type { HumanDecisionValue, ReportRead } from '@/lib/api/reports'
import { HumanDecisionPanel } from './HumanDecisionPanel'
import { QuestionByQuestion } from './QuestionByQuestion'
import { QuickSummary } from './QuickSummary'
import { ReportMethodologyFooter } from './ReportMethodologyFooter'
import { ReportTopBar } from './ReportTopBar'
import './report.css'
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
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.85fr_1fr]">
        <div className="space-y-4">
          {[
            <SessionPlaybackStub key="p" />,
            <WhyContrast key="w" decision={report.decision} />,
            <QuickSummary key="s" text={report.quick_summary} />,
            <StrengthsConcerns key="sc" strengths={report.strengths} concerns={report.concerns} />,
            <QuestionByQuestion key="q" questions={report.questions} />,
            <SignalAuditTable key="a" assessments={report.signal_assessments} />,
          ].map((node, i) => (
            <div key={node.key} className="px-reveal" style={{ '--px-stagger': i } as CSSProperties}>{node}</div>
          ))}
        </div>
        <div className="space-y-4">
          {[
            <ScoresCard key="scores" report={report} />,
            <HumanDecisionPanel key="decision" verdict={report.verdict} decision={report.human_decision} onSubmit={onDecision} isSubmitting={isSubmitting} />,
          ].map((node, i) => (
            <div key={node.key} className="px-reveal" style={{ '--px-stagger': i } as CSSProperties}>{node}</div>
          ))}
        </div>
      </div>
      <ReportMethodologyFooter methodology={report.methodology} manifest={report.scoring_manifest} />
    </div>
  )
}
