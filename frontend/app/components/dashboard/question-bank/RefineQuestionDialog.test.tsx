import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { RefineQuestionDialog } from './RefineQuestionDialog'

const sampleQuestion = { id: 'q1', text: 'Original question?', signal_probed: 'competency', mandatory: false }

describe('RefineQuestionDialog', () => {
  it('shows current question text on open', () => {
    render(<RefineQuestionDialog open={true} onOpenChange={() => {}} question={sampleQuestion}
      onRefine={async () => ({ proposed_text: '', proposed_signal_probed: '', proposed_mandatory: false })}
      onAccept={() => {}} />)
    expect(screen.getByText(/original question\?/i)).toBeInTheDocument()
  })

  it('user types instruction → submit calls onRefine, then shows proposal', async () => {
    const onRefine = vi.fn().mockResolvedValue({
      proposed_text: 'Refined version of the question.',
      proposed_signal_probed: 'competency:python',
      proposed_mandatory: true,
      rationale: 'Made it stricter.',
    })
    render(<RefineQuestionDialog open={true} onOpenChange={() => {}} question={sampleQuestion}
      onRefine={onRefine} onAccept={() => {}} />)
    fireEvent.change(screen.getByLabelText(/what do you want to change/i),
      { target: { value: 'Make it stricter' } })
    fireEvent.click(screen.getByRole('button', { name: /^refine$/i }))
    await waitFor(() => expect(onRefine).toHaveBeenCalledWith({ instruction: 'Make it stricter' }))
    await waitFor(() => expect(screen.getByText(/refined version of the question/i)).toBeInTheDocument())
  })

  it('Accept button submits proposal to onAccept and closes dialog', async () => {
    const onRefine = vi.fn().mockResolvedValue({
      proposed_text: 'Refined.',
      proposed_signal_probed: 'competency',
      proposed_mandatory: true,
    })
    const onAccept = vi.fn()
    const onOpenChange = vi.fn()
    render(<RefineQuestionDialog open={true} onOpenChange={onOpenChange} question={sampleQuestion}
      onRefine={onRefine} onAccept={onAccept} />)
    fireEvent.change(screen.getByLabelText(/what do you want to change/i), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /^refine$/i }))
    await waitFor(() => screen.getByText(/refined\./i))
    fireEvent.click(screen.getByRole('button', { name: /accept/i }))
    expect(onAccept).toHaveBeenCalledWith({
      text: 'Refined.', signal_probed: 'competency', mandatory: true,
    })
  })

  it('Refine again clears proposal and lets user re-prompt', async () => {
    const onRefine = vi.fn()
      .mockResolvedValueOnce({ proposed_text: 'P1', proposed_signal_probed: 'c', proposed_mandatory: false })
      .mockResolvedValueOnce({ proposed_text: 'P2', proposed_signal_probed: 'c', proposed_mandatory: false })
    render(<RefineQuestionDialog open={true} onOpenChange={() => {}} question={sampleQuestion}
      onRefine={onRefine} onAccept={() => {}} />)
    fireEvent.change(screen.getByLabelText(/what do you want to change/i), { target: { value: 'a' } })
    fireEvent.click(screen.getByRole('button', { name: /^refine$/i }))
    await waitFor(() => screen.getByText(/^p1$/i))
    fireEvent.click(screen.getByRole('button', { name: /refine again/i }))
    fireEvent.change(screen.getByLabelText(/what do you want to change/i), { target: { value: 'b' } })
    fireEvent.click(screen.getByRole('button', { name: /^refine$/i }))
    await waitFor(() => screen.getByText(/^p2$/i))
    expect(onRefine).toHaveBeenCalledTimes(2)
  })

  it('shows loading state while onRefine is pending', async () => {
    let resolveLLM: (v: unknown) => void = () => {}
    const onRefine = vi.fn().mockImplementation(() => new Promise(r => { resolveLLM = r }))
    render(<RefineQuestionDialog open={true} onOpenChange={() => {}} question={sampleQuestion}
      onRefine={onRefine} onAccept={() => {}} />)
    fireEvent.change(screen.getByLabelText(/what do you want to change/i), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /^refine$/i }))
    await waitFor(() => expect(screen.getByText(/refining/i)).toBeInTheDocument())
    resolveLLM({ proposed_text: 'OK', proposed_signal_probed: 'c', proposed_mandatory: false })
    await waitFor(() => screen.getByText(/^ok$/i))
  })

  it('shows error when onRefine throws', async () => {
    const onRefine = vi.fn().mockRejectedValue(new Error('LLM error'))
    render(<RefineQuestionDialog open={true} onOpenChange={() => {}} question={sampleQuestion}
      onRefine={onRefine} onAccept={() => {}} />)
    fireEvent.change(screen.getByLabelText(/what do you want to change/i), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /^refine$/i }))
    await waitFor(() => expect(screen.getByText(/llm error/i)).toBeInTheDocument())
  })
})
