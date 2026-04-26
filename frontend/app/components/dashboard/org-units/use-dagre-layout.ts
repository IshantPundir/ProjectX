import dagre from '@dagrejs/dagre'
import { Position, type Edge, type Node } from '@xyflow/react'
import { useMemo } from 'react'

export const NODE_WIDTH = 168
export const NODE_HEIGHT = 52

export type Direction = 'TB' | 'LR'

/**
 * Pure layout function — no React. Runs dagre on the given graph and
 * returns the same nodes with positions and source/target handle sides
 * filled in. Easy to unit-test.
 */
export function getDagreLayout<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  edges: Edge[],
  direction: Direction,
): Node<T>[] {
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
  return nodes.map((n) => {
    const d = g.node(n.id)
    return {
      ...n,
      // dagre returns center-anchored coordinates; xyflow expects top-left.
      position: { x: d.x - NODE_WIDTH / 2, y: d.y - NODE_HEIGHT / 2 },
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      // Pre-supply the dimensions xyflow would otherwise wait for
      // ResizeObserver to measure. Cards are fixed size by design, so
      // this is accurate. Without it, xyflow keeps every node at
      // `visibility: hidden` on first paint until the observer fires —
      // a real-world flash, and it also breaks RTL queries that skip
      // hidden nodes (jsdom never fires ResizeObserver organically).
      measured: { width: NODE_WIDTH, height: NODE_HEIGHT },
    }
  })
}

/**
 * Memoized React hook wrapper around `getDagreLayout`. Recomputes only
 * when nodes, edges, or direction reference-change.
 */
export function useDagreLayout<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  edges: Edge[],
  direction: Direction,
): Node<T>[] {
  return useMemo(
    () => getDagreLayout(nodes, edges, direction),
    [nodes, edges, direction],
  )
}
