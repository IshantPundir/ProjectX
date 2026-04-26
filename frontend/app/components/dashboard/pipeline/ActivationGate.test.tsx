import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ActivationGate } from './ActivationGate'

describe('ActivationGate', () => {
  it('shows amber strip with failures + disabled button when not ready', () => {
    render(<ActivationGate failures={[
      { code: 'missing_interviewer', message: 'Add an interviewer to Phone Screen', stage_id: 's1' },
      { code: 'missing_bank', message: 'Generate a question bank for AI Screening', stage_id: 's2' },
    ]} onActivate={() => {}} onFocusStage={() => {}} />)
    expect(screen.getByText(/2 things needed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /activate/i })).toBeDisabled()
  })

  it('shows green strip and enables Activate when no failures', () => {
    render(<ActivationGate failures={[]} onActivate={() => {}} onFocusStage={() => {}} />)
    expect(screen.getByText(/ready to activate/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /activate/i })).not.toBeDisabled()
  })

  it('clicking a failure with stage_id calls onFocusStage', () => {
    const onFocus = vi.fn()
    render(<ActivationGate failures={[
      { code: 'missing_interviewer', message: 'Add an interviewer', stage_id: 's1' },
    ]} onActivate={() => {}} onFocusStage={onFocus} />)
    fireEvent.click(screen.getByRole('button', { name: /add an interviewer/i }))
    expect(onFocus).toHaveBeenCalledWith('s1')
  })

  it('clicking a failure with no stage_id does NOT call onFocusStage', () => {
    const onFocus = vi.fn()
    render(<ActivationGate failures={[
      { code: 'no_pipeline', message: 'Pipeline not yet built', stage_id: null },
    ]} onActivate={() => {}} onFocusStage={onFocus} />)
    // Either it's not a button at all, or clicking it doesn't fire onFocus
    const item = screen.queryByRole('button', { name: /pipeline not yet built/i })
    if (item) {
      fireEvent.click(item)
      expect(onFocus).not.toHaveBeenCalled()
    }
    // Otherwise, just having the message visible is fine
    expect(screen.getByText(/pipeline not yet built/i)).toBeInTheDocument()
  })

  it('clicking Activate calls onActivate when ready', () => {
    const onActivate = vi.fn()
    render(<ActivationGate failures={[]} onActivate={onActivate} onFocusStage={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /activate/i }))
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it('singular vs plural copy adapts to failure count', () => {
    const { rerender } = render(<ActivationGate failures={[
      { code: 'x', message: 'm', stage_id: 's1' },
    ]} onActivate={() => {}} onFocusStage={() => {}} />)
    expect(screen.getByText(/1 thing needed/i)).toBeInTheDocument()

    rerender(<ActivationGate failures={[
      { code: 'a', message: '1', stage_id: 's1' },
      { code: 'b', message: '2', stage_id: 's2' },
    ]} onActivate={() => {}} onFocusStage={() => {}} />)
    expect(screen.getByText(/2 things needed/i)).toBeInTheDocument()
  })
})
