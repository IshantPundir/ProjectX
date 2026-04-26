import {
  Building,
  Building2,
  Globe2,
  Network,
  Users,
  type LucideIcon,
} from 'lucide-react'

export type UnitType =
  | 'company'
  | 'client_account'
  | 'region'
  | 'division'
  | 'team'

export interface UnitTypeStyle {
  /** CSS var for the 4px side strip + icon stroke. */
  stripVar: string
  /** CSS var for the icon plate background. */
  bgVar: string
  /** CSS var for the icon plate border. */
  lineVar: string
  /** Lucide icon component rendered inside the plate. */
  icon: LucideIcon
}

/**
 * Per-type visual mapping. Colors resolve to existing tokens —
 * Tailwind ramps are remapped to the warm-light palette via @theme in
 * globals.css. Icons come from `lucide-react`, the project's standard
 * icon library.
 *
 * Shape rationale:
 *   - `company` and `client_account` both share the Building family
 *     because both carry a `company_profile`. The variant (Building2 vs
 *     Building) plus the color split (blue vs purple) keeps them
 *     distinguishable.
 *   - The other three are semantically literal: a globe for region, a
 *     network for an organizational division, a group of people for team.
 */
export const UNIT_TYPE_STYLE: Record<UnitType, UnitTypeStyle> = {
  company: {
    stripVar: 'var(--color-blue-600)',
    bgVar: 'var(--color-blue-50)',
    lineVar: 'var(--color-blue-200)',
    icon: Building2,
  },
  client_account: {
    stripVar: 'var(--color-purple-600)',
    bgVar: 'var(--color-purple-50)',
    lineVar: 'var(--color-purple-100)',
    icon: Building,
  },
  region: {
    stripVar: 'var(--color-amber-700)',
    bgVar: 'var(--color-amber-50)',
    lineVar: 'var(--color-amber-100)',
    icon: Globe2,
  },
  division: {
    stripVar: 'var(--px-accent-2)',
    bgVar: 'var(--color-green-50)',
    lineVar: 'var(--color-green-100)',
    icon: Network,
  },
  team: {
    stripVar: 'var(--px-fg)',
    bgVar: 'var(--px-bg-2)',
    lineVar: 'var(--px-hairline-strong)',
    icon: Users,
  },
}

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
