import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QuestionByQuestion } from '@/components/dashboard/reports/QuestionByQuestion'
import { makeQuestion, makeReport } from './_fixture'

describe('QuestionByQuestion', () => {
  it('renders each question with badge, quote, and our-read', () => {
    render(<QuestionByQuestion questions={makeReport().questions} />)
    expect(screen.getByText('How many years of experience do you have?')).toBeInTheDocument()
    expect(screen.getByText('Passed')).toBeInTheDocument()
    expect(screen.getByText('Partial')).toBeInTheDocument()
    expect(screen.getByText(/Comfortably clears/)).toBeInTheDocument()
    expect(screen.getByText(/Around six years\./)).toBeInTheDocument()
  })

  it('renders new rubric fields: difficulty chip, listen-for hits, red flags, probe count', () => {
    const q = makeQuestion({
      difficulty: 'hard',
      listen_for_hits: ['Checks certificate profiles'],
      red_flags_tripped: ['Does not know Wi-Fi/cert handling'],
      probes_used: 3,
      probes_available: 3,
    })
    render(<QuestionByQuestion questions={[q]} />)
    // difficulty chip
    expect(screen.getByText('hard')).toBeInTheDocument()
    // listen-for text
    expect(screen.getByText('Checks certificate profiles')).toBeInTheDocument()
    // red flag text
    expect(screen.getByText('Does not know Wi-Fi/cert handling')).toBeInTheDocument()
    // probe usage
    expect(screen.getByText('3/3 probes')).toBeInTheDocument()
  })

  it('renders nothing for empty listen-for, red-flags, and zero probes_available', () => {
    const q = makeQuestion({
      listen_for_hits: [],
      red_flags_tripped: [],
      probes_available: 0,
    })
    render(<QuestionByQuestion questions={[q]} />)
    expect(screen.queryByText(/Listen for/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Red flags/)).not.toBeInTheDocument()
    expect(screen.queryByText(/probes/)).not.toBeInTheDocument()
  })
})
