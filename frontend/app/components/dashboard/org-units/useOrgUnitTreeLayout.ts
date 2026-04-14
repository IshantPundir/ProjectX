'use client'

import { useMemo } from 'react'
import dagre from 'dagre'
import type { Edge, Node } from '@xyflow/react'

import type { OrgUnit } from '@/lib/api/org-units'

export type OrgUnitNodeData = {
  unit: OrgUnit
}

// A strongly-typed Node for our custom "orgUnit" node type. This is the
// recommended React Flow v12 pattern — attach the data shape + literal
// `type` string so NodeProps<OrgUnitFlowNode> narrows correctly inside the
// custom node component.
export type OrgUnitFlowNode = Node<OrgUnitNodeData, 'orgUnit'>

type LayoutDirection = 'TB' | 'LR'

const NODE_WIDTH = 280
const NODE_HEIGHT = 96

export function useOrgUnitTreeLayout(
  units: OrgUnit[],
  direction: LayoutDirection = 'TB',
): { nodes: OrgUnitFlowNode[]; edges: Edge[] } {
  return useMemo(() => {
    if (units.length === 0) {
      return { nodes: [], edges: [] }
    }

    const graph = new dagre.graphlib.Graph()
    graph.setDefaultEdgeLabel(() => ({}))
    graph.setGraph({
      rankdir: direction,
      ranksep: direction === 'TB' ? 80 : 120,
      nodesep: direction === 'TB' ? 50 : 40,
      marginx: 40,
      marginy: 40,
    })

    // Add nodes to dagre.
    for (const unit of units) {
      graph.setNode(unit.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
    }
    // Add edges (parent -> child). Skip edges whose source is missing — a
    // parent_unit_id may point at an org unit the viewer can't see (the
    // backend still returns inaccessible ancestors so the tree has root).
    const unitIds = new Set(units.map((u) => u.id))
    for (const unit of units) {
      if (unit.parent_unit_id && unitIds.has(unit.parent_unit_id)) {
        graph.setEdge(unit.parent_unit_id, unit.id)
      }
    }

    dagre.layout(graph)

    const nodes: OrgUnitFlowNode[] = units.map((unit) => {
      const pos = graph.node(unit.id)
      return {
        id: unit.id,
        type: 'orgUnit',
        position: {
          x: (pos?.x ?? 0) - NODE_WIDTH / 2,
          y: (pos?.y ?? 0) - NODE_HEIGHT / 2,
        },
        data: { unit },
        draggable: false, // drag-to-reparent is a future feature
        selectable: true,
      }
    })

    const edges: Edge[] = units
      .filter((u) => u.parent_unit_id && unitIds.has(u.parent_unit_id))
      .map((u) => ({
        id: `e-${u.parent_unit_id}-${u.id}`,
        source: u.parent_unit_id as string,
        target: u.id,
        type: 'smoothstep',
        animated: false,
        style: { stroke: '#d4d4d8', strokeWidth: 1.5 },
      }))

    return { nodes, edges }
  }, [units, direction])
}
