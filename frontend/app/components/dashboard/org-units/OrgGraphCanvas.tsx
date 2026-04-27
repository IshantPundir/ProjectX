'use client'

import {
  useEffect,
  useRef,
  type CSSProperties,
  type ReactNode,
} from 'react'

import { OrgGraphControls } from './OrgGraphControls'
import { OrgUnitEdge } from './OrgUnitEdge'
import { OrgUnitNode } from './OrgUnitNode'
import { useFitView } from './use-fit-view'
import { usePanZoom } from './use-pan-zoom'
import type { GraphNodeData } from './OrgGraph'
import type { LayoutEdge, LayoutNode } from './types'
import { NODE_HEIGHT, NODE_WIDTH } from './use-dagre-layout'

export interface OrgGraphCanvasNodeData {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
  onContextMenu: (id: string) => void
}

interface Props {
  nodes: LayoutNode<OrgGraphCanvasNodeData>[]
  edges: LayoutEdge[]
  /** Currently-selected unit id. Each change animates the viewport so
   *  the selected card is centred. */
  selectedId: string | null
  /** Set of unit ids on the selection ancestry chain — colours edges. */
  selectedPath: Set<string>
  /** Bumped to trigger a fit-view animation. */
  fitRunId: unknown
  onNodeDoubleClick: (id: string) => void
  onNodeContextMenu: (id: string) => void
  /** Optional overlay rendered at wrapper level (above everything,
   *  unaffected by pan/zoom). Use for chrome — direction toggle, etc. */
  overlay?: ReactNode
  /** Id of the unit `worldOverlay` is anchored to. When set, the
   *  overlay is rendered inside the transformed viewport at the unit's
   *  right-edge midpoint, so it tracks the card through pan / zoom /
   *  centring animations. A counter-scale keeps the content at native
   *  pixel size regardless of zoom level. */
  worldAnchorId?: string | null
  worldOverlay?: ReactNode
}

