'use client'

import '@xyflow/react/dist/style.css'

import { useCallback, useMemo, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type NodeMouseHandler,
  type NodeTypes,
} from '@xyflow/react'
import { LayoutGrid } from 'lucide-react'

import type { OrgUnit } from '@/lib/api/org-units'

import { OrgUnitLegend } from './OrgUnitLegend'
import { OrgUnitNode } from './OrgUnitNode'
import {
  useOrgUnitTreeLayout,
  type OrgUnitFlowNode,
  type OrgUnitNodeData,
} from './useOrgUnitTreeLayout'

// Casting the typed custom node to NodeTypes[string] is the recommended
// React Flow v12 pattern — nodeTypes accepts a looser base `Node` type so
// the custom component (which narrows to OrgUnitFlowNode) must be adapted.
const nodeTypes: NodeTypes = {
  orgUnit: OrgUnitNode as unknown as NodeTypes[string],
}

const MINIMAP_COLORS: Record<string, string> = {
  company: '#3b82f6',
  division: '#8b5cf6',
  client_account: '#10b981',
  region: '#f97316',
  team: '#f59e0b',
}

type Props = {
  units: OrgUnit[]
  selectedUnitId: string | null
  onNodeClick: (unitId: string) => void
}

export function OrgUnitCanvas({ units, selectedUnitId, onNodeClick }: Props) {
  const [direction, setDirection] = useState<'TB' | 'LR'>('TB')
  const { nodes: layoutNodes, edges } = useOrgUnitTreeLayout(units, direction)

  // Derive the final node list by stamping `selected` onto each node. This
  // avoids the React 19 / react-hooks/set-state-in-effect lint rule that
  // would fire if we used useNodesState + useEffect sync. For a read-only
  // tree (no drag/resize/connect), useMemo is the cleanest pattern.
  const nodes = useMemo<OrgUnitFlowNode[]>(
    () =>
      layoutNodes.map((n) => ({
        ...n,
        selected: n.id === selectedUnitId,
      })),
    [layoutNodes, selectedUnitId],
  )

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_evt, node) => {
      const data = node.data as OrgUnitNodeData | undefined
      if (!data?.unit?.is_accessible) return
      onNodeClick(node.id)
    },
    [onNodeClick],
  )

  // Re-mount React Flow when layout direction flips so fitView re-runs on
  // the new positions. Cheap: handful of nodes.
  const flowKey = `org-units-${direction}`

  return (
    <div className="relative w-full h-full bg-zinc-50 border border-zinc-200 rounded-xl overflow-hidden">
      <ReactFlow
        key={flowKey}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.2, minZoom: 0.2, maxZoom: 1.5 }}
        minZoom={0.2}
        maxZoom={2}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{
          type: 'smoothstep',
          style: { stroke: '#d4d4d8', strokeWidth: 1.5 },
        }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color="#e4e4e7"
        />
        <Controls position="bottom-left" showInteractive={false} />
        <MiniMap
          position="bottom-right"
          nodeColor={(n) => {
            const data = n.data as OrgUnitNodeData | undefined
            const unitType = data?.unit?.unit_type ?? 'team'
            return MINIMAP_COLORS[unitType] ?? '#a1a1aa'
          }}
          pannable
          zoomable
          className="!bg-white !border !border-zinc-200"
        />
      </ReactFlow>

      <OrgUnitLegend />

      {/* Layout toggle (top-right) */}
      <button
        type="button"
        onClick={() => setDirection((d) => (d === 'TB' ? 'LR' : 'TB'))}
        className="absolute top-3 right-3 z-10 inline-flex items-center gap-1.5 bg-white/90 backdrop-blur border border-zinc-200 rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-white shadow-sm transition"
        title="Toggle layout direction"
      >
        <LayoutGrid className="w-3.5 h-3.5" />
        {direction === 'TB' ? 'Top-down' : 'Left-right'}
      </button>
    </div>
  )
}
