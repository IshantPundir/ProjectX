import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SessionPlaybackStub, VerbalContentOnlyBadge } from '@/components/dashboard/reports/SessionPlaybackStub'
import { ReportSummary } from '@/components/dashboard/reports/ReportSummary'
import { ReportMethodologyFooter } from '@/components/dashboard/reports/ReportMethodologyFooter'
import { ReportTopBar } from '@/components/dashboard/reports/ReportTopBar'

describe('report presentational components', () => {
  it('SessionPlaybackStub names the future recording feature', () => {
    render(<SessionPlaybackStub />)
    expect(screen.getByText(/session playback/i)).toBeInTheDocument()
    expect(screen.getByText(/recording/i)).toBeInTheDocument()
  })
  it('VerbalContentOnlyBadge states no facial/affect scoring', () => {
    render(<VerbalContentOnlyBadge />)
    expect(screen.getByText(/no facial/i)).toBeInTheDocument()
  })
  it('ReportSummary renders strengths and gaps', () => {
    render(<ReportSummary summary={{ headline: 'Weak Python', strengths: ['REST APIs'], gaps: ['No depth'], rationale: 'r' }} />)
    expect(screen.getByText('Weak Python')).toBeInTheDocument()
    expect(screen.getByText('REST APIs')).toBeInTheDocument()
    expect(screen.getByText('No depth')).toBeInTheDocument()
  })
  it('ReportMethodologyFooter shows the verbal-content-only line + model', () => {
    render(<ReportMethodologyFooter manifest={{ scorer_model: 'gpt-5.4', reasoning_effort: 'medium', prompt_version: 'v1', generated_at: '2026-05-26T00:00:00Z', correlation_id: 'abc', verbosity: null, prompt_cache_key: null, scorer_code_version: null, bank_id: null, signal_snapshot_id: null, n_samples: 3, cache_hit_rate: null, evidence_grounding_summary: null }} />)
    expect(screen.getByText(/verbal-content-only/i)).toBeInTheDocument()
    expect(screen.getByText(/gpt-5\.4/)).toBeInTheDocument()
  })
  it('ReportTopBar shows the regenerate control only when canRegenerate', () => {
    const onRegen = vi.fn()
    const { rerender } = render(<ReportTopBar candidateName="Anand" candidateId="c1" title="Senior Python Engineer" subtitle="AI Screening" verdict="reject" canRegenerate={false} onRegenerate={onRegen} />)
    expect(screen.queryByRole('button', { name: /regenerate/i })).not.toBeInTheDocument()
    rerender(<ReportTopBar candidateName="Anand" candidateId="c1" title="Senior Python Engineer" subtitle="AI Screening" verdict="reject" canRegenerate onRegenerate={onRegen} />)
    fireEvent.click(screen.getByRole('button', { name: /regenerate/i }))
    expect(onRegen).toHaveBeenCalled()
  })
})
