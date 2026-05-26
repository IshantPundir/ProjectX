import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportView } from '@/components/dashboard/reports/ReportView'
import { ReportEmptyState } from '@/components/dashboard/reports/ReportStates'
import type { ReportRead } from '@/lib/api/reports'

const base = {
  verdict: 'reject', verdict_reason: 'failed', overall_score: 36, overall_coverage: 0.7,
  overall_confidence: 'medium',
  dimension_scores: { technical: { name: 'Technical', score: 37, coverage: 0.6, confidence: 'medium', note: null } },
  knockout_results: [], signal_scorecards: [], question_scorecards: [],
  summary: { headline: 'h', strengths: [], gaps: [], rationale: '' },
  id: 'r1', session_id: 's1', version: 1, engine_version: 'v2', scoring_manifest: null,
  human_decision: null, generated_at: null,
} as unknown as ReportRead

const noop = vi.fn()

describe('ReportView', () => {
  it('renders the verdict band for a ready report', () => {
    render(<ReportView report={{ ...base, status: 'ready' }} candidateName="A" candidateId="c1" canRegenerate={false} onRegenerate={noop} onDecision={noop} isSubmitting={false} />)
    expect(screen.getAllByText('Reject').length).toBeGreaterThan(0)
  })
})

describe('ReportEmptyState', () => {
  it('shows Generate report only for super-admin', () => {
    const { rerender } = render(<ReportEmptyState canGenerate={false} onGenerate={noop} />)
    expect(screen.queryByRole('button', { name: /generate report/i })).not.toBeInTheDocument()
    rerender(<ReportEmptyState canGenerate onGenerate={noop} />)
    expect(screen.getByRole('button', { name: /generate report/i })).toBeInTheDocument()
  })
})
