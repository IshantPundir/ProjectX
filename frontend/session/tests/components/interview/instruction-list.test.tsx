import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { InstructionList, type Instruction } from '@/app/interview/[token]/InstructionList'

function Glyph() {
  return <svg data-testid="glyph" />
}

const items: Instruction[] = [
  { id: 'a', Icon: Glyph, title: 'Meet Arjun', detail: 'Led by a friendly AI.' },
  { id: 'b', Icon: Glyph, title: 'One-time link', detail: 'Used up once you start.', tone: 'caution' },
]

describe('InstructionList', () => {
  it('renders every instruction title', () => {
    render(<InstructionList items={items} />)
    expect(screen.getByText('Meet Arjun')).toBeInTheDocument()
    expect(screen.getByText('One-time link')).toBeInTheDocument()
  })

  it('toggles a row open via aria-expanded on click', async () => {
    const user = userEvent.setup()
    render(<InstructionList items={items} />)
    const row = screen.getByRole('button', { name: /meet arjun/i })
    expect(row).toHaveAttribute('aria-expanded', 'false')
    await user.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'true')
  })

  it('marks a caution row with data-tone', () => {
    render(<InstructionList items={items} />)
    const row = screen.getByRole('button', { name: /one-time link/i })
    expect(row).toHaveAttribute('data-tone', 'caution')
  })
})