export function OrgGraphCanvas({
  nodes,
  edges,
  selectedId,
  selectedPath,
  fitRunId,
  onNodeDoubleClick,
  onNodeContextMenu,
  overlay,
  worldAnchorId,
  worldOverlay,
}: Props) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const pz = usePanZoom(wrapperRef, { minScale: 0.25, maxScale: 2.5 })

  const fit = useFitView({
    wrapperRef,
    nodes,
    nodeWidth: NODE_WIDTH,
    nodeHeight: NODE_HEIGHT,
    setView: pz.setView,
    runId: fitRunId,
    options: { padding: 0.2, minScale: 0.25, maxScale: 2.5 },
  })

  // Quick lookup to resolve edge anchor coordinates.
  const nodeIndex = useNodeIndex(nodes)

  // Centre the selected card in the viewport at the current zoom level
  // whenever `selectedId` changes. Layout-only changes (direction flip,
  // node add/remove) intentionally don't re-trigger this — fit-view
  // handles those.
  useEffect(() => {
    if (!selectedId) return
    const node = nodeIndex.get(selectedId)
    if (!node) return
    const wrapper = wrapperRef.current
    if (!wrapper) return
    const rect = wrapper.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return

    const cx = node.position.x + NODE_WIDTH / 2
    const cy = node.position.y + NODE_HEIGHT / 2
    const scale = pz.scale
    pz.setView({
      tx: rect.width / 2 - scale * cx,
      ty: rect.height / 2 - scale * cy,
      scale,
      animate: true,
    })
    // Intentionally only re-runs when selectedId changes — we read the
    // latest nodeIndex and pz values from the closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  // SVG edge layer: paths use raw world coordinates. The SVG itself is
  // a 1×1 element with overflow:visible so we don't need to size it to
  // the bbox.
  const svgStyle: CSSProperties = {
    position: 'absolute',
    left: 0,
    top: 0,
    width: 1,
    height: 1,
    overflow: 'visible',
    pointerEvents: 'none',
  }

  return (
    <div
      ref={wrapperRef}
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        cursor: 'grab',
        touchAction: 'none',
      }}
      onPointerDown={pz.onPointerDown}
      onPointerMove={pz.onPointerMove}
      onPointerUp={pz.onPointerUp}
      onPointerCancel={pz.onPointerUp}
      onContextMenu={(e) => {
        // Suppress the browser menu when right-clicking the canvas
        // background (right-click on a card is handled per-node below).
        e.preventDefault()
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          transformOrigin: '0 0',
          // Round translation to integer pixels to avoid sub-pixel
          // rendering blur on cards / pill text. Scale is left exact so
          // wheel-zoom math stays continuous.
          transform: `translate(${Math.round(pz.tx)}px, ${Math.round(pz.ty)}px) scale(${pz.scale})`,
          transition: pz.animating ? 'transform 240ms ease' : 'none',
          willChange: 'transform',
        }}
      >
        <svg style={svgStyle}>
          {edges.map((e) => {
            const s = nodeIndex.get(e.source)
            const t = nodeIndex.get(e.target)
            if (!s || !t) return null
            const sourcePosition = s.sourcePosition ?? 'bottom'
            const targetPosition = t.targetPosition ?? 'top'
            // Anchor coordinates: centre of the matching edge of the card.
            const { sourceX, sourceY } = anchor(s.position, sourcePosition)
            const { sourceX: targetX, sourceY: targetY } = anchor(
              t.position,
              targetPosition,
            )
            return (
              <OrgUnitEdge
                key={e.id}
                id={e.id}
                source={e.source}
                target={e.target}
                sourceX={sourceX}
                sourceY={sourceY}
                targetX={targetX}
                targetY={targetY}
                sourcePosition={sourcePosition}
                targetPosition={targetPosition}
                selectedPath={selectedPath}
              />
            )
          })}
        </svg>

        {nodes.map((n) => (
          <div
            key={n.id}
            data-id={n.id}
            style={{
              position: 'absolute',
              left: n.position.x,
              top: n.position.y,
              width: NODE_WIDTH,
              height: NODE_HEIGHT,
            }}
            onDoubleClick={(e) => {
              e.stopPropagation()
              onNodeDoubleClick(n.id)
            }}
            onContextMenu={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onNodeContextMenu(n.id)
            }}
          >
            <OrgUnitNode
              unit={n.data.unit}
              selectedId={n.data.selectedId}
              onSelectPath={n.data.onSelectPath}
              onSelect={n.data.onSelect}
              onContextMenu={n.data.onContextMenu}
            />
          </div>
        ))}

        {/* World-anchored overlay: lives inside the transformed viewport
         * so it tracks the card through pan / zoom / centring. The
         * counter-scale `1 / pz.scale` neutralises the viewport zoom so
         * the overlay content renders at native pixel sizes. */}
        {worldAnchorId &&
          (() => {
            const anchor = nodeIndex.get(worldAnchorId)
            if (!anchor) return null
            return (
              <div
                // `data-no-pan` lets the pan hook see a click inside
                // any anchored overlay (radial menu pills) and skip
                // initiating a pan — otherwise pointer-capture eats the
                // click event before the pill's onClick can fire.
                data-no-pan
                style={{
                  position: 'absolute',
                  left: anchor.position.x + NODE_WIDTH,
                  top: anchor.position.y + NODE_HEIGHT / 2,
                  transformOrigin: '0 0',
                  transform: `scale(${1 / pz.scale})`,
                  pointerEvents: 'none',
                  zIndex: 5,
                }}
              >
                {worldOverlay}
              </div>
            )
          })()}
      </div>

      <OrgGraphControls
        onZoomIn={() => pz.zoomBy(1.2)}
        onZoomOut={() => pz.zoomBy(1 / 1.2)}
        onFitView={fit}
      />

      {overlay}
    </div>
  )
}

function useNodeIndex(
  nodes: LayoutNode<OrgGraphCanvasNodeData>[],
): Map<string, LayoutNode<OrgGraphCanvasNodeData>> {
  const map = new Map<string, LayoutNode<OrgGraphCanvasNodeData>>()
  for (const n of nodes) map.set(n.id, n)
  return map
}

function anchor(
  pos: { x: number; y: number },
  side: 'top' | 'bottom' | 'left' | 'right',
): { sourceX: number; sourceY: number } {
  switch (side) {
    case 'top':
      return { sourceX: pos.x + NODE_WIDTH / 2, sourceY: pos.y }
    case 'bottom':
      return { sourceX: pos.x + NODE_WIDTH / 2, sourceY: pos.y + NODE_HEIGHT }
    case 'left':
      return { sourceX: pos.x, sourceY: pos.y + NODE_HEIGHT / 2 }
    case 'right':
      return { sourceX: pos.x + NODE_WIDTH, sourceY: pos.y + NODE_HEIGHT / 2 }
  }
}
