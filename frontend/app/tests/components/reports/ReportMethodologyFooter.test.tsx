import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportMethodologyFooter } from '@/components/dashboard/reports/ReportMethodologyFooter'
import { makeReport } from './_fixture'

describe('ReportMethodologyFooter', () => {
  it('renders the methodology note, charity flags, and manifest line', () => {
    const r = makeReport()
    render(<ReportMethodologyFooter methodology={r.methodology} manifest={r.scoring_manifest} />)
    expect(screen.getByText(/Reached 7 of 8/)).toBeInTheDocument()
    expect(screen.getByText(/long mid-interview silence/)).toBeInTheDocument()
    expect(screen.getByText(/gpt-5.4/)).toBeInTheDocument()
  })
})
