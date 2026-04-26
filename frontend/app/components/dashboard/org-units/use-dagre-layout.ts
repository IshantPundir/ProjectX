import dagre from '@dagrejs/dagre'
import { useMemo } from 'react'

import type { Direction, LayoutEdge, LayoutNode, Position } from './types'

export const NODE_WIDTH = 168
export const NODE_HEIGHT = 52

export type { Direction } from './types'

/**
 * Pure layout function — no React. Runs dagre on the given graph and
 * returns the same nodes with positions and source/target handle sides
 * filled in. Easy to unit-test.
 */
export function getDagreLayout<T extends object>(
  nodes: LayoutNode<T>[],
  edges: LayoutEdge[],
  direction: Direction,
): LayoutNode<T>[] {
  if (nodes.length === 0) return []

  const g = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: direction, nodesep: 28, ranksep: 64 })

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target)
  }

  dagre.layout(g)

  const isHorizontal = direction === 'LR'
  const sourcePosition: Position = isHorizontal ? 'right' : 'bottom'
  const targetPosition: Position = isHorizontal ? 'left' : 'top'

  return nodes.map((n) => {
    const d = g.node(n.id)
    return {
      ...n,
      // dagre returns center-anchored coordinates; we expect top-left.
      position: { x: d.x - NODE_WIDTH / 2, y: d.y - NODE_HEIGHT / 2 },
      sourcePosition,
      targetPosition,
    }
  })
}

/**
 * Memoized React hook wrapper around `getDagreLayout`. Recomputes only
 * when nodes, edges, or direction reference-change.
 */
export function useDagreLayout<T extends object>(
  nodes: LayoutNode<T>[],
  edges: LayoutEdge[],
  direction: Direction,
): LayoutNode<T>[] {
  return useMemo(
    () => getDagreLayout(nodes, edges, direction),
    [nodes, edges, direction],
  )
}
