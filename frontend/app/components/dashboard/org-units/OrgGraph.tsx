'use client'

import { useCallback, useEffect, useMemo, useRef } from 'react'
import {
  Background,
  Controls,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeTypes,
  type OnNodesChange,
} from '@xyflow/react'

import '@xyflow/react/dist/style.css'

import type { OrgUnit } from '@/lib/api/org-units'

import { OrgUnitEdge } from './OrgUnitEdge'
import { OrgUnitNode } from './OrgUnitNode'
import { useDagreLayout } from './use-dagre-layout'
import { useDirectionToggle } from './use-direction-toggle'
import { UNIT_TYPE_STYLE, type UnitType } from './unit-type-style'

// ─── Public types ──────────────────────────────────────────────────────────

export type Pressure = 'hot' | 'steady' | 'cool'

export interface GraphNodeData extends OrgUnit {
  /** Rolled-up open-role count for this unit and its descendants. */
  openRoles: number
  /** Coarse tier derived from `openRoles` via `pressureFor()` in page.tsx. */
  pressure: Pressure
}

interface OrgGraphProps {
  units: GraphNodeData[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  hoverId?: string | null
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  onHover?: (id: string | null) => void
}

// Index-signature intersection is required by xyflow's `Node<T>` generic,
// which constrains its data parameter to `Record<string, unknown>`.
type OrgUnitNodeData = {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
} & Record<string, unknown>

// `nodeTypes` and `edgeTypes` MUST be defined outside the component so
// xyflow doesn't recreate them on every render — that triggers a
// console warning and degrades performance.
const nodeTypes: NodeTypes = { orgUnit: OrgUnitNode }
const edgeTypes: EdgeTypes = { orgUnit: OrgUnitEdge }

// ─── Inner canvas component (uses useReactFlow → must be inside Provider) ──

function OrgGraphInner({ units, selectedId, onSelect }: OrgGraphProps) {
  const { fitView } = useReactFlow()
  const [direction, setDirection] = useDirectionToggle()

  // Walk parents from the selected node up to the root so the card +
  // edge components can highlight the path.
  const onSelectPath = useMemo(() => {
    const set = new Set<string>()
    if (!selectedId) return set
    const byId = new Map(units.map((u) => [u.id, u]))
    let cur: GraphNodeData | undefined = byId.get(selectedId)
    while (cur) {
      set.add(cur.id)
      cur = cur.parent_unit_id ? byId.get(cur.parent_unit_id) : undefined
    }
    return set
  }, [units, selectedId])

  // Detect data corruption (multiple roots) — log once, don't crash.
  const orphanWarned = useRef(false)
  useEffect(() => {
    const roots = units.filter((u) => !u.parent_unit_id)
    if (roots.length > 1 && !orphanWarned.current) {
      console.warn(
        `OrgGraph: expected one root unit per tenant, found ${roots.length}: ${roots
          .map((r) => r.id)
          .join(', ')}`,
      )
      orphanWarned.current = true
    }
  }, [units])

  const rawNodes = useMemo<Node<OrgUnitNodeData>[]>(
    () =>
      units.map((u) => ({
        id: u.id,
        type: 'orgUnit',
        // dagre overwrites this in useDagreLayout.
        position: { x: 0, y: 0 },
        data: { unit: u, selectedId, onSelectPath, onSelect },
      })),
    [units, selectedId, onSelectPath, onSelect],
  )

  const rawEdges = useMemo<Edge[]>(
    () =>
      units
        // Defensive: drop self-loops; rest of the codebase enforces them
        // server-side, but the canvas should not infinite-loop dagre.
        .filter((u) => u.parent_unit_id && u.parent_unit_id !== u.id)
        .map((u) => ({
          id: `${u.parent_unit_id}->${u.id}`,
          source: u.parent_unit_id!,
          target: u.id,
          type: 'orgUnit',
          data: { selectedPath: onSelectPath },
        })),
    [units, onSelectPath],
  )

  const positionedNodes = useDagreLayout(rawNodes, rawEdges, direction)

  // Smoothly recenter when the user flips direction. `fitView` is
  // stable across renders per xyflow docs.
  useEffect(() => {
    fitView({ padding: 0.2, duration: 240 })
  }, [direction, fitView])

  // Controlled mode → xyflow expects a handler even though we ignore
  // changes (no drag, no edit).
  const onNodesChange: OnNodesChange = useCallback(() => {}, [])

  return (
    <ReactFlow
      nodes={positionedNodes}
      edges={rawEdges}
      onNodesChange={onNodesChange}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitView
      // Keep the xyflow attribution visible (no Pro license) but out of
      // the way of <Controls> at bottom-right.
      attributionPosition="bottom-left"
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnDrag
      zoomOnScroll
    >
      <Background gap={22} size={1} color="var(--px-fg-4)" />
      <Controls position="bottom-right" showInteractive={false} />
      <Panel position="top-right">
        <div
          role="group"
          aria-label="Layout direction"
          className="flex overflow-hidden rounded-md border"
          style={{
            borderColor: 'var(--px-hairline-strong)',
            background: 'var(--px-surface)',
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') {
              e.preventDefault()
              setDirection('TB')
            } else if (e.key === 'ArrowRight') {
              e.preventDefault()
              setDirection('LR')
            }
          }}
        >
          <DirButton
            active={direction === 'TB'}
            onClick={() => setDirection('TB')}
            label="Top → Bottom"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <rect x="4" y="1" width="4" height="3" rx="0.5" fill="currentColor" />
              <rect x="4" y="8" width="4" height="3" rx="0.5" fill="currentColor" />
              <line x1="6" y1="4" x2="6" y2="8" stroke="currentColor" strokeWidth="1" />
            </svg>
          </DirButton>
          <DirButton
            active={direction === 'LR'}
            onClick={() => setDirection('LR')}
            label="Left → Right"
            borderLeft
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <rect x="1" y="4" width="3" height="4" rx="0.5" fill="currentColor" />
              <rect x="8" y="4" width="3" height="4" rx="0.5" fill="currentColor" />
              <line x1="4" y1="6" x2="8" y2="6" stroke="currentColor" strokeWidth="1" />
            </svg>
          </DirButton>
        </div>
      </Panel>
    </ReactFlow>
  )
}

