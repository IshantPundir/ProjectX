import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ThisMomentPanel } from '@/components/dashboard/reports/theater/ThisMomentPanel'
import type { DecisionOut, QuestionOut } from '@/lib/api/reports'

const decision: DecisionOut = {
  headline: 'Closed early on agent experience',
  why_positive: { title: 'Relevant platform experience', body: '~6 yrs' },
  why_negative: { title: 'No agent experience', body: 'never worked with AI agents' },
}

const question: QuestionOut = {
  seq: 3, question_id: 'q3', title: 'AI triage', status_badge: 'partial', status_tone: 'caution',
  question_text: 'How would you design the flow?', candidate_quote: 'extract metadata…',
  our_read: 'workable but no validation', asked_at_ms: 74_000, thumbnail_url: null,
}

describe('ThisMomentPanel', () => {
  it('shows the decision summary by default', () => {
    render(<ThisMomentPanel selection={null} decision={decision} onJump={() => {}} />)
    expect(screen.getByText(/Closed early on agent experience/)).toBeInTheDocument()
    expect(screen.getByText(/No agent experience/)).toBeInTheDocument()
  })

  it('shows the question read when a question is selected', () => {
    render(
      <ThisMomentPanel selection={{ type: 'question', question }} decision={decision} onJump={() => {}} />,
    )
    expect(screen.getByText(/How would you design the flow/)).toBeInTheDocument()
    expect(screen.getByText(/extract metadata/)).toBeInTheDocument()
    expect(screen.getByText(/workable but no validation/)).toBeInTheDocument()
  })

  it('shows stars/score, difficulty, probes and observations for a scored question', () => {
    const rich: QuestionOut = {
      ...question,
      score: 7,
      difficulty: 'hard',
      probes_used: 2,
      probes_available: 3,
      listen_for_hits: ['Mentioned idempotency'],
      red_flags_tripped: ['Hand-waved on scale'],
    }
    render(
      <ThisMomentPanel selection={{ type: 'question', question: rich }} decision={decision} onJump={() => {}} />,
    )
    expect(screen.getByRole('img', { name: /3\.5 out of 5/i })).toBeInTheDocument()
    expect(screen.getByText('3.5 / 5')).toBeInTheDocument()
    expect(screen.getByText('hard')).toBeInTheDocument()
    expect(screen.getByText('2/3 probes')).toBeInTheDocument()
    expect(screen.getByText(/Mentioned idempotency/)).toBeInTheDocument()
    expect(screen.getByText(/Hand-waved on scale/)).toBeInTheDocument()
  })

  it('shows "Not assessed" when the question has no score', () => {
    render(
      <ThisMomentPanel selection={{ type: 'question', question }} decision={decision} onJump={() => {}} />,
    )
    expect(screen.getByText('Not assessed')).toBeInTheDocument()
  })

  it('renders the agent verdict (humanized closure + grade) when present', () => {
    const graded: QuestionOut = { ...question, closure: 'tapped_out', level: 'strong' }
    render(
      <ThisMomentPanel selection={{ type: 'question', question: graded }} decision={decision} onJump={() => {}} />,
    )
    expect(screen.getByText('Agent verdict')).toBeInTheDocument()
    expect(screen.getByText('Tapped out')).toBeInTheDocument()
    expect(screen.getByText('Strong')).toBeInTheDocument()
  })

  it('omits the agent verdict when closure is null and no grade exists', () => {
    const ungraded: QuestionOut = { ...question, closure: null }
    render(
      <ThisMomentPanel selection={{ type: 'question', question: ungraded }} decision={decision} onJump={() => {}} />,
    )
    expect(screen.queryByText('Agent verdict')).not.toBeInTheDocument()
    // still renders the rest of the panel without crashing
    expect(screen.getByText(/How would you design the flow/)).toBeInTheDocument()
  })
})
