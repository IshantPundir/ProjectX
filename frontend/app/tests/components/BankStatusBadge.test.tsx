import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BankStatusBadge } from '@/components/dashboard/question-bank/BankStatusBadge'
import type { BankStatus } from '@/lib/api/question-banks'

describe('BankStatusBadge', () => {
  const statuses: BankStatus[] = [
    'draft',
    'generating',
    'reviewing',
    'confirmed',
    'failed',
  ]

  statuses.forEach((status) => {
    it(`renders ${status} with correct label`, () => {
      const { getByText } = render(<BankStatusBadge status={status} />)
      expect(getByText(status.toUpperCase())).toBeInTheDocument()
    })
  })

  it('renders generating with an animated spinner', () => {
    const { container } = render(<BankStatusBadge status="generating" />)
    expect(container.querySelector('.animate-spin')).not.toBeNull()
  })

  it('renders small variant with smaller text class', () => {
    const { container } = render(<BankStatusBadge status="confirmed" small />)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toContain('text-[9px]')
  })
})
