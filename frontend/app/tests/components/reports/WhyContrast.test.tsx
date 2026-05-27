import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WhyContrast } from '@/components/dashboard/reports/WhyContrast'
import { makeReport } from './_fixture'

describe('WhyContrast', () => {
  it('renders both columns with titles and bodies', () => {
    render(<WhyContrast decision={makeReport().decision} />)
    expect(screen.getByText('Foundations are there')).toBeInTheDocument()
    expect(screen.getByText('Meets the experience bar.')).toBeInTheDocument()
    expect(screen.getByText('But depth was not shown')).toBeInTheDocument()
    expect(screen.getByText('Technical answers stayed thin.')).toBeInTheDocument()
  })
})
