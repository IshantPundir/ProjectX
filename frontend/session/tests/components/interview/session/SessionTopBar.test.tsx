import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SessionTopBar } from '@/components/interview/session/SessionTopBar'

describe('SessionTopBar', () => {
  it('shows company, role, recording indicator and an End control', () => {
    render(<SessionTopBar companyName="Acme" jobTitle="Senior Engineer" onEnd={vi.fn()} />)
    expect(screen.getByText(/Acme · Senior Engineer/)).toBeInTheDocument()
    expect(screen.getByText(/Recording/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /end interview/i })).toBeInTheDocument()
  })
})
