import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AddQuestionDialog } from './AddQuestionDialog'

describe('AddQuestionDialog', () => {
  it('does NOT render a current-question header', () => {
    render(<AddQuestionDialog open={true} onOpenChange={() => {}}
      onDraft={async () => ({ proposed_text: '', proposed_signal_probed: '', proposed_mandatory: false, proposed_position: 0 })}
      onAccept={() => {}} />)
    // Just verify no element matches "Original question" or similar header text
    expect(screen.queryByText(/original/i)).not.toBeInTheDocument()
  })

  it('user types instruction → submit calls onDraft, shows proposal', async () => {
    const onDraft = vi.fn().mockResolvedValue({
      proposed_text: 'New question about deadlines.',
      proposed_signal_probed: 'behavioral:resilience',
      proposed_mandatory: false,
      proposed_position: 4,
    })
    render(<AddQuestionDialog open={true} onOpenChange={() => {}}
      onDraft={onDraft} onAccept={() => {}} />)
    fireEvent.change(screen.getByLabelText(/describe the question/i),
      { target: { value: 'Behavioral question about deadline pressure' } })
    fireEvent.click(screen.getByRole('button', { name: /draft/i }))
    await waitFor(() => expect(onDraft).toHaveBeenCalled())
    await waitFor(() => screen.getByText(/new question about deadlines/i))
  })

  it('Accept submits with position to onAccept', async () => {
    const onDraft = vi.fn().mockResolvedValue({
      proposed_text: 'Q', proposed_signal_probed: 's', proposed_mandatory: false, proposed_position: 7,
    })
    const onAccept = vi.fn()
    render(<AddQuestionDialog open={true} onOpenChange={() => {}}
      onDraft={onDraft} onAccept={onAccept} />)
    fireEvent.change(screen.getByLabelText(/describe the question/i), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /draft/i }))
    await waitFor(() => screen.getByText(/^q$/i))
    fireEvent.click(screen.getByRole('button', { name: /accept/i }))
    expect(onAccept).toHaveBeenCalledWith({
      text: 'Q', signal_probed: 's', mandatory: false, position: 7,
    })
  })
})
