# Drop @xyflow/react — Hand-Rolled Org Graph Viewport

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `@xyflow/react` (and the transitive `@xyflow/system`) in the org-units canvas with a hand-rolled viewport, while keeping the public `<OrgGraph>` API, all peripheral components, all design tokens, and all composition tests intact.

**Architecture:** A new `<OrgGraphCanvas>` owns a single transformed viewport `<div>` containing an absolutely-positioned node layer plus a full-canvas `<svg>` for bezier edges. Two hooks — `usePanZoom` (wheel-zoom-around-cursor + background-drag-to-pan) and `useFitView` (bbox-fit on mount and direction change, animated via a CSS transition on `transform`) — provide the interaction model. `OrgGraph.tsx` becomes a thin wrapper that wires units → dagre layout → canvas, plus existing overlays (context menu, inline create) and the direction toggle.

**Tech Stack:** React 19, TypeScript strict, `@dagrejs/dagre` for layout (kept), Vitest + @testing-library/react for tests. **No xyflow.**

---

## Working Directory

All paths are absolute under `/home/ishant/Projects/ProjectX`. Frontend root is `frontend/app/`. Run all `npm` commands from `frontend/app/`.

---

## File Structure

### Files to create

| Path | Responsibility |
|---|---|
| `frontend/app/components/dashboard/org-units/types.ts` | Local `Position` and `Direction` enums plus shared `LayoutNode` / `LayoutEdge` types so no module imports xyflow types. |
| `frontend/app/components/dashboard/org-units/edge-path.ts` | Pure function `getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, curvature? }) → string`. Replaces `@xyflow/react`'s `getBezierPath`. |
| `frontend/app/components/dashboard/org-units/use-pan-zoom.ts` | Hook that returns `{ tx, ty, scale, animating, setView, zoomBy, onPointerDown, onPointerMove, onPointerUp }` and attaches a non-passive `wheel` listener to a wrapper ref. |
| `frontend/app/components/dashboard/org-units/use-fit-view.ts` | Hook that, given a wrapper ref + positioned nodes, computes a bbox + target transform and applies it via the `setView` setter from `usePanZoom` whenever a `runId` changes. |
| `frontend/app/components/dashboard/org-units/OrgGraphCanvas.tsx` | The viewport: wrapper div, dot-grid background, transformed inner viewport, SVG edge layer, node layer, direction toggle, zoom controls cluster. Receives positioned nodes + edges + handlers. |
| `frontend/app/components/dashboard/org-units/OrgGraphControls.tsx` | Bottom-right cluster: zoom-in, zoom-out, fit-view buttons. |
| `frontend/app/tests/components/edge-path.test.ts` | Unit tests for the bezier path function. |
| `frontend/app/tests/components/use-pan-zoom.test.ts` | Smoke + math tests for the pan/zoom hook. |

### Files to modify

| Path | Change |
|---|---|
| `frontend/app/components/dashboard/org-units/OrgGraph.tsx` | Remove every xyflow import; replace `<ReactFlowProvider>` + `<ReactFlow>` block with `<OrgGraphCanvas>`; preserve `OrgGraphProps`, `GraphNodeData`, `Pressure`, and `OrgLegend` exports verbatim. Drop the `SUPPRESS_DEFAULT_SELECTED_OUTLINE` style block. The `data-id` lookup for the keyboard-triggered radial menu still works because `OrgGraphCanvas` writes `data-id` on every node wrapper. |
| `frontend/app/components/dashboard/org-units/OrgUnitNode.tsx` | Drop `Handle` and `NodeProps` imports. The component's exported signature becomes `OrgUnitNode(props: OrgUnitNodeProps)` with the explicit fields it uses today, instead of unwrapping `data` from `NodeProps`. Card markup, role, ARIA, data-state, focus ring, and keyboard handler are preserved exactly. |
| `frontend/app/components/dashboard/org-units/OrgUnitEdge.tsx` | Drop `BaseEdge`, `getBezierPath`, `EdgeProps` imports. Component takes explicit props (`sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, source, target, selectedPath`) and returns a single `<path>` element. |
| `frontend/app/components/dashboard/org-units/use-dagre-layout.ts` | Drop the xyflow `Position`, `Edge`, `Node` imports. Use the local types from `types.ts`. Export the same `getDagreLayout` / `useDagreLayout` API but typed against `LayoutNode<T>` / `LayoutEdge` from the new module. The `measured` field can go away — nothing else needs it. |
| `frontend/app/tests/components/OrgUnitNode.test.tsx` | Drop `ReactFlowProvider`, `Position`, `NodeProps` imports. Render `<OrgUnitNode unit={…} selectedId={…} … />` directly. All `getByRole`/`getByText` assertions stay. |
| `frontend/app/tests/components/use-dagre-layout.test.ts` | Drop `Position`, `Edge`, `Node` imports from xyflow; use local types and string literals. |
| `frontend/app/tests/components/OrgGraph.test.tsx` | Remove the per-test `ResizeObserver` polyfill block in `beforeEach` (no longer needed). All assertions stay. |
| `frontend/app/tests/setup.ts` | Drop the `FakeResizeObserver` and `FakeDOMMatrixReadOnly` polyfills (both were purely for xyflow). Keep the `StoragePolyfill`. |
| `frontend/app/package.json` | Remove `@xyflow/react` from `dependencies`. (`@xyflow/system` is a transitive dep of `@xyflow/react` — not listed in our `package.json`, so nothing to delete there. It will disappear from `node_modules` after `npm install`.) |

### Files NOT to modify

- `frontend/app/components/dashboard/org-units/OrgUnitContextMenu.tsx`
- `frontend/app/components/dashboard/org-units/OrgUnitInlineCreate.tsx`
- `frontend/app/components/dashboard/org-units/unit-type-style.tsx`
- `frontend/app/components/dashboard/org-units/unit-children-rules.ts`
- `frontend/app/components/dashboard/org-units/use-direction-toggle.ts`
- `frontend/app/app/(dashboard)/settings/org-units/page.tsx` — its `<OrgGraph units selectedId hoverId onSelect onHover onOpen onDelete onCreateChild />` contract MUST stay identical.
- `frontend/app/tests/components/OrgUnitContextMenu.test.tsx`
- `frontend/app/tests/components/OrgUnitInlineCreate.test.tsx`

---

## Task 1: Create local types module

**Files:**
- Create: `frontend/app/components/dashboard/org-units/types.ts`

- [ ] **Step 1: Write the file**

```typescript
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
```

- [ ] **Step 2: Verify the file compiles**

