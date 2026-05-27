import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QuestionByQuestion } from '@/components/dashboard/reports/QuestionByQuestion'
import { makeReport } from './_fixture'

describe('QuestionByQuestion', () => {
  it('renders each question with badge, quote, and our-read', () => {
    render(<QuestionByQuestion questions={makeReport().questions} />)
    expect(screen.getByText('How many years of experience do you have?')).toBeInTheDocument()
    expect(screen.getByText('Passed')).toBeInTheDocument()
    expect(screen.getByText('Partial')).toBeInTheDocument()
    expect(screen.getByText(/Comfortably clears/)).toBeInTheDocument()
    expect(screen.getByText(/Around six years\./)).toBeInTheDocument()
  })
})
