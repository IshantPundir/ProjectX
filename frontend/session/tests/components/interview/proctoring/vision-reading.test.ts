import { describe, expect, it } from 'vitest'
import { ReadingAccumulator } from '@/components/interview/proctoring/vision/reading'

describe('ReadingAccumulator', () => {
  it('does not flag when gaze stays on-screen', () => {
    const acc = new ReadingAccumulator()
    for (let t = 0; t < 6000; t += 200) acc.push('center', t)
    expect(acc.isReading()).toBe(false)
    expect(acc.offScreenRatio()).toBe(0)
  })

  it('flags reading when off-screen with left-right scanning over the window', () => {
    const acc = new ReadingAccumulator()
    const zones = ['down_away', 'left', 'down_away', 'right'] as const
    for (let i = 0, t = 0; t < 4000; i++, t += 200) acc.push(zones[i % zones.length], t)
    expect(acc.isReading()).toBe(true)
    expect(acc.offScreenRatio()).toBeGreaterThan(0.8)
  })

  it('does not flag a single brief glance away', () => {
    const acc = new ReadingAccumulator()
    acc.push('center', 0)
    acc.push('down_away', 200)
    acc.push('center', 400)
    expect(acc.isReading()).toBe(false)
  })

  it('prunes samples older than the window', () => {
    const acc = new ReadingAccumulator()
    acc.push('down_away', 0)
    acc.push('center', 10000) // 10s later — old sample pruned
    expect(acc.offScreenRatio()).toBe(0)
  })

  it('does not flag sustained off-screen gaze without left-right scanning', () => {
    const acc = new ReadingAccumulator()
    // Entire window off-screen (down_away only) — no left/right alternation
    for (let t = 0; t < 5500; t += 200) acc.push('down_away', t)
    expect(acc.offScreenRatio()).toBeGreaterThan(0.6) // ratio condition passes
    expect(acc.isReading()).toBe(false) // direction-change guard must block it
  })
})
