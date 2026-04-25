import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'

const removeMutateAsync = vi.fn().mockResolvedValue(undefined)
const assignMutateAsync = vi.fn().mockResolvedValue(undefined)

vi.mock('@/lib/hooks/use-remove-role', () => ({
  useRemoveRole: () => ({
    mutateAsync: removeMutateAsync,
    isPending: false,
  }),
}))
vi.mock('@/lib/hooks/use-assign-role', () => ({
  useAssignRole: () => ({
    mutateAsync: assignMutateAsync,
    isPending: false,
  }),
}))
vi.mock('@/lib/hooks/use-roles', () => ({
  useRoles: () => ({
    data: [{ id: 'r1', name: 'Hiring Manager' }],
    isLoading: false,
  }),
}))
vi.mock('@/lib/hooks/use-org-unit-members', () => ({
  useOrgUnitMembers: () => ({
    data: [
      {
        user_id: 'u1',
        email: 'alice@example.com',
        full_name: 'Alice',
        roles: [
          {
            role_id: 'r1',
            role_name: 'Hiring Manager',
            assigned_at: '2026-04-01T00:00:00Z',
          },
        ],
      },
    ],
    isLoading: false,
  }),
}))
vi.mock('@/lib/hooks/use-team-members', () => ({
  useTeamMembers: () => ({ data: [], isLoading: false }),
}))

// Import after mocks.
import { MembersSection } from '@/app/(dashboard)/settings/org-units/[unitId]/MembersSection'

describe('MembersSection cancel path', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('clicking Cancel in the Remove-role dialog does NOT call the mutation', async () => {
    renderWithProviders(<MembersSection unitId="u-1" />)

    // Open the Remove-role dialog by clicking the × on the assigned role.
    const removeChip = await screen.findByRole('button', {
      name: /Remove Hiring Manager/,
    })
    fireEvent.click(removeChip)

    // Dialog should be open.
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /Remove role/i }),
      ).toBeInTheDocument()
    })

    // Click Cancel.
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    // After cancel, the mutation must not have been invoked.
    expect(removeMutateAsync).not.toHaveBeenCalled()

    // Dialog should close.
    await waitFor(() => {
      expect(
        screen.queryByRole('heading', { name: /Remove role/i }),
      ).not.toBeInTheDocument()
    })
  })
})