function DirButton({
  active,
  onClick,
  label,
  borderLeft,
  children,
}: {
  active: boolean
  onClick: () => void
  label: string
  borderLeft?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="flex items-center gap-1.5 px-2.5 py-1 text-xs"
      style={{
        color: active ? 'var(--px-accent)' : 'var(--px-fg-2)',
        background: active ? 'var(--px-accent-tint)' : 'transparent',
        borderLeft: borderLeft ? '1px solid var(--px-hairline-strong)' : undefined,
      }}
    >
      {children}
      {label}
    </button>
  )
}

// ─── Public entry: wrap in Provider so consumers don't need to. ────────────

export function OrgGraph(props: OrgGraphProps) {
  return (
    <ReactFlowProvider>
      <OrgGraphInner {...props} />
    </ReactFlowProvider>
  )
}

// ─── Legend (still consumed from the same import path by page.tsx) ─────────

export function OrgLegend() {
  const items: { type: UnitType; label: string }[] = [
    { type: 'company', label: 'Company' },
    { type: 'client_account', label: 'Client account' },
    { type: 'region', label: 'Region' },
    { type: 'division', label: 'Division' },
    { type: 'team', label: 'Team' },
  ]
  return (
    <div
      className="flex flex-wrap gap-2.5 text-[11px]"
      style={{ color: 'var(--px-fg-3)' }}
    >
      {items.map(({ type, label }) => {
        const s = UNIT_TYPE_STYLE[type]
        const Icon = s.icon
        return (
          <span
            key={type}
            className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <span
              className="inline-flex items-center justify-center"
              style={{
                width: 18,
                height: 18,
                borderRadius: 4,
                background: s.bgVar,
                border: `1px solid ${s.lineVar}`,
              }}
            >
              <Icon size={11} color={s.stripVar} strokeWidth={1.8} aria-hidden />
            </span>
            <span>{label}</span>
          </span>
        )
      })}
    </div>
  )
}
