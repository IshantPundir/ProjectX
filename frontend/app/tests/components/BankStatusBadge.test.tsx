import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BankStatusBadge } from '@/components/dashboard/question-bank/BankStatusBadge'
import type { BankStatus } from '@/lib/api/question-banks'

describe('BankStatusBadge', () => {
  const statuses: { status: BankStatus; label: string }[] = [
    { status: 'draft', label: 'DRAFT' },
    { status: 'generating', label: 'GENERATING' },
    { status: 'self_reviewing', label: 'SELF-REVIEW' },
    { status: 'reviewing', label: 'REVIEWING' },
    { status: 'confirmed', label: 'CONFIRMED' },
    { status: 'failed', label: 'FAILED' },
  ]

  statuses.forEach(({ status, label }) => {
    it(`renders ${status} with correct label`, () => {
      const { getByText } = render(<BankStatusBadge status={status} />)
      expect(getByText(label)).toBeInTheDocument()
    })
  })

  it('renders generating with an animated spinner', () => {
    const { container } = render(<BankStatusBadge status="generating" />)
    expect(container.querySelector('.animate-spin')).not.toBeNull()
  })

  it('renders self_reviewing with an animated spinner', () => {
    const { container } = render(<BankStatusBadge status="self_reviewing" />)
    expect(container.querySelector('.animate-spin')).not.toBeNull()
  })

  it('renders small variant with smaller text class', () => {
    const { container } = render(<BankStatusBadge status="confirmed" small />)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toContain('text-[9px]')
  })
})
