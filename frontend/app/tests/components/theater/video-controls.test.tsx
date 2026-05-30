// tests/components/theater/video-controls.test.tsx
import { describe, it, expect } from 'vitest'

import { clockFromSec } from '@/components/dashboard/reports/theater/useVideoController'

describe('clockFromSec', () => {
  it('formats seconds as m:ss and floors fractions', () => {
    expect(clockFromSec(0)).toBe('0:00')
    expect(clockFromSec(9.9)).toBe('0:09')
    expect(clockFromSec(75)).toBe('1:15')
  })
  it('guards NaN/negative to 0:00', () => {
    expect(clockFromSec(NaN)).toBe('0:00')
    expect(clockFromSec(-5)).toBe('0:00')
  })
})
