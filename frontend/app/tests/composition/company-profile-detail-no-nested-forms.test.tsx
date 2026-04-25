import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'

// Mock at the API namespace module boundary so the real hooks (and
// their query-key shapes) get exercised — but without any network.
//
// The MembersSection subtree calls:
//   - useOrgUnitMembers -> orgUnitsApi.listMembers
//   - useRoles          -> orgUnitsApi.listRoles
//   - useTeamMembers    -> teamApi.list
// CompanyProfileDetail itself only wires useUpdateOrgUnit, which is a
// mutation and does not fire on mount.

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(async () => 'test-token'),
}))

vi.mock('@/lib/api/team', () => ({
  teamApi: {
    list: vi.fn(async () => [
      {
        id: 'u1',
        email: 'alice@example.com',
        full_name: 'Alice',
        is_active: true,
        is_super_admin: false,
        assignments: [],
        source: 'user',
        status: 'active',
        created_at: '2026-04-01T00:00:00Z',
      },
    ]),
  },
}))

vi.mock('@/lib/api/org-units', async () => {
  const actual = await vi.importActual<
    typeof import('@/lib/api/org-units')
  >('@/lib/api/org-units')
  return {
    ...actual,
    orgUnitsApi: {
      ...actual.orgUnitsApi,
      listMembers: vi.fn(async () => []),
      listRoles: vi.fn(async () => [
        {
          id: 'r1',
          name: 'Recruiter',
          description: '',
          permissions: [],
          is_system: true,
        },
      ]),
    },
  }
})

// Import the component AFTER mocks are declared.
import { CompanyProfileDetail } from '@/app/(dashboard)/settings/org-units/[unitId]/CompanyProfileDetail'
import type { OrgUnit } from '@/lib/api/org-units'

const MIN_UNIT: OrgUnit = {
  id: 'u-1',
  client_id: 'client-1',
  parent_unit_id: null,
  name: 'TestCo',
  unit_type: 'company',
  member_count: 0,
  created_at: '2026-04-01T00:00:00Z',
  created_by: null,
  created_by_email: null,
  deletable_by: null,
  deletable_by_email: null,
  admin_delete_disabled: false,
  is_accessible: true,
  admin_emails: [],
  is_root: true,
  company_profile: {
    about:
      'We build distributed log processing for real-time analytics at petabyte scale.',
    industry: 'saas_enterprise_software',
    company_stage: 'series_a_b',
    hiring_bar:
      'Pragmatic engineers comfortable with ambiguity and operational ownership.',
  },
  company_profile_completed_at: '2026-04-01T00:00:00Z',
  metadata: null,
}

describe('CompanyProfileDetail composition', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('has no nested <form> elements when rendered with MembersSection', async () => {
    const { container } = renderWithProviders(
      <CompanyProfileDetail
        unit={MIN_UNIT}
        subUnits={[]}
        onBack={vi.fn()}
        onSaved={vi.fn()}
        openRolesCount={0}
      />,
    )

    // Wait for the embedded MembersSection's queries to settle —
    // its Section title is our load-bearing signal that the subtree
    // mounted and rendered.
    await waitFor(() => {
      expect(screen.getByText(/Members & Roles/i)).toBeInTheDocument()
    })

    // Open the assign-role form so the inner <form> is actually in the DOM —
    // otherwise a future regression that wraps the section in a parent <form>
    // would go undetected (because no inner <form> exists to nest).
    const toggle = screen.getByRole('button', { name: /\+ Assign role/i })
    toggle.click()

    await waitFor(() => {
      // The assign-role <form> now exists in the DOM.
      expect(container.querySelector('form')).not.toBeNull()
    })

    const forms = container.querySelectorAll('form')
    for (const form of forms) {
      expect(
        form.querySelector('form'),
        `Found a nested <form> inside another <form>: ${form.outerHTML.slice(0, 200)}…`,
      ).toBeNull()
    }
  })
})
