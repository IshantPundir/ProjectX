import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

describe('usePrefersReducedMotion', () => {
  it('returns false by default (matchMedia stub does not match)', () => {
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(false)
  })
  it('does not throw when matchMedia is unavailable', () => {
    const original = window.matchMedia
    // @ts-expect-error force-remove for the test
    delete window.matchMedia
    expect(() => renderHook(() => usePrefersReducedMotion())).not.toThrow()
    window.matchMedia = original
  })
})
