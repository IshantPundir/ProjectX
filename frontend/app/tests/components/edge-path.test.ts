import { describe, expect, it } from 'vitest'

import { getBezierPath } from '@/components/dashboard/org-units/edge-path'

describe('getBezierPath', () => {
  it('starts at the source point and ends at the target point', () => {
    const path = getBezierPath({
      sourceX: 0,
      sourceY: 0,
      sourcePosition: 'bottom',
      targetX: 100,
      targetY: 200,
      targetPosition: 'top',
    })
    expect(path.startsWith('M0,0 ')).toBe(true)
    expect(path.endsWith(' 100,200')).toBe(true)
    expect(path).toMatch(/^M0,0 C[\-\d.,\s]+ 100,200$/)
  })

  it('offsets control points along Y for vertical (TB) edges', () => {
    const path = getBezierPath({
      sourceX: 50,
      sourceY: 0,
      sourcePosition: 'bottom',
      targetX: 50,
      targetY: 100,
      targetPosition: 'top',
      curvature: 0.25,
    })
    expect(path).toBe('M50,0 C50,25 50,75 50,100')
  })

  it('offsets control points along X for horizontal (LR) edges', () => {
    const path = getBezierPath({
      sourceX: 0,
      sourceY: 50,
      sourcePosition: 'right',
      targetX: 100,
      targetY: 50,
      targetPosition: 'left',
      curvature: 0.25,
    })
    expect(path).toBe('M0,50 C25,50 75,50 100,50')
  })

  it('uses absolute distance so reversed-direction inputs still curve outward', () => {
    const path = getBezierPath({
      sourceX: 50,
      sourceY: 100,
      sourcePosition: 'bottom',
      targetX: 50,
      targetY: 0,
      targetPosition: 'top',
      curvature: 0.25,
    })
    expect(path).toBe('M50,100 C50,125 50,-25 50,0')
  })

  it('defaults curvature to 0.25 when omitted', () => {
    const a = getBezierPath({
      sourceX: 0, sourceY: 0, sourcePosition: 'bottom',
      targetX: 0, targetY: 100, targetPosition: 'top',
    })
    const b = getBezierPath({
      sourceX: 0, sourceY: 0, sourcePosition: 'bottom',
      targetX: 0, targetY: 100, targetPosition: 'top',
      curvature: 0.25,
    })
    expect(a).toBe(b)
  })
})
