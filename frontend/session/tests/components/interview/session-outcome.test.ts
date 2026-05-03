import { describe, expect, it } from 'vitest'

import {
  isSessionOutcome,
  SESSION_OUTCOMES,
  type SessionOutcome,
} from '@/components/interview/lib/session-outcome'

describe('SESSION_OUTCOMES', () => {
  it('lists all 6 backend outcomes', () => {
    expect(SESSION_OUTCOMES).toEqual([
      'completed',
      'knockout_closed',
      'time_expired',
      'candidate_ended',
      'candidate_unresponsive',
      'error',
    ])
  })
})

describe('isSessionOutcome', () => {
  it.each(SESSION_OUTCOMES)('returns true for %s', (v) => {
    expect(isSessionOutcome(v)).toBe(true)
  })

  it('returns false for an unknown outcome string', () => {
    expect(isSessionOutcome('mystery_outcome')).toBe(false)
  })

  it('returns false for null', () => {
    expect(isSessionOutcome(null)).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isSessionOutcome(undefined)).toBe(false)
  })

  it('returns false for empty string', () => {
    expect(isSessionOutcome('')).toBe(false)
  })

  it('returns false for non-string values', () => {
    // @ts-expect-error — testing the runtime guard, not the type
    expect(isSessionOutcome(42)).toBe(false)
    // @ts-expect-error — testing the runtime guard, not the type
    expect(isSessionOutcome({})).toBe(false)
  })
})
