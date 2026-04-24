import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import InvitePage from '@/app/(auth)/invite/page'

const pushMock = vi.fn()
const refreshMock = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, refresh: refreshMock }),
  useSearchParams: () => ({ get: (k: string) => (k === 'token' ? 'raw-token' : null) }),
}))

const acceptInviteMock = vi.fn()
vi.mock('@/lib/api/auth', () => ({
  authApi: {
    acceptInvite: (body: unknown) => acceptInviteMock(body),
  },
}))

const apiFetchMock = vi.fn()
vi.mock('@/lib/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/client')>('@/lib/api/client')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

const setSessionMock = vi.fn(async () => ({ error: null }))
vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ auth: { setSession: setSessionMock } }),
}))

describe('InvitePage (B4)', () => {
  beforeEach(() => {
    pushMock.mockClear()
    refreshMock.mockClear()
    acceptInviteMock.mockReset()
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue({ email: 'user@example.com', client_name: 'Acme' })
    setSessionMock.mockClear()
  })

  it('shows a field-level error when passwords do not match', async () => {
    render(<InvitePage />)
    await screen.findByText(/acme/i)

    await userEvent.type(screen.getByLabelText(/^set password$/i), 'abcdefgh')
    await userEvent.type(screen.getByLabelText(/^confirm password$/i), 'different')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument()
    })
    expect(acceptInviteMock).not.toHaveBeenCalled()
  })

  it('shows a field-level error when password is too short', async () => {
    render(<InvitePage />)
    await screen.findByText(/acme/i)

    await userEvent.type(screen.getByLabelText(/^set password$/i), 'short')
    await userEvent.type(screen.getByLabelText(/^confirm password$/i), 'short')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument()
    })
    expect(acceptInviteMock).not.toHaveBeenCalled()
  })

  it('submits valid passwords to acceptInvite and installs session', async () => {
    acceptInviteMock.mockResolvedValue({
      access_token: 'a', refresh_token: 'r', expires_in: 3600, redirect_to: '/',
    })

    render(<InvitePage />)
    await screen.findByText(/acme/i)

    await userEvent.type(screen.getByLabelText(/^set password$/i), 'hunter2hunter2')
    await userEvent.type(screen.getByLabelText(/^confirm password$/i), 'hunter2hunter2')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(acceptInviteMock).toHaveBeenCalledWith({
        raw_token: 'raw-token',
        password: 'hunter2hunter2',
      })
    })
    expect(setSessionMock).toHaveBeenCalled()
    expect(pushMock).toHaveBeenCalledWith('/')
  })
})
