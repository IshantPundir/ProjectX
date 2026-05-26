'use client'

import { useParams, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'

import { ReportView } from '@/components/dashboard/reports/ReportView'
import {
  ReportEmptyState, ReportFailedState, ReportForbiddenState, ReportPendingState,
} from '@/components/dashboard/reports/ReportStates'
import type { HumanDecisionValue } from '@/lib/api/reports'
import { useMe } from '@/lib/hooks/use-me'
import { useRecordDecision, useRegenerateReport, useReport } from '@/lib/hooks/use-report'

export default function ReportPage() {
  const params = useParams<{ sessionId: string }>()
  const sessionId = params.sessionId
  const sp = useSearchParams()
  const candidateId = sp.get('candidateId') ?? ''
  const candidateName = sp.get('candidateName') ?? 'Candidate'
  const title = sp.get('title') ?? 'Interview'
  const subtitle = sp.get('subtitle') ?? ''

  const { data: me } = useMe()
  const isSuperAdmin = !!me?.is_super_admin

  const { state, markGenerating } = useReport(sessionId)
  const regenerate = useRegenerateReport(sessionId)
  const decision = useRecordDecision(sessionId)

  const handleRegenerate = () => {
    markGenerating()
    regenerate.mutate(undefined, {
      onSuccess: () => toast.success('Report generation started'),
      onError: (e) => toast.error(e.message || 'Could not start generation'),
    })
  }

  const handleDecision = (reportId: string) => (d: HumanDecisionValue, rationale: string) => {
    decision.mutate(
      { reportId, body: { decision: d, rationale } },
      {
        onSuccess: () => toast.success('Decision recorded'),
        onError: (e) => toast.error(e.message || 'Could not record decision'),
      },
    )
  }

  switch (state.kind) {
    case 'loading':
    case 'pending':
      return <ReportPendingState />
    case 'forbidden':
      return <ReportForbiddenState />
    case 'noReport':
      return <ReportEmptyState canGenerate={isSuperAdmin} onGenerate={handleRegenerate} />
    case 'failed':
      return <ReportFailedState canRegenerate={isSuperAdmin} onRegenerate={handleRegenerate} />
    case 'ready':
      return (
        <ReportView
          report={state.report}
          candidateName={candidateName}
          candidateId={candidateId}
          title={title}
          subtitle={subtitle}
          canRegenerate={isSuperAdmin}
          onRegenerate={handleRegenerate}
          onDecision={handleDecision(state.report.id ?? '')}
          isSubmitting={decision.isPending}
        />
      )
  }
}
