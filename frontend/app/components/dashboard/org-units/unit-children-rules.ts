import type { UnitType } from './unit-type-style'

/**
 * Stable display order for child-type items. The radial menu places
 * items in this order starting after Delete.
 */
const ALL_CHILD_TYPES: readonly UnitType[] = [
  'region',
  'division',
  'client_account',
  'team',
] as const

/**
 * Mirrors the backend nesting rules in
 * `app/modules/org_units/service.py::create_org_unit`:
 *   - Teams are leaves: no children allowed.
 *   - client_account cannot nest under another client_account.
 *   - company is never a child of anything (root-only). It is never
 *     part of the returned list.
 *
 * The backend re-validates on every create — this helper is a UX gate,
 * not a security boundary.
 */
export function getAllowedChildTypes(parent: UnitType): UnitType[] {
  if (parent === 'team') return []
  if (parent === 'client_account') {
    return ALL_CHILD_TYPES.filter((t) => t !== 'client_account')
  }
  // company / region / division — all four are allowed.
  return [...ALL_CHILD_TYPES]
}
