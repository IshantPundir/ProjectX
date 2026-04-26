import { describe, expect, it } from 'vitest'

import { getAllowedChildTypes } from '@/components/dashboard/org-units/unit-children-rules'

describe('getAllowedChildTypes', () => {
  it('returns nothing for team (leaf node)', () => {
    expect(getAllowedChildTypes('team')).toEqual([])
  })

  it('forbids client_account under client_account but allows the others', () => {
    const out = getAllowedChildTypes('client_account')
    expect(out).toEqual(['region', 'division', 'team'])
    expect(out).not.toContain('client_account')
  })

  it('allows all four child types under company', () => {
    expect(getAllowedChildTypes('company')).toEqual([
      'region',
      'division',
      'client_account',
      'team',
    ])
  })

  it('allows all four child types under region', () => {
    expect(getAllowedChildTypes('region')).toEqual([
      'region',
      'division',
      'client_account',
      'team',
    ])
  })

  it('allows all four child types under division', () => {
    expect(getAllowedChildTypes('division')).toEqual([
      'region',
      'division',
      'client_account',
      'team',
    ])
  })
})
