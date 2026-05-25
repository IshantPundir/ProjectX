'use client'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { Trash2, type LucideIcon } from 'lucide-react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'

import type { GraphNodeData } from './OrgGraph'
import { UNIT_TYPE_STYLE, type UnitType } from './unit-type-style'

const RADIUS = 110
const PILL_HEIGHT = 36
const HOVER_SCALE = 1.08
// Fan geometry. The menu opens from the card's right-edge midpoint, so
// items spread across the right semicircle. Centre = 90° = 3 o'clock,
// span = 180° → endpoints land at 0° (12 o'clock) and 180° (6 o'clock).
const FAN_CENTER_DEG = 90
const FAN_SPAN_DEG = 180

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
  /** Outside-click / Escape close requests. Parent flips `closing` to
   *  true in response to play the exit animation. */
  onClose: () => void
  onPickDelete: () => void
  onPickChild: (type: UnitType) => void
  /** When true, the menu plays its reverse-stagger exit animation and
   *  fires `onExitComplete` once finished. */
  closing: boolean
  /** Called when the exit animation finishes (or immediately under
   *  prefers-reduced-motion). Parent uses this to remove the menu from
   *  the tree and apply any queued follow-up overlay. */
  onExitComplete: () => void
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
  closing,
  onExitComplete,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [activeIdx, setActiveIdx] = useState(0)

  const items: Item[] = useMemo(() => {
    const out: Item[] = []
    // Root company units are never deletable (backend rejects the
    // request unconditionally — see `delete_org_unit` in
    // `backend/nexus/app/modules/org_units/service.py`). We mirror that
    // invariant in the UI so the user never sees a Delete affordance
    // they can't act on.
    const isDeletable =
      !target.unit.is_root && !target.unit.admin_delete_disabled
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
  // Items distribute across a `FAN_SPAN_DEG` arc centred on
  // `FAN_CENTER_DEG`. Each item sits in the centre of its slice (so for
  // N=1 it lands directly on the centre, not at an edge of the fan).
  const placements = useMemo(
    () =>
      items.map((item, i) => {
        const n = items.length
        const angleDeg =
          n === 0
            ? FAN_CENTER_DEG
            : FAN_CENTER_DEG -
              FAN_SPAN_DEG / 2 +
              ((i + 0.5) * FAN_SPAN_DEG) / n
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

  // Entrance animation: spokes draw out from the pivot via scaleX, then
  // pills pop in with a staggered back.out bounce. GSAP parses the
  // existing inline `transform` on each element and preserves the
  // rotation (spokes) / centering translate (pills) while only animating
  // scale. Skipped under prefers-reduced-motion — pills then start at
  // their final state because we never run gsap.set().
  useGSAP(
    () => {
      const root = ref.current
      if (!root) return
      if (
        typeof window !== 'undefined' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
      ) {
        return
      }
      const spokes = root.querySelectorAll<HTMLElement>('.rmenu-spoke')
      const pills = root.querySelectorAll<HTMLElement>('.rmenu-pill')

      gsap.set(spokes, { scaleX: 0 })
      gsap.set(pills, { scale: 0, opacity: 0 })

      const tl = gsap.timeline()
      tl.to(spokes, {
        scaleX: 1,
        duration: 0.3,
        ease: 'power2.out',
        stagger: 0.04,
      })
      tl.to(
        pills,
        {
          scale: 1,
          opacity: 1,
          duration: 0.42,
          ease: 'back.out(2.2)',
          stagger: 0.05,
        },
        '<0.06',
      )
    },
    { scope: ref },
  )

  // Exit animation: mirrors the entrance reversed. Pills retract first
  // (last-in-first-out), spokes follow. `overwrite: 'auto'` cleanly
  // takes over any in-flight entrance tween so closing mid-entrance
  // doesn't pop. Under prefers-reduced-motion we skip straight to
  // onExitComplete so the parent unmounts immediately.
  useEffect(() => {
    if (!closing) return
    const root = ref.current
    if (!root) {
      onExitComplete()
      return
    }
    if (
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      onExitComplete()
      return
    }
    const spokes = root.querySelectorAll<HTMLElement>('.rmenu-spoke')
    const pills = root.querySelectorAll<HTMLElement>('.rmenu-pill')

    const tl = gsap.timeline({ onComplete: onExitComplete })
    tl.to(pills, {
      scale: 0,
      opacity: 0,
      duration: 0.22,
      ease: 'back.in(1.7)',
      stagger: { each: 0.035, from: 'end' },
      overwrite: 'auto',
    })
    tl.to(
      spokes,
      {
        scaleX: 0,
        duration: 0.18,
        ease: 'power2.in',
        stagger: { each: 0.03, from: 'end' },
        overwrite: 'auto',
      },
      '<0.05',
    )

    return () => {
      tl.kill()
    }
  }, [closing, onExitComplete])

  // Hover lift: scale the pill up slightly on pointer enter, back on
  // leave. `overwrite: 'auto'` keeps rapid mouse movement smooth and
  // gracefully takes over from the entrance tween if the user hovers
  // mid-animation.
  const handlePillEnter = useCallback(
    (e: ReactPointerEvent<HTMLButtonElement>) => {
      gsap.to(e.currentTarget, {
        scale: HOVER_SCALE,
        duration: 0.16,
        ease: 'power2.out',
        overwrite: 'auto',
      })
    },
    [],
  )
  const handlePillLeave = useCallback(
    (e: ReactPointerEvent<HTMLButtonElement>) => {
      gsap.to(e.currentTarget, {
        scale: 1,
        duration: 0.22,
        ease: 'power2.out',
        overwrite: 'auto',
      })
    },
    [],
  )

  // Focus first item on mount.
  useEffect(() => {
    originElRef.current = (document.activeElement as HTMLElement) ?? null
    const first = ref.current?.querySelector<HTMLElement>('[role="menuitem"]')
    first?.focus()
  }, [])

  // Click-outside closes the menu. Listen on document; the inner
  // click handlers stop propagation so this only fires for outside.
  // Skip while exiting — the menu is already on its way out.
  useEffect(() => {
    if (closing) return
    function onDocPointer(e: MouseEvent) {
      if (!ref.current) return
      if (ref.current.contains(e.target as Node)) return
      onClose()
    }
    document.addEventListener('mousedown', onDocPointer)
    return () => document.removeEventListener('mousedown', onDocPointer)
  }, [onClose, closing])

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
    // Disable interaction once the menu starts exiting so half-faded
    // pills aren't clickable.
    pointerEvents: closing ? 'none' : 'auto',
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
            onPointerEnter={handlePillEnter}
            onPointerLeave={handlePillLeave}
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
                '0 8px 24px rgba(20, 40, 60, 0.08), 0 2px 4px rgba(20, 40, 60, 0.04)',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              // Promote each pill to its own compositor layer. Combined
              // with the integer-rounded viewport translation, this keeps
              // text crisp during the entrance / exit / hover animations.
              willChange: 'transform',
              backfaceVisibility: 'hidden',
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
