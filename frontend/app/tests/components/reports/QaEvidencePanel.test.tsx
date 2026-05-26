import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QaEvidencePanel } from '@/components/dashboard/reports/QaEvidencePanel'
import type { QuestionScorecard } from '@/lib/api/reports'

const qs: QuestionScorecard[] = [
  {
    question_id: 'q_python', question_text: 'Tell me about your Python depth.',
    level: 'below_bar', red_flags_hit: ['no concrete depth'], probes_fired: 1, opportunity: 'full',
    evidence: [{ quote: 'ChatGPT writes most of it', timestamp_ms: 252000, question_id: 'q_python', grounded: true }],
  },
]

describe('QaEvidencePanel', () => {
  it('lists each question with its level and evidence quote', () => {
    render(<QaEvidencePanel questionScorecards={qs} />)
    expect(screen.getByText(/Tell me about your Python depth/)).toBeInTheDocument()
    expect(screen.getByText(/ChatGPT writes most of it/)).toBeInTheDocument()
  })
  it('has a Q&A and an Evidence tab', () => {
    render(<QaEvidencePanel questionScorecards={qs} />)
    expect(screen.getByRole('tab', { name: /q&a/i })).toBeInTheDocument()
    const evTab = screen.getByRole('tab', { name: /evidence/i })
    fireEvent.click(evTab)
    expect(screen.getByText('04:12')).toBeInTheDocument()
  })
})
