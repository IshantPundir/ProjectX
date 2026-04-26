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

describe('StageConfigDrawer matrix-driven rendering', () => {
  it('intake stage hides duration, difficulty, signal filter, pass criteria editor', () => {
    const stage = { position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(wrap(<StageConfigDrawer stage={stage} onChange={() => {}} onClose={() => {}} />))
    expect(screen.queryByLabelText(/duration/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/difficulty/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/signal types/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/pass criteria/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/advance behavior/i)).not.toBeInTheDocument()
  })

  it('intake stage shows name and sla days fields', () => {
    const stage = { position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(wrap(<StageConfigDrawer stage={stage} onChange={() => {}} onClose={() => {}} />))
    expect(screen.getByLabelText(/^name$/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/stage sla/i)).toBeInTheDocument()
  })

  it('phone_screen stage shows full screening config', () => {
    const stage = {
      position: 1,
      name: 'Phone Screen',
      stage_type: 'phone_screen' as const,
      duration_minutes: 30,
      difficulty: 'medium' as const,
      signal_filter: { include_types: [] },
      pass_criteria: { type: 'all_knockouts_pass' as const },
      advance_behavior: 'auto_advance' as const,
    }
    render(wrap(<StageConfigDrawer stage={stage} onChange={() => {}} onClose={() => {}} />))
    expect(screen.getByLabelText(/duration/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/difficulty/i)).toBeInTheDocument()
    // Signal types label + SignalFilterEditor both contain the phrase — at least one present
    expect(screen.getAllByText(/signal types/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/pass criteria/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/advance behavior/i)).toBeInTheDocument()
  })

  it('debrief stage hides duration, difficulty, signal types', () => {
    const stage = { position: 4, name: 'Debrief', stage_type: 'debrief' as const }
    render(wrap(<StageConfigDrawer stage={stage} onChange={() => {}} onClose={() => {}} />))
    expect(screen.queryByLabelText(/duration/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/difficulty/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/signal types/i)).not.toBeInTheDocument()
  })

  it('debrief stage shows locked pass criteria chip', () => {
    const stage = { position: 4, name: 'Debrief', stage_type: 'debrief' as const }
    render(wrap(<StageConfigDrawer stage={stage} onChange={() => {}} onClose={() => {}} />))
    // The pass_criteria locked chip includes "HM decides" — use that to find it uniquely
    const chip = screen.getByText(/manual review \(hm decides\)/i)
    expect(chip).toBeInTheDocument()
    // The locked chip must have an aria-disabled attribute
    const disabled = chip.closest('[aria-disabled]')
    expect(disabled).toBeTruthy()
  })

  it('Advanced settings section does NOT render for intake (IO category)', () => {
    const stage = { position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(wrap(<StageConfigDrawer stage={stage} onChange={() => {}} onClose={() => {}} />))
    expect(screen.queryByText(/advanced settings/i)).not.toBeInTheDocument()
  })

  it('Advanced settings section DOES render for screening categories', () => {
    render(wrap(<StageConfigDrawer stage={baseStage} onChange={() => {}} onClose={() => {}} />))
    expect(screen.getByText(/advanced settings/i)).toBeInTheDocument()
  })

  it('participants editor renders only when participantSlotsFor returns slots', () => {
    const intakeStage = { position: 0, name: 'Intake', stage_type: 'intake' as const }
    const { rerender } = render(wrap(
      <StageConfigDrawer stage={intakeStage} jobId="j1" onChange={() => {}} onClose={() => {}} />,
    ))
    expect(screen.queryByText(/interviewer/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/reviewer/i)).not.toBeInTheDocument()

    const debriefStage = { position: 4, name: 'Debrief', stage_type: 'debrief' as const }
    rerender(wrap(
      <StageConfigDrawer stage={debriefStage} jobId="j1" onChange={() => {}} onClose={() => {}} />,
    ))
    // Debrief needs reviewers — the participants editor should render with the reviewer slot
    expect(screen.getByText(/^Reviewer$/)).toBeInTheDocument()
  })
})
