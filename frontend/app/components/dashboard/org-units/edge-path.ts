import type { Position } from './types'

interface BezierPathInput {
  sourceX: number
  sourceY: number
  sourcePosition: Position
  targetX: number
  targetY: number
  targetPosition: Position
  /** Fraction of the source-to-target distance used for the control offset. */
  curvature?: number
}

function controlOffset(value: number): number {
  return value < 0 ? 0 : value
}

function controlPoint(
  pos: Position,
  x: number,
  y: number,
  oppositeX: number,
  oppositeY: number,
  curvature: number,
): [number, number] {
  switch (pos) {
    case 'left':
      return [x - controlOffset(curvature * Math.abs(x - oppositeX)), y]
    case 'right':
      return [x + controlOffset(curvature * Math.abs(x - oppositeX)), y]
    case 'top':
      return [x, y - controlOffset(curvature * Math.abs(y - oppositeY))]
    case 'bottom':
      return [x, y + controlOffset(curvature * Math.abs(y - oppositeY))]
  }
}

/**
 * Returns the SVG `d` attribute for a cubic bezier between two anchored
 * points. Drop-in replacement for `@xyflow/react`'s `getBezierPath`,
 * minus the label-position outputs (we don't render edge labels).
 */
export function getBezierPath({
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  curvature = 0.25,
}: BezierPathInput): string {
  const [c1x, c1y] = controlPoint(
    sourcePosition,
    sourceX,
    sourceY,
    targetX,
    targetY,
    curvature,
  )
  const [c2x, c2y] = controlPoint(
    targetPosition,
    targetX,
    targetY,
    sourceX,
    sourceY,
    curvature,
  )
  return `M${sourceX},${sourceY} C${c1x},${c1y} ${c2x},${c2y} ${targetX},${targetY}`
}
