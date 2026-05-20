import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { InterviewSessionPanel } from '@/components/interview/session/InterviewSessionPanel'
import type { RawMessage } from '@/components/interview/session/transcript-model'

const m = (id: string, isLocal: boolean, message: string): RawMessage => ({
  id, timestamp: Number(id), from: { isLocal }, message,
})

const messages = [m('1', false, 'Welcome — introduce yourself.'), m('2', true, 'I am John.')]

describe('InterviewSessionPanel', () => {
  it('is minimized by default: shows the title pill but not the transcript bubbles', () => {
    render(<InterviewSessionPanel messages={messages} agentState="listening" />)
    expect(screen.getByText('Interview Session')).toBeInTheDocument()
    expect(screen.queryByText('I am John.')).not.toBeInTheDocument()
  })

  it('expands to show the conversation when the toggle is clicked', async () => {
    const user = userEvent.setup()
    render(<InterviewSessionPanel messages={messages} agentState="listening" />)
    await user.click(screen.getByRole('button', { name: /open transcript/i }))
    expect(screen.getByText('Welcome — introduce yourself.')).toBeInTheDocument()
    expect(screen.getByText('I am John.')).toBeInTheDocument()
  })

  it('collapses again when the minimize button is clicked', async () => {
    const user = userEvent.setup()
    render(<InterviewSessionPanel messages={messages} agentState="listening" />)
    await user.click(screen.getByRole('button', { name: /open transcript/i }))
    await user.click(screen.getByRole('button', { name: /minimize transcript/i }))
    expect(screen.queryByText('I am John.')).not.toBeInTheDocument()
  })
})