Run from `frontend/app/`:
```
npx tsc --noEmit components/dashboard/org-units/types.ts
```
Expected: zero output (success).

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/org-units/types.ts
git commit -m "feat(org-graph): add local Position/Direction/LayoutNode types"
```

---

## Task 2: Build pure bezier-path function

**Files:**
- Create: `frontend/app/components/dashboard/org-units/edge-path.ts`
- Test: `frontend/app/tests/components/edge-path.test.ts`

This replaces `@xyflow/react`'s `getBezierPath`. The output is the SVG `d` attribute for a cubic bezier between two anchor points, with control points offset perpendicular to each anchor's `Position`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/app/tests/components/edge-path.test.ts
import { describe, expect, it } from 'vitest'

import { getBezierPath } from '@/components/dashboard/org-units/edge-path'

describe('getBezierPath', () => {
  it('starts at the source point and ends at the target point', () => {
    const path = getBezierPath({
      sourceX: 0,
      sourceY: 0,
      sourcePosition: 'bottom',
      targetX: 100,
      targetY: 200,
      targetPosition: 'top',
    })
    expect(path.startsWith('M0,0 ')).toBe(true)
    expect(path.endsWith(' 100,200')).toBe(true)
    expect(path).toMatch(/^M0,0 C[\-\d.,\s]+ 100,200$/)
  })

  it('offsets control points along Y for vertical (TB) edges', () => {
    const path = getBezierPath({
      sourceX: 50,
      sourceY: 0,
      sourcePosition: 'bottom',
      targetX: 50,
      targetY: 100,
      targetPosition: 'top',
      curvature: 0.25,
    })
    // First control: same X as source (50), Y offset down by 25 (100 * 0.25).
    // Second control: same X as target (50), Y offset up by 25.
    expect(path).toBe('M50,0 C50,25 50,75 50,100')
  })

  it('offsets control points along X for horizontal (LR) edges', () => {
    const path = getBezierPath({
      sourceX: 0,
      sourceY: 50,
      sourcePosition: 'right',
      targetX: 100,
      targetY: 50,
      targetPosition: 'left',
      curvature: 0.25,
    })
    // First control: X offset right by 25, same Y. Second: X offset left by 25.
    expect(path).toBe('M0,50 C25,50 75,50 100,50')
  })

  it('uses absolute distance so reversed-direction inputs still curve outward', () => {
    const path = getBezierPath({
      sourceX: 50,
      sourceY: 100,
      sourcePosition: 'bottom',
      targetX: 50,
      targetY: 0,
      targetPosition: 'top',
      curvature: 0.25,
    })
    // Source bottom always pushes control DOWN by |target - source| * curvature.
    expect(path).toBe('M50,100 C50,125 50,-25 50,0')
  })

  it('defaults curvature to 0.25 when omitted', () => {
    const a = getBezierPath({
      sourceX: 0, sourceY: 0, sourcePosition: 'bottom',
      targetX: 0, targetY: 100, targetPosition: 'top',
    })
    const b = getBezierPath({
      sourceX: 0, sourceY: 0, sourcePosition: 'bottom',
      targetX: 0, targetY: 100, targetPosition: 'top',
      curvature: 0.25,
    })
    expect(a).toBe(b)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend/app && npx vitest run tests/components/edge-path.test.ts
```
Expected: FAIL — `Cannot find module '@/components/dashboard/org-units/edge-path'`.

- [ ] **Step 3: Implement the function**

```typescript
// frontend/app/components/dashboard/org-units/edge-path.ts
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
```

- [ ] **Step 4: Run test to verify it passes**

```
cd frontend/app && npx vitest run tests/components/edge-path.test.ts
```
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/org-units/edge-path.ts frontend/app/tests/components/edge-path.test.ts
git commit -m "feat(org-graph): add hand-rolled getBezierPath replacing xyflow"
```

---

## Task 3: Build the pan-zoom hook

**Files:**
- Create: `frontend/app/components/dashboard/org-units/use-pan-zoom.ts`
- Test: `frontend/app/tests/components/use-pan-zoom.test.ts`

The hook owns viewport `tx`, `ty`, `scale`, an `animating` flag (so consumers can toggle a CSS transition), and exposes:
- `setView({ tx, ty, scale, animate? })` — imperative setter (used by fit-view).
- `zoomBy(factor, anchor?)` — programmatic zoom, anchored at viewport center by default (used by zoom buttons).
- `onPointerDown / onPointerMove / onPointerUp` — drag-to-pan handlers; pan is suppressed if the pointer-down target is inside `[data-node-card]`.
- A `useEffect` attached to the wrapper ref that registers a non-passive `wheel` listener for cursor-anchored zoom.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/app/tests/components/use-pan-zoom.test.ts
import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useRef } from 'react'

import { usePanZoom } from '@/components/dashboard/org-units/use-pan-zoom'

function setup() {
  return renderHook(() => {
    const ref = useRef<HTMLDivElement>(null)
    const pz = usePanZoom(ref, { minScale: 0.25, maxScale: 2.5 })
    return { ref, pz }
  })
}

describe('usePanZoom', () => {
  it('initialises at identity transform', () => {
    const { result } = setup()
    expect(result.current.pz.tx).toBe(0)
    expect(result.current.pz.ty).toBe(0)
    expect(result.current.pz.scale).toBe(1)
    expect(result.current.pz.animating).toBe(false)
  })

  it('setView updates tx/ty/scale and toggles animating', () => {
    const { result } = setup()
    act(() => {
      result.current.pz.setView({ tx: 10, ty: 20, scale: 0.5, animate: true })
    })
    expect(result.current.pz.tx).toBe(10)
    expect(result.current.pz.ty).toBe(20)
    expect(result.current.pz.scale).toBe(0.5)
    expect(result.current.pz.animating).toBe(true)
  })

  it('zoomBy clamps within [minScale, maxScale]', () => {
    const { result } = setup()
    act(() => {
      // Try to zoom way past the cap.
      result.current.pz.zoomBy(100, { x: 0, y: 0 })
    })
    expect(result.current.pz.scale).toBe(2.5)
    act(() => {
      result.current.pz.zoomBy(0.0001, { x: 0, y: 0 })
    })
    expect(result.current.pz.scale).toBe(0.25)
  })

  it('zoomBy preserves the canvas point under the supplied anchor', () => {
    const { result } = setup()
    // Shift to a known transform first.
    act(() => result.current.pz.setView({ tx: 100, ty: 50, scale: 1 }))
    // Canvas point currently under viewport coord (200, 100):
    //   cx = (200 - 100) / 1 = 100
    //   cy = (100 - 50) / 1 = 50
    act(() => result.current.pz.zoomBy(2, { x: 200, y: 100 }))
    expect(result.current.pz.scale).toBe(2)
    // After zoom, that same canvas point must still sit at (200, 100):
    //   200 = tx' + 2 * 100  ⇒  tx' = 0
    //   100 = ty' + 2 * 50   ⇒  ty' = 0
    expect(result.current.pz.tx).toBe(0)
    expect(result.current.pz.ty).toBe(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend/app && npx vitest run tests/components/use-pan-zoom.test.ts
```
Expected: FAIL — `Cannot find module '@/components/dashboard/org-units/use-pan-zoom'`.

- [ ] **Step 3: Implement the hook**

