import {
  BaseEdge,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'

interface OrgUnitEdgeData {
  selectedPath: Set<string>
}

export function OrgUnitEdge(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    source,
    target,
    data,
  } = props

  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const selectedPath = (data as unknown as OrgUnitEdgeData | undefined)
    ?.selectedPath
  const onPath =
    selectedPath?.has(source) === true && selectedPath?.has(target) === true

  return (
    <BaseEdge
      id={id}
      path={path}
      style={{
        stroke: onPath ? 'var(--px-accent)' : 'var(--px-hairline-strong)',
        strokeWidth: onPath ? 1.8 : 1.4,
        opacity: onPath ? 0.9 : 0.55,
      }}
    />
  )
}
