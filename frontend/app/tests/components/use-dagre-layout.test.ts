import { describe, expect, it } from 'vitest'

import {
  getDagreLayout,
  NODE_HEIGHT,
  NODE_WIDTH,
} from '@/components/dashboard/org-units/use-dagre-layout'
import type {
  LayoutEdge,
  LayoutNode,
} from '@/components/dashboard/org-units/types'

function makeNode(id: string): LayoutNode<{ label: string }> {
  return {
    id,
    type: 'orgUnit',
    position: { x: 0, y: 0 },
    data: { label: id },
  }
}

function makeEdge(source: string, target: string): LayoutEdge {
  return { id: `${source}->${target}`, source, target }
}

describe('getDagreLayout', () => {
  it('returns an empty array for empty input', () => {
    expect(getDagreLayout([], [], 'TB')).toEqual([])
  })

  it('positions a single node and assigns TB handle positions', () => {
    const out = getDagreLayout([makeNode('a')], [], 'TB')
    expect(out).toHaveLength(1)
    expect(out[0].position).toEqual(
      expect.objectContaining({
        x: expect.any(Number),
        y: expect.any(Number),
      }),
    )
    expect(out[0].sourcePosition).toBe('bottom')
    expect(out[0].targetPosition).toBe('top')
  })

  it('positions child below parent in TB direction', () => {
    const out = getDagreLayout(
      [makeNode('p'), makeNode('c')],
      [makeEdge('p', 'c')],
      'TB',
    )
    const p = out.find((n) => n.id === 'p')!
    const c = out.find((n) => n.id === 'c')!
    expect(c.position.y).toBeGreaterThan(p.position.y)
  })

  it('positions child to the right of parent in LR direction', () => {
    const out = getDagreLayout(
      [makeNode('p'), makeNode('c')],
      [makeEdge('p', 'c')],
      'LR',
    )
    const p = out.find((n) => n.id === 'p')!
    const c = out.find((n) => n.id === 'c')!
    expect(c.position.x).toBeGreaterThan(p.position.x)
    expect(c.sourcePosition).toBe('right')
    expect(c.targetPosition).toBe('left')
  })

  it('flips source/target positions when direction changes', () => {
    const tb = getDagreLayout([makeNode('a')], [], 'TB')
    const lr = getDagreLayout([makeNode('a')], [], 'LR')
    expect(tb[0].sourcePosition).toBe('bottom')
    expect(lr[0].sourcePosition).toBe('right')
  })

  it('uses the hardcoded card dimensions for layout', () => {
    expect(NODE_WIDTH).toBe(168)
    expect(NODE_HEIGHT).toBe(52)
  })

  it('preserves the original node data and type', () => {
    const out = getDagreLayout([makeNode('a')], [], 'TB')
    expect(out[0].data).toEqual({ label: 'a' })
    expect(out[0].type).toBe('orgUnit')
  })
})