```typescript
// frontend/app/components/dashboard/org-units/use-pan-zoom.ts
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from 'react'

interface PanZoomOptions {
  minScale?: number
  maxScale?: number
  /** Multiplier applied per wheel notch. Default 1.1. */
  zoomStep?: number
}

interface SetViewArgs {
  tx: number
  ty: number
  scale: number
  /** When true, the consumer's CSS transition on `transform` should fire. */
  animate?: boolean
}

interface PanZoomApi {
  tx: number
  ty: number
  scale: number
  animating: boolean
  setView: (args: SetViewArgs) => void
  /** Multiplies the current scale, clamped, anchored at the supplied
   *  viewport-local point (default: wrapper center). Static (translate
   *  + scale) so the canvas point under the anchor stays under it. */
  zoomBy: (factor: number, anchor?: { x: number; y: number }) => void
  onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerUp: (e: ReactPointerEvent<HTMLDivElement>) => void
}

/**
 * Pan + zoom for a fixed-position wrapper. The wrapper renders the
 * background pane and the (transformed) viewport; pan is suppressed
 * when the pointer-down target is inside a `[data-node-card]` element
 * so node clicks still fire.
 */
export function usePanZoom(
  wrapperRef: RefObject<HTMLDivElement | null>,
  opts: PanZoomOptions = {},
): PanZoomApi {
  const minScale = opts.minScale ?? 0.25
  const maxScale = opts.maxScale ?? 2.5
  const zoomStep = opts.zoomStep ?? 1.1

  const [tx, setTx] = useState(0)
  const [ty, setTy] = useState(0)
  const [scale, setScale] = useState(1)
  const [animating, setAnimating] = useState(false)

  // Latest values for use inside event handlers without stale closures.
  // The wheel listener is attached imperatively (passive: false) and
  // doesn't pick up new closures when state changes, so we mirror state
  // into a ref here. Updating the ref directly during render is flagged
  // by `react-hooks/refs` (lint error in React 19), so do it in a
  // useLayoutEffect that fires after every render.
  const latest = useRef({ tx, ty, scale })
  useLayoutEffect(() => {
    latest.current = { tx, ty, scale }
  }, [tx, ty, scale])

  const panState = useRef<{
    pointerId: number
    startClientX: number
    startClientY: number
    startTx: number
    startTy: number
  } | null>(null)

  const setView = useCallback((args: SetViewArgs) => {
    setAnimating(args.animate === true)
    setTx(args.tx)
    setTy(args.ty)
    setScale(args.scale)
  }, [])

  const clampScale = useCallback(
    (s: number) => Math.max(minScale, Math.min(maxScale, s)),
    [minScale, maxScale],
  )

  const zoomBy = useCallback(
    (factor: number, anchor?: { x: number; y: number }) => {
      const wrapper = wrapperRef.current
      let ax = anchor?.x
      let ay = anchor?.y
      if (ax === undefined || ay === undefined) {
        const rect = wrapper?.getBoundingClientRect()
        ax = (rect?.width ?? 0) / 2
        ay = (rect?.height ?? 0) / 2
      }
      const cur = latest.current
      const newScale = clampScale(cur.scale * factor)
      const ratio = newScale / cur.scale
      // Keep the canvas point under (ax, ay) anchored:
      //   newTx = ax - ratio * (ax - tx)
      const newTx = ax - ratio * (ax - cur.tx)
      const newTy = ay - ratio * (ay - cur.ty)
      setAnimating(false)
      setScale(newScale)
      setTx(newTx)
      setTy(newTy)
    },
    [clampScale, wrapperRef],
  )

  // Wheel listener attached imperatively so we can pass passive: false.
  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    function onWheel(e: WheelEvent) {
      e.preventDefault()
      const rect = el!.getBoundingClientRect()
      const ax = e.clientX - rect.left
      const ay = e.clientY - rect.top
      const factor = e.deltaY < 0 ? zoomStep : 1 / zoomStep
      const cur = latest.current
      const newScale = clampScale(cur.scale * factor)
      const ratio = newScale / cur.scale
      setAnimating(false)
      setScale(newScale)
      setTx(ax - ratio * (ax - cur.tx))
      setTy(ay - ratio * (ay - cur.ty))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [wrapperRef, clampScale, zoomStep])

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      // Only left-button drags initiate a pan.
      if (e.button !== 0) return
      const target = e.target as HTMLElement | null
      // If the press lands on a node card or any interactive overlay,
      // skip pan so the node's own click/contextmenu handlers fire.
      if (target?.closest('[data-node-card]')) return
      if (target?.closest('[data-no-pan]')) return
      panState.current = {
        pointerId: e.pointerId,
        startClientX: e.clientX,
        startClientY: e.clientY,
        startTx: latest.current.tx,
        startTy: latest.current.ty,
      }
      setAnimating(false)
      ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
    },
    [],
  )

  const onPointerMove = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      const ps = panState.current
      if (!ps) return
      const dx = e.clientX - ps.startClientX
      const dy = e.clientY - ps.startClientY
      setTx(ps.startTx + dx)
      setTy(ps.startTy + dy)
    },
    [],
  )

  const onPointerUp = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      const ps = panState.current
      if (!ps) return
      panState.current = null
      ;(e.currentTarget as HTMLElement).releasePointerCapture?.(ps.pointerId)
    },
    [],
  )

  return {
    tx,
    ty,
    scale,
    animating,
    setView,
    zoomBy,
    onPointerDown,
    onPointerMove,
    onPointerUp,
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```
cd frontend/app && npx vitest run tests/components/use-pan-zoom.test.ts
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/org-units/use-pan-zoom.ts frontend/app/tests/components/use-pan-zoom.test.ts
git commit -m "feat(org-graph): add usePanZoom hook (wheel-zoom + drag-pan)"
```

---

## Task 4: Build the fit-view hook

**Files:**
- Create: `frontend/app/components/dashboard/org-units/use-fit-view.ts`

This hook computes the bounding box of all positioned nodes (using a fixed `nodeWidth`/`nodeHeight` since all org-unit cards are uniform), then calls `setView` with an animated transform that centres the bbox in the wrapper with a padding fraction. It re-runs whenever `runId` changes — consumers bump `runId` on initial mount and on direction change.

- [ ] **Step 1: Write the file**

```typescript
// frontend/app/components/dashboard/org-units/use-fit-view.ts
import { useCallback, useEffect, type RefObject } from 'react'

import type { LayoutNode } from './types'

interface FitViewOptions {
  /** Fraction of viewport to leave as breathing room. Default 0.2 (20%). */
  padding?: number
  /** Minimum scale clamp — matches usePanZoom's minScale. */
  minScale?: number
  /** Maximum scale clamp — matches usePanZoom's maxScale. */
  maxScale?: number
}

interface FitViewArgs<T extends Record<string, unknown>> {
  wrapperRef: RefObject<HTMLDivElement | null>
  nodes: LayoutNode<T>[]
  nodeWidth: number
  nodeHeight: number
  setView: (args: { tx: number; ty: number; scale: number; animate?: boolean }) => void
  /** Bumped each time consumers want a fit. Mount-time value: any. */
  runId: unknown
  options?: FitViewOptions
}

/**
 * Computes the bounding box of `nodes` (using uniform node dimensions),
 * then animates the viewport transform so the bbox is centred and fits
 * within the wrapper with `padding` breathing room. Re-runs whenever
 * `runId` reference-changes.
 */
