'use client'

import { useState, type CSSProperties } from 'react'
import { Share2 } from 'lucide-react'

import type { HumanDecisionValue, ReportRead } from '@/lib/api/reports'
import { useReel } from '@/lib/hooks/use-reel'
import { Button } from '@/components/px'
import { AtAGlanceBand } from './AtAGlanceBand'
import { HumanDecisionPanel } from './HumanDecisionPanel'
import { ImmersiveHeader } from './ImmersiveHeader'
import { ProctoringIntegrityPanel } from './ProctoringIntegrityPanel'
import { QuestionByQuestion } from './QuestionByQuestion'
import { QuickSummary } from './QuickSummary'
import { ReportMethodologyFooter } from './ReportMethodologyFooter'
import './report.css'
import { ScoresCard } from './ScoresCard'
import { ShareReportDialog } from './ShareReportDialog'
import { SignalAuditTable } from './SignalAuditTable'
import { StrengthsConcerns } from './StrengthsConcerns'
import { ReelTheater } from './theater/ReelTheater'
import { ReviewTheater } from './theater/ReviewTheater'
import { WhyContrast } from './WhyContrast'

interface Props {
  report: ReportRead
  sessionId: string
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
  report, sessionId, candidateName, candidateId, title = 'Interview', subtitle = '',
  canRegenerate, onRegenerate, onDecision, isSubmitting,
}: Props) {
  // ── Share state ───────────────────────────────────────────────────────────
  const [shareOpen, setShareOpen] = useState(false)

  // ── Theater state ─────────────────────────────────────────────────────────
  const [theaterOpen, setTheaterOpen] = useState(false)
  const [theaterFlagMs, setTheaterFlagMs] = useState<number | null>(null)
  const openTheater = (flagMs: number | null) => {
    setTheaterFlagMs(flagMs)
    setTheaterOpen(true)
  }

  // ── Reel state ────────────────────────────────────────────────────────────
  const [reelOpen, setReelOpen] = useState(false)
  const { data: reelData } = useReel(sessionId)
  const hasReel = reelData?.status === 'ready' && !!reelData.signed_url

  // ── Identity: prefer report.header over query-param props (legacy fallback) ─
  const resolvedName = report.header?.candidate_name ?? candidateName
  const resolvedSubtitle = report.header?.job_title ?? title

  return (
    <div className="mx-auto max-w-[1400px] px-6 pb-10 pt-5">
      {/* ── Action cluster — Share + Regenerate ── */}
      <div className="mb-3 flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => setShareOpen(true)}>
          <Share2 size={14} className="mr-1.5" /> Share
        </Button>
        {canRegenerate && (
          <Button type="button" variant="outline" size="sm" onClick={onRegenerate}>Regenerate</Button>
        )}
      </div>
      <ShareReportDialog sessionId={sessionId} open={shareOpen} onOpenChange={setShareOpen} />

      {/* ── Immersive header ── */}
      {report.header ? (
        <div className="mb-5 px-reveal" style={{ '--px-stagger': 0 } as CSSProperties}>
          <ImmersiveHeader
            header={report.header}
            verdict={report.verdict}
            hasReel={hasReel}
            onOpenReel={() => setReelOpen(true)}
            onOpenSession={() => openTheater(null)}
          />
        </div>
      ) : (
        /* Legacy reports without a header: minimal title bar so the page never crashes */
        <div className="mb-4 px-reveal" style={{ '--px-stagger': 0 } as CSSProperties}>
          <div className="flex items-baseline gap-3">
            <h1 className="text-[22px] font-bold" style={{ color: 'var(--px-fg)' }}>
              {resolvedName}
            </h1>
            {resolvedSubtitle && (
              <span className="text-[14px]" style={{ color: 'var(--px-fg-3)' }}>
                {resolvedSubtitle}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── At-a-glance band ── */}
      <div className="mb-5 px-reveal" style={{ '--px-stagger': 1 } as CSSProperties}>
        <AtAGlanceBand report={report} />
      </div>

      {/* ── Two-column body ── */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.7fr_1fr]">
        {/* LEFT — main content */}
        <div className="space-y-4">
          {[
            <QuickSummary key="s" text={report.quick_summary} />,
            <WhyContrast key="w" decision={report.decision} />,
            <StrengthsConcerns key="sc" strengths={report.strengths} concerns={report.concerns} />,
            <QuestionByQuestion key="q" questions={report.questions} />,
            <SignalAuditTable key="a" assessments={report.signal_assessments} />,
          ].map((node, i) => (
            <div key={node.key} className="px-reveal" style={{ '--px-stagger': i + 2 } as CSSProperties}>{node}</div>
          ))}
        </div>

        {/* RIGHT — sticky rail */}
        <div>
          <div className="sticky top-4 space-y-4">
            {[
              <ScoresCard key="scores" report={report} />,
              <ProctoringIntegrityPanel key="proctoring" sessionId={report.session_id ?? sessionId} onSeek={(ms) => openTheater(ms)} />,
              <HumanDecisionPanel key="decision" verdict={report.verdict} decision={report.human_decision} onSubmit={onDecision} isSubmitting={isSubmitting} />,
            ].map((node, i) => (
              <div key={node.key} className="px-reveal" style={{ '--px-stagger': i + 7 } as CSSProperties}>{node}</div>
            ))}
          </div>
        </div>
      </div>

      <ReportMethodologyFooter methodology={report.methodology} manifest={report.scoring_manifest} />

      {/* ── Full-session theater ── */}
      {theaterOpen && (
        <ReviewTheater
          open
          report={report}
          candidateName={resolvedName}
          subtitle={resolvedSubtitle}
          initialFlagStartMs={theaterFlagMs}
          onClose={() => setTheaterOpen(false)}
        />
      )}

      {/* ── Reel theater — opened from ImmersiveHeader CTA ── */}
      {reelOpen && hasReel && reelData?.signed_url && (
        <ReelTheater
          open
          signedUrl={reelData.signed_url}
          chapters={reelData.chapters ?? []}
          durationSeconds={reelData.duration_seconds ?? null}
          candidateName={resolvedName}
          subtitle={resolvedSubtitle}
          onClose={() => setReelOpen(false)}
        />
      )}
    </div>
  )
}
