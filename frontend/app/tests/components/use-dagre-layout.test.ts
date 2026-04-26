import { describe, expect, it } from 'vitest'
import { Position, type Edge, type Node } from '@xyflow/react'

import {
  getDagreLayout,
  NODE_HEIGHT,
  NODE_WIDTH,
} from '@/components/dashboard/org-units/use-dagre-layout'

function makeNode(id: string): Node<{ label: string }> {
  return {
    id,
    type: 'orgUnit',
    position: { x: 0, y: 0 },
    data: { label: id },
  }
}

function makeEdge(source: string, target: string): Edge {
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
    expect(out[0].sourcePosition).toBe(Position.Bottom)
    expect(out[0].targetPosition).toBe(Position.Top)
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
    expect(c.sourcePosition).toBe(Position.Right)
    expect(c.targetPosition).toBe(Position.Left)
  })

  it('flips source/target positions when direction changes', () => {
    const tb = getDagreLayout([makeNode('a')], [], 'TB')
    const lr = getDagreLayout([makeNode('a')], [], 'LR')
    expect(tb[0].sourcePosition).toBe(Position.Bottom)
    expect(lr[0].sourcePosition).toBe(Position.Right)
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
