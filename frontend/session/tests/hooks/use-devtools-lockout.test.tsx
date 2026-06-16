import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useDevtoolsLockout } from '@/hooks/use-devtools-lockout'

function setSize(outerW: number, innerW: number, outerH: number, innerH: number) {
  Object.defineProperty(window, 'outerWidth', { configurable: true, value: outerW })
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: innerW })
  Object.defineProperty(window, 'outerHeight', { configurable: true, value: outerH })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: innerH })
}

afterEach(() => setSize(1024, 1024, 768, 768))

describe('useDevtoolsLockout', () => {
  it('does nothing when disabled', () => {
    const { result } = renderHook(() => useDevtoolsLockout(false))
    expect(result.current).toBe(false)
  })

  it('detects a window-inner delta jump (devtools docked after load)', () => {
    setSize(1024, 1024, 768, 768) // baseline delta 0 captured at mount
    const { result } = renderHook(() => useDevtoolsLockout(true))
    expect(result.current).toBe(false)
    act(() => {
      setSize(1024, 800, 768, 768) // innerWidth shrank 224 > 160 threshold
      window.dispatchEvent(new Event('resize'))
    })
    expect(result.current).toBe(true)
  })

  it('ignores a size change below the threshold', () => {
    setSize(1024, 1024, 768, 768)
    const { result } = renderHook(() => useDevtoolsLockout(true))
    act(() => {
      setSize(1024, 950, 768, 768) // 74 < 160
      window.dispatchEvent(new Event('resize'))
    })
    expect(result.current).toBe(false)
  })
})
