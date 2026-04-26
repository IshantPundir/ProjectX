import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StageConfigurationTab } from './StageConfigurationTab'

vi.mock('@/lib/hooks/use-assignable-users', () => ({
  useAssignableUsers: () => ({ data: [], isLoading: false, isError: false }),
}))

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient()
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

const baseProps = {
  onChange: () => {},
  jobId: 'j1',
}

describe('StageConfigurationTab matrix-driven rendering', () => {
  it('intake stage hides duration, difficulty, signal filter, pass_criteria editor', () => {
    const stage = { id: 's0', position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} />))
    expect(screen.queryByLabelText(/duration/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/difficulty/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/signal types/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^pass criteria$/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^advance behavior$/i)).not.toBeInTheDocument()
  })

  it('intake stage shows name and sla days', () => {
    const stage = { id: 's0', position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} />))
    expect(screen.getByLabelText(/^name$/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/stage sla/i)).toBeInTheDocument()
  })

  it('phone_screen stage shows full config', () => {
    const stage = {
      id: 's1',
      position: 1,
      name: 'Phone Screen',
      stage_type: 'phone_screen' as const,
      duration_minutes: 30,
      difficulty: 'medium' as const,
      signal_filter: { include_types: [] as ('competency' | 'experience' | 'credential' | 'behavioral')[] },
      pass_criteria: { type: 'all_knockouts_pass' as const },
      advance_behavior: 'auto_advance' as const,
    }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} />))
    expect(screen.getByLabelText(/duration/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/difficulty/i)).toBeInTheDocument()
    // Signal types label is visible inside the advanced section (need to open it)
    // Pass criteria and advance behavior are inside advanced section — expand it first
    fireEvent.click(screen.getByText(/advanced settings/i))
    expect(screen.getByText(/^pass criteria$/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/advance behavior/i)).toBeInTheDocument()
    expect(screen.getAllByText(/signal types/i).length).toBeGreaterThan(0)
  })

  it('debrief stage shows locked pass_criteria as disabled', () => {
    const stage = { id: 's4', position: 4, name: 'Debrief', stage_type: 'debrief' as const }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} />))
    const chip = screen.getByText(/manual review \(hm decides\)/i)
    expect(chip).toBeInTheDocument()
    const disabled = chip.closest('[aria-disabled]')
    expect(disabled).toBeTruthy()
  })

  it('debrief stage hides duration, difficulty, signal types', () => {
    const stage = { id: 's4', position: 4, name: 'Debrief', stage_type: 'debrief' as const }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} />))
    expect(screen.queryByLabelText(/duration/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/difficulty/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/signal types/i)).not.toBeInTheDocument()
  })

  it('Advanced settings section does NOT render for intake (IO category)', () => {
    const stage = { id: 's0', position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} />))
    expect(screen.queryByText(/advanced settings/i)).not.toBeInTheDocument()
  })

  it('Advanced settings section DOES render for screening categories', () => {
    const stage = {
      id: 's1',
      position: 1,
      name: 'Phone Screen',
      stage_type: 'phone_screen' as const,
      duration_minutes: 30,
      difficulty: 'medium' as const,
      signal_filter: { include_types: [] as ('competency' | 'experience' | 'credential' | 'behavioral')[] },
      pass_criteria: { type: 'all_knockouts_pass' as const },
      advance_behavior: 'auto_advance' as const,
    }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} />))
    expect(screen.getByText(/advanced settings/i)).toBeInTheDocument()
  })

  it('strips mismatched participants when stage_type changes category', () => {
    const onChange = vi.fn()
    const stage = {
      id: 's1',
      position: 1,
      name: 'Human Interview',
      stage_type: 'human_interview' as const,
      duration_minutes: 30,
      difficulty: 'medium' as const,
      signal_filter: { include_types: [] as ('competency' | 'experience' | 'credential' | 'behavioral')[] },
      pass_criteria: { type: 'all_knockouts_pass' as const },
      advance_behavior: 'auto_advance' as const,
      participants: [{ user_id: 'u1', role: 'interviewer' as const }],
    }
    render(wrap(<StageConfigurationTab {...baseProps} stage={stage} onChange={onChange} />))
    fireEvent.change(screen.getByLabelText(/stage type/i), { target: { value: 'debrief' } })
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0]
    expect(lastCall.stage_type).toBe('debrief')
    expect(lastCall.participants).toEqual([])
  })

  it('does NOT render participants editor when jobId is absent', () => {
    const stage = {
      id: 's1',
      position: 1,
      name: 'Human Interview',
      stage_type: 'human_interview' as const,
      duration_minutes: 30,
      difficulty: 'medium' as const,
      signal_filter: { include_types: [] as ('competency' | 'experience' | 'credential' | 'behavioral')[] },
      pass_criteria: { type: 'all_knockouts_pass' as const },
      advance_behavior: 'auto_advance' as const,
    }
    render(wrap(<StageConfigurationTab stage={stage} onChange={() => {}} />))
    expect(screen.queryByText(/^Interviewer$/)).not.toBeInTheDocument()
  })
})
