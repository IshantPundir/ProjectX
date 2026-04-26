import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useDirectionToggle } from '@/components/dashboard/org-units/use-direction-toggle'

const KEY = 'org-graph-direction'

describe('useDirectionToggle', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })
  afterEach(() => {
    window.localStorage.clear()
  })

  it('defaults to TB when localStorage is empty', () => {
    const { result } = renderHook(() => useDirectionToggle())
    expect(result.current[0]).toBe('TB')
  })

  it('reads the persisted direction on mount', () => {
    window.localStorage.setItem(KEY, 'LR')
    const { result } = renderHook(() => useDirectionToggle())
    expect(result.current[0]).toBe('LR')
  })

  it('falls back to TB for an invalid persisted value', () => {
    window.localStorage.setItem(KEY, 'BAD')
    const { result } = renderHook(() => useDirectionToggle())
    expect(result.current[0]).toBe('TB')
  })

  it('writes to localStorage when the direction changes', () => {
    const { result } = renderHook(() => useDirectionToggle())
    act(() => {
      result.current[1]('LR')
    })
    expect(result.current[0]).toBe('LR')
    expect(window.localStorage.getItem(KEY)).toBe('LR')
  })

  it('does not throw if localStorage.setItem throws (private mode)', () => {
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = () => {
      throw new Error('quota')
    }
    try {
      const { result } = renderHook(() => useDirectionToggle())
      expect(() => {
        act(() => {
          result.current[1]('LR')
        })
      }).not.toThrow()
      expect(result.current[0]).toBe('LR')
    } finally {
      Storage.prototype.setItem = original
    }
  })
})
