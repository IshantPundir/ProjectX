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
    // provenance column should show the real provenance value
    expect(screen.getByText(/asked_directly/)).toBeInTheDocument()
  })
  it('renders nothing when there are no assessments', () => {
    const { container } = render(<SignalAuditTable assessments={[]} />)
    expect(container.firstChild).toBeNull()
  })
  it('shows the thin-evidence bluff chip and the per-signal score', () => {
    const thin: SignalAssessmentOut = {
      signal: 'API expertise: RESTful APIs', type: 'competency', weight: 2, knockout: false,
      priority: 'required', provenance: 'asked_directly',
      // score is already 0–10 (backend native scale); formatTen renders as-is
      level: 'thin', score: 2.5, evidence: [], overridden: false, override_reason: null,
    }
    render(<SignalAuditTable assessments={[thin]} />)
    expect(screen.getByTitle(/possible bluff/i)).toBeInTheDocument()  // the "thin" chip
    expect(screen.getByText('2.5')).toBeInTheDocument()               // score 2.5 → "2.5"
  })

  it('renders the level in the grade cell', () => {
    const a = makeSignalAssessment({ level: 'solid', provenance: 'asked_directly', score: 8.0 })
    render(<SignalAuditTable assessments={[a]} />)
    expect(screen.getByText('solid')).toBeInTheDocument()
    expect(screen.getByText(/asked_directly/)).toBeInTheDocument()
  })

  it('renders a not_reached level as plain text, not the thin chip', () => {
    const a = makeSignalAssessment({ signal: 'Some skill', level: 'not_reached' })
    render(<SignalAuditTable assessments={[a]} />)
    expect(screen.getByText('not_reached')).toBeInTheDocument()
    expect(screen.queryByTitle(/possible bluff/i)).not.toBeInTheDocument()
  })

  it('renders overridden asterisk when overridden is true', () => {
    const a = makeSignalAssessment({ provenance: 'cross_credited', overridden: true, override_reason: 'Re-checked' })
    render(<SignalAuditTable assessments={[a]} />)
    expect(screen.getByText(/cross_credited \*/)).toBeInTheDocument()
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

  it('renders the score as X.X and a mini-bar with width reflecting the score', () => {
    // score 8.0 → "8.0" text; mini-bar width = 80%
    const a = makeSignalAssessment({ score: 8.0 })
    const { container } = render(<SignalAuditTable assessments={[a]} />)
    // score label
    expect(screen.getByText('8.0')).toBeInTheDocument()
    // mini-bar element: a div with role "presentation" and inline width style
    const bar = container.querySelector('[data-testid="score-mini-bar"]')
    expect(bar).not.toBeNull()
    expect((bar as HTMLElement).style.width).toBe('80%')
  })

  it('renders an em-dash and no mini-bar fill when score is null', () => {
    const a = makeSignalAssessment({ score: null as unknown as number })
    const { container } = render(<SignalAuditTable assessments={[a]} />)
    // em-dash for null score
    expect(screen.getByText('—')).toBeInTheDocument()
    // bar should still be present but with 0% width
    const bar = container.querySelector('[data-testid="score-mini-bar"]')
    expect(bar).not.toBeNull()
    expect((bar as HTMLElement).style.width).toBe('0%')
  })
})