export function useFitView<T extends Record<string, unknown>>({
  wrapperRef,
  nodes,
  nodeWidth,
  nodeHeight,
  setView,
  runId,
  options,
}: FitViewArgs<T>): () => void {
  const padding = options?.padding ?? 0.2
  const minScale = options?.minScale ?? 0.25
  const maxScale = options?.maxScale ?? 2.5

  const fit = useCallback(() => {
    const wrapper = wrapperRef.current
    if (!wrapper || nodes.length === 0) return
    const rect = wrapper.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return

    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const n of nodes) {
      minX = Math.min(minX, n.position.x)
      minY = Math.min(minY, n.position.y)
      maxX = Math.max(maxX, n.position.x + nodeWidth)
      maxY = Math.max(maxY, n.position.y + nodeHeight)
    }
    const bboxW = maxX - minX
    const bboxH = maxY - minY
    if (bboxW <= 0 || bboxH <= 0) return

    const usableW = rect.width * (1 - padding)
    const usableH = rect.height * (1 - padding)
    const rawScale = Math.min(usableW / bboxW, usableH / bboxH)
    const scale = Math.max(minScale, Math.min(maxScale, rawScale))

    const tx = (rect.width - bboxW * scale) / 2 - minX * scale
    const ty = (rect.height - bboxH * scale) / 2 - minY * scale

    setView({ tx, ty, scale, animate: true })
  }, [wrapperRef, nodes, nodeWidth, nodeHeight, setView, padding, minScale, maxScale])

  useEffect(() => {
    fit()
    // `runId` intentionally drives re-fits; `fit` already depends on
    // the inputs that affect the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, fit])

  return fit
}
```

- [ ] **Step 2: Verify the file type-checks**

```
cd frontend/app && npx tsc --noEmit
```
Expected: zero errors. (If you see errors in unrelated files that already exist on this branch, ignore them; only block on errors in files this plan touches.)

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/org-units/use-fit-view.ts
git commit -m "feat(org-graph): add useFitView hook (bbox-centred animated transform)"
```

---

## Task 5: Build the zoom-controls cluster

**Files:**
- Create: `frontend/app/components/dashboard/org-units/OrgGraphControls.tsx`

Bottom-right cluster mirroring the look of the existing `<Controls>` xyflow component but built from our existing `--px-*` tokens. Three buttons: zoom-in, zoom-out, fit-view.

- [ ] **Step 1: Write the file**

```tsx
// frontend/app/components/dashboard/org-units/OrgGraphControls.tsx
import type { CSSProperties } from 'react'
import { Maximize, Minus, Plus } from 'lucide-react'

interface Props {
  onZoomIn: () => void
  onZoomOut: () => void
  onFitView: () => void
}

const buttonStyle: CSSProperties = {
  width: 28,
  height: 28,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'var(--px-surface)',
  color: 'var(--px-fg-2)',
  cursor: 'pointer',
}

export function OrgGraphControls({ onZoomIn, onZoomOut, onFitView }: Props) {
  return (
    <div
      data-no-pan
      role="group"
      aria-label="Canvas controls"
      style={{
        position: 'absolute',
        right: 12,
        bottom: 12,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        borderRadius: 6,
        border: '1px solid var(--px-hairline-strong)',
        boxShadow: 'var(--px-shadow-sm)',
        background: 'var(--px-surface)',
        zIndex: 10,
      }}
    >
      <button
        type="button"
        aria-label="Zoom in"
        onClick={onZoomIn}
        style={{ ...buttonStyle, borderBottom: '1px solid var(--px-hairline)' }}
      >
        <Plus size={14} aria-hidden strokeWidth={2} />
      </button>
      <button
        type="button"
        aria-label="Zoom out"
        onClick={onZoomOut}
        style={{ ...buttonStyle, borderBottom: '1px solid var(--px-hairline)' }}
      >
        <Minus size={14} aria-hidden strokeWidth={2} />
      </button>
      <button
        type="button"
        aria-label="Fit view"
        onClick={onFitView}
        style={buttonStyle}
      >
        <Maximize size={13} aria-hidden strokeWidth={2} />
      </button>
    </div>
  )
}
```

- [ ] **Step 2: Verify type-check**

```
cd frontend/app && npx tsc --noEmit
```
Expected: zero errors in `OrgGraphControls.tsx`.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/org-units/OrgGraphControls.tsx
git commit -m "feat(org-graph): add zoom/fit-view controls cluster"
```

---

## Task 6: Rewrite OrgUnitEdge to draw a plain SVG path

**Files:**
- Modify: `frontend/app/components/dashboard/org-units/OrgUnitEdge.tsx`

Drop xyflow's `BaseEdge`, `getBezierPath`, `EdgeProps`. The edge becomes a normal React component that takes anchor coordinates + positions + the selected-path Set and renders one `<path>`.

- [ ] **Step 1: Replace the file contents**

```tsx
// frontend/app/components/dashboard/org-units/OrgUnitEdge.tsx
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
```

- [ ] **Step 2: Verify type-check passes**

```
cd frontend/app && npx tsc --noEmit
```
Expected: errors only in files we have not yet rewritten (`OrgGraph.tsx` still imports the old `OrgUnitEdge` shape). Move on.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/org-units/OrgUnitEdge.tsx
git commit -m "refactor(org-graph): rewrite OrgUnitEdge as a plain SVG <path>"
```

---

## Task 7: Rewrite OrgUnitNode to drop xyflow Handle/NodeProps

**Files:**
- Modify: `frontend/app/components/dashboard/org-units/OrgUnitNode.tsx`

The card markup, `role="button"`, `aria-label`, `aria-pressed`, `data-state`, focus-visible ring, keyboard handler (Enter/Space/Shift+F10/ContextMenu), the open-roles badge, and all `cardStyle` styling stay *exactly* as they are. Only the props shape and the two `<Handle>` siblings change. We also add `data-node-card` so the pan hook knows to skip pan when pressing on a node, and we no longer render xyflow's invisible handles.

- [ ] **Step 1: Replace the file contents**

```tsx
// frontend/app/components/dashboard/org-units/OrgUnitNode.tsx
import { memo, type CSSProperties, type KeyboardEvent } from 'react'

import type { GraphNodeData } from './OrgGraph'
import { getUnitTypeStyle } from './unit-type-style'

export interface OrgUnitNodeProps {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
  onContextMenu?: (id: string) => void
}

type Pressure = 'hot' | 'steady' | null

function pressureForOpenRoles(openRoles: number): Pressure {
  if (openRoles >= 3) return 'hot'
  if (openRoles > 0) return 'steady'
  return null
}

function OrgUnitNodeImpl({
  unit,
  selectedId,
  onSelectPath,
  onSelect,
  onContextMenu,
}: OrgUnitNodeProps) {
  const style = getUnitTypeStyle(unit.unit_type)
  const Icon = style.icon

  const isSelected = selectedId === unit.id
  const isOnPath = !isSelected && onSelectPath.has(unit.id)
  const pressure = pressureForOpenRoles(unit.openRoles)

  const dataState: 'selected' | 'on-path' | 'default' = isSelected
    ? 'selected'
    : isOnPath
      ? 'on-path'
      : 'default'

  function handleKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(unit.id)
      return
    }
    // OS-standard "open context menu" shortcuts.
    if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
      e.preventDefault()
      onContextMenu?.(unit.id)
    }
  }

  const badgeClass =
    pressure === 'hot'
      ? 'bg-red-50 text-red-700 border-red-200'
      : pressure === 'steady'
        ? 'bg-amber-50 text-amber-700 border-amber-200'
        : ''

  const cardStyle: CSSProperties = {
    width: 168,
    height: 52,
    background: 'var(--px-surface)',
    borderRadius: 10,
    border: `1px solid ${
      isSelected
        ? 'var(--px-accent)'
        : isOnPath
          ? 'var(--px-accent-line)'
          : 'var(--px-hairline-strong)'
    }`,
    boxShadow: isSelected
      ? '0 0 0 3px var(--px-accent-glow)'
      : 'var(--px-shadow-sm)',
    display: 'flex',
    alignItems: 'center',
    paddingRight: 8,
    overflow: 'hidden',
    transition: 'box-shadow 120ms ease, border-color 120ms ease',
    cursor: 'pointer',
  }

  return (
    <div
      role="button"
      tabIndex={0}
      data-node-card
      aria-label={`${unit.unit_type}: ${unit.name}`}
      aria-pressed={isSelected}
      data-state={dataState}
      className="focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--px-accent)]"
      style={cardStyle}
      onClick={() => onSelect(unit.id)}
      onKeyDown={handleKey}
    >
      <span
        aria-hidden="true"
        style={{
          width: 4,
          alignSelf: 'stretch',
          background: style.stripVar,
          borderRadius: '10px 0 0 10px',
          marginRight: 10,
          flex: 'none',
        }}
      />
      <span
        aria-hidden="true"
        style={{
          width: 28,
          height: 28,
          borderRadius: 7,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flex: 'none',
          marginRight: 9,
          background: style.bgVar,
          border: `1px solid ${style.lineVar}`,
        }}
      >
        <Icon size={16} color={style.stripVar} strokeWidth={1.8} aria-hidden />
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: 'block',
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--px-fg)',
            lineHeight: 1.15,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {unit.name}
        </span>
        <span
          style={{
            display: 'block',
            fontSize: 10.5,
            color: 'var(--px-fg-3)',
            marginTop: 2,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {unit.unit_type} &middot; {unit.member_count} members
        </span>
      </span>
      {pressure && (
        <span
          data-testid="open-roles-badge"
          className={`ml-2 flex-none rounded-full border px-[7px] py-[2px] text-[10px] font-bold ${badgeClass}`}
        >
          {unit.openRoles}
        </span>
      )}
    </div>
  )
}

export const OrgUnitNode = memo(OrgUnitNodeImpl)
```

- [ ] **Step 2: Update the unit test**

Rewrite `frontend/app/tests/components/OrgUnitNode.test.tsx` so it constructs props directly:

```tsx
// frontend/app/tests/components/OrgUnitNode.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'
import { OrgUnitNode } from '@/components/dashboard/org-units/OrgUnitNode'
import type { GraphNodeData } from '@/components/dashboard/org-units/OrgGraph'

function makeUnit(overrides: Partial<GraphNodeData> = {}): GraphNodeData {
  return {
    id: 'u1',
    client_id: 't1',
    parent_unit_id: null,
    name: 'Engineering',
    unit_type: 'division',
    member_count: 5,
    created_at: '2026-04-01T00:00:00Z',
    created_by: null,
    created_by_email: null,
    deletable_by: null,
    deletable_by_email: null,
    admin_delete_disabled: false,
    is_accessible: true,
    admin_emails: [],
    is_root: false,
    company_profile: null,
    company_profile_completed_at: null,
    metadata: null,
    openRoles: 0,
    pressure: 'cool',
    ...overrides,
  }
}

function renderNode(opts: {
  unit?: Partial<GraphNodeData>
  selectedId?: string | null
  onSelectPath?: Set<string>
  onSelect?: (id: string) => void
  onContextMenu?: (id: string) => void
} = {}) {
  const unit = makeUnit(opts.unit)
  const onSelect = opts.onSelect ?? vi.fn()
  const onContextMenu = opts.onContextMenu ?? vi.fn()
  const utils = renderWithProviders(
    <OrgUnitNode
      unit={unit}
      selectedId={opts.selectedId ?? null}
      onSelectPath={opts.onSelectPath ?? new Set<string>()}
      onSelect={onSelect}
      onContextMenu={onContextMenu}
    />,
  )
  return { ...utils, unit, onSelect, onContextMenu }
}

describe('OrgUnitNode', () => {
  it('renders the unit name', () => {
    renderNode()
    expect(screen.getByText('Engineering')).toBeInTheDocument()
  })

  it('renders the type-and-member-count subtitle', () => {
    renderNode()
    expect(screen.getByText(/division\s+·\s+5 members/)).toBeInTheDocument()
  })

  it('hides the open-roles badge when openRoles is 0', () => {
    renderNode()
    expect(screen.queryByTestId('open-roles-badge')).not.toBeInTheDocument()
  })

  it('renders an amber-styled badge for openRoles 1–2', () => {
    renderNode({ unit: { openRoles: 2 } })
    const badge = screen.getByTestId('open-roles-badge')
    expect(badge).toHaveTextContent('2')
    expect(badge.className).toMatch(/amber/)
  })

  it('renders a red-styled badge for openRoles 3+', () => {
    renderNode({ unit: { openRoles: 5 } })
    const badge = screen.getByTestId('open-roles-badge')
    expect(badge).toHaveTextContent('5')
    expect(badge.className).toMatch(/red/)
  })

  it('calls onSelect with the unit id on click', () => {
    const { onSelect } = renderNode()
    fireEvent.click(screen.getByRole('button', { name: /division: Engineering/ }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('u1')
  })

  it('calls onSelect on Enter and Space keypress', () => {
    const { onSelect } = renderNode()
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'Enter' })
    fireEvent.keyDown(card, { key: ' ' })
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('exposes data-state="selected" when selectedId matches', () => {
    renderNode({ selectedId: 'u1' })
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'selected')
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  })

  it('exposes data-state="on-path" when in selectedPath but not selected', () => {
    renderNode({ selectedId: 'other', onSelectPath: new Set(['u1']) })
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'on-path')
  })

  it('falls back to team style and warns for an unknown unit_type', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    renderNode({
      unit: { unit_type: 'totally_unknown' as GraphNodeData['unit_type'] },
    })
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('unknown unit_type'))
    warn.mockRestore()
  })

  it('calls data.onContextMenu on Shift+F10', () => {
    const onContextMenu = vi.fn()
    renderNode({ onContextMenu })
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'F10', shiftKey: true })
    expect(onContextMenu).toHaveBeenCalledTimes(1)
    expect(onContextMenu).toHaveBeenCalledWith('u1')
  })

  it('calls data.onContextMenu on the ContextMenu key', () => {
    const onContextMenu = vi.fn()
    renderNode({ onContextMenu })
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'ContextMenu' })
    expect(onContextMenu).toHaveBeenCalledTimes(1)
  })

  it('marks the card with data-node-card so the pan hook can skip it', () => {
    renderNode()
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    expect(card.hasAttribute('data-node-card')).toBe(true)
  })
})
```

- [ ] **Step 3: Run the OrgUnitNode tests**

```
cd frontend/app && npx vitest run tests/components/OrgUnitNode.test.tsx
```
Expected: PASS, all tests including the new `data-node-card` assertion.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/dashboard/org-units/OrgUnitNode.tsx frontend/app/tests/components/OrgUnitNode.test.tsx
git commit -m "refactor(org-graph): drop xyflow Handle/NodeProps from OrgUnitNode"
```

---

## Task 8: Migrate use-dagre-layout to local types

**Files:**
- Modify: `frontend/app/components/dashboard/org-units/use-dagre-layout.ts`
- Modify: `frontend/app/tests/components/use-dagre-layout.test.ts`

Drop the `Position`, `Edge`, `Node` imports from xyflow. Use `LayoutNode`, `LayoutEdge`, `Position`, `Direction` from `./types`. Remove the `measured` field — nothing reads it once xyflow is gone.

- [ ] **Step 1: Rewrite the implementation**

```typescript
// frontend/app/components/dashboard/org-units/use-dagre-layout.ts
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
export function getDagreLayout<T extends Record<string, unknown>>(
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
export function useDagreLayout<T extends Record<string, unknown>>(
  nodes: LayoutNode<T>[],
  edges: LayoutEdge[],
  direction: Direction,
): LayoutNode<T>[] {
  return useMemo(
    () => getDagreLayout(nodes, edges, direction),
    [nodes, edges, direction],
  )
}
```

- [ ] **Step 2: Rewrite the test**

```typescript
// frontend/app/tests/components/use-dagre-layout.test.ts
import { describe, expect, it } from 'vitest'

import {
  getDagreLayout,
  NODE_HEIGHT,
  NODE_WIDTH,
} from '@/components/dashboard/org-units/use-dagre-layout'
import type {
  LayoutEdge,
  LayoutNode,
} from '@/components/dashboard/org-units/types'

function makeNode(id: string): LayoutNode<{ label: string }> {
  return {
    id,
    type: 'orgUnit',
    position: { x: 0, y: 0 },
    data: { label: id },
  }
}

function makeEdge(source: string, target: string): LayoutEdge {
  return { id: `${source}->${target}`, source, target }
}

describe('getDagreLayout', () => {
  it('returns an empty array for empty input', () => {
    expect(getDagreLayout([], [], 'TB')).toEqual([])
  })

  it('positions a single node and assigns TB handle positions', () => {
    const out = getDagreLayout([makeNode('a')], [], 'TB')
    expect(out).toHaveLength(1)
    expect(out[0].position).toEqual(
      expect.objectContaining({
        x: expect.any(Number),
        y: expect.any(Number),
      }),
    )
    expect(out[0].sourcePosition).toBe('bottom')
    expect(out[0].targetPosition).toBe('top')
  })

  it('positions child below parent in TB direction', () => {
    const out = getDagreLayout(
      [makeNode('p'), makeNode('c')],
      [makeEdge('p', 'c')],
      'TB',
    )
    const p = out.find((n) => n.id === 'p')!
    const c = out.find((n) => n.id === 'c')!
    expect(c.position.y).toBeGreaterThan(p.position.y)
  })

  it('positions child to the right of parent in LR direction', () => {
    const out = getDagreLayout(
      [makeNode('p'), makeNode('c')],
      [makeEdge('p', 'c')],
      'LR',
    )
    const p = out.find((n) => n.id === 'p')!
    const c = out.find((n) => n.id === 'c')!
    expect(c.position.x).toBeGreaterThan(p.position.x)
    expect(c.sourcePosition).toBe('right')
    expect(c.targetPosition).toBe('left')
  })

  it('flips source/target positions when direction changes', () => {
    const tb = getDagreLayout([makeNode('a')], [], 'TB')
    const lr = getDagreLayout([makeNode('a')], [], 'LR')
    expect(tb[0].sourcePosition).toBe('bottom')
    expect(lr[0].sourcePosition).toBe('right')
  })

  it('uses the hardcoded card dimensions for layout', () => {
    expect(NODE_WIDTH).toBe(168)
    expect(NODE_HEIGHT).toBe(52)
  })

  it('preserves the original node data and type', () => {
    const out = getDagreLayout([makeNode('a')], [], 'TB')
    expect(out[0].data).toEqual({ label: 'a' })
    expect(out[0].type).toBe('orgUnit')
  })
})
```

- [ ] **Step 3: Run the tests**

```
cd frontend/app && npx vitest run tests/components/use-dagre-layout.test.ts
```
Expected: PASS, all 7 tests.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/dashboard/org-units/use-dagre-layout.ts frontend/app/tests/components/use-dagre-layout.test.ts
git commit -m "refactor(org-graph): use local types in use-dagre-layout"
```

---

## Task 9: Build OrgGraphCanvas (the viewport)

**Files:**
- Create: `frontend/app/components/dashboard/org-units/OrgGraphCanvas.tsx`

The canvas owns:
1. The wrapper div (fills its positioned parent via `position: absolute; inset: 0`).
2. The dot-grid background (CSS `radial-gradient` painted on the wrapper, fixed-relative-to-wrapper — does not pan or scale, matching xyflow's `<Background>` default behaviour for our purposes).
3. The transformed inner viewport (`translate + scale`).
4. The SVG edge layer inside the viewport (so edges scale with nodes).
5. The absolutely-positioned node layer inside the viewport.
6. The bottom-right `<OrgGraphControls>` cluster (outside the viewport, fixed position).

It accepts already-positioned nodes and a click/dblclick/contextmenu callback per node.

- [ ] **Step 1: Write the file**

```tsx
// frontend/app/components/dashboard/org-units/OrgGraphCanvas.tsx
'use client'

import {
  useRef,
  type CSSProperties,
  type MouseEvent,
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
  /** Set of unit ids on the selection ancestry chain — colours edges. */
  selectedPath: Set<string>
  /** Bumped to trigger a fit-view animation. */
  fitRunId: unknown
  onNodeDoubleClick: (id: string) => void
  onNodeContextMenu: (id: string, e: MouseEvent<HTMLDivElement>) => void
  /** Optional overlay layer rendered above everything — radial menu /
   *  inline create live here so the consumer can position them. */
  overlay?: ReactNode
}

export function OrgGraphCanvas({
  nodes,
  edges,
  selectedPath,
  fitRunId,
  onNodeDoubleClick,
  onNodeContextMenu,
  overlay,
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
        // Dot grid background — the same look as xyflow's <Background>
        // with size=1 gap=22, resolved in our token system.
        backgroundColor: 'transparent',
        backgroundImage:
          'radial-gradient(circle, var(--px-fg-4) 1px, transparent 1px)',
        backgroundSize: '22px 22px',
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
          transform: `translate(${pz.tx}px, ${pz.ty}px) scale(${pz.scale})`,
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
              onNodeContextMenu(n.id, e)
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
```

- [ ] **Step 2: Verify type-check**

```
cd frontend/app && npx tsc --noEmit
```
Expected: errors only in `OrgGraph.tsx` (still importing xyflow). Move on to Task 10.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/org-units/OrgGraphCanvas.tsx
git commit -m "feat(org-graph): add OrgGraphCanvas viewport with svg edges + node layer"
```

---

## Task 10: Rewire OrgGraph.tsx through OrgGraphCanvas

**Files:**
- Modify: `frontend/app/components/dashboard/org-units/OrgGraph.tsx`

Drop every xyflow import and the `<style>` block that suppressed the default selected outline. Keep:
- All exported types: `Pressure`, `GraphNodeData`, `OrgGraphProps`.
- `OrgLegend` export, unchanged.
- The orphan-warning effect.
- The `onSelectPath` ancestry walker.
- The overlay state machine + `OrgUnitContextMenu` + `OrgUnitInlineCreate` rendering.
- The direction toggle UI, but moved into a `<div>` positioned `top-right` inside the canvas.
- The `wrapperRef.current?.querySelector('[data-id="${id}"]')` lookup for the keyboard-triggered radial menu — `OrgGraphCanvas` writes `data-id` on every node wrapper, so this still works.

`OrgGraph` becomes synchronous (no Provider needed). Replace the `useReactFlow().fitView` call with a `fitRunId` state that bumps on direction change.

- [ ] **Step 1: Replace the file contents**

```tsx
// frontend/app/components/dashboard/org-units/OrgGraph.tsx
'use client'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
} from 'react'

import type { OrgUnit } from '@/lib/api/org-units'

import { OrgGraphCanvas, type OrgGraphCanvasNodeData } from './OrgGraphCanvas'
import { OrgUnitContextMenu } from './OrgUnitContextMenu'
import { OrgUnitInlineCreate } from './OrgUnitInlineCreate'
import { getAllowedChildTypes } from './unit-children-rules'
import { UNIT_TYPE_STYLE, type UnitType } from './unit-type-style'
import { useDagreLayout } from './use-dagre-layout'
import { useDirectionToggle } from './use-direction-toggle'
import type { LayoutEdge, LayoutNode } from './types'

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
  /** Fired when the user double-clicks a card. Typically wired to a
   *  `router.push` to the unit's detail page. */
  onOpen?: (id: string) => void
  /** Fired when the user picks Delete in the right-click menu. */
  onDelete?: (id: string) => void
  /** Fired when the user submits the inline create form. */
  onCreateChild?: (
    parentId: string,
    unitType: UnitType,
    name: string,
  ) => Promise<void>
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  hoverId?: string | null
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  onHover?: (id: string | null) => void
}

export function OrgGraph({
  units,
  selectedId,
  onSelect,
  onOpen,
  onDelete,
  onCreateChild,
}: OrgGraphProps) {
  const [direction, setDirection] = useDirectionToggle()

  type Overlay =
    | { kind: 'menu'; unit: GraphNodeData; x: number; y: number }
    | { kind: 'create'; unit: GraphNodeData; childType: UnitType; x: number; y: number }
    | null
  const [overlay, setOverlay] = useState<Overlay>(null)
  const [createPending, setCreatePending] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)

  // Bumped to trigger a fit-view: on first mount and on direction flip.
  const [fitRunId, setFitRunId] = useState(0)
  useEffect(() => {
    setFitRunId((n) => n + 1)
  }, [direction])

  // Translate a viewport-coords MouseEvent into wrapper-local coords.
  const toWrapperCoords = useCallback(
    (e: { clientX: number; clientY: number }) => {
      const rect = wrapperRef.current?.getBoundingClientRect()
      if (!rect) return { x: 0, y: 0 }
      return { x: e.clientX - rect.left, y: e.clientY - rect.top }
    },
    [],
  )

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

  const onCardContextMenu = useCallback(
    (id: string) => {
      const unit = units.find((u) => u.id === id)
      if (!unit) return
      // Anchor the menu at the card's bounding-box centre in wrapper-local
      // coords. We write `data-id` on every node wrapper inside
      // OrgGraphCanvas, so this lookup keeps working.
      const cardEl = wrapperRef.current?.querySelector<HTMLElement>(
        `[data-id="${id}"]`,
      )
      const wrapperRect = wrapperRef.current?.getBoundingClientRect()
      if (!cardEl || !wrapperRect) return
      const cardRect = cardEl.getBoundingClientRect()
      const x = cardRect.left + cardRect.width / 2 - wrapperRect.left
      const y = cardRect.top + cardRect.height / 2 - wrapperRect.top
      onSelect(unit.id)
      setOverlay({ kind: 'menu', unit, x, y })
      setCreateError(null)
    },
    [units, onSelect],
  )

  const rawNodes = useMemo<LayoutNode<OrgGraphCanvasNodeData>[]>(
    () =>
      units.map((u) => ({
        id: u.id,
        type: 'orgUnit',
        // dagre overwrites this in useDagreLayout.
        position: { x: 0, y: 0 },
        data: {
          unit: u,
          selectedId,
          onSelectPath,
          onSelect,
          onContextMenu: onCardContextMenu,
        },
      })),
    [units, selectedId, onSelectPath, onSelect, onCardContextMenu],
  )

  const rawEdges = useMemo<LayoutEdge[]>(
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
        })),
    [units],
  )

  const positionedNodes = useDagreLayout(rawNodes, rawEdges, direction)

  const onNodeContextMenu = useCallback(
    (id: string, e: MouseEvent<HTMLDivElement>) => {
      const unit = units.find((u) => u.id === id)
      if (!unit) return
      onSelect(unit.id)
      const { x, y } = toWrapperCoords(e)
      setOverlay({ kind: 'menu', unit, x, y })
      setCreateError(null)
    },
    [units, onSelect, toWrapperCoords],
  )

  return (
    <div ref={wrapperRef} style={{ position: 'absolute', inset: 0 }}>
      <OrgGraphCanvas
        nodes={positionedNodes}
        edges={rawEdges}
        selectedPath={onSelectPath}
        fitRunId={fitRunId}
        onNodeDoubleClick={(id) => onOpen?.(id)}
        onNodeContextMenu={onNodeContextMenu}
        overlay={
          <>
            <DirectionToggle
              direction={direction}
              setDirection={setDirection}
            />
            {overlay?.kind === 'menu' && (
              <OrgUnitContextMenu
                target={{ unit: overlay.unit, x: overlay.x, y: overlay.y }}
                allowedChildTypes={getAllowedChildTypes(
                  overlay.unit.unit_type as UnitType,
                )}
                onClose={() => setOverlay(null)}
                onPickDelete={() => {
                  const id = overlay.unit.id
                  setOverlay(null)
                  onDelete?.(id)
                }}
                onPickChild={(type) => {
                  setOverlay({
                    kind: 'create',
                    unit: overlay.unit,
                    childType: type,
                    x: overlay.x,
                    y: overlay.y,
                  })
                  setCreateError(null)
                }}
              />
            )}
            {overlay?.kind === 'create' && (
              <OrgUnitInlineCreate
                unitType={overlay.childType}
                x={overlay.x}
                y={overlay.y}
                pending={createPending}
                error={createError}
                onCancel={() => {
                  setOverlay(null)
                  setCreateError(null)
                }}
                onSubmit={async (name) => {
                  if (!onCreateChild) {
                    setOverlay(null)
                    return
                  }
                  setCreatePending(true)
                  setCreateError(null)
                  try {
                    await onCreateChild(overlay.unit.id, overlay.childType, name)
                    setOverlay(null)
                  } catch (err) {
                    setCreateError(
                      err instanceof Error ? err.message : 'Failed to create unit',
                    )
                  } finally {
                    setCreatePending(false)
                  }
                }}
              />
            )}
          </>
        }
      />
    </div>
  )
}

