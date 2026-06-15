import { afterEach, describe, expect, it, vi } from 'vitest'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'

function setScreen(props: Partial<{ isExtended: boolean; addEventListener: unknown; removeEventListener: unknown }>) {
  Object.defineProperty(window, 'screen', { value: { ...props }, configurable: true })
}

afterEach(() => {
  // restore a benign screen object
  Object.defineProperty(window, 'screen', { value: {}, configurable: true })
})

describe('isMultiDisplay', () => {
  it('returns true when screen.isExtended is true', () => {
    setScreen({ isExtended: true })
    expect(isMultiDisplay()).toBe(true)
  })
  it('returns false when screen.isExtended is false', () => {
    setScreen({ isExtended: false })
    expect(isMultiDisplay()).toBe(false)
  })
  it('returns null when the API is unavailable', () => {
    setScreen({})
    expect(isMultiDisplay()).toBeNull()
  })
})

describe('subscribeDisplayChange', () => {
  it('adds and removes a change listener when supported', () => {
    const add = vi.fn()
    const remove = vi.fn()
    setScreen({ isExtended: false, addEventListener: add, removeEventListener: remove })
    const cb = vi.fn()
    const unsub = subscribeDisplayChange(cb)
    expect(add).toHaveBeenCalledWith('change', cb)
    unsub()
    expect(remove).toHaveBeenCalledWith('change', cb)
  })
  it('is a no-op when the API is unavailable', () => {
    setScreen({})
    const unsub = subscribeDisplayChange(vi.fn())
    expect(() => unsub()).not.toThrow()
  })
})
