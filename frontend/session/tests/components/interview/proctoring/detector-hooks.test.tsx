import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useVisibilityGuard } from '@/components/interview/proctoring/use-visibility-guard'
import { useFocusGuard } from '@/components/interview/proctoring/use-focus-guard'
import { useKeyboardGuard } from '@/components/interview/proctoring/use-keyboard-guard'

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  Object.defineProperty(document, 'hidden', { value: state === 'hidden', configurable: true })
}

describe('useVisibilityGuard', () => {
  it('fires tab_switch when the tab is hidden while armed', () => {
    const onViolation = vi.fn()
    renderHook(() => useVisibilityGuard({ armed: true, onViolation }))
    act(() => {
      setVisibility('hidden')
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onViolation).toHaveBeenCalledWith('tab_switch')
  })

  it('does nothing when not armed', () => {
    const onViolation = vi.fn()
    renderHook(() => useVisibilityGuard({ armed: false, onViolation }))
    act(() => {
      setVisibility('hidden')
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onViolation).not.toHaveBeenCalled()
  })
})

describe('useFocusGuard', () => {
  it('opens the grace overlay on blur without an immediate violation', () => {
    vi.useFakeTimers()
    setVisibility('visible')
    const onViolation = vi.fn()
    const { result } = renderHook(() =>
      useFocusGuard({ armed: true, graceSeconds: 10, onViolation }),
    )
    act(() => window.dispatchEvent(new Event('blur')))
    expect(result.current.showOverlay).toBe(true)
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('reports soft focus_loss and closes the overlay when focus returns in time', () => {
    vi.useFakeTimers()
    setVisibility('visible')
    const onViolation = vi.fn()
    const { result } = renderHook(() =>
      useFocusGuard({ armed: true, graceSeconds: 10, onViolation }),
    )
    act(() => window.dispatchEvent(new Event('blur')))
    act(() => {
      vi.advanceTimersByTime(3000)
      window.dispatchEvent(new Event('focus'))
    })
    expect(onViolation).toHaveBeenCalledWith('focus_loss')
    expect(onViolation).toHaveBeenCalledTimes(1)
    expect(result.current.showOverlay).toBe(false)
  })

  it('reports hard focus_abandoned when the grace window expires', () => {
    vi.useFakeTimers()
    setVisibility('visible')
    const onViolation = vi.fn()
    const { result } = renderHook(() =>
      useFocusGuard({ armed: true, graceSeconds: 3, onViolation }),
    )
    act(() => window.dispatchEvent(new Event('blur')))
    act(() => vi.advanceTimersByTime(3000))
    expect(onViolation).toHaveBeenCalledWith('focus_abandoned')
    expect(result.current.showOverlay).toBe(false)
  })

  it('defers to the visibility guard when the blur is a tab switch (hidden)', () => {
    vi.useFakeTimers()
    setVisibility('hidden')
    const onViolation = vi.fn()
    const { result } = renderHook(() =>
      useFocusGuard({ armed: true, graceSeconds: 10, onViolation }),
    )
    act(() => window.dispatchEvent(new Event('blur')))
    expect(result.current.showOverlay).toBe(false)
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('does nothing when not armed', () => {
    vi.useFakeTimers()
    setVisibility('visible')
    const onViolation = vi.fn()
    const { result } = renderHook(() =>
      useFocusGuard({ armed: false, graceSeconds: 10, onViolation }),
    )
    act(() => window.dispatchEvent(new Event('blur')))
    act(() => vi.advanceTimersByTime(10000))
    expect(result.current.showOverlay).toBe(false)
    expect(onViolation).not.toHaveBeenCalled()
  })
})

describe('useKeyboardGuard', () => {
  it('reports a debounced keyboard violation on a typing key', () => {
    const onViolation = vi.fn()
    renderHook(() => useKeyboardGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' })))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b' })))
    expect(onViolation).toHaveBeenCalledTimes(1) // debounced within the burst window
    expect(onViolation).toHaveBeenCalledWith('keyboard')
  })

  it('ignores navigation keys so the End button stays operable', () => {
    const onViolation = vi.fn()
    renderHook(() => useKeyboardGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' })))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })))
    expect(onViolation).not.toHaveBeenCalled()
  })
})