// ─── Direction toggle (wraps over the canvas, top-right) ───────────────────

function DirectionToggle({
  direction,
  setDirection,
}: {
  direction: 'TB' | 'LR'
  setDirection: (d: 'TB' | 'LR') => void
}) {
  const overlayStyle: CSSProperties = {
    position: 'absolute',
    top: 12,
    right: 12,
    zIndex: 10,
  }
  return (
    <div data-no-pan style={overlayStyle}>
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
    </div>
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
```

- [ ] **Step 2: Update OrgGraph.test.tsx — remove the ResizeObserver polyfill block**

In `frontend/app/tests/components/OrgGraph.test.tsx`, replace the existing `beforeEach` (lines 47–59 in the current file) with:

```typescript
beforeEach(() => {
  window.localStorage.clear()
})
```

(The whole `if (!('ResizeObserver' in window))` block goes away — there is no xyflow to feed.)

- [ ] **Step 3: Run the full org-units test suite**

```
cd frontend/app && npx vitest run tests/components/OrgGraph.test.tsx tests/components/OrgUnitNode.test.tsx tests/components/OrgUnitContextMenu.test.tsx tests/components/OrgUnitInlineCreate.test.tsx tests/components/use-dagre-layout.test.ts tests/components/edge-path.test.ts tests/components/use-pan-zoom.test.ts
```
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/dashboard/org-units/OrgGraph.tsx frontend/app/tests/components/OrgGraph.test.tsx
git commit -m "refactor(org-graph): rewire OrgGraph through OrgGraphCanvas (drop xyflow)"
```

---

## Task 11: Drop the xyflow polyfills from tests/setup.ts

**Files:**
- Modify: `frontend/app/tests/setup.ts`

The `FakeResizeObserver` and `FakeDOMMatrixReadOnly` polyfills exist purely for xyflow. Once xyflow is gone, they can go too. Keep `StoragePolyfill`.

- [ ] **Step 1: Replace the file contents**

```typescript
// frontend/app/tests/setup.ts
import '@testing-library/jest-dom/vitest'

/**
 * In-memory `Storage` polyfill for the test environment.
 *
 * On Node 25 + Vitest 4, Node's `--experimental-webstorage` flag installs
 * a no-op `localStorage`/`sessionStorage` stub that shadows jsdom's
 * implementation. The result: `window.localStorage` is an empty object
 * with no `getItem`/`setItem`/`clear` methods, breaking any test that
 * exercises Storage-backed code.
 *
 * This polyfill replaces both storages with a class-backed in-memory
 * implementation. Exposing `globalThis.Storage = StoragePolyfill` lets
 * tests monkey-patch `Storage.prototype.setItem` to simulate quota
 * errors, since both instances inherit from the same prototype.
 */
class StoragePolyfill {
  private store: Map<string, string> = new Map()

  get length(): number {
    return this.store.size
  }

  clear(): void {
    this.store.clear()
  }

  getItem(key: string): string | null {
    return this.store.get(key) ?? null
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.store.delete(key)
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value))
  }
}

if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'localStorage', {
    value: new StoragePolyfill(),
    writable: true,
    configurable: true,
  })
  Object.defineProperty(window, 'sessionStorage', {
    value: new StoragePolyfill(),
    writable: true,
    configurable: true,
  })
}

;(globalThis as unknown as { Storage: typeof StoragePolyfill }).Storage =
  StoragePolyfill
```

- [ ] **Step 2: Run the FULL Vitest suite to confirm no other test silently depended on the polyfills**

```
cd frontend/app && npm test
```
Expected: all PASS. If any test fails because something else (e.g. `@dnd-kit`) needs `ResizeObserver` in jsdom, restore the `FakeResizeObserver` block but leave `FakeDOMMatrixReadOnly` removed. Document the reason in a comment above the restored block: `// Required by @dnd-kit for the pipeline-flow tests.`

- [ ] **Step 3: Commit**

```bash
git add frontend/app/tests/setup.ts
git commit -m "test: drop xyflow-only ResizeObserver and DOMMatrixReadOnly polyfills"
```

---

## Task 12: Remove @xyflow/react from package.json and reinstall

**Files:**
- Modify: `frontend/app/package.json`

- [ ] **Step 1: Remove the dependency**

Edit `frontend/app/package.json` and delete this line from `dependencies`:

```
    "@xyflow/react": "^12.10.2",
```

- [ ] **Step 2: Reinstall**

```
cd frontend/app && npm install
```
Expected: `package-lock.json` updates; `node_modules/@xyflow/` is removed (both `react` and `system`).

- [ ] **Step 3: Verify no remaining xyflow imports anywhere**

```
cd frontend/app && grep -rn "@xyflow" --include="*.ts" --include="*.tsx" --include="*.json" . 2>/dev/null | grep -v node_modules | grep -v '\.next' | grep -v 'package-lock.json'
```
Expected: zero results.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/package.json frontend/app/package-lock.json
git commit -m "chore: drop @xyflow/react (and transitive @xyflow/system) dependency"
```

---

## Task 13: Final verification — typecheck, lint, tests, dev-server smoke

Each substep is a checkpoint; do not collapse them.

- [ ] **Step 1: Typecheck**

```
cd frontend/app && npx tsc --noEmit
```
Expected: zero errors.

- [ ] **Step 2: Lint**

```
cd frontend/app && npm run lint
```
Expected: zero errors. (Warnings are tolerable if pre-existing.)

- [ ] **Step 3: Full test suite**

```
cd frontend/app && npm test
```
Expected: every test passes.

- [ ] **Step 4: Production build**

```
cd frontend/app && npm run build
```
Expected: build succeeds.

- [ ] **Step 5: Manual dev-server smoke (the only thing automated tests can't catch)**

```
cd frontend/app && npm run dev
```
Open `http://localhost:3000/settings/org-units` (sign in if needed) and verify, in this exact order:

1. **Cards render and are positioned** — multiple unit cards visible in a tree.
2. **Single-click selects** — clicking a card highlights it (accent border + glow), and the detail panel below updates.
3. **Selection ancestry** — ancestors of the selected card show the on-path styling.
4. **Double-click opens detail** — double-clicking navigates to `/settings/org-units/{id}`. Use browser back to return.
5. **Right-click opens the radial menu** — at the card's centre.
6. **Shift+F10 / ContextMenu key** — focus a card with Tab, press Shift+F10 — same radial menu appears.
7. **Inline create** — pick "Add Team", type a name, press Enter; toast fires and the new card appears.
8. **Wheel zoom** — scroll over the canvas; canvas zooms toward the cursor (the point under the cursor stays put). Zoom clamps at min/max.
9. **Drag-to-pan** — click-and-drag from a blank area; canvas pans. Click-and-drag starting on a card does NOT pan (it selects on release).
10. **Direction toggle** — click "Left → Right"; layout flips and animates a fit-view re-centre.
11. **Zoom controls cluster (bottom-right)** — `+`, `-`, fit-view buttons all work.
12. **Dot grid background** — visible behind the canvas.
13. **No xyflow attribution badge** — the "React Flow" link in the bottom-left from the old build must be gone.

If anything in 1–13 is broken, stop and debug before declaring the refactor complete.

- [ ] **Step 6: Final summary commit (only if any squash/cleanup is needed)**

If no squash needed, skip this step.

---

## Spec Coverage Check

| Spec requirement | Implemented in |
|---|---|
| Drop `@xyflow/react` from package.json | Task 12 |
| Drop `@xyflow/system` from package.json | Task 12 (transitive — disappears from `node_modules` after reinstall; not in our `package.json` to begin with) |
| Keep `@dagrejs/dagre` | Untouched throughout |
| `<OrgUnitNode>` becomes a regular React component | Task 7 |
| Preserve card structure / role / aria / data-state / focus ring / keyboard handler | Task 7 (verbatim copy of cardStyle + handlers) |
| `OrgUnitContextMenu` / `OrgUnitInlineCreate` / `unit-type-style` / `unit-children-rules` / `use-direction-toggle` preserved | Listed under "Files NOT to modify" |
| `<OrgUnitEdge>` rewrite to draw an SVG `<path>` with same selected-path styling | Task 6 |
| `use-dagre-layout.ts` keeps dagre, drops xyflow `Position` type — local enum | Task 8 |
| All composition tests in `OrgGraph.test.tsx` and `OrgUnitNode.test.tsx` keep passing with minimal tweaks | Task 7 (rewrite) and Task 10 Step 2 (polyfill removal) |
| `page.tsx` contract identical — zero changes | Verified by reading current import surface; no task touches `page.tsx` |
| `<OrgGraphCanvas>` owns viewport `<div style={transform translate scale}>` + positioned-absolute node layer + full-canvas `<svg>` for edges | Task 9 |
| `usePanZoom` hook — wheel-to-zoom around cursor, drag-to-pan from background, min/max scale clamp; pan must NOT trigger when pointerdown on a node | Task 3 (data-node-card check inside `onPointerDown`) |
| `useFitView` hook — bbox of all nodes, animate transform via CSS transition; initial mount + direction change | Task 4 + Task 10 (`fitRunId` bumps) |
| Background — CSS dot grid via radial-gradient on the canvas wrapper | Task 9 (`backgroundImage: radial-gradient(...)`) |
| Controls — bottom-right cluster with zoom +/-, fit-view, using `--px-surface` / `--px-fg-2` styling | Task 5 |
| `data-id` attribute preserved on node wrappers so the keyboard-triggered radial menu lookup keeps working | Task 9 (`data-id={n.id}` on each node wrapper); Task 10 (lookup unchanged) |
| Polyfills only needed for xyflow removed from `tests/setup.ts` | Task 11 (with safety check in Step 2) |

