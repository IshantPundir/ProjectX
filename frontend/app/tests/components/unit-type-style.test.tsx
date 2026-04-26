import { describe, expect, it, vi } from 'vitest'
import { Building, Building2, Globe2, Network, Users } from 'lucide-react'

import {
  UNIT_TYPE_STYLE,
  getUnitTypeStyle,
  type UnitType,
} from '@/components/dashboard/org-units/unit-type-style'

describe('UNIT_TYPE_STYLE', () => {
  it('has an entry for each of the five unit types', () => {
    const expected: UnitType[] = [
      'company',
      'client_account',
      'region',
      'division',
      'team',
    ]
    for (const t of expected) {
      expect(UNIT_TYPE_STYLE[t]).toBeDefined()
      expect(UNIT_TYPE_STYLE[t].stripVar).toMatch(/^var\(--/)
      expect(UNIT_TYPE_STYLE[t].bgVar).toMatch(/^var\(--/)
      expect(UNIT_TYPE_STYLE[t].lineVar).toMatch(/^var\(--/)
      expect(typeof UNIT_TYPE_STYLE[t].icon).toBe('object')
    }
  })

  it('binds each type to its semantic lucide icon', () => {
    expect(UNIT_TYPE_STYLE.company.icon).toBe(Building2)
    expect(UNIT_TYPE_STYLE.client_account.icon).toBe(Building)
    expect(UNIT_TYPE_STYLE.region.icon).toBe(Globe2)
    expect(UNIT_TYPE_STYLE.division.icon).toBe(Network)
    expect(UNIT_TYPE_STYLE.team.icon).toBe(Users)
  })

  it('uses Building-family icons for both company-profile-bearing types', () => {
    // company and client_account share the company_profile concept;
    // their icons should cluster visually (both Building*) while the
    // variant + color split keeps them distinguishable.
    const profileBearingIcons = [
      UNIT_TYPE_STYLE.company.icon,
      UNIT_TYPE_STYLE.client_account.icon,
    ]
    expect(profileBearingIcons).toContain(Building)
    expect(profileBearingIcons).toContain(Building2)
    expect(UNIT_TYPE_STYLE.company.icon).not.toBe(
      UNIT_TYPE_STYLE.client_account.icon,
    )
  })
})

describe('getUnitTypeStyle', () => {
  it('returns the typed style for a known unit type', () => {
    expect(getUnitTypeStyle('region')).toBe(UNIT_TYPE_STYLE.region)
  })

  it('falls back to team style and warns once per unknown type', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // Use a distinct type name per test run so the module-level dedup
    // set doesn't suppress the warn from prior tests.
    const unknown = `not_a_real_type_${Math.random().toString(36).slice(2)}`
    const result1 = getUnitTypeStyle(unknown)
    const result2 = getUnitTypeStyle(unknown)
    expect(result1).toBe(UNIT_TYPE_STYLE.team)
    expect(result2).toBe(UNIT_TYPE_STYLE.team)
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('unknown unit_type'),
    )
    warn.mockRestore()
  })
})
