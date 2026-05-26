import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { HumanDecisionPanel } from '@/components/dashboard/reports/HumanDecisionPanel'

describe('HumanDecisionPanel', () => {
  it('disables submit until a decision is chosen AND a rationale is entered', () => {
    render(<HumanDecisionPanel verdict="reject" decision={null} onSubmit={vi.fn()} isSubmitting={false} />)
    const submit = screen.getByRole('button', { name: /record decision/i })
    expect(submit).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /^reject$/i }))
    expect(submit).toBeDisabled() // rationale still empty
    fireEvent.change(screen.getByLabelText(/rationale/i), { target: { value: 'agrees with the evidence' } })
    expect(submit).toBeEnabled()
  })

  it('borderline shows the locked, required-review notice (never one-click)', () => {
    render(<HumanDecisionPanel verdict="borderline" decision={null} onSubmit={vi.fn()} isSubmitting={false} />)
    expect(screen.getByText(/requires a human decision/i)).toBeInTheDocument()
    // no quick-action button exists; the only path is the rationale-gated form
    expect(screen.getByRole('button', { name: /record decision/i })).toBeDisabled()
  })

  it('calls onSubmit with the chosen decision + rationale', () => {
    const onSubmit = vi.fn()
    render(<HumanDecisionPanel verdict="advance" decision={null} onSubmit={onSubmit} isSubmitting={false} />)
    fireEvent.click(screen.getByRole('button', { name: /^advance$/i }))
    fireEvent.change(screen.getByLabelText(/rationale/i), { target: { value: 'strong signals' } })
    fireEvent.click(screen.getByRole('button', { name: /record decision/i }))
    expect(onSubmit).toHaveBeenCalledWith('advance', 'strong signals')
  })

  it('renders the recorded decision + a Change decision affordance when already decided', () => {
    render(
      <HumanDecisionPanel
        verdict="reject"
        decision={{ decided_by: 'u1', decision: 'reject', rationale: 'weak', decided_at: '2026-05-26T00:00:00Z' }}
        onSubmit={vi.fn()}
        isSubmitting={false}
      />,
    )
    expect(screen.getByText(/decision recorded/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /change decision/i })).toBeInTheDocument()
  })
})
