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
})
