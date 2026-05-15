import { describe, expect, it, vi } from 'vitest'

import { postedAgo } from '@/lib/utils'

describe('postedAgo', () => {
  // Pin "now" so the test isn't time-dependent. 2026-05-15T12:00:00Z.
  const NOW = new Date('2026-05-15T12:00:00.000Z').getTime()

  it('returns "today" for the same day', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-05-15T08:00:00.000Z')).toBe('today')
  })

  it('returns "1d ago" for one day prior', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-05-14T08:00:00.000Z')).toBe('1d ago')
  })

  it('returns "Nd ago" for under a month', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-05-01T08:00:00.000Z')).toBe('14d ago')
  })

  it('returns "1mo ago" for one month prior', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-04-10T08:00:00.000Z')).toBe('1mo ago')
  })

  it('returns "Nmo ago" beyond two months', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-02-01T08:00:00.000Z')).toBe('3mo ago')
  })
})
