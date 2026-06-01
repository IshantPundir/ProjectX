import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ReportMethodologyFooter } from '@/components/dashboard/reports/ReportMethodologyFooter'
import { ReportTopBar } from '@/components/dashboard/reports/ReportTopBar'
import { makeReport } from './_fixture'

describe('report presentational components', () => {
  it('ReportMethodologyFooter shows the verbal-content-only line + model', () => {
    const r = makeReport()
    render(<ReportMethodologyFooter methodology={r.methodology} manifest={r.scoring_manifest} />)
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
