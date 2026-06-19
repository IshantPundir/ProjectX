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

  // --- B7 hero-star restyle ---

  it('renders the FULL question_text (never truncated) for a long question', () => {
    const longQuestion =
      'A company has 800 Intune-managed phones, and new iPhones are getting enrolled but not showing the expected settings — walk me through how you would diagnose and fix the Intune configuration.'
    const q = makeQuestion({ question_text: longQuestion })
    render(<QuestionByQuestion questions={[q]} />)
    expect(screen.getByText(longQuestion)).toBeInTheDocument()
  })

  it('does NOT render q.title as visible text', () => {
    const q = makeQuestion({ title: 'A short truncated title', question_text: 'The full question text here.' })
    render(<QuestionByQuestion questions={[q]} />)
    // title must not appear as standalone text; question_text must appear
    expect(screen.queryByText('A short truncated title')).not.toBeInTheDocument()
    expect(screen.getByText('The full question text here.')).toBeInTheDocument()
  })

  it('renders StarRating with correct label when score=8 (4 out of 5)', () => {
    const q = makeQuestion({ score: 8 })
    render(<QuestionByQuestion questions={[q]} />)
    expect(screen.getByLabelText('4 out of 5')).toBeInTheDocument()
    // X.X / 5 label present
    expect(screen.getByText('4.0 / 5')).toBeInTheDocument()
  })

  it('renders "Not assessed" chip and no star label when score is null', () => {
    const q = makeQuestion({ score: null })
    render(<QuestionByQuestion questions={[q]} />)
    expect(screen.getByText('Not assessed')).toBeInTheDocument()
    // no star role=img with "out of 5" label
    expect(screen.queryByLabelText(/out of 5/)).not.toBeInTheDocument()
  })

  it('renders "Not assessed" chip and no star label when score is undefined', () => {
    // score field is optional on QuestionOut — undefined means not yet assessed
    const q = makeQuestion()
    // makeQuestion doesn't set score, so it's undefined
    delete (q as Partial<typeof q>).score
    render(<QuestionByQuestion questions={[q]} />)
    expect(screen.getByText('Not assessed')).toBeInTheDocument()
    expect(screen.queryByLabelText(/out of 5/)).not.toBeInTheDocument()
  })

  it('renders listen-for hits as green pills', () => {
    const q = makeQuestion({ listen_for_hits: ['Assignment groups', 'ABM token sync'] })
    render(<QuestionByQuestion questions={[q]} />)
    expect(screen.getByText('Assignment groups')).toBeInTheDocument()
    expect(screen.getByText('ABM token sync')).toBeInTheDocument()
  })

  it('renders red-flag pills for tripped flags', () => {
    const q = makeQuestion({ red_flags_tripped: ['Missed profile conflicts'] })
    render(<QuestionByQuestion questions={[q]} />)
    expect(screen.getByText('Missed profile conflicts')).toBeInTheDocument()
  })
})
