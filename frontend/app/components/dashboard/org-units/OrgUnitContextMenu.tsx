'use client'

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from 'react'
import { Trash2, type LucideIcon } from 'lucide-react'

import type { GraphNodeData } from './OrgGraph'
import { UNIT_TYPE_STYLE, type UnitType } from './unit-type-style'

const RADIUS = 110
const PILL_HEIGHT = 36

// NOTE: The spec called for staggered scale-in animations on pills and a
// scaleX draw-in on spokes (see design §8). Implementation deferred:
// xyflow's inline `transform: translate(...)` on each pill blocks a
// CSS-keyframe `transform: ... scale(...)` from composing without
// rewriting the positioning to use CSS variables. The menu currently
// appears instantly. Revisit if motion polish becomes a priority.

const CHILD_LABEL: Record<UnitType, string> = {
  company: 'Company',
  client_account: 'Client account',
  region: 'Region',
  division: 'Division',
  team: 'Team',
}

export interface ContextMenuTarget {
  unit: GraphNodeData
  /** Pivot in canvas-local coordinates. */
  x: number
  y: number
}

interface Props {
  target: ContextMenuTarget
  allowedChildTypes: readonly UnitType[]
  onClose: () => void
  onPickDelete: () => void
  onPickChild: (type: UnitType) => void
}

interface Item {
  key: string
  label: string
  ariaLabel: string
  icon: LucideIcon
  iconColor: string
  iconBg: string
  iconLine: string
  isDanger: boolean
  onPick: () => void
}

export function OrgUnitContextMenu({
  target,
  allowedChildTypes,
  onClose,
  onPickDelete,
  onPickChild,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [activeIdx, setActiveIdx] = useState(0)

  const items: Item[] = useMemo(() => {
    const out: Item[] = []
    const isDeletable = !target.unit.admin_delete_disabled
    if (isDeletable) {
      out.push({
        key: 'delete',
        label: 'Delete',
        ariaLabel: `Delete ${target.unit.name}`,
        icon: Trash2,
        iconColor: 'var(--color-red-700)',
        iconBg: 'var(--color-red-50)',
        iconLine: 'var(--color-red-200)',
        isDanger: true,
        onPick: onPickDelete,
      })
    }
    for (const type of allowedChildTypes) {
      const style = UNIT_TYPE_STYLE[type]
      out.push({
        key: type,
        label: CHILD_LABEL[type],
        ariaLabel: `Add ${CHILD_LABEL[type]}`,
        icon: style.icon,
        iconColor: style.stripVar,
        iconBg: style.bgVar,
        iconLine: style.lineVar,
        isDanger: false,
        onPick: () => onPickChild(type),
      })
    }
    return out
  }, [allowedChildTypes, target.unit, onPickDelete, onPickChild])

  // Compute placements: angle 0 = top (12 o'clock), clockwise.
  // dx = R sin θ, dy = -R cos θ.
  const placements = useMemo(
    () =>
      items.map((item, i) => {
        const angleDeg = items.length === 0 ? 0 : (i * 360) / items.length
        const rad = (angleDeg * Math.PI) / 180
        const dx = RADIUS * Math.sin(rad)
        const dy = -RADIUS * Math.cos(rad)
        return { ...item, angleDeg, dx, dy }
      }),
    [items],
  )

  // Capture the element that had focus before we steal it for the
  // first menu item. Used to restore focus on Escape (a11y).
  const originElRef = useRef<HTMLElement | null>(null)

  // Focus first item on mount.
  useEffect(() => {
    originElRef.current = (document.activeElement as HTMLElement) ?? null
    const first = ref.current?.querySelector<HTMLElement>('[role="menuitem"]')
    first?.focus()
  }, [])

  // Click-outside closes the menu. Listen on document; the inner
  // click handlers stop propagation so this only fires for outside.
  useEffect(() => {
    function onDocPointer(e: MouseEvent) {
      if (!ref.current) return
      if (ref.current.contains(e.target as Node)) return
      onClose()
    }
    document.addEventListener('mousedown', onDocPointer)
    return () => document.removeEventListener('mousedown', onDocPointer)
  }, [onClose])

  function focusItem(i: number) {
    const list = ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]')
    list?.[i]?.focus()
    setActiveIdx(i)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      const origin = originElRef.current
      onClose()
      // Restore focus AFTER unmount so the body doesn't briefly own it.
      origin?.focus()
      return
    }
    if (items.length === 0) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      focusItem((activeIdx + 1) % items.length)
      return
    }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      focusItem((activeIdx - 1 + items.length) % items.length)
      return
    }
  }

  const rootStyle: CSSProperties = {
    position: 'absolute',
    left: target.x,
    top: target.y,
    width: 0,
    height: 0,
    pointerEvents: 'auto',
    zIndex: 50,
  }

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Actions for ${target.unit.name}`}
      onKeyDown={handleKeyDown}
      onContextMenu={(e) => e.preventDefault()}
      style={rootStyle}
    >
      {/* Pivot dot */}
      <span
        aria-hidden="true"
        className="pointer-events-none rmenu-pivot"
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: 8,
          height: 8,
          background: 'var(--px-accent)',
          borderRadius: '50%',
          transform: 'translate(-50%, -50%)',
          boxShadow: '0 0 0 6px var(--px-accent-tint)',
        }}
      />

      {/* Spokes */}
      {placements.map((p) => (
        <span
          key={`spoke-${p.key}`}
          aria-hidden="true"
          className="rmenu-spoke"
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: RADIUS,
            height: 1.4,
            background: 'var(--px-accent)',
            opacity: 0.55,
            transform: `rotate(${p.angleDeg - 90}deg)`,
            transformOrigin: '0 50%',
          }}
        />
      ))}

      {/* Pills */}
      {placements.map((p, i) => {
        const Icon = p.icon
        return (
          <button
            key={p.key}
            type="button"
            role="menuitem"
            tabIndex={i === activeIdx ? 0 : -1}
            aria-label={p.ariaLabel}
            data-angle={p.angleDeg}
            data-key={p.key}
            onClick={(e) => {
              e.stopPropagation()
              p.onPick()
            }}
            className="rmenu-pill"
            style={{
              position: 'absolute',
              left: p.dx,
              top: p.dy,
              transform: 'translate(-50%, -50%)',
              height: PILL_HEIGHT,
              borderRadius: 999,
              padding: '0 14px 0 10px',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12,
              fontWeight: 600,
              color: p.isDanger ? 'var(--color-red-700)' : 'var(--px-fg)',
              background: 'var(--px-surface)',
              border: `1px solid ${p.isDanger ? 'var(--color-red-200)' : 'var(--px-hairline-strong)'}`,
              boxShadow:
                '0 8px 24px rgba(58, 45, 28, 0.08), 0 2px 4px rgba(58, 45, 28, 0.04)',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              animationDelay: `${i * 30}ms`,
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 22,
                height: 22,
                borderRadius: 999,
                background: p.iconBg,
                border: `1px solid ${p.iconLine}`,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flex: 'none',
              }}
            >
              <Icon
                size={12}
                color={p.iconColor}
                strokeWidth={2.4}
                aria-hidden
              />
            </span>
            {p.label}
          </button>
        )
      })}
    </div>
  )
}
