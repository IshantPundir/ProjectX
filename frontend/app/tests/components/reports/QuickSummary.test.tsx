import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QuickSummary } from '@/components/dashboard/reports/QuickSummary'

describe('QuickSummary', () => {
  it('renders the narrative text', () => {
    render(<QuickSummary text="This candidate sits right on the line." />)
    expect(screen.getByText('This candidate sits right on the line.')).toBeInTheDocument()
  })
  it('renders nothing when text is empty', () => {
    const { container } = render(<QuickSummary text="" />)
    expect(container.firstChild).toBeNull()
  })
})
