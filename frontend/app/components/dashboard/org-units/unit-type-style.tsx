import type { ReactNode } from 'react'

export type UnitType =
  | 'company'
  | 'client_account'
  | 'region'
  | 'division'
  | 'team'

export type GlyphKind =
  | 'square'
  | 'diamond'
  | 'hexagon'
  | 'pill'
  | 'circle'

export interface UnitTypeStyle {
  /** CSS var for the 4px side strip + glyph fill. */
  stripVar: string
  /** CSS var for the glyph plate background. */
  bgVar: string
  /** CSS var for the glyph plate border. */
  lineVar: string
  /** Shape rendered inside the glyph plate. */
  glyph: GlyphKind
}

/**
 * Per-type visual mapping. All values resolve to existing tokens —
 * Tailwind ramps are remapped to the warm-light palette via @theme in
 * globals.css, so these vars stay consistent with the rest of the
 * dashboard.
 */
export const UNIT_TYPE_STYLE: Record<UnitType, UnitTypeStyle> = {
  company: {
    stripVar: 'var(--color-blue-600)',
    bgVar: 'var(--color-blue-50)',
    lineVar: 'var(--color-blue-200)',
    glyph: 'square',
  },
  client_account: {
    stripVar: 'var(--color-purple-600)',
    bgVar: 'var(--color-purple-50)',
    lineVar: 'var(--color-purple-100)',
    glyph: 'diamond',
  },
  region: {
    stripVar: 'var(--color-amber-700)',
    bgVar: 'var(--color-amber-50)',
    lineVar: 'var(--color-amber-100)',
    glyph: 'hexagon',
  },
  division: {
    stripVar: 'var(--px-accent-2)',
    bgVar: 'var(--color-green-50)',
    lineVar: 'var(--color-green-100)',
    glyph: 'pill',
  },
  team: {
    stripVar: 'var(--px-fg)',
    bgVar: 'var(--px-bg-2)',
    lineVar: 'var(--px-hairline-strong)',
    glyph: 'circle',
  },
} as const

const FALLBACK_STYLE: UnitTypeStyle = UNIT_TYPE_STYLE.team

const _warnedTypes = new Set<string>()

/**
 * Look up the style for an org unit type. If the backend ever ships a
 * sixth type before the frontend updates this map, fall back to the
 * team style and emit a single console warning per unknown type so the
 * bug surfaces without spamming the log.
 */
export function getUnitTypeStyle(type: string): UnitTypeStyle {
  if (type in UNIT_TYPE_STYLE) {
    return UNIT_TYPE_STYLE[type as UnitType]
  }
  if (!_warnedTypes.has(type)) {
    _warnedTypes.add(type)
    console.warn(
      `OrgGraph: unknown unit_type "${type}", falling back to team style`,
    )
  }
  return FALLBACK_STYLE
}

/**
 * Renders a small SVG shape filled with `color`. Used both inside the
 * node card (color = strip color, large glyph plate) and inside the
 * legend (color = strip color, small inline icon).
 */
export function Glyph({
  kind,
  color,
  size = 14,
}: {
  kind: GlyphKind
  color: string
  size?: number
}): ReactNode {
  const half = size / 2
  const viewBox = `-${half} -${half} ${size} ${size}`

  switch (kind) {
    case 'square':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <rect
            x={-half + 2}
            y={-half + 2.5}
            width={size - 4}
            height={size - 5}
            rx={2}
            fill={color}
          />
        </svg>
      )
    case 'diamond':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <polygon
            points={`0,${-half + 2} ${half - 2},0 0,${half - 2} ${-half + 2},0`}
            fill={color}
          />
        </svg>
      )
    case 'hexagon': {
      const r = half - 2
      const h = r * Math.sin(Math.PI / 3)
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <polygon
            points={`-${r},0 -${r / 2},${-h} ${r / 2},${-h} ${r},0 ${r / 2},${h} -${r / 2},${h}`}
            fill={color}
          />
        </svg>
      )
    }
    case 'pill':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <rect
            x={-half + 1.5}
            y={-2.5}
            width={size - 3}
            height={5}
            rx={2.5}
            fill={color}
          />
        </svg>
      )
    case 'circle':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <circle r={half - 2.5} fill={color} />
        </svg>
      )
    default: {
      const _exhaustive: never = kind
      return _exhaustive
    }
  }
}
