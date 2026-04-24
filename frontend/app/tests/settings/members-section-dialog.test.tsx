import { describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { MembersSection } from '@/app/(dashboard)/settings/org-units/[unitId]/MembersSection'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: async () => 'stub-token',
}))

const listMembersMock = vi.fn(async () => [
  {
    user_id: 'u1',
    email: 'member@x.com',
    full_name: 'Member',
    roles: [{ role_id: 'r1', role_name: 'Recruiter', assigned_at: '2026-01-01' }],
  },
])
const listRolesMock = vi.fn(async () => [
  { id: 'r1', name: 'Recruiter', description: '', permissions: [], is_system: true },
])
const removeRoleMock = vi.fn(async () => ({ status: 'ok' }))
const listTenantUsersMock = vi.fn(async () => [])

vi.mock('@/lib/api/org-units', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/org-units')>(
    '@/lib/api/org-units',
  )
  return {
    ...actual,
    orgUnitsApi: {
      ...actual.orgUnitsApi,
      listMembers: () => listMembersMock(),
      listRoles: () => listRolesMock(),
      removeRole: (_t: string, _u: string, _uid: string, _rid: string) => removeRoleMock(),
    },
  }
})

vi.mock('@/lib/api/team', () => ({
  teamApi: {
    list: () => listTenantUsersMock(),
  },
}))

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('MembersSection (B4) — Dialog instead of window.confirm', () => {
  it('opens a Dialog when Remove is clicked (no window.confirm)', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    wrap(<MembersSection unitId="u1" />)

    await screen.findByText(/member@x.com/i)
    await userEvent.click(await screen.findByRole('button', { name: /remove/i }))

    expect(confirmSpy).not.toHaveBeenCalled()
    await screen.findByRole('dialog')

    confirmSpy.mockRestore()
  })

  it('calls removeRole after confirming in the Dialog', async () => {
    wrap(<MembersSection unitId="u1" />)

    await screen.findByText(/member@x.com/i)
    await userEvent.click(await screen.findByRole('button', { name: /^remove/i }))

    const confirmBtn = await screen.findByRole('button', { name: /remove role/i })
    await userEvent.click(confirmBtn)

    await waitFor(() => {
      expect(removeRoleMock).toHaveBeenCalled()
    })
  })
})
