import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SignalAuditTable } from '@/components/dashboard/reports/SignalAuditTable'
import { makeReport } from './_fixture'

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
})
