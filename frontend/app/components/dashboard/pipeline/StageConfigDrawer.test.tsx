import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StageConfigDrawer } from './StageConfigDrawer'

vi.mock('@/lib/hooks/use-assignable-users', () => ({
  useAssignableUsers: () => ({ data: [], isLoading: false, isError: false }),
}))

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient()
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

const baseStage = {
  position: 0,
  name: 'Test',
  stage_type: 'human_interview' as const,
  duration_minutes: 30,
  difficulty: 'medium' as const,
  signal_filter: { include_types: [] },
  pass_criteria: { type: 'manual_review' as const },
  advance_behavior: 'manual_review' as const,
  participants: [],
}

describe('StageConfigDrawer', () => {
  it('does NOT render the participants editor when jobId is absent', () => {
    render(wrap(
      <StageConfigDrawer stage={baseStage} onChange={() => {}} onClose={() => {}} />,
    ))
    // Interviewer label only renders inside the participants editor section.
    expect(screen.queryByText(/^Interviewer$/)).not.toBeInTheDocument()
  })

  it('renders the participants editor when jobId is present and category has slots', () => {
    render(wrap(
      <StageConfigDrawer
        stage={baseStage}
        jobId="j1"
        onChange={() => {}}
        onClose={() => {}}
      />,
    ))
    expect(screen.getByText(/^Interviewer$/)).toBeInTheDocument()
  })

  it('strips mismatched participants when stage_type changes category', () => {
    const onChange = vi.fn()
    const stageWithInterviewer = {
      ...baseStage,
      participants: [{ user_id: 'u1', role: 'interviewer' as const }],
    }
    render(wrap(
      <StageConfigDrawer
        stage={stageWithInterviewer}
        jobId="j1"
        onChange={onChange}
        onClose={() => {}}
      />,
    ))
    // Change the type to debrief, whose slot is "reviewer" — the interviewer
    // participant should be stripped because its role no longer matches.
    fireEvent.change(screen.getByLabelText(/stage type/i), { target: { value: 'debrief' } })
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0]
    expect(lastCall.stage_type).toBe('debrief')
    expect(lastCall.participants).toEqual([])
  })
})
