import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SignalAuditTable } from '@/components/dashboard/reports/SignalAuditTable'
import type { SignalAssessmentOut } from '@/lib/api/reports'
import { makeReport, makeSignalAssessment } from './_fixture'

describe('SignalAuditTable', () => {
  it('renders a collapsed details with the signal rows', () => {
    render(<SignalAuditTable assessments={makeReport().signal_assessments} />)
    const summary = screen.getByText(/Audit detail/i)
    expect(summary).toBeInTheDocument()
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    expect(details?.hasAttribute('open')).toBe(false)
    expect(screen.getByText('4+ years total professional experience')).toBeInTheDocument()
    expect(screen.getByText(/sufficient/)).toBeInTheDocument()
  })
  it('renders nothing when there are no assessments', () => {
    const { container } = render(<SignalAuditTable assessments={[]} />)
    expect(container.firstChild).toBeNull()
  })
  it('shows the thin-evidence bluff chip and the per-signal score', () => {
    const thin: SignalAssessmentOut = {
      signal: 'API expertise: RESTful APIs', type: 'competency', weight: 2, knockout: false,
      priority: 'required', engine_state: 'partial', final_state: 'partial',
      grade: 'thin', score: 25, evidence: [], overridden: false, override_reason: null,
    }
    render(<SignalAuditTable assessments={[thin]} />)
    expect(screen.getByTitle(/possible bluff/i)).toBeInTheDocument()  // the "thin" chip
    expect(screen.getByText('2.5')).toBeInTheDocument()               // score 25 → /10
  })

  it('renders level_basis sub-label and cross-credit tag when present', () => {
    const a = makeSignalAssessment({
      cross_credit_applied: true,
      level_basis: 'dedicated: thin; +1 cross-credit → solid',
    })
    render(<SignalAuditTable assessments={[a]} />)
    expect(screen.getByText('dedicated: thin; +1 cross-credit → solid')).toBeInTheDocument()
    expect(screen.getByText('cross-credited')).toBeInTheDocument()
  })

  it('does not render level_basis or cross-credit tag when absent/false', () => {
    const a = makeSignalAssessment({ cross_credit_applied: false, level_basis: '' })
    render(<SignalAuditTable assessments={[a]} />)
    expect(screen.queryByText('cross-credited')).not.toBeInTheDocument()
  })
})
