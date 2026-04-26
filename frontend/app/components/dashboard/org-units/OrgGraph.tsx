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
