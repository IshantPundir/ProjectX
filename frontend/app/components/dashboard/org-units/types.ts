// frontend/app/components/dashboard/org-units/types.ts

/**
 * Local replacements for the xyflow types we used to import. Keeping
 * them here means nothing in this module depends on @xyflow/react.
 */

/** Side of a node where an edge anchors. */
export type Position = 'top' | 'bottom' | 'left' | 'right'

/** Layout direction. Mirrors dagre's `rankdir`. */
export type Direction = 'TB' | 'LR'

/** A node after dagre layout. Generic `T` is the per-node payload. */
export interface LayoutNode<T extends Record<string, unknown> = Record<string, unknown>> {
  id: string
  type?: string
  position: { x: number; y: number }
  sourcePosition?: Position
  targetPosition?: Position
  data: T
}

/** An edge between two nodes. */
export interface LayoutEdge {
  id: string
  source: string
  target: string
  type?: string
  data?: Record<string, unknown>
}
