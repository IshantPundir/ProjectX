import { render, screen, fireEvent, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ViolationNoticeOverlay } from '@/components/interview/proctoring/ViolationNoticeOverlay'

afterEach(() => vi.useRealTimers())

describe('ViolationNoticeOverlay', () => {
  it('shows the violation label and the warning count', () => {
    render(
      <ViolationNoticeOverlay kind="keyboard" softCount={2} limit={3} onAcknowledge={vi.fn()} />,
    )
    expect(screen.getByText(/keyboard activity/i)).toBeInTheDocument()
    expect(screen.getByText(/warning 2 of 3/i)).toBeInTheDocument()
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
  })

  it('calls onAcknowledge when the button is clicked', () => {
    const onAck = vi.fn()
    render(
      <ViolationNoticeOverlay kind="keyboard" softCount={1} limit={3} onAcknowledge={onAck} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /i understand/i }))
    expect(onAck).toHaveBeenCalledTimes(1)
  })

  it('auto-dismisses after the timeout', () => {
    vi.useFakeTimers()
    const onAck = vi.fn()
    render(
      <ViolationNoticeOverlay kind="keyboard" softCount={1} limit={3} onAcknowledge={onAck} />,
    )
    act(() => { vi.advanceTimersByTime(6000) })
    expect(onAck).toHaveBeenCalledTimes(1)
  })
})
