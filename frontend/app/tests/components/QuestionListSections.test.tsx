/**
 * Composition test: QuestionList + QuestionCard rendered together (no stubs for
 * child components). Mocks at the API/hook boundary — the project convention.
 *
 * Covers:
 *  - Behavioral / Technical section headers render
 *  - Questions are routed to the correct section by question_kind
 *  - Per-section SectionStatus pills reflect generation_status_by_kind
 *  - Negative control: a bank with only technical questions renders NO
 *    empty Behavioral section
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { QuestionList } from '@/components/dashboard/question-bank/QuestionList'
import type { BankWithQuestionsResponse, QuestionResponse } from '@/lib/api/question-banks'

// ---- Stub hooks used inside QuestionCard ---------------------------------
// QuestionCard calls useDeleteQuestion and useRegenerateQuestion.
// We mock at the hook boundary so these tests don't hit the network.

vi.mock('@/lib/hooks/use-save-question', () => ({
  useDeleteQuestion: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
  }),
}))

vi.mock('@/lib/hooks/use-regenerate-question', () => ({
  useRegenerateQuestion: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
  }),
}))

// ---- Helpers ---------------------------------------------------------------

function makeQuestion(overrides: Partial<QuestionResponse>): QuestionResponse {
  return {
    id: 'q-default',
    bank_id: 'bank-1',
    position: 0,
    source: 'ai_generated',
    text: 'Default question text.',
    signal_values: ['signal:a'],
    estimated_minutes: 5,
    is_mandatory: false,
    follow_ups: [],
    positive_evidence: [],
    red_flags: [],
    rubric: { excellent: '', meets_bar: '', below_bar: '' },
    evaluation_hint: 'Hint.',
    edited_by_recruiter: false,
    question_kind: 'behavioral',
    primary_signal: null,
    difficulty: null,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-01T00:00:00Z',
    ...overrides,
  }
}

function makeBank(
  questions: QuestionResponse[],
  generation_status_by_kind: Record<string, string> = {},
): BankWithQuestionsResponse {
  return {
    id: 'bank-1',
    stage_id: 'stage-1',
    job_posting_id: 'job-1',
    signal_snapshot_id: 'snap-1',
    status: 'reviewing',
    prompt_version: 'v2',
    generation_error: null,
    coverage_notes: null,
    generated_at: '2026-05-01T00:00:00Z',
    generated_by: 'system',
    confirmed_at: null,
    confirmed_by: null,
    question_count: questions.length,
    total_minutes: questions.reduce((s, q) => s + q.estimated_minutes, 0),
    is_stale: false,
    generation_status_by_kind,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-01T00:00:00Z',
    questions,
  }
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

// ---- Tests -----------------------------------------------------------------

describe('QuestionList — Behavioral / Technical sections', () => {
  const MIXED_BANK = makeBank(
    [
      makeQuestion({
        id: 'q-exp',
        question_kind: 'experience_check',
        text: 'How long have you used Python?',
        position: 0,
      }),
      makeQuestion({
        id: 'q-beh',
        question_kind: 'behavioral',
        text: 'Tell me about a time you led a project.',
        position: 1,
      }),
      makeQuestion({
        id: 'q-tech',
        question_kind: 'technical_scenario',
        text: 'How would you design a rate-limiter?',
        position: 2,
      }),
    ],
    { behavioral: 'reviewing', technical: 'generating' },
  )

  it('renders Behavioral and Technical section headers', () => {
    renderWithClient(
      <QuestionList jobId="job-1" stageId="stage-1" bank={MIXED_BANK} />,
    )
    expect(screen.getByText('Behavioral')).toBeInTheDocument()
    expect(screen.getByText('Technical')).toBeInTheDocument()
  })

  it('places experience_check and behavioral questions under the Behavioral section', () => {
    renderWithClient(
      <QuestionList jobId="job-1" stageId="stage-1" bank={MIXED_BANK} />,
    )
    const behavioralSection = screen.getByRole('region', { name: 'Behavioral' })
    expect(behavioralSection).toContainElement(
      screen.getByText('How long have you used Python?'),
    )
    expect(behavioralSection).toContainElement(
      screen.getByText('Tell me about a time you led a project.'),
    )
  })

  it('places technical_scenario questions under the Technical section', () => {
    renderWithClient(
      <QuestionList jobId="job-1" stageId="stage-1" bank={MIXED_BANK} />,
    )
    const technicalSection = screen.getByRole('region', { name: 'Technical' })
    expect(technicalSection).toContainElement(
      screen.getByText('How would you design a rate-limiter?'),
    )
  })

  it('renders Technical section with generating status pill', () => {
    renderWithClient(
      <QuestionList jobId="job-1" stageId="stage-1" bank={MIXED_BANK} />,
    )
    const technicalSection = screen.getByRole('region', { name: 'Technical' })
    expect(technicalSection).toContainElement(
      screen.getByText('Generating…'),
    )
  })

  it('renders Behavioral section with Ready status pill when status is reviewing', () => {
    renderWithClient(
      <QuestionList jobId="job-1" stageId="stage-1" bank={MIXED_BANK} />,
    )
    const behavioralSection = screen.getByRole('region', { name: 'Behavioral' })
    expect(behavioralSection).toContainElement(
      screen.getByText('Ready'),
    )
  })

  // ---- Negative control ----------------------------------------------------
  it('does NOT render a Behavioral section when the bank has only technical questions', () => {
    const techOnlyBank = makeBank(
      [
        makeQuestion({
          id: 'q-t1',
          question_kind: 'technical_scenario',
          text: 'Explain consistent hashing.',
          position: 0,
        }),
      ],
      { technical: 'reviewing' },
    )
    renderWithClient(
      <QuestionList jobId="job-1" stageId="stage-1" bank={techOnlyBank} />,
    )
    expect(screen.queryByText('Behavioral')).not.toBeInTheDocument()
    expect(screen.getByText('Technical')).toBeInTheDocument()
  })
})
