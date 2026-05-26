import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EvidenceQuote } from '@/components/dashboard/reports/EvidenceQuote'

const ev = { quote: 'I have sixteen years', timestamp_ms: 90000, question_id: 'years_experience', grounded: true }

describe('EvidenceQuote', () => {
  it('renders the quote, mm:ss timestamp, and question id', () => {
    render(<EvidenceQuote evidence={ev} />)
    expect(screen.getByText(/I have sixteen years/)).toBeInTheDocument()
    expect(screen.getByText('01:30')).toBeInTheDocument()
    expect(screen.getByText(/years_experience/)).toBeInTheDocument()
  })
  it('marks ungrounded evidence with a warning', () => {
    render(<EvidenceQuote evidence={{ ...ev, grounded: false }} />)
    expect(screen.getByLabelText(/unverified/i)).toBeInTheDocument()
  })
  it('the timestamp chip is inert today (no seek handler) and labelled as future playback', () => {
    render(<EvidenceQuote evidence={ev} />)
    const chip = screen.getByText('01:30').closest('[data-seek-stub]')
    expect(chip).not.toBeNull()
  })
})
