'use client'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react'

import type { OrgUnit } from '@/lib/api/org-units'

import { OrgGraphCanvas, type OrgGraphCanvasNodeData } from './OrgGraphCanvas'
import { OrgUnitContextMenu } from './OrgUnitContextMenu'
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
  /** Fired when the user picks a child unit type in the right-click
   *  menu. The consumer is responsible for opening a create dialog and
   *  calling the create API. */
  onPickChild?: (parentId: string, childType: UnitType) => void
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
  onPickChild,
}: OrgGraphProps) {
  const [direction, setDirection] = useDirectionToggle()

  // The radial menu is rendered through OrgGraphCanvas's `worldOverlay`
  // slot anchored to the unit's right-edge midpoint in canvas-world
  // coords. The canvas applies a counter-scale so the menu stays at
  // native pixel sizes while still tracking the card through pan /
  // zoom / centring animations.
  //
  // The `closing` flag drives the menu's reverse-stagger exit. When
  // true, the menu plays the exit and fires `onExitComplete`; the
  // parent then runs any queued `pendingActionRef.current` (e.g. firing
  // `onPickChild` once the menu has visually retracted, so the create
  // dialog doesn't pop while the menu is still on screen).
  type Overlay = { unit: GraphNodeData; closing: boolean } | null
  const [overlay, setOverlay] = useState<Overlay>(null)
  const pendingActionRef = useRef<(() => void) | null>(null)

  // Pass `direction` itself as the fit-view runId — every direction
  // flip reference-changes it, which is exactly the signal the canvas's
  // useFitView watches for. Initial mount counts too, so the first fit
  // happens automatically.

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

  const openMenuFor = useCallback(
    (id: string) => {
      const unit = units.find((u) => u.id === id)
      if (!unit) return
      // Locked nodes (is_accessible=false) are ancestors-for-context only.
      // Suppress the radial menu so a non-admin can't try to add children
      // or delete — backend would 403 anyway, this avoids the confusion.
      if (!unit.is_accessible) {
        onSelect(unit.id)
        return
      }
      onSelect(unit.id)
      // Cancel any in-flight close — this is a fresh open.
      pendingActionRef.current = null
      setOverlay({ unit, closing: false })
    },
    [units, onSelect],
  )

  // Mark the menu as closing (plays the reverse stagger). Optionally
  // queue an action to fire once the exit completes — used by the
  // pick-child flow so the create dialog opens after the menu has
  // visually retracted, not while it's still on screen.
  const closeMenuAnimated = useCallback((next: (() => void) | null = null) => {
    setOverlay((current) => {
      if (!current || current.closing) return current
      pendingActionRef.current = next
      return { ...current, closing: true }
    })
  }, [])

  const handleMenuExitComplete = useCallback(() => {
    setOverlay(null)
    const action = pendingActionRef.current
    pendingActionRef.current = null
    action?.()
  }, [])

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
          onContextMenu: openMenuFor,
        },
      })),
    [units, selectedId, onSelectPath, onSelect, openMenuFor],
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
    (id: string) => {
      openMenuFor(id)
    },
    [openMenuFor],
  )

  return (
    <div style={{ position: 'absolute', inset: 0 }}>
      <OrgGraphCanvas
        nodes={positionedNodes}
        edges={rawEdges}
        selectedId={selectedId}
        selectedPath={onSelectPath}
        fitRunId={direction}
        onNodeDoubleClick={(id) => {
          const unit = units.find((u) => u.id === id)
          // Don't navigate into the detail page for locked nodes — the
          // detail route would just render the same locked stub.
          if (unit && !unit.is_accessible) return
          onOpen?.(id)
        }}
        onNodeContextMenu={onNodeContextMenu}
        overlay={
          <DirectionToggle
            direction={direction}
            setDirection={setDirection}
          />
        }
        worldAnchorId={overlay?.unit.id ?? null}
        worldOverlay={
          overlay && (
            <OrgUnitContextMenu
              // `key` forces a remount when switching to a different
              // unit's menu so the entrance animation plays again.
              key={overlay.unit.id}
              target={{ unit: overlay.unit, x: 0, y: 0 }}
              allowedChildTypes={getAllowedChildTypes(
                overlay.unit.unit_type as UnitType,
              )}
              closing={overlay.closing}
              onExitComplete={handleMenuExitComplete}
              onClose={() => closeMenuAnimated()}
              onPickDelete={() => {
                // Fire delete immediately (opens the confirm dialog)
                // while the menu plays its exit in parallel.
                onDelete?.(overlay.unit.id)
                closeMenuAnimated()
              }}
              onPickChild={(type) => {
                // Queue the create flow to fire after the menu has
                // visually retracted, so the dialog doesn't pop while
                // the menu is still on screen.
                const parentId = overlay.unit.id
                closeMenuAnimated(() => onPickChild?.(parentId, type))
              }}
            />
          )
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
