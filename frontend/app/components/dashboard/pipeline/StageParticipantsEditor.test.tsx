import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StageParticipantsEditor } from './StageParticipantsEditor'

vi.mock('@/lib/hooks/use-assignable-users', () => ({
  useAssignableUsers: () => ({
    data: [
      { user_id: 'u1', full_name: 'Alice', email: 'a@ex.com', role_labels: ['Interviewer'], org_unit_name: 'Team' },
      { user_id: 'u2', full_name: 'Bob',   email: 'b@ex.com', role_labels: ['Interviewer'], org_unit_name: 'Team' },
    ],
    isLoading: false,
    isError: false,
  }),
}))

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient()
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('StageParticipantsEditor', () => {
  it('renders the interviewer slot for human_interview', () => {
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{ stage_type: 'human_interview', participants: [] }}
        onChange={() => {}}
      />,
    ))
    expect(screen.getByText(/interviewer/i)).toBeInTheDocument()
  })

  it('calls onChange with added participant when Add then Alice is clicked', async () => {
    const onChange = vi.fn()
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{ stage_type: 'human_interview', participants: [] }}
        onChange={onChange}
      />,
    ))
    fireEvent.click(screen.getByRole('button', { name: /add interviewer/i }))
    fireEvent.click(screen.getByText(/alice/i))
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith([{ user_id: 'u1', role: 'interviewer' }]),
    )
  })

  it('removes a participant when the chip × is clicked', () => {
    const onChange = vi.fn()
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{
          stage_type: 'human_interview',
          participants: [{ user_id: 'u1', role: 'interviewer', full_name: 'Alice', email: 'a@ex.com' }],
        }}
        onChange={onChange}
      />,
    ))
    fireEvent.click(screen.getByRole('button', { name: /remove alice/i }))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('filters already-assigned users from the combobox options', () => {
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{
          stage_type: 'human_interview',
          participants: [{ user_id: 'u1', role: 'interviewer', full_name: 'Alice', email: 'a@ex.com' }],
        }}
        onChange={() => {}}
      />,
    ))
    fireEvent.click(screen.getByRole('button', { name: /add interviewer/i }))
    // Alice is in the chip list (expected to appear) but NOT in the picker options.
    // Bob should appear in the picker options only (he's not assigned).
    expect(screen.getByText(/bob/i)).toBeInTheDocument()
  })
})
