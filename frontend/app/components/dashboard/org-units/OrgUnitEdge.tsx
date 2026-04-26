import { getBezierPath } from './edge-path'
import type { Position } from './types'

export interface OrgUnitEdgeProps {
  id: string
  source: string
  target: string
  sourceX: number
  sourceY: number
  targetX: number
  targetY: number
  sourcePosition: Position
  targetPosition: Position
  selectedPath?: Set<string>
}

export function OrgUnitEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selectedPath,
}: OrgUnitEdgeProps) {
  const onPath =
    selectedPath?.has(source) === true && selectedPath?.has(target) === true

  const d = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  return (
    <path
      data-edge-id={id}
      d={d}
      fill="none"
      stroke={onPath ? 'var(--px-accent)' : 'var(--px-hairline-strong)'}
      strokeWidth={onPath ? 1.8 : 1.4}
      opacity={onPath ? 0.9 : 0.55}
    />
  )
}
