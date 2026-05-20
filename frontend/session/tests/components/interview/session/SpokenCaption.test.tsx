import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SpokenCaption } from '@/components/interview/session/SpokenCaption'
import type { RawMessage } from '@/components/interview/session/transcript-model'

const m = (id: string, isLocal: boolean, message: string): RawMessage => ({
  id, timestamp: Number(id), from: { isLocal }, message,
})

describe('SpokenCaption', () => {
  it('shows the latest AI line', () => {
    render(<SpokenCaption messages={[m('1', false, 'Tell me about a project.')]} />)
    expect(screen.getByText('Tell me about a project.')).toBeInTheDocument()
  })
  it('renders nothing when there is no AI line', () => {
    const { container } = render(<SpokenCaption messages={[m('1', true, 'me')]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
