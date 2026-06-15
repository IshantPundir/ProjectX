import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const { isMultiDisplay, subscribeDisplayChange } = vi.hoisted(() => ({
  isMultiDisplay: vi.fn(),
  subscribeDisplayChange: vi.fn((_cb: () => void) => () => {}),
}))
vi.mock('@/lib/proctoring/displays', () => ({ isMultiDisplay, subscribeDisplayChange }))

import { useDisplayGuard } from '@/components/interview/proctoring/use-display-guard'

afterEach(() => {
  vi.restoreAllMocks()
  isMultiDisplay.mockReset()
  subscribeDisplayChange.mockReset()
  subscribeDisplayChange.mockReturnValue(() => {})
})

describe('useDisplayGuard', () => {
  it('does nothing when not armed', () => {
    isMultiDisplay.mockReturnValue(true)
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: false, onViolation }))
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('fires multiple_displays when already extended at arm time', () => {
    isMultiDisplay.mockReturnValue(true)
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: true, onViolation }))
    expect(onViolation).toHaveBeenCalledWith('multiple_displays')
  })

  it('does not fire when single-display, then fires on a change to extended', () => {
    isMultiDisplay.mockReturnValue(false)
    let changeCb = () => {}
    subscribeDisplayChange.mockImplementation((cb: () => void) => {
      changeCb = cb
      return () => {}
    })
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: true, onViolation }))
    expect(onViolation).not.toHaveBeenCalled()
    isMultiDisplay.mockReturnValue(true)
    act(() => { changeCb() })
    expect(onViolation).toHaveBeenCalledWith('multiple_displays')
  })

  it('fires only once while extended (no spam)', () => {
    isMultiDisplay.mockReturnValue(true)
    let changeCb = () => {}
    subscribeDisplayChange.mockImplementation((cb: () => void) => { changeCb = cb; return () => {} })
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: true, onViolation }))
    act(() => { changeCb() })
    act(() => { changeCb() })
    expect(onViolation).toHaveBeenCalledTimes(1)
  })
})
