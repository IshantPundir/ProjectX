import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import TeamPage from '@/app/(dashboard)/settings/team/page'
import { ApiValidationError } from '@/lib/api/client'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: async () => 'stub-token',
}))

const listMock = vi.fn()
const inviteMock = vi.fn()
vi.mock('@/lib/api/team', () => ({
  teamApi: {
    list: () => listMock(),
    invite: (_t: string, body: unknown) => inviteMock(body),
    resend: async () => undefined,
    revoke: async () => undefined,
    deactivate: async () => undefined,
  },
}))

const meMock = vi.fn()
vi.mock('@/lib/api/auth', () => ({
  authApi: { me: () => meMock() },
}))

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('TeamPage invite form', () => {
  beforeEach(() => {
    listMock.mockResolvedValue([])
    meMock.mockResolvedValue({
      user_id: 'u1', email: 'admin@x.com', full_name: 'A', tenant_id: 't1',
      client_name: 'Acme', is_super_admin: true, onboarding_complete: true,
      has_org_units: true, assignments: [],
    })
    inviteMock.mockReset()
  })

  it('shows a schema validation error on bad email', async () => {
    wrap(<TeamPage />)
    await screen.findByRole('heading', { name: /team & access/i })
    await screen.findByLabelText(/email/i)

    await userEvent.type(screen.getByLabelText(/email/i), 'not-an-email')
    await userEvent.click(screen.getByRole('button', { name: /send invite/i }))

    await waitFor(() => {
      expect(screen.getByText(/valid email/i)).toBeInTheDocument()
    })
    expect(inviteMock).not.toHaveBeenCalled()
  })

  it('maps backend 422 field errors into the form', async () => {
    inviteMock.mockRejectedValue(
      new ApiValidationError('email taken', [
        { loc: ['body', 'email'], msg: 'email already taken', type: 'x' },
      ]),
    )

    wrap(<TeamPage />)
    await screen.findByRole('heading', { name: /team & access/i })
    await screen.findByLabelText(/email/i)

    await userEvent.type(screen.getByLabelText(/email/i), 'taken@x.com')
    await userEvent.click(screen.getByRole('button', { name: /send invite/i }))

    await waitFor(() => {
      expect(screen.getByText(/email already taken/i)).toBeInTheDocument()
    })
  })

  it('submits a valid email and resets the form', async () => {
    inviteMock.mockResolvedValue({
      invite_id: 'i1', email: 'new@x.com', invite_url: 'https://app/invite?token=abc',
    })

    wrap(<TeamPage />)
    await screen.findByRole('heading', { name: /team & access/i })
    await screen.findByLabelText(/email/i)

    await userEvent.type(screen.getByLabelText(/email/i), 'new@x.com')
    await userEvent.click(screen.getByRole('button', { name: /send invite/i }))

    await waitFor(() => {
      expect(inviteMock).toHaveBeenCalledWith({ email: 'new@x.com' })
    })
  })
})
