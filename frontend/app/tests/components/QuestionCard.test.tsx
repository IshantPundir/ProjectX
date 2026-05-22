import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { QuestionCard } from '@/components/dashboard/question-bank/QuestionCard'
import type { QuestionResponse } from '@/lib/api/question-banks'

function makeQuestion(
  overrides: Partial<QuestionResponse> = {},
): QuestionResponse {
  return {
    id: 'q1',
    bank_id: 'b1',
    position: 0,
    source: 'ai_generated',
    text: 'Walk me through a production incident.',
    signal_values: ['Incident response'],
    estimated_minutes: 5,
    is_mandatory: false,
    follow_ups: ['What tools did you use?'],
    positive_evidence: [
      'Names specific tools',
      'Describes hypothesis-verify',
      'Mentions post-mortem',
    ],
    red_flags: ['No specific tools', 'Blames team'],
    rubric: {
      excellent:
        'Strong answer with specific tooling and hypothesis-verify approach.',
      meets_bar: 'Acceptable answer with some structure and tools mentioned.',
      below_bar: 'Vague answer with no specific tools or structure.',
    },
    evaluation_hint: 'Strong = names observability tools + structured debugging.',
    edited_by_recruiter: false,
    question_kind: 'behavioral',
    primary_signal: null,
    difficulty: null,
    created_at: '2026-04-12T00:00:00Z',
    updated_at: '2026-04-12T00:00:00Z',
    ...overrides,
  }
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('QuestionCard', () => {
  it('renders the question text and signals', () => {
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion()}
        expanded={false}
        onToggleExpand={() => {}}
      />,
    )
    expect(
      screen.getByText('Walk me through a production incident.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/Incident response/)).toBeInTheDocument()
  })

  it('shows MANDATORY badge for mandatory questions', () => {
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion({ is_mandatory: true })}
        expanded={false}
        onToggleExpand={() => {}}
      />,
    )
    expect(screen.getByText('MANDATORY')).toBeInTheDocument()
  })

  it('shows CUSTOM badge for recruiter-sourced questions', () => {
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion({ source: 'recruiter' })}
        expanded={false}
        onToggleExpand={() => {}}
      />,
    )
    expect(screen.getByText('CUSTOM')).toBeInTheDocument()
  })

  it('calls onToggleExpand when the card is clicked', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion()}
        expanded={false}
        onToggleExpand={onToggle}
      />,
    )
    await user.click(screen.getByText('Walk me through a production incident.'))
    expect(onToggle).toHaveBeenCalled()
  })
})
