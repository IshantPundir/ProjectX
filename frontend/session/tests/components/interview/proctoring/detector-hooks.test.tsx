import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useVisibilityGuard } from '@/components/interview/proctoring/use-visibility-guard'
import { useFocusGuard } from '@/components/interview/proctoring/use-focus-guard'
import { useKeyboardGuard } from '@/components/interview/proctoring/use-keyboard-guard'

afterEach(() => vi.restoreAllMocks())

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
  it('fires focus_loss on blur when the tab is still visible', () => {
    setVisibility('visible')
    const onViolation = vi.fn()
    renderHook(() => useFocusGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new Event('blur')))
    expect(onViolation).toHaveBeenCalledWith('focus_loss')
  })

  it('defers to the visibility guard when the blur is a tab switch (hidden)', () => {
    setVisibility('hidden')
    const onViolation = vi.fn()
    renderHook(() => useFocusGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new Event('blur')))
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
