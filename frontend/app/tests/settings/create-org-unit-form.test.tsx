import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import OrgUnitsPage from '@/app/(dashboard)/settings/org-units/page'

const pushMock = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, refresh: vi.fn() }),
}))

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: async () => 'stub-token',
}))

const listUnitsMock = vi.fn()
const createMock = vi.fn()
vi.mock('@/lib/api/org-units', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/org-units')>(
    '@/lib/api/org-units',
  )
  return {
    ...actual,
    orgUnitsApi: {
      ...actual.orgUnitsApi,
      list: () => listUnitsMock(),
      create: (_t: string, body: unknown) => createMock(body),
    },
  }
})
vi.mock('@/lib/api/jobs', () => ({
  jobsApi: { list: async () => [] },
}))
vi.mock('@/lib/api/auth', () => ({
  authApi: {
    me: async () => ({
      user_id: 'u', email: 'a@x.com', full_name: null, tenant_id: 't',
      client_name: 'Acme', is_super_admin: true, onboarding_complete: true,
      has_org_units: true, workspace_mode: 'enterprise', assignments: [],
    }),
  },
}))

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('OrgUnitsPage create form', () => {
  beforeEach(() => {
    listUnitsMock.mockResolvedValue([
      {
        id: 'root-1', client_id: 't', parent_unit_id: null, name: 'Acme',
        unit_type: 'company', member_count: 0, created_at: '2026-01-01T00:00:00Z',
        created_by: null, created_by_email: null, deletable_by: null,
        deletable_by_email: null, admin_delete_disabled: false,
        is_accessible: true, admin_emails: [], is_root: true,
        company_profile: null, company_profile_completed_at: null, metadata: null,
      },
    ])
    createMock.mockReset()
  })

  it('blocks submit when name is empty', async () => {
    wrap(<OrgUnitsPage />)
    await screen.findByText(/org structure/i)

    await userEvent.click(await screen.findByRole('button', { name: /new unit/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^create unit$/i }))

    await waitFor(() => {
      expect(screen.getByText(/unit name is required/i)).toBeInTheDocument()
    })
    expect(createMock).not.toHaveBeenCalled()
  })

  it('submits a division with a valid name', async () => {
    createMock.mockResolvedValue({
      id: 'new-1', client_id: 't', parent_unit_id: null, name: 'Eng',
      unit_type: 'division', member_count: 0, created_at: '2026-01-01T00:00:00Z',
      created_by: null, created_by_email: null, deletable_by: null,
      deletable_by_email: null, admin_delete_disabled: false,
      is_accessible: true, admin_emails: [], is_root: false,
      company_profile: null, company_profile_completed_at: null, metadata: null,
    })

    wrap(<OrgUnitsPage />)
    await screen.findByText(/org structure/i)

    await userEvent.click(await screen.findByRole('button', { name: /new unit/i }))
    await userEvent.type(await screen.findByLabelText(/name/i), 'Eng')
    await userEvent.click(await screen.findByRole('button', { name: /^create unit$/i }))

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        name: 'Eng',
        unit_type: 'division',
        parent_unit_id: null,
        company_profile: null,
      })
    })
  })
})
